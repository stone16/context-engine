from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.freeze_openapi import (
    BreakingContractChange,
    SnapshotAlreadyExists,
    SnapshotDrift,
    assert_historical_artifacts_unchanged,
    assert_no_breaking_changes,
    check_snapshot,
    render_openapi_snapshot,
    write_new_snapshot,
)

ROOT = Path(__file__).parents[2]
SNAPSHOT = ROOT / "openapi" / "v0" / "openapi.json"
DIGEST = ROOT / "openapi" / "v0" / "openapi.sha256"


def test_checked_in_v0_snapshot_and_digest_match_deterministic_generation() -> None:
    generated = render_openapi_snapshot()

    assert generated == SNAPSHOT.read_bytes()
    assert DIGEST.read_text(encoding="ascii") == f"{sha256(generated).hexdigest()}\n"


def test_historical_snapshot_cannot_be_overwritten(tmp_path: Path) -> None:
    version_directory = tmp_path / "v0"
    version_directory.mkdir()
    (version_directory / "openapi.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(SnapshotAlreadyExists):
        write_new_snapshot(version_directory)


def test_breaking_change_gate_rejects_a_deliberate_required_field_removal() -> None:
    accepted = create_contract_document()
    candidate = deepcopy(accepted)
    typed_candidate = cast(dict[str, Any], candidate)
    required = typed_candidate["components"]["schemas"]["ContextPackageWire"][
        "required"
    ]
    required.remove("audienceDigest")

    with pytest.raises(BreakingContractChange, match="ContextPackageWire"):
        assert_no_breaking_changes(accepted, candidate)


@pytest.mark.parametrize(
    "mutation",
    (
        "security",
        "response",
        "outcome_union",
        "field_type",
    ),
)
def test_recursive_gate_rejects_security_response_union_and_type_changes(
    mutation: str,
) -> None:
    accepted = create_contract_document()
    candidate = cast(dict[str, Any], deepcopy(accepted))
    operation = candidate["paths"]["/v0/resolve"]["post"]
    schemas = candidate["components"]["schemas"]
    if mutation == "security":
        operation.pop("security")
    elif mutation == "response":
        operation["responses"].pop("401")
    elif mutation == "outcome_union":
        schemas["ResolutionOutcomeWire"]["oneOf"].pop()
    else:
        schemas["ContextPackageWire"]["properties"]["ttlSeconds"]["type"] = "string"

    with pytest.raises(BreakingContractChange):
        assert_no_breaking_changes(accepted, candidate)


def test_historical_artifact_pair_is_append_only_after_first_publication() -> None:
    snapshot = b'{"openapi":"3.1.0"}\n'
    digest = f"{sha256(snapshot).hexdigest()}\n".encode()
    assert_historical_artifacts_unchanged(
        current_snapshot=snapshot,
        current_digest=digest,
        historical_snapshot=None,
        historical_digest=None,
    )

    with pytest.raises(SnapshotDrift, match="historical OpenAPI version changed"):
        assert_historical_artifacts_unchanged(
            current_snapshot=snapshot + b" ",
            current_digest=digest,
            historical_snapshot=snapshot,
            historical_digest=digest,
        )


def test_snapshot_check_rejects_mutation_against_the_git_baseline(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    version_directory = repository / "openapi" / "v0"
    version_directory.mkdir(parents=True)
    snapshot_path = version_directory / "openapi.json"
    digest_path = version_directory / "openapi.sha256"
    accepted = SNAPSHOT.read_bytes()
    snapshot_path.write_bytes(accepted)
    digest_path.write_text(f"{sha256(accepted).hexdigest()}\n", encoding="ascii")
    for arguments in (
        ("init", "--quiet"),
        ("config", "user.email", "contract-test@example.invalid"),
        ("config", "user.name", "Contract Test"),
        ("add", "openapi/v0/openapi.json", "openapi/v0/openapi.sha256"),
        ("commit", "--quiet", "-m", "freeze v0"),
    ):
        subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=True,
            capture_output=True,
        )

    mutated_document = json.loads(accepted)
    mutated_document["info"]["description"] = "mutated historical contract"
    mutated = (
        json.dumps(
            mutated_document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        )
        + "\n"
    ).encode()
    snapshot_path.write_bytes(mutated)
    digest_path.write_text(f"{sha256(mutated).hexdigest()}\n", encoding="ascii")

    with pytest.raises(SnapshotDrift, match="historical OpenAPI version changed"):
        check_snapshot(
            version_directory,
            baseline_ref="HEAD",
            repository_root=repository,
        )


def create_contract_document() -> dict[str, object]:
    from json import loads

    document = loads(render_openapi_snapshot())
    assert isinstance(document, dict)
    return document
