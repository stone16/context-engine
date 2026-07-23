"""Public in-process ContextControl deep Module boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from engine.control.authority import (
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    TrustedControlCall,
    _validate_and_consume_control_call,
)
from engine.control.contracts import (
    RegisterFileSource,
    SourceControlUnavailable,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
)
from engine.control.file_deletions import (
    FileResourceTombstone,
    TombstoneFileResource,
)
from engine.control.file_imports import PreparedFileImport, PrepareFileImport
from engine.control.file_source_progress import FileSourceProgress


class ControlStorePort(Protocol):
    """Persistence operations visible only behind ContextControl."""

    def register_file_source(
        self,
        call: TrustedControlCall,
        command: RegisterFileSource,
    ) -> SourceManifest: ...

    def read_source(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> SourceManifest: ...

    def prepare_file_import(
        self,
        call: TrustedControlCall,
        command: PrepareFileImport,
    ) -> PreparedFileImport: ...

    def tombstone_file_resource(
        self,
        call: TrustedControlCall,
        command: TombstoneFileResource,
    ) -> FileResourceTombstone: ...

    def read_file_source_progress(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> FileSourceProgress: ...


class ContextControl:
    """Own trusted File enrollment, read-back, and import preparation."""

    __slots__ = ("_authority", "_clock", "_store")

    def __init__(
        self,
        *,
        store: ControlStorePort,
        authority: ControlOperatorAuthority,
        clock: Callable[[], datetime],
    ) -> None:
        for method_name in (
            "prepare_file_import",
            "register_file_source",
            "read_source",
            "read_file_source_progress",
            "tombstone_file_resource",
        ):
            if not callable(getattr(store, method_name, None)):
                raise TypeError("ContextControl store is incomplete")
        if type(authority) is not ControlOperatorAuthority:
            raise TypeError("ContextControl requires ControlOperatorAuthority")
        if not callable(clock):
            raise TypeError("ContextControl clock must be callable")
        self._store = store
        self._authority = authority
        self._clock = clock

    def register_source(
        self,
        call: TrustedControlCall,
        command: RegisterFileSource,
    ) -> SourceManifest:
        """Register one exact File source or expose one generic refusal."""

        if type(command) is not RegisterFileSource:
            raise TypeError("register_source requires RegisterFileSource")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.REGISTER_SOURCE,
                checked_at=self._clock(),
            )
            manifest = self._store.register_file_source(call, command)
            self._require_manifest(manifest)
            return manifest
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "source registration is unavailable"
            ) from None

    def read_source(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> SourceManifest:
        """Read one source in the trusted Organization or refuse generically."""

        if type(source_ref) is not SourceRef:
            raise TypeError("read_source requires SourceRef")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.READ_SOURCE,
                checked_at=self._clock(),
            )
            manifest = self._store.read_source(call, source_ref)
            self._require_manifest(manifest)
            if manifest.source_ref != source_ref:
                raise SourceControlUnavailable(
                    "source store returned a mismatched manifest"
                )
            return manifest
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable("source read is unavailable") from None

    def prepare_file_import(
        self,
        call: TrustedControlCall,
        command: PrepareFileImport,
    ) -> PreparedFileImport:
        """Create one durable acquisition/job under trusted Control authority."""

        if type(command) is not PrepareFileImport:
            raise TypeError("prepare_file_import requires PrepareFileImport")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.IMPORT_FILE,
                checked_at=self._clock(),
            )
            prepared = self._store.prepare_file_import(call, command)
            if type(prepared) is not PreparedFileImport:
                raise SourceControlUnavailable(
                    "source store returned an invalid File import"
                )
            if (
                prepared.organization_id != call.organization_id
                or prepared.source_ref != command.source_ref
            ):
                raise SourceControlUnavailable(
                    "source store returned a mismatched File import"
                )
            return prepared
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File import preparation is unavailable"
            ) from None

    def read_file_source_progress(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> FileSourceProgress:
        """Read separate durable acceptance and visibility progress signals."""

        if type(source_ref) is not SourceRef:
            raise TypeError("read_file_source_progress requires SourceRef")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.READ_SOURCE_PROGRESS,
                checked_at=self._clock(),
            )
            progress = self._store.read_file_source_progress(call, source_ref)
            if (
                type(progress) is not FileSourceProgress
                or progress.organization_id != call.organization_id
                or progress.source_ref != source_ref
            ):
                raise SourceControlUnavailable(
                    "source store returned mismatched File progress"
                )
            return progress
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File Source progress read is unavailable"
            ) from None

    def tombstone_file_resource(
        self,
        call: TrustedControlCall,
        command: TombstoneFileResource,
    ) -> FileResourceTombstone:
        """Make one published File Resource immediately invisible."""

        if type(command) is not TombstoneFileResource:
            raise TypeError("tombstone_file_resource requires TombstoneFileResource")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.TOMBSTONE_FILE_RESOURCE,
                checked_at=self._clock(),
            )
            result = self._store.tombstone_file_resource(call, command)
            if type(result) is not FileResourceTombstone:
                raise SourceControlUnavailable(
                    "source store returned an invalid File tombstone"
                )
            if (
                result.organization_id != call.organization_id
                or result.source_ref != command.source_ref
                or result.resource_ref != command.resource_ref
            ):
                raise SourceControlUnavailable(
                    "source store returned a mismatched File tombstone"
                )
            return result
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File Resource tombstone is unavailable"
            ) from None

    @staticmethod
    def _require_manifest(manifest: object) -> None:
        if type(manifest) is not SourceManifest:
            raise SourceControlUnavailable("source store returned an invalid manifest")
