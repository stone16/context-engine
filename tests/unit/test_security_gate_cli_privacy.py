from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

import pytest

from scripts.run_m0_security_gate import main as security_gate_main
from scripts.security_gate.runner import _execute_pytest

_ABSOLUTE_PATHS = (
    re.compile(r"(?<![:A-Za-z0-9_])/(?:[^\s/]+/)*[^\s/]+"),
    re.compile(r"[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s\\]+"),
)
_SYNTHETIC_PRIVATE_ROOT = "synthetic-private-vault-root"


def _assert_private_safe_output(output: str) -> None:
    assert _SYNTHETIC_PRIVATE_ROOT not in output
    assert all(pattern.search(output) is None for pattern in _ABSOLUTE_PATHS)


def test_gate_cli_scrubs_complete_output_across_all_channels(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    absolute_root = f"/private-machine/{_SYNTHETIC_PRIVATE_ROOT}"

    def noisy_gate(_paths: object) -> dict[str, str]:
        print(f"stdout evidence at {absolute_root}/stdout.json")
        print(f"stderr evidence at {absolute_root}/stderr.json", file=sys.stderr)
        logger = logging.getLogger("synthetic-security-gate-output")
        logger.addHandler(logging.StreamHandler(sys.stderr))
        logger.warning("log evidence at %s/log.json", absolute_root)
        return {
            "m0SecurityDecision": "pass",
            "releaseDecision": "not-evaluated",
        }

    monkeypatch.setattr("scripts.run_m0_security_gate.run_gate", noisy_gate)

    assert security_gate_main(["--output-dir", absolute_root]) == 0
    captured = capsys.readouterr()
    _assert_private_safe_output(captured.out + captured.err)


def test_gate_child_process_output_is_scrubbed_before_forwarding(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    absolute_root = f"/private-machine/{_SYNTHETIC_PRIVATE_ROOT}"
    program = (
        "import sys; "
        f"print({str(absolute_root + '/stdout.json')!r}); "
        f"print({str(absolute_root + '/stderr.json')!r}, file=sys.stderr)"
    )

    exit_code = _execute_pytest(
        (sys.executable, "-c", program),
        cwd=tmp_path,
        env=os.environ,
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    _assert_private_safe_output(captured.out + captured.err)
