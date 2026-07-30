from __future__ import annotations

import json
from pathlib import Path

from scripts.daily_driver.jobs import SCHEDULED_OPERATION_CATEGORIES

ROOT = Path(__file__).resolve().parents[2]
DEFINITION = ROOT / "deploy" / "daily-driver" / "scheduled-jobs.json"
TEMPLATES = ROOT / "deploy" / "daily-driver"
FORBIDDEN_TOKENS = {"promote", "promotion", "activate", "activation", "rollback"}


def test_scheduled_jobs_use_only_the_closed_non_publication_allowlist() -> None:
    document = json.loads(DEFINITION.read_text(encoding="utf-8"))

    assert document["allowedOperations"] == sorted(SCHEDULED_OPERATION_CATEGORIES)
    assert document["jobs"]
    assert {job["operation"] for job in document["jobs"]} <= (
        SCHEDULED_OPERATION_CATEGORIES
    )
    assert all(job["publicationAuthority"] == "NONE" for job in document["jobs"])


def test_scheduled_job_definition_contains_no_publication_operation() -> None:
    scheduled_artifacts = [
        DEFINITION,
        ROOT / "scripts" / "daily_driver" / "jobs.py",
        *sorted(TEMPLATES.glob("*.plist.template")),
    ]
    for artifact in scheduled_artifacts:
        content = artifact.read_text(encoding="utf-8").lower()
        assert all(token not in content for token in FORBIDDEN_TOKENS), artifact
