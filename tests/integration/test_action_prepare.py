from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileRootRef,
    RegisterFileSource,
    VerifiedControlOperatorIdentity,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLControlStore,
    PostgreSQLDeliveryEvidenceIssuerPort,
    create_database_engine,
)
from engine.runtime.delivery_evidence import (
    DeliveryEvidenceProfile,
    PrivateDeliveryEvidenceIssue,
    PrivateDeliveryEvidenceIssuer,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
ACTION_PACKAGE = ROOT / "action_plane" / "typescript"
SERVICE_REF = "application:private-bot"
DESTINATION_REF = "private-chat:same-label"
CONSUMER_REF = "consumer:private-bot"
PURPOSE = "context.answer"
EVIDENCE_REF = "der_" + "7" * 64
EXPIRED_EVIDENCE_REF = "der_" + "6" * 64


class _ControlAuthenticator:
    def __init__(self, organization_id: UUID, now: datetime) -> None:
        self.organization_id = organization_id
        self.now = now

    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        assert opaque_credential == "credential:action-67"
        return VerifiedControlOperatorIdentity(
            organization_id=self.organization_id,
            operator_ref="operator:action-67",
            authentication_binding_ref="binding:control-action-67",
            authority_ref="authority:action-67",
            allowed_operations=frozenset({ControlOperation.REGISTER_SOURCE}),
            valid_from=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(minutes=10),
        )


def _node_database_url(configuration: DatabaseConfiguration) -> str:
    return configuration.url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _issue_evidence(
    identity_configuration: DatabaseConfiguration,
    *,
    organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
    now: datetime,
    evidence_ref: str = EVIDENCE_REF,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> None:
    identity_engine = create_database_engine(identity_configuration)
    try:
        issuer = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=5),
            ),
            reference_factory=lambda: evidence_ref,
            resolution_ref_factory=lambda: (
                "dlr_" + ("8" if evidence_ref == EVIDENCE_REF else "6") * 32
            ),
        )
        issued = issuer.issue_private(
            PrivateDeliveryEvidenceIssue(
                organization_id=organization_id,
                user_id=user_id,
                membership_id=membership_id,
                membership_version=1,
                authenticated_service_ref=SERVICE_REF,
                authentication_binding_ref="binding:private-bot",
                request_id=(
                    "delivery-request-action-67"
                    if evidence_ref == EVIDENCE_REF
                    else "delivery-request-action-67-expired"
                ),
                destination_ref=DESTINATION_REF,
                consumer_ref=CONSUMER_REF,
                purpose=PURPOSE,
                policy_epoch=1,
                issued_at=issued_at or now,
                expires_at=expires_at or now + timedelta(minutes=4),
            )
        )
        assert issued.evidence_ref == evidence_ref
    finally:
        identity_engine.dispose()


def _run_live_prepare(
    action_configuration: DatabaseConfiguration,
    *,
    organization_id: UUID,
    other_organization_id: UUID,
    user_id: UUID,
    membership_id: UUID,
    source_id: UUID,
    source_version_id: UUID,
    expected_active_source: str = "prepared",
) -> dict[str, object]:
    environment = {
        **os.environ,
        "CE_ACTION_DATABASE_URL": _node_database_url(action_configuration),
        "CE_ACTION_DELIVERY_EVIDENCE_REF": EVIDENCE_REF,
        "CE_ACTION_EXPIRED_EVIDENCE_REF": EXPIRED_EVIDENCE_REF,
        "CE_ACTION_MEMBERSHIP_ID": str(membership_id),
        "CE_ACTION_ORGANIZATION_ID": str(organization_id),
        "CE_ACTION_OTHER_ORGANIZATION_ID": str(other_organization_id),
        "CE_ACTION_USER_ID": str(user_id),
        "CE_ACTION_SOURCE_ID": str(source_id),
        "CE_ACTION_SOURCE_VERSION_ID": str(source_version_id),
        "CE_ACTION_EXPECT_ACTIVE_SOURCE": expected_active_source,
    }
    completed = subprocess.run(
        ["node", "test/live-prepare.mjs"],
        cwd=ACTION_PACKAGE,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    return result


@pytest.mark.security_evidence(id="PG-ACTION-PREPARE-067", layer="postgres")
def test_private_prepare_is_exact_idempotent_digest_only_and_restricted(
    guarded_action_engine: Engine,
    action_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    identity_configuration: DatabaseConfiguration,
    egress_configuration: DatabaseConfiguration,
    runtime_configuration: DatabaseConfiguration,
    worker_configuration: DatabaseConfiguration,
    learning_configuration: DatabaseConfiguration,
    operator_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
) -> None:
    del guarded_action_engine
    now = datetime.now(UTC).replace(microsecond=0)
    organization_id, other_organization_id = uuid4(), uuid4()
    user_id, membership_id = uuid4(), uuid4()
    other_user_id, other_membership_id = uuid4(), uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        application_roles = (
            action_configuration.expected_role,
            control_configuration.expected_role,
            identity_configuration.expected_role,
            egress_configuration.expected_role,
            runtime_configuration.expected_role,
            worker_configuration.expected_role,
            learning_configuration.expected_role,
            operator_configuration.expected_role,
        )
        with migration_engine.begin() as connection:
            for role in application_roles:
                observed = tuple(
                    connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege(:role, 'action_delivery_attempt', "
                            "'SELECT'), "
                            "has_table_privilege(:role, 'action_ticket', 'SELECT'), "
                            "has_table_privilege(:role, 'action_prepare_audit', "
                            "'SELECT'), "
                            "has_function_privilege(:role, "
                            "'context_action_prepare_private_effect(uuid,bytea,"
                            "bytea,bytea,uuid,uuid,bigint,bytea,bytea,bytea,bytea,"
                            "bytea,bigint,text,text,bytea,bytea,bytea,text,text,text,"
                            "text,integer,bigint,uuid,uuid,text,bigint)', 'EXECUTE')"
                        ),
                        {"role": role},
                    ).one()
                )
                assert observed == (
                    False,
                    False,
                    False,
                    role == action_configuration.expected_role,
                )

            catalog = connection.execute(
                text(
                    "SELECT relation.relname, relation.relrowsecurity, "
                    "relation.relforcerowsecurity, owner.rolname "
                    "FROM pg_class AS relation "
                    "JOIN pg_roles AS owner ON owner.oid = relation.relowner "
                    "WHERE relation.relname IN ('action_delivery_attempt', "
                    "'action_ticket', 'action_prepare_audit') "
                    "ORDER BY relation.relname"
                )
            ).all()
            assert [tuple(row) for row in catalog] == [
                ("action_delivery_attempt", True, True, "context_engine_migrator"),
                ("action_prepare_audit", True, True, "context_engine_migrator"),
                ("action_ticket", True, True, "context_engine_migrator"),
            ]
            function_owner = connection.execute(
                text(
                    "SELECT owner.rolname, function.prosecdef, "
                    "function.proconfig FROM pg_proc AS function "
                    "JOIN pg_roles AS owner ON owner.oid = function.proowner "
                    "WHERE function.oid = "
                    "'context_action_prepare_private_effect(uuid,bytea,bytea,"
                    "bytea,uuid,uuid,bigint,bytea,bytea,bytea,bytea,bytea,bigint,"
                    "text,text,bytea,bytea,bytea,text,text,text,text,integer,bigint,"
                    "uuid,uuid,text,bigint)'::regprocedure"
                )
            ).one()
            assert function_owner.rolname == "context_engine_action_prepare_definer"
            assert function_owner.prosecdef is True
            assert sorted(function_owner.proconfig) == [
                "row_security=on",
                "search_path=pg_catalog, pg_temp",
            ]
            definer_privileges = {
                (row.table_name, row.privilege_type)
                for row in connection.execute(
                    text(
                        "SELECT table_name, privilege_type "
                        "FROM information_schema.table_privileges "
                        "WHERE table_schema = 'public' AND grantee = "
                        "'context_engine_action_prepare_definer' AND table_name "
                        "IN ('action_delivery_attempt', 'action_ticket', "
                        "'action_prepare_audit')"
                    )
                )
            }
            assert definer_privileges == {
                ("action_delivery_attempt", "INSERT"),
                ("action_delivery_attempt", "SELECT"),
                ("action_prepare_audit", "INSERT"),
                ("action_ticket", "INSERT"),
                ("action_ticket", "SELECT"),
            }
            assert not any(
                privilege in {"DELETE", "TRUNCATE", "UPDATE"}
                for _, privilege in definer_privileges
            )

            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id), (:other_organization_id)"
                ),
                {
                    "organization_id": organization_id,
                    "other_organization_id": other_organization_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO user_account (user_id) "
                    "VALUES (:user_id), (:other_user_id)"
                ),
                {"user_id": user_id, "other_user_id": other_user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO membership (organization_id, membership_id, "
                    "user_id, status, membership_version, valid_from) VALUES "
                    "(:organization_id, :membership_id, :user_id, 'active', 1, "
                    ":valid_from), "
                    "(:other_organization_id, :other_membership_id, "
                    ":other_user_id, 'active', 1, :valid_from)"
                ),
                {
                    "organization_id": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
                    "other_organization_id": other_organization_id,
                    "other_membership_id": other_membership_id,
                    "other_user_id": other_user_id,
                    "valid_from": now - timedelta(minutes=1),
                },
            )

        action_engine = create_database_engine(action_configuration)
        try:
            with action_engine.connect() as connection:
                wrong_operation = tuple(
                    connection.execute(
                        text(
                            "SELECT * FROM context_action_prepare_private_effect("
                            "NULL::uuid, NULL::bytea, NULL::bytea, NULL::bytea, "
                            "NULL::uuid, NULL::uuid, NULL::bigint, NULL::bytea, "
                            "NULL::bytea, NULL::bytea, NULL::bytea, NULL::bytea, "
                            "NULL::bigint, :operation, NULL::text, NULL::bytea, "
                            "NULL::bytea, NULL::bytea, NULL::text, NULL::text, "
                            "NULL::text, NULL::text, NULL::integer, NULL::bigint, "
                            "NULL::uuid, NULL::uuid, NULL::text, NULL::bigint)"
                        ),
                        {"operation": "delete_message"},
                    ).one()
                )
                assert wrong_operation == (
                    "generic_denied",
                    None,
                    None,
                    None,
                    None,
                    False,
                )
        finally:
            action_engine.dispose()

        _issue_evidence(
            identity_configuration,
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            now=now,
        )
        _issue_evidence(
            identity_configuration,
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            now=now,
            evidence_ref=EXPIRED_EVIDENCE_REF,
        )
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE delivery_evidence SET issued_at = :issued_at, "
                    "expires_at = :expires_at WHERE organization_id = "
                    ":organization_id AND evidence_digest = digest(:evidence_ref, "
                    "'sha256')"
                ),
                {
                    "organization_id": organization_id,
                    "evidence_ref": EXPIRED_EVIDENCE_REF,
                    "issued_at": now - timedelta(minutes=5),
                    "expires_at": now - timedelta(minutes=1),
                },
            )
        control_authority = ControlOperatorAuthority(
            _ControlAuthenticator(organization_id, now),
            call_ttl=timedelta(minutes=5),
            clock=lambda: now,
        )
        control = ContextControl(
            store=PostgreSQLControlStore(guarded_control_engine, clock=lambda: now),
            authority=control_authority,
            clock=lambda: now,
        )
        with control_authority.authorize(
            opaque_credential="credential:action-67",
            operation=ControlOperation.REGISTER_SOURCE,
            request_id="register-source-action-67",
        ) as call:
            source = control.register_source(
                call,
                RegisterFileSource(
                    display_name="Action source",
                    root_ref=FileRootRef("action-source-root"),
                    idempotency_key="action-source-67",
                ),
            )
        subprocess.run(
            ["npm", "run", "build"],
            cwd=ACTION_PACKAGE,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = _run_live_prepare(
            action_configuration,
            organization_id=organization_id,
            other_organization_id=other_organization_id,
            user_id=user_id,
            membership_id=membership_id,
            source_id=source.source_ref.value,
            source_version_id=source.active_version.version_ref,
        )
        closed_denials = {
            "organization": "generic_denied",
            "service": "generic_denied",
            "binding": "generic_denied",
            "destination": "generic_denied",
            "consumer": "generic_denied",
            "purpose": "generic_denied",
            "audience": "audience_changed",
            "epoch": "generic_denied",
            "membership_version": "generic_denied",
        }
        assert result == {
            "denied": {
                "create_placeholder": closed_denials,
                "finalize_reply": closed_denials,
                "send_private_followup": closed_denials,
            },
            "distinctTicketTypes": [
                "CE-CreatePlaceholderActionTicket",
                "CE-FinalizeReplyActionTicket",
                "CE-SendPrivateFollowupActionTicket",
            ],
            "effectCount": 0,
            "exactRetryIdempotent": True,
            "expiredEvidence": "generic_denied",
            "matrixEffectCount": 0,
            "operation": "create_placeholder",
            "payloadConflicts": {
                "create_placeholder": "generic_denied",
                "finalize_reply": "generic_denied",
                "send_private_followup": "generic_denied",
            },
            "prepared": "prepared",
            "sourceContext": {
                "active": "prepared",
                "stale": "generic_denied",
            },
        }

        with migration_engine.connect() as connection:
            counts = connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM action_delivery_attempt), "
                    "(SELECT count(*) FROM action_ticket), "
                    "(SELECT count(*) FROM action_prepare_audit)"
                )
            ).one()
            assert counts[0:2] == (3, 4)
            assert counts[2] == 25
            rows = connection.execute(
                text(
                    "SELECT authenticated_service_digest, "
                    "delivery_evidence_digest, authentication_binding_digest, "
                    "destination_digest, consumer_digest, purpose_digest, "
                    "audience_digest, identity_digest, retention_policy_ref, "
                    "retain_until > created_at AS retained "
                    "FROM action_delivery_attempt"
                )
            ).all()
            assert rows
            for row in rows:
                assert all(len(bytes(value)) == 32 for value in row[:8])
                assert row.retention_policy_ref == (
                    "action-digest-audit-retention-v1"
                )
                assert row.retained is True
            ticket_rows = connection.execute(
                text(
                    "SELECT operation, ticket_audience, length(payload_digest), "
                    "length(idempotency_digest), length(approval_digest), state "
                    "FROM action_ticket ORDER BY operation"
                )
            ).all()
            assert {row.operation for row in ticket_rows} == {
                "create_placeholder",
                "finalize_reply",
                "send_private_followup",
            }
            assert all(row[2:5] == (32, 32, 32) for row in ticket_rows)
            assert all(row.state == "prepared" for row in ticket_rows)
            serialized_rows = " ".join(
                repr(row) for row in [*rows, *ticket_rows]
            )
            for secret in (
                EVIDENCE_REF,
                DESTINATION_REF,
                SERVICE_REF,
                CONSUMER_REF,
                "Working…",
                "Conflicting payload",
            ):
                assert secret not in serialized_rows

        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE context_source SET lifecycle_state = 'disabled', "
                    "disabled_version_id = active_version_id, "
                    "disabled_at = :disabled_at WHERE organization_id = "
                    ":organization_id AND source_id = :source_id"
                ),
                {
                    "disabled_at": datetime.now(UTC),
                    "organization_id": organization_id,
                    "source_id": source.source_ref.value,
                },
            )
        disabled_result = _run_live_prepare(
            action_configuration,
            organization_id=organization_id,
            other_organization_id=other_organization_id,
            user_id=user_id,
            membership_id=membership_id,
            source_id=source.source_ref.value,
            source_version_id=source.active_version.version_ref,
            expected_active_source="generic_denied",
        )
        assert disabled_result["sourceContext"] == {
            "active": "generic_denied",
            "stale": "generic_denied",
        }

        action_engine = create_database_engine(action_configuration)
        try:
            with action_engine.connect() as connection:
                for table_name in (
                    "action_delivery_attempt",
                    "action_ticket",
                    "action_prepare_audit",
                ):
                    with pytest.raises(ProgrammingError):
                        connection.execute(text(f"SELECT * FROM {table_name}"))
                    connection.rollback()
        finally:
            action_engine.dispose()
    finally:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM action_prepare_audit WHERE organization_id = "
                    ":organization_id"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM action_ticket WHERE organization_id = "
                    ":organization_id"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM action_delivery_attempt WHERE organization_id = "
                    ":organization_id"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM delivery_evidence WHERE organization_id = "
                    ":organization_id"
                ),
                {"organization_id": organization_id},
            )
        migration_engine.dispose()
