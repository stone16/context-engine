from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("service", ("api", "worker"))
def test_daemon_process_refuses_missing_binding_without_leaking_inputs(
    tmp_path: Path,
    service: str,
) -> None:
    private = "PRIVATE_DATABASE_VALUE_MUST_NOT_LEAK"
    checkout = tmp_path / "checkout-private-path"
    checkout.mkdir()
    database = tmp_path / "database-private.env"
    operator = tmp_path / "operator-private.env"
    database.write_text(f"SYNTHETIC={private}\n", encoding="utf-8")
    operator.write_text(f"SYNTHETIC={private}\n", encoding="utf-8")
    database.chmod(0o600)
    operator.chmod(0o600)
    command = [
        sys.executable,
        "-m",
        "scripts.daily_driver.jobs",
        "daemon",
        "--service",
        service,
        "--checkout",
        str(checkout),
        "--database-environment",
        str(database),
        "--operator-environment",
        str(operator),
    ]
    if service == "api":
        command.extend(("--api-port", "8137"))

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "")},
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == ""
    combined = completed.stdout + completed.stderr
    assert private not in combined
    assert str(checkout) not in combined
    assert str(database) not in combined
    assert str(operator) not in combined
