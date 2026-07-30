from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from applications.control import (
    _bulk_article_policy_preview_json,
    _bulk_article_policy_result_json,
    _parser,
)
from engine.article_access_policy import (
    ArticleAccessPolicyKind,
    ArticleAccessPolicySetting,
    ArticlePolicyResolutionRung,
    GroupRef,
)
from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    ControlStorePort,
    SourceControlUnavailable,
    VerifiedControlOperatorIdentity,
)
from engine.control.bulk_article_policy import (
    BulkArticlePolicyChange,
    BulkArticlePolicyCommit,
    BulkArticlePolicyConfirmation,
    BulkArticlePolicyPreview,
    BulkArticlePolicyPreviewItem,
    BulkArticlePolicyResult,
)
from engine.control.module import BulkArticlePolicyStorePort

ORGANIZATION_ID = UUID("a683f00a-548c-4f23-9a54-a2517829ca0b")
PRIVATE = ArticleAccessPolicySetting(ArticleAccessPolicyKind.PRIVATE)
ENGINEERING = ArticleAccessPolicySetting(
    ArticleAccessPolicyKind.GROUPS,
    frozenset({GroupRef("group:engineering")}),
)
NOW = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)


class _Authenticator:
    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "bulk-policy-credential":
            raise ControlOperatorAuthenticationRejected
        return VerifiedControlOperatorIdentity(
            organization_id=ORGANIZATION_ID,
            operator_ref="operator:bulk-policy",
            authentication_binding_ref="binding:bulk-policy",
            authority_ref="authority:bulk-policy",
            allowed_operations=frozenset(
                {
                    ControlOperation.PREVIEW_BULK_ARTICLE_POLICY_CHANGE,
                    ControlOperation.COMMIT_BULK_ARTICLE_POLICY_CHANGE,
                }
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(minutes=30),
        )


class _Store:
    def __init__(self, preview: BulkArticlePolicyPreview) -> None:
        self.preview = preview
        self.preview_calls = 0
        self.commit_calls = 0

    def preview_bulk_article_policy_change(
        self, organization_id: UUID, command: BulkArticlePolicyChange
    ) -> BulkArticlePolicyPreview:
        assert organization_id == ORGANIZATION_ID
        assert command.resource_refs == ("article:a", "article:b")
        self.preview_calls += 1
        return self.preview

    def change_access(
        self, command: BulkArticlePolicyCommit
    ) -> BulkArticlePolicyResult:
        assert command.organization_id == ORGANIZATION_ID
        assert command.preview == self.preview
        assert len(command._seal) == 32
        self.commit_calls += 1
        return BulkArticlePolicyResult(
            organization_id=ORGANIZATION_ID,
            policy_epoch=2,
            changed_articles=2,
            audit_ref=UUID("0974ff97-1825-484e-bfeb-472844ce3b64"),
        )

    def __getattr__(self, name: str) -> object:
        return lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(f"unexpected store call: {name}")
        )


def _control(store: _Store) -> tuple[ContextControl, ControlOperatorAuthority]:
    authority = ControlOperatorAuthority(
        _Authenticator(), call_ttl=timedelta(minutes=5), clock=lambda: NOW
    )
    control = ContextControl(
        store=cast(ControlStorePort, store),
        bulk_article_policy_store=cast(BulkArticlePolicyStorePort, store),
        authority=authority,
        clock=lambda: NOW,
    )
    return control, authority


def test_preview_canonicalizes_one_exact_selection_and_binds_confirmation() -> None:
    command = BulkArticlePolicyChange(
        resource_refs=("article:b", "article:a"),
        target_policy=ENGINEERING,
    )
    assert command.resource_refs == ("article:a", "article:b")

    preview = BulkArticlePolicyPreview.create(
        organization_id=ORGANIZATION_ID,
        command=command,
        items=(
            BulkArticlePolicyPreviewItem(
                resource_ref="article:b",
                policy_version=4,
                current_policy=PRIVATE,
                resolution_rung=ArticlePolicyResolutionRung.SOURCE_DEFAULT,
                target_policy=ENGINEERING,
            ),
            BulkArticlePolicyPreviewItem(
                resource_ref="article:a",
                policy_version=2,
                current_policy=None,
                resolution_rung=ArticlePolicyResolutionRung.ISOLATION,
                target_policy=ENGINEERING,
            ),
        ),
    )

    assert tuple(item.resource_ref for item in preview.items) == command.resource_refs
    assert len(preview.digest) == 64
    assert (
        BulkArticlePolicyConfirmation(preview.digest).preview_digest
        == preview.digest
    )


@pytest.mark.parametrize(
    "resource_refs",
    [(), ("article:a", "article:a"), ("article:a", " "), ("article:a\n",)],
)
def test_bulk_selection_refuses_empty_duplicate_or_unsafe_refs(
    resource_refs: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        BulkArticlePolicyChange(resource_refs=resource_refs, target_policy=PRIVATE)


def test_confirmation_accepts_only_a_lowercase_sha256_digest() -> None:
    for value in ("", "0" * 63, "G" * 64, "0" * 65):
        with pytest.raises(ValueError):
            BulkArticlePolicyConfirmation(value)


def test_context_control_preview_then_confirm_consumes_two_exact_operations() -> None:
    command = BulkArticlePolicyChange(
        resource_refs=("article:b", "article:a"), target_policy=ENGINEERING
    )
    preview = BulkArticlePolicyPreview.create(
        organization_id=ORGANIZATION_ID,
        command=command,
        items=tuple(
            BulkArticlePolicyPreviewItem(
                resource_ref=resource_ref,
                policy_version=index,
                current_policy=PRIVATE,
                resolution_rung=ArticlePolicyResolutionRung.SOURCE_DEFAULT,
                target_policy=ENGINEERING,
            )
            for index, resource_ref in enumerate(command.resource_refs, start=1)
        ),
    )
    store = _Store(preview)
    control, authority = _control(store)

    with authority.authorize(
        opaque_credential="bulk-policy-credential",
        operation=ControlOperation.PREVIEW_BULK_ARTICLE_POLICY_CHANGE,
        request_id="preview-bulk-policy",
    ) as preview_call:
        observed = control.preview_bulk_article_policy_change(preview_call, command)
    with authority.authorize(
        opaque_credential="bulk-policy-credential",
        operation=ControlOperation.COMMIT_BULK_ARTICLE_POLICY_CHANGE,
        request_id="commit-bulk-policy",
    ) as commit_call:
        result = control.commit_bulk_article_policy_change(
            commit_call,
            command,
            BulkArticlePolicyConfirmation(observed.digest),
        )

    assert result.policy_epoch == 2
    assert result.changed_articles == 2
    assert store.preview_calls == 2
    assert store.commit_calls == 1


def test_mismatched_confirmation_never_reaches_mutation_store() -> None:
    command = BulkArticlePolicyChange(
        resource_refs=("article:a",), target_policy=PRIVATE
    )
    preview = BulkArticlePolicyPreview.create(
        organization_id=ORGANIZATION_ID,
        command=command,
        items=(
            BulkArticlePolicyPreviewItem(
                resource_ref="article:a",
                policy_version=1,
                current_policy=PRIVATE,
                resolution_rung=ArticlePolicyResolutionRung.TENANT_DEFAULT,
                target_policy=PRIVATE,
            ),
        ),
    )
    store = _Store(preview)
    control, authority = _control(store)

    with authority.authorize(
        opaque_credential="bulk-policy-credential",
        operation=ControlOperation.COMMIT_BULK_ARTICLE_POLICY_CHANGE,
        request_id="mismatched-confirmation",
    ) as call, pytest.raises(SourceControlUnavailable):
        control.commit_bulk_article_policy_change(
            call, command, BulkArticlePolicyConfirmation("f" * 64)
        )

    assert store.commit_calls == 0


def test_bulk_function_is_the_only_historical_article_policy_mutation_path() -> None:
    migration = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260730_0044_bulk_article_policy_change.py"
    )
    source = migration.read_text(encoding="utf-8")

    with pytest.raises(TypeError, match="issued by ContextControl"):
        BulkArticlePolicyCommit()
    assert "resource_access_policy_fix_article_policy" not in source
    assert source.count("UPDATE public.article_access_policy AS policy") == 1
    assert "context_control_bulk_change_article_policy" in source


def test_local_operator_bulk_commands_each_request_one_explicit_operation() -> None:
    arguments = _parser().parse_args(
        [
            "preview-bulk-article-policy",
            "--organization-id",
            str(ORGANIZATION_ID),
            "--resource-ref",
            "article:a",
            "--target-policy",
            "private",
        ]
    )

    assert not hasattr(arguments, "confirm_preview_digest")
    confirmed = _parser().parse_args(
        [
            "commit-bulk-article-policy",
            "--organization-id",
            str(ORGANIZATION_ID),
            "--resource-ref",
            "article:a",
            "--target-policy",
            "private",
            "--confirm-preview-digest",
            "0" * 64,
        ]
    )
    assert confirmed.confirm_preview_digest == "0" * 64


def test_operator_json_contains_policy_facts_but_no_authority_or_content() -> None:
    command = BulkArticlePolicyChange(("article:a",), PRIVATE)
    preview = BulkArticlePolicyPreview.create(
        organization_id=ORGANIZATION_ID,
        command=command,
        items=(
            BulkArticlePolicyPreviewItem(
                resource_ref="article:a",
                policy_version=1,
                current_policy=PRIVATE,
                resolution_rung=ArticlePolicyResolutionRung.TENANT_DEFAULT,
                target_policy=PRIVATE,
            ),
        ),
    )
    preview_json = _bulk_article_policy_preview_json(preview)
    result_json = _bulk_article_policy_result_json(
        BulkArticlePolicyResult(
            organization_id=ORGANIZATION_ID,
            policy_epoch=2,
            changed_articles=1,
            audit_ref=UUID("bf8c2019-85cb-4bc1-b085-d4a3f89e2d56"),
        )
    )

    assert '"resolutionRung":"tenant_default"' in preview_json
    assert '"targetPolicy":{"groupRefs":[],"kind":"private"}' in preview_json
    assert str(ORGANIZATION_ID) not in preview_json + result_json
    assert "operator:" not in preview_json + result_json
    assert "authority:" not in preview_json + result_json
