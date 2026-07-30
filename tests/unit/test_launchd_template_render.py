from __future__ import annotations

import plistlib
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

import pytest

from applications.file_scan import _proof_keys
from applications.operator_authentication import local_secret_fingerprint
from engine.control import SourceNotAvailable
from scripts.daily_driver.environment import (
    EnvironmentRefused,
    combined_environment,
    load_owner_environment,
)
from scripts.daily_driver.jobs import (
    _run_database_bootstrap,
    process_environment,
    validate_scan_secret_separation,
)
from scripts.daily_driver.launchd import (
    LaunchdRenderConfiguration,
    LaunchdRenderRefused,
    render_launchd_templates,
    write_rendered_templates,
)

ROOT = Path(__file__).resolve().parents[2]


def _configuration(tmp_path: Path) -> LaunchdRenderConfiguration:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    (checkout / ".venv" / "bin").mkdir(parents=True)
    (checkout / ".venv" / "bin" / "python").touch()
    state = checkout / ".context-engine"
    state.mkdir(mode=0o700)
    for environment in (state / "database.env", state / "operators.env"):
        environment.write_text("SYNTHETIC_VALUE=present\n", encoding="utf-8")
        environment.chmod(0o600)
    shutil.copytree(
        ROOT / "deploy" / "daily-driver",
        checkout / "deploy" / "daily-driver",
    )
    backup_root = tmp_path / "database-backups"
    backup_root.mkdir(mode=0o700)
    docker_executable = tmp_path / "docker"
    docker_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    docker_executable.chmod(0o700)
    uv_executable = tmp_path / "uv"
    uv_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    uv_executable.chmod(0o700)
    return LaunchdRenderConfiguration(
        checkout=checkout,
        backup_root=backup_root,
        docker_executable=docker_executable,
        uv_executable=uv_executable,
        label_prefix="org.example.context-engine",
        backup_hour=2,
        scan_hour=3,
        health_interval_seconds=300,
        api_port=8137,
    )


def test_render_is_deterministic_and_contains_no_credentials(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)
    database_secret = "DATABASE_SECRET_MUST_NOT_RENDER"
    operator_secret = "OPERATOR_SECRET_MUST_NOT_RENDER"
    state = configuration.checkout / ".context-engine"
    (state / "database.env").write_text(
        f"POSTGRES_PASSWORD={database_secret}\n", encoding="utf-8"
    )
    (state / "operators.env").write_text(
        f"CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET={operator_secret}\n",
        encoding="utf-8",
    )

    first = render_launchd_templates(configuration)
    second = render_launchd_templates(configuration)

    assert first == second
    assert set(first) == {
        "org.example.context-engine.api.plist",
        "org.example.context-engine.backup.plist",
        "org.example.context-engine.database.plist",
        "org.example.context-engine.health.plist",
        "org.example.context-engine.scan.plist",
        "org.example.context-engine.worker.plist",
    }
    for rendered in first.values():
        assert database_secret not in rendered
        assert operator_secret not in rendered
        parsed = plistlib.loads(rendered.encode("utf-8"))
        assert parsed["Label"].startswith("org.example.context-engine.")
    database = plistlib.loads(
        first["org.example.context-engine.database.plist"].encode("utf-8")
    )
    assert database["RunAtLoad"] is True
    assert database["KeepAlive"] == {"SuccessfulExit": False}
    assert "bootstrap" in database["ProgramArguments"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("label_prefix", ""),
        ("docker_executable", None),
        ("uv_executable", None),
        ("backup_hour", None),
        ("scan_hour", None),
        ("health_interval_seconds", None),
        ("api_port", None),
    ),
)
def test_missing_required_render_input_refuses(
    tmp_path: Path,
    field: str,
    value: str | int | None,
) -> None:
    values = _configuration(tmp_path).__dict__ | {field: value}

    with pytest.raises(LaunchdRenderRefused, match="required"):
        LaunchdRenderConfiguration(**values)


def test_rendered_units_reference_the_single_live_environment_sources(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)

    rendered = render_launchd_templates(configuration)

    combined = "\n".join(rendered.values())
    assert ".context-engine/database.env" in combined
    assert ".context-engine/operators.env" in combined
    assert "POSTGRES_PASSWORD" not in combined
    assert "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET" not in combined


def test_rendered_templates_pass_the_platform_plist_validator(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)
    rendered = render_launchd_templates(configuration)

    for name, content in rendered.items():
        plist = tmp_path / name
        plist.write_text(content, encoding="utf-8")
        completed = subprocess.run(
            ("plutil", "-lint", str(plist)),
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


def test_writing_the_same_render_twice_is_idempotent(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    destination = configuration.checkout / ".context-engine" / "launchd"

    first = write_rendered_templates(configuration, destination)
    first_inodes = {path.name: path.stat().st_ino for path in first}
    second = write_rendered_templates(configuration, destination)

    assert first == second
    assert {path.name: path.stat().st_ino for path in second} == first_inodes


def test_render_refuses_a_label_change_until_the_old_services_are_uninstalled(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)
    destination = configuration.checkout / ".context-engine" / "launchd"
    first = write_rendered_templates(configuration, destination)
    changed = LaunchdRenderConfiguration(
        **(
            configuration.__dict__
            | {"label_prefix": "org.example.context-engine-v2"}
        )
    )

    with pytest.raises(LaunchdRenderRefused, match="prefix is immutable"):
        write_rendered_templates(changed, destination)

    assert set(destination.glob("*.plist")) == set(first)


def test_render_refuses_to_delete_an_unknown_plist(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    destination = configuration.checkout / ".context-engine" / "launchd"
    first = write_rendered_templates(configuration, destination)
    unknown = destination / "maintainer-owned.plist"
    unknown.write_text("preserve me", encoding="utf-8")

    with pytest.raises(LaunchdRenderRefused, match="unowned"):
        write_rendered_templates(configuration, destination)

    assert unknown.read_text(encoding="utf-8") == "preserve me"
    assert set(first) <= set(destination.glob("*.plist"))


def test_render_refuses_a_symbolic_link_at_an_owned_target(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    destination = configuration.checkout / ".context-engine" / "launchd"
    first = write_rendered_templates(configuration, destination)
    external = tmp_path / "external"
    external.write_text("must remain unchanged", encoding="utf-8")
    target = destination / "org.example.context-engine.api.plist"
    target.unlink()
    target.symlink_to(external)

    with pytest.raises(LaunchdRenderRefused, match="target is unsafe"):
        write_rendered_templates(configuration, destination)

    assert external.read_text(encoding="utf-8") == "must remain unchanged"
    assert set(path.name for path in first) - {target.name} <= {
        path.name for path in destination.glob("*.plist")
    }


def test_shell_quoted_json_environment_remains_one_live_source(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)
    operator_environment = (
        configuration.checkout / ".context-engine" / "operators.env"
    )
    operator_environment.write_text(
        "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON="
        "'{\"maintainer-notes\":\"/private/notes\"}'\n",
        encoding="utf-8",
    )
    operator_environment.chmod(0o600)

    rendered = render_launchd_templates(configuration)

    assert rendered
    assert "/private/notes" not in "\n".join(rendered.values())


@pytest.mark.parametrize(
    "malformed",
    (
        "value'",
        "'value",
        "'value'continued",
        '"value"',
        "$(touch /tmp/not-allowed)",
        "`touch /tmp/not-allowed`",
        "value;false",
    ),
)
def test_environment_parser_refuses_unmatched_or_interior_shell_quotes(
    tmp_path: Path,
    malformed: str,
) -> None:
    environment = tmp_path / "operators.env"
    environment.write_text(f"SYNTHETIC={malformed}\n", encoding="utf-8")
    environment.chmod(0o600)

    with pytest.raises(EnvironmentRefused, match="malformed"):
        load_owner_environment(environment)


def test_operator_environment_cannot_override_the_database_contract() -> None:
    with pytest.raises(EnvironmentRefused, match="single live contract"):
        combined_environment(
            {"CONTEXT_ENGINE_RUNTIME_DATABASE_URL": "database-source"},
            {"CONTEXT_ENGINE_RUNTIME_DATABASE_URL": "operator-source"},
        )


def test_child_processes_receive_only_their_closed_credential_projection() -> None:
    database = {
        "CONTEXT_ENGINE_RUNTIME_DATABASE_URL": "runtime-url",
        "CONTEXT_ENGINE_RUNTIME_ROLE": "runtime-role",
        "CONTEXT_ENGINE_WORKER_DATABASE_URL": "worker-url",
        "CONTEXT_ENGINE_WORKER_ROLE": "worker-role",
        "CONTEXT_ENGINE_SCHEDULER_DATABASE_URL": "scheduler-url",
        "CONTEXT_ENGINE_SCHEDULER_ROLE": "scheduler-role",
        "CONTEXT_ENGINE_CONTROL_DATABASE_URL": "control-url",
        "CONTEXT_ENGINE_CONTROL_ROLE": "control-role",
        "CONTEXT_ENGINE_MIGRATION_DATABASE_URL": "must-not-leak",
        "POSTGRES_PASSWORD": "must-not-leak",
    }
    operator = {
        name: "configured"
        for name in {
            "CONTEXT_ENGINE_API_COMPOSITION",
            "CONTEXT_ENGINE_DOGFOOD_AGENT_VERSION_REF",
            "CONTEXT_ENGINE_DOGFOOD_APPLICATION_REF",
            "CONTEXT_ENGINE_DOGFOOD_AUTHENTICATION_BINDING_REF",
            "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_PROVIDER",
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID",
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION",
            "CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID",
            "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF",
            "CONTEXT_ENGINE_DOGFOOD_SECRET",
            "CONTEXT_ENGINE_DOGFOOD_USER_ID",
            "CONTEXT_ENGINE_CONTROL_OPERATOR_OPERATIONS",
            "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET",
            "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID",
            "CONTEXT_ENGINE_OPERATOR_SOURCE_REF",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER",
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON",
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID",
            "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET",
        }
    }

    api = process_environment("api", database, operator)
    worker = process_environment("worker", database, operator)
    scan = process_environment("scan", database, operator)

    assert api["CONTEXT_ENGINE_RUNTIME_DATABASE_URL"] == "runtime-url"
    assert worker["CONTEXT_ENGINE_WORKER_DATABASE_URL"] == "worker-url"
    assert scan["CONTEXT_ENGINE_CONTROL_DATABASE_URL"] == "control-url"
    assert "POSTGRES_PASSWORD" not in api | worker | scan
    assert "CONTEXT_ENGINE_MIGRATION_DATABASE_URL" not in api | worker | scan
    assert "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET" not in api | worker | scan
    assert "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET" not in api | worker
    assert "CONTEXT_ENGINE_DOGFOOD_SECRET" not in worker | scan


def test_scan_validates_cross_plane_key_collisions_before_projection() -> None:
    proof_key = "11" * 32
    operator = {
        "CONTEXT_ENGINE_CONTROL_OPERATOR_OPERATIONS": "read_source",
        "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET": "control-" + "a" * 32,
        "CONTEXT_ENGINE_DOGFOOD_SECRET": "dogfood-" + "b" * 32,
        "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX": "22" * 32,
        "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX": proof_key,
        "CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID": (
            "14900000-0000-4000-8000-000000000001"
        ),
        "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET": proof_key.upper(),
        "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": "33" * 32,
    }

    with pytest.raises(EnvironmentRefused, match="separation"):
        validate_scan_secret_separation(operator)

    separated_operator = operator | {
        "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET": "release-" + "c" * 32,
    }
    fingerprints = validate_scan_secret_separation(separated_operator)
    assert set(fingerprints) == {
        "CONTEXT_ENGINE_DOGFOOD_SECRET_SHA256",
        "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET_SHA256",
    }
    assert proof_key not in fingerprints.values()
    assert "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET" not in process_environment(
        "scan",
        {
            "CONTEXT_ENGINE_CONTROL_DATABASE_URL": "control-url",
            "CONTEXT_ENGINE_CONTROL_ROLE": "control-role",
        },
        separated_operator
        | {
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID": "membership",
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION": "1",
            "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF": "principal",
            "CONTEXT_ENGINE_OPERATOR_SOURCE_REF": "source",
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": "{}",
            "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID": "service",
        },
    )


def test_scan_child_uses_fingerprints_without_receiving_release_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_key = "11" * 32
    checkpoint_key = "22" * 32
    release_secret = "release-" + "c" * 32
    dogfood_secret = "dogfood-" + "d" * 32
    environment = {
        "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET": "control-" + "a" * 32,
        "CONTEXT_ENGINE_DOGFOOD_SECRET_SHA256": local_secret_fingerprint(
            dogfood_secret
        ),
        "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX": checkpoint_key,
        "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX": provider_key,
        "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET_SHA256": local_secret_fingerprint(
            release_secret
        ),
        "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": "33" * 32,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CONTEXT_ENGINE_DOGFOOD_SECRET", raising=False)
    monkeypatch.delenv("CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET", raising=False)

    assert len(_proof_keys()) == 2

    monkeypatch.setenv(
        "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET_SHA256",
        local_secret_fingerprint(provider_key),
    )
    with pytest.raises(SourceNotAvailable):
        _proof_keys()


def test_database_bootstrap_opens_docker_then_runs_the_idempotent_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    harness = checkout / "scripts" / "database_harness.sh"
    harness.parent.mkdir(parents=True)
    harness.write_text("#!/bin/bash\n", encoding="utf-8")
    docker = tmp_path / "docker"
    uv = tmp_path / "uv"
    for executable in (docker, uv):
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)
    run = Mock(
        side_effect=(
            subprocess.CompletedProcess(("open",), 0),
            subprocess.CompletedProcess(("database_harness.sh",), 0),
        )
    )
    monkeypatch.setattr(subprocess, "run", run)

    result = _run_database_bootstrap(
        Namespace(
            checkout=checkout,
            docker_executable=docker,
            uv_executable=uv,
        )
    )

    assert result == 0
    assert run.call_args_list[0].args[0] == ("/usr/bin/open", "-gja", "Docker")
    assert run.call_args_list[1].args[0] == (
        "/bin/bash",
        str(harness),
        "up",
    )
    child_environment = run.call_args_list[1].kwargs["env"]
    assert str(docker.parent) in child_environment["PATH"]
    assert str(uv.parent) in child_environment["PATH"]
