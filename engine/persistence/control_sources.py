"""PostgreSQL store for trusted ContextControl File source registration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from engine._opaque import encode_base64url
from engine.control import (
    DEFAULT_FILE_CHANGE_BASELINE_SIZE,
    FILE_CAPABILITY_MANIFEST,
    FILE_CHANGE_CAPABILITY_MANIFEST,
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    FILE_IMPORT_CAPABILITY_MANIFEST,
    AcceptedChangePage,
    ActivateFileChangeFeed,
    ActivateFileDeleteObservations,
    ChangeCursor,
    ExecutedFileDeleteObservation,
    ExecuteFileDeleteObservation,
    FileChangeBaseline,
    FileChangeBaselineEntry,
    FileChangeBaselineRef,
    FileChangeKind,
    FileChangeScanHead,
    FileCompilationRefusal,
    FileCompilationRefusalCategory,
    FileImportPath,
    FileResourceTombstone,
    FileRootRef,
    FileScanRefusalCategory,
    FileSourceAcquisitionCheckpoint,
    FileSourceChangeKind,
    FileSourceCleanupState,
    FileSourceOffboarding,
    FileSourceProgress,
    FileSourcePublishOutcome,
    FileSourcePublishWatermark,
    FileSourceStatus,
    OffboardFileSource,
    PendingFileChangeSchedule,
    RegisterFileSource,
    ScheduledFileChange,
    ScheduledFileChangePage,
    ScheduleFileChangePage,
    SetSourceArticlePolicyDefault,
    SetTenantArticlePolicyDefault,
    SourceControlUnavailable,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
    TombstoneFileResource,
    TrustedControlCall,
    VerifiedChangePage,
)
from engine.control.file_change_pages import _accepted_cursor_payload
from engine.persistence.role_guard import assert_control_role
from engine.supply import (
    FileImportReceiver,
    PreparedFileImport,
    PrepareFileImport,
)

_REGISTRATION_OPERATION = "register_source"
_FILE_SCAN_BOUND_MIGRATION_FENCE = "context-engine.file-status-migration-fence"
_BOUNDED_FILE_DELETE_ACCEPT = (
    "public.context_control_accept_bounded_file_delete_observation_page"
    "(uuid,uuid,uuid,text,uuid,smallint,text,text,text,bigint,uuid,jsonb,"
    "boolean,integer,jsonb)"
)
_LEGACY_FILE_DELETE_ACCEPT = (
    "public.context_control_accept_file_delete_observation_page"
    "(uuid,uuid,uuid,text,uuid,smallint,text,text,text,bigint,uuid,jsonb,"
    "boolean,jsonb)"
)
_ACTIVE_SOURCE_SELECT = """
    SELECT
        source.source_id,
        source.display_name,
        source.source_kind,
        source.created_at AS source_created_at,
        source.registration_digest,
        version.version_id,
        version.source_kind AS version_source_kind,
        version.root_ref,
        version.capability_manifest,
        version.created_at AS version_created_at
    FROM context_source AS source
    JOIN source_version AS version
      ON version.organization_id = source.organization_id
     AND version.source_id = source.source_id
     AND version.version_id = source.active_version_id
     AND source.lifecycle_state = 'active'
"""


def _capability_document() -> dict[str, object]:
    return FILE_CAPABILITY_MANIFEST.document()


_REGISTRATION_CAPABILITY_DOCUMENT = FILE_CAPABILITY_MANIFEST.document()
_KNOWN_CAPABILITY_DOCUMENTS = {
    FILE_CAPABILITY_MANIFEST.declaration_version: FILE_CAPABILITY_MANIFEST,
    FILE_IMPORT_CAPABILITY_MANIFEST.declaration_version: (
        FILE_IMPORT_CAPABILITY_MANIFEST
    ),
    FILE_CHANGE_CAPABILITY_MANIFEST.declaration_version: (
        FILE_CHANGE_CAPABILITY_MANIFEST
    ),
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST.declaration_version: (
        FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST
    ),
}


def _registration_digest(command: RegisterFileSource) -> str:
    document = {
        "display_name": command.display_name,
        "idempotency_key": command.idempotency_key,
        "operation": _REGISTRATION_OPERATION,
        "root_ref": command.root_ref.value,
        "source_kind": "file",
    }
    return hashlib.sha256(
        b"context-engine.register-file-source.v1\x00" + rfc8785.dumps(document)
    ).hexdigest()


def _set_organization_context(connection: Any, organization_id: UUID) -> None:
    observed = connection.execute(
        text(
            "SELECT set_config('app.organization_id', :organization_id, true), "
            "current_setting('app.organization_id', true)"
        ),
        {"organization_id": str(organization_id)},
    ).one()
    if tuple(observed) != (str(organization_id), str(organization_id)):
        raise SourceControlUnavailable(
            "source Control Organization context could not be bound"
        )


class PostgreSQLControlStore:
    """Register/read File source manifests under the exact non-owner Control role."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID] = uuid4,
        file_import_receiver: FileImportReceiver | None = None,
        file_change_checkpoint_signing_key: Ed25519PrivateKey | None = None,
    ) -> None:
        if not callable(clock) or not callable(uuid_factory):
            raise TypeError("PostgreSQLControlStore requires clock and UUID factory")
        self._engine = engine
        self._clock = clock
        self._uuid_factory = uuid_factory
        if (
            file_import_receiver is not None
            and type(file_import_receiver) is not FileImportReceiver
        ):
            raise TypeError("file_import_receiver must be FileImportReceiver")
        self._file_import_receiver = file_import_receiver
        if file_change_checkpoint_signing_key is not None and not isinstance(
            file_change_checkpoint_signing_key, Ed25519PrivateKey
        ):
            raise TypeError("File change checkpoint signing key is invalid")
        self._file_change_checkpoint_signing_key = file_change_checkpoint_signing_key

    def register_file_source(
        self,
        call: TrustedControlCall,
        command: RegisterFileSource,
    ) -> SourceManifest:
        if (
            type(call) is not TrustedControlCall
            or type(command) is not RegisterFileSource
        ):
            raise SourceNotAvailable
        digest = _registration_digest(command)
        source_id = self._uuid_factory()
        version_id = self._uuid_factory()
        created_at = self._clock()
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                _set_organization_context(connection, call.organization_id)
                inserted = connection.execute(
                    text(
                        """
                        INSERT INTO context_source (
                            organization_id, source_id, display_name, source_kind,
                            registration_operation, idempotency_key,
                            registration_digest, active_version_id, created_at
                        ) VALUES (
                            :organization_id, :source_id, :display_name, 'file',
                            :registration_operation, :idempotency_key,
                            :registration_digest, :active_version_id, :created_at
                        )
                        ON CONFLICT (
                            organization_id,
                            registration_operation,
                            idempotency_key
                        ) DO NOTHING
                        RETURNING source_id
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": source_id,
                        "display_name": command.display_name,
                        "registration_operation": _REGISTRATION_OPERATION,
                        "idempotency_key": command.idempotency_key,
                        "registration_digest": digest,
                        "active_version_id": version_id,
                        "created_at": created_at,
                    },
                ).scalar_one_or_none()
                if inserted is not None:
                    connection.execute(
                        text(
                            """
                            INSERT INTO source_version (
                                organization_id, source_id, version_id,
                                source_kind, root_ref, capability_manifest,
                                created_at
                            ) VALUES (
                                :organization_id, :source_id, :version_id,
                                'file', :root_ref, CAST(:capabilities AS jsonb),
                                :created_at
                            )
                            """
                        ),
                        {
                            "organization_id": call.organization_id,
                            "source_id": source_id,
                            "version_id": version_id,
                            "root_ref": command.root_ref.value,
                            "capabilities": rfc8785.dumps(
                                cast(Any, _REGISTRATION_CAPABILITY_DOCUMENT)
                            ).decode("utf-8"),
                            "created_at": created_at,
                        },
                    )
                row = self._select_registration(
                    connection,
                    organization_id=call.organization_id,
                    idempotency_key=command.idempotency_key,
                )
                if row is None or row["registration_digest"] != digest:
                    raise SourceNotAvailable
                return self._manifest(row)
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError):
            raise SourceControlUnavailable(
                "File source registration database authority is unavailable"
            ) from None

    def read_source(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> SourceManifest:
        if type(call) is not TrustedControlCall or type(source_ref) is not SourceRef:
            raise SourceNotAvailable
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                _set_organization_context(connection, call.organization_id)
                row = (
                    connection.execute(
                        text(
                            _ACTIVE_SOURCE_SELECT
                            + """
                        WHERE source.organization_id = :organization_id
                          AND source.source_id = :source_id
                        """
                        ),
                        {
                            "organization_id": call.organization_id,
                            "source_id": source_ref.value,
                        },
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise SourceNotAvailable
                return self._manifest(cast(Mapping[str, object], row))
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError):
            raise SourceControlUnavailable(
                "File source read database authority is unavailable"
            ) from None

    def list_file_sources(
        self,
        call: TrustedControlCall,
    ) -> tuple[SourceManifest, ...]:
        """List active File sources inside the already trusted Organization."""

        if type(call) is not TrustedControlCall:
            raise SourceNotAvailable
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                _set_organization_context(connection, call.organization_id)
                rows = tuple(
                    connection.execute(
                        text(
                            _ACTIVE_SOURCE_SELECT
                            + """
                        WHERE source.organization_id = :organization_id
                        ORDER BY source.source_id
                        """
                        ),
                        {"organization_id": call.organization_id},
                    ).mappings()
                )
                return tuple(
                    self._manifest(cast(Mapping[str, object], row)) for row in rows
                )
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError):
            raise SourceControlUnavailable(
                "File source listing database authority is unavailable"
            ) from None

    @staticmethod
    def _policy_parameters(setting: object) -> tuple[str | None, list[str]]:
        from engine.article_access_policy import ArticleAccessPolicySetting

        if setting is None:
            return None, []
        if type(setting) is not ArticleAccessPolicySetting:
            raise SourceNotAvailable
        setting.__post_init__()
        return setting.kind.value, sorted(ref.value for ref in setting.group_refs)

    def set_tenant_article_policy_default(
        self,
        call: TrustedControlCall,
        command: SetTenantArticlePolicyDefault,
    ) -> int:
        if (
            type(call) is not TrustedControlCall
            or type(command) is not SetTenantArticlePolicyDefault
        ):
            raise SourceNotAvailable
        command.__post_init__()
        kind, groups = self._policy_parameters(command.setting)
        result = self._call_article_policy_function(
            call,
            "context_control_set_tenant_article_policy_default",
            {
                "expected_version": command.expected_version,
                "policy_kind": kind,
                "group_refs": groups,
            },
        )
        if type(result) is not int:
            raise SourceNotAvailable
        return result

    def set_source_article_policy_default(
        self,
        call: TrustedControlCall,
        command: SetSourceArticlePolicyDefault,
    ) -> int:
        if (
            type(call) is not TrustedControlCall
            or type(command) is not SetSourceArticlePolicyDefault
        ):
            raise SourceNotAvailable
        command.__post_init__()
        kind, groups = self._policy_parameters(command.setting)
        result = self._call_article_policy_function(
            call,
            "context_control_set_source_article_policy_default",
            {
                "source_ref": command.source_ref,
                "expected_version": command.expected_version,
                "policy_kind": kind,
                "group_refs": groups,
            },
        )
        if type(result) is not int:
            raise SourceNotAvailable
        return result

    def _call_article_policy_function(
        self,
        call: TrustedControlCall,
        function_name: str,
        parameters: dict[str, object],
    ) -> object:
        arguments = ["requested_organization_id => :organization_id"]
        for name in parameters:
            value = (
                "CAST(:group_refs AS text[])"
                if name == "group_refs"
                else f":{name}"
            )
            database_name = name if name == "expected_version" else f"requested_{name}"
            arguments.append(f"{database_name} => {value}")
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                _set_organization_context(connection, call.organization_id)
                return connection.execute(
                    text(
                        f"SELECT public.{function_name}(" + ", ".join(arguments) + ")"
                    ),
                    {"organization_id": call.organization_id, **parameters},
                ).scalar_one_or_none()
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError):
            raise SourceControlUnavailable(
                "Article policy database authority is unavailable"
            ) from None

    def activate_file_change_feed(
        self,
        call: TrustedControlCall,
        command: ActivateFileChangeFeed,
    ) -> SourceManifest:
        """Atomically advance one active File SourceVersion from v2 to v3."""

        if (
            type(call) is not TrustedControlCall
            or type(command) is not ActivateFileChangeFeed
        ):
            raise SourceNotAvailable
        activated_version_id = self._uuid_factory()
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                row = connection.execute(
                    text(
                        """
                        SELECT activated_version_id
                        FROM public.context_control_activate_file_change_feed(
                            :organization_id, :source_id, :activated_version_id
                        )
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": command.source_ref.value,
                        "activated_version_id": activated_version_id,
                    },
                ).one_or_none()
                if row is None:
                    raise SourceNotAvailable
                source_row = (
                    connection.execute(
                        text(
                            _ACTIVE_SOURCE_SELECT
                            + """
                        WHERE source.organization_id = :organization_id
                          AND source.source_id = :source_id
                        """
                        ),
                        {
                            "organization_id": call.organization_id,
                            "source_id": command.source_ref.value,
                        },
                    )
                    .mappings()
                    .one_or_none()
                )
                if source_row is None or source_row["version_id"] != row[0]:
                    raise SourceNotAvailable
                manifest = self._manifest(cast(Mapping[str, object], source_row))
                if (
                    manifest.active_version.capabilities
                    is not FILE_CHANGE_CAPABILITY_MANIFEST
                ):
                    raise SourceNotAvailable
                return manifest
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError, TypeError, ValueError):
            raise SourceControlUnavailable(
                "File change feed activation database authority is unavailable"
            ) from None

    def activate_file_delete_observations(
        self,
        call: TrustedControlCall,
        command: ActivateFileDeleteObservations,
    ) -> SourceManifest:
        """Atomically advance one active File SourceVersion from v3 to v4."""

        if (
            type(call) is not TrustedControlCall
            or type(command) is not ActivateFileDeleteObservations
        ):
            raise SourceNotAvailable
        activated_version_id = self._uuid_factory()
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                row = connection.execute(
                    text(
                        """
                        SELECT activated_version_id
                        FROM public.context_control_activate_file_delete_observations(
                            :organization_id, :source_id, :activated_version_id
                        )
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": command.source_ref.value,
                        "activated_version_id": activated_version_id,
                    },
                ).one_or_none()
                if row is None:
                    raise SourceNotAvailable
                source_row = (
                    connection.execute(
                        text(
                            _ACTIVE_SOURCE_SELECT
                            + """
                        WHERE source.organization_id = :organization_id
                          AND source.source_id = :source_id
                        """
                        ),
                        {
                            "organization_id": call.organization_id,
                            "source_id": command.source_ref.value,
                        },
                    )
                    .mappings()
                    .one_or_none()
                )
                if source_row is None or source_row["version_id"] != row[0]:
                    raise SourceNotAvailable
                manifest = self._manifest(cast(Mapping[str, object], source_row))
                if (
                    manifest.active_version.capabilities
                    is not FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST
                ):
                    raise SourceNotAvailable
                return manifest
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError, TypeError, ValueError):
            raise SourceControlUnavailable(
                "File delete observation activation database authority is unavailable"
            ) from None

    def accept_file_change_page(
        self,
        call: TrustedControlCall,
        page: VerifiedChangePage,
    ) -> AcceptedChangePage:
        """Persist one provider-verified page and its checkpoint atomically."""

        if type(call) is not TrustedControlCall or type(page) is not VerifiedChangePage:
            raise SourceNotAvailable
        value = page.page
        signing_key = self._file_change_checkpoint_signing_key
        if signing_key is None:
            raise SourceControlUnavailable(
                "File change checkpoint signing authority is unavailable"
            )
        try:
            changes_document = [
                {
                    "contentLength": change.content_length,
                    "contentSha256": change.content_sha256,
                    "kind": change.kind.value,
                    "path": change.path.value,
                }
                for change in value.changes
            ]
            with self._engine.begin() as connection:
                assert_control_role(connection)
                baseline_document = (
                    None
                    if value.baseline_ref is None
                    else {
                        "checkpointRef": value.baseline_ref.checkpoint_ref,
                        "pageRef": value.baseline_ref.page_ref,
                        "scanEpoch": str(value.baseline_ref.scan_epoch),
                        "scanRef": value.baseline_ref.scan_ref,
                        "sequence": value.baseline_ref.sequence,
                        "sourceVersionId": str(value.baseline_ref.source_version_ref),
                    }
                )
                delete_observations = (
                    value.capability_version
                    == FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST.declaration_version
                )
                bounded_delete_observations = False
                if delete_observations:
                    connection.execute(
                        text(
                            "SELECT pg_catalog.pg_advisory_xact_lock_shared("
                            "pg_catalog.hashtextextended(:fence, 0))"
                        ),
                        {"fence": _FILE_SCAN_BOUND_MIGRATION_FENCE},
                    )
                    accept_authority = connection.execute(
                        text(
                            "SELECT "
                            "to_regprocedure(:bounded_accept) IS NOT NULL, "
                            "CASE WHEN to_regprocedure(:legacy_accept) IS NULL "
                            "THEN false ELSE pg_catalog.has_function_privilege("
                            "SESSION_USER, to_regprocedure(:legacy_accept), "
                            "'EXECUTE') END"
                        ),
                        {
                            "bounded_accept": _BOUNDED_FILE_DELETE_ACCEPT,
                            "legacy_accept": _LEGACY_FILE_DELETE_ACCEPT,
                        },
                    ).one()
                    bounded_delete_observations = accept_authority[0] is True
                    legacy_delete_observations = accept_authority[1] is True
                    if bounded_delete_observations:
                        function_name = (
                            "context_control_accept_bounded_file_delete_observation_page"
                        )
                        baseline_argument = ", :scan_bound, CAST(:baseline AS jsonb)"
                    elif (
                        legacy_delete_observations
                        and value.scan_bound == DEFAULT_FILE_CHANGE_BASELINE_SIZE
                    ):
                        # A pre-provenance schema can accept only ADR-0065's
                        # historical default. Raised bounds never cross the
                        # legacy authority, which cannot retain their provenance.
                        function_name = (
                            "context_control_accept_file_delete_observation_page"
                        )
                        baseline_argument = ", CAST(:baseline AS jsonb)"
                    else:
                        raise SourceNotAvailable
                else:
                    function_name = "context_control_accept_file_change_page"
                    baseline_argument = ""
                row = connection.execute(
                    text(
                        f"""
                        SELECT *
                        FROM public.{function_name}(
                            :organization_id, :source_id, :source_version_id,
                            :scan_ref, :scan_epoch, :page_limit, :page_ref,
                            :predecessor_page_ref,
                            :predecessor_checkpoint_ref, :predecessor_sequence,
                            :superseded_scan_epoch,
                            CAST(:changes AS jsonb), :complete
                            {baseline_argument}
                        )
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": value.source_ref,
                        "source_version_id": value.source_version_ref,
                        "scan_ref": value.scan_ref,
                        "scan_epoch": value.scan_epoch,
                        "page_limit": value.page_limit,
                        "page_ref": page.page_ref,
                        "predecessor_page_ref": value.predecessor_page_ref,
                        "predecessor_checkpoint_ref": (
                            value.predecessor_checkpoint_ref
                        ),
                        "predecessor_sequence": value.predecessor_sequence,
                        "superseded_scan_epoch": value.superseded_scan_epoch,
                        "changes": rfc8785.dumps(cast(Any, changes_document)).decode(
                            "utf-8"
                        ),
                        "complete": value.complete,
                        "scan_bound": value.scan_bound,
                        "baseline": (
                            None
                            if baseline_document is None
                            else rfc8785.dumps(cast(Any, baseline_document)).decode(
                                "utf-8"
                            )
                        ),
                    },
                ).one_or_none()
                if row is None:
                    raise SourceNotAvailable
            # Reaching this point proves the transaction context committed.
            source_ref = SourceRef(row.source_id)
            pending = value.next_cursor
            next_cursor = None
            if pending is not None:
                payload = _accepted_cursor_payload(
                    organization_id=call.organization_id,
                    source_ref=source_ref,
                    source_version_ref=row.source_version_id,
                    scan_ref=value.scan_ref,
                    scan_epoch=value.scan_epoch,
                    page_ref=row.page_ref,
                    checkpoint_ref=row.checkpoint_ref,
                    sequence=row.sequence,
                    pending_cursor=pending,
                )
                next_cursor = ChangeCursor(
                    f"{encode_base64url(payload)}."
                    f"{encode_base64url(signing_key.sign(payload))}"
                )
            return AcceptedChangePage(
                source_ref=source_ref,
                source_version_ref=row.source_version_id,
                scan_ref=value.scan_ref,
                scan_epoch=value.scan_epoch,
                page_limit=row.page_limit,
                superseded_scan_epoch=row.superseded_scan_epoch,
                page_ref=row.page_ref,
                checkpoint_ref=row.checkpoint_ref,
                sequence=row.sequence,
                change_count=row.change_count,
                complete=row.complete,
                next_cursor=next_cursor,
                accepted_at=row.accepted_at,
                scan_bound=(
                    row.scan_bound
                    if bounded_delete_observations
                    else value.scan_bound
                ),
            )
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError, TypeError, ValueError):
            raise SourceControlUnavailable(
                "File change page database authority is unavailable"
            ) from None

    def report_file_scan_bound_refusal(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
        scan_bound: int,
    ) -> None:
        """Persist one closed scan-bound condition on the active File source."""

        if (
            type(call) is not TrustedControlCall
            or type(source_ref) is not SourceRef
            or type(scan_bound) is not int
        ):
            raise SourceNotAvailable
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                row = connection.execute(
                    text(
                        "SELECT * FROM public."
                        "context_control_report_file_scan_bound_refusal("
                        ":organization_id, :source_id, :scan_bound)"
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": source_ref.value,
                        "scan_bound": scan_bound,
                    },
                ).one_or_none()
                if (
                    row is None
                    or row.refusal_category != "scan_bound_exceeded"
                    or row.scan_bound != scan_bound
                ):
                    raise SourceNotAvailable
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError, TypeError, ValueError):
            raise SourceControlUnavailable(
                "File scan bound refusal database authority is unavailable"
            ) from None

    def clear_file_scan_bound_refusal(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> None:
        """Clear one retained bound condition after complete revalidation."""

        if type(call) is not TrustedControlCall or type(source_ref) is not SourceRef:
            raise SourceNotAvailable
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                row = connection.execute(
                    text(
                        "SELECT * FROM public."
                        "context_control_clear_file_scan_bound_refusal("
                        ":organization_id, :source_id)"
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": source_ref.value,
                    },
                ).one_or_none()
                if row is None or row.cleared is not True:
                    raise SourceNotAvailable
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError, TypeError, ValueError):
            raise SourceControlUnavailable(
                "File scan bound refusal clear database authority is unavailable"
            ) from None

    def offboard_file_source(
        self,
        call: TrustedControlCall,
        command: OffboardFileSource,
    ) -> FileSourceOffboarding:
        """Commit source disable, epoch advance, cancellation and cleanup intent."""

        if (
            type(call) is not TrustedControlCall
            or type(command) is not OffboardFileSource
        ):
            raise SourceNotAvailable
        cleanup_intent_id = self._uuid_factory()
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                _set_organization_context(connection, call.organization_id)
                row = connection.execute(
                    text(
                        """
                        SELECT *
                        FROM public.context_control_offboard_file_source(
                            :organization_id, :source_id, :cleanup_intent_id
                        )
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": command.source_ref.value,
                        "cleanup_intent_id": cleanup_intent_id,
                    },
                ).one_or_none()
                if row is None:
                    raise SourceNotAvailable
                return FileSourceOffboarding(
                    organization_id=call.organization_id,
                    source_ref=SourceRef(row.source_id),
                    source_version_ref=row.source_version_id,
                    policy_epoch=row.policy_epoch,
                    cleanup_intent_ref=row.cleanup_intent_id,
                    cancelled_job_count=row.cancelled_job_count,
                    retained_resource_count=row.retained_resource_count,
                    security_completed_at=row.security_completed_at,
                    cleanup_state=FileSourceCleanupState(row.cleanup_state),
                )
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError, TypeError, ValueError):
            raise SourceControlUnavailable(
                "File source offboarding database authority is unavailable"
            ) from None

    def prepare_file_import(
        self,
        call: TrustedControlCall,
        command: PrepareFileImport,
    ) -> PreparedFileImport:
        """Atomically persist one acquisition and its exact worker job."""

        receiver = self._file_import_receiver
        if (
            type(call) is not TrustedControlCall
            or type(command) is not PrepareFileImport
            or receiver is None
        ):
            raise SourceNotAvailable
        job_id = self._uuid_factory()
        acquisition_id = self._uuid_factory()
        activated_version_id = self._uuid_factory()
        document = {
            "audience_membership_id": str(command.audience.membership_id),
            "audience_membership_version": command.audience.membership_version,
            "audience_principal_ref": command.audience.principal_ref,
            "idempotency_key": command.idempotency_key,
            "operation": receiver.operation,
            "path": command.path.value,
            "source_id": str(command.source_ref.value),
        }
        digest = hashlib.sha256(
            b"context-engine.prepare-file-import.v1\x00"
            + rfc8785.dumps(cast(Any, document))
        ).hexdigest()
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                row = connection.execute(
                    text(
                        """
                        SELECT job_id, service_principal_id
                        FROM public.context_control_prepare_file_import(
                            :organization_id,
                            :acquisition_id,
                            :job_id,
                            :activated_version_id,
                            :source_id,
                            :relative_path,
                            :audience_principal_ref,
                            :audience_membership_id,
                            :audience_membership_version,
                            :idempotency_key,
                            :request_digest,
                            :service_principal_id
                        )
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "acquisition_id": acquisition_id,
                        "job_id": job_id,
                        "activated_version_id": activated_version_id,
                        "source_id": command.source_ref.value,
                        "relative_path": command.path.value,
                        "audience_principal_ref": command.audience.principal_ref,
                        "audience_membership_id": command.audience.membership_id,
                        "audience_membership_version": (
                            command.audience.membership_version
                        ),
                        "idempotency_key": command.idempotency_key,
                        "request_digest": digest,
                        "service_principal_id": receiver.service_principal_id,
                    },
                ).one_or_none()
                if row is None:
                    raise SourceNotAvailable
                return PreparedFileImport(
                    organization_id=call.organization_id,
                    job_id=row.job_id,
                    source_ref=command.source_ref,
                    service_principal_id=row.service_principal_id,
                )
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError):
            raise SourceControlUnavailable(
                "File import Control database authority is unavailable"
            ) from None

    def schedule_file_change_page(
        self,
        call: TrustedControlCall,
        command: ScheduleFileChangePage,
    ) -> ScheduledFileChangePage:
        """Atomically bind one accepted page to existing File import jobs."""

        receiver = self._file_import_receiver
        if (
            type(call) is not TrustedControlCall
            or type(command) is not ScheduleFileChangePage
            or receiver is None
        ):
            raise SourceNotAvailable
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                rows = connection.execute(
                    text(
                        """
                        SELECT *
                        FROM public.context_control_schedule_file_change_page(
                            :organization_id, :source_id, :source_version_id,
                            :page_ref, :audience_principal_ref,
                            :audience_membership_id,
                            :audience_membership_version,
                            :service_principal_id
                        )
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": command.source_ref.value,
                        "source_version_id": command.source_version_ref,
                        "page_ref": command.page_ref,
                        "audience_principal_ref": command.audience.principal_ref,
                        "audience_membership_id": command.audience.membership_id,
                        "audience_membership_version": (
                            command.audience.membership_version
                        ),
                        "service_principal_id": receiver.service_principal_id,
                    },
                ).all()
                if not rows:
                    raise SourceNotAvailable
                changes = tuple(
                    ScheduledFileChange(
                        ordinal=row.change_ordinal,
                        path=FileImportPath(row.relative_path),
                        content_sha256=row.content_sha256,
                        content_length=row.content_length,
                        prepared_import=PreparedFileImport(
                            organization_id=call.organization_id,
                            job_id=row.job_id,
                            source_ref=command.source_ref,
                            service_principal_id=row.service_principal_id,
                        ),
                    )
                    for row in rows
                )
                return ScheduledFileChangePage(
                    organization_id=call.organization_id,
                    source_ref=command.source_ref,
                    source_version_ref=command.source_version_ref,
                    page_ref=command.page_ref,
                    changes=changes,
                )
        except SourceNotAvailable:
            raise
        except (
            DBAPIError,
            SQLAlchemyError,
            AssertionError,
            TypeError,
            ValueError,
        ):
            raise SourceControlUnavailable(
                "File change scheduling database authority is unavailable"
            ) from None

    def read_file_source_progress(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> FileSourceProgress:
        """Read the current contiguous progress signals and durable lineage."""

        if type(call) is not TrustedControlCall or type(source_ref) is not SourceRef:
            raise SourceNotAvailable
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                _set_organization_context(connection, call.organization_id)
                snapshot_rows = tuple(
                    connection.execute(
                        text(
                            """
                            WITH progress AS MATERIALIZED (
                                SELECT * FROM public.
                                    context_control_read_file_source_progress(
                                        :organization_id, :source_id
                                    )
                            ), baseline AS MATERIALIZED (
                                SELECT * FROM public.
                                context_control_read_complete_file_change_baseline(
                                    :organization_id, :source_id
                                )
                            ), pending AS MATERIALIZED (
                                SELECT COALESCE(
                                    jsonb_agg(
                                        jsonb_build_object(
                                            'source_version_ref',
                                                pending_source_version_id,
                                            'page_ref', pending_page_ref
                                        ) ORDER BY pending_page_ref COLLATE "C"
                                    ),
                                    '[]'::jsonb
                                ) AS pending_schedules
                                FROM public.
                                    context_control_read_pending_file_change_schedules(
                                        :organization_id, :source_id
                                    )
                            ), status_rows AS MATERIALIZED (
                                SELECT * FROM public.
                                    context_control_read_file_source_status(
                                        :organization_id, :source_id
                                    )
                            ), scan_bound_status AS MATERIALIZED (
                                SELECT * FROM public.
                                    context_control_read_file_scan_bound_status(
                                        :organization_id, :source_id
                                    )
                            ), status AS MATERIALIZED (
                                SELECT max(status_observed_at)
                                           AS status_observed_at,
                                       max(active_resource_count)
                                           AS active_resource_count,
                                       max(last_successful_acquisition_at)
                                           AS last_successful_acquisition_at,
                                       max(last_successful_acquisition_age_seconds)
                                           AS last_successful_acquisition_age_seconds,
                                       COALESCE(
                                           jsonb_agg(
                                               jsonb_build_object(
                                                   'path', refusal_path,
                                                   'category', refusal_category
                                               ) ORDER BY
                                                   refusal_path COLLATE "C"
                                           ) FILTER (
                                               WHERE refusal_path IS NOT NULL
                                           ),
                                           '[]'::jsonb
                                       ) AS refusal_documents
                                FROM status_rows
                            )
                            SELECT progress.*, baseline.*, pending.*, status.*,
                                   scan_bound_status.*
                            FROM progress
                            LEFT JOIN baseline ON true
                            CROSS JOIN pending
                            CROSS JOIN status
                            CROSS JOIN scan_bound_status
                            """
                        ),
                        {
                            "organization_id": call.organization_id,
                            "source_id": source_ref.value,
                        },
                    ).mappings()
                )
                if not snapshot_rows:
                    raise SourceNotAvailable
                row = snapshot_rows[0]
                baseline_rows = tuple(
                    cast(Mapping[str, object], snapshot_row)
                    for snapshot_row in snapshot_rows
                )
                pending_schedule_documents = cast(
                    list[dict[str, object]],
                    row["pending_schedules"],
                )
                refusal_documents = cast(
                    list[dict[str, object]],
                    row["refusal_documents"],
                )
                if row["status_observed_at"] is None:
                    raise SourceNotAvailable
                checkpoint = (
                    None
                    if row["acquisition_sequence"] is None
                    else FileSourceAcquisitionCheckpoint(
                        sequence=row["acquisition_sequence"],
                        checkpoint_ref=row["acquisition_checkpoint_ref"],
                        change_kind=FileSourceChangeKind(
                            row["acquisition_change_kind"]
                        ),
                        acquisition_ref=row["acquisition_acquisition_id"],
                        job_ref=row["acquisition_job_id"],
                        cleanup_intent_ref=row["acquisition_cleanup_intent_id"],
                        resource_ref=row["acquisition_resource_ref"],
                        revision_ref=row["acquisition_revision_id"],
                        event_ref=row["acquisition_event_ref"],
                        event_sequence=row["acquisition_event_sequence"],
                        accepted_at=row["acquisition_accepted_at"],
                        source_version_ref=row["acquisition_source_version_id"],
                        change_page_ref=row["acquisition_change_page_ref"],
                    )
                )
                watermark = (
                    None
                    if row["publish_sequence"] is None
                    else FileSourcePublishWatermark(
                        sequence=row["publish_sequence"],
                        watermark_ref=row["publish_watermark_ref"],
                        checkpoint_ref=row["publish_checkpoint_ref"],
                        change_kind=FileSourceChangeKind(row["publish_change_kind"]),
                        outcome=FileSourcePublishOutcome(row["publish_outcome"]),
                        acquisition_ref=row["publish_acquisition_id"],
                        job_ref=row["publish_job_id"],
                        cleanup_intent_ref=row["publish_cleanup_intent_id"],
                        resource_ref=row["publish_resource_ref"],
                        revision_ref=row["publish_revision_id"],
                        event_ref=row["publish_event_ref"],
                        event_sequence=row["publish_event_sequence"],
                        published_at=row["publish_published_at"],
                    )
                )
                return FileSourceProgress(
                    organization_id=call.organization_id,
                    source_ref=source_ref,
                    acquisition_checkpoint=checkpoint,
                    publish_watermark=watermark,
                    change_scan_head=(
                        None
                        if row["change_scan_epoch"] is None
                        else FileChangeScanHead(
                            source_version_ref=row["change_source_version_id"],
                            scan_ref=row["change_scan_ref"],
                            scan_epoch=row["change_scan_epoch"],
                            page_limit=row["change_page_limit"],
                            superseded_scan_epoch=row["change_superseded_scan_epoch"],
                            page_ref=row["change_page_ref"],
                            checkpoint_ref=row["change_checkpoint_ref"],
                            sequence=row["change_sequence"],
                            complete=row["change_complete"],
                            scan_bound=row["head_scan_bound"],
                        )
                    ),
                    complete_change_baseline=self._complete_change_baseline(
                        cast(Sequence[Mapping[str, object]], baseline_rows),
                    ),
                    pending_change_schedules=tuple(
                        PendingFileChangeSchedule(
                            source_version_ref=UUID(
                                cast(str, document["source_version_ref"]),
                            ),
                            page_ref=cast(str, document["page_ref"]),
                        )
                        for document in pending_schedule_documents
                    ),
                    status=FileSourceStatus(
                        observed_at=cast(
                            datetime,
                            row["status_observed_at"],
                        ),
                        active_resource_count=cast(
                            int,
                            row["active_resource_count"],
                        ),
                        last_successful_acquisition_at=cast(
                            datetime | None,
                            row["last_successful_acquisition_at"],
                        ),
                        last_successful_acquisition_age_seconds=cast(
                            int | None,
                            row["last_successful_acquisition_age_seconds"],
                        ),
                        refusals=tuple(
                            FileCompilationRefusal(
                                path=cast(str, document["path"]),
                                category=FileCompilationRefusalCategory(
                                    cast(str, document["category"])
                                ),
                            )
                            for document in refusal_documents
                        ),
                        scan_refusal_category=(
                            None
                            if row["refusal_category"] is None
                            else FileScanRefusalCategory(row["refusal_category"])
                        ),
                        scan_refusal_bound=row["refusal_scan_bound"],
                    ),
                )
        except SourceNotAvailable:
            raise
        except (
            DBAPIError,
            SQLAlchemyError,
            AssertionError,
            TypeError,
            ValueError,
        ):
            raise SourceControlUnavailable(
                "File Source progress database authority is unavailable"
            ) from None

    @staticmethod
    def _complete_change_baseline(
        rows: Sequence[Mapping[str, object]],
    ) -> FileChangeBaseline | None:
        if not rows or rows[0].get("baseline_scan_epoch") is None:
            return None
        row = rows[0]
        comparison_reference = (
            None
            if row.get("baseline_parent_scan_epoch") is None
            else FileChangeBaselineRef(
                source_version_ref=cast(UUID, row["baseline_source_version_id"]),
                scan_ref=cast(str, row["baseline_parent_scan_ref"]),
                scan_epoch=cast(UUID, row["baseline_parent_scan_epoch"]),
                page_ref=cast(str, row["baseline_parent_page_ref"]),
                checkpoint_ref=cast(
                    str,
                    row["baseline_parent_checkpoint_ref"],
                ),
                sequence=cast(int, row["baseline_parent_sequence"]),
                scan_bound=cast(int, row["baseline_parent_scan_bound"]),
            )
        )
        reference = FileChangeBaselineRef(
            source_version_ref=cast(UUID, row["baseline_source_version_id"]),
            scan_ref=cast(str, row["baseline_scan_ref"]),
            scan_epoch=cast(UUID, row["baseline_scan_epoch"]),
            page_ref=cast(str, row["baseline_page_ref"]),
            checkpoint_ref=cast(str, row["baseline_checkpoint_ref"]),
            sequence=cast(int, row["baseline_sequence"]),
            scan_bound=cast(int, row["baseline_scan_bound"]),
            comparison_baseline_ref=comparison_reference,
        )
        entries: list[FileChangeBaselineEntry] = []
        for value in rows:
            if value.get("baseline_entry_kind") is None:
                continue
            entries.append(
                FileChangeBaselineEntry(
                    kind=FileChangeKind(cast(str, value["baseline_entry_kind"])),
                    path=FileImportPath(cast(str, value["baseline_entry_path"])),
                    content_sha256=cast(
                        str,
                        value["baseline_entry_content_sha256"],
                    ),
                    content_length=cast(
                        int,
                        value["baseline_entry_content_length"],
                    ),
                )
            )
        entries.sort(key=lambda entry: entry.path.value.encode("utf-8"))
        return FileChangeBaseline(reference=reference, entries=tuple(entries))

    def tombstone_file_resource(
        self,
        call: TrustedControlCall,
        command: TombstoneFileResource,
    ) -> FileResourceTombstone:
        """Commit one tombstone, epoch bump, and pending cleanup intent."""

        if (
            type(call) is not TrustedControlCall
            or type(command) is not TombstoneFileResource
        ):
            raise SourceNotAvailable
        cleanup_intent_id = self._uuid_factory()
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                _set_organization_context(connection, call.organization_id)
                row = connection.execute(
                    text(
                        """
                        SELECT *
                        FROM public.context_control_tombstone_file_resource(
                            :organization_id, :source_id, :resource_ref,
                            :event_ref, :event_sequence, :cleanup_intent_id
                        )
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": command.source_ref.value,
                        "resource_ref": command.resource_ref,
                        "event_ref": command.event_ref,
                        "event_sequence": command.event_sequence,
                        "cleanup_intent_id": cleanup_intent_id,
                    },
                ).one_or_none()
                if row is None:
                    raise SourceNotAvailable
                return FileResourceTombstone(
                    organization_id=call.organization_id,
                    source_ref=SourceRef(row.source_id),
                    resource_ref=row.resource_ref,
                    revision_ref=row.revision_id,
                    event_ref=row.event_ref,
                    event_sequence=row.event_sequence,
                    policy_epoch=row.policy_epoch,
                    cleanup_intent_ref=row.cleanup_intent_id,
                    tombstoned_at=row.tombstoned_at,
                )
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError, TypeError, ValueError):
            raise SourceControlUnavailable(
                "File Resource tombstone database authority is unavailable"
            ) from None

    def execute_file_delete_observation(
        self,
        call: TrustedControlCall,
        command: ExecuteFileDeleteObservation,
    ) -> ExecutedFileDeleteObservation:
        """Atomically bind one current delete observation to its tombstone."""

        if (
            type(call) is not TrustedControlCall
            or type(command) is not ExecuteFileDeleteObservation
        ):
            raise SourceNotAvailable
        cleanup_intent_id = self._uuid_factory()
        try:
            with self._engine.begin() as connection:
                assert_control_role(connection)
                _set_organization_context(connection, call.organization_id)
                row = connection.execute(
                    text(
                        """
                        SELECT *
                        FROM public.context_control_execute_file_delete_observation(
                            :organization_id, :source_id, :source_version_id,
                            :page_ref, :change_ordinal, :cleanup_intent_id
                        )
                        """
                    ),
                    {
                        "organization_id": call.organization_id,
                        "source_id": command.source_ref.value,
                        "source_version_id": command.source_version_ref,
                        "page_ref": command.page_ref,
                        "change_ordinal": command.change_ordinal,
                        "cleanup_intent_id": cleanup_intent_id,
                    },
                ).one_or_none()
                if row is None:
                    raise SourceNotAvailable
                tombstone = FileResourceTombstone(
                    organization_id=call.organization_id,
                    source_ref=SourceRef(row.source_id),
                    resource_ref=row.resource_ref,
                    revision_ref=row.revision_id,
                    event_ref=row.event_ref,
                    event_sequence=row.event_sequence,
                    policy_epoch=row.policy_epoch,
                    cleanup_intent_ref=row.cleanup_intent_id,
                    tombstoned_at=row.tombstoned_at,
                )
                return ExecutedFileDeleteObservation(
                    organization_id=call.organization_id,
                    source_ref=SourceRef(row.source_id),
                    source_version_ref=row.source_version_id,
                    page_ref=row.page_ref,
                    change_ordinal=row.change_ordinal,
                    tombstone=tombstone,
                )
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError, TypeError, ValueError):
            raise SourceControlUnavailable(
                "File delete observation database authority is unavailable"
            ) from None

    @staticmethod
    def _select_registration(
        connection: Any,
        *,
        organization_id: UUID,
        idempotency_key: str,
    ) -> Mapping[str, object] | None:
        row = (
            connection.execute(
                text(
                    _ACTIVE_SOURCE_SELECT
                    + """
                WHERE source.organization_id = :organization_id
                  AND source.registration_operation = :registration_operation
                  AND source.idempotency_key = :idempotency_key
                """
                ),
                {
                    "organization_id": organization_id,
                    "registration_operation": _REGISTRATION_OPERATION,
                    "idempotency_key": idempotency_key,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return cast(Mapping[str, object], row)

    @staticmethod
    def _manifest(row: Mapping[str, object]) -> SourceManifest:
        capabilities = row["capability_manifest"]
        if type(capabilities) is not dict:
            raise SourceControlUnavailable(
                "stored File capability declaration is not recognized"
            )
        declaration_version_value = capabilities.get("declarationVersion")
        declaration_version = (
            declaration_version_value if type(declaration_version_value) is str else ""
        )
        capability_manifest = _KNOWN_CAPABILITY_DOCUMENTS.get(declaration_version)
        if (
            capability_manifest is None
            or capabilities != capability_manifest.document()
        ):
            raise SourceControlUnavailable(
                "stored File capability declaration is not recognized"
            )
        source_id = row["source_id"]
        version_id = row["version_id"]
        display_name = row["display_name"]
        source_kind = row["source_kind"]
        version_source_kind = row["version_source_kind"]
        root_ref = row["root_ref"]
        source_created_at = row["source_created_at"]
        version_created_at = row["version_created_at"]
        if (
            type(source_id) is not UUID
            or type(version_id) is not UUID
            or type(display_name) is not str
            or source_kind != "file"
            or version_source_kind != "file"
            or type(root_ref) is not str
            or type(source_created_at) is not datetime
            or type(version_created_at) is not datetime
            or version_created_at < source_created_at
        ):
            raise SourceControlUnavailable("stored File source manifest is invalid")
        return SourceManifest.registered_file(
            source_ref=SourceRef(source_id),
            version_ref=version_id,
            display_name=display_name,
            root_ref=FileRootRef(root_ref),
            created_at=source_created_at,
            version_created_at=version_created_at,
            capabilities=capability_manifest,
        )
