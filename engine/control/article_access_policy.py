"""Trusted ContextControl commands for future-Article visibility settings."""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.article_access_policy import (
    ArticleAccessPolicySetting,
)


def _require_nonblank_ref(field_name: str, value: object) -> str:
    if type(value) is not str or not value or value.isspace():
        raise ValueError(f"{field_name} must be a nonblank exact reference")
    return value


@dataclass(frozen=True, slots=True)
class SetTenantArticlePolicyDefault:
    """Replace the default used only by later first ingestions."""

    expected_version: int
    setting: ArticleAccessPolicySetting | None

    def __post_init__(self) -> None:
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ValueError("tenant default expected version must be positive")
        if self.setting is not None:
            if type(self.setting) is not ArticleAccessPolicySetting:
                raise TypeError("tenant default setting has the wrong nominal type")
            self.setting.__post_init__()


@dataclass(frozen=True, slots=True)
class SetSourceArticlePolicyDefault:
    """Replace one Source default used only by later first ingestions."""

    source_ref: str = field(repr=False)
    expected_version: int
    setting: ArticleAccessPolicySetting | None

    def __post_init__(self) -> None:
        _require_nonblank_ref("source default source_ref", self.source_ref)
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ValueError("source default expected version must be positive")
        if self.setting is not None:
            if type(self.setting) is not ArticleAccessPolicySetting:
                raise TypeError("source default setting has the wrong nominal type")
            self.setting.__post_init__()
