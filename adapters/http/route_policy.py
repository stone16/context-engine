"""Server-owned resolve route policy independent of protected objects."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from adapters.http.authentication import VerifiedAuthenticationContext


class ResolveRouteDecision(StrEnum):
    ALLOW = "allow"
    FORBID = "forbid"
    RATE_LIMIT = "rate_limit"


class ResolveRoutePolicy(Protocol):
    def decide(
        self,
        authentication: VerifiedAuthenticationContext,
    ) -> ResolveRouteDecision: ...


class AllowAuthenticatedResolveRoutePolicy:
    """Default server policy after successful transport authentication."""

    def decide(
        self,
        authentication: VerifiedAuthenticationContext,
    ) -> ResolveRouteDecision:
        if type(authentication) is not VerifiedAuthenticationContext:
            raise TypeError("resolve route policy requires verified authentication")
        return ResolveRouteDecision.ALLOW


__all__ = [
    "AllowAuthenticatedResolveRoutePolicy",
    "ResolveRouteDecision",
    "ResolveRoutePolicy",
]
