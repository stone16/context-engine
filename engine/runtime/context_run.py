"""Lifetime-bound durable ContextRun and restricted DecisionAudit contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final, NoReturn, Protocol, cast
from uuid import UUID

from engine.runtime.budget import PackageBudget
from engine.runtime.contracts import (
    Acquire,
    ContextPackage,
    CoverageStatus,
    context_package_digest_document,
)
from engine.runtime.package_digest import (
    PACKAGE_DIGEST_PROFILE,
    QUERY_DIGEST_PROFILE,
    QueryDigestKeyring,
    query_digest,
    verify_context_package_digest,
)

if TYPE_CHECKING:
    from engine.runtime.scope import EffectiveScope

MAX_SIGNED_BIGINT: Final = (1 << 63) - 1
PACKAGE_RETENTION_MODE: Final = "digest_only"
PACKAGE_RETENTION_POLICY_REF: Final = "package-digest-only-retention-v1"


class ContextRunPersistenceUnavailable(RuntimeError):
    """The durable run lineage could not be committed safely."""


class ContextRunOutcome(StrEnum):
    """Tenant-safe terminal outcomes active for Acquire."""

    DELIVERED_AUTHORIZED = "delivered_authorized"
    DELIVERED_EMPTY = "delivered_empty"


class DecisionAuditCategory(StrEnum):
    """Restricted closed category with no denied-object detail."""

    NO_AUTHORIZED_EVIDENCE = "no_authorized_evidence"


def _require_nonblank(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"ContextRun {field_name} must be nonblank")
    return value


def _require_digest(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"ContextRun {field_name} must be lowercase SHA-256")
    return value


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"ContextRun {field_name} must be aware UTC")
    return value


@dataclass(frozen=True, slots=True)
class ContextRunRecord:
    """Authorized-only final lineage for one successful Acquire delivery."""

    organization_id: UUID = field(repr=False)
    run_ref: str
    decision_ref: str
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)
    principal_ref: str = field(repr=False)
    agent_version_ref: str = field(repr=False)
    authenticated_application_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    request_id: str = field(repr=False)
    purpose: str
    policy_snapshot_ref: str
    policy_epoch: int
    effective_scope_digest: str = field(repr=False)
    query_digest_profile: str = field(repr=False)
    query_digest_key_version: int = field(repr=False)
    query_digest: str = field(repr=False)
    outcome: ContextRunOutcome
    package_digest_profile: str
    package_digest: str
    package_retention_mode: str
    authorized_evidence_refs: tuple[str, ...]
    effective_max_tokens: int
    effective_max_provider_calls: int
    effective_max_cost_microunits: int
    effective_max_elapsed_ms: int
    usage_tokens: int
    usage_provider_calls: int
    usage_cost_microunits: int
    usage_elapsed_ms: int
    accepted_at: datetime
    finalized_at: datetime
    package_as_of: datetime
    package_expires_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("organization_id", "user_id", "membership_id"):
            if type(getattr(self, field_name)) is not UUID:
                raise TypeError(f"ContextRun {field_name} must be UUID")
        for field_name in (
            "run_ref",
            "decision_ref",
            "principal_ref",
            "agent_version_ref",
            "authenticated_application_ref",
            "authentication_binding_ref",
            "request_id",
            "purpose",
            "policy_snapshot_ref",
        ):
            _require_nonblank(field_name, getattr(self, field_name))
        for field_name in ("membership_version", "policy_epoch"):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value <= MAX_SIGNED_BIGINT:
                raise ValueError(f"ContextRun {field_name} must be positive bigint")
        _require_digest("effective_scope_digest", self.effective_scope_digest)
        _require_digest("query_digest", self.query_digest)
        _require_digest("package_digest", self.package_digest)
        if self.query_digest_profile != QUERY_DIGEST_PROFILE:
            raise ValueError("ContextRun query digest profile is not active")
        if (
            type(self.query_digest_key_version) is not int
            or not 1 <= self.query_digest_key_version <= MAX_SIGNED_BIGINT
        ):
            raise ValueError("ContextRun query digest key version must be positive")
        if type(self.outcome) is not ContextRunOutcome:
            raise TypeError("ContextRun outcome must be ContextRunOutcome")
        if self.package_digest_profile != PACKAGE_DIGEST_PROFILE:
            raise ValueError("ContextRun package digest profile is not active")
        if self.package_retention_mode != PACKAGE_RETENTION_MODE:
            raise ValueError("ContextRun Package retention must remain digest_only")
        if type(self.authorized_evidence_refs) is not tuple:
            raise TypeError("ContextRun authorized Evidence refs must be a tuple")
        if any(
            type(value) is not str
            or len(value) != 67
            or not value.startswith("ev_")
            or any(character not in "0123456789abcdef" for character in value[3:])
            for value in self.authorized_evidence_refs
        ):
            raise ValueError(
                "ContextRun authorized Evidence refs must use the closed opaque format"
            )
        if len(set(self.authorized_evidence_refs)) != len(
            self.authorized_evidence_refs
        ):
            raise ValueError("ContextRun authorized Evidence refs must be unique")
        if self.outcome is ContextRunOutcome.DELIVERED_EMPTY:
            if self.authorized_evidence_refs:
                raise ValueError("empty ContextRun cannot contain Evidence refs")
        elif not self.authorized_evidence_refs:
            raise ValueError("authorized ContextRun requires Evidence refs")
        for field_name in (
            "effective_max_tokens",
            "effective_max_provider_calls",
            "effective_max_cost_microunits",
            "effective_max_elapsed_ms",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"ContextRun {field_name} must be positive")
        for usage_field in (
            "usage_tokens",
            "usage_provider_calls",
            "usage_cost_microunits",
            "usage_elapsed_ms",
        ):
            value = getattr(self, usage_field)
            if type(value) is not int or value < 0:
                raise ValueError(f"ContextRun {usage_field} must be nonnegative")
        for usage_field, ceiling_field in (
            ("usage_tokens", "effective_max_tokens"),
            ("usage_provider_calls", "effective_max_provider_calls"),
            ("usage_cost_microunits", "effective_max_cost_microunits"),
            ("usage_elapsed_ms", "effective_max_elapsed_ms"),
        ):
            if getattr(self, usage_field) > getattr(self, ceiling_field):
                raise ValueError(
                    f"ContextRun {usage_field} must not exceed its ceiling"
                )
        for field_name in (
            "accepted_at",
            "finalized_at",
            "package_as_of",
            "package_expires_at",
        ):
            _require_utc(field_name, getattr(self, field_name))
        if self.finalized_at < self.accepted_at:
            raise ValueError("ContextRun finalization cannot precede acceptance")
        if self.package_as_of != self.finalized_at:
            raise ValueError("ContextRun Package as-of must equal finalization")
        if self.package_expires_at <= self.package_as_of:
            raise ValueError("ContextRun Package expiry must follow as-of")


@dataclass(frozen=True, slots=True)
class DecisionAuditRecord:
    """Restricted empty-decision lineage without denied identifiers or counts."""

    organization_id: UUID = field(repr=False)
    run_ref: str
    decision_ref: str
    policy_snapshot_ref: str
    policy_epoch: int
    category: DecisionAuditCategory
    recorded_at: datetime

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("DecisionAudit organization_id must be UUID")
        for field_name in ("run_ref", "decision_ref", "policy_snapshot_ref"):
            _require_nonblank(field_name, getattr(self, field_name))
        if (
            type(self.policy_epoch) is not int
            or not 1 <= self.policy_epoch <= MAX_SIGNED_BIGINT
        ):
            raise ValueError("DecisionAudit policy_epoch must be positive bigint")
        if self.category is not DecisionAuditCategory.NO_AUTHORIZED_EVIDENCE:
            raise ValueError("DecisionAudit category must remain closed")
        _require_utc("recorded_at", self.recorded_at)


class ContextRunPersistencePort(Protocol):
    """Narrow write owned by the retained current-UserActor transaction."""

    def persist(
        self,
        run: ContextRunRecord,
        audit: DecisionAuditRecord | None,
    ) -> None: ...


class DecisionProvenance(Protocol):
    package_id: str
    organization_id: UUID
    user_id: UUID
    membership_id: UUID
    membership_version: int
    principal_ref: str
    agent_version_ref: str
    authenticated_application_ref: str
    authentication_binding_ref: str
    request_id: str
    purpose: str
    as_of: datetime
    run_ref: str
    decision_ref: str
    policy_snapshot_ref: str
    policy_epoch: int
    effective_scope_digest: str
    source_acl_decision_ref: str


class _ContextRunPersistenceScope:
    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("ContextRun persistence scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("ContextRun persistence scopes are not serializable")


_CONTEXT_RUN_PERSISTENCE_SCOPE_SEAL = object()


def _open_context_run_persistence_scope() -> _ContextRunPersistenceScope:
    scope = object.__new__(_ContextRunPersistenceScope)
    scope._active = True
    scope._seal = _CONTEXT_RUN_PERSISTENCE_SCOPE_SEAL
    return scope


def _close_context_run_persistence_scope(
    scope: _ContextRunPersistenceScope,
) -> None:
    if (
        type(scope) is not _ContextRunPersistenceScope
        or getattr(scope, "_seal", None) is not _CONTEXT_RUN_PERSISTENCE_SCOPE_SEAL
    ):
        raise TypeError("ContextRun persistence scope has the wrong nominal type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class ContextRunPersistenceSession:
    """Nominal final-write capability valid only in its owning transaction."""

    _authority_scope: _ContextRunPersistenceScope = field(repr=False)
    _port: ContextRunPersistencePort = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("ContextRunPersistenceSession is authority-constructed")

    def __reduce__(self) -> NoReturn:
        raise TypeError("ContextRunPersistenceSession is not serializable")


def _require_active_context_run_persistence_session(
    session: ContextRunPersistenceSession,
) -> None:
    if type(session) is not ContextRunPersistenceSession:
        raise TypeError("ContextRun persistence session has the wrong nominal type")
    scope = session._authority_scope
    if (
        type(scope) is not _ContextRunPersistenceScope
        or getattr(scope, "_seal", None) is not _CONTEXT_RUN_PERSISTENCE_SCOPE_SEAL
        or not getattr(scope, "_active", False)
    ):
        raise ValueError("ContextRun persistence requires an active scope")


def _construct_context_run_persistence_session(
    *,
    authority_scope: _ContextRunPersistenceScope,
    port: ContextRunPersistencePort,
) -> ContextRunPersistenceSession:
    session = object.__new__(ContextRunPersistenceSession)
    object.__setattr__(session, "_authority_scope", authority_scope)
    object.__setattr__(session, "_port", port)
    _require_active_context_run_persistence_session(session)
    if not callable(getattr(port, "persist", None)):
        raise TypeError("ContextRun persistence port is incomplete")
    return session


def persist_context_run(
    session: ContextRunPersistenceSession,
    run: ContextRunRecord,
    audit: DecisionAuditRecord | None,
) -> None:
    """Persist one finalized run and optional restricted audit atomically."""

    _require_active_context_run_persistence_session(session)
    if type(run) is not ContextRunRecord:
        raise TypeError("ContextRun persistence requires ContextRunRecord")
    if audit is not None:
        if type(audit) is not DecisionAuditRecord:
            raise TypeError("ContextRun persistence audit has the wrong type")
        if (
            audit.organization_id != run.organization_id
            or audit.run_ref != run.run_ref
            or audit.decision_ref != run.decision_ref
            or audit.policy_snapshot_ref != run.policy_snapshot_ref
            or audit.policy_epoch != run.policy_epoch
            or run.outcome is not ContextRunOutcome.DELIVERED_EMPTY
        ):
            raise ValueError("DecisionAudit must match its empty ContextRun")
    elif run.outcome is ContextRunOutcome.DELIVERED_EMPTY:
        raise ValueError("empty ContextRun requires restricted DecisionAudit")
    try:
        session._port.persist(run, audit)
    except ContextRunPersistenceUnavailable:
        raise
    except Exception as error:
        raise ContextRunPersistenceUnavailable(
            "ContextRun persistence authority failed"
        ) from error


def build_context_run_records(
    *,
    invocation: object,
    request: Acquire,
    provenance: object,
    package: ContextPackage,
    final_effective_scope: EffectiveScope,
    effective_budget: PackageBudget,
    keyring: QueryDigestKeyring,
) -> tuple[ContextRunRecord, DecisionAuditRecord | None]:
    """Project one finalized Package into safe durable lineage."""

    from engine.runtime.invocation import AuthenticatedInvocation
    from engine.runtime.scope import (
        OMITTED_REQUEST_NARROWING,
        EffectiveScope,
        compute_effective_scope,
    )
    from engine.runtime.scope_authority import _trusted_operands_from_snapshot

    if type(invocation) is not AuthenticatedInvocation:
        raise TypeError("ContextRun projection requires AuthenticatedInvocation")
    if type(request) is not Acquire:
        raise TypeError("ContextRun projection requires Acquire")
    if type(package) is not ContextPackage:
        raise TypeError("ContextRun projection requires ContextPackage")
    active_release = invocation.user_actor.active_runtime_release
    if active_release is None:
        raise ValueError("ContextRun requires an active Runtime release")
    if type(final_effective_scope) is not EffectiveScope:
        raise TypeError("ContextRun projection requires final EffectiveScope")
    if type(effective_budget) is not PackageBudget:
        raise TypeError("ContextRun projection requires PackageBudget")
    if not verify_context_package_digest(
        context_package_digest_document(package),
        package.package_digest,
    ):
        raise ValueError("ContextRun Package digest must match its public document")
    required_provenance_fields = (
        "package_id",
        "organization_id",
        "user_id",
        "membership_id",
        "membership_version",
        "principal_ref",
        "agent_version_ref",
        "authenticated_application_ref",
        "authentication_binding_ref",
        "request_id",
        "purpose",
        "as_of",
        "run_ref",
        "decision_ref",
        "policy_snapshot_ref",
        "policy_epoch",
        "effective_scope_digest",
        "source_acl_decision_ref",
    )
    if any(not hasattr(provenance, name) for name in required_provenance_fields):
        raise TypeError("ContextRun projection requires decision provenance")
    decision_provenance = cast(DecisionProvenance, provenance)
    authorized_scope = compute_effective_scope(
        _trusted_operands_from_snapshot(invocation.trusted_scope_snapshot),
        request.narrowing
        if request.narrowing is not None
        else OMITTED_REQUEST_NARROWING,
    )
    if invocation.trusted_scope_snapshot.policy_epoch != invocation.policy_epoch:
        authorized_scope = EffectiveScope(frozenset())
    empty_scope = EffectiveScope(frozenset())
    if final_effective_scope not in (authorized_scope, empty_scope):
        raise ValueError("ContextRun final EffectiveScope must preserve or veto scope")
    if not final_effective_scope.targets and package.evidence:
        raise ValueError("ContextRun empty final EffectiveScope cannot carry Evidence")
    if (
        decision_provenance.organization_id != invocation.user_actor.organization_id
        or decision_provenance.user_id != invocation.user_actor.user_id
        or decision_provenance.membership_id != invocation.user_actor.membership_id
        or decision_provenance.membership_version
        != invocation.user_actor.membership_version
        or decision_provenance.principal_ref != invocation.principal_ref
        or decision_provenance.agent_version_ref != invocation.agent_version_ref
        or decision_provenance.authenticated_application_ref
        != invocation.authenticated_application_ref
        or decision_provenance.authentication_binding_ref
        != invocation.authentication_binding_ref
        or decision_provenance.request_id != invocation.request_id
        or decision_provenance.purpose != invocation.trusted_scope_snapshot.purpose
        or decision_provenance.as_of != package.as_of
        or decision_provenance.package_id != package.package_id
        or decision_provenance.decision_ref != package.decision_ref
        or decision_provenance.policy_epoch != invocation.policy_epoch
        or decision_provenance.effective_scope_digest != final_effective_scope.digest
        or package.purpose != invocation.trusted_scope_snapshot.purpose
        or package.policy_epoch != decision_provenance.policy_epoch
        or package.policy_snapshot_ref != decision_provenance.policy_snapshot_ref
        or package.run_ref != decision_provenance.run_ref
        or package.release_manifest_ref != active_release.manifest_ref
        or package.tokenizer_ref != active_release.tokenizer_ref
        or package.package_schema_ref != active_release.package_schema_ref
        or package.retention_policy_ref != PACKAGE_RETENTION_POLICY_REF
        or package.as_of < invocation.received_at
    ):
        raise ValueError("ContextRun Package and provenance must match invocation")
    if any(
        item.lineage.run_ref != decision_provenance.run_ref
        or item.lineage.principal_ref != decision_provenance.principal_ref
        or item.lineage.purpose != decision_provenance.purpose
        or item.lineage.as_of != decision_provenance.as_of
        or item.lineage.decision_ref != decision_provenance.decision_ref
        or item.lineage.policy_snapshot_ref != decision_provenance.policy_snapshot_ref
        or item.lineage.policy_epoch != decision_provenance.policy_epoch
        or item.lineage.source_acl_decision_ref
        != decision_provenance.source_acl_decision_ref
        for item in package.evidence
    ):
        raise ValueError("ContextRun Evidence lineage must match provenance")
    query_receipt = query_digest(
        keyring,
        invocation.user_actor.organization_id,
        request.need.query,
    )
    outcome = (
        ContextRunOutcome.DELIVERED_AUTHORIZED
        if package.coverage.status is CoverageStatus.SUFFICIENT
        else ContextRunOutcome.DELIVERED_EMPTY
    )
    run = ContextRunRecord(
        organization_id=invocation.user_actor.organization_id,
        run_ref=decision_provenance.run_ref,
        decision_ref=decision_provenance.decision_ref,
        user_id=invocation.user_actor.user_id,
        membership_id=invocation.user_actor.membership_id,
        membership_version=invocation.user_actor.membership_version,
        principal_ref=invocation.principal_ref,
        agent_version_ref=invocation.agent_version_ref,
        authenticated_application_ref=invocation.authenticated_application_ref,
        authentication_binding_ref=invocation.authentication_binding_ref,
        request_id=invocation.request_id,
        purpose=package.purpose,
        policy_snapshot_ref=decision_provenance.policy_snapshot_ref,
        policy_epoch=decision_provenance.policy_epoch,
        effective_scope_digest=decision_provenance.effective_scope_digest,
        query_digest_profile=query_receipt.profile,
        query_digest_key_version=query_receipt.key_version,
        query_digest=query_receipt.value,
        outcome=outcome,
        package_digest_profile=PACKAGE_DIGEST_PROFILE,
        package_digest=package.package_digest,
        package_retention_mode=PACKAGE_RETENTION_MODE,
        authorized_evidence_refs=tuple(item.evidence_ref for item in package.evidence),
        effective_max_tokens=effective_budget.max_tokens,
        effective_max_provider_calls=effective_budget.max_provider_calls,
        effective_max_cost_microunits=effective_budget.max_cost_microunits,
        effective_max_elapsed_ms=effective_budget.max_elapsed_ms,
        usage_tokens=package.budget_usage.tokens,
        usage_provider_calls=package.budget_usage.provider_calls,
        usage_cost_microunits=package.budget_usage.cost_microunits,
        usage_elapsed_ms=package.budget_usage.elapsed_ms,
        accepted_at=invocation.received_at,
        finalized_at=package.as_of,
        package_as_of=package.as_of,
        package_expires_at=package.expires_at,
    )
    audit = (
        DecisionAuditRecord(
            organization_id=run.organization_id,
            run_ref=run.run_ref,
            decision_ref=run.decision_ref,
            policy_snapshot_ref=run.policy_snapshot_ref,
            policy_epoch=run.policy_epoch,
            category=DecisionAuditCategory.NO_AUTHORIZED_EVIDENCE,
            recorded_at=run.finalized_at,
        )
        if run.outcome is ContextRunOutcome.DELIVERED_EMPTY
        else None
    )
    return run, audit
