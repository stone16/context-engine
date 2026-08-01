from __future__ import annotations

import importlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from applications import embedding_benchmark as cli
from eval.embedding_benchmark import (
    BenchmarkUnavailable,
    CaseHitMetric,
    DatasetLockProfile,
    EvidenceRecallMetric,
    MacroRecallMetric,
    MicroRecallMetric,
    RetrievalJudgeCase,
    RetrievalMetrics,
    SliceMetrics,
)
from tests.unit.test_embedding_benchmark_metrics import (
    RecordingJudge,
    SyntheticProvider,
    _identity,
)


class BaselineWinningJudge:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate_retrieval(
        self, cases: tuple[RetrievalJudgeCase, ...]
    ) -> RetrievalMetrics:
        self.calls += 1
        value = 0.0 if self.calls == 1 else 1.0
        return RetrievalMetrics(
            case_hit=CaseHitMetric(hits=int(value), total_cases=1, value=value),
            evidence_recall=EvidenceRecallMetric(
                macro=MacroRecallMetric(value=value),
                micro=MicroRecallMetric(
                    hits=int(value), total_expected=1, value=value
                ),
            ),
            per_slice={
                "single_doc": SliceMetrics(
                    case_hit=CaseHitMetric(
                        hits=int(value), total_cases=1, value=value
                    ),
                    evidence_recall=EvidenceRecallMetric(
                        macro=MacroRecallMetric(value=value),
                        micro=MicroRecallMetric(
                            hits=int(value), total_expected=1, value=value
                        ),
                    ),
                )
            },
        )


def _write_dataset(path: Path) -> None:
    document = {
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
    content_digest = cli.dataset_content_digest(document)
    document["lock"] = {
        "contentDigest": content_digest,
        "profile": "sha256-rfc8785-accidental-edit-detection-v1",
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def test_loader_refuses_a_pilot_changed_after_locking(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    _write_dataset(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["cases"][0]["query"] = "changed after locking"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(BenchmarkUnavailable, match="lock is unavailable"):
        cli.load_dataset(path)


def test_loader_returns_the_typed_accidental_edit_detection_profile(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dataset.json"
    _write_dataset(path)

    dataset = cli.load_dataset(path)

    assert dataset.lock_profile is DatasetLockProfile.ACCIDENTAL_EDIT_DETECTION


@pytest.mark.parametrize(
    "mutate",
    (
        lambda document: document["cases"][0].update({"caseRef": "INVALID CASE"}),
        lambda document: document["cases"][0].update({"unknown": True}),
        lambda document: document["documents"].clear(),
        lambda document: document["cases"][0].update(
            {"expectedDocumentRefs": ["doc-alpha", "doc-alpha"]}
        ),
    ),
)
def test_loader_and_tracked_schema_refuse_the_same_boundary_documents(
    tmp_path: Path,
    mutate: Any,
) -> None:
    path = tmp_path / "dataset.json"
    _write_dataset(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    unlocked = {key: value for key, value in document.items() if key != "lock"}
    document["lock"]["contentDigest"] = cli.dataset_content_digest(unlocked)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(BenchmarkUnavailable, match="schema is unavailable"):
        cli.load_dataset(path)


def test_locked_document_accepted_by_schema_is_accepted_by_loader(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dataset.json"
    _write_dataset(path)

    dataset = cli.load_dataset(path)

    assert dataset.locked is True


def test_loader_accepts_multiline_markdown_document_text(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    _write_dataset(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["documents"][0]["text"] = "# Heading\n\nA real Markdown paragraph."
    unlocked = {key: value for key, value in document.items() if key != "lock"}
    document["lock"]["contentDigest"] = cli.dataset_content_digest(unlocked)
    path.write_text(json.dumps(document), encoding="utf-8")

    dataset = cli.load_dataset(path)

    assert dataset.documents[0].text == "# Heading\n\nA real Markdown paragraph."


def test_loader_preserves_a_markdown_terminal_newline(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    _write_dataset(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["documents"][0]["text"] = "# Heading\n"
    unlocked = {key: value for key, value in document.items() if key != "lock"}
    document["lock"]["contentDigest"] = cli.dataset_content_digest(unlocked)
    path.write_text(json.dumps(document), encoding="utf-8")

    dataset = cli.load_dataset(path)

    assert dataset.documents[0].text == "# Heading\n"


def test_loader_treats_the_supplied_schema_as_its_only_shape_authority(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    path = tmp_path / "dataset.json"
    schema_path = tmp_path / "input.schema.json"
    _write_dataset(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["operatorAnnotation"] = "ignored by the benchmark domain loader"
    unlocked = {key: value for key, value in document.items() if key != "lock"}
    document["lock"]["contentDigest"] = cli.dataset_content_digest(unlocked)
    path.write_text(json.dumps(document), encoding="utf-8")
    schema = json.loads(cli.INPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["properties"]["operatorAnnotation"] = {"type": "string"}
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(cli, "INPUT_SCHEMA_PATH", schema_path)

    dataset = cli.load_dataset(path)

    assert dataset.cases[0].case_ref == "case-alpha"


def test_cli_run_writes_a_schema_valid_report_for_a_synthetic_set(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    output_path = Path(".context-engine") / "test" / "embedding-benchmark.json"
    _write_dataset(dataset_path)

    def provider(
        _backend: cli.SupportedBackend, role: str, _model_dir: Path
    ) -> SyntheticProvider:
        model_id = (
            "Qwen/Qwen3-Embedding-0.6B"
            if role == "primary"
            else "intfloat/multilingual-e5-small"
        )
        return SyntheticProvider(_identity(model_id))

    monkeypatch.setattr(cli, "build_local_provider", provider)
    monkeypatch.setattr(cli, "load_retrieval_judge", lambda: RecordingJudge())

    result = cli.main(
        [
            "run",
            "--dataset",
            str(dataset_path),
            "--primary-model-dir",
            str(tmp_path / "primary"),
            "--baseline-model-dir",
            str(tmp_path / "baseline"),
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
            "--output",
            str(Path("tests") / "tracked.json"),
        ]
    )

    assert result == 2
    assert not (Path("tests") / "tracked.json").exists()


def test_cli_reports_a_baseline_win_with_success_exit_status(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    output_path = Path(".context-engine") / "test" / "embedding-loss.json"
    _write_dataset(dataset_path)

    def provider(
        _backend: cli.SupportedBackend, role: str, _model_dir: Path
    ) -> SyntheticProvider:
        model_id = (
            "Qwen/Qwen3-Embedding-0.6B"
            if role == "primary"
            else "intfloat/multilingual-e5-small"
        )
        return SyntheticProvider(_identity(model_id))

    monkeypatch.setattr(cli, "build_local_provider", provider)
    monkeypatch.setattr(cli, "load_retrieval_judge", lambda: BaselineWinningJudge())

    result = cli.main(
        [
            "run",
            "--dataset",
            str(dataset_path),
            "--backend",
            "sentence-transformers",
            "--primary-model-dir",
            str(tmp_path / "primary"),
            "--baseline-model-dir",
            str(tmp_path / "baseline"),
            "--output",
            str(output_path),
            "--top-k",
            "1",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert report["comparison"]["primaryAgainstModelBaseline"] == "lose"
    output_path.unlink()


def test_cli_refuses_a_missing_retrieval_judge(monkeypatch: Any) -> None:
    def missing(_module_name: str) -> Any:
        raise ModuleNotFoundError

    monkeypatch.setattr(importlib, "import_module", missing)

    with pytest.raises(BenchmarkUnavailable, match="judge is unavailable"):
        cli.load_retrieval_judge()


def test_output_cannot_escape_the_workspace_state_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(BenchmarkUnavailable, match="must be under"):
        cli._require_ignored_output(
            workspace / ".context-engine" / ".." / "tracked.json",
            workspace_root=workspace,
        )


def test_missing_real_backend_names_the_optional_extra(
    monkeypatch: Any,
) -> None:
    def missing(_name: str) -> Any:
        raise ModuleNotFoundError

    monkeypatch.setattr(importlib, "import_module", missing)

    with pytest.raises(BenchmarkUnavailable, match=r"context-engine\[benchmark\]"):
        cli._load_sentence_transformers()


def test_closed_backend_enum_refuses_arbitrary_import_paths() -> None:
    parser = cli._parser()

    with pytest.raises(SystemExit, match="2"):
        parser.parse_args(
            [
                "run",
                "--dataset",
                "dataset.json",
                "--backend",
                "some.module:factory",
                "--primary-model-dir",
                "primary",
                "--baseline-model-dir",
                "baseline",
                "--output",
                ".context-engine/report.json",
            ]
        )


@pytest.mark.parametrize(
    "invalid_json",
    (
        '{"absurd": 9223372036854775808}',
        '{"absurd": 1e1000000}',
        '{"absurd": NaN}',
        '{"absurd": Infinity}',
        '{"absurd": -Infinity}',
        "[" * 80 + "0" + "]" * 80,
        json.dumps({"absurd": "x" * (1024 * 1024 + 1)}),
        json.dumps({"absurd": [0] * 10_001}),
    ),
)
def test_loader_refuses_pathological_json_as_a_typed_outcome(
    tmp_path: Path,
    invalid_json: str,
) -> None:
    path = tmp_path / "dataset.json"
    path.write_text(invalid_json, encoding="utf-8")

    with pytest.raises(BenchmarkUnavailable):
        cli.load_dataset(path)


def test_loader_refuses_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    path.write_text('{"duplicate": 1, "duplicate": 2}', encoding="utf-8")

    with pytest.raises(BenchmarkUnavailable, match="JSON is unavailable"):
        cli.load_dataset(path)


def test_bounded_loader_refuses_a_fifo_before_reading(tmp_path: Path) -> None:
    path = tmp_path / "dataset.pipe"
    os.mkfifo(path)

    with pytest.raises(BenchmarkUnavailable, match="JSON is unavailable"):
        cli.load_dataset(path)


def test_bounded_loader_refuses_a_directory(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkUnavailable, match="JSON is unavailable"):
        cli.load_dataset(tmp_path)


def test_bounded_loader_refuses_a_device_node() -> None:
    null_device = Path(os.devnull)
    assert stat.S_ISCHR(null_device.stat().st_mode)

    with pytest.raises(BenchmarkUnavailable, match="JSON is unavailable"):
        cli.load_dataset(null_device)


def test_bounded_loader_refuses_a_dangling_symlink(tmp_path: Path) -> None:
    path = tmp_path / "dangling.json"
    path.symlink_to(tmp_path / "missing.json")

    with pytest.raises(BenchmarkUnavailable, match="JSON is unavailable"):
        cli.load_dataset(path)


def test_bounded_loader_refuses_a_symlink_outside_its_expected_root(
    tmp_path: Path,
) -> None:
    expected_root = tmp_path / "expected"
    expected_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    path = expected_root / "dataset.json"
    path.symlink_to(outside)

    with pytest.raises(BenchmarkUnavailable, match="JSON is unavailable"):
        cli.load_dataset(path)
