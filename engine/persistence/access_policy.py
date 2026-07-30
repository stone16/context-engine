"""Trusted control-plane access revocation backed by one PostgreSQL transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast, overload
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from engine.article_access_policy import (
    ArticleAccessPolicyKind,
    ArticleAccessPolicySetting,
    ArticlePolicyResolutionRung,
    GroupRef,
)
from engine.control.bulk_article_policy import (
    BulkArticlePolicyChange,
    BulkArticlePolicyCommit,
    BulkArticlePolicyPreview,
    BulkArticlePolicyPreviewItem,
    BulkArticlePolicyResult,
    _validate_bulk_article_policy_commit,
)
from engine.persistence.role_guard import assert_control_role

MAX_ACCESS_VERSION = 2**63 - 1
MAX_POLICY_EPOCH = 2**63 - 1


class AccessChangeRejected(Exception):
    """The exact currently allowed grant could not be safely revoked."""

    def __init__(self) -> None:
        super().__init__("access change was not accepted")


class AccessPolicyControlUnavailable(RuntimeError):
    """The trusted access-policy database authority could not complete."""


@dataclass(frozen=True, slots=True)
class ResourceAccessRevocation:
    """Exact optimistic-concurrency locator for one allowed Resource grant."""

    organization_id: UUID
    resource_ref: str
    principal_ref: str
    expected_access_version: int

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("access revocation organization_id must be UUID")
        for field_name in ("resource_ref", "principal_ref"):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"access revocation {field_name} must be nonblank")
        if (
            type(self.expected_access_version) is not int
            or not 1 <= self.expected_access_version <= MAX_ACCESS_VERSION
        ):
            raise ValueError(
                "expected access version must fit a positive signed 64-bit integer"
            )


@dataclass(frozen=True, slots=True)
class PolicyEpoch:
    """Current Organization-owned monotonic revocation value after a commit."""

    organization_id: UUID
    value: int

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("Policy Epoch organization_id must be UUID")
        if type(self.value) is not int or not 1 <= self.value <= MAX_POLICY_EPOCH:
            raise ValueError("Policy Epoch must fit a positive signed 64-bit integer")


class PostgreSQLAccessPolicyControl:
    """Revoke one exact grant and advance its Organization epoch atomically."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def preview_bulk_article_policy_change(
        self,
        organization_id: UUID,
        command: BulkArticlePolicyChange,
    ) -> BulkArticlePolicyPreview:
        """Read exactly one administrable selection without mutating durable state."""

        if type(organization_id) is not UUID:
            raise TypeError("bulk Article preview organization_id must be UUID")
        if type(command) is not BulkArticlePolicyChange:
            raise TypeError("bulk Article preview requires its exact command")
        command.__post_init__()
        try:
            with self._engine.begin() as connection:
                self._bind_control_organization(connection, organization_id)
                rows = connection.execute(
                    text(
                        """
                        SELECT resource_ref, policy_version, policy_kind,
                               group_refs, published, resolution_rung
                        FROM public.context_control_preview_bulk_article_policy_change(
                            :organization_id,
                            CAST(:resource_refs AS text[]),
                            :target_policy_kind,
                            CAST(:target_group_refs AS text[])
                        )
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "resource_refs": list(command.resource_refs),
                        "target_policy_kind": command.target_policy.kind.value,
                        "target_group_refs": sorted(
                            ref.value for ref in command.target_policy.group_refs
                        ),
                    },
                ).mappings().all()
                if tuple(row["resource_ref"] for row in rows) != command.resource_refs:
                    raise AccessChangeRejected
                items = tuple(
                    BulkArticlePolicyPreviewItem(
                        resource_ref=row["resource_ref"],
                        policy_version=row["policy_version"],
                        current_policy=(
                            _policy_setting(row["policy_kind"], row["group_refs"])
                            if row["published"]
                            else None
                        ),
                        resolution_rung=ArticlePolicyResolutionRung(
                            row["resolution_rung"]
                            if row["published"]
                            else ArticlePolicyResolutionRung.ISOLATION.value
                        ),
                        target_policy=command.target_policy,
                    )
                    for row in rows
                )
                return BulkArticlePolicyPreview.create(
                    organization_id=organization_id,
                    command=command,
                    items=items,
                )
        except (AccessChangeRejected, AccessPolicyControlUnavailable):
            raise
        except (SQLAlchemyError, TypeError, ValueError) as error:
            raise AccessPolicyControlUnavailable(
                "access-policy control database work failed"
            ) from error

    @overload
    def change_access(self, command: ResourceAccessRevocation) -> PolicyEpoch: ...

    @overload
    def change_access(
        self, command: BulkArticlePolicyCommit
    ) -> BulkArticlePolicyResult: ...

    def change_access(
        self,
        command: ResourceAccessRevocation | BulkArticlePolicyCommit,
    ) -> PolicyEpoch | BulkArticlePolicyResult:
        """Commit the sole trusted single or preview-bound bulk access change."""

        if type(command) is BulkArticlePolicyCommit:
            return self._change_bulk_article_policy(command)
        if type(command) is not ResourceAccessRevocation:
            raise TypeError("access change requires an exact access command")
        try:
            with self._engine.begin() as connection:
                self._bind_control_organization(connection, command.organization_id)

                next_epoch = connection.execute(
                    text(
                        """
                        SELECT public.context_control_revoke_resource_access(
                            :organization_id,
                            :resource_ref,
                            :principal_ref,
                            :expected_access_version
                        )
                        """
                    ),
                    {
                        "organization_id": command.organization_id,
                        "resource_ref": command.resource_ref,
                        "principal_ref": command.principal_ref,
                        "expected_access_version": command.expected_access_version,
                    },
                ).scalar_one()
                result = PolicyEpoch(
                    organization_id=command.organization_id,
                    value=next_epoch,
                )
            return result
        except (AccessChangeRejected, AccessPolicyControlUnavailable):
            raise
        except SQLAlchemyError as error:
            sqlstate = (
                getattr(error.orig, "sqlstate", None)
                if isinstance(error, DBAPIError)
                else None
            )
            if sqlstate == "P0001":
                raise AccessChangeRejected from None
            raise AccessPolicyControlUnavailable(
                "access-policy control database work failed"
            ) from error

    def _change_bulk_article_policy(
        self,
        command: BulkArticlePolicyCommit,
    ) -> BulkArticlePolicyResult:
        _validate_bulk_article_policy_commit(command)
        preview = command.preview
        target = preview.items[0].target_policy
        expected_versions = [item.policy_version for item in preview.items]
        try:
            with self._engine.begin() as connection:
                self._bind_control_organization(connection, command.organization_id)
                row = connection.execute(
                    text(
                        """
                        SELECT next_epoch, audit_ref, changed_articles
                        FROM public.context_control_bulk_change_article_policy(
                            :organization_id,
                            CAST(:resource_refs AS text[]),
                            CAST(:expected_versions AS bigint[]),
                            :target_policy_kind,
                            CAST(:target_group_refs AS text[]),
                            :preview_digest,
                            :operator_ref,
                            :authority_ref,
                            :request_id
                        )
                        """
                    ),
                    {
                        "organization_id": command.organization_id,
                        "resource_refs": [item.resource_ref for item in preview.items],
                        "expected_versions": expected_versions,
                        "target_policy_kind": target.kind.value,
                        "target_group_refs": sorted(
                            group_ref.value for group_ref in target.group_refs
                        ),
                        "preview_digest": preview.digest,
                        "operator_ref": command.operator_ref,
                        "authority_ref": command.authority_ref,
                        "request_id": command.request_id,
                    },
                ).mappings().one()
                return BulkArticlePolicyResult(
                    organization_id=command.organization_id,
                    policy_epoch=row["next_epoch"],
                    changed_articles=row["changed_articles"],
                    audit_ref=row["audit_ref"],
                )
        except (AccessChangeRejected, AccessPolicyControlUnavailable):
            raise
        except SQLAlchemyError as error:
            sqlstate = (
                getattr(error.orig, "sqlstate", None)
                if isinstance(error, DBAPIError)
                else None
            )
            if sqlstate == "P0001":
                raise AccessChangeRejected from None
            raise AccessPolicyControlUnavailable(
                "access-policy control database work failed"
            ) from error

    @staticmethod
    def _bind_control_organization(
        connection: Connection, organization_id: UUID
    ) -> None:
        try:
            assert_control_role(connection)
        except AssertionError as error:
            raise AccessPolicyControlUnavailable(
                "access-policy control is not the dedicated control role"
            ) from error
        expected_organization = str(organization_id)
        connection.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": expected_organization},
        )
        observed_organization = connection.execute(
            text("SELECT current_setting('app.organization_id', true)")
        ).scalar_one()
        if observed_organization != expected_organization:
            raise AccessPolicyControlUnavailable(
                "control Organization context binding failed"
            )


def _policy_setting(kind: object, group_refs: object) -> ArticleAccessPolicySetting:
    if type(kind) is not str or type(group_refs) not in (list, tuple):
        raise AccessPolicyControlUnavailable(
            "access-policy control database work failed"
        )
    refs = cast(list[object] | tuple[object, ...], group_refs)
    if any(type(value) is not str for value in refs):
        raise AccessPolicyControlUnavailable(
            "access-policy control database work failed"
        )
    return ArticleAccessPolicySetting(
        ArticleAccessPolicyKind(kind),
        frozenset(GroupRef(cast(str, value)) for value in refs),
    )
