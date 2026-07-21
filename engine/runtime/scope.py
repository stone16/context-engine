"""Pure finite-set authorization scope oracle."""

from dataclasses import dataclass, field, fields
from hashlib import sha256
from typing import NoReturn
from uuid import UUID

from engine.runtime.contracts import RequestNarrowing

__all__ = [
    "EffectiveScope",
    "MISSING_TRUSTED_SCOPE",
    "OMITTED_REQUEST_NARROWING",
    "MissingTrustedScope",
    "OmittedRequestNarrowing",
    "ScopeSet",
    "ScopeTarget",
    "TrustedScopeOperands",
    "compute_effective_scope",
]


def _require_nonblank_exact_string(field_name: str, value: object) -> None:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"{field_name} must be a nonblank exact string")


@dataclass(frozen=True, slots=True)
class ScopeTarget:
    """One exact Organization/source/resource authorization atom."""

    organization_id: UUID = field(repr=False)
    source_ref: str = field(repr=False)
    resource_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("organization_id must be UUID")
        _require_nonblank_exact_string("source_ref", self.source_ref)
        if self.resource_ref is not None:
            _require_nonblank_exact_string("resource_ref", self.resource_ref)


@dataclass(frozen=True, slots=True)
class ScopeSet:
    """One exact nominal finite set of authorization targets."""

    targets: frozenset[ScopeTarget] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.targets) is not frozenset or any(
            type(target) is not ScopeTarget for target in self.targets
        ):
            raise TypeError("ScopeSet targets must be a frozenset of ScopeTarget")


class MissingTrustedScope:
    """Explicit fail-closed value for one unavailable trusted operand."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("MissingTrustedScope is not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("MissingTrustedScope is not serializable")

    def __repr__(self) -> str:
        return "MissingTrustedScope"


class OmittedRequestNarrowing:
    """Explicit identity value for an omitted untrusted narrowing filter."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("OmittedRequestNarrowing is not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("OmittedRequestNarrowing is not serializable")

    def __repr__(self) -> str:
        return "OmittedRequestNarrowing"


MISSING_TRUSTED_SCOPE = object.__new__(MissingTrustedScope)
OMITTED_REQUEST_NARROWING = object.__new__(OmittedRequestNarrowing)
type TrustedScopeOperand = ScopeSet | MissingTrustedScope


@dataclass(frozen=True, slots=True)
class TrustedScopeOperands:
    """The seven named trusted inputs required by the scope oracle."""

    organization_boundary: TrustedScopeOperand = field(repr=False)
    membership_rights: TrustedScopeOperand = field(repr=False)
    principal_grants: TrustedScopeOperand = field(repr=False)
    agent_ceiling: TrustedScopeOperand = field(repr=False)
    source_native_acl: TrustedScopeOperand = field(repr=False)
    resource_acl: TrustedScopeOperand = field(repr=False)
    purpose_policy: TrustedScopeOperand = field(repr=False)

    def __post_init__(self) -> None:
        for operand_field in fields(self):
            value = getattr(self, operand_field.name)
            if type(value) is ScopeSet:
                continue
            if value is MISSING_TRUSTED_SCOPE:
                continue
            raise TypeError(
                f"{operand_field.name} must be ScopeSet or MISSING_TRUSTED_SCOPE"
            )


@dataclass(frozen=True, slots=True)
class EffectiveScope:
    """Immutable result of the sole finite scope intersection oracle."""

    targets: frozenset[ScopeTarget] = field(repr=False)
    digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.targets) is not frozenset or any(
            type(target) is not ScopeTarget for target in self.targets
        ):
            raise TypeError(
                "EffectiveScope targets must be a frozenset of ScopeTarget"
            )
        object.__setattr__(self, "digest", _effective_scope_digest(self.targets))


def _length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(8, byteorder="big") + value


def _canonical_target_bytes(target: ScopeTarget) -> bytes:
    resource_bytes = (
        b"\x00"
        if target.resource_ref is None
        else b"\x01"
        + _length_prefix(target.resource_ref.encode("utf-8", "surrogatepass"))
    )
    return b"".join(
        (
            _length_prefix(target.organization_id.bytes),
            _length_prefix(target.source_ref.encode("utf-8", "surrogatepass")),
            resource_bytes,
        )
    )


def _effective_scope_digest(targets: frozenset[ScopeTarget]) -> str:
    encoded_targets = sorted(_canonical_target_bytes(target) for target in targets)
    canonical = b"context-engine:effective-scope:v1" + len(encoded_targets).to_bytes(
        8, byteorder="big"
    )
    canonical += b"".join(_length_prefix(target) for target in encoded_targets)
    return sha256(canonical).hexdigest()


def compute_effective_scope(
    trusted_operands: TrustedScopeOperands,
    request_narrowing: RequestNarrowing | OmittedRequestNarrowing,
) -> EffectiveScope:
    """Intersect all trusted operands, then apply optional exact ref filters."""

    if type(trusted_operands) is not TrustedScopeOperands:
        raise TypeError("trusted_operands must be TrustedScopeOperands")
    if (
        type(request_narrowing) is not RequestNarrowing
        and request_narrowing is not OMITTED_REQUEST_NARROWING
    ):
        raise TypeError(
            "request_narrowing must be RequestNarrowing or "
            "OMITTED_REQUEST_NARROWING"
        )

    operands = tuple(
        getattr(trusted_operands, operand_field.name)
        for operand_field in fields(trusted_operands)
    )
    if any(operand is MISSING_TRUSTED_SCOPE for operand in operands):
        return EffectiveScope(frozenset())

    scopes = tuple(operand for operand in operands if type(operand) is ScopeSet)
    intersection = set(scopes[0].targets)
    for scope in scopes[1:]:
        intersection.intersection_update(scope.targets)

    if type(request_narrowing) is RequestNarrowing:
        if request_narrowing.source_refs is not None:
            source_refs = frozenset(request_narrowing.source_refs)
            intersection = {
                target for target in intersection if target.source_ref in source_refs
            }
        if request_narrowing.resource_refs is not None:
            resource_refs = frozenset(request_narrowing.resource_refs)
            intersection = {
                target
                for target in intersection
                if target.resource_ref in resource_refs
            }

    return EffectiveScope(frozenset(intersection))
