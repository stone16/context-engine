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
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >>"$HARNESS_COMMAND_LOG"\n',
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

    first_project, first_command = _run_stubbed_harness(first_checkout, stub_directory)
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


def test_fresh_environment_has_a_dedicated_security_operator_credential(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    checkout = tmp_path / "checkout"

    _run_stubbed_harness(checkout, stub_directory)

    environment_path = checkout / ".context-engine/database.env"
    generated = dict(
        line.split("=", maxsplit=1)
        for line in environment_path.read_text(encoding="utf-8").splitlines()
    )
    operator_password = generated["CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD"]
    assert generated["CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE"] == (
        "context_engine_security_operator"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", operator_password)
    assert generated["CONTEXT_ENGINE_SECURITY_OPERATOR_DATABASE_URL"] == (
        "postgresql+psycopg://context_engine_security_operator:"
        f"{operator_password}@127.0.0.1:"
        f"{generated['CONTEXT_ENGINE_POSTGRES_PORT']}/context_engine"
    )
    assert operator_password not in {
        generated["POSTGRES_PASSWORD"],
        generated["CONTEXT_ENGINE_MIGRATOR_PASSWORD"],
        generated["CONTEXT_ENGINE_CONTROL_PASSWORD"],
        generated["CONTEXT_ENGINE_RUNTIME_PASSWORD"],
        generated["CONTEXT_ENGINE_WORKER_PASSWORD"],
    }
    assert environment_path.stat().st_mode & 0o777 == 0o600


def test_fresh_environment_has_a_dedicated_learning_credential(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    checkout = tmp_path / "checkout"

    _run_stubbed_harness(checkout, stub_directory)

    environment_path = checkout / ".context-engine/database.env"
    generated = dict(
        line.split("=", maxsplit=1)
        for line in environment_path.read_text(encoding="utf-8").splitlines()
    )
    learning_password = generated["CONTEXT_ENGINE_LEARNING_PASSWORD"]
    assert generated["CONTEXT_ENGINE_LEARNING_ROLE"] == "context_engine_learning"
    assert re.fullmatch(r"[0-9a-f]{64}", learning_password)
    assert generated["CONTEXT_ENGINE_LEARNING_DATABASE_URL"] == (
        "postgresql+psycopg://context_engine_learning:"
        f"{learning_password}@127.0.0.1:"
        f"{generated['CONTEXT_ENGINE_POSTGRES_PORT']}/context_engine"
    )
    assert learning_password not in {
        generated["POSTGRES_PASSWORD"],
        generated["CONTEXT_ENGINE_MIGRATOR_PASSWORD"],
        generated["CONTEXT_ENGINE_CONTROL_PASSWORD"],
        generated["CONTEXT_ENGINE_RUNTIME_PASSWORD"],
        generated["CONTEXT_ENGINE_WORKER_PASSWORD"],
        generated["CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD"],
    }
    assert environment_path.stat().st_mode & 0o777 == 0o600


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
        'if [[ "$2" == */database.env ]]; then\n'
        '  marker="$HARNESS_BARRIER_DIR/$$"\n'
        '  : >"$marker"\n'
        '  if mkdir "$HARNESS_BARRIER_DIR/leader" 2>/dev/null; then\n'
        '    while [[ $(find "$HARNESS_BARRIER_DIR" -type f | wc -l) -lt 2 ]]; do\n'
        "      sleep 0.01\n"
        "    done\n"
        "  else\n"
        '    while [[ $(find "$HARNESS_BARRIER_DIR" -type f | wc -l) -lt 2 ]]; do\n'
        "      sleep 0.01\n"
        "    done\n"
        "    sleep 0.3\n"
        "  fi\n"
        "fi\n"
        'exec /bin/ln "$@"\n',
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


def test_legacy_environment_gains_one_generated_control_credential(
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
        if not line.startswith("CONTEXT_ENGINE_CONTROL_")
    )
    environment_path.write_text(f"{legacy_environment}\n", encoding="utf-8")
    environment_path.chmod(0o600)

    migrated_project, command = _run_stubbed_harness(checkout, stub_directory)

    migrated_lines = environment_path.read_text(encoding="utf-8").splitlines()
    migrated = dict(line.split("=", maxsplit=1) for line in migrated_lines)
    assert migrated_project == project
    assert f"--project-name {project}" in command
    assert migrated["CONTEXT_ENGINE_CONTROL_ROLE"] == "context_engine_control"
    control_password = migrated["CONTEXT_ENGINE_CONTROL_PASSWORD"]
    assert re.fullmatch(r"[0-9a-f]{64}", control_password)
    assert migrated["CONTEXT_ENGINE_CONTROL_DATABASE_URL"] == (
        "postgresql+psycopg://context_engine_control:"
        f"{control_password}@127.0.0.1:"
        f"{migrated['CONTEXT_ENGINE_POSTGRES_PORT']}/context_engine"
    )
    assert environment_path.stat().st_mode & 0o777 == 0o600


def test_legacy_environment_gains_one_generated_security_operator_credential(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    checkout = tmp_path / "checkout"
    project, _ = _run_stubbed_harness(checkout, stub_directory)
    environment_path = checkout / ".context-engine/database.env"
    original = dict(
        line.split("=", maxsplit=1)
        for line in environment_path.read_text(encoding="utf-8").splitlines()
    )
    legacy_environment = "\n".join(
        line
        for line in environment_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("CONTEXT_ENGINE_SECURITY_OPERATOR_")
    )
    environment_path.write_text(f"{legacy_environment}\n", encoding="utf-8")
    environment_path.chmod(0o600)

    migrated_project, command = _run_stubbed_harness(checkout, stub_directory)

    migrated_lines = environment_path.read_text(encoding="utf-8").splitlines()
    migrated = dict(line.split("=", maxsplit=1) for line in migrated_lines)
    assert migrated_project == project
    assert f"--project-name {project}" in command
    assert migrated["CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE"] == (
        "context_engine_security_operator"
    )
    operator_password = migrated["CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD"]
    assert re.fullmatch(r"[0-9a-f]{64}", operator_password)
    assert migrated["CONTEXT_ENGINE_SECURITY_OPERATOR_DATABASE_URL"] == (
        "postgresql+psycopg://context_engine_security_operator:"
        f"{operator_password}@127.0.0.1:"
        f"{migrated['CONTEXT_ENGINE_POSTGRES_PORT']}/context_engine"
    )
    for name, value in original.items():
        if not name.startswith("CONTEXT_ENGINE_SECURITY_OPERATOR_"):
            assert migrated[name] == value
    assert environment_path.stat().st_mode & 0o777 == 0o600

    persisted_environment = environment_path.read_text(encoding="utf-8")
    repeated_project, _ = _run_stubbed_harness(checkout, stub_directory)
    assert repeated_project == project
    assert environment_path.read_text(encoding="utf-8") == persisted_environment


def test_partial_legacy_learning_identity_is_replaced_as_one_exact_triple(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    checkout = tmp_path / "checkout"
    project, _ = _run_stubbed_harness(checkout, stub_directory)
    environment_path = checkout / ".context-engine/database.env"
    non_learning = [
        line
        for line in environment_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("CONTEXT_ENGINE_LEARNING_")
    ]
    environment_path.write_text(
        "\n".join(
            [
                *non_learning,
                "CONTEXT_ENGINE_LEARNING_ROLE=context_engine_learning",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    environment_path.chmod(0o600)

    migrated_project, _ = _run_stubbed_harness(checkout, stub_directory)

    lines = environment_path.read_text(encoding="utf-8").splitlines()
    learning_lines = [
        line for line in lines if line.startswith("CONTEXT_ENGINE_LEARNING_")
    ]
    migrated = dict(line.split("=", maxsplit=1) for line in lines)
    assert migrated_project == project
    assert len(learning_lines) == 3
    assert len({line.split("=", maxsplit=1)[0] for line in learning_lines}) == 3
    password = migrated["CONTEXT_ENGINE_LEARNING_PASSWORD"]
    assert migrated["CONTEXT_ENGINE_LEARNING_DATABASE_URL"] == (
        "postgresql+psycopg://context_engine_learning:"
        f"{password}@127.0.0.1:"
        f"{migrated['CONTEXT_ENGINE_POSTGRES_PORT']}/context_engine"
    )


def test_concurrent_legacy_learning_migration_generates_one_password(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    checkout = tmp_path / "checkout"
    _run_stubbed_harness(checkout, stub_directory)
    environment_path = checkout / ".context-engine/database.env"
    retained_lines = [
        line
        for line in environment_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("CONTEXT_ENGINE_LEARNING_")
    ]
    environment_path.write_text("\n".join(retained_lines) + "\n", encoding="utf-8")
    environment_path.chmod(0o600)

    python_log = tmp_path / "python-calls.log"
    _write_executable(
        stub_directory / "python3",
        "#!/usr/bin/env bash\n"
        "printf 'called\\n' >>\"$HARNESS_PYTHON_LOG\"\n"
        "sleep 0.2\n"
        "printf '%064d\\n' 0\n",
    )
    scripts = checkout / "scripts"
    command_log = checkout / "docker-command.log"
    environment = {
        **os.environ,
        "HARNESS_COMMAND_LOG": str(command_log),
        "HARNESS_PYTHON_LOG": str(python_log),
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
    assert python_log.read_text(encoding="utf-8").splitlines() == ["called"]
    lines = environment_path.read_text(encoding="utf-8").splitlines()
    learning_lines = [
        line for line in lines if line.startswith("CONTEXT_ENGINE_LEARNING_")
    ]
    assert len(learning_lines) == 3
    assert len({line.split("=", maxsplit=1)[0] for line in learning_lines}) == 3


def test_legacy_environment_gains_one_generated_learning_credential(
    tmp_path: Path,
) -> None:
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    _stub_harness_dependencies(stub_directory)
    checkout = tmp_path / "checkout"
    project, _ = _run_stubbed_harness(checkout, stub_directory)
    environment_path = checkout / ".context-engine/database.env"
    original = dict(
        line.split("=", maxsplit=1)
        for line in environment_path.read_text(encoding="utf-8").splitlines()
    )
    legacy_environment = "\n".join(
        line
        for line in environment_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("CONTEXT_ENGINE_LEARNING_")
    )
    environment_path.write_text(f"{legacy_environment}\n", encoding="utf-8")
    environment_path.chmod(0o600)

    migrated_project, command = _run_stubbed_harness(checkout, stub_directory)

    migrated = dict(
        line.split("=", maxsplit=1)
        for line in environment_path.read_text(encoding="utf-8").splitlines()
    )
    assert migrated_project == project
    assert f"--project-name {project}" in command
    assert migrated["CONTEXT_ENGINE_LEARNING_ROLE"] == "context_engine_learning"
    learning_password = migrated["CONTEXT_ENGINE_LEARNING_PASSWORD"]
    assert re.fullmatch(r"[0-9a-f]{64}", learning_password)
    assert migrated["CONTEXT_ENGINE_LEARNING_DATABASE_URL"] == (
        "postgresql+psycopg://context_engine_learning:"
        f"{learning_password}@127.0.0.1:"
        f"{migrated['CONTEXT_ENGINE_POSTGRES_PORT']}/context_engine"
    )
    for name, value in original.items():
        if not name.startswith("CONTEXT_ENGINE_LEARNING_"):
            assert migrated[name] == value
    assert environment_path.stat().st_mode & 0o777 == 0o600

    persisted_environment = environment_path.read_text(encoding="utf-8")
    repeated_project, _ = _run_stubbed_harness(checkout, stub_directory)
    assert repeated_project == project
    assert environment_path.read_text(encoding="utf-8") == persisted_environment
