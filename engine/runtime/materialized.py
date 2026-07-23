"""Lifetime-bound same-transaction materialized Fragment projection seam."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, NoReturn, Protocol
from uuid import UUID

from engine.runtime.evidence import (
    MAX_PROJECTED_FIELD_REF_LENGTH,
    MAX_PROJECTED_FIELD_REFS,
    CandidateRef,
    validate_projected_field_refs,
)

__all__ = [
    "MaterializedFieldValue",
    "MaterializedFragmentLocator",
    "MaterializedFragmentProjection",
    "MaterializedProjectionKind",
    "MaterializedProjectionPort",
    "MaterializedProjectionSession",
    "MaterializedPublicationTrace",
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


class MaterializedProjectionPort(Protocol):
    """Narrow operations executed by the owning current database transaction."""

    def discover_exact_phrase(
        self,
        phrase_digest: str,
    ) -> tuple[CandidateRef, ...]: ...

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

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "MaterializedProjectionSession can only be constructed by a trusted "
            "transaction authority"
        )

    def __reduce__(self) -> NoReturn:
        raise TypeError("MaterializedProjectionSession is not serializable")


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
        not callable(getattr(port, "locate", None))
        or not callable(getattr(port, "project", None))
        or not callable(getattr(port, "discover_exact_phrase", None))
        or not callable(getattr(port, "observe_publication", None))
    ):
        raise TypeError("materialized projection port is incomplete")
    session = object.__new__(MaterializedProjectionSession)
    object.__setattr__(session, "_authority_scope", authority_scope)
    object.__setattr__(session, "_port", port)
    return session


def _is_materialized_source_active(
    session: MaterializedProjectionSession,
    source_ref: str,
) -> bool:
    """Read File-source lifecycle on the current UserActor transaction."""

    _require_active_materialized_projection_session(session)
    if type(source_ref) is not str or not source_ref:
        raise ValueError("materialized source reference must be nonblank")
    lifecycle_reader = getattr(session._port, "source_is_active", None)
    if not callable(lifecycle_reader):
        raise TypeError("materialized source lifecycle authority is unavailable")
    observed = lifecycle_reader(source_ref)
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
    session: MaterializedProjectionSession,
    phrase_digest: str,
) -> tuple[CandidateRef, ...]:
    """Discover content-free lineage on the retained current-UserActor transaction."""

    _require_active_materialized_projection_session(session)
    if type(phrase_digest) is not str or not phrase_digest:
        raise ValueError("exact phrase digest must be nonblank")
    candidates = session._port.discover_exact_phrase(phrase_digest)
    if type(candidates) is not tuple or any(
        type(candidate) is not CandidateRef for candidate in candidates
    ):
        raise TypeError(
            "materialized exact discovery must return exact CandidateRef values"
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
