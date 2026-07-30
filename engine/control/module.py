"""Public in-process ContextControl deep Module boundary."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from engine.control.article_access_policy import (
    SetSourceArticlePolicyDefault,
    SetTenantArticlePolicyDefault,
)
from engine.control.authority import (
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    TrustedControlCall,
    _validate_and_consume_control_call,
)
from engine.control.bulk_article_policy import (
    BulkArticlePolicyChange,
    BulkArticlePolicyCommit,
    BulkArticlePolicyConfirmation,
    BulkArticlePolicyPreview,
    BulkArticlePolicyResult,
    _issue_bulk_article_policy_commit,
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


class SourceListingStorePort(Protocol):
    """Optional Organization-scoped discovery under existing source-read authority."""

    def list_file_sources(
        self,
        call: TrustedControlCall,
    ) -> tuple[SourceManifest, ...]: ...


class FileChangePageStorePort(Protocol):
    """Optional v3 persistence surface activated with File change proofs."""

    def accept_file_change_page(
        self,
        call: TrustedControlCall,
        page: VerifiedChangePage,
    ) -> AcceptedChangePage: ...

    def report_file_scan_bound_refusal(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
        scan_bound: int,
    ) -> None: ...

    def clear_file_scan_bound_refusal(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> None: ...


class ArticlePolicyDefaultStorePort(Protocol):
    """Narrow persistence capability for future-Article default writes."""

    def set_tenant_article_policy_default(
        self,
        call: TrustedControlCall,
        command: SetTenantArticlePolicyDefault,
    ) -> int: ...

    def set_source_article_policy_default(
        self,
        call: TrustedControlCall,
        command: SetSourceArticlePolicyDefault,
    ) -> int: ...


class BulkArticlePolicyStorePort(Protocol):
    """Sole preview/commit persistence capability for historical policies."""

    def preview_bulk_article_policy_change(
        self,
        organization_id: UUID,
        command: BulkArticlePolicyChange,
    ) -> BulkArticlePolicyPreview: ...

    def change_access(
        self,
        command: BulkArticlePolicyCommit,
    ) -> BulkArticlePolicyResult: ...


class ContextControl:
    """Own trusted File enrollment, read-back, and import preparation."""

    __slots__ = (
        "_article_policy_store",
        "_authority",
        "_bulk_article_policy_store",
        "_clock",
        "_file_change_proofs",
        "_store",
    )

    def __init__(
        self,
        *,
        store: ControlStorePort,
        authority: ControlOperatorAuthority,
        clock: Callable[[], datetime],
        file_change_proofs: FileChangeControlProofs | None = None,
        article_policy_store: ArticlePolicyDefaultStorePort | None = None,
        bulk_article_policy_store: BulkArticlePolicyStorePort | None = None,
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
                (
                    "accept_file_change_page",
                    "clear_file_scan_bound_refusal",
                    "report_file_scan_bound_refusal",
                    "schedule_file_change_page",
                )
            )
        for method_name in required_methods:
            if not callable(getattr(store, method_name, None)):
                raise TypeError("ContextControl store is incomplete")
        if type(authority) is not ControlOperatorAuthority:
            raise TypeError("ContextControl requires ControlOperatorAuthority")
        if not callable(clock):
            raise TypeError("ContextControl clock must be callable")
        if (
            file_change_proofs is not None
            and type(file_change_proofs) is not FileChangeControlProofs
        ):
            raise TypeError("ContextControl File change proofs are invalid")
        if article_policy_store is not None:
            for method_name in (
                "set_source_article_policy_default",
                "set_tenant_article_policy_default",
            ):
                if not callable(getattr(article_policy_store, method_name, None)):
                    raise TypeError("Article policy default store is incomplete")
        if bulk_article_policy_store is not None:
            for method_name in (
                "change_access",
                "preview_bulk_article_policy_change",
            ):
                if not callable(getattr(bulk_article_policy_store, method_name, None)):
                    raise TypeError("bulk Article policy store is incomplete")
        self._store = store
        self._article_policy_store = article_policy_store
        self._bulk_article_policy_store = bulk_article_policy_store
        self._authority = authority
        self._clock = clock
        self._file_change_proofs = file_change_proofs

    def preview_bulk_article_policy_change(
        self,
        call: TrustedControlCall,
        command: BulkArticlePolicyChange,
    ) -> BulkArticlePolicyPreview:
        if type(command) is not BulkArticlePolicyChange:
            raise TypeError("bulk Article preview requires its exact command")
        command.__post_init__()
        self._consume_article_policy_call(
            call, ControlOperation.PREVIEW_BULK_ARTICLE_POLICY_CHANGE
        )
        store = self._bulk_article_policy_store
        if store is None:
            raise SourceControlUnavailable("bulk Article policy is unavailable")
        result = self._invoke_article_policy_store(
            lambda: store.preview_bulk_article_policy_change(
                call.organization_id, command
            )
        )
        if type(result) is not BulkArticlePolicyPreview:
            raise SourceControlUnavailable("bulk Article preview was not produced")
        result.__post_init__()
        if result.organization_id != call.organization_id:
            raise SourceControlUnavailable("bulk Article preview was not produced")
        return result

    def commit_bulk_article_policy_change(
        self,
        call: TrustedControlCall,
        command: BulkArticlePolicyChange,
        confirmation: BulkArticlePolicyConfirmation,
    ) -> BulkArticlePolicyResult:
        if (
            type(command) is not BulkArticlePolicyChange
            or type(confirmation) is not BulkArticlePolicyConfirmation
        ):
            raise TypeError("bulk Article commit requires command and confirmation")
        command.__post_init__()
        confirmation.__post_init__()
        self._consume_article_policy_call(
            call, ControlOperation.COMMIT_BULK_ARTICLE_POLICY_CHANGE
        )
        store = self._bulk_article_policy_store
        if store is None:
            raise SourceControlUnavailable("bulk Article policy is unavailable")
        preview = self._invoke_article_policy_store(
            lambda: store.preview_bulk_article_policy_change(
                call.organization_id, command
            )
        )
        if type(preview) is not BulkArticlePolicyPreview:
            raise SourceControlUnavailable("bulk Article preview was not produced")
        preview.__post_init__()
        if (
            preview.organization_id != call.organization_id
            or not hmac.compare_digest(
                preview.digest, confirmation.preview_digest
            )
        ):
            raise SourceNotAvailable
        commit = _issue_bulk_article_policy_commit(
            organization_id=call.organization_id,
            preview=preview,
            operator_ref=call.operator_ref,
            authority_ref=call.authority_ref,
            request_id=call.request_id,
        )
        try:
            result = self._invoke_article_policy_store(
                lambda: store.change_access(commit)
            )
        finally:
            object.__setattr__(commit, "_seal", b"")
        if type(result) is not BulkArticlePolicyResult:
            raise SourceControlUnavailable("bulk Article policy was not changed")
        result.__post_init__()
        return result

    def set_tenant_article_policy_default(
        self,
        call: TrustedControlCall,
        command: SetTenantArticlePolicyDefault,
    ) -> int:
        if type(command) is not SetTenantArticlePolicyDefault:
            raise TypeError("tenant Article default requires its exact command")
        self._consume_article_policy_call(
            call, ControlOperation.SET_TENANT_ARTICLE_POLICY_DEFAULT
        )
        store = self._article_policy_store
        if store is None:
            raise SourceControlUnavailable("Article policy defaults are unavailable")
        result = self._invoke_article_policy_store(
            lambda: store.set_tenant_article_policy_default(call, command)
        )
        if type(result) is not int:
            raise SourceControlUnavailable("tenant Article default was not changed")
        return result

    def set_source_article_policy_default(
        self,
        call: TrustedControlCall,
        command: SetSourceArticlePolicyDefault,
    ) -> int:
        if type(command) is not SetSourceArticlePolicyDefault:
            raise TypeError("source Article default requires its exact command")
        self._consume_article_policy_call(
            call, ControlOperation.SET_SOURCE_ARTICLE_POLICY_DEFAULT
        )
        store = self._article_policy_store
        if store is None:
            raise SourceControlUnavailable("Article policy defaults are unavailable")
        result = self._invoke_article_policy_store(
            lambda: store.set_source_article_policy_default(call, command)
        )
        if type(result) is not int:
            raise SourceControlUnavailable("source Article default was not changed")
        return result

    def _consume_article_policy_call(
        self,
        call: TrustedControlCall,
        operation: ControlOperation,
    ) -> None:
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=operation,
                checked_at=self._clock(),
            )
        except ControlOperatorAuthenticationRejected:
            raise SourceNotAvailable from None

    @staticmethod
    def _invoke_article_policy_store(work: Callable[[], object]) -> object:
        try:
            return work()
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "Article policy administration is unavailable"
            ) from None

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
                or accepted.scan_bound != page.scan_bound
                or accepted.page_limit != page.page_limit
                or (
                    page.predecessor_page_ref is None
                    and accepted.superseded_scan_epoch != page.superseded_scan_epoch
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

    def report_file_scan_bound_refusal(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
        scan_bound: int,
    ) -> None:
        """Retain one closed operator condition under exact page-accept authority."""

        if type(source_ref) is not SourceRef or type(scan_bound) is not int:
            raise TypeError("File scan bound refusal is invalid")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                checked_at=self._clock(),
            )
            cast(FileChangePageStorePort, self._store).report_file_scan_bound_refusal(
                call,
                source_ref,
                scan_bound,
            )
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File scan bound refusal reporting is unavailable"
            ) from None

    def clear_file_scan_bound_refusal(
        self,
        call: TrustedControlCall,
        source_ref: SourceRef,
    ) -> None:
        """Clear the closed condition after a complete snapshot revalidation."""

        if type(source_ref) is not SourceRef:
            raise TypeError("File scan bound refusal clear is invalid")
        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
                checked_at=self._clock(),
            )
            cast(FileChangePageStorePort, self._store).clear_file_scan_bound_refusal(
                call,
                source_ref,
            )
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable(
                "File scan bound refusal clear is unavailable"
            ) from None

    def activate_file_change_feed(
        self,
        call: TrustedControlCall,
        command: ActivateFileChangeFeed,
    ) -> SourceManifest:
        """Activate only the server-owned immutable File change capability."""

        if type(command) is not ActivateFileChangeFeed:
            raise TypeError("activate_file_change_feed requires ActivateFileChangeFeed")
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
                expected_operation=(ControlOperation.ACTIVATE_FILE_DELETE_OBSERVATIONS),
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

    def list_sources(
        self,
        call: TrustedControlCall,
    ) -> tuple[SourceManifest, ...]:
        """List active source manifests under one exact existing read call."""

        try:
            _validate_and_consume_control_call(
                call,
                authority=self._authority,
                expected_operation=ControlOperation.READ_SOURCE,
                checked_at=self._clock(),
            )
            method = getattr(self._store, "list_file_sources", None)
            if not callable(method):
                raise SourceControlUnavailable("source listing is unavailable")
            manifests = cast(SourceListingStorePort, self._store).list_file_sources(
                call
            )
            if type(manifests) is not tuple:
                raise SourceControlUnavailable("source listing is invalid")
            for manifest in manifests:
                self._require_manifest(manifest)
            source_refs = tuple(manifest.source_ref.value for manifest in manifests)
            if (
                source_refs != tuple(sorted(source_refs))
                or len(set(source_refs)) != len(source_refs)
            ):
                raise SourceControlUnavailable("source listing is invalid")
            return manifests
        except (ControlOperatorAuthenticationRejected, SourceNotAvailable):
            raise SourceNotAvailable from None
        except SourceControlUnavailable:
            raise
        except Exception:
            raise SourceControlUnavailable("source listing is unavailable") from None

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
            raise TypeError("schedule_file_change_page requires ScheduleFileChangePage")
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
