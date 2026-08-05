from __future__ import annotations

import ast
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from engine.runtime.budget import (
    BudgetUsage,
    PackageBudget,
    PackageBudgetExceeded,
    PackageBudgetMeter,
)
from engine.tokenizer_accounting import (
    UNICODE_SCALAR_TOKENIZER_PROFILE,
    TokenizerUnavailable,
    load_registered_tokenizer,
)


@pytest.mark.security_evidence(id="ACCOUNTING-DETERMINISM-217", layer="property")
def test_registered_tokenizer_is_digest_bound_and_cross_process_deterministic() -> None:
    tokenizer = load_registered_tokenizer(
        UNICODE_SCALAR_TOKENIZER_PROFILE.canonical_json(),
        UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest,
    )

    assert tokenizer.count("Context 世界 👩🏽\u200d💻") == 15
    assert tokenizer.profile is UNICODE_SCALAR_TOKENIZER_PROFILE

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from engine.tokenizer_accounting import "
                "UNICODE_SCALAR_TOKENIZER_PROFILE as p, "
                "load_registered_tokenizer; "
                "t=load_registered_tokenizer(p.canonical_json(), p.profile_digest); "
                "print(json.dumps({'count':t.count('Context 世界 👩🏽\\u200d💻'),"
                "'digest':t.profile.profile_digest}, sort_keys=True))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[2],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "count": 15,
        "digest": UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest,
    }


@pytest.mark.security_evidence(id="ACCOUNTING-MISSING-TOKENIZER-217", layer="property")
def test_missing_tokenizer_profile_fails_closed() -> None:
    with pytest.raises(TokenizerUnavailable):
        load_registered_tokenizer(
            "",
            UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest,
        )


@pytest.mark.security_evidence(id="ACCOUNTING-UNKNOWN-TOKENIZER-217", layer="property")
def test_unknown_tokenizer_profile_fails_closed() -> None:
    with pytest.raises(TokenizerUnavailable):
        load_registered_tokenizer('{"profileRef":"unknown"}', "0" * 64)


@pytest.mark.security_evidence(id="ACCOUNTING-PROFILE-DIGEST-217", layer="property")
def test_tokenizer_profile_digest_mismatch_fails_closed() -> None:
    with pytest.raises(TokenizerUnavailable):
        load_registered_tokenizer(
            UNICODE_SCALAR_TOKENIZER_PROFILE.canonical_json(),
            "f" * 64,
        )


@pytest.mark.security_evidence(id="ACCOUNTING-ARTIFACT-MISSING-217", layer="property")
def test_unavailable_tokenizer_artifact_fails_closed(tmp_path: Path) -> None:
    missing = replace(
        UNICODE_SCALAR_TOKENIZER_PROFILE,
        artifact_path=tmp_path / "missing.json",
    )
    with pytest.raises(TokenizerUnavailable):
        missing.load()


@pytest.mark.security_evidence(id="ACCOUNTING-ARTIFACT-HASH-217", layer="property")
def test_hash_mismatched_tokenizer_artifact_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "tokenizer.json"
    artifact.write_text("{}", encoding="utf-8")
    mismatched = replace(
        UNICODE_SCALAR_TOKENIZER_PROFILE,
        artifact_path=artifact,
    )
    with pytest.raises(TokenizerUnavailable):
        mismatched.load()


def _meter(*, maximum_tokens: int = 10) -> PackageBudgetMeter:
    return PackageBudgetMeter(
        PackageBudget(maximum_tokens, 2, 2, 10),
        tokenizer_profile=UNICODE_SCALAR_TOKENIZER_PROFILE,
        release_generation=7,
    )


def test_meter_rejects_carrier_tokenizer_identity() -> None:
    meter = _meter()

    with pytest.raises(TokenizerUnavailable):
        meter.require_tokenizer("other-tokenizer", "0" * 64, 7)


def test_meter_rejects_mixed_release_generation() -> None:
    meter = _meter()

    with pytest.raises(TokenizerUnavailable):
        meter.require_tokenizer(
            UNICODE_SCALAR_TOKENIZER_PROFILE.profile_ref,
            UNICODE_SCALAR_TOKENIZER_PROFILE.profile_digest,
            8,
        )


@pytest.mark.security_evidence(id="ACCOUNTING-RESERVATION-RACE-217", layer="property")
def test_concurrent_over_limit_reservations_admit_exactly_one() -> None:
    meter = _meter(maximum_tokens=5)
    maximum = BudgetUsage(4, 0, 0, 0)

    def reserve() -> object:
        try:
            return meter._reserve(maximum)
        except PackageBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservations = tuple(executor.map(lambda _index: reserve(), range(2)))

    assert sum(reservation is not None for reservation in reservations) == 1


@pytest.mark.security_evidence(id="ACCOUNTING-CANCEL-NO-LEAK-217", layer="property")
def test_cancel_releases_capacity_without_usage_leakage() -> None:
    meter = _meter(maximum_tokens=5)
    maximum = BudgetUsage(5, 0, 0, 0)

    first = meter._reserve(maximum)
    meter._cancel(first)
    second = meter._reserve(maximum)
    meter._commit(second, BudgetUsage(3, 0, 0, 0))

    assert meter.usage == BudgetUsage(3, 0, 0, 0)


@pytest.mark.security_evidence(id="ACCOUNTING-ONE-METER-STATIC-217", layer="property")
def test_runtime_has_one_meter_creation_and_no_v1_usage_reset() -> None:
    root = Path(__file__).parents[2]
    module = ast.parse((root / "engine/runtime/construction.py").read_text())
    runtime = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "Runtime"
    )
    resolve = max(
        (
            node
            for node in runtime.body
            if isinstance(node, ast.FunctionDef) and node.name == "resolve"
        ),
        key=lambda node: len(node.body),
    )
    meter_calls = [
        node
        for node in ast.walk(resolve)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PackageBudgetMeter"
    ]
    usage_assignments = [
        node
        for node in ast.walk(resolve)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "cumulative_usage"
            for target in node.targets
        )
    ]

    meter_factory_calls = [
        node
        for node in ast.walk(resolve)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_resolve_budget_meter"
    ]

    assert meter_calls == []
    assert len(meter_factory_calls) == 2  # Acquire and OpenCitation branches
    assert len(usage_assignments) == 1
    assert isinstance(usage_assignments[0].value, ast.Attribute)
    assert usage_assignments[0].value.attr == "usage"
