"""PostgreSQL authority for the bounded persistent no-op WorkerLease carrier."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.control import PreparedFileImport
from engine.persistence.role_guard import assert_control_role, assert_worker_role
from engine.supply.jobs import (
    FILE_IMPORT_WORKER_LEASE_OPERATION,
    WORKER_LEASE_ACTOR_KIND,
    WORKER_LEASE_OPERATION,
    WorkerLeaseClaims,
    WorkerLeaseCodec,
    WorkerLeaseRejectionAuditReceipt,
    WorkerLeaseToken,
    WorkNotAvailable,
    _require_identifier,
    _require_utc,
    _require_uuid,
    generate_worker_lease_nonce,
    worker_lease_digest,
)

DEFAULT_WORKER_LEASE_TTL_SECONDS: Final = 300
MAX_WORKER_LEASE_TTL_SECONDS: Final = 3600
def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True, slots=True)
class WorkerLeaseIssueRequest:
    """Trusted durable-row locator; the issuer owns lease time and entropy."""

    organization_id: UUID
    job_id: UUID
    service_principal_id: UUID
    workload: str
    worker_audience: str
    actor_kind: Literal["service"] = field(
        default=WORKER_LEASE_ACTOR_KIND, init=False
    )
    operation: Literal["noop.complete"] = field(
        default=WORKER_LEASE_OPERATION, init=False
    )

    def __post_init__(self) -> None:
        _require_uuid("organization_id", self.organization_id)
        _require_uuid("job_id", self.job_id)
        _require_uuid("service_principal_id", self.service_principal_id)
        _require_identifier("workload", self.workload, maximum_length=128)
        _require_identifier(
            "worker_audience", self.worker_audience, maximum_length=255
        )


@dataclass(frozen=True, slots=True)
class WorkerExecutionIdentity:
    """Authority-owned receiver identity, never copied from a lease message."""

    service_principal_id: UUID = field(repr=False)
    workload: str = field(repr=False)
    worker_audience: str = field(repr=False)
    operation: Literal["noop.complete"] = field(
        default=WORKER_LEASE_OPERATION, init=False, repr=False
    )

    def __post_init__(self) -> None:
        _require_uuid("service_principal_id", self.service_principal_id)
        _require_identifier("workload", self.workload, maximum_length=128)
        _require_identifier(
            "worker_audience", self.worker_audience, maximum_length=255
        )


@dataclass(frozen=True, slots=True)
class WorkerLeaseRedemption:
    """Untrusted carrier: opaque lease plus independent durable-job routing."""

    token: WorkerLeaseToken = field(repr=False)
    expected_organization_id: UUID = field(repr=False)
    expected_job_id: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.token) is not WorkerLeaseToken:
            raise TypeError("token must be WorkerLeaseToken")
        _require_uuid("expected_organization_id", self.expected_organization_id)
        _require_uuid("expected_job_id", self.expected_job_id)


class WorkerNoOpOutcome(StrEnum):
    """Closed success category for the only Issue #17 durable effect."""

    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class WorkerNoOpAuditReceipt:
    """Restricted completion audit with no raw claims, nonce, or credentials."""

    lease_digest: str
    outcome: WorkerNoOpOutcome = WorkerNoOpOutcome.COMPLETED

    def __post_init__(self) -> None:
        if type(self.lease_digest) is not str or len(self.lease_digest) != 64:
            raise ValueError("lease digest must be lowercase SHA-256")
        if any(
            character not in "0123456789abcdef"
            for character in self.lease_digest
        ):
            raise ValueError("lease digest must be lowercase SHA-256")
        if self.outcome is not WorkerNoOpOutcome.COMPLETED:
            raise ValueError("worker no-op audit outcome must remain closed")


@dataclass(frozen=True, slots=True)
class WorkerNoOpCompletion:
    """Closed result proving exactly one committed no-op transition."""

    audit_receipt: WorkerNoOpAuditReceipt
    effect_count: Literal[1] = 1

    def __post_init__(self) -> None:
        if type(self.audit_receipt) is not WorkerNoOpAuditReceipt:
            raise TypeError("audit_receipt must be WorkerNoOpAuditReceipt")
        if self.effect_count != 1:
            raise ValueError("a completed worker no-op has exactly one effect")


class WorkerLeaseIssueNotAvailable(Exception):
    """The requested durable row could not receive a lease."""

    def __init__(self) -> None:
        super().__init__("work not available")


class WorkerLeaseAuthorityUnavailable(RuntimeError):
    """A trusted lease database authority could not complete safely."""


def _rejection(token: WorkerLeaseToken) -> WorkNotAvailable:
    return WorkNotAvailable(
        WorkerLeaseRejectionAuditReceipt(lease_digest=worker_lease_digest(token))
    )


class PostgreSQLWorkerLeaseIssuer:
    """Mint one DB-timed lease through the dedicated Control function."""

    def __init__(
        self,
        control_engine: Engine,
        codec: WorkerLeaseCodec,
        *,
        lease_ttl_seconds: int = DEFAULT_WORKER_LEASE_TTL_SECONDS,
    ) -> None:
        if type(codec) is not WorkerLeaseCodec:
            raise TypeError("codec must be WorkerLeaseCodec")
        if (
            type(lease_ttl_seconds) is not int
            or not 1 <= lease_ttl_seconds <= MAX_WORKER_LEASE_TTL_SECONDS
        ):
            raise ValueError("worker lease TTL must be between 1 and 3600 seconds")
        self._control_engine = control_engine
        self._codec = codec
        self._lease_ttl_seconds = lease_ttl_seconds

    def issue_noop_lease(
        self, request: WorkerLeaseIssueRequest
    ) -> WorkerLeaseToken:
        """Atomically lease one available row and sign its DB-owned times."""

        if type(request) is not WorkerLeaseIssueRequest:
            raise TypeError("lease issuance requires WorkerLeaseIssueRequest")
        nonce = generate_worker_lease_nonce()
        try:
            with self._control_engine.begin() as connection:
                self._require_control_role(connection)
                row = connection.execute(
                    text(
                        """
                        SELECT issued_at, expires_at
                        FROM public.context_worker_issue_noop_lease(
                            :organization_id,
                            :job_id,
                            :service_principal_id,
                            :workload,
                            :worker_audience,
                            :signing_key_version,
                            :nonce,
                            :lease_ttl_seconds
                        )
                        """
                    ),
                    {
                        "organization_id": request.organization_id,
                        "job_id": request.job_id,
                        "service_principal_id": request.service_principal_id,
                        "workload": request.workload,
                        "worker_audience": request.worker_audience,
                        "signing_key_version": (
                            self._codec.active_signing_key_version
                        ),
                        "nonce": nonce,
                        "lease_ttl_seconds": self._lease_ttl_seconds,
                    },
                ).one_or_none()
                if row is None:
                    raise WorkerLeaseIssueNotAvailable
                issued_at = _require_utc("issued_at", row.issued_at)
                expires_at = _require_utc("expires_at", row.expires_at)
                claims = WorkerLeaseClaims(
                    signing_key_version=self._codec.active_signing_key_version,
                    organization_id=request.organization_id,
                    job_id=request.job_id,
                    service_principal_id=request.service_principal_id,
                    workload=request.workload,
                    worker_audience=request.worker_audience,
                    issued_at=issued_at,
                    expires_at=expires_at,
                    nonce=nonce,
                )
                token = self._codec.mint(claims)
            return token
        except (WorkerLeaseIssueNotAvailable, WorkerLeaseAuthorityUnavailable):
            raise
        except SQLAlchemyError:
            raise WorkerLeaseAuthorityUnavailable(
                "worker lease issuance database work failed"
            ) from None

    def issue_file_import_lease(
        self,
        prepared: PreparedFileImport,
    ) -> WorkerLeaseToken:
        """Lease one prepared File import with exact Source binding."""

        if type(prepared) is not PreparedFileImport:
            raise TypeError("File import lease requires PreparedFileImport")
        nonce = generate_worker_lease_nonce()
        try:
            with self._control_engine.begin() as connection:
                self._require_control_role(connection)
                row = connection.execute(
                    text(
                        """
                        SELECT issued_at, expires_at
                        FROM public.context_worker_issue_file_import_lease(
                            :organization_id, :job_id,
                            :service_principal_id, :source_ref,
                            :signing_key_version, :nonce,
                            :lease_ttl_seconds
                        )
                        """
                    ),
                    {
                        "organization_id": prepared.organization_id,
                        "job_id": prepared.job_id,
                        "service_principal_id": prepared.service_principal_id,
                        "source_ref": str(prepared.source_ref.value),
                        "signing_key_version": (
                            self._codec.active_signing_key_version
                        ),
                        "nonce": nonce,
                        "lease_ttl_seconds": self._lease_ttl_seconds,
                    },
                ).one_or_none()
                if row is None:
                    raise WorkerLeaseIssueNotAvailable
                claims = WorkerLeaseClaims(
                    signing_key_version=self._codec.active_signing_key_version,
                    organization_id=prepared.organization_id,
                    job_id=prepared.job_id,
                    service_principal_id=prepared.service_principal_id,
                    workload=prepared.workload,
                    worker_audience=prepared.worker_audience,
                    issued_at=_require_utc("issued_at", row.issued_at),
                    expires_at=_require_utc("expires_at", row.expires_at),
                    nonce=nonce,
                    operation=FILE_IMPORT_WORKER_LEASE_OPERATION,
                    source_ref=str(prepared.source_ref.value),
                )
                token = self._codec.mint(claims)
            return token
        except (WorkerLeaseIssueNotAvailable, WorkerLeaseAuthorityUnavailable):
            raise
        except SQLAlchemyError:
            raise WorkerLeaseAuthorityUnavailable(
                "File import lease issuance database work failed"
            ) from None

    @staticmethod
    def _require_control_role(connection: Connection) -> None:
        try:
            assert_control_role(connection)
        except AssertionError as error:
            raise WorkerLeaseAuthorityUnavailable(
                "worker lease issuer is not the dedicated control role"
            ) from error


class PostgreSQLWorkerLeaseAuthority:
    """Redeem one lease for an injected receiver through worker-role FORCE RLS."""

    def __init__(
        self,
        worker_engine: Engine,
        codec: WorkerLeaseCodec,
        execution_identity: WorkerExecutionIdentity,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if type(codec) is not WorkerLeaseCodec:
            raise TypeError("codec must be WorkerLeaseCodec")
        if type(execution_identity) is not WorkerExecutionIdentity:
            raise TypeError("execution_identity must be WorkerExecutionIdentity")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._worker_engine = worker_engine
        self._codec = codec
        self._execution_identity = execution_identity
        self._clock = clock

    def complete_noop(
        self, redemption: WorkerLeaseRedemption
    ) -> WorkerNoOpCompletion:
        """Verify before DB access, then invoke the narrow atomic completion."""

        if type(redemption) is not WorkerLeaseRedemption:
            raise TypeError("lease redemption requires WorkerLeaseRedemption")
        checked_at = _require_utc("worker authority clock", self._clock())
        identity = self._execution_identity
        claims = self._codec.verify(
            redemption.token,
            expected_organization_id=redemption.expected_organization_id,
            expected_job_id=redemption.expected_job_id,
            expected_service_principal_id=identity.service_principal_id,
            expected_workload=identity.workload,
            expected_operation=identity.operation,
            expected_worker_audience=identity.worker_audience,
            now=checked_at,
        )
        try:
            with self._worker_engine.begin() as connection:
                self._require_worker_role(connection)
                row = connection.execute(
                    text(
                        """
                        SELECT effect_count
                        FROM public.context_worker_complete_noop_job(
                            :organization_id,
                            :job_id,
                            :signing_key_version,
                            :nonce,
                            :issued_at,
                            :expires_at
                        )
                        """
                    ),
                    {
                        "organization_id": claims.organization_id,
                        "job_id": claims.job_id,
                        "signing_key_version": claims.signing_key_version,
                        "nonce": claims.nonce,
                        "issued_at": claims.issued_at,
                        "expires_at": claims.expires_at,
                    },
                ).one_or_none()
                if row is None or row.effect_count != 1:
                    raise _rejection(redemption.token)
            return WorkerNoOpCompletion(
                audit_receipt=WorkerNoOpAuditReceipt(
                    lease_digest=worker_lease_digest(redemption.token)
                )
            )
        except (WorkNotAvailable, WorkerLeaseAuthorityUnavailable):
            raise
        except SQLAlchemyError:
            raise WorkerLeaseAuthorityUnavailable(
                "worker lease redemption database work failed"
            ) from None

    @staticmethod
    def _require_worker_role(connection: Connection) -> None:
        try:
            assert_worker_role(connection)
        except AssertionError as error:
            raise WorkerLeaseAuthorityUnavailable(
                "worker lease authority is not the dedicated worker role"
            ) from error
