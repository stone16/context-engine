"""Lifetime-bound same-transaction materialized Fragment projection seam."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, NoReturn, Protocol, runtime_checkable
from uuid import UUID

from engine.runtime.evidence import (
    MAX_PROJECTED_FIELD_REF_LENGTH,
    MAX_PROJECTED_FIELD_REFS,
    CandidateRef,
    validate_projected_field_refs,
)
from engine.runtime.scope import (
    CandidateDiscoveryScope,
    EffectiveScope,
    ScopeTarget,
    _require_candidate_discovery_scope_integrity,
)

__all__ = [
    "CandidateDiscoverySession",
    "ExactPhraseDiscoveryRequest",
    "MaterializedFieldValue",
    "MaterializedFragmentLocator",
    "MaterializedFragmentProjection",
    "MaterializedFragmentWindowRead",
    "MaterializedFragmentWindowItem",
    "MaterializedProjectionKind",
    "MaterializedProjectionPort",
    "MaterializedProjectionSession",
    "MaterializedPublicationTrace",
    "MaterializedScopeOperands",
    "MaterializedScopePort",
    "MaterializedScopeUnavailable",
    "VectorDiscoveryRequest",
]

_STRUCTURED_FIELD_LINE_BREAKS: Final = frozenset(
    "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
)


def _require_nonblank_ref(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"{field_name} must be a nonblank exact string")
    return value


def _require_field_ref(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_PROJECTED_FIELD_REF_LENGTH
        or value[0] not in "abcdefghijklmnopqrstuvwxyz"
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in value
        )
    ):
        raise ValueError(
            "materialized field_ref must be a bounded lowercase identifier"
        )
    return value


def _encode_structured_field_value(value: str) -> str:
    """Escape structural delimiters while preserving ordinary text exactly."""

    encoded: list[str] = []
    for character in value:
        if character == "\\":
            encoded.append("\\\\")
        elif character == "=":
            encoded.append("\\=")
        elif character in _STRUCTURED_FIELD_LINE_BREAKS:
            encoded.append(f"\\u{ord(character):04x}")
        else:
            encoded.append(character)
    return "".join(encoded)


class MaterializedProjectionKind(StrEnum):
    """Closed persisted Fragment representations understood by Runtime."""

    LEGACY_BODY = "body"
    STRUCTURED_FIELDS = "fields"


class MaterializedScopeUnavailable(RuntimeError):
    """Same-transaction scope facts could not be established."""


@dataclass(frozen=True, slots=True)
class MaterializedScopeOperands:
    """Independent durable operands observed by one UserActor transaction."""

    organization_boundary: frozenset[ScopeTarget]
    membership_rights: frozenset[ScopeTarget]
    principal_grants: frozenset[ScopeTarget]
    source_native_acl: frozenset[ScopeTarget]
    resource_acl: frozenset[ScopeTarget]

    def __post_init__(self) -> None:
        for field_name in (
            "organization_boundary",
            "membership_rights",
            "principal_grants",
            "source_native_acl",
            "resource_acl",
        ):
            targets = getattr(self, field_name)
            if type(targets) is not frozenset or any(
                type(target) is not ScopeTarget or target.resource_ref is None
                for target in targets
            ):
                raise TypeError(
                    f"materialized {field_name} must contain exact Resource targets"
                )


@dataclass(frozen=True, slots=True)
class MaterializedFieldValue:
    """One already-authorized field returned by the retained transaction."""

    field_ref: str = field(repr=False)
    field_value: str = field(repr=False)
    ordinal: int

    def __post_init__(self) -> None:
        _require_field_ref(self.field_ref)
        if (
            type(self.field_value) is not str
            or not self.field_value
            or self.field_value.isspace()
        ):
            raise ValueError("materialized field_value must be a nonblank string")
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("materialized field ordinal must be nonnegative")


@dataclass(frozen=True, slots=True)
class MaterializedFragmentProjection:
    """Fields already reduced by one trusted Membership projection ceiling."""

    kind: MaterializedProjectionKind
    fields: tuple[MaterializedFieldValue, ...] = field(repr=False)
    projection_ceiling: frozenset[str] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not MaterializedProjectionKind:
            raise TypeError("materialized projection kind must be closed")
        if type(self.fields) is not tuple or not self.fields or any(
            type(value) is not MaterializedFieldValue for value in self.fields
        ):
            raise ValueError("materialized projection requires authorized fields")
        if (
            type(self.projection_ceiling) is not frozenset
            or not self.projection_ceiling
        ):
            raise ValueError("materialized projection ceiling must be nonempty")
        for field_ref in self.projection_ceiling:
            _require_field_ref(field_ref)
        field_refs = tuple(value.field_ref for value in self.fields)
        try:
            validate_projected_field_refs(field_refs)
        except ValueError as error:
            raise ValueError(
                "materialized projected field refs must be unique, valid, and "
                f"contain at most {MAX_PROJECTED_FIELD_REFS} items"
            ) from error
        if tuple(value.ordinal for value in self.fields) != tuple(
            range(len(self.fields))
        ):
            raise ValueError("materialized fields require canonical ordinal order")
        if not set(field_refs).issubset(self.projection_ceiling):
            raise ValueError(
                "materialized projection returned a field outside its trusted ceiling"
            )
        if self.kind is MaterializedProjectionKind.LEGACY_BODY and field_refs != (
            "body",
        ):
            raise ValueError("legacy body projection requires only the body field")
        if (
            self.kind is MaterializedProjectionKind.STRUCTURED_FIELDS
            and "body" in field_refs
        ):
            raise ValueError("structured projection cannot contain the legacy body")

    @property
    def projected_field_refs(self) -> tuple[str, ...]:
        return tuple(value.field_ref for value in self.fields)

    @property
    def rendered_body(self) -> str:
        if self.kind is MaterializedProjectionKind.LEGACY_BODY:
            return self.fields[0].field_value
        return "\n".join(
            f"{value.field_ref}={_encode_structured_field_value(value.field_value)}"
            for value in self.fields
        )


@dataclass(frozen=True, slots=True)
class MaterializedFragmentLocator:
    """Authoritative active lineage without any Fragment body fields."""

    organization_id: UUID = field(repr=False)
    source_ref: str = field(repr=False)
    resource_ref: str = field(repr=False)
    revision_ref: str = field(repr=False)
    fragment_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("materialized organization_id must be UUID")
        for field_name in (
            "source_ref",
            "resource_ref",
            "revision_ref",
            "fragment_ref",
        ):
            _require_nonblank_ref(
                f"materialized {field_name}",
                getattr(self, field_name),
            )


@dataclass(frozen=True, slots=True)
class MaterializedPublicationTrace:
    """Authorized, content-free observation of one active publication lineage."""

    states: tuple[str, ...]
    active_revision_ref: str

    def __post_init__(self) -> None:
        if self.states != ("prepared", "indexed", "active"):
            raise ValueError("publication trace must have the closed initial sequence")
        _require_nonblank_ref("active revision ref", self.active_revision_ref)


@dataclass(frozen=True, slots=True)
class MaterializedFragmentWindowItem:
    """One active same-Article/current-Revision Fragment in ordinal order."""

    locator: MaterializedFragmentLocator
    projection: MaterializedFragmentProjection

    def __post_init__(self) -> None:
        if type(self.locator) is not MaterializedFragmentLocator:
            raise TypeError("window item locator has the wrong nominal type")
        if type(self.projection) is not MaterializedFragmentProjection:
            raise TypeError("window item projection has the wrong nominal type")


@dataclass(frozen=True, slots=True)
class MaterializedFragmentWindowRead:
    """Authoritative expansion read from one retained Runtime transaction."""

    items: tuple[MaterializedFragmentWindowItem, ...] = field(repr=False)
    reauthorization_refs: tuple[CandidateRef, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            type(item) is not MaterializedFragmentWindowItem for item in self.items
        ):
            raise TypeError("materialized fragment window requires exact items")
        if type(self.reauthorization_refs) is not tuple or any(
            type(candidate) is not CandidateRef
            for candidate in self.reauthorization_refs
        ):
            raise TypeError(
                "materialized fragment window requires exact reauthorization refs"
            )


class MaterializedProjectionPort(Protocol):
    """Narrow operations executed by the owning current database transaction."""

    def source_is_active(self, source_ref: UUID) -> bool: ...

    def observe_publication(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedPublicationTrace | None: ...

    def locate(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedFragmentLocator | None: ...

    def project(
        self,
        locator: MaterializedFragmentLocator,
    ) -> MaterializedFragmentProjection | None: ...


@runtime_checkable
class MaterializedScopePort(Protocol):
    """Explicit optional capability for independent trusted scope operands."""

    def current_scope_operands(
        self,
        active_revision_ids: tuple[UUID, ...],
    ) -> MaterializedScopeOperands: ...


@runtime_checkable
class _MaterializedFragmentWindowPort(Protocol):
    def read_fragment_window(
        self,
        anchor: MaterializedFragmentLocator,
        before: int,
        after: int,
        expansion_candidates: tuple[CandidateRef, ...],
    ) -> MaterializedFragmentWindowRead: ...


class _MaterializedProjectionScope:
    """Private lifetime token owned by one current UserActor transaction."""

    __slots__ = ("_active", "_seal")
    _active: bool
    _seal: object

    def __init__(self) -> None:
        raise TypeError("materialized projection scopes are not constructible")

    def __reduce__(self) -> NoReturn:
        raise TypeError("materialized projection scopes are not serializable")


_MATERIALIZED_PROJECTION_SCOPE_SEAL = object()


def _open_materialized_projection_scope() -> _MaterializedProjectionScope:
    scope = object.__new__(_MaterializedProjectionScope)
    scope._active = True
    scope._seal = _MATERIALIZED_PROJECTION_SCOPE_SEAL
    return scope


def _close_materialized_projection_scope(
    scope: _MaterializedProjectionScope,
) -> None:
    if (
        type(scope) is not _MaterializedProjectionScope
        or getattr(scope, "_seal", None) is not _MATERIALIZED_PROJECTION_SCOPE_SEAL
    ):
        raise TypeError("materialized projection scope has the wrong nominal type")
    scope._active = False


@dataclass(frozen=True, slots=True, init=False)
class MaterializedProjectionSession:
    """Nominal projection capability valid only inside its owning transaction."""

    _authority_scope: _MaterializedProjectionScope = field(repr=False)
    _port: MaterializedProjectionPort = field(repr=False)
    _scope_port: MaterializedScopePort | None = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "MaterializedProjectionSession can only be constructed by a trusted "
            "transaction authority"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("MaterializedProjectionSession is not serializable")


@dataclass(frozen=True, slots=True)
class ExactPhraseDiscoveryRequest:
    """Validated content-free exact-phrase lookup plan."""

    phrase_digest: str

    def __post_init__(self) -> None:
        if type(self.phrase_digest) is not str or not self.phrase_digest:
            raise ValueError("exact phrase digest must be nonblank")


@dataclass(frozen=True, slots=True)
class VectorDiscoveryRequest:
    """Validated content-free vector lookup plan."""

    query_embedding: tuple[float, ...]
    limit: int
    source_refs: tuple[str, ...] | None = None
    resource_refs: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if type(self.query_embedding) is not tuple or not self.query_embedding:
            raise ValueError("vector discovery requires a nonempty query embedding")
        if any(type(value) is not float for value in self.query_embedding):
            raise TypeError("vector discovery embedding requires exact floats")
        if type(self.limit) is not int or self.limit <= 0:
            raise ValueError("vector discovery requires a positive exact limit")
        for field_name, refs in (
            ("source_refs", self.source_refs),
            ("resource_refs", self.resource_refs),
        ):
            if refs is not None and (
                type(refs) is not tuple
                or not refs
                or any(type(ref) is not str or not ref for ref in refs)
            ):
                raise ValueError(
                    f"vector discovery {field_name} must be nonempty refs"
                )


CandidateDiscoveryRequest = ExactPhraseDiscoveryRequest | VectorDiscoveryRequest


class CandidateDiscoverySession:
    """Disposable data-only discovery result; carries no database capability."""

    __slots__ = ("_active", "_candidates")
    _active: bool
    _candidates: tuple[CandidateRef, ...]

    def __init__(self) -> None:
        raise TypeError(
            "CandidateDiscoverySession can only be constructed by Runtime"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("CandidateDiscoverySession is not serializable")


def _require_active_materialized_projection_session(
    session: MaterializedProjectionSession,
) -> None:
    if type(session) is not MaterializedProjectionSession:
        raise TypeError("materialized projection session has the wrong nominal type")
    scope = session._authority_scope
    if (
        type(scope) is not _MaterializedProjectionScope
        or getattr(scope, "_seal", None) is not _MATERIALIZED_PROJECTION_SCOPE_SEAL
        or not getattr(scope, "_active", False)
    ):
        raise ValueError(
            "materialized projection requires an active materialized projection "
            "scope"
        )


def _construct_materialized_projection_session(
    *,
    authority_scope: _MaterializedProjectionScope,
    port: MaterializedProjectionPort,
) -> MaterializedProjectionSession:
    if (
        type(authority_scope) is not _MaterializedProjectionScope
        or getattr(authority_scope, "_seal", None)
        is not _MATERIALIZED_PROJECTION_SCOPE_SEAL
        or not getattr(authority_scope, "_active", False)
    ):
        raise ValueError(
            "materialized projection requires an active materialized projection "
            "scope"
        )
    if (
        not callable(getattr(port, "source_is_active", None))
        or not callable(getattr(port, "locate", None))
        or not callable(getattr(port, "project", None))
        or not callable(getattr(port, "observe_publication", None))
    ):
        raise TypeError("materialized projection port is incomplete")
    session = object.__new__(MaterializedProjectionSession)
    object.__setattr__(session, "_authority_scope", authority_scope)
    object.__setattr__(session, "_port", port)
    object.__setattr__(
        session,
        "_scope_port",
        port if isinstance(port, MaterializedScopePort) else None,
    )
    return session


def _construct_candidate_discovery_session(
    session: MaterializedProjectionSession,
    request: CandidateDiscoveryRequest | None,
    *,
    effective_scope: EffectiveScope,
) -> CandidateDiscoverySession:
    """Execute trusted lookup, then expose only its content-free result."""

    _require_active_materialized_projection_session(session)
    from engine.runtime.scope import _require_effective_scope_integrity

    _require_effective_scope_integrity(effective_scope)
    if request is None:
        candidates: tuple[CandidateRef, ...] = ()
    elif type(request) is ExactPhraseDiscoveryRequest:
        discover_exact_phrase = getattr(
            session._port,
            "discover_exact_phrase",
            None,
        )
        if not callable(discover_exact_phrase):
            raise TypeError("materialized candidate discovery port is incomplete")
        candidates = discover_exact_phrase(request.phrase_digest)
    elif type(request) is VectorDiscoveryRequest:
        discover_vector = getattr(session._port, "discover_vector", None)
        if not callable(discover_vector):
            raise TypeError("materialized candidate discovery port is incomplete")
        candidates = discover_vector(
            request.query_embedding,
            request.limit,
            request.source_refs,
            request.resource_refs,
            frozenset(effective_scope.targets),
        )
        if len(candidates) > request.limit:
            raise TypeError(
                "materialized vector discovery must return bounded candidates"
            )
    else:
        raise TypeError("candidate discovery request has the wrong nominal type")
    if type(candidates) is not tuple or any(
        type(candidate) is not CandidateRef for candidate in candidates
    ):
        raise TypeError(
            "materialized discovery must return exact CandidateRef values"
        )
    discovery = object.__new__(CandidateDiscoverySession)
    discovery._active = True
    discovery._candidates = candidates
    return discovery


def _candidate_discovery_candidates(
    session: CandidateDiscoverySession,
) -> tuple[CandidateRef, ...]:
    if type(session) is not CandidateDiscoverySession:
        raise TypeError("candidate discovery session has the wrong nominal type")
    if not session._active:
        raise ValueError("candidate discovery session is inactive")
    return session._candidates


def _discover_materialized_candidates(
    session: CandidateDiscoverySession,
) -> tuple[CandidateRef, ...]:
    """Return the already-materialized content-free lookup result once."""

    return _candidate_discovery_candidates(session)


def _close_candidate_discovery_session(
    session: CandidateDiscoverySession,
) -> None:
    if type(session) is not CandidateDiscoverySession:
        raise TypeError("candidate discovery session has the wrong nominal type")
    session._active = False
    session._candidates = ()


def _current_materialized_scope_operands(
    session: MaterializedProjectionSession,
    active_revision_refs: tuple[str, ...],
) -> MaterializedScopeOperands:
    """Read independent File-scope facts in the retained transaction."""

    _require_active_materialized_projection_session(session)
    if type(active_revision_refs) is not tuple or not active_revision_refs:
        raise MaterializedScopeUnavailable(
            "materialized scope release selection is unavailable"
        )
    try:
        revision_ids = tuple(UUID(value) for value in active_revision_refs)
    except (AttributeError, TypeError, ValueError):
        raise MaterializedScopeUnavailable(
            "materialized scope release selection is unavailable"
        ) from None
    if any(str(value) != reference for value, reference in zip(
        revision_ids,
        active_revision_refs,
        strict=True,
    )):
        raise MaterializedScopeUnavailable(
            "materialized scope release selection is unavailable"
        )
    if session._scope_port is None:
        raise MaterializedScopeUnavailable(
            "materialized scope authority is unavailable"
        )
    operands = session._scope_port.current_scope_operands(revision_ids)
    if type(operands) is not MaterializedScopeOperands:
        raise TypeError("materialized scope authority returned the wrong nominal type")
    return operands


def _is_materialized_source_active(
    session: MaterializedProjectionSession,
    source_ref: UUID,
) -> bool:
    """Read File-source lifecycle on the current UserActor transaction."""

    _require_active_materialized_projection_session(session)
    if type(source_ref) is not UUID:
        raise TypeError("materialized source reference must be UUID")
    observed = session._port.source_is_active(source_ref)
    if type(observed) is not bool:
        raise TypeError("materialized source lifecycle returned a non-boolean")
    return observed


def _locate_materialized_fragment(
    session: MaterializedProjectionSession,
    candidate_ref: CandidateRef,
) -> MaterializedFragmentLocator | None:
    _require_active_materialized_projection_session(session)
    if type(candidate_ref) is not CandidateRef:
        raise TypeError("materialized locator requires CandidateRef")
    locator = session._port.locate(candidate_ref)
    if locator is not None and type(locator) is not MaterializedFragmentLocator:
        raise TypeError("materialized locator port returned the wrong nominal type")
    return locator


def _discover_materialized_exact_phrase(
    session: CandidateDiscoverySession,
    phrase_digest: str,
) -> tuple[CandidateRef, ...]:
    """Discover content-free lineage on the retained current-UserActor transaction."""

    if type(phrase_digest) is not str or not phrase_digest:
        raise ValueError("exact phrase digest must be nonblank")
    del phrase_digest
    return _candidate_discovery_candidates(session)


def _discover_materialized_vector(
    session: CandidateDiscoverySession,
    query_embedding: tuple[float, ...],
    limit: int,
    *,
    source_refs: tuple[str, ...] | None = None,
    resource_refs: tuple[str, ...] | None = None,
    effective_scope: CandidateDiscoveryScope,
) -> tuple[CandidateRef, ...]:
    """Discover bounded content-free ANN lineage in the retained transaction."""

    if type(query_embedding) is not tuple or not query_embedding:
        raise ValueError("vector discovery requires a nonempty query embedding")
    if type(limit) is not int or limit <= 0:
        raise ValueError("vector discovery requires a positive exact limit")
    _require_candidate_discovery_scope_integrity(effective_scope)
    for field_name, refs in (
        ("source_refs", source_refs),
        ("resource_refs", resource_refs),
    ):
        if refs is not None and (
            type(refs) is not tuple
            or not refs
            or any(type(ref) is not str or not ref for ref in refs)
        ):
            raise ValueError(f"vector discovery {field_name} must be nonempty refs")
    candidates = _candidate_discovery_candidates(session)
    if (
        type(candidates) is not tuple
        or len(candidates) > limit
        or any(type(candidate) is not CandidateRef for candidate in candidates)
    ):
        raise TypeError(
            "materialized vector discovery must return bounded exact CandidateRef "
            "values"
        )
    return candidates


def _observe_materialized_publication(
    session: MaterializedProjectionSession,
    candidate_ref: CandidateRef,
) -> MaterializedPublicationTrace | None:
    """Read initial publication state on the current UserActor transaction."""

    _require_active_materialized_projection_session(session)
    if type(candidate_ref) is not CandidateRef:
        raise TypeError("publication observation requires CandidateRef")
    observed = session._port.observe_publication(candidate_ref)
    if observed is not None and type(observed) is not MaterializedPublicationTrace:
        raise TypeError("publication observation returned the wrong nominal type")
    return observed


def _project_materialized_fragment(
    session: MaterializedProjectionSession,
    locator: MaterializedFragmentLocator,
) -> MaterializedFragmentProjection | None:
    _require_active_materialized_projection_session(session)
    if type(locator) is not MaterializedFragmentLocator:
        raise TypeError("materialized field projection requires exact locator")
    projection = session._port.project(locator)
    if projection is None:
        return None
    if type(projection) is not MaterializedFragmentProjection:
        raise TypeError("materialized projection port returned the wrong nominal type")
    projection.__post_init__()
    return projection


def _read_materialized_fragment_window(
    session: MaterializedProjectionSession,
    anchor: MaterializedFragmentLocator,
    before: int,
    after: int,
    expansion_candidates: tuple[CandidateRef, ...] = (),
) -> MaterializedFragmentWindowRead:
    """Read a bounded active Article/Revision window in the retained transaction."""

    _require_active_materialized_projection_session(session)
    if type(anchor) is not MaterializedFragmentLocator:
        raise TypeError("fragment window requires exact active locator")
    if (
        type(before) is not int
        or type(after) is not int
        or not 0 <= before <= 32
        or not 0 <= after <= 32
    ):
        raise ValueError("fragment window bounds must be exact integers from 0 to 32")
    if type(expansion_candidates) is not tuple or any(
        type(candidate) is not CandidateRef for candidate in expansion_candidates
    ):
        raise TypeError("fragment expansion candidates require exact refs")
    if len(expansion_candidates) > 64:
        raise ValueError("fragment expansion candidates must be bounded")
    if not isinstance(session._port, _MaterializedFragmentWindowPort):
        raise TypeError("materialized projection port has no fragment window")
    read = session._port.read_fragment_window(
        anchor,
        before,
        after,
        expansion_candidates,
    )
    if type(read) is not MaterializedFragmentWindowRead:
        raise TypeError("materialized fragment window returned the wrong nominal type")
    read.__post_init__()
    items = read.items
    if any(
        type(item) is not MaterializedFragmentWindowItem for item in items
    ):
        raise TypeError("materialized fragment window returned the wrong nominal type")
    for item in items:
        item.__post_init__()
        if (
            item.locator.organization_id != anchor.organization_id
            or item.locator.source_ref != anchor.source_ref
            or item.locator.resource_ref != anchor.resource_ref
            or item.locator.revision_ref != anchor.revision_ref
        ):
            raise ValueError("materialized fragment window crossed Article lineage")
    if len(set(read.reauthorization_refs)) != len(read.reauthorization_refs):
        raise ValueError("materialized fragment expansion refs must be unique")
    if any(
        candidate.organization_id == anchor.organization_id
        and candidate.source_ref == anchor.source_ref
        and candidate.resource_ref == anchor.resource_ref
        for candidate in read.reauthorization_refs
    ):
        raise ValueError("same-Article expansion cannot request reauthorization")
    return read
