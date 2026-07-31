"""Learning-only projection of captured feedback with exact authorized lineage."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.learning.feedback import (
    FeedbackBindingUnavailable,
    FeedbackEvidence,
    feedback_evidence_from_document,
)
from engine.persistence.role_guard import assert_learning_role


class PostgreSQLFeedbackInbox:
    """Read one captured item through the narrow ContextLearning function."""

    def __init__(self, engine: Engine) -> None:
        if not isinstance(engine, Engine):
            raise TypeError("PostgreSQLFeedbackInbox requires a SQLAlchemy Engine")
        self._engine = engine

    def find_exact(
        self,
        organization_id: UUID,
        feedback_ref: str,
    ) -> FeedbackEvidence:
        if type(organization_id) is not UUID:
            raise TypeError("feedback inbox requires an Organization UUID")
        try:
            with self._engine.begin() as connection:
                assert_learning_role(connection)
                document = connection.execute(
                    text(
                        "SELECT context_learning_read_feedback_evidence("
                        ":organization_id, :feedback_ref)"
                    ),
                    {
                        "organization_id": organization_id,
                        "feedback_ref": feedback_ref,
                    },
                ).scalar_one_or_none()
            if document is None:
                raise FeedbackBindingUnavailable(
                    "feedback exact binding is unavailable"
                )
            return feedback_evidence_from_document(document)
        except FeedbackBindingUnavailable:
            raise
        except (AssertionError, SQLAlchemyError, TypeError, ValueError):
            raise FeedbackBindingUnavailable(
                "feedback exact binding is unavailable"
            ) from None
