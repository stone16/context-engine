from __future__ import annotations

import re
from datetime import timedelta
from uuid import UUID, uuid4

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
from tests.support.article_access_policy import (
    delete_article_policy_scenario,
    ingest_article,
    insert_organization,
    observe_source_acl,
    policy_epoch,
    set_tenant_default,
)
from tests.support.file_imports import NOW

pytestmark = pytest.mark.integration
TOKEN = "ui-policy-confirm-token"


class _Authenticator:
    def __init__(
        self,
        organization_id: UUID,
        user_id: UUID,
        membership_id: UUID,
    ) -> None:
        self.organization_id = organization_id
        self.user_id = user_id
        self.membership_id = membership_id

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        assert opaque_credential == TOKEN
        return VerifiedAuthenticationContext(
            organization_ref=str(self.organization_id),
            user_ref=str(self.user_id),
            principal_ref="principal:ui-policy",
            membership_ref=str(self.membership_id),
            membership_version=1,
            agent_version_ref="agent:ui-policy",
            authenticated_application_ref="application:ui-policy",
            authentication_binding_ref="binding:ui-policy",
        )


def test_no_implicit_policy_change(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
) -> None:
    migration_engine = create_database_engine(migration_configuration)
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    source_ref = f"source:ui-policy:{uuid4()}"
    resource_ref = f"resource:ui-policy:{uuid4()}"
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
        set_tenant_default(migration_engine, organization_id, "private")
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
        client = TestClient(
            create_app(
                authenticator=_Authenticator(
                    organization_id,
                    user_id,
                    membership_id,
                ),
                ui_bearer_token=TOKEN,
                ui_api=PostgreSQLUiApi(
                    PostgreSQLMembershipAuthority(guarded_runtime_engine),
                    guarded_control_engine,
                    preview_key=b"p" * 32,
                    clock=lambda: NOW,
                ),
            )
        )
        before = _policy_state(migration_engine, organization_id, resource_ref)

        viewed = client.post(
            "/ui/articles/view",
            content=f"resourceRef={resource_ref}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        previewed = client.post(
            "/ui/articles/preview",
            content=(f"resourceRef={resource_ref}&policyKind=organization&groupRefs="),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert viewed.status_code == 200
        assert previewed.status_code == 200
        assert "Preview · no historical change yet" in previewed.text
        assert _policy_state(migration_engine, organization_id, resource_ref) == before
        assert policy_epoch(migration_engine, organization_id) == before[1]

        cancelled = client.post(
            "/ui/articles/view",
            content=f"resourceRef={resource_ref}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert cancelled.status_code == 200
        assert _policy_state(migration_engine, organization_id, resource_ref) == before

        match = re.search(
            r'name="previewToken" value="([A-Za-z0-9_.-]+)"',
            previewed.text,
        )
        assert match is not None
        confirmed = client.post(
            "/ui/articles/confirm",
            content=f"previewToken={match.group(1)}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert confirmed.status_code == 200
        assert "Article policy changed" in confirmed.text
        after = _policy_state(migration_engine, organization_id, resource_ref)
        assert after == (before[0] + 1, before[1] + 1, "organization")

        replay = client.post(
            "/ui/articles/confirm",
            content=f"previewToken={match.group(1)}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert replay.status_code == 503
        assert _policy_state(migration_engine, organization_id, resource_ref) == after
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


def _policy_state(
    engine: Engine,
    organization_id: UUID,
    resource_ref: str,
) -> tuple[int, int, str | None]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT policy.policy_version, epoch.policy_epoch,
                       policy.policy_kind
                FROM article_access_policy AS policy
                JOIN organization_policy_epoch AS epoch
                  ON epoch.organization_id = policy.organization_id
                WHERE policy.organization_id = :organization_id
                  AND policy.resource_ref = :resource_ref
                """
            ),
            {
                "organization_id": organization_id,
                "resource_ref": resource_ref,
            },
        ).one()
    return row.policy_version, row.policy_epoch, row.policy_kind
