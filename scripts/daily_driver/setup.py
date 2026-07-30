"""Idempotently prepare, but never install, one daily-driver deployment."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path

from engine.learning.golden_storage import (
    require_durable_golden_path,
    require_durable_storage_root,
)
from scripts.daily_driver.environment import EnvironmentRefused, load_owner_environment
from scripts.daily_driver.launchd import (
    LaunchdRenderConfiguration,
    write_rendered_templates,
)


class SetupRefused(ValueError):
    """The requested checkout could be disposable or overwrite operator work."""


DURABLE_DEPLOYMENT_MARKER = "daily-driver-v1\n"


def require_setup_target(*, target: Path, current_directory: Path) -> Path:
    """Refuse execution in worktrees and accept only a plain dedicated clone."""

    current = current_directory.resolve(strict=True)
    if _inside_git_worktree(current):
        raise SetupRefused("run from outside every git worktree")
    if not target.is_absolute() or target.is_symlink():
        raise SetupRefused("dedicated checkout path must be absolute and non-symlink")
    if ".context-engine" in target.parts:
        raise SetupRefused("dedicated checkout must follow the durable root contract")
    try:
        parent = require_durable_storage_root(target.parent)
        require_durable_golden_path(target, root=parent)
    except ValueError as error:
        raise SetupRefused(str(error)) from None
    if not target.exists():
        return target
    resolved = target.resolve(strict=True)
    git_entry = resolved / ".git"
    if git_entry.is_file():
        raise SetupRefused("durable target must be a plain dedicated checkout")
    if not git_entry.is_dir():
        if any(resolved.iterdir()):
            raise SetupRefused("durable target must be absent or a dedicated checkout")
        return resolved
    return resolved


def _inside_git_worktree(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare tracked ContextEngine daily-driver artifacts"
    )
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--docker-executable", type=Path, required=True)
    parser.add_argument("--uv-executable", type=Path, required=True)
    parser.add_argument("--label-prefix", required=True)
    parser.add_argument("--api-port", type=int, required=True)
    parser.add_argument("--backup-hour", type=int, required=True)
    parser.add_argument("--scan-hour", type=int, required=True)
    parser.add_argument("--health-interval-seconds", type=int, required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    checkout = require_setup_target(
        target=parsed.checkout,
        current_directory=Path.cwd(),
    )
    if not checkout.exists() or not (checkout / ".git").is_dir():
        subprocess.run(
            (
                "git",
                "clone",
                "--branch",
                parsed.branch,
                "--single-branch",
                parsed.origin,
                str(checkout),
            ),
            check=True,
        )
    else:
        _update_existing_checkout(checkout, parsed.origin, parsed.branch)

    state = checkout / ".context-engine"
    _prepare_state_directory(state)
    _write_durable_deployment_marker(state)
    _ensure_operator_environment(state / "operators.env")
    subprocess.run(("make", "install"), cwd=checkout, check=True)
    subprocess.run(("make", "db-up"), cwd=checkout, check=True)
    (state / "logs").mkdir(mode=0o700, exist_ok=True)
    write_rendered_templates(
        LaunchdRenderConfiguration(
            checkout=checkout,
            backup_root=parsed.backup_root,
            docker_executable=parsed.docker_executable,
            uv_executable=parsed.uv_executable,
            label_prefix=parsed.label_prefix,
            backup_hour=parsed.backup_hour,
            scan_hour=parsed.scan_hour,
            health_interval_seconds=parsed.health_interval_seconds,
            api_port=parsed.api_port,
        ),
        state / "launchd",
    )
    return 0


def _prepare_state_directory(state: Path) -> None:
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise SetupRefused("durable deployment state is unsafe")
    state.mkdir(mode=0o700, exist_ok=True)
    metadata = state.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise SetupRefused("durable deployment state is unsafe")
    state.chmod(0o700)


def _ensure_operator_environment(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            load_owner_environment(path)
        except EnvironmentRefused as error:
            raise SetupRefused(str(error)) from None
        return
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)


def _write_durable_deployment_marker(state: Path) -> None:
    marker = state / "durable-deployment"
    if marker.is_symlink() or (marker.exists() and not marker.is_file()):
        raise SetupRefused("durable deployment marker is unsafe")
    if marker.exists() and marker.read_text(encoding="utf-8") != (
        DURABLE_DEPLOYMENT_MARKER
    ):
        raise SetupRefused("durable deployment marker is invalid")
    marker.write_text(DURABLE_DEPLOYMENT_MARKER, encoding="utf-8")
    marker.chmod(0o600)


def _update_existing_checkout(checkout: Path, origin: str, branch: str) -> None:
    remote = subprocess.run(
        ("git", "-C", str(checkout), "remote", "get-url", "origin"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if remote != origin:
        raise SetupRefused("existing checkout origin does not match")
    status = subprocess.run(
        ("git", "-C", str(checkout), "status", "--porcelain"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise SetupRefused("existing checkout contains uncommitted changes")
    subprocess.run(("git", "-C", str(checkout), "fetch", "origin", branch), check=True)
    subprocess.run(("git", "-C", str(checkout), "checkout", branch), check=True)
    subprocess.run(
        ("git", "-C", str(checkout), "merge", "--ff-only", f"origin/{branch}"),
        check=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
