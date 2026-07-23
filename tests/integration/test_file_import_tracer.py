from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from adapters.exact_phrase import PostgreSQLExactPhraseCandidateIndex
from adapters.file_source import FileReadLimits, FileRootRegistry
from adapters.http.app import create_app
from adapters.http.authentication import VerifiedAuthenticationContext
from adapters.http.organization_authority import OrganizationVerificationRejected
from adapters.http.scope_authority import ScopeAuthorityIdentity
from adapters.parsers.markdown import compile_markdown
from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
    FileImportAudience,
    FileImportPath,
    FileImportReceiver,
    FileRootRef,
    PreparedFileImport,
    PrepareFileImport,
    RegisterFileSource,
    SourceNotAvailable,
    SourceRef,
    VerifiedControlOperatorIdentity,
)
from engine.persistence import (
    DatabaseConfiguration,
    FileImportLeaseRedemption,
    FileImportUnavailable,
    PostgreSQLControlStore,
    PostgreSQLFileImportWorker,
    PostgreSQLMembershipAuthority,
    PostgreSQLWorkerLeaseIssuer,
    PublishedFileImport,
    create_database_engine,
)
from engine.persistence.membership_context import (
    MembershipIdentity,
    _PostgreSQLMaterializedProjectionPort,
)
from engine.runtime.construction import Runtime, required_kernel_dependencies
from engine.runtime.content_io import exact_phrase_digest
from engine.runtime.context_run import ContextRunOutcome
from engine.runtime.contracts import Acquire, ContextNeed
from engine.runtime.evidence import CandidateRef
from engine.runtime.materialized import (
    MaterializedProjectionSession,
    _close_materialized_projection_scope,
    _construct_materialized_projection_session,
    _observe_materialized_publication,
    _open_materialized_projection_scope,
)
from engine.runtime.organization import (
    ExistingOrganizationVerification,
    _construct_existing_http_organization_verification,
)
from engine.runtime.package_digest import QueryDigestKeyring
from engine.runtime.scope import ScopeSet, ScopeTarget
from engine.runtime.scope_authority import (
    TrustedScopeSnapshot,
    _close_scope_authority_scope,
    _construct_trusted_scope_snapshot,
    _open_scope_authority_scope,
)
from engine.supply import (
    MarkdownCompilerConfig,
    ParsedDocument,
    WorkerLeaseClaims,
    WorkerLeaseCodec,
    WorkerLeaseKeyring,
    WorkerLeaseToken,
    canonicalize_parsed_document,
)
from tests.support.context_run_operator import exact_test_context_run_operator_read

pytestmark = pytest.mark.integration
NOW = datetime.now(UTC).replace(microsecond=0)
SIGNING_KEY = bytes(range(32))
MARKDOWN_FIXTURES = Path(__file__).parents[1] / "fixtures/markdown"
ROOT = Path(__file__).parents[2]


def _publication_effect_counts(
    connection: Connection,
    organization_id: UUID,
) -> tuple[int, ...]:
    row = connection.execute(
        text(
            """
            SELECT
                (SELECT count(*) FROM file_acquisition
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM file_import_job
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM context_resource
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM context_revision
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM context_fragment
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM file_revision_snapshot
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM revision_publication_event
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM exact_phrase_candidate
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM resource_access_policy
                 WHERE organization_id = :organization_id),
                (SELECT count(*) FROM membership_resource_field_right
                 WHERE organization_id = :organization_id)
            """
        ),
        {"organization_id": organization_id},
    ).one()
    return tuple(row)


class _ControlAuthenticator:
    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id

    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "control-secret":
            raise AssertionError("unexpected Control credential")
        return VerifiedControlOperatorIdentity(
            organization_id=self.organization_id,
            operator_ref="operator:file-import",
            authentication_binding_ref="binding:file-import",
            authority_ref="authority:file-import",
            allowed_operations=frozenset(
                {
                    ControlOperation.REGISTER_SOURCE,
                    ControlOperation.READ_SOURCE,
                    ControlOperation.IMPORT_FILE,
                }
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        )


class _RuntimeAuthenticator:
    def __init__(
        self,
        organization_id: UUID,
        user_id: UUID,
        membership_id: UUID,
        *,
        token: str = "runtime-secret",
    ) -> None:
        self.organization_id = organization_id
        self.user_id = user_id
        self.membership_id = membership_id
        self.token = token

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        assert opaque_credential == self.token
        return VerifiedAuthenticationContext(
            organization_ref=str(self.organization_id),
            user_ref=str(self.user_id),
            principal_ref="principal:file-reader",
            membership_ref=str(self.membership_id),
            membership_version=1,
            agent_version_ref="agent:file-tracer",
            authenticated_application_ref="application:file-tracer",
            authentication_binding_ref="binding:file-tracer",
        )


class _MultiTenantRuntimeAuthenticator:
    def __init__(
        self,
        identities: dict[str, tuple[UUID, UUID, UUID]],
    ) -> None:
        self.identities = identities

    def authenticate(self, opaque_credential: str) -> VerifiedAuthenticationContext:
        organization_id, user_id, membership_id = self.identities[opaque_credential]
        return _RuntimeAuthenticator(
            organization_id,
            user_id,
            membership_id,
            token=opaque_credential,
        ).authenticate(opaque_credential)


class _OrganizationAuthority:
    def verify_existing(
        self,
        authentication: VerifiedAuthenticationContext,
        *,
        request_id: str,
        verified_at: datetime,
    ) -> ExistingOrganizationVerification:
        try:
            organization_id = UUID(authentication.organization_ref)
        except ValueError:
            raise OrganizationVerificationRejected from None
        return _construct_existing_http_organization_verification(
            organization_id=organization_id,
            request_id=request_id,
            authentication_binding_ref=authentication.authentication_binding_ref,
            verified_at=verified_at,
        )


class _ExactScopeAuthority:
    def __init__(
        self,
        source_ref: str,
        resource_ref: str,
        *,
        allowed: bool = True,
    ) -> None:
        self.source_ref = source_ref
        self.resource_ref = resource_ref
        self.allowed = allowed

    @contextmanager
    def current_scope(
        self, identity: ScopeAuthorityIdentity
    ) -> Iterator[TrustedScopeSnapshot]:
        scope = _open_scope_authority_scope()
        try:
            target = ScopeSet(
                frozenset(
                    {
                        ScopeTarget(
                            identity.organization_id,
                            self.source_ref,
                            self.resource_ref,
                        )
                    }
                    if self.allowed
                    else set()
                )
            )
            yield _construct_trusted_scope_snapshot(
                authority_scope=scope,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                membership_version=identity.membership_version,
                policy_epoch=identity.policy_epoch,
                principal_ref=identity.principal_ref,
                agent_version_ref=identity.agent_version_ref,
                purpose=identity.purpose,
                request_id=identity.request_id,
                authentication_binding_ref=identity.authentication_binding_ref,
                checked_at=identity.checked_at,
                organization_boundary=target,
                membership_rights=target,
                principal_grants=target,
                agent_ceiling=target,
                source_native_acl=target,
                resource_acl=target,
                purpose_policy=target,
            )
        finally:
            _close_scope_authority_scope(scope)


class _ExactThenReplayCandidateIndex:
    def __init__(self, replay: CandidateRef) -> None:
        self.exact = PostgreSQLExactPhraseCandidateIndex()
        self.replay = replay

    def discover(
        self,
        request: Acquire,
        projection_session: MaterializedProjectionSession,
    ) -> tuple[CandidateRef, ...]:
        exact = self.exact.discover(request, projection_session)
        return exact or (self.replay,)


@dataclass(frozen=True, slots=True)
class _FileImportScenario:
    organization_id: UUID
    membership_id: UUID
    receiver: FileImportReceiver
    source_ref: SourceRef
    prepared: PreparedFileImport
    codec: WorkerLeaseCodec
    token: WorkerLeaseToken | None
    root_ref: FileRootRef
    root: Path


def _prepare_file_import_scenario(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    *,
    payload: bytes | None = b"# Handbook\n\nContextEngine delivers context.\n",
    issue_lease: bool = True,
    lease_ttl_seconds: int = 300,
) -> _FileImportScenario:
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    root_ref = FileRootRef(f"root-{organization_id.hex}")
    root = tmp_path / root_ref.value
    root.mkdir()
    if payload is not None:
        (root / "handbook.md").write_bytes(payload)

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from
                    ) VALUES (:org, :membership_id, :user_id, 'active', 1, :now)
                    """
                ),
                {
                    "org": organization_id,
                    "membership_id": membership_id,
                    "user_id": user_id,
                    "now": NOW - timedelta(days=1),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO service_principal (
                        organization_id, service_principal_id, workload,
                        worker_audience, operation, enabled
                    ) VALUES (:org, :receiver, 'supply.file-import',
                        'context-engine-worker', 'file.import', true)
                    """
                ),
                {
                    "org": organization_id,
                    "receiver": receiver.service_principal_id,
                },
            )
    finally:
        migration_engine.dispose()

    authority = ControlOperatorAuthority(
        _ControlAuthenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=receiver,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-file-security-scenario",
    ) as call:
        source = control.register_source(
            call,
            RegisterFileSource("Handbook", root_ref, organization_id.hex),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id="prepare-file-security-scenario",
    ) as call:
        prepared = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="file-security-scenario",
            ),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id="retry-import-after-lost-response",
    ) as call:
        prepared_retry = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="file-security-scenario",
            ),
        )
    assert prepared_retry == prepared
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    token = (
        PostgreSQLWorkerLeaseIssuer(
            guarded_control_engine,
            codec,
            lease_ttl_seconds=lease_ttl_seconds,
        ).issue_file_import_lease(prepared)
        if issue_lease
        else None
    )
    return _FileImportScenario(
        organization_id=organization_id,
        membership_id=membership_id,
        receiver=receiver,
        source_ref=source.source_ref,
        prepared=prepared,
        codec=codec,
        token=token,
        root_ref=root_ref,
        root=root,
    )


def _scenario_claims(scenario: _FileImportScenario) -> WorkerLeaseClaims:
    assert scenario.token is not None
    return scenario.codec.verify(
        scenario.token,
        expected_organization_id=scenario.organization_id,
        expected_job_id=scenario.prepared.job_id,
        expected_service_principal_id=scenario.receiver.service_principal_id,
        expected_workload=scenario.receiver.workload,
        expected_operation=scenario.receiver.operation,
        expected_worker_audience=scenario.receiver.worker_audience,
        expected_source_ref=str(scenario.source_ref.value),
        now=datetime.now(UTC).replace(microsecond=0),
    )


def _prepare_repeat_file_import(
    scenario: _FileImportScenario,
    guarded_control_engine: Engine,
    *,
    idempotency_key: str,
    path: FileImportPath | None = None,
    lease_ttl_seconds: int = 300,
) -> tuple[PreparedFileImport, WorkerLeaseToken]:
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(scenario.organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=scenario.receiver,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id=f"repeat-{idempotency_key}",
    ) as call:
        prepared = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=scenario.source_ref,
                path=path or FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=scenario.membership_id,
                    membership_version=1,
                ),
                idempotency_key=idempotency_key,
            ),
        )
    token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        scenario.codec,
        lease_ttl_seconds=lease_ttl_seconds,
    ).issue_file_import_lease(prepared)
    return prepared, token


def _run_file_import(
    scenario: _FileImportScenario,
    prepared: PreparedFileImport,
    token: WorkerLeaseToken,
    guarded_worker_engine: Engine,
    *,
    config_version: str = "markdown-config-v1",
) -> PublishedFileImport:
    return PostgreSQLFileImportWorker(
        guarded_worker_engine,
        scenario.codec,
        scenario.receiver,
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=4096),
        ),
        MarkdownCompilerConfig(config_version),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    ).run(
        FileImportLeaseRedemption(
            token,
            prepared.organization_id,
            prepared.job_id,
            prepared.source_ref,
        )
    )


def _redeem_direct(
    guarded_worker_engine: Engine,
    claims: WorkerLeaseClaims,
    *,
    organization_id: UUID | None = None,
    job_id: UUID | None = None,
    service_principal_id: UUID | None = None,
    source_ref: str | None = None,
) -> object | None:
    with guarded_worker_engine.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT * FROM public.context_worker_redeem_file_import(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :signing_key_version, :nonce,
                    :issued_at, :expires_at
                )
                """
            ),
            {
                "organization_id": organization_id or claims.organization_id,
                "job_id": job_id or claims.job_id,
                "service_principal_id": (
                    service_principal_id or claims.service_principal_id
                ),
                "source_ref": source_ref or claims.source_ref,
                "signing_key_version": claims.signing_key_version,
                "nonce": claims.nonce,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
            },
        ).one_or_none()


def _publish_direct(
    guarded_worker_engine: Engine,
    claims: WorkerLeaseClaims,
    *,
    resource_ref: str,
    revision_id: UUID,
    organization_id: UUID | None = None,
    job_id: UUID | None = None,
    service_principal_id: UUID | None = None,
    source_ref: str | None = None,
    compiler_version: str = "context-engine-markdown-v1",
    config_version: str = "markdown-config-v1",
) -> int | None:
    with guarded_worker_engine.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT effect_count
                FROM public.context_worker_publish_file_import_v2(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref,
                    :revision_id, 'fragment:paragraph:1',
                    '# Handbook\n\nContextEngine delivers context.\n',
                    'ContextEngine delivers context.',
                    :content_hash, :compilation_digest,
                    :compiler_version, :config_version,
                    :phrase_digest,
                    :signing_key_version, :nonce, :issued_at, :expires_at
                )
                """
            ),
            {
                "organization_id": organization_id or claims.organization_id,
                "job_id": job_id or claims.job_id,
                "service_principal_id": (
                    service_principal_id or claims.service_principal_id
                ),
                "source_ref": source_ref or claims.source_ref,
                "resource_ref": resource_ref,
                "revision_id": revision_id,
                "content_hash": sha256(
                    b"# Handbook\n\nContextEngine delivers context.\n"
                ).hexdigest(),
                "compilation_digest": "b" * 64,
                "phrase_digest": "c" * 64,
                "compiler_version": compiler_version,
                "config_version": config_version,
                "signing_key_version": claims.signing_key_version,
                "nonce": claims.nonce,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
            },
        ).scalar_one_or_none()


def _publish_structural_direct(
    guarded_worker_engine: Engine,
    claims: WorkerLeaseClaims,
    document: ParsedDocument,
    compilation_document: dict[str, object],
) -> int | None:
    with guarded_worker_engine.begin() as connection:
        return connection.execute(
            text(
                """
                SELECT effect_count
                FROM public.context_worker_publish_structural_file_import_v2(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref, :revision_id,
                    :canonical_text, :content_hash, :compilation_digest,
                    :compiler_version, :config_version,
                    CAST(:compilation_document AS jsonb),
                    :signing_key_version, :nonce, :issued_at, :expires_at
                )
                """
            ),
            {
                "organization_id": claims.organization_id,
                "job_id": claims.job_id,
                "service_principal_id": claims.service_principal_id,
                "source_ref": claims.source_ref,
                "resource_ref": f"resource:malformed:{uuid4()}",
                "revision_id": uuid4(),
                "canonical_text": document.canonical_text,
                "content_hash": document.content_hash,
                "compilation_digest": document.compilation_digest,
                "compiler_version": document.provenance.compiler_version,
                "config_version": document.provenance.config_version,
                "compilation_document": json.dumps(compilation_document),
                "signing_key_version": claims.signing_key_version,
                "nonce": claims.nonce,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
            },
        ).scalar_one_or_none()


def _fail_direct(
    guarded_worker_engine: Engine,
    claims: WorkerLeaseClaims,
    *,
    organization_id: UUID | None = None,
    job_id: UUID | None = None,
    service_principal_id: UUID | None = None,
    source_ref: str | None = None,
) -> bool:
    with guarded_worker_engine.begin() as connection:
        return bool(
            connection.execute(
                text(
                    """
                    SELECT public.context_worker_fail_file_import(
                        :organization_id, :job_id, :service_principal_id,
                        :source_ref, :signing_key_version, :nonce,
                        :issued_at, :expires_at
                    )
                    """
                ),
                {
                    "organization_id": organization_id or claims.organization_id,
                    "job_id": job_id or claims.job_id,
                    "service_principal_id": (
                        service_principal_id or claims.service_principal_id
                    ),
                    "source_ref": source_ref or claims.source_ref,
                    "signing_key_version": claims.signing_key_version,
                    "nonce": claims.nonce,
                    "issued_at": claims.issued_at,
                    "expires_at": claims.expires_at,
                },
            ).scalar_one()
        )


def _job_state(
    migration_configuration: DatabaseConfiguration,
    scenario: _FileImportScenario,
) -> tuple[str, int]:
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT state, effect_count FROM file_import_job
                    WHERE organization_id = :org AND job_id = :job_id
                    """
                ),
                {
                    "org": scenario.organization_id,
                    "job_id": scenario.prepared.job_id,
                },
            ).one()
            return row.state, row.effect_count
    finally:
        migration_engine.dispose()


def _scenario_effect_counts(
    migration_configuration: DatabaseConfiguration,
    scenario: _FileImportScenario,
) -> tuple[int, ...]:
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            return _publication_effect_counts(connection, scenario.organization_id)
    finally:
        migration_engine.dispose()


@pytest.mark.security_evidence(id="PG-FILE-IMPORT-023", layer="postgres")
def test_registered_file_import_publishes_one_exact_authorized_http_package(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    guarded_operator_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    organization_id = uuid4()
    other_organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    other_user_id = uuid4()
    other_membership_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    migration_engine = create_database_engine(migration_configuration)
    root = tmp_path / "handbook"
    root.mkdir()
    source_bytes = b"# Handbook\n\nContextEngine delivers context.\n"
    (root / "handbook.md").write_bytes(source_bytes)
    with migration_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organization (organization_id) VALUES (:a), (:b)"),
            {"a": organization_id, "b": other_organization_id},
        )
        connection.execute(
            text(
                "INSERT INTO user_account (user_id) VALUES (:user_id), (:other_user_id)"
            ),
            {"user_id": user_id, "other_user_id": other_user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO membership (
                    organization_id, membership_id, user_id, status,
                    membership_version, valid_from
                ) VALUES (:organization_id, :membership_id, :user_id,
                    'active', 1, :valid_from)
                """
            ),
            {
                "organization_id": organization_id,
                "membership_id": membership_id,
                "user_id": user_id,
                "valid_from": NOW - timedelta(days=1),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO membership (
                    organization_id, membership_id, user_id, status,
                    membership_version, valid_from
                ) VALUES (:organization_id, :membership_id, :user_id,
                    'active', 1, :valid_from)
                """
            ),
            {
                "organization_id": other_organization_id,
                "membership_id": other_membership_id,
                "user_id": other_user_id,
                "valid_from": NOW - timedelta(days=1),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO service_principal (
                    organization_id, service_principal_id, workload,
                    worker_audience, operation, enabled
                ) VALUES (:organization_id, :service_principal_id,
                    'supply.file-import', 'context-engine-worker',
                    'file.import', true)
                """
            ),
            {
                "organization_id": organization_id,
                "service_principal_id": receiver.service_principal_id,
            },
        )

    authority = ControlOperatorAuthority(
        _ControlAuthenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=receiver,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-file",
    ) as call:
        source = control.register_source(
            call,
            RegisterFileSource("Handbook", FileRootRef("handbook"), "handbook"),
        )
    with migration_engine.connect() as connection:
        before_invalid = _publication_effect_counts(connection, organization_id)
    with pytest.raises(ValueError, match="Markdown filename"):
        PrepareFileImport(
            source_ref=source.source_ref,
            path=FileImportPath("../outside.md"),
            audience=FileImportAudience(
                principal_ref="principal:file-reader",
                membership_id=membership_id,
                membership_version=1,
            ),
            idempotency_key="outside-import",
        )
    with migration_engine.connect() as connection:
        assert (
            _publication_effect_counts(connection, organization_id)
            == before_invalid
            == (0,) * 10
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id="import-file",
    ) as call:
        prepared = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="handbook-import",
            ),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.READ_SOURCE,
        request_id="read-activated-file",
    ) as call:
        activated_source = control.read_source(call, source.source_ref)
    assert activated_source.active_version.capabilities.file_source_access.value == (
        "available"
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-file-idempotent-after-activation",
    ) as call:
        registered_again = control.register_source(
            call,
            RegisterFileSource("Handbook", FileRootRef("handbook"), "handbook"),
        )
    assert registered_again == activated_source

    expired_user_id = uuid4()
    expired_membership_id = uuid4()
    with migration_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
            {"user_id": expired_user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO membership (
                    organization_id, membership_id, user_id, status,
                    membership_version, valid_from, valid_until
                ) VALUES (:organization_id, :membership_id, :user_id,
                    'active', 1, :valid_from, :valid_until)
                """
            ),
            {
                "organization_id": organization_id,
                "membership_id": expired_membership_id,
                "user_id": expired_user_id,
                "valid_from": NOW - timedelta(days=2),
                "valid_until": NOW - timedelta(days=1),
            },
        )
    with (
        pytest.raises(SourceNotAvailable),
        authority.authorize(
            opaque_credential="control-secret",
            operation=ControlOperation.IMPORT_FILE,
            request_id="expired-membership-import",
        ) as call,
    ):
        control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("handbook.md"),
                audience=FileImportAudience(
                    principal_ref="principal:expired-reader",
                    membership_id=expired_membership_id,
                    membership_version=1,
                ),
                idempotency_key="expired-membership-import",
            ),
        )
    with migration_engine.connect() as connection:
        assert _publication_effect_counts(connection, organization_id)[:2] == (1, 1)

    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
    ).issue_file_import_lease(prepared)
    published = PostgreSQLFileImportWorker(
        guarded_worker_engine,
        codec,
        receiver,
        FileRootRegistry(
            {FileRootRef("handbook"): root},
            limits=FileReadLimits(max_file_bytes=1024),
        ),
        MarkdownCompilerConfig("markdown-config-v1"),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    ).run(
        FileImportLeaseRedemption(
            token,
            prepared.organization_id,
            prepared.job_id,
            prepared.source_ref,
        )
    )

    runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=_ExactThenReplayCandidateIndex(published.candidate_ref),
        clock=lambda: NOW,
        query_digest_keyring=query_digest_keyring,
    )
    app = create_app(
        authenticator=_MultiTenantRuntimeAuthenticator(
            {
                "runtime-secret": (organization_id, user_id, membership_id),
                "other-runtime-secret": (
                    other_organization_id,
                    other_user_id,
                    other_membership_id,
                ),
            }
        ),
        organization_authority=_OrganizationAuthority(),
        membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
        scope_authority=_ExactScopeAuthority(
            published.candidate_ref.source_ref,
            published.candidate_ref.resource_ref,
        ),
        runtime=runtime,
        clock=lambda: NOW,
        request_id_factory=lambda: "file-import-http",
    )
    client = TestClient(app)
    response = client.post(
        "/v1/context:resolve",
        headers={"Authorization": "Bearer runtime-secret"},
        json={
            "kind": "acquire",
            "need": {"query": "ContextEngine delivers context."},
        },
    )

    assert response.status_code == 200
    package = response.json()["package"]
    assert package["blocks"][0]["text"] == "ContextEngine delivers context."
    assert package["evidence"][0]["sourceRef"] == str(source.source_ref.value)
    assert package["evidence"][0]["resourceRef"] == published.candidate_ref.resource_ref
    assert package["evidence"][0]["revisionRef"] == published.candidate_ref.revision_ref
    assert package["evidence"][0]["fragmentRef"] == "fragment:paragraph:1"
    assert package["blocks"][0]["evidenceRefs"] == [
        package["evidence"][0]["evidenceRef"]
    ]
    with exact_test_context_run_operator_read(
        control_engine=guarded_control_engine,
        operator_engine=guarded_operator_engine,
        organization_id=organization_id,
        decision_ref=package["decisionRef"],
        request_id="file-import-context-run-read",
        opaque_credential="file-import-operator-secret",
        authorized_at=NOW,
    ) as (reader, authorization):
        run = reader.find_by_decision_ref(authorization, package["decisionRef"])
    assert run is not None
    assert run.outcome is ContextRunOutcome.DELIVERED_AUTHORIZED
    assert run.authorized_evidence_refs == (package["evidence"][0]["evidenceRef"],)
    assert run.decision_audit_category is None

    with PostgreSQLMembershipAuthority(
        guarded_runtime_engine
    ).current_projection_session(
        MembershipIdentity(
            organization_id=organization_id,
            user_id=user_id,
            membership_id=membership_id,
            membership_version=1,
            principal_ref="principal:file-reader",
            request_id="file-import-publication-read",
            authentication_binding_ref="binding:file-tracer",
            checked_at=NOW,
        )
    ) as projection_session:
        publication = _observe_materialized_publication(
            projection_session,
            published.candidate_ref,
        )
    assert publication is not None
    assert publication.states == ("prepared", "indexed", "active")
    assert publication.active_revision_ref == published.candidate_ref.revision_ref

    with migration_engine.connect() as connection:
        assert _publication_effect_counts(connection, organization_id) == (
            1,
            1,
            1,
            1,
            1,
            1,
            3,
            1,
            1,
            1,
        )
        assert (
            _publication_effect_counts(connection, other_organization_id) == (0,) * 10
        )

    unauthorized_runtime = Runtime(
        required_kernel_dependencies(),
        candidate_index=PostgreSQLExactPhraseCandidateIndex(),
        clock=lambda: NOW,
        query_digest_keyring=query_digest_keyring,
    )
    unauthorized = TestClient(
        create_app(
            authenticator=_RuntimeAuthenticator(
                organization_id, user_id, membership_id
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=_ExactScopeAuthority(
                published.candidate_ref.source_ref,
                published.candidate_ref.resource_ref,
                allowed=False,
            ),
            runtime=unauthorized_runtime,
            clock=lambda: NOW,
            request_id_factory=lambda: "file-import-unauthorized-http",
        )
    ).post(
        "/v1/context:resolve",
        headers={"Authorization": "Bearer runtime-secret"},
        json={
            "kind": "acquire",
            "need": {"query": "ContextEngine delivers context."},
        },
    )
    assert unauthorized.status_code == 200
    assert unauthorized.json()["package"]["blocks"] == []
    assert unauthorized.json()["package"]["evidence"] == []
    assert "ContextEngine delivers context." not in unauthorized.text

    cross_organization = client.post(
        "/v1/context:resolve",
        headers={"Authorization": "Bearer other-runtime-secret"},
        json={
            "kind": "acquire",
            "need": {"query": "ContextEngine delivers context."},
        },
    )
    assert cross_organization.status_code == 200
    assert cross_organization.json()["package"]["blocks"] == []
    assert cross_organization.json()["package"]["evidence"] == []
    assert "ContextEngine delivers context." not in cross_organization.text
    with migration_engine.connect() as connection:
        assert _publication_effect_counts(connection, organization_id) == (
            1,
            1,
            1,
            1,
            1,
            1,
            3,
            1,
            1,
            1,
        )
        assert (
            _publication_effect_counts(connection, other_organization_id) == (0,) * 10
        )


def _assert_structural_file_import_returns_coherent_authorized_units_over_http(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=(MARKDOWN_FIXTURES / "combined-v2.md").read_bytes(),
    )
    assert scenario.token is not None
    published = PostgreSQLFileImportWorker(
        guarded_worker_engine,
        scenario.codec,
        scenario.receiver,
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=4096),
        ),
        MarkdownCompilerConfig("markdown-config-v2"),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    ).run(
        FileImportLeaseRedemption(
            scenario.token,
            scenario.prepared.organization_id,
            scenario.prepared.job_id,
            scenario.prepared.source_ref,
        )
    )
    assert tuple(candidate.fragment_ref for candidate in published.candidate_refs) == (
        "fragment:heading:1",
        "fragment:paragraph:1",
        "fragment:heading:2",
        "fragment:list:1",
        "fragment:fenced_code:1",
        "fragment:table:1",
    )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
                text(
                    """
                    SELECT user_id FROM membership
                    WHERE organization_id = :organization_id
                      AND membership_id = :membership_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "membership_id": scenario.membership_id,
                },
            ).scalar_one()
            fragments = connection.execute(
                text(
                    """
                    SELECT fragment_ref, ordinal, content
                    FROM context_fragment
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                      AND revision_id = :revision_id
                    ORDER BY ordinal
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": published.candidate_ref.resource_ref,
                    "revision_id": UUID(published.candidate_ref.revision_ref),
                },
            ).all()
            snapshot = connection.execute(
                text(
                    """
                    SELECT compiler_version, config_version, compilation_digest,
                           compilation_document
                    FROM file_revision_snapshot
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                      AND revision_id = :revision_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": published.candidate_ref.resource_ref,
                    "revision_id": UUID(published.candidate_ref.revision_ref),
                },
            ).one()
            candidate_count = connection.execute(
                text(
                    """
                    SELECT count(*) FROM exact_phrase_candidate
                    WHERE organization_id = :organization_id
                      AND resource_ref = :resource_ref
                      AND revision_id = :revision_id
                    """
                ),
                {
                    "organization_id": scenario.organization_id,
                    "resource_ref": published.candidate_ref.resource_ref,
                    "revision_id": UUID(published.candidate_ref.revision_ref),
                },
            ).scalar_one()
    finally:
        migration_engine.dispose()

    assert tuple((row.fragment_ref, row.ordinal) for row in fragments) == (
        ("fragment:heading:1", 0),
        ("fragment:paragraph:1", 1),
        ("fragment:heading:2", 2),
        ("fragment:list:1", 3),
        ("fragment:fenced_code:1", 4),
        ("fragment:table:1", 5),
    )
    assert snapshot.compiler_version == "context-engine-markdown-v2"
    assert snapshot.config_version == "markdown-config-v2"
    assert snapshot.compilation_document["compilationDigest"] == (
        snapshot.compilation_digest
    )
    assert [
        fragment["position"] for fragment in snapshot.compilation_document["fragments"]
    ] == [
        fragment["position"] for fragment in snapshot.compilation_document["sections"]
    ]
    assert candidate_count == 15

    request_ids = iter(
        (
            "structure-heading",
            "structure-paragraph",
            "structure-list",
            "structure-code",
            "structure-table",
        )
    )
    client = TestClient(
        create_app(
            authenticator=_RuntimeAuthenticator(
                scenario.organization_id,
                user_id,
                scenario.membership_id,
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=_ExactScopeAuthority(
                published.candidate_ref.source_ref,
                published.candidate_ref.resource_ref,
            ),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                clock=lambda: NOW,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: NOW,
            request_id_factory=lambda: next(request_ids),
        )
    )
    expectations = (
        (
            "Handbook",
            "fragment:heading:1",
            "# Handbook",
        ),
        (
            "Stable delivery rules.",
            "fragment:paragraph:1",
            "# Handbook\n\nStable delivery rules.",
        ),
        (
            "Escalate red-rocket immediately.",
            "fragment:list:1",
            "# Handbook\n\n## Operations\n\n"
            "- Keep exact lineage.\n- Escalate red-rocket immediately.",
        ),
        (
            'release_marker = "blue-comet"',
            "fragment:fenced_code:1",
            "# Handbook\n\n## Operations\n\n"
            '```python\nrelease_marker = "blue-comet"\n```',
        ),
        (
            "silver-compass",
            "fragment:table:1",
            "# Handbook\n\n## Operations\n\n"
            "| Mode | Result |\n| --- | --- |\n| strict | silver-compass |",
        ),
    )
    for query, fragment_ref, expected_text in expectations:
        response = client.post(
            "/v1/context:resolve",
            headers={"Authorization": "Bearer runtime-secret"},
            json={"kind": "acquire", "need": {"query": query}},
        )

        assert response.status_code == 200
        package = response.json()["package"]
        assert len(package["blocks"]) == 1
        assert package["blocks"][0]["text"] == expected_text
        assert package["blocks"][0]["evidenceRefs"] == [
            package["evidence"][0]["evidenceRef"]
        ]
        assert package["evidence"][0]["fragmentRef"] == fragment_ref
        assert package["evidence"][0]["revisionRef"] == (
            published.candidate_ref.revision_ref
        )

    denied = TestClient(
        create_app(
            authenticator=_RuntimeAuthenticator(
                scenario.organization_id,
                user_id,
                scenario.membership_id,
            ),
            organization_authority=_OrganizationAuthority(),
            membership_authority=PostgreSQLMembershipAuthority(guarded_runtime_engine),
            scope_authority=_ExactScopeAuthority(
                published.candidate_ref.source_ref,
                published.candidate_ref.resource_ref,
                allowed=False,
            ),
            runtime=Runtime(
                required_kernel_dependencies(),
                candidate_index=PostgreSQLExactPhraseCandidateIndex(),
                clock=lambda: NOW,
                query_digest_keyring=query_digest_keyring,
            ),
            clock=lambda: NOW,
            request_id_factory=lambda: "structure-denied",
        )
    ).post(
        "/v1/context:resolve",
        headers={"Authorization": "Bearer runtime-secret"},
        json={
            "kind": "acquire",
            "need": {"query": "Escalate red-rocket immediately."},
        },
    )
    assert denied.status_code == 200
    assert denied.json()["package"]["blocks"] == []
    assert denied.json()["package"]["evidence"] == []
    assert "Handbook" not in denied.text
    assert "red-rocket" not in denied.text

    with pytest.raises(
        RuntimeError,
        match="structural Markdown downgrade requires no v2 snapshots",
    ):
        command.downgrade(Config(ROOT / "alembic.ini"), "20260722_0011")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "20260723_0014"
        )


def test_file_publication_requires_noop_and_replacement_aware_worker_entrypoints(
    migration_configuration: DatabaseConfiguration,
) -> None:
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            security = {
                row.proname: row
                for row in connection.execute(
                    text(
                        """
                    SELECT procedure.proname,
                           pg_get_userbyid(procedure.proowner) AS owner,
                           procedure.prosecdef,
                           procedure.proconfig
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = 'public'
                      AND procedure.proname IN (
                          'context_worker_publish_file_import',
                          'context_worker_publish_file_import_v1_internal',
                          'context_worker_publish_structural_file_import',
                          'context_worker_classify_file_import_internal',
                          'context_worker_publish_file_import_v2',
                          'context_worker_publish_structural_file_import_v2',
                          'context_worker_stage_file_replacement',
                          'context_worker_stage_structural_file_replacement',
                          'context_worker_activate_file_replacement'
                      )
                    """
                    )
                )
            }
            grants = {
                (row.proname, row.grantee, row.privilege_type)
                for row in connection.execute(
                    text(
                        """
                        SELECT procedure.proname,
                               COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
                               privilege.privilege_type
                        FROM pg_proc AS procedure
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        CROSS JOIN LATERAL aclexplode(
                            COALESCE(
                                procedure.proacl,
                                acldefault('f', procedure.proowner)
                            )
                        ) AS privilege
                        LEFT JOIN pg_roles AS grantee
                          ON grantee.oid = privilege.grantee
                        WHERE namespace.nspname = 'public'
                          AND procedure.proname IN (
                              'context_worker_publish_file_import',
                              'context_worker_publish_file_import_v1_internal',
                              'context_worker_publish_structural_file_import',
                              'context_worker_classify_file_import_internal',
                              'context_worker_publish_file_import_v2',
                              'context_worker_publish_structural_file_import_v2',
                              'context_worker_stage_file_replacement',
                              'context_worker_stage_structural_file_replacement',
                              'context_worker_activate_file_replacement'
                          )
                        """
                    )
                )
            }
            worker_direct_dml = tuple(
                connection.execute(
                    text(
                        """
                        SELECT has_table_privilege(
                                   'context_engine_worker', table_name, 'INSERT'
                               )
                        FROM unnest(ARRAY[
                            'public.context_resource',
                            'public.context_revision',
                            'public.file_revision_snapshot',
                            'public.context_fragment',
                            'public.exact_phrase_candidate',
                            'public.file_resource_ingestion_guard',
                            'public.file_acquisition_result',
                            'public.file_revision_replacement_plan',
                            'public.file_revision_supersession'
                        ]) AS table_name
                        """
                    )
                ).scalars()
            )
    finally:
        migration_engine.dispose()

    assert set(security) == {
        "context_worker_publish_file_import",
        "context_worker_publish_file_import_v1_internal",
        "context_worker_publish_structural_file_import",
        "context_worker_classify_file_import_internal",
        "context_worker_publish_file_import_v2",
        "context_worker_publish_structural_file_import_v2",
        "context_worker_stage_file_replacement",
        "context_worker_stage_structural_file_replacement",
        "context_worker_activate_file_replacement",
    }
    for function in security.values():
        assert function.owner == "context_engine_worker_lease_definer"
        assert function.prosecdef is True
        assert set(function.proconfig) == {
            "search_path=pg_catalog, pg_temp",
            "row_security=on",
        }
    assert grants == {
        (
            "context_worker_publish_file_import",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
        (
            "context_worker_publish_file_import_v1_internal",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
        (
            "context_worker_publish_structural_file_import",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
        (
            "context_worker_classify_file_import_internal",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
        (
            "context_worker_publish_file_import_v2",
            "context_engine_worker",
            "EXECUTE",
        ),
        (
            "context_worker_publish_file_import_v2",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
        (
            "context_worker_publish_structural_file_import_v2",
            "context_engine_worker",
            "EXECUTE",
        ),
        (
            "context_worker_publish_structural_file_import_v2",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
        (
            "context_worker_stage_file_replacement",
            "context_engine_worker",
            "EXECUTE",
        ),
        (
            "context_worker_stage_file_replacement",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
        (
            "context_worker_stage_structural_file_replacement",
            "context_engine_worker",
            "EXECUTE",
        ),
        (
            "context_worker_stage_structural_file_replacement",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
        (
            "context_worker_activate_file_replacement",
            "context_engine_worker",
            "EXECUTE",
        ),
        (
            "context_worker_activate_file_replacement",
            "context_engine_worker_lease_definer",
            "EXECUTE",
        ),
    }
    assert worker_direct_dml == (False,) * 9


def test_structural_publisher_rejects_malformed_json_without_partial_effects(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=(MARKDOWN_FIXTURES / "combined-v2.md").read_bytes(),
    )
    claims = _scenario_claims(scenario)
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    document = compile_markdown(
        (MARKDOWN_FIXTURES / "combined-v2.md").read_bytes(),
        MarkdownCompilerConfig("markdown-config-v2"),
    )
    assert type(document) is ParsedDocument

    assert (
        _publish_direct(
            guarded_worker_engine,
            claims,
            resource_ref=f"resource:legacy-v2:{uuid4()}",
            revision_id=uuid4(),
            compiler_version="context-engine-markdown-v2",
            config_version="markdown-config-v2",
        )
        is None
    )

    missing_array = json.loads(canonicalize_parsed_document(document))
    missing_array["fragments"][0].pop("searchPhrases")
    malformed_offset = json.loads(canonicalize_parsed_document(document))
    malformed_offset["fragments"][0]["position"]["start"]["byteOffset"] = "not-a-number"
    mismatched_section = json.loads(canonicalize_parsed_document(document))
    mismatched_section["sections"][0]["kind"] = "paragraph"

    for malformed in (missing_array, malformed_offset, mismatched_section):
        assert (
            _publish_structural_direct(
                guarded_worker_engine,
                claims,
                document,
                malformed,
            )
            is None
        )

    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            assert _publication_effect_counts(
                connection,
                scenario.organization_id,
            ) == (1, 1, 0, 0, 0, 0, 0, 0, 0, 0)
            assert (
                connection.execute(
                    text(
                        """
                    SELECT state FROM file_import_job
                    WHERE organization_id = :organization_id
                      AND job_id = :job_id
                    """
                    ),
                    {
                        "organization_id": scenario.organization_id,
                        "job_id": scenario.prepared.job_id,
                    },
                ).scalar_one()
                == "running"
            )
    finally:
        migration_engine.dispose()


def test_missing_file_after_redemption_records_terminal_zero_effect_failure(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    receiver = FileImportReceiver(uuid4())
    migration_engine = create_database_engine(migration_configuration)
    with migration_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organization (organization_id) VALUES (:org)"),
            {"org": organization_id},
        )
        connection.execute(
            text("INSERT INTO user_account (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO membership (
                    organization_id, membership_id, user_id, status,
                    membership_version, valid_from
                ) VALUES (:org, :membership_id, :user_id, 'active', 1, :now)
                """
            ),
            {
                "org": organization_id,
                "membership_id": membership_id,
                "user_id": user_id,
                "now": NOW - timedelta(days=1),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO service_principal (
                    organization_id, service_principal_id, workload,
                    worker_audience, operation, enabled
                ) VALUES (:org, :receiver, 'supply.file-import',
                    'context-engine-worker', 'file.import', true)
                """
            ),
            {"org": organization_id, "receiver": receiver.service_principal_id},
        )
    authority = ControlOperatorAuthority(
        _ControlAuthenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=lambda: NOW,
            file_import_receiver=receiver,
        ),
        authority=authority,
        clock=lambda: NOW,
    )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.REGISTER_SOURCE,
        request_id="register-missing-file",
    ) as call:
        source = control.register_source(
            call,
            RegisterFileSource("Missing", FileRootRef("missing"), "missing"),
        )
    with authority.authorize(
        opaque_credential="control-secret",
        operation=ControlOperation.IMPORT_FILE,
        request_id="import-missing-file",
    ) as call:
        prepared = control.prepare_file_import(
            call,
            PrepareFileImport(
                source_ref=source.source_ref,
                path=FileImportPath("missing.md"),
                audience=FileImportAudience(
                    principal_ref="principal:file-reader",
                    membership_id=membership_id,
                    membership_version=1,
                ),
                idempotency_key="missing-file",
            ),
        )
    codec = WorkerLeaseCodec(
        WorkerLeaseKeyring(active_version=1, keys={1: SIGNING_KEY})
    )
    token = PostgreSQLWorkerLeaseIssuer(
        guarded_control_engine,
        codec,
    ).issue_file_import_lease(prepared)
    root = tmp_path / "missing-root"
    root.mkdir()
    worker = PostgreSQLFileImportWorker(
        guarded_worker_engine,
        codec,
        receiver,
        FileRootRegistry(
            {FileRootRef("missing"): root},
            limits=FileReadLimits(max_file_bytes=1024),
        ),
        MarkdownCompilerConfig("markdown-config-v1"),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    )
    with pytest.raises(FileImportUnavailable):
        worker.run(
            FileImportLeaseRedemption(
                token,
                prepared.organization_id,
                prepared.job_id,
                prepared.source_ref,
            )
        )
    with migration_engine.connect() as connection:
        state, failed_at, effect_count = connection.execute(
            text(
                """
                SELECT state, failed_at, effect_count
                FROM file_import_job
                WHERE organization_id = :org AND job_id = :job_id
                """
            ),
            {"org": organization_id, "job_id": prepared.job_id},
        ).one()
        assert state == "failed"
        assert failed_at is not None
        assert effect_count == 0
        assert _publication_effect_counts(connection, organization_id) == (
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )


def test_exact_phrase_discovery_does_not_hide_a_match_after_sixty_four_rows(
    migration_configuration: DatabaseConfiguration,
) -> None:
    organization_id = uuid4()
    candidate_rows: list[dict[str, object]] = []
    for index in range(65):
        resource_ref = f"resource:exact-limit:{index:03d}"
        candidate_rows.append(
            {
                "organization_id": organization_id,
                "source_ref": "source:exact-limit",
                "resource_ref": resource_ref,
                "revision_id": uuid4(),
                "fragment_ref": "fragment:paragraph:1",
                "ordinal": index,
            }
        )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            connection.execute(
                text(
                    """
                    INSERT INTO context_resource (
                        organization_id, resource_ref, source_ref,
                        active_revision_id, tombstoned
                    ) VALUES (:organization_id, :resource_ref, :source_ref,
                        :revision_id, false)
                    """
                ),
                candidate_rows,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_revision (
                        organization_id, resource_ref, revision_id
                    ) VALUES (:organization_id, :resource_ref, :revision_id)
                    """
                ),
                candidate_rows,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO context_fragment (
                        organization_id, resource_ref, revision_id,
                        fragment_ref, ordinal, content, projection_kind
                    ) VALUES (:organization_id, :resource_ref, :revision_id,
                        :fragment_ref, 0, 'same exact paragraph', 'body')
                    """
                ),
                candidate_rows,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO exact_phrase_candidate (
                        organization_id, phrase_digest, source_ref,
                        resource_ref, revision_id, fragment_ref
                    ) VALUES (:organization_id, :phrase_digest, :source_ref,
                        :resource_ref, :revision_id, :fragment_ref)
                    """
                ),
                [
                    {
                        **row,
                        "phrase_digest": exact_phrase_digest("same exact paragraph"),
                    }
                    for row in candidate_rows
                ],
            )
            projection_scope = _open_materialized_projection_scope()
            try:
                projection_session = _construct_materialized_projection_session(
                    authority_scope=projection_scope,
                    port=_PostgreSQLMaterializedProjectionPort(connection),
                )
                discovered = PostgreSQLExactPhraseCandidateIndex().discover(
                    Acquire(need=ContextNeed(query="same exact paragraph")),
                    projection_session,
                )
            finally:
                _close_materialized_projection_scope(projection_scope)
                transaction.rollback()
    finally:
        migration_engine.dispose()

    assert len(discovered) == 65
    assert discovered[-1].resource_ref == "resource:exact-limit:064"


@pytest.mark.parametrize(
    "changed_binding",
    ["organization", "job", "receiver", "source"],
)
def test_redeem_database_boundary_rejects_every_wrong_exact_binding(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    changed_binding: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    claims = _scenario_claims(scenario)
    organization_id: UUID | None = None
    job_id: UUID | None = None
    service_principal_id: UUID | None = None
    source_ref: str | None = None
    if changed_binding == "organization":
        organization_id = uuid4()
    elif changed_binding == "job":
        job_id = uuid4()
    elif changed_binding == "receiver":
        service_principal_id = uuid4()
    else:
        source_ref = str(uuid4())

    assert (
        _redeem_direct(
            guarded_worker_engine,
            claims,
            organization_id=organization_id,
            job_id=job_id,
            service_principal_id=service_principal_id,
            source_ref=source_ref,
        )
        is None
    )
    assert _job_state(migration_configuration, scenario) == ("leased", 0)
    assert _scenario_effect_counts(migration_configuration, scenario) == (
        1,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )


def test_redeem_is_one_shot_before_any_content_effect(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    claims = _scenario_claims(scenario)

    assert _redeem_direct(guarded_worker_engine, claims) is not None
    assert _redeem_direct(guarded_worker_engine, claims) is None
    assert _job_state(migration_configuration, scenario) == ("running", 0)
    assert _scenario_effect_counts(migration_configuration, scenario)[2:] == (0,) * 8


@pytest.mark.parametrize("operation", ["publish", "fail"])
@pytest.mark.parametrize(
    "changed_binding",
    ["organization", "job", "receiver", "source"],
)
def test_running_job_database_boundary_rejects_every_wrong_exact_binding(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    operation: str,
    changed_binding: str,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    claims = _scenario_claims(scenario)
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    organization_id: UUID | None = None
    job_id: UUID | None = None
    service_principal_id: UUID | None = None
    source_ref: str | None = None
    if changed_binding == "organization":
        organization_id = uuid4()
    elif changed_binding == "job":
        job_id = uuid4()
    elif changed_binding == "receiver":
        service_principal_id = uuid4()
    else:
        source_ref = str(uuid4())

    if operation == "publish":
        result: int | bool | None = _publish_direct(
            guarded_worker_engine,
            claims,
            resource_ref=f"resource:test:{uuid4()}",
            revision_id=uuid4(),
            organization_id=organization_id,
            job_id=job_id,
            service_principal_id=service_principal_id,
            source_ref=source_ref,
        )
        assert result is None
    else:
        result = _fail_direct(
            guarded_worker_engine,
            claims,
            organization_id=organization_id,
            job_id=job_id,
            service_principal_id=service_principal_id,
            source_ref=source_ref,
        )
        assert result is False
    assert _job_state(migration_configuration, scenario) == ("running", 0)
    assert _scenario_effect_counts(migration_configuration, scenario)[2:] == (0,) * 8


def test_completed_file_import_rejects_redeem_publish_and_fail_replay(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    claims = _scenario_claims(scenario)
    resource_ref = f"resource:test:{uuid4()}"
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    assert (
        _publish_direct(
            guarded_worker_engine,
            claims,
            resource_ref=resource_ref,
            revision_id=uuid4(),
        )
        == 1
    )

    before_replay = _scenario_effect_counts(migration_configuration, scenario)
    assert _redeem_direct(guarded_worker_engine, claims) is None
    assert (
        _publish_direct(
            guarded_worker_engine,
            claims,
            resource_ref=f"resource:test:{uuid4()}",
            revision_id=uuid4(),
        )
        is None
    )
    assert _fail_direct(guarded_worker_engine, claims) is False
    assert _job_state(migration_configuration, scenario) == ("completed", 1)
    assert (
        _scenario_effect_counts(migration_configuration, scenario)
        == before_replay
        == (1, 1, 1, 1, 1, 1, 3, 1, 1, 1)
    )


def test_failed_file_import_rejects_fail_redeem_and_publish_replay(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    claims = _scenario_claims(scenario)
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    assert _fail_direct(guarded_worker_engine, claims) is True

    before_replay = _scenario_effect_counts(migration_configuration, scenario)
    assert _fail_direct(guarded_worker_engine, claims) is False
    assert _redeem_direct(guarded_worker_engine, claims) is None
    assert (
        _publish_direct(
            guarded_worker_engine,
            claims,
            resource_ref=f"resource:test:{uuid4()}",
            revision_id=uuid4(),
        )
        is None
    )
    assert _job_state(migration_configuration, scenario) == ("failed", 0)
    assert (
        _scenario_effect_counts(migration_configuration, scenario)
        == before_replay
        == (1, 1, 0, 0, 0, 0, 0, 0, 0, 0)
    )


def test_disabled_receiver_after_redeem_cannot_publish_or_record_failure(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    claims = _scenario_claims(scenario)
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE service_principal SET enabled = false
                    WHERE organization_id = :org
                      AND service_principal_id = :receiver
                    """
                ),
                {
                    "org": scenario.organization_id,
                    "receiver": scenario.receiver.service_principal_id,
                },
            )
    finally:
        migration_engine.dispose()

    assert (
        _publish_direct(
            guarded_worker_engine,
            claims,
            resource_ref=f"resource:test:{uuid4()}",
            revision_id=uuid4(),
        )
        is None
    )
    assert _fail_direct(guarded_worker_engine, claims) is False
    assert _job_state(migration_configuration, scenario) == ("running", 0)
    assert _scenario_effect_counts(migration_configuration, scenario)[2:] == (0,) * 8


def test_expired_redeemed_lease_cannot_publish_or_record_failure(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        lease_ttl_seconds=1,
    )
    claims = _scenario_claims(scenario)
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(1.1)"))
    finally:
        migration_engine.dispose()

    assert (
        _publish_direct(
            guarded_worker_engine,
            claims,
            resource_ref=f"resource:test:{uuid4()}",
            revision_id=uuid4(),
        )
        is None
    )
    assert _fail_direct(guarded_worker_engine, claims) is False
    assert _job_state(migration_configuration, scenario) == ("running", 0)
    assert _scenario_effect_counts(migration_configuration, scenario)[2:] == (0,) * 8


def test_invalid_markdown_records_terminal_failure_without_content_effects(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        payload=b"## Unsupported heading\n",
    )
    assert scenario.token is not None
    worker = PostgreSQLFileImportWorker(
        guarded_worker_engine,
        scenario.codec,
        scenario.receiver,
        FileRootRegistry(
            {scenario.root_ref: scenario.root},
            limits=FileReadLimits(max_file_bytes=1024),
        ),
        MarkdownCompilerConfig("markdown-config-v1"),
        clock=lambda: datetime.now(UTC).replace(microsecond=0),
    )

    with pytest.raises(FileImportUnavailable):
        worker.run(
            FileImportLeaseRedemption(
                scenario.token,
                scenario.organization_id,
                scenario.prepared.job_id,
                scenario.source_ref,
            )
        )

    assert _job_state(migration_configuration, scenario) == ("failed", 0)
    assert _scenario_effect_counts(migration_configuration, scenario) == (
        1,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )


def test_late_publication_error_rolls_back_every_content_and_access_write(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario = _prepare_file_import_scenario(
        tmp_path, migration_configuration, guarded_control_engine
    )
    claims = _scenario_claims(scenario)
    assert _redeem_direct(guarded_worker_engine, claims) is not None
    migration_engine = create_database_engine(migration_configuration)
    trigger_installed = False
    try:
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.context_test_reject_file_activation()
                    RETURNS trigger LANGUAGE plpgsql AS $function$
                    BEGIN
                        RAISE EXCEPTION 'injected late file publication failure';
                    END; $function$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TRIGGER reject_file_activation
                    BEFORE UPDATE OF active_revision_id ON context_resource
                    FOR EACH ROW EXECUTE FUNCTION
                        public.context_test_reject_file_activation()
                    """
                )
            )
            trigger_installed = True
        with pytest.raises(SQLAlchemyError, match="injected late"):
            _publish_direct(
                guarded_worker_engine,
                claims,
                resource_ref=f"resource:test:{uuid4()}",
                revision_id=uuid4(),
            )
    finally:
        if trigger_installed:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "DROP TRIGGER IF EXISTS reject_file_activation "
                        "ON context_resource"
                    )
                )
                connection.execute(
                    text(
                        "DROP FUNCTION IF EXISTS "
                        "public.context_test_reject_file_activation()"
                    )
                )
        migration_engine.dispose()

    assert _job_state(migration_configuration, scenario) == ("running", 0)
    assert _scenario_effect_counts(migration_configuration, scenario) == (
        1,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
