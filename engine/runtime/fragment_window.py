"""Authorized same-Article/current-Revision Fragment expansion contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from engine.runtime.evidence import AuthorizedProjection, CandidateRef
from engine.runtime.materialized import (
    MaterializedFragmentWindowItem,
    MaterializedProjectionSession,
)

__all__ = [
    "FragmentWindowNotAvailable",
    "FragmentWindowReader",
    "FragmentWindowRead",
    "FragmentWindowRequest",
    "FragmentWindowResult",
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


@dataclass(frozen=True, slots=True)
class FragmentWindowResult:
    """Inherited projections plus cross-Article refs awaiting reauthorization."""

    projections: tuple[AuthorizedProjection, ...] = field(repr=False)
    reauthorization_refs: tuple[CandidateRef, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class FragmentWindowRead:
    """Internal current-lineage read awaiting Kernel-owned construction."""

    items: tuple[MaterializedFragmentWindowItem, ...] = field(repr=False)
    reauthorization_refs: tuple[CandidateRef, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or not self.items or any(
            type(item) is not MaterializedFragmentWindowItem for item in self.items
        ):
            raise ValueError("fragment window read requires materialized items")
        if type(self.reauthorization_refs) is not tuple or any(
            type(candidate) is not CandidateRef
            for candidate in self.reauthorization_refs
        ):
            raise TypeError("fragment window read requires exact reauthorization refs")


class FragmentWindowReader(Protocol):
    """Existing Runtime content-I/O access port for authorized expansion."""

    def read_window(
        self,
        request: FragmentWindowRequest,
        projection_session: MaterializedProjectionSession,
    ) -> FragmentWindowRead: ...
