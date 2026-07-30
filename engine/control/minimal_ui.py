"""Exact Control-authority gate for the co-resident M1 operator carrier."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from engine.control.authority import (
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    TrustedControlCall,
    _validate_and_consume_control_call,
)


class MinimalUiControlGate:
    """Consume one authority-constructed call before any UI Control database work."""

    __slots__ = ("_authority", "_clock")

    def __init__(
        self,
        authority: ControlOperatorAuthority,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        if type(authority) is not ControlOperatorAuthority or not callable(clock):
            raise TypeError("minimal UI Control authority is unavailable")
        self._authority = authority
        self._clock = clock

    def consume(
        self,
        call: TrustedControlCall,
        *,
        organization_id: UUID,
        operation: ControlOperation,
    ) -> None:
        if type(organization_id) is not UUID or type(operation) is not ControlOperation:
            raise ControlOperatorAuthenticationRejected
        _validate_and_consume_control_call(
            call,
            authority=self._authority,
            expected_operation=operation,
            checked_at=self._clock(),
        )
        if call.organization_id != organization_id:
            raise ControlOperatorAuthenticationRejected
