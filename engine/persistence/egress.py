"""PostgreSQL one-shot redemption boundary for exact egress grants."""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence.role_guard import assert_egress_role
from engine.runtime.egress import (
    EGRESS_GRANT_DIGEST_PROFILE,
    EgressGrantAuthorityUnavailable,
    EgressGrantRedemption,
)


class PostgreSQLEgressGrantRedemptionAuthority:
    """Redeem through one function-only trusted egress login."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def redeem(self, redemption: EgressGrantRedemption) -> bool:
        if type(redemption) is not EgressGrantRedemption:
            raise TypeError("egress redemption has the wrong nominal type")
        try:
            with self._engine.begin() as connection:
                assert_egress_role(connection)
                accepted = connection.execute(
                    text(
                        """
                        SELECT context_egress_redeem_grant(
                            :organization_id, :grant_digest, :digest_profile,
                            :hop_kind, :package_digest, :payload_digest,
                            :purpose, :audience_digest, :policy_epoch,
                            :retention_policy_ref, :sensitivity_policy_ref,
                            :issuer_ref, :consumer_ref, :provider_ref,
                            :model_ref, :channel_ref, :destination_ref,
                            :region_ref, :profile_ref
                        )
                        """
                    ),
                    {
                        "organization_id": redemption.organization_id,
                        "grant_digest": redemption.grant_digest,
                        "digest_profile": EGRESS_GRANT_DIGEST_PROFILE,
                        "hop_kind": redemption.hop_kind,
                        "package_digest": bytes.fromhex(
                            redemption.package_digest
                        ),
                        "payload_digest": bytes.fromhex(redemption.payload_digest),
                        "purpose": redemption.purpose,
                        "audience_digest": bytes.fromhex(
                            redemption.audience_digest
                        ),
                        "policy_epoch": redemption.policy_epoch,
                        "retention_policy_ref": (
                            redemption.retention_policy_ref
                        ),
                        "sensitivity_policy_ref": (
                            redemption.sensitivity_policy_ref
                        ),
                        "issuer_ref": redemption.issuer_ref,
                        "consumer_ref": redemption.consumer_ref,
                        "provider_ref": redemption.provider_ref,
                        "model_ref": redemption.model_ref,
                        "channel_ref": redemption.channel_ref,
                        "destination_ref": redemption.destination_ref,
                        "region_ref": redemption.region_ref,
                        "profile_ref": redemption.profile_ref,
                    },
                ).scalar_one()
        except (AssertionError, SQLAlchemyError):
            raise EgressGrantAuthorityUnavailable from None
        return accepted is True
