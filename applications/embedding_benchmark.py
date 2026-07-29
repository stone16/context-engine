"""Tracked offline CLI for the embedding benchmark; never production-composed."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Sequence
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
    DatasetLockProfile,
    ModelIdentity,
    RetrievalJudge,
    run_benchmark,
    validate_json_schema_document,
    validate_report_document,
)

EVAL_ROOT = Path(__file__).resolve().parents[1] / "eval" / "embedding-benchmark"
INPUT_SCHEMA_PATH = EVAL_ROOT / "input.schema.json"
REPORT_SCHEMA_PATH = EVAL_ROOT / "report.schema.json"
MODEL_REGISTRY_PATH = EVAL_ROOT / "model-registry.json"
MODEL_REGISTRY_SCHEMA_PATH = EVAL_ROOT / "model-registry.schema.json"
LOCK_PROFILE = DatasetLockProfile.ACCIDENTAL_EDIT_DETECTION
BENCHMARK_EXTRA = "context-engine[benchmark]"
_IDENTITY_OVERRIDE_FILENAME = "benchmark-model-identity.json"


class SupportedBackend(StrEnum):
    """Closed benchmark backend set; arbitrary import paths are not accepted."""

    SENTENCE_TRANSFORMERS = "sentence-transformers"


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

    if type(backend) is not SupportedBackend or role not in {"primary", "baseline"}:
        raise BenchmarkUnavailable("benchmark backend is unavailable")
    identity = load_registered_model_identity(
        role=role,
        backend=backend,
        model_dir=model_dir,
    )
    if backend is SupportedBackend.SENTENCE_TRANSFORMERS:
        return SentenceTransformersProvider(identity=identity, model_dir=model_dir)
    raise BenchmarkUnavailable("benchmark backend is unavailable")


def load_registered_model_identity(
    *,
    role: str,
    backend: SupportedBackend,
    model_dir: Path,
) -> ModelIdentity:
    """Resolve identity only from the tracked registry and verify local bytes."""

    override_path = model_dir / _IDENTITY_OVERRIDE_FILENAME
    if override_path.exists():
        raise BenchmarkUnavailable("local model identity override is unavailable")
    try:
        registry = json.loads(MODEL_REGISTRY_PATH.read_text(encoding="utf-8"))
        registry_schema = json.loads(
            MODEL_REGISTRY_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        try:
            validate_json_schema_document(registry, registry_schema)
        except BenchmarkUnavailable:
            raise BenchmarkUnavailable("model identity is unresolved") from None
        registry_root = cast(dict[str, object], registry)
        if (
            registry_root["schemaVersion"]
            != "context-engine-embedding-model-registry-v1"
        ):
            raise BenchmarkUnavailable("model identity is unresolved")
        models = cast(dict[str, object], registry_root["models"])
        if (
            role not in {"primary", "baseline"}
            or frozenset(models).difference({"primary", "baseline"})
            or role not in models
        ):
            raise BenchmarkUnavailable("model identity is unresolved")
        model = cast(dict[str, object], models[role])
        if model["backend"] != backend.value:
            raise BenchmarkUnavailable("model identity is unresolved")
        artifact_documents = cast(list[object], model["artifacts"])
        expected_artifacts = tuple(
            (
                cast(str, cast(dict[str, object], value)["path"]),
                cast(str, cast(dict[str, object], value)["sha256"]),
            )
            for value in artifact_documents
        )
        identity_document = cast(dict[str, object], model["identity"])
        identity = ModelIdentity(
            model_id=cast(str, identity_document["modelId"]),
            revision=cast(str, identity_document["revision"]),
            artifact_digest=cast(str, identity_document["artifactDigest"]),
            dimension=cast(int, identity_document["dimension"]),
            normalization=cast(str, identity_document["normalization"]),
            pooling=cast(str, identity_document["pooling"]),
            query_prefix=cast(str, identity_document["queryPrefix"]),
            document_prefix=cast(str, identity_document["documentPrefix"]),
            reduction=cast(str, identity_document["reduction"]),
            precision=cast(str, identity_document["precision"]),
            batch_size=cast(int, identity_document["batchSize"]),
        )
        if identity.artifact_digest != _model_artifact_digest(
            model_dir,
            expected_artifacts=expected_artifacts,
        ):
            raise BenchmarkUnavailable("model identity is unresolved")
        return identity
    except BenchmarkUnavailable:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        raise BenchmarkUnavailable("model identity is unresolved") from None


def _model_artifact_digest(
    model_dir: Path,
    *,
    expected_artifacts: tuple[tuple[str, str], ...],
) -> str:
    files = tuple(sorted(path for path in model_dir.rglob("*") if path.is_file()))
    if (
        not files
        or any(path.is_symlink() for path in files)
        or any(not path.resolve().is_relative_to(model_dir.resolve()) for path in files)
    ):
        raise BenchmarkUnavailable("model identity is unresolved")
    actual_paths = tuple(path.relative_to(model_dir).as_posix() for path in files)
    if actual_paths != tuple(path for path, _digest in expected_artifacts):
        raise BenchmarkUnavailable("model identity is unresolved")
    manifest: list[dict[str, str]] = []
    for path, (relative_path, expected_digest) in zip(
        files, expected_artifacts, strict=True
    ):
        file_digest = sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                file_digest.update(block)
        actual_digest = file_digest.hexdigest()
        if actual_digest != expected_digest:
            raise BenchmarkUnavailable("model identity is unresolved")
        manifest.append({"path": relative_path, "sha256": actual_digest})
    return sha256(rfc8785.dumps(manifest)).hexdigest()


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
        root = cast(dict[str, object], raw_root)
        lock = cast(dict[str, object], root["lock"])
        unlocked = {key: value for key, value in root.items() if key != "lock"}
        if (
            lock["profile"] != LOCK_PROFILE.value
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
            for document in [cast(dict[str, object], value)]
        )
        cases = tuple(
            BenchmarkCase(
                case_ref=_text(case["caseRef"]),
                query=_text(case["query"]),
                expected_document_refs=_refs(case["expectedDocumentRefs"]),
                slice_name=_text(case["slice"]),
            )
            for value in raw_cases
            for case in [cast(dict[str, object], value)]
        )
        return BenchmarkDataset(
            documents=documents,
            cases=cases,
            lock_profile=LOCK_PROFILE,
        )
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
