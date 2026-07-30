from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from engine.learning.golden import create_golden_lock, load_golden_set
from tests.support.golden import valid_composed_entries

SYNTHETIC_RELEASE_REF = "synthetic-release-v1"
SYNTHETIC_CAPTURED_AT = "2026-07-29T00:00:00Z"


@dataclass(frozen=True, slots=True)
class StagedCorpus:
    """A synthetic corpus staged exactly as the durable root holds it."""

    source_root: Path
    golden_path: Path
    lock_path: Path
    lineage_map_path: Path
    entries: list[dict[str, object]]


def marked_entries(marker: str) -> list[dict[str, object]]:
    """Composed entries whose every value carries one recognizable marker."""

    document = json.dumps(valid_composed_entries())
    return cast(
        list[dict[str, object]],
        json.loads(document.replace("synthetic", marker)),
    )


def golden_document(entries: list[dict[str, object]], marker: str) -> dict[str, object]:
    return {
        "schemaVersion": "context-engine-golden-set-v1",
        "name": f"{marker}-golden-v1",
        "synthetic": True,
        "entries": entries,
    }


def expected_lineages(entries: list[dict[str, object]]) -> list[dict[str, str]]:
    """Every expected Evidence lineage the entries currently bind to."""

    lineages: list[dict[str, str]] = []
    for entry in entries:
        for evidence in cast(list[dict[str, str]], entry["expectedEvidence"]):
            lineages.append(
                {
                    key: value
                    for key, value in evidence.items()
                    if key != "path"
                }
            )
    return lineages


def lineage_document(
    lineages: list[dict[str, str]],
    *,
    release_ref: str = SYNTHETIC_RELEASE_REF,
    captured_at: str = SYNTHETIC_CAPTURED_AT,
) -> dict[str, object]:
    return {
        "schemaVersion": "context-engine-golden-lineage-map-v1",
        "releaseRef": release_ref,
        "capturedAt": captured_at,
        "entries": lineages,
    }


def write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def stage_corpus(
    source_root: Path,
    *,
    marker: str = "synthetic",
    entries: list[dict[str, object]] | None = None,
) -> StagedCorpus:
    """Write a locked synthetic set plus its lineage map into a durable root."""

    source_root.mkdir(parents=True, exist_ok=True)
    staged = entries if entries is not None else marked_entries(marker)
    golden_path = source_root / "golden-v1.json"
    lock_path = source_root / "golden-v1.lock.json"
    lineage_map_path = source_root / "lineage-map.json"
    write_json(golden_path, golden_document(staged, marker))
    create_golden_lock(
        load_golden_set(golden_path, allow_unlocked_pilot_for_initial_lock=True),
        lock_path,
        authority="maintainer",
        reason=f"{marker}-staged-lock",
        recorded_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    write_json(lineage_map_path, lineage_document(expected_lineages(staged)))
    return StagedCorpus(
        source_root=source_root,
        golden_path=golden_path,
        lock_path=lock_path,
        lineage_map_path=lineage_map_path,
        entries=staged,
    )
