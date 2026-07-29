from __future__ import annotations

import copy
from pathlib import Path

import pytest

from engine.learning.golden import GoldenSetUnavailable, load_golden_set
from tests.support.golden import valid_composed_entries, write_golden


def _load(tmp_path: Path, entries: list[dict[str, object]]) -> None:
    path = tmp_path / "golden.json"
    write_golden(path, entries)
    load_golden_set(path, allow_unlocked_pilot_for_initial_lock=True)


def test_composition_accepts_each_counted_floor_at_its_boundary(
    tmp_path: Path,
) -> None:
    _load(tmp_path, valid_composed_entries())


def test_composition_rejects_fewer_than_twenty_dev_cases(tmp_path: Path) -> None:
    entries = valid_composed_entries()
    entries.pop(0)

    with pytest.raises(GoldenSetUnavailable, match="dev.*20"):
        _load(tmp_path, entries)


@pytest.mark.parametrize("pilot_count", (49, 51))
def test_composition_requires_exactly_fifty_pilot_cases(
    tmp_path: Path,
    pilot_count: int,
) -> None:
    entries = valid_composed_entries()
    if pilot_count == 49:
        entries.pop()
    else:
        extra = copy.deepcopy(entries[-1])
        extra["caseRef"] = "pilot-extra"
        extra["query"] = "synthetic-query-pilot-extra"
        entries.append(extra)

    with pytest.raises(GoldenSetUnavailable, match="pilot.*50"):
        _load(tmp_path, entries)


def test_composition_requires_ten_percent_unanswerable_pilot_cases(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    fifth_unanswerable = next(
        case
        for case in entries
        if case["partition"] == "pilot"
        and case["answerability"] == "unanswerable"
        and case["caseRef"] != "pilot-00"
    )
    fifth_unanswerable["answerability"] = "answerable"
    fifth_unanswerable["expectedEvidence"] = [
        {
            "path": "synthetic/replacement.md",
            "sourceRef": "synthetic-source-replacement",
            "resourceRef": "synthetic-resource-replacement",
            "revisionRef": "synthetic-revision-replacement",
            "fragmentRef": "synthetic-fragment-replacement",
        }
    ]
    fifth_unanswerable["requiredClaims"] = [
        {
            "claimRef": "claim-replacement",
            "claim": "synthetic-required-replacement",
            "expectedEvidence": [
                {
                    "sourceRef": "synthetic-source-replacement",
                    "resourceRef": "synthetic-resource-replacement",
                    "revisionRef": "synthetic-revision-replacement",
                    "fragmentRef": "synthetic-fragment-replacement",
                }
            ],
        }
    ]

    with pytest.raises(GoldenSetUnavailable, match="unanswerable.*5"):
        _load(tmp_path, entries)


def test_composition_requires_hard_negative_for_every_pilot_topic(
    tmp_path: Path,
) -> None:
    entries = valid_composed_entries()
    for case in entries:
        if (
            case["partition"] == "pilot"
            and case["topicCluster"] == "synthetic-topic-b"
        ):
            case["hardNegativeEvidence"] = []

    with pytest.raises(GoldenSetUnavailable, match="synthetic-topic-b"):
        _load(tmp_path, entries)
