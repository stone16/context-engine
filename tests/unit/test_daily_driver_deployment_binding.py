from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.daily_driver import deployment
from scripts.daily_driver.deployment import DeploymentBindingRefused


def _ready_manifest(checkout: Path) -> Path:
    manifest = checkout / ".context-engine" / deployment.READY_DEPLOYMENT_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "codeRevision": "a" * 40,
                "labelPrefix": "org.example.context-engine",
                "plists": ["org.example.context-engine.api.plist"],
                "schemaStateDigest": "sha256:" + "b" * 64,
                "schemaVersion": 2,
                "status": "ready",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    return manifest


def test_ready_binding_accepts_exact_code_and_live_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    manifest = _ready_manifest(checkout)
    monkeypatch.setattr(deployment, "current_code_revision", lambda _path: "a" * 40)
    monkeypatch.setattr(
        deployment,
        "require_exact_schema_binding",
        lambda _environment: "sha256:" + "b" * 64,
    )

    deployment.verify_ready_deployment(
        checkout=checkout,
        database_environment={"SYNTHETIC": "private"},
    )

    assert manifest.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("codeRevision", "c" * 40),
        ("codeRevision", "z" * 40),
        ("schemaStateDigest", "sha256:" + "d" * 64),
        ("schemaVersion", 1),
        ("status", "preparing"),
    ),
)
def test_ready_binding_refuses_stale_or_incomplete_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    checkout = tmp_path / "checkout"
    manifest = _ready_manifest(checkout)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document[field] = value
    manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
    manifest.chmod(0o600)
    monkeypatch.setattr(deployment, "current_code_revision", lambda _path: "a" * 40)
    monkeypatch.setattr(
        deployment,
        "require_exact_schema_binding",
        lambda _environment: "sha256:" + "b" * 64,
    )

    with pytest.raises(DeploymentBindingRefused):
        deployment.verify_ready_deployment(
            checkout=checkout,
            database_environment={"SYNTHETIC": "private"},
        )


def test_ready_binding_refuses_an_unsafe_owner_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    manifest = _ready_manifest(checkout)
    manifest.chmod(0o644)
    monkeypatch.setattr(
        deployment,
        "current_code_revision",
        lambda _path: pytest.fail("unsafe manifest must refuse before Git"),
    )

    with pytest.raises(DeploymentBindingRefused):
        deployment.verify_ready_deployment(
            checkout=checkout,
            database_environment={"SYNTHETIC": "private"},
        )


def test_ready_binding_refusal_never_contains_private_inputs(
    tmp_path: Path,
) -> None:
    private = "PRIVATE_SECRET_OR_PATH_MUST_NOT_LEAK"
    checkout = tmp_path / private

    with pytest.raises(DeploymentBindingRefused) as failure:
        deployment.verify_ready_deployment(
            checkout=checkout,
            database_environment={"SYNTHETIC": private},
        )

    assert private not in str(failure.value)
