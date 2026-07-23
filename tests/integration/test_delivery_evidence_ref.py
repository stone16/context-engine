from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLDeliveryEvidenceIssuerPort,
    PostgreSQLDeliveryEvidenceRetentionPort,
    create_database_engine,
)
from engine.persistence.membership_context import (
    MembershipIdentity,
    PostgreSQLMembershipAuthority,
)
from engine.runtime.delivery_evidence import (
    DeliveryEvidenceNotAvailable,
    DeliveryEvidenceProfile,
    PrivateDeliveryEvidenceIssue,
    PrivateDeliveryEvidenceIssuer,
    PrivateDeliveryEvidenceRedemption,
    PrivateDeliveryEvidenceRetention,
    private_delivery_audience_digest,
    redeem_private_delivery_evidence,
)

pytestmark = pytest.mark.integration
NOW = datetime.now(UTC).replace(microsecond=0)
ROOT = Path(__file__).parents[2]


def _delete_delivery_evidence(
    migration_engine: Engine,
    organization_id: object,
) -> None:
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM delivery_evidence WHERE organization_id = :organization_id"
            ),
            {"organization_id": organization_id},
        )


def test_identity_issues_digest_only_and_runtime_redeems_one_stable_private_request(
    identity_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO membership (organization_id, membership_id, "
                    "user_id, status, membership_version, valid_from) VALUES "
                    "(:organization_id, :membership_id, :user_id, 'active', "
                    "1, :valid_from)"
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
                    "valid_from": NOW - timedelta(minutes=1),
                },
            )
        issuer = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(
                create_database_engine(identity_configuration)
            ),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=5),
            ),
            reference_factory=lambda: "der_" + "7" * 64,
            resolution_ref_factory=lambda: "dlr_" + "8" * 32,
        )
        issue = PrivateDeliveryEvidenceIssue(
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            membership_version=1,
            authenticated_service_ref="application:private-bot",
            authentication_binding_ref="binding:private-bot",
            request_id="delivery-request-1",
            destination_ref="private-chat:same-label",
            consumer_ref="consumer:private-bot",
            purpose="context.answer",
            policy_epoch=1,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=2),
        )
        issued = issuer.issue_private(issue)

        identity = MembershipIdentity(
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            membership_version=1,
            principal_ref="principal:private-user",
            request_id="delivery-request-1",
            authentication_binding_ref="binding:private-bot",
            checked_at=NOW + timedelta(seconds=1),
        )
        authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
        with authority.current_user_actor(identity) as verification:
            session = verification.delivery_evidence_redemption_session
            assert session is not None
            request = PrivateDeliveryEvidenceRedemption(
                evidence_ref=issued.evidence_ref,
                evidence_digest=bytes.fromhex(
                    "9a5ccd1ad58cc2d326ed6f191be606975d3d70f0f08bb7e04b5f265b4d9e4f7a"
                ),
                authenticated_service_ref="application:private-bot",
                authentication_binding_ref="binding:private-bot",
                request_id="delivery-request-1",
                organization_id=organization_id,
                user_id=user_id,
                membership_id=membership_id,
                membership_version=1,
                destination_ref=issue.destination_ref,
                consumer_ref=issue.consumer_ref,
                delivery_kind="private",
                audience_digest=private_delivery_audience_digest(issue),
                purpose="context.answer",
                policy_epoch=1,
                redeemed_at=NOW + timedelta(seconds=1),
            )
            first = redeem_private_delivery_evidence(session, request)
            retry = redeem_private_delivery_evidence(session, request)
        assert first == retry
        assert first.logical_resolution_ref == "dlr_" + "8" * 32

        with migration_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT evidence_digest, first_redeemed_at "
                    "FROM delivery_evidence "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            ).one()
        assert bytes(row.evidence_digest) == bytes.fromhex(
            "9a5ccd1ad58cc2d326ed6f191be606975d3d70f0f08bb7e04b5f265b4d9e4f7a"
        )
        assert row.first_redeemed_at == NOW + timedelta(seconds=1)
        assert issued.evidence_ref not in repr(row)
        with pytest.raises(
            SQLAlchemyError,
            match="cannot downgrade with delivery evidence rows",
        ):
            command.downgrade(Config(ROOT / "alembic.ini"), "20260723_0018")
    finally:
        _delete_delivery_evidence(migration_engine, organization_id)
        migration_engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    [
        "reference",
        "service",
        "binding",
        "request",
        "organization",
        "user",
        "membership",
        "membership_version",
        "destination",
        "consumer",
        "kind",
        "audience",
        "purpose",
        "policy_epoch",
        "expiry",
    ],
)
def test_private_evidence_one_field_mutations_are_equivalent_not_available(
    mutation: str,
    identity_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
) -> None:
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO membership (organization_id, membership_id, "
                    "user_id, status, membership_version, valid_from) VALUES "
                    "(:organization_id, :membership_id, :user_id, 'active', "
                    "1, :valid_from)"
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
                    "valid_from": NOW - timedelta(minutes=1),
                },
            )
        issuer = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(
                create_database_engine(identity_configuration)
            ),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=5),
            ),
            reference_factory=lambda: "der_"
            + sha256(f"delivery-{mutation}".encode()).hexdigest(),
            resolution_ref_factory=lambda: "dlr_"
            + sha256(f"resolution-{mutation}".encode()).hexdigest()[:32],
        )
        issue = PrivateDeliveryEvidenceIssue(
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            membership_version=1,
            authenticated_service_ref="application:private-bot",
            authentication_binding_ref="binding:private-bot",
            request_id="delivery-mutation",
            destination_ref="private-chat:same-label",
            consumer_ref="consumer:private-bot",
            purpose="context.answer",
            policy_epoch=1,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=2),
        )
        issued = issuer.issue_private(issue)
        checked_at = NOW + timedelta(seconds=1)
        membership_identity = MembershipIdentity(
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            membership_version=1,
            principal_ref="principal:private-user",
            request_id="delivery-mutation",
            authentication_binding_ref="binding:private-bot",
            checked_at=checked_at,
        )
        authority = PostgreSQLMembershipAuthority(guarded_runtime_engine)
        with authority.current_user_actor(membership_identity) as verification:
            session = verification.delivery_evidence_redemption_session
            assert session is not None
            values: dict[str, object] = {
                "evidence_ref": issued.evidence_ref,
                "evidence_digest": sha256(issued.evidence_ref.encode()).digest(),
                "authenticated_service_ref": "application:private-bot",
                "authentication_binding_ref": "binding:private-bot",
                "request_id": "delivery-mutation",
                "organization_id": organization_id,
                "user_id": user_id,
                "membership_id": membership_id,
                "membership_version": 1,
                "destination_ref": issue.destination_ref,
                "consumer_ref": issue.consumer_ref,
                "delivery_kind": "private",
                "audience_digest": private_delivery_audience_digest(issue),
                "purpose": "context.answer",
                "policy_epoch": 1,
                "redeemed_at": checked_at,
            }
            replacements: dict[str, object] = {
                "reference": "forged",
                "service": "application:wrong",
                "binding": "binding:wrong",
                "request": "other-request",
                "organization": uuid4(),
                "user": uuid4(),
                "membership": uuid4(),
                "membership_version": 2,
                "destination": "private-chat:wrong",
                "consumer": "consumer:wrong",
                "kind": "group",
                "audience": "0" * 64,
                "purpose": "citation.open",
                "policy_epoch": 2,
                "expiry": NOW + timedelta(minutes=3),
            }
            fields = {
                "reference": "evidence_ref",
                "service": "authenticated_service_ref",
                "binding": "authentication_binding_ref",
                "request": "request_id",
                "organization": "organization_id",
                "user": "user_id",
                "membership": "membership_id",
                "membership_version": "membership_version",
                "destination": "destination_ref",
                "consumer": "consumer_ref",
                "kind": "delivery_kind",
                "audience": "audience_digest",
                "purpose": "purpose",
                "policy_epoch": "policy_epoch",
                "expiry": "redeemed_at",
            }
            values[fields[mutation]] = replacements[mutation]
            if mutation == "reference":
                values["evidence_digest"] = sha256(b"forged").digest()
            with pytest.raises(DeliveryEvidenceNotAvailable):
                redeem_private_delivery_evidence(
                    session,
                    PrivateDeliveryEvidenceRedemption(**values),  # type: ignore[arg-type]
                )
    finally:
        _delete_delivery_evidence(migration_engine, organization_id)
        migration_engine.dispose()


@pytest.mark.parametrize("binding_field", ["destination_ref", "consumer_ref"])
def test_same_logical_request_cannot_be_rebound_to_another_private_audience(
    binding_field: str,
    identity_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
) -> None:
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    migration_engine = create_database_engine(migration_configuration)
    identity_engine = create_database_engine(identity_configuration)
    references = iter(("der_" + "1" * 64, "der_" + "2" * 64))
    resolutions = iter(("dlr_" + "3" * 32, "dlr_" + "4" * 32))
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO membership (organization_id, membership_id, "
                    "user_id, status, membership_version, valid_from) VALUES "
                    "(:org, :membership_id, :user_id, 'active', 1, :valid_from)"
                ),
                {
                    "org": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
                    "valid_from": NOW - timedelta(minutes=1),
                },
            )
        issuer = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=5),
            ),
            reference_factory=lambda: next(references),
            resolution_ref_factory=lambda: next(resolutions),
        )
        issue = PrivateDeliveryEvidenceIssue(
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            membership_version=1,
            authenticated_service_ref="application:private-bot",
            authentication_binding_ref="binding:private-bot",
            request_id=f"audience-rebind-{binding_field}",
            destination_ref="private-chat:original",
            consumer_ref="consumer:original",
            purpose="context.answer",
            policy_epoch=1,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=2),
        )
        first = issuer.issue_private(issue)

        rebound = (
            replace(issue, destination_ref="rebound")
            if binding_field == "destination_ref"
            else replace(issue, consumer_ref="rebound")
        )
        with pytest.raises(DeliveryEvidenceNotAvailable):
            issuer.issue_private(rebound)

        with migration_engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT evidence_digest, logical_resolution_ref "
                    "FROM delivery_evidence WHERE organization_id = :org"
                ),
                {"org": organization_id},
            ).all()
        assert len(rows) == 1
        assert rows[0].logical_resolution_ref == first.logical_resolution_ref
    finally:
        _delete_delivery_evidence(migration_engine, organization_id)
        identity_engine.dispose()
        migration_engine.dispose()


def test_expired_private_evidence_cleanup_is_exactly_organization_scoped(
    identity_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
) -> None:
    organization_a, organization_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    membership_a, membership_b = uuid4(), uuid4()
    migration_engine = create_database_engine(migration_configuration)
    identity_engine = create_database_engine(identity_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:a), (:b)"),
                {"a": organization_a, "b": organization_b},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:a), (:b)"),
                {"a": user_a, "b": user_b},
            )
            for organization_id, user_id, membership_id in (
                (organization_a, user_a, membership_a),
                (organization_b, user_b, membership_b),
            ):
                connection.execute(
                    text(
                        "INSERT INTO membership (organization_id, membership_id, "
                        "user_id, status, membership_version, valid_from) VALUES "
                        "(:org, :membership, :user_id, 'active', 1, :valid_from)"
                    ),
                    {
                        "org": organization_id,
                        "membership": membership_id,
                        "user_id": user_id,
                        "valid_from": NOW - timedelta(days=1),
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO delivery_evidence (organization_id, "
                        "evidence_digest, digest_profile, delivery_kind, "
                        "authenticated_service_ref, authentication_binding_ref, "
                        "request_id, user_id, membership_id, membership_version, "
                        "destination_ref, consumer_ref, purpose, audience_digest, "
                        "policy_epoch, issued_at, expires_at, "
                        "logical_resolution_ref, profile_ref) VALUES "
                        "(:org, :digest, 'delivery-evidence-ref-sha256-v1', "
                        "'private', 'application:test', 'binding:test', :request, "
                        ":user_id, :membership, 1, 'destination:test', "
                        "'consumer:test', 'context.answer', :audience_digest, 1, "
                        ":issued_at, :expires_at, :resolution, "
                        "'private-delivery-evidence-v1')"
                    ),
                    {
                        "org": organization_id,
                        "digest": sha256(organization_id.bytes).digest(),
                        "request": f"expired-{organization_id}",
                        "user_id": user_id,
                        "membership": membership_id,
                        "audience_digest": sha256(membership_id.bytes).hexdigest(),
                        "issued_at": NOW - timedelta(minutes=3),
                        "expires_at": NOW - timedelta(minutes=2),
                        "resolution": f"expired-{organization_id}",
                    },
                )
        retention = PrivateDeliveryEvidenceRetention(
            PostgreSQLDeliveryEvidenceRetentionPort(identity_engine)
        )

        assert retention.delete_expired(organization_a) == 1

        with migration_engine.connect() as connection:
            remaining = (
                connection.execute(
                    text("SELECT organization_id FROM delivery_evidence"),
                )
                .scalars()
                .all()
            )
        assert remaining == [organization_b]
    finally:
        _delete_delivery_evidence(migration_engine, organization_a)
        _delete_delivery_evidence(migration_engine, organization_b)
        identity_engine.dispose()
        migration_engine.dispose()


def test_identity_cannot_backdate_issuance_for_a_membership_not_current_now(
    identity_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
) -> None:
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    migration_engine = create_database_engine(migration_configuration)
    identity_engine = create_database_engine(identity_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO membership (organization_id, membership_id, "
                    "user_id, status, membership_version, valid_from, valid_until) "
                    "VALUES (:org, :membership, :user_id, 'active', 1, "
                    ":valid_from, :valid_until)"
                ),
                {
                    "org": organization_id,
                    "membership": membership_id,
                    "user_id": user_id,
                    "valid_from": NOW - timedelta(minutes=5),
                    "valid_until": NOW - timedelta(minutes=1),
                },
            )
        issuer = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=10),
            ),
        )

        with pytest.raises(DeliveryEvidenceNotAvailable):
            issuer.issue_private(
                PrivateDeliveryEvidenceIssue(
                    organization_id=organization_id,
                    user_id=user_id,
                    membership_id=membership_id,
                    membership_version=1,
                    authenticated_service_ref="application:private-bot",
                    authentication_binding_ref="binding:private-bot",
                    request_id="backdated-membership",
                    destination_ref="private-chat:expired-member",
                    consumer_ref="consumer:private-bot",
                    purpose="context.answer",
                    policy_epoch=1,
                    issued_at=NOW - timedelta(minutes=2),
                    expires_at=NOW + timedelta(minutes=2),
                )
            )
    finally:
        _delete_delivery_evidence(migration_engine, organization_id)
        identity_engine.dispose()
        migration_engine.dispose()


@pytest.mark.security_evidence(id="PG-DELIVERY-EVIDENCE-063", layer="postgres")
def test_delivery_evidence_table_and_functions_enforce_exact_role_split(
    migration_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    identity_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    learning_configuration: DatabaseConfiguration,
    operator_configuration: DatabaseConfiguration,
) -> None:
    configurations = (
        control_configuration,
        identity_configuration,
        runtime_configuration,
        worker_configuration,
        learning_configuration,
        operator_configuration,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            facts = {
                configuration.expected_role: tuple(
                    connection.execute(
                        text(
                            "SELECT has_table_privilege(:role, "
                            "'delivery_evidence', 'SELECT'), "
                            "has_function_privilege(:role, "
                            "'context_identity_issue_private_delivery_evidence("
                            "uuid,bytea,text,text,text,text,uuid,uuid,bigint,text,"
                            "text,text,text,bigint,timestamptz,timestamptz,text,"
                            "text)', 'EXECUTE'), "
                            "has_function_privilege(:role, "
                            "'context_identity_delete_expired_private_delivery_evidence("
                            "uuid)', 'EXECUTE'), "
                            "has_function_privilege(:role, "
                            "'context_runtime_redeem_private_delivery_evidence("
                            "bytea,text,text,text,uuid,uuid,uuid,bigint,text,text,"
                            "text,text,text,bigint,timestamptz)', 'EXECUTE')"
                        ),
                        {"role": configuration.expected_role},
                    ).one()
                )
                for configuration in configurations
            }
        assert facts[identity_configuration.expected_role] == (
            False,
            True,
            True,
            False,
        )
        assert facts[runtime_configuration.expected_role] == (
            False,
            False,
            False,
            True,
        )
        for configuration in (
            control_configuration,
            worker_configuration,
            learning_configuration,
            operator_configuration,
        ):
            assert facts[configuration.expected_role] == (False,) * 4

        for configuration in configurations:
            engine = create_database_engine(configuration)
            try:
                with (
                    engine.connect() as connection,
                    pytest.raises(ProgrammingError),
                ):
                    connection.execute(text("SELECT * FROM delivery_evidence"))
            finally:
                engine.dispose()
    finally:
        migration_engine.dispose()
