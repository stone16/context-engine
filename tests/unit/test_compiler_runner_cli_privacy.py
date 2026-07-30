from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
_MACHINE_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![:A-Za-z0-9_])/(?:[^\s/]+/)*[^\s/]+"),
    re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:/(?:[^\s/]+/)*[^\s/]+"),
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
        "C:/" + "Users" + "/person/repository/app.py",
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


def test_process_output_privacy_assertion_rejects_a_planted_leak() -> None:
    completed = subprocess.CompletedProcess(
        args=(),
        returncode=1,
        stdout="C:/" + "Users" + "/person/repository/app.py\n",
        stderr="",
    )

    with pytest.raises(AssertionError):
        _assert_process_output_is_private(completed)


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


def test_acceptance_cli_parser_error_does_not_echo_the_invalid_argument() -> None:
    invalid_argument = "/private-machine/repository/not-an-integer"

    completed = _run_acceptance_cli("--token-ceiling", invalid_argument)

    assert completed.returncode != 0
    assert invalid_argument not in completed.stdout + completed.stderr
    _assert_process_output_is_private(completed)


def test_five_acceptance_cli_operator_errors_emit_their_specific_safe_messages(
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
        (
            ("--root", str(tmp_path / "missing")),
            "acceptance root must be a non-symlink directory",
        ),
        (
            ("--root", str(root_file)),
            "acceptance root must be a non-symlink directory",
        ),
        (
            ("--root", str(corpus), "--output", str(tmp_path / "report.json")),
            "acceptance reports must be written under .context-engine",
        ),
        (
            ("--root", str(corpus), "--token-ceiling", "0"),
            "rich Markdown token ceiling must be positive",
        ),
        (
            ("--root", str(corpus), "--output", str(state_directory)),
            "acceptance reports must be written under .context-engine",
        ),
    )

    for arguments, expected_message in operator_errors:
        completed = _run_acceptance_cli("--acceptance-report", *arguments)

        assert completed.returncode != 0
        assert completed.stdout == ""
        assert completed.stderr == f"{expected_message}\n"
        _assert_process_output_is_private(completed)


def test_acceptance_cli_unexpected_failure_emits_only_the_generic_safe_message(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "note.md").write_text("# Note\n\nBody.\n", encoding="utf-8")
    state_directory = tmp_path / ".context-engine"
    state_directory.mkdir()
    blocked_parent = state_directory / "blocked-parent"
    blocked_parent.write_text("not a directory\n", encoding="utf-8")

    completed = _run_acceptance_cli(
        "--acceptance-report",
        "--root",
        str(corpus),
        "--output",
        str(blocked_parent / "report.json"),
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "compiler runner operation failed\n"
    _assert_process_output_is_private(completed)
