"""Nominal exact-authorization and request-scoped Evidence contracts."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Final, NoReturn
from uuid import UUID

__all__ = [
    "AuthorizedProjection",
    "CandidateRef",
    "Evidence",
    "EvidenceLineage",
    "MAX_PROJECTED_FIELD_REFS",
    "PackageBlock",
    "PackageContent",
    "construct_package_content",
    "validate_projected_field_refs",
    "validate_package_content",
]

MAX_OPAQUE_REF_LENGTH: Final = 256
MAX_POLICY_EPOCH: Final = (1 << 63) - 1
MAX_PROJECTED_FIELD_REFS: Final = 64
MAX_PROJECTED_FIELD_REF_LENGTH: Final = 64
EVIDENCE_REF_PREFIX: Final = "ev"
EVIDENCE_REF_ENTROPY_LENGTH: Final = 64


def _require_opaque_ref(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_OPAQUE_REF_LENGTH
        or any(character.isspace() for character in value)
    ):
        raise ValueError(
            f"{field_name} must be a non-empty bounded opaque string without "
            "whitespace"
        )
    return value


def _require_nonblank_body(value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError("projected body must be a nonblank exact string")
    return value


def _require_utc_as_of(value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("Evidence as_of must be an aware UTC datetime")
    return value


def _require_evidence_ref(value: object) -> str:
    expected_length = (
        len(EVIDENCE_REF_PREFIX) + 1 + EVIDENCE_REF_ENTROPY_LENGTH
    )
    if (
        type(value) is not str
        or len(value) != expected_length
        or not value.startswith(f"{EVIDENCE_REF_PREFIX}_")
    ):
        raise ValueError("evidence_ref must use the closed opaque format")
    entropy = value[len(EVIDENCE_REF_PREFIX) + 1 :]
    if any(character not in "0123456789abcdef" for character in entropy):
        raise ValueError("evidence_ref must use lowercase opaque entropy")
    return value


def _length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(8, byteorder="big") + value


def _encode_text(value: str) -> bytes:
    return _length_prefix(value.encode("utf-8", "surrogatepass"))


@dataclass(frozen=True, slots=True)
class CandidateRef:
    """Content-free exact locator nominated by an untrusted candidate index."""

    organization_id: UUID = field(repr=False)
    source_ref: str = field(repr=False)
    resource_ref: str = field(repr=False)
    revision_ref: str = field(repr=False)
    fragment_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("CandidateRef organization_id must be UUID")
        for field_name in (
            "source_ref",
            "resource_ref",
            "revision_ref",
            "fragment_ref",
        ):
            _require_opaque_ref(
                f"CandidateRef {field_name}",
                getattr(self, field_name),
            )

    def __reduce__(self) -> NoReturn:
        raise TypeError("CandidateRef is not serializable")


@dataclass(frozen=True, slots=True)
class EvidenceLineage:
    """Complete request, principal, policy, and decision binding for Evidence."""

    run_ref: str = field(repr=False)
    principal_ref: str = field(repr=False)
    purpose: str = field(repr=False)
    as_of: datetime = field(repr=False)
    decision_ref: str = field(repr=False)
    policy_snapshot_ref: str = field(repr=False)
    policy_epoch: int = field(repr=False)
    source_acl_decision_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        for field_name in (
            "run_ref",
            "principal_ref",
            "purpose",
            "decision_ref",
            "policy_snapshot_ref",
            "source_acl_decision_ref",
        ):
            _require_opaque_ref(
                f"Evidence lineage {field_name}",
                getattr(self, field_name),
            )
        _require_utc_as_of(self.as_of)
        if (
            type(self.policy_epoch) is not int
            or not 1 <= self.policy_epoch <= MAX_POLICY_EPOCH
        ):
            raise ValueError(
                "Evidence lineage policy_epoch must fit a positive signed "
                "64-bit integer"
            )


class AuthorizedProjectionProvenance(StrEnum):
    """Closed provenance of an exact-authorized content projection."""

    AUTHORIZATION_KERNEL = "authorization_kernel"


class _AuthorizationKernelScope:
    """Private lifetime token owned by one sealed Kernel operation."""

    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("AuthorizationKernel scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("AuthorizationKernel scopes are not serializable")


_AUTHORIZATION_KERNEL_SCOPE_SEAL = object()


def _open_authorization_kernel_scope() -> _AuthorizationKernelScope:
    """Open the private construction lifetime for one Kernel operation."""

    scope = object.__new__(_AuthorizationKernelScope)
    scope._active = True
    scope._seal = _AUTHORIZATION_KERNEL_SCOPE_SEAL
    return scope


def _close_authorization_kernel_scope(scope: _AuthorizationKernelScope) -> None:
    """End the lifetime of every projection constructed by this operation."""

    if (
        type(scope) is not _AuthorizationKernelScope
        or getattr(scope, "_seal", None) is not _AUTHORIZATION_KERNEL_SCOPE_SEAL
    ):
        raise TypeError("AuthorizationKernel scope has the wrong nominal type")
    scope._active = False


def _projection_integrity_digest(
    candidate_ref: CandidateRef,
    projected_body: str,
    projected_field_refs: tuple[str, ...],
    lineage: EvidenceLineage,
) -> str:
    canonical = b"context-engine:authorized-projection:v2"
    canonical += _length_prefix(candidate_ref.organization_id.bytes)
    for value in (
        candidate_ref.source_ref,
        candidate_ref.resource_ref,
        candidate_ref.revision_ref,
        candidate_ref.fragment_ref,
        projected_body,
        lineage.run_ref,
        lineage.principal_ref,
        lineage.purpose,
        lineage.as_of.isoformat(timespec="microseconds"),
        lineage.decision_ref,
        lineage.policy_snapshot_ref,
        str(lineage.policy_epoch),
        lineage.source_acl_decision_ref,
    ):
        canonical += _encode_text(value)
    for field_ref in projected_field_refs:
        canonical += _encode_text(field_ref)
    return sha256(canonical).hexdigest()


def validate_projected_field_refs(value: object) -> tuple[str, ...]:
    """Return one exact field set that every public projection layer accepts."""

    if type(value) is not tuple or not value:
        raise ValueError("projected field refs must be a nonempty exact tuple")
    refs = value
    if len(refs) > MAX_PROJECTED_FIELD_REFS:
        raise ValueError(
            f"projected field refs must contain at most {MAX_PROJECTED_FIELD_REFS} "
            "items"
        )
    if any(
        type(ref) is not str
        or not ref
        or len(ref) > MAX_PROJECTED_FIELD_REF_LENGTH
        or ref[0] not in "abcdefghijklmnopqrstuvwxyz"
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in ref
        )
        for ref in refs
    ):
        raise ValueError("projected field refs must use closed lowercase identifiers")
    if len(refs) != len(set(refs)):
        raise ValueError("projected field refs must be unique")
    return refs


@dataclass(frozen=True, slots=True, init=False)
class AuthorizedProjection:
    """Content that only the sealed Kernel can project after exact authorization."""

    candidate_ref: CandidateRef = field(repr=False)
    projected_body: str = field(repr=False)
    projected_field_refs: tuple[str, ...] = field(repr=False)
    lineage: EvidenceLineage = field(repr=False)
    construction_provenance: AuthorizedProjectionProvenance
    _kernel_scope: _AuthorizationKernelScope = field(repr=False)
    _integrity_digest: str = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "AuthorizedProjection can only be constructed by AuthorizationKernel"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("AuthorizedProjection is not serializable")


def _require_active_authorization_kernel_scope(
    scope: _AuthorizationKernelScope,
) -> None:
    if (
        type(scope) is not _AuthorizationKernelScope
        or getattr(scope, "_seal", None) is not _AUTHORIZATION_KERNEL_SCOPE_SEAL
        or not getattr(scope, "_active", False)
    ):
        raise ValueError("projection requires an active AuthorizationKernel scope")


def _construct_authorized_projection(
    *,
    kernel_scope: _AuthorizationKernelScope,
    candidate_ref: CandidateRef,
    body: str,
    projected_field_refs: tuple[str, ...],
    lineage: EvidenceLineage,
) -> AuthorizedProjection:
    """Construct content after exact Kernel authorization and field projection."""

    _require_active_authorization_kernel_scope(kernel_scope)
    if type(candidate_ref) is not CandidateRef:
        raise TypeError("authorized candidate_ref must be CandidateRef")
    if type(lineage) is not EvidenceLineage:
        raise TypeError("authorized lineage must be EvidenceLineage")
    projected_body = _require_nonblank_body(body)
    field_refs = validate_projected_field_refs(projected_field_refs)
    projection = object.__new__(AuthorizedProjection)
    object.__setattr__(projection, "candidate_ref", candidate_ref)
    object.__setattr__(projection, "projected_body", projected_body)
    object.__setattr__(projection, "projected_field_refs", field_refs)
    object.__setattr__(projection, "lineage", lineage)
    object.__setattr__(
        projection,
        "construction_provenance",
        AuthorizedProjectionProvenance.AUTHORIZATION_KERNEL,
    )
    object.__setattr__(projection, "_kernel_scope", kernel_scope)
    object.__setattr__(
        projection,
        "_integrity_digest",
        _projection_integrity_digest(
            candidate_ref,
            projected_body,
            field_refs,
            lineage,
        ),
    )
    return projection


def _require_active_authorized_projection(
    projection: AuthorizedProjection,
) -> None:
    """Reject forged, mutated, or out-of-lifetime content before every use."""

    if type(projection) is not AuthorizedProjection:
        raise TypeError("content consumer requires AuthorizedProjection")
    if (
        getattr(projection, "construction_provenance", None)
        is not AuthorizedProjectionProvenance.AUTHORIZATION_KERNEL
    ):
        raise ValueError("AuthorizedProjection has invalid construction provenance")
    kernel_scope = getattr(projection, "_kernel_scope", None)
    if type(kernel_scope) is not _AuthorizationKernelScope:
        raise ValueError("AuthorizedProjection has invalid Kernel scope integrity")
    _require_active_authorization_kernel_scope(kernel_scope)
    candidate_ref = getattr(projection, "candidate_ref", None)
    projected_body = getattr(projection, "projected_body", None)
    projected_field_refs = getattr(projection, "projected_field_refs", None)
    lineage = getattr(projection, "lineage", None)
    if type(candidate_ref) is not CandidateRef:
        raise ValueError("AuthorizedProjection candidate integrity is invalid")
    if type(lineage) is not EvidenceLineage:
        raise ValueError("AuthorizedProjection lineage integrity is invalid")
    try:
        body = _require_nonblank_body(projected_body)
        field_refs = validate_projected_field_refs(projected_field_refs)
    except ValueError as error:
        raise ValueError("AuthorizedProjection field integrity is invalid") from error
    expected_digest = _projection_integrity_digest(
        candidate_ref,
        body,
        field_refs,
        lineage,
    )
    if getattr(projection, "_integrity_digest", None) != expected_digest:
        raise ValueError("AuthorizedProjection integrity check failed")


@dataclass(frozen=True, slots=True)
class Evidence:
    """Request-scoped designation of one selected authorized Fragment."""

    evidence_ref: str
    source_ref: str
    resource_ref: str
    revision_ref: str
    fragment_ref: str
    projected_field_refs: tuple[str, ...]
    lineage: EvidenceLineage
    _integrity_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_evidence_ref(self.evidence_ref)
        for field_name in (
            "source_ref",
            "resource_ref",
            "revision_ref",
            "fragment_ref",
        ):
            _require_opaque_ref(
                f"Evidence {field_name}",
                getattr(self, field_name),
            )
        if type(self.lineage) is not EvidenceLineage:
            raise TypeError("Evidence lineage must be EvidenceLineage")
        validate_projected_field_refs(self.projected_field_refs)
        self.lineage.__post_init__()
        object.__setattr__(self, "_integrity_digest", _evidence_integrity_digest(self))


@dataclass(frozen=True, slots=True)
class PackageBlock:
    """One selected authorized body linked to exactly one Evidence value."""

    evidence_ref: str
    body: str
    _integrity_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_evidence_ref(self.evidence_ref)
        _require_nonblank_body(self.body)
        object.__setattr__(self, "_integrity_digest", _block_integrity_digest(self))


def _lineage_canonical_bytes(lineage: EvidenceLineage) -> bytes:
    canonical = b""
    for value in (
        lineage.run_ref,
        lineage.principal_ref,
        lineage.purpose,
        lineage.as_of.isoformat(timespec="microseconds"),
        lineage.decision_ref,
        lineage.policy_snapshot_ref,
        str(lineage.policy_epoch),
        lineage.source_acl_decision_ref,
    ):
        canonical += _encode_text(value)
    return canonical


def _evidence_integrity_digest(evidence: Evidence) -> str:
    canonical = b"context-engine:evidence-integrity:v2"
    for value in (
        evidence.evidence_ref,
        evidence.source_ref,
        evidence.resource_ref,
        evidence.revision_ref,
        evidence.fragment_ref,
    ):
        canonical += _encode_text(value)
    for field_ref in evidence.projected_field_refs:
        canonical += _encode_text(field_ref)
    canonical += _lineage_canonical_bytes(evidence.lineage)
    return sha256(canonical).hexdigest()


def _block_integrity_digest(block: PackageBlock) -> str:
    canonical = b"context-engine:package-block-integrity:v1"
    canonical += _encode_text(block.evidence_ref)
    canonical += _encode_text(block.body)
    return sha256(canonical).hexdigest()


def _package_lineage_binding(lineage: EvidenceLineage) -> tuple[object, ...]:
    return (
        lineage.run_ref,
        lineage.principal_ref,
        lineage.purpose,
        lineage.as_of,
        lineage.decision_ref,
        lineage.policy_snapshot_ref,
        lineage.policy_epoch,
    )


def validate_package_content(
    blocks: tuple[PackageBlock, ...],
    evidence: tuple[Evidence, ...],
) -> None:
    """Prove exact block/Evidence cardinality and one request-decision lineage."""

    if type(blocks) is not tuple or any(
        type(block) is not PackageBlock for block in blocks
    ):
        raise TypeError("package blocks must be a tuple of PackageBlock")
    if type(evidence) is not tuple or any(
        type(item) is not Evidence for item in evidence
    ):
        raise TypeError("package evidence must be a tuple of Evidence")
    for block in blocks:
        _require_evidence_ref(block.evidence_ref)
        _require_nonblank_body(block.body)
        if block._integrity_digest != _block_integrity_digest(block):
            raise ValueError("PackageBlock integrity check failed")
    for item in evidence:
        item.lineage.__post_init__()
        if item._integrity_digest != _evidence_integrity_digest(item):
            raise ValueError("Evidence integrity check failed")

    block_refs = tuple(block.evidence_ref for block in blocks)
    evidence_refs = tuple(item.evidence_ref for item in evidence)
    if len(block_refs) != len(set(block_refs)):
        raise ValueError("package contains a duplicate block evidence_ref")
    if len(evidence_refs) != len(set(evidence_refs)):
        raise ValueError("package contains a duplicate Evidence evidence_ref")

    fragment_identities = tuple(
        (
            item.source_ref,
            item.resource_ref,
            item.revision_ref,
            item.fragment_ref,
        )
        for item in evidence
    )
    if len(fragment_identities) != len(set(fragment_identities)):
        raise ValueError("package contains a duplicate Fragment Evidence")

    dangling = set(block_refs).difference(evidence_refs)
    if dangling:
        raise ValueError("package contains a dangling block evidence_ref")
    orphan = set(evidence_refs).difference(block_refs)
    if orphan:
        raise ValueError("package contains orphan Evidence")

    if evidence:
        expected_binding = _package_lineage_binding(evidence[0].lineage)
        if any(
            _package_lineage_binding(item.lineage) != expected_binding
            for item in evidence[1:]
        ):
            raise ValueError("package Evidence must share one request decision")


@dataclass(frozen=True, slots=True, init=False)
class PackageContent:
    """Validated deterministic content portion of one ContextPackage."""

    blocks: tuple[PackageBlock, ...]
    evidence: tuple[Evidence, ...]

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "PackageContent can only be constructed from authorized projections"
        )


def _construct_validated_package_content(
    blocks: tuple[PackageBlock, ...],
    evidence: tuple[Evidence, ...],
) -> PackageContent:
    validate_package_content(blocks, evidence)
    content = object.__new__(PackageContent)
    object.__setattr__(content, "blocks", blocks)
    object.__setattr__(content, "evidence", evidence)
    return content


def _candidate_sort_key(candidate_ref: CandidateRef) -> tuple[object, ...]:
    return (
        candidate_ref.organization_id.bytes,
        candidate_ref.source_ref,
        candidate_ref.resource_ref,
        candidate_ref.revision_ref,
        candidate_ref.fragment_ref,
    )


def _evidence_ref_for_projection(projection: AuthorizedProjection) -> str:
    candidate_ref = projection.candidate_ref
    lineage = projection.lineage
    canonical = b"context-engine:evidence-ref:v2"
    canonical += _length_prefix(candidate_ref.organization_id.bytes)
    for value in (
        candidate_ref.source_ref,
        candidate_ref.resource_ref,
        candidate_ref.revision_ref,
        candidate_ref.fragment_ref,
        projection.projected_body,
        lineage.run_ref,
        lineage.principal_ref,
        lineage.purpose,
        lineage.as_of.isoformat(timespec="microseconds"),
        lineage.decision_ref,
        lineage.policy_snapshot_ref,
        str(lineage.policy_epoch),
        lineage.source_acl_decision_ref,
    ):
        canonical += _encode_text(value)
    for field_ref in projection.projected_field_refs:
        canonical += _encode_text(field_ref)
    return f"{EVIDENCE_REF_PREFIX}_{sha256(canonical).hexdigest()}"


def construct_package_content(
    projections: tuple[AuthorizedProjection, ...],
) -> PackageContent:
    """Build deterministic Package content only from active authorized values."""

    if type(projections) is not tuple or any(
        type(projection) is not AuthorizedProjection for projection in projections
    ):
        raise TypeError("package content requires a tuple of AuthorizedProjection")
    for projection in projections:
        _require_active_authorized_projection(projection)

    if projections:
        organization_id = projections[0].candidate_ref.organization_id
        if any(
            projection.candidate_ref.organization_id != organization_id
            for projection in projections[1:]
        ):
            raise ValueError("package projections must belong to one Organization")
        expected_binding = _package_lineage_binding(projections[0].lineage)
        if any(
            _package_lineage_binding(projection.lineage) != expected_binding
            for projection in projections[1:]
        ):
            raise ValueError("package projections must share one request decision")

    ordered = sorted(
        projections,
        key=lambda projection: _candidate_sort_key(projection.candidate_ref),
    )
    blocks: list[PackageBlock] = []
    evidence: list[Evidence] = []
    for projection in ordered:
        candidate_ref = projection.candidate_ref
        evidence_ref = _evidence_ref_for_projection(projection)
        evidence.append(
            Evidence(
                evidence_ref=evidence_ref,
                source_ref=candidate_ref.source_ref,
                resource_ref=candidate_ref.resource_ref,
                revision_ref=candidate_ref.revision_ref,
                fragment_ref=candidate_ref.fragment_ref,
                projected_field_refs=projection.projected_field_refs,
                lineage=projection.lineage,
            )
        )
        blocks.append(
            PackageBlock(
                evidence_ref=evidence_ref,
                body=projection.projected_body,
            )
        )
    return _construct_validated_package_content(tuple(blocks), tuple(evidence))
