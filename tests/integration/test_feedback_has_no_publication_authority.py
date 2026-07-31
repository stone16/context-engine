from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from adapters.http.app import create_app
from adapters.http.authentication import VerifiedAuthenticationContext
from adapters.http.ui_api import PostgreSQLUiApi
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from tests.integration.test_context_run_schema import (
    LineageIdentity,
    insert_context_run,
)
from tests.integration.test_context_run_schema import (
    lineage_identity as _lineage_identity,
)
from tests.support.ui import authenticate_ui

pytestmark = pytest.mark.integration
TOKEN = "ui-feedback-evidence-token"
lineage_identity = _lineage_identity


class _Authenticator:
    def __init__(self, identity: LineageIdentity) -> None:
        self.identity = identity

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        assert opaque_credential == TOKEN
        return VerifiedAuthenticationContext(
            organization_ref=str(self.identity.organization_id),
            user_ref=str(self.identity.user_id),
            principal_ref="principal:issue-19",
            membership_ref=str(self.identity.membership_id),
            membership_version=1,
            agent_version_ref="agent:issue-19",
            authenticated_application_ref="application:issue-19",
            authentication_binding_ref="binding:issue-19",
        )


def test_feedback_has_no_publication_authority(
    lineage_identity: LineageIdentity,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            citation = {
                "evidenceRef": "ev_" + "6" * 64,
                "fragmentRef": "synthetic-fragment-feedback",
                "resourceRef": "synthetic-resource-feedback",
                "revisionRef": "synthetic-revision-feedback",
                "sourceRef": "synthetic-source-feedback",
            }
            insert_context_run(
                connection,
                lineage_identity,
                outcome="delivered_authorized",
                authorized_evidence_refs=(citation["evidenceRef"],),
                package_ref="pkg_" + "2" * 32,
                release_ref="rel_" + "4" * 64,
                release_generation=7,
                authorized_citation_lineage=(citation,),
            )
            release_count_before = connection.execute(
                text(
                    "SELECT count(*) FROM active_release_manifest "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": lineage_identity.organization_id},
            ).scalar_one()
        client = TestClient(
            create_app(
                authenticator=_Authenticator(lineage_identity),
                ui_bearer_token=TOKEN,
                ui_api=PostgreSQLUiApi(
                    PostgreSQLMembershipAuthority(guarded_runtime_engine),
                    None,
                    preview_key=b"f" * 32,
                    feedback_engine=guarded_runtime_engine,
                    clock=lambda: datetime.now(UTC),
                ),
            )
        )
        authenticate_ui(client, TOKEN)

        response = client.post(
            "/ui/feedback",
            content=(
                f"runRef={lineage_identity.run_ref}&rating=helpful&"
                "note=Lineage+was+clear"
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 200
        assert "Feedback recorded" in response.text
        with migration_engine.connect() as connection:
            feedback = connection.execute(
                text(
                    """
                    SELECT run_ref, rating, note
                    FROM context_feedback
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": lineage_identity.organization_id},
            ).one()
            release_count_after = connection.execute(
                text(
                    "SELECT count(*) FROM active_release_manifest "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": lineage_identity.organization_id},
            ).scalar_one()
            privileges = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            has_function_privilege(
                                'context_engine_runtime',
                                'context_runtime_capture_context_feedback'
                                '(uuid,text,text,uuid,uuid,bigint,text,text,text)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                'context_engine_control',
                                'context_runtime_capture_context_feedback'
                                '(uuid,text,text,uuid,uuid,bigint,text,text,text)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                'context_engine_release_operator',
                                'context_runtime_capture_context_feedback'
                                '(uuid,text,text,uuid,uuid,bigint,text,text,text)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                'context_engine_learning',
                                'context_learning_read_feedback_evidence'
                                '(uuid,text)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                'context_engine_runtime',
                                'context_learning_read_feedback_evidence'
                                '(uuid,text)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                'context_engine_control',
                                'context_learning_read_feedback_evidence'
                                '(uuid,text)',
                                'EXECUTE'
                            ),
                            has_function_privilege(
                                'context_engine_release_operator',
                                'context_learning_read_feedback_evidence'
                                '(uuid,text)',
                                'EXECUTE'
                            )
                        """
                    )
                ).one()
            )
        assert tuple(feedback) == (
            lineage_identity.run_ref,
            "helpful",
            "Lineage was clear",
        )
        assert release_count_after == release_count_before
        assert privileges == (True, False, False, True, False, False, False)
    finally:
        migration_engine.dispose()


def test_feedback_workflow_roles_cannot_issue_or_mutate_release_authority(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            privileges = {
                (row.role_name, row.relation_name, row.privilege_name)
                for row in connection.execute(
                    text(
                        """
                        SELECT role_name, relation_name, privilege_name
                        FROM (
                            VALUES
                            ('context_engine_learning',
                             'release_operator_grant', 'INSERT'),
                            ('context_engine_learning',
                             'release_operator_grant', 'UPDATE'),
                            ('context_engine_learning',
                             'active_release_manifest', 'INSERT'),
                            ('context_engine_learning',
                             'active_release_manifest', 'UPDATE'),
                            ('context_engine_runtime',
                             'release_operator_grant', 'INSERT'),
                            ('context_engine_control',
                             'release_operator_grant', 'INSERT')
                        ) AS requested(
                            role_name, relation_name, privilege_name
                        )
                        WHERE has_table_privilege(
                            role_name,
                            'public.' || relation_name,
                            privilege_name
                        )
                        """
                    )
                )
            }
            promotion_execute = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.routine_privileges
                    WHERE routine_schema = 'public'
                      AND routine_name = 'context_learning_promote_release'
                      AND grantee IN (
                          'context_engine_runtime', 'context_engine_control'
                      )
                      AND privilege_type = 'EXECUTE'
                    """
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert privileges == set()
    assert promotion_execute == 0
