"""Restricted PostgreSQL cleanup for retained citation-locator lineage."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence.role_guard import assert_security_operator_role
from engine.runtime.citation import CitationAuthorityUnavailable


class PostgreSQLCitationOpenRetentionPort:
    """Delete retained digests through the dedicated security-operator login."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def delete_expired_lineage(self, organization_id: UUID) -> int:
        try:
            with self._engine.begin() as connection:
                assert_security_operator_role(connection)
                deleted = connection.execute(
                    text(
                        "SELECT "
                        "context_security_delete_expired_citation_open_lineage("
                        ":organization_id)"
                    ),
                    {"organization_id": organization_id},
                ).scalar_one()
        except (AssertionError, SQLAlchemyError):
            raise CitationAuthorityUnavailable from None
        if type(deleted) is not int or deleted < 0:
            raise CitationAuthorityUnavailable
        return deleted
