from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_fusion_is_content_free_and_does_not_own_server_weights() -> None:
    path = ROOT / "engine/runtime/prekernel_fusion.py"
    tree = ast.parse(path.read_text())
    imports = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    names = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert imports == {
        "engine.runtime.candidate_ranking",
        "engine.runtime.evidence",
    }
    assert not {
        "AuthorizedProjection",
        "Evidence",
        "MaterializedFragmentProjection",
        "PackageContent",
    }.intersection(names)
    assert "ranker_weights" not in path.read_text()
