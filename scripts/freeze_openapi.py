#!/usr/bin/env python3
"""Create immutable OpenAPI snapshots and reject unreviewed contract drift."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn

from adapters.http.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION_DIRECTORY = ROOT / "openapi" / "v0"


class SnapshotAlreadyExists(RuntimeError):
    """A historical contract snapshot cannot be replaced in place."""


class BreakingContractChange(RuntimeError):
    """The candidate no longer contains every accepted contract requirement."""


class SnapshotDrift(RuntimeError):
    """Generated OpenAPI differs from its accepted immutable snapshot."""


def render_openapi_snapshot() -> bytes:
    """Render the server contract with stable key ordering and one trailing LF."""

    document = create_app().openapi()
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")


def write_new_snapshot(version_directory: Path) -> None:
    """Create one new version directory, refusing every in-place replacement."""

    snapshot = version_directory / "openapi.json"
    digest = version_directory / "openapi.sha256"
    if snapshot.exists() or digest.exists():
        raise SnapshotAlreadyExists(
            "historical OpenAPI snapshots require a new reviewed version"
        )
    version_directory.mkdir(parents=True, exist_ok=True)
    rendered = render_openapi_snapshot()
    snapshot.write_bytes(rendered)
    digest.write_text(f"{sha256(rendered).hexdigest()}\n", encoding="ascii")


def _breaking(message: str) -> NoReturn:
    raise BreakingContractChange(message)


def _schema_map(document: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        schemas = document["components"]["schemas"]
    except (KeyError, TypeError):
        _breaking("candidate removed components.schemas")
    if not isinstance(schemas, Mapping):
        _breaking("candidate components.schemas is not an object")
    return schemas


def _first_contract_difference(
    accepted: object,
    candidate: object,
    *,
    pointer: str = "$",
) -> str | None:
    """Return the first recursive structural difference in stable order."""

    if type(accepted) is not type(candidate):
        return f"{pointer} changed type"
    if isinstance(accepted, Mapping) and isinstance(candidate, Mapping):
        accepted_keys = set(accepted)
        candidate_keys = set(candidate)
        removed = sorted(accepted_keys - candidate_keys)
        if removed:
            return f"{pointer} removed {removed[0]}"
        added = sorted(candidate_keys - accepted_keys)
        if added:
            return f"{pointer} added {added[0]}"
        for key in sorted(accepted_keys):
            difference = _first_contract_difference(
                accepted[key],
                candidate[key],
                pointer=f"{pointer}.{key}",
            )
            if difference is not None:
                return difference
        return None
    if isinstance(accepted, list) and isinstance(candidate, list):
        if len(accepted) != len(candidate):
            return f"{pointer} changed array length"
        for index, accepted_item in enumerate(accepted):
            difference = _first_contract_difference(
                accepted_item,
                candidate[index],
                pointer=f"{pointer}[{index}]",
            )
            if difference is not None:
                return difference
        return None
    if accepted != candidate:
        return f"{pointer} changed value"
    return None


def assert_no_breaking_changes(
    accepted: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    """Reject every recursive mutation of one already-frozen version."""

    _schema_map(accepted)
    _schema_map(candidate)
    difference = _first_contract_difference(accepted, candidate)
    if difference is not None:
        _breaking(f"frozen contract changed at {difference}")


def assert_historical_artifacts_unchanged(
    *,
    current_snapshot: bytes,
    current_digest: bytes,
    historical_snapshot: bytes | None,
    historical_digest: bytes | None,
) -> None:
    """Permit first publication, then make the accepted version append-only."""

    if historical_snapshot is None and historical_digest is None:
        return
    if historical_snapshot is None or historical_digest is None:
        raise SnapshotDrift("historical OpenAPI artifact pair is incomplete")
    if current_snapshot != historical_snapshot or current_digest != historical_digest:
        raise SnapshotDrift(
            "historical OpenAPI version changed; publish a new version directory"
        )


def _historical_file(
    baseline_ref: str,
    path: Path,
    *,
    repository_root: Path,
) -> bytes | None:
    relative_path = path.relative_to(repository_root).as_posix()
    result = subprocess.run(
        ["git", "show", f"{baseline_ref}:{relative_path}"],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return result.stdout
    return None


def check_snapshot(
    version_directory: Path = DEFAULT_VERSION_DIRECTORY,
    *,
    baseline_ref: str | None = None,
    repository_root: Path = ROOT,
) -> None:
    """Verify digest, compatibility, and exact deterministic server equality."""

    snapshot_path = version_directory / "openapi.json"
    digest_path = version_directory / "openapi.sha256"
    accepted_bytes = snapshot_path.read_bytes()
    accepted_digest_bytes = digest_path.read_bytes()
    accepted_digest = accepted_digest_bytes.decode("ascii")
    expected_digest = f"{sha256(accepted_bytes).hexdigest()}\n"
    if accepted_digest != expected_digest:
        raise SnapshotDrift("accepted OpenAPI checksum does not match the snapshot")
    if baseline_ref is not None:
        verified_ref = subprocess.run(
            ["git", "rev-parse", "--verify", f"{baseline_ref}^{{commit}}"],
            cwd=repository_root,
            check=False,
            capture_output=True,
        )
        if verified_ref.returncode != 0:
            raise SnapshotDrift("OpenAPI baseline ref is unavailable")
        assert_historical_artifacts_unchanged(
            current_snapshot=accepted_bytes,
            current_digest=accepted_digest_bytes,
            historical_snapshot=_historical_file(
                baseline_ref,
                snapshot_path,
                repository_root=repository_root,
            ),
            historical_digest=_historical_file(
                baseline_ref,
                digest_path,
                repository_root=repository_root,
            ),
        )
    generated_bytes = render_openapi_snapshot()
    accepted = json.loads(accepted_bytes)
    candidate = json.loads(generated_bytes)
    assert_no_breaking_changes(accepted, candidate)
    if generated_bytes != accepted_bytes:
        raise SnapshotDrift(
            "generated OpenAPI drifted; create a reviewed version instead of "
            "mutating v0"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "generate"))
    parser.add_argument(
        "--version-directory",
        type=Path,
        default=DEFAULT_VERSION_DIRECTORY,
    )
    parser.add_argument("--baseline-ref")
    arguments = parser.parse_args()
    if arguments.command == "generate":
        write_new_snapshot(arguments.version_directory)
    else:
        check_snapshot(
            arguments.version_directory,
            baseline_ref=arguments.baseline_ref,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
