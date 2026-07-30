"""Preview-bound contracts for explicit historical Article policy changes."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from typing import Final, NoReturn
from uuid import UUID

from engine.article_access_policy import (
    ArticleAccessPolicySetting,
    ArticlePolicyResolutionRung,
)
from engine.control.contracts import _require_bounded_text, _require_sha256

MAX_BULK_ARTICLE_SELECTION: Final = 1_000
MAX_RESOURCE_REF_LENGTH: Final = 512


def article_policy_setting_document(
    setting: ArticleAccessPolicySetting | None,
) -> object:
    """Return the one canonical content-free policy representation."""

    if setting is None:
        return None
    if type(setting) is not ArticleAccessPolicySetting:
        raise TypeError("bulk Article policy setting has the wrong nominal type")
    setting.__post_init__()
    return {
        "groupRefs": sorted(group_ref.value for group_ref in setting.group_refs),
        "kind": setting.kind.value,
    }


def _resource_ref(value: object) -> str:
    resource_ref = _require_bounded_text(
        "bulk Article resource_ref", value, MAX_RESOURCE_REF_LENGTH
    )
    if any(character.isspace() for character in resource_ref):
        raise ValueError("bulk Article resource_ref must not contain whitespace")
    return resource_ref


@dataclass(frozen=True, slots=True)
class BulkArticlePolicyChange:
    """Canonical nonempty Article selection and one exact target setting."""

    resource_refs: tuple[str, ...] = field(repr=False)
    target_policy: ArticleAccessPolicySetting

    def __post_init__(self) -> None:
        if type(self.resource_refs) is not tuple:
            raise TypeError("bulk Article selection must be a tuple")
        if not 1 <= len(self.resource_refs) <= MAX_BULK_ARTICLE_SELECTION:
            raise ValueError("bulk Article selection is empty or too large")
        refs = tuple(_resource_ref(value) for value in self.resource_refs)
        if len(refs) != len(set(refs)):
            raise ValueError("bulk Article selection must not contain duplicates")
        article_policy_setting_document(self.target_policy)
        object.__setattr__(self, "resource_refs", tuple(sorted(refs)))


@dataclass(frozen=True, slots=True)
class BulkArticlePolicyPreviewItem:
    """Content-free before/after policy facts for one administrable Article."""

    resource_ref: str = field(repr=False)
    policy_version: int
    current_policy: ArticleAccessPolicySetting | None
    resolution_rung: ArticlePolicyResolutionRung
    target_policy: ArticleAccessPolicySetting

    def __post_init__(self) -> None:
        _resource_ref(self.resource_ref)
        if type(self.policy_version) is not int or not 1 <= self.policy_version < 2**63:
            raise ValueError("bulk Article policy version must be a positive bigint")
        article_policy_setting_document(self.current_policy)
        article_policy_setting_document(self.target_policy)
        if type(self.resolution_rung) is not ArticlePolicyResolutionRung:
            raise TypeError("bulk Article resolution rung must be closed")
        if (self.current_policy is None) is not (
            self.resolution_rung is ArticlePolicyResolutionRung.ISOLATION
        ):
            raise ValueError("only isolation can carry no current Article policy")


@dataclass(frozen=True, slots=True)
class BulkArticlePolicyPreview:
    """Exact preview snapshot whose digest is the confirmation capability."""

    organization_id: UUID = field(repr=False)
    items: tuple[BulkArticlePolicyPreviewItem, ...]
    digest: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("bulk Article preview organization_id must be UUID")
        if type(self.items) is not tuple or not self.items:
            raise ValueError("bulk Article preview must contain its exact selection")
        if any(type(item) is not BulkArticlePolicyPreviewItem for item in self.items):
            raise TypeError("bulk Article preview items have the wrong nominal type")
        for item in self.items:
            item.__post_init__()
        refs = tuple(item.resource_ref for item in self.items)
        if refs != tuple(sorted(refs)) or len(refs) != len(set(refs)):
            raise ValueError("bulk Article preview items must be unique and canonical")
        _require_sha256("bulk Article preview digest", self.digest)
        if not hashlib.sha256(self._payload()).hexdigest() == self.digest:
            raise ValueError("bulk Article preview digest does not match its facts")

    @classmethod
    def create(
        cls,
        *,
        organization_id: UUID,
        command: BulkArticlePolicyChange,
        items: tuple[BulkArticlePolicyPreviewItem, ...],
    ) -> BulkArticlePolicyPreview:
        if type(command) is not BulkArticlePolicyChange:
            raise TypeError("bulk Article preview requires its exact command")
        command.__post_init__()
        if type(items) is not tuple:
            raise TypeError("bulk Article preview items must be a tuple")
        canonical_items = tuple(sorted(items, key=lambda item: item.resource_ref))
        if (
            tuple(item.resource_ref for item in canonical_items)
            != command.resource_refs
        ):
            raise ValueError(
                "bulk Article preview selection does not match its command"
            )
        if any(item.target_policy != command.target_policy for item in canonical_items):
            raise ValueError("bulk Article preview target does not match its command")
        unsealed = object.__new__(cls)
        object.__setattr__(unsealed, "organization_id", organization_id)
        object.__setattr__(unsealed, "items", canonical_items)
        object.__setattr__(unsealed, "digest", "0" * 64)
        digest = hashlib.sha256(unsealed._payload()).hexdigest()
        return cls(
            organization_id=organization_id,
            items=canonical_items,
            digest=digest,
        )

    def _payload(self) -> bytes:
        document = {
            "items": [
                {
                    "currentPolicy": article_policy_setting_document(
                        item.current_policy
                    ),
                    "policyVersion": item.policy_version,
                    "resolutionRung": item.resolution_rung.value,
                    "resourceRef": item.resource_ref,
                    "targetPolicy": article_policy_setting_document(
                        item.target_policy
                    ),
                }
                for item in self.items
            ],
            "organizationId": str(self.organization_id),
            "profile": "context-engine.bulk-article-policy-preview.v1",
        }
        return json.dumps(
            document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")


@dataclass(frozen=True, slots=True)
class BulkArticlePolicyConfirmation:
    """Explicit confirmation of one immutable preview and no other selection."""

    preview_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_sha256("bulk Article confirmation digest", self.preview_digest)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class BulkArticlePolicyCommit:
    """Construction-sealed one-shot commit capability for one confirmation."""

    organization_id: UUID = field(repr=False)
    preview: BulkArticlePolicyPreview
    operator_ref: str = field(repr=False)
    authority_ref: str = field(repr=False)
    request_id: str = field(repr=False)
    _seal: bytes = field(repr=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("bulk Article commits are issued by ContextControl")

    def __reduce__(self) -> NoReturn:
        raise TypeError("bulk Article commits are not serializable")


_BULK_ARTICLE_COMMIT_KEY = secrets.token_bytes(32)


def _bulk_article_commit_material(
    *,
    organization_id: UUID,
    preview: BulkArticlePolicyPreview,
    operator_ref: str,
    authority_ref: str,
    request_id: str,
) -> bytes:
    if type(organization_id) is not UUID:
        raise TypeError("bulk Article commit organization_id must be UUID")
    if type(preview) is not BulkArticlePolicyPreview:
        raise TypeError("bulk Article commit requires its exact preview")
    preview.__post_init__()
    if preview.organization_id != organization_id:
        raise ValueError("bulk Article commit Organization does not match preview")
    for name, value in (
        ("operator_ref", operator_ref),
        ("authority_ref", authority_ref),
        ("request_id", request_id),
    ):
        _require_bounded_text(f"bulk Article commit {name}", value, 256)
    document = {
        "authorityRef": authority_ref,
        "operatorRef": operator_ref,
        "organizationId": str(organization_id),
        "previewDigest": preview.digest,
        "profile": "context-engine.bulk-article-policy-commit.v1",
        "requestId": request_id,
    }
    return json.dumps(
        document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _issue_bulk_article_policy_commit(
    *,
    organization_id: UUID,
    preview: BulkArticlePolicyPreview,
    operator_ref: str,
    authority_ref: str,
    request_id: str,
) -> BulkArticlePolicyCommit:
    material = _bulk_article_commit_material(
        organization_id=organization_id,
        preview=preview,
        operator_ref=operator_ref,
        authority_ref=authority_ref,
        request_id=request_id,
    )
    commit = object.__new__(BulkArticlePolicyCommit)
    for name, value in (
        ("organization_id", organization_id),
        ("preview", preview),
        ("operator_ref", operator_ref),
        ("authority_ref", authority_ref),
        ("request_id", request_id),
        ("_seal", hmac.digest(_BULK_ARTICLE_COMMIT_KEY, material, "sha256")),
    ):
        object.__setattr__(commit, name, value)
    return commit


def _validate_bulk_article_policy_commit(
    commit: BulkArticlePolicyCommit,
) -> None:
    if type(commit) is not BulkArticlePolicyCommit:
        raise TypeError("bulk Article change requires a sealed commit")
    try:
        material = _bulk_article_commit_material(
            organization_id=commit.organization_id,
            preview=commit.preview,
            operator_ref=commit.operator_ref,
            authority_ref=commit.authority_ref,
            request_id=commit.request_id,
        )
        seal = commit._seal
    except (AttributeError, TypeError, ValueError):
        raise TypeError("bulk Article change requires a sealed commit") from None
    if type(seal) is not bytes or not hmac.compare_digest(
        seal, hmac.digest(_BULK_ARTICLE_COMMIT_KEY, material, "sha256")
    ):
        raise TypeError("bulk Article change requires a sealed commit")


@dataclass(frozen=True, slots=True)
class BulkArticlePolicyResult:
    """Content-free receipt for one committed bulk policy operation."""

    organization_id: UUID = field(repr=False)
    policy_epoch: int
    changed_articles: int
    audit_ref: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("bulk Article result organization_id must be UUID")
        if type(self.policy_epoch) is not int or not 1 <= self.policy_epoch < 2**63:
            raise ValueError("bulk Article result Policy Epoch is invalid")
        if (
            type(self.changed_articles) is not int
            or not 1 <= self.changed_articles <= MAX_BULK_ARTICLE_SELECTION
        ):
            raise ValueError("bulk Article result changed count is invalid")
        if type(self.audit_ref) is not UUID:
            raise TypeError("bulk Article result audit_ref must be UUID")
