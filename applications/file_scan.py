"""Compose one bounded File source acquisition cycle."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import Engine

from adapters.file_source import FileChangeProvider, FileRootRegistry
from applications.file_root_configuration import required_environment
from applications.operator_authentication import (
    CONTROL_OPERATOR_SECRET_ENV,
    DOGFOOD_SECRET_ENV,
    DOGFOOD_SECRET_FINGERPRINT_ENV,
    RELEASE_OPERATOR_SECRET_ENV,
    RELEASE_OPERATOR_SECRET_FINGERPRINT_ENV,
    WORKER_SECRET_ENV,
    local_secret_fingerprint,
)
from engine.control import (
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    ChangeCursor,
    ChangeLimit,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileChangeControlProofs,
    FileChangeKind,
    FileChangeProviderProofs,
    FileChangeSource,
    FileImportAudience,
    FileImportPath,
    FileImportReceiver,
    FileSourceProgress,
    InitialScan,
    ProviderOk,
    ProviderScanBoundExceeded,
    ScheduledFileChangePage,
    ScheduleFileChangePage,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
)
from engine.persistence import PostgreSQLControlStore

PROVIDER_SIGNING_KEY_ENV = "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX"
CHECKPOINT_SIGNING_KEY_ENV = "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX"
WORKER_SERVICE_PRINCIPAL_ENV = "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID"
DOGFOOD_MEMBERSHIP_ENV = "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID"
DOGFOOD_MEMBERSHIP_VERSION_ENV = "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION"
DOGFOOD_PRINCIPAL_ENV = "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF"
# One accepted path per page lets the existing all-or-none page scheduler remain
# unchanged while the application skips unchanged observations exactly.
FILE_SCAN_PAGE_LIMIT = 1


@dataclass(frozen=True, slots=True)
class FileScanReport:
    """Deterministic content-free summary of one completed scan cycle."""

    source_ref: SourceRef
    paths_observed: int
    changes_accepted: int
    imports_scheduled: int
    deletes_observed: int
    compilation_refusals: int
    advanced_cursor: str | None
    scan_bound: int


class SourceScanRefused(SourceNotAvailable):
    """One Source cannot complete its independently bounded scan cycle."""


def scan_file_source(
    *,
    organization_id: UUID,
    source_ref: SourceRef,
    authority: ControlOperatorAuthority,
    opaque_credential: str,
    engine: Engine,
    clock: Callable[[], datetime],
    roots: FileRootRegistry,
) -> FileScanReport:
    """Observe, accept, and explicitly schedule one complete File scan."""

    provider_key, checkpoint_key = _proof_keys()
    receiver = FileImportReceiver(
        UUID(required_environment(WORKER_SERVICE_PRINCIPAL_ENV))
    )
    audience = FileImportAudience(
        required_environment(DOGFOOD_PRINCIPAL_ENV),
        UUID(required_environment(DOGFOOD_MEMBERSHIP_ENV)),
        _positive_bigint(required_environment(DOGFOOD_MEMBERSHIP_VERSION_ENV)),
    )
    store = PostgreSQLControlStore(
        engine,
        clock=clock,
        file_import_receiver=receiver,
        file_change_checkpoint_signing_key=checkpoint_key,
    )
    control = ContextControl(
        store=store,
        authority=authority,
        clock=clock,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=provider_key.public_key()
        ),
    )
    manifest = _read_manifest(
        control=control,
        authority=authority,
        opaque_credential=opaque_credential,
        organization_id=organization_id,
        source_ref=source_ref,
    )
    if (
        manifest.active_version.capabilities
        is not FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST
    ):
        raise SourceScanRefused
    progress = _read_progress(
        control=control,
        authority=authority,
        opaque_credential=opaque_credential,
        organization_id=organization_id,
        source_ref=source_ref,
    )
    imports_scheduled = 0
    compilation_refusals = 0
    reconciled_page_refs: set[str] = set()
    for pending in progress.pending_change_schedules:
        scheduled = _schedule_page(
            control=control,
            authority=authority,
            opaque_credential=opaque_credential,
            organization_id=organization_id,
            source_ref=source_ref,
            source_version_ref=pending.source_version_ref,
            page_ref=pending.page_ref,
            audience=audience,
        )
        imports_scheduled += len(scheduled.changes)
        for scheduled_change in scheduled.changes:
            _verify_accepted_content_identity(
                roots,
                manifest,
                scheduled_change.path.value,
                scheduled_change.content_sha256,
                scheduled_change.content_length,
            )
        reconciled_page_refs.add(pending.page_ref)
    source = FileChangeSource(
        organization_id,
        manifest.active_version,
        scan_head=progress.change_scan_head,
        complete_baseline=progress.complete_change_baseline,
    )
    provider = FileChangeProvider(
        roots,
        proofs=FileChangeProviderProofs(
            provider_signing_key=provider_key,
            checkpoint_verification_key=checkpoint_key.public_key(),
        ),
    )
    prior = _prior_identities(source)
    cursor: InitialScan | ChangeCursor = InitialScan()
    paths_observed: set[str] = set()
    changes_accepted = 0
    deletes_observed = 0
    advanced_cursor: str | None = None
    while True:
        proposed = provider.read_changes(
            source,
            cursor,
            ChangeLimit(FILE_SCAN_PAGE_LIMIT),
        )
        if type(proposed) is ProviderScanBoundExceeded:
            with authority.authorize(
                opaque_credential=opaque_credential,
                operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                request_id=f"local-scan-bound-refusal-{uuid4().hex}",
            ) as call:
                if call.organization_id != organization_id:
                    raise SourceNotAvailable
                control.report_file_scan_bound_refusal(
                    call,
                    source_ref,
                    proposed.scan_bound,
                )
            raise SourceNotAvailable
        if type(proposed) is not ProviderOk:
            raise SourceScanRefused
        page = proposed.value
        if page.page_limit != FILE_SCAN_PAGE_LIMIT:
            raise SourceNotAvailable
        if type(cursor) is InitialScan and _replays_complete_baseline(
            source,
            page.scan_ref,
            page.scan_epoch,
        ):
            baseline = source.complete_baseline
            if baseline is None:  # pragma: no cover - proven by the predicate
                raise SourceNotAvailable
            with authority.authorize(
                opaque_credential=opaque_credential,
                operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                request_id=f"local-scan-bound-clear-{uuid4().hex}",
            ) as call:
                if call.organization_id != organization_id:
                    raise SourceNotAvailable
                control.clear_file_scan_bound_refusal(call, source_ref)
            return FileScanReport(
                source_ref=source_ref,
                paths_observed=sum(
                    entry.kind is FileChangeKind.UPSERT for entry in baseline.entries
                ),
                changes_accepted=0,
                imports_scheduled=imports_scheduled,
                deletes_observed=0,
                compilation_refusals=compilation_refusals,
                advanced_cursor=baseline.reference.checkpoint_ref,
                scan_bound=baseline.reference.scan_bound,
            )
        observed = tuple(page.changes)
        for change in observed:
            if change.kind is FileChangeKind.UPSERT:
                paths_observed.add(change.path.value)
        novel_upserts = tuple(
            change
            for change in observed
            if change.kind is FileChangeKind.UPSERT
            and prior.get(change.path.value)
            != (change.content_sha256, change.content_length)
        )
        deletes = tuple(
            change for change in observed if change.kind is FileChangeKind.DELETE
        )
        with authority.authorize(
            opaque_credential=opaque_credential,
            operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
            request_id=f"local-scan-accept-{uuid4().hex}",
        ) as call:
            if call.organization_id != organization_id:
                raise SourceNotAvailable
            accepted = control.accept_file_change_page(call, page)
        changes_accepted += len(novel_upserts) + len(deletes)
        deletes_observed += len(deletes)
        if novel_upserts and accepted.page_ref not in reconciled_page_refs:
            scheduled = _schedule_page(
                control=control,
                authority=authority,
                opaque_credential=opaque_credential,
                organization_id=organization_id,
                source_ref=accepted.source_ref,
                source_version_ref=accepted.source_version_ref,
                page_ref=accepted.page_ref,
                audience=audience,
            )
            scheduled_changes = {
                change.path.value: change
                for change in scheduled.changes
                if change.path.value
                in {candidate.path.value for candidate in novel_upserts}
            }
            imports_scheduled += len(scheduled_changes)
            for path, scheduled_change in scheduled_changes.items():
                _verify_accepted_content_identity(
                    roots,
                    manifest,
                    path,
                    scheduled_change.content_sha256,
                    scheduled_change.content_length,
                )
        advanced_cursor = accepted.checkpoint_ref
        if accepted.next_cursor is None:
            break
        cursor = accepted.next_cursor
        source = replace(source, scan_head=accepted.scan_head)
    return FileScanReport(
        source_ref=source_ref,
        paths_observed=len(paths_observed),
        changes_accepted=changes_accepted,
        imports_scheduled=imports_scheduled,
        deletes_observed=deletes_observed,
        compilation_refusals=compilation_refusals,
        advanced_cursor=advanced_cursor,
        scan_bound=roots._limits.max_baseline_entries,
    )


def _schedule_page(
    *,
    control: ContextControl,
    authority: ControlOperatorAuthority,
    opaque_credential: str,
    organization_id: UUID,
    source_ref: SourceRef,
    source_version_ref: UUID,
    page_ref: str,
    audience: FileImportAudience,
) -> ScheduledFileChangePage:
    with authority.authorize(
        opaque_credential=opaque_credential,
        operation=ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
        request_id=f"local-scan-schedule-{uuid4().hex}",
    ) as call:
        if call.organization_id != organization_id:
            raise SourceNotAvailable
        return control.schedule_file_change_page(
            call,
            ScheduleFileChangePage(
                source_ref,
                source_version_ref,
                page_ref,
                audience,
            ),
        )


def _read_manifest(
    *,
    control: ContextControl,
    authority: ControlOperatorAuthority,
    opaque_credential: str,
    organization_id: UUID,
    source_ref: SourceRef,
) -> SourceManifest:
    with authority.authorize(
        opaque_credential=opaque_credential,
        operation=ControlOperation.READ_SOURCE,
        request_id=f"local-scan-read-source-{uuid4().hex}",
    ) as call:
        if call.organization_id != organization_id:
            raise SourceNotAvailable
        return control.read_source(call, source_ref)


def _read_progress(
    *,
    control: ContextControl,
    authority: ControlOperatorAuthority,
    opaque_credential: str,
    organization_id: UUID,
    source_ref: SourceRef,
) -> FileSourceProgress:
    with authority.authorize(
        opaque_credential=opaque_credential,
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id=f"local-scan-read-progress-{uuid4().hex}",
    ) as call:
        if call.organization_id != organization_id:
            raise SourceNotAvailable
        return control.read_file_source_progress(call, source_ref)


def _private_key_material(name: str) -> bytes:
    raw = required_environment(name)
    if len(raw) != 64:
        raise SourceNotAvailable
    try:
        value = bytes.fromhex(raw)
    except ValueError:
        raise SourceNotAvailable from None
    if len(value) != 32:
        raise SourceNotAvailable
    return value


def _proof_keys() -> tuple[Ed25519PrivateKey, Ed25519PrivateKey]:
    provider_material = _private_key_material(PROVIDER_SIGNING_KEY_ENV)
    checkpoint_material = _private_key_material(CHECKPOINT_SIGNING_KEY_ENV)
    operator_secret_values = (
        required_environment(CONTROL_OPERATOR_SECRET_ENV),
        required_environment(WORKER_SECRET_ENV),
    )
    encoded_proof_values = (
        provider_material.hex(),
        checkpoint_material.hex(),
    )
    external_fingerprints = tuple(
        _external_secret_fingerprint(secret_name, fingerprint_name)
        for secret_name, fingerprint_name in (
            (RELEASE_OPERATOR_SECRET_ENV, RELEASE_OPERATOR_SECRET_FINGERPRINT_ENV),
            (DOGFOOD_SECRET_ENV, DOGFOOD_SECRET_FINGERPRINT_ENV),
        )
    )
    if any(
        hmac.compare_digest(
            local_secret_fingerprint(proof_value),
            external_fingerprint,
        )
        for proof_value in encoded_proof_values
        for external_fingerprint in external_fingerprints
    ) or any(
        hmac.compare_digest(proof_value, operator_secret.lower())
        for proof_value in encoded_proof_values
        for operator_secret in operator_secret_values
    ):
        raise SourceNotAvailable
    configured_secrets = (
        provider_material,
        checkpoint_material,
        _private_key_material(WORKER_SECRET_ENV),
        *(value.encode("utf-8") for value in operator_secret_values),
    )
    for index, secret in enumerate(configured_secrets):
        if any(
            hmac.compare_digest(secret, other)
            for other in configured_secrets[index + 1 :]
        ):
            raise SourceNotAvailable
    return (
        Ed25519PrivateKey.from_private_bytes(provider_material),
        Ed25519PrivateKey.from_private_bytes(checkpoint_material),
    )


def _external_secret_fingerprint(secret_name: str, fingerprint_name: str) -> str:
    raw = os.environ.get(secret_name)
    if raw is not None:
        return local_secret_fingerprint(raw)
    fingerprint = required_environment(fingerprint_name)
    if len(fingerprint) != 64:
        raise SourceNotAvailable
    try:
        decoded = bytes.fromhex(fingerprint)
    except ValueError:
        raise SourceNotAvailable from None
    if len(decoded) != 32:
        raise SourceNotAvailable
    return fingerprint.lower()


def _positive_bigint(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise SourceNotAvailable
    parsed = int(value)
    if not 1 <= parsed <= 2**63 - 1:
        raise SourceNotAvailable
    return parsed


def _prior_identities(source: FileChangeSource) -> dict[str, tuple[str, int]]:
    baseline = source.complete_baseline
    if baseline is None:
        return {}
    return {
        entry.path.value: (entry.content_sha256, entry.content_length)
        for entry in baseline.entries
        if entry.kind is FileChangeKind.UPSERT
    }


def _replays_complete_baseline(
    source: FileChangeSource,
    scan_ref: str,
    scan_epoch: UUID,
) -> bool:
    """Recognize the provider's exact replay of the current complete scan."""

    baseline = source.complete_baseline
    head = source.scan_head
    if baseline is None or head is None or not head.complete:
        return False
    reference = baseline.reference
    return (
        (
            head.source_version_ref,
            head.scan_ref,
            head.scan_epoch,
            head.page_ref,
            head.checkpoint_ref,
            head.sequence,
            head.scan_bound,
        )
        == (
            reference.source_version_ref,
            reference.scan_ref,
            reference.scan_epoch,
            reference.page_ref,
            reference.checkpoint_ref,
            reference.sequence,
            reference.scan_bound,
        )
        and scan_ref == reference.scan_ref
        and scan_epoch == reference.scan_epoch
    )


def _verify_accepted_content_identity(
    roots: FileRootRegistry,
    manifest: SourceManifest,
    path: str,
    expected_sha256: str,
    expected_length: int,
) -> None:
    try:
        payload = roots.read(
            manifest.active_version.root_ref,
            FileImportPath(path),
        )
    except LookupError:
        raise SourceScanRefused from None
    if (
        len(payload) != expected_length
        or hashlib.sha256(payload).hexdigest() != expected_sha256
    ):
        raise SourceScanRefused
    # Production rich compilation is owned by the exact leased Supply worker.
    # Scan retains only the accepted-byte identity preflight and cannot invoke
    # or predict the runner's durable refusal classification.
