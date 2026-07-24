from __future__ import annotations

import json
import os
import select
import subprocess
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from engine.persistence import (
    DatabaseConfiguration,
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
EVIDENCE_REF = "der_" + "5" * 64
OTHER_EVIDENCE_REF = "der_" + "6" * 64


def _read_process_line(
    process: subprocess.Popen[bytes], *, timeout_seconds: float
) -> bytes:
    assert process.stdout is not None
    deadline = monotonic() + timeout_seconds
    line = bytearray()
    descriptor = process.stdout.fileno()
    while b"\n" not in line:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        readable, _, _ = select.select([descriptor], [], [], remaining)
        if not readable:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        chunk = os.read(descriptor, 4096)
        if not chunk:
            break
        line.extend(chunk)
    return bytes(line)


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
    evidence_ref: str,
) -> None:
    issued_at = max(now, datetime.now(UTC) - timedelta(seconds=1))
    identity_engine = create_database_engine(identity_configuration)
    try:
        issuer = PrivateDeliveryEvidenceIssuer(
            PostgreSQLDeliveryEvidenceIssuerPort(identity_engine),
            profile=DeliveryEvidenceProfile(
                profile_ref="private-delivery-evidence-v1",
                maximum_ttl=timedelta(minutes=5),
            ),
            reference_factory=lambda: evidence_ref,
            resolution_ref_factory=lambda: "dlr_" + evidence_ref[-32:],
        )
        issuer.issue_private(
            PrivateDeliveryEvidenceIssue(
                organization_id=organization_id,
                user_id=user_id,
                membership_id=membership_id,
                membership_version=1,
                authenticated_service_ref=SERVICE_REF,
                authentication_binding_ref="binding:private-bot",
                request_id="delivery-request-action-68",
                destination_ref=DESTINATION_REF,
                consumer_ref=CONSUMER_REF,
                purpose=PURPOSE,
                policy_epoch=1,
                issued_at=issued_at,
                expires_at=issued_at + timedelta(minutes=4),
            )
        )
    finally:
        identity_engine.dispose()


def _run_live_perform(
    action_configuration: DatabaseConfiguration,
    migration_engine: Engine,
    *,
    delivery_evidence_ref: str,
    membership_id: UUID,
    organization_id: UUID,
    reference_offset: int,
    stale_mutation: str,
    user_id: UUID,
) -> dict[str, object]:
    process = subprocess.Popen(
        ["node", "test/live-perform.mjs"],
        cwd=ACTION_PACKAGE,
        env={
            **os.environ,
            "CE_ACTION_DATABASE_URL": _node_database_url(action_configuration),
            "CE_ACTION_DELIVERY_EVIDENCE_REF": delivery_evidence_ref,
            "CE_ACTION_MEMBERSHIP_ID": str(membership_id),
            "CE_ACTION_ORGANIZATION_ID": str(organization_id),
            "CE_ACTION_REF_OFFSET": str(reference_offset),
            "CE_ACTION_STALE_MUTATION": stale_mutation,
            "CE_ACTION_USER_ID": str(user_id),
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert process.stdin is not None
        ready = _read_process_line(process, timeout_seconds=10)
        if ready == b"":
            assert process.stderr is not None
            _, stderr = process.communicate(timeout=5)
            raise AssertionError(
                "live perform exited before stale mutation: "
                f"{stderr.decode(errors='replace')}"
            )
        assert ready == (
            b"READY_FOR_EPOCH_CHANGE\n"
            if stale_mutation == "epoch"
            else b"READY_FOR_MEMBERSHIP_CHANGE\n"
        )
        with migration_engine.begin() as connection:
            if stale_mutation == "epoch":
                connection.execute(
                    text(
                        "UPDATE organization_policy_epoch SET policy_epoch = 2 "
                        "WHERE organization_id = "
                        ":organization_id AND policy_epoch = 1"
                    ),
                    {"organization_id": organization_id},
                )
            else:
                connection.execute(
                    text(
                        "UPDATE membership SET status = 'revoked', "
                        "valid_until = clock_timestamp() WHERE organization_id = "
                        ":organization_id AND membership_id = :membership_id"
                    ),
                    {
                        "membership_id": membership_id,
                        "organization_id": organization_id,
                    },
                )
        process.stdin.write(b"continue\n")
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode(errors="replace")
        result = json.loads(stdout)
        assert isinstance(result, dict)
        return cast(dict[str, object], result)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.security_evidence(id="PG-ACTION-PERFORM-068", layer="postgres")
def test_private_perform_is_one_shot_replayable_and_reconcilable(
    guarded_action_engine: Engine,
    action_configuration: DatabaseConfiguration,
    control_configuration: DatabaseConfiguration,
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
    organization_id, user_id, membership_id = uuid4(), uuid4(), uuid4()
    other_organization_id = uuid4()
    other_user_id, other_membership_id = uuid4(), uuid4()
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
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
            for role in application_roles:
                privileges = tuple(
                    connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege(:role, 'action_provider_attempt', "
                            "'SELECT'), "
                            "has_table_privilege(:role, 'action_receipt', 'SELECT'), "
                            "has_table_privilege(:role, 'action_reconciliation', "
                            "'SELECT'), "
                            "has_table_privilege(:role, 'action_perform_audit', "
                            "'SELECT'), "
                            "has_function_privilege(:role, "
                            "'context_action_begin_private_effect(uuid,text,text,"
                            "text,text,bytea,bytea,bytea,bigint,integer,text,bytea,"
                            "bytea,bytea,bytea,bytea,bytea,timestamptz,"
                            "timestamptz,bytea,text,bigint)', 'EXECUTE'), "
                            "has_function_privilege(:role, "
                            "'context_action_complete_private_effect(uuid,text,"
                            "text,text,bytea,timestamptz,text,text,bigint)', "
                            "'EXECUTE'), "
                            "has_function_privilege(:role, "
                            "'context_action_reconcile_private_effect(uuid,text,"
                            "text,bytea,timestamptz,text,bytea,text,bigint)', "
                            "'EXECUTE')"
                        ),
                        {"role": role},
                    ).one()
                )
                assert privileges == (
                    False,
                    False,
                    False,
                    False,
                    role == action_configuration.expected_role,
                    role == action_configuration.expected_role,
                    role == action_configuration.expected_role,
                )
            catalog = connection.execute(
                text(
                    "SELECT relation.relname, relation.relrowsecurity, "
                    "relation.relforcerowsecurity, owner.rolname "
                    "FROM pg_class AS relation JOIN pg_roles AS owner "
                    "ON owner.oid = relation.relowner WHERE relation.relname "
                    "IN ('action_provider_attempt', 'action_receipt', "
                    "'action_reconciliation', 'action_perform_audit') "
                    "ORDER BY relation.relname"
                )
            ).all()
            assert [tuple(row) for row in catalog] == [
                ("action_perform_audit", True, True, "context_engine_migrator"),
                ("action_provider_attempt", True, True, "context_engine_migrator"),
                ("action_receipt", True, True, "context_engine_migrator"),
                ("action_reconciliation", True, True, "context_engine_migrator"),
            ]
            function_catalog = connection.execute(
                text(
                    "SELECT function.proname, owner.rolname, function.prosecdef, "
                    "function.proconfig FROM pg_proc AS function JOIN pg_roles "
                    "AS owner ON owner.oid = function.proowner WHERE "
                    "function.proname IN ('context_action_begin_private_effect', "
                    "'context_action_complete_private_effect', "
                    "'context_action_reconcile_private_effect') "
                    "ORDER BY function.proname"
                )
            ).all()
            assert len(function_catalog) == 3
            for function in function_catalog:
                assert function.rolname == "context_engine_action_execute_definer"
                assert function.prosecdef is True
                assert sorted(function.proconfig) == [
                    "row_security=on",
                    "search_path=pg_catalog, pg_temp",
                ]
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
                    ":valid_from), (:other_organization_id, "
                    ":other_membership_id, :other_user_id, 'active', 1, "
                    ":valid_from)"
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
        _issue_evidence(
            identity_configuration,
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            now=now,
            evidence_ref=EVIDENCE_REF,
        )
        _issue_evidence(
            identity_configuration,
            organization_id=other_organization_id,
            user_id=other_user_id,
            membership_id=other_membership_id,
            now=now,
            evidence_ref=OTHER_EVIDENCE_REF,
        )
        subprocess.run(
            ["npm", "run", "build"],
            cwd=ACTION_PACKAGE,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        results = [
            _run_live_perform(
                action_configuration,
                migration_engine,
                delivery_evidence_ref=EVIDENCE_REF,
                membership_id=membership_id,
                organization_id=organization_id,
                reference_offset=100,
                stale_mutation="epoch",
                user_id=user_id,
            ),
            _run_live_perform(
                action_configuration,
                migration_engine,
                delivery_evidence_ref=OTHER_EVIDENCE_REF,
                membership_id=other_membership_id,
                organization_id=other_organization_id,
                reference_offset=1_000,
                stale_mutation="membership",
                user_id=other_user_id,
            ),
        ]
        for result in results:
            applied = cast(dict[str, object], result["applied"])
            ambiguous = cast(dict[str, object], result["ambiguous"])
            assert result["senderCalls"] == 3
            assert result["senderEffects"] == 3
            assert result["postLockFailure"] == {
                "senderCalls": 0,
                "unlocked": True,
            }
            assert result["farFutureAppliedAt"] == {
                "outcome": "reconciliation_required",
                "reconcile": "already_applied",
                "senderCalls": 1,
            }
            bounded_skew = cast(dict[str, object], result["boundedPositiveSkew"])
            assert bounded_skew["outcome"] == "applied"
            assert bounded_skew["senderCalls"] == 1
            assert cast(str, bounded_skew["appliedAt"]).endswith("Z")
            assert set(applied) == {
                "create_placeholder",
                "finalize_reply",
                "send_private_followup",
            }
            assert ambiguous["first"] == "reconciliation_required"
            assert ambiguous["replay"] == "reconciliation_required"
            assert ambiguous["reconcile"] == "already_applied"
            assert ambiguous["reconciliationReplay"] == "already_applied"
            assert ambiguous["terminalReplay"] == "already_applied"
            assert ambiguous["senderCalls"] == 1
            assert result["reconciledRejected"] == {
                "conflict": "rejected",
                "first": "reconciliation_required",
                "reconcile": "rejected",
                "reconciliationReplay": "rejected",
                "replay": "rejected",
                "senderCalls": 1,
            }
            assert result["rejected"] == {
                "first": "rejected",
                "firstReasonCategory": "provider_rejected",
                "replay": "rejected",
                "replayReasonCategory": "provider_rejected",
                "senderCalls": 1,
                "senderEffects": 0,
            }
            assert result["crash"] == {
                "closedSessions": 1,
                "first": "reconciliation_required",
                "reconcile": "already_applied",
                "replay": "reconciliation_required",
                "senderCalls": 1,
                "unlockAfterConnectionLossAttempts": 1,
            }
            concurrent = cast(dict[str, object], result["concurrent"])
            outcomes = cast(list[str], concurrent["outcomes"])
            assert outcomes.count("applied") == 1
            assert set(outcomes) <= {
                "already_applied",
                "applied",
                "reconciliation_required",
            }
            assert concurrent["senderCalls"] == 1
            assert concurrent["senderEffects"] == 1
            assert concurrent["prematureReconciliation"] == "rejected"
            assert result["nullDispositionReconciliation"] == "rejected"
            assert result["stale"] == {
                "outcome": "rejected",
                "reasonCategory": "not_available",
                "senderCalls": 0,
            }
            assert result["expired"] == {
                "outcome": "rejected",
                "reasonCategory": "not_available",
                "senderCalls": 0,
            }
            assert result["mutationMatrix"] == {
                "approval": "rejected:not_available",
                "attempt": "rejected:not_available",
                "audience": "rejected:not_available",
                "destination": "rejected:not_available",
                "epoch": "rejected:not_available",
                "idempotency": "rejected:not_available",
                "operation": "rejected:not_available",
                "organization": "rejected:not_available",
                "payload": "rejected:not_available",
                "service": "rejected:not_available",
                "service_null": "rejected:not_available",
            }

        with migration_engine.connect() as connection:
            counts = tuple(
                connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM action_provider_attempt), "
                        "(SELECT count(*) FROM action_receipt), "
                        "(SELECT count(*) FROM action_reconciliation)"
                    )
                ).one()
            )
            assert counts == (20, 16, 8)
            assert connection.execute(
                text(
                    "SELECT count(*) FROM action_provider_attempt "
                    "WHERE state <> 'applied' OR provider_effect_digest IS NULL"
                )
            ).scalar_one() == 4
            per_organization = connection.execute(
                text(
                    "SELECT organization_id, "
                    "(SELECT count(*) FROM action_provider_attempt AS attempt "
                    "WHERE attempt.organization_id = organization.organization_id), "
                    "(SELECT count(*) FROM action_receipt AS receipt "
                    "WHERE receipt.organization_id = organization.organization_id), "
                    "(SELECT count(*) FROM action_reconciliation AS reconciliation "
                    "WHERE reconciliation.organization_id = "
                    "organization.organization_id) FROM organization WHERE "
                    "organization_id IN (:organization_id, "
                    ":other_organization_id) ORDER BY organization_id"
                ),
                {
                    "organization_id": organization_id,
                    "other_organization_id": other_organization_id,
                },
            ).all()
            assert len(per_organization) == 2
            assert all(tuple(row[1:]) == (10, 8, 4) for row in per_organization)
            audit_rows = connection.execute(
                text(
                    "SELECT organization_id, decision_digest, category, "
                    "recorded_at, retention_policy_ref, retain_until "
                    "FROM action_perform_audit ORDER BY organization_id, audit_id"
                )
            ).all()
            observed_audit_categories = {
                str(observed_organization_id): Counter(
                    row.category
                    for row in audit_rows
                    if row.organization_id == observed_organization_id
                )
                for observed_organization_id in (
                    organization_id,
                    other_organization_id,
                )
            }
            assert len(audit_rows) == 96, observed_audit_categories
            for expected_organization_id in (
                organization_id,
                other_organization_id,
            ):
                organization_audits = [
                    row
                    for row in audit_rows
                    if row.organization_id == expected_organization_id
                ]
                assert len(organization_audits) == 48
                assert Counter(row.category for row in organization_audits) == {
                    "sender_required": 10,
                    "applied": 5,
                    "already_applied": 5,
                    "rejected": 19,
                    "reconciliation_required": 5,
                    "reconciled_applied": 3,
                    "reconciled_rejected": 1,
                }
                assert all(
                    len(row.decision_digest) == 32
                    and row.retention_policy_ref
                    == "action-digest-audit-retention-v1"
                    and row.retain_until > row.recorded_at
                    for row in organization_audits
                )
            retained_rows: list[Any] = list(
                connection.execute(
                    text(
                        "SELECT organization_id, provider_attempt_ref, "
                        "ticket_ref, delivery_attempt_ref, operation, "
                        "destination_digest, audience_digest, payload_digest, "
                        "idempotency_digest, state, provider_effect_digest "
                        "FROM action_provider_attempt"
                    )
                ).all()
            )
            retained_rows.extend(
                connection.execute(
                    text(
                        "SELECT organization_id, receipt_ref, "
                        "provider_attempt_ref, ticket_ref, delivery_attempt_ref, "
                        "operation, destination_digest, audience_digest, "
                        "payload_digest, idempotency_digest, "
                        "provider_effect_digest FROM action_receipt"
                    )
                ).all()
            )
            retained_rows.extend(
                connection.execute(
                    text(
                        "SELECT organization_id, provider_attempt_ref, state, "
                        "decision_digest, reconciled_at, retention_policy_ref, "
                        "retain_until FROM action_reconciliation"
                    )
                ).all()
            )
            retained_rows.extend(audit_rows)
            serialized_rows = " ".join(repr(row) for row in retained_rows)
            for raw_value in (
                EVIDENCE_REF,
                OTHER_EVIDENCE_REF,
                SERVICE_REF,
                DESTINATION_REF,
                CONSUMER_REF,
                "Working…",
                "Done",
                "More context",
            ):
                assert raw_value not in serialized_rows
            with (
                pytest.raises(OperationalError, match="ActionReceipt is immutable"),
                migration_engine.begin() as immutable_connection,
            ):
                immutable_connection.execute(
                    text(
                        "UPDATE action_receipt SET operation = operation "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
            with (
                pytest.raises(OperationalError, match="ActionReceipt is immutable"),
                migration_engine.begin() as immutable_connection,
            ):
                immutable_connection.execute(
                    text(
                        "DELETE FROM action_receipt WHERE organization_id = "
                        ":organization_id"
                    ),
                    {"organization_id": organization_id},
                )

        action_engine = create_database_engine(action_configuration)
        try:
            with action_engine.connect() as connection:
                for table_name in (
                    "action_provider_attempt",
                    "action_receipt",
                    "action_reconciliation",
                    "action_perform_audit",
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
                    "ALTER TABLE action_receipt DISABLE TRIGGER "
                    "action_receipt_reject_mutation"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM action_receipt WHERE organization_id IN "
                    "(:organization_id, :other_organization_id)"
                ),
                {
                    "organization_id": organization_id,
                    "other_organization_id": other_organization_id,
                },
            )
            connection.execute(
                text(
                    "ALTER TABLE action_receipt ENABLE TRIGGER "
                    "action_receipt_reject_mutation"
                )
            )
            for table_name in (
                "action_perform_audit",
                "action_reconciliation",
                "action_provider_attempt",
                "action_prepare_audit",
                "action_ticket",
                "action_delivery_attempt",
                "delivery_evidence",
                "membership",
                "organization_policy_epoch",
            ):
                connection.execute(
                    text(
                        f"DELETE FROM {table_name} WHERE organization_id "
                        "IN (:organization_id, :other_organization_id)"
                    ),
                    {
                        "organization_id": organization_id,
                        "other_organization_id": other_organization_id,
                    },
                )
            connection.execute(
                text(
                    "DELETE FROM user_account WHERE user_id IN "
                    "(:user_id, :other_user_id)"
                ),
                {"other_user_id": other_user_id, "user_id": user_id},
            )
            connection.execute(
                text(
                    "DELETE FROM organization WHERE organization_id IN "
                    "(:organization_id, :other_organization_id)"
                ),
                {
                    "organization_id": organization_id,
                    "other_organization_id": other_organization_id,
                },
            )
        migration_engine.dispose()
