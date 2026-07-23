from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from engine.runtime.budget import PackageBudget, PackageBudgetRequest
from engine.runtime.contracts import (
    Acquire,
    BudgetUsage,
    ContextNeed,
    ContextPackage,
    Coverage,
    CoverageReason,
    CoverageStatus,
    RequestNarrowing,
    Resolved,
    ScopeDecisionReceipt,
    context_package_digest_document,
    context_package_public_document,
)
from engine.runtime.delivery import (
    DeliveryConstructionProvenance,
    TrustedDeliveryContext,
    _construct_direct_delivery_context,
)
from engine.runtime.evidence import Evidence, EvidenceLineage, PackageBlock
from engine.runtime.package_digest import context_package_digest

AS_OF = datetime(2026, 7, 21, 5, 0, tzinfo=UTC)
EXPIRES_AT = AS_OF + timedelta(seconds=300)
EMPTY_USAGE = BudgetUsage(
    tokens=0,
    provider_calls=0,
    cost_microunits=0,
    elapsed_ms=0,
)
EMPTY_COVERAGE = Coverage(
    status=CoverageStatus.EMPTY,
    reason=CoverageReason.NO_AUTHORIZED_EVIDENCE,
)
EFFECTIVE_BUDGET = PackageBudget(
    max_tokens=1_000,
    max_provider_calls=8,
    max_cost_microunits=25_000,
    max_elapsed_ms=2_500,
)
SCOPE_DECISION = ScopeDecisionReceipt(
    digest="0" * 64,
    target_count=0,
    is_empty=True,
)
EVIDENCE_REF = "ev_" + "a" * 64
AUTHORIZED_LINEAGE = EvidenceLineage(
    run_ref="run-authorized",
    principal_ref="principal-hidden",
    purpose="direct_agent_context",
    as_of=AS_OF,
    decision_ref="dec_00000000000000000000000000000001",
    policy_snapshot_ref="policy-current",
    policy_epoch=1,
    source_acl_decision_ref="source-decision-current",
)
AUTHORIZED_EVIDENCE = Evidence(
    evidence_ref=EVIDENCE_REF,
    source_ref="source-authorized",
    resource_ref="resource-authorized",
    revision_ref="revision-authorized",
    fragment_ref="fragment-authorized",
    projected_field_refs=("body",),
    lineage=AUTHORIZED_LINEAGE,
)
AUTHORIZED_BLOCK = PackageBlock(
    evidence_ref=EVIDENCE_REF,
    body="A-safe",
)


def make_package(**changes: object) -> ContextPackage:
    values: dict[str, object] = {
        "organization_ref": "orgpkg_00000000000000000000000000000001",
        "purpose": "direct_agent_context",
        "ttl_seconds": 300,
        "as_of": AS_OF,
        "expires_at": EXPIRES_AT,
        "decision_ref": "dec_00000000000000000000000000000001",
        "blocks": (),
        "evidence": (),
        "gaps": (),
        "budget_usage": EMPTY_USAGE,
        "coverage": EMPTY_COVERAGE,
    }
    values.update(changes)
    return ContextPackage(**cast(Any, values))


@pytest.mark.parametrize("invalid", ["", " ", "\t\n", 42, True, None])
def test_context_need_requires_a_nonblank_exact_string(invalid: object) -> None:
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        ContextNeed(query=cast(Any, invalid))


def test_context_need_is_immutable() -> None:
    need = ContextNeed(query="Which decisions constrain Runtime delivery?")

    with pytest.raises(FrozenInstanceError):
        need.query = "different"  # type: ignore[misc]


def test_request_narrowing_accepts_one_or_both_closed_ref_sets() -> None:
    assert RequestNarrowing(source_refs=("source_a",)) == RequestNarrowing(
        source_refs=("source_a",),
        resource_refs=None,
    )
    assert RequestNarrowing(resource_refs=("resource_a",)).resource_refs == (
        "resource_a",
    )
    assert RequestNarrowing(
        source_refs=("source_a",),
        resource_refs=("resource_a", "resource_b"),
    ).source_refs == ("source_a",)


@pytest.mark.parametrize(
    "narrowing",
    [
        {"source_refs": tuple(f"source_{index}" for index in range(65))},
        {"resource_refs": ("r" * 257,)},
    ],
)
def test_request_narrowing_enforces_active_profile_bounds(
    narrowing: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="active profile"):
        RequestNarrowing(**cast(Any, narrowing))


@pytest.mark.parametrize(
    "narrowing",
    [
        {},
        {"source_refs": ()},
        {"resource_refs": ()},
        {"source_refs": (), "resource_refs": ("resource_a",)},
        {"source_refs": ("source_a",), "resource_refs": ()},
        {"source_refs": ("source_a", "source_a")},
        {"resource_refs": ("resource_a", "resource_a")},
        {"source_refs": ("",)},
        {"resource_refs": (" ",)},
        {"source_refs": cast(Any, ["source_a"])},
        {"resource_refs": cast(Any, (42,))},
    ],
)
def test_request_narrowing_rejects_empty_mutable_duplicate_or_invalid_sets(
    narrowing: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=r"narrowing|refs"):
        RequestNarrowing(**cast(Any, narrowing))


def test_acquire_is_a_closed_immutable_domain_request() -> None:
    need = ContextNeed(query="What is authorized?")
    budget = PackageBudgetRequest(max_tokens=100)
    narrowing = RequestNarrowing(source_refs=("source_a",))
    acquire = Acquire(need=need, package_budget=budget, narrowing=narrowing)

    assert acquire.need is need
    assert acquire.package_budget is budget
    assert acquire.narrowing is narrowing
    with pytest.raises(FrozenInstanceError):
        acquire.need = ContextNeed(query="different")  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"need": object()},
        {"package_budget": object()},
        {"narrowing": object()},
    ],
)
def test_acquire_rejects_values_outside_its_closed_contract(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {"need": ContextNeed(query="valid")}
    values.update(changes)

    with pytest.raises(TypeError, match="must be"):
        Acquire(**cast(Any, values))


def test_direct_delivery_context_is_nominal_and_server_constructed() -> None:
    with pytest.raises(
        TypeError,
        match="can only be constructed by trusted ingress",
    ):
        TrustedDeliveryContext(
            purpose="caller-purpose",
            authenticated_application_ref="caller-app",
            delivery_binding_ref="caller-binding",
            established_at=AS_OF,
        )

    context = _construct_direct_delivery_context(
        purpose="direct_agent_context",
        authenticated_application_ref="application-from-auth",
        delivery_binding_ref="route-policy-binding",
        established_at=AS_OF,
    )

    assert context.purpose == "direct_agent_context"
    assert context.authenticated_application_ref == "application-from-auth"
    assert context.delivery_binding_ref == "route-policy-binding"
    assert context.established_at == AS_OF
    assert (
        context.construction_provenance
        is DeliveryConstructionProvenance.AUTHENTICATED_DIRECT_INGRESS
    )
    with pytest.raises(FrozenInstanceError):
        context.purpose = "different"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("purpose", ""),
        ("purpose", " "),
        ("purpose", 42),
        ("authenticated_application_ref", ""),
        ("authenticated_application_ref", True),
        ("delivery_binding_ref", "\t"),
        ("delivery_binding_ref", object()),
        ("established_at", datetime(2026, 7, 21, 5, 0)),
        ("established_at", "2026-07-21T05:00:00Z"),
    ],
)
def test_trusted_delivery_factory_rejects_incomplete_or_untrusted_facts(
    field_name: str,
    invalid: object,
) -> None:
    values: dict[str, object] = {
        "purpose": "direct_agent_context",
        "authenticated_application_ref": "application-from-auth",
        "delivery_binding_ref": "route-policy-binding",
        "established_at": AS_OF,
    }
    values[field_name] = invalid

    with pytest.raises(ValueError, match="trusted delivery"):
        _construct_direct_delivery_context(**cast(Any, values))


@pytest.mark.parametrize("field_name", [field.name for field in fields(BudgetUsage)])
@pytest.mark.parametrize("invalid", [-1, True, False, 1.0, "0", None])
def test_budget_usage_requires_nonnegative_exact_integers(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match="non-negative exact integer"):
        replace(EMPTY_USAGE, **{field_name: invalid})  # type: ignore[arg-type]


def test_empty_coverage_is_typed_closed_and_immutable() -> None:
    assert EMPTY_COVERAGE.status is CoverageStatus.EMPTY
    assert EMPTY_COVERAGE.reason is CoverageReason.NO_AUTHORIZED_EVIDENCE

    with pytest.raises(TypeError, match="CoverageStatus"):
        Coverage(
            status=cast(Any, "empty"),
            reason=CoverageReason.NO_AUTHORIZED_EVIDENCE,
        )
    with pytest.raises(TypeError, match="CoverageReason"):
        Coverage(
            status=CoverageStatus.EMPTY,
            reason=cast(Any, "no_authorized_evidence"),
        )
    with pytest.raises(FrozenInstanceError):
        EMPTY_COVERAGE.reason = CoverageReason.NO_AUTHORIZED_EVIDENCE  # type: ignore[misc]


def test_sufficient_coverage_has_no_empty_reason() -> None:
    sufficient = Coverage(status=CoverageStatus.SUFFICIENT)

    assert sufficient.reason is None
    with pytest.raises(ValueError, match="must not contain a reason"):
        Coverage(
            status=CoverageStatus.SUFFICIENT,
            reason=CoverageReason.NO_AUTHORIZED_EVIDENCE,
        )


def test_context_package_is_the_tenant_safe_evidence_free_deliverable() -> None:
    package = make_package()

    assert package.blocks == ()
    assert package.evidence == ()
    assert package.gaps == ()
    assert package.budget_usage == EMPTY_USAGE
    assert package.coverage == EMPTY_COVERAGE
    assert package.ttl_seconds == 300
    assert package.expires_at > package.as_of
    assert package.package_digest == (
        "3e454e57a97eb4bb47bde1af0d6c7817b080630ecbc88275837329cbd2c4a4a5"
    )
    assert not hasattr(package, "denied_count")
    assert not hasattr(package.coverage, "details")
    assert {field.name for field in fields(package)} == {
        "organization_ref",
        "purpose",
        "ttl_seconds",
        "as_of",
        "expires_at",
        "decision_ref",
        "package_digest",
        "blocks",
        "evidence",
        "gaps",
        "budget_usage",
        "coverage",
    }

    with pytest.raises(FrozenInstanceError):
        package.organization_ref = "other"  # type: ignore[misc]


def test_context_package_accepts_only_closed_exact_authorized_content() -> None:
    package = make_package(
        blocks=(AUTHORIZED_BLOCK,),
        evidence=(AUTHORIZED_EVIDENCE,),
        budget_usage=BudgetUsage(
            tokens=len(b"A-safe"),
            provider_calls=0,
            cost_microunits=0,
            elapsed_ms=0,
        ),
        coverage=Coverage(status=CoverageStatus.SUFFICIENT),
    )

    assert package.blocks == (AUTHORIZED_BLOCK,)
    assert package.evidence == (AUTHORIZED_EVIDENCE,)


def test_package_digest_detects_any_alteration_to_the_public_package() -> None:
    package = make_package()
    document = context_package_digest_document(package)
    document["ttlSeconds"] = 301

    assert context_package_digest(document) != package.package_digest
    assert "packageDigest" not in document


def test_public_package_projection_is_digest_document_plus_digest() -> None:
    package = make_package()
    public_document = context_package_public_document(package)
    package_digest = public_document.pop("packageDigest")

    assert public_document == context_package_digest_document(package)
    assert package_digest == package.package_digest


@pytest.mark.parametrize(
    "changes",
    (
        {"blocks": (AUTHORIZED_BLOCK,)},
        {"evidence": (AUTHORIZED_EVIDENCE,)},
        {
            "blocks": (AUTHORIZED_BLOCK,),
            "evidence": (AUTHORIZED_EVIDENCE,),
            "budget_usage": BudgetUsage(
                tokens=5,
                provider_calls=0,
                cost_microunits=0,
                elapsed_ms=0,
            ),
            "coverage": Coverage(status=CoverageStatus.SUFFICIENT),
        },
        {
            "blocks": (AUTHORIZED_BLOCK,),
            "evidence": (AUTHORIZED_EVIDENCE,),
            "budget_usage": BudgetUsage(
                tokens=6,
                provider_calls=1,
                cost_microunits=0,
                elapsed_ms=0,
            ),
            "coverage": Coverage(status=CoverageStatus.SUFFICIENT),
        },
        {
            "blocks": (AUTHORIZED_BLOCK,),
            "evidence": (AUTHORIZED_EVIDENCE,),
            "budget_usage": BudgetUsage(
                tokens=6,
                provider_calls=0,
                cost_microunits=0,
                elapsed_ms=0,
            ),
            "coverage": EMPTY_COVERAGE,
        },
    ),
)
def test_context_package_rejects_incomplete_or_misaccounted_content(
    changes: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError), match="package|content|usage"):
        make_package(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_ref": ""},
        {"organization_ref": 42},
        {"organization_ref": "81e18bca-86a1-478a-937d-7675c6fe69b0"},
        {"organization_ref": "orgpkg_0000000000000000000000000000000A"},
        {"purpose": " "},
        {"purpose": True},
        {"decision_ref": ""},
        {"decision_ref": object()},
        {"decision_ref": "dec_0000000000000000000000000000000A"},
        {"ttl_seconds": 0},
        {"ttl_seconds": True},
        {"ttl_seconds": 300.0},
        {"as_of": datetime(2026, 7, 21, 5, 0)},
        {"expires_at": datetime(2026, 7, 21, 5, 5)},
        {"as_of": AS_OF.astimezone(timezone(timedelta(hours=8)))},
        {"expires_at": EXPIRES_AT.astimezone(timezone(timedelta(hours=8)))},
        {"expires_at": AS_OF},
        {"expires_at": AS_OF - timedelta(seconds=1), "ttl_seconds": 1},
        {"expires_at": EXPIRES_AT + timedelta(microseconds=1)},
        {"ttl_seconds": 299},
        {"blocks": (object(),)},
        {"evidence": (object(),)},
        {"gaps": ("denied source exists",)},
        {"blocks": cast(Any, [])},
        {"evidence": cast(Any, [])},
        {"gaps": cast(Any, [])},
        {"budget_usage": replace(EMPTY_USAGE, tokens=1)},
        {"budget_usage": object()},
        {"coverage": object()},
    ],
)
def test_empty_package_rejects_invalid_or_detail_bearing_state(
    changes: dict[str, object],
) -> None:
    with pytest.raises(
        (TypeError, ValueError),
        match=r"package|empty|UTC|TTL|usage",
    ):
        make_package(**changes)


def test_resolved_is_a_closed_immutable_outcome() -> None:
    package = make_package()
    outcome = Resolved(
        package=package,
        effective_budget=EFFECTIVE_BUDGET,
        scope_decision=SCOPE_DECISION,
    )

    assert outcome.kind == "resolved"
    assert outcome.package is package
    assert outcome.effective_budget is EFFECTIVE_BUDGET
    with pytest.raises(FrozenInstanceError):
        outcome.kind = "different"  # type: ignore[misc,assignment]
    with pytest.raises((TypeError, ValueError), match=r"resolved|ContextPackage"):
        Resolved(
            package=package,
            effective_budget=EFFECTIVE_BUDGET,
            scope_decision=SCOPE_DECISION,
            kind=cast(Any, "other"),
        )
    with pytest.raises(TypeError, match="ContextPackage"):
        Resolved(
            package=cast(Any, object()),
            effective_budget=EFFECTIVE_BUDGET,
            scope_decision=SCOPE_DECISION,
        )
    with pytest.raises(TypeError, match="PackageBudget"):
        Resolved(
            package=package,
            effective_budget=cast(Any, object()),
            scope_decision=SCOPE_DECISION,
        )
    with pytest.raises(TypeError, match="ScopeDecisionReceipt"):
        Resolved(
            package=package,
            effective_budget=EFFECTIVE_BUDGET,
            scope_decision=cast(Any, object()),
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"digest": ""},
        {"digest": "0" * 63},
        {"digest": "G" * 64},
        {"target_count": -1},
        {"target_count": True},
        {"is_empty": 0},
        {"target_count": 1, "is_empty": True},
        {"target_count": 0, "is_empty": False},
    ),
)
def test_scope_decision_receipt_is_closed_and_consistent(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "digest": "0" * 64,
        "target_count": 0,
        "is_empty": True,
    }
    values.update(changes)

    with pytest.raises((TypeError, ValueError), match="scope decision"):
        ScopeDecisionReceipt(**cast(Any, values))


def test_scope_decision_receipt_hides_digest_from_repr() -> None:
    receipt = ScopeDecisionReceipt(
        digest="a" * 64,
        target_count=1,
        is_empty=False,
    )

    assert "a" * 64 not in repr(receipt)
    with pytest.raises(FrozenInstanceError):
        receipt.target_count = 2  # type: ignore[misc]
