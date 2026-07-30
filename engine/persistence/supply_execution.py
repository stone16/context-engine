"""PostgreSQL authority for the exact-job Supply execution/checkpoint bridge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from engine.persistence.role_guard import assert_control_role, assert_worker_role
from engine.supply.execution import (
    ConnectorAdapter,
    ConnectorCheckpointBinding,
    ConnectorCheckpointStore,
    StagedArtifact,
    StagedArtifactSink,
    SupplyBridgeExecution,
    SupplyChangePage,
    SupplyExecutionBoundExceeded,
    SupplyExecutionBoundReason,
    SupplyExecutionConfiguration,
    SupplyStagedPageByteLimitExceeded,
    serialize_supply_change_page,
)
from engine.supply.jobs import (
    SUPPLY_CONNECTOR_WORKER_LEASE_OPERATION,
    WorkerLeaseClaims,
    WorkerLeaseCodec,
    WorkerLeaseRejectionAuditReceipt,
    WorkerLeaseToken,
    WorkNotAvailable,
    _require_utc,
    generate_worker_lease_nonce,
    worker_lease_digest,
)

SUPPLY_CONNECTOR_WORKLOAD: Final = "supply.connector"
SUPPLY_CONNECTOR_WORKER_AUDIENCE: Final = "context-engine-connector-runner"
DEFAULT_SUPPLY_BRIDGE_LEASE_TTL_SECONDS: Final = 300
MAX_SUPPLY_BRIDGE_LEASE_TTL_SECONDS: Final = 3600


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _require_uuid(field_name: str, value: object) -> UUID:
    if type(value) is not UUID:
        raise TypeError(f"{field_name} must be UUID")
    return value


def _require_sha256(field_name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


def _rejection(token: WorkerLeaseToken) -> WorkNotAvailable:
    return WorkNotAvailable(
        WorkerLeaseRejectionAuditReceipt(lease_digest=worker_lease_digest(token))
    )


@dataclass(frozen=True, slots=True)
class SupplyBridgeLeaseIssueRequest:
    """Trusted exact connector-job locator for server-owned lease issuance."""

    organization_id: UUID = field(repr=False)
    source_id: UUID = field(repr=False)
    source_version_id: UUID = field(repr=False)
    worker_job_id: UUID = field(repr=False)
    service_principal_id: UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid("Supply bridge Organization", self.organization_id)
        _require_uuid("Supply bridge source", self.source_id)
        _require_uuid("Supply bridge SourceVersion", self.source_version_id)
        _require_uuid("Supply bridge WorkerJob", self.worker_job_id)
        _require_uuid("Supply bridge ServiceActor", self.service_principal_id)


@dataclass(frozen=True, slots=True)
class SupplyBridgeLeasePreemptionRequest(SupplyBridgeLeaseIssueRequest):
    """Explicit operator intent to replace one still-live connector lease."""

    reason_digest: str = field(repr=False)

    def __post_init__(self) -> None:
        SupplyBridgeLeaseIssueRequest.__post_init__(self)
        _require_sha256("Supply bridge preemption reason digest", self.reason_digest)


@dataclass(frozen=True, slots=True)
class SupplyBridgeExecutionIdentity:
    """Authority-owned ServiceActor identity; no UserActor fields exist."""

    organization_id: UUID = field(repr=False)
    service_principal_id: UUID = field(repr=False)
    allowed_source_version_ids: tuple[UUID, ...] = field(repr=False)
    allowed_operations: tuple[str, ...] = field(repr=False)
    policy_epoch: int = field(repr=False)
    idempotency_key: str = field(repr=False)
    expires_at: datetime = field(repr=False)
    actor_kind: Literal["service"] = field(default="service", init=False, repr=False)
    workload: Literal["supply.connector"] = field(
        default=SUPPLY_CONNECTOR_WORKLOAD, init=False, repr=False
    )
    worker_audience: Literal["context-engine-connector-runner"] = field(
        default=SUPPLY_CONNECTOR_WORKER_AUDIENCE,
        init=False,
        repr=False,
    )
    operation: Literal["connector.execute"] = field(
        default=SUPPLY_CONNECTOR_WORKER_LEASE_OPERATION,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        _require_uuid("Supply bridge Organization", self.organization_id)
        _require_uuid("Supply bridge ServiceActor", self.service_principal_id)
        if (
            type(self.allowed_source_version_ids) is not tuple
            or not self.allowed_source_version_ids
            or any(type(value) is not UUID for value in self.allowed_source_version_ids)
            or self.allowed_source_version_ids
            != tuple(sorted(set(self.allowed_source_version_ids), key=str))
        ):
            raise ValueError("ServiceActor allowed source set must be exact")
        if (
            type(self.allowed_operations) is not tuple
            or not self.allowed_operations
            or any(
                type(value) is not str or not value for value in self.allowed_operations
            )
            or self.allowed_operations != tuple(sorted(set(self.allowed_operations)))
        ):
            raise ValueError("ServiceActor allowed operation set must be exact")
        if type(self.policy_epoch) is not int or self.policy_epoch <= 0:
            raise ValueError("ServiceActor policy epoch must be positive")
        if (
            type(self.idempotency_key) is not str
            or len(self.idempotency_key) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.idempotency_key
            )
        ):
            raise ValueError("ServiceActor idempotency key must be lowercase SHA-256")
        _require_utc("ServiceActor expiry", self.expires_at)


@dataclass(frozen=True, slots=True)
class SupplyBridgeExecutionResult:
    """Content-free durable page-acceptance lineage for one execution."""

    accepted_page_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.accepted_page_refs) is not tuple
            or not self.accepted_page_refs
            or any(
                type(value) is not str or not value for value in self.accepted_page_refs
            )
        ):
            raise ValueError("Supply bridge result requires accepted page refs")


class SupplyBridgeUnavailable(RuntimeError):
    """Trusted infrastructure failed without weakening the execution boundary."""


class PostgreSQLSupplyBridgeLeaseIssuer:
    """Issue one database-timed exact connector-job WorkerLease."""

    __slots__ = ("_codec", "_control_engine", "_lease_ttl_seconds")

    def __init__(
        self,
        control_engine: Engine,
        codec: WorkerLeaseCodec,
        *,
        lease_ttl_seconds: int = DEFAULT_SUPPLY_BRIDGE_LEASE_TTL_SECONDS,
    ) -> None:
        if type(codec) is not WorkerLeaseCodec:
            raise TypeError("Supply bridge issuer requires WorkerLeaseCodec")
        if (
            type(lease_ttl_seconds) is not int
            or not 1 <= lease_ttl_seconds <= MAX_SUPPLY_BRIDGE_LEASE_TTL_SECONDS
        ):
            raise ValueError("Supply bridge lease TTL is outside the closed bounds")
        self._control_engine = control_engine
        self._codec = codec
        self._lease_ttl_seconds = lease_ttl_seconds

    def issue(self, request: SupplyBridgeLeaseIssueRequest) -> WorkerLeaseToken:
        if type(request) is not SupplyBridgeLeaseIssueRequest:
            raise TypeError("Supply bridge lease issuance requires an exact request")
        return self._issue(request)

    def preempt(
        self,
        request: SupplyBridgeLeasePreemptionRequest,
    ) -> WorkerLeaseToken:
        if type(request) is not SupplyBridgeLeasePreemptionRequest:
            raise TypeError(
                "Supply bridge preemption requires explicit operator intent"
            )
        return self._issue(
            request,
            reason_digest=request.reason_digest,
        )

    def _issue(
        self,
        request: SupplyBridgeLeaseIssueRequest,
        *,
        reason_digest: str | None = None,
    ) -> WorkerLeaseToken:
        nonce = generate_worker_lease_nonce()
        statement = (
            text(
                """
                SELECT issued_at, expires_at, lease_generation,
                       policy_epoch, idempotency_key,
                       service_actor_expires_at
                FROM public.context_supply_issue_connector_lease(
                    :organization_id, :source_id, :source_version_id,
                    :worker_job_id, :service_principal_id,
                    :signing_key_version, :nonce, :lease_ttl_seconds
                )
                """
            )
            if reason_digest is None
            else text(
                """
                SELECT issued_at, expires_at, lease_generation,
                       policy_epoch, idempotency_key,
                       service_actor_expires_at
                FROM public.context_supply_preempt_connector_lease(
                    :organization_id, :source_id, :source_version_id,
                    :worker_job_id, :service_principal_id,
                    :signing_key_version, :nonce, :lease_ttl_seconds,
                    :reason_digest
                )
                """
            )
        )
        try:
            with self._control_engine.begin() as connection:
                assert_control_role(connection)
                row = connection.execute(
                    statement,
                    {
                        "organization_id": request.organization_id,
                        "source_id": request.source_id,
                        "source_version_id": request.source_version_id,
                        "worker_job_id": request.worker_job_id,
                        "service_principal_id": request.service_principal_id,
                        "signing_key_version": self._codec.active_signing_key_version,
                        "nonce": nonce,
                        "lease_ttl_seconds": self._lease_ttl_seconds,
                        "reason_digest": reason_digest,
                    },
                ).one_or_none()
                if row is None:
                    raise WorkNotAvailable(
                        WorkerLeaseRejectionAuditReceipt(lease_digest="0" * 64)
                    )
                claims = WorkerLeaseClaims(
                    signing_key_version=self._codec.active_signing_key_version,
                    organization_id=request.organization_id,
                    job_id=request.worker_job_id,
                    service_principal_id=request.service_principal_id,
                    workload=SUPPLY_CONNECTOR_WORKLOAD,
                    worker_audience=SUPPLY_CONNECTOR_WORKER_AUDIENCE,
                    issued_at=_require_utc("issued_at", row.issued_at),
                    expires_at=_require_utc("expires_at", row.expires_at),
                    nonce=nonce,
                    operation=SUPPLY_CONNECTOR_WORKER_LEASE_OPERATION,
                    source_version_ref=str(request.source_version_id),
                    lease_generation=row.lease_generation,
                    policy_epoch=row.policy_epoch,
                    idempotency_key=row.idempotency_key,
                    allowed_source_version_refs=(str(request.source_version_id),),
                    allowed_operations=(SUPPLY_CONNECTOR_WORKER_LEASE_OPERATION,),
                    service_actor_expires_at=_require_utc(
                        "ServiceActor expires_at",
                        row.service_actor_expires_at,
                    ),
                )
                return self._codec.mint(claims)
        except WorkNotAvailable:
            raise
        except AssertionError:
            raise SupplyBridgeUnavailable(
                "checkpoint execution role is unavailable"
            ) from None
        except SQLAlchemyError:
            raise SupplyBridgeUnavailable(
                "Supply bridge lease issuance is unavailable"
            ) from None


class PostgreSQLConnectorCheckpointStore:
    """Engine-owned opaque checkpoint store using one caller transaction."""

    __slots__ = ("_worker_engine",)

    def __init__(self, worker_engine: Engine) -> None:
        self._worker_engine = worker_engine

    def load(
        self,
        binding: ConnectorCheckpointBinding,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> bytes | None:
        if type(binding) is not ConnectorCheckpointBinding:
            raise TypeError("checkpoint load requires exact binding")
        if type(lease_claims) is not WorkerLeaseClaims:
            raise TypeError("checkpoint load requires verified WorkerLease claims")
        try:
            with self._worker_engine.begin() as connection:
                assert_worker_role(connection)
                checkpoint, _ = self._load_on_connection(
                    connection, binding, lease_claims
                )
                return checkpoint
        except AssertionError:
            raise SupplyBridgeUnavailable("checkpoint role is unavailable") from None
        except SQLAlchemyError:
            raise SupplyBridgeUnavailable("checkpoint load is unavailable") from None

    def redeem_for_execution(
        self,
        binding: ConnectorCheckpointBinding,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> bytes | None:
        if type(binding) is not ConnectorCheckpointBinding:
            raise TypeError("checkpoint execution load requires exact binding")
        if type(lease_claims) is not WorkerLeaseClaims:
            raise TypeError("checkpoint execution load requires verified claims")
        try:
            with self._worker_engine.begin() as connection:
                assert_worker_role(connection)
                row = connection.execute(
                    text(
                        """
                        SELECT opaque_checkpoint, job_state
                        FROM public.context_supply_redeem_connector_lease(
                            :organization_id, :source_version_id, :worker_job_id,
                            :service_principal_id, :lease_generation,
                            :signing_key_version, :nonce, :issued_at, :expires_at,
                            :policy_epoch, :idempotency_key,
                            :allowed_source_version_refs, :allowed_operations,
                            :service_actor_expires_at
                        )
                        """
                    ),
                    _lease_parameters(binding, lease_claims),
                ).one_or_none()
                if row is None or row.job_state != "running":
                    raise _rejection(
                        WorkerLeaseToken("unavailable.unavailable.unavailable")
                    )
                return (
                    bytes(row.opaque_checkpoint)
                    if row.opaque_checkpoint is not None
                    else None
                )
        except WorkNotAvailable:
            raise
        except AssertionError:
            raise SupplyBridgeUnavailable(
                "checkpoint execution role is unavailable"
            ) from None
        except SQLAlchemyError:
            raise SupplyBridgeUnavailable(
                "checkpoint execution load is unavailable"
            ) from None

    def _load_on_connection(
        self,
        connection: Connection,
        binding: ConnectorCheckpointBinding,
        lease_claims: WorkerLeaseClaims,
    ) -> tuple[bytes | None, str | None]:
        row = connection.execute(
            text(
                """
                SELECT opaque_checkpoint, job_state
                FROM public.context_supply_load_connector_checkpoint(
                    :organization_id, :source_version_id, :worker_job_id,
                    :service_principal_id, :lease_generation,
                    :signing_key_version, :nonce, :issued_at, :expires_at,
                    :policy_epoch, :idempotency_key,
                    :allowed_source_version_refs, :allowed_operations,
                    :service_actor_expires_at
                )
                """
            ),
            _lease_parameters(binding, lease_claims),
        ).one_or_none()
        if row is None:
            return None, None
        checkpoint = (
            bytes(row.opaque_checkpoint) if row.opaque_checkpoint is not None else None
        )
        return checkpoint, row.job_state


class PostgreSQLStagedArtifactSink:
    """Engine-owned byte-exact staging joined to the caller transaction."""

    __slots__ = ("_worker_engine",)

    def __init__(self, worker_engine: Engine) -> None:
        self._worker_engine = worker_engine

    def accept_change_page(
        self,
        connection: Connection,
        page: SupplyChangePage,
        serialized_page: bytes,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> None:
        if type(connection) is not Connection:
            raise TypeError("page acceptance requires caller transaction")
        if type(page) is not SupplyChangePage:
            raise TypeError("page acceptance requires SupplyChangePage")
        if type(serialized_page) is not bytes:
            raise TypeError("page acceptance requires serialized page bytes")
        if type(lease_claims) is not WorkerLeaseClaims:
            raise TypeError("page acceptance requires verified WorkerLease claims")
        row = connection.execute(
            text(
                """
                SELECT accepted_ordinal
                FROM public.context_supply_accept_connector_page(
                    :organization_id, :source_version_id, :worker_job_id,
                    :service_principal_id, :page_ref, :page_payload,
                    :lease_generation, :signing_key_version, :nonce,
                    :issued_at, :expires_at, :policy_epoch,
                    :idempotency_key, :allowed_source_version_refs,
                    :allowed_operations, :service_actor_expires_at
                )
                """
            ),
            {
                **_lease_parameters(page.binding, lease_claims),
                "page_ref": page.page_ref,
                "page_payload": serialized_page,
            },
        ).one_or_none()
        if row is None or type(row.accepted_ordinal) is not int:
            raise _rejection(WorkerLeaseToken("unavailable.unavailable.unavailable"))

    def load(
        self,
        binding: ConnectorCheckpointBinding,
        artifact_ref: str,
        *,
        lease_claims: WorkerLeaseClaims,
    ) -> StagedArtifact | None:
        if type(binding) is not ConnectorCheckpointBinding:
            raise TypeError("staged artifact load requires exact binding")
        if type(artifact_ref) is not str or not artifact_ref:
            raise ValueError("staged artifact load requires artifact reference")
        if type(lease_claims) is not WorkerLeaseClaims:
            raise TypeError("staged artifact load requires verified lease claims")
        try:
            with self._worker_engine.begin() as connection:
                assert_worker_role(connection)
                row = connection.execute(
                    text(
                        """
                        SELECT page_payload
                        FROM public.context_supply_load_staged_connector_page(
                            :organization_id, :source_version_id, :worker_job_id,
                            :service_principal_id, :page_ref,
                            :lease_generation, :signing_key_version, :nonce,
                            :issued_at, :expires_at, :policy_epoch,
                            :idempotency_key, :allowed_source_version_refs,
                            :allowed_operations, :service_actor_expires_at
                        )
                        """
                    ),
                    {
                        **_lease_parameters(binding, lease_claims),
                        "page_ref": artifact_ref,
                    },
                ).one_or_none()
                if row is None:
                    return None
                return StagedArtifact(
                    binding=binding,
                    artifact_ref=artifact_ref,
                    payload=bytes(row.page_payload),
                )
        except AssertionError:
            raise SupplyBridgeUnavailable(
                "staged artifact role is unavailable"
            ) from None
        except SQLAlchemyError:
            raise SupplyBridgeUnavailable(
                "staged artifact load is unavailable"
            ) from None


def _lease_parameters(
    binding: ConnectorCheckpointBinding,
    claims: WorkerLeaseClaims,
) -> dict[str, object]:
    return {
        "organization_id": binding.organization_id,
        "source_version_id": binding.source_version_id,
        "worker_job_id": binding.worker_job_id,
        "service_principal_id": claims.service_principal_id,
        "lease_generation": claims.lease_generation,
        "signing_key_version": claims.signing_key_version,
        "nonce": claims.nonce,
        "issued_at": claims.issued_at,
        "expires_at": claims.expires_at,
        "policy_epoch": claims.policy_epoch,
        "idempotency_key": claims.idempotency_key,
        "allowed_source_version_refs": list(claims.allowed_source_version_refs or ()),
        "allowed_operations": list(claims.allowed_operations or ()),
        "service_actor_expires_at": claims.service_actor_expires_at,
    }


def _verify_execution(
    codec: WorkerLeaseCodec,
    execution: SupplyBridgeExecution,
    token: WorkerLeaseToken,
    checked_at: datetime,
    *,
    identity: SupplyBridgeExecutionIdentity | None = None,
) -> WorkerLeaseClaims:
    if identity is None:
        raise TypeError("Supply bridge requires configured ServiceActor identity")
    checked_at = _require_utc("Supply bridge clock", checked_at)
    if (
        identity.organization_id != execution.organization_id
        or execution.source_version_id not in identity.allowed_source_version_ids
        or SUPPLY_CONNECTOR_WORKER_LEASE_OPERATION not in identity.allowed_operations
        or checked_at >= identity.expires_at
    ):
        raise _rejection(token)
    claims = codec.verify(
        token,
        expected_organization_id=execution.organization_id,
        expected_job_id=execution.worker_job_id,
        expected_service_principal_id=identity.service_principal_id,
        expected_workload=identity.workload,
        expected_operation=identity.operation,
        expected_worker_audience=identity.worker_audience,
        expected_source_version_ref=str(execution.source_version_id),
        now=checked_at,
    )
    if (
        claims.policy_epoch != identity.policy_epoch
        or claims.idempotency_key != identity.idempotency_key
        or claims.allowed_source_version_refs
        != tuple(str(value) for value in identity.allowed_source_version_ids)
        or claims.allowed_operations != identity.allowed_operations
        or claims.service_actor_expires_at != identity.expires_at
    ):
        raise _rejection(token)
    return claims


class PostgreSQLSupplyExecutionBridge:
    """Verify ServiceActor lease, then accept pages and checkpoints atomically."""

    __slots__ = (
        "_clock",
        "_codec",
        "_engine",
        "_identity",
        "_staged_sink",
        "_store",
        "_configuration",
    )

    def __init__(
        self,
        worker_engine: Engine,
        codec: WorkerLeaseCodec,
        identity: SupplyBridgeExecutionIdentity,
        store: ConnectorCheckpointStore,
        staged_sink: StagedArtifactSink,
        *,
        configuration: SupplyExecutionConfiguration | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if type(codec) is not WorkerLeaseCodec:
            raise TypeError("Supply bridge requires WorkerLeaseCodec")
        if type(identity) is not SupplyBridgeExecutionIdentity:
            raise TypeError("Supply bridge requires ServiceActor identity")
        if not callable(clock):
            raise TypeError("Supply bridge clock must be callable")
        if (
            configuration is not None
            and type(configuration) is not SupplyExecutionConfiguration
        ):
            raise TypeError("Supply bridge requires SupplyExecutionConfiguration")
        self._engine = worker_engine
        self._codec = codec
        self._identity = identity
        self._store = store
        self._staged_sink = staged_sink
        self._configuration = configuration or SupplyExecutionConfiguration()
        self._clock = clock

    def execute(
        self,
        execution: SupplyBridgeExecution,
        adapter: ConnectorAdapter,
    ) -> SupplyBridgeExecutionResult:
        if type(execution) is not SupplyBridgeExecution:
            raise TypeError("Supply bridge requires exact execution")
        claims = _verify_execution(
            self._codec,
            execution,
            execution.worker_lease,
            self._clock(),
            identity=self._identity,
        )
        if not all(
            callable(getattr(adapter, method, None))
            for method in ("load", "poll", "load_checkpoint")
        ):
            raise TypeError("Supply bridge requires ConnectorAdapter")
        accepted: list[str] = []
        checkpoint = self._store.redeem_for_execution(
            execution.binding,
            lease_claims=claims,
        )
        accepted_page_count = 0
        accepted_byte_count = 0
        consecutive_no_progress_pages = 0
        while True:
            if accepted_page_count >= self._configuration.page_limit:
                raise SupplyExecutionBoundExceeded(
                    SupplyExecutionBoundReason.PAGE_COUNT
                )
            adapter.load_checkpoint(checkpoint)
            page = (
                adapter.load(execution.binding)
                if checkpoint is None
                else adapter.poll(execution.binding)
            )
            if type(page) is not SupplyChangePage or page.binding != execution.binding:
                raise SupplyBridgeUnavailable("connector page binding is unavailable")
            try:
                serialized_page = serialize_supply_change_page(page)
            except SupplyStagedPageByteLimitExceeded:
                raise SupplyExecutionBoundExceeded(
                    SupplyExecutionBoundReason.PAGE_BYTES
                ) from None
            page_byte_count = len(serialized_page)
            next_byte_count = accepted_byte_count + page_byte_count
            if next_byte_count > self._configuration.cumulative_byte_limit:
                raise SupplyExecutionBoundExceeded(
                    SupplyExecutionBoundReason.CUMULATIVE_BYTES
                )
            made_no_progress = (
                not page.terminal
                and not page.documents
                and not page.deleted_document_refs
                and page.checkpoint_proposal == checkpoint
            )
            if (
                made_no_progress
                and consecutive_no_progress_pages
                >= self._configuration.no_progress_page_limit
            ):
                raise SupplyExecutionBoundExceeded(
                    SupplyExecutionBoundReason.NO_PROGRESS
                )
            try:
                with self._engine.begin() as connection:
                    assert_worker_role(connection)
                    self._staged_sink.accept_change_page(
                        connection,
                        page,
                        serialized_page,
                        lease_claims=claims,
                    )
            except (WorkNotAvailable, RuntimeError):
                raise
            except (AssertionError, SQLAlchemyError):
                raise SupplyBridgeUnavailable(
                    "Supply change-page acceptance is unavailable"
                ) from None
            accepted.append(page.page_ref)
            accepted_page_count += 1
            accepted_byte_count = next_byte_count
            consecutive_no_progress_pages = (
                consecutive_no_progress_pages + 1 if made_no_progress else 0
            )
            if page.terminal:
                return SupplyBridgeExecutionResult(tuple(accepted))
            checkpoint = page.checkpoint_proposal


__all__ = [
    "DEFAULT_SUPPLY_BRIDGE_LEASE_TTL_SECONDS",
    "MAX_SUPPLY_BRIDGE_LEASE_TTL_SECONDS",
    "PostgreSQLConnectorCheckpointStore",
    "PostgreSQLStagedArtifactSink",
    "PostgreSQLSupplyBridgeLeaseIssuer",
    "PostgreSQLSupplyExecutionBridge",
    "SupplyBridgeExecutionIdentity",
    "SupplyBridgeExecutionResult",
    "SupplyBridgeLeaseIssueRequest",
    "SupplyBridgeLeasePreemptionRequest",
    "SupplyBridgeUnavailable",
]
