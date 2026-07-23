"""Test-only digest recording egress issuance authority."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from engine.runtime.egress import (
    EgressGrantIssuancePort,
    EgressGrantIssuanceSession,
    EgressGrantIssue,
    _close_egress_grant_issuance_scope,
    _construct_egress_grant_issuance_session,
    _open_egress_grant_issuance_scope,
)


class RecordingEgressIssuancePort:
    def __init__(self) -> None:
        self.calls: list[tuple[EgressGrantIssue, bytes]] = []

    def issue(self, request: EgressGrantIssue, grant_digest: bytes) -> bool:
        self.calls.append((request, grant_digest))
        return True


@contextmanager
def recording_egress_issuance_session() -> Iterator[
    tuple[EgressGrantIssuanceSession, RecordingEgressIssuancePort]
]:
    scope = _open_egress_grant_issuance_scope()
    port = RecordingEgressIssuancePort()
    try:
        yield (
            _construct_egress_grant_issuance_session(
                authority_scope=scope,
                port=cast(EgressGrantIssuancePort, port),
            ),
            port,
        )
    finally:
        _close_egress_grant_issuance_scope(scope)
