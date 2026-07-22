from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.configuration import (
    CONTEXT_RUN_READER_DEFINER_ROLE,
    CONTROL_ROLE,
    OPERATOR_ROLE,
    RUNTIME_ROLE,
    WORKER_ROLE,
)

pytestmark = pytest.mark.integration
ACCEPTED_AT = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
POLICY_SNAPSHOT_REF = "policy:issue-19"
AUTHORIZED_EVIDENCE_REF = "ev_" + "d" * 64


@dataclass(frozen=True, slots=True)
class LineageIdentity:
    organization_id: UUID
    user_id: UUID
    membership_id: UUID
    run_ref: str
    decision_ref: str


@contextmanager
def current_user_actor(
    engine: Engine,
    identity: LineageIdentity,
) -> Iterator[Connection]:
    settings = {
        "app.organization_id": str(identity.organization_id),
        "app.actor_kind": "user",
        "app.user_id": str(identity.user_id),
        "app.membership_id": str(identity.membership_id),
        "app.membership_version": "1",
        "app.principal_ref": "principal:issue-19",
        "app.request_id": "request:issue-19",
        "app.authentication_binding_ref": "binding:issue-19",
        "app.checked_at": ACCEPTED_AT.isoformat().replace("+00:00", "Z"),
    }
    with engine.begin() as connection:
        for setting_name, setting_value in settings.items():
            connection.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": setting_name, "value": setting_value},
            )
        yield connection


@pytest.fixture
def lineage_identity(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[LineageIdentity]:
    identity = LineageIdentity(
        organization_id=uuid4(),
        user_id=uuid4(),
        membership_id=uuid4(),
        run_ref="run_" + "1" * 32,
        decision_ref="dec_" + "2" * 32,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization (organization_id) "
                    "VALUES (:organization_id)"
                ),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_id, :membership_id, :user_id, 'active',
                        1, :valid_from, NULL
                    )
                    """
                ),
                {
                    "organization_id": identity.organization_id,
                    "membership_id": identity.membership_id,
                    "user_id": identity.user_id,
                    "valid_from": ACCEPTED_AT - timedelta(days=1),
                },
            )
        yield identity
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM decision_audit "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM context_run WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text("DELETE FROM membership WHERE organization_id = :organization_id"),
                {"organization_id": identity.organization_id},
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text(
                    "DELETE FROM organization WHERE organization_id = :organization_id"
                ),
                {"organization_id": identity.organization_id},
            )
        engine.dispose()


@pytest.fixture
def cross_organization_lineages(
    migration_configuration: DatabaseConfiguration,
) -> Iterator[tuple[LineageIdentity, LineageIdentity]]:
    identities = (
        LineageIdentity(
            organization_id=uuid4(),
            user_id=uuid4(),
            membership_id=uuid4(),
            run_ref="run_" + "5" * 32,
            decision_ref="dec_" + "6" * 32,
        ),
        LineageIdentity(
            organization_id=uuid4(),
            user_id=uuid4(),
            membership_id=uuid4(),
            run_ref="run_" + "7" * 32,
            decision_ref="dec_" + "8" * 32,
        ),
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            for identity in identities:
                connection.execute(
                    text(
                        "INSERT INTO organization (organization_id) "
                        "VALUES (:organization_id)"
                    ),
                    {"organization_id": identity.organization_id},
                )
                connection.execute(
                    text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                    {"user_id": identity.user_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO membership (
                            organization_id, membership_id, user_id, status,
                            membership_version, valid_from, valid_until
                        ) VALUES (
                            :organization_id, :membership_id, :user_id, 'active',
                            1, :valid_from, NULL
                        )
                        """
                    ),
                    {
                        "organization_id": identity.organization_id,
                        "membership_id": identity.membership_id,
                        "user_id": identity.user_id,
                        "valid_from": ACCEPTED_AT - timedelta(days=1),
                    },
                )
        yield identities
    finally:
        with engine.begin() as connection:
            for identity in identities:
                connection.execute(
                    text(
                        "DELETE FROM decision_audit "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": identity.organization_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM context_run "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": identity.organization_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM membership "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": identity.organization_id},
                )
                connection.execute(
                    text("DELETE FROM user_account WHERE user_id = :user_id"),
                    {"user_id": identity.user_id},
                )
                connection.execute(
                    text(
                        "DELETE FROM organization "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": identity.organization_id},
                )
        engine.dispose()


@pytest.fixture
def same_organization_other_actor(
    migration_configuration: DatabaseConfiguration,
    lineage_identity: LineageIdentity,
) -> Iterator[LineageIdentity]:
    identity = LineageIdentity(
        organization_id=lineage_identity.organization_id,
        user_id=uuid4(),
        membership_id=uuid4(),
        run_ref=lineage_identity.run_ref,
        decision_ref=lineage_identity.decision_ref,
    )
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": identity.user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from, valid_until
                    ) VALUES (
                        :organization_id, :membership_id, :user_id, 'active',
                        1, :valid_from, NULL
                    )
                    """
                ),
                {
                    "organization_id": identity.organization_id,
                    "membership_id": identity.membership_id,
                    "user_id": identity.user_id,
                    "valid_from": ACCEPTED_AT - timedelta(days=1),
                },
            )
        yield identity
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM membership "
                    "WHERE organization_id = :organization_id "
                    "AND membership_id = :membership_id"
                ),
                {
                    "organization_id": identity.organization_id,
                    "membership_id": identity.membership_id,
                },
            )
            connection.execute(
                text("DELETE FROM user_account WHERE user_id = :user_id"),
                {"user_id": identity.user_id},
            )
        engine.dispose()


def insert_context_run(
    connection: Connection,
    identity: LineageIdentity,
    *,
    outcome: str = "delivered_empty",
    authorized_evidence_refs: tuple[str, ...] = (),
    policy_snapshot_ref: str = POLICY_SNAPSHOT_REF,
    policy_epoch: int = 1,
    finalized_at: datetime = ACCEPTED_AT,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO context_run (
                organization_id, run_ref, decision_ref,
                user_id, membership_id, membership_version,
                principal_ref, agent_version_ref,
                authenticated_application_ref, authentication_binding_ref,
                request_id, purpose, policy_snapshot_ref, policy_epoch,
                effective_scope_digest, query_digest_profile,
                query_digest_key_version, query_digest, outcome,
                package_digest_profile, package_digest,
                package_retention_mode, authorized_evidence_refs,
                effective_max_tokens, effective_max_provider_calls,
                effective_max_cost_microunits, effective_max_elapsed_ms,
                usage_tokens, usage_provider_calls,
                usage_cost_microunits, usage_elapsed_ms,
                accepted_at, finalized_at, package_as_of, package_expires_at
            ) VALUES (
                :organization_id, :run_ref, :decision_ref,
                :user_id, :membership_id, 1,
                'principal:issue-19', 'agent:issue-19',
                'application:issue-19', 'binding:issue-19',
                'request:issue-19', 'answer', :policy_snapshot_ref, :policy_epoch,
                :effective_scope_digest, 'context-query-json-hmac-sha256-v1',
                1, :query_digest, :outcome,
                'context-package-canonical-json-v1', :package_digest,
                'digest_only', CAST(:authorized_evidence_refs AS jsonb),
                1000, 8, 100000, 5000,
                0, 0, 0, 0,
                :accepted_at, :finalized_at, :package_as_of,
                :package_expires_at
            )
            """
        ),
        {
            "organization_id": identity.organization_id,
            "run_ref": identity.run_ref,
            "decision_ref": identity.decision_ref,
            "user_id": identity.user_id,
            "membership_id": identity.membership_id,
            "effective_scope_digest": "a" * 64,
            "query_digest": "b" * 64,
            "package_digest": "c" * 64,
            "authorized_evidence_refs": json.dumps(authorized_evidence_refs),
            "outcome": outcome,
            "policy_snapshot_ref": policy_snapshot_ref,
            "policy_epoch": policy_epoch,
            "accepted_at": ACCEPTED_AT,
            "finalized_at": finalized_at,
            "package_as_of": finalized_at,
            "package_expires_at": finalized_at + timedelta(minutes=5),
        },
    )


def insert_decision_audit(
    connection: Connection,
    identity: LineageIdentity,
    *,
    policy_snapshot_ref: str = POLICY_SNAPSHOT_REF,
    policy_epoch: int = 1,
    recorded_at: datetime = ACCEPTED_AT,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO decision_audit (
                organization_id, run_ref, decision_ref,
                policy_snapshot_ref, policy_epoch, category, recorded_at
            ) VALUES (
                :organization_id, :run_ref, :decision_ref,
                :policy_snapshot_ref, :policy_epoch,
                'no_authorized_evidence', :recorded_at
            )
            """
        ),
        {
            "organization_id": identity.organization_id,
            "run_ref": identity.run_ref,
            "decision_ref": identity.decision_ref,
            "policy_snapshot_ref": policy_snapshot_ref,
            "policy_epoch": policy_epoch,
            "recorded_at": recorded_at,
        },
    )


def insert_empty_lineage(connection: Connection, identity: LineageIdentity) -> None:
    insert_context_run(connection, identity)
    insert_decision_audit(connection, identity)


def test_runtime_can_only_append_exact_current_user_actor_lineage(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    lineage_identity: LineageIdentity,
) -> None:
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_empty_lineage(connection, lineage_identity)

    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        guarded_runtime_engine.begin() as connection,
    ):
        connection.execute(text("SELECT * FROM context_run"))

    forged = LineageIdentity(
        organization_id=lineage_identity.organization_id,
        user_id=uuid4(),
        membership_id=lineage_identity.membership_id,
        run_ref="run_" + "3" * 32,
        decision_ref="dec_" + "4" * 32,
    )
    with (
        pytest.raises(DBAPIError, match="row-level security"),
        current_user_actor(guarded_runtime_engine, lineage_identity) as connection,
    ):
        insert_empty_lineage(connection, forged)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM context_run "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": lineage_identity.organization_id},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM decision_audit "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": lineage_identity.organization_id},
                ).scalar_one()
                == 1
            )
    finally:
        migration_engine.dispose()


@pytest.mark.security_evidence(id="PG-TRACE-REDACTION-012", layer="postgres")
def test_runtime_cross_organization_insert_attempts_are_bidirectionally_zero_effect(
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    cross_organization_lineages: tuple[LineageIdentity, LineageIdentity],
) -> None:
    organization_a, organization_b = cross_organization_lineages

    for current_actor, attempted_lineage in (
        (organization_a, organization_b),
        (organization_b, organization_a),
    ):
        with (
            pytest.raises(DBAPIError, match="row-level security"),
            current_user_actor(guarded_runtime_engine, current_actor) as connection,
        ):
            insert_empty_lineage(connection, attempted_lineage)

    for identity in cross_organization_lineages:
        with current_user_actor(guarded_runtime_engine, identity) as connection:
            insert_context_run(connection, identity)

    for current_actor, other_organization_lineage in (
        (organization_a, organization_b),
        (organization_b, organization_a),
    ):
        with (
            pytest.raises(DBAPIError, match="decision audit parent lineage"),
            current_user_actor(guarded_runtime_engine, current_actor) as connection,
        ):
            insert_decision_audit(connection, other_organization_lineage)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            for identity in cross_organization_lineages:
                counts = connection.execute(
                    text(
                        """
                        SELECT
                            (SELECT count(*) FROM context_run
                             WHERE organization_id = :organization_id),
                            (SELECT count(*) FROM decision_audit
                             WHERE organization_id = :organization_id)
                        """
                    ),
                    {"organization_id": identity.organization_id},
                ).one()
                assert tuple(counts) == (1, 0)
    finally:
        migration_engine.dispose()


def test_decision_audit_requires_exact_current_empty_parent_lineage(
    guarded_runtime_engine: Engine,
    lineage_identity: LineageIdentity,
    same_organization_other_actor: LineageIdentity,
) -> None:
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_context_run(connection, lineage_identity)

    with (
        pytest.raises(DBAPIError, match="decision audit parent lineage"),
        current_user_actor(
            guarded_runtime_engine,
            same_organization_other_actor,
        ) as connection,
    ):
        insert_decision_audit(connection, lineage_identity)


@pytest.mark.parametrize(
    ("audit_policy_snapshot_ref", "audit_policy_epoch", "audit_recorded_at"),
    (
        ("policy:other", 1, ACCEPTED_AT),
        (POLICY_SNAPSHOT_REF, 2, ACCEPTED_AT),
        (POLICY_SNAPSHOT_REF, 1, ACCEPTED_AT + timedelta(seconds=1)),
    ),
)
def test_decision_audit_rejects_parent_policy_or_time_mismatch(
    guarded_runtime_engine: Engine,
    lineage_identity: LineageIdentity,
    audit_policy_snapshot_ref: str,
    audit_policy_epoch: int,
    audit_recorded_at: datetime,
) -> None:
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_context_run(connection, lineage_identity)

    with (
        pytest.raises(DBAPIError, match="decision audit parent lineage"),
        current_user_actor(guarded_runtime_engine, lineage_identity) as connection,
    ):
        insert_decision_audit(
            connection,
            lineage_identity,
            policy_snapshot_ref=audit_policy_snapshot_ref,
            policy_epoch=audit_policy_epoch,
            recorded_at=audit_recorded_at,
        )


def test_decision_audit_rejects_delivered_authorized_parent(
    guarded_runtime_engine: Engine,
    lineage_identity: LineageIdentity,
) -> None:
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_context_run(
            connection,
            lineage_identity,
            outcome="delivered_authorized",
            authorized_evidence_refs=(AUTHORIZED_EVIDENCE_REF,),
        )

    with (
        pytest.raises(DBAPIError, match="decision audit parent lineage"),
        current_user_actor(guarded_runtime_engine, lineage_identity) as connection,
    ):
        insert_decision_audit(connection, lineage_identity)


def test_operator_has_no_direct_read_even_with_forgeable_settings(
    guarded_runtime_engine: Engine,
    guarded_operator_engine: Engine,
    lineage_identity: LineageIdentity,
) -> None:
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_empty_lineage(connection, lineage_identity)

    for table_name in (
        "context_run",
        "decision_audit",
        "context_run_operator_read_ticket",
    ):
        with (
            guarded_operator_engine.begin() as connection,
            pytest.raises(ProgrammingError, match="permission denied"),
        ):
            for setting_name, setting_value in (
                ("app.organization_id", str(lineage_identity.organization_id)),
                ("app.operator_authorized", "true"),
                ("app.context_run_operator_ticket_mode", "read"),
                (
                    "app.context_run_operator_ticket_organization_id",
                    str(lineage_identity.organization_id),
                ),
                (
                    "app.context_run_operator_ticket_decision_ref",
                    lineage_identity.decision_ref,
                ),
                ("app.context_run_operator_ticket_digest", "0" * 64),
            ):
                connection.execute(
                    text("SELECT set_config(:name, :value, true)"),
                    {"name": setting_name, "value": setting_value},
                )
            connection.execute(text(f"SELECT * FROM {table_name}"))


def issue_operator_ticket(
    connection: Connection,
    identity: LineageIdentity,
    ticket: str,
) -> bool:
    issued = connection.execute(
        text(
            """
            SELECT issue_context_run_operator_read_ticket(
                :ticket, :organization_id, :decision_ref,
                'security-operator:test', 'operator-request:test',
                'operator-auth-binding:test'
            )
            """
        ),
        {
            "ticket": ticket,
            "organization_id": identity.organization_id,
            "decision_ref": identity.decision_ref,
        },
    ).scalar_one()
    assert type(issued) is bool
    return issued


def read_by_operator_ticket(
    connection: Connection,
    identity: LineageIdentity,
    ticket: str,
) -> tuple[object, ...] | None:
    row = connection.execute(
        text(
            """
            SELECT * FROM read_context_run_by_operator_ticket(
                :ticket, :organization_id, :decision_ref
            )
            """
        ),
        {
            "ticket": ticket,
            "organization_id": identity.organization_id,
            "decision_ref": identity.decision_ref,
        },
    ).one_or_none()
    return tuple(row) if row is not None else None


def test_control_issues_exact_ticket_and_committed_operator_read_consumes_it(
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    lineage_identity: LineageIdentity,
) -> None:
    ticket = "a" * 64
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_empty_lineage(connection, lineage_identity)

    with guarded_control_engine.begin() as connection:
        assert issue_operator_ticket(connection, lineage_identity, ticket) is True
    with guarded_operator_engine.begin() as connection:
        first = read_by_operator_ticket(connection, lineage_identity, ticket)
        assert first is not None
        assert first[0] == lineage_identity.organization_id
        assert first[2] == lineage_identity.decision_ref
        assert first[-2:] == ("no_authorized_evidence", ACCEPTED_AT)
    with guarded_operator_engine.begin() as connection:
        assert read_by_operator_ticket(connection, lineage_identity, ticket) is None


def test_operator_ticket_rollback_restores_exact_read_until_a_commit(
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    lineage_identity: LineageIdentity,
) -> None:
    ticket = "9" * 64
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_empty_lineage(connection, lineage_identity)
    with guarded_control_engine.begin() as connection:
        assert issue_operator_ticket(connection, lineage_identity, ticket) is True

    with guarded_operator_engine.connect() as connection:
        transaction = connection.begin()
        first = read_by_operator_ticket(connection, lineage_identity, ticket)
        assert first is not None
        transaction.rollback()

    with guarded_operator_engine.begin() as connection:
        second = read_by_operator_ticket(connection, lineage_identity, ticket)
        assert second == first
    with guarded_operator_engine.begin() as connection:
        assert read_by_operator_ticket(connection, lineage_identity, ticket) is None


def test_operator_ticket_rejects_wrong_binding_and_can_be_revoked(
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    lineage_identity: LineageIdentity,
) -> None:
    ticket = "b" * 64
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_empty_lineage(connection, lineage_identity)
    with guarded_control_engine.begin() as connection:
        assert issue_operator_ticket(connection, lineage_identity, ticket) is True

    wrong_identity = LineageIdentity(
        organization_id=uuid4(),
        user_id=lineage_identity.user_id,
        membership_id=lineage_identity.membership_id,
        run_ref=lineage_identity.run_ref,
        decision_ref=lineage_identity.decision_ref,
    )
    with guarded_operator_engine.begin() as connection:
        assert read_by_operator_ticket(connection, wrong_identity, ticket) is None
        assert read_by_operator_ticket(connection, lineage_identity, "c" * 64) is None
    with guarded_control_engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT revoke_context_run_operator_read_ticket(:ticket)"),
                {"ticket": ticket},
            ).scalar_one()
            is True
        )
    with guarded_operator_engine.begin() as connection:
        assert read_by_operator_ticket(connection, lineage_identity, ticket) is None


def test_operator_ticket_expiry_is_consumed_without_disclosure(
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    lineage_identity: LineageIdentity,
) -> None:
    ticket = "d" * 64
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_empty_lineage(connection, lineage_identity)
    with guarded_control_engine.begin() as connection:
        assert issue_operator_ticket(connection, lineage_identity, ticket) is True
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE context_run_operator_read_ticket
                    SET issued_at = issued_at - interval '61 seconds',
                        expires_at = expires_at - interval '61 seconds'
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": lineage_identity.organization_id},
            )
    finally:
        migration_engine.dispose()
    with guarded_operator_engine.begin() as connection:
        assert read_by_operator_ticket(connection, lineage_identity, ticket) is None
    with guarded_control_engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT revoke_context_run_operator_read_ticket(:ticket)"),
                {"ticket": ticket},
            ).scalar_one()
            is False
        )


def test_only_exact_roles_can_issue_or_read_operator_ticket(
    guarded_runtime_engine: Engine,
    guarded_control_engine: Engine,
    guarded_operator_engine: Engine,
    lineage_identity: LineageIdentity,
) -> None:
    ticket = "e" * 64
    with current_user_actor(guarded_runtime_engine, lineage_identity) as connection:
        insert_empty_lineage(connection, lineage_identity)
    with (
        guarded_runtime_engine.begin() as connection,
        pytest.raises(ProgrammingError, match="permission denied"),
    ):
        issue_operator_ticket(connection, lineage_identity, ticket)
    with (
        guarded_operator_engine.begin() as connection,
        pytest.raises(ProgrammingError, match="permission denied"),
    ):
        issue_operator_ticket(connection, lineage_identity, ticket)
    with guarded_control_engine.begin() as connection:
        assert issue_operator_ticket(connection, lineage_identity, ticket) is True
    with (
        guarded_control_engine.begin() as connection,
        pytest.raises(ProgrammingError, match="permission denied"),
    ):
        read_by_operator_ticket(connection, lineage_identity, ticket)


def test_catalog_has_no_worker_control_or_denial_detail_surface(
    migration_configuration: DatabaseConfiguration,
) -> None:
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            grants = {
                (row.grantee, row.table_name, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, table_name, privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema = 'public'
                          AND table_name IN (
                              'context_run', 'decision_audit',
                              'context_run_operator_read_ticket'
                          )
                          AND grantee IN (
                              :runtime_role, :operator_role,
                              :worker_role, :control_role, :definer_role
                          )
                        """
                    ),
                    {
                        "runtime_role": RUNTIME_ROLE,
                        "operator_role": OPERATOR_ROLE,
                        "worker_role": WORKER_ROLE,
                        "control_role": CONTROL_ROLE,
                        "definer_role": CONTEXT_RUN_READER_DEFINER_ROLE,
                    },
                )
            }
            audit_columns = set(
                connection.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'decision_audit'
                        """
                    )
                ).scalars()
            )
            rls = {
                row.relname: (row.relrowsecurity, row.relforcerowsecurity)
                for row in connection.execute(
                    text(
                        """
                        SELECT relname, relrowsecurity, relforcerowsecurity
                        FROM pg_class
                        WHERE relnamespace = 'public'::regnamespace
                          AND relname IN (
                              'context_run', 'decision_audit',
                              'context_run_operator_read_ticket'
                          )
                        """
                    )
                )
            }
            parent_guard = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                            trigger_record.tgname,
                            trigger_record.tgenabled,
                            trigger_record.tgisinternal,
                            pg_get_triggerdef(trigger_record.oid, true),
                            function_record.proname,
                            pg_get_userbyid(function_record.proowner),
                            function_record.prosecdef,
                            language_record.lanname,
                            function_record.proconfig,
                            function_record.prosrc
                        FROM pg_trigger AS trigger_record
                        JOIN pg_class AS relation
                          ON relation.oid = trigger_record.tgrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        JOIN pg_proc AS function_record
                          ON function_record.oid = trigger_record.tgfoid
                        JOIN pg_language AS language_record
                          ON language_record.oid = function_record.prolang
                        WHERE namespace.nspname = 'public'
                          AND relation.relname = 'decision_audit'
                          AND trigger_record.tgname =
                              'decision_audit_exact_empty_parent_guard'
                        """
                    )
                ).one()
            )
            parent_guard_grants = {
                (row.grantee, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT grantee, privilege_type
                        FROM information_schema.routine_privileges
                        WHERE routine_schema = 'public'
                          AND routine_name =
                              'decision_audit_require_exact_empty_parent'
                        """
                    )
                )
            }
            ticket_functions = {
                row.proname: (
                    row.owner,
                    row.prosecdef,
                    row.proconfig,
                    row.identity_arguments,
                )
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            function_record.proname,
                            pg_get_userbyid(function_record.proowner) AS owner,
                            function_record.prosecdef,
                            function_record.proconfig,
                            pg_get_function_identity_arguments(
                                function_record.oid
                            ) AS identity_arguments
                        FROM pg_proc AS function_record
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function_record.pronamespace
                        WHERE namespace.nspname = 'public'
                          AND function_record.proname IN (
                              'issue_context_run_operator_read_ticket',
                              'revoke_context_run_operator_read_ticket',
                              'read_context_run_by_operator_ticket'
                          )
                        """
                    )
                )
            }
            ticket_function_grants = {
                (row.routine_name, row.grantee, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            function_record.proname AS routine_name,
                            pg_get_userbyid(privilege_record.grantee) AS grantee,
                            privilege_record.privilege_type
                        FROM pg_proc AS function_record
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = function_record.pronamespace
                        CROSS JOIN LATERAL aclexplode(
                            COALESCE(
                                function_record.proacl,
                                acldefault('f', function_record.proowner)
                            )
                        ) AS privilege_record
                        WHERE namespace.nspname = 'public'
                          AND function_record.proname IN (
                              'issue_context_run_operator_read_ticket',
                              'revoke_context_run_operator_read_ticket',
                              'read_context_run_by_operator_ticket'
                          )
                          AND privilege_record.grantee <> function_record.proowner
                        """
                    )
                )
            }
    finally:
        engine.dispose()

    assert grants == {
        (RUNTIME_ROLE, "context_run", "INSERT"),
        (RUNTIME_ROLE, "decision_audit", "INSERT"),
        (CONTEXT_RUN_READER_DEFINER_ROLE, "context_run", "SELECT"),
        (CONTEXT_RUN_READER_DEFINER_ROLE, "decision_audit", "SELECT"),
        (
            CONTEXT_RUN_READER_DEFINER_ROLE,
            "context_run_operator_read_ticket",
            "SELECT",
        ),
        (
            CONTEXT_RUN_READER_DEFINER_ROLE,
            "context_run_operator_read_ticket",
            "INSERT",
        ),
        (
            CONTEXT_RUN_READER_DEFINER_ROLE,
            "context_run_operator_read_ticket",
            "DELETE",
        ),
    }
    assert audit_columns == {
        "organization_id",
        "run_ref",
        "decision_ref",
        "policy_snapshot_ref",
        "policy_epoch",
        "category",
        "recorded_at",
    }
    assert rls == {
        "context_run": (True, True),
        "decision_audit": (True, True),
        "context_run_operator_read_ticket": (True, True),
    }
    assert parent_guard[:3] == (
        "decision_audit_exact_empty_parent_guard",
        "O",
        False,
    )
    normalized_trigger = str(parent_guard[3]).lower()
    assert "before insert" in normalized_trigger
    assert "for each row" in normalized_trigger
    assert "decision_audit_require_exact_empty_parent()" in normalized_trigger
    assert parent_guard[4:9] == (
        "decision_audit_require_exact_empty_parent",
        "context_engine_migrator",
        True,
        "plpgsql",
        ["search_path=pg_catalog, pg_temp"],
    )
    normalized_guard = str(parent_guard[9]).lower()
    for required_clause in (
        "session_user",
        "parent_run.user_id",
        "parent_run.membership_id",
        "parent_run.membership_version",
        "parent_run.principal_ref",
        "parent_run.request_id",
        "parent_run.authentication_binding_ref",
        "parent_run.accepted_at",
        "parent_run.outcome = 'delivered_empty'",
        "parent_run.policy_snapshot_ref = new.policy_snapshot_ref",
        "parent_run.policy_epoch = new.policy_epoch",
        "parent_run.finalized_at = new.recorded_at",
    ):
        assert required_clause in normalized_guard
    assert parent_guard_grants == {
        ("context_engine_migrator", "EXECUTE"),
    }
    assert set(ticket_functions) == {
        "issue_context_run_operator_read_ticket",
        "revoke_context_run_operator_read_ticket",
        "read_context_run_by_operator_ticket",
    }
    for owner, security_definer, configuration, _ in ticket_functions.values():
        assert owner == CONTEXT_RUN_READER_DEFINER_ROLE
        assert security_definer is True
        assert configuration == [
            "search_path=pg_catalog, pg_temp",
            "row_security=on",
        ]
    assert ticket_functions["issue_context_run_operator_read_ticket"][3] == (
        "requested_ticket text, requested_organization_id uuid, "
        "requested_decision_ref text, requested_operator_ref text, "
        "requested_request_id text, "
        "requested_authentication_binding_ref text"
    )
    assert ticket_function_grants == {
        (
            "issue_context_run_operator_read_ticket",
            CONTROL_ROLE,
            "EXECUTE",
        ),
        (
            "revoke_context_run_operator_read_ticket",
            CONTROL_ROLE,
            "EXECUTE",
        ),
        (
            "read_context_run_by_operator_ticket",
            OPERATOR_ROLE,
            "EXECUTE",
        ),
    }


@pytest.mark.parametrize(
    "configuration_fixture",
    ("control_configuration", "worker_configuration"),
)
def test_control_and_worker_cannot_read_or_affect_context_runs(
    request: pytest.FixtureRequest,
    configuration_fixture: str,
    lineage_identity: LineageIdentity,
) -> None:
    configuration = request.getfixturevalue(configuration_fixture)
    assert isinstance(configuration, DatabaseConfiguration)
    engine = create_database_engine(configuration)
    try:
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            engine.begin() as connection,
        ):
            connection.execute(text("SELECT * FROM context_run"))
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            engine.begin() as connection,
        ):
            connection.execute(
                text(
                    "DELETE FROM context_run WHERE organization_id = :organization_id"
                ),
                {"organization_id": lineage_identity.organization_id},
            )
    finally:
        engine.dispose()
