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
    record_lineage_check,
)
from engine.learning.golden import (
    GoldenSet,
    GoldenSetUnavailable,
    create_golden_lock,
    load_golden_set,
    relock_golden_set,
)
from engine.learning.golden_storage import (
    durable_golden_root,
    require_durable_golden_path,
)
from engine.learning.governance import (
    PublicSubsetPromotionAuthority,
    _local_public_subset_promotion_authority,
    load_public_subset_governance,
)
from engine.learning.lineage import (
    LineageMapUnavailable,
    LineageResolutionReport,
    StaleGoldenLineage,
    detect_stale_lineage,
    load_lineage_map,
    require_resolved_lineage,
)
from engine.learning.thresholds import DEFAULT_THRESHOLDS_PATH, load_thresholds

PUBLIC_SUBSET_MAINTAINER_SECRET_ENV = (
    "CONTEXT_ENGINE_PUBLIC_SUBSET_MAINTAINER_SECRET"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SUBSET_GOVERNANCE_PATH = (
    REPOSITORY_ROOT / "eval/public-subset-governance.json"
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
    validate.add_argument("--lock", required=True, type=Path)

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

    lineage = commands.add_parser("lineage-check")
    lineage.add_argument("--golden-set", required=True, type=Path)
    lineage.add_argument("--lock", required=True, type=Path)
    lineage.add_argument("--lineage-map", required=True, type=Path)

    report = commands.add_parser("report")
    report.add_argument("--golden-set", required=True, type=Path)
    report.add_argument("--lock", required=True, type=Path)
    report.add_argument("--run", required=True, type=Path)
    report.add_argument("--lineage-map", type=Path)
    report.add_argument("--output", required=True, type=Path)
    report.add_argument("--generated-at", required=True, type=_time)

    execute = commands.add_parser("execute")
    execute.add_argument("--golden-set", required=True, type=Path)
    execute.add_argument("--lock", required=True, type=Path)
    execute.add_argument("--judgments", required=True, type=Path)
    execute.add_argument("--lineage-map", type=Path)
    execute.add_argument("--output", required=True, type=Path)
    execute.add_argument("--generated-at", required=True, type=_time)
    return parser


def _write_report(path: Path, report: dict[str, object]) -> None:
    _require_ignored_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _require_ignored_output(path)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"golden v1 report written: digest={report['reportDigest']}", flush=True)


def _resolved_lineage_check(
    golden_set: GoldenSet,
    lineage_map_path: Path | None,
) -> LineageResolutionReport | None:
    if lineage_map_path is None:
        return None
    lineage_check = detect_stale_lineage(
        golden_set,
        load_lineage_map(lineage_map_path),
    )
    require_resolved_lineage(lineage_check)
    return lineage_check


def _record_lineage_check(
    report: dict[str, object],
    lineage_check: LineageResolutionReport | None,
) -> dict[str, object]:
    if lineage_check is None:
        return report
    return record_lineage_check(report, lineage_check)


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        durable_root = durable_golden_root()
        require_durable_golden_path(args.golden_set, root=durable_root)
        require_durable_golden_path(args.lock, root=durable_root)
        lineage_map_path = getattr(args, "lineage_map", None)
        if lineage_map_path is not None:
            require_durable_golden_path(lineage_map_path, root=durable_root)
        judgments_path = getattr(args, "judgments", None)
        if judgments_path is not None:
            require_durable_golden_path(judgments_path, root=durable_root)
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
            print(f"golden pilot locked: digest={golden_set.pilot_digest}", flush=True)
            return
        if args.command == "relock":
            pilot_digest = relock_golden_set(
                args.golden_set,
                args.lock,
                authority=args.authority,
                reason=args.reason,
                recorded_at=args.recorded_at,
            )
            print(
                f"golden pilot re-locked: digest={pilot_digest}",
                flush=True,
            )
            return
        if args.command == "lineage-check":
            golden_set = load_golden_set(args.golden_set, lock_path=args.lock)
            resolution = detect_stale_lineage(
                golden_set,
                load_lineage_map(args.lineage_map),
            )
            require_resolved_lineage(resolution)
            print(
                f"golden lineage resolved: {resolution.resolved_case_count} cases "
                f"mapDigest={resolution.map_digest}",
                flush=True,
            )
            return
        if args.command == "report":
            golden_set = load_golden_set(args.golden_set, lock_path=args.lock)
            lineage_check = _resolved_lineage_check(
                golden_set,
                lineage_map_path,
            )
            report = build_evaluation_report(
                golden_set,
                load_evaluation_run(args.run),
                load_thresholds(DEFAULT_THRESHOLDS_PATH),
                generated_at=args.generated_at,
            )
            _write_report(
                args.output,
                _record_lineage_check(report, lineage_check),
            )
            return
        if args.command == "execute":
            from applications.eval_executor import (
                execute_evaluation_report,
                load_answer_judgments,
            )

            golden_set = load_golden_set(args.golden_set, lock_path=args.lock)
            lineage_check = _resolved_lineage_check(
                golden_set,
                lineage_map_path,
            )
            report = execute_evaluation_report(
                golden_set,
                load_answer_judgments(args.judgments),
                load_thresholds(DEFAULT_THRESHOLDS_PATH),
                generated_at=args.generated_at,
            )
            _write_report(
                args.output,
                _record_lineage_check(report, lineage_check),
            )
            return
    except (
        GoldenSetUnavailable,
        EvaluationRunUnavailable,
        LineageMapUnavailable,
        StaleGoldenLineage,
        ValueError,
    ) as error:
        parser.exit(1, f"golden v1 evaluation unavailable: {error}\n")
    raise AssertionError("closed parser returned an unknown command")


if __name__ == "__main__":
    main()
