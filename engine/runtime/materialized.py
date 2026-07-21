"""Lifetime-bound same-transaction materialized Fragment projection seam."""

from dataclasses import dataclass, field
from typing import NoReturn, Protocol
from uuid import UUID

from engine.runtime.evidence import CandidateRef

__all__ = [
    "MaterializedFragmentLocator",
    "MaterializedProjectionPort",
    "MaterializedProjectionSession",
]


def _require_nonblank_ref(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"{field_name} must be a nonblank exact string")
    return value


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


class MaterializedProjectionPort(Protocol):
    """Narrow operations executed by the owning current database transaction."""

    def locate(
        self,
        candidate_ref: CandidateRef,
    ) -> MaterializedFragmentLocator | None: ...

    def project_body(
        self,
        locator: MaterializedFragmentLocator,
    ) -> str | None: ...


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
    if not callable(getattr(port, "locate", None)) or not callable(
        getattr(port, "project_body", None)
    ):
        raise TypeError("materialized projection port is incomplete")
    session = object.__new__(MaterializedProjectionSession)
    object.__setattr__(session, "_authority_scope", authority_scope)
    object.__setattr__(session, "_port", port)
    return session


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


def _project_materialized_fragment_body(
    session: MaterializedProjectionSession,
    locator: MaterializedFragmentLocator,
) -> str | None:
    _require_active_materialized_projection_session(session)
    if type(locator) is not MaterializedFragmentLocator:
        raise TypeError("materialized body projection requires exact locator")
    body = session._port.project_body(locator)
    if body is None:
        return None
    if type(body) is not str or not body or body.isspace():
        raise ValueError("materialized body projection must be a nonblank string")
    return body
