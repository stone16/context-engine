from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
EGRESS_CONTRACT_PATH = "tests/unit/test_bot_delivery_model_egress_contract.py"
STATIC_EGRESS_TEST = "test_model_egress_package_contract_is_closed_and_pinned"
NODE_EGRESS_TEST = (
    "test_typescript_model_egress_is_closed_pinned_and_zero_byte_on_denial"
)


def _dry_run_make(target: str) -> list[str]:
    # Drop inherited sub-make state so the output is identical whether pytest
    # itself runs under `make` (CI) or standalone; sub-makes otherwise add
    # "Entering/Leaving directory" lines.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}
    }
    result = subprocess.run(
        ("make", "--dry-run", "--always-make", "--no-print-directory", target),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _collect_only(arguments: tuple[str, ...]) -> str:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CONTEXT_ENGINE_SECURITY_GATE_")
    }
    result = subprocess.run(
        (sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_python_unit_lane_is_fast_while_full_gates_keep_their_contracts() -> None:
    python_commands = _dry_run_make("test-python")

    assert python_commands == [
        'uv run pytest -q tests/unit -m "not node_toolchain"'
    ]
    assert all(
        "npm" not in command and not command.startswith("node")
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


def test_python_lane_marker_deselects_only_the_node_toolchain_portion() -> None:
    filtered = _collect_only(
        ("-m", "not node_toolchain", EGRESS_CONTRACT_PATH)
    )
    assert f"{EGRESS_CONTRACT_PATH}::{STATIC_EGRESS_TEST}" in filtered
    assert NODE_EGRESS_TEST not in filtered

    unfiltered = _collect_only((EGRESS_CONTRACT_PATH,))
    assert f"{EGRESS_CONTRACT_PATH}::{STATIC_EGRESS_TEST}" in unfiltered
    assert f"{EGRESS_CONTRACT_PATH}::{NODE_EGRESS_TEST}" in unfiltered
