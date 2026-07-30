"""Closed, patched registry derived from the pinned Onyx MIT registry shape."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ConnectorKind(StrEnum):
    FILE_OBSIDIAN = "file-obsidian"


@dataclass(frozen=True, slots=True)
class ConnectorMapping:
    module_path: str
    class_name: str


CONNECTOR_CLASS_MAP = MappingProxyType(
    {
        ConnectorKind.FILE_OBSIDIAN: ConnectorMapping(
            module_path="adapters.connectors.file",
            class_name="FileConnectorAdapter",
        )
    }
)


__all__ = ["CONNECTOR_CLASS_MAP", "ConnectorKind", "ConnectorMapping"]
