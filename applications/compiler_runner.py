"""Pure subprocess boundary for the registered rich Markdown compiler."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePath
from typing import Final, cast

from adapters.parsers.ragflow_markdown import compile_rich_markdown, rich_token_count
from engine.supply import (
    MARKDOWN_RICH_TOKEN_CEILING,
    CompilationFailure,
    CompilationFailureCode,
    CompilationOutcome,
    MarkdownCompilerConfig,
    ParsedDocument,
    SourcePoint,
    UnsupportedConstruct,
    canonicalize_parsed_document,
    deserialize_parsed_document,
)

_RUNNER_MODULE: Final = "applications.compiler_runner"


def _failure_document(failure: CompilationFailure) -> dict[str, object]:
    return {
        "code": failure.code.value,
        "construct": failure.construct.value if failure.construct is not None else None,
        "position": (
            {
                "line": failure.position.line,
                "column": failure.position.column,
                "byteOffset": failure.position.byte_offset,
            }
            if failure.position is not None
            else None
        ),
    }


def _failure_from_document(value: object) -> CompilationFailure:
    if type(value) is not dict:
        raise ValueError("runner failure must be an object")
    document = cast(dict[str, object], value)
    position_value = document["position"]
    position = None
    if type(position_value) is dict:
        point = cast(dict[str, object], position_value)
        position = SourcePoint(
            line=cast(int, point["line"]),
            column=cast(int, point["column"]),
            byte_offset=cast(int, point["byteOffset"]),
        )
    construct_value = document["construct"]
    return CompilationFailure(
        code=CompilationFailureCode(cast(str, document["code"])),
        position=position,
        construct=(
            UnsupportedConstruct(cast(str, construct_value))
            if construct_value is not None
            else None
        ),
    )


def compile_in_compiler_runner(
    source: bytes,
    config: MarkdownCompilerConfig,
    *,
    token_ceiling: int = MARKDOWN_RICH_TOKEN_CEILING,
) -> CompilationOutcome:
    """Compile once in a fresh, state-free ContextEngine-owned process."""

    if type(source) is not bytes:
        raise TypeError("compiler-runner source must be exact bytes")
    if type(config) is not MarkdownCompilerConfig:
        raise TypeError("compiler-runner config must be exact")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            _RUNNER_MODULE,
            "--compile",
            "--config",
            config.version,
            "--token-ceiling",
            str(token_ceiling),
        ],
        input=source,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("compiler-runner process failed")
    envelope = json.loads(completed.stdout)
    if type(envelope) is not dict:
        raise RuntimeError("compiler-runner emitted an invalid envelope")
    document = cast(dict[str, object], envelope)
    if document.get("outcome") == "parsed":
        encoded = document.get("document")
        if type(encoded) is not str:
            raise RuntimeError("compiler-runner parsed payload is invalid")
        return deserialize_parsed_document(base64.b64decode(encoded, validate=True))
    if document.get("outcome") == "failure":
        return _failure_from_document(document.get("failure"))
    raise RuntimeError("compiler-runner outcome is invalid")


def _emit(source: bytes, config: MarkdownCompilerConfig, token_ceiling: int) -> None:
    outcome = compile_rich_markdown(source, config, token_ceiling=token_ceiling)
    if type(outcome) is ParsedDocument:
        envelope: dict[str, object] = {
            "outcome": "parsed",
            "document": base64.b64encode(
                canonicalize_parsed_document(outcome)
            ).decode("ascii"),
        }
    else:
        assert type(outcome) is CompilationFailure
        envelope = {"outcome": "failure", "failure": _failure_document(outcome)}
    sys.stdout.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--config", default="markdown-config-v3")
    parser.add_argument(
        "--token-ceiling",
        type=int,
        default=MARKDOWN_RICH_TOKEN_CEILING,
    )
    parser.add_argument("--acceptance-report", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".context-engine/compiler-runner-acceptance.json"),
    )
    return parser


_CONSTRUCT_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "atxHeadings": re.compile(r"(?m)^ {0,3}#{1,6}[ \t]+"),
    "setextHeadings": re.compile(r"(?m)^.+\n {0,3}(?:=+|-+)[ \t]*$"),
    "lists": re.compile(r"(?m)^[ \t]*(?:[-+*]|[0-9]+[.)])[ \t]+"),
    "fencedCode": re.compile(r"(?m)^ {0,3}(?:`{3,}|~{3,})"),
    "tables": re.compile(r"(?m)^.*\|.*\n[ \t]*\|?[ :|-]+\|"),
    "wikilinks": re.compile(r"(?<!!)\[\[[^]]+]]"),
    "embeds": re.compile(r"!\[\[[^]]+]]"),
    "footnotes": re.compile(r"\[\^[^]]+]"),
    "htmlBlocks": re.compile(r"(?m)^ {0,3}<[/!?A-Za-z]"),
    "callouts": re.compile(r"(?m)^ {0,3}>[ \t]*\[![A-Za-z0-9_-]+]"),
    "inlineMath": re.compile(r"(?<!\\)\$(?!\s).+?(?<!\s)(?<!\\)\$"),
    "frontmatter": re.compile(r"\A---\n"),
}


def _safe_markdown_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        raise ValueError("acceptance root must be a directory")
    return tuple(
        sorted(
            (path for path in root.rglob("*.md") if path.is_file()),
            key=lambda path: PurePath(*path.relative_to(root).parts).as_posix(),
        )
    )


def _acceptance_report(root: Path, token_ceiling: int) -> dict[str, object]:
    accepted = 0
    refused = 0
    digests: list[str] = []
    maximum = 0
    histogram = {name: 0 for name in _CONSTRUCT_PATTERNS}
    for path in _safe_markdown_files(root):
        source = path.read_bytes()
        try:
            inspected = source.removeprefix(b"\xef\xbb\xbf").decode("utf-8")
        except UnicodeDecodeError:
            inspected = ""
        normalized = inspected.replace("\r\n", "\n").replace("\r", "\n")
        for name, pattern in _CONSTRUCT_PATTERNS.items():
            histogram[name] += len(pattern.findall(normalized))
        outcome = compile_rich_markdown(
            source,
            MarkdownCompilerConfig("markdown-config-v3"),
            token_ceiling=token_ceiling,
        )
        if type(outcome) is ParsedDocument:
            accepted += 1
            digests.append(outcome.compilation_digest)
            maximum = max(
                maximum,
                *(
                    rich_token_count(fragment.contextual_text)
                    for fragment in outcome.fragments
                ),
            )
        else:
            refused += 1
    aggregate = hashlib.sha256()
    for digest in sorted(digests):
        aggregate.update(bytes.fromhex(digest))
    total = accepted + refused
    return {
        "schemaVersion": "compiler-runner-acceptance-v1",
        "compilerVersion": "context-engine-markdown-v3",
        "configVersion": "markdown-config-v3",
        "documents": {"accepted": accepted, "refused": refused, "total": total},
        "constructHistogram": histogram,
        "tokenCeiling": token_ceiling,
        "maxFragmentTokenCount": maximum,
        "aggregateCompilationDigest": aggregate.hexdigest(),
    }


def _write_acceptance_report(root: Path, output: Path, token_ceiling: int) -> None:
    if ".context-engine" not in output.parts:
        raise ValueError("acceptance reports must be written under .context-engine")
    report = _acceptance_report(root, token_ceiling)
    serialized = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)


def main() -> None:
    args = _parser().parse_args()
    if args.compile:
        _emit(
            sys.stdin.buffer.read(),
            MarkdownCompilerConfig(args.config),
            cast(int, args.token_ceiling),
        )
        return
    if args.acceptance_report:
        if args.root is None:
            raise SystemExit("--acceptance-report requires --root")
        _write_acceptance_report(
            cast(Path, args.root),
            cast(Path, args.output),
            cast(int, args.token_ceiling),
        )
        return
    raise SystemExit("one runner operation is required")


if __name__ == "__main__":
    main()
