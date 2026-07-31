from __future__ import annotations

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from engine.supply.link_graph import extract_revision_links
from engine.supply.markdown import MarkdownCompilerConfig, ParsedDocument


def test_rich_compilation_extracts_deterministic_content_free_note_links() -> None:
    source = b"""# Synthetic root

See [[Adjacent Note|synthetic alias]], ![[nested/Embedded#Section]], and
[linked](../shared/Linked%20Note.md "synthetic title"). Follow
[reference][synthetic-ref]. Ignore ![asset](image.png),
[remote](https://example.test/note.md), and `[[code-not-a-link]]`.

[synthetic-ref]: <../shared/Reference Note.md> "synthetic title"
"""
    outcome = compile_rich_markdown(
        source,
        MarkdownCompilerConfig("markdown-config-v3"),
    )
    assert type(outcome) is ParsedDocument

    first = extract_revision_links(outcome, source_path="folder/root.md")
    second = extract_revision_links(outcome, source_path="folder/root.md")

    assert first == second
    assert tuple(link.target_path for link in first) == (
        "folder/Adjacent Note.md",
        "folder/nested/Embedded.md",
        "shared/Linked Note.md",
        "shared/Reference Note.md",
    )
    assert tuple(link.kind.value for link in first) == (
        "wikilink",
        "embed",
        "markdown_link",
        "markdown_link",
    )
    assert all(not hasattr(link, "acl") for link in first)
    assert "synthetic alias" not in repr(first)


def test_revision_link_extraction_deduplicates_and_refuses_root_escape() -> None:
    source = b"# Synthetic\n\n[[Same]] [[Same#Section]] [escape](../../outside.md)\n"
    outcome = compile_rich_markdown(
        source,
        MarkdownCompilerConfig("markdown-config-v3"),
    )
    assert type(outcome) is ParsedDocument

    links = extract_revision_links(outcome, source_path="folder/root.md")

    assert tuple(link.target_path for link in links) == ("folder/Same.md",)


def test_only_rich_validated_output_can_produce_revision_links() -> None:
    from adapters.parsers.markdown import compile_markdown

    outcome = compile_markdown(
        b"# Synthetic\n\nNo links.\n",
        MarkdownCompilerConfig("markdown-config-v1"),
    )
    assert type(outcome) is ParsedDocument

    try:
        extract_revision_links(outcome, source_path="root.md")
    except ValueError as error:
        assert str(error) == "Revision links require validated rich Markdown"
    else:  # pragma: no cover - assertion branch
        raise AssertionError("narrow Markdown output must not produce graph edges")
