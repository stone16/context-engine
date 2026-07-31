"""PostgreSQL Control authority for accepted Feishu ACL observations."""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.control.feishu_acl import (
    AppliedFeishuAclObservation,
    ApplyFeishuAclObservation,
)
from engine.persistence.role_guard import assert_control_role


class FeishuAclObservationNotAvailable(Exception):
    """The exact accepted observation could not be applied."""


class FeishuAclControlUnavailable(RuntimeError):
    """Trusted Feishu ACL persistence could not complete."""


class PostgreSQLFeishuAclControl:
    """Apply one accepted Feishu observation and epoch transition atomically."""

    __slots__ = ("_engine",)

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def apply(
        self,
        command: ApplyFeishuAclObservation,
    ) -> AppliedFeishuAclObservation:
        if type(command) is not ApplyFeishuAclObservation:
            raise TypeError("Feishu ACL apply requires its exact command")
        command.__post_init__()
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                organization = str(command.organization_id)
                bound = connection.execute(
                    text(
                        "SELECT set_config('app.organization_id', :org, true), "
                        "current_setting('app.organization_id', true)"
                    ),
                    {"org": organization},
                ).one()
                if tuple(bound) != (organization, organization):
                    raise FeishuAclControlUnavailable(
                        "Feishu ACL Organization context could not be bound"
                    )
                row = connection.execute(
                    text(
                        """
                        SELECT *
                        FROM public.context_control_apply_feishu_acl_observation(
                            :organization_id, :source_version_id, :worker_job_id,
                            :page_ref, :document_ref, :delete_observation
                        )
                        """
                    ),
                    {
                        "organization_id": command.organization_id,
                        "source_version_id": command.source_version_id,
                        "worker_job_id": command.worker_job_id,
                        "page_ref": command.page_ref,
                        "document_ref": command.document_ref,
                        "delete_observation": command.delete_observation,
                    },
                ).one_or_none()
                if row is None:
                    raise FeishuAclObservationNotAvailable
                return AppliedFeishuAclObservation(
                    organization_id=command.organization_id,
                    document_ref=command.document_ref,
                    observation_version=row.observation_version,
                    policy_epoch=row.policy_epoch,
                    published=row.published,
                    tombstoned=row.tombstoned,
                )
        except (FeishuAclObservationNotAvailable, FeishuAclControlUnavailable):
            raise
        except (AssertionError, SQLAlchemyError):
            raise FeishuAclControlUnavailable(
                "Feishu ACL database authority is unavailable"
            ) from None


__all__ = [
    "FeishuAclControlUnavailable",
    "FeishuAclObservationNotAvailable",
    "PostgreSQLFeishuAclControl",
]
