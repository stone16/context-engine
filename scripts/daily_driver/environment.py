"""Strict loading of the ignored single-source deployment environments."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path

_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_SAFE_UNQUOTED_VALUE = re.compile(r"[A-Za-z0-9_./:@%+,=-]+")


class EnvironmentRefused(ValueError):
    """The ignored environment source is absent, exposed, or malformed."""


def load_owner_environment(
    path: Path,
    *,
    required: Iterable[str] = (),
) -> Mapping[str, str]:
    """Load plain KEY=VALUE records without evaluating shell syntax."""

    if not path.is_absolute():
        raise EnvironmentRefused("environment path must be absolute")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise EnvironmentRefused("required environment source is unavailable") from None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise EnvironmentRefused(
            "environment source must be a current-user-owned regular file"
        )
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise EnvironmentRefused("environment source must have mode 0600")

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise EnvironmentRefused("environment source is unreadable") from None
    for line in lines:
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if (
            not separator
            or _NAME.fullmatch(name) is None
            or not value
            or "\x00" in value
        ):
            raise EnvironmentRefused("environment source is malformed")
        if "'" in value:
            if (
                len(value) < 2
                or not value.startswith("'")
                or not value.endswith("'")
                or "'" in value[1:-1]
            ):
                raise EnvironmentRefused("environment source is malformed")
            value = value[1:-1]
        elif _SAFE_UNQUOTED_VALUE.fullmatch(value) is None:
            raise EnvironmentRefused("environment source is malformed")
        if not value:
            raise EnvironmentRefused("environment source is malformed")
        if name in values:
            raise EnvironmentRefused("environment source contains a duplicate key")
        values[name] = value

    missing = sorted(name for name in required if not values.get(name))
    if missing:
        raise EnvironmentRefused("environment source lacks required values")
    return values


def combined_environment(*sources: Mapping[str, str]) -> dict[str, str]:
    """Return the process environment with explicit sources layered once."""

    combined = dict(os.environ)
    owned_names: set[str] = set()
    for source in sources:
        overlap = owned_names.intersection(source)
        if overlap:
            raise EnvironmentRefused(
                "environment sources may not redefine the single live contract"
            )
        combined.update(source)
        owned_names.update(source)
    return combined


def project_environment(
    *sources: Mapping[str, str],
    allowed: Iterable[str],
    required: Iterable[str],
) -> dict[str, str]:
    """Project the single live sources into one least-privilege child contract."""

    allowed_names = frozenset(allowed)
    required_names = frozenset(required)
    if not required_names <= allowed_names:
        raise EnvironmentRefused("process environment projection is invalid")
    projected_source: dict[str, str] = {}
    for source in sources:
        overlap = projected_source.keys() & source.keys()
        if overlap:
            raise EnvironmentRefused(
                "environment sources may not redefine the single live contract"
            )
        projected_source.update(source)
    missing = required_names - projected_source.keys()
    if missing:
        raise EnvironmentRefused("process environment lacks required values")
    inherited = {
        name: os.environ[name]
        for name in ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR", "USER")
        if name in os.environ
    }
    return inherited | {
        name: projected_source[name]
        for name in allowed_names
        if name in projected_source
    }
