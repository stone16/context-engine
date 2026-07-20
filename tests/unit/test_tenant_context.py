from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine

from engine.persistence import organization_transaction


class _ScalarResult:
    def __init__(self, value: str) -> None:
        self._value = value

    def scalar_one(self) -> str:
        return self._value


class _FakeConnection:
    def __init__(self, events: list[str], observed_value: str) -> None:
        self._events = events
        self._observed_value = observed_value

    def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> _ScalarResult | None:
        sql = str(statement)
        self._events.append(f"sql:{sql}")
        if "set_config" in sql:
            assert parameters is not None
            assert set(parameters) == {"organization_id"}
            assert isinstance(parameters["organization_id"], str)
            return None
        assert "current_setting('app.organization_id', true)" in sql
        assert parameters is None
        return _ScalarResult(self._observed_value)

    def record_caller_query(self) -> None:
        self._events.append("caller-query")


class _BeginContext(AbstractContextManager[Connection]):
    def __init__(self, connection: _FakeConnection, events: list[str]) -> None:
        self._connection = connection
        self._events = events

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
        return None


class _FakeEngine:
    def __init__(self, observed_value: str) -> None:
        self.events: list[str] = []
        self.connection = _FakeConnection(self.events, observed_value)

    def begin(self, **kwargs: Any) -> AbstractContextManager[Connection]:
        assert kwargs == {}
        return _BeginContext(self.connection, self.events)


def test_organization_transaction_binds_context_before_caller_queries() -> None:
    """PROP-TENANT-OWNERSHIP-001: no query runs before local context."""

    organization_id = uuid4()
    fake_engine = _FakeEngine(str(organization_id))

    with organization_transaction(cast(Engine, fake_engine), organization_id) as db:
        cast(_FakeConnection, db).record_caller_query()

    assert fake_engine.events[0] == "begin"
    assert "set_config('app.organization_id', :organization_id, true)" in (
        fake_engine.events[1]
    )
    assert "current_setting('app.organization_id', true)" in fake_engine.events[2]
    assert fake_engine.events[3:] == ["caller-query", "commit"]


def test_organization_transaction_rolls_back_when_caller_fails() -> None:
    organization_id = uuid4()
    fake_engine = _FakeEngine(str(organization_id))

    with (
        pytest.raises(LookupError, match="operation failed"),
        organization_transaction(cast(Engine, fake_engine), organization_id) as db,
    ):
        cast(_FakeConnection, db).record_caller_query()
        raise LookupError("operation failed")

    assert fake_engine.events[-1] == "rollback"


@pytest.mark.parametrize("invalid_id", ["org-a", None, 1])
def test_organization_transaction_rejects_non_uuid_context(
    invalid_id: object,
) -> None:
    """RLS-FAIL-CLOSED-003: there is no string or missing-context default."""

    fake_engine = _FakeEngine("unused")

    with (
        pytest.raises(TypeError, match="organization_id must be a UUID"),
        organization_transaction(
            cast(Engine, fake_engine), cast(UUID, invalid_id)
        ),
    ):
        pytest.fail("an invalid context must never reach a caller query")

    assert fake_engine.events == []


def test_organization_transaction_rejects_failed_context_readback() -> None:
    requested_id = uuid4()
    fake_engine = _FakeEngine(str(uuid4()))

    with (
        pytest.raises(RuntimeError, match="organization context binding failed"),
        organization_transaction(cast(Engine, fake_engine), requested_id),
    ):
        pytest.fail("a mismatched context must never reach a caller query")

    assert fake_engine.events[-1] == "rollback"
