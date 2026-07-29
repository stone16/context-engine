"""Private capability for local compiler acceptance and tests only."""

from __future__ import annotations

from typing import Final

_CONSTRUCTION_TOKEN: Final = object()


class _AcceptanceContext:
    __slots__ = ()

    def __new__(cls, token: object) -> _AcceptanceContext:
        if cls is not _AcceptanceContext or token is not _CONSTRUCTION_TOKEN:
            raise TypeError("compiler acceptance context cannot be constructed")
        return super().__new__(cls)


_CONTEXT: Final = _AcceptanceContext(_CONSTRUCTION_TOKEN)


def acceptance_context() -> _AcceptanceContext:
    """Return the process-local capability for explicit acceptance work."""

    return _CONTEXT


def is_acceptance_context(value: object) -> bool:
    """Return whether the exact private process-local capability was supplied."""

    return value is _CONTEXT
