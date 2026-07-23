"""Exact WorkerLease execution path for one registered Markdown file."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import Engine, Row, text
from sqlalchemy.exc import SQLAlchemyError

from adapters.file_source import FileRootRegistry
from adapters.parsers.markdown import compile_markdown
from engine.control import (
    FileImportPath,
    FileImportReceiver,
    FileRootRef,
    SourceRef,
)
from engine.persistence.role_guard import assert_worker_role
from engine.runtime.content_io import exact_phrase_digest
from engine.runtime.evidence import CandidateRef
from engine.supply import (
    FILE_IMPORT_WORKER_LEASE_OPERATION,
    CompilationFailure,
    MarkdownCompilerConfig,
    ParsedDocument,
    WorkerLeaseClaims,
    WorkerLeaseCodec,
    WorkerLeaseRejectionAuditReceipt,
    WorkerLeaseToken,
    WorkNotAvailable,
    canonicalize_parsed_document,
    worker_lease_digest,
)
from engine.supply.jobs import _require_utc


@dataclass(frozen=True, slots=True)
class FileImportLeaseRedemption:
    """Untrusted queue carrier with one opaque lease and routing locators."""

    token: WorkerLeaseToken = field(repr=False)
    expected_organization_id: UUID = field(repr=False)
    expected_job_id: UUID = field(repr=False)
    expected_source_ref: SourceRef = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.token) is not WorkerLeaseToken:
            raise TypeError("File import redemption requires WorkerLeaseToken")
        if (
            type(self.expected_organization_id) is not UUID
            or type(self.expected_job_id) is not UUID
        ):
            raise TypeError("File import redemption identifiers must be UUID")
        if type(self.expected_source_ref) is not SourceRef:
            raise TypeError("File import redemption source must be SourceRef")


@dataclass(frozen=True, slots=True)
class PublishedFileImport:
    """Complete active lineage for one published or unchanged acquisition."""

    candidate_refs: tuple[CandidateRef, ...]
    acquisition_id: UUID = field(repr=False)
    content_identity_digest: str = field(repr=False)
    outcome: Literal["published", "replaced", "unchanged"] = "published"
    reason_digest: str | None = field(default=None, repr=False)
    publication_states: tuple[str, str, str] = (
        "prepared",
        "indexed",
        "active",
    )
    effect_count: Literal[0, 1] = 1

    def __post_init__(self) -> None:
        if (
            type(self.candidate_refs) is not tuple
            or not self.candidate_refs
            or any(
                type(candidate) is not CandidateRef for candidate in self.candidate_refs
            )
        ):
            raise TypeError("published File import requires CandidateRef values")
        first = self.candidate_refs[0]
        if any(
            candidate.organization_id != first.organization_id
            or candidate.source_ref != first.source_ref
            or candidate.resource_ref != first.resource_ref
            or candidate.revision_ref != first.revision_ref
            for candidate in self.candidate_refs
        ) or len({candidate.fragment_ref for candidate in self.candidate_refs}) != len(
            self.candidate_refs
        ):
            raise ValueError(
                "published File candidates must share exact Revision lineage"
            )
        if type(self.acquisition_id) is not UUID:
            raise TypeError("published File import acquisition must be UUID")
        if (
            type(self.content_identity_digest) is not str
            or len(self.content_identity_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.content_identity_digest
            )
        ):
            raise ValueError("File import content identity must be SHA-256")
        if self.publication_states != ("prepared", "indexed", "active"):
            raise ValueError("File publication state sequence must remain closed")
        if self.outcome in {"published", "replaced"}:
            if self.effect_count != 1 or self.reason_digest is not None:
                raise ValueError("published File import has exactly one effect")
        elif self.outcome == "unchanged":
            if (
                self.effect_count != 0
                or type(self.reason_digest) is not str
                or len(self.reason_digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in self.reason_digest
                )
            ):
                raise ValueError("unchanged File import requires a reason digest")
        else:
            raise ValueError("File import outcome must be closed")

    @property
    def candidate_ref(self) -> CandidateRef:
        """Compatibility locator for the first source-ordered Fragment."""

        return self.candidate_refs[0]


class FileImportUnavailable(RuntimeError):
    """Generic failure after a valid lease reaches acquisition/publication."""


@dataclass(frozen=True, slots=True)
class _RedeemedFileImport:
    source_ref: SourceRef
    root_ref: FileRootRef
    path: FileImportPath
    acquisition_id: UUID


def _rejection(token: WorkerLeaseToken) -> WorkNotAvailable:
    return WorkNotAvailable(
        WorkerLeaseRejectionAuditReceipt(worker_lease_digest(token))
    )


def _resource_ref(source_ref: SourceRef, path: FileImportPath) -> str:
    identity = sha256(
        b"context-engine.file-resource.v1\x00"
        + source_ref.value.bytes
        + path.value.encode("utf-8")
    ).hexdigest()
    return f"resource:file:{identity}"


class PostgreSQLFileImportWorker:
    """Verify, redeem, acquire, compile, and atomically publish one file."""

    __slots__ = (
        "_clock",
        "_codec",
        "_config",
        "_engine",
        "_identity",
        "_roots",
        "_uuid_factory",
    )

    def __init__(
        self,
        engine: Engine,
        codec: WorkerLeaseCodec,
        identity: FileImportReceiver,
        roots: FileRootRegistry,
        config: MarkdownCompilerConfig,
        *,
        clock: Callable[[], object],
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if type(codec) is not WorkerLeaseCodec:
            raise TypeError("File import worker requires WorkerLeaseCodec")
        if type(identity) is not FileImportReceiver:
            raise TypeError("File import worker requires FileImportReceiver")
        if type(roots) is not FileRootRegistry:
            raise TypeError("File import worker requires FileRootRegistry")
        if type(config) is not MarkdownCompilerConfig:
            raise TypeError("File import worker requires MarkdownCompilerConfig")
        if not callable(clock) or not callable(uuid_factory):
            raise TypeError("File import worker requires clock and UUID factory")
        self._engine = engine
        self._codec = codec
        self._identity = identity
        self._roots = roots
        self._config = config
        self._clock = clock
        self._uuid_factory = uuid_factory

    def run(self, redemption: FileImportLeaseRedemption) -> PublishedFileImport:
        """Perform file I/O only after signature and durable lease redemption."""

        if type(redemption) is not FileImportLeaseRedemption:
            raise TypeError("File import worker requires exact redemption")
        checked_at = _require_utc("File import worker clock", self._clock())
        identity = self._identity
        claims = self._codec.verify(
            redemption.token,
            expected_organization_id=redemption.expected_organization_id,
            expected_job_id=redemption.expected_job_id,
            expected_service_principal_id=identity.service_principal_id,
            expected_workload=identity.workload,
            expected_operation=FILE_IMPORT_WORKER_LEASE_OPERATION,
            expected_worker_audience=identity.worker_audience,
            expected_source_ref=str(redemption.expected_source_ref.value),
            now=checked_at,
        )
        redeemed = self._redeem(redemption.token, claims)
        try:
            source = self._roots.read(redeemed.root_ref, redeemed.path)
            outcome = compile_markdown(source, self._config)
        except LookupError:
            self._fail(redemption.token, claims)
            raise FileImportUnavailable("File import is unavailable") from None
        if type(outcome) is CompilationFailure:
            self._fail(redemption.token, claims)
            raise FileImportUnavailable("File import is unavailable")
        if type(outcome) is not ParsedDocument:  # pragma: no cover - closed union
            self._fail(redemption.token, claims)
            raise FileImportUnavailable("File import is unavailable")
        try:
            return self._publish(redemption.token, claims, redeemed, outcome)
        except (FileImportUnavailable, WorkNotAvailable):
            self._fail(redemption.token, claims)
            raise

    def _redeem(
        self,
        token: WorkerLeaseToken,
        claims: WorkerLeaseClaims,
    ) -> _RedeemedFileImport:
        if type(claims) is not WorkerLeaseClaims:
            raise _rejection(token)
        try:
            with self._engine.begin() as connection:
                assert_worker_role(connection)
                row = connection.execute(
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
                        "organization_id": claims.organization_id,
                        "job_id": claims.job_id,
                        "service_principal_id": claims.service_principal_id,
                        "source_ref": claims.source_ref,
                        "signing_key_version": claims.signing_key_version,
                        "nonce": claims.nonce,
                        "issued_at": claims.issued_at,
                        "expires_at": claims.expires_at,
                    },
                ).one_or_none()
                if row is None or row.source_ref != claims.source_ref:
                    raise _rejection(token)
                return _RedeemedFileImport(
                    source_ref=SourceRef(UUID(row.source_ref)),
                    root_ref=FileRootRef(row.root_ref),
                    path=FileImportPath(row.relative_path),
                    acquisition_id=row.acquisition_id,
                )
        except WorkNotAvailable:
            raise
        except (SQLAlchemyError, AssertionError, ValueError):
            raise FileImportUnavailable(
                "File import redemption is unavailable"
            ) from None

    def _publish(
        self,
        token: WorkerLeaseToken,
        claims: WorkerLeaseClaims,
        redeemed: _RedeemedFileImport,
        document: ParsedDocument,
    ) -> PublishedFileImport:
        if type(claims) is not WorkerLeaseClaims:
            raise _rejection(token)
        revision_id = self._uuid_factory()
        resource_ref = _resource_ref(redeemed.source_ref, redeemed.path)
        structural = document.provenance.is_structural_v2
        if structural:
            compilation_document = json.loads(
                canonicalize_parsed_document(document).decode("utf-8")
            )
            statement = """
                SELECT *
                FROM public.context_worker_publish_structural_file_import_v2(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref,
                    :revision_id, :canonical_text,
                    :content_hash, :compilation_digest,
                    :compiler_version, :config_version,
                    CAST(:compilation_document AS jsonb),
                    :signing_key_version, :nonce, :issued_at, :expires_at
                )
            """
            payload: dict[str, object] = {
                "compilation_document": json.dumps(
                    compilation_document,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            }
        else:
            fragment = document.fragments[0]
            statement = """
                SELECT *
                FROM public.context_worker_publish_file_import_v2(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref,
                    :revision_id, :fragment_ref, :canonical_text,
                    :paragraph, :content_hash, :compilation_digest,
                    :compiler_version, :config_version, :phrase_digest,
                    :signing_key_version, :nonce, :issued_at, :expires_at
                )
            """
            payload = {
                "fragment_ref": fragment.fragment_ref,
                "paragraph": fragment.contextual_text,
                "phrase_digest": exact_phrase_digest(fragment.search_phrases[0]),
            }
        try:
            with self._engine.begin() as connection:
                assert_worker_role(connection)
                row = connection.execute(
                    text(statement),
                    {
                        "organization_id": claims.organization_id,
                        "job_id": claims.job_id,
                        "service_principal_id": claims.service_principal_id,
                        "source_ref": claims.source_ref,
                        "resource_ref": resource_ref,
                        "revision_id": revision_id,
                        "canonical_text": document.canonical_text,
                        "content_hash": document.content_hash,
                        "compilation_digest": document.compilation_digest,
                        "compiler_version": document.provenance.compiler_version,
                        "config_version": document.provenance.config_version,
                        "signing_key_version": claims.signing_key_version,
                        "nonce": claims.nonce,
                        "issued_at": claims.issued_at,
                        "expires_at": claims.expires_at,
                        **payload,
                    },
                ).one_or_none()
            if row is None:
                row = self._replace(
                    claims,
                    resource_ref,
                    revision_id,
                    document,
                    payload,
                    structural=structural,
                )
            if (
                row is None
                or row.effect_count not in {0, 1}
                or row.outcome not in {"published", "replaced", "unchanged"}
                or row.active_revision_id is None
                or not row.fragment_refs
            ):
                raise _rejection(token)
        except WorkNotAvailable:
            raise
        except (SQLAlchemyError, AssertionError):
            raise FileImportUnavailable("File publication is unavailable") from None
        return PublishedFileImport(
            candidate_refs=tuple(
                CandidateRef(
                    organization_id=claims.organization_id,
                    source_ref=str(redeemed.source_ref.value),
                    resource_ref=resource_ref,
                    revision_ref=str(row.active_revision_id),
                    fragment_ref=fragment_ref,
                )
                for fragment_ref in row.fragment_refs
            ),
            acquisition_id=redeemed.acquisition_id,
            content_identity_digest=row.content_identity_digest,
            outcome=row.outcome,
            reason_digest=row.reason_digest,
            effect_count=row.effect_count,
        )

    def _replace(
        self,
        claims: WorkerLeaseClaims,
        resource_ref: str,
        revision_id: UUID,
        document: ParsedDocument,
        payload: dict[str, object],
        *,
        structural: bool,
    ) -> Row[tuple[object, ...]] | None:
        if structural:
            stage_statement = """
                SELECT *
                FROM public.context_worker_stage_structural_file_replacement(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref, :revision_id,
                    :canonical_text, :content_hash, :compilation_digest,
                    :compiler_version, :config_version,
                    CAST(:compilation_document AS jsonb),
                    :signing_key_version, :nonce, :issued_at, :expires_at
                )
            """
        else:
            stage_statement = """
                SELECT *
                FROM public.context_worker_stage_file_replacement(
                    :organization_id, :job_id, :service_principal_id,
                    :source_ref, :resource_ref, :revision_id,
                    :fragment_ref, :canonical_text, :paragraph,
                    :content_hash, :compilation_digest,
                    :compiler_version, :config_version, :phrase_digest,
                    :signing_key_version, :nonce, :issued_at, :expires_at
                )
            """
        with self._engine.begin() as connection:
            assert_worker_role(connection)
            staged = connection.execute(
                text(stage_statement),
                {
                    "organization_id": claims.organization_id,
                    "job_id": claims.job_id,
                    "service_principal_id": claims.service_principal_id,
                    "source_ref": claims.source_ref,
                    "resource_ref": resource_ref,
                    "revision_id": revision_id,
                    "canonical_text": document.canonical_text,
                    "content_hash": document.content_hash,
                    "compilation_digest": document.compilation_digest,
                    "compiler_version": document.provenance.compiler_version,
                    "config_version": document.provenance.config_version,
                    "signing_key_version": claims.signing_key_version,
                    "nonce": claims.nonce,
                    "issued_at": claims.issued_at,
                    "expires_at": claims.expires_at,
                    **payload,
                },
            ).one_or_none()
        if staged is None:
            return None
        with self._engine.begin() as connection:
            assert_worker_role(connection)
            return connection.execute(
                text(
                    """
                    SELECT *
                    FROM public.context_worker_activate_file_replacement(
                        :organization_id, :job_id, :service_principal_id,
                        :source_ref, :resource_ref,
                        :previous_revision_id, :replacement_revision_id,
                        :signing_key_version, :nonce, :issued_at, :expires_at
                    )
                    """
                ),
                {
                    "organization_id": claims.organization_id,
                    "job_id": claims.job_id,
                    "service_principal_id": claims.service_principal_id,
                    "source_ref": claims.source_ref,
                    "resource_ref": resource_ref,
                    "previous_revision_id": staged.previous_revision_id,
                    "replacement_revision_id": staged.replacement_revision_id,
                    "signing_key_version": claims.signing_key_version,
                    "nonce": claims.nonce,
                    "issued_at": claims.issued_at,
                    "expires_at": claims.expires_at,
                },
            ).one_or_none()

    def _fail(
        self,
        token: WorkerLeaseToken,
        claims: WorkerLeaseClaims,
    ) -> None:
        """Seal a redeemed job as failed without retaining content or reason."""

        try:
            with self._engine.begin() as connection:
                assert_worker_role(connection)
                changed = connection.execute(
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
                        "organization_id": claims.organization_id,
                        "job_id": claims.job_id,
                        "service_principal_id": claims.service_principal_id,
                        "source_ref": claims.source_ref,
                        "signing_key_version": claims.signing_key_version,
                        "nonce": claims.nonce,
                        "issued_at": claims.issued_at,
                        "expires_at": claims.expires_at,
                    },
                ).scalar_one()
                if changed is not True:
                    raise _rejection(token)
        except WorkNotAvailable:
            raise
        except (SQLAlchemyError, AssertionError):
            raise FileImportUnavailable(
                "File import failure recording is unavailable"
            ) from None
