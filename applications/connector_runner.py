"""ContextEngine-owned process boundary for one leased connector job."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from adapters.connectors.feishu import (
    DeterministicFeishuTwin,
    FeishuDocsConnectorAdapter,
)
from adapters.connectors.file import FileConnectorAdapter, FileRootVaultSource
from adapters.file_source import FileReadLimits, FileRootRegistry
from engine.control import FileRootRef
from engine.supply import (
    SupplyBridgeExecution,
    WorkerLeaseToken,
    serialize_supply_change_page,
)

_MAX_JOB_BYTES = 64 * 1024
_MAX_FEISHU_TWIN_JOB_BYTES = 2 * 1024 * 1024
_MAX_FILE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ConnectorRunnerRequest:
    """Closed serialized input for exactly one engine-minted connector job."""

    organization_id: UUID
    source_version_id: UUID
    worker_job_id: UUID
    service_principal_id: UUID
    worker_lease: WorkerLeaseToken
    policy_epoch: int
    idempotency_key: str
    service_actor_expires_at: datetime
    root_ref: FileRootRef
    root_path: Path
    opaque_checkpoint: bytes | None

    @classmethod
    def from_json(cls, payload: bytes) -> ConnectorRunnerRequest:
        if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_JOB_BYTES:
            raise ValueError("connector runner job is unavailable")
        try:
            decoded = json.loads(payload)
            if type(decoded) is not dict or set(decoded) != {
                "idempotency_key",
                "opaque_checkpoint",
                "organization_id",
                "policy_epoch",
                "root_path",
                "root_ref",
                "service_actor_expires_at",
                "service_principal_id",
                "source_version_id",
                "worker_job_id",
                "worker_lease",
            }:
                raise ValueError
            checkpoint = decoded["opaque_checkpoint"]
            parsed = cls(
                organization_id=UUID(cast(str, decoded["organization_id"])),
                source_version_id=UUID(cast(str, decoded["source_version_id"])),
                worker_job_id=UUID(cast(str, decoded["worker_job_id"])),
                service_principal_id=UUID(cast(str, decoded["service_principal_id"])),
                worker_lease=WorkerLeaseToken(cast(str, decoded["worker_lease"])),
                policy_epoch=cast(int, decoded["policy_epoch"]),
                idempotency_key=cast(str, decoded["idempotency_key"]),
                service_actor_expires_at=datetime.fromisoformat(
                    cast(str, decoded["service_actor_expires_at"])
                ),
                root_ref=FileRootRef(cast(str, decoded["root_ref"])),
                root_path=Path(cast(str, decoded["root_path"])),
                opaque_checkpoint=(
                    None
                    if checkpoint is None
                    else base64.b64decode(cast(str, checkpoint), validate=True)
                ),
            )
        except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError):
            raise ValueError("connector runner job is unavailable") from None
        parsed._validate()
        return parsed

    def _validate(self) -> None:
        if type(self.policy_epoch) is not int or self.policy_epoch < 1:
            raise ValueError("connector runner job is unavailable")
        if (
            type(self.idempotency_key) is not str
            or len(self.idempotency_key) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.idempotency_key
            )
        ):
            raise ValueError("connector runner job is unavailable")
        if (
            type(self.service_actor_expires_at) is not datetime
            or self.service_actor_expires_at.tzinfo is None
        ):
            raise ValueError("connector runner job is unavailable")
        if not isinstance(self.root_path, Path) or not self.root_path.is_absolute():
            raise ValueError("connector runner job is unavailable")

    @property
    def execution(self) -> SupplyBridgeExecution:
        return SupplyBridgeExecution(
            organization_id=self.organization_id,
            source_version_id=self.source_version_id,
            worker_job_id=self.worker_job_id,
            worker_lease=self.worker_lease,
        )

    def create_adapter(self) -> tuple[FileConnectorAdapter, FileRootRegistry]:
        """Compose the sole admitted connector from explicitly passed root facts."""

        roots = FileRootRegistry(
            {self.root_ref: self.root_path},
            limits=FileReadLimits(_MAX_FILE_BYTES),
        )
        adapter = FileConnectorAdapter(
            FileRootVaultSource(roots, self.root_ref),
            FileRootVaultSource(roots, self.root_ref),
            policy_epoch=self.policy_epoch,
        )
        return adapter, roots

    def execute(self) -> bytes:
        adapter, roots = self.create_adapter()
        try:
            adapter.load_checkpoint(self.opaque_checkpoint)
            page = (
                adapter.load(self.execution.binding)
                if self.opaque_checkpoint is None
                else adapter.poll(self.execution.binding)
            )
            return serialize_supply_change_page(page)
        finally:
            roots.close()


@dataclass(frozen=True, slots=True)
class FeishuTwinRunnerRequest:
    """Closed serialized input for one credential-free Feishu twin execution."""

    organization_id: UUID
    source_version_id: UUID
    worker_job_id: UUID
    service_principal_id: UUID
    worker_lease: WorkerLeaseToken
    policy_epoch: int
    idempotency_key: str
    service_actor_expires_at: datetime
    fixture_payload: bytes
    opaque_checkpoint: bytes | None

    @classmethod
    def from_json(cls, payload: bytes) -> FeishuTwinRunnerRequest:
        if (
            type(payload) is not bytes
            or not 1 <= len(payload) <= _MAX_FEISHU_TWIN_JOB_BYTES
        ):
            raise ValueError("Feishu twin runner job is unavailable")
        try:
            decoded = json.loads(payload)
            if type(decoded) is not dict or set(decoded) != {
                "fixture_payload",
                "idempotency_key",
                "opaque_checkpoint",
                "organization_id",
                "policy_epoch",
                "service_actor_expires_at",
                "service_principal_id",
                "source_version_id",
                "worker_job_id",
                "worker_lease",
            }:
                raise ValueError
            checkpoint = decoded["opaque_checkpoint"]
            fixture_payload = base64.b64decode(
                cast(str, decoded["fixture_payload"]),
                validate=True,
            )
            parsed = cls(
                organization_id=UUID(cast(str, decoded["organization_id"])),
                source_version_id=UUID(cast(str, decoded["source_version_id"])),
                worker_job_id=UUID(cast(str, decoded["worker_job_id"])),
                service_principal_id=UUID(cast(str, decoded["service_principal_id"])),
                worker_lease=WorkerLeaseToken(cast(str, decoded["worker_lease"])),
                policy_epoch=cast(int, decoded["policy_epoch"]),
                idempotency_key=cast(str, decoded["idempotency_key"]),
                service_actor_expires_at=datetime.fromisoformat(
                    cast(str, decoded["service_actor_expires_at"])
                ),
                fixture_payload=fixture_payload,
                opaque_checkpoint=(
                    None
                    if checkpoint is None
                    else base64.b64decode(cast(str, checkpoint), validate=True)
                ),
            )
        except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError):
            raise ValueError("Feishu twin runner job is unavailable") from None
        parsed._validate()
        return parsed

    def _validate(self) -> None:
        if type(self.policy_epoch) is not int or self.policy_epoch < 1:
            raise ValueError("Feishu twin runner job is unavailable")
        if (
            type(self.idempotency_key) is not str
            or len(self.idempotency_key) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.idempotency_key
            )
            or not 1 <= len(self.fixture_payload) <= 1024 * 1024
            or self.service_actor_expires_at.tzinfo is None
        ):
            raise ValueError("Feishu twin runner job is unavailable")

    @property
    def execution(self) -> SupplyBridgeExecution:
        return SupplyBridgeExecution(
            organization_id=self.organization_id,
            source_version_id=self.source_version_id,
            worker_job_id=self.worker_job_id,
            worker_lease=self.worker_lease,
        )

    def execute(self) -> bytes:
        adapter = FeishuDocsConnectorAdapter.from_twin(
            DeterministicFeishuTwin(
                self.fixture_payload,
                policy_epoch=self.policy_epoch,
            )
        )
        adapter.load_checkpoint(self.opaque_checkpoint)
        page = (
            adapter.load(self.execution.binding)
            if self.opaque_checkpoint is None
            else adapter.poll(self.execution.binding)
        )
        return serialize_supply_change_page(page)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-file", action="store_true")
    parser.add_argument("--run-feishu-twin", action="store_true")
    args = parser.parse_args(argv)
    if args.scan_file == args.run_feishu_twin:
        parser.error("one runner operation is required")
    try:
        payload = sys.stdin.buffer.read()
        sys.stdout.buffer.write(
            ConnectorRunnerRequest.from_json(payload).execute()
            if args.scan_file
            else FeishuTwinRunnerRequest.from_json(payload).execute()
        )
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ConnectorRunnerRequest", "FeishuTwinRunnerRequest", "main"]
