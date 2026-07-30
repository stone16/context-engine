from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from adapters.http.app import create_app
from adapters.http.authentication import VerifiedAuthenticationContext
from adapters.http.ui_api import PostgreSQLUiApi
from engine.control import ControlOperation
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLMembershipAuthority,
    create_database_engine,
)
from tests.support.article_access_policy import (
    delete_article_policy_scenario,
    ingest_article,
    insert_organization,
    observe_source_acl,
    set_source_default,
    set_tenant_default,
)
from tests.support.file_imports import NOW
from tests.support.ui import authenticate_ui, ui_control_authority

pytestmark = pytest.mark.integration
TOKEN = "ui-visibility-token"
CONTROL_TOKEN = "ui-visibility-control-token"


class _Authenticator:
    def __init__(
        self, *, organization_id: object, user_id: object, membership_id: object
    ):
        self.organization_id = organization_id
        self.user_id = user_id
        self.membership_id = membership_id

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        assert opaque_credential == TOKEN
        return VerifiedAuthenticationContext(
            organization_ref=str(self.organization_id),
            user_ref=str(self.user_id),
            principal_ref="principal:ui-visibility",
            membership_ref=str(self.membership_id),
            membership_version=1,
            agent_version_ref="agent:ui-visibility",
            authenticated_application_ref="application:ui-visibility",
            authentication_binding_ref="binding:ui-visibility",
        )


def test_visibility_view_shows_rung(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
) -> None:
    migration_engine = create_database_engine(migration_configuration)
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    articles = {
        "explicit_article": (
            f"source:explicit:{uuid4()}",
            f"resource:explicit:{uuid4()}",
        ),
        "source_default": (f"source:default:{uuid4()}", f"resource:default:{uuid4()}"),
        "tenant_default": (f"source:tenant:{uuid4()}", f"resource:tenant:{uuid4()}"),
        "isolation": (f"source:isolation:{uuid4()}", f"resource:isolation:{uuid4()}"),
    }
    try:
        insert_organization(migration_engine, organization_id)
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from
                    ) VALUES (
                        :organization_id, :membership_id, :user_id,
                        'active', 1, :valid_from
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
                    "valid_from": NOW - timedelta(days=1),
                },
            )
            explicit_source, explicit_resource = articles["explicit_article"]
            connection.execute(
                text(
                    """
                    INSERT INTO article_explicit_policy_setting (
                        organization_id, source_ref, resource_ref,
                        policy_kind, group_refs
                    ) VALUES (
                        :organization_id, :source_ref, :resource_ref,
                        'private', ARRAY[]::text[]
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "source_ref": explicit_source,
                    "resource_ref": explicit_resource,
                },
            )
        set_tenant_default(migration_engine, organization_id, "organization")
        source_source, _ = articles["source_default"]
        set_source_default(migration_engine, organization_id, source_source, "private")
        for rung in ("explicit_article", "source_default", "tenant_default"):
            source_ref, resource_ref = articles[rung]
            observe_source_acl(
                migration_engine,
                organization_id=organization_id,
                source_ref=source_ref,
                resource_ref=resource_ref,
            )
            ingest_article(
                migration_engine,
                organization_id=organization_id,
                source_ref=source_ref,
                resource_ref=resource_ref,
            )
        set_tenant_default(migration_engine, organization_id, None)
        isolation_source, isolation_resource = articles["isolation"]
        observe_source_acl(
            migration_engine,
            organization_id=organization_id,
            source_ref=isolation_source,
            resource_ref=isolation_resource,
        )
        ingest_article(
            migration_engine,
            organization_id=organization_id,
            source_ref=isolation_source,
            resource_ref=isolation_resource,
        )
        control_authority, control_gate = ui_control_authority(
            organization_id=organization_id,
            credential=CONTROL_TOKEN,
            operations=frozenset({ControlOperation.READ_ARTICLE_POLICY}),
            clock=lambda: NOW,
        )
        api = PostgreSQLUiApi(
            PostgreSQLMembershipAuthority(guarded_runtime_engine),
            guarded_control_engine,
            preview_key=b"v" * 32,
            control_gate=control_gate,
            clock=lambda: NOW,
        )
        client = TestClient(
            create_app(
                authenticator=_Authenticator(
                    organization_id=organization_id,
                    user_id=user_id,
                    membership_id=membership_id,
                ),
                ui_bearer_token=TOKEN,
                ui_control_authority=control_authority,
                ui_api=api,
            )
        )
        authenticate_ui(client, TOKEN)

        for expected_rung, (_, resource_ref) in articles.items():
            response = client.post(
                "/ui/articles/view",
                content=(
                    f"resourceRef={resource_ref}&controlCredential={CONTROL_TOKEN}"
                ),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert response.status_code == 200
            assert CONTROL_TOKEN not in response.text
            assert f'<span class="status">{expected_rung}</span>' in response.text
            assert resource_ref in response.text
            assert ("Effective policy</dt><dd>isolation" in response.text) is (
                expected_rung == "isolation"
            )
    finally:
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM membership "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
                connection.execute(
                    text("DELETE FROM user_account WHERE user_id = :user_id"),
                    {"user_id": user_id},
                )
        finally:
            migration_engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)
