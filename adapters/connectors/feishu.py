"""Clean-room Feishu Docs connector on the closed Supply execution seam.

The module depends only on the repository-owned behavior contract.  It contains
no live Feishu transport; callers inject either the deterministic twin or a
future separately admitted public-API client.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from engine.supply import (
    ConnectorCheckpointBinding,
    SourceAclEvidenceClass,
    SourceAclObservation,
    SupplyChangePage,
    SupplyDocumentDeleteObservation,
    SupplyDocumentEnvelope,
    WorkerLeaseToken,
    deserialize_supply_change_page,
)

_CHECKPOINT_VERSION = 1
_ACL_ARTIFACT_VERSION = "feishu-acl-observation-v1"
_GROUP_ARTIFACT_VERSION = "feishu-group-flattening-v1"
_MAX_ITEMS_PER_PAGE = 100
FEISHU_DOCS_CAPABILITY_MANIFEST_JSON = (
    '{"aclEvidenceMode":"mirrored","authorizeAndProject":"unavailable",'
    '"batchLimits":"available","checkpoint":"available",'
    '"checkpointSemantics":"available","contentKinds":["markdown"],'
    '"consistencyGuarantees":"unavailable","cursorSemantics":"available",'
    '"declarationVersion":"feishu-docs-capabilities-v1",'
    '"deleteObservations":"available","deletion":"unavailable",'
    '"describeCapabilities":"available","discover":"unavailable",'
    '"fileSourceAccess":"unavailable","freshness":"available",'
    '"ingestionJobs":"available","liveNetwork":"not_active",'
    '"projectionFields":[],"readChanges":"available",'
    '"resourceKinds":["markdown_document"],"sourceMode":"materialized"}'
)


def _require_ref(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded opaque reference")
    return value


def _require_utc(field_name: str, value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field_name} must be an aware UTC datetime")
    return value


class FeishuPermissionKind(StrEnum):
    USER = "user"
    GROUP = "group"


class FeishuAclVisibility(StrEnum):
    PRIVATE = "private"
    ORGANIZATION = "organization"


class FeishuObservationStatus(StrEnum):
    RESOLVED = "resolved"
    FAILED = "failed"
    UNRESOLVED_GROUP = "unresolved_group"


class FeishuSourceError(RuntimeError):
    """Source response could not produce a trustworthy change page."""


class FeishuRateLimited(FeishuSourceError):
    """Bounded rate response; no partial page or checkpoint is emitted."""

    __slots__ = ("retry_after_seconds",)

    def __init__(self, retry_after_seconds: int) -> None:
        if type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 3600:
            raise ValueError("Feishu retry delay must be bounded")
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Feishu source is temporarily unavailable")


@dataclass(frozen=True, slots=True)
class FeishuDocument:
    """One synthetic/public-API document revision selected for ingestion."""

    document_ref: str = field(repr=False)
    revision_ref: str = field(repr=False)
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _require_ref("Feishu document", self.document_ref)
        _require_ref("Feishu revision", self.revision_ref)
        if type(self.content) is not bytes or not self.content:
            raise ValueError("Feishu document content must be nonempty bytes")


@dataclass(frozen=True, slots=True)
class FeishuDocumentDelete:
    """Source-side deletion with the time at which it was observed."""

    document_ref: str = field(repr=False)
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_ref("Feishu deleted document", self.document_ref)
        _require_utc("Feishu delete observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class FeishuPermissionSubject:
    kind: FeishuPermissionKind
    external_ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not FeishuPermissionKind:
            raise TypeError("Feishu permission subject kind must be closed")
        _require_ref("Feishu permission subject", self.external_ref)


@dataclass(frozen=True, slots=True)
class FeishuAclResponse:
    document_ref: str = field(repr=False)
    visibility: FeishuAclVisibility
    subjects: tuple[FeishuPermissionSubject, ...] = field(repr=False)
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_ref("Feishu ACL document", self.document_ref)
        if type(self.visibility) is not FeishuAclVisibility:
            raise TypeError("Feishu ACL visibility must be closed")
        if type(self.subjects) is not tuple or any(
            type(subject) is not FeishuPermissionSubject for subject in self.subjects
        ):
            raise TypeError("Feishu ACL subjects must be an exact tuple")
        refs = tuple(
            (subject.kind.value, subject.external_ref) for subject in self.subjects
        )
        if refs != tuple(sorted(set(refs))):
            raise ValueError("Feishu ACL subjects must be sorted and unique")
        _require_utc("Feishu ACL observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class FeishuAclFailure:
    """Timestamped strong-ACL observation failure; it can only isolate."""

    document_ref: str = field(repr=False)
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_ref("Feishu ACL failure document", self.document_ref)
        _require_utc("Feishu ACL failure observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class FeishuIdentityMapping:
    """Opaque source identity with an optional trusted local principal mapping."""

    external_ref: str = field(repr=False)
    local_principal_ref: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_ref("Feishu identity", self.external_ref)
        if self.local_principal_ref is not None:
            _require_ref("local principal", self.local_principal_ref)

    @property
    def opaque(self) -> bool:
        return self.local_principal_ref is None


@dataclass(frozen=True, slots=True)
class FeishuGroupNode:
    external_ref: str = field(repr=False)
    local_group_ref: str | None = field(default=None, repr=False)
    identity_refs: tuple[str, ...] = field(default=(), repr=False)
    child_group_refs: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _require_ref("Feishu group", self.external_ref)
        if self.local_group_ref is not None:
            _require_ref("local group", self.local_group_ref)
        for values, label in (
            (self.identity_refs, "Feishu group identities"),
            (self.child_group_refs, "Feishu child groups"),
        ):
            if type(values) is not tuple:
                raise TypeError(f"{label} must be an exact tuple")
            for value in values:
                _require_ref(label, value)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")


@dataclass(frozen=True, slots=True)
class FeishuGroupSnapshot:
    """Versioned group graph used to produce a reproducible flattening artifact."""

    version_ref: str = field(repr=False)
    nodes: tuple[FeishuGroupNode, ...] = field(repr=False)
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_ref("Feishu group snapshot version", self.version_ref)
        if type(self.nodes) is not tuple or any(
            type(node) is not FeishuGroupNode for node in self.nodes
        ):
            raise TypeError("Feishu group snapshot nodes must be an exact tuple")
        refs = tuple(node.external_ref for node in self.nodes)
        if refs != tuple(sorted(set(refs))):
            raise ValueError("Feishu group nodes must be sorted and unique")
        _require_utc("Feishu group snapshot observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class FeishuChangePage:
    documents: tuple[FeishuDocument, ...]
    deleted_document_refs: tuple[FeishuDocumentDelete, ...]
    next_page_token: str | None
    checkpoint_token: str

    def __post_init__(self) -> None:
        if type(self.documents) is not tuple or any(
            type(document) is not FeishuDocument for document in self.documents
        ):
            raise TypeError("Feishu documents must be an exact tuple")
        if type(self.deleted_document_refs) is not tuple:
            raise TypeError("Feishu deletes must be an exact tuple")
        if any(
            type(deletion) is not FeishuDocumentDelete
            for deletion in self.deleted_document_refs
        ):
            raise TypeError("Feishu deletes must carry exact observations")
        for deletion in self.deleted_document_refs:
            deletion.__post_init__()
        emitted_refs = tuple(document.document_ref for document in self.documents)
        deleted_refs = tuple(
            deletion.document_ref for deletion in self.deleted_document_refs
        )
        if (
            len(self.documents) + len(deleted_refs) > _MAX_ITEMS_PER_PAGE
            or emitted_refs != tuple(sorted(set(emitted_refs)))
            or deleted_refs != tuple(sorted(set(deleted_refs)))
            or set(emitted_refs).intersection(deleted_refs)
        ):
            raise ValueError(
                "Feishu page identities must be bounded, sorted, and disjoint"
            )
        if self.next_page_token is not None:
            _require_ref("Feishu next page token", self.next_page_token)
        _require_ref("Feishu checkpoint token", self.checkpoint_token)


class FeishuDocsClient(Protocol):
    """Closed public behavior surface; it intentionally has no generic fetch."""

    policy_epoch: int

    def read_changes(self, page_token: str | None) -> FeishuChangePage: ...

    def observe_acl(
        self,
        document_ref: str,
    ) -> FeishuAclResponse | FeishuAclFailure: ...

    def map_identity(self, external_ref: str) -> FeishuIdentityMapping: ...

    def group_snapshot(self) -> FeishuGroupSnapshot: ...


class DeterministicFeishuTwin:
    """Credential-free fixture client used by the admitted offline runner mode."""

    __slots__ = (
        "_acl_responses",
        "_group_snapshot",
        "_identity_mappings",
        "_pages",
        "policy_epoch",
    )

    def __init__(self, fixture_payload: bytes, *, policy_epoch: int) -> None:
        if type(policy_epoch) is not int or policy_epoch < 1:
            raise ValueError("Feishu twin Policy Epoch must be positive")
        if type(fixture_payload) is not bytes or not 1 <= len(fixture_payload) <= 2**20:
            raise ValueError("Feishu twin fixture must be bounded bytes")
        try:
            raw = json.loads(fixture_payload)
            if type(raw) is not dict or set(raw) != {
                "acl_responses",
                "group_snapshot",
                "identity_mappings",
                "pages",
                "schema_version",
            }:
                raise ValueError
            if raw["schema_version"] != "feishu-docs-twin-v2":
                raise ValueError
            if any(
                type(raw[field_name]) is not list
                for field_name in ("acl_responses", "identity_mappings", "pages")
            ):
                raise ValueError
            pages = tuple(_parse_twin_page(item) for item in raw["pages"])
            acl_responses = tuple(
                _parse_twin_acl(item) for item in raw["acl_responses"]
            )
            identity_mappings = tuple(
                _parse_twin_identity_mapping(item) for item in raw["identity_mappings"]
            )
            group_snapshot = _parse_twin_group_snapshot(raw["group_snapshot"])
        except (
            KeyError,
            TypeError,
            ValueError,
            binascii.Error,
            json.JSONDecodeError,
        ):
            raise ValueError("Feishu twin fixture is unavailable") from None
        page_tokens = tuple(token for token, _page in pages)
        acl_refs = tuple(document_ref for document_ref, _response in acl_responses)
        identity_refs = tuple(
            external_ref for external_ref, _mapping in identity_mappings
        )
        if (
            page_tokens
            != tuple(sorted(set(page_tokens), key=lambda value: value or ""))
            or acl_refs != tuple(sorted(set(acl_refs)))
            or identity_refs != tuple(sorted(set(identity_refs)))
        ):
            raise ValueError("Feishu twin fixture is unavailable")
        self.policy_epoch = policy_epoch
        self._pages = dict(pages)
        self._acl_responses = dict(acl_responses)
        self._identity_mappings = dict(identity_mappings)
        self._group_snapshot = group_snapshot

    def read_changes(self, page_token: str | None) -> FeishuChangePage:
        page = self._pages.get(page_token)
        if page is None:
            raise FeishuSourceError("synthetic Feishu page is unavailable")
        if isinstance(page, FeishuSourceError):
            raise page
        return page

    def observe_acl(self, document_ref: str) -> FeishuAclResponse | FeishuAclFailure:
        response = self._acl_responses.get(document_ref)
        if response is None:
            raise FeishuSourceError("synthetic Feishu ACL is unavailable")
        if isinstance(response, FeishuSourceError):
            raise response
        return response

    def map_identity(self, external_ref: str) -> FeishuIdentityMapping:
        mapping = self._identity_mappings.get(
            external_ref, FeishuIdentityMapping(external_ref)
        )
        if isinstance(mapping, FeishuSourceError):
            raise mapping
        return mapping

    def group_snapshot(self) -> FeishuGroupSnapshot:
        if isinstance(self._group_snapshot, FeishuSourceError):
            raise self._group_snapshot
        return self._group_snapshot


def _twin_datetime(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError
    parsed = datetime.fromisoformat(value)
    return _require_utc("Feishu twin timestamp", parsed)


def _parse_twin_page(
    value: object,
) -> tuple[str | None, FeishuChangePage | FeishuSourceError]:
    if type(value) is not dict:
        raise ValueError
    page_token = value.get("page_token")
    if page_token is not None:
        _require_ref("Feishu twin page token", page_token)
    outcome = value.get("outcome")
    if outcome == "rate_limited":
        if set(value) != {"outcome", "page_token", "retry_after_seconds"}:
            raise ValueError
        return cast(str | None, page_token), FeishuRateLimited(
            value["retry_after_seconds"]
        )
    if outcome == "source_error":
        if set(value) != {"outcome", "page_token"}:
            raise ValueError
        return cast(str | None, page_token), FeishuSourceError(
            "synthetic Feishu page is unavailable"
        )
    if outcome != "page" or set(value) != {
        "checkpoint_token",
        "deleted_documents",
        "documents",
        "next_page_token",
        "outcome",
        "page_token",
    }:
        raise ValueError
    if (
        type(value["documents"]) is not list
        or type(value["deleted_documents"]) is not list
    ):
        raise ValueError
    documents = tuple(
        FeishuDocument(
            item["document_ref"],
            item["revision_ref"],
            base64.b64decode(item["content"], validate=True),
        )
        for item in value["documents"]
        if type(item) is dict
        and set(item) == {"content", "document_ref", "revision_ref"}
    )
    if len(documents) != len(value["documents"]):
        raise ValueError
    deletions = tuple(
        FeishuDocumentDelete(
            item["document_ref"],
            _twin_datetime(item["observed_at"]),
        )
        for item in value["deleted_documents"]
        if type(item) is dict and set(item) == {"document_ref", "observed_at"}
    )
    if len(deletions) != len(value["deleted_documents"]):
        raise ValueError
    next_page_token = value["next_page_token"]
    if next_page_token is not None:
        _require_ref("Feishu twin next page token", next_page_token)
    return cast(str | None, page_token), FeishuChangePage(
        documents,
        deletions,
        cast(str | None, next_page_token),
        cast(str, value["checkpoint_token"]),
    )


def _parse_twin_acl(
    value: object,
) -> tuple[str, FeishuAclResponse | FeishuAclFailure | FeishuSourceError]:
    if type(value) is not dict:
        raise ValueError
    document_ref = _require_ref(
        "Feishu twin ACL document", value.get("document_ref")
    )
    outcome = value.get("outcome")
    if outcome == "rate_limited":
        if set(value) != {"document_ref", "outcome", "retry_after_seconds"}:
            raise ValueError
        return document_ref, FeishuRateLimited(value["retry_after_seconds"])
    if outcome == "source_error":
        if set(value) != {"document_ref", "outcome"}:
            raise ValueError
        return document_ref, FeishuSourceError(
            "synthetic Feishu ACL is unavailable"
        )
    if outcome != "observation" or set(value) != {
        "document_ref",
        "observed_at",
        "outcome",
        "status",
        "subjects",
        "visibility",
    }:
        raise ValueError
    if type(value["subjects"]) is not list:
        raise ValueError
    observed_at = _twin_datetime(value["observed_at"])
    if value["status"] == "failed":
        if value["subjects"] != [] or value["visibility"] is not None:
            raise ValueError
        return document_ref, FeishuAclFailure(document_ref, observed_at)
    if value["status"] != "resolved":
        raise ValueError
    subjects = tuple(
        FeishuPermissionSubject(
            FeishuPermissionKind(item["kind"]),
            item["external_ref"],
        )
        for item in value["subjects"]
        if type(item) is dict and set(item) == {"external_ref", "kind"}
    )
    if len(subjects) != len(value["subjects"]):
        raise ValueError
    return (
        document_ref,
        FeishuAclResponse(
            document_ref,
            FeishuAclVisibility(value["visibility"]),
            subjects,
            observed_at,
        ),
    )


def _parse_twin_identity_mapping(
    value: object,
) -> tuple[str, FeishuIdentityMapping | FeishuSourceError]:
    if type(value) is not dict:
        raise ValueError
    external_ref = _require_ref(
        "Feishu twin identity", value.get("external_ref")
    )
    outcome = value.get("outcome")
    if outcome == "source_error":
        if set(value) != {"external_ref", "outcome"}:
            raise ValueError
        return external_ref, FeishuSourceError(
            "synthetic Feishu identity mapping is unavailable"
        )
    if outcome != "mapping" or set(value) != {
        "external_ref",
        "local_principal_ref",
        "outcome",
    }:
        raise ValueError
    return external_ref, FeishuIdentityMapping(
        external_ref, value["local_principal_ref"]
    )


def _parse_twin_group_snapshot(
    value: object,
) -> FeishuGroupSnapshot | FeishuSourceError:
    if type(value) is not dict:
        raise ValueError
    outcome = value.get("outcome")
    if outcome == "source_error":
        if set(value) != {"outcome"}:
            raise ValueError
        return FeishuSourceError("synthetic Feishu group snapshot is unavailable")
    if outcome != "snapshot" or set(value) != {
        "nodes",
        "observed_at",
        "outcome",
        "version_ref",
    }:
        raise ValueError
    if type(value["nodes"]) is not list or any(
        type(item) is not dict
        or set(item)
        != {
            "child_group_refs",
            "external_ref",
            "identity_refs",
            "local_group_ref",
        }
        or type(item["identity_refs"]) is not list
        or type(item["child_group_refs"]) is not list
        for item in value["nodes"]
    ):
        raise ValueError
    nodes = tuple(
        FeishuGroupNode(
            item["external_ref"],
            item["local_group_ref"],
            tuple(item["identity_refs"]),
            tuple(item["child_group_refs"]),
        )
        for item in value["nodes"]
        if type(item) is dict
    )
    if len(nodes) != len(value["nodes"]):
        raise ValueError
    return FeishuGroupSnapshot(
        value["version_ref"],
        nodes,
        _twin_datetime(value["observed_at"]),
    )


def serialize_feishu_twin_fixture(
    *,
    pages: Mapping[str | None, FeishuChangePage | FeishuSourceError],
    acl_responses: Mapping[
        str, FeishuAclResponse | FeishuAclFailure | FeishuSourceError
    ],
    identity_mappings: Mapping[str, FeishuIdentityMapping | FeishuSourceError],
    group_snapshot: FeishuGroupSnapshot | FeishuSourceError,
) -> bytes:
    """Serialize exact synthetic source facts for the credential-free runner."""

    payload = {
        "acl_responses": [
            _serialize_twin_acl(ref, response)
            for ref, response in sorted(acl_responses.items())
        ],
        "group_snapshot": _serialize_twin_group_snapshot(group_snapshot),
        "identity_mappings": [
            _serialize_twin_identity_mapping(ref, mapping)
            for ref, mapping in sorted(identity_mappings.items())
        ],
        "pages": [
            _serialize_twin_page(token, page)
            for token, page in sorted(pages.items(), key=lambda item: item[0] or "")
        ],
        "schema_version": "feishu-docs-twin-v2",
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _serialize_twin_failure(
    response: FeishuSourceError,
    *,
    binding: dict[str, object],
) -> dict[str, object]:
    if type(response) is FeishuRateLimited:
        return {
            **binding,
            "outcome": "rate_limited",
            "retry_after_seconds": response.retry_after_seconds,
        }
    if type(response) is FeishuSourceError:
        return {**binding, "outcome": "source_error"}
    raise TypeError("Feishu twin failure must be exact")


def _serialize_twin_page(
    page_token: str | None,
    page: FeishuChangePage | FeishuSourceError,
) -> dict[str, object]:
    if isinstance(page, FeishuSourceError):
        return _serialize_twin_failure(page, binding={"page_token": page_token})
    if type(page) is not FeishuChangePage:
        raise TypeError("Feishu twin page must be exact")
    return {
        "checkpoint_token": page.checkpoint_token,
        "deleted_documents": [
            {
                "document_ref": deletion.document_ref,
                "observed_at": deletion.observed_at.isoformat(),
            }
            for deletion in page.deleted_document_refs
        ],
        "documents": [
            {
                "content": base64.b64encode(document.content).decode("ascii"),
                "document_ref": document.document_ref,
                "revision_ref": document.revision_ref,
            }
            for document in page.documents
        ],
        "next_page_token": page.next_page_token,
        "outcome": "page",
        "page_token": page_token,
    }


def _serialize_twin_acl(
    document_ref: str,
    response: FeishuAclResponse | FeishuAclFailure | FeishuSourceError,
) -> dict[str, object]:
    if isinstance(response, FeishuSourceError):
        return _serialize_twin_failure(
            response,
            binding={"document_ref": document_ref},
        )
    if type(response) is FeishuAclFailure:
        if response.document_ref != document_ref:
            raise ValueError("Feishu twin ACL binding is unavailable")
        return {
            "document_ref": response.document_ref,
            "observed_at": response.observed_at.isoformat(),
            "outcome": "observation",
            "status": "failed",
            "subjects": [],
            "visibility": None,
        }
    if type(response) is not FeishuAclResponse:
        raise TypeError("Feishu twin ACL must be exact")
    if response.document_ref != document_ref:
        raise ValueError("Feishu twin ACL binding is unavailable")
    return {
        "document_ref": response.document_ref,
        "observed_at": response.observed_at.isoformat(),
        "outcome": "observation",
        "status": "resolved",
        "subjects": [
            {
                "external_ref": subject.external_ref,
                "kind": subject.kind.value,
            }
            for subject in response.subjects
        ],
        "visibility": response.visibility.value,
    }


def _serialize_twin_identity_mapping(
    external_ref: str,
    mapping: FeishuIdentityMapping | FeishuSourceError,
) -> dict[str, object]:
    if type(mapping) is FeishuSourceError:
        return _serialize_twin_failure(
            mapping,
            binding={"external_ref": external_ref},
        )
    if type(mapping) is not FeishuIdentityMapping:
        raise TypeError("Feishu twin identity mapping must be exact")
    if mapping.external_ref != external_ref:
        raise ValueError("Feishu twin identity binding is unavailable")
    return {
        "external_ref": mapping.external_ref,
        "local_principal_ref": mapping.local_principal_ref,
        "outcome": "mapping",
    }


def _serialize_twin_group_snapshot(
    snapshot: FeishuGroupSnapshot | FeishuSourceError,
) -> dict[str, object]:
    if type(snapshot) is FeishuSourceError:
        return _serialize_twin_failure(snapshot, binding={})
    if type(snapshot) is not FeishuGroupSnapshot:
        raise TypeError("Feishu twin group snapshot must be exact")
    return {
        "nodes": [
            {
                "child_group_refs": list(node.child_group_refs),
                "external_ref": node.external_ref,
                "identity_refs": list(node.identity_refs),
                "local_group_ref": node.local_group_ref,
            }
            for node in snapshot.nodes
        ],
        "observed_at": snapshot.observed_at.isoformat(),
        "outcome": "snapshot",
        "version_ref": snapshot.version_ref,
    }


class FeishuConnectorProcessAdapter:
    """Execute the admitted deterministic Feishu twin in the connector runner."""

    __slots__ = (
        "_checkpoint",
        "_fixture_payload",
        "_idempotency_key",
        "_policy_epoch",
        "_service_actor_expires_at",
        "_service_principal_id",
        "_worker_lease",
    )

    def __init__(
        self,
        fixture_payload: bytes,
        *,
        policy_epoch: int,
        worker_lease: WorkerLeaseToken,
        service_principal_id: UUID,
        idempotency_key: str,
        service_actor_expires_at: datetime,
    ) -> None:
        if type(fixture_payload) is not bytes or not 1 <= len(fixture_payload) <= 2**20:
            raise ValueError("Feishu twin fixture must be bounded bytes")
        if type(policy_epoch) is not int or policy_epoch < 1:
            raise ValueError("Feishu connector process Policy Epoch must be positive")
        if type(worker_lease) is not WorkerLeaseToken:
            raise TypeError("Feishu connector process requires WorkerLeaseToken")
        if type(service_principal_id) is not UUID:
            raise TypeError("Feishu connector process requires ServiceActor UUID")
        if (
            type(idempotency_key) is not str
            or len(idempotency_key) != 64
            or any(character not in "0123456789abcdef" for character in idempotency_key)
        ):
            raise ValueError("Feishu connector process requires an idempotency digest")
        _require_utc("Feishu connector actor expiry", service_actor_expires_at)
        self._fixture_payload = fixture_payload
        self._policy_epoch = policy_epoch
        self._worker_lease = worker_lease
        self._service_principal_id = service_principal_id
        self._idempotency_key = idempotency_key
        self._service_actor_expires_at = service_actor_expires_at
        self._checkpoint: bytes | None = None

    def load_checkpoint(self, opaque_checkpoint: bytes | None) -> None:
        if opaque_checkpoint is not None:
            decode_feishu_checkpoint(opaque_checkpoint)
        self._checkpoint = opaque_checkpoint

    def load(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        return self._run(binding)

    def poll(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        return self._run(binding)

    def _run(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        if type(binding) is not ConnectorCheckpointBinding:
            raise TypeError("Feishu connector process requires exact binding")
        request = json.dumps(
            {
                "fixture_payload": base64.b64encode(self._fixture_payload).decode(
                    "ascii"
                ),
                "idempotency_key": self._idempotency_key,
                "opaque_checkpoint": (
                    None
                    if self._checkpoint is None
                    else base64.b64encode(self._checkpoint).decode("ascii")
                ),
                "organization_id": str(binding.organization_id),
                "policy_epoch": self._policy_epoch,
                "service_actor_expires_at": self._service_actor_expires_at.isoformat(),
                "service_principal_id": str(self._service_principal_id),
                "source_version_id": str(binding.source_version_id),
                "worker_job_id": str(binding.worker_job_id),
                "worker_lease": self._worker_lease.serialize(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "applications.connector_runner",
                    "--run-feishu-twin",
                ],
                input=request,
                capture_output=True,
                check=False,
                env={
                    "PATH": os.defpath,
                    "PYTHONPATH": os.pathsep.join(sys.path),
                    "PYTHONUTF8": "1",
                },
                timeout=30.0,
            )
        except Exception:
            raise RuntimeError("Feishu connector process is unavailable") from None
        if completed.returncode != 0 or completed.stderr:
            raise RuntimeError("Feishu connector process is unavailable")
        try:
            page = deserialize_supply_change_page(completed.stdout)
        except ValueError:
            raise RuntimeError(
                "Feishu connector process output is unavailable"
            ) from None
        if page.binding != binding:
            raise RuntimeError("Feishu connector process binding is unavailable")
        return page


@dataclass(frozen=True, slots=True)
class FeishuCheckpoint:
    page_token: str | None


def encode_feishu_checkpoint(checkpoint: FeishuCheckpoint) -> bytes:
    if type(checkpoint) is not FeishuCheckpoint:
        raise TypeError("Feishu checkpoint requires an exact value")
    return json.dumps(
        {"page_token": checkpoint.page_token, "version": _CHECKPOINT_VERSION},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def decode_feishu_checkpoint(payload: bytes) -> FeishuCheckpoint:
    if type(payload) is not bytes or not payload:
        raise ValueError("Feishu checkpoint is unavailable")
    try:
        decoded = json.loads(payload)
        if type(decoded) is not dict or set(decoded) != {"page_token", "version"}:
            raise ValueError
        if decoded["version"] != _CHECKPOINT_VERSION:
            raise ValueError
        page_token = decoded["page_token"]
        if page_token is not None:
            _require_ref("Feishu checkpoint token", page_token)
        return FeishuCheckpoint(cast(str | None, page_token))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("Feishu checkpoint is unavailable") from None


@dataclass(frozen=True, slots=True)
class FeishuFlattenedGroupArtifact:
    version_ref: str
    digest: str
    canonical_graph: str
    requested_group_refs: tuple[str, ...]
    local_group_refs: tuple[str, ...]
    local_principal_refs: tuple[str, ...]
    group_mapping_claims: tuple[tuple[str, str], ...]
    identity_mapping_claims: tuple[tuple[str, str], ...]
    mapped_identity_refs: tuple[str, ...]
    opaque_identity_refs: tuple[str, ...]
    unresolved_group_refs: tuple[str, ...]

    @property
    def resolved(self) -> bool:
        return not self.unresolved_group_refs


def flatten_feishu_groups(
    snapshot: FeishuGroupSnapshot,
    requested_group_refs: tuple[str, ...],
    identity_mapper: Callable[[str], FeishuIdentityMapping],
) -> FeishuFlattenedGroupArtifact:
    """Flatten nested groups once, recording cycles without granting through them."""

    if type(snapshot) is not FeishuGroupSnapshot:
        raise TypeError("Feishu flattening requires a group snapshot")
    snapshot.__post_init__()
    if type(requested_group_refs) is not tuple:
        raise TypeError("requested Feishu groups must be an exact tuple")
    nodes = {node.external_ref: node for node in snapshot.nodes}
    local_groups: set[str] = set()
    local_principals: set[str] = set()
    identity_mapping_claims: dict[str, str] = {}
    opaque_identities: set[str] = set()
    unresolved: set[str] = set()

    def visit(group_ref: str, path: frozenset[str]) -> None:
        node = nodes.get(group_ref)
        if node is None:
            unresolved.add(group_ref)
            return
        if node.local_group_ref is None:
            unresolved.add(group_ref)
        else:
            local_groups.add(node.local_group_ref)
        if group_ref in path:
            return
        next_path = path | {group_ref}
        for identity_ref in node.identity_refs:
            mapping = identity_mapper(identity_ref)
            if (
                type(mapping) is not FeishuIdentityMapping
                or mapping.external_ref != identity_ref
            ):
                raise FeishuSourceError("Feishu identity mapping is unavailable")
            if mapping.opaque:
                opaque_identities.add(identity_ref)
            else:
                assert mapping.local_principal_ref is not None
                local_principals.add(mapping.local_principal_ref)
                identity_mapping_claims[identity_ref] = mapping.local_principal_ref
        for child_ref in node.child_group_refs:
            visit(child_ref, next_path)

    for group_ref in requested_group_refs:
        _require_ref("requested Feishu group", group_ref)
        visit(group_ref, frozenset())

    canonical_graph = {
        "nodes": [
            {
                "child_group_refs": list(node.child_group_refs),
                "external_ref": node.external_ref,
                "identity_refs": list(node.identity_refs),
                "local_group_ref": node.local_group_ref,
            }
            for node in snapshot.nodes
        ],
        "observed_at": snapshot.observed_at.isoformat(),
        "version_ref": snapshot.version_ref,
    }
    canonical_graph_json = json.dumps(
        canonical_graph,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical_graph_json.encode("ascii")).hexdigest()
    reachable_group_refs = tuple(
        sorted(
            external_ref
            for external_ref, node in nodes.items()
            if node.local_group_ref in local_groups
        )
    )
    return FeishuFlattenedGroupArtifact(
        version_ref=snapshot.version_ref,
        digest=digest,
        canonical_graph=canonical_graph_json,
        requested_group_refs=requested_group_refs,
        local_group_refs=tuple(sorted(local_groups)),
        local_principal_refs=tuple(sorted(local_principals)),
        group_mapping_claims=tuple(
            (external_ref, nodes[external_ref].local_group_ref or "")
            for external_ref in reachable_group_refs
        ),
        identity_mapping_claims=tuple(sorted(identity_mapping_claims.items())),
        mapped_identity_refs=tuple(sorted(identity_mapping_claims)),
        opaque_identity_refs=tuple(sorted(opaque_identities)),
        unresolved_group_refs=tuple(sorted(unresolved)),
    )


class FeishuDocsConnectorAdapter:
    """Translate one injected Feishu page into the CE-owned Supply contracts."""

    __slots__ = ("_checkpoint", "_client", "emitted_pages")

    def __init__(self, client: FeishuDocsClient) -> None:
        if (
            type(getattr(client, "policy_epoch", None)) is not int
            or client.policy_epoch < 1
        ):
            raise ValueError("Feishu connector requires a positive Policy Epoch")
        for operation in (
            "read_changes",
            "observe_acl",
            "map_identity",
            "group_snapshot",
        ):
            if not callable(getattr(client, operation, None)):
                raise TypeError("Feishu connector requires the closed client surface")
        self._client = client
        self._checkpoint: FeishuCheckpoint | None = None
        self.emitted_pages: list[SupplyChangePage] = []

    @classmethod
    def from_twin(cls, twin: FeishuDocsClient) -> FeishuDocsConnectorAdapter:
        return cls(twin)

    def load_checkpoint(self, opaque_checkpoint: bytes | None) -> None:
        self._checkpoint = (
            FeishuCheckpoint(None)
            if opaque_checkpoint is None
            else decode_feishu_checkpoint(opaque_checkpoint)
        )

    def load(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        return self._run(binding)

    def poll(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        return self._run(binding)

    def _run(self, binding: ConnectorCheckpointBinding) -> SupplyChangePage:
        if type(binding) is not ConnectorCheckpointBinding:
            raise TypeError("Feishu connector requires an exact checkpoint binding")
        if self._checkpoint is None:
            raise RuntimeError("Feishu connector checkpoint was not loaded")
        source_page = self._client.read_changes(self._checkpoint.page_token)
        if type(source_page) is not FeishuChangePage:
            raise FeishuSourceError("Feishu change response is unavailable")
        source_page.__post_init__()
        documents: list[SupplyDocumentEnvelope] = []
        deletes: list[SupplyDocumentDeleteObservation] = []
        for document in source_page.documents:
            acl, metadata = self._observe(binding, document.document_ref)
            documents.append(
                SupplyDocumentEnvelope(
                    organization_id=binding.organization_id,
                    source_version_id=binding.source_version_id,
                    worker_job_id=binding.worker_job_id,
                    document_ref=document.document_ref,
                    content=document.content,
                    content_type="text/markdown",
                    acl_observation=acl,
                    metadata=(
                        (
                            "acl_artifact_sha256",
                            hashlib.sha256(acl.evidence_payload or b"").hexdigest(),
                        ),
                        ("connector", "feishu_docs"),
                        ("source_revision", document.revision_ref),
                    ),
                )
            )
        for deletion in source_page.deleted_document_refs:
            acl, _metadata = self._failed_observation(
                binding,
                deletion.document_ref,
                deletion.observed_at,
            )
            deletes.append(
                SupplyDocumentDeleteObservation(
                    document_ref=deletion.document_ref,
                    acl_observation=acl,
                )
            )
        checkpoint = encode_feishu_checkpoint(
            FeishuCheckpoint(source_page.next_page_token)
        )
        page = SupplyChangePage(
            binding=binding,
            page_ref=_feishu_page_ref(
                binding,
                self._checkpoint.page_token,
                source_page.checkpoint_token,
            ),
            documents=tuple(documents),
            deleted_document_refs=tuple(deletes),
            checkpoint_proposal=checkpoint,
            terminal=source_page.next_page_token is None,
        )
        self.emitted_pages.append(page)
        return page

    def _observe(
        self,
        binding: ConnectorCheckpointBinding,
        document_ref: str,
    ) -> tuple[SourceAclObservation, tuple[tuple[str, str], ...]]:
        response = self._client.observe_acl(document_ref)
        if type(response) not in {FeishuAclResponse, FeishuAclFailure}:
            raise FeishuSourceError("Feishu ACL observation is unavailable")
        if response.document_ref != document_ref:
            raise FeishuSourceError("Feishu ACL observation is unavailable")
        response.__post_init__()
        if type(response) is FeishuAclFailure:
            return self._failed_observation(
                binding,
                document_ref,
                response.observed_at,
            )
        if type(response) is not FeishuAclResponse:
            raise FeishuSourceError("Feishu ACL observation is unavailable")
        try:
            user_refs = tuple(
                subject.external_ref
                for subject in response.subjects
                if subject.kind is FeishuPermissionKind.USER
            )
            group_refs = tuple(
                subject.external_ref
                for subject in response.subjects
                if subject.kind is FeishuPermissionKind.GROUP
            )
            mapped_principals: set[str] = set()
            identity_mapping_claims: dict[str, str] = {}
            opaque_identities: set[str] = set()
            for identity_ref in user_refs:
                mapping = self._client.map_identity(identity_ref)
                if (
                    type(mapping) is not FeishuIdentityMapping
                    or mapping.external_ref != identity_ref
                ):
                    raise FeishuSourceError(
                        "Feishu identity mapping is unavailable"
                    )
                if mapping.opaque:
                    opaque_identities.add(identity_ref)
                else:
                    assert mapping.local_principal_ref is not None
                    mapped_principals.add(mapping.local_principal_ref)
                    identity_mapping_claims[identity_ref] = (
                        mapping.local_principal_ref
                    )
            flattened = flatten_feishu_groups(
                self._client.group_snapshot(),
                group_refs,
                self._client.map_identity,
            )
        except (FeishuSourceError, TypeError, ValueError):
            return self._failed_observation(
                binding,
                document_ref,
                response.observed_at,
            )
        opaque_identities.update(flattened.opaque_identity_refs)
        mapped_principals.update(flattened.local_principal_refs)
        identity_mapping_claims.update(dict(flattened.identity_mapping_claims))
        if flattened.unresolved_group_refs:
            status = FeishuObservationStatus.UNRESOLVED_GROUP
            policy_kind = None
            local_group_refs: tuple[str, ...] = ()
            mapped_principals.clear()
        elif response.visibility is FeishuAclVisibility.ORGANIZATION:
            status = FeishuObservationStatus.RESOLVED
            policy_kind = "organization"
            local_group_refs = ()
        elif flattened.local_group_refs:
            status = FeishuObservationStatus.RESOLVED
            policy_kind = "groups"
            local_group_refs = flattened.local_group_refs
        else:
            status = FeishuObservationStatus.RESOLVED
            policy_kind = "private"
            local_group_refs = ()
        return self._source_observation(
            binding=binding,
            document_ref=document_ref,
            observed_at=response.observed_at,
            flattened=flattened,
            status=status,
            policy_kind=policy_kind,
            local_group_refs=local_group_refs,
            mapped_principals=mapped_principals,
            opaque_identities=opaque_identities,
            identity_mapping_claims=identity_mapping_claims,
        )

    def _failed_observation(
        self,
        binding: ConnectorCheckpointBinding,
        document_ref: str,
        observed_at: datetime,
    ) -> tuple[SourceAclObservation, tuple[tuple[str, str], ...]]:
        empty_graph = FeishuGroupSnapshot("failure:no-group-read", (), observed_at)
        return self._source_observation(
            binding=binding,
            document_ref=document_ref,
            observed_at=observed_at,
            flattened=flatten_feishu_groups(
                empty_graph,
                (),
                FeishuIdentityMapping,
            ),
            status=FeishuObservationStatus.FAILED,
            policy_kind=None,
            local_group_refs=(),
            mapped_principals=set(),
            opaque_identities=set(),
            identity_mapping_claims={},
        )

    def _source_observation(
        self,
        *,
        binding: ConnectorCheckpointBinding,
        document_ref: str,
        observed_at: datetime,
        flattened: FeishuFlattenedGroupArtifact,
        status: FeishuObservationStatus,
        policy_kind: str | None,
        local_group_refs: tuple[str, ...],
        mapped_principals: set[str],
        opaque_identities: set[str],
        identity_mapping_claims: dict[str, str],
    ) -> tuple[SourceAclObservation, tuple[tuple[str, str], ...]]:
        artifact = {
            "document_ref": document_ref,
            "flattening": {
                "artifact_version": _GROUP_ARTIFACT_VERSION,
                "canonical_graph": flattened.canonical_graph,
                "digest": flattened.digest,
                "group_mapping_claims": [
                    list(claim) for claim in flattened.group_mapping_claims
                ],
                "identity_mapping_claims": [
                    list(claim) for claim in sorted(identity_mapping_claims.items())
                ],
                "direct_identity_refs": sorted(
                    identity_mapping_claims.keys() | opaque_identities
                ),
                "local_group_refs": list(local_group_refs),
                "local_principal_refs": sorted(mapped_principals),
                "mapped_identity_refs": sorted(identity_mapping_claims),
                "opaque_identity_refs": sorted(opaque_identities),
                "requested_group_refs": list(flattened.requested_group_refs),
                "snapshot_version_ref": flattened.version_ref,
                "unresolved_group_refs": list(flattened.unresolved_group_refs),
            },
            "policy_kind": policy_kind,
            "schema_version": _ACL_ARTIFACT_VERSION,
            "status": status.value,
        }
        payload = json.dumps(
            artifact,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return (
            SourceAclObservation(
                organization_id=binding.organization_id,
                observed_at=observed_at,
                policy_epoch=self._client.policy_epoch,
                evidence_class=SourceAclEvidenceClass.MIRRORED,
                evidence_payload=payload,
            ),
            (("group_artifact_sha256", flattened.digest),),
        )


def _feishu_page_ref(
    binding: ConnectorCheckpointBinding,
    prior_token: str | None,
    next_token: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(binding.organization_id.bytes)
    digest.update(binding.source_version_id.bytes)
    digest.update(binding.worker_job_id.bytes)
    digest.update((prior_token or "initial").encode("utf-8"))
    digest.update(next_token.encode("utf-8"))
    return f"feishu-page:{digest.hexdigest()}"


__all__ = [
    "DeterministicFeishuTwin",
    "FeishuAclResponse",
    "FeishuAclFailure",
    "FeishuAclVisibility",
    "FeishuChangePage",
    "FeishuCheckpoint",
    "FeishuDocsClient",
    "FeishuDocsConnectorAdapter",
    "FeishuConnectorProcessAdapter",
    "FeishuDocument",
    "FeishuDocumentDelete",
    "FeishuFlattenedGroupArtifact",
    "FeishuGroupNode",
    "FeishuGroupSnapshot",
    "FeishuIdentityMapping",
    "FeishuObservationStatus",
    "FeishuPermissionKind",
    "FeishuPermissionSubject",
    "FeishuRateLimited",
    "FeishuSourceError",
    "FEISHU_DOCS_CAPABILITY_MANIFEST_JSON",
    "decode_feishu_checkpoint",
    "encode_feishu_checkpoint",
    "flatten_feishu_groups",
    "serialize_feishu_twin_fixture",
]
