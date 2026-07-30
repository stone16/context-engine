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
from engine.runtime.materialized import (
    CandidateDiscoveryRequest,
    CandidateDiscoverySession,
)
from engine.runtime.scope import CandidateDiscoveryScope

ROOT = Path(__file__).parents[2]
_CANDIDATE_RESULT_TYPES = frozenset(
    {
        "CandidateQuery",
        "CandidateRef",
        "FusedCandidates",
        "RankedCandidate",
        "RankedCandidateList",
    }
)


def _candidate_discovery_protocols(
    paths: tuple[Path, ...],
    *,
    relative_to: Path,
) -> list[tuple[Path, str]]:
    parsed = tuple((path, ast.parse(path.read_text())) for path in paths)
    classes = tuple(
        (path, node)
        for path, tree in parsed
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    )
    protocol_names = {"Protocol"}
    changed = True
    while changed:
        changed = False
        for _path, node in classes:
            if node.name in protocol_names or not any(
                ast.unparse(base).split(".")[-1] in protocol_names
                for base in node.bases
            ):
                continue
            protocol_names.add(node.name)
            changed = True

    candidate_protocol_names = {
        node.name
        for _path, node in classes
        if node.name in protocol_names
        and any(
            isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            and child.returns is not None
            and any(
                result_type in ast.unparse(child.returns)
                for result_type in _CANDIDATE_RESULT_TYPES
            )
            for child in node.body
        )
    }
    changed = True
    while changed:
        changed = False
        for _path, node in classes:
            if (
                node.name in candidate_protocol_names
                or node.name not in protocol_names
                or not any(
                    ast.unparse(base).split(".")[-1]
                    in candidate_protocol_names
                    for base in node.bases
                )
            ):
                continue
            candidate_protocol_names.add(node.name)
            changed = True

    return sorted(
        (
            (path.relative_to(relative_to), node.name)
            for path, node in classes
            if node.name in candidate_protocol_names
        ),
        key=lambda item: (str(item[0]), item[1]),
    )


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
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery:
        del request, discovery_session, effective_scope
        return self.result


def test_exactly_one_candidate_discovery_protocol_extends_existing_seam() -> None:
    paths = tuple(
        path
        for path in ROOT.rglob("*.py")
        if not any(
            part in {".context-engine", ".git", ".venv", "tests", "third_party"}
            for part in path.relative_to(ROOT).parts
        )
    )
    assert _candidate_discovery_protocols(paths, relative_to=ROOT) == [
        (Path("engine/runtime/content_io.py"), "CandidateIndex")
    ]
    assert get_type_hints(CandidateIndex.discover)["return"] is CandidateQuery
    assert (
        get_type_hints(CandidateIndex.prepare_discovery)["return"]
        == CandidateDiscoveryRequest
    )
    assert get_type_hints(PostgreSQLExactPhraseCandidateIndex.discover)[
        "return"
    ] is CandidateQuery


def test_candidate_protocol_oracle_detects_other_packages_and_inherited_seams(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "engine.py"
    application = tmp_path / "application.py"
    engine.write_text(
        "from typing import Protocol\n"
        "class CandidateIndex(Protocol):\n"
        "    def discover(self) -> CandidateQuery: ...\n"
    )
    application.write_text(
        "class AlternateLookup(CandidateIndex):\n"
        "    def search_candidates(self) -> CandidateRef: ...\n"
    )

    assert _candidate_discovery_protocols(
        (engine, application),
        relative_to=tmp_path,
    ) == [
        (Path("application.py"), "AlternateLookup"),
        (Path("engine.py"), "CandidateIndex"),
    ]
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
        cast(CandidateDiscoverySession, object()),
        effective_scope=cast(CandidateDiscoveryScope, object()),
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
