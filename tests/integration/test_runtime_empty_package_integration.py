from __future__ import annotations

from datetime import UTC, datetime
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
from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import RuntimeContentIo
from engine.runtime.contracts import Acquire
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    _construct_existing_http_organization_verification,
)

pytestmark = pytest.mark.integration
TOKEN = "seeded-existing-organization"
RECEIVED_AT = datetime(2026, 7, 21, 5, 0, tzinfo=UTC)


class SeededAuthenticator:
    def __init__(self, organization_id: UUID) -> None:
        self._organization_id = organization_id

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        if opaque_credential != TOKEN:
            raise AuthenticationRejected
        return VerifiedAuthenticationContext(
            organization_ref=str(self._organization_id),
            principal_ref="seeded-principal",
            membership_ref=None,
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


def test_seeded_existing_organization_reaches_http_empty_package(
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    """Issue #10: real root existence plus HTTP/Runtime zero-content path."""

    organization_id = uuid4()
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
        )
        client = TestClient(
            create_app(
                authenticator=SeededAuthenticator(organization_id),
                organization_authority=SeededOrganizationAuthority(organization_id),
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
        assert spy.calls == 0

        with guarded_runtime_engine.connect() as connection:
            assert connection.execute(
                text("SELECT current_setting('app.organization_id', true)")
            ).scalar_one_or_none() in {None, ""}
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM organization WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )
        migration_engine.dispose()
