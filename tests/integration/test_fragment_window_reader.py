from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from adapters.fragment_window import PostgreSQLFragmentWindowReader
from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.membership_context import (
    MembershipIdentity,
    PostgreSQLMembershipAuthority,
)
from engine.runtime.construction import (
    AuthorizationKernel,
    required_kernel_dependencies,
)
from engine.runtime.evidence import (
    CandidateRef,
    EvidenceLineage,
    _close_authorization_kernel_scope,
    _construct_authorized_projection,
    _open_authorization_kernel_scope,
)
from engine.runtime.fragment_window import (
    FragmentWindowNotAvailable,
    FragmentWindowRequest,
)
from tests.integration.test_runtime_authorized_evidence_integration import (
    RECEIVED_AT,
    _cleanup_fixture,
    _new_fixture,
    _seed_fixture,
)

pytestmark = pytest.mark.integration


def _lineage() -> EvidenceLineage:
    return EvidenceLineage(
        run_ref="run:fragment-window",
        principal_ref="principal:authorized-evidence:org-a",
        purpose="context.answer",
        as_of=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
        decision_ref="decision:fragment-window",
        policy_snapshot_ref="policy:fragment-window",
        policy_epoch=1,
        source_acl_decision_ref="sourceacl:fragment-window",
    )


def _identity(
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
) -> MembershipIdentity:
    return MembershipIdentity(
        organization_id=organization_id,
        user_id=user_id,
        membership_id=membership_id,
        membership_version=1,
        principal_ref="principal:authorized-evidence:org-a",
        request_id="request:fragment-window",
        authentication_binding_ref="binding:fragment-window",
        checked_at=RECEIVED_AT,
    )


def test_real_postgres_window_is_same_article_current_revision_only(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    fixture = _new_fixture()
    article = fixture.org_a.authorized
    other_article = fixture.org_a.denied
    neighbor = CandidateRef(
        organization_id=article.organization_id,
        source_ref=article.source_ref,
        resource_ref=article.resource_ref,
        revision_ref=article.revision_ref,
        fragment_ref="fragment:authorized-neighbor",
    )
    superseded_revision = uuid4()
    superseded = CandidateRef(
        organization_id=article.organization_id,
        source_ref=article.source_ref,
        resource_ref=article.resource_ref,
        revision_ref=str(superseded_revision),
        fragment_ref="fragment:superseded",
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        _seed_fixture(migration_engine, fixture)
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 1, 'ORG-A-AUTHORIZED-NEIGHBOR'
                    )
                    """
                ),
                {
                    "organization_id": neighbor.organization_id,
                    "resource_ref": neighbor.resource_ref,
                    "revision_id": UUID(neighbor.revision_ref),
                    "fragment_ref": neighbor.fragment_ref,
                },
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
                {
                    "organization_id": superseded.organization_id,
                    "resource_ref": superseded.resource_ref,
                    "revision_id": superseded_revision,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content
                    ) VALUES (
                        :organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 0, 'SUPERSEDED-BODY-MUST-NOT-SURFACE'
                    )
                    """
                ),
                {
                    "organization_id": superseded.organization_id,
                    "resource_ref": superseded.resource_ref,
                    "revision_id": superseded_revision,
                    "fragment_ref": superseded.fragment_ref,
                },
            )

        reader = PostgreSQLFragmentWindowReader()
        kernel = AuthorizationKernel(required_kernel_dependencies())
        authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
        scope = _open_authorization_kernel_scope()
        try:
            anchor = _construct_authorized_projection(
                kernel_scope=scope,
                candidate_ref=article,
                body=fixture.org_a.authorized_body,
                projected_field_refs=("body",),
                lineage=_lineage(),
            )
            superseded_anchor = _construct_authorized_projection(
                kernel_scope=scope,
                candidate_ref=superseded,
                body="synthetic retained bytes",
                projected_field_refs=("body",),
                lineage=_lineage(),
            )
            with authority.current_projection_session(
                _identity(
                    fixture.org_a.organization_id,
                    fixture.org_a.user_id,
                    fixture.org_a.membership_id,
                )
            ) as projection_session:
                window = kernel.expand_fragment_window(
                    FragmentWindowRequest(
                        anchor=anchor,
                        before=0,
                        after=1,
                        expansion_candidates=(other_article,),
                    ),
                    reader=reader,
                    projection_session=projection_session,
                )
                with pytest.raises(
                    FragmentWindowNotAvailable,
                    match="Fragment window is not available",
                ):
                    kernel.expand_fragment_window(
                        FragmentWindowRequest(
                            anchor=superseded_anchor,
                            before=0,
                            after=1,
                        ),
                        reader=reader,
                        projection_session=projection_session,
                    )
        finally:
            _close_authorization_kernel_scope(scope)

        assert tuple(
            projection.candidate_ref for projection in window.projections
        ) == (article, neighbor)
        assert tuple(
            projection.projected_body for projection in window.projections
        ) == (
            fixture.org_a.authorized_body,
            "ORG-A-AUTHORIZED-NEIGHBOR",
        )
        assert window.reauthorization_refs == (other_article,)
        assert all(
            projection.candidate_ref.resource_ref == article.resource_ref
            and projection.candidate_ref.revision_ref == article.revision_ref
            for projection in window.projections
        )
        assert "ORG-A-DENIED-BODY" not in repr(window)
        assert "SUPERSEDED-BODY-MUST-NOT-SURFACE" not in repr(window)
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            migration_engine.dispose()
