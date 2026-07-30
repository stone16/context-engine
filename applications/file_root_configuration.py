"""Shared server-owned File root registry configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from adapters.file_source import FileReadLimits, FileRootRegistry
from engine.control import DEFAULT_FILE_CHANGE_BASELINE_SIZE, FileRootRef

DEFAULT_WORKER_MAX_FILE_BYTES = 1_048_576
WORKER_MAX_FILE_BYTES_ENV = "CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES"
WORKER_FILE_ROOTS_ENV = "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON"
WORKER_FILE_CURATED_SUBTREES_ENV = "CONTEXT_ENGINE_WORKER_FILE_CURATED_SUBTREES_JSON"
WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV = (
    "CONTEXT_ENGINE_WORKER_MAX_FILE_CHANGE_BASELINE_SIZE"
)


def required_environment(
    name: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Read one explicit nonblank process configuration value."""

    source = os.environ if environment is None else environment
    value = source.get(name)
    if value is None or not value or value != value.strip():
        raise ValueError("Supply worker configuration is not available")
    return value


def file_read_limits(
    environment: Mapping[str, str] | None = None,
) -> FileReadLimits:
    """Load the one bounded File byte ceiling shared by scan and worker."""

    source = os.environ if environment is None else environment
    raw_limit = source.get(WORKER_MAX_FILE_BYTES_ENV)
    raw_scan_bound = source.get(WORKER_MAX_FILE_CHANGE_BASELINE_SIZE_ENV)
    if raw_scan_bound is None:
        scan_bound = DEFAULT_FILE_CHANGE_BASELINE_SIZE
    elif (
        not raw_scan_bound
        or raw_scan_bound != raw_scan_bound.strip()
        or not raw_scan_bound.isdecimal()
    ):
        raise ValueError("Supply worker configuration is not available")
    else:
        scan_bound = int(raw_scan_bound)
    if raw_limit is None:
        try:
            return FileReadLimits(
                max_file_bytes=DEFAULT_WORKER_MAX_FILE_BYTES,
                max_baseline_entries=scan_bound,
            )
        except ValueError:
            raise ValueError("Supply worker configuration is not available") from None
    if not raw_limit or raw_limit != raw_limit.strip() or not raw_limit.isdecimal():
        raise ValueError("Supply worker configuration is not available")
    try:
        return FileReadLimits(
            max_file_bytes=int(raw_limit),
            max_baseline_entries=scan_bound,
        )
    except ValueError:
        raise ValueError("Supply worker configuration is not available") from None


def file_root_bindings(
    environment: Mapping[str, str] | None = None,
) -> dict[FileRootRef, Path]:
    """Load every explicitly configured logical root and host path."""

    raw_registry = required_environment(WORKER_FILE_ROOTS_ENV, environment)
    try:
        document = json.loads(raw_registry)
    except json.JSONDecodeError:
        raise ValueError("Supply worker configuration is not available") from None
    if type(document) is not dict or not document:
        raise ValueError("Supply worker configuration is not available")
    bindings: dict[FileRootRef, Path] = {}
    for raw_ref, raw_path in document.items():
        path = Path(raw_path) if type(raw_path) is str else None
        if (
            type(raw_ref) is not str
            or type(raw_path) is not str
            or not raw_path
            or raw_path != raw_path.strip()
            or path is None
            or not path.is_absolute()
        ):
            raise ValueError("Supply worker configuration is not available")
        bindings[FileRootRef(raw_ref)] = path
    return bindings


def file_curated_subtrees(
    environment: Mapping[str, str] | None = None,
) -> dict[FileRootRef, str]:
    """Load explicit root-relative traversal selections without remapping roots."""

    source = os.environ if environment is None else environment
    bindings = file_root_bindings(environment)
    raw_subtrees = source.get(WORKER_FILE_CURATED_SUBTREES_ENV)
    if raw_subtrees is None:
        return {}
    try:
        document = json.loads(raw_subtrees)
    except json.JSONDecodeError:
        raise ValueError("Supply worker configuration is not available") from None
    raw_refs = {root_ref.value for root_ref in bindings}
    if (
        type(document) is not dict
        or any(
            type(raw_ref) is not str
            or raw_ref not in raw_refs
            or not _canonical_subtree(selection)
            for raw_ref, selection in document.items()
        )
    ):
        raise ValueError("Supply worker configuration is not available")
    return {
        FileRootRef(raw_ref): selection for raw_ref, selection in document.items()
    }


def _canonical_subtree(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and not value.startswith("/")
        and "\\" not in value
        and all(component not in {"", ".", ".."} for component in value.split("/"))
        and not any(ord(character) < 0x20 for character in value)
    )


def file_roots(
    environment: Mapping[str, str] | None = None,
) -> FileRootRegistry:
    """Open the configured roots as anchored directory capabilities."""

    return FileRootRegistry(
        file_root_bindings(environment),
        limits=file_read_limits(environment),
        curated_subtrees=file_curated_subtrees(environment),
    )
