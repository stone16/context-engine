from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLEgressGrantRedemptionAuthority,
    create_database_engine,
)
from engine.persistence.membership_context import (
    MembershipIdentity,
    PostgreSQLMembershipAuthority,
)
from engine.runtime.egress import (
    ChannelEgressGrant,
    ChannelEgressProfile,
    EgressGrantIssue,
    EgressGrantRedemption,
    ModelEgressGrant,
    ModelEgressProfile,
    issue_egress_grant,
)

pytestmark = pytest.mark.integration


def _profile() -> ModelEgressProfile:
    return ModelEgressProfile(
        profile_ref="model-egress-integration-v1",
        retention_policy_ref="no-provider-retention-v1",
        sensitivity_policy_ref="internal-authorized-package-v1",
        issuer_ref="context-runtime-integration",
        consumer_ref="model-gateway-integration",
        provider_ref="provider-integration",
        model_ref="model-integration",
        region_ref="region-integration",
        maximum_ttl=timedelta(minutes=2),
    )


def _channel_profile() -> ChannelEgressProfile:
    return ChannelEgressProfile(
        profile_ref="channel-egress-integration-v1",
        retention_policy_ref="no-channel-retention-v1",
        sensitivity_policy_ref="internal-authorized-package-v1",
        issuer_ref="context-runtime-integration",
        consumer_ref="sender-preflight-integration",
        channel_ref="channel-integration",
        destination_ref="destination-integration",
        region_ref="region-integration",
        maximum_ttl=timedelta(minutes=2),
    )


@pytest.mark.security_evidence(id="PG-EGRESS-011", layer="postgres")
def test_digest_only_grant_is_atomic_one_shot_and_audited(
    guarded_runtime_engine: Engine,
    control_configuration: DatabaseConfiguration,
    identity_configuration: DatabaseConfiguration,
    egress_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    learning_configuration: DatabaseConfiguration,
    operator_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    other_organization_id, other_user_id, other_membership_id = (
        uuid4(),
        uuid4(),
        uuid4(),
    )
    package_digest = "1" * 64
    payload_digest = "2" * 64
    audience_digest = "3" * 64
    bearer = "egrm_" + "4" * 64
    stale_bearer = "egrm_" + "5" * 64
    expired_bearer = "egrm_" + "6" * 64
    channel_bearer = "egrc_" + "7" * 64
    other_bearer = "egrm_" + "9" * 64
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            application_roles = (
                control_configuration.expected_role,
                identity_configuration.expected_role,
                egress_configuration.expected_role,
                runtime_configuration.expected_role,
                worker_configuration.expected_role,
                learning_configuration.expected_role,
                operator_configuration.expected_role,
            )
            role_privileges = {
                role: tuple(
                    connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege(:role, 'egress_grant', 'SELECT'), "
                            "has_table_privilege(:role, 'egress_grant', 'UPDATE'), "
                            "has_table_privilege(:role, 'egress_audit', 'SELECT'), "
                            "has_function_privilege(:role, "
                            "'context_runtime_issue_egress_grant(uuid,bytea,text,"
                            "text,bytea,bytea,text,bytea,bigint,text,text,text,text,"
                            "text,text,text,text,text,timestamptz,timestamptz,text,"
                            "text,text)', 'EXECUTE'), "
                            "has_function_privilege(:role, "
                            "'context_egress_redeem_grant(uuid,bytea,text,text,"
                            "bytea,bytea,text,bytea,bigint,text,text,text,text,text,"
                            "text,text,text,text,text)', 'EXECUTE')"
                        ),
                        {"role": role},
                    ).one()
                )
                for role in application_roles
            }
            assert role_privileges[runtime_configuration.expected_role] == (
                False,
                False,
                False,
                True,
                False,
            )
            assert role_privileges[egress_configuration.expected_role] == (
                False,
                False,
                False,
                False,
                True,
            )
            for role in set(application_roles) - {
                runtime_configuration.expected_role,
                egress_configuration.expected_role,
            }:
                assert role_privileges[role] == (False,) * 5
            assert connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'egress_audit'::regclass "
                    "AND conname = 'pk_egress_audit'"
                )
            ).scalar_one() == "PRIMARY KEY (organization_id, audit_id)"

            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:org), (:other_org)"
                ),
                {"org": organization_id, "other_org": other_organization_id},
            )
            connection.execute(
                text(
                    "INSERT INTO user_account (user_id) "
                    "VALUES (:user), (:other_user)"
                ),
                {"user": user_id, "other_user": other_user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO membership (organization_id, membership_id, "
                    "user_id, status, membership_version, valid_from) VALUES "
                    "(:org, :membership, :user, 'active', 1, :valid_from)"
                ),
                {
                    "org": organization_id,
                    "membership": membership_id,
                    "user": user_id,
                    "valid_from": now - timedelta(minutes=1),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO membership (organization_id, membership_id, "
                    "user_id, status, membership_version, valid_from) VALUES "
                    "(:org, :membership, :user, 'active', 1, :valid_from)"
                ),
                {
                    "org": other_organization_id,
                    "membership": other_membership_id,
                    "user": other_user_id,
                    "valid_from": now - timedelta(minutes=1),
                },
            )

        authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
        identity = MembershipIdentity(
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            membership_version=1,
            principal_ref="principal:egress-integration",
            request_id="request:egress-integration",
            authentication_binding_ref="binding:egress-integration",
            checked_at=now,
        )
        issue = EgressGrantIssue.for_model(
            organization_id=organization_id,
            package_digest=package_digest,
            payload_digest=payload_digest,
            purpose="context.answer",
            audience_digest=audience_digest,
            policy_epoch=1,
            issued_at=now,
            expires_at=now + timedelta(minutes=1),
            profile=_profile(),
        )
        with authority.current_user_actor(identity) as actor:
            session = actor.egress_grant_issuance_session
            assert session is not None
            grant = issue_egress_grant(
                session,
                issue,
                reference_factory=lambda _prefix: bearer,
            )
            stale_grant = issue_egress_grant(
                session,
                issue,
                reference_factory=lambda _prefix: stale_bearer,
            )
            expired_grant = issue_egress_grant(
                session,
                EgressGrantIssue.for_model(
                    organization_id=organization_id,
                    package_digest=package_digest,
                    payload_digest=payload_digest,
                    purpose="context.answer",
                    audience_digest=audience_digest,
                    policy_epoch=1,
                    issued_at=now - timedelta(minutes=1),
                    expires_at=now + timedelta(seconds=30),
                    profile=_profile(),
                ),
                reference_factory=lambda _prefix: expired_bearer,
            )
            channel_grant = issue_egress_grant(
                session,
                EgressGrantIssue.for_channel(
                    organization_id=organization_id,
                    package_digest=package_digest,
                    payload_digest=payload_digest,
                    purpose="context.answer",
                    audience_digest=audience_digest,
                    policy_epoch=1,
                    issued_at=now,
                    expires_at=now + timedelta(minutes=1),
                    profile=_channel_profile(),
                ),
                reference_factory=lambda _prefix: channel_bearer,
            )
        assert type(grant) is ModelEgressGrant
        assert type(stale_grant) is ModelEgressGrant
        assert type(expired_grant) is ModelEgressGrant
        assert type(channel_grant) is ChannelEgressGrant
        other_identity = MembershipIdentity(
            organization_id=other_organization_id,
            user_id=other_user_id,
            membership_id=other_membership_id,
            membership_version=1,
            principal_ref="principal:other-egress-integration",
            request_id="request:other-egress-integration",
            authentication_binding_ref="binding:other-egress-integration",
            checked_at=now,
        )
        with authority.current_user_actor(other_identity) as actor:
            other_session = actor.egress_grant_issuance_session
            assert other_session is not None
            other_grant = issue_egress_grant(
                other_session,
                replace(issue, organization_id=other_organization_id),
                reference_factory=lambda _prefix: other_bearer,
            )
        assert type(other_grant) is ModelEgressGrant

        redemption = EgressGrantRedemption.for_model(
            grant=grant,
            organization_id=organization_id,
            package_digest=package_digest,
            payload_digest=payload_digest,
            purpose="context.answer",
            audience_digest=audience_digest,
            policy_epoch=1,
            profile=_profile(),
        )
        egress_engine = create_database_engine(egress_configuration)
        try:
            consumer = PostgreSQLEgressGrantRedemptionAuthority(egress_engine)
            mutations = (
                replace(
                    redemption,
                    grant_digest=ModelEgressGrant("egrm_" + "8" * 64).digest,
                ),
                replace(redemption, organization_id=other_organization_id),
                replace(redemption, package_digest="a" * 64),
                replace(redemption, payload_digest="b" * 64),
                replace(redemption, purpose="citation.open"),
                replace(redemption, audience_digest="c" * 64),
                replace(redemption, policy_epoch=2),
                replace(redemption, retention_policy_ref="retention-wrong"),
                replace(redemption, sensitivity_policy_ref="sensitivity-wrong"),
                replace(redemption, issuer_ref="issuer-wrong"),
                replace(redemption, consumer_ref="consumer-wrong"),
                replace(redemption, provider_ref="provider-wrong"),
                replace(redemption, model_ref="model-wrong"),
                replace(redemption, region_ref="region-wrong"),
                replace(redemption, profile_ref="profile-wrong"),
                replace(
                    redemption,
                    hop_kind="channel",
                    provider_ref=None,
                    model_ref=None,
                    channel_ref="channel-integration",
                    destination_ref="destination-integration",
                ),
            )
            assert all(consumer.redeem(mutation) is False for mutation in mutations)

            with ThreadPoolExecutor(max_workers=8) as executor:
                winners = tuple(executor.map(consumer.redeem, (redemption,) * 16))
            assert Counter(winners) == Counter({True: 1, False: 15})
            assert consumer.redeem(redemption) is False

            channel_redemption = EgressGrantRedemption.for_channel(
                grant=channel_grant,
                organization_id=organization_id,
                package_digest=package_digest,
                payload_digest=payload_digest,
                purpose="context.answer",
                audience_digest=audience_digest,
                policy_epoch=1,
                profile=_channel_profile(),
            )
            assert consumer.redeem(
                replace(channel_redemption, channel_ref="channel-wrong")
            ) is False
            assert consumer.redeem(
                replace(channel_redemption, destination_ref="destination-wrong")
            ) is False
            assert consumer.redeem(channel_redemption) is True

            other_redemption = replace(
                redemption,
                grant_digest=other_grant.digest,
                organization_id=other_organization_id,
            )
            assert consumer.redeem(other_redemption) is True

            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE egress_grant SET expires_at = "
                        "clock_timestamp() - interval '30 seconds' "
                        "WHERE organization_id = :org AND grant_digest = :digest"
                    ),
                    {
                        "org": organization_id,
                        "digest": expired_grant.digest,
                    },
                )
            expired_redemption = replace(
                redemption,
                grant_digest=expired_grant.digest,
            )
            assert consumer.redeem(expired_redemption) is False

            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE organization_policy_epoch SET policy_epoch = 2 "
                        "WHERE organization_id = :org"
                    ),
                    {"org": organization_id},
                )
            stale_redemption = replace(
                redemption,
                grant_digest=stale_grant.digest,
            )
            assert consumer.redeem(stale_redemption) is False
        finally:
            egress_engine.dispose()

        with migration_engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT grant_digest, payload_digest, consumed_at "
                    "FROM egress_grant WHERE organization_id = :org "
                    "AND grant_digest = :digest"
                ),
                {"org": organization_id, "digest": grant.digest},
            ).one()
            audit = connection.execute(
                text(
                    "SELECT grant_digest, payload_digest, category "
                    "FROM egress_audit WHERE organization_id = :org "
                    "ORDER BY recorded_at"
                ),
                {"org": organization_id},
            ).all()
        expected_digest = sha256(bearer.encode("utf-8")).digest()
        assert bytes(state.grant_digest) == expected_digest
        assert bytes(state.payload_digest) == bytes.fromhex(payload_digest)
        assert state.consumed_at is not None
        categories = Counter(row.category for row in audit)
        assert categories["issued"] == 4
        assert categories["consumed"] == 2
        assert categories["not_available"] >= 34
        assert set(categories) == {"issued", "consumed", "not_available"}
        assert bearer not in repr(state)
        assert bearer not in repr(audit)
        assert stale_bearer not in repr(audit)
        assert expired_bearer not in repr(audit)
        assert channel_bearer not in repr(audit)
        assert other_bearer not in repr(audit)
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM egress_audit WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text("DELETE FROM egress_grant WHERE organization_id = :org"),
                {"org": organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM egress_audit WHERE organization_id = :other_org"
                ),
                {"other_org": other_organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM egress_grant WHERE organization_id = :other_org"
                ),
                {"other_org": other_organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM membership WHERE organization_id IN (:org, :other_org)"
                ),
                {"org": organization_id, "other_org": other_organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM organization "
                    "WHERE organization_id IN (:org, :other_org)"
                ),
                {"org": organization_id, "other_org": other_organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM user_account WHERE user_id IN (:user, :other_user)"
                ),
                {"user": user_id, "other_user": other_user_id},
            )
        migration_engine.dispose()
