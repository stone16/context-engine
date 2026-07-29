from __future__ import annotations

from typing import Any, cast

from eval.embedding_benchmark import run_benchmark
from tests.unit.test_embedding_benchmark_metrics import (
    RecordingJudge,
    SyntheticProvider,
    _dataset,
    _identity,
)


def test_same_dataset_and_model_identity_produce_identical_retrieval_metrics() -> None:
    primary = SyntheticProvider(_identity("Qwen/Qwen3-Embedding-0.6B"))
    baseline = SyntheticProvider(_identity("intfloat/multilingual-e5-small"))

    first = run_benchmark(
        dataset=_dataset(),
        primary=primary,
        baseline=baseline,
        judge=RecordingJudge(),
        top_k=2,
        clock=iter((1.0, 1.001, 1.002, 1.003, 2.0, 2.001, 2.002, 2.003)).__next__,
    )
    second = run_benchmark(
        dataset=_dataset(),
        primary=primary,
        baseline=baseline,
        judge=RecordingJudge(),
        top_k=2,
        clock=iter((5.0, 5.5, 6.0, 6.75, 8.0, 8.5, 9.0, 9.75)).__next__,
    )

    first_models = cast(dict[str, Any], first["models"])
    second_models = cast(dict[str, Any], second["models"])
    assert first_models["primary"]["metrics"] == second_models["primary"]["metrics"]
    assert first_models["baseline"]["metrics"] == second_models["baseline"][
        "metrics"
    ]
    assert first["run"] == second["run"]
    assert first_models["primary"]["timing"] != second_models["primary"]["timing"]
