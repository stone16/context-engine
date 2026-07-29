"""Offline-only embedding benchmark contracts and report validation."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Final, Protocol, cast

EMBEDDING_DIMENSION: Final = 384
REPORT_SCHEMA_VERSION: Final = "context-engine-embedding-benchmark-report-v1"
_PINNED_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_NORMALIZATIONS: Final = frozenset({"l2", "none"})
_REDUCTIONS: Final = frozenset({"none_native_384", "matryoshka_truncate_384"})


class BenchmarkUnavailable(RuntimeError):
    """The benchmark input, identity, provider, judge, or output is unavailable."""


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """Complete, reproducible identity for one offline embedding provider."""

    model_id: str
    revision: str
    artifact_digest: str
    dimension: int
    normalization: str
    pooling: str
    query_prefix: str
    document_prefix: str
    reduction: str
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
            or self.normalization not in _NORMALIZATIONS
            or type(self.pooling) is not str
            or not self.pooling
            or self.pooling != self.pooling.strip()
            or type(self.query_prefix) is not str
            or type(self.document_prefix) is not str
            or self.reduction not in _REDUCTIONS
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
            "normalization": self.normalization,
            "pooling": self.pooling,
            "precision": self.precision,
            "queryPrefix": self.query_prefix,
            "reduction": self.reduction,
            "revision": self.revision,
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
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            type(self.documents) is not tuple
            or not self.documents
            or any(type(value) is not BenchmarkDocument for value in self.documents)
            or type(self.cases) is not tuple
            or not self.cases
            or any(type(value) is not BenchmarkCase for value in self.cases)
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
    if primary.identity.reduction != "matryoshka_truncate_384":
        raise BenchmarkUnavailable("primary benchmark reduction is unavailable")
    if baseline.identity.model_id != "intfloat/multilingual-e5-small":
        raise BenchmarkUnavailable("baseline benchmark model is unavailable")
    if baseline.identity.reduction != "none_native_384":
        raise BenchmarkUnavailable("baseline benchmark reduction is unavailable")
    primary_report = _run_model(dataset, primary, judge, top_k, clock)
    baseline_report = _run_model(dataset, baseline, judge, top_k, clock)
    primary_hit = _metric_case_hit(primary_report)
    baseline_hit = _metric_case_hit(baseline_report)
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
            "primaryAgainstModelBaseline": _comparison(primary_hit, baseline_hit),
            "primaryAgainstStandingTwinBaseline": _comparison(primary_hit, 0.038),
        },
        "models": {"baseline": baseline_report, "primary": primary_report},
        "run": {**run_document, "runIdentity": run_identity},
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "standingTwinBaseline": {"caseHitValue": 0.038},
    }


def _run_model(
    dataset: BenchmarkDataset,
    provider: BenchmarkEmbeddingProvider,
    judge: RetrievalJudge,
    top_k: int,
    clock: Callable[[], float],
) -> dict[str, object]:
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
    return {
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
    }


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


def _metric_case_hit(model_report: dict[str, object]) -> float:
    metrics = _object(model_report["metrics"])
    case_hit = _object(metrics["caseHit"])
    value = case_hit["value"]
    if type(value) not in {int, float}:
        raise BenchmarkUnavailable("retrieval judge is unavailable")
    return float(cast(int | float, value))


def _comparison(left: float, right: float) -> str:
    if left > right:
        return "win"
    if left < right:
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
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        _validate_json_schema(document, _object(schema), _object(schema))
        _validate_report(document, schema)
    except BenchmarkUnavailable:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        raise BenchmarkUnavailable("benchmark report schema is unavailable") from None


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
    _closed_object(
        report["comparison"],
        frozenset(
            {"primaryAgainstModelBaseline", "primaryAgainstStandingTwinBaseline"}
        ),
    )
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
    _validate_model_report(models["primary"])
    _validate_model_report(models["baseline"])


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
    elif expected_type is not None:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")


def _validate_numeric_bounds(value: int | float, schema: dict[str, object]) -> None:
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


def _validate_model_report(value: object) -> None:
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
                "normalization",
                "pooling",
                "precision",
                "queryPrefix",
                "reduction",
                "revision",
            }
        ),
    )
    ModelIdentity(
        model_id=cast(str, identity["modelId"]),
        revision=cast(str, identity["revision"]),
        artifact_digest=cast(str, identity["artifactDigest"]),
        dimension=cast(int, identity["dimension"]),
        normalization=cast(str, identity["normalization"]),
        pooling=cast(str, identity["pooling"]),
        query_prefix=cast(str, identity["queryPrefix"]),
        document_prefix=cast(str, identity["documentPrefix"]),
        reduction=cast(str, identity["reduction"]),
        precision=cast(str, identity["precision"]),
        batch_size=cast(int, identity["batchSize"]),
    )
    _validate_metrics(report["metrics"])
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


def _validate_metrics(value: object) -> None:
    metrics = _closed_object(
        value,
        frozenset({"caseHit", "evidenceRecall", "perSlice"}),
    )
    _validate_case_hit(metrics["caseHit"])
    _validate_recall(metrics["evidenceRecall"])
    per_slice = _object(metrics["perSlice"])
    if not per_slice:
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    for slice_metrics in per_slice.values():
        item = _closed_object(
            slice_metrics,
            frozenset({"caseHit", "evidenceRecall"}),
        )
        _validate_case_hit(item["caseHit"])
        _validate_recall(item["evidenceRecall"])


def _validate_case_hit(value: object) -> None:
    metric = _closed_object(value, frozenset({"hits", "totalCases", "value"}))
    if (
        type(metric["hits"]) is not int
        or type(metric["totalCases"]) is not int
        or not 0 <= metric["hits"] <= metric["totalCases"]
        or metric["totalCases"] < 1
    ):
        raise BenchmarkUnavailable("benchmark report schema is unavailable")
    _require_ratio(metric["value"])


def _validate_recall(value: object) -> None:
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
