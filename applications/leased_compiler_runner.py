"""Pure rich-Markdown transform selected by an exact leased Supply worker."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from typing import Never, cast

from adapters.parsers.ragflow_markdown import compile_rich_markdown
from engine.supply.markdown import (
    CompilationFailure,
    CompilationFailureCode,
    CompilationOutcome,
    MarkdownCompilerConfig,
    ParsedDocument,
    canonicalize_parsed_document,
)


class _ClosedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise SystemExit("leased compiler runner arguments are invalid")


def _boundary_failure() -> CompilationFailure:
    return CompilationFailure(
        code=CompilationFailureCode.UNSUPPORTED_DOCUMENT_SHAPE,
        position=None,
    )


def _failure_document(failure: CompilationFailure) -> dict[str, object]:
    return {"code": failure.code.value}


def _emit(outcome: CompilationOutcome) -> None:
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


def main() -> None:
    parser = _ClosedArgumentParser(description=__doc__)
    parser.add_argument("--compile-leased", action="store_true")
    parser.add_argument("--config", required=True)
    parser.add_argument("--token-ceiling", required=True, type=int)
    args = parser.parse_args()
    if not args.compile_leased:
        raise SystemExit("leased compiler runner arguments are invalid")
    try:
        outcome = compile_rich_markdown(
            sys.stdin.buffer.read(),
            MarkdownCompilerConfig(
                cast(str, args.config),
                token_ceiling=cast(int, args.token_ceiling),
            ),
        )
    except Exception:
        outcome = _boundary_failure()
    _emit(outcome)


if __name__ == "__main__":
    main()
