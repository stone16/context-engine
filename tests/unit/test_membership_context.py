from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import OperationalError

import engine.persistence.membership_context as membership_context_module
from engine.persistence.membership_context import (
    MembershipAuthorityUnavailable,
    MembershipIdentity,
    MembershipNotCurrent,
    PostgreSQLMembershipAuthority,
)
from engine.runtime.actor import (
    MembershipRejectionAuditReceipt,
    MembershipRejectionCategory,
)
from engine.runtime.materialized import (
    _require_active_materialized_projection_session,
)

CHECKED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value

    def one_or_none(self) -> _MembershipRow | None:
        return cast(_MembershipRow | None, self._value)


class _MembershipRow:
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id


class _FakeConnection:
    def __init__(
        self,
        events: list[str],
        settings: dict[str, str],
        row: _MembershipRow | None,
        *,
        fail_on_query: bool = False,
    ) -> None:
        self._events = events
        self._settings = settings
        self._row = row
        self._fail_on_query = fail_on_query

    def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> _ScalarResult:
        sql = str(statement)
        self._events.append(f"sql:{sql}")
        if "FROM pg_roles AS role" in sql:
            return _MappingResult(
                {
                    "current_role": "context_engine_runtime",
                    "session_role": "context_engine_runtime",
                    "is_superuser": False,
                    "bypasses_rls": False,
                    "inherits_roles": False,
                    "can_create_roles": False,
                    "can_create_databases": False,
                    "can_replicate": False,
                    "has_no_role_memberships": True,
                    "is_migrator_member": False,
                    "can_use_migrator": False,
                    "owns_database": False,
                    "owns_public_schema": False,
                    "owns_no_public_relations": True,
                    "can_create_in_database": False,
                    "can_create_temporary_tables": False,
                    "can_create_in_public_schema": False,
                }
            )
        if "set_config" in sql:
            assert parameters is not None
            name = cast(str, parameters["setting_name"])
            value = cast(str, parameters["setting_value"])
            self._settings[name] = value
            return _ScalarResult(value)
        if "current_setting" in sql:
            assert parameters is not None
            return _ScalarResult(self._settings[cast(str, parameters["setting_name"])])
        if self._fail_on_query:
            raise OperationalError("query", {}, RuntimeError("database unavailable"))
        return _ScalarResult(self._row)


class _MappingResult(_ScalarResult):
    def mappings(self) -> _MappingResult:
        return self

    def one(self) -> object:
        return self._value


class _BeginContext(AbstractContextManager[Connection]):
    def __init__(
        self,
        connection: _FakeConnection,
        events: list[str],
        *,
        fail_on_commit: bool = False,
    ) -> None:
        self._connection = connection
        self._events = events
        self._fail_on_commit = fail_on_commit

    def __enter__(self) -> Connection:
        self._events.append("begin")
        return cast(Connection, self._connection)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del exc_value, traceback
        self._events.append("rollback" if exc_type is not None else "commit")
        if exc_type is None and self._fail_on_commit:
            raise OperationalError(
                "commit",
                {},
                RuntimeError("database unavailable during commit"),
            )
        return None


class _FakeEngine:
    def __init__(
        self,
        row: _MembershipRow | None,
        *,
        fail_on_query: bool = False,
        fail_on_begin: bool = False,
        fail_on_commit: bool = False,
    ) -> None:
        self.events: list[str] = []
        self.settings: dict[str, str] = {}
        self.connection = _FakeConnection(
            self.events,
            self.settings,
            row,
            fail_on_query=fail_on_query,
        )
        self._fail_on_begin = fail_on_begin
        self._fail_on_commit = fail_on_commit

    def begin(self, **kwargs: Any) -> AbstractContextManager[Connection]:
        assert kwargs == {}
        if self._fail_on_begin:
            raise OperationalError(
                "begin",
                {},
                RuntimeError("database unavailable during begin"),
            )
        return _BeginContext(
            self.connection,
            self.events,
            fail_on_commit=self._fail_on_commit,
        )


def identity() -> MembershipIdentity:
    return MembershipIdentity(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        user_id=UUID("9d429284-aea8-467b-a177-d4cdb7670a65"),
        membership_id=UUID("d8e1c5bf-bcd0-48a9-b651-b539225bcad8"),
        membership_version=7,
        principal_ref="principal-from-auth",
        request_id="request-1",
        authentication_binding_ref="binding-from-auth",
        checked_at=CHECKED_AT,
    )


def test_current_membership_transaction_binds_every_actor_fact_before_lookup() -> None:
    expected = identity()
    fake_engine = _FakeEngine(_MembershipRow(expected.user_id))
    authority = PostgreSQLMembershipAuthority(cast(Engine, fake_engine))

    with authority.current_user_actor(expected) as verification:
        fake_engine.events.append("runtime-resolve")
        assert verification.user_id == expected.user_id
        assert verification.membership_id == expected.membership_id
        assert verification.materialized_projection_session is not None
        _require_active_materialized_projection_session(
            verification.materialized_projection_session
        )

    assert verification.materialized_projection_session is not None
    with pytest.raises(ValueError, match="active materialized projection scope"):
        _require_active_materialized_projection_session(
            verification.materialized_projection_session
        )

    assert fake_engine.events[0] == "begin"
    lookup_position = next(
        index
        for index, event in enumerate(fake_engine.events)
        if "FROM membership" in event
    )
    assert lookup_position > 17
    assert fake_engine.events[-2:] == ["runtime-resolve", "commit"]
    assert fake_engine.settings == {
        "app.organization_id": str(expected.organization_id),
        "app.actor_kind": "user",
        "app.user_id": str(expected.user_id),
        "app.membership_id": str(expected.membership_id),
        "app.membership_version": "7",
        "app.principal_ref": expected.principal_ref,
        "app.request_id": expected.request_id,
        "app.authentication_binding_ref": expected.authentication_binding_ref,
        "app.checked_at": "2026-07-21T08:00:00Z",
    }


@pytest.mark.parametrize("row", [None, _MembershipRow(uuid4())])
def test_missing_or_mismatched_membership_is_one_generic_denial(
    row: _MembershipRow | None,
) -> None:
    expected = identity()
    fake_engine = _FakeEngine(row)
    authority = PostgreSQLMembershipAuthority(cast(Engine, fake_engine))

    with (
        pytest.raises(MembershipNotCurrent) as rejection,
        authority.current_user_actor(expected),
    ):
        pytest.fail("an invalid Membership must not reach Runtime")

    assert fake_engine.events[-1] == "rollback"
    assert rejection.value.audit_receipt == MembershipRejectionAuditReceipt(
        category=MembershipRejectionCategory.NOT_CURRENT,
        denied_detail_count=0,
    )
    assert repr(rejection.value.audit_receipt) == (
        "MembershipRejectionAuditReceipt("
        "category=<MembershipRejectionCategory.NOT_CURRENT: "
        "'membership_not_current'>, denied_detail_count=0)"
    )


def test_database_fault_is_not_reclassified_as_membership_denial() -> None:
    fake_engine = _FakeEngine(None, fail_on_query=True)
    authority = PostgreSQLMembershipAuthority(cast(Engine, fake_engine))

    with (
        pytest.raises(MembershipAuthorityUnavailable),
        authority.current_user_actor(identity()),
    ):
        pytest.fail("an unavailable authority must not reach Runtime")

    assert fake_engine.events[-1] == "rollback"


def test_non_runtime_database_role_never_opens_membership_authority_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = identity()
    fake_engine = _FakeEngine(_MembershipRow(expected.user_id))
    authority = PostgreSQLMembershipAuthority(cast(Engine, fake_engine))

    def reject_role(connection: Connection) -> None:
        del connection
        raise AssertionError("not the exact Runtime role")

    monkeypatch.setattr(
        membership_context_module,
        "assert_runtime_role",
        reject_role,
    )

    with (
        pytest.raises(MembershipAuthorityUnavailable, match="Runtime role"),
        authority.current_user_actor(expected),
    ):
        pytest.fail("a privileged database role must not reach Runtime")

    assert fake_engine.events == ["begin", "rollback"]


@pytest.mark.parametrize("failure_point", ["begin", "commit"])
def test_transaction_boundary_fault_is_authority_unavailability(
    failure_point: str,
) -> None:
    expected = identity()
    fake_engine = _FakeEngine(
        _MembershipRow(expected.user_id),
        fail_on_begin=failure_point == "begin",
        fail_on_commit=failure_point == "commit",
    )
    authority = PostgreSQLMembershipAuthority(cast(Engine, fake_engine))

    with (
        pytest.raises(MembershipAuthorityUnavailable),
        authority.current_user_actor(expected),
    ):
        fake_engine.events.append("runtime-resolve")


@pytest.mark.parametrize(
    "change",
    [
        {"membership_version": 0},
        {"membership_version": True},
        {"membership_version": 1 << 63},
        {"principal_ref": " "},
        {"request_id": ""},
        {"authentication_binding_ref": object()},
        {"checked_at": datetime(2026, 7, 21, 8, 0)},
    ],
)
def test_membership_identity_is_closed_and_exact(change: dict[str, object]) -> None:
    values = {
        "organization_id": uuid4(),
        "user_id": uuid4(),
        "membership_id": uuid4(),
        "membership_version": 1,
        "principal_ref": "principal",
        "request_id": "request",
        "authentication_binding_ref": "binding",
        "checked_at": CHECKED_AT,
    }
    values.update(change)

    with pytest.raises((TypeError, ValueError), match=r"Membership|membership"):
        MembershipIdentity(**cast(Any, values))
