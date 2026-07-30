"""Closed connector-runner contracts owned by the Supply execution seam."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy import Connection

from engine.supply.jobs import WorkerLeaseClaims, WorkerLeaseToken

_MAX_OPAQUE_REF_LENGTH = 512
_MAX_CONTENT_BYTES = 64 * 1024 * 1024
_MAX_STAGED_PAGE_BYTES = 256 * 1024 * 1024
_MAX_CHECKPOINT_BYTES = 1024 * 1024
_MAX_EVIDENCE_BYTES = 1024 * 1024
_MAX_METADATA_ITEMS = 128
_MAX_REASON_LENGTH = 512
_MAX_POLICY_EPOCH = (1 << 63) - 1


def _require_uuid(field_name: str, value: object) -> UUID:
    if type(value) is not UUID:
        raise TypeError(f"{field_name} must be UUID")
    return value


def _require_ref(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
        or len(value) > _MAX_OPAQUE_REF_LENGTH
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded nonblank opaque reference")
    return value


def _require_bytes(
    field_name: str,
    value: object,
    *,
    maximum_length: int,
) -> bytes:
    if type(value) is not bytes or not value or len(value) > maximum_length:
        raise ValueError(f"{field_name} must be bounded nonempty bytes")
    return value


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be an aware UTC datetime")
    return value


def _require_policy_epoch(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_POLICY_EPOCH:
        raise ValueError(
            "ACL observation Policy Epoch must fit a positive signed 64-bit integer"
        )
    return value


def _require_sha256(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


def _require_metadata(value: object) -> tuple[tuple[str, str], ...]:
    if type(value) is not tuple or len(value) > _MAX_METADATA_ITEMS:
        raise TypeError("Supply document metadata must be a bounded exact tuple")
    metadata = value
    for item in metadata:
        if type(item) is not tuple or len(item) != 2:
            raise TypeError("Supply document metadata items must be exact pairs")
        key, item_value = item
        _require_ref("Supply document metadata key", key)
        if (
            type(item_value) is not str
            or not item_value
            or item_value.isspace()
            or len(item_value) > _MAX_OPAQUE_REF_LENGTH
        ):
            raise ValueError("Supply document metadata values must be bounded")
    keys = tuple(key for key, _ in metadata)
    if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
        raise ValueError("Supply document metadata keys must be unique and sorted")
    return metadata


@dataclass(frozen=True, slots=True)
class ConnectorCheckpointBinding:
    """Exact Organization, SourceVersion, and durable WorkerJob identity."""

    organization_id: UUID = field(repr=False)
    source_version_id: UUID = field(repr=False)
    worker_job_id: UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid("checkpoint Organization", self.organization_id)
        _require_uuid("checkpoint SourceVersion", self.source_version_id)
        _require_uuid("checkpoint WorkerJob", self.worker_job_id)


@dataclass(frozen=True, slots=True)
class SupplyBridgeExecution:
    """Untrusted runner invocation bound to tenant, version, job, and lease."""

    organization_id: UUID = field(repr=False)
    source_version_id: UUID = field(repr=False)
    worker_job_id: UUID = field(repr=False)
    worker_lease: WorkerLeaseToken = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid("Supply bridge Organization", self.organization_id)
        _require_uuid("Supply bridge SourceVersion", self.source_version_id)
        _require_uuid("Supply bridge WorkerJob", self.worker_job_id)
        if type(self.worker_lease) is not WorkerLeaseToken:
            raise TypeError("Supply bridge execution requires WorkerLeaseToken")

    @property
    def binding(self) -> ConnectorCheckpointBinding:
        return ConnectorCheckpointBinding(
            organization_id=self.organization_id,
            source_version_id=self.source_version_id,
            worker_job_id=self.worker_job_id,
        )


class SourceAclEvidenceClass(StrEnum):
    """Honest source evidence strength; never an authorization outcome."""

    LIVE = "live"
    MIRRORED = "mirrored"
    WEAK = "weak"


@dataclass(frozen=True, slots=True)
class SourceAclObservation:
    """Connector-local ACL evidence that grants no access by itself."""

    organization_id: UUID = field(repr=False)
    observed_at: datetime
    policy_epoch: int
    evidence_class: SourceAclEvidenceClass
    evidence_payload: bytes | None = field(default=None, repr=False)
    source_lacks_stronger_acl: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_source_acl_observation(self)


def _validate_source_acl_observation(observation: SourceAclObservation) -> None:
    _require_uuid("ACL observation Organization", observation.organization_id)
    _require_utc("ACL observation observed_at", observation.observed_at)
    _require_policy_epoch(observation.policy_epoch)
    if type(observation.evidence_class) is not SourceAclEvidenceClass:
        raise TypeError("ACL observation evidence_class must be closed")
    if observation.evidence_payload is not None:
        _require_bytes(
            "ACL observation evidence payload",
            observation.evidence_payload,
            maximum_length=_MAX_EVIDENCE_BYTES,
        )
    justification = observation.source_lacks_stronger_acl
    if justification is not None and (
        type(justification) is not str
        or not justification
        or justification.isspace()
        or justification != justification.strip()
        or len(justification) > _MAX_REASON_LENGTH
    ):
        raise ValueError("Weak ACL evidence requires a bounded justification")
    if observation.evidence_class in {
        SourceAclEvidenceClass.LIVE,
        SourceAclEvidenceClass.MIRRORED,
    }:
        if observation.evidence_payload is None:
            raise ValueError("Live and Mirrored ACL evidence require a payload")
        if justification is not None:
            raise ValueError("Strong ACL evidence cannot claim Weak justification")
    elif observation.evidence_payload is not None or justification is None:
        raise ValueError(
            "Weak ACL evidence requires explicit source-lacks-stronger-ACL "
            "justification and no strong evidence payload"
        )


@dataclass(frozen=True, slots=True)
class SupplyDocumentDeleteObservation:
    """One source document deletion with its exact ACL observation."""

    document_ref: str = field(repr=False)
    acl_observation: SourceAclObservation = field(repr=False)

    def __post_init__(self) -> None:
        _validate_supply_document_delete_observation(self)


def _validate_supply_document_delete_observation(
    observation: SupplyDocumentDeleteObservation,
) -> None:
    _require_ref("deleted document_ref", observation.document_ref)
    if type(observation.acl_observation) is not SourceAclObservation:
        raise TypeError("deleted document ACL must be SourceAclObservation")
    _validate_source_acl_observation(observation.acl_observation)


@dataclass(frozen=True, slots=True)
class SupplyDocumentEnvelope:
    """One source document emitted under an exact tenant and job binding."""

    organization_id: UUID = field(repr=False)
    source_version_id: UUID = field(repr=False)
    worker_job_id: UUID = field(repr=False)
    document_ref: str = field(repr=False)
    content: bytes = field(repr=False)
    content_type: str
    acl_observation: SourceAclObservation = field(repr=False)
    metadata: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_supply_document_envelope(self)


def _validate_supply_document_envelope(
    envelope: SupplyDocumentEnvelope,
) -> None:
    _require_uuid("document Organization", envelope.organization_id)
    _require_uuid("document SourceVersion", envelope.source_version_id)
    _require_uuid("document WorkerJob", envelope.worker_job_id)
    _require_ref("document_ref", envelope.document_ref)
    _require_bytes(
        "document content",
        envelope.content,
        maximum_length=_MAX_CONTENT_BYTES,
    )
    _require_ref("document content_type", envelope.content_type)
    if type(envelope.acl_observation) is not SourceAclObservation:
        raise TypeError("document acl_observation must be SourceAclObservation")
    _validate_source_acl_observation(envelope.acl_observation)
    if envelope.acl_observation.organization_id != envelope.organization_id:
        raise ValueError("document and ACL observation Organization must match")
    _require_metadata(envelope.metadata)


@dataclass(frozen=True, slots=True)
class ConnectorCheckpointProposal:
    """Opaque progress proposed by a connector for one emitted change page."""

    binding: ConnectorCheckpointBinding = field(repr=False)
    opaque_checkpoint: bytes = field(repr=False)
    change_page_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.binding) is not ConnectorCheckpointBinding:
            raise TypeError("checkpoint proposal requires exact binding")
        _require_bytes(
            "opaque checkpoint",
            self.opaque_checkpoint,
            maximum_length=_MAX_CHECKPOINT_BYTES,
        )
        _require_ref("checkpoint change_page_ref", self.change_page_ref)


@dataclass(frozen=True, slots=True)
class SupplyChangePage:
    """One engine-acceptable page plus a still-nondurable checkpoint proposal."""

    binding: ConnectorCheckpointBinding = field(repr=False)
    page_ref: str = field(repr=False)
    documents: tuple[SupplyDocumentEnvelope, ...] = field(repr=False)
    deleted_document_refs: tuple[SupplyDocumentDeleteObservation, ...] = field(
        repr=False
    )
    checkpoint_proposal: bytes = field(repr=False)
    terminal: bool = False

    def __post_init__(self) -> None:
        _validate_supply_change_page(self)


def _validate_supply_change_page(page: SupplyChangePage) -> None:
    if type(page.binding) is not ConnectorCheckpointBinding:
        raise TypeError("change page requires exact binding")
    _require_ref("change page_ref", page.page_ref)
    if type(page.documents) is not tuple or any(
        type(document) is not SupplyDocumentEnvelope for document in page.documents
    ):
        raise TypeError("change page documents must be an exact envelope tuple")
    for document in page.documents:
        _validate_supply_document_envelope(document)
        if (
            document.organization_id != page.binding.organization_id
            or document.source_version_id != page.binding.source_version_id
            or document.worker_job_id != page.binding.worker_job_id
        ):
            raise ValueError("change page document exact binding must match")
    if type(page.deleted_document_refs) is not tuple or any(
        type(observation) is not SupplyDocumentDeleteObservation
        for observation in page.deleted_document_refs
    ):
        raise TypeError("deleted document refs must be an exact observation tuple")
    for observation in page.deleted_document_refs:
        _validate_supply_document_delete_observation(observation)
        if observation.acl_observation.organization_id != page.binding.organization_id:
            raise ValueError("change page deleted document Organization must match")
    emitted_refs = tuple(document.document_ref for document in page.documents)
    deleted_refs = tuple(
        observation.document_ref for observation in page.deleted_document_refs
    )
    if (
        len(emitted_refs) != len(set(emitted_refs))
        or len(deleted_refs) != len(set(deleted_refs))
        or set(emitted_refs).intersection(deleted_refs)
    ):
        raise ValueError("change page document identities must be disjoint and unique")
    _require_bytes(
        "change page checkpoint proposal",
        page.checkpoint_proposal,
        maximum_length=_MAX_CHECKPOINT_BYTES,
    )
    if type(page.terminal) is not bool:
        raise TypeError("change page terminal must be bool")


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    """Durably accepted connector page bytes awaiting corpus compilation."""

    binding: ConnectorCheckpointBinding = field(repr=False)
    artifact_ref: str = field(repr=False)
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.binding) is not ConnectorCheckpointBinding:
            raise TypeError("staged artifact requires exact binding")
        _require_ref("staged artifact_ref", self.artifact_ref)
        _require_bytes(
            "staged artifact payload",
            self.payload,
            maximum_length=_MAX_STAGED_PAGE_BYTES,
        )


def serialize_supply_change_page(page: SupplyChangePage) -> bytes:
    """Serialize every emitted page fact into one stable engine-owned payload."""

    if type(page) is not SupplyChangePage:
        raise TypeError("staged serialization requires SupplyChangePage")
    _validate_supply_change_page(page)
    document = {
        "binding": {
            "organization_id": str(page.binding.organization_id),
            "source_version_id": str(page.binding.source_version_id),
            "worker_job_id": str(page.binding.worker_job_id),
        },
        "checkpoint_proposal": base64.b64encode(page.checkpoint_proposal).decode(
            "ascii"
        ),
        "deleted_document_refs": [
            {
                "acl_observation": _serialize_source_acl_observation(
                    observation.acl_observation
                ),
                "document_ref": observation.document_ref,
            }
            for observation in page.deleted_document_refs
        ],
        "documents": [
            {
                "acl_observation": _serialize_source_acl_observation(
                    envelope.acl_observation
                ),
                "content": base64.b64encode(envelope.content).decode("ascii"),
                "content_type": envelope.content_type,
                "document_ref": envelope.document_ref,
                "metadata": [list(item) for item in envelope.metadata],
                "organization_id": str(envelope.organization_id),
                "source_version_id": str(envelope.source_version_id),
                "worker_job_id": str(envelope.worker_job_id),
            }
            for envelope in page.documents
        ],
        "page_ref": page.page_ref,
        "terminal": page.terminal,
    }
    payload = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    _require_bytes(
        "serialized staged page",
        payload,
        maximum_length=_MAX_STAGED_PAGE_BYTES,
    )
    return payload


def _serialize_source_acl_observation(
    observation: SourceAclObservation,
) -> dict[str, object]:
    return {
        "evidence_class": observation.evidence_class.value,
        "evidence_payload": (
            base64.b64encode(observation.evidence_payload).decode("ascii")
            if observation.evidence_payload is not None
            else None
        ),
        "observed_at": observation.observed_at.isoformat(),
        "organization_id": str(observation.organization_id),
        "policy_epoch": observation.policy_epoch,
        "source_lacks_stronger_acl": observation.source_lacks_stronger_acl,
    }


@dataclass(frozen=True, slots=True)
class ConnectorHeartbeat:
    """Content-free liveness observation for one exact connector job."""

    binding: ConnectorCheckpointBinding = field(repr=False)
    observed_at: datetime

    def __post_init__(self) -> None:
        if type(self.binding) is not ConnectorCheckpointBinding:
            raise TypeError("connector heartbeat requires exact binding")
        _require_utc("connector heartbeat observed_at", self.observed_at)


class ConnectorFailureCategory(StrEnum):
    """Closed content-free operational failure categories."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class ConnectorFailure:
    """Digest-only connector failure for one exact job."""

    binding: ConnectorCheckpointBinding = field(repr=False)
    category: ConnectorFailureCategory
    reason_digest: str = field(repr=False)
    observed_at: datetime

    def __post_init__(self) -> None:
        if type(self.binding) is not ConnectorCheckpointBinding:
            raise TypeError("connector failure requires exact binding")
        if type(self.category) is not ConnectorFailureCategory:
            raise TypeError("connector failure category must be closed")
        _require_sha256("connector failure reason_digest", self.reason_digest)
        _require_utc("connector failure observed_at", self.observed_at)


class ConnectorAdapter(Protocol):
    """Runner-side adapter with no persistence or authorization authority."""

    def load(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage: ...

    def poll(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage: ...

    def load_checkpoint(self, opaque_checkpoint: bytes | None) -> None: ...


class ConnectorCheckpointStore(Protocol):
    """Engine-side opaque checkpoint persistence joined to page acceptance."""

    def load(
        self,
        binding: ConnectorCheckpointBinding,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> bytes | None: ...

    def redeem_for_execution(
        self,
        binding: ConnectorCheckpointBinding,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> bytes | None: ...


class StagedArtifactSink(Protocol):
    """Engine staging and checkpoint acceptance exposed as one atomic write."""

    def accept_change_page(
        self,
        connection: Connection,
        page: SupplyChangePage,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> None: ...

    def load(
        self,
        binding: ConnectorCheckpointBinding,
        artifact_ref: str,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> StagedArtifact | None: ...


class ConnectorOperationalSink(Protocol):
    """Content-free heartbeat and failure observation surface."""

    def heartbeat(self, observation: ConnectorHeartbeat) -> None: ...

    def failure(self, observation: ConnectorFailure) -> None: ...


__all__ = [
    "ConnectorAdapter",
    "ConnectorCheckpointBinding",
    "ConnectorCheckpointProposal",
    "ConnectorCheckpointStore",
    "ConnectorFailure",
    "ConnectorFailureCategory",
    "ConnectorHeartbeat",
    "ConnectorOperationalSink",
    "SourceAclEvidenceClass",
    "SourceAclObservation",
    "StagedArtifact",
    "StagedArtifactSink",
    "SupplyBridgeExecution",
    "SupplyChangePage",
    "SupplyDocumentDeleteObservation",
    "SupplyDocumentEnvelope",
    "serialize_supply_change_page",
]
