from __future__ import annotations

from uuid import UUID

import pytest

from engine.article_access_policy import (
    AclObservationStatus,
    ArticleAccessPolicyKind,
    ArticleAccessPolicySetting,
    ArticlePolicyResolution,
    ArticlePolicyResolutionRung,
    GroupRef,
    SourceAclEvidence,
    apply_source_acl_floor,
    resolve_article_access_policy,
)

ORGANIZATION_ID = UUID("e907b18b-1a9c-4699-a5f5-e63afd98fbd8")
TEAM_A = GroupRef("group:team-a")
TEAM_B = GroupRef("group:team-b")
PRIVATE = ArticleAccessPolicySetting(ArticleAccessPolicyKind.PRIVATE)
ORGANIZATION = ArticleAccessPolicySetting(ArticleAccessPolicyKind.ORGANIZATION)
GROUP_A = ArticleAccessPolicySetting(
    ArticleAccessPolicyKind.GROUPS,
    frozenset({TEAM_A}),
)
GROUP_A_B = ArticleAccessPolicySetting(
    ArticleAccessPolicyKind.GROUPS,
    frozenset({TEAM_A, TEAM_B}),
)


class GroupDirectory:
    def resolve_organization_id(self, group_ref: GroupRef) -> UUID | None:
        if group_ref in {TEAM_A, TEAM_B}:
            return ORGANIZATION_ID
        return None


def _local_resolution(
    setting: ArticleAccessPolicySetting,
    rung: ArticlePolicyResolutionRung = ArticlePolicyResolutionRung.EXPLICIT_ARTICLE,
) -> ArticlePolicyResolution:
    cascade = {
        ArticlePolicyResolutionRung.EXPLICIT_ARTICLE: (setting, None, None),
        ArticlePolicyResolutionRung.SOURCE_DEFAULT: (None, setting, None),
        ArticlePolicyResolutionRung.TENANT_DEFAULT: (None, None, setting),
    }
    explicit, source_default, tenant_default = cascade[rung]
    return resolve_article_access_policy(
        organization_id=ORGANIZATION_ID,
        explicit=explicit,
        source_default=source_default,
        tenant_default=tenant_default,
        group_directory=GroupDirectory(),
    )


@pytest.mark.parametrize(
    ("local", "source", "expected"),
    (
        (ORGANIZATION, PRIVATE, PRIVATE),
        (ORGANIZATION, GROUP_A, GROUP_A),
        (GROUP_A_B, GROUP_A, GROUP_A),
        (PRIVATE, ORGANIZATION, PRIVATE),
        (GROUP_A, ORGANIZATION, GROUP_A),
        (GROUP_A, GROUP_A_B, GROUP_A),
    ),
)
def test_source_acl_floor_narrows_but_never_widens(
    local: ArticleAccessPolicySetting,
    source: ArticleAccessPolicySetting,
    expected: ArticleAccessPolicySetting,
) -> None:
    result = apply_source_acl_floor(
        organization_id=ORGANIZATION_ID,
        local_resolution=_local_resolution(local),
        source_evidence=SourceAclEvidence(
            status=AclObservationStatus.RESOLVED,
            observed_policy=source,
        ),
        group_directory=GroupDirectory(),
    )

    assert result.policy is not None
    assert result.policy.setting == expected
    assert result.published is True


def test_disjoint_group_intersection_isolates_instead_of_widening() -> None:
    result = apply_source_acl_floor(
        organization_id=ORGANIZATION_ID,
        local_resolution=_local_resolution(GROUP_A),
        source_evidence=SourceAclEvidence(
            status=AclObservationStatus.RESOLVED,
            observed_policy=ArticleAccessPolicySetting(
                ArticleAccessPolicyKind.GROUPS,
                frozenset({TEAM_B}),
            ),
        ),
        group_directory=GroupDirectory(),
    )

    assert result.policy is None
    assert result.published is False


def test_source_acl_floor_retains_the_local_cascade_provenance() -> None:
    result = apply_source_acl_floor(
        organization_id=ORGANIZATION_ID,
        local_resolution=_local_resolution(
            ORGANIZATION,
            ArticlePolicyResolutionRung.SOURCE_DEFAULT,
        ),
        source_evidence=SourceAclEvidence(
            status=AclObservationStatus.RESOLVED,
            observed_policy=GROUP_A,
        ),
        group_directory=GroupDirectory(),
    )

    assert result.policy is not None
    assert result.policy.setting == GROUP_A
    assert result.rung is ArticlePolicyResolutionRung.SOURCE_DEFAULT
