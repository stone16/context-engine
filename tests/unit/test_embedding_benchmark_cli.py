from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from applications import embedding_benchmark as cli
from eval.embedding_benchmark import BenchmarkUnavailable
from tests.unit.test_embedding_benchmark_metrics import (
    RecordingJudge,
    SyntheticProvider,
    _identity,
)


def _write_dataset(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "caseRef": "case-alpha",
                        "expectedDocumentRefs": ["doc-alpha"],
                        "query": "alpha",
                        "slice": "single_doc",
                    }
                ],
                "documents": [
                    {"documentRef": "doc-alpha", "text": "alpha"},
                    {"documentRef": "doc-beta", "text": "beta"},
                ],
                "schemaVersion": "context-engine-embedding-benchmark-input-v1",
            }
        ),
        encoding="utf-8",
    )


def test_cli_run_writes_a_schema_valid_report_for_a_synthetic_set(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    output_path = Path(".context-engine") / "test" / "embedding-benchmark.json"
    _write_dataset(dataset_path)

    def provider(role: str, _model_dir: Path) -> SyntheticProvider:
        model_id = (
            "Qwen/Qwen3-Embedding-0.6B"
            if role == "primary"
            else "intfloat/multilingual-e5-small"
        )
        return SyntheticProvider(_identity(model_id))

    monkeypatch.setattr(cli, "build_local_provider", provider)
    monkeypatch.setattr(cli, "load_retrieval_judge", lambda _spec: RecordingJudge())

    result = cli.main(
        [
            "run",
            "--dataset",
            str(dataset_path),
            "--primary-model-dir",
            str(tmp_path / "primary"),
            "--baseline-model-dir",
            str(tmp_path / "baseline"),
            "--judge",
            "eval.retrieval_judge:create_judge",
            "--output",
            str(output_path),
            "--top-k",
            "1",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert report["models"]["primary"]["identity"]["modelId"] == (
        "Qwen/Qwen3-Embedding-0.6B"
    )
    assert report["comparison"]["primaryAgainstModelBaseline"] == "tie"
    output_path.unlink()


def test_cli_refuses_report_output_outside_the_ignored_state_directory(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    _write_dataset(dataset_path)

    result = cli.main(
        [
            "run",
            "--dataset",
            str(dataset_path),
            "--primary-model-dir",
            str(tmp_path / "primary"),
            "--baseline-model-dir",
            str(tmp_path / "baseline"),
            "--judge",
            "eval.retrieval_judge:create_judge",
            "--output",
            str(Path("tests") / "tracked.json"),
        ]
    )

    assert result == 2
    assert not (Path("tests") / "tracked.json").exists()


def test_cli_refuses_a_missing_retrieval_judge() -> None:
    with pytest.raises(BenchmarkUnavailable, match="judge is unavailable"):
        cli.load_retrieval_judge("eval.retrieval_judge:create_judge")


def test_output_cannot_escape_the_workspace_state_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(BenchmarkUnavailable, match="must be under"):
        cli._require_ignored_output(
            workspace / ".context-engine" / ".." / "tracked.json",
            workspace_root=workspace,
        )


def test_local_plugin_provider_is_loaded_only_from_the_requested_model_directory(
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    plugin = model_dir / "context_engine_provider.py"
    plugin.write_text(
        "def create_provider(model_dir):\n"
        "    return {'requested': str(model_dir)}\n",
        encoding="utf-8",
    )

    provider = cli.build_local_provider("primary", model_dir)

    assert cast(Any, provider) == {"requested": str(model_dir)}
