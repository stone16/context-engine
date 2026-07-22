"""Shared atomic artifact writes for the M0 security gate."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path


def atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    """Replace one JSON artifact atomically after a complete durable write."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
