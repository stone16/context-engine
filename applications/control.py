"""Short-lived local operator process for ContextEngine control operations."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine

from applications.file_root_configuration import file_roots
from applications.file_scan import (
    FileScanReport,
    SourceScanRefused,
    scan_file_source,
)
from applications.operator_authentication import (
    CONTROL_OPERATOR_SECRET_ENV,
    LocalControlOperatorConfiguration,
    LocalOperatorConfiguration,
)
from applications.release_promotion import promote_release, release_report_json
from engine.control import (
    ActivateFileChangeFeed,
    ActivateFileDeleteObservations,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileRootRef,
    FileSourceProgress,
    RegisterFileSource,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
)
from engine.persistence import (
    DatabasePurpose,
    PostgreSQLControlStore,
    create_database_engine,
    load_database_configuration,
)
from engine.persistence.migrations import migrate_to_head

_OPERATOR_SUBCOMMANDS = frozenset(
    {
        "register-file-source",
        "read-source",
        "activate-change-feed",
        "activate-delete-observations",
        "scan",
        "scan-all",
        "status",
    }
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="context-engine-control")
    subcommands = parser.add_subparsers(dest="subcommand", required=True)
    subcommands.add_parser(
        "migrate",
        help="upgrade the configured database to the current schema head",
    )
    register = subcommands.add_parser(
        "register-file-source",
        help="register one logical File root",
    )
    _organization_argument(register)
    register.add_argument("--display-name", required=True)
    register.add_argument("--root-ref", required=True)
    register.add_argument("--idempotency-key", required=True)
    for name, help_text in (
        ("read-source", "read one registered File source"),
        ("activate-change-feed", "activate one File source change feed"),
        (
            "activate-delete-observations",
            "activate one File source delete-observation capability",
        ),
    ):
        source_command = subcommands.add_parser(name, help=help_text)
        _organization_argument(source_command)
        source_command.add_argument("--source-ref", required=True)
    scan = subcommands.add_parser(
        "scan",
        help="scan one registered File source and schedule changed upserts",
    )
    _organization_argument(scan)
    scan.add_argument("--source-ref", required=True)
    scan_all = subcommands.add_parser(
        "scan-all",
        help="scan every active registered File source without caller source routing",
    )
    _organization_argument(scan_all)
    status = subcommands.add_parser(
        "status",
        help="report one or every active registered File source's status",
    )
    _organization_argument(status)
    status.add_argument("--source-ref")
    promote = subcommands.add_parser(
        "promote-release",
        help="evaluate and promote the exact current dogfood File corpus",
    )
    _organization_argument(promote)
    promote.add_argument("--evidence-file", required=True, type=Path)
    return parser


def _organization_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--organization-id", required=True)


def main(argv: Sequence[str] | None = None) -> None:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.subcommand == "migrate":
        try:
            revision = migrate_to_head()
        except Exception:  # The local process must never render connection details.
            parser.exit(1, "context-engine-control: migration refused\n")
        print(revision, flush=True)
        return
    if arguments.subcommand == "promote-release":
        try:
            organization_id = UUID(arguments.organization_id)
            configuration = LocalOperatorConfiguration.load(os.environ)
            if (
                configuration is None
                or configuration.organization_id != organization_id
            ):
                raise SourceNotAvailable
            promotion = promote_release(
                organization_id=organization_id,
                evidence_file=arguments.evidence_file,
                configuration=configuration,
                authorities=configuration.authorities(),
            )
            rendered = release_report_json(promotion)
        except Exception:
            parser.exit(1, "context-engine-control: operation refused\n")
        print(rendered, flush=True)
        return
    if arguments.subcommand not in _OPERATOR_SUBCOMMANDS:
        parser.error("unknown operation")
    try:
        outcome = _run_operator_subcommand(arguments)
        if type(outcome) is MultiSourceScanReport:
            rendered = _multi_scan_report_json(outcome)
        elif type(outcome) is MultiSourceStatusReport:
            rendered = _multi_status_json(outcome)
        elif type(outcome) is FileScanReport:
            rendered = _scan_report_json(outcome)
        elif type(outcome) is FileSourceProgress:
            rendered = _status_json(outcome)
        elif type(outcome) is SourceManifest:
            rendered = _manifest_json(outcome)
        else:  # pragma: no cover - closed application union
            raise SourceNotAvailable
    except Exception:  # Operator refusals disclose no supplied or trusted facts.
        parser.exit(1, "context-engine-control: operation refused\n")
    print(rendered, flush=True)


def local_control_operator_authority() -> ControlOperatorAuthority | None:
    """Construct routine Control authority without loading release credentials."""

    configuration = LocalControlOperatorConfiguration.load(os.environ)
    if configuration is None:
        return None
    return configuration.authority()


def _run_operator_subcommand(
    arguments: argparse.Namespace,
) -> (
    SourceManifest
    | FileScanReport
    | FileSourceProgress
    | MultiSourceScanReport
    | MultiSourceStatusReport
):
    authority = local_control_operator_authority()
    if authority is None:
        raise SourceNotAvailable
    organization_id = UUID(arguments.organization_id)
    opaque_credential = os.environ[CONTROL_OPERATOR_SECRET_ENV]
    configuration = load_database_configuration(DatabasePurpose.CONTROL_PLANE)
    engine = create_database_engine(configuration)

    def clock() -> datetime:
        return datetime.now(UTC)

    try:
        if arguments.subcommand in {"scan", "scan-all"}:
            with file_roots() as roots:
                if arguments.subcommand == "scan-all":
                    manifests = _list_sources(
                        organization_id=organization_id,
                        authority=authority,
                        opaque_credential=opaque_credential,
                        engine=engine,
                        clock=clock,
                    )
                    outcomes: list[FileScanReport | SourceScanRefusal] = []
                    for manifest in manifests:
                        try:
                            outcome: FileScanReport | SourceScanRefusal = (
                                scan_file_source(
                                    organization_id=organization_id,
                                    source_ref=manifest.source_ref,
                                    authority=authority,
                                    opaque_credential=opaque_credential,
                                    engine=engine,
                                    clock=clock,
                                    roots=roots,
                                )
                            )
                        except SourceScanRefused:
                            outcome = SourceScanRefusal(manifest.source_ref)
                        outcomes.append(outcome)
                    return MultiSourceScanReport(tuple(outcomes))
                return scan_file_source(
                    organization_id=organization_id,
                    source_ref=SourceRef(UUID(arguments.source_ref)),
                    authority=authority,
                    opaque_credential=opaque_credential,
                    engine=engine,
                    clock=clock,
                    roots=roots,
                )
        operation = _operation(arguments.subcommand)
        control = ContextControl(
            store=PostgreSQLControlStore(engine, clock=clock),
            authority=authority,
            clock=clock,
        )
        if arguments.subcommand == "status" and arguments.source_ref is None:
            manifests = _list_sources_with_control(
                control=control,
                organization_id=organization_id,
                authority=authority,
                opaque_credential=opaque_credential,
            )
            progress = tuple(
                _read_status(
                    control=control,
                    organization_id=organization_id,
                    source_ref=manifest.source_ref,
                    authority=authority,
                    opaque_credential=opaque_credential,
                )
                for manifest in manifests
            )
            return MultiSourceStatusReport(progress)
        with authority.authorize(
            opaque_credential=opaque_credential,
            operation=operation,
            request_id=f"local-{arguments.subcommand}-{uuid4().hex}",
        ) as call:
            if call.organization_id != organization_id:
                raise SourceNotAvailable
            if operation is ControlOperation.REGISTER_SOURCE:
                return control.register_source(
                    call,
                    RegisterFileSource(
                        display_name=arguments.display_name,
                        root_ref=FileRootRef(arguments.root_ref),
                        idempotency_key=arguments.idempotency_key,
                    ),
                )
            source_ref = SourceRef(UUID(arguments.source_ref))
            if operation is ControlOperation.READ_SOURCE_PROGRESS:
                return control.read_file_source_progress(call, source_ref)
            if operation is ControlOperation.READ_SOURCE:
                return control.read_source(call, source_ref)
            if operation is ControlOperation.ACTIVATE_FILE_CHANGE_FEED:
                return control.activate_file_change_feed(
                    call,
                    ActivateFileChangeFeed(source_ref),
                )
            if operation is ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS:
                return control.activate_file_delete_observations(
                    call,
                    ActivateFileDeleteObservations(source_ref),
                )
            raise SourceNotAvailable
    finally:
        engine.dispose()


class SourceScanFailureCategory(StrEnum):
    """Closed, content-free reason for one Source scan refusal."""

    OPERATION_REFUSED = "operation_refused"


@dataclass(frozen=True, slots=True)
class SourceScanRefusal:
    """One content-free refusal from an independently bounded Source scan."""

    source_ref: SourceRef
    reason_category: SourceScanFailureCategory = field(
        default=SourceScanFailureCategory.OPERATION_REFUSED,
        init=False,
    )

    def __post_init__(self) -> None:
        if (
            type(self.source_ref) is not SourceRef
            or self.reason_category is not SourceScanFailureCategory.OPERATION_REFUSED
        ):
            raise SourceNotAvailable


@dataclass(frozen=True, slots=True)
class MultiSourceScanReport:
    """One content-free aggregate over discovered active File sources."""

    outcomes: tuple[FileScanReport | SourceScanRefusal, ...]

    def __post_init__(self) -> None:
        if type(self.outcomes) is not tuple or any(
            type(outcome) not in {FileScanReport, SourceScanRefusal}
            for outcome in self.outcomes
        ):
            raise SourceNotAvailable
        refs = tuple(outcome.source_ref.value for outcome in self.outcomes)
        if refs != tuple(sorted(refs)) or len(refs) != len(set(refs)):
            raise SourceNotAvailable


@dataclass(frozen=True, slots=True)
class MultiSourceStatusReport:
    """One content-free status snapshot for discovered active File sources."""

    sources: tuple[FileSourceProgress, ...]

    def __post_init__(self) -> None:
        if type(self.sources) is not tuple or any(
            type(progress) is not FileSourceProgress for progress in self.sources
        ):
            raise SourceNotAvailable
        refs = tuple(progress.source_ref.value for progress in self.sources)
        if refs != tuple(sorted(refs)) or len(refs) != len(set(refs)):
            raise SourceNotAvailable


def _list_sources(
    *,
    organization_id: UUID,
    authority: ControlOperatorAuthority,
    opaque_credential: str,
    engine: Engine,
    clock: Callable[[], datetime],
) -> tuple[SourceManifest, ...]:
    control = ContextControl(
        store=PostgreSQLControlStore(engine, clock=clock),
        authority=authority,
        clock=clock,
    )
    return _list_sources_with_control(
        control=control,
        organization_id=organization_id,
        authority=authority,
        opaque_credential=opaque_credential,
    )


def _list_sources_with_control(
    *,
    control: ContextControl,
    organization_id: UUID,
    authority: ControlOperatorAuthority,
    opaque_credential: str,
) -> tuple[SourceManifest, ...]:
    with authority.authorize(
        opaque_credential=opaque_credential,
        operation=ControlOperation.READ_SOURCE,
        request_id=f"local-list-sources-{uuid4().hex}",
    ) as call:
        if call.organization_id != organization_id:
            raise SourceNotAvailable
        return control.list_sources(call)


def _read_status(
    *,
    control: ContextControl,
    organization_id: UUID,
    source_ref: SourceRef,
    authority: ControlOperatorAuthority,
    opaque_credential: str,
) -> FileSourceProgress:
    with authority.authorize(
        opaque_credential=opaque_credential,
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id=f"local-status-{uuid4().hex}",
    ) as call:
        if call.organization_id != organization_id:
            raise SourceNotAvailable
        return control.read_file_source_progress(call, source_ref)


def _operation(subcommand: str) -> ControlOperation:
    operations = {
        "register-file-source": ControlOperation.REGISTER_SOURCE,
        "read-source": ControlOperation.READ_SOURCE,
        "status": ControlOperation.READ_SOURCE_PROGRESS,
        "activate-change-feed": ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
        "activate-delete-observations": (
            ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS
        ),
    }
    try:
        return operations[subcommand]
    except KeyError:
        raise SourceNotAvailable from None


def _manifest_json(manifest: SourceManifest) -> str:
    if type(manifest) is not SourceManifest:
        raise SourceNotAvailable
    document = {
        "activeVersion": {
            "capabilities": manifest.active_version.capabilities.document(),
            "createdAt": _timestamp(manifest.active_version.created_at),
            "kind": manifest.active_version.kind.value,
            "rootRef": manifest.active_version.root_ref.value,
            "versionRef": str(manifest.active_version.version_ref),
        },
        "createdAt": _timestamp(manifest.created_at),
        "displayName": manifest.display_name,
        "kind": manifest.kind.value,
        "sourceRef": str(manifest.source_ref.value),
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True)


def _scan_report_json(report: FileScanReport) -> str:
    if type(report) is not FileScanReport:
        raise SourceNotAvailable
    return json.dumps(
        _scan_report_document(report),
        separators=(",", ":"),
        sort_keys=True,
    )


def _scan_report_document(report: FileScanReport) -> dict[str, object]:
    if type(report) is not FileScanReport:
        raise SourceNotAvailable
    return {
        "advancedCursor": report.advanced_cursor,
        "changesAccepted": report.changes_accepted,
        "compilationRefusals": report.compilation_refusals,
        "deletesObserved": report.deletes_observed,
        "importsScheduled": report.imports_scheduled,
        "pathsObserved": report.paths_observed,
        "sourceRef": str(report.source_ref.value),
    }


def _multi_scan_report_json(report: MultiSourceScanReport) -> str:
    if type(report) is not MultiSourceScanReport:
        raise SourceNotAvailable
    sources = tuple(
        outcome for outcome in report.outcomes if type(outcome) is FileScanReport
    )
    refusals = tuple(
        outcome for outcome in report.outcomes if type(outcome) is SourceScanRefusal
    )
    return json.dumps(
        {
            "refusals": [
                {
                    "reasonCategory": refusal.reason_category.value,
                    "sourceRef": str(refusal.source_ref.value),
                }
                for refusal in refusals
            ],
            "sources": [_scan_report_document(source) for source in sources],
            "summary": {
                "changesAccepted": sum(
                    source.changes_accepted for source in sources
                ),
                "compilationRefusals": sum(
                    source.compilation_refusals for source in sources
                ),
                "deletesObserved": sum(
                    source.deletes_observed for source in sources
                ),
                "importsScheduled": sum(
                    source.imports_scheduled for source in sources
                ),
                "pathsObserved": sum(
                    source.paths_observed for source in sources
                ),
                "refusalCount": len(refusals),
                "sourceCount": len(report.outcomes),
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _status_json(progress: FileSourceProgress) -> str:
    """Render content-free operational status with stable keys and ordering."""

    if type(progress) is not FileSourceProgress or progress.status is None:
        raise SourceNotAvailable
    return json.dumps(
        _status_document(progress),
        separators=(",", ":"),
        sort_keys=True,
    )


def _status_document(progress: FileSourceProgress) -> dict[str, object]:
    if type(progress) is not FileSourceProgress or progress.status is None:
        raise SourceNotAvailable
    return _status_document_with_refusals(
        progress,
        [
            {"category": refusal.category.value, "path": refusal.path}
            for refusal in progress.status.refusals
        ],
    )


def _status_document_with_refusals(
    progress: FileSourceProgress,
    refusals: list[dict[str, object]],
) -> dict[str, object]:
    if type(progress) is not FileSourceProgress or progress.status is None:
        raise SourceNotAvailable
    status = progress.status
    checkpoint = progress.acquisition_checkpoint
    watermark = progress.publish_watermark
    head = progress.change_scan_head
    baseline = progress.complete_change_baseline
    last_successful_acquisition: dict[str, object] = (
        {"state": "never"}
        if status.last_successful_acquisition_at is None
        else {
            "ageSeconds": status.last_successful_acquisition_age_seconds,
            "at": _timestamp(status.last_successful_acquisition_at),
            "state": "succeeded",
        }
    )
    return {
        "acquisitionCheckpoint": (
            None
            if checkpoint is None
            else {
                "acceptedAt": _timestamp(checkpoint.accepted_at),
                "changeKind": checkpoint.change_kind.value,
                "checkpointRef": checkpoint.checkpoint_ref,
                "sequence": checkpoint.sequence,
            }
        ),
        "activeResourceCount": status.active_resource_count,
        "changeScanHead": (
            None
            if head is None
            else {
                "checkpointRef": head.checkpoint_ref,
                "complete": head.complete,
                "pageLimit": head.page_limit,
                "pageRef": head.page_ref,
                "scanEpoch": str(head.scan_epoch),
                "scanRef": head.scan_ref,
                "sequence": head.sequence,
                "sourceVersionRef": str(head.source_version_ref),
            }
        ),
        "completeChangeBaselineSize": (
            0 if baseline is None else len(baseline.entries)
        ),
        "lastSuccessfulAcquisition": last_successful_acquisition,
        "publishWatermark": (
            None
            if watermark is None
            else {
                "changeKind": watermark.change_kind.value,
                "outcome": watermark.outcome.value,
                "publishedAt": _timestamp(watermark.published_at),
                "sequence": watermark.sequence,
                "watermarkRef": watermark.watermark_ref,
            }
        ),
        "refusals": refusals,
        "sourceRef": str(progress.source_ref.value),
    }


def _multi_status_json(report: MultiSourceStatusReport) -> str:
    if type(report) is not MultiSourceStatusReport:
        raise SourceNotAvailable
    documents = [_multi_status_document(source) for source in report.sources]
    return json.dumps(
        {
            "sources": documents,
            "summary": {
                "activeResourceCount": sum(
                    source.status.active_resource_count
                    for source in report.sources
                    if source.status is not None
                ),
                "refusalCount": sum(
                    len(source.status.refusals)
                    for source in report.sources
                    if source.status is not None
                ),
                "sourceCount": len(report.sources),
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _multi_status_document(progress: FileSourceProgress) -> dict[str, object]:
    """Aggregate refusal categories without expanding path-bearing status."""

    if type(progress) is not FileSourceProgress or progress.status is None:
        raise SourceNotAvailable
    category_counts: dict[str, int] = {}
    for refusal in progress.status.refusals:
        category = refusal.category.value
        category_counts[category] = category_counts.get(category, 0) + 1
    return _status_document_with_refusals(
        progress,
        [
            {"category": category, "count": count}
            for category, count in sorted(category_counts.items())
        ],
    )


def _timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise SourceNotAvailable
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
