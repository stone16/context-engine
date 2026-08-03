from __future__ import annotations

import json
import re
from pathlib import Path

REPORT_PATH = Path("docs/evaluation/2026-07-29-embedding-benchmark.json")
FORBIDDEN_KEYS = frozenset({"excerpt", "path", "query", "text", "title"})
PERSONAL_PATH = re.compile(
    r"(?:/" + "Users" + r"/|[A-Za-z]:\\|\.md(?:\b|$))"
)


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | {
            nested for item in value.values() for nested in _keys(item)
        }
    if isinstance(value, list):
        return {nested for item in value for nested in _keys(item)}
    return set()


def test_tracked_frozen_report_cannot_carry_personal_content() -> None:
    report_text = REPORT_PATH.read_text(encoding="utf-8")
    report = json.loads(report_text)

    assert not FORBIDDEN_KEYS.intersection(_keys(report))
    assert PERSONAL_PATH.search(report_text) is None
    assert report == {
        "report": {
            "datasetDigest": (
                "84ce04e760e57414e5dd7277815ef6972"
                "7d90e056d1e67c8bbbaa6a6c516bc87"
            ),
            "documentCount": 4166,
            "fullReportRetention": "ignored .context-engine root",
            "models": {
                "baseline": {
                    "caseHit": {
                        "hits": 13,
                        "totalCases": 30,
                        "value": 0.43333333333333335,
                    },
                    "evidenceRecall": {
                        "macroValue": 0.43333333333333335,
                        "microHits": 13,
                        "microTotalExpected": 30,
                        "microValue": 0.43333333333333335,
                    },
                    "modelId": "intfloat/multilingual-e5-small",
                    "timing": {
                        "perDocumentEmbedMilliseconds": 2.858616798600621,
                        "wallClockMilliseconds": 16778.844833839685,
                    },
                },
                "primary": {
                    "caseHit": {
                        "hits": 17,
                        "totalCases": 30,
                        "value": 0.5666666666666667,
                    },
                    "evidenceRecall": {
                        "macroValue": 0.5666666666666667,
                        "microHits": 17,
                        "microTotalExpected": 30,
                        "microValue": 0.5666666666666667,
                    },
                    "modelId": "Qwen/Qwen3-Embedding-0.6B",
                    "timing": {
                        "perDocumentEmbedMilliseconds": 36.18728767594692,
                        "wallClockMilliseconds": 155945.89870912023,
                    },
                },
            },
            "runIdentity": (
                "201cce2037447f3c29b8eea1da0ce1b63"
                "23f0f4311cae17fa24f3aa7c8b0dacb"
            ),
            "standingTwinBaseline": {
                "caseHitValue": 0.038,
                "reference": (
                    "https://github.com/stone16/context-engine/issues/128"
                ),
            },
            "topK": 10,
        },
        "schemaVersion": "context-engine-embedding-benchmark-frozen-result-v1",
        "verdict": {
            "activationAcceptanceReference": (
                "https://github.com/stone16/context-engine/issues/128"
                "#issuecomment-5161569127"
            ),
            "activationAcceptance": {
                "activeProfile": {
                    "evidenceHits": 7,
                    "evidenceRecall": 0.25,
                    "eligibleCases": 28,
                },
                "twinBaseline": {
                    "evidenceHits": 0,
                    "evidenceRecall": 0.0,
                    "eligibleCases": 28,
                },
            },
            "primaryAgainstModelBaseline": "win",
            "primaryAgainstStandingTwinBaseline": "win",
            "winner": "Qwen/Qwen3-Embedding-0.6B",
        },
    }
