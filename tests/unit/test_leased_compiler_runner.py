from __future__ import annotations

import json
import subprocess
from typing import cast

import pytest

import engine.supply.compiler_runner as compiler_runner
from engine.supply import (
    CompilationFailure,
    CompilationFailureCode,
    MarkdownCompilerConfig,
    ParsedDocument,
    UnsupportedConstruct,
)

CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")


def _assert_boundary_failure(outcome: object) -> None:
    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is None


def test_leased_runner_sends_only_source_and_closed_config_to_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=cast(list[str], args[0]),
            returncode=1,
            stdout=b"",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_LEASE_TOKEN", "must-not-cross")
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_ORGANIZATION_ID", "must-not-cross")
    monkeypatch.setenv("CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID", "must-not-cross")

    outcome = compiler_runner.compile_in_leased_compiler_runner(b"# T\n", CONFIG)

    _assert_boundary_failure(outcome)
    argv = cast(tuple[list[str]], observed["args"])[0]
    kwargs = cast(dict[str, object], observed["kwargs"])
    assert argv[2:] == [
        "applications.leased_compiler_runner",
        "--compile-leased",
        "--config",
        "markdown-config-v3",
        "--token-ceiling",
        str(CONFIG.token_ceiling),
    ]
    assert kwargs["input"] == b"# T\n"
    assert kwargs["env"] == {}
    assert all("WORKER_LEASE" not in argument for argument in argv)
    assert all("ORGANIZATION_ID" not in argument for argument in argv)
    assert all("SERVICE_PRINCIPAL" not in argument for argument in argv)
    assert all("actor" not in argument.casefold() for argument in argv)


def test_leased_runner_returns_parsed_document_across_real_process() -> None:
    outcome = compiler_runner.compile_in_leased_compiler_runner(b"# T\n", CONFIG)

    assert type(outcome) is ParsedDocument


@pytest.mark.parametrize(
    "completed",
    (
        subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"crash"),
        subprocess.CompletedProcess([], 0, stdout=b"not-json", stderr=b""),
        subprocess.CompletedProcess([], 0, stdout=b"[]", stderr=b""),
        subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({"outcome": "parsed", "document": "%%%"}).encode(),
            stderr=b"",
        ),
        subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({"outcome": "unknown"}).encode(),
            stderr=b"",
        ),
    ),
)
def test_leased_runner_crash_or_malformed_envelope_is_one_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[bytes],
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)

    _assert_boundary_failure(
        compiler_runner.compile_in_leased_compiler_runner(b"# T\n", CONFIG)
    )


def test_leased_runner_timeout_is_one_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[float] = []

    def wedge(*args: object, timeout: float, **kwargs: object) -> object:
        observed.append(timeout)
        raise subprocess.TimeoutExpired("synthetic-wedge", timeout)

    monkeypatch.setattr(subprocess, "run", wedge)

    outcome = compiler_runner.compile_in_leased_compiler_runner(b"# T\n", CONFIG)

    assert observed == [compiler_runner.COMPILER_RUNNER_TIMEOUT_SECONDS]
    _assert_boundary_failure(outcome)


def test_leased_runner_preserves_closed_compiler_refusal() -> None:
    outcome = compiler_runner.compile_in_leased_compiler_runner(
        b"# T\n\n&amp;\n",
        CONFIG,
    )

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_CONSTRUCT
    assert outcome.construct is UnsupportedConstruct.ENTITY
    assert outcome.position is not None
