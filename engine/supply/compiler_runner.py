"""Leased parent boundary for the pure rich-Markdown compiler subprocess."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from typing import Final, cast

from engine.supply.markdown import (
    CompilationFailure,
    CompilationFailureCode,
    CompilationOutcome,
    MarkdownCompilerConfig,
    deserialize_parsed_document,
)

_RUNNER_MODULE: Final = "applications.leased_compiler_runner"
COMPILER_RUNNER_TIMEOUT_SECONDS: Final = 30.0


def _boundary_failure() -> CompilationFailure:
    return CompilationFailure(
        code=CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
        position=None,
    )


def _failure_from_document(value: object) -> CompilationFailure:
    if type(value) is not dict:
        raise ValueError("runner failure must be an object")
    document = cast(dict[str, object], value)
    if set(document) != {"code"}:
        raise ValueError("runner failure must contain only its closed category")
    return CompilationFailure(
        code=CompilationFailureCode(cast(str, document["code"])),
        position=None,
    )


def compile_in_leased_compiler_runner(
    source: bytes,
    config: MarkdownCompilerConfig,
) -> CompilationOutcome:
    """Run the pure child selected by an already-verified leased worker."""

    if type(source) is not bytes:
        raise TypeError("compiler-runner source must be exact bytes")
    if type(config) is not MarkdownCompilerConfig or config.token_ceiling is None:
        raise TypeError("compiler-runner requires rich Markdown config")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                _RUNNER_MODULE,
                "--compile-leased",
                "--config",
                config.version,
                "--token-ceiling",
                str(config.token_ceiling),
            ],
            input=source,
            capture_output=True,
            check=False,
            env={},
            timeout=COMPILER_RUNNER_TIMEOUT_SECONDS,
        )
    except Exception:
        return _boundary_failure()
    if completed.returncode != 0:
        return _boundary_failure()
    try:
        envelope = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _boundary_failure()
    if type(envelope) is not dict:
        return _boundary_failure()
    document = cast(dict[str, object], envelope)
    if document.get("outcome") == "parsed":
        encoded = document.get("document")
        if type(encoded) is not str:
            return _boundary_failure()
        try:
            return deserialize_parsed_document(base64.b64decode(encoded, validate=True))
        except Exception:
            return _boundary_failure()
    if document.get("outcome") == "failure":
        try:
            return _failure_from_document(document.get("failure"))
        except Exception:
            return _boundary_failure()
    return _boundary_failure()


__all__ = ["compile_in_leased_compiler_runner"]
