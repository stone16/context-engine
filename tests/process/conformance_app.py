"""Test-only listening API composition for the current process smoke."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from adapters.http.app import create_app
from adapters.http.authentication import (
    AuthenticationRejected,
    VerifiedAuthenticationContext,
)
from adapters.http.organization_authority import OrganizationVerificationRejected
from engine.persistence import MembershipIdentity, MembershipNotCurrent
from engine.runtime.actor import (
    CurrentMembershipVerification,
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    _construct_existing_http_organization_verification,
)
from engine.runtime.policy_epoch import (
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
)

PROCESS_VALID_TOKEN = "process-test-credential"
PROCESS_ORGANIZATION_REF = "81e18bca-86a1-478a-937d-7675c6fe69b0"
PROCESS_USER_REF = "d3d9893f-82d2-4890-8cb2-4c7e57a56f16"
PROCESS_MEMBERSHIP_REF = "9c9e9f4c-a5ec-4417-9408-0346e1c6c998"


class ProcessTestAuthenticator:
    """Recognize one test credential; this module is never a product entrypoint."""

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        if opaque_credential != PROCESS_VALID_TOKEN:
            raise AuthenticationRejected
        return VerifiedAuthenticationContext(
            organization_ref=PROCESS_ORGANIZATION_REF,
            user_ref=PROCESS_USER_REF,
            principal_ref="process-principal",
            membership_ref=PROCESS_MEMBERSHIP_REF,
            membership_version=1,
            agent_version_ref="process-agent-version",
            authenticated_application_ref="process-application",
            authentication_binding_ref="process-binding",
        )


class ProcessTestMembershipAuthority:
    """One active conformance Membership with a request-lived proof."""

    @contextmanager
    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> Iterator[CurrentMembershipVerification]:
        if (
            identity.organization_id != UUID(PROCESS_ORGANIZATION_REF)
            or identity.user_id != UUID(PROCESS_USER_REF)
            or identity.membership_id != UUID(PROCESS_MEMBERSHIP_REF)
            or identity.membership_version != 1
        ):
            raise MembershipNotCurrent
        scope = _open_membership_authority_scope()
        policy_epoch_scope = _open_policy_epoch_authority_scope()

        class CurrentEpochPort:
            def read_current_epoch(self, organization_id: UUID) -> object:
                assert organization_id == identity.organization_id
                return 1

        try:
            verification = _observe_current_policy_epoch(
                _construct_policy_epoch_session(
                    authority_scope=policy_epoch_scope,
                    organization_id=identity.organization_id,
                    port=CurrentEpochPort(),
                )
            )
            yield _construct_current_membership_verification(
                authority_scope=scope,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                membership_version=identity.membership_version,
                principal_ref=identity.principal_ref,
                request_id=identity.request_id,
                authentication_binding_ref=identity.authentication_binding_ref,
                checked_at=identity.checked_at,
                policy_epoch_verification=verification,
            )
        finally:
            _close_policy_epoch_authority_scope(policy_epoch_scope)
            _close_membership_authority_scope(scope)


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
    membership_authority=ProcessTestMembershipAuthority(),
)
