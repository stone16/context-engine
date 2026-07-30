"""Run daily-driver setup by absolute path while outside every worktree."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.daily_driver.setup import main as _main  # noqa: E402, I001


if __name__ == "__main__":
    raise SystemExit(_main())
