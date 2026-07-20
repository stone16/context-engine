from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _run_stubbed_harness(checkout: Path, stub_directory: Path) -> tuple[str, str]:
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "scripts/database_harness.sh", scripts)
    (checkout / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    command_log = checkout / "docker-command.log"
    environment = {
        **os.environ,
        "HARNESS_COMMAND_LOG": str(command_log),
        "PATH": f"{stub_directory}{os.pathsep}{os.environ['PATH']}",
    }

    subprocess.run(
        ["/bin/bash", str(scripts / "database_harness.sh"), "up"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    environment_contents = (checkout / ".context-engine/database.env").read_text(
        encoding="utf-8"
    )
    project = next(
        line.partition("=")[2]
        for line in environment_contents.splitlines()
        if line.startswith("CONTEXT_ENGINE_COMPOSE_PROJECT=")
    )
    return project, command_log.read_text(encoding="utf-8")


def _stub_harness_dependencies(stub_directory: Path) -> None:
    _write_executable(
        stub_directory / "docker",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >>\"$HARNESS_COMMAND_LOG\"\n",
    )
    _write_executable(stub_directory / "uv", "#!/usr/bin/env bash\nexit 0\n")


def test_two_checkouts_generate_distinct_persistent_compose_projects(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    first_checkout = tmp_path / "first"
    second_checkout = tmp_path / "second"

    first_project, first_command = _run_stubbed_harness(
        first_checkout, stub_directory
    )
    second_project, second_command = _run_stubbed_harness(
        second_checkout, stub_directory
    )

    assert first_project.startswith("context-engine-")
    assert second_project.startswith("context-engine-")
    assert first_project != second_project
    assert f"--project-name {first_project}" in first_command
    assert f"--project-name {second_project}" in second_command

    repeated_project, repeated_command = _run_stubbed_harness(
        first_checkout, stub_directory
    )
    assert repeated_project == first_project
    assert f"--project-name {first_project}" in repeated_command


def test_concurrent_first_use_converges_on_one_persisted_compose_project(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    barrier_directory = tmp_path / "barrier"
    barrier_directory.mkdir()
    _write_executable(
        stub_directory / "ln",
        "#!/usr/bin/env bash\n"
        "if [[ \"$2\" == */database.env ]]; then\n"
        "  marker=\"$HARNESS_BARRIER_DIR/$$\"\n"
        "  : >\"$marker\"\n"
        "  if mkdir \"$HARNESS_BARRIER_DIR/leader\" 2>/dev/null; then\n"
        "    while [[ $(find \"$HARNESS_BARRIER_DIR\" -type f | wc -l) -lt 2 ]]; do\n"
        "      sleep 0.01\n"
        "    done\n"
        "  else\n"
        "    while [[ $(find \"$HARNESS_BARRIER_DIR\" -type f | wc -l) -lt 2 ]]; do\n"
        "      sleep 0.01\n"
        "    done\n"
        "    sleep 0.3\n"
        "  fi\n"
        "fi\n"
        "exec /bin/ln \"$@\"\n",
    )
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/database_harness.sh", scripts)
    (checkout / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    command_log = checkout / "docker-command.log"
    environment = {
        **os.environ,
        "HARNESS_BARRIER_DIR": str(barrier_directory),
        "HARNESS_COMMAND_LOG": str(command_log),
        "PATH": f"{stub_directory}{os.pathsep}{os.environ['PATH']}",
    }

    processes = [
        subprocess.Popen(
            ["/bin/bash", str(scripts / "database_harness.sh"), "up"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=15) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], outputs
    projects = re.findall(
        r"--project-name (context-engine-[0-9a-f]{16})",
        command_log.read_text(encoding="utf-8"),
    )
    environment_contents = (checkout / ".context-engine/database.env").read_text(
        encoding="utf-8"
    )
    persisted_project = next(
        line.partition("=")[2]
        for line in environment_contents.splitlines()
        if line.startswith("CONTEXT_ENGINE_COMPOSE_PROJECT=")
    )
    assert len(projects) == 2
    assert set(projects) == {persisted_project}


def test_legacy_two_file_state_migrates_without_changing_project_identity(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    checkout = tmp_path / "checkout"
    project, _ = _run_stubbed_harness(checkout, stub_directory)
    environment_path = checkout / ".context-engine/database.env"
    legacy_environment = "\n".join(
        line
        for line in environment_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("CONTEXT_ENGINE_COMPOSE_PROJECT=")
    )
    environment_path.write_text(f"{legacy_environment}\n", encoding="utf-8")
    environment_path.chmod(0o600)
    project_path = checkout / ".context-engine/compose-project"
    project_path.write_text(f"{project}\n", encoding="utf-8")
    project_path.chmod(0o600)

    migrated_project, command = _run_stubbed_harness(checkout, stub_directory)

    assert migrated_project == project
    assert f"--project-name {project}" in command
    assert project_path.read_text(encoding="utf-8").strip() == project
