"""Explicit test-only Runtime release lineage and real Learning promotion."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid5

from sqlalchemy import Connection, text

from adapters.embeddings import DeterministicEmbeddingTwin
from engine.learning import (
    ContentProfileRef,
    ContextLearning,
    CurationProfileRef,
    Gate,
    GateEvidence,
    GateStatus,
    IndexProfileRef,
    PromotionAuthorizationRequest,
    ReleaseCandidate,
    ReleaseEvaluationKeyring,
    ReleaseManifest,
    ReleaseOperatorAuthenticationRejected,
    ReleaseOperatorAuthority,
    RuntimeProfileRef,
    VerifiedReleaseOperatorIdentity,
    release_authority_digest,
)
from engine.persistence import (
    assert_learning_role,
    create_database_engine,
    load_harness_database_configurations,
)
from engine.persistence.releases import PostgreSQLReleaseStore
from engine.runtime.release_lineage import (
    CONTENT_PROFILE_DIGEST_V0,
    CONTENT_PROFILE_REF_V0,
    CONTENT_SCHEMA_REF_V0,
    CURATION_PROFILE_DIGEST_V0,
    CURATION_PROFILE_REF_V0,
    INDEX_PROFILE_DIGEST_V0,
    INDEX_PROFILE_REF_V0,
    INDEX_SCHEMA_REF_V0,
    PACKAGE_SCHEMA_REF_V0,
    QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
    QWEN_VECTOR_INDEX_PROFILE_REF_V1,
    RUNTIME_PROFILE_DIGEST_V0,
    RUNTIME_PROFILE_REF_V0,
    RUNTIME_TOKENIZER_REF_V0,
    ActiveRuntimeRelease,
)
from engine.supply import (
    DETERMINISTIC_TWIN_EMBEDDING_PROFILE,
    QWEN3_EMBEDDING_PROFILE,
    EmbeddingProviderProfile,
)
from tests.support.embeddings import QwenEmbeddingTwin

_SIGNING_KEY = b"openapi-v0-test-release-evaluation-key"
_SIGNING_KEY_VERSION = 66
_SENTINEL_NAMESPACE = UUID("41f3a801-50f8-4ed1-8964-261ebbad325e")
_SENTINEL_SOURCE_REF = "source:test-release-sentinel"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _sentinel_revision_id(
    organization_id: UUID,
    profile: EmbeddingProviderProfile,
) -> UUID:
    return uuid5(
        _SENTINEL_NAMESPACE,
        f"{organization_id}:{profile.profile_digest}",
    )


def _ensure_test_release_sentinel(
    connection: Connection,
    organization_id: UUID,
    profile: EmbeddingProviderProfile,
) -> str:
    """Seed one inaccessible test Revision so database promotion is non-vacuous."""

    revision_id = _sentinel_revision_id(organization_id, profile)
    resource_ref = f"resource:test-release-sentinel:{revision_id}"
    embedding_provider = (
        QwenEmbeddingTwin()
        if profile == QWEN3_EMBEDDING_PROFILE
        else DeterministicEmbeddingTwin()
    )
    embedding = embedding_provider.embed(("test release sentinel",))[0]
    parameters = {
        "organization_id": organization_id,
        "resource_ref": resource_ref,
        "revision_id": revision_id,
        "embedding": "[" + ",".join(repr(value) for value in embedding) + "]",
        "embedding_profile_digest": profile.profile_digest,
    }
    connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    connection.execute(
        text(
            """
            INSERT INTO context_resource (
                organization_id, resource_ref, source_ref,
                active_revision_id, tombstoned
            ) VALUES (
                :organization_id, :resource_ref, :source_ref,
                :revision_id, false
            ) ON CONFLICT (organization_id, resource_ref) DO NOTHING
            """
        ),
        {**parameters, "source_ref": _SENTINEL_SOURCE_REF},
    )
    connection.execute(
        text(
            """
            INSERT INTO context_revision (
                organization_id, resource_ref, revision_id
            ) VALUES (
                :organization_id, :resource_ref, :revision_id
            ) ON CONFLICT (organization_id, resource_ref, revision_id) DO NOTHING
            """
        ),
        parameters,
    )
    connection.execute(
        text(
            """
            INSERT INTO context_fragment (
                organization_id, resource_ref, revision_id, fragment_ref,
                ordinal, content, projection_kind, embedding,
                embedding_profile_digest
            ) VALUES (
                :organization_id, :resource_ref, :revision_id,
                'fragment:test-release-sentinel', 0,
                'test release sentinel', 'body', CAST(:embedding AS vector),
                :embedding_profile_digest
            ) ON CONFLICT (
                organization_id, resource_ref, revision_id, fragment_ref
            ) DO NOTHING
            """
        ),
        parameters,
    )
    connection.commit()
    return str(revision_id)


class _ExactReleaseAuthenticator:
    def __init__(
        self,
        credential: str,
        identity: VerifiedReleaseOperatorIdentity,
    ) -> None:
        self._credential = credential
        self._identity = identity

    def authenticate(self, opaque_credential: str) -> VerifiedReleaseOperatorIdentity:
        if opaque_credential != self._credential:
            raise ReleaseOperatorAuthenticationRejected
        return self._identity


def active_runtime_release(
    organization_id: UUID,
    *,
    suffix: str = "test-v0",
    active_revision_refs: tuple[str, ...] = (),
    index_profile_ref: str = INDEX_PROFILE_REF_V0,
    index_profile_digest: str = INDEX_PROFILE_DIGEST_V0,
    embedding_provider_profile: EmbeddingProviderProfile = (
        DETERMINISTIC_TWIN_EMBEDDING_PROFILE
    ),
) -> ActiveRuntimeRelease:
    return ActiveRuntimeRelease(
        organization_id=organization_id,
        manifest_digest=_digest(f"manifest-{suffix}"),
        active_generation=1,
        content_profile_ref=CONTENT_PROFILE_REF_V0,
        content_schema_ref=CONTENT_SCHEMA_REF_V0,
        index_profile_ref=index_profile_ref,
        index_schema_ref=INDEX_SCHEMA_REF_V0,
        runtime_profile_ref=RUNTIME_PROFILE_REF_V0,
        runtime_profile_digest=RUNTIME_PROFILE_DIGEST_V0,
        content_profile_digest=CONTENT_PROFILE_DIGEST_V0,
        index_profile_digest=index_profile_digest,
        embedding_profile_document=embedding_provider_profile.canonical_json(),
        embedding_profile_digest=embedding_provider_profile.profile_digest,
        tokenizer_ref=RUNTIME_TOKENIZER_REF_V0,
        package_schema_ref=PACKAGE_SCHEMA_REF_V0,
        curation_profile_ref=CURATION_PROFILE_REF_V0,
        curation_profile_digest=CURATION_PROFILE_DIGEST_V0,
        curation_mode="curation_off",
        curation_snapshot_ref=None,
        curation_evaluation_digest=None,
        compatible_revision_refs=(),
        active_revision_refs=active_revision_refs,
    )


def ensure_test_runtime_release(
    organization_id: UUID,
    *,
    active_revision_refs: tuple[str, ...] | None = None,
    runtime_profile_ref: str = RUNTIME_PROFILE_REF_V0,
    index_profile_ref: str = INDEX_PROFILE_REF_V0,
    index_profile_digest: str = INDEX_PROFILE_DIGEST_V0,
    tokenizer_ref: str = RUNTIME_TOKENIZER_REF_V0,
    package_schema_ref: str = PACKAGE_SCHEMA_REF_V0,
    embedding_provider_profile: EmbeddingProviderProfile | None = None,
) -> ActiveRuntimeRelease:
    """Promote the test profile through ContextLearning when no pointer exists.

    This helper is intentionally integration-only: it provisions the current
    test operator grant, then uses the same signed evaluation and sole
    ContextLearning promotion owner as production. It never writes the active
    pointer directly and never creates a production fallback.
    """

    if embedding_provider_profile is None:
        embedding_provider_profile = (
            QWEN3_EMBEDDING_PROFILE
            if (
                index_profile_ref,
                index_profile_digest,
            )
            == (
                QWEN_VECTOR_INDEX_PROFILE_REF_V1,
                QWEN_VECTOR_INDEX_PROFILE_DIGEST_V1,
            )
            else DETERMINISTIC_TWIN_EMBEDDING_PROFILE
        )
    revisions_were_explicit = active_revision_refs is not None
    configurations = load_harness_database_configurations()
    migration_engine = create_database_engine(configurations.migration)
    learning_engine = create_database_engine(configurations.learning)
    suffix = f"openapi-v0-{organization_id.hex}"
    advisory_lock_scope = f"context-engine.test-runtime-release:{organization_id}"
    lock_connection = migration_engine.connect()
    try:
        lock_connection.execute(
            text(
                "SELECT pg_catalog.pg_advisory_lock("
                "pg_catalog.hashtextextended(:scope, 0))"
            ),
            {"scope": advisory_lock_scope},
        )
        lock_connection.commit()
        if active_revision_refs is None:
            active_revision_refs = tuple(
                str(revision_id)
                for revision_id in lock_connection.execute(
                    text(
                        """
                        SELECT active_revision_id
                        FROM context_resource
                        WHERE organization_id = :organization_id
                          AND active_revision_id IS NOT NULL
                          AND tombstoned IS FALSE
                          AND source_ref <> :sentinel_source_ref
                        ORDER BY active_revision_id
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "sentinel_source_ref": _SENTINEL_SOURCE_REF,
                    },
                ).scalars()
            )
            lock_connection.commit()
        existing = lock_connection.execute(
            text(
                """
                SELECT active.active_generation,
                       manifest.manifest_ref,
                       manifest.manifest_digest,
                       manifest.content_profile_ref,
                       manifest.content_schema_ref,
                       manifest.index_profile_ref,
                       manifest.embedding_profile_document,
                       manifest.embedding_profile_digest,
                       manifest.index_schema_ref,
                       manifest.runtime_profile_ref,
                       manifest.runtime_profile_digest,
                       manifest.runtime_content_profile_digest,
                       manifest.runtime_index_profile_digest,
                       manifest.runtime_tokenizer_ref,
                       manifest.runtime_package_schema_ref,
                       manifest.curation_profile_ref,
                       manifest.curation_profile_digest,
                       manifest.curation_mode,
                       manifest.curation_snapshot_ref,
                       manifest.curation_evaluation_digest,
                       manifest.compatible_revision_refs,
                       manifest.active_revision_refs
                FROM active_release_manifest AS active
                JOIN release_manifest AS manifest
                  ON manifest.organization_id = active.organization_id
                 AND manifest.manifest_ref = active.manifest_ref
                 AND manifest.manifest_digest = active.manifest_digest
                WHERE active.organization_id = :organization_id
                """
            ),
            {"organization_id": organization_id},
        ).one_or_none()
        lock_connection.commit()
        if existing is not None:
            selected_revisions = tuple(existing.active_revision_refs)
            if (
                (
                    selected_revisions == tuple(sorted(active_revision_refs))
                    or (
                        not revisions_were_explicit
                        and not active_revision_refs
                        and selected_revisions
                        == (
                            str(
                                _sentinel_revision_id(
                                    organization_id,
                                    embedding_provider_profile,
                                )
                            ),
                        )
                    )
                )
                and existing.index_profile_ref == index_profile_ref
                and existing.runtime_index_profile_digest == index_profile_digest
                and existing.embedding_profile_digest
                == embedding_provider_profile.profile_digest
            ):
                try:
                    return ActiveRuntimeRelease(
                        organization_id=organization_id,
                        manifest_digest=existing.manifest_digest,
                        active_generation=existing.active_generation,
                        content_profile_ref=existing.content_profile_ref,
                        content_schema_ref=existing.content_schema_ref,
                        index_profile_ref=existing.index_profile_ref,
                        index_schema_ref=existing.index_schema_ref,
                        runtime_profile_ref=existing.runtime_profile_ref,
                        runtime_profile_digest=existing.runtime_profile_digest,
                        content_profile_digest=(
                            existing.runtime_content_profile_digest
                        ),
                        index_profile_digest=existing.runtime_index_profile_digest,
                        embedding_profile_document=json.dumps(
                            existing.embedding_profile_document,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        embedding_profile_digest=existing.embedding_profile_digest,
                        tokenizer_ref=existing.runtime_tokenizer_ref,
                        package_schema_ref=existing.runtime_package_schema_ref,
                        curation_profile_ref=existing.curation_profile_ref,
                        curation_profile_digest=existing.curation_profile_digest,
                        curation_mode=existing.curation_mode,
                        curation_snapshot_ref=existing.curation_snapshot_ref,
                        curation_evaluation_digest=(
                            existing.curation_evaluation_digest
                        ),
                        compatible_revision_refs=tuple(
                            existing.compatible_revision_refs
                        ),
                        active_revision_refs=selected_revisions,
                    )
                except (TypeError, ValueError):
                    pass
            clear_test_runtime_release(organization_id)

        if not active_revision_refs and not revisions_were_explicit:
            active_revision_refs = (
                _ensure_test_release_sentinel(
                    lock_connection,
                    organization_id,
                    embedding_provider_profile,
                ),
            )

        now = datetime.now(UTC).replace(microsecond=0)
        operator_ref = f"operator-{suffix}"
        authentication_binding_ref = f"authentication-{suffix}"
        authority_ref = f"authority-{suffix}"
        credential = f"credential-{suffix}"
        identity = VerifiedReleaseOperatorIdentity(
            organization_id=organization_id,
            operator_ref=operator_ref,
            authentication_binding_ref=authentication_binding_ref,
            authority_ref=authority_ref,
            authority_digest=release_authority_digest(
                organization_id=organization_id,
                operator_ref=operator_ref,
                authentication_binding_ref=authentication_binding_ref,
                authority_ref=authority_ref,
            ),
            valid_from=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=1),
        )
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO release_operator_grant (
                        organization_id, authority_ref, authority_digest,
                        operator_ref, authentication_binding_ref,
                        valid_from, expires_at
                    ) VALUES (
                        :organization_id, :authority_ref, :authority_digest,
                        :operator_ref, :authentication_binding_ref,
                        :valid_from, :expires_at
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "authority_ref": identity.authority_ref,
                    "authority_digest": identity.authority_digest,
                    "operator_ref": identity.operator_ref,
                    "authentication_binding_ref": (identity.authentication_binding_ref),
                    "valid_from": identity.valid_from,
                    "expires_at": identity.expires_at,
                },
            )

        content = ContentProfileRef(
            profile_ref=CONTENT_PROFILE_REF_V0,
            profile_digest=CONTENT_PROFILE_DIGEST_V0,
            content_schema_ref=CONTENT_SCHEMA_REF_V0,
        )
        index = IndexProfileRef(
            profile_ref=index_profile_ref,
            profile_digest=index_profile_digest,
            content_profile_digest=content.profile_digest,
            content_schema_ref=content.content_schema_ref,
            index_schema_ref=INDEX_SCHEMA_REF_V0,
            embedding_profile_document=(
                embedding_provider_profile.canonical_json()
            ),
            embedding_profile_digest=embedding_provider_profile.profile_digest,
        )
        runtime = RuntimeProfileRef(
            profile_ref=runtime_profile_ref,
            profile_digest=RUNTIME_PROFILE_DIGEST_V0,
            content_profile_digest=content.profile_digest,
            index_profile_digest=index.profile_digest,
            content_schema_ref=content.content_schema_ref,
            index_schema_ref=index.index_schema_ref,
            tokenizer_ref=tokenizer_ref,
            package_schema_ref=package_schema_ref,
        )
        manifest = ReleaseManifest(
            organization_id=organization_id,
            manifest_ref=f"manifest-{suffix}",
            content_profile=content,
            index_profile=index,
            runtime_profile=runtime,
            curation_profile=CurationProfileRef.off(
                profile_ref=CURATION_PROFILE_REF_V0,
                profile_digest=CURATION_PROFILE_DIGEST_V0,
            ),
            active_revision_refs=tuple(sorted(active_revision_refs)),
        )
        candidate = ReleaseCandidate(
            organization_id=organization_id,
            candidate_ref=f"candidate-{suffix}",
            manifest=manifest,
            expected_active_generation=0,
            expected_base_manifest_digest=None,
            gate_evidence=tuple(
                GateEvidence(
                    gate=gate,
                    status=GateStatus.PASS,
                    evidence_digest=_digest(f"{gate.value}-gate-{suffix}"),
                )
                for gate in Gate
            ),
            capability_coverage_digest=_digest(f"capability-coverage-{suffix}"),
            fixture_digest=_digest(f"fixture-{suffix}"),
            verification_commands=("make check",),
        )
        with learning_engine.connect() as connection:
            assert_learning_role(connection)
        keyring = ReleaseEvaluationKeyring(
            active_version=_SIGNING_KEY_VERSION,
            keys={_SIGNING_KEY_VERSION: _SIGNING_KEY},
        )
        authority = ReleaseOperatorAuthority(
            _ExactReleaseAuthenticator(credential, identity),
            call_ttl=timedelta(minutes=5),
            clock=lambda: now,
        )
        store = PostgreSQLReleaseStore(learning_engine)
        learning = ContextLearning(
            store=store,
            evaluation_keyring=keyring,
            promotion_authority=authority,
            clock=lambda: now,
        )
        store.persist_candidate(candidate)
        evaluation = learning.evaluate(candidate.reference())
        request = PromotionAuthorizationRequest(
            organization_id=organization_id,
            promotion_ref=f"promotion-{suffix}",
            candidate=candidate,
            evaluation=evaluation,
            request_id=f"request-{suffix}",
            audit_reason="activate explicit OpenAPI v0 integration test profile",
            opaque_credential=credential,
        )
        with authority.authorize(request) as call:
            receipt = learning.promote(call)
        if receipt.manifest_ref != manifest.manifest_ref:
            raise AssertionError("test release promotion returned wrong manifest")
        return ActiveRuntimeRelease(
            organization_id=organization_id,
            manifest_digest=manifest.manifest_digest,
            active_generation=receipt.active_generation,
            content_profile_ref=content.profile_ref,
            content_schema_ref=content.content_schema_ref,
            index_profile_ref=index.profile_ref,
            index_schema_ref=index.index_schema_ref,
            runtime_profile_ref=runtime.profile_ref,
            runtime_profile_digest=runtime.profile_digest,
            content_profile_digest=runtime.content_profile_digest,
            index_profile_digest=runtime.index_profile_digest,
            embedding_profile_document=index.embedding_profile_document,
            embedding_profile_digest=index.embedding_profile_digest,
            tokenizer_ref=runtime.tokenizer_ref,
            package_schema_ref=runtime.package_schema_ref,
            curation_profile_ref=manifest.curation_profile.profile_ref,
            curation_profile_digest=manifest.curation_profile.profile_digest,
            curation_mode=manifest.curation_profile.mode.value,
            curation_snapshot_ref=manifest.curation_profile.curation_snapshot_ref,
            curation_evaluation_digest=manifest.curation_profile.evaluation_digest,
            compatible_revision_refs=(
                manifest.curation_profile.compatible_revision_refs
            ),
            active_revision_refs=manifest.active_revision_refs,
        )
    finally:
        lock_connection.execute(
            text(
                "SELECT pg_catalog.pg_advisory_unlock("
                "pg_catalog.hashtextextended(:scope, 0))"
            ),
            {"scope": advisory_lock_scope},
        )
        lock_connection.commit()
        lock_connection.close()
        learning_engine.dispose()
        migration_engine.dispose()


def clear_test_runtime_release(organization_id: UUID) -> None:
    """Remove only explicit integration-test release rows for fixture teardown."""

    configuration = load_harness_database_configurations().migration
    engine = create_database_engine(configuration)
    immutable_tables = (
        "release_promotion_audit",
        "release_evaluation",
        "release_candidate",
        "release_manifest",
        "context_fragment",
        "context_revision",
    )
    try:
        with engine.begin() as connection:
            for table_name in immutable_tables:
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} DISABLE TRIGGER "
                        f"{table_name}_reject_mutation"
                    )
                )
            connection.execute(
                text(
                    "DELETE FROM context_fragment "
                    "WHERE organization_id = :organization_id "
                    "AND resource_ref LIKE 'resource:test-release-sentinel:%'"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM context_revision "
                    "WHERE organization_id = :organization_id "
                    "AND resource_ref LIKE 'resource:test-release-sentinel:%'"
                ),
                {"organization_id": organization_id},
            )
            connection.execute(
                text(
                    "DELETE FROM context_resource "
                    "WHERE organization_id = :organization_id "
                    "AND source_ref = :source_ref"
                ),
                {
                    "organization_id": organization_id,
                    "source_ref": _SENTINEL_SOURCE_REF,
                },
            )
            for table_name in (
                "decision_audit",
                "context_run",
                "active_release_manifest",
                "release_promotion_audit",
                "release_evaluation",
                "release_candidate",
                "release_manifest",
                "release_operator_grant",
            ):
                connection.execute(
                    text(
                        f"DELETE FROM {table_name} "
                        "WHERE organization_id = :organization_id"
                    ),
                    {"organization_id": organization_id},
                )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            for table_name in reversed(immutable_tables):
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ENABLE TRIGGER "
                        f"{table_name}_reject_mutation"
                    )
                )
    finally:
        engine.dispose()


def clear_all_test_runtime_releases() -> None:
    """Clear every release created by this integration-only helper."""

    configuration = load_harness_database_configurations().migration
    engine = create_database_engine(configuration)
    try:
        with engine.connect() as connection:
            organization_ids = tuple(
                connection.execute(
                    text(
                        """
                        SELECT DISTINCT organization_id
                        FROM release_manifest
                        WHERE manifest_ref LIKE 'manifest-openapi-v0-%'
                        """
                    )
                ).scalars()
            )
    finally:
        engine.dispose()
    for organization_id in organization_ids:
        clear_test_runtime_release(organization_id)


__all__ = [
    "active_runtime_release",
    "clear_all_test_runtime_releases",
    "clear_test_runtime_release",
    "ensure_test_runtime_release",
]
