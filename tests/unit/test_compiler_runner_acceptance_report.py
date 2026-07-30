from __future__ import annotations

import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import applications.compiler_runner as compiler_runner
from eval.embedding_benchmark import (
    BenchmarkUnavailable,
    validate_json_schema_document,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
REPORT_SCHEMA = (
    REPOSITORY_ROOT / "docs/contracts/compiler-runner-acceptance-v1.schema.json"
)
_PERSONAL_ROOT_PATTERNS = (
    re.compile(r"/" + "Users" + r"/[^/\s]+/"),
    re.compile(r"/" + "home" + r"/[^/\s]+/"),
    re.compile(r"[A-Za-z]:\\(?:Users|Documents and Settings)\\", re.IGNORECASE),
)
_SYNTHETIC_PRIVATE_ROOT_FRAGMENT = "-".join(("synthetic", "corpus", "canary"))
_PRIVACY_BEARING_SCHEMA_WORDS = frozenset(
    {"excerpt", "file", "filename", "path", "root", "source", "text", "title"}
)
_PRIVACY_GUARD_PATHS = (
    Path(__file__),
    REPOSITORY_ROOT / "tests/integration/test_zzz_security_gate_cli_privacy.py",
)


def _schema_property_names(value: object) -> set[str]:
    if type(value) is dict:
        document = value
        names: set[str] = set()
        properties = document.get("properties")
        if type(properties) is dict:
            names.update(str(key).casefold() for key in properties)
        names.update(
            nested
            for item in document.values()
            for nested in _schema_property_names(item)
        )
        return names
    if type(value) is list:
        return {
            nested for item in value for nested in _schema_property_names(item)
        }
    return set()


def _schema_name_words(name: str) -> set[str]:
    return {
        word.casefold()
        for word in re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+",
            name,
        )
    }


def _contains_private_location(value: str) -> bool:
    return any(pattern.search(value) for pattern in _PERSONAL_ROOT_PATTERNS) or (
        _SYNTHETIC_PRIVATE_ROOT_FRAGMENT in value
    )


def test_privacy_guards_do_not_embed_identifier_fingerprints() -> None:
    fingerprint = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")

    assert all(
        fingerprint.search(path.read_text(encoding="utf-8")) is None
        for path in _PRIVACY_GUARD_PATHS
    )


def test_private_location_scan_rejects_synthetic_path_and_fragment_canaries() -> None:
    assert _contains_private_location("/" + "Users" + "/person/corpus")
    assert _contains_private_location(_SYNTHETIC_PRIVATE_ROOT_FRAGMENT)
    assert not _contains_private_location("aggregate-counts-only")


def test_tracked_tree_and_acceptance_schema_cannot_carry_private_paths() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    leaks: list[str] = []
    for raw_path in tracked:
        if not raw_path:
            continue
        path = REPOSITORY_ROOT / raw_path.decode("utf-8")
        content = path.read_text(encoding="utf-8", errors="ignore")
        if _contains_private_location(content):
            leaks.append(path.relative_to(REPOSITORY_ROOT).as_posix())

    schema_text = REPORT_SCHEMA.read_text(encoding="utf-8")
    schema = json.loads(schema_text)
    schema_names = _schema_property_names(schema)
    assert leaks == []
    assert not any(
        _PRIVACY_BEARING_SCHEMA_WORDS.intersection(_schema_name_words(name))
        for name in schema_names
    )
    assert all(
        pattern.search(schema_text) is None
        for pattern in _PERSONAL_ROOT_PATTERNS
    )


def _count_only_report() -> dict[str, object]:
    return {
        "aggregateCompilationDigest": "a" * 64,
        "compilerVersion": "context-engine-markdown-v3",
        "configVersion": "markdown-config-v3",
        "constructHistogram": {
            "atxHeadings": 0,
            "callouts": 0,
            "embeds": 0,
            "fencedCode": 0,
            "footnotes": 0,
            "frontmatter": 0,
            "htmlBlocks": 0,
            "inlineMath": 0,
            "lists": 0,
            "setextHeadings": 0,
            "tables": 0,
            "wikilinks": 0,
        },
        "documents": {
            "accepted": 1,
            "acceptanceRate": "1.000000",
            "refused": 0,
            "total": 1,
        },
        "maxFragmentTokenCount": 1,
        "refusalHistogram": {},
        "schemaVersion": "compiler-runner-acceptance-v1",
        "tokenCeiling": 2048,
    }


def _private_path_shapes() -> tuple[str, ...]:
    slash = chr(47)
    backslash = chr(92)
    return (
        slash.join(("", "Volumes", "private", "entry.md")),
        slash.join(("", "var", "private", "entry.md")),
        slash.join(("~", "private", "entry.md")),
        "Z" + chr(58) + backslash + backslash.join(("private", "entry.md")),
        backslash * 2 + backslash.join(("server", "share", "entry.md")),
        "private-entry.md",
    )


@pytest.mark.parametrize("path_shaped_key", _private_path_shapes())
def test_acceptance_schema_rejects_paths_and_filenames_in_histogram_keys(
    path_shaped_key: str,
) -> None:
    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    report = deepcopy(_count_only_report())
    refusal_histogram = report["refusalHistogram"]
    assert type(refusal_histogram) is dict
    refusal_histogram[path_shaped_key] = 1

    with pytest.raises(BenchmarkUnavailable, match="report schema"):
        validate_json_schema_document(report, schema)


def test_acceptance_report_is_count_only_deterministic_and_written_under_ignore(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "private-corpus"
    corpus.mkdir()
    (corpus / "one.md").write_text("# One\n\nFirst.\n", encoding="utf-8")
    (corpus / "two.md").write_text(
        "# Two\n\n| A | B |\n| --- | --- |\n| x | y |\n",
        encoding="utf-8",
    )
    (corpus / "refused.md").write_text(
        "# Refused\n\n&amp;\n",
        encoding="utf-8",
    )
    output = tmp_path / ".context-engine/compiler-runner-acceptance.json"
    command = [
        sys.executable,
        "-m",
        "applications.compiler_runner",
        "--acceptance-report",
        "--root",
        str(corpus),
        "--output",
        str(output),
    ]

    first = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=30
    )
    first_bytes = output.read_bytes()
    second = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=30
    )

    report = json.loads(first_bytes)
    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    validate_json_schema_document(report, schema)
    assert report["schemaVersion"] == "compiler-runner-acceptance-v1"
    assert report["documents"] == {
        "accepted": 2,
        "acceptanceRate": "0.666667",
        "refused": 1,
        "total": 3,
    }
    assert report["refusalHistogram"] == {
        "unsupported_construct:entity": 1,
    }
    assert report["aggregateCompilationDigest"]
    assert report["maxFragmentTokenCount"] <= report["tokenCeiling"]
    assert report["constructHistogram"]["tables"] == 1
    assert str(corpus) not in first_bytes.decode("utf-8")
    assert "one.md" not in first_bytes.decode("utf-8")
    assert "refused.md" not in first_bytes.decode("utf-8")
    assert first_bytes == output.read_bytes()
    assert first.stdout == second.stdout


def test_acceptance_corpus_matches_file_provider_markdown_directory_rules(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    included = corpus / "included.MD"
    included.write_text("# Included\n", encoding="utf-8")
    ordinary = corpus / "ordinary.md"
    ordinary.write_text("# Ordinary\n", encoding="utf-8")
    target = corpus / "target.md"
    target.write_text("# Target\n", encoding="utf-8")
    (corpus / "linked.md").symlink_to(target)
    external = tmp_path / "external"
    external.mkdir()
    (external / "outside.md").write_text("# Outside\n", encoding="utf-8")
    (corpus / "linked-directory").symlink_to(external, target_is_directory=True)

    discovered = compiler_runner._safe_markdown_files(corpus)

    assert discovered == (included, ordinary, target)


def test_acceptance_corpus_root_must_not_be_a_symlink(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(corpus, target_is_directory=True)

    with pytest.raises(ValueError, match="non-symlink directory"):
        compiler_runner._safe_markdown_files(linked_root)


def test_acceptance_output_must_resolve_beneath_its_state_directory(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    state_directory = tmp_path / ".context-engine"
    state_directory.mkdir()
    escaped_output = state_directory / ".." / "report.json"

    with pytest.raises(ValueError, match="under .context-engine"):
        compiler_runner._write_acceptance_report(
            corpus,
            escaped_output,
            2048,
            acceptance_context=compiler_runner.acceptance_context(),
        )

    assert not (tmp_path / "report.json").exists()


@pytest.mark.parametrize(
    "failure_kind",
    ("io", "permission", "vanished", "directory"),
)
def test_acceptance_cli_error_paths_emit_only_counted_private_safe_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_kind: str,
) -> None:
    corpus = tmp_path / _SYNTHETIC_PRIVATE_ROOT_FRAGMENT
    corpus.mkdir()
    note = corpus / "private-note-canary.md"
    if failure_kind == "directory":
        note.mkdir()
    elif failure_kind != "vanished":
        note.write_text("# Synthetic\n", encoding="utf-8")
    output = tmp_path / ".context-engine/compiler-runner-acceptance.json"
    monkeypatch.setattr(
        compiler_runner,
        "_safe_markdown_files",
        lambda root: (note,),
    )
    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path != note:
            return original_read_bytes(path)
        if failure_kind == "io":
            raise OSError(5, "synthetic I/O failure", str(path))
        if failure_kind == "permission":
            raise PermissionError(13, "synthetic permission failure", str(path))
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compiler-runner",
            "--acceptance-report",
            "--root",
            str(corpus),
            "--output",
            str(output),
        ],
    )

    compiler_runner.main()

    captured = capsys.readouterr()
    emitted = captured.out + captured.err
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["documents"] == {
        "accepted": 0,
        "acceptanceRate": "0.000000",
        "refused": 1,
        "total": 1,
    }
    assert report["refusalHistogram"] == {"unsupported_document_shape": 1}
    assert captured.err == ""
    assert not _contains_private_location(emitted)
    assert str(tmp_path) not in emitted
    assert note.name not in emitted
