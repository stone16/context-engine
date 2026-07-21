from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from adapters.http.app import _resolved_to_wire
from adapters.http.contracts import ContextPackageWire, EvidenceWire
from engine.runtime.budget import PackageBudget
from engine.runtime.contracts import (
    BudgetUsage,
    ContextPackage,
    Coverage,
    CoverageReason,
    CoverageStatus,
    Resolved,
)
from engine.runtime.evidence import Evidence, EvidenceLineage, PackageBlock

AS_OF = datetime(2026, 7, 21, 5, 0, tzinfo=UTC)
EVIDENCE_ENTROPY = "a" * 64
EVIDENCE_REF = f"ev_{EVIDENCE_ENTROPY}"
BLOCK_ID = f"block_{EVIDENCE_ENTROPY}"
DECISION_REF = "dec_" + "b" * 32


def empty_outcome() -> Resolved:
    package = ContextPackage(
        organization_ref="orgpkg_" + "c" * 32,
        purpose="context.answer",
        ttl_seconds=300,
        as_of=AS_OF,
        expires_at=datetime(2026, 7, 21, 5, 5, tzinfo=UTC),
        decision_ref=DECISION_REF,
        blocks=(),
        evidence=(),
        gaps=(),
        budget_usage=BudgetUsage(
            tokens=0,
            provider_calls=0,
            cost_microunits=0,
            elapsed_ms=0,
        ),
        coverage=Coverage(
            status=CoverageStatus.EMPTY,
            reason=CoverageReason.NO_AUTHORIZED_EVIDENCE,
        ),
    )
    return cast(
        Resolved,
        SimpleNamespace(kind="resolved", package=package),
    )


def authorized_outcome() -> Resolved:
    lineage = EvidenceLineage(
        run_ref="run-authorized-request",
        principal_ref="principal-secret-must-not-cross-http",
        purpose="context.answer",
        as_of=AS_OF,
        decision_ref=DECISION_REF,
        policy_snapshot_ref="policy-snapshot-current",
        policy_epoch=17,
        source_acl_decision_ref="source-decision-current",
    )
    evidence = Evidence(
        evidence_ref=EVIDENCE_REF,
        source_ref="source:authorized",
        resource_ref="resource:authorized",
        revision_ref="revision:authorized",
        fragment_ref="fragment:authorized",
        lineage=lineage,
    )
    package = SimpleNamespace(
        organization_ref="orgpkg_" + "c" * 32,
        purpose="context.answer",
        ttl_seconds=300,
        as_of=AS_OF,
        expires_at=datetime(2026, 7, 21, 5, 5, tzinfo=UTC),
        decision_ref=DECISION_REF,
        blocks=(PackageBlock(evidence_ref=EVIDENCE_REF, body="authorized body"),),
        evidence=(evidence,),
        gaps=(),
        budget_usage=SimpleNamespace(
            tokens=len(b"authorized body"),
            provider_calls=0,
            cost_microunits=0,
            elapsed_ms=0,
        ),
        coverage=SimpleNamespace(status="sufficient", reason=None),
    )
    return cast(
        Resolved,
        SimpleNamespace(
            kind="resolved",
            package=package,
            effective_budget=PackageBudget(
                max_tokens=4_096,
                max_provider_calls=8,
                max_cost_microunits=100_000,
                max_elapsed_ms=5_000,
            ),
        ),
    )


def test_authorized_package_maps_to_the_exact_closed_public_shape() -> None:
    wire = _resolved_to_wire(authorized_outcome())

    document = wire.model_dump(mode="json", by_alias=True, exclude_none=True)

    assert document == {
        "kind": "resolved",
        "package": {
            "organizationRef": "orgpkg_" + "c" * 32,
            "purpose": "context.answer",
            "ttlSeconds": 300,
            "asOf": "2026-07-21T05:00:00Z",
            "expiresAt": "2026-07-21T05:05:00Z",
            "decisionRef": DECISION_REF,
            "blocks": [
                {
                    "blockId": BLOCK_ID,
                    "text": "authorized body",
                    "evidenceRefs": [EVIDENCE_REF],
                }
            ],
            "evidence": [
                {
                    "evidenceRef": EVIDENCE_REF,
                    "sourceRef": "source:authorized",
                    "resourceRef": "resource:authorized",
                    "revisionRef": "revision:authorized",
                    "fragmentRef": "fragment:authorized",
                    "runRef": "run-authorized-request",
                    "purpose": "context.answer",
                    "authorizationAsOf": "2026-07-21T05:00:00Z",
                    "decisionRef": DECISION_REF,
                    "policySnapshotRef": "policy-snapshot-current",
                    "policyEpoch": 17,
                    "sourceDecisionRef": "source-decision-current",
                }
            ],
            "gaps": [],
            "budgetUsage": {
                "tokens": 15,
                "providerCalls": 0,
                "costMicrounits": 0,
                "elapsedMs": 0,
            },
            "coverage": {"status": "sufficient"},
        },
    }
    serialized = repr(document).casefold()
    for forbidden in (
        "principalref",
        "principal-secret-must-not-cross-http",
        "candidateref",
        "organizationid",
        "denied",
        "candidatecount",
    ):
        assert forbidden not in serialized


def test_context_package_wire_requires_exact_block_evidence_closure() -> None:
    document = _resolved_to_wire(authorized_outcome()).package.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    document["blocks"][0]["evidenceRefs"] = ["ev_" + "d" * 64]
    document["blocks"][0]["blockId"] = "block_" + "d" * 64

    with pytest.raises(ValidationError, match="block/Evidence closure"):
        ContextPackageWire.model_validate(document)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("resourceRef", "resource:denied-secret"),
        ("resourceName", "Denied quarterly plan"),
        ("deniedCount", 1),
        ("candidateCount", 1),
        ("denialReason", "outside_effective_scope"),
        ("existenceDetail", "resource_exists"),
    ),
)
def test_empty_package_wire_rejects_existence_and_denial_metadata(
    field_name: str,
    value: object,
) -> None:
    document = _resolved_to_wire(empty_outcome()).package.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    document[field_name] = value

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ContextPackageWire.model_validate(document)


@pytest.mark.parametrize("forbidden", ["principalRef", "candidateRef"])
def test_evidence_wire_rejects_internal_authority_fields(forbidden: str) -> None:
    evidence_document = _resolved_to_wire(authorized_outcome()).package.evidence[
        0
    ].model_dump(mode="json", by_alias=True)
    evidence_document[forbidden] = "must-not-cross-http"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceWire.model_validate(evidence_document)


def test_authorized_budget_usage_allows_tokens_but_no_external_consumption() -> None:
    document = _resolved_to_wire(authorized_outcome()).package.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    document["budgetUsage"]["providerCalls"] = 1

    with pytest.raises(ValidationError):
        ContextPackageWire.model_validate(document)


def test_authorized_wire_rejects_misaccounted_content_bytes() -> None:
    document = _resolved_to_wire(authorized_outcome()).package.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    document["budgetUsage"]["tokens"] = 14

    with pytest.raises(ValidationError, match="authorized UTF-8 bytes"):
        ContextPackageWire.model_validate(document)
