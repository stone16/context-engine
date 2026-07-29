from __future__ import annotations

from uuid import UUID

import pytest

from engine.runtime.article_access_policy import (
    ArticleAccessPolicy,
    ArticleAccessPolicyKind,
    GroupRef,
)

ORGANIZATION_ID = UUID("d126cfe2-7f48-4a97-bf0c-30f7b60b79e4")
FOREIGN_ORGANIZATION_ID = UUID("74132dda-561c-4405-b7a2-31cb095b67d4")
LOCAL_GROUP = GroupRef("group:local")
FOREIGN_GROUP = GroupRef("group:foreign")
MISSING_GROUP = GroupRef("group:missing")


class GroupDirectory:
    def resolve_organization_id(self, group_ref: GroupRef) -> UUID | None:
        return {
            LOCAL_GROUP: ORGANIZATION_ID,
            FOREIGN_GROUP: FOREIGN_ORGANIZATION_ID,
        }.get(group_ref)


def test_groups_policy_requires_at_least_one_group() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ArticleAccessPolicy.create(
            organization_id=ORGANIZATION_ID,
            kind=ArticleAccessPolicyKind.GROUPS,
            group_refs=frozenset(),
            version=1,
            group_directory=GroupDirectory(),
        )


@pytest.mark.parametrize(
    ("group_ref", "message"),
    (
        (FOREIGN_GROUP, "owning Organization"),
        (MISSING_GROUP, "resolvable"),
    ),
)
def test_groups_policy_rejects_foreign_or_unresolvable_group(
    group_ref: GroupRef,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ArticleAccessPolicy.create(
            organization_id=ORGANIZATION_ID,
            kind=ArticleAccessPolicyKind.GROUPS,
            group_refs=frozenset({group_ref}),
            version=1,
            group_directory=GroupDirectory(),
        )


def test_groups_policy_accepts_only_resolved_groups_in_owning_organization() -> None:
    policy = ArticleAccessPolicy.create(
        organization_id=ORGANIZATION_ID,
        kind=ArticleAccessPolicyKind.GROUPS,
        group_refs=frozenset({LOCAL_GROUP}),
        version=7,
        group_directory=GroupDirectory(),
    )

    assert policy.organization_id == ORGANIZATION_ID
    assert policy.kind is ArticleAccessPolicyKind.GROUPS
    assert policy.group_refs == frozenset({LOCAL_GROUP})
    assert policy.version == 7


@pytest.mark.parametrize(
    "kind",
    (ArticleAccessPolicyKind.PRIVATE, ArticleAccessPolicyKind.ORGANIZATION),
)
def test_non_group_policy_rejects_group_refs(kind: ArticleAccessPolicyKind) -> None:
    with pytest.raises(ValueError, match="must not carry group refs"):
        ArticleAccessPolicy.create(
            organization_id=ORGANIZATION_ID,
            kind=kind,
            group_refs=frozenset({LOCAL_GROUP}),
            version=1,
            group_directory=GroupDirectory(),
        )
