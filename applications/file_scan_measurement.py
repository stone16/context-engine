"""Measure the bounded File provider against generated synthetic trees."""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import cast
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from applications.file_scan import FILE_SCAN_PAGE_LIMIT
from engine._opaque import encode_base64url
from engine.control import (
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    ChangeCursor,
    ChangeLimit,
    FileChangeControlProofs,
    FileChangeProviderProofs,
    FileChangeScanHead,
    FileChangeSource,
    FileRootRef,
    InitialScan,
    PendingChangeCursor,
    ProviderOk,
    SourceManifest,
    SourceRef,
)
from engine.control.file_change_pages import _accepted_cursor_payload

MEASUREMENT_SIZES = (1_000, 5_000, 10_000, 15_000)
SCHEMA_VERSION = "context-engine-file-scan-measurement-v1"
_ROOT_REF = FileRootRef("synthetic-scan-root")
_ORGANIZATION_ID = UUID("0c499906-b9b0-4865-a6f5-45bc35178a90")
_SOURCE_ID = UUID("b1712c37-d2d1-4e23-834a-0f49e137268c")
_SOURCE_VERSION_ID = UUID("4452cf6a-88c3-470b-b3e4-5be2b4897b0a")
_SCAN_EPOCH_CHECKPOINT = "facp_" + "b" * 64
_PROVIDER_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("11" * 32))
_CHECKPOINT_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("22" * 32))


class MeasurementUnavailable(RuntimeError):
    """The synthetic provider measurement could not produce its exact contract."""


def _source() -> FileChangeSource:
    manifest = SourceManifest.registered_file(
        source_ref=SourceRef(_SOURCE_ID),
        version_ref=_SOURCE_VERSION_ID,
        display_name="Synthetic scan measurement",
        root_ref=_ROOT_REF,
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        capabilities=FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    )
    return FileChangeSource(_ORGANIZATION_ID, manifest.active_version)


def _timed(call: Callable[[], object]) -> tuple[object, float, int]:
    tracemalloc.start()
    started = perf_counter()
    try:
        value = call()
        elapsed = perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return value, elapsed, peak


def _generate_tree(root: Path, path_count: int) -> None:
    directories = tuple(root / f"group-{ordinal:03d}" for ordinal in range(100))
    for directory in directories:
        directory.mkdir()
    for ordinal in range(path_count):
        directory = directories[ordinal % len(directories)]
        (directory / f"entry-{ordinal:05d}.md").write_bytes(b"# Synthetic\n")


def measure_size(
    path_count: int,
    *,
    curated_subtree: bool = False,
) -> dict[str, int | float | str]:
    """Measure one generated tree through the production provider seam."""

    if type(path_count) is not int or path_count < 2 or path_count > 15_000:
        raise MeasurementUnavailable("synthetic path count is unavailable")
    with TemporaryDirectory(prefix="context-engine-synthetic-scan-") as directory:
        root = Path(directory).resolve(strict=True)
        observed_root = root / "curated" if curated_subtree else root
        if curated_subtree:
            observed_root.mkdir()
        _generate_tree(observed_root, path_count)
        registry = FileRootRegistry(
            {_ROOT_REF: root},
            limits=FileReadLimits(
                max_file_bytes=1_024,
                max_baseline_entries=path_count,
            ),
            curated_subtrees=({_ROOT_REF: "curated"} if curated_subtree else None),
        )
        try:
            provider_proofs = FileChangeProviderProofs(
                provider_signing_key=_PROVIDER_KEY,
                checkpoint_verification_key=_CHECKPOINT_KEY.public_key(),
            )
            provider = FileChangeProvider(registry, proofs=provider_proofs)
            source = _source()
            initial_raw, initial_seconds, initial_peak = _timed(
                lambda: provider.read_changes(
                    source,
                    InitialScan(),
                    ChangeLimit(FILE_SCAN_PAGE_LIMIT),
                )
            )
            if type(initial_raw) is not ProviderOk:
                raise MeasurementUnavailable("initial synthetic scan was refused")
            initial = initial_raw.value
            pending = initial.next_cursor
            if type(pending) is not PendingChangeCursor or initial.complete:
                raise MeasurementUnavailable("synthetic continuation was unavailable")
            verified = FileChangeControlProofs(
                provider_verification_key=_PROVIDER_KEY.public_key()
            ).verify_page(initial)
            if verified is None:
                raise MeasurementUnavailable("synthetic page proof was unavailable")
            payload = _accepted_cursor_payload(
                organization_id=_ORGANIZATION_ID,
                source_ref=SourceRef(_SOURCE_ID),
                source_version_ref=_SOURCE_VERSION_ID,
                scan_ref=initial.scan_ref,
                scan_epoch=initial.scan_epoch,
                page_ref=verified.page_ref,
                checkpoint_ref=_SCAN_EPOCH_CHECKPOINT,
                sequence=1,
                pending_cursor=pending,
            )
            cursor = ChangeCursor(
                f"{encode_base64url(payload)}."
                f"{encode_base64url(_CHECKPOINT_KEY.sign(payload))}"
            )
            continued_source = replace(
                source,
                scan_head=FileChangeScanHead(
                    source_version_ref=_SOURCE_VERSION_ID,
                    scan_ref=initial.scan_ref,
                    scan_epoch=initial.scan_epoch,
                    page_limit=FILE_SCAN_PAGE_LIMIT,
                    page_ref=verified.page_ref,
                    checkpoint_ref=_SCAN_EPOCH_CHECKPOINT,
                    sequence=1,
                    complete=False,
                    scan_bound=path_count,
                ),
            )
            continuation_raw, continuation_seconds, continuation_peak = _timed(
                lambda: provider.read_changes(
                    continued_source,
                    cursor,
                    ChangeLimit(FILE_SCAN_PAGE_LIMIT),
                )
            )
            if type(continuation_raw) is not ProviderOk:
                raise MeasurementUnavailable("synthetic continuation was refused")
            page_count = math.ceil(path_count / FILE_SCAN_PAGE_LIMIT)
            estimated_cycle_seconds = initial_seconds + (
                (page_count - 1) * continuation_seconds
            )
            return {
                "continuationPeakMemoryBytes": continuation_peak,
                "continuationWallClockSeconds": round(continuation_seconds, 6),
                "estimatedSingletonCycleSeconds": round(
                    estimated_cycle_seconds, 3
                ),
                "initialPeakMemoryBytes": initial_peak,
                "initialWallClockSeconds": round(initial_seconds, 6),
                "measurementRef": (
                    f"synthetic-curated-{path_count}"
                    if curated_subtree
                    else f"synthetic-{path_count}"
                ),
                "pageCount": page_count,
                "pathCount": path_count,
                "peakMemoryBytes": max(initial_peak, continuation_peak),
            }
        finally:
            registry.close()


def run_measurement() -> dict[str, object]:
    """Return aggregate-only results for the fixed representative sizes."""

    measurements = [measure_size(size) for size in MEASUREMENT_SIZES]
    by_size = {cast(int, value["pathCount"]): value for value in measurements}
    curated_measurement = measure_size(5_000, curated_subtree=True)

    def option(
        name: str,
        measured: dict[str, int | float | str],
    ) -> dict[str, object]:
        return {
            "label": name,
            "representativePathCount": measured["pathCount"],
            "measurementRef": measured["measurementRef"],
            "initialWallClockSeconds": measured["initialWallClockSeconds"],
            "continuationWallClockSeconds": measured[
                "continuationWallClockSeconds"
            ],
            "peakMemoryBytes": measured["peakMemoryBytes"],
            "pageCount": measured["pageCount"],
            "estimatedSingletonCycleSeconds": measured[
                "estimatedSingletonCycleSeconds"
            ],
        }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "measuredAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "environment": {
            "machine": platform.machine(),
            "python": platform.python_version(),
            "system": platform.system(),
        },
        "method": {
            "generatedTree": True,
            "pageLimit": FILE_SCAN_PAGE_LIMIT,
            "productionProviderSeam": True,
            "curatedOptionUsesConfiguredTraversal": True,
            "singletonCycleEstimate": (
                "initial call plus pageCount minus one times the measured signed "
                "continuation call"
            ),
        },
        "measurements": measurements,
        "options": {
            "configurableWholeVault": option(
                "configurable whole-vault bound", by_size[15_000]
            ),
            "curatedSubtree": option(
                "illustrative curated subtree",
                curated_measurement,
            ),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-engine-file-scan-measurement",
        description="Measure recursive File scans over generated synthetic trees.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = run_measurement()
        output = cast(Path, arguments.output)
        repository_root = Path.cwd().resolve()
        state_root = repository_root / ".context-engine"
        tracked_report = (
            repository_root
            / "docs/evaluation/2026-07-30-file-scan-measurement.json"
        )
        if (
            not output.resolve().is_relative_to(state_root)
            and output.resolve() != tracked_report
        ):
            raise MeasurementUnavailable(
                "measurement output target is unavailable"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except (MeasurementUnavailable, OSError) as failure:
        print(str(failure), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
