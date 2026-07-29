"""Offline-only embedding benchmark contracts and report validation."""

from __future__ import annotations

import json
import math
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, Protocol, cast

import rfc8785

from engine.supply.embeddings import CONTEXT_FRAGMENT_EMBEDDING_DIMENSION

EMBEDDING_DIMENSION: Final = CONTEXT_FRAGMENT_EMBEDDING_DIMENSION
REPORT_SCHEMA_VERSION: Final = "context-engine-embedding-benchmark-report-v1"
_PINNED_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")
PRIMARY_TRANSFORMATION_PIPELINE: Final = "l2 -> truncate 1024->384 -> l2"
BASELINE_TRANSFORMATION_PIPELINE: Final = "l2 -> keep native 384 -> l2"
_MAX_JSON_BYTES: Final = 16 * 1024 * 1024
_MAX_JSON_DEPTH: Final = 64
_MAX_JSON_CONTAINER_ITEMS: Final = 10_000
_MAX_JSON_STRING_LENGTH: Final = 1024 * 1024
_MAX_JSON_NODES: Final = 100_000
_MAX_JSON_INTEGER: Final = (1 << 63) - 1
_MIN_JSON_INTEGER: Final = -(1 << 63)
_MAX_ABS_JSON_FLOAT: Final = 1e18
_METRIC_TOLERANCE: Final = 1e-12


class BenchmarkUnavailable(RuntimeError):
    """The benchmark input, identity, provider, judge, or output is unavailable."""


class DatasetLockProfile(StrEnum):
    """Typed M1 lock that detects accidental edits by one trusted operator."""

    ACCIDENTAL_EDIT_DETECTION = (
        "sha256-rfc8785-accidental-edit-detection-v1"
    )


class ModelTransformationPipeline(StrEnum):
    """Closed, executable vector transformation identities."""

    PRIMARY = PRIMARY_TRANSFORMATION_PIPELINE
    BASELINE = BASELINE_TRANSFORMATION_PIPELINE

    @property
    def raw_dimension(self) -> int:
        if self is ModelTransformationPipeline.PRIMARY:
            return 1024
        if self is ModelTransformationPipeline.BASELINE:
            return EMBEDDING_DIMENSION
        raise BenchmarkUnavailable("model identity is unresolved")


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Complete, reproducible identity for one offline embedding provider."""

    model_id: str
    revision: str
    artifact_digest: str
    dimension: int
    transformation_pipeline: ModelTransformationPipeline
    pooling: str
    query_prefix: str
    document_prefix: str
    precision: str
    batch_size: int

    def __post_init__(self) -> None:
        if (
            type(self.model_id) is not str
            or not self.model_id
            or self.model_id != self.model_id.strip()
            or not _PINNED_REVISION.fullmatch(self.revision)
            or not _SHA256_DIGEST.fullmatch(self.artifact_digest)
            or type(self.dimension) is not int
            or self.dimension != EMBEDDING_DIMENSION
            or type(self.transformation_pipeline) is not ModelTransformationPipeline
            or type(self.pooling) is not str
            or not self.pooling
            or self.pooling != self.pooling.strip()
            or type(self.query_prefix) is not str
            or type(self.document_prefix) is not str
            or type(self.precision) is not str
            or not self.precision
            or self.precision != self.precision.strip()
            or type(self.batch_size) is not int
            or not 1 <= self.batch_size <= 1024
        ):
            raise BenchmarkUnavailable("model identity is unresolved")

    def public_document(self) -> dict[str, object]:
        return {
            "artifactDigest": self.artifact_digest,
            "batchSize": self.batch_size,
            "dimension": self.dimension,
            "documentPrefix": self.document_prefix,
            "modelId": self.model_id,
            "pooling": self.pooling,
            "precision": self.precision,
            "queryPrefix": self.query_prefix,
            "revision": self.revision,
            "transformationPipeline": self.transformation_pipeline.value,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkDocument:
    """One opaque benchmark document reference and its offline content."""

    document_ref: str
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_nonblank("benchmark document_ref", self.document_ref)
        _require_nonblank("benchmark document text", self.text)


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One retrieval query and its exact expected document references."""

    case_ref: str
    query: str = field(repr=False)
    expected_document_refs: tuple[str, ...]
    slice_name: str

    def __post_init__(self) -> None:
        _require_nonblank("benchmark case_ref", self.case_ref)
        _require_nonblank("benchmark query", self.query)
        _require_nonblank("benchmark slice", self.slice_name)
        if (
            type(self.expected_document_refs) is not tuple
            or not self.expected_document_refs
            or any(
                type(value) is not str or not value or value != value.strip()
                for value in self.expected_document_refs
            )
            or len(set(self.expected_document_refs))
            != len(self.expected_document_refs)
        ):
            raise BenchmarkUnavailable("benchmark expected Evidence is unavailable")


@dataclass(frozen=True, slots=True)
class BenchmarkDataset:
    """Strict offline input that can be loaded from the pending durable corpus."""

    documents: tuple[BenchmarkDocument, ...] = field(repr=False)
    cases: tuple[BenchmarkCase, ...] = field(repr=False)
    locked: bool = True
    lock_profile: DatasetLockProfile = DatasetLockProfile.ACCIDENTAL_EDIT_DETECTION
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            type(self.documents) is not tuple
            or not self.documents
            or any(type(value) is not BenchmarkDocument for value in self.documents)
            or type(self.cases) is not tuple
            or not self.cases
            or any(type(value) is not BenchmarkCase for value in self.cases)
            or type(self.locked) is not bool
            or not self.locked
            or self.lock_profile is not DatasetLockProfile.ACCIDENTAL_EDIT_DETECTION
        ):
            raise BenchmarkUnavailable("benchmark dataset is unavailable")
        document_refs = tuple(value.document_ref for value in self.documents)
        case_refs = tuple(value.case_ref for value in self.cases)
        if (
            len(set(document_refs)) != len(document_refs)
            or len(set(case_refs)) != len(case_refs)
            or any(
                expected not in set(document_refs)
                for case in self.cases
                for expected in case.expected_document_refs
            )
        ):
            raise BenchmarkUnavailable("benchmark dataset is unavailable")
        document = {
            "cases": [
                {
                    "caseRef": case.case_ref,
                    "expectedDocumentRefs": list(case.expected_document_refs),
                    "query": case.query,
                    "slice": case.slice_name,
                }
                for case in self.cases
            ],
            "documents": [
                {"documentRef": value.document_ref, "text": value.text}
                for value in self.documents
            ],
            "schemaVersion": "context-engine-embedding-benchmark-input-v1",
        }
        object.__setattr__(self, "digest", _digest(document))


class BenchmarkEmbeddingProvider(Protocol):
    """Benchmark-only provider; no production composition accepts this seam."""

    @property
    def identity(self) -> ModelIdentity: ...

    def embed_queries(
        self, values: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]: ...

    def embed_documents(
        self, values: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]: ...


@dataclass(frozen=True, slots=True)
class RetrievalJudgeCase:
    """Adapter document passed to the #129 retrieval judge."""

    case_ref: str
    expected_evidence: tuple[str, ...]
    retrieved_evidence: tuple[str, ...]
    slice_name: str


@dataclass(frozen=True, slots=True)
class CaseHitMetric:
    hits: int
    total_cases: int
    value: float

    def __post_init__(self) -> None:
        if (
            type(self.hits) is not int
            or type(self.total_cases) is not int
            or not 0 <= self.hits <= self.total_cases
            or self.total_cases < 1
        ):
            raise BenchmarkUnavailable("retrieval judge is unavailable")
        _require_judge_ratio(self.value)


@dataclass(frozen=True, slots=True)
class MacroRecallMetric:
    value: float

    def __post_init__(self) -> None:
        _require_judge_ratio(self.value)


@dataclass(frozen=True, slots=True)
class MicroRecallMetric:
    hits: int
    total_expected: int
    value: float

    def __post_init__(self) -> None:
        if (
            type(self.hits) is not int
            or type(self.total_expected) is not int
            or not 0 <= self.hits <= self.total_expected
            or self.total_expected < 1
        ):
            raise BenchmarkUnavailable("retrieval judge is unavailable")
        _require_judge_ratio(self.value)


@dataclass(frozen=True, slots=True)
class EvidenceRecallMetric:
    macro: MacroRecallMetric
    micro: MicroRecallMetric

    def __post_init__(self) -> None:
        if (
            type(self.macro) is not MacroRecallMetric
            or type(self.micro) is not MicroRecallMetric
        ):
            raise BenchmarkUnavailable("retrieval judge is unavailable")


@dataclass(frozen=True, slots=True)
class SliceMetrics:
    case_hit: CaseHitMetric
    evidence_recall: EvidenceRecallMetric

    def __post_init__(self) -> None:
        if (
            type(self.case_hit) is not CaseHitMetric
            or type(self.evidence_recall) is not EvidenceRecallMetric
        ):
            raise BenchmarkUnavailable("retrieval judge is unavailable")


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    case_hit: CaseHitMetric
    evidence_recall: EvidenceRecallMetric
    per_slice: Mapping[str, SliceMetrics]

    def __post_init__(self) -> None:
        if (
            type(self.case_hit) is not CaseHitMetric
            or type(self.evidence_recall) is not EvidenceRecallMetric
            or not isinstance(self.per_slice, Mapping)
            or not self.per_slice
            or any(
                type(name) is not str
                or not name
                or type(value) is not SliceMetrics
                for name, value in self.per_slice.items()
            )
        ):
            raise BenchmarkUnavailable("retrieval judge is unavailable")


class ModelComparisonOutcome(StrEnum):
    """Closed Pareto outcome; mixed metric wins stay first-class."""

    WIN = "win"
    LOSE = "lose"
    TIE = "tie"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class ModelComparison:
    """Typed Pareto verdict and primary-minus-baseline metric deltas."""

    outcome: ModelComparisonOutcome
    deltas: Mapping[str, float]

    def __post_init__(self) -> None:
        if type(self.outcome) is not ModelComparisonOutcome:
            raise BenchmarkUnavailable("model comparison is unavailable")
        if frozenset(self.deltas) != frozenset(
            {"caseHit", "macroEvidenceRecall", "microEvidenceRecall"}
        ) or any(not math.isfinite(value) for value in self.deltas.values()):
            raise BenchmarkUnavailable("model comparison is unavailable")

    def public_document(self) -> dict[str, object]:
        return {"deltas": dict(self.deltas), "outcome": self.outcome.value}


class RetrievalJudge(Protocol):
    """Injected adapter to #129; this issue intentionally implements no metrics."""

    def evaluate_retrieval(
        self, cases: tuple[RetrievalJudgeCase, ...]
    ) -> RetrievalMetrics: ...


def run_benchmark(
    *,
    dataset: BenchmarkDataset,
    primary: BenchmarkEmbeddingProvider,
    baseline: BenchmarkEmbeddingProvider,
    judge: RetrievalJudge,
    top_k: int,
    clock: Callable[[], float],
) -> dict[str, object]:
    """Compare two pinned providers and delegate every metric to #129's judge."""

    if type(dataset) is not BenchmarkDataset or type(top_k) is not int or top_k < 1:
        raise BenchmarkUnavailable("benchmark configuration is unavailable")
    if primary.identity.model_id != "Qwen/Qwen3-Embedding-0.6B":
        raise BenchmarkUnavailable("primary benchmark model is unavailable")
    if (
        primary.identity.transformation_pipeline
        is not ModelTransformationPipeline.PRIMARY
    ):
        raise BenchmarkUnavailable("primary benchmark pipeline is unavailable")
    if baseline.identity.model_id != "intfloat/multilingual-e5-small":
        raise BenchmarkUnavailable("baseline benchmark model is unavailable")
    if (
        baseline.identity.transformation_pipeline
        is not ModelTransformationPipeline.BASELINE
    ):
        raise BenchmarkUnavailable("baseline benchmark pipeline is unavailable")
    primary_report = _run_model(dataset, primary, judge, top_k, clock)
    baseline_report = _run_model(dataset, baseline, judge, top_k, clock)
    model_comparison = compare_model_metrics(
        primary_report.metrics,
        baseline_report.metrics,
    )
    primary_hit = primary_report.metrics.case_hit.value
    run_document = {
        "datasetDigest": dataset.digest,
        "topK": top_k,
    }
    run_identity = _digest(
        {
            **run_document,
            "baselineIdentity": baseline.identity.public_document(),
            "primaryIdentity": primary.identity.public_document(),
        }
    )
    return {
        "comparison": {
            "metricDeltas": dict(model_comparison.deltas),
            "primaryAgainstModelBaseline": model_comparison.outcome.value,
            "primaryAgainstStandingTwinBaseline": _comparison(primary_hit, 0.038),
        },
        "models": {
            "baseline": baseline_report.public_document(),
            "primary": primary_report.public_document(),
        },
        "run": {**run_document, "runIdentity": run_identity},
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "standingTwinBaseline": {"caseHitValue": 0.038},
    }


@dataclass(frozen=True, slots=True)
class _ModelRun:
    document: Mapping[str, object]
    metrics: RetrievalMetrics

    def public_document(self) -> dict[str, object]:
        return dict(self.document)


@dataclass(frozen=True, slots=True)
class _ReportPopulation:
    """Structural population identity shared by both model evaluations."""

    total_cases: int
    total_expected: int
    slice_counts: tuple[tuple[str, int, int], ...]
    document_count: int


@dataclass(frozen=True, slots=True)
class _ValidatedModelReport:
    values: tuple[float, float, float]
    population: _ReportPopulation


def _run_model(
    dataset: BenchmarkDataset,
    provider: BenchmarkEmbeddingProvider,
    judge: RetrievalJudge,
    top_k: int,
    clock: Callable[[], float],
) -> _ModelRun:
    query_values = tuple(
        provider.identity.query_prefix + case.query for case in dataset.cases
    )
    document_values = tuple(
        provider.identity.document_prefix + document.text
        for document in dataset.documents
    )
    started = clock()
    query_vectors = _validated_vectors(
        query_values,
        provider.embed_queries(query_values),
    )
    document_started = clock()
    document_vectors = _validated_vectors(
        document_values, provider.embed_documents(document_values)
    )
    document_finished = clock()
    document_refs = tuple(value.document_ref for value in dataset.documents)
    judged_cases = tuple(
        RetrievalJudgeCase(
            case_ref=case.case_ref,
            expected_evidence=case.expected_document_refs,
            retrieved_evidence=_retrieve(
                query_vectors[index], document_vectors, document_refs, top_k
            ),
            slice_name=case.slice_name,
        )
        for index, case in enumerate(dataset.cases)
    )
    metrics = judge.evaluate_retrieval(judged_cases)
    if type(metrics) is not RetrievalMetrics:
        raise BenchmarkUnavailable("retrieval judge is unavailable")
    finished = clock()
    return _ModelRun(
        document={
            "identity": provider.identity.public_document(),
            "metrics": _metrics_document(metrics),
            "timing": {
                "documentCount": len(dataset.documents),
                "perDocumentEmbedMilliseconds": max(
                    0.0,
                    (document_finished - document_started)
                    * 1000
                    / len(dataset.documents),
                ),
                "wallClockMilliseconds": max(0.0, (finished - started) * 1000),
            },
        },
        metrics=metrics,
    )


def _validated_vectors(
    inputs: tuple[str, ...],
    vectors: object,
) -> tuple[tuple[float, ...], ...]:
    if type(vectors) is not tuple or len(vectors) != len(inputs):
        raise BenchmarkUnavailable("embedding provider is unavailable")
    validated: list[tuple[float, ...]] = []
    for vector in vectors:
        if (
            type(vector) is not tuple
            or len(vector) != EMBEDDING_DIMENSION
            or any(type(value) not in {int, float} for value in vector)
        ):
            raise BenchmarkUnavailable("embedding provider is unavailable")
        converted = tuple(float(value) for value in vector)
        if not all(math.isfinite(value) for value in converted) or not any(converted):
            raise BenchmarkUnavailable("embedding provider is unavailable")
        validated.append(converted)
    return tuple(validated)


def _retrieve(
    query: tuple[float, ...],
    documents: tuple[tuple[float, ...], ...],
    document_refs: tuple[str, ...],
    top_k: int,
) -> tuple[str, ...]:
    ranked = sorted(
        zip(document_refs, documents, strict=True),
        key=lambda item: (
            -_cosine_similarity(query, item[1]),
            item[0],
        ),
    )
    return tuple(ref for ref, _vector in ranked[:top_k])


def _cosine_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    )
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot_product / (left_norm * right_norm)


def _metrics_document(metrics: RetrievalMetrics) -> dict[str, object]:
    return {
        "caseHit": {
            "hits": metrics.case_hit.hits,
            "totalCases": metrics.case_hit.total_cases,
            "value": metrics.case_hit.value,
        },
        "evidenceRecall": _recall_document(metrics.evidence_recall),
        "perSlice": {
            name: {
                "caseHit": {
                    "hits": value.case_hit.hits,
                    "totalCases": value.case_hit.total_cases,
                    "value": value.case_hit.value,
                },
                "evidenceRecall": _recall_document(value.evidence_recall),
            }
            for name, value in sorted(metrics.per_slice.items())
        },
    }


def _recall_document(value: EvidenceRecallMetric) -> dict[str, object]:
    return {
        "macro": {"value": value.macro.value},
        "micro": {
            "hits": value.micro.hits,
            "totalExpected": value.micro.total_expected,
            "value": value.micro.value,
        },
    }


def compare_model_metrics(
    primary: RetrievalMetrics,
    baseline: RetrievalMetrics,
) -> ModelComparison:
    """Apply the frozen no-blending Pareto rule across three retrieval metrics."""

    primary_values = (
        primary.case_hit.value,
        primary.evidence_recall.macro.value,
        primary.evidence_recall.micro.value,
    )
    baseline_values = (
        baseline.case_hit.value,
        baseline.evidence_recall.macro.value,
        baseline.evidence_recall.micro.value,
    )
    outcome = _pareto_outcome(primary_values, baseline_values)
    return ModelComparison(
        outcome=outcome,
        deltas={
            "caseHit": _metric_delta(primary_values[0], baseline_values[0]),
            "macroEvidenceRecall": _metric_delta(
                primary_values[1], baseline_values[1]
            ),
            "microEvidenceRecall": _metric_delta(
                primary_values[2], baseline_values[2]
            ),
        },
    )


def _pareto_outcome(
    primary_values: tuple[float, float, float],
    baseline_values: tuple[float, float, float],
) -> ModelComparisonOutcome:
    comparisons = tuple(
        _compare_metric(left, right)
        for left, right in zip(primary_values, baseline_values, strict=True)
    )
    if all(comparison == 0 for comparison in comparisons):
        return ModelComparisonOutcome.TIE
    if all(comparison >= 0 for comparison in comparisons):
        return ModelComparisonOutcome.WIN
    if all(comparison <= 0 for comparison in comparisons):
        return ModelComparisonOutcome.LOSE
    return ModelComparisonOutcome.INCONCLUSIVE


def _comparison(left: float, right: float) -> str:
    comparison = _compare_metric(left, right)
    if comparison > 0:
        return "win"
    if comparison < 0:
        return "lose"
    return "tie"


def _digest(document: object) -> str:
    return sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _require_nonblank(name: str, value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise BenchmarkUnavailable(f"{name} is unavailable")
    return value


def _require_judge_ratio(value: object) -> None:
    if type(value) not in {int, float}:
        raise BenchmarkUnavailable("retrieval judge is unavailable")
    number = float(cast(int | float, value))
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise BenchmarkUnavailable("retrieval judge is unavailable")


def validate_report_document(
    document: object,
    *,
    schema_path: Path,
) -> None:
    """Validate the closed v1 report without adding a production dependency."""

    try:
        schema = load_bounded_json(schema_path)
        validate_json_schema_document(document, schema)
        _validate_report(document, schema)
    except BenchmarkUnavailable:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
        MemoryError,
    ):
        raise BenchmarkUnavailable("benchmark report schema is unavailable") from None


def load_bounded_json(path: Path) -> object:
    """Parse a bounded JSON document and reject hostile numeric extensions."""

    try:
        path_metadata = path.lstat()
        if not stat.S_ISREG(path_metadata.st_mode):
            raise BenchmarkUnavailable("benchmark JSON is unavailable")
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > _MAX_JSON_BYTES
            ):
                raise BenchmarkUnavailable("benchmark JSON is unavailable")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                encoded = stream.read(_MAX_JSON_BYTES + 1)
            if len(encoded) > _MAX_JSON_BYTES:
                raise BenchmarkUnavailable("benchmark JSON is unavailable")
            raw = encoded.decode("utf-8")
        finally:
            os.close(descriptor)
        value = json.loads(
            raw,
            parse_int=_parse_json_integer,
            parse_float=_parse_json_float,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        _validate_bounded_json_value(value)
        return value
    except BenchmarkUnavailable:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        MemoryError,
        ValueError,
    ):
        raise BenchmarkUnavailable("benchmark JSON is unavailable") from None


def _parse_json_integer(raw: str) -> int:
    value = int(raw)
    if not _MIN_JSON_INTEGER <= value <= _MAX_JSON_INTEGER:
        raise BenchmarkUnavailable("benchmark JSON is unavailable")
    return value


def _parse_json_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or abs(value) > _MAX_ABS_JSON_FLOAT:
        raise BenchmarkUnavailable("benchmark JSON is unavailable")
    return value


def _reject_json_constant(_raw: str) -> None:
    raise BenchmarkUnavailable("benchmark JSON is unavailable")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise BenchmarkUnavailable("benchmark JSON is unavailable")
        document[key] = value
    return document


def _validate_bounded_json_value(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise BenchmarkUnavailable("benchmark JSON is unavailable")
        if type(item) is str:
            if len(item) > _MAX_JSON_STRING_LENGTH:
                raise BenchmarkUnavailable("benchmark JSON is unavailable")
        elif type(item) is int:
            if not _MIN_JSON_INTEGER <= item <= _MAX_JSON_INTEGER:
                raise BenchmarkUnavailable("benchmark JSON is unavailable")
        elif type(item) is float:
            if not math.isfinite(item) or abs(item) > _MAX_ABS_JSON_FLOAT:
                raise BenchmarkUnavailable("benchmark JSON is unavailable")
        elif type(item) is list:
            if len(item) > _MAX_JSON_CONTAINER_ITEMS:
                raise BenchmarkUnavailable("benchmark JSON is unavailable")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            if len(item) > _MAX_JSON_CONTAINER_ITEMS or any(
                type(key) is not str or len(key) > _MAX_JSON_STRING_LENGTH
                for key in item
            ):
                raise BenchmarkUnavailable("benchmark JSON is unavailable")
            pending.extend((child, depth + 1) for child in item.values())
        elif item is not None and type(item) is not bool:
            raise BenchmarkUnavailable("benchmark JSON is unavailable")


def _validate_report(document: object, schema: object) -> None:
    del schema
    report = _closed_object(
        document,
        frozenset(
            {
                "comparison",
                "models",
                "run",
                "schemaVersion",
                "standingTwinBaseline",
            }
        ),
    )
    if report["schemaVersion"] != REPORT_SCHEMA_VERSION:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    comparison = _closed_object(
        report["comparison"],
        frozenset(
            {
                "metricDeltas",
                "primaryAgainstModelBaseline",
                "primaryAgainstStandingTwinBaseline",
            }
        ),
    )
    deltas = _closed_object(
        comparison["metricDeltas"],
        frozenset(
            {"caseHit", "macroEvidenceRecall", "microEvidenceRecall"}
        ),
    )
    for value in deltas.values():
        _require_finite_number(value)
    run = _closed_object(
        report["run"], frozenset({"datasetDigest", "runIdentity", "topK"})
    )
    _require_digest(run["datasetDigest"])
    _require_digest(run["runIdentity"])
    if type(run["topK"]) is not int or run["topK"] < 1:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    standing = _closed_object(
        report["standingTwinBaseline"], frozenset({"caseHitValue"})
    )
    _require_ratio(standing["caseHitValue"])
    models = _closed_object(report["models"], frozenset({"baseline", "primary"}))
    primary_report = _validate_model_report(models["primary"])
    baseline_report = _validate_model_report(models["baseline"])
    if primary_report.population != baseline_report.population:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    primary_values = primary_report.values
    baseline_values = baseline_report.values
    try:
        declared_outcome = ModelComparisonOutcome(
            cast(str, comparison["primaryAgainstModelBaseline"])
        )
    except ValueError:
        raise BenchmarkUnavailable("benchmark report verdict is unavailable") from None
    expected_outcome = _pareto_outcome(primary_values, baseline_values)
    expected_deltas = {
        "caseHit": _metric_delta(primary_values[0], baseline_values[0]),
        "macroEvidenceRecall": _metric_delta(
            primary_values[1], baseline_values[1]
        ),
        "microEvidenceRecall": _metric_delta(
            primary_values[2], baseline_values[2]
        ),
    }
    expected_standing = _comparison(
        primary_values[0], cast(float, standing["caseHitValue"])
    )
    if (
        declared_outcome is not expected_outcome
        or deltas != expected_deltas
        or comparison["primaryAgainstStandingTwinBaseline"] != expected_standing
    ):
        raise BenchmarkUnavailable("benchmark report verdict is unavailable")


def validate_json_schema_document(value: object, schema: object) -> None:
    """Validate documents against the tracked schema vocabulary used by eval."""

    _validate_bounded_json_value(value)
    _validate_bounded_json_value(schema)
    root = _object(schema)
    _validate_json_schema(value, root, root)


def _validate_json_schema(
    value: object,
    schema: dict[str, object],
    root: dict[str, object],
) -> None:
    """Apply the small Draft 2020-12 vocabulary used by the tracked schema."""

    reference = schema.get("$ref")
    if reference is not None:
        if type(reference) is not str or not reference.startswith("#/"):
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        resolved: object = root
        for component in reference[2:].split("/"):
            resolved = _object(resolved).get(component)
        _validate_json_schema(value, _object(resolved), root)
        return

    if "const" in schema and value != schema["const"]:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    enum = schema.get("enum")
    if enum is not None and (type(enum) is not list or value not in enum):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")

    expected_type = schema.get("type")
    if expected_type == "object":
        document = _object(value)
        required = schema.get("required", [])
        if type(required) is not list or any(
            type(item) is not str for item in required
        ):
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        if not set(cast(list[str], required)).issubset(document):
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        minimum_properties = schema.get("minProperties", 0)
        if type(minimum_properties) is not int or len(document) < minimum_properties:
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        properties = _object(schema.get("properties", {}))
        additional = schema.get("additionalProperties", True)
        for key, item in document.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                _validate_json_schema(item, _object(child_schema), root)
            elif additional is False:
                raise BenchmarkUnavailable("benchmark report schema is unavailable")
            elif type(additional) is dict:
                _validate_json_schema(item, _object(additional), root)
            elif additional is not True:
                raise BenchmarkUnavailable("benchmark report schema is unavailable")
    elif expected_type == "string":
        if type(value) is not str:
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        minimum_length = schema.get("minLength", 0)
        pattern = schema.get("pattern")
        if type(minimum_length) is not int or len(value) < minimum_length:
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        if pattern is not None and (
            type(pattern) is not str or re.fullmatch(pattern, value) is None
        ):
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
    elif expected_type == "integer":
        if type(value) is not int:
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        _validate_numeric_bounds(value, schema)
    elif expected_type == "number":
        if type(value) not in {int, float}:
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        _validate_numeric_bounds(cast(int | float, value), schema)
    elif expected_type == "array":
        if type(value) is not list:
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        minimum_items = schema.get("minItems", 0)
        if type(minimum_items) is not int or len(value) < minimum_items:
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        if schema.get("uniqueItems") is True and len(
            {_canonical_json(item) for item in value}
        ) != len(value):
            raise BenchmarkUnavailable("benchmark report schema is unavailable")
        items = schema.get("items")
        if items is not None:
            item_schema = _object(items)
            for item in value:
                _validate_json_schema(item, item_schema, root)
    elif expected_type is not None:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")


def _validate_numeric_bounds(value: int | float, schema: dict[str, object]) -> None:
    if not math.isfinite(float(value)):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and (
        type(minimum) not in {int, float} or value < cast(int | float, minimum)
    ):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    if maximum is not None and (
        type(maximum) not in {int, float} or value > cast(int | float, maximum)
    ):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")


def _canonical_json(value: object) -> bytes:
    try:
        return rfc8785.dumps(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        raise BenchmarkUnavailable("benchmark report schema is unavailable") from None


def _validate_model_report(value: object) -> _ValidatedModelReport:
    report = _closed_object(value, frozenset({"identity", "metrics", "timing"}))
    identity = _closed_object(
        report["identity"],
        frozenset(
            {
                "artifactDigest",
                "batchSize",
                "dimension",
                "documentPrefix",
                "modelId",
                "pooling",
                "precision",
                "queryPrefix",
                "revision",
                "transformationPipeline",
            }
        ),
    )
    ModelIdentity(
        model_id=cast(str, identity["modelId"]),
        revision=cast(str, identity["revision"]),
        artifact_digest=cast(str, identity["artifactDigest"]),
        dimension=cast(int, identity["dimension"]),
        transformation_pipeline=ModelTransformationPipeline(
            cast(str, identity["transformationPipeline"])
        ),
        pooling=cast(str, identity["pooling"]),
        query_prefix=cast(str, identity["queryPrefix"]),
        document_prefix=cast(str, identity["documentPrefix"]),
        precision=cast(str, identity["precision"]),
        batch_size=cast(int, identity["batchSize"]),
    )
    metric_values, total_cases, total_expected, slice_counts = _validate_metrics(
        report["metrics"]
    )
    timing = _closed_object(
        report["timing"],
        frozenset(
            {"documentCount", "perDocumentEmbedMilliseconds", "wallClockMilliseconds"}
        ),
    )
    if type(timing["documentCount"]) is not int or timing["documentCount"] < 1:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    _require_nonnegative_number(timing["perDocumentEmbedMilliseconds"])
    _require_nonnegative_number(timing["wallClockMilliseconds"])
    return _ValidatedModelReport(
        values=metric_values,
        population=_ReportPopulation(
            total_cases=total_cases,
            total_expected=total_expected,
            slice_counts=slice_counts,
            document_count=timing["documentCount"],
        ),
    )


def _validate_metrics(
    value: object,
) -> tuple[
    tuple[float, float, float],
    int,
    int,
    tuple[tuple[str, int, int], ...],
]:
    metrics = _closed_object(
        value,
        frozenset({"caseHit", "evidenceRecall", "perSlice"}),
    )
    aggregate_case_hit = _validate_case_hit(metrics["caseHit"])
    aggregate_recall = _validate_recall(metrics["evidenceRecall"])
    per_slice = _object(metrics["perSlice"])
    if not per_slice:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    slice_case_hits: list[tuple[int, int, float]] = []
    slice_recalls: list[tuple[float, int, int, float]] = []
    slice_counts: list[tuple[str, int, int]] = []
    for slice_name, slice_metrics in sorted(per_slice.items()):
        item = _closed_object(
            slice_metrics,
            frozenset({"caseHit", "evidenceRecall"}),
        )
        case_hit = _validate_case_hit(item["caseHit"])
        recall = _validate_recall(item["evidenceRecall"])
        slice_case_hits.append(case_hit)
        slice_recalls.append(recall)
        slice_counts.append((slice_name, case_hit[1], recall[2]))
    if (
        aggregate_case_hit[0] != sum(item[0] for item in slice_case_hits)
        or aggregate_case_hit[1] != sum(item[1] for item in slice_case_hits)
        or aggregate_recall[1] != sum(item[1] for item in slice_recalls)
        or aggregate_recall[2] != sum(item[2] for item in slice_recalls)
    ):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    weighted_macro = sum(
        recall[0] * case_hit[1]
        for recall, case_hit in zip(slice_recalls, slice_case_hits, strict=True)
    ) / aggregate_case_hit[1]
    if not _same_metric(aggregate_recall[0], weighted_macro):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    return (
        (
            aggregate_case_hit[2],
            aggregate_recall[0],
            aggregate_recall[3],
        ),
        aggregate_case_hit[1],
        aggregate_recall[2],
        tuple(slice_counts),
    )


def _validate_case_hit(value: object) -> tuple[int, int, float]:
    metric = _closed_object(value, frozenset({"hits", "totalCases", "value"}))
    if (
        type(metric["hits"]) is not int
        or type(metric["totalCases"]) is not int
        or not 0 <= metric["hits"] <= metric["totalCases"]
        or metric["totalCases"] < 1
    ):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    _require_ratio(metric["value"])
    hits = metric["hits"]
    total = metric["totalCases"]
    reported = float(cast(int | float, metric["value"]))
    if not _same_metric(reported, hits / total):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    return hits, total, reported


def _validate_recall(value: object) -> tuple[float, int, int, float]:
    recall = _closed_object(value, frozenset({"macro", "micro"}))
    macro = _closed_object(recall["macro"], frozenset({"value"}))
    _require_ratio(macro["value"])
    micro = _closed_object(
        recall["micro"], frozenset({"hits", "totalExpected", "value"})
    )
    if (
        type(micro["hits"]) is not int
        or type(micro["totalExpected"]) is not int
        or not 0 <= micro["hits"] <= micro["totalExpected"]
        or micro["totalExpected"] < 1
    ):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    _require_ratio(micro["value"])
    macro_value = float(cast(int | float, macro["value"]))
    hits = micro["hits"]
    total = micro["totalExpected"]
    reported = float(cast(int | float, micro["value"]))
    if not _same_metric(reported, hits / total):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    return macro_value, hits, total, reported


def _same_metric(left: float, right: float) -> bool:
    return _compare_metric(left, right) == 0


def _compare_metric(left: float, right: float) -> int:
    if math.isclose(left, right, rel_tol=0.0, abs_tol=_METRIC_TOLERANCE):
        return 0
    return 1 if left > right else -1


def _metric_delta(left: float, right: float) -> float:
    return 0.0 if _same_metric(left, right) else left - right


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    return value


def _closed_object(value: object, fields: frozenset[str]) -> dict[str, object]:
    document = _object(value)
    if frozenset(document) != fields:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    return document


def _require_digest(value: object) -> None:
    if type(value) is not str or not _SHA256_DIGEST.fullmatch(value):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")


def _require_ratio(value: object) -> None:
    if type(value) not in {int, float}:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    number = float(cast(int | float, value))
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")


def _require_nonnegative_number(value: object) -> None:
    if type(value) not in {int, float}:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    number = float(cast(int | float, value))
    if not math.isfinite(number) or number < 0.0:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")


def _require_finite_number(value: object) -> None:
    if type(value) not in {int, float} or not math.isfinite(
        float(cast(int | float, value))
    ):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
