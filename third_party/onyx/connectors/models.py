"""Patched connector wire models from the pinned Onyx connector framework.

This registered MIT region is deliberately not ContextEngine canonical state.
The CE adapter translates every value into the Supply execution contracts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConnectorCheckpoint:
    """One opaque connector-owned progress value."""

    payload: bytes

    def __post_init__(self) -> None:
        if type(self.payload) is not bytes or not self.payload:
            raise ValueError("connector checkpoint must be nonempty bytes")


@dataclass(frozen=True, slots=True)
class Document:
    """Small runner-side document shape translated at the CE boundary."""

    document_id: str
    content: bytes
    content_type: str
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if type(self.document_id) is not str or not self.document_id:
            raise ValueError("connector document requires an identity")
        if type(self.content) is not bytes or not self.content:
            raise ValueError("connector document requires content bytes")
        if type(self.content_type) is not str or not self.content_type:
            raise ValueError("connector document requires a content type")
        if type(self.metadata) is not tuple:
            raise TypeError("connector document metadata must be a tuple")


@dataclass(frozen=True, slots=True)
class DeletedDocument:
    """One source identity observed absent from the current snapshot."""

    document_id: str

    def __post_init__(self) -> None:
        if type(self.document_id) is not str or not self.document_id:
            raise ValueError("connector delete requires an identity")


@dataclass(frozen=True, slots=True)
class ConnectorFailure:
    """Content-free connector failure returned through the batch runner."""

    category: str

    def __post_init__(self) -> None:
        if self.category not in {"retryable", "terminal"}:
            raise ValueError("connector failure category must be closed")


ConnectorItem = Document | DeletedDocument | ConnectorFailure


__all__ = [
    "ConnectorCheckpoint",
    "ConnectorFailure",
    "ConnectorItem",
    "DeletedDocument",
    "Document",
]
