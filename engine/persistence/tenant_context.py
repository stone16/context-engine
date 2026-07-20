"""Transaction-local Organization context for the first RLS evidence slice.

The Organization UUID passed here must already have been established by a
trusted caller.  This database boundary deliberately does not authenticate a
request or manufacture the future closed ActorContext contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Connection, Engine, text


class OrganizationContextBindingError(RuntimeError):
    """Raised before content work when PostgreSQL did not retain the local GUC."""


@contextmanager
def organization_transaction(
    engine: Engine,
    organization_id: UUID,
) -> Iterator[Connection]:
    """Open, bind, verify, and own one Organization-scoped transaction.

    No connection is exposed before ``app.organization_id`` is set with
    transaction-local scope.  Normal exit commits; every exception rolls the
    transaction back through SQLAlchemy's engine context manager.
    """

    if not isinstance(organization_id, UUID):
        raise TypeError("organization_id must be a UUID")

    expected_value = str(organization_id)
    with engine.begin() as connection:
        connection.execute(
            text(
                "SELECT set_config("
                "'app.organization_id', :organization_id, true"
                ")"
            ),
            {"organization_id": expected_value},
        )
        observed_value = connection.execute(
            text("SELECT current_setting('app.organization_id', true)")
        ).scalar_one()
        if observed_value != expected_value:
            raise OrganizationContextBindingError(
                "organization context binding failed before database work"
            )
        yield connection
