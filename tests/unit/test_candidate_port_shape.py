from __future__ import annotations

import ast
import pickle
from pathlib import Path
from typing import cast, get_type_hints
from uuid import UUID

import pytest

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.pgvector import PostgreSQLVectorCandidateIndex
from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.content_io import CandidateIndex
from engine.runtime.contracts import Acquire, ContextNeed
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import MaterializedProjectionSession
from engine.runtime.scope import EffectiveScope

ROOT = Path(__file__).parents[2]


def _candidate(label: str) -> CandidateRef:
    return CandidateRef(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        source_ref="source:ranked",
        resource_ref=f"resource:{label}",
        revision_ref="05b82c43-4e8f-49ae-a286-a40289a3413e",
        fragment_ref=f"fragment:{label}",
    )


class _FakeRanker:
    def __init__(self, result: CandidateQuery) -> None:
        self.result = result

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
        *,
        effective_scope: EffectiveScope,
    ) -> CandidateQuery:
        del request, projection_session, effective_scope
        return self.result


def test_exactly_one_candidate_discovery_protocol_extends_existing_seam() -> None:
    protocols: list[tuple[Path, str]] = []
    paths = (
        *ROOT.joinpath("engine").rglob("*.py"),
        *ROOT.joinpath("adapters").rglob("*.py"),
    )
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {
                base.id
                for base in node.bases
                if isinstance(base, ast.Name)
            }
            methods = {
                child.name
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            if "Protocol" in base_names and "discover" in methods:
                protocols.append((path.relative_to(ROOT), node.name))

    assert protocols == [(Path("engine/runtime/content_io.py"), "CandidateIndex")]
    assert get_type_hints(CandidateIndex.discover)["return"] is CandidateQuery
    assert get_type_hints(PostgreSQLExactPhraseCandidateIndex.discover)[
        "return"
    ] is CandidateQuery
    assert get_type_hints(PostgreSQLVectorCandidateIndex.discover)[
        "return"
    ] is CandidateQuery


def test_candidate_query_preserves_ranker_identity_and_opaque_candidate_order() -> None:
    first = _candidate("first")
    second = _candidate("second")
    query = CandidateQuery(
        ranked_lists=(
            RankedCandidateList(
                ranker_ref="lexical",
                candidates=(
                    RankedCandidate(candidate_ref=first, score=0.9),
                    RankedCandidate(candidate_ref=second, score=0.4),
                ),
            ),
            RankedCandidateList(
                ranker_ref="vector",
                candidates=(RankedCandidate(candidate_ref=second, score=0.8),),
            ),
        )
    )
    ranker = cast(CandidateIndex, _FakeRanker(query))

    discovered = ranker.discover(
        Acquire(need=ContextNeed(query="ranked query")),
        cast(MaterializedProjectionSession, object()),
        effective_scope=cast(EffectiveScope, object()),
    )

    assert tuple(item.ranker_ref for item in discovered.ranked_lists) == (
        "lexical",
        "vector",
    )
    assert tuple(
        item.candidate_ref
        for ranked_list in discovered.ranked_lists
        for item in ranked_list.candidates
    ) == (first, second, second)
    assert set(CandidateRef.__dataclass_fields__) == {
        "organization_id",
        "source_ref",
        "resource_ref",
        "revision_ref",
        "fragment_ref",
    }
    assert "body" not in repr(discovered)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(first)


def test_candidate_query_refuses_merged_or_duplicate_ranker_lists() -> None:
    with pytest.raises(ValueError, match="at least one named ranked list"):
        CandidateQuery(ranked_lists=())
    repeated = RankedCandidateList(
        ranker_ref="lexical",
        candidates=(RankedCandidate(candidate_ref=_candidate("one"), score=1.0),),
    )
    with pytest.raises(ValueError, match="ranker identity must be unique"):
        CandidateQuery(ranked_lists=(repeated, repeated))
