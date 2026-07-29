from __future__ import annotations

from pathlib import Path

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from applications.compiler_runner import compile_in_compiler_runner
from engine.supply import MarkdownCompilerConfig, ParsedDocument

FIXTURES = Path(__file__).parents[1] / "fixtures/markdown"
CONFIG = MarkdownCompilerConfig(version="markdown-config-v3")


def test_rich_compilation_digest_is_stable_in_process_and_across_runner() -> None:
    source = (FIXTURES / "rich-code-tables.md").read_bytes()

    first = compile_rich_markdown(source, CONFIG)
    second = compile_rich_markdown(source, CONFIG)
    subprocess_result = compile_in_compiler_runner(source, CONFIG)

    assert type(first) is ParsedDocument
    assert type(second) is ParsedDocument
    assert type(subprocess_result) is ParsedDocument
    assert first.compilation_digest == second.compilation_digest
    assert first.compilation_digest == subprocess_result.compilation_digest
    assert subprocess_result == first
