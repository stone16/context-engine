from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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
from adapters.http.scope_authority import ScopeAuthorityIdentity
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    _construct_existing_http_organization_verification,
)
from engine.runtime.scope import ScopeSet, ScopeTarget
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)

pytestmark = pytest.mark.integration

TOKEN_PREFIX = "authorized-evidence-integration"
REQUEST_ID_PREFIX = "authorized-evidence-http-request"
RECEIVED_AT = datetime(2026, 7, 21, 11, 0, tzinfo=UTC)
ORG_A_AUTHORIZED_BODY = "ORG-A-AUTHORIZED-BODY"
ORG_A_DENIED_BODY = "ORG-A-DENIED-BODY"
ORG_B_AUTHORIZED_BODY = "ORG-B-AUTHORIZED-BODY"
ORG_B_DENIED_BODY = "ORG-B-DENIED-BODY"


@dataclass(frozen=True, slots=True)
class OrganizationEvidenceFixture:
    label: str
    organization_id: UUID
    user_id: UUID
    membership_id: UUID
    authorized: CandidateRef
    denied: CandidateRef
    authorized_body: str
    denied_body: str


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceFixture:
    org_a: OrganizationEvidenceFixture
    org_b: OrganizationEvidenceFixture


class SeededAuthenticator:
    def __init__(
        self,
        fixture: OrganizationEvidenceFixture,
        *,
        token: str,
    ) -> None:
        self._fixture = fixture
        self._token = token

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        if opaque_credential != self._token:
            raise AuthenticationRejected
        return VerifiedAuthenticationContext(
            organization_ref=str(self._fixture.organization_id),
            user_ref=str(self._fixture.user_id),
            principal_ref=f"principal:authorized-evidence:{self._fixture.label}",
            membership_ref=str(self._fixture.membership_id),
            membership_version=1,
            agent_version_ref=f"agent:authorized-evidence:{self._fixture.label}",
            authenticated_application_ref=(
                f"application:authorized-evidence:{self._fixture.label}"
            ),
            authentication_binding_ref=(
                f"binding:authorized-evidence:{self._fixture.label}"
            ),
        )


class SeededOrganizationAuthority:
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


class ExactScopeAuthority:
    def __init__(self, authorized: CandidateRef) -> None:
        self._authorized = authorized
        self.identities: list[ScopeAuthorityIdentity] = []

    @contextmanager
    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> Iterator[TrustedScopeSnapshot]:
        self.identities.append(identity)
        authorized = ScopeSet(
            frozenset(
                {
                    ScopeTarget(
                        identity.organization_id,
                        self._authorized.source_ref,
                        self._authorized.resource_ref,
                    )
                }
            )
        )
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
                organization_boundary=authorized,
                membership_rights=authorized,
                principal_grants=authorized,
                agent_ceiling=authorized,
                source_native_acl=authorized,
                resource_acl=authorized,
                purpose_policy=authorized,
            )
        finally:
            _close_scope_authority_scope(authority_scope)


class HostileCandidateIndex:
    """Rank denied and cross-Organization Candidates ahead of the allowed one."""

    def __init__(
        self,
        fixture: OrganizationEvidenceFixture,
        *,
        cross_organization: CandidateRef,
    ) -> None:
        self._ranked = (
            fixture.denied,
            cross_organization,
            fixture.authorized,
        )
        self.calls: list[Acquire] = []

    def discover(self, request: Acquire) -> tuple[CandidateRef, ...]:
        self.calls.append(request)
        return self._ranked


def _new_fixture() -> RuntimeEvidenceFixture:
    def candidate(
        candidate_organization_id: UUID,
        category: str,
    ) -> CandidateRef:
        return CandidateRef(
            organization_id=candidate_organization_id,
            source_ref=f"source:{category}:{uuid4()}",
            resource_ref=f"resource:{category}:{uuid4()}",
            revision_ref=str(uuid4()),
            fragment_ref=f"fragment:{category}:{uuid4()}",
        )

    def organization(
        label: str,
        *,
        authorized_body: str,
        denied_body: str,
    ) -> OrganizationEvidenceFixture:
        organization_id = uuid4()
        return OrganizationEvidenceFixture(
            label=label,
            organization_id=organization_id,
            user_id=uuid4(),
            membership_id=uuid4(),
            authorized=candidate(organization_id, f"{label}:authorized"),
            denied=candidate(organization_id, f"{label}:denied"),
            authorized_body=authorized_body,
            denied_body=denied_body,
        )

    return RuntimeEvidenceFixture(
        org_a=organization(
            "org-a",
            authorized_body=ORG_A_AUTHORIZED_BODY,
            denied_body=ORG_A_DENIED_BODY,
        ),
        org_b=organization(
            "org-b",
            authorized_body=ORG_B_AUTHORIZED_BODY,
            denied_body=ORG_B_DENIED_BODY,
        ),
    )


def _candidate_parameters(
    candidate: CandidateRef,
    *,
    ordinal: int,
    body: str,
) -> dict[str, object]:
    return {
        "organization_id": candidate.organization_id,
        "source_ref": candidate.source_ref,
        "resource_ref": candidate.resource_ref,
        "revision_id": UUID(candidate.revision_ref),
        "fragment_ref": candidate.fragment_ref,
        "ordinal": ordinal,
        "content": body,
    }


def _seed_fixture(
    engine: Engine,
    fixture: RuntimeEvidenceFixture,
) -> None:
    organizations = (fixture.org_a, fixture.org_b)
    candidates = (
        _candidate_parameters(
            fixture.org_a.authorized,
            ordinal=0,
            body=fixture.org_a.authorized_body,
        ),
        _candidate_parameters(
            fixture.org_a.denied,
            ordinal=0,
            body=fixture.org_a.denied_body,
        ),
        _candidate_parameters(
            fixture.org_b.authorized,
            ordinal=0,
            body=fixture.org_b.authorized_body,
        ),
        _candidate_parameters(
            fixture.org_b.denied,
            ordinal=0,
            body=fixture.org_b.denied_body,
        ),
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organization (organization_id) "
                "VALUES (:organization_id)"
            ),
            [
                {"organization_id": organization.organization_id}
                for organization in organizations
            ],
        )
        connection.execute(
            text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
            [{"user_id": organization.user_id} for organization in organizations],
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
            [
                {
                    "organization_id": organization.organization_id,
                    "membership_id": organization.membership_id,
                    "user_id": organization.user_id,
                    "valid_from": RECEIVED_AT - timedelta(days=1),
                }
                for organization in organizations
            ],
        )
        connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        connection.execute(
            text(
                """
                INSERT INTO context_resource (
                    organization_id,
                    resource_ref,
                    source_ref,
                    active_revision_id,
                    tombstoned
                ) VALUES (
                    :organization_id,
                    :resource_ref,
                    :source_ref,
                    :revision_id,
                    false
                )
                """
            ),
            candidates,
        )
        connection.execute(
            text(
                """
                INSERT INTO context_revision (
                    organization_id,
                    resource_ref,
                    revision_id
                ) VALUES (
                    :organization_id,
                    :resource_ref,
                    :revision_id
                )
                """
            ),
            candidates,
        )
        connection.execute(
            text(
                """
                INSERT INTO context_fragment (
                    organization_id,
                    resource_ref,
                    revision_id,
                    fragment_ref,
                    ordinal,
                    content
                ) VALUES (
                    :organization_id,
                    :resource_ref,
                    :revision_id,
                    :fragment_ref,
                    :ordinal,
                    :content
                )
                """
            ),
            candidates,
        )
        connection.execute(
            text(
                """
                INSERT INTO resource_access_policy (
                    organization_id,
                    resource_ref,
                    principal_ref,
                    access_version,
                    access_state,
                    revoked_at
                ) VALUES (
                    :organization_id,
                    :resource_ref,
                    :principal_ref,
                    1,
                    'allowed',
                    NULL
                )
                """
            ),
            [
                {
                    "organization_id": organization.organization_id,
                    "resource_ref": organization.authorized.resource_ref,
                    "principal_ref": (
                        f"principal:authorized-evidence:{organization.label}"
                    ),
                }
                for organization in organizations
            ],
        )


def _persistent_content_snapshot(
    engine: Engine,
    fixture: RuntimeEvidenceFixture,
) -> tuple[tuple[object, ...], ...]:
    with engine.connect() as connection:
        return tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT
                        resource.organization_id,
                        resource.source_ref,
                        resource.resource_ref,
                        resource.active_revision_id,
                        resource.tombstoned,
                        fragment.fragment_ref,
                        fragment.ordinal,
                        fragment.content
                    FROM context_resource AS resource
                    JOIN context_fragment AS fragment
                      ON fragment.organization_id = resource.organization_id
                     AND fragment.resource_ref = resource.resource_ref
                     AND fragment.revision_id = resource.active_revision_id
                    WHERE resource.organization_id IN (
                        :org_a_id,
                        :org_b_id
                    )
                    ORDER BY
                        resource.organization_id,
                        resource.resource_ref,
                        fragment.ordinal
                    """
                ),
                {
                    "org_a_id": fixture.org_a.organization_id,
                    "org_b_id": fixture.org_b.organization_id,
                },
            )
        )


def _cleanup_fixture(engine: Engine, fixture: RuntimeEvidenceFixture) -> None:
    organizations = {
        "org_a_id": fixture.org_a.organization_id,
        "org_b_id": fixture.org_b.organization_id,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE context_fragment "
                "DISABLE TRIGGER context_fragment_reject_mutation"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE context_revision "
                "DISABLE TRIGGER context_revision_reject_mutation"
            )
        )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM resource_access_policy
                    WHERE organization_id IN (
                        :org_a_id,
                        :org_b_id
                    )
                    """
                ),
                organizations,
            )
            connection.execute(
                text(
                    """
                    DELETE FROM context_fragment
                    WHERE organization_id IN (
                        :org_a_id,
                        :org_b_id
                    )
                    """
                ),
                organizations,
            )
            connection.execute(
                text(
                    """
                    DELETE FROM context_revision
                    WHERE organization_id IN (
                        :org_a_id,
                        :org_b_id
                    )
                    """
                ),
                organizations,
            )
            connection.execute(
                text(
                    """
                    DELETE FROM context_resource
                    WHERE organization_id IN (
                        :org_a_id,
                        :org_b_id
                    )
                    """
                ),
                organizations,
            )
            connection.execute(
                text(
                    """
                    DELETE FROM membership
                    WHERE (organization_id, membership_id) IN (
                        (:org_a_id, :org_a_membership_id),
                        (:org_b_id, :org_b_membership_id)
                    )
                    """
                ),
                {
                    **organizations,
                    "org_a_membership_id": fixture.org_a.membership_id,
                    "org_b_membership_id": fixture.org_b.membership_id,
                },
            )
            connection.execute(
                text(
                    """
                    DELETE FROM user_account
                    WHERE user_id IN (:org_a_user_id, :org_b_user_id)
                    """
                ),
                {
                    "org_a_user_id": fixture.org_a.user_id,
                    "org_b_user_id": fixture.org_b.user_id,
                },
            )
            connection.execute(
                text(
                    """
                    DELETE FROM organization
                    WHERE organization_id IN (
                        :org_a_id,
                        :org_b_id
                    )
                    """
                ),
                organizations,
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE context_revision "
                    "ENABLE TRIGGER context_revision_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE context_fragment "
                    "ENABLE TRIGGER context_fragment_reject_mutation"
                )
            )


def _candidate_wire_values(
    candidate: CandidateRef,
    *,
    body: str,
) -> tuple[str, ...]:
    return (
        body,
        candidate.source_ref,
        candidate.resource_ref,
        candidate.revision_ref,
        candidate.fragment_ref,
    )


def _assert_exact_authorized_http_resolve(
    *,
    active: OrganizationEvidenceFixture,
    other: OrganizationEvidenceFixture,
    guarded_runtime_engine: Engine,
) -> None:
    token = f"{TOKEN_PREFIX}:{active.label}"
    request_id = f"{REQUEST_ID_PREFIX}:{active.label}"
    index = HostileCandidateIndex(
        active,
        cross_organization=other.authorized,
    )
    scope_authority = ExactScopeAuthority(active.authorized)
    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=cast(CandidateIndex, index),
        clock=lambda: RECEIVED_AT,
    )
    client = TestClient(
        create_app(
            authenticator=SeededAuthenticator(active, token=token),
            organization_authority=SeededOrganizationAuthority(
                active.organization_id
            ),
            membership_authority=PostgreSQLMembershipAuthority(
                guarded_runtime_engine
            ),
            scope_authority=scope_authority,
            runtime=runtime,
            clock=lambda: RECEIVED_AT,
            request_id_factory=lambda: request_id,
        )
    )

    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"kind": "acquire", "need": {"query": "hostile rank"}},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-context-request-id"] == request_id
    assert len(index.calls) == 1
    assert len(scope_authority.identities) == 1
    identity = scope_authority.identities[0]
    assert identity.organization_id == active.organization_id
    assert identity.user_id == active.user_id
    assert identity.membership_id == active.membership_id
    assert identity.membership_version == 1

    response_document = response.json()
    assert "effects" not in response_document
    package = response_document["package"]
    assert "effects" not in package
    assert package["organizationRef"] not in {
        str(active.organization_id),
        str(other.organization_id),
    }
    assert package["purpose"] == "context.answer"
    assert package["asOf"] == RECEIVED_AT.isoformat().replace("+00:00", "Z")
    assert package["gaps"] == []
    assert package["budgetUsage"] == {
        "tokens": len(active.authorized_body.encode("utf-8")),
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }
    assert package["coverage"] == {"status": "sufficient"}
    assert len(package["blocks"]) == len(package["evidence"]) == 1

    block = package["blocks"][0]
    evidence = package["evidence"][0]
    assert block["text"] == active.authorized_body
    assert block["evidenceRefs"] == [evidence["evidenceRef"]]
    assert block["blockId"] == (
        f"block_{evidence['evidenceRef'].removeprefix('ev_')}"
    )
    assert sum(
        item["evidenceRefs"].count(evidence["evidenceRef"])
        for item in package["blocks"]
    ) == 1
    assert sum(
        item["evidenceRef"] == evidence["evidenceRef"]
        for item in package["evidence"]
    ) == 1
    assert evidence == {
        "evidenceRef": evidence["evidenceRef"],
        "sourceRef": active.authorized.source_ref,
        "resourceRef": active.authorized.resource_ref,
        "revisionRef": active.authorized.revision_ref,
        "fragmentRef": active.authorized.fragment_ref,
        "runRef": evidence["runRef"],
        "purpose": package["purpose"],
        "authorizationAsOf": package["asOf"],
        "decisionRef": package["decisionRef"],
        "policySnapshotRef": evidence["policySnapshotRef"],
        "policyEpoch": 1,
        "sourceDecisionRef": evidence["sourceDecisionRef"],
    }
    for lineage_ref in (
        evidence["runRef"],
        evidence["policySnapshotRef"],
        evidence["sourceDecisionRef"],
    ):
        assert isinstance(lineage_ref, str)
        assert lineage_ref

    response_text = response.text
    forbidden_values = (
        *_candidate_wire_values(active.denied, body=active.denied_body),
        *_candidate_wire_values(other.authorized, body=other.authorized_body),
        *_candidate_wire_values(other.denied, body=other.denied_body),
        str(active.organization_id),
        str(other.organization_id),
    )
    assert all(value not in response_text for value in forbidden_values)


def test_real_postgres_http_delivers_only_exact_authorized_evidence_bidirectionally(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    """Issue #13: both Organizations seal CandidateRef -> Kernel -> projection."""

    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        before = _persistent_content_snapshot(migration_engine, fixture)
        assert len(before) == 4

        _assert_exact_authorized_http_resolve(
            active=fixture.org_a,
            other=fixture.org_b,
            guarded_runtime_engine=guarded_runtime_engine,
        )
        _assert_exact_authorized_http_resolve(
            active=fixture.org_b,
            other=fixture.org_a,
            guarded_runtime_engine=guarded_runtime_engine,
        )

        after = _persistent_content_snapshot(migration_engine, fixture)
        assert after == before
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            migration_engine.dispose()
