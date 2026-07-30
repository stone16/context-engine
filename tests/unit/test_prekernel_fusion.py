from __future__ import annotations

import ast
from pathlib import Path
from uuid import UUID

from engine.runtime.candidate_ranking import (
    CandidateQuery,
    RankedCandidate,
    RankedCandidateList,
)
from engine.runtime.evidence import CandidateRef
from engine.runtime.prekernel_fusion import fuse_candidate_evidence

ROOT = Path(__file__).parents[2]


def _candidate(label: str) -> CandidateRef:
    return CandidateRef(
        organization_id=UUID("81e18bca-86a1-478a-937d-7675c6fe69b0"),
        source_ref="source:fusion",
        resource_ref=f"resource:{label}",
        revision_ref="05b82c43-4e8f-49ae-a286-a40289a3413e",
        fragment_ref=f"fragment:{label}",
    )


def test_weighted_fusion_deduplicates_by_exact_candidate_ref_only() -> None:
    shared = _candidate("shared")
    lexical_only = _candidate("lexical")
    vector_only = _candidate("vector")
    query = CandidateQuery(
        ranked_lists=(
            RankedCandidateList(
                ranker_ref="lexical",
                candidates=(
                    RankedCandidate(shared, 0.9),
                    RankedCandidate(lexical_only, 0.8),
                ),
            ),
            RankedCandidateList(
                ranker_ref="vector",
                candidates=(
                    RankedCandidate(vector_only, 1.0),
                    RankedCandidate(shared, 0.7),
                ),
            ),
        )
    )

    fused = fuse_candidate_evidence(query)

    assert fused.candidate_refs == (shared, vector_only, lexical_only)
    assert tuple(item.candidate_ref for item in fused.rank_evidence) == (
        shared,
        vector_only,
        lexical_only,
    )
    assert fused.rank_evidence[0].fused_rank == 1
    assert tuple(item.ranker_ref for item in fused.rank_evidence[0].per_ranker) == (
        "lexical",
        "vector",
    )


def test_fusion_module_has_only_the_exact_content_free_import() -> None:
    path = ROOT / "engine/runtime/prekernel_fusion.py"
    tree = ast.parse(path.read_text())
    imports = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_names = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert imports == {
        "engine.runtime.candidate_ranking",
        "engine.runtime.evidence",
    }
    assert imported_names == {
        "CandidateQuery",
        "CandidateRankEvidence",
        "FusedCandidates",
        "RankerEvidence",
        "CandidateRef",
        "_candidate_sort_key",
    }
    assert not {
        "AuthorizedProjection",
        "Evidence",
        "PackageBlock",
        "PackageContent",
        "MaterializedFragmentProjection",
    }.intersection(imported_names)


def test_runtime_activates_content_free_fusion_without_pre_kernel_weights() -> None:
    path = ROOT / "engine/runtime/construction.py"
    tree = ast.parse(path.read_text())
    imported_names = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "fuse_candidate_evidence" in imported_names
    assert "weighted_fuse_candidates" not in imported_names
