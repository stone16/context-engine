from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

import engine.persistence.membership_context as membership_context_module
from adapters.http.app import create_app
from adapters.http.authentication import (
    AuthenticationRejected,
    VerifiedAuthenticationContext,
)
from adapters.http.organization_authority import OrganizationVerificationRejected
from adapters.http.scope_authority import ScopeAuthorityIdentity
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.context_run import (
    ContextRunOutcome,
    ContextRunRecord,
    DecisionAuditCategory,
    DecisionAuditRecord,
)
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    _construct_existing_http_organization_verification,
)
from engine.runtime.package_digest import (
    QueryDigestKeyring,
    verify_context_package_digest,
)
from engine.runtime.scope import ScopeSet, ScopeTarget
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)
from tests.support.context_run_operator import exact_test_context_run_operator_read
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)
from tests.support.security_gate import record_security_oracles

pytestmark = pytest.mark.integration

RECEIVED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
QUERY = "same content-free candidate"
STATUS_BLOCK = "status=open"
FULL_BLOCK = "status=open\nprivate_note=secret"


@dataclass(frozen=True, slots=True)
class MembershipFixture:
    label: str
    user_id: UUID
    membership_id: UUID
    principal_ref: str
    token: str


@dataclass(frozen=True, slots=True)
class FieldProjectionFixture:
    organization_id: UUID
    candidate: CandidateRef
    full: MembershipFixture
    limited: MembershipFixture


class MembershipAuthenticator:
    def __init__(
        self,
        fixture: FieldProjectionFixture,
    ) -> None:
        self._members = {
            member.token: member for member in (fixture.full, fixture.limited)
        }
        self._organization_id = fixture.organization_id

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        member = self._members.get(opaque_credential)
        if member is None:
            raise AuthenticationRejected
        return VerifiedAuthenticationContext(
            organization_ref=str(self._organization_id),
            user_ref=str(member.user_id),
            principal_ref=member.principal_ref,
            membership_ref=str(member.membership_id),
            membership_version=7,
            agent_version_ref="agent:accept-002:v1",
            authenticated_application_ref="application:accept-002",
            authentication_binding_ref=f"binding:accept-002:{member.label}",
        )


class ExactOrganizationAuthority:
    def __init__(self, organization_id: UUID) -> None:
        self._organization_id = organization_id

    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        if authentication.organization_ref != str(self._organization_id):
            raise OrganizationVerificationRejected
        return _construct_existing_http_organization_verification(
            organization_id=self._organization_id,
            request_id=request_id,
            authentication_binding_ref=authentication.authentication_binding_ref,
            verified_at=verified_at,
        )


class ExactResourceScopeAuthority:
    def __init__(self, candidate: CandidateRef) -> None:
        self._candidate = candidate
        self.identities: list[ScopeAuthorityIdentity] = []

    @contextmanager
    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> Iterator[TrustedScopeSnapshot]:
        self.identities.append(identity)
        target = ScopeTarget(
            identity.organization_id,
            self._candidate.source_ref,
            self._candidate.resource_ref,
        )
        exact = ScopeSet(frozenset({target}))
        authority_scope = _open_scope_authority_scope()
        try:
            yield _construct_trusted_scope_snapshot(
                authority_scope=authority_scope,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                membership_version=identity.membership_version,
                policy_epoch=identity.policy_epoch,
                principal_ref=identity.principal_ref,
                agent_version_ref=identity.agent_version_ref,
                purpose=identity.purpose,
                request_id=identity.request_id,
                authentication_binding_ref=identity.authentication_binding_ref,
                checked_at=identity.checked_at,
                organization_boundary=exact,
                membership_rights=exact,
                principal_grants=exact,
                agent_ceiling=exact,
                source_native_acl=exact,
                resource_acl=exact,
                purpose_policy=exact,
            )
        finally:
            _close_scope_authority_scope(authority_scope)


class SameContentFreeCandidateIndex:
    """Return one immutable locator without body or field authority metadata."""

    def __init__(self, candidate: CandidateRef) -> None:
        self.candidate = candidate
        self.calls: list[Acquire] = []
        self.returned_candidates: list[CandidateRef] = []

    def discover(
        self, request: Acquire, projection_session: object
    ) -> tuple[CandidateRef, ...]:
        del projection_session
        self.calls.append(request)
        self.returned_candidates.append(self.candidate)
        return (self.candidate,)


def _new_fixture() -> FieldProjectionFixture:
    organization_id = uuid4()
    resource_ref = f"resource:accept-002:{uuid4()}"
    return FieldProjectionFixture(
        organization_id=organization_id,
        candidate=CandidateRef(
            organization_id=organization_id,
            source_ref="source:accept-002",
            resource_ref=resource_ref,
            revision_ref=str(uuid4()),
            fragment_ref=f"fragment:accept-002:{uuid4()}",
        ),
        full=MembershipFixture(
            label="member-full",
            user_id=uuid4(),
            membership_id=uuid4(),
            principal_ref="principal:accept-002:member-full",
            token="accept-002-token-full",
        ),
        limited=MembershipFixture(
            label="member-limited",
            user_id=uuid4(),
            membership_id=uuid4(),
            principal_ref="principal:accept-002:member-limited",
            token="accept-002-token-limited",
        ),
    )


def _seed_fixture(engine: Engine, fixture: FieldProjectionFixture) -> None:
    candidate = fixture.candidate
    parameters = {
        "organization_id": fixture.organization_id,
        "source_ref": candidate.source_ref,
        "resource_ref": candidate.resource_ref,
        "revision_id": UUID(candidate.revision_ref),
        "fragment_ref": candidate.fragment_ref,
        "full_user_id": fixture.full.user_id,
        "full_membership_id": fixture.full.membership_id,
        "full_principal_ref": fixture.full.principal_ref,
        "limited_user_id": fixture.limited.user_id,
        "limited_membership_id": fixture.limited.membership_id,
        "limited_principal_ref": fixture.limited.principal_ref,
        "valid_from": RECEIVED_AT - timedelta(days=1),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (organization_id) VALUES (:organization_id)"
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO user_account (user_id)
                VALUES (:full_user_id), (:limited_user_id)
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO membership (
                    organization_id, membership_id, user_id, status,
                    membership_version, valid_from, valid_until
                ) VALUES
                (
                    :organization_id, :full_membership_id, :full_user_id,
                    'active', 7, :valid_from, NULL
                ),
                (
                    :organization_id, :limited_membership_id, :limited_user_id,
                    'active', 7, :valid_from, NULL
                )
                """
            ),
            parameters,
        )
        connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        connection.execute(
            text(
                """
                INSERT INTO context_resource (
                    organization_id, resource_ref, source_ref,
                    active_revision_id, tombstoned
                ) VALUES (
                    :organization_id, :resource_ref, :source_ref,
                    :revision_id, false
                )
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO context_revision (
                    organization_id, resource_ref, revision_id
                ) VALUES (
                    :organization_id, :resource_ref, :revision_id
                )
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO context_fragment (
                    organization_id, resource_ref, revision_id,
                    fragment_ref, ordinal, projection_kind, content
                ) VALUES (
                    :organization_id, :resource_ref, :revision_id,
                    :fragment_ref, 0, 'fields', NULL
                )
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO context_fragment_field (
                    organization_id, resource_ref, revision_id,
                    fragment_ref, field_ref, ordinal, field_value
                ) VALUES
                (
                    :organization_id, :resource_ref, :revision_id,
                    :fragment_ref, 'status', 0, 'open'
                ),
                (
                    :organization_id, :resource_ref, :revision_id,
                    :fragment_ref, 'private_note', 1, 'secret'
                )
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO resource_access_policy (
                    organization_id, resource_ref, principal_ref,
                    access_version, access_state, revoked_at
                ) VALUES
                (
                    :organization_id, :resource_ref, :full_principal_ref,
                    1, 'allowed', NULL
                ),
                (
                    :organization_id, :resource_ref, :limited_principal_ref,
                    1, 'allowed', NULL
                )
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO membership_resource_field_right (
                    organization_id, membership_id, membership_version,
                    resource_ref, field_ref
                ) VALUES
                (
                    :organization_id, :full_membership_id, 7,
                    :resource_ref, 'status'
                ),
                (
                    :organization_id, :full_membership_id, 7,
                    :resource_ref, 'private_note'
                ),
                (
                    :organization_id, :limited_membership_id, 7,
                    :resource_ref, 'status'
                )
                """
            ),
            parameters,
        )


def _cleanup_fixture(engine: Engine, fixture: FieldProjectionFixture) -> None:
    clear_test_runtime_release(fixture.organization_id)
    parameters = {
        "organization_id": fixture.organization_id,
        "full_user_id": fixture.full.user_id,
        "limited_user_id": fixture.limited.user_id,
    }
    with engine.begin() as connection:
        for table_name, trigger_name in (
            (
                "context_fragment_field",
                "context_fragment_field_reject_mutation",
            ),
            ("context_fragment", "context_fragment_reject_mutation"),
            ("context_revision", "context_revision_reject_mutation"),
        ):
            connection.execute(
                text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}")
            )
    try:
        with engine.begin() as connection:
            for statement in (
                "DELETE FROM decision_audit WHERE organization_id = :organization_id",
                "DELETE FROM context_run WHERE organization_id = :organization_id",
                "DELETE FROM membership_resource_field_right "
                "WHERE organization_id = :organization_id",
                "DELETE FROM resource_access_policy "
                "WHERE organization_id = :organization_id",
                "DELETE FROM context_fragment_field "
                "WHERE organization_id = :organization_id",
                "DELETE FROM context_fragment WHERE organization_id = :organization_id",
                "DELETE FROM context_revision WHERE organization_id = :organization_id",
                "DELETE FROM context_resource WHERE organization_id = :organization_id",
                "DELETE FROM membership WHERE organization_id = :organization_id",
                "DELETE FROM user_account "
                "WHERE user_id IN (:full_user_id, :limited_user_id)",
                "DELETE FROM organization WHERE organization_id = :organization_id",
            ):
                connection.execute(text(statement), parameters)
    finally:
        with engine.begin() as connection:
            for table_name, trigger_name in (
                ("context_revision", "context_revision_reject_mutation"),
                ("context_fragment", "context_fragment_reject_mutation"),
                (
                    "context_fragment_field",
                    "context_fragment_field_reject_mutation",
                ),
            ):
                connection.execute(
                    text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}")
                )


def _resolve(
    client: TestClient,
    member: MembershipFixture,
    *,
    request_label: str,
) -> Response:
    return client.post(
        "/v1/context:resolve",
        headers={
            "Authorization": f"Bearer {member.token}",
            "X-Context-Request-Id": f"accept-002:{request_label}",
        },
        json={"kind": "acquire", "need": {"query": QUERY}},
    )


def _assert_authorized_projection(
    response: Response,
    *,
    fixture: FieldProjectionFixture,
    expected_body: str,
    expected_fields: list[str],
) -> tuple[str, str]:
    assert response.status_code == 200
    document = response.json()
    package = document["package"]
    assert package["coverage"] == {"status": "sufficient"}
    assert package["gaps"] == []
    assert len(package["blocks"]) == len(package["evidence"]) == 1
    evidence = package["evidence"][0]
    assert package["blocks"][0] == {
        "blockId": f"block_{evidence['evidenceRef'].removeprefix('ev_')}",
        "text": expected_body,
        "evidenceRefs": [evidence["evidenceRef"]],
    }
    assert evidence["sourceRef"] == fixture.candidate.source_ref
    assert evidence["resourceRef"] == fixture.candidate.resource_ref
    assert evidence["revisionRef"] == fixture.candidate.revision_ref
    assert evidence["fragmentRef"] == fixture.candidate.fragment_ref
    assert evidence["projectedFields"] == expected_fields
    assert package["budgetUsage"] == {
        "tokens": len(expected_body.encode("utf-8")),
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }
    digest_document = dict(package)
    package_digest = digest_document.pop("packageDigest")
    assert verify_context_package_digest(digest_document, package_digest)
    return package["decisionRef"], evidence["evidenceRef"]


def _assert_run_has_only_authorized_evidence_ref(
    *,
    control_engine: Engine,
    operator_engine: Engine,
    fixture: FieldProjectionFixture,
    decision_ref: str,
    evidence_ref: str,
) -> None:
    with exact_test_context_run_operator_read(
        control_engine=control_engine,
        operator_engine=operator_engine,
        organization_id=fixture.organization_id,
        decision_ref=decision_ref,
        request_id=f"test:accept-002:read:{decision_ref}",
        opaque_credential=f"test:accept-002:credential:{decision_ref}",
        authorized_at=RECEIVED_AT,
    ) as (reader, authorization):
        run = reader.find_by_decision_ref(authorization, decision_ref)
    assert run is not None
    assert run.outcome is ContextRunOutcome.DELIVERED_AUTHORIZED
    assert run.authorized_evidence_refs == (evidence_ref,)
    assert run.decision_audit_category is None


def _persisted_decision_documents(
    engine: Engine,
    *,
    fixture: FieldProjectionFixture,
    decision_ref: str,
) -> tuple[str, str | None]:
    with engine.connect() as connection:
        run_document = connection.execute(
            text(
                """
                SELECT row_to_json(run)::text
                FROM context_run AS run
                WHERE organization_id = :organization_id
                  AND decision_ref = :decision_ref
                """
            ),
            {
                "organization_id": fixture.organization_id,
                "decision_ref": decision_ref,
            },
        ).scalar_one()
        audit_document = connection.execute(
            text(
                """
                SELECT row_to_json(audit)::text
                FROM decision_audit AS audit
                WHERE organization_id = :organization_id
                  AND decision_ref = :decision_ref
                """
            ),
            {
                "organization_id": fixture.organization_id,
                "decision_ref": decision_ref,
            },
        ).scalar_one_or_none()
    return run_document, audit_document


@pytest.mark.security_evidence(id="FIXTURE-ACCEPT-002", layer="runtime")
def test_accept_002_same_organization_memberships_receive_only_authorized_fields(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    record_property: Callable[[str, object], None],
) -> None:
    """ACCEPT-002: Membership field ceilings precede Kernel content projection."""

    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    index = SameContentFreeCandidateIndex(fixture.candidate)
    scope_authority = ExactResourceScopeAuthority(fixture.candidate)
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: RECEIVED_AT,
        query_digest_keyring=query_digest_keyring,
    )
    client = TestClient(
        create_app(
            authenticator=MembershipAuthenticator(fixture),
            organization_authority=ExactOrganizationAuthority(fixture.organization_id),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=scope_authority,
            runtime=runtime,
            clock=lambda: RECEIVED_AT,
        )
    )

    try:
        _seed_fixture(migration_engine, fixture)
        ensure_test_runtime_release(fixture.organization_id)

        full_response = _resolve(
            client,
            fixture.full,
            request_label="member-full",
        )
        full_decision_ref, full_evidence_ref = _assert_authorized_projection(
            full_response,
            fixture=fixture,
            expected_body=FULL_BLOCK,
            expected_fields=["status", "private_note"],
        )

        limited_response = _resolve(
            client,
            fixture.limited,
            request_label="member-limited-reusing-full-candidate",
        )
        limited_decision_ref, limited_evidence_ref = _assert_authorized_projection(
            limited_response,
            fixture=fixture,
            expected_body=STATUS_BLOCK,
            expected_fields=["status"],
        )
        limited_serialized = limited_response.text
        assert "private_note" not in limited_serialized
        assert "secret" not in limited_serialized

        assert len(index.calls) == 2
        assert all(call.need.query == QUERY for call in index.calls)
        assert index.returned_candidates == [fixture.candidate, fixture.candidate]
        assert index.returned_candidates[0] is index.returned_candidates[1]
        assert [identity.membership_id for identity in scope_authority.identities] == [
            fixture.full.membership_id,
            fixture.limited.membership_id,
        ]
        _assert_run_has_only_authorized_evidence_ref(
            control_engine=guarded_control_engine,
            operator_engine=guarded_operator_engine,
            fixture=fixture,
            decision_ref=limited_decision_ref,
            evidence_ref=limited_evidence_ref,
        )
        _assert_run_has_only_authorized_evidence_ref(
            control_engine=guarded_control_engine,
            operator_engine=guarded_operator_engine,
            fixture=fixture,
            decision_ref=full_decision_ref,
            evidence_ref=full_evidence_ref,
        )

        limited_run, limited_audit = _persisted_decision_documents(
            migration_engine,
            fixture=fixture,
            decision_ref=limited_decision_ref,
        )
        assert limited_audit is None
        for forbidden in (
            "status=open",
            "private_note",
            "secret",
            "field_ref",
            "field_value",
            "projectedFields",
        ):
            assert forbidden not in limited_run

        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM membership_resource_field_right
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                      AND membership_version = 7
                      AND resource_ref = :resource_ref
                    """
                ),
                {
                    "organization_id": fixture.organization_id,
                    "membership_id": fixture.limited.membership_id,
                    "resource_ref": fixture.candidate.resource_ref,
                },
            )

        missing_right_response = _resolve(
            client,
            fixture.limited,
            request_label="member-limited-missing-right",
        )
        assert missing_right_response.status_code == 200
        missing_document = missing_right_response.json()
        assert missing_document["package"]["blocks"] == []
        assert missing_document["package"]["evidence"] == []
        assert missing_document["package"]["coverage"] == {
            "status": "empty",
            "reason": "no_authorized_evidence",
        }
        assert all(
            forbidden not in missing_right_response.text
            for forbidden in (
                STATUS_BLOCK,
                "private_note",
                "secret",
                fixture.candidate.source_ref,
                fixture.candidate.resource_ref,
                fixture.candidate.revision_ref,
                fixture.candidate.fragment_ref,
            )
        )

        missing_decision_ref = missing_document["package"]["decisionRef"]
        with exact_test_context_run_operator_read(
            control_engine=guarded_control_engine,
            operator_engine=guarded_operator_engine,
            organization_id=fixture.organization_id,
            decision_ref=missing_decision_ref,
            request_id="test:accept-002:read:missing-right",
            opaque_credential="test:accept-002:credential:missing-right",
            authorized_at=RECEIVED_AT,
        ) as (reader, authorization):
            missing_run = reader.find_by_decision_ref(
                authorization,
                missing_decision_ref,
            )
        assert missing_run is not None
        assert missing_run.outcome is ContextRunOutcome.DELIVERED_EMPTY
        assert missing_run.authorized_evidence_refs == ()
        assert missing_run.decision_audit_category is (
            DecisionAuditCategory.NO_AUTHORIZED_EVIDENCE
        )
        missing_persisted_run, missing_persisted_audit = _persisted_decision_documents(
            migration_engine,
            fixture=fixture,
            decision_ref=missing_decision_ref,
        )
        assert missing_persisted_audit is not None
        missing_persisted = missing_persisted_run + missing_persisted_audit
        for forbidden in (
            STATUS_BLOCK,
            "private_note",
            "secret",
            "field_ref",
            "field_value",
            "projectedFields",
            fixture.candidate.source_ref,
            fixture.candidate.resource_ref,
            fixture.candidate.revision_ref,
            fixture.candidate.fragment_ref,
        ):
            assert forbidden not in missing_persisted
        assert len(index.calls) == 3
        response_documents = (
            full_response.json(),
            limited_response.json(),
            missing_document,
        )
        unauthorized_evidence_count = sum(
            forbidden in response.text
            for response in (limited_response, missing_right_response)
            for forbidden in ("secret",)
        )
        wrong_organization_effect_count = sum(
            "effects" in document for document in response_documents
        )
        missing_context_fallback_count = int(
            missing_document["package"]["coverage"]
            != {"status": "empty", "reason": "no_authorized_evidence"}
            or missing_document["package"]["blocks"] != []
            or missing_document["package"]["evidence"] != []
        )
        assert unauthorized_evidence_count == 0
        assert wrong_organization_effect_count == 0
        assert missing_context_fallback_count == 0
        record_security_oracles(
            record_property,
            fixture_ref="ACCEPT-002",
            unauthorized_evidence_count=unauthorized_evidence_count,
            wrong_organization_effect_count=wrong_organization_effect_count,
            missing_context_fallback_count=missing_context_fallback_count,
        )
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            client.close()
            migration_engine.dispose()


def test_concurrent_field_right_revoke_cannot_commit_before_delivery_transaction(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A projected field retains the Organization right lock through commit."""

    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    projected = Event()
    release_delivery = Event()
    original_persist = (
        membership_context_module._PostgreSQLContextRunPersistencePort.persist
    )

    def pause_after_projection(
        port: membership_context_module._PostgreSQLContextRunPersistencePort,
        run: ContextRunRecord,
        audit: DecisionAuditRecord | None,
    ) -> None:
        projected.set()
        if not release_delivery.wait(timeout=10):
            raise RuntimeError("field-right lock test was not released")
        original_persist(port, run, audit)

    monkeypatch.setattr(
        membership_context_module._PostgreSQLContextRunPersistencePort,
        "persist",
        pause_after_projection,
    )
    index = SameContentFreeCandidateIndex(fixture.candidate)
    client = TestClient(
        create_app(
            authenticator=MembershipAuthenticator(fixture),
            organization_authority=ExactOrganizationAuthority(
                fixture.organization_id
            ),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=ExactResourceScopeAuthority(fixture.candidate),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=cast(CandidateIndex, index),
                clock=lambda: RECEIVED_AT,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: RECEIVED_AT,
        )
    )

    def revoke_private_note() -> None:
        with migration_engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '250ms'"))
            connection.execute(
                text(
                    """
                    DELETE FROM membership_resource_field_right
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                      AND membership_version = 7
                      AND resource_ref = :resource_ref
                      AND field_ref = 'private_note'
                    """
                ),
                {
                    "organization_id": fixture.organization_id,
                    "membership_id": fixture.full.membership_id,
                    "resource_ref": fixture.candidate.resource_ref,
                },
            )

    try:
        _seed_fixture(migration_engine, fixture)
        ensure_test_runtime_release(fixture.organization_id)
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending_response = executor.submit(
                _resolve,
                client,
                fixture.full,
                request_label="member-full-locking-rights",
            )
            assert projected.wait(timeout=10)
            pending_revoke = executor.submit(revoke_private_note)
            with pytest.raises(OperationalError, match="lock timeout"):
                pending_revoke.result(timeout=10)
            release_delivery.set()
            response = pending_response.result(timeout=10)

        _assert_authorized_projection(
            response,
            fixture=fixture,
            expected_body=FULL_BLOCK,
            expected_fields=["status", "private_note"],
        )
        revoke_private_note()
        post_revoke = _resolve(
            client,
            fixture.full,
            request_label="member-full-after-right-revoke",
        )
        _assert_authorized_projection(
            post_revoke,
            fixture=fixture,
            expected_body=STATUS_BLOCK,
            expected_fields=["status"],
        )
    finally:
        release_delivery.set()
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            client.close()
            migration_engine.dispose()
