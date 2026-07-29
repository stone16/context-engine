from __future__ import annotations

from uuid import UUID

import pytest

from engine.runtime.article_access_policy import (
    AclObservationStatus,
    ArticleAccessPolicyKind,
    ArticleAccessPolicySetting,
    GroupRef,
    SourceAclEvidence,
    apply_source_acl_floor,
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
        local_policy=local,
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
        local_policy=GROUP_A,
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
