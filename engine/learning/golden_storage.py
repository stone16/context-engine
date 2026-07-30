"""One durable, worktree-external storage contract for the private corpus."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

GOLDEN_ROOT_ENV: Final = "CONTEXT_ENGINE_GOLDEN_ROOT"
GOLDEN_BACKUP_ROOT_ENV: Final = "CONTEXT_ENGINE_GOLDEN_BACKUP_ROOT"
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]


def require_durable_storage_root(root: Path) -> Path:
    """Apply the one durable, worktree-external root convention."""

    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or not root.is_dir()
        or root.is_symlink()
        or ".context-engine" in root.parts
    ):
        raise ValueError("durable storage root is unavailable")
    resolved = root.resolve(strict=True)
    if any(
        (candidate / ".git").exists()
        for candidate in (resolved, *resolved.parents)
    ):
        raise ValueError("durable storage root must be outside every git worktree")
    return resolved


def _configured_durable_root(variable: str) -> Path:
    try:
        configured = os.environ[variable]
    except KeyError:
        raise ValueError("durable golden root is unavailable") from None
    if not configured or configured != configured.strip():
        raise ValueError("durable golden root is unavailable")
    try:
        return require_durable_storage_root(Path(configured))
    except ValueError as error:
        message = str(error).replace("storage", "golden")
        raise ValueError(message) from None


def durable_golden_root() -> Path:
    """Resolve the configured corpus root; no worktree-local default exists."""

    return _configured_durable_root(GOLDEN_ROOT_ENV)


def durable_backup_root() -> Path:
    """Resolve a backup root that cannot share the corpus root's deletion."""

    root = _configured_durable_root(GOLDEN_BACKUP_ROOT_ENV)
    corpus = durable_golden_root()
    if root.is_relative_to(corpus) or corpus.is_relative_to(root):
        raise ValueError(
            "durable backup root must not contain or live inside the golden root"
        )
    return root


def require_durable_golden_path(path: Path, *, root: Path) -> None:
    """Refuse corpus paths outside the durable root or inside the repository."""

    if (
        not isinstance(path, Path)
        or ".." in path.parts
        or any(
            candidate.is_symlink()
            for candidate in (path, *path.parents)
            if candidate.exists()
        )
    ):
        raise ValueError("golden corpus path must stay under the durable root")
    resolved = path.resolve(strict=False)
    if (
        not resolved.is_relative_to(root)
        or resolved in (root, REPOSITORY_ROOT)
        or resolved.is_relative_to(REPOSITORY_ROOT)
        or ".context-engine" in resolved.parts
    ):
        raise ValueError("golden corpus path must stay under the durable root")
