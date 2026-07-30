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
from typing import Final, Never, Protocol, cast

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
from eval._compiler_acceptance import (
    _AcceptanceContext,
    acceptance_context,
    is_acceptance_context,
)

_RUNNER_MODULE: Final = "applications.compiler_runner"
COMPILER_RUNNER_TIMEOUT_SECONDS: Final = 30.0


class _AcceptanceEntryPoint(Protocol):
    def __call__(
        self,
        source: bytes,
        config: MarkdownCompilerConfig,
        *,
        acceptance_context: _AcceptanceContext,
    ) -> CompilationOutcome: ...


class _PrivacySafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise SystemExit("compiler runner arguments are invalid")


def _boundary_failure() -> CompilationFailure:
    return CompilationFailure(
        code=CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
        position=None,
    )


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


def _require_acceptance_context(
    entry_point: _AcceptanceEntryPoint,
) -> _AcceptanceEntryPoint:
    def guarded(
        source: bytes,
        config: MarkdownCompilerConfig,
        *,
        acceptance_context: _AcceptanceContext | None = None,
    ) -> CompilationOutcome:
        if is_acceptance_context(acceptance_context):
            return entry_point(
                source,
                config,
                acceptance_context=cast(_AcceptanceContext, acceptance_context),
            )
        return _boundary_failure()

    return cast(_AcceptanceEntryPoint, guarded)


@_require_acceptance_context
def compile_in_local_compiler_runner(
    source: bytes,
    config: MarkdownCompilerConfig,
    *,
    acceptance_context: _AcceptanceContext,
) -> CompilationOutcome:
    """Compile in an unleased local process that production must never call."""

    assert is_acceptance_context(acceptance_context)
    if type(source) is not bytes:
        raise TypeError("compiler-runner source must be exact bytes")
    if type(config) is not MarkdownCompilerConfig:
        raise TypeError("compiler-runner config must be exact")
    if config.token_ceiling is None:
        raise ValueError("compiler-runner requires rich Markdown config")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                _RUNNER_MODULE,
                "--compile",
                "--config",
                config.version,
                "--token-ceiling",
                str(config.token_ceiling),
            ],
            input=source,
            capture_output=True,
            check=False,
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
            return deserialize_parsed_document(
                base64.b64decode(encoded, validate=True)
            )
        except Exception:
            return _boundary_failure()
    if document.get("outcome") == "failure":
        try:
            return _failure_from_document(document.get("failure"))
        except Exception:
            return _boundary_failure()
    return _boundary_failure()


def _emit(
    source: bytes,
    config: MarkdownCompilerConfig,
    *,
    acceptance_context: _AcceptanceContext | None = None,
) -> None:
    if not is_acceptance_context(acceptance_context):
        outcome: CompilationOutcome = _boundary_failure()
    else:
        try:
            outcome = compile_rich_markdown(source, config)
        except Exception:
            outcome = _boundary_failure()
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
    parser = _PrivacySafeArgumentParser(description=__doc__)
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
    if root.is_symlink() or not root.is_dir():
        raise SystemExit("acceptance root must be a non-symlink directory")
    return tuple(
        sorted(
            (
                path
                for path in root.rglob("*", recurse_symlinks=False)
                if path.name.casefold().endswith(".md")
                and not path.is_symlink()
                and path.is_file()
            ),
            key=lambda path: PurePath(*path.relative_to(root).parts).as_posix(),
        )
    )


def _acceptance_report(
    root: Path,
    token_ceiling: int,
    *,
    acceptance_context: _AcceptanceContext,
) -> dict[str, object]:
    if not is_acceptance_context(acceptance_context):
        raise ValueError("acceptance report requires its private context")
    accepted = 0
    refused = 0
    digests: list[str] = []
    maximum = 0
    histogram = {name: 0 for name in _CONSTRUCT_PATTERNS}
    refusal_histogram: dict[str, int] = {}
    for path in _safe_markdown_files(root):
        try:
            source = path.read_bytes()
        except OSError:
            refused += 1
            category = CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE.value
            refusal_histogram[category] = refusal_histogram.get(category, 0) + 1
            continue
        try:
            inspected = source.removeprefix(b"\xef\xbb\xbf").decode("utf-8")
        except UnicodeDecodeError:
            inspected = ""
        normalized = inspected.replace("\r\n", "\n").replace("\r", "\n")
        for name, pattern in _CONSTRUCT_PATTERNS.items():
            histogram[name] += len(pattern.findall(normalized))
        outcome = compile_rich_markdown(
            source,
            MarkdownCompilerConfig(
                "markdown-config-v3",
                token_ceiling=token_ceiling,
            ),
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
            assert type(outcome) is CompilationFailure
            category = outcome.code.value
            if outcome.construct is not None:
                category = f"{category}:{outcome.construct.value}"
            refusal_histogram[category] = refusal_histogram.get(category, 0) + 1
    aggregate = hashlib.sha256()
    for digest in sorted(digests):
        aggregate.update(bytes.fromhex(digest))
    total = accepted + refused
    acceptance_rate = f"{accepted / total:.6f}" if total else "0.000000"
    return {
        "schemaVersion": "compiler-runner-acceptance-v1",
        "compilerVersion": "context-engine-markdown-v3",
        "configVersion": "markdown-config-v3",
        "documents": {
            "accepted": accepted,
            "acceptanceRate": acceptance_rate,
            "refused": refused,
            "total": total,
        },
        "constructHistogram": histogram,
        "refusalHistogram": refusal_histogram,
        "tokenCeiling": token_ceiling,
        "maxFragmentTokenCount": maximum,
        "aggregateCompilationDigest": aggregate.hexdigest(),
    }


def _write_acceptance_report(
    root: Path,
    output: Path,
    token_ceiling: int,
    *,
    acceptance_context: _AcceptanceContext,
) -> None:
    try:
        state_index = len(output.parts) - 1 - output.parts[::-1].index(
            ".context-engine"
        )
    except ValueError:
        raise SystemExit(
            "acceptance reports must be written under .context-engine"
        ) from None
    state_directory = Path(*output.parts[: state_index + 1]).resolve()
    resolved_output = output.resolve()
    if resolved_output == state_directory or not resolved_output.is_relative_to(
        state_directory
    ):
        raise SystemExit("acceptance reports must be written under .context-engine")
    report = _acceptance_report(
        root,
        token_ceiling,
        acceptance_context=acceptance_context,
    )
    serialized = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)


def main() -> None:
    args = _parser().parse_args()
    if args.compile:
        _emit(
            sys.stdin.buffer.read(),
            MarkdownCompilerConfig(
                args.config,
                token_ceiling=cast(int, args.token_ceiling),
            ),
            acceptance_context=acceptance_context(),
        )
        return
    if args.acceptance_report:
        if args.root is None:
            raise SystemExit("--acceptance-report requires --root")
        if cast(int, args.token_ceiling) < 1:
            raise SystemExit("rich Markdown token ceiling must be positive")
        _write_acceptance_report(
            cast(Path, args.root),
            cast(Path, args.output),
            cast(int, args.token_ceiling),
            acceptance_context=acceptance_context(),
        )
        return
    raise SystemExit("one runner operation is required")


def _privacy_safe_main() -> None:
    try:
        main()
    except Exception:
        raise SystemExit("compiler runner operation failed") from None


if __name__ == "__main__":
    _privacy_safe_main()
