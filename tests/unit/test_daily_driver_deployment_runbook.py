from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPOSITORY_ROOT / "docs/operations/daily-driver-deployment.md"


def _migration_shell() -> str:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(?P<shell>.*?)```", runbook, re.DOTALL)
    matches = [
        block
        for block in blocks
        if "context-engine-control migrate" in block
        and "scripts/daily_driver_setup.py" in block
    ]
    assert len(matches) == 1, "runbook must keep one copyable migration/rerun block"
    return str(matches[0])


def test_migration_credentials_are_scoped_and_use_the_bound_uv(
    tmp_path: Path,
) -> None:
    shell = _migration_shell()
    scoped = re.match(r"^\(\n(?P<body>.*?)^\)\n", shell, re.DOTALL | re.MULTILINE)
    assert scoped is not None, "credential-bearing migration must start in a subshell"

    checkout = tmp_path / "checkout"
    state = checkout / ".context-engine"
    state.mkdir(parents=True)
    private_value = "CREDENTIAL_VISIBLE_ONLY_TO_MIGRATION"
    (state / "database.env").write_text(
        f"POSTGRES_PASSWORD={private_value}\n",
        encoding="utf-8",
    )

    capture = tmp_path / "bound-uv-invocation"
    bound_uv = tmp_path / "bound uv"
    bound_uv.write_text(
        "#!/bin/sh\n"
        f'test "$POSTGRES_PASSWORD" = "{private_value}" || exit 81\n'
        'printf \'%s\\n\' "$@" > "$CONTEXT_ENGINE_CAPTURE"\n',
        encoding="utf-8",
    )
    bound_uv.chmod(0o700)

    poisoned_path = tmp_path / "poisoned-path"
    poisoned_path.mkdir()
    bare_uv = poisoned_path / "uv"
    bare_uv.write_text("#!/bin/sh\nexit 82\n", encoding="utf-8")
    bare_uv.chmod(0o700)

    bash = shutil.which("bash")
    assert bash is not None
    completed = subprocess.run(
        (
            bash,
            "-eu",
            "-o",
            "pipefail",
            "-c",
            scoped.group(0) + 'test -z "${POSTGRES_PASSWORD+x}"\n',
        ),
        check=False,
        capture_output=True,
        text=True,
        env={
            "CONTEXT_ENGINE_CAPTURE": str(capture),
            "CONTEXT_ENGINE_DEPLOY_CHECKOUT": str(checkout),
            "CONTEXT_ENGINE_UV_EXECUTABLE": str(bound_uv),
            "PATH": str(poisoned_path),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert capture.read_text(encoding="utf-8") == (
        "run\ncontext-engine-control\nmigrate\n"
    )
