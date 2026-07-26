"""Public in-process ContextControl deep Module boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, cast

from engine.control.authority import (
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    TrustedControlCall,
    _validate_and_consume_control_call,
)
from engine.control.contracts import (
    ActivateFileChangeFeed,
    ActivateFileDeleteObservations,
    RegisterFileSource,
    SourceControlUnavailable,
    SourceManifest,
    SourceNotAvailable,
    SourceRef,
)
from engine.control.file_change_pages import (
    AcceptedChangePage,
    ChangePage,
    FileChangeControlProofs,
    VerifiedChangePage,
)
from engine.control.file_deletions import (
    ExecutedFileDeleteObservation,
    ExecuteFileDeleteObservation,
    FileResourceTombstone,
    TombstoneFileResource,
)
from engine.control.file_imports import (
    PreparedFileImport,
    PrepareFileImport,
    ScheduledFileChangePage,
    ScheduleFileChangePage,
)
from engine.control.file_source_offboarding import (
    FileSourceOffboarding,
    OffboardFileSource,
)
from engine.control.file_source_progress import FileSourceProgress


class ControlStorePort(Protocol):
    """Persistence operations visible only behind ContextControl."""

    def register_file_source(
        self,
        call: TrustedControlCall,
        command: RegisterFileSource,
    ) -> SourceManifest: ...

    def activate_file_change_feed(
        self,
        call: TrustedControlCall,
        command: ActivateFileChangeFeed,
    ) -> SourceManifest: ...

    def activate_file_delete_observations(
        self,
        call: TrustedControlCall,
        command: ActivateFileDeleteObservations,
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

    def schedule_file_change_page(
        self,
        call: TrustedControlCall,
        command: ScheduleFileChangePage,
    ) -> ScheduledFileChangePage: ...

    def tombstone_file_resource(
        self,
        call: TrustedControlCall,
        command: TombstoneFileResource,
    ) -> FileResourceTombstone: ...

    def execute_file_delete_observation(
        self,
        call: TrustedControlCall,
        command: ExecuteFileDeleteObservation,
    ) -> ExecutedFileDeleteObservation: ...

    def read_file_source_progress(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> FileSourceProgress: ...

    def offboard_file_source(
        self,
        call: TrustedControlCall,
        command: OffboardFileSource,
    ) -> FileSourceOffboarding: ...


class FileChangePageStorePort(Protocol):
    """Optional v3 persistence surface activated with File change proofs."""

    def accept_file_change_page(
        self,
        call: TrustedControlCall,
        page: VerifiedChangePage,
    ) -> AcceptedChangePage: ...


class ContextControl:
    """Own trusted File enrollment, read-back, and import preparation."""

    __slots__ = ("_authority", "_clock", "_file_change_proofs", "_store")

    def __init__(
        self,
        *,
        store: ControlStorePort,
        authority: ControlOperatorAuthority,
        clock: Callable[[], datetime],
        file_change_proofs: FileChangeControlProofs | None = None,
    ) -> None:
        required_methods = [
            "activate_file_change_feed",
            "activate_file_delete_observations",
            "execute_file_delete_observation",
            "offboard_file_source",
            "prepare_file_import",
            "register_file_source",
            "read_source",
            "read_file_source_progress",
            "tombstone_file_resource",
        ]
        if file_change_proofs is not None:
            required_methods.extend(
                ("accept_file_change_page", "schedule_file_change_page")
            )
        for method_name in required_methods:
            if not callable(getattr(store, method_name, None)):
                raise TypeError("ContextControl store is incomplete")
        if type(authority) is not ControlOperatorAuthority:
            raise TypeError("ContextControl requires ControlOperatorAuthority")
        if not callable(clock):
            raise TypeError("ContextControl clock must be callable")
        if file_change_proofs is not None and type(
            file_change_proofs
        ) is not FileChangeControlProofs:
            raise TypeError("ContextControl File change proofs are invalid")
        self._store = store
        self._authority = authority
        self._clock = clock
        self._file_change_proofs = file_change_proofs

    def accept_file_change_page(
        self,
        call: TrustedControlCall,
        page: ChangePage,
    ) -> AcceptedChangePage:
        """Verify and durably accept a whole page before issuing continuation."""

        if type(page) is not ChangePage:
            raise TypeError("accept_file_change_page requires ChangePage")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                checked_at=self._clock(),
            )
            proofs = self._file_change_proofs
            if proofs is None or page.organization_id != call.organization_id:
                raise SourceNotAvailable
            verified = proofs.verify_page(page)
            if verified is None:
                raise SourceNotAvailable
            accepted = cast(
                FileChangePageStorePort,
                self._store,
            ).accept_file_change_page(call, verified)
            if (
                type(accepted) is not AcceptedChangePage
                or accepted.source_ref != SourceRef(page.source_ref)
                or accepted.source_version_ref != page.source_version_ref
                or accepted.scan_ref != page.scan_ref
                or accepted.scan_epoch != page.scan_epoch
                or accepted.page_limit != page.page_limit
                or (
                    page.predecessor_page_ref is None
                    and accepted.superseded_scan_epoch
                    != page.superseded_scan_epoch
                )
                or accepted.page_ref != verified.page_ref
                or accepted.change_count != len(page.changes)
                or accepted.complete is not page.complete
            ):
                raise SourceControlUnavailable(
                    "source store returned mismatched File page acceptance"
                )
            return accepted
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File change page acceptance is unavailable"
            ) from None

    def activate_file_change_feed(
        self,
        call: TrustedControlCall,
        command: ActivateFileChangeFeed,
    ) -> SourceManifest:
        """Activate only the server-owned immutable File change capability."""

        if type(command) is not ActivateFileChangeFeed:
            raise TypeError(
                "activate_file_change_feed requires ActivateFileChangeFeed"
            )
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
                checked_at=self._clock(),
            )
            manifest = self._store.activate_file_change_feed(call, command)
            self._require_manifest(manifest)
            if manifest.source_ref != command.source_ref:
                raise SourceControlUnavailable(
                    "source store returned a mismatched File change manifest"
                )
            return manifest
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File change feed activation is unavailable"
            ) from None

    def activate_file_delete_observations(
        self,
        call: TrustedControlCall,
        command: ActivateFileDeleteObservations,
    ) -> SourceManifest:
        """Explicitly advance a v3 File source to delete observations."""

        if type(command) is not ActivateFileDeleteObservations:
            raise TypeError(
                "activate_file_delete_observations requires "
                "ActivateFileDeleteObservations"
            )
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=(
                    ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS
                ),
                checked_at=self._clock(),
            )
            manifest = self._store.activate_file_delete_observations(
                call,
                command,
            )
            self._require_manifest(manifest)
            if manifest.source_ref != command.source_ref:
                raise SourceControlUnavailable(
                    "source store returned a mismatched delete observation manifest"
                )
            return manifest
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File delete observation activation is unavailable"
            ) from None

    def offboard_file_source(
        self,
        call: TrustedControlCall,
        command: OffboardFileSource,
    ) -> FileSourceOffboarding:
        """Disable one File source synchronously and queue retained cleanup."""

        if type(command) is not OffboardFileSource:
            raise TypeError("offboard_file_source requires OffboardFileSource")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.OFFBOARD_FILE_SOURCE,
                checked_at=self._clock(),
            )
            result = self._store.offboard_file_source(call, command)
            if (
                type(result) is not FileSourceOffboarding
                or result.organization_id != call.organization_id
                or result.source_ref != command.source_ref
            ):
                raise SourceControlUnavailable(
                    "source store returned mismatched File offboarding"
                )
            return result
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File source offboarding is unavailable"
            ) from None

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

    def schedule_file_change_page(
        self,
        call: TrustedControlCall,
        command: ScheduleFileChangePage,
    ) -> ScheduledFileChangePage:
        """Schedule one complete accepted page under explicit Control authority."""

        if type(command) is not ScheduleFileChangePage:
            raise TypeError(
                "schedule_file_change_page requires ScheduleFileChangePage"
            )
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.SCHEDULE_FILE_CHANGE_PAGE,
                checked_at=self._clock(),
            )
            scheduled = self._store.schedule_file_change_page(call, command)
            if (
                type(scheduled) is not ScheduledFileChangePage
                or scheduled.organization_id != call.organization_id
                or scheduled.source_ref != command.source_ref
                or scheduled.source_version_ref != command.source_version_ref
                or scheduled.page_ref != command.page_ref
            ):
                raise SourceControlUnavailable(
                    "source store returned a mismatched scheduled File page"
                )
            return scheduled
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File change page scheduling is unavailable"
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

    def execute_file_delete_observation(
        self,
        call: TrustedControlCall,
        command: ExecuteFileDeleteObservation,
    ) -> ExecutedFileDeleteObservation:
        """Execute one current accepted delete through tombstone authority."""

        if type(command) is not ExecuteFileDeleteObservation:
            raise TypeError(
                "execute_file_delete_observation requires ExecuteFileDeleteObservation"
            )
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=(ControlOperation.EXECUTE_FILE_DELETE_OBSERVATION),
                checked_at=self._clock(),
            )
            result = self._store.execute_file_delete_observation(call, command)
            if (
                type(result) is not ExecutedFileDeleteObservation
                or result.organization_id != call.organization_id
                or result.source_ref != command.source_ref
                or result.source_version_ref != command.source_version_ref
                or result.page_ref != command.page_ref
                or result.change_ordinal != command.change_ordinal
            ):
                raise SourceControlUnavailable(
                    "source store returned a mismatched File delete execution"
                )
            return result
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File delete observation execution is unavailable"
            ) from None

    @staticmethod
    def _require_manifest(manifest: object) -> None:
        if type(manifest) is not SourceManifest:
            raise SourceControlUnavailable("source store returned an invalid manifest")
