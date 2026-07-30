from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest

from applications import golden_backup
from applications.golden_backup import main
from engine.learning import backup as backup_module
from engine.learning.golden_storage import (
    GOLDEN_BACKUP_ROOT_ENV,
    GOLDEN_ROOT_ENV,
)
from tests.support.golden_backup import stage_corpus

MARKER: Final = "privatemarkerz"
MARKED_FILE: Final = f"{MARKER}-observations.json"
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
# Split so this file can scan itself without matching its own needles.
PERSONAL_PATH_MARKERS: Final = tuple(
    "".join(parts)
    for parts in (
        ("/Us", "ers/"),
        ("/ho", "me/"),
        ("$HO", "ME"),
        ("~", "/"),
        ("C:", "\\"),
    )
)
TRACKED_SOURCES: Final = (
    "applications/eval_v1.py",
    "applications/golden_backup.py",
    "engine/learning/backup.py",
    "engine/learning/golden_storage.py",
    "engine/learning/lineage.py",
    "eval/README.md",
    "docs/decisions/0082-recover-the-golden-corpus-and-refuse-stale-lineage.md",
    "tests/support/golden_backup.py",
    "tests/unit/test_backup_permissions.py",
    "tests/unit/test_backup_privacy.py",
    "tests/unit/test_golden_backup_idempotent.py",
    "tests/unit/test_golden_backup_integrity.py",
    "tests/unit/test_golden_recovery_roundtrip.py",
    "tests/unit/test_stale_lineage_detector.py",
)


@pytest.fixture
def marked_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    source_root = tmp_path / "corpus"
    backup_root = tmp_path / "backups"
    stage_corpus(source_root, marker=MARKER)
    (source_root / MARKED_FILE).write_text(
        json.dumps({"note": f"{MARKER}-observation"}),
        encoding="utf-8",
    )
    backup_root.mkdir()
    monkeypatch.setenv(GOLDEN_ROOT_ENV, str(source_root))
    monkeypatch.setenv(GOLDEN_BACKUP_ROOT_ENV, str(backup_root))
    return source_root, backup_root


def _assert_content_free(captured: str) -> None:
    assert MARKER not in captured
    assert MARKED_FILE not in captured
    for personal in PERSONAL_PATH_MARKERS:
        assert personal not in captured


def test_no_backup_command_prints_corpus_content_paths_or_filenames(
    marked_roots: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root, _ = marked_roots

    main(["backup", "--recorded-at", "2026-07-29T12:00:00Z"])
    main(["list"])
    main(["verify"])
    for path in sorted(source_root.iterdir()):
        path.unlink()
    main(["recover"])
    captured = capsys.readouterr()

    _assert_content_free(captured.out + captured.err)
    assert "golden backup created" in captured.out
    assert "golden backup recovered" in captured.out


def test_a_refused_backup_never_prints_the_operating_system_path(
    marked_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _fail(path: Path, data: bytes) -> None:
        raise OSError(f"cannot write {path}")

    monkeypatch.setattr(backup_module, "_write_private_file", _fail)

    with pytest.raises(SystemExit) as error:
        main(["backup", "--recorded-at", "2026-07-29T12:00:00Z"])

    captured = capsys.readouterr()
    assert error.value.code == 1
    _assert_content_free(captured.out + captured.err)


def test_a_corrupted_backup_refusal_never_prints_the_affected_filename(
    marked_roots: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, backup_root = marked_roots
    main(["backup", "--recorded-at", "2026-07-29T12:00:00Z"])
    corrupted = backup_root / "20260729T120000Z" / MARKED_FILE
    corrupted.chmod(0o600)
    corrupted.write_bytes(b"corrupted")
    corrupted.chmod(0o600)

    with pytest.raises(SystemExit) as error:
        main(["verify"])

    captured = capsys.readouterr()
    assert error.value.code == 1
    assert "golden backup unavailable" in captured.err
    _assert_content_free(captured.out + captured.err)


@pytest.mark.parametrize(
    "argv",
    (
        ("backup", "--source-root", "/tmp/corpus"),
        ("backup", "--golden-set", "/tmp/corpus/golden.json"),
        ("verify", "--backup-root", "/tmp/backups"),
        ("recover", "--into", "/tmp/corpus"),
    ),
)
def test_no_command_accepts_a_corpus_path_argument(
    marked_roots: tuple[Path, Path],
    argv: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit) as error:
        main([*argv])

    assert error.value.code == 2


def test_no_worktree_local_default_replaces_the_configured_durable_roots(
    marked_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(GOLDEN_BACKUP_ROOT_ENV)

    with pytest.raises(SystemExit) as error:
        main(["backup", "--recorded-at", "2026-07-29T12:00:00Z"])

    captured = capsys.readouterr()
    assert error.value.code == 1
    assert "unavailable" in captured.err
    _assert_content_free(captured.out + captured.err)


def test_tracked_sources_declare_no_personal_path_default() -> None:
    for relative in TRACKED_SOURCES:
        path = REPOSITORY_ROOT / relative
        assert path.is_file(), relative
        text = path.read_text(encoding="utf-8")
        for personal in PERSONAL_PATH_MARKERS:
            assert personal not in text, f"{relative} discloses a personal path"


def test_the_backup_module_names_no_corpus_location(
    marked_roots: tuple[Path, Path],
) -> None:
    source_root, backup_root = marked_roots
    text = (REPOSITORY_ROOT / "engine/learning/backup.py").read_text(encoding="utf-8")

    assert str(source_root) not in text
    assert str(backup_root) not in text
    assert golden_backup.__doc__ is not None
    _assert_content_free(golden_backup.__doc__)
