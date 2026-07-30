from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
_ABSOLUTE_PATHS = (
    re.compile(r"(?<![:A-Za-z0-9_])/(?:[^\s/]+/)*[^\s/]+"),
    re.compile(r"[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s\\]+"),
)
@pytest.mark.integration
def test_real_security_gate_cli_output_is_private_and_portable(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        (
            sys.executable,
            "scripts/run_m0_security_gate.py",
            "--output-dir",
            str(tmp_path / "gate-evidence"),
        ),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0
    assert output == "M0 SECURITY PASS\n"
    assert all(pattern.search(output) is None for pattern in _ABSOLUTE_PATHS)
