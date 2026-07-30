"""Patched checkpoint extraction and batching from the pinned Onyx MIT runner."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

from third_party.onyx.connectors.interfaces import (
    CheckpointedConnector,
    CheckpointedConnectorWithPermSync,
    CheckpointOutput,
)
from third_party.onyx.connectors.models import (
    ConnectorCheckpoint,
    ConnectorFailure,
    DeletedDocument,
    Document,
)


class CheckpointOutputWrapper:
    """Expose a generator's final checkpoint only after all items."""

    def __init__(self) -> None:
        self.next_checkpoint: ConnectorCheckpoint | None = None

    def __call__(
        self,
        checkpoint_connector_generator: CheckpointOutput,
    ) -> Generator[Document | DeletedDocument | ConnectorFailure | ConnectorCheckpoint]:
        def _inner_wrapper(
            output: CheckpointOutput,
        ) -> CheckpointOutput:
            self.next_checkpoint = yield from output
            return self.next_checkpoint

        yield from _inner_wrapper(checkpoint_connector_generator)
        if self.next_checkpoint is None:
            raise RuntimeError("connector did not return a checkpoint")
        yield self.next_checkpoint


@dataclass(frozen=True, slots=True)
class ConnectorBatch:
    """One ordered item batch or the final connector checkpoint."""

    documents: tuple[Document, ...] = ()
    deleted_documents: tuple[DeletedDocument, ...] = ()
    failures: tuple[ConnectorFailure, ...] = ()
    checkpoint: ConnectorCheckpoint | None = None


class ConnectorRunner:
    """Batch one checkpointed connector without persistence or logging content."""

    def __init__(
        self,
        connector: CheckpointedConnector,
        checkpoint: ConnectorCheckpoint,
        *,
        batch_size: int,
        include_permissions: bool,
    ) -> None:
        if not isinstance(connector, CheckpointedConnector):
            raise TypeError("runner requires a checkpointed connector")
        if type(checkpoint) is not ConnectorCheckpoint:
            raise TypeError("runner requires an exact checkpoint")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("runner batch size must be positive")
        if include_permissions and not isinstance(
            connector,
            CheckpointedConnectorWithPermSync,
        ):
            raise ValueError("connector does not support permission observation")
        self._connector = connector
        self._checkpoint = checkpoint
        self._batch_size = batch_size
        self._include_permissions = include_permissions

    def run(self) -> Generator[ConnectorBatch]:
        load = (
            self._connector.load_from_checkpoint_with_perm_sync
            if self._include_permissions
            and isinstance(self._connector, CheckpointedConnectorWithPermSync)
            else self._connector.load_from_checkpoint
        )
        documents: list[Document] = []
        deleted: list[DeletedDocument] = []
        failures: list[ConnectorFailure] = []
        for item in CheckpointOutputWrapper()(load(self._checkpoint)):
            if type(item) is ConnectorCheckpoint:
                if documents or deleted or failures:
                    yield ConnectorBatch(
                        tuple(documents),
                        tuple(deleted),
                        tuple(failures),
                    )
                yield ConnectorBatch(checkpoint=item)
                continue
            if type(item) is Document:
                documents.append(item)
            elif type(item) is DeletedDocument:
                deleted.append(item)
            elif type(item) is ConnectorFailure:
                failures.append(item)
            else:
                raise ValueError("connector returned an invalid item")
            if len(documents) + len(deleted) + len(failures) >= self._batch_size:
                yield ConnectorBatch(
                    tuple(documents),
                    tuple(deleted),
                    tuple(failures),
                )
                documents = []
                deleted = []
                failures = []


__all__ = ["CheckpointOutputWrapper", "ConnectorBatch", "ConnectorRunner"]
