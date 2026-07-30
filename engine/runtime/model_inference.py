"""One governed in-Runtime model-inference port with closed operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event, Thread
from typing import Any, Final, Protocol, TypeVar, cast
from uuid import UUID

import rfc8785

from engine.runtime.budget import (
    BudgetUsage,
    PackageBudgetExceeded,
    PackageBudgetMeter,
)
from engine.runtime.egress import (
    EgressGrantRedemption,
    EgressGrantRedemptionAuthority,
    ModelEgressGrant,
    ModelEgressProfile,
)
from engine.runtime.evidence import (
    AuthorizedProjection,
    _require_active_authorized_projection,
)

MODEL_INFERENCE_DIGEST_PROFILE: Final = "model-inference-rfc8785-sha256-v1"
MODEL_INFERENCE_TOKENIZER_PROFILE: Final = "utf8-byte-token-v1"
_DIGEST_DOMAIN = b"context-engine.model-inference.v1\x00"
_OUTPUT_DIGEST_DOMAIN = b"context-engine.model-inference-output.v1\x00"
_UNAVAILABLE_PROFILE_REF: Final = "model-inference-profile-unavailable-v1"


def _require_nonblank(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"{field_name} must be nonblank")
    return value


def _require_sha256(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


def _require_positive_integer(field_name: str, value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive exact integer")
    return value


def _require_nonnegative_integer(field_name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative exact integer")
    return value


class ModelInferenceOperation(StrEnum):
    """The complete operation set admitted by the Runtime port."""

    REWRITE = "rewrite"
    RERANK = "rerank"
    SELECT = "select"


class ModelInferenceOutcomeCategory(StrEnum):
    """Content-free trace outcomes; failure detail never crosses the port."""

    SUCCEEDED = "succeeded"
    UNAVAILABLE = "unavailable"


class ModelInferenceUnavailable(RuntimeError):
    """One content-free availability category for every port refusal."""

    __slots__ = ("receipt",)

    def __init__(
        self,
        receipt: ModelInferenceTraceReceipt | None = None,
    ) -> None:
        self.receipt = receipt
        super().__init__("model inference is unavailable")


@dataclass(frozen=True, slots=True)
class ModelInferenceRetryPolicy:
    """Deterministic one-attempt policy compatible with one-shot grants."""

    maximum_attempts: int
    backoff_ms: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.maximum_attempts != 1 or self.backoff_ms != ():
            raise ValueError(
                "model inference retry policy is one attempt with no backoff"
            )


def _profile_document(profile: ModelInferenceProfile) -> dict[str, object]:
    egress = profile.egress_profile
    return {
        "cost": {
            "inputTokenMicrounits": profile.input_token_cost_microunits,
            "outputTokenMicrounits": profile.output_token_cost_microunits,
        },
        "egress": {
            "consumerRef": egress.consumer_ref,
            "issuerRef": egress.issuer_ref,
            "maximumTtlMicroseconds": int(
                egress.maximum_ttl.total_seconds() * 1_000_000
            ),
            "modelRef": egress.model_ref,
            "profileRef": egress.profile_ref,
            "providerRef": egress.provider_ref,
            "regionRef": egress.region_ref,
            "retentionPolicyRef": egress.retention_policy_ref,
            "sensitivityPolicyRef": egress.sensitivity_policy_ref,
        },
        "limits": {
            "elapsedMs": profile.maximum_elapsed_ms,
            "inputItems": profile.maximum_input_items,
            "inputTokens": profile.maximum_input_tokens,
            "outputItems": profile.maximum_output_items,
            "outputTokens": profile.maximum_output_tokens,
            "providerCalls": profile.maximum_provider_calls,
            "totalCostMicrounits": profile.maximum_cost_microunits,
        },
        "operation": profile.operation.value,
        "profileRef": profile.profile_ref,
        "profileVersion": profile.profile_version,
        "retry": {
            "backoffMs": profile.retry_policy.backoff_ms,
            "maximumAttempts": profile.retry_policy.maximum_attempts,
        },
        "timeoutMs": profile.timeout_ms,
        "tokenizerRef": profile.tokenizer_ref,
    }


@dataclass(frozen=True, slots=True)
class ModelInferenceProfile:
    """Explicit versioned server profile for one exact operation and model hop."""

    profile_ref: str
    profile_version: int
    operation: ModelInferenceOperation
    egress_profile: ModelEgressProfile = field(repr=False)
    tokenizer_ref: str
    maximum_input_tokens: int
    maximum_output_tokens: int
    maximum_input_items: int
    maximum_output_items: int
    maximum_provider_calls: int
    maximum_cost_microunits: int
    maximum_elapsed_ms: int
    timeout_ms: int
    retry_policy: ModelInferenceRetryPolicy
    input_token_cost_microunits: int
    output_token_cost_microunits: int
    _integrity_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_profile_fields(self)
        object.__setattr__(
            self,
            "_integrity_digest",
            hashlib.sha256(
                rfc8785.dumps(cast(Any, _profile_document(self)))
            ).hexdigest(),
        )


def _validate_profile_fields(profile: ModelInferenceProfile) -> None:
    """Validate fields without rewriting the sealed integrity digest."""

    try:
        egress_profile = profile.egress_profile
    except AttributeError as error:
        raise ValueError("model inference profile is incomplete") from error
    if type(egress_profile) is not ModelEgressProfile:
        raise TypeError("model inference profile requires ModelEgressProfile")
    egress_profile.__post_init__()
    for field_name in ("profile_ref", "tokenizer_ref"):
        getattr(profile, field_name)
    for field_name in (
        "profile_version",
        "maximum_input_tokens",
        "maximum_output_tokens",
        "maximum_input_items",
        "maximum_output_items",
        "maximum_provider_calls",
        "maximum_cost_microunits",
        "maximum_elapsed_ms",
        "timeout_ms",
        "input_token_cost_microunits",
        "output_token_cost_microunits",
    ):
        getattr(profile, field_name)
    try:
        retry_policy = profile.retry_policy
        operation = profile.operation
    except AttributeError as error:
        raise ValueError("model inference profile is incomplete") from error
    if type(retry_policy) is not ModelInferenceRetryPolicy:
        raise TypeError("model inference retry policy has the wrong nominal type")
    retry_policy.__post_init__()

    _require_nonblank("model inference profile_ref", profile.profile_ref)
    _require_positive_integer("profile_version", profile.profile_version)
    if type(operation) is not ModelInferenceOperation:
        raise TypeError("model inference operation has the wrong nominal type")
    if egress_profile.profile_ref != profile.profile_ref:
        raise ValueError("model inference and egress profile refs must match")
    if profile.tokenizer_ref != MODEL_INFERENCE_TOKENIZER_PROFILE:
        raise ValueError("model inference tokenizer profile is not active")
    for field_name in (
        "maximum_input_tokens",
        "maximum_output_tokens",
        "maximum_input_items",
        "maximum_output_items",
        "maximum_provider_calls",
        "maximum_cost_microunits",
        "maximum_elapsed_ms",
        "timeout_ms",
    ):
        _require_positive_integer(field_name, getattr(profile, field_name))
    if profile.maximum_provider_calls != 1:
        raise ValueError("one-shot model grants allow one provider call")
    if profile.timeout_ms > profile.maximum_elapsed_ms:
        raise ValueError("model inference timeout exceeds elapsed bound")
    _require_nonnegative_integer(
        "input_token_cost_microunits",
        profile.input_token_cost_microunits,
    )
    _require_nonnegative_integer(
        "output_token_cost_microunits",
        profile.output_token_cost_microunits,
    )
    maximum_cost = (
        profile.maximum_input_tokens * profile.input_token_cost_microunits
        + profile.maximum_output_tokens * profile.output_token_cost_microunits
    )
    if maximum_cost > profile.maximum_cost_microunits:
        raise ValueError("model inference token rates exceed profile cost bound")
    if (
        profile.operation is ModelInferenceOperation.REWRITE
        and profile.maximum_input_items != 1
    ):
        raise ValueError("rewrite profiles accept exactly one input item")
    if (
        profile.operation is ModelInferenceOperation.RERANK
        and profile.maximum_output_items < profile.maximum_input_items
    ):
        raise ValueError("rerank profiles must bound one output per input")


def _require_profile_integrity(profile: ModelInferenceProfile) -> None:
    if type(profile) is not ModelInferenceProfile:
        raise TypeError("model inference requires ModelInferenceProfile")
    try:
        _validate_profile_fields(profile)
    except (TypeError, ValueError) as error:
        raise ValueError("model inference profile integrity is invalid") from error
    expected = hashlib.sha256(
        rfc8785.dumps(cast(Any, _profile_document(profile)))
    ).hexdigest()
    if profile._integrity_digest != expected:
        raise ValueError("model inference profile integrity is invalid")


def _snapshot_profile(profile: ModelInferenceProfile) -> ModelInferenceProfile:
    """Copy a validated server profile so later caller mutation cannot change a hop."""

    _require_profile_integrity(profile)
    integrity_digest = profile._integrity_digest
    egress = profile.egress_profile
    snapshot = ModelInferenceProfile(
        profile_ref=profile.profile_ref,
        profile_version=profile.profile_version,
        operation=profile.operation,
        egress_profile=ModelEgressProfile(
            profile_ref=egress.profile_ref,
            retention_policy_ref=egress.retention_policy_ref,
            sensitivity_policy_ref=egress.sensitivity_policy_ref,
            issuer_ref=egress.issuer_ref,
            consumer_ref=egress.consumer_ref,
            provider_ref=egress.provider_ref,
            model_ref=egress.model_ref,
            region_ref=egress.region_ref,
            maximum_ttl=egress.maximum_ttl,
        ),
        tokenizer_ref=profile.tokenizer_ref,
        maximum_input_tokens=profile.maximum_input_tokens,
        maximum_output_tokens=profile.maximum_output_tokens,
        maximum_input_items=profile.maximum_input_items,
        maximum_output_items=profile.maximum_output_items,
        maximum_provider_calls=profile.maximum_provider_calls,
        maximum_cost_microunits=profile.maximum_cost_microunits,
        maximum_elapsed_ms=profile.maximum_elapsed_ms,
        timeout_ms=profile.timeout_ms,
        retry_policy=ModelInferenceRetryPolicy(
            maximum_attempts=profile.retry_policy.maximum_attempts,
            backoff_ms=profile.retry_policy.backoff_ms,
        ),
        input_token_cost_microunits=profile.input_token_cost_microunits,
        output_token_cost_microunits=profile.output_token_cost_microunits,
    )
    if snapshot._integrity_digest != integrity_digest:
        raise ValueError("model inference profile changed while being validated")
    return snapshot


@dataclass(frozen=True, slots=True)
class ModelInferenceEgressBinding:
    """Exact trusted grant bindings needed for one Runtime inference hop."""

    organization_id: UUID = field(repr=False)
    package_digest: str
    purpose: str
    audience_digest: str
    policy_epoch: int

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("model inference Organization must be UUID")
        _require_sha256("package_digest", self.package_digest)
        _require_nonblank("purpose", self.purpose)
        _require_sha256("audience_digest", self.audience_digest)
        _require_positive_integer("policy_epoch", self.policy_epoch)


def _snapshot_egress_binding(
    binding: ModelInferenceEgressBinding,
) -> ModelInferenceEgressBinding:
    if type(binding) is not ModelInferenceEgressBinding:
        raise TypeError("model inference requires ModelInferenceEgressBinding")
    return ModelInferenceEgressBinding(
        organization_id=binding.organization_id,
        package_digest=binding.package_digest,
        purpose=binding.purpose,
        audience_digest=binding.audience_digest,
        policy_epoch=binding.policy_epoch,
    )


def _model_payload(
    *,
    operation: ModelInferenceOperation,
    profile: ModelInferenceProfile,
    input_document: dict[str, object],
) -> bytes:
    return rfc8785.dumps(
        cast(
            Any,
            {
                "input": input_document,
                "operation": operation.value,
                "profileRef": profile.profile_ref,
                "profileVersion": profile.profile_version,
            },
        )
    )


def _rewrite_payload(profile: ModelInferenceProfile, query: str) -> bytes:
    return _model_payload(
        operation=ModelInferenceOperation.REWRITE,
        profile=profile,
        input_document={"query": query},
    )


def _projection_payload(
    *,
    operation: ModelInferenceOperation,
    profile: ModelInferenceProfile,
    query: str,
    projections: tuple[AuthorizedProjection, ...],
    maximum_items: int | None = None,
) -> bytes:
    input_document: dict[str, object] = {
        "items": [
            {
                "body": projection.projected_body,
                "projectedFieldRefs": projection.projected_field_refs,
            }
            for projection in projections
        ],
        "query": query,
    }
    if maximum_items is not None:
        input_document["maximumItems"] = maximum_items
    return _model_payload(
        operation=operation,
        profile=profile,
        input_document=input_document,
    )


@dataclass(frozen=True, slots=True)
class _RequestSnapshot:
    """Immutable execution facts sealed before budget or egress state changes."""

    operation: ModelInferenceOperation
    profile: ModelInferenceProfile
    payload: bytes = field(repr=False)
    payload_digest: str
    input_items: int
    projections: tuple[AuthorizedProjection, ...] = field(default=(), repr=False)
    maximum_items: int | None = None


def _seal_request_snapshot(
    *,
    operation: ModelInferenceOperation,
    profile: ModelInferenceProfile,
    payload: bytes,
    stored_payload: object,
    stored_digest: object,
    input_items: int,
    projections: tuple[AuthorizedProjection, ...] = (),
    maximum_items: int | None = None,
) -> _RequestSnapshot:
    payload_digest = hashlib.sha256(_DIGEST_DOMAIN + payload).hexdigest()
    if stored_payload != payload or stored_digest != payload_digest:
        raise ValueError("model inference request integrity is invalid")
    return _RequestSnapshot(
        operation=operation,
        profile=profile,
        payload=payload,
        payload_digest=payload_digest,
        input_items=input_items,
        projections=projections,
        maximum_items=maximum_items,
    )


@dataclass(frozen=True, slots=True)
class RewriteModelRequest:
    """Pre-Kernel rewrite input with no ambient request or tenant object."""

    profile: ModelInferenceProfile
    query: str = field(repr=False)
    payload_digest: str = field(init=False)
    _payload: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_profile_integrity(self.profile)
        if self.profile.operation is not ModelInferenceOperation.REWRITE:
            raise ValueError("rewrite requires a rewrite profile")
        _require_nonblank("rewrite query", self.query)
        payload = _rewrite_payload(self.profile, self.query)
        object.__setattr__(self, "_payload", payload)
        object.__setattr__(
            self,
            "payload_digest",
            hashlib.sha256(_DIGEST_DOMAIN + payload).hexdigest(),
        )

    def _snapshot(self, profile: ModelInferenceProfile) -> _RequestSnapshot:
        if profile.operation is not ModelInferenceOperation.REWRITE:
            raise ValueError("rewrite requires a rewrite profile")
        query = _require_nonblank("rewrite query", self.query)
        return _seal_request_snapshot(
            operation=ModelInferenceOperation.REWRITE,
            profile=profile,
            payload=_rewrite_payload(profile, query),
            stored_payload=self._payload,
            stored_digest=self.payload_digest,
            input_items=1,
        )


@dataclass(frozen=True, slots=True)
class RerankModelRequest:
    """Content-bearing rerank input containing exact projections only."""

    profile: ModelInferenceProfile
    query: str = field(repr=False)
    projections: tuple[AuthorizedProjection, ...] = field(repr=False)
    payload_digest: str = field(init=False)
    _payload: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_profile_integrity(self.profile)
        if self.profile.operation is not ModelInferenceOperation.RERANK:
            raise ValueError("rerank requires a rerank profile")
        _require_nonblank("rerank query", self.query)
        if type(self.projections) is not tuple or not self.projections:
            raise TypeError("rerank requires AuthorizedProjection inputs")
        for projection in self.projections:
            _require_active_authorized_projection(projection)
        payload = _projection_payload(
            operation=ModelInferenceOperation.RERANK,
            profile=self.profile,
            query=self.query,
            projections=self.projections,
        )
        object.__setattr__(self, "_payload", payload)
        object.__setattr__(
            self,
            "payload_digest",
            hashlib.sha256(_DIGEST_DOMAIN + payload).hexdigest(),
        )

    def _snapshot(self, profile: ModelInferenceProfile) -> _RequestSnapshot:
        if profile.operation is not ModelInferenceOperation.RERANK:
            raise ValueError("rerank requires a rerank profile")
        query = _require_nonblank("rerank query", self.query)
        projections = self.projections
        if type(projections) is not tuple or not projections:
            raise TypeError("rerank requires AuthorizedProjection inputs")
        for projection in projections:
            _require_active_authorized_projection(projection)
        return _seal_request_snapshot(
            operation=ModelInferenceOperation.RERANK,
            profile=profile,
            payload=_projection_payload(
                operation=ModelInferenceOperation.RERANK,
                profile=profile,
                query=query,
                projections=projections,
            ),
            stored_payload=self._payload,
            stored_digest=self.payload_digest,
            input_items=len(projections),
            projections=projections,
        )


@dataclass(frozen=True, slots=True)
class SelectModelRequest:
    """Content-bearing selection input containing exact projections only."""

    profile: ModelInferenceProfile
    query: str = field(repr=False)
    projections: tuple[AuthorizedProjection, ...] = field(repr=False)
    maximum_items: int
    payload_digest: str = field(init=False)
    _payload: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_profile_integrity(self.profile)
        if self.profile.operation is not ModelInferenceOperation.SELECT:
            raise ValueError("select requires a select profile")
        _require_nonblank("select query", self.query)
        if type(self.projections) is not tuple or not self.projections:
            raise TypeError("select requires AuthorizedProjection inputs")
        for projection in self.projections:
            _require_active_authorized_projection(projection)
        _require_positive_integer("select maximum_items", self.maximum_items)
        if self.maximum_items > self.profile.maximum_output_items:
            raise ValueError("select maximum_items exceeds its profile")
        payload = _projection_payload(
            operation=ModelInferenceOperation.SELECT,
            profile=self.profile,
            query=self.query,
            projections=self.projections,
            maximum_items=self.maximum_items,
        )
        object.__setattr__(self, "_payload", payload)
        object.__setattr__(
            self,
            "payload_digest",
            hashlib.sha256(_DIGEST_DOMAIN + payload).hexdigest(),
        )

    def _snapshot(self, profile: ModelInferenceProfile) -> _RequestSnapshot:
        if profile.operation is not ModelInferenceOperation.SELECT:
            raise ValueError("select requires a select profile")
        query = _require_nonblank("select query", self.query)
        projections = self.projections
        if type(projections) is not tuple or not projections:
            raise TypeError("select requires AuthorizedProjection inputs")
        for projection in projections:
            _require_active_authorized_projection(projection)
        maximum_items = _require_positive_integer(
            "select maximum_items",
            self.maximum_items,
        )
        if maximum_items > profile.maximum_output_items:
            raise ValueError("select maximum_items exceeds its profile")
        return _seal_request_snapshot(
            operation=ModelInferenceOperation.SELECT,
            profile=profile,
            payload=_projection_payload(
                operation=ModelInferenceOperation.SELECT,
                profile=profile,
                query=query,
                projections=projections,
                maximum_items=maximum_items,
            ),
            stored_payload=self._payload,
            stored_digest=self.payload_digest,
            input_items=len(projections),
            projections=projections,
            maximum_items=maximum_items,
        )


type _InferenceRequest = RewriteModelRequest | RerankModelRequest | SelectModelRequest


@dataclass(frozen=True, slots=True)
class ModelInferenceTraceReceipt:
    """Restricted digests-only operation trace with bounded usage."""

    operation: ModelInferenceOperation
    outcome_category: ModelInferenceOutcomeCategory
    profile_ref: str
    profile_version: int
    input_digest: str
    output_digest: str | None
    budget_usage: BudgetUsage
    digest_profile: str = MODEL_INFERENCE_DIGEST_PROFILE

    def __post_init__(self) -> None:
        if type(self.operation) is not ModelInferenceOperation:
            raise TypeError("model inference trace operation has the wrong type")
        if type(self.outcome_category) is not ModelInferenceOutcomeCategory:
            raise TypeError("model inference trace outcome has the wrong type")
        _require_nonblank("model inference trace profile_ref", self.profile_ref)
        _require_positive_integer("trace profile_version", self.profile_version)
        _require_sha256("model inference trace input_digest", self.input_digest)
        if self.output_digest is not None:
            _require_sha256("model inference trace output_digest", self.output_digest)
        if type(self.budget_usage) is not BudgetUsage:
            raise TypeError("model inference trace usage has the wrong type")
        if self.digest_profile != MODEL_INFERENCE_DIGEST_PROFILE:
            raise ValueError("model inference trace digest profile is not active")


@dataclass(frozen=True, slots=True)
class RewriteModelResult:
    """Validated rewrite output plus its restricted trace receipt."""

    rewrites: tuple[str, ...]
    receipt: ModelInferenceTraceReceipt


@dataclass(frozen=True, slots=True)
class RerankModelResult:
    """Validated ordering over the exact authorized input projections."""

    projections: tuple[AuthorizedProjection, ...]
    receipt: ModelInferenceTraceReceipt


@dataclass(frozen=True, slots=True)
class SelectModelResult:
    """Validated subset of the exact authorized input projections."""

    projections: tuple[AuthorizedProjection, ...]
    receipt: ModelInferenceTraceReceipt


class _ModelGatewayPort(Protocol):
    def __call__(self, payload: bytes, *, timeout_ms: int) -> bytes: ...


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate model response key")
        result[key] = value
    return result


def _reject_nonfinite_number(_value: str) -> object:
    raise ValueError("non-finite model response number")


def _parse_closed_document(raw_output: bytes) -> dict[str, object]:
    document = json.loads(
        raw_output,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite_number,
    )
    if type(document) is not dict:
        raise ValueError("model response must be an object")
    return cast(dict[str, object], document)


_ParsedOutput = TypeVar("_ParsedOutput")


@dataclass(frozen=True, slots=True)
class _TraceContext:
    operation: ModelInferenceOperation
    profile_ref: str
    profile_version: int
    input_digest: str


_REQUEST_OPERATIONS: Final[dict[type[object], ModelInferenceOperation]] = {
    RewriteModelRequest: ModelInferenceOperation.REWRITE,
    RerankModelRequest: ModelInferenceOperation.RERANK,
    SelectModelRequest: ModelInferenceOperation.SELECT,
}


def _request_operation(request: object) -> ModelInferenceOperation:
    operation = _REQUEST_OPERATIONS.get(type(request))
    if operation is None:
        raise TypeError("model inference request has the wrong nominal type")
    return operation


def _fallback_trace_context(request: _InferenceRequest) -> _TraceContext:
    operation = _request_operation(request)
    try:
        _require_profile_integrity(request.profile)
        if request.profile.operation is not operation:
            raise ValueError("model inference request operation changed")
        profile_ref = request.profile.profile_ref
        profile_version = request.profile.profile_version
    except (AttributeError, TypeError, ValueError):
        profile_ref = _UNAVAILABLE_PROFILE_REF
        profile_version = 1
    raw_payload = getattr(request, "_payload", None)
    input_digest = hashlib.sha256(
        _DIGEST_DOMAIN + (raw_payload if type(raw_payload) is bytes else b"invalid")
    ).hexdigest()
    return _TraceContext(
        operation=operation,
        profile_ref=profile_ref,
        profile_version=profile_version,
        input_digest=input_digest,
    )


class ModelInferencePort:
    """Sole governed entry point for Runtime rewrite, rerank, and select calls."""

    __slots__ = (
        "_authority",
        "_gateway",
        "_monotonic_ms",
        "_profiles",
        "_trace_observer",
    )

    def __init__(
        self,
        *,
        profiles: tuple[ModelInferenceProfile, ...],
        authority: EgressGrantRedemptionAuthority,
        gateway: _ModelGatewayPort,
        trace_observer: Callable[[ModelInferenceTraceReceipt], None],
        monotonic_ms: Callable[[], int],
    ) -> None:
        if type(profiles) is not tuple or not profiles:
            raise TypeError("model inference requires registered profiles")
        registered: dict[
            tuple[ModelInferenceOperation, str, int], ModelInferenceProfile
        ] = {}
        for profile in profiles:
            snapshot = _snapshot_profile(profile)
            key = (
                snapshot.operation,
                snapshot.profile_ref,
                snapshot.profile_version,
            )
            if key in registered:
                raise ValueError("model inference profile lineage must be unique")
            registered[key] = snapshot
        if not callable(getattr(authority, "redeem", None)):
            raise TypeError("model inference redemption authority is incomplete")
        for field_name, value in (
            ("gateway", gateway),
            ("trace_observer", trace_observer),
            ("monotonic_ms", monotonic_ms),
        ):
            if not callable(value):
                raise TypeError(f"model inference {field_name} must be callable")
        self._authority = authority
        self._gateway = gateway
        self._profiles = registered
        self._trace_observer = trace_observer
        self._monotonic_ms = monotonic_ms

    def _emit_unavailable(
        self,
        trace_context: _TraceContext,
        usage: BudgetUsage,
    ) -> None:
        receipt = ModelInferenceTraceReceipt(
            operation=trace_context.operation,
            outcome_category=ModelInferenceOutcomeCategory.UNAVAILABLE,
            profile_ref=trace_context.profile_ref,
            profile_version=trace_context.profile_version,
            input_digest=trace_context.input_digest,
            output_digest=None,
            budget_usage=usage,
        )
        with suppress(Exception):
            self._trace_observer(receipt)
        raise ModelInferenceUnavailable(receipt)

    def _call_gateway_bounded(
        self,
        payload: bytes,
        *,
        timeout_ms: int,
    ) -> bytes:
        """Return within the profile deadline even if an injected gateway hangs."""

        finished = Event()
        outputs: list[bytes] = []
        failed: list[bool] = []

        def invoke() -> None:
            try:
                output = self._gateway(payload, timeout_ms=timeout_ms)
                if type(output) is bytes:
                    outputs.append(output)
                else:
                    failed.append(True)
            except Exception:
                failed.append(True)
            finally:
                finished.set()

        Thread(
            target=invoke,
            name="context-engine-model-inference",
            daemon=True,
        ).start()
        if not finished.wait(timeout_ms / 1_000):
            raise TimeoutError("model gateway exceeded its profile")
        if failed or len(outputs) != 1:
            raise ValueError("model gateway is unavailable")
        return outputs[0]

    def _execute(
        self,
        request: _InferenceRequest,
        *,
        grant: ModelEgressGrant,
        egress: ModelInferenceEgressBinding,
        budget: PackageBudgetMeter,
        parse_output: Callable[[bytes, _RequestSnapshot], _ParsedOutput],
    ) -> tuple[_ParsedOutput, ModelInferenceTraceReceipt]:
        """Run the shared budget -> redeem -> provider -> trace pipeline."""

        if type(budget) is not PackageBudgetMeter:
            raise TypeError("model inference requires PackageBudgetMeter")
        empty = BudgetUsage(0, 0, 0, 0)
        trace_context = _fallback_trace_context(request)
        try:
            operation = _request_operation(request)
            profile = _snapshot_profile(request.profile)
            snapshot = request._snapshot(profile)
            if snapshot.operation is not operation:
                raise ValueError("model inference request operation changed")
            trace_context = _TraceContext(
                operation=snapshot.operation,
                profile_ref=snapshot.profile.profile_ref,
                profile_version=snapshot.profile.profile_version,
                input_digest=snapshot.payload_digest,
            )
            profile_key = (
                snapshot.operation,
                snapshot.profile.profile_ref,
                snapshot.profile.profile_version,
            )
            if self._profiles.get(profile_key) != snapshot.profile:
                raise ValueError("model inference profile is not registered")
            if type(grant) is not ModelEgressGrant:
                self._emit_unavailable(trace_context, empty)
            grant_snapshot = ModelEgressGrant(grant.value)
            egress_snapshot = _snapshot_egress_binding(egress)
            redemption = EgressGrantRedemption.for_model(
                grant=grant_snapshot,
                organization_id=egress_snapshot.organization_id,
                package_digest=egress_snapshot.package_digest,
                payload_digest=snapshot.payload_digest,
                purpose=egress_snapshot.purpose,
                audience_digest=egress_snapshot.audience_digest,
                policy_epoch=egress_snapshot.policy_epoch,
                profile=snapshot.profile.egress_profile,
            )
            input_tokens = len(snapshot.payload)
            if (
                input_tokens > snapshot.profile.maximum_input_tokens
                or snapshot.input_items > snapshot.profile.maximum_input_items
            ):
                self._emit_unavailable(trace_context, empty)
            maximum = BudgetUsage(
                tokens=input_tokens + snapshot.profile.maximum_output_tokens,
                provider_calls=1,
                cost_microunits=(
                    input_tokens * snapshot.profile.input_token_cost_microunits
                    + snapshot.profile.maximum_output_tokens
                    * snapshot.profile.output_token_cost_microunits
                ),
                elapsed_ms=snapshot.profile.maximum_elapsed_ms,
            )
            reservation = budget._reserve(maximum)
        except ModelInferenceUnavailable:
            raise
        except (PackageBudgetExceeded, TypeError, ValueError):
            self._emit_unavailable(trace_context, empty)

        try:
            started_ms = self._monotonic_ms()
            accepted = self._authority.redeem(redemption)
        except Exception:
            budget._cancel(reservation)
            self._emit_unavailable(trace_context, empty)
        if accepted is not True:
            budget._cancel(reservation)
            self._emit_unavailable(trace_context, empty)

        try:
            raw_output = self._call_gateway_bounded(
                snapshot.payload,
                timeout_ms=snapshot.profile.timeout_ms,
            )
            finished_ms = self._monotonic_ms()
            elapsed_ms = finished_ms - started_ms
            if (
                elapsed_ms < 0
                or elapsed_ms > snapshot.profile.timeout_ms
                or len(raw_output) > snapshot.profile.maximum_output_tokens
            ):
                raise ValueError("model gateway exceeded its profile")
            parsed = parse_output(raw_output, snapshot)
            actual = BudgetUsage(
                tokens=input_tokens + len(raw_output),
                provider_calls=1,
                cost_microunits=(
                    input_tokens * snapshot.profile.input_token_cost_microunits
                    + len(raw_output) * snapshot.profile.output_token_cost_microunits
                ),
                elapsed_ms=elapsed_ms,
            )
            budget._commit(reservation, actual)
        except Exception:
            budget._commit(reservation, maximum)
            self._emit_unavailable(trace_context, maximum)

        receipt = ModelInferenceTraceReceipt(
            operation=snapshot.operation,
            outcome_category=ModelInferenceOutcomeCategory.SUCCEEDED,
            profile_ref=snapshot.profile.profile_ref,
            profile_version=snapshot.profile.profile_version,
            input_digest=snapshot.payload_digest,
            output_digest=hashlib.sha256(
                _OUTPUT_DIGEST_DOMAIN + raw_output
            ).hexdigest(),
            budget_usage=actual,
        )
        try:
            self._trace_observer(receipt)
        except Exception:
            self._emit_unavailable(trace_context, actual)
        return parsed, receipt

    def rewrite(
        self,
        request: RewriteModelRequest,
        *,
        grant: ModelEgressGrant,
        egress: ModelInferenceEgressBinding,
        budget: PackageBudgetMeter,
    ) -> RewriteModelResult:
        """Rewrite one explicit query after budget reservation and grant redemption."""

        if type(request) is not RewriteModelRequest:
            raise TypeError("rewrite requires RewriteModelRequest")

        def parse(
            raw_output: bytes,
            snapshot: _RequestSnapshot,
        ) -> tuple[str, ...]:
            document = _parse_closed_document(raw_output)
            if set(document) != {"rewrites"}:
                raise ValueError("rewrite output has the wrong shape")
            raw_rewrites = document["rewrites"]
            if (
                type(raw_rewrites) is not list
                or not 1 <= len(raw_rewrites) <= snapshot.profile.maximum_output_items
                or any(
                    type(value) is not str or not value or value.isspace()
                    for value in raw_rewrites
                )
            ):
                raise ValueError("rewrite output is invalid")
            return tuple(cast(list[str], raw_rewrites))

        parsed, receipt = self._execute(
            request,
            grant=grant,
            egress=egress,
            budget=budget,
            parse_output=parse,
        )
        return RewriteModelResult(rewrites=parsed, receipt=receipt)

    def rerank(
        self,
        request: RerankModelRequest,
        *,
        grant: ModelEgressGrant,
        egress: ModelInferenceEgressBinding,
        budget: PackageBudgetMeter,
    ) -> RerankModelResult:
        """Rerank exact-authorized projections and reject invented ordering."""

        if type(request) is not RerankModelRequest:
            raise TypeError("rerank requires RerankModelRequest")

        def parse(
            raw_output: bytes,
            snapshot: _RequestSnapshot,
        ) -> tuple[AuthorizedProjection, ...]:
            document = _parse_closed_document(raw_output)
            if set(document) != {"order"} or type(document["order"]) is not list:
                raise ValueError("rerank output has the wrong shape")
            order = document["order"]
            expected = list(range(len(snapshot.projections)))
            if (
                any(type(index) is not int for index in order)
                or sorted(cast(list[int], order)) != expected
            ):
                raise ValueError("rerank output must be one exact permutation")
            return tuple(
                snapshot.projections[index] for index in cast(list[int], order)
            )

        parsed, receipt = self._execute(
            request,
            grant=grant,
            egress=egress,
            budget=budget,
            parse_output=parse,
        )
        return RerankModelResult(
            projections=parsed,
            receipt=receipt,
        )

    def select(
        self,
        request: SelectModelRequest,
        *,
        grant: ModelEgressGrant,
        egress: ModelInferenceEgressBinding,
        budget: PackageBudgetMeter,
    ) -> SelectModelResult:
        """Select a bounded subset of exact-authorized projections."""

        if type(request) is not SelectModelRequest:
            raise TypeError("select requires SelectModelRequest")

        def parse(
            raw_output: bytes,
            snapshot: _RequestSnapshot,
        ) -> tuple[AuthorizedProjection, ...]:
            document = _parse_closed_document(raw_output)
            if set(document) != {"selected"} or type(document["selected"]) is not list:
                raise ValueError("select output has the wrong shape")
            selected = cast(list[object], document["selected"])
            if snapshot.maximum_items is None:
                raise ValueError("select maximum_items is unavailable")
            if (
                len(selected) > snapshot.maximum_items
                or any(type(index) is not int for index in selected)
                or len(cast(list[int], selected)) != len(set(cast(list[int], selected)))
                or any(
                    not 0 <= index < len(snapshot.projections)
                    for index in cast(list[int], selected)
                )
            ):
                raise ValueError("select output must be a bounded unique subset")
            return tuple(
                snapshot.projections[index] for index in cast(list[int], selected)
            )

        parsed, receipt = self._execute(
            request,
            grant=grant,
            egress=egress,
            budget=budget,
            parse_output=parse,
        )
        return SelectModelResult(
            projections=parsed,
            receipt=receipt,
        )


__all__ = [
    "MODEL_INFERENCE_DIGEST_PROFILE",
    "MODEL_INFERENCE_TOKENIZER_PROFILE",
    "ModelInferenceEgressBinding",
    "ModelInferenceOperation",
    "ModelInferenceOutcomeCategory",
    "ModelInferencePort",
    "ModelInferenceProfile",
    "ModelInferenceRetryPolicy",
    "ModelInferenceTraceReceipt",
    "ModelInferenceUnavailable",
    "RerankModelRequest",
    "RerankModelResult",
    "SelectModelRequest",
    "SelectModelResult",
    "RewriteModelRequest",
    "RewriteModelResult",
]
