"""Shared Article visibility domain and source-ACL intersection oracles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from engine.source_acl import SourceAclEvidenceMode

MAX_ARTICLE_POLICY_VERSION: Final = (1 << 63) - 1


class ArticleAccessPolicyKind(StrEnum):
    """Closed Article visibility policy kinds from ADR-0077."""

    PRIVATE = "private"
    ORGANIZATION = "organization"
    GROUPS = "groups"


@dataclass(frozen=True, slots=True, order=True)
class GroupRef:
    """Opaque durable reference to one group resolved by trusted authority."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or not self.value
            or len(self.value) > 256
            or any(character.isspace() for character in self.value)
        ):
            raise ValueError(
                "group ref must be a non-empty bounded opaque string without whitespace"
            )


class GroupDirectory(Protocol):
    """Narrow authority for current group ownership."""

    def resolve_organization_id(self, group_ref: GroupRef) -> UUID | None: ...


@dataclass(frozen=True, slots=True)
class ArticleAccessPolicySetting:
    """Non-authoritative policy proposal before Organization group validation."""

    kind: ArticleAccessPolicyKind
    group_refs: frozenset[GroupRef] = frozenset()

    def __post_init__(self) -> None:
        _require_policy_setting_shape(self)


@dataclass(frozen=True, slots=True, init=False)
class ArticleAccessPolicy:
    """Validated, versioned Article policy fixed for one owning Organization."""

    organization_id: UUID = field(repr=False)
    kind: ArticleAccessPolicyKind
    group_refs: frozenset[GroupRef] = field(repr=False)
    version: int

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("ArticleAccessPolicy requires current group authority")

    @classmethod
    def create(
        cls,
        *,
        organization_id: UUID,
        kind: ArticleAccessPolicyKind,
        group_refs: frozenset[GroupRef],
        version: int,
        group_directory: GroupDirectory,
    ) -> ArticleAccessPolicy:
        setting = ArticleAccessPolicySetting(kind=kind, group_refs=group_refs)
        _validate_policy_setting_for_organization(
            organization_id=organization_id,
            setting=setting,
            group_directory=group_directory,
        )
        if type(version) is not int or not 1 <= version <= MAX_ARTICLE_POLICY_VERSION:
            raise ValueError(
                "Article access policy version must fit a positive signed "
                "64-bit integer"
            )
        policy = object.__new__(cls)
        object.__setattr__(policy, "organization_id", organization_id)
        object.__setattr__(policy, "kind", kind)
        object.__setattr__(policy, "group_refs", group_refs)
        object.__setattr__(policy, "version", version)
        return policy

    @property
    def setting(self) -> ArticleAccessPolicySetting:
        return ArticleAccessPolicySetting(self.kind, self.group_refs)


class ArticlePolicyResolutionRung(StrEnum):
    """Exact ADR-0077 cascade provenance for first-ingest fixation."""

    EXPLICIT_ARTICLE = "explicit_article"
    SOURCE_DEFAULT = "source_default"
    TENANT_DEFAULT = "tenant_default"
    ISOLATION = "isolation"


@dataclass(frozen=True, slots=True)
class ArticlePolicyResolution:
    """Resolved publish decision; isolation carries no publishable policy."""

    policy: ArticleAccessPolicy | None
    rung: ArticlePolicyResolutionRung

    def __post_init__(self) -> None:
        if type(self.rung) is not ArticlePolicyResolutionRung:
            raise TypeError("Article policy resolution rung must be closed")
        if self.rung is ArticlePolicyResolutionRung.ISOLATION:
            if self.policy is not None:
                raise ValueError("isolated Article resolution cannot carry a policy")
        elif type(self.policy) is not ArticleAccessPolicy:
            raise ValueError("published Article resolution requires a policy")

    @property
    def published(self) -> bool:
        return self.policy is not None


class AclObservationStatus(StrEnum):
    """Closed observation result used by the source-native ACL floor."""

    RESOLVED = "resolved"
    MISSING = "missing"
    FAILED = "failed"
    UNRESOLVED_GROUP = "unresolved_group"


@dataclass(frozen=True, slots=True)
class SourceAclEvidence:
    """One locally managed Mirrored ACL observation from an admitted source."""

    status: AclObservationStatus
    observed_policy: ArticleAccessPolicySetting | None = None
    mode: SourceAclEvidenceMode = SourceAclEvidenceMode.MIRRORED

    def __post_init__(self) -> None:
        if type(self.status) is not AclObservationStatus:
            raise TypeError("source ACL observation status must be closed")
        if type(self.mode) is not SourceAclEvidenceMode:
            raise TypeError("source ACL evidence mode must be closed")
        if self.mode is not SourceAclEvidenceMode.MIRRORED:
            raise ValueError("only the Mirrored ACL carrier is active")
        if self.status is AclObservationStatus.RESOLVED:
            if type(self.observed_policy) is not ArticleAccessPolicySetting:
                raise ValueError("resolved source ACL requires an observed policy")
        elif self.observed_policy is not None:
            raise ValueError("failed source ACL observation cannot carry a policy")


def _require_policy_setting_shape(
    setting: ArticleAccessPolicySetting,
) -> ArticleAccessPolicySetting:
    if type(setting) is not ArticleAccessPolicySetting:
        raise TypeError("Article access policy setting has the wrong nominal type")
    if type(setting.kind) is not ArticleAccessPolicyKind:
        raise TypeError("Article access policy kind must be closed")
    if type(setting.group_refs) is not frozenset or any(
        type(group_ref) is not GroupRef for group_ref in setting.group_refs
    ):
        raise TypeError("Article access policy group refs must be GroupRef values")
    if setting.kind is ArticleAccessPolicyKind.GROUPS:
        if not setting.group_refs:
            raise ValueError("GROUPS Article access policy requires at least one group")
    elif setting.group_refs:
        raise ValueError("non-GROUPS Article access policy must not carry group refs")
    return setting


def _validate_policy_setting_for_organization(
    *,
    organization_id: UUID,
    setting: ArticleAccessPolicySetting,
    group_directory: GroupDirectory,
) -> None:
    if type(organization_id) is not UUID:
        raise TypeError("Article access policy organization_id must be UUID")
    _require_policy_setting_shape(setting)
    resolver = getattr(group_directory, "resolve_organization_id", None)
    if not callable(resolver):
        raise TypeError("Article access policy requires a group directory")
    for group_ref in setting.group_refs:
        resolved_organization_id = resolver(group_ref)
        if resolved_organization_id is None:
            raise ValueError("Article access policy group ref must be resolvable")
        if type(resolved_organization_id) is not UUID:
            raise TypeError("group directory returned an invalid Organization")
        if resolved_organization_id != organization_id:
            raise ValueError(
                "Article access policy group must belong to the owning Organization"
            )


def _validated_policy(
    *,
    organization_id: UUID,
    setting: ArticleAccessPolicySetting,
    version: int,
    group_directory: GroupDirectory,
) -> ArticleAccessPolicy:
    # Re-read every field at the use boundary. Frozen dataclasses are not authority.
    _require_policy_setting_shape(setting)
    return ArticleAccessPolicy.create(
        organization_id=organization_id,
        kind=setting.kind,
        group_refs=setting.group_refs,
        version=version,
        group_directory=group_directory,
    )


def resolve_article_access_policy(
    *,
    organization_id: UUID,
    explicit: ArticleAccessPolicySetting | None,
    source_default: ArticleAccessPolicySetting | None,
    tenant_default: ArticleAccessPolicySetting | None,
    group_directory: GroupDirectory,
) -> ArticlePolicyResolution:
    """Resolve the exact first-ingest cascade without a broad fallback."""

    for setting, rung in (
        (explicit, ArticlePolicyResolutionRung.EXPLICIT_ARTICLE),
        (source_default, ArticlePolicyResolutionRung.SOURCE_DEFAULT),
        (tenant_default, ArticlePolicyResolutionRung.TENANT_DEFAULT),
    ):
        if setting is not None:
            return ArticlePolicyResolution(
                policy=_validated_policy(
                    organization_id=organization_id,
                    setting=setting,
                    version=1,
                    group_directory=group_directory,
                ),
                rung=rung,
            )
    return ArticlePolicyResolution(
        policy=None,
        rung=ArticlePolicyResolutionRung.ISOLATION,
    )


def _intersect_policy_settings(
    local: ArticleAccessPolicySetting,
    source: ArticleAccessPolicySetting,
) -> ArticleAccessPolicySetting | None:
    if local.kind is ArticleAccessPolicyKind.PRIVATE:
        return local
    if source.kind is ArticleAccessPolicyKind.PRIVATE:
        return source
    if local.kind is ArticleAccessPolicyKind.ORGANIZATION:
        return source
    if source.kind is ArticleAccessPolicyKind.ORGANIZATION:
        return local
    shared_groups = local.group_refs & source.group_refs
    if not shared_groups:
        return None
    return ArticleAccessPolicySetting(ArticleAccessPolicyKind.GROUPS, shared_groups)


def apply_source_acl_floor(
    *,
    organization_id: UUID,
    local_resolution: ArticlePolicyResolution,
    source_evidence: SourceAclEvidence | None,
    group_directory: GroupDirectory,
) -> ArticlePolicyResolution:
    """Intersect local visibility with source evidence or isolate fail closed."""

    if type(local_resolution) is not ArticlePolicyResolution:
        raise TypeError("source ACL floor requires an Article policy resolution")
    try:
        local_resolution.__post_init__()
    except (TypeError, ValueError):
        return ArticlePolicyResolution(None, ArticlePolicyResolutionRung.ISOLATION)
    if local_resolution.policy is None:
        return local_resolution
    try:
        local = _validated_policy(
            organization_id=organization_id,
            setting=local_resolution.policy.setting,
            version=local_resolution.policy.version,
            group_directory=group_directory,
        )
    except (TypeError, ValueError):
        return ArticlePolicyResolution(None, ArticlePolicyResolutionRung.ISOLATION)
    if source_evidence is None or type(source_evidence) is not SourceAclEvidence:
        return ArticlePolicyResolution(None, ArticlePolicyResolutionRung.ISOLATION)
    try:
        source_evidence.__post_init__()
    except (TypeError, ValueError):
        return ArticlePolicyResolution(None, ArticlePolicyResolutionRung.ISOLATION)
    if source_evidence.status is not AclObservationStatus.RESOLVED:
        return ArticlePolicyResolution(None, ArticlePolicyResolutionRung.ISOLATION)
    assert source_evidence.observed_policy is not None
    try:
        source = _validated_policy(
            organization_id=organization_id,
            setting=source_evidence.observed_policy,
            version=local.version,
            group_directory=group_directory,
        )
    except (TypeError, ValueError):
        return ArticlePolicyResolution(None, ArticlePolicyResolutionRung.ISOLATION)
    intersection = _intersect_policy_settings(local.setting, source.setting)
    if intersection is None:
        return ArticlePolicyResolution(None, ArticlePolicyResolutionRung.ISOLATION)
    try:
        policy = _validated_policy(
            organization_id=organization_id,
            setting=intersection,
            version=local.version,
            group_directory=group_directory,
        )
    except (TypeError, ValueError):
        return ArticlePolicyResolution(None, ArticlePolicyResolutionRung.ISOLATION)
    return ArticlePolicyResolution(policy, local_resolution.rung)
