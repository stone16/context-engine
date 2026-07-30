from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.daily_driver.setup import (
    DURABLE_DEPLOYMENT_MARKER,
    SetupRefused,
    _ensure_operator_environment,
    _prepare_state_directory,
    _write_durable_deployment_marker,
    require_setup_target,
)


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
