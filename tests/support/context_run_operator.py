"""Test-only exact operator authority for the ContextRun conformance seam."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine

from engine.persistence import (
    ContextRunOperatorAccessRequest,
    ContextRunOperatorAuthenticationRejected,
    ContextRunOperatorAuthority,
    ContextRunOperatorAuthorization,
    PostgreSQLContextRunReader,
    VerifiedContextRunOperatorIdentity,
)


@dataclass(frozen=True, slots=True)
class ExactTestContextRunOperatorAuthenticator:
    """Accept one opaque test credential for one exact Organization."""

    organization_id: UUID = field(repr=False)
    opaque_credential: str = field(repr=False)
    authorized_at: datetime = field(repr=False)

    def authenticate(
        self,
        opaque_credential: str,
    ) -> VerifiedContextRunOperatorIdentity:
        if opaque_credential != self.opaque_credential:
            raise ContextRunOperatorAuthenticationRejected
        return VerifiedContextRunOperatorIdentity(
            organization_id=self.organization_id,
            operator_ref="test:context-run-security-operator",
            authentication_binding_ref="test:context-run-operator-binding",
            authorized_at=self.authorized_at,
        )


@contextmanager
def exact_test_context_run_operator_read(
    *,
    control_engine: Engine,
    operator_engine: Engine,
    organization_id: UUID,
    decision_ref: str,
    request_id: str,
    opaque_credential: str,
    authorized_at: datetime,
) -> Iterator[tuple[PostgreSQLContextRunReader, ContextRunOperatorAuthorization]]:
    """Compose one verified, lifetime-bound reader capability for integration tests."""

    authority = ContextRunOperatorAuthority(
        ExactTestContextRunOperatorAuthenticator(
            organization_id=organization_id,
            opaque_credential=opaque_credential,
            authorized_at=authorized_at,
        )
    )
    reader = PostgreSQLContextRunReader(
        control_engine,
        operator_engine,
        operator_authority=authority,
    )
    request = ContextRunOperatorAccessRequest(
        organization_id=organization_id,
        decision_ref=decision_ref,
        request_id=request_id,
        opaque_credential=opaque_credential,
    )
    with authority.authorize(request) as authorization:
        yield reader, authorization
