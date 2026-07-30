from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient

from engine.control import (
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    MinimalUiControlGate,
    VerifiedControlOperatorIdentity,
)
from ui.public_http import UI_SESSION_COOKIE, issue_ui_session


def authenticate_ui(client: TestClient, credential: str) -> None:
    """Install the same short-lived browser proof emitted by the login route."""

    client.cookies.set(
        UI_SESSION_COOKIE,
        issue_ui_session(credential),
        path="/ui",
    )


class _UiControlAuthenticator:
    def __init__(
        self,
        *,
        organization_id: UUID,
        credential: str,
        operations: frozenset[ControlOperation],
        clock: Callable[[], datetime],
    ) -> None:
        self._organization_id = organization_id
        self._credential = credential
        self._operations = operations
        self._clock = clock

    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if not hmac.compare_digest(opaque_credential, self._credential):
            raise ControlOperatorAuthenticationRejected
        now = self._clock()
        return VerifiedControlOperatorIdentity(
            organization_id=self._organization_id,
            operator_ref="operator:test-ui-control",
            authentication_binding_ref="binding:test-ui-control",
            authority_ref="authority:test-ui-control",
            allowed_operations=self._operations,
            valid_from=now,
            expires_at=now + timedelta(minutes=15),
        )


def ui_control_authority(
    *,
    organization_id: UUID,
    credential: str,
    operations: frozenset[ControlOperation],
    clock: Callable[[], datetime],
) -> tuple[ControlOperatorAuthority, MinimalUiControlGate]:
    authority = ControlOperatorAuthority(
        _UiControlAuthenticator(
            organization_id=organization_id,
            credential=credential,
            operations=operations,
            clock=clock,
        ),
        call_ttl=timedelta(minutes=5),
        clock=clock,
    )
    return authority, MinimalUiControlGate(authority, clock=clock)
