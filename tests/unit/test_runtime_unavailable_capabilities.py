from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

from adapters.http.scope_authority import ScopeAuthorityIdentity
from engine.runtime import capabilities as capability_module
from engine.runtime.actor import (
    _close_membership_authority_scope,
    _construct_current_membership_verification,
    _open_membership_authority_scope,
)
from engine.runtime.budget import PackageBudgetRequest
from engine.runtime.capabilities import (
    M0_RUNTIME_CAPABILITY_DECLARATION,
    RuntimeCapability,
    RuntimeCapabilityDeclaration,
    RuntimeRefusalCategory,
)
from engine.runtime.construction import (
    Runtime,
    RuntimeConfigurationError,
    required_kernel_dependencies,
)
from engine.runtime.content_io import RuntimeContentIo
from engine.runtime.context_run import (
    ContextRunPersistenceSession,
    ContextRunPersistenceUnavailable,
)
from engine.runtime.contracts import (
    Acquire,
    CitationNotAvailable,
    CitationOpenRef,
    ContextNeed,
    ContinuationToken,
    Continue,
    OpenCitation,
    RequestNotAvailable,
    Resolved,
)
from engine.runtime.delivery import (
    TrustedDeliveryContext,
    _construct_direct_delivery_context,
)
from engine.runtime.invocation import (
    AuthenticatedInvocation,
    _construct_authenticated_http_invocation,
)
from engine.runtime.organization import (
    _construct_existing_http_organization_verification,
)
from engine.runtime.policy_epoch import (
    _close_policy_epoch_authority_scope,
    _construct_policy_epoch_session,
    _observe_current_policy_epoch,
    _open_policy_epoch_authority_scope,
)
from engine.runtime.scope import MISSING_TRUSTED_SCOPE
from engine.runtime.scope_authority import (
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)
from tests.support.context_run import (
    TEST_QUERY_DIGEST_KEYRING,
    recording_context_run_session,
)

AS_OF = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("81e18bca-86a1-478a-937d-7675c6fe69b0")
USER_ID = UUID("d3d9893f-82d2-4890-8cb2-4c7e57a56f16")
MEMBERSHIP_ID = UUID("9c9e9f4c-a5ec-4417-9408-0346e1c6c998")


class ContentIoTwin:
    """Instrument every content dependency named by the M0 refusal oracle."""

    def __init__(self) -> None:
        self.index_calls = 0
        self.provider_calls = 0
        self.source_calls = 0

    def discover(self, request: Acquire) -> tuple[()]:
        del request
        self.index_calls += 1
        return ()

    def authorize_and_project(self) -> tuple[()]:
        self.provider_calls += 1
        return ()

    def read_content(self) -> tuple[()]:
        self.source_calls += 1
        return ()

    @property
    def calls(self) -> tuple[int, int, int]:
        return self.index_calls, self.provider_calls, self.source_calls


@contextmanager
def trusted_operands(
    *,
    purpose: str,
    context_run_persistence_session: ContextRunPersistenceSession | None = None,
) -> Iterator[tuple[AuthenticatedInvocation, TrustedDeliveryContext]]:
    membership_scope = _open_membership_authority_scope()
    epoch_scope = _open_policy_epoch_authority_scope()
    trusted_scope = _open_scope_authority_scope()
    try:
        class CurrentEpochPort:
            def read_current_epoch(self, organization_id: UUID) -> object:
                assert organization_id == ORGANIZATION_ID
                return 9

        epoch_verification = _observe_current_policy_epoch(
            _construct_policy_epoch_session(
                authority_scope=epoch_scope,
                organization_id=ORGANIZATION_ID,
                port=CurrentEpochPort(),
            )
        )
        organization_verification = (
            _construct_existing_http_organization_verification(
                organization_id=ORGANIZATION_ID,
                request_id="unavailable-request",
                authentication_binding_ref="unavailable-binding",
                verified_at=AS_OF,
            )
        )
        membership_verification = _construct_current_membership_verification(
            authority_scope=membership_scope,
            organization_id=ORGANIZATION_ID,
            user_id=USER_ID,
            membership_id=MEMBERSHIP_ID,
            membership_version=5,
            principal_ref="principal-unavailable",
            request_id="unavailable-request",
            authentication_binding_ref="unavailable-binding",
            checked_at=AS_OF,
            policy_epoch_verification=epoch_verification,
            context_run_persistence_session=context_run_persistence_session,
        )
        scope_identity = ScopeAuthorityIdentity(
            organization_id=ORGANIZATION_ID,
            user_id=USER_ID,
            membership_id=MEMBERSHIP_ID,
            membership_version=5,
            policy_epoch=9,
            principal_ref="principal-unavailable",
            agent_version_ref="agent-unavailable",
            purpose=purpose,
            request_id="unavailable-request",
            authentication_binding_ref="unavailable-binding",
            checked_at=AS_OF,
        )
        scope_snapshot = _construct_trusted_scope_snapshot(
            authority_scope=trusted_scope,
            organization_id=scope_identity.organization_id,
            user_id=scope_identity.user_id,
            membership_id=scope_identity.membership_id,
            membership_version=scope_identity.membership_version,
            policy_epoch=scope_identity.policy_epoch,
            principal_ref=scope_identity.principal_ref,
            agent_version_ref=scope_identity.agent_version_ref,
            purpose=scope_identity.purpose,
            request_id=scope_identity.request_id,
            authentication_binding_ref=scope_identity.authentication_binding_ref,
            checked_at=scope_identity.checked_at,
            organization_boundary=MISSING_TRUSTED_SCOPE,
            membership_rights=MISSING_TRUSTED_SCOPE,
            principal_grants=MISSING_TRUSTED_SCOPE,
            agent_ceiling=MISSING_TRUSTED_SCOPE,
            source_native_acl=MISSING_TRUSTED_SCOPE,
            resource_acl=MISSING_TRUSTED_SCOPE,
            purpose_policy=MISSING_TRUSTED_SCOPE,
        )
        invocation = _construct_authenticated_http_invocation(
            request_id="unavailable-request",
            authenticated_organization_ref=str(ORGANIZATION_ID),
            organization_verification=organization_verification,
            user_ref=str(USER_ID),
            principal_ref="principal-unavailable",
            membership_ref=str(MEMBERSHIP_ID),
            membership_version=5,
            current_membership_verification=membership_verification,
            agent_version_ref="agent-unavailable",
            authenticated_application_ref="application-unavailable",
            authentication_binding_ref="unavailable-binding",
            trusted_purpose=purpose,
            received_at=AS_OF,
            trusted_scope_snapshot=scope_snapshot,
        )
        delivery = _construct_direct_delivery_context(
            purpose=purpose,
            authenticated_application_ref="application-unavailable",
            delivery_binding_ref="unavailable-binding",
            established_at=AS_OF,
        )
        yield invocation, delivery
    finally:
        _close_scope_authority_scope(trusted_scope)
        _close_policy_epoch_authority_scope(epoch_scope)
        _close_membership_authority_scope(membership_scope)


def runtime(
    twin: ContentIoTwin,
    *,
    acquire_capability: RuntimeCapability = RuntimeCapability.MATERIALIZED_ACQUIRE,
) -> Runtime:
    return Runtime(
        required_kernel_dependencies(),
        content_io=RuntimeContentIo(
            index=twin,
            provider=twin,
            source_content=twin,
        ),
        candidate_index=twin,
        acquire_capability=acquire_capability,
        clock=lambda: AS_OF,
        query_digest_keyring=TEST_QUERY_DIGEST_KEYRING,
    )


@pytest.mark.parametrize(
    ("runtime_request", "purpose", "expected_type"),
    [
        (
            Continue(
                continuation_token=ContinuationToken("opaque-stale-token"),
                package_budget=PackageBudgetRequest(max_tokens=1),
            ),
            "context.answer",
            RequestNotAvailable,
        ),
        (
            OpenCitation(citation_open_ref=CitationOpenRef("opaque-citation-ref")),
            "citation.open",
            CitationNotAvailable,
        ),
    ],
    ids=("ACCEPT-005-continue", "ACCEPT-010-open-citation"),
)
def test_known_unavailable_runtime_variants_return_generic_typed_outcomes_before_io(
    runtime_request: Continue | OpenCitation,
    purpose: str,
    expected_type: type[RequestNotAvailable] | type[CitationNotAvailable],
) -> None:
    twin = ContentIoTwin()

    with trusted_operands(purpose=purpose) as (invocation, delivery):
        runtime_instance = runtime(twin)
        outcome = runtime_instance.resolve(invocation, delivery, runtime_request)

    assert type(outcome) is expected_type
    assert not hasattr(outcome, "audit_receipt")
    assert runtime_instance._unsupported_capability_audit_snapshot() == (
        RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY,
        1,
        0,
    )
    assert twin.calls == (0, 0, 0)
    serialized_public_shape = (
        {"kind": "request_not_available", "retryable": False}
        if type(outcome) is RequestNotAvailable
        else {"kind": "citation_not_available"}
    )
    assert "opaque" not in repr(serialized_public_shape)


@pytest.mark.parametrize(
    "acquire_capability",
    (
        RuntimeCapability.FEDERATED_DISCOVERY,
        RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION,
    ),
    ids=("federated", "ACCEPT-009-source-native"),
)
def test_server_owned_unavailable_acquire_plan_fails_before_any_content_io(
    acquire_capability: RuntimeCapability,
) -> None:
    twin = ContentIoTwin()

    with (
        recording_context_run_session() as (persistence_session, persistence_port),
        trusted_operands(
            purpose="context.answer",
            context_run_persistence_session=persistence_session,
        ) as (invocation, delivery),
    ):
        selected_runtime = runtime(twin, acquire_capability=acquire_capability)
        outcome = selected_runtime.resolve(
            invocation,
            delivery,
            cast(
                Any,
                Acquire(need=ContextNeed(query="unavailable source behavior")),
            ),
        )

    assert outcome == RequestNotAvailable()
    assert selected_runtime._unsupported_capability_audit_snapshot() == (
        RuntimeRefusalCategory.UNSUPPORTED_CAPABILITY,
        1,
        0,
    )
    assert twin.calls == (0, 0, 0)
    assert persistence_port.calls == []


def test_content_twins_are_observable_controls_for_every_prohibited_call() -> None:
    twin = ContentIoTwin()
    content_io = RuntimeContentIo(index=twin, provider=twin, source_content=twin)

    content_io.index.discover(Acquire(need=ContextNeed(query="control")))
    content_io.provider.authorize_and_project()
    content_io.source_content.read_content()

    assert twin.calls == (1, 1, 1)


def test_runtime_rejects_a_split_candidate_index_composition() -> None:
    twin = ContentIoTwin()
    with pytest.raises(RuntimeConfigurationError, match="composed content_io index"):
        Runtime(
            required_kernel_dependencies(),
            content_io=RuntimeContentIo(
                index=twin,
                provider=twin,
                source_content=twin,
            ),
            candidate_index=ContentIoTwin(),
            clock=lambda: AS_OF,
        )


def test_default_materialized_acquire_path_remains_resolved() -> None:
    twin = ContentIoTwin()

    with (
        recording_context_run_session() as (persistence_session, persistence_port),
        trusted_operands(
            purpose="context.answer",
            context_run_persistence_session=persistence_session,
        ) as (invocation, delivery),
    ):
        outcome = runtime(twin).resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="existing M0 path")),
        )

    assert type(outcome) is Resolved
    assert outcome.kind == "resolved"
    assert len(persistence_port.calls) == 1
    assert twin.calls == (0, 0, 0)


def test_materialized_acquire_without_persistence_session_fails_closed() -> None:
    twin = ContentIoTwin()

    with (
        trusted_operands(purpose="context.answer") as (invocation, delivery),
        pytest.raises(
            ContextRunPersistenceUnavailable,
            match="requires durable ContextRun persistence",
        ),
    ):
        runtime(twin).resolve(
            invocation,
            delivery,
            Acquire(need=ContextNeed(query="missing persistence authority")),
        )

    assert twin.calls == (0, 0, 0)


def test_unknown_runtime_variant_is_rejected_and_never_treated_as_unavailable() -> None:
    twin = ContentIoTwin()

    with (
        trusted_operands(purpose="context.answer") as (invocation, delivery),
        pytest.raises(TypeError, match="closed Runtime request"),
    ):
        runtime(twin).resolve(invocation, delivery, cast(Any, object()))

    assert twin.calls == (0, 0, 0)


@pytest.mark.parametrize(
    "runtime_request",
    (
        Continue(continuation_token=ContinuationToken("opaque-token")),
        OpenCitation(citation_open_ref=CitationOpenRef("opaque-citation")),
    ),
)
def test_unavailable_request_rejects_forged_runtime_operands_before_minting_outcome(
    runtime_request: Continue | OpenCitation,
) -> None:
    twin = ContentIoTwin()

    with pytest.raises(TypeError, match="trusted AuthenticatedInvocation"):
        runtime(twin).resolve(
            cast(Any, object()),
            cast(Any, object()),
            runtime_request,
        )

    assert twin.calls == (0, 0, 0)


def test_unavailable_request_runs_runtime_clock_provenance_and_policy_epoch_gate(
) -> None:
    twin = ContentIoTwin()
    selected_runtime = runtime(twin)
    clock_calls = 0

    def invalid_clock() -> Any:
        nonlocal clock_calls
        clock_calls += 1
        return "not-a-datetime"

    selected_runtime._clock = invalid_clock

    with (
        trusted_operands(purpose="context.answer") as (invocation, delivery),
        pytest.raises(ValueError, match="Runtime clock"),
    ):
        selected_runtime.resolve(
            invocation,
            delivery,
            Continue(continuation_token=ContinuationToken("opaque-token")),
        )

    assert clock_calls == 1
    assert twin.calls == (0, 0, 0)


def test_replacing_the_mandatory_capability_gate_is_rejected_before_io() -> None:
    twin = ContentIoTwin()
    selected_runtime = runtime(twin)
    selected_runtime._capability_gate = cast(Any, object())

    with (
        trusted_operands(purpose="context.answer") as (invocation, delivery),
        pytest.raises(RuntimeConfigurationError, match="capability gate"),
    ):
        selected_runtime.resolve(
            invocation,
            delivery,
            Continue(continuation_token=ContinuationToken("opaque-token")),
        )

    assert twin.calls == (0, 0, 0)


@pytest.mark.security_evidence(id="PROP-CITATION-AUTH-010", layer="property")
def test_capability_declarations_are_closed_and_m0_does_not_false_green_carriers(
) -> None:
    assert capability_module.__all__ == ["RuntimeCapability"]
    assert RuntimeCapabilityDeclaration(
        available=frozenset({RuntimeCapability.MATERIALIZED_ACQUIRE})
    ) == M0_RUNTIME_CAPABILITY_DECLARATION
    assert not {
        RuntimeCapability.CONTINUE,
        RuntimeCapability.OPEN_CITATION,
        RuntimeCapability.FEDERATED_DISCOVERY,
        RuntimeCapability.SOURCE_NATIVE_AUTHORIZATION,
    }.intersection(M0_RUNTIME_CAPABILITY_DECLARATION.available)

    with pytest.raises(TypeError, match="RuntimeCapability"):
        RuntimeCapabilityDeclaration(available=cast(Any, frozenset({"unknown"})))
    with pytest.raises(RuntimeConfigurationError, match="acquire capability"):
        Runtime(
            required_kernel_dependencies(),
            acquire_capability=cast(Any, "undeclared"),
        )
    with pytest.raises(RuntimeConfigurationError, match="acquire capability"):
        Runtime(
            required_kernel_dependencies(),
            acquire_capability=RuntimeCapability.CONTINUE,
        )


def test_opaque_capability_inputs_are_distinct_bounded_immutable_and_redacted() -> None:
    continuation = ContinuationToken("continuation-secret")
    citation = CitationOpenRef("citation-secret")

    assert continuation.value == "continuation-secret"
    assert citation.value == "citation-secret"
    assert "continuation-secret" not in repr(continuation)
    assert "citation-secret" not in repr(citation)
    with pytest.raises(FrozenInstanceError):
        continuation.value = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError, match="ContinuationToken"):
        Continue(continuation_token=cast(Any, citation))
    with pytest.raises(TypeError, match="CitationOpenRef"):
        OpenCitation(citation_open_ref=cast(Any, continuation))
    for constructor in (ContinuationToken, CitationOpenRef):
        for invalid in ("", " ", "contains whitespace", "x" * 4097):
            with pytest.raises(ValueError, match="opaque"):
                constructor(invalid)
