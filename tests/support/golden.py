from __future__ import annotations

import copy
import json
from pathlib import Path


def evidence(label: str) -> dict[str, str]:
    return {
        "path": f"synthetic/{label}.md",
        "sourceRef": f"synthetic-source-{label}",
        "resourceRef": f"synthetic-resource-{label}",
        "revisionRef": f"synthetic-revision-{label}",
        "fragmentRef": f"synthetic-fragment-{label}",
    }


def golden_case(
    case_ref: str,
    *,
    partition: str = "dev",
    answerability: str = "answerable",
    topic_cluster: str = "synthetic-topic-a",
    hard_negative: bool = True,
) -> dict[str, object]:
    expected_evidence = [] if answerability == "unanswerable" else [evidence(case_ref)]
    required_claims = []
    if answerability == "answerable":
        expected = evidence(case_ref)
        required_claims = [
            {
                "claimRef": f"claim-{case_ref}",
                "claim": f"synthetic-required-claim-{case_ref}",
                "expectedEvidence": [
                    {key: value for key, value in expected.items() if key != "path"}
                ],
            }
        ]
    return {
        "caseRef": case_ref,
        "query": f"synthetic-query-{case_ref}",
        "expectedEvidence": expected_evidence,
        "expectedAnswer": f"synthetic-expected-answer-{case_ref}",
        "requiredClaims": required_claims,
        "answerability": answerability,
        "slice": "single_doc",
        "partition": partition,
        "topicCluster": topic_cluster,
        "hardNegativeEvidence": [evidence(f"hard-negative-{case_ref}")]
        if hard_negative
        else [],
    }


def golden_document(entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schemaVersion": "context-engine-golden-set-v1",
        "name": "synthetic-golden-v1",
        "synthetic": True,
        "entries": entries,
    }


def valid_composed_entries() -> list[dict[str, object]]:
    dev = [golden_case(f"dev-{index:02d}") for index in range(20)]
    pilot = [
        golden_case(
            f"pilot-{index:02d}",
            partition="pilot",
            answerability="unanswerable" if index < 5 else "answerable",
            topic_cluster=(
                "synthetic-topic-a" if index < 25 else "synthetic-topic-b"
            ),
            hard_negative=index in {0, 25},
        )
        for index in range(50)
    ]
    return dev + pilot


def write_golden(
    path: Path,
    entries: list[dict[str, object]],
) -> None:
    path.write_text(
        json.dumps(golden_document(copy.deepcopy(entries)), ensure_ascii=False),
        encoding="utf-8",
    )
