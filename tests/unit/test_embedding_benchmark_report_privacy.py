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
        "models": {
            "baseline": "pending_corpus",
            "primary": "pending_corpus",
        },
        "result": "pending_corpus",
        "schemaVersion": "context-engine-embedding-benchmark-frozen-result-v1",
    }
