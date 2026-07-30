from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime, timedelta
from inspect import signature
from pathlib import Path
from subprocess import run
from sys import executable
from typing import Any, cast, get_type_hints
from uuid import UUID

import pytest

from engine.runtime.budget import PackageBudget, PackageBudgetMeter
from engine.runtime.egress import (
    ChannelEgressGrant,
    EgressGrantRedemption,
    ModelEgressGrant,
    ModelEgressProfile,
)
from engine.runtime.evidence import (
    AuthorizedProjection,
    CandidateRef,
    EvidenceLineage,
    _close_authorization_kernel_scope,
    _construct_authorized_projection,
    _open_authorization_kernel_scope,
)
from engine.runtime.model_inference import (
    ModelInferenceEgressBinding,
    ModelInferenceOperation,
    ModelInferenceOutcomeCategory,
    ModelInferencePort,
    ModelInferenceProfile,
    ModelInferenceRetryPolicy,
    ModelInferenceTraceReceipt,
    ModelInferenceUnavailable,
    RerankModelRequest,
    RewriteModelRequest,
    SelectModelRequest,
)


def _profile(
    operation: ModelInferenceOperation = ModelInferenceOperation.REWRITE,
) -> ModelInferenceProfile:
    profile_ref = f"runtime-{operation.value}-v1"
    return ModelInferenceProfile(
        profile_ref=profile_ref,
        profile_version=1,
        operation=operation,
        egress_profile=ModelEgressProfile(
            profile_ref=profile_ref,
            retention_policy_ref="no-provider-retention-v1",
            sensitivity_policy_ref="authorized-runtime-input-v1",
            issuer_ref="context-runtime",
            consumer_ref="runtime-model-inference",
            provider_ref="deterministic-twin",
            model_ref=f"{operation.value}-twin-v1",
            region_ref="local",
            maximum_ttl=timedelta(seconds=30),
        ),
        tokenizer_ref="utf8-byte-token-v1",
        maximum_input_tokens=512,
        maximum_output_tokens=128,
        maximum_input_items=(1 if operation is ModelInferenceOperation.REWRITE else 8),
        maximum_output_items=(8 if operation is ModelInferenceOperation.RERANK else 4),
        maximum_provider_calls=1,
        maximum_cost_microunits=1_000,
        maximum_elapsed_ms=500,
        timeout_ms=250,
        retry_policy=ModelInferenceRetryPolicy(maximum_attempts=1),
        input_token_cost_microunits=1,
        output_token_cost_microunits=1,
    )


def _registered_profiles() -> tuple[ModelInferenceProfile, ...]:
    return tuple(_profile(operation) for operation in ModelInferenceOperation)


def _candidate(label: str) -> CandidateRef:
    return CandidateRef(
        organization_id=UUID("10000000-0000-0000-0000-000000000001"),
        source_ref="source:model-inference",
        resource_ref=f"resource:{label}",
        revision_ref="revision:model-inference",
        fragment_ref=f"fragment:{label}",
    )


@contextmanager
def _projections(*labels: str) -> Iterator[tuple[AuthorizedProjection, ...]]:
    scope = _open_authorization_kernel_scope()
    try:
        yield tuple(
            _construct_authorized_projection(
                kernel_scope=scope,
                candidate_ref=_candidate(label),
                body=f"authorized body {label}",
                projected_field_refs=("body",),
                lineage=EvidenceLineage(
                    run_ref="run:model-inference",
                    principal_ref="principal:model-inference",
                    purpose="context.answer",
                    as_of=datetime(2026, 7, 30, tzinfo=UTC),
                    decision_ref="decision:model-inference",
                    policy_snapshot_ref="policy:model-inference",
                    policy_epoch=1,
                    source_acl_decision_ref="sourceacl:model-inference",
                    source_acl_projection_ref="sourceacl_projection:model-inference",
                    source_acl_as_of=datetime(2026, 7, 30, tzinfo=UTC),
                ),
            )
            for label in labels
        )
    finally:
        _close_authorization_kernel_scope(scope)


def _egress() -> ModelInferenceEgressBinding:
    return ModelInferenceEgressBinding(
        organization_id=UUID("10000000-0000-0000-0000-000000000001"),
        package_digest="2" * 64,
        purpose="context.answer",
        audience_digest="3" * 64,
        policy_epoch=1,
    )


def _budget() -> PackageBudgetMeter:
    return PackageBudgetMeter(
        PackageBudget(
            max_tokens=1_000,
            max_provider_calls=2,
            max_cost_microunits=2_000,
            max_elapsed_ms=1_000,
        )
    )


class _AcceptingAuthority:
    def __init__(self, events: list[str] | None = None) -> None:
        self.calls: list[EgressGrantRedemption] = []
        self.events = events

    def redeem(self, redemption: EgressGrantRedemption) -> bool:
        self.calls.append(redemption)
        if self.events is not None:
            self.events.append("redeem")
        return True


def test_rewrite_uses_explicit_profile_redeems_then_charges_budget() -> None:
    boundary_events: list[str] = []
    authority = _AcceptingAuthority(boundary_events)
    provider_events: list[tuple[str, int]] = []

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        boundary_events.append("provider")
        provider_events.append((payload.decode("utf-8"), timeout_ms))
        return b'{"rewrites":["governed rewrite"]}'

    traces: list[ModelInferenceTraceReceipt] = []
    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=authority,
        gateway=gateway,
        trace_observer=traces.append,
        monotonic_ms=iter((100, 108)).__next__,
    )
    budget = _budget()
    request = RewriteModelRequest(
        profile=_profile(),
        query="original query",
    )

    result = port.rewrite(
        request,
        grant=ModelEgressGrant("egrm_" + "1" * 64),
        egress=_egress(),
        budget=budget,
    )

    assert result.rewrites == ("governed rewrite",)
    assert len(authority.calls) == 1
    assert authority.calls[0] == EgressGrantRedemption.for_model(
        grant=ModelEgressGrant("egrm_" + "1" * 64),
        organization_id=_egress().organization_id,
        package_digest=_egress().package_digest,
        payload_digest=request.payload_digest,
        purpose=_egress().purpose,
        audience_digest=_egress().audience_digest,
        policy_epoch=_egress().policy_epoch,
        profile=request.profile.egress_profile,
    )
    assert boundary_events == ["redeem", "provider"]
    assert provider_events and provider_events[0][1] == request.profile.timeout_ms
    assert "original query" in provider_events[0][0]
    assert all(
        forbidden not in provider_events[0][0]
        for forbidden in (
            "organizationId",
            "audienceDigest",
            "policyEpoch",
            "egrm_",
            "session",
            "chatHistory",
        )
    )
    assert budget.usage == result.receipt.budget_usage
    assert budget.usage.tokens > 0
    assert budget.usage.provider_calls == 1
    assert traces == [result.receipt]


def test_rerank_accepts_only_authorized_projection_and_returns_validated_order() -> (
    None
):
    authority = _AcceptingAuthority()
    provider_calls = 0
    provider_payloads: list[str] = []

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        nonlocal provider_calls
        del timeout_ms
        provider_calls += 1
        provider_payloads.append(payload.decode("utf-8"))
        return b'{"order":[1,0]}'

    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=authority,
        gateway=gateway,
        trace_observer=lambda _receipt: None,
        monotonic_ms=iter((100, 104)).__next__,
    )

    with _projections("first", "second") as projections:
        result = port.rerank(
            RerankModelRequest(
                profile=_profile(ModelInferenceOperation.RERANK),
                query="rank these",
                projections=projections,
            ),
            grant=ModelEgressGrant("egrm_" + "4" * 64),
            egress=_egress(),
            budget=_budget(),
        )

        assert result.projections == (projections[1], projections[0])
    assert provider_calls == 1
    assert len(authority.calls) == 1
    assert "authorized body first" in provider_payloads[0]
    assert all(
        forbidden not in provider_payloads[0]
        for forbidden in (
            "organization",
            "resource:first",
            "fragment:first",
            "principal",
            "decision",
            "policyEpoch",
        )
    )


def test_select_accepts_authorized_projection_and_returns_only_declared_subset() -> (
    None
):
    authority = _AcceptingAuthority()

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        del payload, timeout_ms
        return b'{"selected":[2,0]}'

    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=authority,
        gateway=gateway,
        trace_observer=lambda _receipt: None,
        monotonic_ms=iter((100, 102)).__next__,
    )

    with _projections("first", "second", "third") as projections:
        result = port.select(
            SelectModelRequest(
                profile=_profile(ModelInferenceOperation.SELECT),
                query="select these",
                projections=projections,
                maximum_items=2,
            ),
            grant=ModelEgressGrant("egrm_" + "5" * 64),
            egress=_egress(),
            budget=_budget(),
        )

    assert result.projections == (projections[2], projections[0])
    assert len(authority.calls) == 1


def test_rerank_and_select_reject_candidate_ref_at_type_and_runtime_seams(
    tmp_path: Path,
) -> None:
    candidate = _candidate("raw")

    for request_type, operation in (
        (RerankModelRequest, ModelInferenceOperation.RERANK),
        (SelectModelRequest, ModelInferenceOperation.SELECT),
    ):
        hints = get_type_hints(request_type)
        assert hints["projections"] == tuple[AuthorizedProjection, ...]
        kwargs: dict[str, object] = {
            "profile": _profile(operation),
            "query": "must reject raw candidate",
            "projections": cast(Any, (candidate,)),
        }
        if request_type is SelectModelRequest:
            kwargs["maximum_items"] = 1
        with pytest.raises(TypeError, match="AuthorizedProjection"):
            request_type(**kwargs)  # type: ignore[arg-type]

    source = tmp_path / "candidate_ref_type_error.py"
    source.write_text(
        "from engine.runtime.evidence import CandidateRef\n"
        "from engine.runtime.model_inference import "
        "ModelInferenceProfile, RerankModelRequest\n"
        "def rejected(\n"
        "    profile: ModelInferenceProfile, candidate: CandidateRef,\n"
        ") -> None:\n"
        "    RerankModelRequest(profile, 'query', (candidate,))\n"
    )
    checked = run(
        [executable, "-m", "mypy", "--strict", str(source)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert checked.returncode == 1
    assert "CandidateRef" in checked.stdout
    assert "AuthorizedProjection" in checked.stdout


class _ExactOneShotAuthority:
    def __init__(self, expected_grant: ModelEgressGrant) -> None:
        self._expected_digest = expected_grant.digest
        self.consumed = False
        self.calls: list[EgressGrantRedemption] = []

    def redeem(self, redemption: EgressGrantRedemption) -> bool:
        self.calls.append(redemption)
        if self.consumed or redemption.grant_digest != self._expected_digest:
            return False
        self.consumed = True
        return True


@pytest.mark.parametrize(
    "authority_outcome",
    ("wrong_grant", "stale", "expired", "wrong_hop"),
)
def test_every_grant_refusal_is_one_generic_outcome_and_zero_provider_bytes(
    authority_outcome: str,
) -> None:
    expected = ModelEgressGrant("egrm_" + "6" * 64)
    wrong = ModelEgressGrant("egrm_" + "7" * 64)
    exact = _ExactOneShotAuthority(expected)

    class _RefusingAuthority:
        def redeem(self, redemption: EgressGrantRedemption) -> bool:
            if authority_outcome == "wrong_grant":
                return exact.redeem(redemption)
            return False

    provider_bytes = 0

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        nonlocal provider_bytes
        del timeout_ms
        provider_bytes += len(payload)
        return b'{"rewrites":["must not run"]}'

    traces: list[ModelInferenceTraceReceipt] = []
    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=_RefusingAuthority(),
        gateway=gateway,
        trace_observer=traces.append,
        monotonic_ms=lambda: 100,
    )
    budget = _budget()

    with pytest.raises(ModelInferenceUnavailable) as refusal:
        presented_grant = (
            cast(Any, ChannelEgressGrant("egrc_" + "7" * 64))
            if authority_outcome == "wrong_hop"
            else (wrong if authority_outcome == "wrong_grant" else expected)
        )
        port.rewrite(
            RewriteModelRequest(profile=_profile(), query="exact grant only"),
            grant=presented_grant,
            egress=_egress(),
            budget=budget,
        )

    assert str(refusal.value) == "model inference is unavailable"
    assert provider_bytes == 0
    assert budget.usage.provider_calls == 0
    assert len(traces) == 1
    assert traces[0].outcome_category is ModelInferenceOutcomeCategory.UNAVAILABLE
    assert traces[0].budget_usage.provider_calls == 0


def test_replayed_grant_is_generic_and_emits_no_second_provider_byte() -> None:
    grant = ModelEgressGrant("egrm_" + "8" * 64)
    authority = _ExactOneShotAuthority(grant)
    provider_bytes = 0

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        nonlocal provider_bytes
        del timeout_ms
        provider_bytes += len(payload)
        return b'{"rewrites":["once"]}'

    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=authority,
        gateway=gateway,
        trace_observer=lambda _receipt: None,
        monotonic_ms=iter((100, 101, 102)).__next__,
    )
    request = RewriteModelRequest(profile=_profile(), query="one shot")
    first = port.rewrite(
        request,
        grant=grant,
        egress=_egress(),
        budget=_budget(),
    )
    bytes_after_first = provider_bytes

    with pytest.raises(ModelInferenceUnavailable, match="is unavailable"):
        port.rewrite(
            request,
            grant=grant,
            egress=_egress(),
            budget=_budget(),
        )

    assert first.rewrites == ("once",)
    assert bytes_after_first > 0
    assert provider_bytes == bytes_after_first
    assert len(authority.calls) == 2


@pytest.mark.parametrize("refusal_kind", ("package_budget", "profile_input"))
def test_budget_and_profile_preflight_refuse_before_redemption_or_provider_bytes(
    refusal_kind: str,
) -> None:
    authority = _AcceptingAuthority()
    provider_bytes = 0

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        nonlocal provider_bytes
        del timeout_ms
        provider_bytes += len(payload)
        return b'{"rewrites":["must not run"]}'

    traces: list[ModelInferenceTraceReceipt] = []
    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=authority,
        gateway=gateway,
        trace_observer=traces.append,
        monotonic_ms=lambda: 100,
    )
    profile = _profile()
    budget = _budget()
    if refusal_kind == "package_budget":
        budget = PackageBudgetMeter(
            PackageBudget(
                max_tokens=1,
                max_provider_calls=1,
                max_cost_microunits=1,
                max_elapsed_ms=1,
            )
        )
    else:
        profile = replace(profile, maximum_input_tokens=1)

    with pytest.raises(ModelInferenceUnavailable, match="is unavailable"):
        port.rewrite(
            RewriteModelRequest(profile=profile, query="preflight this"),
            grant=ModelEgressGrant("egrm_" + "9" * 64),
            egress=_egress(),
            budget=budget,
        )

    assert authority.calls == []
    assert provider_bytes == 0
    assert budget.usage.provider_calls == 0
    assert len(traces) == 1
    assert traces[0].budget_usage.provider_calls == 0


@pytest.mark.parametrize("profile_change", ("version", "model"))
def test_unregistered_profile_or_caller_chosen_model_refuses_before_bytes(
    profile_change: str,
) -> None:
    registered = _profile()
    requested = (
        replace(registered, profile_version=2)
        if profile_change == "version"
        else replace(
            registered,
            egress_profile=replace(
                registered.egress_profile,
                model_ref="caller-chosen-model",
            ),
        )
    )
    authority = _AcceptingAuthority()
    provider_bytes = 0

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        nonlocal provider_bytes
        del timeout_ms
        provider_bytes += len(payload)
        return b'{"rewrites":["must not run"]}'

    port = ModelInferencePort(
        profiles=(registered,),
        authority=authority,
        gateway=gateway,
        trace_observer=lambda _receipt: None,
        monotonic_ms=lambda: 100,
    )

    with pytest.raises(ModelInferenceUnavailable, match="is unavailable"):
        port.rewrite(
            RewriteModelRequest(profile=requested, query="registered profiles only"),
            grant=ModelEgressGrant("egrm_" + "d" * 64),
            egress=_egress(),
            budget=_budget(),
        )

    assert authority.calls == []
    assert provider_bytes == 0


def test_profile_owns_one_bounded_timeout_and_retry_policy() -> None:
    profile = _profile()

    assert profile.timeout_ms == 250
    assert profile.timeout_ms <= profile.maximum_elapsed_ms
    assert profile.retry_policy == ModelInferenceRetryPolicy(
        maximum_attempts=1,
        backoff_ms=(),
    )
    assert all(
        "timeout" not in signature(operation).parameters
        for operation in (
            ModelInferencePort.rewrite,
            ModelInferencePort.rerank,
            ModelInferencePort.select,
        )
    )
    with pytest.raises(ValueError, match="one attempt"):
        ModelInferenceRetryPolicy(maximum_attempts=2)
    with pytest.raises(ValueError, match="one provider call"):
        replace(profile, maximum_provider_calls=2)
    with pytest.raises(ValueError, match="elapsed bound"):
        replace(profile, timeout_ms=profile.maximum_elapsed_ms + 1)


@pytest.mark.parametrize("provider_outcome", ("exception", "malformed", "timeout"))
def test_provider_failures_collapse_to_one_content_free_availability_category(
    provider_outcome: str,
) -> None:
    authority = _AcceptingAuthority()

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        del payload, timeout_ms
        if provider_outcome == "exception":
            raise RuntimeError("private provider detail")
        if provider_outcome == "malformed":
            return b'{"private":"provider detail"}'
        return b'{"rewrites":["too late"]}'

    traces: list[ModelInferenceTraceReceipt] = []
    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=authority,
        gateway=gateway,
        trace_observer=traces.append,
        monotonic_ms=iter(
            (
                100,
                100
                + (_profile().timeout_ms + 1 if provider_outcome == "timeout" else 1),
            )
        ).__next__,
    )
    budget = _budget()

    with pytest.raises(ModelInferenceUnavailable) as unavailable:
        port.rewrite(
            RewriteModelRequest(profile=_profile(), query="no provider detail"),
            grant=ModelEgressGrant("egrm_" + "a" * 64),
            egress=_egress(),
            budget=budget,
        )

    assert str(unavailable.value) == "model inference is unavailable"
    assert "private" not in str(unavailable.value)
    assert budget.usage.provider_calls == 1
    assert len(traces) == 1
    assert traces[0].outcome_category is ModelInferenceOutcomeCategory.UNAVAILABLE
    assert traces[0].output_digest is None
    assert "provider detail" not in repr(traces[0])


def test_trace_receipt_is_digest_only_with_bounded_usage_and_profile_lineage() -> None:
    traces: list[ModelInferenceTraceReceipt] = []
    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=_AcceptingAuthority(),
        gateway=lambda _payload, *, timeout_ms: b'{"rewrites":["safe output"]}',
        trace_observer=traces.append,
        monotonic_ms=iter((100, 101)).__next__,
    )

    result = port.rewrite(
        RewriteModelRequest(profile=_profile(), query="secret query text"),
        grant=ModelEgressGrant("egrm_" + "b" * 64),
        egress=_egress(),
        budget=_budget(),
    )
    receipt = result.receipt

    assert {field.name for field in fields(receipt)} == {
        "operation",
        "outcome_category",
        "profile_ref",
        "profile_version",
        "input_digest",
        "output_digest",
        "budget_usage",
        "digest_profile",
    }
    assert set(asdict(receipt)["budget_usage"]) == {
        "tokens",
        "provider_calls",
        "cost_microunits",
        "elapsed_ms",
    }
    assert "secret query text" not in repr(receipt)
    assert "safe output" not in repr(receipt)
    assert len(receipt.input_digest) == len(receipt.output_digest or "") == 64
    assert traces == [receipt]


@pytest.mark.parametrize(
    "mutation",
    ("query", "payload", "payload_digest", "profile_ref", "model_ref"),
)
def test_mutated_request_or_profile_refuses_before_redemption_without_trace_leak(
    mutation: str,
) -> None:
    request = RewriteModelRequest(profile=_profile(), query="original safe query")
    if mutation == "query":
        object.__setattr__(request, "query", "mutated secret query")
    elif mutation == "payload":
        object.__setattr__(request, "_payload", b"mutated secret payload")
    elif mutation == "payload_digest":
        object.__setattr__(request, "payload_digest", "mutated secret digest")
    elif mutation == "profile_ref":
        object.__setattr__(request.profile, "profile_ref", "mutated-secret-profile")
    else:
        object.__setattr__(
            request.profile.egress_profile,
            "model_ref",
            "mutated-secret-model",
        )
    authority = _AcceptingAuthority()
    traces: list[ModelInferenceTraceReceipt] = []
    provider_bytes = 0

    def gateway(payload: bytes, *, timeout_ms: int) -> bytes:
        nonlocal provider_bytes
        del timeout_ms
        provider_bytes += len(payload)
        return b'{"rewrites":["must not run"]}'

    port = ModelInferencePort(
        profiles=_registered_profiles(),
        authority=authority,
        gateway=gateway,
        trace_observer=traces.append,
        monotonic_ms=lambda: 100,
    )

    with pytest.raises(ModelInferenceUnavailable, match="is unavailable"):
        port.rewrite(
            request,
            grant=ModelEgressGrant("egrm_" + "c" * 64),
            egress=_egress(),
            budget=_budget(),
        )

    assert authority.calls == []
    assert provider_bytes == 0
    assert len(traces) == 1
    assert "mutated-secret" not in repr(traces[0])
    assert "mutated secret" not in repr(traces[0])


def test_port_has_one_closed_typed_surface_and_no_ambient_context_imports() -> None:
    root = Path(__file__).parents[2]
    module_path = root / "engine/runtime/model_inference.py"
    module = ast.parse(module_path.read_text())
    port_classes = [
        (path.relative_to(root), node.name)
        for path in root.rglob("*.py")
        if not any(
            part in {".context-engine", ".git", ".venv", "tests", "third_party"}
            for part in path.relative_to(root).parts
        )
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ClassDef)
        and {
            child.name
            for child in node.body
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        >= {"rewrite", "rerank", "select"}
    ]
    public_methods = {
        child.name
        for node in ast.walk(module)
        if isinstance(node, ast.ClassDef) and node.name == "ModelInferencePort"
        for child in node.body
        if isinstance(child, ast.FunctionDef) and not child.name.startswith("_")
    }
    imported_modules = {
        alias.name
        for node in module.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in module.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert port_classes == [
        (Path("engine/runtime/model_inference.py"), "ModelInferencePort")
    ]
    assert public_methods == {"rewrite", "rerank", "select"}
    assert public_methods.isdisjoint({"infer", "generate", "complete"})
    assert all(
        set(signature(operation).parameters)
        == {"self", "request", "grant", "egress", "budget"}
        for operation in (
            ModelInferencePort.rewrite,
            ModelInferencePort.rerank,
            ModelInferencePort.select,
        )
    )
    assert imported_modules.isdisjoint(
        {
            "engine.runtime.contracts",
            "engine.runtime.invocation",
            "engine.runtime.trusted_inputs",
            "engine.runtime.delivery",
        }
    )
