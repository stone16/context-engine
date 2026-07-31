from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from adapters.http.app import create_app
from adapters.http.ui_api import PostgreSQLUiApi
from engine.learning.feedback import FeedbackBindingUnavailable
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLFeedbackInbox,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from tests.integration.test_context_run_schema import (
    LineageIdentity,
    current_user_actor,
    insert_context_run,
)
from tests.integration.test_context_run_schema import (
    lineage_identity as _lineage_identity,
)
from tests.integration.test_feedback_has_no_publication_authority import (
    TOKEN,
    _Authenticator,
)
from tests.support.ui import authenticate_ui

pytestmark = pytest.mark.integration
lineage_identity = _lineage_identity


def _capture(
    identity: LineageIdentity,
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> str:
    client = TestClient(
        create_app(
            authenticator=_Authenticator(identity),
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
            f"runRef={identity.run_ref}&rating=not_helpful&"
            "note=synthetic-feedback-note"
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            feedback_ref = connection.execute(
                text(
                    "SELECT feedback_ref FROM context_feedback "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            ).scalar_one()
            assert isinstance(feedback_ref, str)
            return feedback_ref
    finally:
        migration_engine.dispose()


def test_captured_feedback_resolves_only_from_exact_authorized_run_lineage(
    lineage_identity: LineageIdentity,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    guarded_learning_engine: Engine,
) -> None:
    citation = {
        "evidenceRef": "ev_" + "6" * 64,
        "fragmentRef": "synthetic-fragment-feedback",
        "resourceRef": "synthetic-resource-feedback",
        "revisionRef": "synthetic-revision-feedback",
        "sourceRef": "synthetic-source-feedback",
    }
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
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

    feedback_ref = _capture(
        lineage_identity,
        guarded_runtime_engine,
        migration_configuration,
    )
    item = PostgreSQLFeedbackInbox(guarded_learning_engine).find_exact(
        lineage_identity.organization_id,
        feedback_ref,
    )

    assert item.feedback_ref == feedback_ref
    assert item.binding.run_ref == lineage_identity.run_ref
    assert item.binding.package_ref == "pkg_" + "2" * 32
    assert item.binding.release_generation == 7
    assert item.binding.citations[0].evidence_ref == citation["evidenceRef"]
    assert not hasattr(item, "denied_details")


def test_learning_refuses_feedback_from_an_empty_or_legacy_unbindable_run(
    lineage_identity: LineageIdentity,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    guarded_learning_engine: Engine,
) -> None:
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_context_run(connection, lineage_identity)

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
            "note=synthetic-empty-run-feedback"
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            feedback_ref = connection.execute(
                text(
                    "SELECT feedback_ref FROM context_feedback "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": lineage_identity.organization_id},
            ).scalar_one()
    finally:
        migration_engine.dispose()
    with pytest.raises(FeedbackBindingUnavailable):
        PostgreSQLFeedbackInbox(guarded_learning_engine).find_exact(
            lineage_identity.organization_id,
            feedback_ref,
        )


def test_learning_inbox_cannot_read_a_different_organization(
    lineage_identity: LineageIdentity,
    guarded_learning_engine: Engine,
) -> None:
    with pytest.raises(FeedbackBindingUnavailable):
        PostgreSQLFeedbackInbox(guarded_learning_engine).find_exact(
            lineage_identity.organization_id,
            "fb_" + "0" * 64,
        )


def test_learning_and_other_processes_have_no_direct_feedback_table_read(
    guarded_learning_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
) -> None:
    for engine in (
        guarded_learning_engine,
        guarded_runtime_engine,
        guarded_control_engine,
    ):
        with (
            engine.begin() as connection,
            pytest.raises(ProgrammingError, match="permission denied"),
        ):
            connection.execute(text("SELECT * FROM context_feedback"))
