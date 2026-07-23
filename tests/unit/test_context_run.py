from __future__ import annotations

import pickle
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import pytest

from engine.runtime.actor import (
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.budget import PackageBudget
from engine.runtime.construction import DecisionProvenanceReceipt
from engine.runtime.context_run import (
    PACKAGE_RETENTION_MODE,
    ContextRunOutcome,
    ContextRunPersistencePort,
    ContextRunPersistenceSession,
    ContextRunPersistenceUnavailable,
    ContextRunRecord,
    DecisionAuditCategory,
    DecisionAuditRecord,
    _close_context_run_persistence_scope,
    _construct_context_run_persistence_session,
    _open_context_run_persistence_scope,
    build_context_run_records,
    persist_context_run,
)
from engine.runtime.contracts import (
    Acquire,
    BudgetUsage,
    ContextNeed,
    ContextPackage,
    Coverage,
    CoverageReason,
    CoverageStatus,
    context_package_digest_document,
)
from engine.runtime.evidence import Evidence, EvidenceLineage, PackageBlock
from engine.runtime.invocation import (
    AuthenticatedInvocation,
    _construct_authenticated_http_invocation,
)
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.package_digest import (
    PACKAGE_DIGEST_PROFILE,
    QUERY_DIGEST_PROFILE,
    QueryDigestKeyring,
    context_package_digest,
)
from engine.runtime.policy_epoch import (
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
)
from engine.runtime.scope import EffectiveScope, ScopeSet, ScopeTarget
from engine.runtime.scope_authority import (
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)
from tests.support.releases import active_runtime_release

ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
OTHER_ORGANIZATION_ID = UUID("48f519e3-c9f1-4e45-af3a-ef48ca5b23f0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")
ACCEPTED_AT = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)
FINALIZED_AT = ACCEPTED_AT + timedelta(milliseconds=20)
EXPIRES_AT = FINALIZED_AT + timedelta(seconds=300)
DECISION_REF = "dec_00000000000000000000000000000019"
ORGANIZATION_REF = "pkg_00000000000000000000000000000019"
EVIDENCE_REF = "ev_" + "a" * 64
QUERY_KEYRING = QueryDigestKeyring(active_version=3, keys={3: b"q" * 32})
EFFECTIVE_BUDGET = PackageBudget(
    max_tokens=1_000,
    max_provider_calls=8,
    max_cost_microunits=25_000,
    max_elapsed_ms=2_500,
)
AUTHORIZED_SCOPE = ScopeSet(
    frozenset(
        {
            ScopeTarget(
                ORGANIZATION_ID,
                "source-authorized",
                "resource-authorized",
            )
        }
    )
)
FINAL_EFFECTIVE_SCOPE = EffectiveScope(AUTHORIZED_SCOPE.targets)
EFFECTIVE_SCOPE_DIGEST = FINAL_EFFECTIVE_SCOPE.digest


class _PolicyEpochPort:
    def read_current_epoch(self, organization_id: UUID) -> object:
        assert organization_id == ORGANIZATION_ID
        return 7


@contextmanager
def _trusted_invocation() -> Iterator[AuthenticatedInvocation]:
    membership_scope = _open_membership_authority_scope()
    policy_epoch_scope = _open_policy_epoch_authority_scope()
    scope_authority_scope = _open_scope_authority_scope()
    try:
        epoch = _observe_current_policy_epoch(
            _construct_policy_epoch_session(
                authority_scope=policy_epoch_scope,
                organization_id=ORGANIZATION_ID,
                port=_PolicyEpochPort(),
            )
        )
        membership = _construct_current_membership_verification(
            authority_scope=membership_scope,
            organization_id=ORGANIZATION_ID,
            user_id=USER_ID,
            membership_id=MEMBERSHIP_ID,
            membership_version=5,
            principal_ref="principal-context-run-secret",
            request_id="request-context-run",
            authentication_binding_ref="binding-context-run-secret",
            checked_at=ACCEPTED_AT,
            policy_epoch_verification=epoch,
            active_runtime_release=active_runtime_release(
                ORGANIZATION_ID,
                active_revision_refs=("revision-authorized",),
            ),
        )
        scope_snapshot = _construct_trusted_scope_snapshot(
            authority_scope=scope_authority_scope,
            organization_id=ORGANIZATION_ID,
            user_id=USER_ID,
            membership_id=MEMBERSHIP_ID,
            membership_version=5,
            policy_epoch=7,
            principal_ref="principal-context-run-secret",
            agent_version_ref="agent-version-19",
            purpose="context.answer",
            request_id="request-context-run",
            authentication_binding_ref="binding-context-run-secret",
            checked_at=ACCEPTED_AT,
            organization_boundary=AUTHORIZED_SCOPE,
            membership_rights=AUTHORIZED_SCOPE,
            principal_grants=AUTHORIZED_SCOPE,
            agent_ceiling=AUTHORIZED_SCOPE,
            source_native_acl=AUTHORIZED_SCOPE,
            resource_acl=AUTHORIZED_SCOPE,
            purpose_policy=AUTHORIZED_SCOPE,
        )
        organization = _construct_existing_http_organization_verification(
            organization_id=ORGANIZATION_ID,
            request_id="request-context-run",
            authentication_binding_ref="binding-context-run-secret",
            verified_at=ACCEPTED_AT,
        )
        yield _construct_authenticated_http_invocation(
            request_id="request-context-run",
            authenticated_organization_ref=str(ORGANIZATION_ID),
            organization_verification=organization,
            user_ref=str(USER_ID),
            principal_ref="principal-context-run-secret",
            membership_ref=str(MEMBERSHIP_ID),
            membership_version=5,
            current_membership_verification=membership,
            agent_version_ref="agent-version-19",
            authenticated_application_ref="application-19",
            authentication_binding_ref="binding-context-run-secret",
            trusted_purpose="context.answer",
            received_at=ACCEPTED_AT,
            trusted_scope_snapshot=scope_snapshot,
        )
    finally:
        _close_scope_authority_scope(scope_authority_scope)
        _close_policy_epoch_authority_scope(policy_epoch_scope)
        _close_membership_authority_scope(membership_scope)


def _provenance() -> DecisionProvenanceReceipt:
    return DecisionProvenanceReceipt(
        decision_ref=DECISION_REF,
        package_id=ORGANIZATION_REF,
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        membership_id=MEMBERSHIP_ID,
        membership_version=5,
        principal_ref="principal-context-run-secret",
        agent_version_ref="agent-version-19",
        authenticated_application_ref="application-19",
        authentication_binding_ref="binding-context-run-secret",
        effective_scope_digest=EFFECTIVE_SCOPE_DIGEST,
        request_id="request-context-run",
        purpose="context.answer",
        as_of=FINALIZED_AT,
        run_ref="run_issue_19",
        policy_snapshot_ref="policy_issue_19",
        policy_epoch=7,
        source_acl_decision_ref="source-acl-19",
    )


def _empty_package() -> ContextPackage:
    return ContextPackage(
        package_id=ORGANIZATION_REF,
        audience_digest="a" * 64,
        policy_epoch=7,
        policy_snapshot_ref="policy_issue_19",
        run_ref="run_issue_19",
        release_manifest_ref=active_runtime_release(ORGANIZATION_ID).manifest_ref,
        retention_policy_ref="package-digest-only-retention-v1",
        tokenizer_ref=active_runtime_release(ORGANIZATION_ID).tokenizer_ref,
        package_schema_ref=active_runtime_release(ORGANIZATION_ID).package_schema_ref,
        purpose="context.answer",
        ttl_seconds=300,
        as_of=FINALIZED_AT,
        expires_at=EXPIRES_AT,
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


def _authorized_package() -> ContextPackage:
    lineage = EvidenceLineage(
        run_ref="run_issue_19",
        principal_ref="principal-context-run-secret",
        purpose="context.answer",
        as_of=FINALIZED_AT,
        decision_ref=DECISION_REF,
        policy_snapshot_ref="policy_issue_19",
        policy_epoch=7,
        source_acl_decision_ref="source-acl-19",
    )
    evidence = Evidence(
        evidence_ref=EVIDENCE_REF,
        source_ref="source-authorized",
        resource_ref="resource-authorized",
        revision_ref="revision-authorized",
        fragment_ref="fragment-authorized",
        projected_field_refs=("body",),
        lineage=lineage,
    )
    body = "safe authorized text"
    return ContextPackage(
        package_id=ORGANIZATION_REF,
        audience_digest="a" * 64,
        policy_epoch=7,
        policy_snapshot_ref="policy_issue_19",
        run_ref="run_issue_19",
        release_manifest_ref=active_runtime_release(ORGANIZATION_ID).manifest_ref,
        retention_policy_ref="package-digest-only-retention-v1",
        tokenizer_ref=active_runtime_release(ORGANIZATION_ID).tokenizer_ref,
        package_schema_ref=active_runtime_release(ORGANIZATION_ID).package_schema_ref,
        purpose="context.answer",
        ttl_seconds=300,
        as_of=FINALIZED_AT,
        expires_at=EXPIRES_AT,
        decision_ref=DECISION_REF,
        blocks=(PackageBlock(evidence_ref=EVIDENCE_REF, body=body),),
        evidence=(evidence,),
        gaps=(),
        budget_usage=BudgetUsage(
            tokens=len(body.encode()),
            provider_calls=0,
            cost_microunits=0,
            elapsed_ms=0,
        ),
        coverage=Coverage(status=CoverageStatus.SUFFICIENT),
    )


def _run(**changes: object) -> ContextRunRecord:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "run_ref": "run_issue_19",
        "decision_ref": DECISION_REF,
        "user_id": USER_ID,
        "membership_id": MEMBERSHIP_ID,
        "membership_version": 5,
        "principal_ref": "principal-context-run-secret",
        "agent_version_ref": "agent-version-19",
        "authenticated_application_ref": "application-19",
        "authentication_binding_ref": "binding-context-run-secret",
        "request_id": "request-context-run",
        "purpose": "context.answer",
        "policy_snapshot_ref": "policy_issue_19",
        "policy_epoch": 7,
        "effective_scope_digest": EFFECTIVE_SCOPE_DIGEST,
        "query_digest_profile": QUERY_DIGEST_PROFILE,
        "query_digest_key_version": 3,
        "query_digest": "2" * 64,
        "outcome": ContextRunOutcome.DELIVERED_AUTHORIZED,
        "package_digest_profile": PACKAGE_DIGEST_PROFILE,
        "package_digest": "3" * 64,
        "package_retention_mode": PACKAGE_RETENTION_MODE,
        "authorized_evidence_refs": (EVIDENCE_REF,),
        "effective_max_tokens": 1_000,
        "effective_max_provider_calls": 8,
        "effective_max_cost_microunits": 25_000,
        "effective_max_elapsed_ms": 2_500,
        "usage_tokens": 20,
        "usage_provider_calls": 1,
        "usage_cost_microunits": 30,
        "usage_elapsed_ms": 40,
        "accepted_at": ACCEPTED_AT,
        "finalized_at": FINALIZED_AT,
        "package_as_of": FINALIZED_AT,
        "package_expires_at": EXPIRES_AT,
    }
    values.update(changes)
    return ContextRunRecord(**cast(Any, values))


def _empty_run() -> ContextRunRecord:
    return _run(
        outcome=ContextRunOutcome.DELIVERED_EMPTY,
        authorized_evidence_refs=(),
        usage_tokens=0,
        usage_provider_calls=0,
        usage_cost_microunits=0,
        usage_elapsed_ms=0,
    )


def _audit(**changes: object) -> DecisionAuditRecord:
    values: dict[str, object] = {
        "organization_id": ORGANIZATION_ID,
        "run_ref": "run_issue_19",
        "decision_ref": DECISION_REF,
        "policy_snapshot_ref": "policy_issue_19",
        "policy_epoch": 7,
        "category": DecisionAuditCategory.NO_AUTHORIZED_EVIDENCE,
        "recorded_at": FINALIZED_AT,
    }
    values.update(changes)
    return DecisionAuditRecord(**cast(Any, values))


class _RecordingPort:
    def __init__(self) -> None:
        self.calls: list[tuple[ContextRunRecord, DecisionAuditRecord | None]] = []

    def persist(
        self,
        run: ContextRunRecord,
        audit: DecisionAuditRecord | None,
    ) -> None:
        self.calls.append((run, audit))


def test_projection_builds_complete_authorized_and_empty_final_records() -> None:
    request = Acquire(need=ContextNeed(query="raw sensitive issue 19 query"))

    with _trusted_invocation() as invocation:
        authorized, authorized_audit = build_context_run_records(
            invocation=invocation,
            request=request,
            provenance=_provenance(),
            package=_authorized_package(),
            final_effective_scope=FINAL_EFFECTIVE_SCOPE,
            effective_budget=EFFECTIVE_BUDGET,
            keyring=QUERY_KEYRING,
        )
        empty, empty_audit = build_context_run_records(
            invocation=invocation,
            request=request,
            provenance=_provenance(),
            package=_empty_package(),
            final_effective_scope=FINAL_EFFECTIVE_SCOPE,
            effective_budget=EFFECTIVE_BUDGET,
            keyring=QUERY_KEYRING,
        )

    assert authorized.outcome is ContextRunOutcome.DELIVERED_AUTHORIZED
    assert authorized.authorized_evidence_refs == (EVIDENCE_REF,)
    assert authorized.package_digest == _authorized_package().package_digest
    assert authorized.query_digest != request.need.query
    assert authorized.query_digest_key_version == 3
    assert authorized_audit is None
    assert empty.outcome is ContextRunOutcome.DELIVERED_EMPTY
    assert empty.authorized_evidence_refs == ()
    assert empty.package_digest == _empty_package().package_digest
    assert empty_audit == _audit()
    assert empty.accepted_at == ACCEPTED_AT
    assert empty.finalized_at == empty.package_as_of == FINALIZED_AT
    assert empty.effective_scope_digest == FINAL_EFFECTIVE_SCOPE.digest
    assert empty.effective_scope_digest != EffectiveScope(frozenset()).digest


def test_projection_rejects_final_scope_outside_original_or_empty_scope() -> None:
    unrelated_scope = EffectiveScope(
        frozenset(
            {
                ScopeTarget(
                    ORGANIZATION_ID,
                    "source-unrelated",
                    "resource-unrelated",
                )
            }
        )
    )

    with (
        _trusted_invocation() as invocation,
        pytest.raises(ValueError, match="preserve or veto scope"),
    ):
        build_context_run_records(
            invocation=invocation,
            request=Acquire(need=ContextNeed(query="safe query")),
            provenance=replace(
                _provenance(),
                effective_scope_digest=unrelated_scope.digest,
            ),
            package=_empty_package(),
            final_effective_scope=unrelated_scope,
            effective_budget=EFFECTIVE_BUDGET,
            keyring=QUERY_KEYRING,
        )


def test_projection_rejects_evidence_after_final_scope_veto() -> None:
    empty_scope = EffectiveScope(frozenset())

    with (
        _trusted_invocation() as invocation,
        pytest.raises(ValueError, match="cannot carry Evidence"),
    ):
        build_context_run_records(
            invocation=invocation,
            request=Acquire(need=ContextNeed(query="safe query")),
            provenance=replace(
                _provenance(),
                effective_scope_digest=empty_scope.digest,
            ),
            package=_authorized_package(),
            final_effective_scope=empty_scope,
            effective_budget=EFFECTIVE_BUDGET,
            keyring=QUERY_KEYRING,
        )


@pytest.mark.parametrize(
    "provenance_change",
    [
        {"organization_id": OTHER_ORGANIZATION_ID},
        {"user_id": UUID("0f54a950-0744-4cc6-a242-1909376bf37d")},
        {"membership_id": UUID("b2252330-a79a-4264-babd-75e8ea775d64")},
        {"membership_version": 6},
        {"principal_ref": "principal-other"},
        {"agent_version_ref": "agent-version-other"},
        {"authenticated_application_ref": "application-other"},
        {"authentication_binding_ref": "binding-other"},
        {"request_id": "request-other"},
        {"purpose": "context.other"},
        {"as_of": FINALIZED_AT + timedelta(microseconds=1)},
        {"package_id": "pkg_" + "f" * 32},
        {"decision_ref": "dec_" + "f" * 32},
        {"policy_epoch": 8},
        {"effective_scope_digest": "f" * 64},
    ],
)
def test_projection_rejects_provenance_not_bound_to_invocation_or_package(
    provenance_change: dict[str, object],
) -> None:
    with (
        _trusted_invocation() as invocation,
        pytest.raises(ValueError, match="must match invocation"),
    ):
        build_context_run_records(
            invocation=invocation,
            request=Acquire(need=ContextNeed(query="safe query")),
            provenance=replace(_provenance(), **cast(Any, provenance_change)),
            package=_authorized_package(),
            final_effective_scope=FINAL_EFFECTIVE_SCOPE,
            effective_budget=EFFECTIVE_BUDGET,
            keyring=QUERY_KEYRING,
        )


@pytest.mark.parametrize(
    "lineage_change",
    [
        {"run_ref": "run-other"},
        {"principal_ref": "principal-other"},
        {"purpose": "context.other"},
        {"as_of": FINALIZED_AT + timedelta(microseconds=1)},
        {"decision_ref": "dec_" + "f" * 32},
        {"policy_snapshot_ref": "policy-other"},
        {"policy_epoch": 8},
        {"source_acl_decision_ref": "source-acl-other"},
    ],
)
def test_projection_rejects_evidence_lineage_not_bound_to_provenance(
    lineage_change: dict[str, object],
) -> None:
    package = _authorized_package()
    evidence = package.evidence[0]
    changed_evidence = replace(
        evidence,
        lineage=replace(
            evidence.lineage,
            **cast(Any, lineage_change),
        ),
    )
    object.__setattr__(package, "evidence", (changed_evidence,))
    object.__setattr__(
        package,
        "package_digest",
        context_package_digest(context_package_digest_document(package)),
    )

    with (
        _trusted_invocation() as invocation,
        pytest.raises(ValueError, match="Evidence lineage must match"),
    ):
        build_context_run_records(
            invocation=invocation,
            request=Acquire(need=ContextNeed(query="safe query")),
            provenance=_provenance(),
            package=package,
            final_effective_scope=FINAL_EFFECTIVE_SCOPE,
            effective_budget=EFFECTIVE_BUDGET,
            keyring=QUERY_KEYRING,
        )


def test_projection_rejects_package_purpose_or_time_not_bound_to_invocation() -> None:
    packages = (
        replace(_empty_package(), purpose="context.other"),
        replace(
            _empty_package(),
            as_of=ACCEPTED_AT - timedelta(seconds=1),
            expires_at=ACCEPTED_AT + timedelta(seconds=299),
        ),
    )

    with _trusted_invocation() as invocation:
        for package in packages:
            with pytest.raises(ValueError, match="must match invocation"):
                build_context_run_records(
                    invocation=invocation,
                    request=Acquire(need=ContextNeed(query="safe query")),
                    provenance=replace(_provenance(), as_of=package.as_of),
                    package=package,
                    final_effective_scope=FINAL_EFFECTIVE_SCOPE,
                    effective_budget=EFFECTIVE_BUDGET,
                    keyring=QUERY_KEYRING,
                )


def test_projection_rejects_package_altered_after_digest_creation() -> None:
    package = _authorized_package()
    object.__setattr__(package, "ttl_seconds", package.ttl_seconds + 1)

    with (
        _trusted_invocation() as invocation,
        pytest.raises(ValueError, match="digest must match"),
    ):
        build_context_run_records(
            invocation=invocation,
            request=Acquire(need=ContextNeed(query="safe query")),
            provenance=_provenance(),
            package=package,
            final_effective_scope=FINAL_EFFECTIVE_SCOPE,
            effective_budget=EFFECTIVE_BUDGET,
            keyring=QUERY_KEYRING,
        )


@pytest.mark.security_evidence(id="PROP-TRACE-REDACTION-012", layer="property")
def test_records_retain_digests_not_raw_query_package_or_denial_details() -> None:
    run = _run()
    audit = _audit()
    run_fields = {item.name for item in fields(run)}
    audit_fields = {item.name for item in fields(audit)}

    assert {
        "query_digest_profile",
        "query_digest_key_version",
        "query_digest",
        "package_digest_profile",
        "package_digest",
        "package_retention_mode",
    } <= run_fields
    assert run.package_retention_mode == "digest_only"
    assert not {"query", "package", "blocks", "denied_refs", "denied_count"} & (
        run_fields | audit_fields
    )
    assert audit_fields == {
        "organization_id",
        "run_ref",
        "decision_ref",
        "policy_snapshot_ref",
        "policy_epoch",
        "category",
        "recorded_at",
    }
    rendered = f"{run!r} {audit!r}"
    assert "principal-context-run-secret" not in rendered
    assert "binding-context-run-secret" not in rendered
    assert "2" * 64 not in rendered


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": ContextRunOutcome.DELIVERED_EMPTY},
        {"authorized_evidence_refs": ()},
        {"authorized_evidence_refs": (EVIDENCE_REF, EVIDENCE_REF)},
    ],
)
def test_outcome_and_authorized_evidence_shape_are_exact(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=r"empty|requires|unique"):
        _run(**changes)


@pytest.mark.parametrize(
    "evidence_ref",
    (
        "evidence-unbounded",
        "ev_" + "a" * 63,
        "ev_" + "A" * 64,
        "xx_" + "a" * 64,
    ),
)
def test_authorized_evidence_refs_use_the_exact_public_format(
    evidence_ref: str,
) -> None:
    with pytest.raises(ValueError, match="closed opaque format"):
        _run(authorized_evidence_refs=(evidence_ref,))


@pytest.mark.parametrize(
    ("usage_field", "ceiling"),
    [
        ("usage_tokens", 1_000),
        ("usage_provider_calls", 8),
        ("usage_cost_microunits", 25_000),
        ("usage_elapsed_ms", 2_500),
    ],
)
def test_usage_cannot_exceed_any_effective_budget_dimension(
    usage_field: str,
    ceiling: int,
) -> None:
    with pytest.raises(ValueError, match="must not exceed its ceiling"):
        _run(**{usage_field: ceiling + 1})


@pytest.mark.parametrize(
    "changes",
    [
        {"effective_scope_digest": "1" * 63},
        {"query_digest": "A" * 64},
        {"package_digest": "3" * 63},
        {"query_digest_profile": "unversioned-query-profile"},
        {"query_digest_key_version": 0},
        {"package_digest_profile": "unversioned-package-profile"},
        {"package_retention_mode": "full_package"},
    ],
)
def test_digest_profiles_key_version_and_retention_mode_are_closed(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=r"digest|profile|key version|retention"):
        _run(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"accepted_at": ACCEPTED_AT.replace(tzinfo=None)},
        {"finalized_at": FINALIZED_AT.astimezone(timezone(timedelta(hours=8)))},
        {"package_as_of": FINALIZED_AT + timedelta(microseconds=1)},
        {"package_expires_at": FINALIZED_AT},
        {
            "accepted_at": FINALIZED_AT + timedelta(seconds=1),
        },
    ],
)
def test_run_timestamps_are_utc_monotonic_and_package_bound(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=r"UTC|finalization|as-of|expiry"):
        _run(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"policy_epoch": 0},
        {"recorded_at": FINALIZED_AT.replace(tzinfo=None)},
        {"category": cast(DecisionAuditCategory, "denied_resource")},
    ],
)
def test_decision_audit_category_epoch_and_time_are_closed(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match=r"policy_epoch|UTC|category"):
        _audit(**changes)


def test_persistence_accepts_only_the_exact_audit_shape_for_each_outcome() -> None:
    port = _RecordingPort()
    scope = _open_context_run_persistence_scope()
    session = _construct_context_run_persistence_session(
        authority_scope=scope,
        port=cast(ContextRunPersistencePort, port),
    )
    authorized = _run()
    empty = _empty_run()
    audit = _audit()

    persist_context_run(session, authorized, None)
    persist_context_run(session, empty, audit)
    assert port.calls == [(authorized, None), (empty, audit)]

    with pytest.raises(ValueError, match="empty ContextRun"):
        persist_context_run(session, authorized, audit)
    with pytest.raises(ValueError, match="requires restricted DecisionAudit"):
        persist_context_run(session, empty, None)
    assert port.calls == [(authorized, None), (empty, audit)]
    _close_context_run_persistence_scope(scope)


@pytest.mark.parametrize(
    "audit",
    [
        _audit(organization_id=OTHER_ORGANIZATION_ID),
        _audit(run_ref="run_other"),
        _audit(decision_ref="dec_other"),
        _audit(policy_snapshot_ref="policy_other"),
        _audit(policy_epoch=8),
    ],
)
def test_persistence_rejects_cross_run_or_cross_organization_audit(
    audit: DecisionAuditRecord,
) -> None:
    port = _RecordingPort()
    scope = _open_context_run_persistence_scope()
    session = _construct_context_run_persistence_session(
        authority_scope=scope,
        port=cast(ContextRunPersistencePort, port),
    )

    with pytest.raises(ValueError, match="must match"):
        persist_context_run(session, _empty_run(), audit)

    assert port.calls == []
    _close_context_run_persistence_scope(scope)


def test_generic_port_failure_is_wrapped_without_exposing_sensitive_detail() -> None:
    secret = "raw database diagnostic with tenant-secret"

    class _FailingPort:
        def persist(
            self,
            run: ContextRunRecord,
            audit: DecisionAuditRecord | None,
        ) -> None:
            del run, audit
            raise RuntimeError(secret)

    scope = _open_context_run_persistence_scope()
    session = _construct_context_run_persistence_session(
        authority_scope=scope,
        port=cast(ContextRunPersistencePort, _FailingPort()),
    )

    with pytest.raises(ContextRunPersistenceUnavailable) as rejected:
        persist_context_run(session, _run(), None)

    assert str(rejected.value) == "ContextRun persistence authority failed"
    assert secret not in str(rejected.value)
    assert secret not in repr(rejected.value)
    _close_context_run_persistence_scope(scope)


def test_persistence_session_expires_with_its_owning_transaction_scope() -> None:
    port = _RecordingPort()
    scope = _open_context_run_persistence_scope()
    session = _construct_context_run_persistence_session(
        authority_scope=scope,
        port=cast(ContextRunPersistencePort, port),
    )
    _close_context_run_persistence_scope(scope)

    with pytest.raises(ValueError, match="active scope"):
        persist_context_run(session, _run(), None)

    assert port.calls == []


def test_persistence_capability_is_nominal_nonserializable_and_redacted() -> None:
    with pytest.raises(TypeError, match="authority-constructed"):
        ContextRunPersistenceSession()

    scope = _open_context_run_persistence_scope()
    session = _construct_context_run_persistence_session(
        authority_scope=scope,
        port=cast(ContextRunPersistencePort, _RecordingPort()),
    )

    assert repr(session) == "ContextRunPersistenceSession()"
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(session)
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(scope)
    _close_context_run_persistence_scope(scope)


def test_persistence_session_rejects_an_incomplete_port() -> None:
    scope = _open_context_run_persistence_scope()

    with pytest.raises(TypeError, match="port is incomplete"):
        _construct_context_run_persistence_session(
            authority_scope=scope,
            port=cast(ContextRunPersistencePort, object()),
        )

    _close_context_run_persistence_scope(scope)
