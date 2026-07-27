"""Shared server-owned File root registry configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from adapters.file_source import FileReadLimits, FileRootRegistry
from engine.control import FileRootRef

DEFAULT_WORKER_MAX_FILE_BYTES = 1_048_576
WORKER_MAX_FILE_BYTES_ENV = "CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES"
WORKER_FILE_ROOTS_ENV = "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON"


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
    if raw_limit is None:
        return FileReadLimits(max_file_bytes=DEFAULT_WORKER_MAX_FILE_BYTES)
    if not raw_limit or raw_limit != raw_limit.strip() or not raw_limit.isdecimal():
        raise ValueError("Supply worker configuration is not available")
    try:
        return FileReadLimits(max_file_bytes=int(raw_limit))
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
        if (
            type(raw_ref) is not str
            or type(raw_path) is not str
            or not raw_path
            or raw_path != raw_path.strip()
        ):
            raise ValueError("Supply worker configuration is not available")
        bindings[FileRootRef(raw_ref)] = Path(raw_path)
    return bindings


def file_roots(
    environment: Mapping[str, str] | None = None,
) -> FileRootRegistry:
    """Open the configured roots as anchored directory capabilities."""

    return FileRootRegistry(
        file_root_bindings(environment),
        limits=file_read_limits(environment),
    )
