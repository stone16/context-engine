"""Tracked offline CLI for the embedding benchmark; never production-composed."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast

from eval.embedding_benchmark import (
    BenchmarkCase,
    BenchmarkDataset,
    BenchmarkDocument,
    BenchmarkEmbeddingProvider,
    BenchmarkUnavailable,
    RetrievalJudge,
    run_benchmark,
    validate_report_document,
)

REPORT_SCHEMA_PATH = Path("eval/embedding-benchmark/report.schema.json")


class LocalProviderFactory(Protocol):
    def __call__(self, model_dir: Path) -> BenchmarkEmbeddingProvider: ...


def build_local_provider(role: str, model_dir: Path) -> BenchmarkEmbeddingProvider:
    """Load an optional benchmark-only provider plugin from the model directory.

    The plugin is deliberately outside the locked production environment. A local
    model directory supplies ``context_engine_provider.py`` with ``create_provider``;
    the returned provider still must expose the exact pinned identity in the report.
    """

    plugin = model_dir / "context_engine_provider.py"
    if not plugin.is_file():
        raise BenchmarkUnavailable(
            f"{role} local provider is unavailable; model directory must contain "
            "context_engine_provider.py"
        )
    spec = importlib.util.spec_from_file_location(
        f"context_engine_embedding_benchmark_{role}", plugin
    )
    if spec is None or spec.loader is None:
        raise BenchmarkUnavailable(f"{role} local provider is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, "create_provider", None)
    if not callable(factory):
        raise BenchmarkUnavailable(f"{role} local provider is unavailable")
    return cast(LocalProviderFactory, factory)(model_dir)


def load_retrieval_judge(specification: str) -> RetrievalJudge:
    """Load #129's judge through its injected adapter factory."""

    try:
        module_name, separator, factory_name = specification.partition(":")
        if not separator or not module_name or not factory_name:
            raise ValueError
        factory = getattr(importlib.import_module(module_name), factory_name)
        if not callable(factory):
            raise TypeError
        return cast(Callable[[], RetrievalJudge], factory)()
    except (ImportError, AttributeError, TypeError, ValueError):
        raise BenchmarkUnavailable("retrieval judge is unavailable") from None


def load_dataset(path: Path) -> BenchmarkDataset:
    """Load the strict drop-in input format used by the pending durable corpus."""

    try:
        root = _closed(
            json.loads(path.read_text(encoding="utf-8")),
            frozenset({"cases", "documents", "schemaVersion"}),
        )
        if root["schemaVersion"] != "context-engine-embedding-benchmark-input-v1":
            raise BenchmarkUnavailable("benchmark dataset is unavailable")
        raw_documents = root["documents"]
        raw_cases = root["cases"]
        if type(raw_documents) is not list or type(raw_cases) is not list:
            raise BenchmarkUnavailable("benchmark dataset is unavailable")
        documents = tuple(
            BenchmarkDocument(
                document_ref=_text(document["documentRef"]),
                text=_text(document["text"]),
            )
            for value in raw_documents
            for document in [_closed(value, frozenset({"documentRef", "text"}))]
        )
        cases = tuple(
            BenchmarkCase(
                case_ref=_text(case["caseRef"]),
                query=_text(case["query"]),
                expected_document_refs=_refs(case["expectedDocumentRefs"]),
                slice_name=_text(case["slice"]),
            )
            for value in raw_cases
            for case in [
                _closed(
                    value,
                    frozenset(
                        {"caseRef", "expectedDocumentRefs", "query", "slice"}
                    ),
                )
            ]
        )
        return BenchmarkDataset(documents=documents, cases=cases)
    except BenchmarkUnavailable:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        raise BenchmarkUnavailable("benchmark dataset is unavailable") from None


def _closed(value: object, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != fields:
        raise BenchmarkUnavailable("benchmark dataset is unavailable")
    if any(type(key) is not str for key in value):
        raise BenchmarkUnavailable("benchmark dataset is unavailable")
    return value


def _text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise BenchmarkUnavailable("benchmark dataset is unavailable")
    return value


def _refs(value: object) -> tuple[str, ...]:
    if type(value) is not list:
        raise BenchmarkUnavailable("benchmark dataset is unavailable")
    return tuple(_text(item) for item in value)


def _require_ignored_output(path: Path, *, workspace_root: Path) -> None:
    state_root = (workspace_root / ".context-engine").resolve()
    if not path.resolve().is_relative_to(state_root):
        raise BenchmarkUnavailable(
            "benchmark output must be under the ignored .context-engine directory"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-engine-embedding-benchmark",
        description="Run the offline 384-dimensional embedding comparison.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run both pinned local models")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--primary-model-dir", type=Path, required=True)
    run.add_argument("--baseline-model-dir", type=Path, required=True)
    run.add_argument("--judge", required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--top-k", type=int, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        output = cast(Path, arguments.output)
        _require_ignored_output(output, workspace_root=Path.cwd())
        report = run_benchmark(
            dataset=load_dataset(cast(Path, arguments.dataset)),
            primary=build_local_provider(
                "primary", cast(Path, arguments.primary_model_dir)
            ),
            baseline=build_local_provider(
                "baseline", cast(Path, arguments.baseline_model_dir)
            ),
            judge=load_retrieval_judge(cast(str, arguments.judge)),
            top_k=cast(int, arguments.top_k),
            clock=perf_counter,
        )
        validate_report_document(report, schema_path=REPORT_SCHEMA_PATH)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except BenchmarkUnavailable as failure:
        print(str(failure), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
