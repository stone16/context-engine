"""Closed Runtime freshness profiles for source-native ACL evidence."""

from __future__ import annotations

from datetime import timedelta

FILE_SOURCE_ACL_FRESHNESS_PROFILE_REF = (
    "file-source-access-current-transaction-v1"
)
FEISHU_DOCS_ACL_FRESHNESS_PROFILE_REF = "feishu-docs-mirrored-five-minute-v1"
FEISHU_DOCS_MIRRORED_ACL_MAX_AGE = timedelta(minutes=5)


__all__ = [
    "FEISHU_DOCS_ACL_FRESHNESS_PROFILE_REF",
    "FEISHU_DOCS_MIRRORED_ACL_MAX_AGE",
    "FILE_SOURCE_ACL_FRESHNESS_PROFILE_REF",
]
