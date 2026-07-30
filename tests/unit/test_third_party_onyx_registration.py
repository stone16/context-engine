from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from collections import Counter
from pathlib import Path

import pytest

from adapters.connectors.file import FileConnectorAdapter
from engine.supply import ConnectorCheckpointBinding
from tests.support.file_connector_twin import SyntheticVaultTwin
from third_party.onyx.connectors.connector_runner import ConnectorRunner

REPOSITORY_ROOT = Path(__file__).parents[2]
REGISTRATION_ROOT = REPOSITORY_ROOT / "third_party/onyx"
REGISTRATION_PATH = REGISTRATION_ROOT / "UPSTREAM.toml"
PINNED_COMMIT = "2fb3dd10493b3883870fa8adced5b1a0e114feff"
REQUIRED_SOURCE_PATHS = {
    "backend/onyx/connectors/interfaces.py",
    "backend/onyx/connectors/connector_runner.py",
    "backend/onyx/connectors/models.py",
    "backend/onyx/connectors/registry.py",
}
REQUIRED_EXCLUSIONS = {"backend/ee", "web/src/app/ee", "web/src/ee"}
UPSTREAM_SHA256 = {
    "backend/onyx/connectors/interfaces.py": (
        "293c0dcca9230b75ea3eef1475262e0b4010ca4df9321880f41a9dad05561756"
    ),
    "backend/onyx/connectors/connector_runner.py": (
        "dc41c82425287c039b0897c135bc45f520eeb88be9b2ef16df159f835a63f311"
    ),
    "backend/onyx/connectors/models.py": (
        "8edcf633de61d2c769c0959ce744e9efae436083917b7ddb1d692f89eaa4f44b"
    ),
    "backend/onyx/connectors/registry.py": (
        "439c49bcb7dcc522545176d015dde73ce93d991e4b3e875db4b4d23d94cad9c4"
    ),
}
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "abc",
    "collections",
    "dataclasses",
    "enum",
    "types",
    "typing",
    "third_party",
}


def _registration() -> dict[str, object]:
    return tomllib.loads(REGISTRATION_PATH.read_text(encoding="utf-8"))


def test_vendored_bytes_match_complete_pinned_registration() -> None:
    registration = _registration()

    assert registration["repository"] == "https://github.com/onyx-dot-app/onyx.git"
    commit = registration["commit"]
    assert isinstance(commit, str)
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    assert commit == PINNED_COMMIT
    assert registration["reuse_mode"] == "copy-patch"
    assert registration["license"] == "MIT"
    assert registration["approval"] == (
        "https://github.com/stone16/context-engine/issues/126"
    )
    source_paths = registration["source_paths"]
    excluded_paths = registration["excluded_paths"]
    assert isinstance(source_paths, list)
    assert isinstance(excluded_paths, list)
    assert set(source_paths) == REQUIRED_SOURCE_PATHS
    assert set(excluded_paths) >= REQUIRED_EXCLUSIONS

    files = registration["files"]
    assert isinstance(files, list) and files
    registered_paths: set[Path] = set()
    for entry in files:
        assert isinstance(entry, dict)
        assert set(entry) == {
            "upstream_path",
            "vendored_path",
            "sha256",
        }
        upstream_path = entry["upstream_path"]
        vendored_path = entry["vendored_path"]
        assert isinstance(upstream_path, str)
        assert isinstance(vendored_path, str)
        assert "ee" not in Path(upstream_path).parts
        assert "ee" not in Path(vendored_path).parts
        expected_hash = entry["sha256"]
        assert isinstance(expected_hash, str)
        assert re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        path = REPOSITORY_ROOT / vendored_path
        path.relative_to(REGISTRATION_ROOT)
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
        registered_paths.add(path)

    vendored_files = {
        path
        for path in REGISTRATION_ROOT.rglob("*.py")
        if "__pycache__" not in path.relative_to(REGISTRATION_ROOT).parts
    }
    assert registered_paths == vendored_files
    assert (REGISTRATION_ROOT / "LICENSE.upstream").is_file()
    assert hashlib.sha256(
        (REGISTRATION_ROOT / "LICENSE.upstream").read_bytes()
    ).hexdigest() == "d4847240794058c7ac3cfdf8e5d528fe8b0edf15b32a96612ecb9b3e182092b7"
    assert (REGISTRATION_ROOT / "MODIFICATIONS.md").is_file()
    modifications = (REGISTRATION_ROOT / "MODIFICATIONS.md").read_text(
        encoding="utf-8"
    )
    for upstream_path, upstream_sha256 in UPSTREAM_SHA256.items():
        assert f"`{upstream_path}`" in modifications
        assert f"`{upstream_sha256}`" in modifications
    assert (REGISTRATION_ROOT / "patches").is_dir()
    notices = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )
    assert "## onyx" in notices
    assert f"- Commit: `{PINNED_COMMIT}`" in notices
    assert "- License: MIT (`third_party/onyx/LICENSE.upstream`)" in notices
    sbom = json.loads(
        (REGISTRATION_ROOT / "sbom.cyclonedx.json").read_text(encoding="utf-8")
    )
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["metadata"]["component"]["bom-ref"] == (
        "context-engine:third-party:onyx"
    )
    assert {component["name"] for component in sbom["components"]} == {
        "Onyx connector framework"
    }


def test_vendored_subtree_imports_only_approved_dependencies() -> None:
    registration = _registration()
    files = registration["files"]
    assert isinstance(files, list)

    imports: set[str] = set()
    for entry in files:
        assert isinstance(entry, dict)
        path = REPOSITORY_ROOT / str(entry["vendored_path"])
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module is not None
                imports.add(node.module.partition(".")[0])

    assert imports <= ALLOWED_IMPORT_ROOTS
    assert imports.isdisjoint(
        {"alembic", "celery", "onyx", "psycopg", "redis", "sqlalchemy"}
    )


def test_registered_runner_region_is_executed_by_file_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    twin = SyntheticVaultTwin({"notes/alpha.md": b"# Alpha\n"})
    binding = ConnectorCheckpointBinding(
        organization_id=twin.organization_id,
        source_version_id=twin.source_version_id,
        worker_job_id=twin.worker_job_id,
    )
    calls: Counter[str] = Counter()
    original = ConnectorRunner.run

    def recording_run(self: ConnectorRunner) -> object:
        calls["run"] += 1
        return original(self)

    monkeypatch.setattr(ConnectorRunner, "run", recording_run)
    adapter = FileConnectorAdapter.from_twin(twin)
    adapter.load_checkpoint(None)

    page = adapter.load(binding)

    assert page.documents
    assert calls == {"run": 1}
