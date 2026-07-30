from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator

from applications.worker_progress import (
    BATCH_PROGRESS_SCHEMA_VERSION,
    FileBatchProgressReporter,
    FileDispatchCycleResult,
    FileDispatchFailureCategory,
    opaque_file_job_ref,
    validate_worker_batch_progress_document,
)

ROOT = Path(__file__).parents[2]
SCHEMA_PATH = ROOT / "docs" / "contracts" / "worker-batch-progress-v1.schema.json"


def _documents() -> list[dict[str, object]]:
    rendered: list[str] = []
    reporter = FileBatchProgressReporter(
        rendered.append,
        batch_ref_factory=lambda: "a" * 64,
    )
    job_ref = opaque_file_job_ref(UUID("d8bd0f3d-b7c5-41b8-af7a-d4e2b0733dd0"))
    reporter.job_active(job_ref)
    reporter.observe_cycle(
        FileDispatchCycleResult(
            "refused",
            job_ref=job_ref,
            reason_category=FileDispatchFailureCategory.FILE_IMPORT_REFUSED,
        )
    )
    reporter.observe_cycle(FileDispatchCycleResult("no_work"))
    return [json.loads(value) for value in rendered]


def test_batch_progress_records_validate_against_the_tracked_closed_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    documents = _documents()

    assert schema["$id"].endswith("worker-batch-progress-v1.schema.json")
    assert schema["additionalProperties"] is False
    assert schema["x-context-engine-invariants"] == [
        "failed <= processed <= total",
        "phase == complete implies processed == total",
    ]
    assert schema["properties"]["reasonCategory"]["enum"] == [
        "file_import_refused",
        "worker_lease_refused",
        None,
    ]
    assert [document["phase"] for document in documents] == [
        "dispatching",
        "dispatching",
        "complete",
    ]
    for document in documents:
        validator.validate(document)
        validate_worker_batch_progress_document(document)
        assert document["schemaVersion"] == BATCH_PROGRESS_SCHEMA_VERSION
        assert set(document) == {
            "batchRef",
            "currentJobRef",
            "failed",
            "outcome",
            "phase",
            "processed",
            "reasonCategory",
            "schemaVersion",
            "total",
        }

    assert documents[-1] == {
        "batchRef": "a" * 64,
        "currentJobRef": None,
        "failed": 1,
        "outcome": None,
        "phase": "complete",
        "processed": 1,
        "reasonCategory": None,
        "schemaVersion": BATCH_PROGRESS_SCHEMA_VERSION,
        "total": 1,
    }


def test_progress_contract_has_no_content_path_title_excerpt_or_identity_field() -> (
    None
):
    forbidden_field_fragments = {
        "content",
        "credential",
        "excerpt",
        "identity",
        "organization",
        "path",
        "principal",
        "source",
        "title",
        "token",
        "user",
    }
    contract_fields = {field.name.lower() for field in fields(FileDispatchCycleResult)}
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    progress_fields = {name.lower() for name in schema["properties"]}

    for field_name in contract_fields | progress_fields:
        assert not any(fragment in field_name for fragment in forbidden_field_fragments)

    raw_job_id = UUID("d8bd0f3d-b7c5-41b8-af7a-d4e2b0733dd0")
    rendered = json.dumps(_documents(), sort_keys=True)
    adversarial_values = (
        "/Users/private/Vault/Plans/Acquisition.md",
        "Confidential acquisition plan",
        "excerpt: the private deal closes Friday",
        "principal:maintainer@example.invalid",
        str(raw_job_id),
    )
    assert all(value not in rendered for value in adversarial_values)
    assert len(opaque_file_job_ref(raw_job_id)) == 64


@pytest.mark.parametrize(
    "counter_updates",
    [
        {"failed": 2, "processed": 1, "total": 1},
        {"failed": 0, "processed": 2, "total": 1},
        {"failed": 0, "processed": 0, "total": 1},
    ],
)
def test_tracked_counter_invariants_reject_adversarial_documents(
    counter_updates: dict[str, int],
) -> None:
    document = _documents()[-1] | counter_updates

    with pytest.raises(ValueError, match="counter"):
        validate_worker_batch_progress_document(document)


def test_idle_polls_emit_no_chatter_after_one_batch_summary() -> None:
    rendered: list[str] = []
    reporter = FileBatchProgressReporter(
        rendered.append,
        batch_ref_factory=lambda: "b" * 64,
    )

    for _ in range(5):
        reporter.observe_cycle(FileDispatchCycleResult("no_work"))
    assert rendered == []

    job_ref = "c" * 64
    reporter.job_active(job_ref)
    reporter.observe_cycle(FileDispatchCycleResult("dispatched", job_ref=job_ref))
    reporter.observe_cycle(FileDispatchCycleResult("no_work"))
    summary_count = len(rendered)
    for _ in range(5):
        reporter.observe_cycle(FileDispatchCycleResult("no_work"))

    assert len(rendered) == summary_count
    assert json.loads(rendered[-1])["phase"] == "complete"
