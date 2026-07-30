from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.third_party_governance import (
    ArtifactKind,
    Registration,
    inspect_all_artifacts,
    inspect_artifact,
    required_artifact_texts,
    validate_tree,
)
from tests.unit._third_party_governance_fixtures import write_fixture_tree

SCHEMA = Path(__file__).parents[2] / "schemas/third-party-upstream.schema.json"


def _evidence(root: Path, registrations: tuple[Registration, ...]) -> dict[str, bytes]:
    return {
        name: (root / name).read_bytes()
        for name in required_artifact_texts(registrations)
    }


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def _archive(path: Path, kind: ArtifactKind, files: dict[str, bytes]) -> None:
    if kind == "wheel":
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return
    if kind == "container":
        layer = _tar_bytes({f"app/{name}": content for name, content in files.items()})
        with tarfile.open(path, "w") as archive:
            info = tarfile.TarInfo("layer-1/layer.tar")
            info.size = len(layer)
            archive.addfile(info, io.BytesIO(layer))
        return
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            prefixed = f"package/{name}" if kind == "npm" else f"context-engine/{name}"
            info = tarfile.TarInfo(prefixed)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [("wheel", ".whl"), ("sdist", ".tar.gz"), ("npm", ".tgz"), ("container", ".tar")],
)
def test_each_artifact_kind_fails_when_evidence_is_missing(
    tmp_path: Path, kind: ArtifactKind, suffix: str
) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    registrations = validate_tree(tmp_path)
    evidence = _evidence(tmp_path, registrations)
    evidence.pop("third_party/example/LICENSE.upstream")
    artifact = tmp_path / f"artifact{suffix}"
    _archive(artifact, kind, evidence)

    status = inspect_artifact(kind, artifact, registrations, root=tmp_path)

    assert not status.passed
    assert "LICENSE.upstream" in status.detail


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [("wheel", ".whl"), ("sdist", ".tar.gz"), ("npm", ".tgz"), ("container", ".tar")],
)
def test_each_artifact_kind_passes_with_physical_evidence(
    tmp_path: Path, kind: ArtifactKind, suffix: str
) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    registrations = validate_tree(tmp_path)
    artifact = tmp_path / f"artifact{suffix}"
    _archive(artifact, kind, _evidence(tmp_path, registrations))
    assert inspect_artifact(kind, artifact, registrations, root=tmp_path).passed


def test_git_only_attribution_does_not_satisfy_artifact_check(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    registrations = validate_tree(tmp_path)
    artifact = tmp_path / "artifact.whl"
    _archive(artifact, "wheel", {"application.py": b"pass\n"})
    status = inspect_artifact("wheel", artifact, registrations, root=tmp_path)
    assert not status.passed
    assert "missing distributed evidence" in status.detail


def test_stale_attribution_does_not_satisfy_artifact_check(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    registrations = validate_tree(tmp_path)
    evidence = _evidence(tmp_path, registrations)
    evidence["THIRD_PARTY_NOTICES.md"] = b"stale\n"
    artifact = tmp_path / "artifact.whl"
    _archive(artifact, "wheel", evidence)
    status = inspect_artifact("wheel", artifact, registrations, root=tmp_path)
    assert not status.passed
    assert "differs from repository" in status.detail


def test_absent_artifact_kinds_are_explicit_failures(tmp_path: Path) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    registrations = validate_tree(tmp_path)
    statuses = inspect_all_artifacts({}, registrations, root=tmp_path)
    assert [status.kind for status in statuses] == [
        "wheel",
        "sdist",
        "npm",
        "container",
    ]
    assert all(not status.passed for status in statuses)
    assert all(status.detail == "artifact kind not produced" for status in statuses)
