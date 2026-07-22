"""Content-free discovery plus prohibited legacy content seams."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from engine.runtime.contracts import Acquire
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import MaterializedProjectionSession

_EXACT_PHRASE_DIGEST_DOMAIN = b"context-engine.exact-phrase.v1\x00"


def exact_phrase_digest(value: str) -> str:
    """Digest exact UTF-8 query text for the content-free candidate index."""

    if type(value) is not str or not value or value.isspace():
        raise ValueError("exact phrase must be nonblank")
    return sha256(_EXACT_PHRASE_DIGEST_DOMAIN + value.encode("utf-8")).hexdigest()


class CandidateIndex(Protocol):
    """Content-free candidate discovery seam; never an authorization source."""

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[CandidateRef, ...]: ...


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


class _ProhibitedCandidateIndex:
    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[()]:
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
