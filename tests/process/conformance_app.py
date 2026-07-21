"""Test-only listening API composition for the Issue #10 process smoke."""

from datetime import datetime
from uuid import UUID

from adapters.http.app import create_app
from adapters.http.authentication import (
    AuthenticationRejected,
    VerifiedAuthenticationContext,
)
from adapters.http.organization_authority import OrganizationVerificationRejected
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    _construct_existing_http_organization_verification,
)

PROCESS_VALID_TOKEN = "process-test-credential"
PROCESS_ORGANIZATION_REF = "81e18bca-86a1-478a-937d-7675c6fe69b0"


class ProcessTestAuthenticator:
    """Recognize one test credential; this module is never a product entrypoint."""

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        if opaque_credential != PROCESS_VALID_TOKEN:
            raise AuthenticationRejected
        return VerifiedAuthenticationContext(
            organization_ref=PROCESS_ORGANIZATION_REF,
            principal_ref="process-principal",
            membership_ref=None,
            agent_version_ref="process-agent-version",
            authenticated_application_ref="process-application",
            authentication_binding_ref="process-binding",
        )


class ProcessTestOrganizationAuthority:
    """Conformance registry containing exactly one known test Organization."""

    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        if authentication.organization_ref != PROCESS_ORGANIZATION_REF:
            raise OrganizationVerificationRejected
        return _construct_existing_http_organization_verification(
            organization_id=UUID(PROCESS_ORGANIZATION_REF),
            request_id=request_id,
            authentication_binding_ref=authentication.authentication_binding_ref,
            verified_at=verified_at,
        )


app = create_app(
    authenticator=ProcessTestAuthenticator(),
    organization_authority=ProcessTestOrganizationAuthority(),
)
