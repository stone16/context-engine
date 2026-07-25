"""Control-owned contracts for deterministic File source change pages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from engine._opaque import decode_base64url, encode_base64url
from engine.control.contracts import (
    SourceRef,
    SourceVersion,
    _require_sha256,
    _require_utc,
)
from engine.control.file_imports import FileImportPath

MAX_FILE_CHANGE_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class ChangeLimit:
    """Server-bounded maximum number of changes returned in one page."""

    value: int

    def __post_init__(self) -> None:
        if (
            type(self.value) is not int
            or not 1 <= self.value <= MAX_FILE_CHANGE_PAGE_SIZE
        ):
            raise ValueError("File change limit must be a bounded positive integer")


@dataclass(frozen=True, slots=True)
class InitialScan:
    """Explicit beginning of one File source scan; never an implicit cursor."""


@dataclass(frozen=True, slots=True)
class PendingChangeCursor:
    """Provider-proposed position that is unusable until Control accepts its page."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or not 64 <= len(self.value) <= 8_192
            or self.value != self.value.strip()
            or self.value.count(".") != 1
            or any(character.isspace() for character in self.value)
        ):
            raise ValueError("pending File change cursor must be an opaque token")


@dataclass(frozen=True, slots=True)
class ChangeCursor:
    """Control-acknowledged opaque position inside one immutable File scan."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or not 128 <= len(self.value) <= 16_384
            or self.value != self.value.strip()
            or self.value.count(".") != 1
            or any(character.isspace() for character in self.value)
        ):
            raise ValueError("accepted File change cursor must be an opaque token")


@dataclass(frozen=True, slots=True)
class FileChangeScanHead:
    """Control-read durable head required to resume or supersede a File scan."""

    source_version_ref: UUID = field(repr=False)
    scan_ref: str = field(repr=False)
    scan_epoch: UUID = field(repr=False)
    page_limit: int
    page_ref: str = field(repr=False)
    checkpoint_ref: str = field(repr=False)
    sequence: int
    complete: bool
    superseded_scan_epoch: UUID | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.source_version_ref) is not UUID:
            raise TypeError("File change scan head SourceVersion must be UUID")
        _require_sha256("File change scan head scan_ref", self.scan_ref)
        if type(self.scan_epoch) is not UUID:
            raise TypeError("File change scan head epoch must be UUID")
        if (
            type(self.page_limit) is not int
            or not 1 <= self.page_limit <= MAX_FILE_CHANGE_PAGE_SIZE
        ):
            raise ValueError("File change scan head page_limit is invalid")
        _require_sha256("File change scan head page_ref", self.page_ref)
        _require_checkpoint_ref(self.checkpoint_ref)
        if type(self.sequence) is not int or not 1 <= self.sequence <= 2**63 - 1:
            raise ValueError("File change scan head sequence is invalid")
        if type(self.complete) is not bool:
            raise TypeError("File change scan head complete must be bool")
        if (
            self.superseded_scan_epoch is not None
            and type(self.superseded_scan_epoch) is not UUID
        ):
            raise TypeError("superseded File scan epoch must be UUID or None")


@dataclass(frozen=True, slots=True)
class FileChangeSource:
    """Trusted active SourceVersion and current durable scan head."""

    organization_id: UUID = field(repr=False)
    source_version: SourceVersion
    scan_head: FileChangeScanHead | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.organization_id) is not UUID:
            raise TypeError("File change source organization_id must be UUID")
        if type(self.source_version) is not SourceVersion:
            raise TypeError("File change source requires SourceVersion")
        if (
            self.scan_head is not None
            and type(self.scan_head) is not FileChangeScanHead
        ):
            raise TypeError("File change source scan head is invalid")


class FileChangeKind(StrEnum):
    """Closed change kinds activated by the shallow File scan."""

    UPSERT = "upsert"


@dataclass(frozen=True, slots=True)
class SourceChange:
    """One content-free File observation bound to an exact source scan."""

    organization_id: UUID = field(repr=False)
    source_ref: UUID = field(repr=False)
    source_version_ref: UUID = field(repr=False)
    scan_ref: str = field(repr=False)
    kind: FileChangeKind
    path: FileImportPath
    content_sha256: str
    content_length: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not UUID
            for value in (
                self.organization_id,
                self.source_ref,
                self.source_version_ref,
            )
        ):
            raise TypeError("SourceChange ownership references must be UUID")
        _require_sha256("SourceChange scan_ref", self.scan_ref)
        if self.kind is not FileChangeKind.UPSERT:
            raise ValueError("SourceChange kind is not active")
        if type(self.path) is not FileImportPath:
            raise TypeError("SourceChange path must be FileImportPath")
        _require_sha256("SourceChange content_sha256", self.content_sha256)
        if type(self.content_length) is not int or self.content_length < 0:
            raise ValueError("SourceChange content_length must be nonnegative")


@dataclass(frozen=True, slots=True)
class ChangePage:
    """Bounded deterministic File change page and its opaque continuation cursor."""

    organization_id: UUID = field(repr=False)
    source_ref: UUID = field(repr=False)
    source_version_ref: UUID = field(repr=False)
    scan_ref: str = field(repr=False)
    scan_epoch: UUID = field(repr=False)
    page_limit: int
    predecessor_page_ref: str | None = field(repr=False)
    predecessor_checkpoint_ref: str | None = field(repr=False)
    predecessor_sequence: int | None
    superseded_scan_epoch: UUID | None = field(repr=False)
    changes: tuple[SourceChange, ...]
    next_cursor: PendingChangeCursor | None = field(repr=False)
    complete: bool
    provider_proof: str = field(repr=False)

    def __post_init__(self) -> None:
        if any(
            type(value) is not UUID
            for value in (
                self.organization_id,
                self.source_ref,
                self.source_version_ref,
            )
        ):
            raise TypeError("ChangePage ownership references must be UUID")
        _require_sha256("ChangePage scan_ref", self.scan_ref)
        if type(self.scan_epoch) is not UUID:
            raise TypeError("ChangePage scan_epoch must be UUID")
        if (
            type(self.page_limit) is not int
            or not 1 <= self.page_limit <= MAX_FILE_CHANGE_PAGE_SIZE
        ):
            raise ValueError("ChangePage page_limit is invalid")
        if self.predecessor_page_ref is not None:
            _require_sha256(
                "ChangePage predecessor_page_ref", self.predecessor_page_ref
            )
        if self.predecessor_checkpoint_ref is not None:
            _require_checkpoint_ref(self.predecessor_checkpoint_ref)
        if self.predecessor_sequence is not None and (
            type(self.predecessor_sequence) is not int
            or not 1 <= self.predecessor_sequence <= 2**63 - 1
        ):
            raise ValueError("ChangePage predecessor_sequence is invalid")
        if (
            len(
                {
                    self.predecessor_page_ref is None,
                    self.predecessor_checkpoint_ref is None,
                    self.predecessor_sequence is None,
                }
            )
            != 1
        ):
            raise ValueError("ChangePage predecessor checkpoint binding is incomplete")
        if (
            self.superseded_scan_epoch is not None
            and type(self.superseded_scan_epoch) is not UUID
        ):
            raise TypeError("ChangePage superseded_scan_epoch must be UUID or None")
        if (
            self.predecessor_page_ref is not None
            and self.superseded_scan_epoch is not None
        ):
            raise ValueError("a continuation cannot supersede another scan")
        if (
            type(self.changes) is not tuple
            or len(self.changes) > self.page_limit
            or any(type(change) is not SourceChange for change in self.changes)
        ):
            raise TypeError("ChangePage changes must be a bounded SourceChange tuple")
        if (
            self.next_cursor is not None
            and type(self.next_cursor) is not PendingChangeCursor
        ):
            raise TypeError(
                "ChangePage next_cursor must be PendingChangeCursor or None"
            )
        if type(self.complete) is not bool:
            raise TypeError("ChangePage complete must be bool")
        if self.complete is not (self.next_cursor is None):
            raise ValueError("ChangePage completion and next cursor disagree")
        if not self.complete and not self.changes:
            raise ValueError("an incomplete ChangePage cannot advance without changes")
        paths = tuple(change.path.value for change in self.changes)
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("ChangePage paths must be in canonical order")
        if len(paths) != len(set(paths)):
            raise ValueError("ChangePage paths must be unique")
        if any(
            change.scan_ref != self.scan_ref
            or change.organization_id != self.organization_id
            or change.source_ref != self.source_ref
            or change.source_version_ref != self.source_version_ref
            for change in self.changes
        ):
            raise ValueError("ChangePage contains a change from another scan")
        if len(decode_base64url(self.provider_proof)) != 64:
            raise ValueError("ChangePage provider proof must be an Ed25519 signature")


_PAGE_DOMAIN = "context-engine.file-change-page.v1"
_CHECKPOINT_CURSOR_DOMAIN = "context-engine.accepted-file-change-cursor.v1"


def _page_document(page: ChangePage) -> dict[str, object]:
    return {
        "changes": [
            {
                "contentLength": change.content_length,
                "contentSha256": change.content_sha256,
                "kind": change.kind.value,
                "path": change.path.value,
            }
            for change in page.changes
        ],
        "complete": page.complete,
        "domain": _PAGE_DOMAIN,
        "nextCursor": (None if page.next_cursor is None else page.next_cursor.value),
        "organizationId": str(page.organization_id),
        "pageLimit": page.page_limit,
        "predecessorCheckpointRef": page.predecessor_checkpoint_ref,
        "predecessorPageRef": page.predecessor_page_ref,
        "predecessorSequence": page.predecessor_sequence,
        "scanEpoch": str(page.scan_epoch),
        "scanRef": page.scan_ref,
        "supersededScanEpoch": (
            None
            if page.superseded_scan_epoch is None
            else str(page.superseded_scan_epoch)
        ),
        "sourceId": str(page.source_ref),
        "sourceVersionId": str(page.source_version_ref),
        "version": 1,
    }


def _page_payload(page: ChangePage) -> bytes:
    return rfc8785.dumps(cast(Any, _page_document(page)))


def _accepted_cursor_payload(
    *,
    organization_id: UUID,
    source_ref: SourceRef,
    source_version_ref: UUID,
    scan_ref: str,
    scan_epoch: UUID,
    page_ref: str,
    checkpoint_ref: str,
    sequence: int,
    pending_cursor: PendingChangeCursor,
) -> bytes:
    """Serialize the exact persistence-accepted continuation claims."""

    if type(organization_id) is not UUID or type(source_ref) is not SourceRef:
        raise TypeError("accepted cursor ownership is invalid")
    if type(source_version_ref) is not UUID:
        raise TypeError("accepted cursor SourceVersion is invalid")
    if type(scan_epoch) is not UUID:
        raise TypeError("accepted cursor scan epoch is invalid")
    _require_sha256("accepted cursor scan_ref", scan_ref)
    _require_sha256("accepted cursor page_ref", page_ref)
    _require_checkpoint_ref(checkpoint_ref)
    if type(sequence) is not int or not 1 <= sequence <= 2**63 - 1:
        raise ValueError("accepted cursor sequence is invalid")
    if type(pending_cursor) is not PendingChangeCursor:
        raise TypeError("accepted cursor requires a pending continuation")
    return rfc8785.dumps(
        cast(
            Any,
            {
                "checkpointRef": checkpoint_ref,
                "domain": _CHECKPOINT_CURSOR_DOMAIN,
                "organizationId": str(organization_id),
                "pageRef": page_ref,
                "pendingCursor": pending_cursor.value,
                "scanEpoch": str(scan_epoch),
                "scanRef": scan_ref,
                "sequence": sequence,
                "sourceId": str(source_ref.value),
                "sourceVersionId": str(source_version_ref),
                "version": 1,
            },
        )
    )


@dataclass(frozen=True, slots=True)
class _AcceptedCursorClaims:
    pending_cursor: PendingChangeCursor
    scan_ref: str
    page_ref: str
    checkpoint_ref: str
    sequence: int
    scan_epoch: UUID


@dataclass(frozen=True, slots=True, init=False, repr=False)
class VerifiedChangePage:
    """Provider-authenticated page accepted only behind ContextControl."""

    page: ChangePage
    page_ref: str

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("VerifiedChangePage is proof-constructed")


@dataclass(frozen=True, slots=True)
class AcceptedChangePage:
    """Control result carrying only a post-commit continuation cursor."""

    source_ref: SourceRef
    source_version_ref: UUID = field(repr=False)
    scan_ref: str = field(repr=False)
    scan_epoch: UUID = field(repr=False)
    page_limit: int
    superseded_scan_epoch: UUID | None = field(repr=False)
    page_ref: str = field(repr=False)
    checkpoint_ref: str = field(repr=False)
    sequence: int
    change_count: int
    complete: bool
    next_cursor: ChangeCursor | None = field(repr=False)
    accepted_at: datetime

    def __post_init__(self) -> None:
        _validate_acceptance_fields(
            source_ref=self.source_ref,
            source_version_ref=self.source_version_ref,
            page_ref=self.page_ref,
            checkpoint_ref=self.checkpoint_ref,
            sequence=self.sequence,
            change_count=self.change_count,
            complete=self.complete,
            accepted_at=self.accepted_at,
        )
        _require_sha256("accepted page scan_ref", self.scan_ref)
        if type(self.scan_epoch) is not UUID:
            raise TypeError("accepted page scan_epoch must be UUID")
        if (
            type(self.page_limit) is not int
            or not 1 <= self.page_limit <= MAX_FILE_CHANGE_PAGE_SIZE
        ):
            raise ValueError("accepted page page_limit is invalid")
        if (
            self.superseded_scan_epoch is not None
            and type(self.superseded_scan_epoch) is not UUID
        ):
            raise TypeError("accepted page superseded scan epoch is invalid")
        if self.next_cursor is not None and type(self.next_cursor) is not ChangeCursor:
            raise TypeError("accepted page next_cursor must be ChangeCursor or None")
        if self.complete is not (self.next_cursor is None):
            raise ValueError("accepted page completion and cursor disagree")

    @property
    def scan_head(self) -> FileChangeScanHead:
        """Project the just-committed page as the next trusted Provider input."""

        return FileChangeScanHead(
            source_version_ref=self.source_version_ref,
            scan_ref=self.scan_ref,
            scan_epoch=self.scan_epoch,
            page_limit=self.page_limit,
            page_ref=self.page_ref,
            checkpoint_ref=self.checkpoint_ref,
            sequence=self.sequence,
            complete=self.complete,
            superseded_scan_epoch=self.superseded_scan_epoch,
        )


def _require_checkpoint_ref(value: object) -> str:
    if (
        type(value) is not str
        or not value.startswith("facp_")
        or len(value) != 69
        or any(character not in "0123456789abcdef" for character in value[5:])
    ):
        raise ValueError("page acceptance checkpoint_ref is invalid")
    return value


def _validate_acceptance_fields(
    *,
    source_ref: object,
    source_version_ref: object,
    page_ref: object,
    checkpoint_ref: object,
    sequence: object,
    change_count: object,
    complete: object,
    accepted_at: object,
) -> None:
    if type(source_ref) is not SourceRef:
        raise TypeError("page acceptance source_ref must be SourceRef")
    if type(source_version_ref) is not UUID:
        raise TypeError("page acceptance source_version_ref must be UUID")
    _require_sha256("page acceptance page_ref", page_ref)
    _require_checkpoint_ref(checkpoint_ref)
    if type(sequence) is not int or not 1 <= sequence <= 2**63 - 1:
        raise ValueError("page acceptance sequence must be positive")
    if (
        type(change_count) is not int
        or not 0 <= change_count <= MAX_FILE_CHANGE_PAGE_SIZE
    ):
        raise ValueError("page acceptance change_count is invalid")
    if type(complete) is not bool:
        raise TypeError("page acceptance complete must be bool")
    _require_utc("page acceptance accepted_at", accepted_at)


_VERIFIED_PAGE_SEAL = object()


def _verified_page(
    page: ChangePage, page_ref: str, *, seal: object
) -> VerifiedChangePage:
    if seal is not _VERIFIED_PAGE_SEAL:
        raise TypeError("VerifiedChangePage is proof-constructed")
    value = object.__new__(VerifiedChangePage)
    object.__setattr__(value, "page", page)
    object.__setattr__(value, "page_ref", page_ref)
    return value


class FileChangeProviderProofs:
    """Provider-held page signer and Control-checkpoint public verifier."""

    __slots__ = ("_checkpoint_verification_key", "_provider_signing_key")

    def __init__(
        self,
        *,
        provider_signing_key: Ed25519PrivateKey,
        checkpoint_verification_key: Ed25519PublicKey,
    ) -> None:
        if not isinstance(provider_signing_key, Ed25519PrivateKey):
            raise TypeError("File change provider signing key is unavailable")
        if not isinstance(checkpoint_verification_key, Ed25519PublicKey):
            raise TypeError("File change checkpoint verification key is unavailable")
        self._provider_signing_key = provider_signing_key
        self._checkpoint_verification_key = checkpoint_verification_key

    def _sign_pending_payload(self, payload: bytes) -> bytes:
        return self._provider_signing_key.sign(payload)

    def _seal_page(self, page: ChangePage) -> str:
        return encode_base64url(self._provider_signing_key.sign(_page_payload(page)))

    def _unwrap_cursor(self, cursor: ChangeCursor) -> _AcceptedCursorClaims | None:
        try:
            encoded_payload, encoded_signature = cursor.value.split(".")
            payload = decode_base64url(encoded_payload)
            signature = decode_base64url(encoded_signature)
            self._checkpoint_verification_key.verify(signature, payload)
            document = json.loads(payload)
            if (
                type(document) is not dict
                or rfc8785.dumps(cast(Any, document)) != payload
                or set(document)
                != {
                    "checkpointRef",
                    "domain",
                    "organizationId",
                    "pageRef",
                    "pendingCursor",
                    "scanEpoch",
                    "scanRef",
                    "sequence",
                    "sourceId",
                    "sourceVersionId",
                    "version",
                }
                or document.get("domain") != _CHECKPOINT_CURSOR_DOMAIN
                or document.get("version") != 1
            ):
                return None
            page_ref = document.get("pageRef")
            scan_ref = document.get("scanRef")
            pending = document.get("pendingCursor")
            checkpoint_ref = document.get("checkpointRef")
            sequence = document.get("sequence")
            scan_epoch_value = document.get("scanEpoch")
            if type(scan_epoch_value) is not str:
                return None
            scan_epoch = UUID(scan_epoch_value)
            if str(scan_epoch) != scan_epoch_value:
                return None
            _require_sha256("accepted cursor page_ref", page_ref)
            _require_sha256("accepted cursor scan_ref", scan_ref)
            _require_checkpoint_ref(checkpoint_ref)
            if type(sequence) is not int or not 1 <= sequence <= 2**63 - 1:
                return None
            for field_name in (
                "organizationId",
                "sourceId",
                "sourceVersionId",
            ):
                field_value = document.get(field_name)
                if (
                    type(field_value) is not str
                    or str(UUID(field_value)) != field_value
                ):
                    return None
            return _AcceptedCursorClaims(
                pending_cursor=PendingChangeCursor(cast(str, pending)),
                scan_ref=cast(str, scan_ref),
                page_ref=cast(str, page_ref),
                checkpoint_ref=cast(str, checkpoint_ref),
                sequence=sequence,
                scan_epoch=scan_epoch,
            )
        except (
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            InvalidSignature,
        ):
            return None


class FileChangeControlProofs:
    """Control-held public verifier for untrusted Provider pages."""

    __slots__ = ("_provider_verification_key",)

    def __init__(
        self,
        *,
        provider_verification_key: Ed25519PublicKey,
    ) -> None:
        if not isinstance(provider_verification_key, Ed25519PublicKey):
            raise TypeError("File change provider verification key is unavailable")
        self._provider_verification_key = provider_verification_key

    def verify_page(self, page: ChangePage) -> VerifiedChangePage | None:
        if type(page) is not ChangePage:
            return None
        payload = _page_payload(page)
        try:
            provided = decode_base64url(page.provider_proof)
            self._provider_verification_key.verify(provided, payload)
        except (ValueError, InvalidSignature):
            return None
        page_ref = hashlib.sha256(payload).hexdigest()
        return _verified_page(page, page_ref, seal=_VERIFIED_PAGE_SEAL)


@dataclass(frozen=True, slots=True)
class ProviderOk[T]:
    """Provider completed the requested operation with one typed value."""

    value: T


@dataclass(frozen=True, slots=True)
class ProviderUnsupported:
    """The immutable SourceVersion does not declare this capability."""

    capability: str


@dataclass(frozen=True, slots=True)
class ProviderRetryableUnavailable:
    """The Provider could not produce a stable observation at this time."""

    retry_after: timedelta

    def __post_init__(self) -> None:
        if type(self.retry_after) is not timedelta or self.retry_after <= timedelta(0):
            raise ValueError("Provider retry_after must be positive")


@dataclass(frozen=True, slots=True)
class ProviderInvalidCheckpoint:
    """The opaque cursor is malformed, stale, foreign, or no longer retained."""


@dataclass(frozen=True, slots=True)
class ProviderGenericDenied:
    """One non-enumerating refusal for an unavailable source binding."""


FileChangeProviderOutcome = (
    ProviderOk[ChangePage]
    | ProviderUnsupported
    | ProviderRetryableUnavailable
    | ProviderInvalidCheckpoint
    | ProviderGenericDenied
)
