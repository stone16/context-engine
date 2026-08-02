"""Stable comparison helpers for independent public resolve calls."""

from typing import Final

REQUEST_SCOPED_RESOLVE_FIELDS: Final = frozenset(
    {
        "asOf",
        "authorizationAsOf",
        "blockId",
        "citationOpenRef",
        "decisionRef",
        "evidenceRef",
        "evidenceRefs",
        "expiresAt",
        "packageDigest",
        "packageId",
        "policySnapshotRef",
        "runRef",
    }
)


def without_request_scoped_resolve_fields(document: object) -> object:
    """Remove values minted independently by otherwise equivalent resolves."""

    if isinstance(document, dict):
        return {
            key: without_request_scoped_resolve_fields(value)
            for key, value in document.items()
            if key not in REQUEST_SCOPED_RESOLVE_FIELDS
        }
    if isinstance(document, list):
        return [without_request_scoped_resolve_fields(value) for value in document]
    return document
