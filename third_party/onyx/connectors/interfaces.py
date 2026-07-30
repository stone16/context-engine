"""Patched checkpoint connector interfaces from the pinned Onyx MIT region."""

from __future__ import annotations

import abc
from collections.abc import Generator

from third_party.onyx.connectors.models import (
    ConnectorCheckpoint,
    ConnectorItem,
)

type CheckpointOutput = Generator[
    ConnectorItem,
    None,
    ConnectorCheckpoint,
]


class CheckpointedConnector(abc.ABC):
    """A source connector whose generator returns exactly one checkpoint."""

    @abc.abstractmethod
    def load_from_checkpoint(
        self,
        checkpoint: ConnectorCheckpoint,
    ) -> CheckpointOutput:
        raise NotImplementedError

    @abc.abstractmethod
    def build_dummy_checkpoint(self) -> ConnectorCheckpoint:
        raise NotImplementedError

    @abc.abstractmethod
    def validate_checkpoint(self, payload: bytes) -> ConnectorCheckpoint:
        raise NotImplementedError


class CheckpointedConnectorWithPermSync(CheckpointedConnector):
    """Checkpoint connector that observes permissions with every item."""

    @abc.abstractmethod
    def load_from_checkpoint_with_perm_sync(
        self,
        checkpoint: ConnectorCheckpoint,
    ) -> CheckpointOutput:
        raise NotImplementedError


__all__ = [
    "CheckpointOutput",
    "CheckpointedConnector",
    "CheckpointedConnectorWithPermSync",
]
