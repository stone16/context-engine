from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.learning.backup import (
    GoldenBackupUnavailable,
    create_backup,
    latest_snapshot,
    recover_backup,
)
from engine.learning.golden import load_golden_set
from engine.learning.lineage import (
    detect_stale_lineage,
    load_lineage_map,
    require_resolved_lineage,
)
from tests.support.golden_backup import StagedCorpus, stage_corpus

RECORDED_AT = datetime(2026, 7, 29, 12, tzinfo=UTC)
LATER = datetime(2026, 7, 30, 12, tzinfo=UTC)


def _staged(tmp_path: Path) -> tuple[StagedCorpus, Path]:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    return stage_corpus(tmp_path / "corpus"), backup_root


def _lose_the_working_copy(source_root: Path) -> None:
    shutil.rmtree(source_root)
    source_root.mkdir()


def test_backup_delete_and_recover_restores_an_identical_locked_corpus(
    tmp_path: Path,
) -> None:
    corpus, backup_root = _staged(tmp_path)
    before = load_golden_set(corpus.golden_path, lock_path=corpus.lock_path)
    outcome = create_backup(corpus.source_root, backup_root, recorded_at=RECORDED_AT)

    _lose_the_working_copy(corpus.source_root)
    recovered = recover_backup(backup_root / outcome.snapshot, corpus.source_root)
    after = load_golden_set(corpus.golden_path, lock_path=corpus.lock_path)

    assert recovered.content_digest == outcome.content_digest
    assert recovered.file_count == outcome.file_count
    assert len(after.cases) == len(before.cases)
    assert after.digest == before.digest
    assert after.pilot_digest == before.pilot_digest


def test_a_recovered_corpus_still_resolves_its_recorded_lineage(
    tmp_path: Path,
) -> None:
    corpus, backup_root = _staged(tmp_path)
    outcome = create_backup(corpus.source_root, backup_root, recorded_at=RECORDED_AT)

    _lose_the_working_copy(corpus.source_root)
    recover_backup(backup_root / outcome.snapshot, corpus.source_root)
    resolution = detect_stale_lineage(
        load_golden_set(corpus.golden_path, lock_path=corpus.lock_path),
        load_lineage_map(corpus.lineage_map_path),
    )

    require_resolved_lineage(resolution)
    assert resolution.status == "resolved"
    assert resolution.resolved_case_count == 70


def test_a_recovered_corpus_reproduces_the_recorded_content_digest(
    tmp_path: Path,
) -> None:
    corpus, backup_root = _staged(tmp_path)
    outcome = create_backup(corpus.source_root, backup_root, recorded_at=RECORDED_AT)
    second_root = tmp_path / "second-backups"
    second_root.mkdir()

    _lose_the_working_copy(corpus.source_root)
    recover_backup(backup_root / outcome.snapshot, corpus.source_root)
    repeated = create_backup(corpus.source_root, second_root, recorded_at=LATER)

    assert repeated.content_digest == outcome.content_digest
    assert repeated.file_count == outcome.file_count
    assert repeated.total_bytes == outcome.total_bytes


def test_recovery_refuses_a_nonempty_destination_instead_of_overwriting_it(
    tmp_path: Path,
) -> None:
    corpus, backup_root = _staged(tmp_path)
    outcome = create_backup(corpus.source_root, backup_root, recorded_at=RECORDED_AT)
    surviving = corpus.golden_path.read_bytes()

    with pytest.raises(GoldenBackupUnavailable, match="empty"):
        recover_backup(backup_root / outcome.snapshot, corpus.source_root)

    assert corpus.golden_path.read_bytes() == surviving


def test_recovery_restores_the_newest_snapshot_by_default(tmp_path: Path) -> None:
    corpus, backup_root = _staged(tmp_path)
    create_backup(corpus.source_root, backup_root, recorded_at=RECORDED_AT)
    (corpus.source_root / "judge-observations.json").write_text(
        '{"note": "synthetic-observation"}',
        encoding="utf-8",
    )
    newest = create_backup(corpus.source_root, backup_root, recorded_at=LATER)

    _lose_the_working_copy(corpus.source_root)
    selected = latest_snapshot(backup_root)
    assert selected is not None
    recovered = recover_backup(backup_root / selected, corpus.source_root)

    assert selected == newest.snapshot
    assert recovered.content_digest == newest.content_digest
    assert (corpus.source_root / "judge-observations.json").is_file()
