from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_acceptance_report_is_count_only_deterministic_and_written_under_ignore(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "private-corpus"
    corpus.mkdir()
    (corpus / "one.md").write_text("# One\n\nFirst.\n", encoding="utf-8")
    (corpus / "two.md").write_text(
        "# Two\n\n| A | B |\n| --- | --- |\n| x | y |\n",
        encoding="utf-8",
    )
    output = tmp_path / ".context-engine/compiler-runner-acceptance.json"
    command = [
        sys.executable,
        "-m",
        "applications.compiler_runner",
        "--acceptance-report",
        "--root",
        str(corpus),
        "--output",
        str(output),
    ]

    first = subprocess.run(command, check=True, capture_output=True, text=True)
    first_bytes = output.read_bytes()
    second = subprocess.run(command, check=True, capture_output=True, text=True)

    report = json.loads(first_bytes)
    assert report["schemaVersion"] == "compiler-runner-acceptance-v1"
    assert report["documents"] == {"accepted": 2, "refused": 0, "total": 2}
    assert report["aggregateCompilationDigest"]
    assert report["maxFragmentTokenCount"] <= report["tokenCeiling"]
    assert report["constructHistogram"]["tables"] == 1
    assert str(corpus) not in first_bytes.decode("utf-8")
    assert "one.md" not in first_bytes.decode("utf-8")
    assert first_bytes == output.read_bytes()
    assert first.stdout == second.stdout
