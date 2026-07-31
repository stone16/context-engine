from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

import engine.persistence.feedback as feedback_persistence
from engine.learning.feedback import FeedbackBindingUnavailable
from engine.persistence.feedback import PostgreSQLFeedbackInbox

ORGANIZATION_ID = UUID("00000000-0000-4000-8000-000000000152")
FEEDBACK_REF = "fb_" + "5" * 64


def _document() -> dict[str, object]:
    return {
        "citations": [
            {
                "evidenceRef": "ev_" + "6" * 64,
                "fragmentRef": "synthetic-fragment-feedback",
                "resourceRef": "synthetic-resource-feedback",
                "revisionRef": "synthetic-revision-feedback",
                "sourceRef": "synthetic-source-feedback",
            }
        ],
        "feedbackRef": FEEDBACK_REF,
        "note": "synthetic-feedback-note",
        "organizationId": str(ORGANIZATION_ID),
        "packageDigest": "3" * 64,
        "packageRef": "pkg_" + "2" * 32,
        "rating": "not_helpful",
        "recordedAt": datetime(2026, 7, 31, tzinfo=UTC).isoformat(),
        "releaseGeneration": 7,
        "releaseRef": "rel_" + "4" * 64,
        "runRef": "run_" + "1" * 32,
        "schemaVersion": "context-engine-feedback-evidence-v1",
    }


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _Connection:
    def __init__(
        self,
        result: object,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _Result:
        sql = str(statement)
        values = dict(parameters or {})
        self.calls.append((sql, values))
        if self.error is not None:
            raise self.error
        return _Result(self.result)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def begin(self) -> Iterator[_Connection]:
        yield self.connection


@pytest.fixture
def accept_fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_persistence, "Engine", _Engine)


def _inbox(connection: _Connection) -> PostgreSQLFeedbackInbox:
    return PostgreSQLFeedbackInbox(cast(Engine, _Engine(connection)))


def test_inbox_requires_learning_role_and_calls_only_the_exact_read_function(
    accept_fake_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(_document())
    guarded: list[object] = []
    monkeypatch.setattr(
        feedback_persistence,
        "assert_learning_role",
        lambda value: guarded.append(value),
    )

    evidence = _inbox(connection).find_exact(ORGANIZATION_ID, FEEDBACK_REF)

    assert guarded == [connection]
    assert len(connection.calls) == 1
    sql, parameters = connection.calls[0]
    assert "context_learning_read_feedback_evidence" in sql
    assert "promote" not in sql
    assert "grant" not in sql
    assert parameters == {
        "organization_id": ORGANIZATION_ID,
        "feedback_ref": FEEDBACK_REF,
    }
    assert evidence.binding.release_generation == 7
    rendered = repr(evidence)
    assert "synthetic-feedback-note" not in rendered
    assert "denied" not in rendered.casefold()


@pytest.mark.parametrize("projection", [None, {}, {"feedbackRef": FEEDBACK_REF}])
def test_inbox_refuses_missing_or_malformed_projection(
    projection: object,
    accept_fake_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        feedback_persistence,
        "assert_learning_role",
        lambda connection: None,
    )

    with pytest.raises(FeedbackBindingUnavailable, match="unavailable|malformed"):
        _inbox(_Connection(projection)).find_exact(ORGANIZATION_ID, FEEDBACK_REF)


@pytest.mark.parametrize(
    "failure",
    [
        AssertionError("wrong role"),
        OperationalError("SELECT", {}, Exception("database refused")),
    ],
)
def test_inbox_normalizes_role_and_database_failures(
    failure: Exception,
    accept_fake_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if isinstance(failure, AssertionError):
        monkeypatch.setattr(
            feedback_persistence,
            "assert_learning_role",
            lambda connection: (_ for _ in ()).throw(failure),
        )
        connection = _Connection(_document())
    else:
        monkeypatch.setattr(
            feedback_persistence,
            "assert_learning_role",
            lambda connection: None,
        )
        connection = _Connection(None, error=failure)

    with pytest.raises(
        FeedbackBindingUnavailable,
        match="feedback exact binding is unavailable",
    ):
        _inbox(connection).find_exact(ORGANIZATION_ID, FEEDBACK_REF)
