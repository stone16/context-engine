from __future__ import annotations

from uuid import UUID

import pytest

from engine.runtime.article_access_policy import (
    ArticleAccessPolicyKind,
    ArticleAccessPolicySetting,
    ArticlePolicyResolutionRung,
    resolve_article_access_policy,
)

ORGANIZATION_ID = UUID("1f0f38bd-b933-4fa4-b27a-ec9b6704f027")
PRIVATE = ArticleAccessPolicySetting(ArticleAccessPolicyKind.PRIVATE)
ORGANIZATION = ArticleAccessPolicySetting(ArticleAccessPolicyKind.ORGANIZATION)


class EmptyGroupDirectory:
    def resolve_organization_id(self, group_ref: object) -> None:
        del group_ref


@pytest.mark.parametrize(
    (
        "explicit",
        "source_default",
        "tenant_default",
        "expected_kind",
        "expected_rung",
    ),
    (
        (
            PRIVATE,
            ORGANIZATION,
            ORGANIZATION,
            ArticleAccessPolicyKind.PRIVATE,
            ArticlePolicyResolutionRung.EXPLICIT_ARTICLE,
        ),
        (
            None,
            PRIVATE,
            ORGANIZATION,
            ArticleAccessPolicyKind.PRIVATE,
            ArticlePolicyResolutionRung.SOURCE_DEFAULT,
        ),
        (
            None,
            None,
            ORGANIZATION,
            ArticleAccessPolicyKind.ORGANIZATION,
            ArticlePolicyResolutionRung.TENANT_DEFAULT,
        ),
        (
            None,
            None,
            None,
            None,
            ArticlePolicyResolutionRung.ISOLATION,
        ),
    ),
)
def test_article_policy_cascade_uses_exact_order_and_isolates_terminal_fallthrough(
    explicit: ArticleAccessPolicySetting | None,
    source_default: ArticleAccessPolicySetting | None,
    tenant_default: ArticleAccessPolicySetting | None,
    expected_kind: ArticleAccessPolicyKind | None,
    expected_rung: ArticlePolicyResolutionRung,
) -> None:
    resolution = resolve_article_access_policy(
        organization_id=ORGANIZATION_ID,
        explicit=explicit,
        source_default=source_default,
        tenant_default=tenant_default,
        group_directory=EmptyGroupDirectory(),
    )

    assert resolution.rung is expected_rung
    if expected_kind is None:
        assert resolution.policy is None
        assert resolution.published is False
    else:
        assert resolution.policy is not None
        assert resolution.policy.kind is expected_kind
        assert resolution.policy.version == 1
        assert resolution.published is True
