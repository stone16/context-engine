from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scripts.daily_driver.setup as daily_driver_setup
from scripts.daily_driver.deployment import (
    READY_DEPLOYMENT_MANIFEST,
    DeploymentBinding,
    DeploymentBindingRefused,
    DeploymentManifest,
    SchemaBindingRefused,
)
from scripts.daily_driver.environment import EnvironmentRefused
from scripts.daily_driver.setup import (
    DURABLE_DEPLOYMENT_MARKER,
    SetupRefused,
    _ensure_operator_environment,
    _prepare_state_directory,
    _write_durable_deployment_marker,
    require_setup_target,
)


def test_unchanged_exact_head_setup_is_idempotent_without_optional_mcp_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / ".context-engine").mkdir()
    database_environment = checkout / ".context-engine" / "database.env"
    database_environment.write_text("SYNTHETIC=value\n", encoding="utf-8")
    database_environment.chmod(0o600)
    executable = tmp_path / "executable"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        daily_driver_setup,
        "require_setup_target",
        lambda **kwargs: checkout,
    )
    monkeypatch.setattr(
        daily_driver_setup,
        "_update_existing_checkout",
        lambda *args: None,
    )
    monkeypatch.setattr(daily_driver_setup, "_prepare_state_directory", lambda *a: None)
    monkeypatch.setattr(
        daily_driver_setup,
        "_write_durable_deployment_marker",
        lambda *args: None,
    )
    monkeypatch.setattr(
        daily_driver_setup,
        "_ensure_operator_environment",
        lambda *args: None,
    )
    monkeypatch.setattr(
        daily_driver_setup,
        "current_deployment_binding",
        lambda _checkout, _environment: DeploymentBinding(
            "a" * 40, "sha256:" + "b" * 64
        ),
    )

    def publish_manifest(
        *_args: object,
        binding: DeploymentBinding,
    ) -> None:
        manifest = checkout / ".context-engine" / READY_DEPLOYMENT_MANIFEST
        manifest.parent.mkdir(exist_ok=True)
        document = DeploymentManifest(
            binding=binding,
            label_prefix="test.context-engine",
            plists=frozenset({"test.context-engine.api.plist"}),
            status="ready",
        ).to_document()
        manifest.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        manifest.chmod(0o600)

    monkeypatch.setattr(
        daily_driver_setup, "write_rendered_templates", publish_manifest
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    arguments = (
        "--checkout",
        str(checkout),
        "--origin",
        "https://example.invalid/context-engine.git",
        "--branch",
        "main",
        "--backup-root",
        str(tmp_path / "backup"),
        "--docker-executable",
        str(executable),
        "--uv-executable",
        str(executable),
        "--label-prefix",
        "test.context-engine",
        "--api-port",
        "8137",
        "--backup-hour",
        "2",
        "--scan-hour",
        "3",
        "--health-interval-seconds",
        "60",
    )

    first = daily_driver_setup.main(arguments)
    first_manifest = (
        checkout / ".context-engine" / READY_DEPLOYMENT_MANIFEST
    ).read_bytes()
    second = daily_driver_setup.main(arguments)

    assert first == second == 0
    assert calls == [
        ("make", "install-runtime"),
        ("make", "db-up"),
        ("make", "install-runtime"),
        ("make", "db-up"),
    ]
    manifest = checkout / ".context-engine" / READY_DEPLOYMENT_MANIFEST
    assert json.loads(manifest.read_text(encoding="utf-8")) == {
        "codeRevision": "a" * 40,
        "labelPrefix": "test.context-engine",
        "plists": ["test.context-engine.api.plist"],
        "schemaStateDigest": "sha256:" + "b" * 64,
        "schemaVersion": 2,
        "status": "ready",
    }
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert manifest.read_bytes() == first_manifest


@pytest.mark.parametrize(
    ("failure_source", "refusal", "expected_error"),
    [
        (
            "environment",
            EnvironmentRefused(),
            "daily-driver setup refused: restore the owner-only "
            ".context-engine/database.env, then rerun setup\n",
        ),
        (
            "binding",
            DeploymentBindingRefused(),
            "daily-driver setup refused: commit the dedicated checkout or restore "
            "a clean checkout, then rerun setup\n",
        ),
        (
            "binding",
            SchemaBindingRefused(),
            "daily-driver setup refused: run context-engine-control migrate, "
            "then rerun setup\n",
        ),
    ],
)
def test_setup_binding_refusal_preserves_existing_deployment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_source: str,
    refusal: ValueError,
    expected_error: str,
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    state = checkout / ".context-engine"
    launchd = state / "launchd"
    launchd.mkdir(parents=True)
    database_environment = state / "database.env"
    operator_environment = state / "operators.env"
    database_environment.write_text("PRESERVE_DATABASE=value\n", encoding="utf-8")
    operator_environment.write_text("PRESERVE_OPERATOR=value\n", encoding="utf-8")
    for environment in (database_environment, operator_environment):
        environment.chmod(0o600)
    manifest = state / READY_DEPLOYMENT_MANIFEST
    old_manifest = '{"status":"old-ready"}\n'
    manifest.write_text(old_manifest, encoding="utf-8")
    manifest.chmod(0o600)
    plist = launchd / "maintainer-owned.plist"
    plist.write_text("preserve plist", encoding="utf-8")
    executable = tmp_path / "executable"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        daily_driver_setup, "require_setup_target", lambda **_kwargs: checkout
    )
    monkeypatch.setattr(
        daily_driver_setup, "_update_existing_checkout", lambda *_a: None
    )
    monkeypatch.setattr(
        daily_driver_setup, "_prepare_state_directory", lambda *_a: None
    )
    monkeypatch.setattr(
        daily_driver_setup, "_write_durable_deployment_marker", lambda *_a: None
    )
    monkeypatch.setattr(
        daily_driver_setup, "_ensure_operator_environment", lambda *_a: None
    )
    if failure_source == "environment":
        monkeypatch.setattr(
            daily_driver_setup,
            "load_owner_environment",
            lambda _path: (_ for _ in ()).throw(refusal),
        )
        monkeypatch.setattr(
            daily_driver_setup,
            "current_deployment_binding",
            lambda *_a: pytest.fail(
                "environment refusal must precede deployment binding"
            ),
        )
    else:
        monkeypatch.setattr(
            daily_driver_setup,
            "current_deployment_binding",
            lambda _checkout, _environment: (_ for _ in ()).throw(refusal),
        )
    monkeypatch.setattr(
        daily_driver_setup,
        "write_rendered_templates",
        lambda *_a, **_kwargs: pytest.fail(
            "setup refusal must precede plist publication"
        ),
    )
    monkeypatch.setattr(
        subprocess, "run", lambda command, **_kwargs: calls.append(command)
    )

    result = daily_driver_setup.main(
        (
            "--checkout",
            str(checkout),
            "--origin",
            "https://example.invalid/context-engine.git",
            "--branch",
            "main",
            "--backup-root",
            str(tmp_path / "backup"),
            "--docker-executable",
            str(executable),
            "--uv-executable",
            str(executable),
            "--label-prefix",
            "test.context-engine",
            "--api-port",
            "8137",
            "--backup-hour",
            "2",
            "--scan-hour",
            "3",
            "--health-interval-seconds",
            "60",
        )
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == expected_error
    assert calls == [("make", "install-runtime"), ("make", "db-up")]
    assert manifest.read_text(encoding="utf-8") == old_manifest
    assert plist.read_text(encoding="utf-8") == "preserve plist"
    assert database_environment.read_text(encoding="utf-8") == (
        "PRESERVE_DATABASE=value\n"
    )
    assert operator_environment.read_text(encoding="utf-8") == (
        "PRESERVE_OPERATOR=value\n"
    )


def test_setup_has_no_implicit_migration_or_override_path() -> None:
    source = Path(daily_driver_setup.__file__).read_text(encoding="utf-8")

    assert "alembic" not in source.lower()
    assert "upgrade" not in source.lower()
    assert "override" not in source.lower()


def _git_repository(path: Path) -> None:
    subprocess.run(("git", "init", "--quiet", str(path)), check=True)


def test_setup_refuses_to_run_from_inside_any_git_worktree(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git_repository(source)
    target = tmp_path / "durable" / "context-engine"
    target.parent.mkdir()

    with pytest.raises(SetupRefused, match="run from outside every git worktree"):
        require_setup_target(target=target, current_directory=source)


def test_setup_refuses_a_target_inside_an_existing_git_worktree(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "disposable"
    parent.mkdir()
    _git_repository(parent)

    with pytest.raises(SetupRefused, match="outside every git worktree"):
        require_setup_target(
            target=parent / "context-engine",
            current_directory=tmp_path,
        )


def test_setup_refuses_a_linked_worktree_as_the_durable_checkout(
    tmp_path: Path,
) -> None:
    current_directory = tmp_path / "operator"
    current_directory.mkdir()
    target = tmp_path / "linked-worktree"
    target.mkdir()
    (target / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    with pytest.raises(SetupRefused, match="plain dedicated checkout"):
        require_setup_target(target=target, current_directory=current_directory)


def test_setup_accepts_an_existing_plain_dedicated_checkout(
    tmp_path: Path,
) -> None:
    current_directory = tmp_path / "operator"
    current_directory.mkdir()
    target = tmp_path / "context-engine"
    target.mkdir()
    (target / ".git").mkdir()

    assert (
        require_setup_target(target=target, current_directory=current_directory)
        == target
    )


def test_setup_refuses_an_absent_checkout_under_context_engine_state(
    tmp_path: Path,
) -> None:
    current_directory = tmp_path / "operator"
    current_directory.mkdir()

    with pytest.raises(SetupRefused, match="durable root contract"):
        require_setup_target(
            target=tmp_path / ".context-engine" / "daily-driver",
            current_directory=current_directory,
        )


def test_setup_marks_the_checkout_so_database_reset_refuses(tmp_path: Path) -> None:
    state = tmp_path / ".context-engine"
    state.mkdir()

    _write_durable_deployment_marker(state)
    _write_durable_deployment_marker(state)

    marker = state / "durable-deployment"
    assert marker.read_text(encoding="utf-8") == DURABLE_DEPLOYMENT_MARKER
    assert marker.stat().st_mode & 0o777 == 0o600


def test_setup_prepares_owner_only_state_without_overwriting_operator_values(
    tmp_path: Path,
) -> None:
    state = tmp_path / ".context-engine"

    _prepare_state_directory(state)
    operator_environment = state / "operators.env"
    _ensure_operator_environment(operator_environment)
    operator_environment.write_text("SYNTHETIC=value\n", encoding="utf-8")
    _ensure_operator_environment(operator_environment)

    assert state.stat().st_mode & 0o777 == 0o700
    assert operator_environment.stat().st_mode & 0o777 == 0o600
    assert operator_environment.read_text(encoding="utf-8") == "SYNTHETIC=value\n"


def test_setup_refuses_a_symbolic_link_state_directory(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    state = tmp_path / ".context-engine"
    state.symlink_to(actual, target_is_directory=True)

    with pytest.raises(SetupRefused, match="state is unsafe"):
        _prepare_state_directory(state)
