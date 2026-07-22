from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from applications.worker import complete_persistent_noop_job
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLWorkerLeaseAuthority,
    PostgreSQLWorkerLeaseIssuer,
    WorkerExecutionIdentity,
    WorkerLeaseIssueNotAvailable,
    WorkerLeaseIssueRequest,
    WorkerLeaseRedemption,
    assert_control_role,
    assert_worker_role,
    create_database_engine,
)
from engine.persistence.configuration import (
    CONTROL_ROLE,
    MIGRATOR_ROLE,
    RUNTIME_ROLE,
    WORKER_ROLE,
)
from engine.supply import (
    WorkerLeaseClaims,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkerLeaseRejectionCategory,
    WorkerLeaseToken,
    WorkNotAvailable,
    worker_lease_digest,
)
from tests.support.security_gate import record_security_oracles

pytestmark = pytest.mark.integration

SIGNING_KEY_VERSION = 7
SIGNING_KEY = b"issue-17-test-key-material-32bytes-minimum"
ROTATED_SIGNING_KEY_VERSION = 8
ROTATED_SIGNING_KEY = b"issue-17-rotated-key-material-32bytes-minimum"
WORKLOAD = "supply.noop"
WORKER_AUDIENCE = "context-engine-worker"
DEFAULT_TEST_TTL_SECONDS = 300

_COMPLETE_NOOP_SQL = text(
    """
    SELECT effect_count
    FROM public.context_worker_complete_noop_job(
        :organization_id,
        :job_id,
        :signing_key_version,
        :nonce,
        :issued_at,
        :expires_at
    )
    """
)


@dataclass(frozen=True, slots=True)
class WorkerJobFixture:
    migration_engine: Engine
    control_engine: Engine
    runtime_engine: Engine
    worker_engine: Engine
    codec: WorkerLeaseCodec
    organization_a: UUID
    organization_b: UUID

    def seed_job(
        self,
        *,
        organization_id: UUID | None = None,
        job_id: UUID | None = None,
        service_principal_id: UUID | None = None,
        workload: str = WORKLOAD,
        worker_audience: str = WORKER_AUDIENCE,
    ) -> WorkerLeaseIssueRequest:
        organization = organization_id or self.organization_a
        job = job_id or uuid4()
        service_principal = service_principal_id or uuid4()
        with self.migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO service_principal (
                        organization_id,
                        service_principal_id,
                        workload,
                        worker_audience,
                        operation,
                        enabled
                    ) VALUES (
                        :organization_id,
                        :service_principal_id,
                        :workload,
                        :worker_audience,
                        'noop.complete',
                        TRUE
                    )
                    """
                ),
                {
                    "organization_id": organization,
                    "service_principal_id": service_principal,
                    "workload": workload,
                    "worker_audience": worker_audience,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO worker_noop_job (
                        organization_id,
                        job_id,
                        service_principal_id,
                        workload,
                        worker_audience,
                        actor_kind,
                        operation,
                        state
                    ) VALUES (
                        :organization_id,
                        :job_id,
                        :service_principal_id,
                        :workload,
                        :worker_audience,
                        'service',
                        'noop.complete',
                        'available'
                    )
                    """
                ),
                {
                    "organization_id": organization,
                    "job_id": job,
                    "service_principal_id": service_principal,
                    "workload": workload,
                    "worker_audience": worker_audience,
                },
            )
        return WorkerLeaseIssueRequest(
            organization_id=organization,
            job_id=job,
            service_principal_id=service_principal,
            workload=workload,
            worker_audience=worker_audience,
        )

    def issuer(
        self, *, lease_ttl_seconds: int = DEFAULT_TEST_TTL_SECONDS
    ) -> PostgreSQLWorkerLeaseIssuer:
        return PostgreSQLWorkerLeaseIssuer(
            self.control_engine,
            self.codec,
            lease_ttl_seconds=lease_ttl_seconds,
        )

    def issue(
        self,
        request: WorkerLeaseIssueRequest,
        *,
        lease_ttl_seconds: int = DEFAULT_TEST_TTL_SECONDS,
    ) -> WorkerLeaseToken:
        return self.issuer(
            lease_ttl_seconds=lease_ttl_seconds
        ).issue_noop_lease(request)

    def authority(
        self,
        request: WorkerLeaseIssueRequest,
        *,
        codec: WorkerLeaseCodec | None = None,
        service_principal_id: UUID | None = None,
        workload: str | None = None,
        worker_audience: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> PostgreSQLWorkerLeaseAuthority:
        lease_codec = self.codec if codec is None else codec
        identity = WorkerExecutionIdentity(
            service_principal_id=(
                service_principal_id or request.service_principal_id
            ),
            workload=workload or request.workload,
            worker_audience=worker_audience or request.worker_audience,
        )
        if clock is None:
            return PostgreSQLWorkerLeaseAuthority(
                self.worker_engine,
                lease_codec,
                identity,
            )
        return PostgreSQLWorkerLeaseAuthority(
            self.worker_engine,
            lease_codec,
            identity,
            clock=clock,
        )

    def job_state(self, request: WorkerLeaseIssueRequest) -> dict[str, Any]:
        with self.migration_engine.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        """
                        SELECT
                            state,
                            signing_key_version,
                            lease_nonce_digest,
                            lease_issued_at,
                            lease_expires_at,
                            lease_redeemed_at,
                            completed_at,
                            effect_count
                        FROM worker_noop_job
                        WHERE organization_id = :organization_id
                          AND job_id = :job_id
                        """
                    ),
                    {
                        "organization_id": request.organization_id,
                        "job_id": request.job_id,
                    },
                ).mappings().one()
            )

    def claims(
        self,
        token: WorkerLeaseToken,
        request: WorkerLeaseIssueRequest,
        *,
        codec: WorkerLeaseCodec | None = None,
    ) -> WorkerLeaseClaims:
        issued_at = self.job_state(request)["lease_issued_at"]
        assert isinstance(issued_at, datetime)
        lease_codec = self.codec if codec is None else codec
        return lease_codec.verify(
            token,
            expected_organization_id=request.organization_id,
            expected_job_id=request.job_id,
            expected_service_principal_id=request.service_principal_id,
            expected_workload=request.workload,
            expected_operation="noop.complete",
            expected_worker_audience=request.worker_audience,
            now=issued_at,
        )


@pytest.fixture
def worker_jobs(
    migration_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
) -> Iterator[WorkerJobFixture]:
    migration_engine = create_database_engine(migration_configuration)
    control_engine = create_database_engine(control_configuration)
    runtime_engine = create_database_engine(runtime_configuration)
    worker_engine = create_database_engine(worker_configuration)
    organizations = (uuid4(), uuid4())
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(
            active_version=SIGNING_KEY_VERSION,
            keys={SIGNING_KEY_VERSION: SIGNING_KEY},
        )
    )
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO organization (organization_id)
                    VALUES (:organization_a), (:organization_b)
                    """
                ),
                {
                    "organization_a": organizations[0],
                    "organization_b": organizations[1],
                },
            )
        with control_engine.connect() as connection:
            assert_control_role(connection)
        with worker_engine.connect() as connection:
            assert_worker_role(connection)
        yield WorkerJobFixture(
            migration_engine=migration_engine,
            control_engine=control_engine,
            runtime_engine=runtime_engine,
            worker_engine=worker_engine,
            codec=codec,
            organization_a=organizations[0],
            organization_b=organizations[1],
        )
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM organization
                    WHERE organization_id IN (:organization_a, :organization_b)
                    """
                ),
                {
                    "organization_a": organizations[0],
                    "organization_b": organizations[1],
                },
            )
        worker_engine.dispose()
        runtime_engine.dispose()
        control_engine.dispose()
        migration_engine.dispose()


def redemption(
    token: WorkerLeaseToken,
    request: WorkerLeaseIssueRequest,
) -> WorkerLeaseRedemption:
    return WorkerLeaseRedemption(
        token=token,
        expected_organization_id=request.organization_id,
        expected_job_id=request.job_id,
    )


def _decode_token_document(
    token: WorkerLeaseToken,
) -> tuple[dict[str, object], dict[str, object]]:
    encoded_header, encoded_claims, _encoded_signature = token.serialize().split(".")

    def decode(encoded: str) -> dict[str, object]:
        padded = encoded + "=" * (-len(encoded) % 4)
        document = json.loads(base64.urlsafe_b64decode(padded))
        assert isinstance(document, dict)
        return document

    return decode(encoded_header), decode(encoded_claims)


def _one_bit_tamper(token: WorkerLeaseToken) -> WorkerLeaseToken:
    header, claims, encoded_signature = token.serialize().split(".")
    padded_signature = encoded_signature + "=" * (-len(encoded_signature) % 4)
    signature = bytearray(base64.urlsafe_b64decode(padded_signature))
    signature[-1] ^= 0b00000001
    tampered_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return WorkerLeaseToken(f"{header}.{claims}.{tampered_signature}")


def _raw_completion_parameters(
    claims: WorkerLeaseClaims,
    *,
    nonce: bytes | None = None,
) -> dict[str, object]:
    return {
        "organization_id": claims.organization_id,
        "job_id": claims.job_id,
        "signing_key_version": claims.signing_key_version,
        "nonce": nonce or claims.nonce,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
    }


def _assert_safe_rejection(
    rejected: WorkNotAvailable,
    token: WorkerLeaseToken,
) -> None:
    receipt = rejected.audit_receipt
    assert {field.name for field in fields(receipt)} == {
        "lease_digest",
        "category",
    }
    assert asdict(receipt) == {
        "lease_digest": worker_lease_digest(token),
        "category": WorkerLeaseRejectionCategory.WORK_NOT_AVAILABLE,
    }
    assert str(rejected) == "work not available"
    assert token.serialize() not in repr(rejected)


def test_control_issuer_uses_db_time_and_signs_every_required_exact_job_claim(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    wrong_job = replace(request, job_id=uuid4())

    with pytest.raises(
        WorkerLeaseIssueNotAvailable,
        match="^work not available$",
    ):
        worker_jobs.issuer(lease_ttl_seconds=37).issue_noop_lease(wrong_job)
    assert worker_jobs.job_state(request)["state"] == "available"

    token = worker_jobs.issue(request, lease_ttl_seconds=37)
    state = worker_jobs.job_state(request)
    claims = worker_jobs.claims(token, request)
    header, document = _decode_token_document(token)

    assert {field.name for field in fields(WorkerLeaseIssueRequest) if field.init} == {
        "organization_id",
        "job_id",
        "service_principal_id",
        "workload",
        "worker_audience",
    }
    assert header["kid"] == SIGNING_KEY_VERSION
    assert set(document) == {
        "actor_kind",
        "expires_at",
        "issued_at",
        "job_id",
        "nonce",
        "operation",
        "organization_id",
        "service_principal_id",
        "signing_key_version",
        "worker_audience",
        "workload",
    }
    assert claims.organization_id == request.organization_id
    assert claims.job_id == request.job_id
    assert claims.service_principal_id == request.service_principal_id
    assert claims.worker_audience == request.worker_audience
    assert claims.workload == request.workload
    assert claims.actor_kind == "service"
    assert claims.operation == "noop.complete"
    assert claims.signing_key_version == SIGNING_KEY_VERSION
    assert len(claims.nonce) == 32
    assert (claims.expires_at - claims.issued_at).total_seconds() == 37
    assert state["state"] == "leased"
    assert state["lease_issued_at"] == claims.issued_at
    assert state["lease_expires_at"] == claims.expires_at
    assert state["signing_key_version"] == claims.signing_key_version
    assert state["lease_nonce_digest"] == hashlib.sha256(claims.nonce).digest()
    assert state["effect_count"] == 0


@pytest.mark.security_evidence(id="RUNTIME-WORKER-LEASE-007", layer="runtime")
@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-008", layer="runtime")
def test_valid_lease_completes_once_through_worker_application_and_replay_is_zero(
    worker_jobs: WorkerJobFixture,
    record_property: Callable[[str, object], None],
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    authority = worker_jobs.authority(request)

    completion = complete_persistent_noop_job(authority, redemption(token, request))

    state = worker_jobs.job_state(request)
    assert completion.effect_count == 1
    assert state["state"] == "completed"
    assert state["effect_count"] == 1
    assert state["lease_redeemed_at"] is not None
    assert state["completed_at"] is not None

    with pytest.raises(WorkNotAvailable, match="^work not available$") as error:
        complete_persistent_noop_job(authority, redemption(token, request))

    _assert_safe_rejection(error.value, token)
    replayed_state = worker_jobs.job_state(request)
    assert replayed_state["state"] == "completed"
    assert replayed_state["effect_count"] == 1
    serialized_token = token.serialize()
    unauthorized_evidence_count = int(
        serialized_token in str(error.value) or serialized_token in repr(error.value)
    )
    wrong_organization_effect_count = replayed_state["effect_count"] - 1
    missing_context_fallback_count = int(
        str(error.value) != "work not available"
        or replayed_state["state"] != "completed"
    )
    assert unauthorized_evidence_count == 0
    assert wrong_organization_effect_count == 0
    assert missing_context_fallback_count == 0
    record_security_oracles(
        record_property,
        fixture_ref="ACCEPT-008",
        unauthorized_evidence_count=unauthorized_evidence_count,
        wrong_organization_effect_count=wrong_organization_effect_count,
        missing_context_fallback_count=missing_context_fallback_count,
    )


def test_two_concurrent_redemptions_commit_exactly_one_effect(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    authority = worker_jobs.authority(request)
    attempt = redemption(token, request)
    barrier = Barrier(2)

    def redeem_once() -> str:
        barrier.wait(timeout=5)
        try:
            complete_persistent_noop_job(authority, attempt)
        except WorkNotAvailable:
            return "rejected"
        return "completed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: redeem_once(), range(2)))

    assert sorted(outcomes) == ["completed", "rejected"]
    assert worker_jobs.job_state(request)["state"] == "completed"
    assert worker_jobs.job_state(request)["effect_count"] == 1


@pytest.mark.parametrize(
    "changed_field",
    [
        "organization_id",
        "job_id",
        "service_principal_id",
        "workload",
        "worker_audience",
    ],
)
def test_wrong_signed_lease_binding_has_zero_effect(
    worker_jobs: WorkerJobFixture,
    changed_field: str,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    attempt = redemption(token, request)
    authority = worker_jobs.authority(request)
    if changed_field == "organization_id":
        attempt = replace(attempt, expected_organization_id=uuid4())
    elif changed_field == "job_id":
        attempt = replace(attempt, expected_job_id=uuid4())
    elif changed_field == "service_principal_id":
        authority = worker_jobs.authority(request, service_principal_id=uuid4())
    elif changed_field == "workload":
        authority = worker_jobs.authority(request, workload="wrong-workload")
    else:
        authority = worker_jobs.authority(
            request,
            worker_audience="wrong-worker-audience",
        )

    with pytest.raises(WorkNotAvailable, match="^work not available$") as error:
        complete_persistent_noop_job(authority, attempt)

    _assert_safe_rejection(error.value, token)
    assert worker_jobs.job_state(request)["state"] == "leased"
    assert worker_jobs.job_state(request)["effect_count"] == 0


@pytest.mark.security_evidence(id="PG-WORKER-LEASE-007", layer="postgres")
def test_wrong_organization_cannot_affect_same_job_identifier_in_another_tenant(
    worker_jobs: WorkerJobFixture,
) -> None:
    shared_job_id = uuid4()
    shared_service_principal_id = uuid4()
    request_a = worker_jobs.seed_job(
        organization_id=worker_jobs.organization_a,
        job_id=shared_job_id,
        service_principal_id=shared_service_principal_id,
    )
    request_b = worker_jobs.seed_job(
        organization_id=worker_jobs.organization_b,
        job_id=shared_job_id,
        service_principal_id=shared_service_principal_id,
    )
    token_a = worker_jobs.issue(request_a)

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        complete_persistent_noop_job(
            worker_jobs.authority(request_b),
            redemption(token_a, request_b),
        )

    assert worker_jobs.job_state(request_a)["effect_count"] == 0
    assert worker_jobs.job_state(request_b)["effect_count"] == 0


def test_one_bit_tampered_lease_has_zero_effect(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    tampered = _one_bit_tamper(token)

    with pytest.raises(WorkNotAvailable, match="^work not available$") as error:
        complete_persistent_noop_job(
            worker_jobs.authority(request),
            redemption(tampered, request),
        )

    _assert_safe_rejection(error.value, tampered)
    assert worker_jobs.job_state(request)["state"] == "leased"
    assert worker_jobs.job_state(request)["effect_count"] == 0


def test_database_current_time_rejects_expired_lease_despite_lagging_worker_clock(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request, lease_ttl_seconds=1)
    issued_at = worker_jobs.job_state(request)["lease_issued_at"]
    assert isinstance(issued_at, datetime)
    with worker_jobs.migration_engine.connect() as connection:
        connection.execute(text("SELECT pg_sleep(1.1)"))

    lagging_authority = worker_jobs.authority(
        request,
        clock=lambda: issued_at,
    )
    with pytest.raises(WorkNotAvailable, match="^work not available$") as error:
        complete_persistent_noop_job(
            lagging_authority,
            redemption(token, request),
        )

    _assert_safe_rejection(error.value, token)
    assert worker_jobs.job_state(request)["state"] == "leased"
    assert worker_jobs.job_state(request)["effect_count"] == 0


def test_signed_nonce_must_match_the_persisted_digest(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    with worker_jobs.migration_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE worker_noop_job
                SET lease_nonce_digest = :different_digest
                WHERE organization_id = :organization_id
                  AND job_id = :job_id
                """
            ),
            {
                "different_digest": b"x" * 32,
                "organization_id": request.organization_id,
                "job_id": request.job_id,
            },
        )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        complete_persistent_noop_job(
            worker_jobs.authority(request),
            redemption(token, request),
        )

    assert worker_jobs.job_state(request)["state"] == "leased"
    assert worker_jobs.job_state(request)["effect_count"] == 0


def test_disabled_registered_service_principal_cannot_redeem_a_valid_lease(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    with worker_jobs.migration_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE service_principal
                SET enabled = FALSE
                WHERE organization_id = :organization_id
                  AND service_principal_id = :service_principal_id
                """
            ),
            {
                "organization_id": request.organization_id,
                "service_principal_id": request.service_principal_id,
            },
        )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        complete_persistent_noop_job(
            worker_jobs.authority(request),
            redemption(token, request),
        )

    assert worker_jobs.job_state(request)["effect_count"] == 0


@contextmanager
def bound_worker_transaction(
    engine: Engine,
    request: WorkerLeaseIssueRequest,
) -> Iterator[Connection]:
    settings = {
        "app.organization_id": str(request.organization_id),
        "app.actor_kind": "service",
        "app.service_principal_id": str(request.service_principal_id),
        "app.workload": request.workload,
        "app.worker_audience": request.worker_audience,
        "app.operation": "noop.complete",
        "app.worker_job_id": str(request.job_id),
    }
    with engine.begin() as connection:
        for name, value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": name, "value": value},
            )
        yield connection


def test_worker_guc_poisoning_cannot_read_either_tenant_table(
    worker_jobs: WorkerJobFixture,
) -> None:
    shared_job_id = uuid4()
    shared_service_principal_id = uuid4()
    request_a = worker_jobs.seed_job(
        organization_id=worker_jobs.organization_a,
        job_id=shared_job_id,
        service_principal_id=shared_service_principal_id,
    )
    worker_jobs.seed_job(
        organization_id=worker_jobs.organization_b,
        job_id=shared_job_id,
        service_principal_id=shared_service_principal_id,
    )

    for table_name in ("service_principal", "worker_noop_job"):
        with (
            pytest.raises(ProgrammingError, match="permission denied for table"),
            bound_worker_transaction(
                worker_jobs.worker_engine,
                request_a,
            ) as connection,
        ):
            connection.execute(text(f"SELECT * FROM {table_name}")).all()

    with worker_jobs.migration_engine.connect() as connection:
        relations = connection.execute(
            text(
                """
                SELECT
                    relation.relname,
                    relation.relrowsecurity,
                    relation.relforcerowsecurity,
                    pg_get_userbyid(relation.relowner)
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relname IN (
                      'service_principal', 'worker_noop_job'
                  )
                ORDER BY relation.relname
                """
            )
        ).all()
    assert [tuple(row) for row in relations] == [
        ("service_principal", True, True, MIGRATOR_ROLE),
        ("worker_noop_job", True, True, MIGRATOR_ROLE),
    ]


def test_worker_tables_have_minimum_grants_and_direct_update_is_denied(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    with worker_jobs.migration_engine.connect() as connection:
        privileges = {
            (role, table_name, privilege): connection.execute(
                text("SELECT has_table_privilege(:role, :table_name, :privilege)"),
                {
                    "role": role,
                    "table_name": f"public.{table_name}",
                    "privilege": privilege,
                },
            ).scalar_one()
            for role in (WORKER_ROLE, RUNTIME_ROLE, CONTROL_ROLE)
            for table_name in ("service_principal", "worker_noop_job")
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }

    assert {key for key, granted in privileges.items() if granted} == set()

    with (
        pytest.raises(ProgrammingError, match="permission denied for table"),
        bound_worker_transaction(
            worker_jobs.worker_engine,
            request,
        ) as connection,
    ):
        connection.execute(
            text(
                """
                UPDATE worker_noop_job
                SET state = 'completed', effect_count = 1
                WHERE organization_id = :organization_id
                  AND job_id = :job_id
                """
            ),
            {
                "organization_id": request.organization_id,
                "job_id": request.job_id,
            },
        )

    assert worker_jobs.job_state(request)["state"] == "available"
    assert worker_jobs.job_state(request)["effect_count"] == 0


def test_raw_completion_is_role_narrow_and_forged_nonce_has_zero_effect(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    claims = worker_jobs.claims(token, request)
    parameters = _raw_completion_parameters(claims)

    for unauthorized_engine in (
        worker_jobs.control_engine,
        worker_jobs.runtime_engine,
    ):
        with (
            unauthorized_engine.connect() as connection,
            pytest.raises(ProgrammingError, match="permission denied for function"),
        ):
            connection.execute(_COMPLETE_NOOP_SQL, parameters).all()

    forged_nonce = bytes([claims.nonce[0] ^ 1]) + claims.nonce[1:]
    with worker_jobs.worker_engine.begin() as connection:
        rows = connection.execute(
            _COMPLETE_NOOP_SQL,
            _raw_completion_parameters(claims, nonce=forged_nonce),
        ).all()

    assert rows == []
    assert worker_jobs.job_state(request)["state"] == "leased"
    assert worker_jobs.job_state(request)["effect_count"] == 0


def test_raw_completion_has_no_caller_supplied_receiver_dimensions(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    claims = worker_jobs.claims(token, request)
    parameters = _raw_completion_parameters(claims)
    old_receiver_parameter_sql = text(
        """
        SELECT * FROM public.context_worker_complete_noop_job(
            :organization_id,
            :job_id,
            :forged_service_principal_id,
            :forged_workload,
            :forged_worker_audience,
            :forged_operation,
            :signing_key_version,
            :nonce,
            :issued_at,
            :expires_at
        )
        """
    )

    with (
        worker_jobs.worker_engine.connect() as connection,
        pytest.raises(ProgrammingError, match="does not exist"),
    ):
        connection.execute(
            old_receiver_parameter_sql,
            {
                **parameters,
                "forged_service_principal_id": uuid4(),
                "forged_workload": "forged.workload",
                "forged_worker_audience": "forged-audience",
                "forged_operation": "noop.complete",
            },
        ).all()

    assert worker_jobs.job_state(request)["state"] == "leased"
    assert worker_jobs.job_state(request)["effect_count"] == 0


def test_issue17_tables_reject_noncanonical_receiver_rows(
    worker_jobs: WorkerJobFixture,
) -> None:
    for changed_column, changed_value, constraint_name in (
        (
            "workload",
            "forged.workload",
            "ck_service_principal_workload_issue17",
        ),
        (
            "worker_audience",
            "forged-audience",
            "ck_service_principal_worker_audience_issue17",
        ),
    ):
        with (
            worker_jobs.migration_engine.begin() as connection,
            pytest.raises(IntegrityError, match=constraint_name),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO service_principal (
                        organization_id,
                        service_principal_id,
                        workload,
                        worker_audience,
                        operation,
                        enabled
                    ) VALUES (
                        :organization_id,
                        :service_principal_id,
                        :workload,
                        :worker_audience,
                        'noop.complete',
                        TRUE
                    )
                    """
                ),
                {
                    "organization_id": worker_jobs.organization_a,
                    "service_principal_id": uuid4(),
                    "workload": (
                        changed_value if changed_column == "workload" else WORKLOAD
                    ),
                    "worker_audience": (
                        changed_value
                        if changed_column == "worker_audience"
                        else WORKER_AUDIENCE
                    ),
                },
            )


def test_current_lease_cannot_be_reissued(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    before = worker_jobs.job_state(request)

    with pytest.raises(WorkerLeaseIssueNotAvailable, match="^work not available$"):
        worker_jobs.issue(request)

    after = worker_jobs.job_state(request)
    assert after == before
    completion = complete_persistent_noop_job(
        worker_jobs.authority(request),
        redemption(token, request),
    )
    assert completion.effect_count == 1


def test_two_concurrent_expired_lease_reissues_produce_one_current_token(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    worker_jobs.issue(request, lease_ttl_seconds=1)
    with worker_jobs.migration_engine.connect() as connection:
        connection.execute(text("SELECT pg_sleep(1.1)"))
    barrier = Barrier(2)

    def reissue_once() -> WorkerLeaseToken | None:
        barrier.wait(timeout=5)
        try:
            return worker_jobs.issue(request)
        except WorkerLeaseIssueNotAvailable:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        tokens = list(executor.map(lambda _index: reissue_once(), range(2)))

    winners = [token for token in tokens if token is not None]
    assert len(winners) == 1
    completion = complete_persistent_noop_job(
        worker_jobs.authority(request),
        redemption(winners[0], request),
    )
    assert completion.effect_count == 1
    assert worker_jobs.job_state(request)["effect_count"] == 1


def test_expired_lease_is_atomically_reissued_and_only_new_token_completes(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    old_token = worker_jobs.issue(request, lease_ttl_seconds=1)
    old_claims = worker_jobs.claims(old_token, request)
    with worker_jobs.migration_engine.connect() as connection:
        connection.execute(text("SELECT pg_sleep(1.1)"))

    rotated_codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(
            active_version=ROTATED_SIGNING_KEY_VERSION,
            keys={
                SIGNING_KEY_VERSION: SIGNING_KEY,
                ROTATED_SIGNING_KEY_VERSION: ROTATED_SIGNING_KEY,
            },
        )
    )
    new_token = PostgreSQLWorkerLeaseIssuer(
        worker_jobs.control_engine,
        rotated_codec,
    ).issue_noop_lease(request)
    new_claims = worker_jobs.claims(new_token, request, codec=rotated_codec)
    state = worker_jobs.job_state(request)
    assert new_token.serialize() != old_token.serialize()
    assert old_claims.signing_key_version == SIGNING_KEY_VERSION
    assert new_claims.signing_key_version == ROTATED_SIGNING_KEY_VERSION
    assert new_claims.nonce != old_claims.nonce
    assert new_claims.issued_at >= old_claims.expires_at
    assert state["lease_nonce_digest"] == hashlib.sha256(new_claims.nonce).digest()
    assert state["lease_issued_at"] == new_claims.issued_at
    assert state["lease_expires_at"] == new_claims.expires_at

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        complete_persistent_noop_job(
            worker_jobs.authority(
                request,
                codec=rotated_codec,
                clock=lambda: old_claims.issued_at,
            ),
            redemption(old_token, request),
        )
    assert worker_jobs.job_state(request)["effect_count"] == 0

    completion = complete_persistent_noop_job(
        worker_jobs.authority(request, codec=rotated_codec),
        redemption(new_token, request),
    )
    assert completion.effect_count == 1
    assert worker_jobs.job_state(request)["effect_count"] == 1


class _ForcedPostCASRollback(RuntimeError):
    pass


def test_failure_after_completion_cas_but_before_commit_rolls_back_every_effect(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    authority = worker_jobs.authority(request)

    def fail_after_completion_cas(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "context_worker_complete_noop_job" in statement:
            raise _ForcedPostCASRollback("forced failure before commit")

    event.listen(
        worker_jobs.worker_engine,
        "after_cursor_execute",
        fail_after_completion_cas,
    )
    try:
        with pytest.raises(
            _ForcedPostCASRollback,
            match="forced failure before commit",
        ):
            complete_persistent_noop_job(authority, redemption(token, request))
    finally:
        event.remove(
            worker_jobs.worker_engine,
            "after_cursor_execute",
            fail_after_completion_cas,
        )

    rolled_back = worker_jobs.job_state(request)
    assert rolled_back["state"] == "leased"
    assert rolled_back["lease_redeemed_at"] is None
    assert rolled_back["completed_at"] is None
    assert rolled_back["effect_count"] == 0

    completion = complete_persistent_noop_job(authority, redemption(token, request))
    assert completion.effect_count == 1
    assert worker_jobs.job_state(request)["effect_count"] == 1


def test_job_token_and_exact_audit_output_contain_no_source_credentials(
    worker_jobs: WorkerJobFixture,
) -> None:
    request = worker_jobs.seed_job()
    token = worker_jobs.issue(request)
    raw_token = token.serialize()
    completion = complete_persistent_noop_job(
        worker_jobs.authority(request),
        redemption(token, request),
    )
    _header, lease_document = _decode_token_document(token)

    with worker_jobs.migration_engine.connect() as connection:
        job_columns = set(
            connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'worker_noop_job'
                    """
                )
            ).scalars()
        )

    assert job_columns == {
        "actor_kind",
        "completed_at",
        "effect_count",
        "job_id",
        "lease_expires_at",
        "lease_issued_at",
        "lease_nonce_digest",
        "lease_redeemed_at",
        "operation",
        "organization_id",
        "service_principal_id",
        "signing_key_version",
        "state",
        "worker_audience",
        "workload",
    }
    assert {field.name for field in fields(completion)} == {
        "audit_receipt",
        "effect_count",
    }
    assert {field.name for field in fields(completion.audit_receipt)} == {
        "lease_digest",
        "outcome",
    }
    audit_output = asdict(completion.audit_receipt)
    assert audit_output == {
        "lease_digest": worker_lease_digest(token),
        "outcome": "completed",
    }

    serialized_evidence = json.dumps(
        {
            "audit": audit_output,
            "job_columns": sorted(job_columns),
            "lease": lease_document,
        },
        sort_keys=True,
    ).lower()
    forbidden_names = {
        "access_token",
        "credential",
        "password",
        "refresh_token",
        "secret",
        "source_payload",
    }
    assert all(name not in serialized_evidence for name in forbidden_names)
    assert SIGNING_KEY.decode() not in raw_token
    assert raw_token not in repr(completion)
    assert str(request.service_principal_id) not in repr(completion)
    assert "nonce" not in repr(completion).lower()
    assert "signing_key_version" not in repr(completion)
