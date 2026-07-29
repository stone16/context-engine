"""Offline golden-set v1 validation, locking, and layered report CLI."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from engine.learning.eval_run import (
    EvaluationRunUnavailable,
    build_evaluation_report,
    load_evaluation_run,
)
from engine.learning.golden import (
    GoldenSetUnavailable,
    create_golden_lock,
    load_golden_set,
    relock_golden_set,
)
from engine.learning.governance import (
    PublicSubsetPromotionAuthority,
    _local_public_subset_promotion_authority,
    load_public_subset_governance,
)
from engine.learning.thresholds import DEFAULT_THRESHOLDS_PATH, load_thresholds

PUBLIC_SUBSET_MAINTAINER_SECRET_ENV = (
    "CONTEXT_ENGINE_PUBLIC_SUBSET_MAINTAINER_SECRET"
)
PUBLIC_SUBSET_GOVERNANCE_PATH = (
    Path(__file__).resolve().parents[1] / "eval/public-subset-governance.json"
)


def load_local_public_subset_promotion_authority() -> PublicSubsetPromotionAuthority:
    """Compose the fixed local maintainer verifier from the process environment."""

    try:
        raw = os.environ[PUBLIC_SUBSET_MAINTAINER_SECRET_ENV]
        credential = raw.encode("utf-8")
    except (KeyError, UnicodeEncodeError):
        raise ValueError(
            "public subset maintainer authentication is unavailable"
        ) from None
    if (
        len(credential) < 32
        or raw != raw.strip()
        or any(character.isspace() for character in raw)
    ):
        raise ValueError("public subset maintainer authentication is unavailable")
    return _local_public_subset_promotion_authority(
        load_public_subset_governance(PUBLIC_SUBSET_GOVERNANCE_PATH),
        credential,
    )


def _require_ignored_output(path: Path) -> None:
    if not isinstance(path, Path):
        raise TypeError("evaluation output path must be Path")
    if ".." in path.parts:
        raise ValueError(
            "evaluation output must stay under an ignored .context-engine directory"
        )
    candidates = tuple(
        parent for parent in (path, *path.parents) if parent.name == ".context-engine"
    )
    if len(candidates) != 1:
        raise ValueError(
            "evaluation output must stay under an ignored .context-engine directory"
        )
    ignored_root = candidates[0]
    if not ignored_root.exists():
        ignored_root.mkdir()
    if (
        not ignored_root.is_dir()
        or ignored_root.is_symlink()
    ):
        raise ValueError(
            "evaluation output must stay under an ignored .context-engine directory"
        )
    resolved_root = ignored_root.resolve(strict=True)
    resolved_output = path.resolve(strict=False)
    if resolved_output == resolved_root or not resolved_output.is_relative_to(
        resolved_root
    ):
        raise ValueError(
            "evaluation output must stay under an ignored .context-engine directory"
        )


def _time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("time must be ISO-8601 aware UTC") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and judge golden set v1")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--golden-set", required=True, type=Path)
    validate.add_argument("--lock", type=Path)

    lock = commands.add_parser("lock")
    lock.add_argument("--golden-set", required=True, type=Path)
    lock.add_argument("--lock", required=True, type=Path)
    lock.add_argument("--authority", required=True)
    lock.add_argument("--reason", required=True)
    lock.add_argument("--recorded-at", required=True, type=_time)

    relock = commands.add_parser("relock")
    relock.add_argument("--golden-set", required=True, type=Path)
    relock.add_argument("--lock", required=True, type=Path)
    relock.add_argument("--authority", required=True)
    relock.add_argument("--reason", required=True)
    relock.add_argument("--recorded-at", required=True, type=_time)

    report = commands.add_parser("report")
    report.add_argument("--golden-set", required=True, type=Path)
    report.add_argument("--lock", required=True, type=Path)
    report.add_argument("--run", required=True, type=Path)
    report.add_argument("--output", required=True, type=Path)
    report.add_argument("--generated-at", required=True, type=_time)
    return parser


def _write_report(path: Path, report: dict[str, object]) -> None:
    _require_ignored_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _require_ignored_output(path)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"golden v1 report written: {path}", flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            golden_set = load_golden_set(args.golden_set, lock_path=args.lock)
            print(
                f"golden v1 valid: {len(golden_set.cases)} cases "
                f"digest={golden_set.digest}",
                flush=True,
            )
            return
        if args.command == "lock":
            golden_set = load_golden_set(
                args.golden_set,
                allow_unlocked_pilot_for_initial_lock=True,
            )
            create_golden_lock(
                golden_set,
                args.lock,
                authority=args.authority,
                reason=args.reason,
                recorded_at=args.recorded_at,
            )
            print(f"golden pilot locked: {args.lock}", flush=True)
            return
        if args.command == "relock":
            relock_golden_set(
                args.golden_set,
                args.lock,
                authority=args.authority,
                reason=args.reason,
                recorded_at=args.recorded_at,
            )
            print(f"golden pilot re-locked: {args.lock}", flush=True)
            return
        if args.command == "report":
            golden_set = load_golden_set(args.golden_set, lock_path=args.lock)
            report = build_evaluation_report(
                golden_set,
                load_evaluation_run(args.run),
                load_thresholds(DEFAULT_THRESHOLDS_PATH),
                generated_at=args.generated_at,
            )
            _write_report(args.output, report)
            return
    except (GoldenSetUnavailable, EvaluationRunUnavailable, ValueError) as error:
        parser.exit(1, f"golden v1 evaluation unavailable: {error}\n")
    raise AssertionError("closed parser returned an unknown command")


if __name__ == "__main__":
    main()
