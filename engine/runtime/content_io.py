"""Replaceable content seams that remain untouched by the empty Runtime path."""

from dataclasses import dataclass
from typing import Protocol

from engine.runtime.contracts import Acquire


class CandidateIndex(Protocol):
    """Future content-free candidate discovery seam."""

    def discover(self, request: Acquire) -> tuple[()]: ...


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
    def discover(self, request: Acquire) -> tuple[()]:
        raise RuntimeError("candidate index is prohibited on the empty Package path")


class _ProhibitedContextProvider:
    def authorize_and_project(self) -> tuple[()]:
        raise RuntimeError("provider I/O is prohibited on the empty Package path")


class _ProhibitedSourceContentReader:
    def read_content(self) -> tuple[()]:
        raise RuntimeError("source content is prohibited on the empty Package path")


def prohibited_empty_path_content_io() -> RuntimeContentIo:
    """Build fail-fast production seams; none may be called in Issue #10."""

    return RuntimeContentIo(
        index=_ProhibitedCandidateIndex(),
        provider=_ProhibitedContextProvider(),
        source_content=_ProhibitedSourceContentReader(),
    )
