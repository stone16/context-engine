from __future__ import annotations

import json
from dataclasses import asdict

from engine.learning.judges import (
    CitationCaseInput,
    CitationClaim,
    RetrievalCaseInput,
    judge_citations,
    judge_retrieval,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def test_retrieval_and_citation_judges_are_byte_identical_across_runs() -> None:
    retrieval_input = (
        RetrievalCaseInput(
            "synthetic-b",
            frozenset({"b2", "b1"}),
            frozenset({"b1"}),
        ),
        RetrievalCaseInput("synthetic-a", frozenset({"a"}), frozenset({"a"})),
    )
    citation_input = (
        CitationCaseInput(
            "synthetic-b",
            frozenset({"claim-b"}),
            (CitationClaim("claim-b", frozenset({"evidence-b"})),),
            (("claim-b", frozenset({"evidence-b"})),),
            frozenset({"evidence-b"}),
        ),
        CitationCaseInput(
            "synthetic-a",
            frozenset({"claim-a"}),
            (CitationClaim("claim-a", frozenset({"evidence-a"})),),
            (("claim-a", frozenset({"evidence-a"})),),
            frozenset({"evidence-a"}),
        ),
    )

    first = _canonical(
        {
            "citation": asdict(judge_citations(citation_input)),
            "retrieval": asdict(judge_retrieval(retrieval_input)),
        }
    )
    second = _canonical(
        {
            "citation": asdict(judge_citations(citation_input)),
            "retrieval": asdict(judge_retrieval(retrieval_input)),
        }
    )

    assert first == second
