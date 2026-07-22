"""PostgreSQL store for trusted ContextControl File source registration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

import rfc8785
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from engine.control import (
    FILE_CAPABILITY_MANIFEST,
    FILE_IMPORT_CAPABILITY_MANIFEST,
    FileRootRef,
    RegisterFileSource,
    SourceControlUnavailable,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
    TrustedControlCall,
)
from engine.persistence.role_guard import assert_control_role
from engine.supply import (
    FileImportReceiver,
    PreparedFileImport,
    PrepareFileImport,
)

_REGISTRATION_OPERATION = "register_source"
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
"""


def _capability_document() -> dict[str, object]:
    return FILE_CAPABILITY_MANIFEST.document()


_REGISTRATION_CAPABILITY_DOCUMENT = FILE_CAPABILITY_MANIFEST.document()
_KNOWN_CAPABILITY_DOCUMENTS = {
    FILE_CAPABILITY_MANIFEST.declaration_version: FILE_CAPABILITY_MANIFEST,
    FILE_IMPORT_CAPABILITY_MANIFEST.declaration_version: (
        FILE_IMPORT_CAPABILITY_MANIFEST
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
        b"context-engine.register-file-source.v1\x00"
        + rfc8785.dumps(document)
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
                row = connection.execute(
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
                ).mappings().one_or_none()
                if row is None:
                    raise SourceNotAvailable
                return self._manifest(cast(Mapping[str, object], row))
        except SourceNotAvailable:
            raise
        except (DBAPIError, SQLAlchemyError, AssertionError):
            raise SourceControlUnavailable(
                "File source read database authority is unavailable"
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

    @staticmethod
    def _select_registration(
        connection: Any,
        *,
        organization_id: UUID,
        idempotency_key: str,
    ) -> Mapping[str, object] | None:
        row = connection.execute(
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
        ).mappings().one_or_none()
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
            declaration_version_value
            if type(declaration_version_value) is str
            else ""
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
