from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
_MACHINE_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![:A-Za-z0-9_])/(?:[^\s/]+/)*[^\s/]+"),
    re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s\\]+"),
    re.compile(r"(?<!\\)\\\\[^\\\r\n]+\\[^\\\r\n]+"),
)


def _run_acceptance_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "applications.compiler_runner", *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _assert_process_output_is_private(
    completed: subprocess.CompletedProcess[str],
) -> None:
    captured = completed.stdout + completed.stderr

    assert str(REPOSITORY_ROOT) not in captured
    assert all(
        pattern.search(captured) is None
        for pattern in _MACHINE_ABSOLUTE_PATH_PATTERNS
    )


def test_machine_absolute_path_guard_recognizes_platform_path_shapes() -> None:
    canaries = (
        "/private-machine/repository/app.py",
        "C:\\Users\\person\\repository\\app.py",
        "\\\\server\\share\\repository\\app.py",
    )

    assert all(
        any(
            pattern.search(canary) is not None
            for pattern in _MACHINE_ABSOLUTE_PATH_PATTERNS
        )
        for canary in canaries
    )


def test_acceptance_cli_success_output_has_no_machine_local_absolute_path(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "note.md").write_text("# Note\n\nBody.\n", encoding="utf-8")
    output = tmp_path / ".context-engine/report.json"

    completed = _run_acceptance_cli(
        "--acceptance-report",
        "--root",
        str(corpus),
        "--output",
        str(output),
    )

    assert completed.returncode == 0
    _assert_process_output_is_private(completed)


def test_five_acceptance_cli_operator_errors_and_uncaught_exceptions_are_private(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "note.md").write_text("# Note\n\nBody.\n", encoding="utf-8")
    root_file = tmp_path / "root-file.md"
    root_file.write_text("# Not a directory\n", encoding="utf-8")
    state_directory = tmp_path / ".context-engine"
    state_directory.mkdir()
    operator_errors = (
        ("--root", str(tmp_path / "missing")),
        ("--root", str(root_file)),
        ("--root", str(corpus), "--output", str(tmp_path / "report.json")),
        ("--root", str(corpus), "--token-ceiling", "0"),
        ("--root", str(corpus), "--output", str(state_directory)),
    )

    for arguments in operator_errors:
        completed = _run_acceptance_cli("--acceptance-report", *arguments)

        assert completed.returncode != 0
        _assert_process_output_is_private(completed)
