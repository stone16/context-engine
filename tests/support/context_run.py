"""Test-only durable ContextRun authority and explicit query keyring."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from engine.runtime.context_run import (
    ContextRunPersistencePort,
    ContextRunPersistenceSession,
    ContextRunRecord,
    DecisionAuditRecord,
    _close_context_run_persistence_scope,
    _construct_context_run_persistence_session,
    _open_context_run_persistence_scope,
)
from engine.runtime.package_digest import QueryDigestKeyring

TEST_QUERY_DIGEST_KEYRING = QueryDigestKeyring(
    active_version=1,
    keys={1: b"context-engine-test-query-key-v1!"},
)


class RecordingContextRunPort:
    """In-memory durable-write twin; callers can inspect finalized records."""

    def __init__(self) -> None:
        self.calls: list[tuple[ContextRunRecord, DecisionAuditRecord | None]] = []

    def persist(
        self,
        run: ContextRunRecord,
        audit: DecisionAuditRecord | None,
    ) -> None:
        self.calls.append((run, audit))


@contextmanager
def recording_context_run_session(
    *,
    port: RecordingContextRunPort | None = None,
) -> Iterator[tuple[ContextRunPersistenceSession, RecordingContextRunPort]]:
    """Open one explicit recording session with a bounded test lifetime."""

    selected_port = port or RecordingContextRunPort()
    persistence_scope = _open_context_run_persistence_scope()
    try:
        yield (
            _construct_context_run_persistence_session(
                authority_scope=persistence_scope,
                port=cast(ContextRunPersistencePort, selected_port),
            ),
            selected_port,
        )
    finally:
        _close_context_run_persistence_scope(persistence_scope)
