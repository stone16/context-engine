"""Content-free discovery plus prohibited legacy content seams."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from engine.runtime.budget import PackageBudgetMeter
from engine.runtime.candidate_ranking import CandidateQuery
from engine.runtime.contracts import Acquire
from engine.runtime.fragment_window import FragmentWindowReader
from engine.runtime.materialized import (
    CandidateDiscoveryRequest,
    CandidateDiscoverySession,
)
from engine.runtime.scope import CandidateDiscoveryScope

__all__ = [
    "CandidateIndex",
    "CandidateIndexUnavailable",
    "ContextProvider",
    "RuntimeContentIo",
    "FragmentWindowReader",
    "SourceContentReader",
    "exact_phrase_digest",
    "prohibited_empty_path_content_io",
]

_EXACT_PHRASE_DIGEST_DOMAIN = b"context-engine.exact-phrase.v1\x00"


def exact_phrase_digest(value: str) -> str:
    """Digest exact UTF-8 query text for the content-free candidate index."""

    if type(value) is not str or not value or value.isspace():
        raise ValueError("exact phrase must be nonblank")
    return sha256(_EXACT_PHRASE_DIGEST_DOMAIN + value.encode("utf-8")).hexdigest()


class CandidateIndex(Protocol):
    """Content-free candidate discovery seam; never an authorization source."""

    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
    ) -> CandidateDiscoveryRequest: ...

    def discover(
        self,
        request: Acquire,
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery: ...


class CandidateIndexUnavailable(RuntimeError):
    """Content-free transient failure of one configured candidate index."""


class ContextProvider(Protocol):
    """Future provider projection seam."""

    def authorize_and_project(self) -> tuple[()]: ...


class SourceContentReader(Protocol):
    """Future source-content read seam."""

    def read_content(self) -> tuple[()]: ...


@dataclass(frozen=True, slots=True)
class RuntimeContentIo:
    """Explicit replaceable content dependencies held behind Runtime."""

    index: CandidateIndex
    provider: ContextProvider
    source_content: SourceContentReader
    fragment_windows: FragmentWindowReader | None = None


class _ProhibitedCandidateIndex:
    def prepare_budgeted_discovery(
        self,
        request: Acquire,
        *,
        effective_scope: CandidateDiscoveryScope,
        budget: PackageBudgetMeter,
        active_embedding_profile_digest: str,
    ) -> CandidateDiscoveryRequest:
        del request, effective_scope, budget, active_embedding_profile_digest
        raise RuntimeError("candidate index is prohibited on the empty Package path")

    def discover(
        self,
        request: Acquire,
        discovery_session: CandidateDiscoverySession,
        *,
        effective_scope: CandidateDiscoveryScope,
    ) -> CandidateQuery:
        del request, discovery_session, effective_scope
        raise RuntimeError("candidate index is prohibited on the empty Package path")


class _ProhibitedContextProvider:
    def authorize_and_project(self) -> tuple[()]:
        raise RuntimeError("provider I/O is prohibited on the empty Package path")


class _ProhibitedSourceContentReader:
    def read_content(self) -> tuple[()]:
        raise RuntimeError("source content is prohibited on the empty Package path")


def prohibited_empty_path_content_io() -> RuntimeContentIo:
    """Build fail-fast legacy seams; authorized content uses the sealed Kernel."""

    return RuntimeContentIo(
        index=_ProhibitedCandidateIndex(),
        provider=_ProhibitedContextProvider(),
        source_content=_ProhibitedSourceContentReader(),
    )
