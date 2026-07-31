"""Trusted locators for applying accepted Feishu ACL observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


def _require_ref(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 512
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded opaque reference")
    return value


@dataclass(frozen=True, slots=True)
class ApplyFeishuAclObservation:
    """Exact accepted-page locator; no caller-authored ACL facts are accepted."""

    organization_id: UUID = field(repr=False)
    source_version_id: UUID = field(repr=False)
    worker_job_id: UUID = field(repr=False)
    page_ref: str = field(repr=False)
    document_ref: str = field(repr=False)
    delete_observation: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "organization_id",
            "source_version_id",
            "worker_job_id",
        ):
            if type(getattr(self, field_name)) is not UUID:
                raise TypeError(f"Feishu ACL {field_name} must be UUID")
        _require_ref("Feishu accepted page", self.page_ref)
        _require_ref("Feishu ACL document", self.document_ref)
        if type(self.delete_observation) is not bool:
            raise TypeError("Feishu delete observation flag must be bool")


@dataclass(frozen=True, slots=True)
class AppliedFeishuAclObservation:
    organization_id: UUID = field(repr=False)
    document_ref: str = field(repr=False)
    observation_version: int
    policy_epoch: int
    published: bool
    tombstoned: bool

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("applied Feishu ACL Organization must be UUID")
        _require_ref("applied Feishu ACL document", self.document_ref)
        for field_name in ("observation_version", "policy_epoch"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"applied Feishu ACL {field_name} must be positive")
        if type(self.published) is not bool or type(self.tombstoned) is not bool:
            raise TypeError("applied Feishu ACL state must be boolean")
        if self.tombstoned and self.published:
            raise ValueError("a tombstoned Feishu Article cannot remain published")


__all__ = ["AppliedFeishuAclObservation", "ApplyFeishuAclObservation"]
