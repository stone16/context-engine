from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _dry_run_make(target: str) -> list[str]:
    result = subprocess.run(
        ("make", "--dry-run", "--always-make", target),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def test_python_unit_lane_is_fast_while_full_gates_keep_their_contracts() -> None:
    python_commands = _dry_run_make("test-python")

    assert python_commands == ["uv run pytest -q tests/unit"]
    assert all(
        "npm" not in command and "node" not in command
        for command in python_commands
    )

    test_commands = _dry_run_make("test")
    assert "npm --prefix sdk/typescript run build" in test_commands
    assert "npm --prefix sdk/typescript-v1 run build" in test_commands
    assert "npm --prefix action_plane/typescript run build" in test_commands
    assert "npm --prefix bot_delivery/typescript run build" in test_commands
    assert test_commands[-1] == "uv run pytest -q tests/unit"

    check_commands = _dry_run_make("check")
    for required_command in (
        "npm --prefix sdk/typescript test",
        "npm --prefix sdk/typescript-v1 test",
        "npm --prefix action_plane/typescript run test:runtime",
        "npm --prefix bot_delivery/typescript run test:runtime",
        "uv run pytest -q tests/catalog",
        "uv run pytest -q tests/process",
        "./scripts/database_harness.sh integration",
        "uv run python scripts/run_m0_security_gate.py "
        "--output-dir .context-engine/security-gate",
    ):
        assert required_command in check_commands
