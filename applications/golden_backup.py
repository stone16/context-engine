"""Back up, verify, and recover the private golden corpus.

Every root comes from the configured durable environment contract, so no
command line, transcript, or log line can carry a corpus path. Output is
counts, snapshot instants, and digests only.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime

from engine.learning.backup import (
    BackupVerification,
    GoldenBackupUnavailable,
    create_backup,
    latest_snapshot,
    read_manifest,
    recover_backup,
    snapshot_names,
    verify_backup,
)
from engine.learning.golden_storage import durable_backup_root, durable_golden_root


def _time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("time must be ISO-8601 aware UTC") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Back up and recover the durable golden corpus",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--recorded-at", required=True, type=_time)
    backup.add_argument("--allow-older", action="store_true")

    verify = commands.add_parser("verify")
    verify.add_argument("--snapshot")

    recover = commands.add_parser("recover")
    recover.add_argument("--snapshot")

    commands.add_parser("list")
    return parser


def _report(action: str, verification: BackupVerification) -> None:
    print(
        f"golden backup {action}: snapshot={verification.snapshot} "
        f"files={verification.file_count} bytes={verification.total_bytes} "
        f"digest={verification.content_digest}",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        backup_root = durable_backup_root()
        if args.command == "backup":
            outcome = create_backup(
                durable_golden_root(),
                backup_root,
                recorded_at=args.recorded_at,
                allow_older=args.allow_older,
            )
            print(
                f"golden backup {outcome.status}: snapshot={outcome.snapshot} "
                f"files={outcome.file_count} bytes={outcome.total_bytes} "
                f"digest={outcome.content_digest}",
                flush=True,
            )
            return
        if args.command == "list":
            for name in snapshot_names(backup_root):
                manifest = read_manifest(backup_root / name)
                print(
                    f"{name} files={len(manifest.files)} "
                    f"bytes={manifest.total_bytes} "
                    f"digest={manifest.content_digest}",
                    flush=True,
                )
            return
        selected = args.snapshot or latest_snapshot(backup_root)
        if selected is None:
            raise GoldenBackupUnavailable("no recorded backup snapshot exists")
        if selected not in snapshot_names(backup_root):
            raise GoldenBackupUnavailable("backup snapshot is not recorded")
        if args.command == "verify":
            _report("verified", verify_backup(backup_root / selected))
            return
        if args.command == "recover":
            _report(
                "recovered",
                recover_backup(backup_root / selected, durable_golden_root()),
            )
            return
    except (GoldenBackupUnavailable, ValueError) as error:
        parser.exit(1, f"golden backup unavailable: {error}\n")
    raise AssertionError("closed parser returned an unknown command")


if __name__ == "__main__":
    main()
