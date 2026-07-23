"""PostgreSQL identity-side issuer for private DeliveryEvidenceRef records."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence.role_guard import assert_identity_role
from engine.runtime.delivery_evidence import (
    DELIVERY_EVIDENCE_DIGEST_PROFILE,
    DeliveryEvidenceAuthorityUnavailable,
    PrivateDeliveryEvidenceIssue,
    private_delivery_audience_digest,
)


class PostgreSQLDeliveryEvidenceIssuerPort:
    """Issue through one function-only identity login; bearer values stay in memory."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def issue_private(
        self,
        *,
        request: PrivateDeliveryEvidenceIssue,
        evidence_digest: bytes,
        audience_digest: str,
        logical_resolution_ref: str,
    ) -> bool:
        if audience_digest != private_delivery_audience_digest(request):
            raise DeliveryEvidenceAuthorityUnavailable
        try:
            with self._engine.begin() as connection:
                assert_identity_role(connection)
                accepted = connection.execute(
                    text(
                        """
                        SELECT context_identity_issue_private_delivery_evidence(
                            :organization_id, :evidence_digest,
                            :digest_profile, :authenticated_service_ref,
                            :authentication_binding_ref, :request_id,
                            :user_id, :membership_id, :membership_version,
                            :destination_ref, :consumer_ref, :purpose,
                            :audience_digest, :policy_epoch, :issued_at,
                            :expires_at, :logical_resolution_ref, :profile_ref
                        )
                        """
                    ),
                    {
                        "organization_id": request.organization_id,
                        "evidence_digest": evidence_digest,
                        "digest_profile": DELIVERY_EVIDENCE_DIGEST_PROFILE,
                        "authenticated_service_ref": (
                            request.authenticated_service_ref
                        ),
                        "authentication_binding_ref": (
                            request.authentication_binding_ref
                        ),
                        "request_id": request.request_id,
                        "user_id": request.user_id,
                        "membership_id": request.membership_id,
                        "membership_version": request.membership_version,
                        "destination_ref": request.destination_ref,
                        "consumer_ref": request.consumer_ref,
                        "purpose": request.purpose,
                        "audience_digest": audience_digest,
                        "policy_epoch": request.policy_epoch,
                        "issued_at": request.issued_at,
                        "expires_at": request.expires_at,
                        "logical_resolution_ref": logical_resolution_ref,
                        "profile_ref": request.profile_ref,
                    },
                ).scalar_one()
        except SQLAlchemyError:
            raise DeliveryEvidenceAuthorityUnavailable from None
        return accepted is True


class PostgreSQLDeliveryEvidenceRetentionPort:
    """Delete expired digest rows through the same restricted identity login."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def delete_expired_private(self, organization_id: UUID) -> int:
        try:
            with self._engine.begin() as connection:
                assert_identity_role(connection)
                deleted = connection.execute(
                    text(
                        "SELECT "
                        "context_identity_delete_expired_private_delivery_evidence("
                        ":organization_id)"
                    ),
                    {"organization_id": organization_id},
                ).scalar_one()
        except SQLAlchemyError:
            raise DeliveryEvidenceAuthorityUnavailable from None
        if type(deleted) is not int or deleted < 0:
            raise DeliveryEvidenceAuthorityUnavailable
        return deleted
