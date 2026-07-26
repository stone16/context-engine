from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from adapters.embeddings import DeterministicEmbeddingTwin, ExternalEmbeddingProvider
from applications.worker import (
    DEFAULT_WORKER_MAX_FILE_BYTES,
    FileDispatchCycleResult,
    _embedding_provider,
    _file_dispatch_roots,
    _file_read_limits,
    _worker_database_time,
    dispatch_file_imports_until_stopped,
    dispatch_one_file_import,
)
from engine.control import FileImportPath, FileRootRef, SourceRef
from engine.persistence.file_imports import FileImportRefused, FileImportUnavailable
from engine.persistence.worker_jobs import (
    FILE_DISPATCH_MAX_LEASE_GENERATION,
    FileDispatchLease,
    FileDispatchNoWork,
    PostgreSQLFileDispatchAuthority,
    _database_timestamp_utc,
)
from engine.supply import (
    CONTEXT_FRAGMENT_EMBEDDING_DIMENSION,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkerLeaseRejectionAuditReceipt,
    WorkerLeaseToken,
    WorkNotAvailable,
)


def test_dispatch_no_work_is_a_closed_content_free_outcome() -> None:
    outcome = FileDispatchNoWork()

    assert asdict(outcome) == {"status": "no_work"}
    assert repr(outcome) == "FileDispatchNoWork(status='no_work')"


def test_dispatch_lease_redacts_every_routing_and_capability_value() -> None:
    claimed_at = datetime(2026, 7, 25, 10, tzinfo=UTC)
    claim = FileDispatchLease(
        token=WorkerLeaseToken("lease-token"),
        organization_id=uuid4(),
        job_id=uuid4(),
        source_ref=SourceRef(uuid4()),
        service_principal_id=uuid4(),
        lease_generation=1,
        issued_at=claimed_at,
        expires_at=claimed_at + timedelta(minutes=5),
    )

    rendered = repr(claim)
    assert rendered == "FileDispatchLease(lease_generation=1)"
    assert "lease-token" not in rendered
    assert "organization_id" not in rendered
    assert "source_ref" not in rendered
    assert claim.redemption.expected_job_id == claim.job_id
    assert claim.redemption.expected_source_ref == claim.source_ref


@pytest.mark.parametrize("generation", [0, 5])
def test_dispatch_rejects_generation_outside_automatic_budget(generation: int) -> None:
    claimed_at = datetime(2026, 7, 25, 10, tzinfo=UTC)

    with pytest.raises(ValueError, match="automatic generation budget"):
        FileDispatchLease(
            token=WorkerLeaseToken("lease-token"),
            organization_id=uuid4(),
            job_id=uuid4(),
            source_ref=SourceRef(uuid4()),
            service_principal_id=uuid4(),
            lease_generation=generation,
            issued_at=claimed_at,
            expires_at=claimed_at + timedelta(minutes=5),
        )


@pytest.mark.parametrize("generation", [1, 2, 3, 4])
def test_dispatch_accepts_only_the_versioned_automatic_generations(
    generation: int,
) -> None:
    claimed_at = datetime(2026, 7, 25, 10, tzinfo=UTC)

    claim = FileDispatchLease(
        token=WorkerLeaseToken("lease-token"),
        organization_id=uuid4(),
        job_id=uuid4(),
        source_ref=SourceRef(uuid4()),
        service_principal_id=uuid4(),
        lease_generation=generation,
        issued_at=claimed_at,
        expires_at=claimed_at + timedelta(minutes=5),
    )

    assert claim.lease_generation == generation
    assert FILE_DISPATCH_MAX_LEASE_GENERATION == 4


class _NoWorkAuthority:
    def claim(self) -> FileDispatchNoWork:
        return FileDispatchNoWork()


class _ForbiddenWorkerFactory:
    def __call__(self, _receiver: object) -> object:
        raise AssertionError("no-work must not construct a worker")


def test_dispatch_cycle_stops_on_content_free_no_work() -> None:
    result = dispatch_one_file_import(
        _NoWorkAuthority(),
        _ForbiddenWorkerFactory(),  # type: ignore[arg-type]
    )

    assert asdict(result) == {"outcome": "no_work", "status": "complete"}


class _OneClaimAuthority:
    def __init__(self, claim: FileDispatchLease) -> None:
        self._claim = claim

    def claim(self) -> FileDispatchLease:
        return self._claim


class _RefusingWorker:
    def run(self, _redemption: object) -> object:
        raise WorkNotAvailable(WorkerLeaseRejectionAuditReceipt(lease_digest="a" * 64))


class _RefusingWorkerFactory:
    def __call__(self, _receiver: object) -> _RefusingWorker:
        return _RefusingWorker()


def test_dispatch_cycle_reports_job_refusal_without_routing_content() -> None:
    claimed_at = datetime(2026, 7, 25, 10, tzinfo=UTC)
    claim = FileDispatchLease(
        token=WorkerLeaseToken("lease-token"),
        organization_id=uuid4(),
        job_id=uuid4(),
        source_ref=SourceRef(uuid4()),
        service_principal_id=uuid4(),
        lease_generation=1,
        issued_at=claimed_at,
        expires_at=claimed_at + timedelta(minutes=5),
    )

    result = dispatch_one_file_import(
        _OneClaimAuthority(claim),
        _RefusingWorkerFactory(),  # type: ignore[arg-type]
    )

    assert asdict(result) == {"outcome": "refused", "status": "complete"}


class _UnavailableWorker:
    def run(self, _redemption: object) -> object:
        raise FileImportUnavailable("File publication is unavailable")


class _UnavailableWorkerFactory:
    def __call__(self, _receiver: object) -> _UnavailableWorker:
        return _UnavailableWorker()


class _TerminallyFailedWorker:
    def run(self, _redemption: object) -> object:
        raise FileImportRefused("File import is unavailable")


class _TerminallyFailedWorkerFactory:
    def __call__(self, _receiver: object) -> _TerminallyFailedWorker:
        return _TerminallyFailedWorker()


def test_dispatch_stops_after_worker_infrastructure_failure() -> None:
    claimed_at = datetime(2026, 7, 25, 10, tzinfo=UTC)
    claim = FileDispatchLease(
        token=WorkerLeaseToken("lease-token"),
        organization_id=uuid4(),
        job_id=uuid4(),
        source_ref=SourceRef(uuid4()),
        service_principal_id=uuid4(),
        lease_generation=1,
        issued_at=claimed_at,
        expires_at=claimed_at + timedelta(minutes=5),
    )

    with pytest.raises(FileImportUnavailable, match="publication is unavailable"):
        dispatch_one_file_import(
            _OneClaimAuthority(claim),
            _UnavailableWorkerFactory(),  # type: ignore[arg-type]
        )


def test_dispatch_continues_after_durably_recorded_job_failure() -> None:
    claimed_at = datetime(2026, 7, 25, 10, tzinfo=UTC)
    claim = FileDispatchLease(
        token=WorkerLeaseToken("lease-token"),
        organization_id=uuid4(),
        job_id=uuid4(),
        source_ref=SourceRef(uuid4()),
        service_principal_id=uuid4(),
        lease_generation=1,
        issued_at=claimed_at,
        expires_at=claimed_at + timedelta(minutes=5),
    )

    result = dispatch_one_file_import(
        _OneClaimAuthority(claim),
        _TerminallyFailedWorkerFactory(),  # type: ignore[arg-type]
    )

    assert asdict(result) == {"outcome": "refused", "status": "complete"}


class _StoppingNoWorkAuthority:
    def __init__(self, stop_event: Event) -> None:
        self.stop_event = stop_event
        self.claim_count = 0

    def claim(self) -> FileDispatchNoWork:
        self.claim_count += 1
        self.stop_event.set()
        return FileDispatchNoWork()


def test_long_running_dispatch_loop_honors_shutdown_after_no_work() -> None:
    stop_event = Event()
    authority = _StoppingNoWorkAuthority(stop_event)
    observed: list[FileDispatchCycleResult] = []

    dispatch_file_imports_until_stopped(
        authority,
        _ForbiddenWorkerFactory(),  # type: ignore[arg-type]
        stop_event,
        observed.append,
    )

    assert authority.claim_count == 1
    assert [asdict(result) for result in observed] == [
        {"outcome": "no_work", "status": "complete"}
    ]


def test_dispatch_loads_every_server_owned_file_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "first.md").write_bytes(b"first")
    (second / "second.md").write_bytes(b"second")
    monkeypatch.setenv(
        "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON",
        json.dumps({"first": str(first), "second": str(second)}),
    )

    with _file_dispatch_roots() as roots:
        assert roots.read(FileRootRef("first"), FileImportPath("first.md")) == b"first"
        assert (
            roots.read(FileRootRef("second"), FileImportPath("second.md")) == b"second"
        )


def test_worker_file_byte_limit_defaults_to_one_mib_and_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES", raising=False)
    assert _file_read_limits().max_file_bytes == DEFAULT_WORKER_MAX_FILE_BYTES

    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES", "8192")
    assert _file_read_limits().max_file_bytes == 8192


def test_worker_composes_only_explicit_fixed_dimension_embedding_twin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER", "twin")
    monkeypatch.setenv(
        "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION",
        str(CONTEXT_FRAGMENT_EMBEDDING_DIMENSION),
    )

    provider = _embedding_provider()

    assert type(provider) is DeterministicEmbeddingTwin
    assert provider.profile.dimension == CONTEXT_FRAGMENT_EMBEDDING_DIMENSION


def test_worker_external_embedding_configuration_keeps_key_out_of_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER", "external")
    monkeypatch.setenv(
        "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION",
        str(CONTEXT_FRAGMENT_EMBEDDING_DIMENSION),
    )
    monkeypatch.setenv(
        "CONTEXT_ENGINE_WORKER_EMBEDDING_ENDPOINT",
        "https://embedding.invalid/v1/embeddings",
    )
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL", "configured-model")
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_API_KEY", "credential-value")
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_BATCH_SIZE", "64")

    provider = _embedding_provider()

    assert type(provider) is ExternalEmbeddingProvider
    assert "credential-value" not in repr(provider)


@pytest.mark.parametrize("batch_size", ["", "0", "257", "not-a-number"])
def test_worker_refuses_missing_or_unbounded_external_embedding_batch_size(
    monkeypatch: pytest.MonkeyPatch,
    batch_size: str,
) -> None:
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER", "external")
    monkeypatch.setenv(
        "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION",
        str(CONTEXT_FRAGMENT_EMBEDDING_DIMENSION),
    )
    monkeypatch.setenv(
        "CONTEXT_ENGINE_WORKER_EMBEDDING_ENDPOINT",
        "https://embedding.invalid/v1/embeddings",
    )
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL", "configured-model")
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_API_KEY", "credential-value")
    if batch_size:
        monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_BATCH_SIZE", batch_size)
    else:
        monkeypatch.delenv(
            "CONTEXT_ENGINE_WORKER_EMBEDDING_BATCH_SIZE",
            raising=False,
        )

    with pytest.raises(ValueError, match="configuration is not available"):
        _embedding_provider()


@pytest.mark.parametrize(
    ("mode", "dimension"),
    [
        ("", str(CONTEXT_FRAGMENT_EMBEDDING_DIMENSION)),
        ("automatic", str(CONTEXT_FRAGMENT_EMBEDDING_DIMENSION)),
        ("twin", str(CONTEXT_FRAGMENT_EMBEDDING_DIMENSION - 1)),
        ("twin", "not-a-number"),
    ],
)
def test_worker_refuses_missing_unknown_or_mismatched_embedding_configuration(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    dimension: str,
) -> None:
    if mode:
        monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER", mode)
    else:
        monkeypatch.delenv("CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION", dimension)

    with pytest.raises(ValueError, match="configuration is not available"):
        _embedding_provider()


def test_worker_default_file_limit_accepts_above_legacy_ceiling_and_refuses_oversize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "configured-root"
    root.mkdir()
    (root / "above-legacy.md").write_bytes(b"a" * 4_097)
    (root / "oversize.md").write_bytes(
        b"b" * (DEFAULT_WORKER_MAX_FILE_BYTES + 1)
    )
    monkeypatch.setenv(
        "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON",
        json.dumps({"configured-root": str(root)}),
    )
    monkeypatch.delenv("CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES", raising=False)

    with _file_dispatch_roots() as roots:
        assert len(
            roots.read(
                FileRootRef("configured-root"),
                FileImportPath("above-legacy.md"),
            )
        ) == 4_097
        with pytest.raises(LookupError, match="regular configured-root file"):
            roots.read(
                FileRootRef("configured-root"),
                FileImportPath("oversize.md"),
            )


@pytest.mark.parametrize("value", ["", "0", " 8192", "8192 ", "nope", "67108865"])
def test_worker_rejects_invalid_file_byte_limits(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES", value)

    with pytest.raises(ValueError, match="configuration is not available"):
        _file_read_limits()


@pytest.mark.parametrize("document", ["[]", "{}", '{"root": 1}', "not-json"])
def test_dispatch_rejects_invalid_server_root_registry(
    monkeypatch: pytest.MonkeyPatch,
    document: str,
) -> None:
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON", document)

    with pytest.raises(ValueError, match="configuration is not available"):
        _file_dispatch_roots()


class _FailingEngine:
    def begin(self) -> None:
        raise SQLAlchemyError("nonce=" + (b"n" * 32).hex())


class _FailingClockEngine:
    def connect(self) -> None:
        raise SQLAlchemyError("database clock failed")


def test_dispatch_database_clock_failure_is_generic() -> None:
    with pytest.raises(FileImportUnavailable, match="clock is unavailable"):
        _worker_database_time(cast(Engine, _FailingClockEngine()))


class _OffsetClockResult:
    def scalar_one(self) -> datetime:
        return datetime(
            2026,
            7,
            25,
            18,
            tzinfo=timezone(timedelta(hours=8)),
        )


class _OffsetClockConnection:
    def __enter__(self) -> _OffsetClockConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _statement: object) -> _OffsetClockResult:
        return _OffsetClockResult()


class _OffsetClockEngine:
    def connect(self) -> _OffsetClockConnection:
        return _OffsetClockConnection()


def test_dispatch_database_clock_normalizes_session_offset_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "applications.worker.assert_worker_role", lambda _connection: None
    )

    assert _worker_database_time(cast(Engine, _OffsetClockEngine())) == datetime(
        2026, 7, 25, 10, tzinfo=UTC
    )


def test_dispatch_claim_normalizes_database_timestamp_offset_to_utc() -> None:
    session_timestamp = datetime(
        2026,
        7,
        25,
        18,
        tzinfo=timezone(timedelta(hours=8)),
    )

    assert _database_timestamp_utc("issued_at", session_timestamp) == datetime(
        2026, 7, 25, 10, tzinfo=UTC
    )


def test_dispatch_database_failure_does_not_retain_generated_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nonce = b"n" * 32
    monkeypatch.setattr(
        "engine.persistence.worker_jobs.generate_worker_lease_nonce",
        lambda: nonce,
    )
    authority = PostgreSQLFileDispatchAuthority(
        cast(Engine, _FailingEngine()),
        WorkerLeaseCodec(WorkerLeaseKeyring(active_version=1, keys={1: b"k" * 32})),
        configured_root_refs=("configured-root",),
    )

    with pytest.raises(RuntimeError) as failed:
        authority.claim()

    rendered = (str(failed.value), repr(failed.value))
    assert all(nonce.hex() not in value for value in rendered)
