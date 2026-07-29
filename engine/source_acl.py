"""Closed source-native ACL evidence modes shared across engine modules."""

from enum import StrEnum


class SourceAclEvidenceMode(StrEnum):
    """Evidence strength declared by an immutable SourceVersion."""

    LIVE = "live"
    MIRRORED = "mirrored"
    WEAK = "weak"
