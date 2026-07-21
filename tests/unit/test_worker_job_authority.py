from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence import (
    MAX_WORKER_LEASE_TTL_SECONDS,
    PostgreSQLWorkerLeaseAuthority,
    PostgreSQLWorkerLeaseIssuer,
    WorkerExecutionIdentity,
    WorkerLeaseIssueRequest,
    WorkerLeaseRedemption,
)
from engine.supply import (
    WORKER_LEASE_OPERATION,
    WorkerLeaseClaims,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkerLeaseToken,
    WorkNotAvailable,
)

NOW = datetime(2026, 7, 22, 10, 0, tzinfo=UTC)
SIGNING_KEY = b"worker-authority-ordering-key-at-least-32-bytes"


class ForbiddenDatabaseEngine:
    def __init__(self) -> None:
        self.connection_attempts = 0

    def begin(self) -> None:
        self.connection_attempts += 1
        raise AssertionError("untrusted lease reached the database")


class FailingDatabaseEngine:
    def __init__(self, marker: bytes = b"n" * 32) -> None:
        self._marker = marker

    def begin(self) -> None:
        raise SQLAlchemyError("nonce=" + self._marker.hex())


def exception_tree(error: BaseException) -> tuple[str, ...]:
    rendered: list[str] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None and not current.__suppress_context__:
            pending.append(current.__context__)
    return tuple(rendered)


def signed_attempt() -> tuple[
    WorkerLeaseCodec,
    WorkerLeaseToken,
    WorkerLeaseRedemption,
    WorkerExecutionIdentity,
]:
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=3, keys={3: SIGNING_KEY})
    )
    claims = WorkerLeaseClaims(
        signing_key_version=3,
        organization_id=uuid4(),
        job_id=uuid4(),
        service_principal_id=uuid4(),
        workload="supply.noop",
        worker_audience="context-engine-worker",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        nonce=b"n" * 32,
    )
    token = codec.mint(claims)
    return (
        codec,
        token,
        WorkerLeaseRedemption(
            token=token,
            expected_organization_id=claims.organization_id,
            expected_job_id=claims.job_id,
        ),
        WorkerExecutionIdentity(
            service_principal_id=claims.service_principal_id,
            workload=claims.workload,
            worker_audience=claims.worker_audience,
        ),
    )


@pytest.mark.parametrize(
    "invalid_case",
    [
        "tampered",
        "organization",
        "job",
        "service_principal",
        "workload",
        "audience",
        "expired",
    ],
)
def test_untrusted_lease_is_rejected_before_any_database_access(
    invalid_case: str,
) -> None:
    codec, token, attempt, identity = signed_attempt()
    forbidden_engine = ForbiddenDatabaseEngine()
    checked_at = NOW + timedelta(minutes=1)
    if invalid_case == "tampered":
        opaque = token.serialize()
        replacement = "A" if opaque[-1] != "A" else "B"
        attempt = replace(
            attempt,
            token=WorkerLeaseToken(f"{opaque[:-1]}{replacement}"),
        )
    elif invalid_case == "organization":
        attempt = replace(attempt, expected_organization_id=uuid4())
    elif invalid_case == "job":
        attempt = replace(attempt, expected_job_id=uuid4())
    elif invalid_case == "service_principal":
        identity = replace(identity, service_principal_id=uuid4())
    elif invalid_case == "workload":
        identity = replace(identity, workload="wrong-workload")
    elif invalid_case == "audience":
        identity = replace(identity, worker_audience="wrong-audience")
    elif invalid_case == "expired":
        checked_at = NOW + timedelta(minutes=5)
    authority = PostgreSQLWorkerLeaseAuthority(
        cast(Engine, forbidden_engine),
        codec,
        identity,
        clock=lambda: checked_at,
    )

    with pytest.raises(WorkNotAvailable, match="^work not available$"):
        authority.complete_noop(attempt)

    assert forbidden_engine.connection_attempts == 0


def test_redemption_message_cannot_supply_receiver_identity_time_or_operation() -> None:
    _codec, _token, attempt, identity = signed_attempt()

    assert [field.name for field in fields(attempt)] == [
        "token",
        "expected_organization_id",
        "expected_job_id",
    ]
    assert [field.name for field in fields(identity)] == [
        "service_principal_id",
        "workload",
        "worker_audience",
        "operation",
    ]
    assert identity.operation == WORKER_LEASE_OPERATION


def test_redemption_database_failure_tree_never_retains_token_or_nonce() -> None:
    codec, token, attempt, identity = signed_attempt()
    authority = PostgreSQLWorkerLeaseAuthority(
        cast(Engine, FailingDatabaseEngine()),
        codec,
        identity,
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError) as failed:
        authority.complete_noop(attempt)

    forbidden = (token.serialize(), (b"n" * 32).hex())
    rendered = exception_tree(failed.value)
    assert all(marker not in value for marker in forbidden for value in rendered)


def test_issuance_database_failure_tree_never_retains_generated_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codec, _token, attempt, identity = signed_attempt()
    generated_nonce = b"i" * 32
    monkeypatch.setattr(
        "engine.persistence.worker_jobs.generate_worker_lease_nonce",
        lambda: generated_nonce,
    )
    issuer = PostgreSQLWorkerLeaseIssuer(
        cast(Engine, FailingDatabaseEngine(generated_nonce)),
        codec,
    )
    request = WorkerLeaseIssueRequest(
        organization_id=attempt.expected_organization_id,
        job_id=attempt.expected_job_id,
        service_principal_id=identity.service_principal_id,
        workload=identity.workload,
        worker_audience=identity.worker_audience,
    )

    with pytest.raises(RuntimeError) as failed:
        issuer.issue_noop_lease(request)

    rendered = exception_tree(failed.value)
    assert all(generated_nonce.hex() not in value for value in rendered)


def test_lease_issue_request_cannot_supply_lease_times() -> None:
    request = WorkerLeaseIssueRequest(
        organization_id=uuid4(),
        job_id=uuid4(),
        service_principal_id=uuid4(),
        workload="supply.noop",
        worker_audience="context-engine-worker",
    )

    assert [field.name for field in fields(request)] == [
        "organization_id",
        "job_id",
        "service_principal_id",
        "workload",
        "worker_audience",
        "actor_kind",
        "operation",
    ]


@pytest.mark.parametrize("ttl", [0, MAX_WORKER_LEASE_TTL_SECONDS + 1, True])
def test_lease_issuer_rejects_unbounded_or_ambiguous_lifetime(ttl: int) -> None:
    codec, _token, _attempt, _identity = signed_attempt()

    with pytest.raises(ValueError, match="TTL must be between"):
        PostgreSQLWorkerLeaseIssuer(
            cast(Engine, ForbiddenDatabaseEngine()),
            codec,
            lease_ttl_seconds=ttl,
        )
