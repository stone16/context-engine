"""Stale-lineage detection for golden expectations after Release promotion.

Golden expectations bind to exact `source/resource/revision/fragment` refs. A
promoted Release publishes new immutable Revisions, so those refs can stop
resolving. Scoring such a case would report a retrieval miss and look like a
quality regression, so the affected cases are reported as stale lineage and
never enter a judge input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, cast

from engine.learning.golden import (
    EvidenceLineage,
    GoldenCase,
    GoldenSet,
    GoldenSetUnavailable,
)

LINEAGE_MAP_SCHEMA_VERSION: Final = "context-engine-golden-lineage-map-v1"
_MAP_FIELDS: Final = frozenset(
    {"capturedAt", "entries", "releaseRef", "schemaVersion"}
)
_LINEAGE_FIELDS: Final = frozenset(
    {"fragmentRef", "resourceRef", "revisionRef", "sourceRef"}
)


class LineageMapUnavailable(RuntimeError):
    """A malformed or unreadable lineage map is refused as a whole."""


class StaleGoldenLineage(RuntimeError):
    """Expected Evidence no longer resolves; scoring it would be a false miss."""


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _captured_at(value: object) -> str:
    if type(value) is not str:
        raise LineageMapUnavailable("lineage map capturedAt is unavailable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LineageMapUnavailable("lineage map capturedAt is unavailable") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise LineageMapUnavailable("lineage map capturedAt must be aware UTC")
    return value


def _release_ref(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise LineageMapUnavailable("lineage map releaseRef is unavailable")
    return value


@dataclass(frozen=True, slots=True)
class LineageMap:
    """Exactly the Evidence lineage that resolves in one promoted Release."""

    release_ref: str
    captured_at: str
    lineages: frozenset[EvidenceLineage] = field(repr=False)
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.lineages) is not frozenset or not self.lineages:
            raise LineageMapUnavailable("lineage map entries are unavailable")
        if any(
            type(lineage) is not EvidenceLineage for lineage in self.lineages
        ):
            raise TypeError("lineage map requires exact EvidenceLineage entries")
        object.__setattr__(
            self,
            "digest",
            _digest(
                {
                    "capturedAt": self.captured_at,
                    "entries": [
                        lineage.document() for lineage in sorted(self.lineages)
                    ],
                    "releaseRef": self.release_ref,
                    "schemaVersion": LINEAGE_MAP_SCHEMA_VERSION,
                }
            ),
        )


def _lineage(value: object) -> EvidenceLineage:
    if type(value) is not dict or frozenset(value) != _LINEAGE_FIELDS:
        raise LineageMapUnavailable("lineage map entry is malformed")
    document = cast(dict[str, object], value)
    try:
        return EvidenceLineage(
            source_ref=cast(str, document["sourceRef"]),
            resource_ref=cast(str, document["resourceRef"]),
            revision_ref=cast(str, document["revisionRef"]),
            fragment_ref=cast(str, document["fragmentRef"]),
        )
    except GoldenSetUnavailable:
        raise LineageMapUnavailable("lineage map entry is malformed") from None


def load_lineage_map(path: Path) -> LineageMap:
    """Load the whole map or refuse it; a partial map would fake resolution."""

    if not isinstance(path, Path):
        raise TypeError("lineage map path must be Path")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise LineageMapUnavailable("lineage map is unavailable") from None
    if type(raw) is not dict or frozenset(raw) != _MAP_FIELDS:
        raise LineageMapUnavailable("lineage map is malformed")
    document = cast(dict[str, object], raw)
    if document["schemaVersion"] != LINEAGE_MAP_SCHEMA_VERSION:
        raise LineageMapUnavailable("lineage map version is unavailable")
    entries = document["entries"]
    if type(entries) is not list or not entries:
        raise LineageMapUnavailable("lineage map entries are unavailable")
    lineages = tuple(_lineage(item) for item in cast(list[object], entries))
    if len(lineages) != len(set(lineages)):
        raise LineageMapUnavailable("lineage map entries must be unique")
    return LineageMap(
        release_ref=_release_ref(document["releaseRef"]),
        captured_at=_captured_at(document["capturedAt"]),
        lineages=frozenset(lineages),
    )


@dataclass(frozen=True, slots=True)
class CaseLineageResolution:
    """One case's resolution state; a stale case carries no retrieval score."""

    case_ref: str
    expected_count: int
    unresolved_count: int
    status: Literal["resolved", "stale_lineage"]


@dataclass(frozen=True, slots=True)
class LineageResolutionReport:
    """Content-free resolution facts bound to one set and one lineage map."""

    golden_digest: str
    release_ref: str
    captured_at: str
    map_digest: str
    total_cases: int
    resolved_case_count: int
    stale_cases: tuple[CaseLineageResolution, ...] = field(repr=False)
    status: Literal["resolved", "stale_lineage"]

    @property
    def stale_case_refs(self) -> tuple[str, ...]:
        return tuple(case.case_ref for case in self.stale_cases)


def detect_stale_lineage(
    golden_set: GoldenSet,
    lineage_map: LineageMap,
) -> LineageResolutionReport:
    """Classify every case as resolved or stale; never as a retrieval miss."""

    if type(golden_set) is not GoldenSet:
        raise TypeError("golden_set must be GoldenSet")
    if type(lineage_map) is not LineageMap:
        raise TypeError("lineage_map must be LineageMap")
    stale: list[CaseLineageResolution] = []
    for case in golden_set.cases:
        expected = tuple(value.lineage for value in case.expected_evidence)
        unresolved = tuple(
            lineage for lineage in expected if lineage not in lineage_map.lineages
        )
        if unresolved:
            stale.append(
                CaseLineageResolution(
                    case_ref=case.case_ref,
                    expected_count=len(expected),
                    unresolved_count=len(unresolved),
                    status="stale_lineage",
                )
            )
    ordered = tuple(sorted(stale, key=lambda value: value.case_ref))
    return LineageResolutionReport(
        golden_digest=golden_set.digest,
        release_ref=lineage_map.release_ref,
        captured_at=lineage_map.captured_at,
        map_digest=lineage_map.digest,
        total_cases=len(golden_set.cases),
        resolved_case_count=len(golden_set.cases) - len(ordered),
        stale_cases=ordered,
        status="stale_lineage" if ordered else "resolved",
    )


def require_resolved_lineage(report: LineageResolutionReport) -> None:
    """Refuse a stale set instead of letting a judge score its dangling refs."""

    if type(report) is not LineageResolutionReport:
        raise TypeError("report must be LineageResolutionReport")
    if report.status != "resolved":
        raise StaleGoldenLineage(
            "golden lineage is stale: cases="
            f"{len(report.stale_cases)} of {report.total_cases}"
        )


def scorable_cases(
    golden_set: GoldenSet,
    report: LineageResolutionReport,
) -> tuple[GoldenCase, ...]:
    """Return only cases whose expected lineage still resolves."""

    if type(golden_set) is not GoldenSet:
        raise TypeError("golden_set must be GoldenSet")
    if type(report) is not LineageResolutionReport:
        raise TypeError("report must be LineageResolutionReport")
    if report.golden_digest != golden_set.digest:
        raise StaleGoldenLineage("lineage resolution belongs to a different set")
    stale = frozenset(report.stale_case_refs)
    return tuple(case for case in golden_set.cases if case.case_ref not in stale)
