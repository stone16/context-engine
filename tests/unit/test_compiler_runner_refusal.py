from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import applications.compiler_runner as compiler_runner
from adapters.parsers.ragflow_markdown import compile_rich_markdown
from applications.compiler_runner import compile_in_local_compiler_runner
from engine.supply import (
    CompilationFailure,
    CompilationFailureCode,
    CompiledFragment,
    MarkdownCompilerConfig,
    ParsedDocument,
    UnsupportedConstruct,
)

CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        (
            b"# Heading\n\n```python\nprint('open')\n",
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
        ),
        (b"# Heading\n\n\xed\xa0\x80\n", CompilationFailureCode.INVALID_UTF8),
        (
            b"# Heading\n\ncontains\x00nul\n",
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
        ),
        (
            b"```text\ncontains\x07bell\n```\n",
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
        ),
        (
            b"```text\ncontains\x1bescape\n```\n",
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
        ),
        (
            b"```text\ncontains\x1fseparator\n```\n",
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
        ),
        (b"# Heading\n\ntruncated \xe4\xb8", CompilationFailureCode.INVALID_UTF8),
        (
            b"# Heading\n\n<script>alert('no')</script>\n",
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
        ),
    ],
)
def test_malformed_source_returns_typed_failure_across_runner_boundary(
    source: bytes,
    expected_code: CompilationFailureCode,
) -> None:
    outcome = compile_in_local_compiler_runner(source, CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is expected_code
    assert outcome.position is not None


@pytest.mark.parametrize(
    ("source", "construct"),
    (
        (b"# T\n\n&amp;\n", UnsupportedConstruct.ENTITY),
        (b"# T\n\nescaped\\*text\n", UnsupportedConstruct.ESCAPE),
        (b"# T\n\n    indented code\n", UnsupportedConstruct.CODE_BLOCK),
        (b"# T\n\n> &amp;\n", UnsupportedConstruct.ENTITY),
        (b"# T\n\n- item\n  &amp;\n", UnsupportedConstruct.ENTITY),
    ),
)
def test_unlisted_inline_constructs_are_typed_refusals(
    source: bytes,
    construct: UnsupportedConstruct,
) -> None:
    outcome = compile_in_local_compiler_runner(source, CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_CONSTRUCT
    assert outcome.construct is construct


def test_unlisted_construct_inside_atomic_ragged_table_is_typed_refusal() -> None:
    source = (
        b"| A | B |\n"
        b"| --- | --- |\n"
        b"| x | &amp; |\n"
        b"| ragged |\n"
    )

    outcome = compile_rich_markdown(source, CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_CONSTRUCT
    assert outcome.construct is UnsupportedConstruct.ENTITY


def test_runner_boundary_converts_unexpected_compiler_exception_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def raise_unexpected(
        source: bytes,
        config: MarkdownCompilerConfig,
    ) -> object:
        raise ValueError("internal parser defect")

    monkeypatch.setattr(compiler_runner, "compile_rich_markdown", raise_unexpected)

    compiler_runner._emit(b"# T\n", CONFIG)

    outcome = compiler_runner._failure_from_document(
        json.loads(capsys.readouterr().out)["failure"]
    )
    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is None


def test_runner_api_converts_child_process_failure_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout=b"",
            stderr=b"internal parser defect",
        ),
    )

    outcome = compile_in_local_compiler_runner(b"# T\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is None


def test_runner_api_passes_a_bound_and_converts_timeout_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[float] = []

    def wedge(*args: object, timeout: float, **kwargs: object) -> object:
        observed.append(timeout)
        raise subprocess.TimeoutExpired(cmd="wedged compiler", timeout=timeout)

    monkeypatch.setattr(subprocess, "run", wedge)

    outcome = compile_in_local_compiler_runner(b"# T\n", CONFIG)

    assert observed and observed[0] > 0
    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is None


def test_runner_api_terminates_a_deliberately_wedged_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "wedged-compiler"
    executable.write_text(
        "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setattr(
        compiler_runner,
        "sys",
        type("Sys", (), {"executable": str(executable)}),
    )
    monkeypatch.setattr(compiler_runner, "COMPILER_RUNNER_TIMEOUT_SECONDS", 0.05)

    outcome = compile_in_local_compiler_runner(b"# T\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is None


def test_direct_compiler_converts_domain_constructor_rejection_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_constructor(*args: object, **kwargs: object) -> ParsedDocument:
        raise ValueError("domain constructor rejected parser metadata")

    monkeypatch.setattr(
        ParsedDocument,
        "rich_v3",
        classmethod(reject_constructor),
    )

    outcome = compile_rich_markdown(b"# T\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is not None


def test_direct_compiler_converts_section_constructor_rejection_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_section(*args: object, **kwargs: object) -> object:
        raise ValueError("section constructor rejected parser metadata")

    monkeypatch.setattr(
        "adapters.parsers.ragflow_markdown._section",
        reject_section,
    )

    outcome = compile_rich_markdown(b"# T\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is not None


def test_direct_compiler_converts_unexpected_section_exception_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_section(*args: object, **kwargs: object) -> object:
        raise RuntimeError("unexpected section defect")

    monkeypatch.setattr(
        "adapters.parsers.ragflow_markdown._section",
        reject_section,
    )

    outcome = compile_rich_markdown(b"# T\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is not None


def test_direct_compiler_converts_unexpected_domain_exception_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_constructor(*args: object, **kwargs: object) -> ParsedDocument:
        raise RuntimeError("unexpected domain defect")

    monkeypatch.setattr(
        ParsedDocument,
        "rich_v3",
        classmethod(reject_constructor),
    )

    outcome = compile_rich_markdown(b"# T\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is not None


def test_direct_compiler_converts_unexpected_fragment_exception_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_fragment(*args: object, **kwargs: object) -> CompiledFragment:
        raise RuntimeError("unexpected Fragment defect")

    monkeypatch.setattr(
        "adapters.parsers.ragflow_markdown.CompiledFragment",
        reject_fragment,
    )

    outcome = compile_rich_markdown(b"# T\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is not None


def test_runner_api_converts_unexpected_deserializer_exception_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {"outcome": "parsed", "document": "e30="}
            ).encode(),
            stderr=b"",
        ),
    )

    def reject_document(payload: bytes) -> ParsedDocument:
        raise RuntimeError("unexpected deserializer defect")

    monkeypatch.setattr(
        compiler_runner,
        "deserialize_parsed_document",
        reject_document,
    )

    outcome = compile_in_local_compiler_runner(b"# T\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is None


def test_direct_compiler_converts_parser_helper_rejection_to_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_parser_helper(*args: object, **kwargs: object) -> object:
        raise ValueError("vendored parser helper rejected source")

    monkeypatch.setattr(
        "third_party.ragflow.deepdoc.parser.markdown_parser."
        "MarkdownElementExtractor._get_fence_marker",
        reject_parser_helper,
    )

    outcome = compile_rich_markdown(b"Body\n", CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE
    assert outcome.position is not None
