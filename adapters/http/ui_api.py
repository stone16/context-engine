"""Tenant-safe backing contracts for the server-rendered operator surface."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final, Literal, Protocol, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, RootModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from adapters.file_source import FileRootRegistry
from adapters.http.authentication import VerifiedAuthenticationContext
from adapters.parsers.markdown import compile_markdown
from engine.control import (
    FILE_CAPABILITY_MANIFEST,
    FILE_CHANGE_CAPABILITY_MANIFEST,
    FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    FILE_IMPORT_CAPABILITY_MANIFEST,
    CapabilityStatus,
    ControlOperation,
    FileImportPath,
    FileRootRef,
    MinimalUiControlGate,
    TrustedControlCall,
)
from engine.persistence.membership_context import (
    MembershipAuthorityUnavailable,
    MembershipIdentity,
    MembershipNotCurrent,
    PostgreSQLMembershipAuthority,
)
from engine.persistence.role_guard import assert_control_role, assert_runtime_role
from engine.runtime.actor import CurrentMembershipVerification
from engine.supply import (
    CompilationFailure,
    CompilationFailureCode,
    MarkdownCompilerConfig,
    ParsedDocument,
    UnsupportedConstruct,
    contains_rich_markdown_link,
)

_PREVIEW_TTL: Final = timedelta(minutes=10)
_FILE_CAPABILITY_MANIFESTS: Final = {
    manifest.declaration_version: manifest
    for manifest in (
        FILE_CAPABILITY_MANIFEST,
        FILE_IMPORT_CAPABILITY_MANIFEST,
        FILE_CHANGE_CAPABILITY_MANIFEST,
        FILE_DELETE_OBSERVATION_CAPABILITY_MANIFEST,
    )
}
_PREVIEW_DOMAIN: Final = b"context-engine.ui-preview.v1\x00"
_FEEDBACK_DOMAIN: Final = b"context-engine.ui-feedback.v1\x00"
_MAX_SIGNED_BIGINT: Final = (1 << 63) - 1


def _contains_rich_markdown_link(source: bytes) -> bool:
    try:
        decoded = source.removeprefix(b"\xef\xbb\xbf").decode(
            "utf-8",
            errors="strict",
        )
    except UnicodeDecodeError:
        return False
    return contains_rich_markdown_link(decoded)


class UiApiUnavailable(RuntimeError):
    """A required operator projection or evidence store is unavailable."""


@dataclass(frozen=True, slots=True)
class UiActor:
    """Verified HTTP identity projected without granting new authority."""

    organization_id: UUID = field(repr=False)
    user_id: UUID = field(repr=False)
    membership_id: UUID = field(repr=False)
    membership_version: int
    principal_ref: str = field(repr=False)
    authentication_binding_ref: str = field(repr=False, default="ui-binding")


@dataclass(frozen=True, slots=True)
class FeedbackCapture:
    """Minimal ContextRun evidence with no behavior or publication action."""

    run_ref: str
    rating: Literal["helpful", "not_helpful"]
    note: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.run_ref) is not str
            or not self.run_ref
            or self.run_ref.isspace()
            or len(self.run_ref) > 256
        ):
            raise ValueError("feedback run ref is invalid")
        if self.rating not in {"helpful", "not_helpful"}:
            raise ValueError("feedback rating is invalid")
        if self.note is not None and (
            type(self.note) is not str
            or not self.note
            or self.note.isspace()
            or len(self.note) > 1000
        ):
            raise ValueError("feedback note is invalid")


class FeedbackWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runRef: str = Field(strict=True, min_length=1, max_length=256)
    rating: Literal["helpful", "not_helpful"]
    note: str | None = Field(default=None, strict=True, min_length=1, max_length=1000)


class ImportPreviewWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceRef: UUID
    path: str = Field(strict=True, min_length=1, max_length=255)


class PreviewConfirmWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previewToken: str = Field(strict=True, min_length=1, max_length=4096)


class ArticleWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resourceRef: str = Field(strict=True, min_length=1, max_length=512)


class ArticlePolicyPreviewWire(ArticleWire):
    policyKind: Literal["private", "organization", "groups"]
    groupRefs: list[str] = Field(default_factory=list, max_length=100)


class UiSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["active"]


class UiProfileIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profileRef: str = Field(strict=True, min_length=1, max_length=512)
    digest: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")


class UiProfilesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    releaseGeneration: int = Field(strict=True, ge=1)
    releaseManifestRef: str = Field(strict=True, min_length=1, max_length=512)
    contentProfile: UiProfileIdentityResponse
    indexProfile: UiProfileIdentityResponse
    runtimeProfile: UiProfileIdentityResponse


class UiSourceHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activeResourceCount: int = Field(strict=True, ge=0)
    displayName: str = Field(strict=True, min_length=1, max_length=200)
    lastSuccessfulAcquisitionAgeSeconds: int | None = Field(
        default=None, strict=True, ge=0
    )
    refusalCategories: list[str] = Field(max_length=100)
    sourceRef: str = Field(strict=True, min_length=1, max_length=512)
    status: Literal["ready", "refused", "waiting_first_success"]


class UiOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    releaseGeneration: int = Field(strict=True, ge=1)
    releaseManifestRef: str = Field(strict=True, min_length=1, max_length=512)
    sources: list[UiSourceHealthResponse]


class UiImportFragmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fragmentRef: str = Field(strict=True, min_length=1, max_length=512)
    text: str = Field(strict=True, min_length=1)


class UiImportPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["preview_ready"]
    compilationDigest: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    fragmentDigest: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    fragments: list[UiImportFragmentResponse] = Field(min_length=1)
    path: str = Field(strict=True, min_length=1, max_length=255)
    previewToken: str = Field(strict=True, min_length=1, max_length=4096)
    sourceRef: str = Field(strict=True, min_length=1, max_length=512)


class UiImportScanHandoffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["scan_handoff"]
    path: str = Field(strict=True, min_length=1, max_length=255)
    prerequisiteCommands: list[str] = Field(max_length=2)
    reason: Literal["rich_markdown_requires_leased_worker"]
    scanCommand: str = Field(strict=True, min_length=1, max_length=1024)
    sourceRef: str = Field(strict=True, min_length=1, max_length=512)
    workerCommand: str = Field(strict=True, min_length=1, max_length=1024)


type UiImportPreviewOutcome = Annotated[
    UiImportPreviewResponse | UiImportScanHandoffResponse,
    Field(discriminator="kind"),
]


class UiImportPreviewOutcomeResponse(RootModel[UiImportPreviewOutcome]):
    pass


class UiImportConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobRef: str = Field(strict=True, min_length=1, max_length=512)
    state: Literal["queued"]


class UiArticleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effectiveGroupRefs: list[str] = Field(max_length=100)
    effectivePolicyKind: Literal["private", "organization", "groups"] | None
    localGroupRefs: list[str] = Field(max_length=100)
    localPolicyKind: Literal["private", "organization", "groups"] | None
    policyEpoch: int = Field(strict=True, ge=1)
    policyVersion: int = Field(strict=True, ge=1)
    published: bool = Field(strict=True)
    resolutionRung: Literal[
        "explicit_article", "source_default", "tenant_default", "isolation"
    ]
    resourceRef: str = Field(strict=True, min_length=1, max_length=512)
    sourceRef: str = Field(strict=True, min_length=1, max_length=512)


class UiArticlePolicyProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groupRefs: list[str] = Field(max_length=100)
    policyKind: Literal["private", "organization", "groups"]


class UiArticlePolicyPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: UiArticleResponse
    proposed: UiArticlePolicyProposalResponse
    previewToken: str = Field(strict=True, min_length=1, max_length=4096)


class UiArticlePolicyConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policyEpoch: int = Field(strict=True, ge=1)
    policyVersion: int = Field(strict=True, ge=1)
    state: Literal["changed"]


class UiFeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedbackRef: str = Field(strict=True, min_length=1, max_length=512)
    state: Literal["recorded"]


class UiApi(Protocol):
    """Narrow backing seam; methods return only closed public JSON documents."""

    def overview(
        self, actor: UiActor, control_call: TrustedControlCall
    ) -> dict[str, object]: ...

    def profiles(self, actor: UiActor) -> dict[str, object]: ...

    def preview_import(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        source_ref: UUID,
        path: str,
    ) -> dict[str, object]: ...

    def confirm_import(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        preview_token: str,
    ) -> dict[str, object]: ...

    def article(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        resource_ref: str,
    ) -> dict[str, object]: ...

    def preview_article_policy(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        resource_ref: str,
        policy_kind: str,
        group_refs: tuple[str, ...],
    ) -> dict[str, object]: ...

    def confirm_article_policy(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        preview_token: str,
    ) -> dict[str, object]: ...

    def capture_feedback(
        self,
        actor: UiActor,
        feedback: FeedbackCapture,
    ) -> dict[str, object]: ...


class RefusingUiApi:
    def overview(
        self, actor: UiActor, control_call: TrustedControlCall
    ) -> dict[str, object]:
        del actor, control_call
        raise UiApiUnavailable

    def profiles(self, actor: UiActor) -> dict[str, object]:
        del actor
        raise UiApiUnavailable

    def preview_import(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        source_ref: UUID,
        path: str,
    ) -> dict[str, object]:
        del actor, control_call, source_ref, path
        raise UiApiUnavailable

    def confirm_import(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        preview_token: str,
    ) -> dict[str, object]:
        del actor, control_call, preview_token
        raise UiApiUnavailable

    def article(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        resource_ref: str,
    ) -> dict[str, object]:
        del actor, control_call, resource_ref
        raise UiApiUnavailable

    def preview_article_policy(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        resource_ref: str,
        policy_kind: str,
        group_refs: tuple[str, ...],
    ) -> dict[str, object]:
        del actor, control_call, resource_ref, policy_kind, group_refs
        raise UiApiUnavailable

    def confirm_article_policy(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        preview_token: str,
    ) -> dict[str, object]:
        del actor, control_call, preview_token
        raise UiApiUnavailable

    def capture_feedback(
        self,
        actor: UiActor,
        feedback: FeedbackCapture,
    ) -> dict[str, object]:
        del actor, feedback
        raise UiApiUnavailable


@dataclass(frozen=True, slots=True)
class _PreviewCodec:
    key: bytes = field(repr=False)
    clock: Callable[[], datetime] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.key) is not bytes or len(self.key) < 32:
            raise ValueError("UI preview signing key is unavailable")
        if not callable(self.clock):
            raise TypeError("UI preview clock is required")

    def issue(
        self,
        kind: Literal["file_import", "article_policy"],
        actor: UiActor,
        payload: dict[str, object],
    ) -> str:
        now = _utc(self.clock())
        document = {
            "actor": _actor_document(actor),
            "expiresAt": int((now + _PREVIEW_TTL).timestamp()),
            "issuedAt": int(now.timestamp()),
            "kind": kind,
            "payload": payload,
        }
        encoded = _encode(
            json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        signature = _encode(
            hmac.digest(self.key, _PREVIEW_DOMAIN + encoded.encode("ascii"), "sha256")
        )
        return f"{encoded}.{signature}"

    def verify(
        self,
        token: str,
        *,
        kind: Literal["file_import", "article_policy"],
        actor: UiActor,
    ) -> dict[str, object]:
        if type(token) is not str or len(token) > 4096 or token.count(".") != 1:
            raise UiApiUnavailable
        encoded, supplied = token.split(".")
        expected = _encode(
            hmac.digest(self.key, _PREVIEW_DOMAIN + encoded.encode("ascii"), "sha256")
        )
        if not hmac.compare_digest(supplied, expected):
            raise UiApiUnavailable
        try:
            document = json.loads(_decode(encoded))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise UiApiUnavailable from None
        now = int(_utc(self.clock()).timestamp())
        if (
            type(document) is not dict
            or document.get("kind") != kind
            or document.get("actor") != _actor_document(actor)
            or type(document.get("issuedAt")) is not int
            or type(document.get("expiresAt")) is not int
            or not cast(int, document["issuedAt"])
            <= now
            < cast(int, document["expiresAt"])
            or cast(int, document["expiresAt"]) - cast(int, document["issuedAt"])
            != int(_PREVIEW_TTL.total_seconds())
            or type(document.get("payload")) is not dict
        ):
            raise UiApiUnavailable
        return cast(dict[str, object], document["payload"])


class PostgreSQLUiApi:
    """Authenticated M1 projections and explicit effects on least-privilege seams."""

    def __init__(
        self,
        membership_authority: PostgreSQLMembershipAuthority,
        control_engine: Engine | None,
        *,
        preview_key: bytes,
        feedback_engine: Engine | None = None,
        control_gate: MinimalUiControlGate | None = None,
        roots: FileRootRegistry | None = None,
        file_import_service_principal_id: UUID | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if type(membership_authority) is not PostgreSQLMembershipAuthority:
            raise TypeError("UI requires the PostgreSQL Membership authority")
        if control_engine is not None and not isinstance(control_engine, Engine):
            raise TypeError("UI Control database engine is invalid")
        if feedback_engine is not None and not isinstance(feedback_engine, Engine):
            raise TypeError("UI feedback database engine is invalid")
        if roots is not None and type(roots) is not FileRootRegistry:
            raise TypeError("UI File roots have the wrong nominal type")
        if (
            file_import_service_principal_id is not None
            and type(file_import_service_principal_id) is not UUID
        ):
            raise TypeError("UI File receiver must be UUID")
        self._membership_authority = membership_authority
        self._control_engine = control_engine
        self._feedback_engine = feedback_engine
        self._control_gate = control_gate
        self._roots = roots
        self._receiver_id = file_import_service_principal_id
        self._clock = clock
        self._preview_codec = _PreviewCodec(preview_key, clock)

    @contextmanager
    def _verified(
        self,
        actor: UiActor,
    ) -> Iterator[CurrentMembershipVerification]:
        checked_at = _utc(self._clock())
        try:
            with self._membership_authority.current_user_actor(
                MembershipIdentity(
                    organization_id=actor.organization_id,
                    user_id=actor.user_id,
                    membership_id=actor.membership_id,
                    membership_version=actor.membership_version,
                    principal_ref=actor.principal_ref,
                    request_id=f"ui-{uuid4().hex}",
                    authentication_binding_ref=actor.authentication_binding_ref,
                    checked_at=checked_at,
                )
            ) as verification:
                yield verification
        except (MembershipAuthorityUnavailable, MembershipNotCurrent):
            raise UiApiUnavailable from None

    @contextmanager
    def _control(self, actor: UiActor) -> Iterator[Any]:
        engine = self._control_engine
        if engine is None:
            raise UiApiUnavailable
        try:
            with engine.begin() as connection:
                assert_control_role(connection)
                observed = connection.execute(
                    text(
                        "SELECT set_config('app.organization_id', "
                        ":organization_id, true)"
                    ),
                    {"organization_id": str(actor.organization_id)},
                ).scalar_one()
                if observed != str(actor.organization_id):
                    raise UiApiUnavailable
                yield connection
        except UiApiUnavailable:
            raise
        except (AssertionError, SQLAlchemyError):
            raise UiApiUnavailable from None

    @contextmanager
    def _feedback(self, actor: UiActor) -> Iterator[Any]:
        engine = self._feedback_engine
        if engine is None:
            raise UiApiUnavailable
        expected = {
            "actor_kind": "user",
            "membership_id": str(actor.membership_id),
            "membership_version": str(actor.membership_version),
            "organization_id": str(actor.organization_id),
            "principal_ref": actor.principal_ref,
            "user_id": str(actor.user_id),
        }
        try:
            with engine.begin() as connection:
                assert_runtime_role(connection)
                observed = dict(
                    connection.execute(
                        text(
                            """
                            SELECT
                                set_config('app.actor_kind', 'user', true)
                                    AS actor_kind,
                                set_config('app.membership_id',
                                    :membership_id, true) AS membership_id,
                                set_config('app.membership_version',
                                    :membership_version, true)
                                    AS membership_version,
                                set_config('app.organization_id',
                                    :organization_id, true) AS organization_id,
                                set_config('app.principal_ref',
                                    :principal_ref, true) AS principal_ref,
                                set_config('app.user_id', :user_id, true)
                                    AS user_id
                            """
                        ),
                        expected,
                    ).mappings().one()
                )
                if observed != expected:
                    raise UiApiUnavailable
                yield connection
        except UiApiUnavailable:
            raise
        except (AssertionError, SQLAlchemyError):
            raise UiApiUnavailable from None

    def _consume_control(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        operation: ControlOperation,
    ) -> None:
        gate = self._control_gate
        if gate is None:
            raise UiApiUnavailable
        try:
            gate.consume(
                control_call,
                organization_id=actor.organization_id,
                operation=operation,
            )
        except Exception:
            raise UiApiUnavailable from None

    def overview(
        self, actor: UiActor, control_call: TrustedControlCall
    ) -> dict[str, object]:
        self._consume_control(
            actor, control_call, ControlOperation.READ_SOURCE_PROGRESS
        )
        with self._verified(actor) as verification:
            release = verification.active_runtime_release
            if release is None:
                raise UiApiUnavailable
            with self._control(actor) as connection:
                sources = tuple(
                    connection.execute(
                        text(
                            """
                            SELECT source_id, display_name
                            FROM context_source
                            WHERE organization_id = :organization_id
                              AND lifecycle_state = 'active'
                            ORDER BY display_name COLLATE "C", source_id
                            """
                        ),
                        {"organization_id": actor.organization_id},
                    ).mappings()
                )
                projected: list[dict[str, object]] = []
                for source in sources:
                    rows = tuple(
                        connection.execute(
                            text(
                                """
                                SELECT * FROM
                                context_control_read_file_source_status(
                                    :organization_id, :source_id
                                )
                                """
                            ),
                            {
                                "organization_id": actor.organization_id,
                                "source_id": source["source_id"],
                            },
                        ).mappings()
                    )
                    if not rows:
                        raise UiApiUnavailable
                    categories = sorted(
                        {
                            cast(str, row["refusal_category"])
                            for row in rows
                            if row["refusal_category"] is not None
                        }
                    )
                    first = rows[0]
                    status = (
                        "refused"
                        if categories
                        else (
                            "waiting_first_success"
                            if first["last_successful_acquisition_at"] is None
                            else "ready"
                        )
                    )
                    projected.append(
                        {
                            "activeResourceCount": first["active_resource_count"],
                            "displayName": source["display_name"],
                            "lastSuccessfulAcquisitionAgeSeconds": first[
                                "last_successful_acquisition_age_seconds"
                            ],
                            "refusalCategories": categories,
                            "sourceRef": str(source["source_id"]),
                            "status": status,
                        }
                    )
        return {
            "releaseGeneration": release.active_generation,
            "releaseManifestRef": release.manifest_ref,
            "sources": projected,
        }

    def profiles(self, actor: UiActor) -> dict[str, object]:
        with self._verified(actor) as verification:
            release = verification.active_runtime_release
            if release is None:
                raise UiApiUnavailable
            return {
                "releaseGeneration": release.active_generation,
                "releaseManifestRef": release.manifest_ref,
                "contentProfile": {
                    "profileRef": release.content_profile_ref,
                    "digest": release.content_profile_digest,
                },
                "indexProfile": {
                    "profileRef": release.index_profile_ref,
                    "digest": release.index_profile_digest,
                },
                "runtimeProfile": {
                    "profileRef": release.runtime_profile_ref,
                    "digest": release.runtime_profile_digest,
                },
            }

    def preview_import(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        source_ref: UUID,
        path: str,
    ) -> dict[str, object]:
        self._consume_control(actor, control_call, ControlOperation.IMPORT_FILE)
        roots = self._roots
        if roots is None or self._receiver_id is None:
            raise UiApiUnavailable
        try:
            import_path = FileImportPath(path)
        except (TypeError, ValueError):
            raise UiApiUnavailable from None
        with self._verified(actor), self._control(actor) as connection:
            source = (
                connection.execute(
                    text(
                        """
                        SELECT version.root_ref,
                               version.capability_manifest
                                      ->>'declarationVersion'
                                      AS declaration_version
                        FROM context_source AS source
                        JOIN source_version AS version
                          ON version.organization_id = source.organization_id
                         AND version.source_id = source.source_id
                         AND version.version_id = source.active_version_id
                        WHERE source.organization_id = :organization_id
                          AND source.source_id = :source_id
                          AND source.lifecycle_state = 'active'
                        """
                    ),
                    {
                        "organization_id": actor.organization_id,
                        "source_id": source_ref,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if (
            source is None
            or type(source["root_ref"]) is not str
            or source["declaration_version"] not in _FILE_CAPABILITY_MANIFESTS
        ):
            raise UiApiUnavailable
        root_ref = source["root_ref"]
        capabilities = _FILE_CAPABILITY_MANIFESTS[source["declaration_version"]]
        try:
            raw = roots.read(FileRootRef(root_ref), import_path)
            outcome = compile_markdown(
                raw,
                MarkdownCompilerConfig("markdown-config-v1"),
            )
        except (LookupError, RuntimeError, TypeError, ValueError):
            raise UiApiUnavailable from None
        requires_scan_handoff = (
            (
                type(outcome) is CompilationFailure
                and outcome.code is CompilationFailureCode.UNSUPPORTED_CONSTRUCT
                and outcome.construct is UnsupportedConstruct.LINK_OR_IMAGE
            )
            or _contains_rich_markdown_link(raw)
        )
        if requires_scan_handoff:
            source_arguments = (
                "--organization-id "
                '"$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" '
                f'--source-ref "{source_ref}"'
            )
            prerequisite_commands: list[str] = []
            if capabilities.read_changes is not CapabilityStatus.AVAILABLE:
                prerequisite_commands.append(
                    "uv run context-engine-control activate-change-feed "
                    + source_arguments
                )
            if capabilities.delete_observations is not CapabilityStatus.AVAILABLE:
                prerequisite_commands.append(
                    "uv run context-engine-control activate-delete-observations "
                    + source_arguments
                )
            return {
                "kind": "scan_handoff",
                "path": import_path.value,
                "prerequisiteCommands": prerequisite_commands,
                "reason": "rich_markdown_requires_leased_worker",
                "scanCommand": (
                    "uv run context-engine-control scan " + source_arguments
                ),
                "sourceRef": str(source_ref),
                "workerCommand": "uv run context-engine-worker --dispatch-file-once",
            }
        if type(outcome) is CompilationFailure:
            raise UiApiUnavailable
        if type(outcome) is not ParsedDocument:
            raise UiApiUnavailable
        fragments = [
            {
                "fragmentRef": fragment.fragment_ref,
                "text": fragment.contextual_text,
            }
            for fragment in outcome.fragments
        ]
        fragment_digest = hashlib.sha256(
            json.dumps(
                fragments,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        payload: dict[str, object] = {
            "configVersion": "markdown-config-v1",
            "contentLength": len(raw),
            "contentSha256": hashlib.sha256(raw).hexdigest(),
            "fragmentDigest": fragment_digest,
            "path": import_path.value,
            "sourceRef": str(source_ref),
        }
        return {
            "kind": "preview_ready",
            "compilationDigest": outcome.compilation_digest,
            "fragmentDigest": fragment_digest,
            "fragments": fragments,
            "path": import_path.value,
            "previewToken": self._preview_codec.issue("file_import", actor, payload),
            "sourceRef": str(source_ref),
        }

    def confirm_import(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        preview_token: str,
    ) -> dict[str, object]:
        self._consume_control(actor, control_call, ControlOperation.IMPORT_FILE)
        receiver_id = self._receiver_id
        if receiver_id is None:
            raise UiApiUnavailable
        payload = self._preview_codec.verify(
            preview_token,
            kind="file_import",
            actor=actor,
        )
        try:
            source_ref = UUID(cast(str, payload["sourceRef"]))
            path = FileImportPath(cast(str, payload["path"]))
            content_sha256 = cast(str, payload["contentSha256"])
            content_length = cast(int, payload["contentLength"])
            fragment_digest = cast(str, payload["fragmentDigest"])
            config_version = cast(str, payload["configVersion"])
            if (
                len(content_sha256) != 64
                or len(fragment_digest) != 64
                or type(content_length) is not int
                or content_length < 0
                or config_version != "markdown-config-v1"
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise UiApiUnavailable from None
        token_digest = hashlib.sha256(preview_token.encode("ascii")).hexdigest()
        request_digest = hashlib.sha256(
            b"context-engine.ui-import-confirm.v1\x00" + token_digest.encode("ascii")
        ).hexdigest()
        # The Runtime membership transaction holds the shared publication fence.
        # Close it before the Control function takes the exclusive publication
        # fence; the function independently rechecks the exact Membership.
        with self._verified(actor):
            pass
        with self._control(actor) as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT * FROM context_control_prepare_exact_file_import(
                        :organization_id, :acquisition_id, :job_id,
                        :activated_version_id, :source_id, :relative_path,
                        :audience_principal_ref, :audience_user_id,
                        :audience_membership_id,
                        :audience_membership_version, :idempotency_key,
                        :request_digest, :service_principal_id,
                        :expected_content_sha256, :expected_content_length,
                        :expected_fragment_digest, :compiler_config_version
                        , :preview_digest
                    )
                    """
                    ),
                    {
                        "organization_id": actor.organization_id,
                        "acquisition_id": uuid4(),
                        "job_id": uuid4(),
                        "activated_version_id": uuid4(),
                        "source_id": source_ref,
                        "relative_path": path.value,
                        "audience_principal_ref": actor.principal_ref,
                        "audience_user_id": actor.user_id,
                        "audience_membership_id": actor.membership_id,
                        "audience_membership_version": actor.membership_version,
                        "idempotency_key": f"ui:{token_digest}",
                        "request_digest": request_digest,
                        "service_principal_id": receiver_id,
                        "expected_content_sha256": content_sha256,
                        "expected_content_length": content_length,
                        "expected_fragment_digest": fragment_digest,
                        "compiler_config_version": config_version,
                        "preview_digest": token_digest,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise UiApiUnavailable
        return {"jobRef": str(row["job_id"]), "state": "queued"}

    def article(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        resource_ref: str,
    ) -> dict[str, object]:
        self._consume_control(actor, control_call, ControlOperation.READ_ARTICLE_POLICY)
        return self._article(actor, resource_ref=resource_ref)

    def _article(self, actor: UiActor, *, resource_ref: str) -> dict[str, object]:
        if (
            type(resource_ref) is not str
            or not resource_ref
            or resource_ref.isspace()
            or len(resource_ref) > 512
        ):
            raise UiApiUnavailable
        with self._verified(actor), self._control(actor) as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT * FROM context_control_read_article_policy("
                        ":organization_id, :resource_ref)"
                    ),
                    {
                        "organization_id": actor.organization_id,
                        "resource_ref": resource_ref,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise UiApiUnavailable
        return _article_document(row)

    def preview_article_policy(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        resource_ref: str,
        policy_kind: str,
        group_refs: tuple[str, ...],
    ) -> dict[str, object]:
        self._consume_control(
            actor, control_call, ControlOperation.CHANGE_ARTICLE_POLICY
        )
        if policy_kind not in {"private", "organization", "groups"}:
            raise UiApiUnavailable
        normalized = tuple(sorted(set(group_refs)))
        if (
            (policy_kind == "groups") != bool(normalized)
            or len(normalized) > 100
            or any(
                type(value) is not str
                or not value
                or len(value) > 256
                or any(character.isspace() for character in value)
                for value in normalized
            )
        ):
            raise UiApiUnavailable
        current = self._article(actor, resource_ref=resource_ref)
        payload = {
            "expectedPolicyEpoch": current["policyEpoch"],
            "expectedPolicyVersion": current["policyVersion"],
            "groupRefs": list(normalized),
            "policyKind": policy_kind,
            "resourceRef": resource_ref,
        }
        return {
            "current": current,
            "proposed": {"groupRefs": list(normalized), "policyKind": policy_kind},
            "previewToken": self._preview_codec.issue(
                "article_policy",
                actor,
                payload,
            ),
        }

    def confirm_article_policy(
        self,
        actor: UiActor,
        control_call: TrustedControlCall,
        *,
        preview_token: str,
    ) -> dict[str, object]:
        self._consume_control(
            actor, control_call, ControlOperation.CHANGE_ARTICLE_POLICY
        )
        payload = self._preview_codec.verify(
            preview_token,
            kind="article_policy",
            actor=actor,
        )
        try:
            resource_ref = cast(str, payload["resourceRef"])
            policy_kind = cast(str, payload["policyKind"])
            group_refs = cast(list[str], payload["groupRefs"])
            expected_version = cast(int, payload["expectedPolicyVersion"])
            expected_epoch = cast(int, payload["expectedPolicyEpoch"])
            if (
                policy_kind not in {"private", "organization", "groups"}
                or type(expected_version) is not int
                or not 1 <= expected_version <= _MAX_SIGNED_BIGINT
                or type(expected_epoch) is not int
                or not 1 <= expected_epoch <= _MAX_SIGNED_BIGINT
                or type(group_refs) is not list
                or any(type(value) is not str for value in group_refs)
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise UiApiUnavailable from None
        preview_digest = hashlib.sha256(preview_token.encode("ascii")).hexdigest()
        # Do not retain Runtime's shared publication fence while Control
        # advances the Policy Epoch. The definer locks and rechecks this exact
        # current Membership before it mutates the Article policy.
        with self._verified(actor):
            pass
        with self._control(actor) as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT * FROM context_control_change_article_policy(
                        :organization_id, :resource_ref,
                        :expected_policy_version, :expected_policy_epoch,
                        :policy_kind, CAST(:group_refs AS text[]),
                        :preview_digest, :user_id, :membership_id,
                        :membership_version
                    )
                    """
                    ),
                    {
                        "organization_id": actor.organization_id,
                        "resource_ref": resource_ref,
                        "expected_policy_version": expected_version,
                        "expected_policy_epoch": expected_epoch,
                        "policy_kind": policy_kind,
                        "group_refs": group_refs,
                        "preview_digest": preview_digest,
                        "user_id": actor.user_id,
                        "membership_id": actor.membership_id,
                        "membership_version": actor.membership_version,
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise UiApiUnavailable
        return {
            "policyEpoch": row["policy_epoch"],
            "policyVersion": row["policy_version"],
            "state": "changed",
        }

    def capture_feedback(
        self,
        actor: UiActor,
        feedback: FeedbackCapture,
    ) -> dict[str, object]:
        feedback.__post_init__()
        entropy = uuid4().bytes
        feedback_ref = (
            "fb_"
            + hashlib.sha256(
                _FEEDBACK_DOMAIN + actor.organization_id.bytes + entropy
            ).hexdigest()
        )
        with self._verified(actor), self._feedback(actor) as connection:
            recorded = connection.execute(
                text(
                    """
                    SELECT context_runtime_capture_context_feedback(
                        :organization_id, :feedback_ref, :run_ref,
                        :user_id, :membership_id, :membership_version,
                        :principal_ref, :rating, :note
                    )
                    """
                ),
                {
                    "organization_id": actor.organization_id,
                    "feedback_ref": feedback_ref,
                    "run_ref": feedback.run_ref,
                    "user_id": actor.user_id,
                    "membership_id": actor.membership_id,
                    "membership_version": actor.membership_version,
                    "principal_ref": actor.principal_ref,
                    "rating": feedback.rating,
                    "note": feedback.note,
                },
            ).scalar_one_or_none()
        if type(recorded) is not str:
            raise UiApiUnavailable
        return {"feedbackRef": recorded, "state": "recorded"}


def _actor_document(actor: UiActor) -> dict[str, object]:
    return {
        "membershipId": str(actor.membership_id),
        "membershipVersion": actor.membership_version,
        "organizationId": str(actor.organization_id),
        "principalRef": actor.principal_ref,
        "userId": str(actor.user_id),
    }


def _article_document(row: Any) -> dict[str, object]:
    return {
        "effectiveGroupRefs": list(row["group_refs"]),
        "effectivePolicyKind": row["policy_kind"],
        "localGroupRefs": list(row["local_group_refs"]),
        "localPolicyKind": row["local_policy_kind"],
        "policyEpoch": row["policy_epoch"],
        "policyVersion": row["policy_version"],
        "published": row["published"],
        "resolutionRung": row["resolution_rung"],
        "resourceRef": row["resource_ref"],
        "sourceRef": row["source_ref"],
    }


def _utc(value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise UiApiUnavailable
    return value


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> str:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise ValueError from None


def ui_actor(authentication: VerifiedAuthenticationContext) -> UiActor:
    try:
        return UiActor(
            organization_id=UUID(authentication.organization_ref),
            user_id=UUID(authentication.user_ref),
            membership_id=UUID(authentication.membership_ref),
            membership_version=authentication.membership_version,
            principal_ref=authentication.principal_ref,
            authentication_binding_ref=authentication.authentication_binding_ref,
        )
    except (TypeError, ValueError):
        raise UiApiUnavailable from None
