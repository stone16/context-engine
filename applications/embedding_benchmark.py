"""Tracked offline CLI for the embedding benchmark; never production-composed."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import rfc8785

from eval.embedding_benchmark import (
    EMBEDDING_DIMENSION,
    BenchmarkCase,
    BenchmarkDataset,
    BenchmarkDocument,
    BenchmarkEmbeddingProvider,
    BenchmarkUnavailable,
    ModelIdentity,
    RetrievalJudge,
    run_benchmark,
    validate_json_schema_document,
    validate_report_document,
)

EVAL_ROOT = Path(__file__).resolve().parents[1] / "eval" / "embedding-benchmark"
INPUT_SCHEMA_PATH = EVAL_ROOT / "input.schema.json"
REPORT_SCHEMA_PATH = EVAL_ROOT / "report.schema.json"
LOCK_PROFILE = "sha256-rfc8785-v1"
BENCHMARK_EXTRA = "context-engine[benchmark]"


class SupportedBackend(StrEnum):
    """Closed benchmark backend set; arbitrary import paths are not accepted."""

    SENTENCE_TRANSFORMERS = "sentence-transformers"


@dataclass(frozen=True, slots=True)
class _ExpectedModel:
    model_id: str
    revision: str
    pooling: str
    query_prefix: str
    document_prefix: str
    reduction: str


_EXPECTED_MODELS = {
    "primary": _ExpectedModel(
        model_id="Qwen/Qwen3-Embedding-0.6B",
        revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        pooling="last_token",
        query_prefix=(
            "Instruct: Given a web search query, retrieve relevant passages that "
            "answer the query\nQuery:"
        ),
        document_prefix="",
        reduction="matryoshka_truncate_384",
    ),
    "baseline": _ExpectedModel(
        model_id="intfloat/multilingual-e5-small",
        revision="614241f622f53c4eeff9890bdc4f31cfecc418b3",
        pooling="mean",
        query_prefix="query: ",
        document_prefix="passage: ",
        reduction="none_native_384",
    ),
}


class SentenceTransformersProvider:
    """Local benchmark backend available only through the optional extra."""

    def __init__(self, *, identity: ModelIdentity, model_dir: Path) -> None:
        self.identity = identity
        backend = _load_sentence_transformers()
        try:
            self._model = backend.SentenceTransformer(
                str(model_dir),
                local_files_only=True,
                trust_remote_code=False,
            )
        except Exception:
            raise BenchmarkUnavailable("local benchmark model is unavailable") from None

    def embed_queries(
        self, values: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return self._embed(values)

    def embed_documents(
        self, values: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        return self._embed(values)

    def _embed(self, values: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        try:
            vectors = self._model.encode(
                list(values),
                batch_size=self.identity.batch_size,
                convert_to_numpy=True,
                normalize_embeddings=self.identity.normalization == "l2",
                precision=self.identity.precision,
                show_progress_bar=False,
            )
            return tuple(
                tuple(float(value) for value in vector[:EMBEDDING_DIMENSION])
                for vector in vectors
            )
        except Exception:
            raise BenchmarkUnavailable("local benchmark model is unavailable") from None


def _load_sentence_transformers() -> Any:
    try:
        return importlib.import_module("sentence_transformers")
    except ModuleNotFoundError:
        raise BenchmarkUnavailable(
            f"benchmark backend is unavailable; install the {BENCHMARK_EXTRA} extra"
        ) from None


def build_local_provider(
    backend: SupportedBackend,
    role: str,
    model_dir: Path,
) -> BenchmarkEmbeddingProvider:
    """Construct one of the two known models through a closed backend enum."""

    if type(backend) is not SupportedBackend or role not in _EXPECTED_MODELS:
        raise BenchmarkUnavailable("benchmark backend is unavailable")
    expected = _EXPECTED_MODELS[role]
    identity = _load_model_identity(model_dir, expected)
    if backend is SupportedBackend.SENTENCE_TRANSFORMERS:
        return SentenceTransformersProvider(identity=identity, model_dir=model_dir)
    raise BenchmarkUnavailable("benchmark backend is unavailable")


def _load_model_identity(model_dir: Path, expected: _ExpectedModel) -> ModelIdentity:
    manifest_path = model_dir / "benchmark-model-identity.json"
    try:
        manifest = _closed(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            frozenset(
                {
                    "artifactDigest",
                    "batchSize",
                    "dimension",
                    "documentPrefix",
                    "modelId",
                    "normalization",
                    "pooling",
                    "precision",
                    "queryPrefix",
                    "reduction",
                    "revision",
                }
            ),
        )
        identity = ModelIdentity(
            model_id=cast(str, manifest["modelId"]),
            revision=cast(str, manifest["revision"]),
            artifact_digest=cast(str, manifest["artifactDigest"]),
            dimension=cast(int, manifest["dimension"]),
            normalization=cast(str, manifest["normalization"]),
            pooling=cast(str, manifest["pooling"]),
            query_prefix=cast(str, manifest["queryPrefix"]),
            document_prefix=cast(str, manifest["documentPrefix"]),
            reduction=cast(str, manifest["reduction"]),
            precision=cast(str, manifest["precision"]),
            batch_size=cast(int, manifest["batchSize"]),
        )
        if (
            identity.model_id != expected.model_id
            or identity.revision != expected.revision
            or identity.pooling != expected.pooling
            or identity.query_prefix != expected.query_prefix
            or identity.document_prefix != expected.document_prefix
            or identity.reduction != expected.reduction
            or identity.artifact_digest
            != _model_artifact_digest(model_dir, excluded=manifest_path)
        ):
            raise BenchmarkUnavailable("model identity is unresolved")
        return identity
    except BenchmarkUnavailable:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        raise BenchmarkUnavailable("model identity is unresolved") from None


def _model_artifact_digest(model_dir: Path, *, excluded: Path) -> str:
    digest = sha256()
    files = tuple(sorted(path for path in model_dir.rglob("*") if path.is_file()))
    if not files or any(path.is_symlink() for path in files):
        raise BenchmarkUnavailable("model identity is unresolved")
    for path in files:
        if path == excluded:
            continue
        relative = path.relative_to(model_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def load_retrieval_judge() -> RetrievalJudge:
    """Load only the fixed #129 judge factory; caller-authored imports are denied."""
    try:
        factory = importlib.import_module("eval.retrieval_judge").create_judge
        if not callable(factory):
            raise TypeError
        return cast(Callable[[], RetrievalJudge], factory)()
    except (ImportError, AttributeError, TypeError):
        raise BenchmarkUnavailable("retrieval judge is unavailable") from None


def load_dataset(path: Path) -> BenchmarkDataset:
    """Load the strict drop-in input format used by the pending durable corpus."""

    try:
        raw_root = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(INPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
        try:
            validate_json_schema_document(raw_root, schema)
        except BenchmarkUnavailable:
            raise BenchmarkUnavailable(
                "benchmark input schema is unavailable"
            ) from None
        root = _closed(
            raw_root,
            frozenset({"cases", "documents", "lock", "schemaVersion"}),
        )
        lock = _closed(root["lock"], frozenset({"contentDigest", "profile"}))
        unlocked = {key: value for key, value in root.items() if key != "lock"}
        if (
            lock["profile"] != LOCK_PROFILE
            or lock["contentDigest"] != dataset_content_digest(unlocked)
        ):
            raise BenchmarkUnavailable("benchmark dataset lock is unavailable")
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


def dataset_content_digest(document: object) -> str:
    """Return the mechanical lock digest over the complete unlocked pilot."""

    try:
        return sha256(rfc8785.dumps(cast(Any, document))).hexdigest()
    except (TypeError, ValueError, OverflowError):
        raise BenchmarkUnavailable("benchmark dataset lock is unavailable") from None


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
    run.add_argument(
        "--backend",
        type=SupportedBackend,
        choices=tuple(SupportedBackend),
        default=SupportedBackend.SENTENCE_TRANSFORMERS,
    )
    run.add_argument("--primary-model-dir", type=Path, required=True)
    run.add_argument("--baseline-model-dir", type=Path, required=True)
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
                cast(SupportedBackend, arguments.backend),
                "primary",
                cast(Path, arguments.primary_model_dir),
            ),
            baseline=build_local_provider(
                cast(SupportedBackend, arguments.backend),
                "baseline",
                cast(Path, arguments.baseline_model_dir),
            ),
            judge=load_retrieval_judge(),
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
