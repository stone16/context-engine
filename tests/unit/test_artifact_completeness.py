from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.third_party_governance import (
    ArtifactKind,
    GovernanceError,
    Registration,
    inspect_all_artifacts,
    inspect_artifact,
    load_artifact_exemptions,
    report_artifact_statuses,
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


def _write_container_exemption(root: Path, *, include_approval: bool = True) -> None:
    approval = (
        'approval_reference = "stone16/context-engine#142 maintainer decision '
        '2026-07-30"\n'
        if include_approval
        else ""
    )
    (root / "third_party/ARTIFACT_EXEMPTIONS.toml").write_text(
        "schema_version = 1\n\n"
        "[[exemptions]]\n"
        'record_type = "EXEMPTION"\n'
        'artifact_kind = "container"\n'
        'disposition = "not-produced-by-maintainer-decision"\n'
        f"{approval}",
        encoding="utf-8",
    )


def _non_container_artifacts(
    root: Path,
    registrations: tuple[Registration, ...],
) -> dict[ArtifactKind, Path | None]:
    artifacts: dict[ArtifactKind, Path | None] = {}
    artifact_specs: tuple[tuple[ArtifactKind, str], ...] = (
        ("wheel", ".whl"),
        ("sdist", ".tar.gz"),
        ("npm", ".tgz"),
    )
    for kind, suffix in artifact_specs:
        artifact = root / f"artifact{suffix}"
        _archive(artifact, kind, _evidence(root, registrations))
        artifacts[kind] = artifact
    artifacts["container"] = None
    return artifacts


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


def test_container_exemption_reports_not_produced_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    _write_container_exemption(tmp_path)
    registrations = validate_tree(tmp_path)
    statuses = inspect_all_artifacts(
        _non_container_artifacts(tmp_path, registrations),
        registrations,
        root=tmp_path,
        exemptions=load_artifact_exemptions(tmp_path),
    )

    exit_code = report_artifact_statuses(statuses)

    assert exit_code == 0
    assert "container: NOT_PRODUCED (maintainer decision)" in capsys.readouterr().out


def test_unproduced_container_without_exemption_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    registrations = validate_tree(tmp_path)
    statuses = inspect_all_artifacts(
        _non_container_artifacts(tmp_path, registrations),
        registrations,
        root=tmp_path,
        exemptions=load_artifact_exemptions(tmp_path),
    )

    exit_code = report_artifact_statuses(statuses)

    assert exit_code == 1
    assert "container: FAIL — artifact kind not produced" in capsys.readouterr().out


def test_exemption_without_approval_reference_fails_schema_validation(
    tmp_path: Path,
) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    _write_container_exemption(tmp_path, include_approval=False)

    with pytest.raises(GovernanceError, match="approval_reference"):
        load_artifact_exemptions(tmp_path)


def test_exemption_does_not_cover_a_produced_container_missing_evidence(
    tmp_path: Path,
) -> None:
    write_fixture_tree(tmp_path, SCHEMA)
    _write_container_exemption(tmp_path)
    registrations = validate_tree(tmp_path)
    artifact = tmp_path / "container.tar"
    evidence = _evidence(tmp_path, registrations)
    evidence.pop("THIRD_PARTY_SBOM.cyclonedx.json")
    _archive(artifact, "container", evidence)

    status = inspect_artifact(
        "container",
        artifact,
        registrations,
        root=tmp_path,
        exemption=load_artifact_exemptions(tmp_path)["container"],
    )

    assert not status.passed
    assert not status.exempted
    assert "THIRD_PARTY_SBOM.cyclonedx.json" in status.detail
