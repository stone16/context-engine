"""Deterministic content-free link structure derived from one rich Revision."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from engine.supply.markdown import ParsedDocument, SectionKind

_INLINE_CODE = re.compile(r"`+[^`\r\n]*`+")
_WIKILINK = re.compile(r"(?P<embed>!)?\[\[(?P<target>[^]\r\n]+)]]")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]\r\n]*]\((?P<target>[^()\r\n]+)\)")
_REFERENCE_DEFINITION = re.compile(
    r'''^ {0,3}\[(?P<label>[^]\r\n]+)]:[ \t]*'''
    r'''(?P<target><[^>\r\n]+>|\S+)'''
    r'''(?:[ \t]+(?:"[^"\r\n]*"|'[^'\r\n]*'|\([^()\r\n]*\)))?[ \t]*$''',
    re.MULTILINE,
)
_REFERENCE_LINK = re.compile(r"(?<!!)\[(?P<text>[^]\r\n]+)]\[(?P<label>[^]\r\n]*)]")


class RevisionLinkKind(StrEnum):
    """Closed syntax class; it carries no permission semantics."""

    WIKILINK = "wikilink"
    EMBED = "embed"
    MARKDOWN_LINK = "markdown_link"


@dataclass(frozen=True, slots=True)
class RevisionLink:
    """One canonical resource target reproducible from an immutable Revision."""

    target_path: str = field(repr=False)
    kind: RevisionLinkKind

    def __post_init__(self) -> None:
        if type(self.kind) is not RevisionLinkKind:
            raise TypeError("Revision link kind must be closed")
        try:
            path = PurePosixPath(self.target_path)
        except (TypeError, ValueError):
            raise ValueError("Revision link target must be canonical") from None
        if (
            type(self.target_path) is not str
            or not self.target_path
            or path.is_absolute()
            or self.target_path != path.as_posix()
            or path.suffix.casefold() != ".md"
            or len(self.target_path) > 255
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(ord(character) < 0x20 for character in self.target_path)
        ):
            raise ValueError("Revision link target must be canonical")


def _canonical_target(source_path: str, raw_target: str) -> str | None:
    split = urlsplit(raw_target.strip().strip("<>"))
    if split.scheme or split.netloc or split.path.startswith("/"):
        return None
    decoded = unquote(split.path).replace("\\", "/").strip()
    if not decoded:
        return None
    target = PurePosixPath(decoded)
    if not target.suffix:
        target = target.with_suffix(".md")
    if target.suffix.casefold() != ".md":
        return None
    combined = posixpath.normpath(
        posixpath.join(PurePosixPath(source_path).parent.as_posix(), target.as_posix())
    )
    if combined == ".." or combined.startswith("../") or combined.startswith("/"):
        return None
    try:
        return RevisionLink(combined, RevisionLinkKind.WIKILINK).target_path
    except ValueError:
        return None


def extract_revision_links(
    document: ParsedDocument,
    *,
    source_path: str,
) -> tuple[RevisionLink, ...]:
    """Extract ordered, duplicate-free local note targets from validated v3 output."""

    if type(document) is not ParsedDocument or not document.provenance.is_rich_v3:
        raise ValueError("Revision links require validated rich Markdown")
    try:
        canonical_source = PurePosixPath(source_path)
    except (TypeError, ValueError):
        raise ValueError("Revision link source path must be canonical") from None
    if (
        type(source_path) is not str
        or not source_path
        or canonical_source.is_absolute()
        or canonical_source.as_posix() != source_path
        or canonical_source.suffix.casefold() != ".md"
        or any(part in {"", ".", ".."} for part in canonical_source.parts)
    ):
        raise ValueError("Revision link source path must be canonical")

    source_text = "\n".join(
        section.text
        for section in document.sections
        if section.kind is not SectionKind.FENCED_CODE
    )
    masked = _INLINE_CODE.sub(lambda match: " " * len(match.group()), source_text)
    definitions = {
        match.group("label").casefold(): match.group("target")
        for match in _REFERENCE_DEFINITION.finditer(masked)
    }
    extracted: list[RevisionLink] = []
    seen: set[str] = set()

    def append(raw_target: str, kind: RevisionLinkKind) -> None:
        if kind is RevisionLinkKind.MARKDOWN_LINK:
            stripped = raw_target.strip()
            if stripped.startswith("<") and ">" in stripped:
                raw_target = stripped[1 : stripped.index(">")]
            else:
                raw_target = stripped.split(maxsplit=1)[0]
        raw_target = raw_target.split("|", maxsplit=1)[0]
        raw_target = raw_target.split("#", maxsplit=1)[0]
        target_path = _canonical_target(source_path, raw_target)
        if target_path is None or target_path in seen:
            return
        seen.add(target_path)
        extracted.append(RevisionLink(target_path=target_path, kind=kind))

    matches: list[tuple[int, str, RevisionLinkKind]] = []
    for match in _WIKILINK.finditer(masked):
        matches.append(
            (
                match.start(),
                match.group("target"),
                (
                    RevisionLinkKind.EMBED
                    if match.group("embed")
                    else RevisionLinkKind.WIKILINK
                ),
            )
        )
    for match in _MARKDOWN_LINK.finditer(masked):
        matches.append(
            (match.start(), match.group("target"), RevisionLinkKind.MARKDOWN_LINK)
        )
    for match in _REFERENCE_LINK.finditer(masked):
        label = match.group("label") or match.group("text")
        target = definitions.get(label.casefold())
        if target is not None:
            matches.append((match.start(), target, RevisionLinkKind.MARKDOWN_LINK))
    for _position, raw_target, kind in sorted(matches, key=lambda item: item[0]):
        append(raw_target, kind)
    return tuple(extracted)


__all__ = ["RevisionLink", "RevisionLinkKind", "extract_revision_links"]
