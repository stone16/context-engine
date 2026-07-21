"""HTTP ingress port for one request-lived current-Membership authority."""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType
from typing import Protocol

from engine.persistence.membership_context import (
    MembershipIdentity,
    MembershipNotCurrent,
)
from engine.runtime.actor import CurrentMembershipVerification


class MembershipAuthority(Protocol):
    """Resolve one trusted identity inside a request-lived authority scope."""

    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> AbstractContextManager[CurrentMembershipVerification]: ...


class _RejectingMembershipContext(
    AbstractContextManager[CurrentMembershipVerification]
):
    def __enter__(self) -> CurrentMembershipVerification:
        raise MembershipNotCurrent

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        del exc_type, exc_value, traceback
        return None


class RejectingMembershipAuthority:
    """Production-safe default until a live Membership authority is composed."""

    def current_user_actor(
        self,
        identity: MembershipIdentity,
    ) -> AbstractContextManager[CurrentMembershipVerification]:
        del identity
        return _RejectingMembershipContext()
