"""Strict golden-set v1 loading, composition validation, and pilot locking."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

GOLDEN_SET_SCHEMA_VERSION: Final = "context-engine-golden-set-v1"
GOLDEN_LOCK_SCHEMA_VERSION: Final = "context-engine-golden-lock-v1"
DEFAULT_GOLDEN_SCHEMA_PATH: Final = (
    Path(__file__).resolve().parents[2] / "eval/golden/v1/schema.json"
)
MINIMUM_DEV_CASES: Final = 20
LOCKED_PILOT_CASES: Final = 50
MINIMUM_UNANSWERABLE_PILOT_CASES: Final = 5


class GoldenSetUnavailable(RuntimeError):
    """A malformed, composition-invalid, or unexpectedly edited set is refused."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _exact_text(field_name: str, value: object, *, maximum: int = 512) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or len(value) > maximum
    ):
        raise GoldenSetUnavailable(f"{field_name} is unavailable")
    return value


def _opaque_ref(field_name: str, value: object) -> str:
    result = _exact_text(field_name, value)
    if any(character.isspace() for character in result):
        raise GoldenSetUnavailable(f"{field_name} is unavailable")
    return result


def _relative_path(value: object) -> str:
    path = _exact_text("golden expected path", value, maximum=1_024)
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or str(parsed) != path
        or path.startswith("./")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise GoldenSetUnavailable("golden expected path is unavailable")
    return path


@dataclass(frozen=True, slots=True, order=True)
class EvidenceLineage:
    """Content-free exact expected Evidence identity."""

    source_ref: str
    resource_ref: str
    revision_ref: str
    fragment_ref: str

    def __post_init__(self) -> None:
        _opaque_ref("Evidence source_ref", self.source_ref)
        _opaque_ref("Evidence resource_ref", self.resource_ref)
        _opaque_ref("Evidence revision_ref", self.revision_ref)
        _opaque_ref("Evidence fragment_ref", self.fragment_ref)

    def document(self) -> dict[str, str]:
        return {
            "fragmentRef": self.fragment_ref,
            "resourceRef": self.resource_ref,
            "revisionRef": self.revision_ref,
            "sourceRef": self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class EvidenceExpectation:
    """Human-review locator plus exact content-free lineage."""

    path: str
    lineage: EvidenceLineage

    def __post_init__(self) -> None:
        _relative_path(self.path)
        if type(self.lineage) is not EvidenceLineage:
            raise TypeError("expected Evidence requires exact lineage")

    def document(self) -> dict[str, str]:
        return {"path": self.path, **self.lineage.document()}


@dataclass(frozen=True, slots=True)
class RequiredClaim:
    """One expected answer claim bound to its exact supporting lineage."""

    claim_ref: str
    claim: str = field(repr=False)
    expected_evidence: tuple[EvidenceLineage, ...]

    def document(self) -> dict[str, object]:
        return {
            "claim": self.claim,
            "claimRef": self.claim_ref,
            "expectedEvidence": [
                lineage.document() for lineage in self.expected_evidence
            ],
        }


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One strict v1 evaluation case."""

    case_ref: str
    query: str = field(repr=False)
    expected_evidence: tuple[EvidenceExpectation, ...] = field(repr=False)
    expected_answer: str = field(repr=False)
    required_claims: tuple[RequiredClaim, ...] = field(repr=False)
    answerability: Literal["answerable", "unanswerable"]
    slice_name: Literal["single_doc", "cross_doc", "temporal"]
    partition: Literal["dev", "pilot"]
    topic_cluster: str
    hard_negative_evidence: tuple[EvidenceExpectation, ...] = field(repr=False)

    def document(self) -> dict[str, object]:
        return {
            "answerability": self.answerability,
            "caseRef": self.case_ref,
            "expectedAnswer": self.expected_answer,
            "expectedEvidence": [value.document() for value in self.expected_evidence],
            "hardNegativeEvidence": [
                value.document() for value in self.hard_negative_evidence
            ],
            "partition": self.partition,
            "query": self.query,
            "requiredClaims": [claim.document() for claim in self.required_claims],
            "slice": self.slice_name,
            "topicCluster": self.topic_cluster,
        }


@dataclass(frozen=True, slots=True)
class GoldenSet:
    """Loaded v1 data with stable complete-set and locked-pilot digests."""

    name: str
    synthetic: bool
    cases: tuple[GoldenCase, ...] = field(repr=False)
    digest: str = field(init=False)
    pilot_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _exact_text("golden set name", self.name)
        if type(self.synthetic) is not bool:
            raise TypeError("golden set synthetic marker must be bool")
        if type(self.cases) is not tuple or not self.cases:
            raise GoldenSetUnavailable("golden entries are unavailable")
        refs = tuple(case.case_ref for case in self.cases)
        if len(refs) != len(set(refs)):
            raise GoldenSetUnavailable("golden caseRef values must be unique")
        document = self.document()
        pilot = [
            case.document() for case in self.cases if case.partition == "pilot"
        ]
        object.__setattr__(self, "digest", _digest(document))
        object.__setattr__(self, "pilot_digest", _digest(pilot))

    def document(self) -> dict[str, object]:
        return {
            "entries": [case.document() for case in self.cases],
            "name": self.name,
            "schemaVersion": GOLDEN_SET_SCHEMA_VERSION,
            "synthetic": self.synthetic,
        }


def _schema_validator(schema_path: Path) -> Draft202012Validator:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeDecodeError, ValueError, SchemaError):
        raise GoldenSetUnavailable("golden schema is unavailable") from None
    return Draft202012Validator(schema)


def _validate_schema(document: object, schema_path: Path) -> Mapping[str, object]:
    errors = sorted(
        _schema_validator(schema_path).iter_errors(document),
        key=lambda error: (
            tuple(str(value) for value in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        error = errors[0]
        path = ".".join(str(value) for value in error.absolute_path) or "root"
        raise GoldenSetUnavailable(f"golden schema {path}: {error.message}")
    if type(document) is not dict:
        raise GoldenSetUnavailable("golden set is unavailable")
    return cast(Mapping[str, object], document)


def validate_golden_document_schema(
    document: object,
    *,
    schema_path: Path = DEFAULT_GOLDEN_SCHEMA_PATH,
) -> None:
    """Validate one document's closed v1 shape without loading private content."""

    if not isinstance(schema_path, Path):
        raise TypeError("golden schema path must be Path")
    _validate_schema(document, schema_path)


def _expectation(value: object) -> EvidenceExpectation:
    if type(value) is not dict:
        raise GoldenSetUnavailable("golden expected Evidence is unavailable")
    document = cast(dict[str, object], value)
    return EvidenceExpectation(
        path=_relative_path(document["path"]),
        lineage=EvidenceLineage(
            source_ref=_opaque_ref("Evidence sourceRef", document["sourceRef"]),
            resource_ref=_opaque_ref("Evidence resourceRef", document["resourceRef"]),
            revision_ref=_opaque_ref("Evidence revisionRef", document["revisionRef"]),
            fragment_ref=_opaque_ref("Evidence fragmentRef", document["fragmentRef"]),
        ),
    )


def _lineage(value: object) -> EvidenceLineage:
    if type(value) is not dict:
        raise GoldenSetUnavailable("golden claim Evidence is unavailable")
    document = cast(dict[str, object], value)
    return EvidenceLineage(
        source_ref=_opaque_ref("Evidence sourceRef", document["sourceRef"]),
        resource_ref=_opaque_ref("Evidence resourceRef", document["resourceRef"]),
        revision_ref=_opaque_ref("Evidence revisionRef", document["revisionRef"]),
        fragment_ref=_opaque_ref("Evidence fragmentRef", document["fragmentRef"]),
    )


def _required_claim(value: object) -> RequiredClaim:
    if type(value) is not dict:
        raise GoldenSetUnavailable("golden required claim is unavailable")
    document = cast(dict[str, object], value)
    expected_values = cast(list[object], document["expectedEvidence"])
    expected = tuple(_lineage(item) for item in expected_values)
    if len(expected) != len(set(expected)):
        raise GoldenSetUnavailable("golden claim Evidence must be unique")
    return RequiredClaim(
        claim_ref=_exact_text("golden claimRef", document["claimRef"], maximum=128),
        claim=_exact_text("golden claim", document["claim"], maximum=4_096),
        expected_evidence=expected,
    )


def _case(value: object) -> GoldenCase:
    if type(value) is not dict:
        raise GoldenSetUnavailable("golden case is unavailable")
    document = cast(dict[str, object], value)
    expected_values = cast(list[object], document["expectedEvidence"])
    hard_negative_values = cast(list[object], document["hardNegativeEvidence"])
    claims = cast(list[object], document["requiredClaims"])
    expected = tuple(_expectation(item) for item in expected_values)
    hard_negatives = tuple(_expectation(item) for item in hard_negative_values)
    if len({value.lineage for value in expected}) != len(expected):
        raise GoldenSetUnavailable("golden expected Evidence lineage must be unique")
    if len({value.lineage for value in hard_negatives}) != len(hard_negatives):
        raise GoldenSetUnavailable("golden hard-negative lineage must be unique")
    required_claims = tuple(_required_claim(claim) for claim in claims)
    claim_refs = tuple(claim.claim_ref for claim in required_claims)
    if len(claim_refs) != len(set(claim_refs)):
        raise GoldenSetUnavailable("golden required claimRef values must be unique")
    expected_lineage = {value.lineage for value in expected}
    if any(
        not set(claim.expected_evidence) <= expected_lineage
        for claim in required_claims
    ):
        raise GoldenSetUnavailable(
            "golden required claim Evidence must belong to expectedEvidence"
        )
    if expected_lineage & {value.lineage for value in hard_negatives}:
        raise GoldenSetUnavailable(
            "golden hard-negative Evidence must not be expected Evidence"
        )
    return GoldenCase(
        case_ref=_exact_text("golden caseRef", document["caseRef"], maximum=128),
        query=_exact_text("golden query", document["query"], maximum=4_096),
        expected_evidence=expected,
        expected_answer=_exact_text(
            "golden expectedAnswer", document["expectedAnswer"], maximum=16_384
        ),
        required_claims=required_claims,
        answerability=cast(
            Literal["answerable", "unanswerable"], document["answerability"]
        ),
        slice_name=cast(
            Literal["single_doc", "cross_doc", "temporal"], document["slice"]
        ),
        partition=cast(Literal["dev", "pilot"], document["partition"]),
        topic_cluster=_opaque_ref("golden topicCluster", document["topicCluster"]),
        hard_negative_evidence=hard_negatives,
    )


def validate_composition(golden_set: GoldenSet) -> None:
    """Enforce every counted dev/pilot composition floor as a hard failure."""

    if type(golden_set) is not GoldenSet:
        raise TypeError("golden_set must be GoldenSet")
    partitions = Counter(case.partition for case in golden_set.cases)
    dev_count = partitions["dev"]
    pilot_count = partitions["pilot"]
    if dev_count < MINIMUM_DEV_CASES:
        raise GoldenSetUnavailable(
            f"golden dev count {dev_count} is below required 20"
        )
    if pilot_count != LOCKED_PILOT_CASES:
        raise GoldenSetUnavailable(
            f"golden pilot count {pilot_count} must equal 50"
        )
    pilot = tuple(case for case in golden_set.cases if case.partition == "pilot")
    unanswerable = sum(case.answerability == "unanswerable" for case in pilot)
    if unanswerable < MINIMUM_UNANSWERABLE_PILOT_CASES:
        raise GoldenSetUnavailable(
            f"golden unanswerable pilot count {unanswerable} is below required 5"
        )
    topic_counts = Counter(case.topic_cluster for case in pilot)
    hard_negative_counts = Counter(
        case.topic_cluster for case in pilot if case.hard_negative_evidence
    )
    missing = tuple(
        topic for topic in sorted(topic_counts) if hard_negative_counts[topic] == 0
    )
    if missing:
        raise GoldenSetUnavailable(
            "golden pilot topic clusters lack same-topic hard negatives: "
            + ", ".join(missing)
        )


def load_golden_set(
    path: Path,
    *,
    schema_path: Path = DEFAULT_GOLDEN_SCHEMA_PATH,
    lock_path: Path | None = None,
    validate_set_composition: bool = True,
    allow_unlocked_pilot_for_initial_lock: bool = False,
) -> GoldenSet:
    """Load all cases or refuse the entire run; no malformed case is dropped."""

    if not isinstance(path, Path) or not isinstance(schema_path, Path):
        raise TypeError("golden paths must be Path")
    try:
        raw_document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise GoldenSetUnavailable("golden set is unavailable") from None
    document = _validate_schema(raw_document, schema_path)
    entries = cast(list[object], document["entries"])
    golden_set = GoldenSet(
        name=_exact_text("golden set name", document["name"]),
        synthetic=cast(bool, document["synthetic"]),
        cases=tuple(_case(value) for value in entries),
    )
    if validate_set_composition:
        validate_composition(golden_set)
    if lock_path is not None:
        _verify_lock(golden_set, lock_path)
    elif (
        any(case.partition == "pilot" for case in golden_set.cases)
        and not allow_unlocked_pilot_for_initial_lock
    ):
        raise GoldenSetUnavailable(
            "golden pilot requires a lock; only initial lock or explicit re-lock "
            "may load it unlocked"
        )
    return golden_set


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("golden lock time must be aware UTC")
    rendered = value.astimezone(UTC).isoformat(timespec="microseconds")
    return rendered.replace("+00:00", "Z")


def _lock_entry(
    golden_set: GoldenSet,
    *,
    authority: str,
    reason: str,
    recorded_at: datetime,
) -> dict[str, str]:
    return {
        "authority": _opaque_ref("golden lock authority", authority),
        "digest": golden_set.pilot_digest,
        "reason": _exact_text("golden lock reason", reason, maximum=1_024),
        "recordedAt": _timestamp(recorded_at),
    }


def _write_lock(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def create_golden_lock(
    golden_set: GoldenSet,
    path: Path,
    *,
    authority: str,
    reason: str,
    recorded_at: datetime,
) -> None:
    """Create the initial immutable pilot digest record."""

    if path.exists():
        raise GoldenSetUnavailable("golden lock already exists")
    entry = _lock_entry(
        golden_set,
        authority=authority,
        reason=reason,
        recorded_at=recorded_at,
    )
    _write_lock(
        path,
        {
            "activePilotDigest": golden_set.pilot_digest,
            "goldenSetName": golden_set.name,
            "history": [entry],
            "schemaVersion": GOLDEN_LOCK_SCHEMA_VERSION,
        },
    )


def _load_lock(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise GoldenSetUnavailable("golden lock is unavailable") from None
    if type(document) is not dict or frozenset(document) != frozenset(
        {"activePilotDigest", "goldenSetName", "history", "schemaVersion"}
    ):
        raise GoldenSetUnavailable("golden lock is malformed")
    if document["schemaVersion"] != GOLDEN_LOCK_SCHEMA_VERSION:
        raise GoldenSetUnavailable("golden lock version is unavailable")
    history = document["history"]
    if type(history) is not list or not history:
        raise GoldenSetUnavailable("golden lock history is unavailable")
    if any(
        type(item) is not dict
        or frozenset(item)
        != frozenset({"authority", "digest", "reason", "recordedAt"})
        for item in history
    ):
        raise GoldenSetUnavailable("golden lock history is malformed")
    last_entry = cast(dict[str, object], history[-1])
    if document["activePilotDigest"] != last_entry["digest"]:
        raise GoldenSetUnavailable(
            "golden lock active digest must match its latest history entry"
        )
    return cast(dict[str, object], document)


def _verify_lock(golden_set: GoldenSet, path: Path) -> None:
    document = _load_lock(path)
    if document["goldenSetName"] != golden_set.name:
        raise GoldenSetUnavailable("golden lock set identity is unavailable")
    if document["activePilotDigest"] != golden_set.pilot_digest:
        raise GoldenSetUnavailable(
            "locked pilot digest changed without an explicit recorded re-lock"
        )


def relock_golden_set(
    golden_path: Path,
    lock_path: Path,
    *,
    authority: str,
    reason: str,
    recorded_at: datetime,
) -> None:
    """Explicitly append a new pilot digest while retaining all prior locks."""

    golden_set = load_golden_set(
        golden_path,
        allow_unlocked_pilot_for_initial_lock=True,
    )
    document = _load_lock(lock_path)
    if document["goldenSetName"] != golden_set.name:
        raise GoldenSetUnavailable("golden lock set identity is unavailable")
    history = cast(list[object], document["history"])
    history.append(
        _lock_entry(
            golden_set,
            authority=authority,
            reason=reason,
            recorded_at=recorded_at,
        )
    )
    document["activePilotDigest"] = golden_set.pilot_digest
    _write_lock(lock_path, document)
