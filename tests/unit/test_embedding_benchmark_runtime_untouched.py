from __future__ import annotations

import subprocess
from pathlib import Path

from engine.supply.embeddings import CONTEXT_FRAGMENT_EMBEDDING_DIMENSION

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_offline_benchmark_does_not_modify_or_compose_production_runtime() -> None:
    changed_runtime = subprocess.run(
        ("git", "diff", "--name-only", "origin/main", "--", "engine/runtime"),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert changed_runtime == ""
    assert CONTEXT_FRAGMENT_EMBEDDING_DIMENSION == 384
    assert not any(
        "eval.embedding_benchmark" in path.read_text(encoding="utf-8")
        for path in (REPOSITORY_ROOT / "engine" / "runtime").glob("*.py")
    )


def test_benchmark_imports_the_one_production_dimension_constant() -> None:
    benchmark_source = (REPOSITORY_ROOT / "eval" / "embedding_benchmark.py").read_text(
        encoding="utf-8"
    )

    assert (
        "from engine.supply.embeddings import CONTEXT_FRAGMENT_EMBEDDING_DIMENSION"
        in benchmark_source
    )
    assert "EMBEDDING_DIMENSION: Final = 384" not in benchmark_source
