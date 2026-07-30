from __future__ import annotations

import json
from pathlib import Path

import pytest

from applications import file_scan_measurement as measurement

REPORT_PATH = Path("docs/evaluation/2026-07-30-file-scan-measurement.json")


def test_tracked_measurement_is_aggregate_synthetic_and_covers_both_options() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    assert report["schemaVersion"] == measurement.SCHEMA_VERSION
    assert report["method"] == {
        "curatedOptionUsesConfiguredTraversal": True,
        "generatedTree": True,
        "pageLimit": 1,
        "productionProviderSeam": True,
        "singletonCycleEstimate": (
            "initial call plus pageCount minus one times the measured signed "
            "continuation call"
        ),
    }
    rows = report["measurements"]
    assert [row["pathCount"] for row in rows] == list(measurement.MEASUREMENT_SIZES)
    for row in rows:
        assert set(row) == {
            "continuationPeakMemoryBytes",
            "continuationWallClockSeconds",
            "estimatedSingletonCycleSeconds",
            "initialPeakMemoryBytes",
            "initialWallClockSeconds",
            "measurementRef",
            "pageCount",
            "pathCount",
            "peakMemoryBytes",
        }
        assert row["measurementRef"] == f"synthetic-{row['pathCount']}"
        assert row["pageCount"] == row["pathCount"]
        assert row["peakMemoryBytes"] > 0
        assert row["initialWallClockSeconds"] > 0
        assert row["continuationWallClockSeconds"] > 0
    options = report["options"]
    assert options["configurableWholeVault"]["representativePathCount"] == 15_000
    assert options["curatedSubtree"]["representativePathCount"] == 5_000
    assert options["curatedSubtree"]["measurementRef"] == "synthetic-curated-5000"

    serialized = REPORT_PATH.read_text(encoding="utf-8")
    assert "/Users/" not in serialized
    assert "Obsidian" not in serialized
    assert ".md" not in serialized


@pytest.mark.parametrize("path_count", [1, 15_001, True])
def test_measurement_refuses_sizes_outside_its_synthetic_contract(
    path_count: int,
) -> None:
    with pytest.raises(
        measurement.MeasurementUnavailable,
        match="synthetic path count is unavailable",
    ):
        measurement.measure_size(path_count)


@pytest.mark.parametrize("curated_subtree", [False, True])
def test_measurement_reexecutes_production_traversal_without_personal_data(
    curated_subtree: bool,
) -> None:
    row = measurement.measure_size(10, curated_subtree=curated_subtree)

    assert row["pathCount"] == 10
    assert row["pageCount"] == 10
    assert type(row["peakMemoryBytes"]) is int
    assert row["peakMemoryBytes"] > 0
    assert row["measurementRef"] == (
        "synthetic-curated-10" if curated_subtree else "synthetic-10"
    )
    serialized = json.dumps(row)
    assert "/Users/" not in serialized
    assert "Obsidian" not in serialized
    assert ".md" not in serialized
