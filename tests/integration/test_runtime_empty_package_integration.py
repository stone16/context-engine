from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from adapters.http.app import create_app
from adapters.http.authentication import (
    AuthenticationRejected,
    VerifiedAuthenticationContext,
)
from adapters.http.organization_authority import OrganizationVerificationRejected
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.persistence.membership_context import (
    MembershipAuthorityUnavailable,
    MembershipIdentity,
)
from engine.runtime.actor import (
    CurrentMembershipVerification,
    MembershipRejectionAuditReceipt,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import RuntimeContentIo
from engine.runtime.context_run import (
    ContextRunOutcome,
    DecisionAuditCategory,
)
from engine.runtime.contracts import Acquire
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    _construct_existing_http_organization_verification,
)
from engine.runtime.package_digest import (
    QueryDigestKeyring,
    query_digest,
    verify_context_package_digest,
)
from tests.support.context_run_operator import exact_test_context_run_operator_read

pytestmark = pytest.mark.integration
TOKEN = "seeded-existing-organization"
RECEIVED_AT = datetime(2026, 7, 21, 5, 0, tzinfo=UTC)


class SeededAuthenticator:
    def __init__(
        self,
        organization_id: UUID,
        user_id: UUID,
        membership_id: UUID,
        membership_version: int = 1,
    ) -> None:
        self._organization_id = organization_id
        self._user_id = user_id
        self._membership_id = membership_id
        self._membership_version = membership_version

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        if opaque_credential != TOKEN:
            raise AuthenticationRejected
        return VerifiedAuthenticationContext(
            organization_ref=str(self._organization_id),
            user_ref=str(self._user_id),
            principal_ref="seeded-principal",
            membership_ref=str(self._membership_id),
            membership_version=self._membership_version,
            agent_version_ref="seeded-agent",
            authenticated_application_ref="seeded-application",
            authentication_binding_ref="seeded-binding",
        )


class SeededOrganizationAuthority:
    """Test authority whose registry is populated from a real inserted row."""

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


class ContentIoSpy:
    def __init__(self) -> None:
        self.calls = 0

    def discover(self, request: Acquire) -> tuple[()]:
        self.calls += 1
        return ()

    def authorize_and_project(self) -> tuple[()]:
        self.calls += 1
        return ()

    def read_content(self) -> tuple[()]:
        self.calls += 1
        return ()


class RollbackAfterResolveMembershipAuthority:
    """Inject a late transaction failure after Runtime persisted its run."""

    def __init__(self, delegate: PostgreSQLMembershipAuthority) -> None:
        self._delegate = delegate

    @contextmanager
    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> Iterator[CurrentMembershipVerification]:
        with self._delegate.current_user_actor(identity) as verification:
            yield verification
            raise MembershipAuthorityUnavailable(
                "injected failure before transaction commit"
            )


def test_seeded_existing_organization_reaches_http_empty_package(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    """Issue #11: a real active Membership reaches the empty Package path."""

    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            inserted = connection.execute(
                text(
                    """
                    INSERT INTO organization (organization_id)
                    VALUES (:organization_id)
                    RETURNING organization_id
                    """
                ),
                {"organization_id": organization_id},
            ).scalar_one()
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id,
                        membership_id,
                        user_id,
                        status,
                        membership_version,
                        valid_from,
                        valid_until
                    ) VALUES (
                        :organization_id,
                        :membership_id,
                        :user_id,
                        'active',
                        1,
                        :valid_from,
                        NULL
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
                    "valid_from": RECEIVED_AT,
                },
            )
        assert cast(UUID, inserted) == organization_id

        spy = ContentIoSpy()
        runtime = Runtime(
            required_kernel_dependencies(),
            content_io=RuntimeContentIo(
                index=spy,
                provider=spy,
                source_content=spy,
            ),
            clock=lambda: RECEIVED_AT,
            query_digest_keyring=query_digest_keyring,
        )
        client = TestClient(
            create_app(
                authenticator=SeededAuthenticator(
                    organization_id,
                    user_id,
                    membership_id,
                ),
                organization_authority=SeededOrganizationAuthority(organization_id),
                membership_authority=PostgreSQLMembershipAuthority(
                    guarded_runtime_engine
                ),
                runtime=runtime,
                clock=lambda: RECEIVED_AT,
            )
        )

        response = client.post(
            "/v1/context:resolve",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"kind": "acquire", "need": {"query": "real PG root"}},
        )

        assert response.status_code == 200
        package = response.json()["package"]
        assert package["organizationRef"] != str(organization_id)
        assert package["blocks"] == package["evidence"] == package["gaps"] == []
        assert package["coverage"] == {
            "status": "empty",
            "reason": "no_authorized_evidence",
        }
        package_document = dict(package)
        package_digest = package_document.pop("packageDigest")
        assert verify_context_package_digest(package_document, package_digest)

        rejected_authentication = client.post(
            "/v1/context:resolve",
            headers={"Authorization": "Bearer rejected-credential"},
            json={"kind": "acquire", "need": {"query": "not accepted"}},
        )
        rejected_injection = client.post(
            "/v1/context:resolve",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "kind": "acquire",
                "need": {"query": "not accepted"},
                "organizationRef": str(organization_id),
            },
        )
        assert rejected_authentication.status_code == 401
        assert rejected_injection.status_code == 422

        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM context_run "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM decision_audit "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                ).scalar_one()
                == 1
            )
        with exact_test_context_run_operator_read(
            control_engine=guarded_control_engine,
            operator_engine=guarded_operator_engine,
            organization_id=organization_id,
            decision_ref=package["decisionRef"],
            request_id="test:issue-19:empty-operator-read",
            opaque_credential="test:issue-19:operator",
            authorized_at=RECEIVED_AT,
        ) as (reader, authorization):
            run = reader.find_by_decision_ref(
                authorization,
                package["decisionRef"],
            )
        assert run is not None
        assert run.organization_id == organization_id
        assert run.decision_ref == package["decisionRef"]
        assert run.package_digest == package["packageDigest"]
        assert run.outcome is ContextRunOutcome.DELIVERED_EMPTY
        assert run.decision_audit_category is (
            DecisionAuditCategory.NO_AUTHORIZED_EVIDENCE
        )
        assert run.authorized_evidence_refs == ()
        assert run.package_retention_mode == "digest_only"
        assert run.user_id == user_id
        assert run.membership_id == membership_id
        assert run.membership_version == 1
        assert run.principal_ref == "seeded-principal"
        assert run.agent_version_ref == "seeded-agent"
        assert run.authenticated_application_ref == "seeded-application"
        assert run.authentication_binding_ref == "seeded-binding"
        assert run.purpose == "context.answer"
        assert run.policy_epoch == 1
        assert run.policy_snapshot_ref
        assert run.effective_scope_digest
        assert (
            run.query_digest
            == query_digest(
                query_digest_keyring,
                organization_id,
                "real PG root",
            ).value
        )
        assert run.usage_tokens == run.usage_provider_calls == 0
        assert run.usage_cost_microunits == run.usage_elapsed_ms == 0
        assert run.accepted_at == run.finalized_at == run.package_as_of == RECEIVED_AT
        assert run.package_expires_at > run.package_as_of
        serialized_run = repr(run).casefold()
        for forbidden in (
            "real pg root",
            "query_text",
            "candidate",
            "denied_count",
            "resource_ref",
        ):
            assert forbidden not in serialized_run
        assert spy.calls == 0

        rollback_client = TestClient(
            create_app(
                authenticator=SeededAuthenticator(
                    organization_id,
                    user_id,
                    membership_id,
                ),
                organization_authority=SeededOrganizationAuthority(organization_id),
                membership_authority=RollbackAfterResolveMembershipAuthority(
                    PostgreSQLMembershipAuthority(guarded_runtime_engine)
                ),
                runtime=runtime,
                clock=lambda: RECEIVED_AT,
            )
        )
        rejected_late_failure = rollback_client.post(
            "/v1/context:resolve",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"kind": "acquire", "need": {"query": "must roll back"}},
        )
        assert rejected_late_failure.status_code == 503
        assert rejected_late_failure.content == b'{"code":"service_unavailable"}'
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM context_run "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM decision_audit "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                ).scalar_one()
                == 1
            )

        with guarded_runtime_engine.connect() as connection:
            for setting_name in (
                "app.organization_id",
                "app.actor_kind",
                "app.user_id",
                "app.membership_id",
                "app.membership_version",
                "app.principal_ref",
                "app.request_id",
                "app.authentication_binding_ref",
                "app.checked_at",
            ):
                assert connection.execute(
                    text("SELECT current_setting(:setting_name, true)"),
                    {"setting_name": setting_name},
                ).scalar_one_or_none() in {None, ""}
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM decision_audit "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM context_run WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM membership
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                },
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    "DELETE FROM organization WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
        migration_engine.dispose()


@pytest.mark.security_evidence(id="RUNTIME-RLS-FAIL-CLOSED-003", layer="runtime")
def test_real_postgres_http_membership_matrix_is_generic_and_zero_io(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    """Issue #11 DoD: the full invalid matrix crosses HTTP and real RLS."""

    organization_a = uuid4()
    organization_b = uuid4()
    users = {
        category: uuid4()
        for category in (
            "active",
            "missing",
            "inactive",
            "expired",
            "revoked",
            "cross-organization",
            "stale-version",
            "not-yet-valid",
            "wrong-user",
        )
    }
    memberships = {category: uuid4() for category in users}
    migration_engine = create_database_engine(migration_configuration)
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
                    "organization_a": organization_a,
                    "organization_b": organization_b,
                },
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                [{"user_id": user_id} for user_id in users.values()],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id,
                        membership_id,
                        user_id,
                        status,
                        membership_version,
                        valid_from,
                        valid_until
                    ) VALUES (
                        :organization_id,
                        :membership_id,
                        :user_id,
                        :status,
                        1,
                        :valid_from,
                        :valid_until
                    )
                    """
                ),
                [
                    {
                        "organization_id": (
                            organization_b
                            if category == "cross-organization"
                            else organization_a
                        ),
                        "membership_id": memberships[category],
                        "user_id": users[category],
                        "status": (
                            category
                            if category in {"inactive", "revoked"}
                            else "active"
                        ),
                        "valid_from": (
                            RECEIVED_AT + timedelta(seconds=1)
                            if category == "not-yet-valid"
                            else RECEIVED_AT - timedelta(days=2)
                        ),
                        "valid_until": (
                            RECEIVED_AT - timedelta(seconds=1)
                            if category == "expired"
                            else None
                        ),
                    }
                    for category in (
                        "active",
                        "inactive",
                        "expired",
                        "revoked",
                        "cross-organization",
                        "stale-version",
                        "not-yet-valid",
                        "wrong-user",
                    )
                ],
            )

        spy = ContentIoSpy()
        runtime = Runtime(
            required_kernel_dependencies(),
            content_io=RuntimeContentIo(
                index=spy,
                provider=spy,
                source_content=spy,
            ),
            clock=lambda: RECEIVED_AT,
            query_digest_keyring=query_digest_keyring,
        )
        authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
        audit_receipts: list[MembershipRejectionAuditReceipt] = []
        responses: dict[str, tuple[int, bytes]] = {}
        for category in users:
            client = TestClient(
                create_app(
                    authenticator=SeededAuthenticator(
                        organization_a,
                        (
                            users["active"]
                            if category == "wrong-user"
                            else users[category]
                        ),
                        memberships[category],
                        2 if category == "stale-version" else 1,
                    ),
                    organization_authority=SeededOrganizationAuthority(organization_a),
                    membership_authority=authority,
                    membership_rejection_observer=audit_receipts.append,
                    runtime=runtime,
                    clock=lambda: RECEIVED_AT,
                )
            )
            response = client.post(
                "/v1/context:resolve",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"kind": "acquire", "need": {"query": category}},
            )
            responses[category] = (response.status_code, response.content)

        assert responses.pop("active")[0] == 200
        assert set(responses.values()) == {(401, b'{"code":"authentication_failed"}')}
        assert audit_receipts == [MembershipRejectionAuditReceipt()] * 8
        assert spy.calls == 0

        with guarded_runtime_engine.connect() as connection:
            for setting_name in (
                "app.organization_id",
                "app.actor_kind",
                "app.user_id",
                "app.membership_id",
                "app.membership_version",
                "app.principal_ref",
                "app.request_id",
                "app.authentication_binding_ref",
                "app.checked_at",
            ):
                assert connection.execute(
                    text("SELECT current_setting(:setting_name, true)"),
                    {"setting_name": setting_name},
                ).scalar_one_or_none() in {None, ""}
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM decision_audit
                    WHERE organization_id IN (:organization_a, :organization_b)
                    """
                ),
                {
                    "organization_a": organization_a,
                    "organization_b": organization_b,
                },
            )
            connection.execute(
                text(
                    """
                    DELETE FROM context_run
                    WHERE organization_id IN (:organization_a, :organization_b)
                    """
                ),
                {
                    "organization_a": organization_a,
                    "organization_b": organization_b,
                },
            )
            connection.execute(
                text(
                    """
                    DELETE FROM membership
                    WHERE organization_id IN (:organization_a, :organization_b)
                    """
                ),
                {
                    "organization_a": organization_a,
                    "organization_b": organization_b,
                },
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                [{"user_id": user_id} for user_id in users.values()],
            )
            connection.execute(
                text(
                    """
                    DELETE FROM organization
                    WHERE organization_id IN (:organization_a, :organization_b)
                    """
                ),
                {
                    "organization_a": organization_a,
                    "organization_b": organization_b,
                },
            )
        migration_engine.dispose()
