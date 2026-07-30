from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from applications.dogfood_evaluation import (
    DogfoodSecretExclusionUnavailable,
)
from applications.local_context import (
    LOCAL_CANDIDATE_FILENAME,
    DurableGoldenCandidateRecorder,
    LocalContextConsumer,
)
from engine.learning.golden_storage import GOLDEN_ROOT_ENV
from engine.runtime.package_digest import context_package_digest

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
SECRET = "poisoned-dogfood-secret-at-least-32-bytes"
EVIDENCE_REF = "ev_" + "a" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPOSITORY_ROOT / ".claude/skills/context-engine/SKILL.md"


def _package_document(*, expires_at: str = "2026-07-30T12:05:00Z") -> dict[str, object]:
    package: dict[str, object] = {
        "packageId": "pkg_" + "b" * 32,
        "purpose": "context.answer",
        "audienceDigest": "c" * 64,
        "policyEpoch": 7,
        "policySnapshotRef": "policy-snapshot-current",
        "decisionRef": "dec_" + "d" * 32,
        "runRef": "run-local-context",
        "releaseManifestRef": "release-current",
        "retentionPolicyRef": "package-digest-only-v1",
        "asOf": "2026-07-30T12:00:00Z",
        "expiresAt": expires_at,
        "ttlSeconds": 300,
        "tokenizerRef": "utf8-byte-v1",
        "packageSchemaRef": "context-package-openapi-v0",
        "blocks": [
            {
                "blockId": "block_" + "a" * 64,
                "text": "Authorized note excerpt",
                "evidenceRefs": [EVIDENCE_REF],
            }
        ],
        "evidence": [
            {
                "evidenceRef": EVIDENCE_REF,
                "sourceRef": "source:file:maintainer",
                "resourceRef": "resource:file:authorized",
                "revisionRef": "revision:current",
                "fragmentRef": "fragment:authorized",
                "projectedFields": ["body"],
                "runRef": "run-local-context",
                "purpose": "context.answer",
                "authorizationAsOf": "2026-07-30T12:00:00Z",
                "decisionRef": "dec_" + "d" * 32,
                "policySnapshotRef": "policy-snapshot-current",
                "policyEpoch": 7,
                "sourceAclEvidence": {
                    "kind": "mirrored",
                    "projectionRef": "source-acl-current",
                    "aclAsOf": "2026-07-30T12:00:00Z",
                    "freshnessProfileRef": "file-source-current-v1",
                },
                "citationOpenRef": "citation-display-only",
            }
        ],
        "gaps": [],
        "coverage": {"status": "sufficient"},
        "budgetUsage": {
            "tokens": len(b"Authorized note excerpt"),
            "providerCalls": 0,
            "costMicrounits": 0,
            "elapsedMs": 0,
        },
        "continuation": None,
    }
    package["packageDigest"] = context_package_digest(package)
    return package


def _resolved(*, expires_at: str = "2026-07-30T12:05:00Z") -> dict[str, object]:
    return {
        "kind": "resolved",
        "package": _package_document(expires_at=expires_at),
        "egressGrant": None,
    }


class RecordingCaller:
    def __init__(self, outcome: dict[str, object]) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    def resolve_acquire(
        self,
        *,
        query: str,
        request_id: str,
    ) -> dict[str, object]:
        self.calls.append((query, request_id))
        return self.outcome

    def reject_secret_material(self, value: object) -> None:
        if SECRET in repr(value):
            raise DogfoodSecretExclusionUnavailable(
                "dogfood secret exclusion is unavailable"
            )


class RecordingCandidateRecorder:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, datetime]] = []

    def record(
        self,
        *,
        question: str,
        disposition: str,
        captured_at: datetime,
    ) -> None:
        self.records.append((question, disposition, captured_at))


def _consumer(
    outcome: dict[str, object],
) -> tuple[LocalContextConsumer, RecordingCaller, RecordingCandidateRecorder]:
    caller = RecordingCaller(outcome)
    recorder = RecordingCandidateRecorder()
    return (
        LocalContextConsumer(
            caller=caller,
            candidate_recorder=recorder,
            now=lambda: NOW,
            request_id_factory=lambda: "claude-skill-fresh-request",
        ),
        caller,
        recorder,
    )


def test_each_consumer_issues_one_fresh_acquire_and_rejects_package_reuse() -> None:
    consumer, caller, recorder = _consumer(_resolved())

    first = consumer.consume("What decision governs local consumers?")
    second = consumer.consume("What is the next maintainer question?")

    assert caller.calls == [
        (
            "What decision governs local consumers?",
            "claude-skill-fresh-request",
        )
    ]
    assert len(recorder.records) == 1
    assert "Authorized note excerpt" in first
    assert "LOCAL_CONTEXT_REFUSAL_FRESH_RESOLVE_REQUIRED" in second


def test_separate_questions_generate_distinct_fresh_request_ids() -> None:
    request_ids = iter(("claude-skill-request-one", "claude-skill-request-two"))
    caller = RecordingCaller(_resolved())
    recorder = RecordingCandidateRecorder()

    first = LocalContextConsumer(
        caller=caller,
        candidate_recorder=recorder,
        now=lambda: NOW,
        request_id_factory=lambda: next(request_ids),
    )
    second = LocalContextConsumer(
        caller=caller,
        candidate_recorder=recorder,
        now=lambda: NOW,
        request_id_factory=lambda: next(request_ids),
    )

    first.consume("What is the first question?")
    second.consume("What is the second question?")

    assert [request_id for _, request_id in caller.calls] == [
        "claude-skill-request-one",
        "claude-skill-request-two",
    ]


def test_expired_package_is_discarded_with_a_distinguishable_refusal() -> None:
    consumer, _, recorder = _consumer(
        _resolved(expires_at="2026-07-30T12:00:00Z")
    )

    rendered = consumer.consume("Which context is current?")

    assert "Authorized note excerpt" not in rendered
    assert "LOCAL_CONTEXT_REFUSAL_EXPIRED_PACKAGE" in rendered
    assert recorder.records[0][1] == "LOCAL_CONTEXT_REFUSAL_EXPIRED_PACKAGE"


def test_expiry_is_checked_after_the_resolve_returns() -> None:
    events: list[str] = []

    class OrderedCaller(RecordingCaller):
        def resolve_acquire(
            self,
            *,
            query: str,
            request_id: str,
        ) -> dict[str, object]:
            events.append("resolve_returned")
            return super().resolve_acquire(query=query, request_id=request_id)

    caller = OrderedCaller(_resolved(expires_at="2026-07-30T12:00:00Z"))

    def current_time() -> datetime:
        events.append("expiry_checked")
        return NOW

    consumer = LocalContextConsumer(
        caller=caller,
        candidate_recorder=RecordingCandidateRecorder(),
        now=current_time,
        request_id_factory=lambda: "claude-skill-boundary-request",
    )

    rendered = consumer.consume("Does the Package expire during transport?")

    assert "LOCAL_CONTEXT_REFUSAL_EXPIRED_PACKAGE" in rendered
    assert events == ["resolve_returned", "expiry_checked"]


@pytest.mark.parametrize(
    "mutate",
    (
        lambda package: cast(list[dict[str, object]], package["blocks"])[0].update(
            {"evidenceRefs": []}
        ),
        lambda package: cast(list[dict[str, object]], package["blocks"])[0].update(
            {"evidenceRefs": [EVIDENCE_REF, "ev_" + "f" * 64]}
        ),
        lambda package: cast(list[dict[str, object]], package["blocks"])[0].update(
            {"evidenceRefs": ["ev_" + "f" * 64]}
        ),
    ),
)
def test_missing_extra_or_unknown_block_evidence_ref_rejects_package(
    mutate: object,
) -> None:
    outcome = _resolved()
    package = cast(dict[str, object], outcome["package"])
    mutate(package)  # type: ignore[operator]
    consumer, _, _ = _consumer(outcome)

    rendered = consumer.consume("Show exact citation lineage")

    assert "Authorized note excerpt" not in rendered
    assert "LOCAL_CONTEXT_REFUSAL_MALFORMED_PACKAGE" in rendered


def test_every_block_is_rendered_with_its_exact_evidence_and_expiry() -> None:
    consumer, _, _ = _consumer(_resolved())

    rendered = consumer.consume("Show exact citation lineage")

    assert "expiresAt: 2026-07-30T12:05:00Z" in rendered
    assert f"evidenceRef: {EVIDENCE_REF}" in rendered
    assert "text: Authorized note excerpt" in rendered
    assert "resourceRef: resource:file:authorized" in rendered
    assert "citationOpenRef: citation-display-only (display-only)" in rendered


def test_request_not_available_and_empty_authorized_set_are_distinguishable() -> None:
    unavailable, _, _ = _consumer(
        {"kind": "request_not_available", "retryable": False}
    )
    empty = _resolved()
    package = cast(dict[str, object], empty["package"])
    package["blocks"] = []
    package["evidence"] = []
    package["coverage"] = {
        "status": "empty",
        "reason": "no_authorized_evidence",
    }
    package["budgetUsage"] = {
        "tokens": 0,
        "providerCalls": 0,
        "costMicrounits": 0,
        "elapsedMs": 0,
    }
    package["packageDigest"] = context_package_digest(
        {key: value for key, value in package.items() if key != "packageDigest"}
    )
    empty_consumer, _, _ = _consumer(empty)

    request_refusal = unavailable.consume("Can this request be served?")
    empty_refusal = empty_consumer.consume("What is authorized?")

    assert "LOCAL_CONTEXT_REFUSAL_REQUEST_NOT_AVAILABLE" in request_refusal
    assert "LOCAL_CONTEXT_REFUSAL_EMPTY_AUTHORIZED_SET" in empty_refusal
    assert "corpus has nothing" not in (request_refusal + empty_refusal).casefold()


def test_poisoned_secret_never_reaches_rendered_output_or_candidate_capture() -> None:
    outcome = copy.deepcopy(_resolved())
    package = cast(dict[str, object], outcome["package"])
    block = cast(list[dict[str, object]], package["blocks"])[0]
    block["text"] = f"poisoned response {SECRET}"
    consumer, _, recorder = _consumer(outcome)

    rendered = consumer.consume("Do not expose configured credentials")

    assert SECRET not in rendered
    assert "LOCAL_CONTEXT_REFUSAL_SECRET_EXCLUSION" in rendered
    assert all(SECRET not in repr(record) for record in recorder.records)


def test_candidate_capture_is_private_question_only_and_path_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(tmp_path))
    recorder = DurableGoldenCandidateRecorder(
        candidate_ref_factory=lambda: "candidate_fixed"
    )

    recorder.record(
        question="What maintainer context should be evaluated?",
        disposition="CONTEXT_RENDERED",
        captured_at=NOW,
    )

    path = tmp_path / LOCAL_CANDIDATE_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == {
        "candidateRef": "candidate_fixed",
        "capturedAt": "2026-07-30T12:00:00Z",
        "question": "What maintainer context should be evaluated?",
        "resolveDisposition": "CONTEXT_RENDERED",
        "schemaVersion": "context-engine-golden-candidate-v1",
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert not any("path" in key.casefold() for key in document)


def test_skill_prescribes_stdin_only_invocation_without_bearer_material() -> None:
    skill = SKILL_PATH.read_text(encoding="utf-8")
    code_blocks = skill.split("```bash\n")[1:]

    assert len(code_blocks) == 1
    invocation = code_blocks[0].split("```", maxsplit=1)[0].strip()
    assert invocation == (
        "printf '%s\\n' 'CURRENT_QUESTION' | "
        "uv run context-engine-local-context"
    )
    for forbidden in (
        "CONTEXT_ENGINE_DOGFOOD_SECRET",
        "Authorization",
        "Bearer ",
        "$",
        "--request-id",
        "/v0/resolve",
    ):
        assert forbidden not in invocation
