"""Privacy governance for tracked synthetic data and public subset promotion."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

PUBLIC_SUBSET_GOVERNANCE_VERSION: Final = (
    "context-engine-public-subset-governance-v1"
)
_PLACEHOLDER_PREFIXES: Final = (
    "synthetic-",
    "synthetic/",
    "placeholder-",
    "placeholder/",
)
_CONTENT_FIELDS: Final = frozenset(
    {
        "query",
        "name",
        "caseRef",
        "claimRef",
        "expectedAnswer",
        "claim",
        "topicCluster",
        "path",
        "sourceRef",
        "resourceRef",
        "revisionRef",
        "fragmentRef",
    }
)


class PublicSubsetPromotionRejected(RuntimeError):
    """The caller lacks the configured maintainer privacy authority."""


@dataclass(frozen=True, slots=True)
class PublicSubsetGovernance:
    promotion_authority: str

    def __post_init__(self) -> None:
        if (
            type(self.promotion_authority) is not str
            or not self.promotion_authority
            or self.promotion_authority.isspace()
        ):
            raise ValueError("public subset promotion authority is unavailable")


def load_public_subset_governance(path: Path) -> PublicSubsetGovernance:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise ValueError("public subset governance is unavailable") from None
    if type(document) is not dict or frozenset(document) != frozenset(
        {"promotionAuthority", "schemaVersion"}
    ):
        raise ValueError("public subset governance is malformed")
    if document["schemaVersion"] != PUBLIC_SUBSET_GOVERNANCE_VERSION:
        raise ValueError("public subset governance version is unavailable")
    return PublicSubsetGovernance(
        promotion_authority=cast(str, document["promotionAuthority"])
    )


def authorize_public_subset_promotion(
    principal: str,
    governance: PublicSubsetGovernance,
) -> None:
    """Refuse every identity except the configured privacy authority."""

    if type(principal) is not str or not principal:
        raise PublicSubsetPromotionRejected("public subset promotion refused")
    if type(governance) is not PublicSubsetGovernance:
        raise TypeError("public subset governance is required")
    if principal != governance.promotion_authority:
        raise PublicSubsetPromotionRejected("public subset promotion refused")


def _assert_placeholder_text(field_name: str, value: object, path: Path) -> None:
    if type(value) is not str or not value.startswith(_PLACEHOLDER_PREFIXES):
        raise ValueError(
            f"tracked golden content requires placeholder text: {path.name} "
            f"field {field_name}"
        )


def _scan_value(value: object, path: Path) -> None:
    if type(value) is dict:
        mapping = cast(Mapping[object, object], value)
        for raw_key, child in mapping.items():
            if type(raw_key) is not str:
                raise ValueError(f"tracked golden object is malformed: {path.name}")
            if raw_key in _CONTENT_FIELDS:
                if type(child) is list:
                    for item in cast(list[object], child):
                        _assert_placeholder_text(raw_key, item, path)
                else:
                    _assert_placeholder_text(raw_key, child, path)
            _scan_value(child, path)
        return
    if type(value) is list:
        for child in cast(list[object], value):
            _scan_value(child, path)


def assert_tracked_golden_tree_is_synthetic(root: Path) -> None:
    """Reject real/anonymized content in every tracked golden JSON fixture."""

    if not isinstance(root, Path):
        raise TypeError("tracked golden root must be Path")
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        if path.name == "schema.json":
            continue
        if path.suffix != ".json" or path.name.endswith(".lock.json"):
            raise ValueError(f"tracked golden file is not synthetic JSON: {path.name}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            raise ValueError(f"tracked golden JSON is malformed: {path.name}") from None
        if (
            type(document) is dict
            and "entries" in document
            and document.get("synthetic") is not True
        ):
            raise ValueError(f"tracked golden set must be synthetic: {path.name}")
        _scan_value(document, path)
