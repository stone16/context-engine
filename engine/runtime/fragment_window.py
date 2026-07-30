"""Authorized same-Article/current-Revision Fragment expansion contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn, Protocol

from engine.runtime.evidence import AuthorizedProjection, CandidateRef
from engine.runtime.materialized import (
    MaterializedFragmentWindowRead,
)

__all__ = [
    "FragmentWindowNotAvailable",
    "FragmentWindowReader",
    "FragmentWindowRead",
    "FragmentWindowRequest",
    "FragmentWindowResult",
    "FragmentWindowSession",
]


class FragmentWindowNotAvailable(RuntimeError):
    """Window lineage is absent, stale, or not current."""


@dataclass(frozen=True, slots=True)
class FragmentWindowRequest:
    """Bounded expansion rooted in an already-authorized projection."""

    anchor: AuthorizedProjection = field(repr=False)
    before: int
    after: int
    expansion_candidates: tuple[CandidateRef, ...] = field(
        default=(),
        repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.anchor) is not AuthorizedProjection:
            raise TypeError("fragment window anchor requires AuthorizedProjection")
        if (
            type(self.before) is not int
            or type(self.after) is not int
            or not 0 <= self.before <= 32
            or not 0 <= self.after <= 32
        ):
            raise ValueError(
                "fragment window bounds must be exact integers from 0 to 32"
            )
        if type(self.expansion_candidates) is not tuple or any(
            type(candidate) is not CandidateRef
            for candidate in self.expansion_candidates
        ):
            raise TypeError("expansion candidates require exact CandidateRef values")
        if len(self.expansion_candidates) > 64:
            raise ValueError("fragment expansion candidates must be bounded")


@dataclass(frozen=True, slots=True)
class FragmentWindowResult:
    """Inherited projections plus cross-Article refs awaiting reauthorization."""

    projections: tuple[AuthorizedProjection, ...] = field(repr=False)
    reauthorization_refs: tuple[CandidateRef, ...] = field(repr=False)


class FragmentWindowSession:
    """Request-bound window read with no arbitrary content/database capability."""

    __slots__ = ("_active", "_read")
    _active: bool
    _read: MaterializedFragmentWindowRead

    def __init__(self) -> None:
        raise TypeError("FragmentWindowSession can only be constructed by Kernel")

    def __reduce__(self) -> NoReturn:
        raise TypeError("FragmentWindowSession is not serializable")


def _construct_fragment_window_session(
    read: MaterializedFragmentWindowRead,
) -> FragmentWindowSession:
    if type(read) is not MaterializedFragmentWindowRead:
        raise TypeError("FragmentWindowSession requires an exact materialized read")
    read.__post_init__()
    session = object.__new__(FragmentWindowSession)
    session._active = True
    session._read = read
    return session


def _read_fragment_window_session(
    session: FragmentWindowSession,
) -> MaterializedFragmentWindowRead:
    if type(session) is not FragmentWindowSession:
        raise TypeError("fragment window session has the wrong nominal type")
    if not session._active:
        raise ValueError("fragment window session is inactive")
    return session._read


def _close_fragment_window_session(session: FragmentWindowSession) -> None:
    if type(session) is not FragmentWindowSession:
        raise TypeError("fragment window session has the wrong nominal type")
    session._active = False
    session._read = MaterializedFragmentWindowRead((), ())


def _fragment_window_read_snapshot(
    read: MaterializedFragmentWindowRead,
) -> tuple[object, ...]:
    """Copy one read into immutable primitive values for hostile-port comparison."""

    if type(read) is not MaterializedFragmentWindowRead:
        raise TypeError("fragment window snapshot requires an exact read")
    read.__post_init__()
    return (
        tuple(
            (
                item.locator.organization_id.bytes,
                item.locator.source_ref,
                item.locator.resource_ref,
                item.locator.revision_ref,
                item.locator.fragment_ref,
                item.locator.source_acl_projection_ref,
                item.locator.source_acl_as_of.isoformat(timespec="microseconds"),
                item.projection.kind.value,
                tuple(
                    (
                        field_value.field_ref,
                        field_value.field_value,
                        field_value.ordinal,
                    )
                    for field_value in item.projection.fields
                ),
                tuple(sorted(item.projection.projection_ceiling)),
            )
            for item in read.items
        ),
        tuple(
            (
                candidate.organization_id.bytes,
                candidate.source_ref,
                candidate.resource_ref,
                candidate.revision_ref,
                candidate.fragment_ref,
            )
            for candidate in read.reauthorization_refs
        ),
    )


class FragmentWindowReader(Protocol):
    """Existing Runtime content-I/O access port for authorized expansion."""

    def read_window(
        self,
        request: FragmentWindowRequest,
        window_session: FragmentWindowSession,
    ) -> MaterializedFragmentWindowRead: ...


FragmentWindowRead = MaterializedFragmentWindowRead
