from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.http.app import create_app
from scripts.freeze_openapi import render_openapi_snapshot

ROOT = Path(__file__).parents[2]


def test_v1_is_a_separate_public_contract_with_cumulative_package_identity() -> None:
    schema = TestClient(
        create_app(public_contract_version="v1")
    ).get("/openapi.json").json()

    assert schema["info"]["version"] == "1.0.0"
    assert set(schema["paths"]) == {"/v1/resolve"}
    operation = schema["paths"]["/v1/resolve"]["post"]
    assert operation["operationId"] == "resolveContextV1"
    package = schema["components"]["schemas"]["ContextPackageV1Wire"]
    assert "tokenizerProfileDigest" in package["required"]
    assert package["properties"]["tokenizerProfileDigest"]["pattern"] == (
        "^[0-9a-f]{64}$"
    )


def test_checked_in_v1_snapshot_has_an_independent_checksum() -> None:
    generated = render_openapi_snapshot("v1")
    snapshot = ROOT / "openapi/v1/openapi.json"
    digest = ROOT / "openapi/v1/openapi.sha256"

    assert snapshot.read_bytes() == generated
    assert digest.read_text(encoding="ascii") == f"{sha256(generated).hexdigest()}\n"


def test_v0_artifacts_remain_byte_identical_to_pre_migration_baseline() -> None:
    assert sha256((ROOT / "openapi/v0/openapi.json").read_bytes()).hexdigest() == (
        "5fa7add530dcfad066d67992bb1b18fdd1ed7ce423f632ebb55f880acaef1237"
    )


def test_aggregate_openapi_check_verifies_both_frozen_versions() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("openapi-check:\n", maxsplit=1)[1].split(
        "\n\n", maxsplit=1
    )[0]

    assert target.count("scripts/freeze_openapi.py check") == 2
    assert "--version-directory openapi/v1" in target
