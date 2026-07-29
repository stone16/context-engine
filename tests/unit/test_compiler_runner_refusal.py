from __future__ import annotations

import pytest

from applications.compiler_runner import compile_in_compiler_runner
from engine.supply import (
    CompilationFailure,
    CompilationFailureCode,
    MarkdownCompilerConfig,
)

CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        (
            b"# Heading\n\n```python\nprint('open')\n",
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
        ),
        (
            b"---\nkey: value\n# Missing close\n",
            CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
        ),
        (b"# Heading\n\n\xed\xa0\x80\n", CompilationFailureCode.INVALID_UTF8),
        (
            b"# Heading\n\ncontains\x00nul\n",
            CompilationFailureCode.UNSUPPORTED_CONSTRUCT,
        ),
        (b"# Heading\n\ntruncated \xe4\xb8", CompilationFailureCode.INVALID_UTF8),
    ],
)
def test_malformed_source_returns_typed_failure_across_runner_boundary(
    source: bytes,
    expected_code: CompilationFailureCode,
) -> None:
    outcome = compile_in_compiler_runner(source, CONFIG)

    assert type(outcome) is CompilationFailure
    assert outcome.code is expected_code
    assert outcome.position is not None
