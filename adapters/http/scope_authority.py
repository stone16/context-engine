"""HTTP ingress port for one request-lived trusted scope authority."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NoReturn, Protocol
from uuid import UUID

from engine.runtime.actor import MAX_MEMBERSHIP_VERSION
from engine.runtime.materialized import (
    MaterializedProjectionSession,
    MaterializedScopeUnavailable,
    _current_materialized_scope_operands,
    _require_active_materialized_projection_session,
)
from engine.runtime.policy_epoch import MAX_POLICY_EPOCH
from engine.runtime.release_lineage import (
    DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1,
    DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1,
    ActiveRuntimeRelease,
)
from engine.runtime.scope import MISSING_TRUSTED_SCOPE, ScopeSet
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)


class ScopeAuthorityUnavailable(Exception):
    """Trusted scope state could not be established for this request."""


@dataclass(frozen=True, slots=True)
class ScopeAuthorityIdentity:
    """Exact trusted facts binding one scope decision to one request."""

    organization_id: UUID = field(repr=False)
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int = field(repr=False)
    policy_epoch: int = field(repr=False)
    principal_ref: str = field(repr=False)
    agent_version_ref: str = field(repr=False)
    purpose: str = field(repr=False)
    request_id: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False)
    checked_at: datetime = field(repr=False)
    materialized_projection_session: MaterializedProjectionSession | None = field(
        default=None,
        repr=False,
    )
    active_runtime_release: ActiveRuntimeRelease | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        for field_name in ("organization_id", "user_id", "membership_id"):
            if type(getattr(self, field_name)) is not UUID:
                raise TypeError(f"Scope authority {field_name} must be UUID")
        if (
            type(self.membership_version) is not int
            or not 1 <= self.membership_version <= MAX_MEMBERSHIP_VERSION
        ):
            raise ValueError(
                "Scope authority Membership version must fit a positive "
                "signed 64-bit integer"
            )
        if self.materialized_projection_session is not None:
            _require_active_materialized_projection_session(
                self.materialized_projection_session
            )
        if self.active_runtime_release is not None and type(
            self.active_runtime_release
        ) is not ActiveRuntimeRelease:
            raise TypeError("Scope authority active release has the wrong nominal type")
        if (
            type(self.policy_epoch) is not int
            or not 1 <= self.policy_epoch <= MAX_POLICY_EPOCH
        ):
            raise ValueError(
                "Scope authority Policy Epoch must fit a positive signed "
                "64-bit integer"
            )
        for field_name in (
            "principal_ref",
            "agent_version_ref",
            "purpose",
            "request_id",
            "authentication_binding_ref",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"Scope authority {field_name} must be non-empty")
        if (
            type(self.checked_at) is not datetime
            or self.checked_at.tzinfo is None
            or self.checked_at.utcoffset() != timedelta(0)
        ):
            raise ValueError(
                "Scope authority checked_at must be a timezone-aware UTC datetime"
            )

    def __reduce__(self) -> NoReturn:
        raise TypeError("ScopeAuthorityIdentity is not serializable")


class ScopeAuthority(Protocol):
    """Resolve seven trusted scope operands inside one authority lifetime."""

    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> AbstractContextManager[TrustedScopeSnapshot]: ...


@contextmanager
def _missing_trusted_scope(
    identity: ScopeAuthorityIdentity,
) -> Iterator[TrustedScopeSnapshot]:
    authority_scope = _open_scope_authority_scope()
    try:
        yield _construct_trusted_scope_snapshot(
            authority_scope=authority_scope,
            organization_id=identity.organization_id,
            user_id=identity.user_id,
            membership_id=identity.membership_id,
            membership_version=identity.membership_version,
            policy_epoch=identity.policy_epoch,
            principal_ref=identity.principal_ref,
            agent_version_ref=identity.agent_version_ref,
            purpose=identity.purpose,
            request_id=identity.request_id,
            authentication_binding_ref=identity.authentication_binding_ref,
            checked_at=identity.checked_at,
            organization_boundary=MISSING_TRUSTED_SCOPE,
            membership_rights=MISSING_TRUSTED_SCOPE,
            principal_grants=MISSING_TRUSTED_SCOPE,
            agent_ceiling=MISSING_TRUSTED_SCOPE,
            source_native_acl=MISSING_TRUSTED_SCOPE,
            resource_acl=MISSING_TRUSTED_SCOPE,
            purpose_policy=MISSING_TRUSTED_SCOPE,
        )
    finally:
        _close_scope_authority_scope(authority_scope)


class MissingTrustedScopeAuthority:
    """Fail-closed default when no trusted scope provider is composed."""

    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> AbstractContextManager[TrustedScopeSnapshot]:
        if type(identity) is not ScopeAuthorityIdentity:
            raise TypeError("Scope authority identity must be ScopeAuthorityIdentity")
        return _missing_trusted_scope(identity)


class DogfoodFileScopeAuthority:
    """Bound one local dogfood Agent/purpose to current File projection facts."""

    __slots__ = (
        "_agent_version_ref",
        "_organization_id",
        "_principal_ref",
        "_purpose",
    )

    def __init__(
        self,
        *,
        organization_id: UUID,
        principal_ref: str,
        agent_version_ref: str,
        purpose: str,
    ) -> None:
        if type(organization_id) is not UUID:
            raise TypeError("dogfood scope Organization must be UUID")
        for field_name, value in (
            ("principal_ref", principal_ref),
            ("agent_version_ref", agent_version_ref),
            ("purpose", purpose),
        ):
            if type(value) is not str or not value or value.isspace():
                raise ValueError(f"dogfood scope {field_name} must be non-empty")
        self._organization_id = organization_id
        self._principal_ref = principal_ref
        self._agent_version_ref = agent_version_ref
        self._purpose = purpose

    @contextmanager
    def current_scope(
        self,
        identity: ScopeAuthorityIdentity,
    ) -> Iterator[TrustedScopeSnapshot]:
        if type(identity) is not ScopeAuthorityIdentity:
            raise TypeError("Scope authority identity must be ScopeAuthorityIdentity")
        if (
            identity.organization_id != self._organization_id
            or identity.principal_ref != self._principal_ref
            or identity.agent_version_ref != self._agent_version_ref
            or identity.purpose != self._purpose
            or identity.materialized_projection_session is None
            or identity.active_runtime_release is None
            or identity.active_runtime_release.organization_id
            != identity.organization_id
            or identity.active_runtime_release.index_profile_ref
            != DOGFOOD_VECTOR_INDEX_PROFILE_REF_V1
            or identity.active_runtime_release.index_profile_digest
            != DOGFOOD_VECTOR_INDEX_PROFILE_DIGEST_V1
            or not identity.active_runtime_release.active_revision_refs
        ):
            raise ScopeAuthorityUnavailable(
                "dogfood scope binding is unavailable"
            )
        try:
            durable = _current_materialized_scope_operands(
                identity.materialized_projection_session,
                identity.active_runtime_release.active_revision_refs,
            )
        except (MaterializedScopeUnavailable, TypeError, ValueError):
            raise ScopeAuthorityUnavailable(
                "dogfood scope authority is unavailable"
            ) from None
        authority_scope = _open_scope_authority_scope()
        try:
            yield _construct_trusted_scope_snapshot(
                authority_scope=authority_scope,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                membership_version=identity.membership_version,
                policy_epoch=identity.policy_epoch,
                principal_ref=identity.principal_ref,
                agent_version_ref=identity.agent_version_ref,
                purpose=identity.purpose,
                request_id=identity.request_id,
                authentication_binding_ref=identity.authentication_binding_ref,
                checked_at=identity.checked_at,
                organization_boundary=ScopeSet(durable.organization_boundary),
                membership_rights=ScopeSet(durable.membership_rights),
                principal_grants=ScopeSet(durable.principal_grants),
                # This local composition binds the one configured Agent and
                # purpose to the Release-selected Organization ceiling. A
                # binding mismatch above is a missing operand, never a grant.
                agent_ceiling=ScopeSet(durable.organization_boundary),
                source_native_acl=ScopeSet(durable.source_native_acl),
                resource_acl=ScopeSet(durable.resource_acl),
                purpose_policy=ScopeSet(durable.organization_boundary),
            )
        finally:
            _close_scope_authority_scope(authority_scope)
