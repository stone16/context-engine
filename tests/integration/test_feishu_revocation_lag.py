from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from adapters.connectors.feishu import (
    FeishuAclResponse,
    FeishuAclVisibility,
    FeishuGroupNode,
    FeishuGroupSnapshot,
    FeishuIdentityMapping,
    FeishuPermissionKind,
    FeishuPermissionSubject,
    FeishuSourceError,
)
from adapters.embeddings import DeterministicEmbeddingTwin
from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.runtime.content_io import exact_phrase_digest
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_runtime_authorized_evidence_integration import (
    RECEIVED_AT,
    HostileCandidateIndex,
    _new_fixture,
)
from tests.integration.test_runtime_policy_epoch_integration import (
    _assert_revoked_empty,
    _client,
    _resolve,
)
from tests.support.feishu_integration import (
    AcceptedFeishuPage,
    accept_feishu_observation_sequence,
    accept_feishu_page,
    apply_feishu_page,
    cleanup_feishu_scenario,
    seed_feishu_article,
)
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration


def test_feishu_observed_revoke_refuses_next_resolve_without_index_rebuild(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    fixture = _new_fixture()
    base = fixture.org_a.authorized
    source_id = uuid4()
    candidate = replace(
        base,
        source_ref=str(source_id),
        resource_ref="document:revocation-lag",
    )
    fixture = replace(
        fixture,
        org_a=replace(fixture.org_a, authorized=candidate),
    )
    observed_at = RECEIVED_AT - timedelta(minutes=2)
    granted, revoked_page = accept_feishu_observation_sequence(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        document_ref=candidate.resource_ref,
        observations=(
            FeishuAclResponse(
                candidate.resource_ref,
                FeishuAclVisibility.PRIVATE,
                (
                    FeishuPermissionSubject(
                        FeishuPermissionKind.USER,
                        "identity:reader",
                    ),
                ),
                observed_at,
            ),
            FeishuAclResponse(
                candidate.resource_ref,
                FeishuAclVisibility.PRIVATE,
                (),
                observed_at + timedelta(minutes=1),
            ),
        ),
        identity_mappings={
            "identity:reader": FeishuIdentityMapping(
                "identity:reader",
                "principal:authorized-evidence:org-a",
            )
        },
    )
    assert granted.scenario.source_id != source_id
    source_id = granted.scenario.source_id
    candidate = replace(
        candidate,
        organization_id=granted.scenario.organization_id,
        source_ref=str(source_id),
    )
    fixture = replace(
        fixture,
        org_a=replace(
            fixture.org_a,
            organization_id=granted.scenario.organization_id,
            authorized=candidate,
            denied=replace(
                fixture.org_a.denied,
                organization_id=granted.scenario.organization_id,
            ),
        ),
    )
    migration_engine = create_database_engine(migration_configuration)
    index = HostileCandidateIndex(
        fixture.org_a,
        cross_organization=fixture.org_b.authorized,
    )
    client = _client(
        active=fixture.org_a,
        guarded_runtime_engine=guarded_runtime_engine,
        index=index,
        query_digest_keyring=query_digest_keyring,
    )
    try:
        _seed_runtime_article(migration_engine, fixture, granted)
        ensure_test_runtime_release(fixture.org_a.organization_id)
        applied = apply_feishu_page(guarded_control_engine, granted)
        assert applied.published is True
        before_resolve = _resolve(client)
        assert before_resolve.status_code == 200
        before_package = before_resolve.json()["package"]
        assert before_package["coverage"] == {"status": "sufficient"}
        assert before_package["blocks"][0]["text"] == fixture.org_a.authorized_body
        assert before_package["evidence"][0]["policyEpoch"] == 2
        with migration_engine.connect() as connection:
            before = connection.execute(
                text(
                    "SELECT resource_ref, revision_id, fragment_ref, phrase_digest "
                    "FROM exact_phrase_candidate WHERE organization_id = :org"
                ),
                {"org": fixture.org_a.organization_id},
            ).all()

        revoked = apply_feishu_page(guarded_control_engine, revoked_page)
        assert revoked.published is True
        _assert_revoked_empty(_resolve(client), fixture.org_a)
        with migration_engine.connect() as connection:
            after = connection.execute(
                text(
                    "SELECT resource_ref, revision_id, fragment_ref, phrase_digest "
                    "FROM exact_phrase_candidate WHERE organization_id = :org"
                ),
                {"org": fixture.org_a.organization_id},
            ).all()
        assert after == before
        assert len(index.calls) == 2
    finally:
        migration_engine.dispose()
        clear_test_runtime_release(fixture.org_a.organization_id)
        cleanup_feishu_scenario(
            migration_configuration,
            granted.scenario.organization_id,
        )


def test_feishu_mirrored_grant_expires_during_outage_without_index_rebuild(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    fixture = _new_fixture()
    base = fixture.org_a.authorized
    candidate = replace(
        base,
        resource_ref="document:stale-mirror-outage",
    )
    fixture = replace(
        fixture,
        org_a=replace(fixture.org_a, authorized=candidate),
    )
    observed_at = RECEIVED_AT - timedelta(minutes=2)
    accepted = accept_feishu_page(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        observed_at=observed_at,
        document_ref=candidate.resource_ref,
        visibility=FeishuAclVisibility.PRIVATE,
        subjects=(
            FeishuPermissionSubject(
                FeishuPermissionKind.USER,
                "identity:reader",
            ),
        ),
        identity_mappings={
            "identity:reader": FeishuIdentityMapping(
                "identity:reader",
                "principal:authorized-evidence:org-a",
            )
        },
    )
    candidate = replace(
        candidate,
        organization_id=accepted.scenario.organization_id,
        source_ref=str(accepted.scenario.source_id),
    )
    fixture = replace(
        fixture,
        org_a=replace(
            fixture.org_a,
            organization_id=accepted.scenario.organization_id,
            authorized=candidate,
            denied=replace(
                fixture.org_a.denied,
                organization_id=accepted.scenario.organization_id,
            ),
        ),
    )
    migration_engine = create_database_engine(migration_configuration)
    index = HostileCandidateIndex(
        fixture.org_a,
        cross_organization=fixture.org_b.authorized,
    )
    fresh_client = _client(
        active=fixture.org_a,
        guarded_runtime_engine=guarded_runtime_engine,
        index=index,
        query_digest_keyring=query_digest_keyring,
    )
    stale_client = _client(
        active=fixture.org_a,
        guarded_runtime_engine=guarded_runtime_engine,
        index=index,
        query_digest_keyring=query_digest_keyring,
        now=RECEIVED_AT + timedelta(minutes=4),
    )
    try:
        _seed_runtime_article(migration_engine, fixture, accepted)
        ensure_test_runtime_release(fixture.org_a.organization_id)
        assert apply_feishu_page(guarded_control_engine, accepted).published is True
        fresh_response = _resolve(fresh_client)
        assert fresh_response.status_code == 200
        assert fresh_response.json()["package"]["coverage"] == {
            "status": "sufficient"
        }
        assert fresh_response.json()["package"]["evidence"][0][
            "sourceAclEvidence"
        ]["freshnessProfileRef"] == "feishu-docs-mirrored-five-minute-v1"
        with migration_engine.connect() as connection:
            before = connection.execute(
                text(
                    "SELECT resource_ref, revision_id, fragment_ref, phrase_digest "
                    "FROM exact_phrase_candidate WHERE organization_id = :org"
                ),
                {"org": fixture.org_a.organization_id},
            ).all()

        _assert_revoked_empty(_resolve(stale_client), fixture.org_a)
        with migration_engine.connect() as connection:
            after = connection.execute(
                text(
                    "SELECT resource_ref, revision_id, fragment_ref, phrase_digest "
                    "FROM exact_phrase_candidate WHERE organization_id = :org"
                ),
                {"org": fixture.org_a.organization_id},
            ).all()
            policy = connection.execute(
                text(
                    "SELECT source_observation_status, published "
                    "FROM article_access_policy WHERE organization_id = :org "
                    "AND resource_ref = :resource"
                ),
                {
                    "org": fixture.org_a.organization_id,
                    "resource": candidate.resource_ref,
                },
            ).one()
        assert after == before
        assert tuple(policy) == ("resolved", True)
        assert len(index.calls) == 2
    finally:
        migration_engine.dispose()
        clear_test_runtime_release(fixture.org_a.organization_id)
        cleanup_feishu_scenario(
            migration_configuration,
            accepted.scenario.organization_id,
        )


@pytest.mark.parametrize("failure_surface", ["identity", "group"])
def test_feishu_acl_dependency_outage_isolates_previous_grant(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    failure_surface: str,
) -> None:
    document_ref = f"document:{failure_surface}-outage"
    external_ref = f"{failure_surface}:reader"
    subject = FeishuPermissionSubject(
        (
            FeishuPermissionKind.USER
            if failure_surface == "identity"
            else FeishuPermissionKind.GROUP
        ),
        external_ref,
    )
    observed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    identity_mapping = FeishuIdentityMapping(
        external_ref,
        "principal:reader",
    )
    group_snapshot = FeishuGroupSnapshot(
        "groups:outage-v1",
        (
            FeishuGroupNode(
                external_ref,
                "local-group:reader",
            ),
        )
        if failure_surface == "group"
        else (),
        observed_at,
    )
    granted, failed = accept_feishu_observation_sequence(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        document_ref=document_ref,
        observations=(
            FeishuAclResponse(
                document_ref,
                FeishuAclVisibility.PRIVATE,
                (subject,),
                observed_at,
            ),
            FeishuAclResponse(
                document_ref,
                FeishuAclVisibility.PRIVATE,
                (subject,),
                observed_at + timedelta(minutes=1),
            ),
        ),
        identity_mappings=(
            {external_ref: identity_mapping}
            if failure_surface == "identity"
            else {}
        ),
        identity_sequences=(
            {
                external_ref: (
                    identity_mapping,
                    FeishuSourceError("synthetic identity outage"),
                )
            }
            if failure_surface == "identity"
            else None
        ),
        group_snapshot=group_snapshot,
        group_snapshot_sequence=(
            (
                group_snapshot,
                FeishuSourceError("synthetic group outage"),
            )
            if failure_surface == "group"
            else None
        ),
    )
    engine = create_database_engine(migration_configuration)
    try:
        seed_feishu_article(migration_configuration, granted)
        assert apply_feishu_page(guarded_control_engine, granted).published is True
        isolated = apply_feishu_page(guarded_control_engine, failed)
        with engine.connect() as connection:
            policy = connection.execute(
                text(
                    "SELECT source_observation_status, published, policy_kind "
                    "FROM article_access_policy WHERE organization_id = :org "
                    "AND resource_ref = :resource"
                ),
                {
                    "org": granted.scenario.organization_id,
                    "resource": document_ref,
                },
            ).one()
            retained_grants = connection.execute(
                text(
                    "SELECT count(*) FROM resource_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource "
                    "AND access_state = 'allowed'"
                ),
                {
                    "org": granted.scenario.organization_id,
                    "resource": document_ref,
                },
            ).scalar_one()
        assert isolated.published is False
        assert tuple(policy) == ("failed", False, None)
        assert retained_grants == 0
    finally:
        engine.dispose()
        cleanup_feishu_scenario(
            migration_configuration,
            granted.scenario.organization_id,
        )


def test_out_of_order_feishu_observation_is_committed_as_isolation(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    observed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    granted, stale = accept_feishu_observation_sequence(
        migration_configuration=migration_configuration,
        guarded_control_engine=guarded_control_engine,
        guarded_worker_engine=guarded_worker_engine,
        document_ref="document:stale-observation",
        observations=(
            FeishuAclResponse(
                "document:stale-observation",
                FeishuAclVisibility.PRIVATE,
                (
                    FeishuPermissionSubject(
                        FeishuPermissionKind.USER,
                        "identity:reader",
                    ),
                ),
                observed_at,
            ),
            FeishuAclResponse(
                "document:stale-observation",
                FeishuAclVisibility.PRIVATE,
                (),
                observed_at - timedelta(minutes=1),
            ),
        ),
        identity_mappings={
            "identity:reader": FeishuIdentityMapping(
                "identity:reader",
                "principal:reader",
            )
        },
    )
    engine = create_database_engine(migration_configuration)
    try:
        seed_feishu_article(migration_configuration, granted)
        assert apply_feishu_page(guarded_control_engine, granted).published is True
        isolated = apply_feishu_page(guarded_control_engine, stale)
        with engine.connect() as connection:
            state = connection.execute(
                text(
                    "SELECT source_observation_status, published, policy_kind, "
                    "source_policy_epoch FROM article_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource"
                ),
                {
                    "org": granted.scenario.organization_id,
                    "resource": granted.document_ref,
                },
            ).one()
        assert isolated.published is False
        assert tuple(state) == ("failed", False, None, 1)
    finally:
        engine.dispose()
        cleanup_feishu_scenario(
            migration_configuration,
            granted.scenario.organization_id,
        )


def _seed_runtime_article(
    engine: Engine,
    fixture: object,
    accepted: AcceptedFeishuPage,
) -> None:
    org = fixture.org_a  # type: ignore[attr-defined]
    candidate = org.authorized
    provider = DeterministicEmbeddingTwin()
    embedding = provider.embed_documents((org.authorized_body,))[0]
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO user_account (user_id) VALUES (:user)"),
            {"user": org.user_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO membership (
                    organization_id, membership_id, user_id, status,
                    membership_version, valid_from, valid_until
                ) VALUES (:org, :membership, :user, 'active', 1,
                          :valid_from, NULL)
                """
            ),
            {
                "org": org.organization_id,
                "membership": org.membership_id,
                "user": org.user_id,
                "valid_from": RECEIVED_AT - timedelta(days=1),
            },
        )
        revision_id = UUID(candidate.revision_ref)
        connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        connection.execute(
            text(
                """
                INSERT INTO context_resource (
                    organization_id, resource_ref, source_ref,
                    active_revision_id, tombstoned
                ) VALUES (:org, :resource, :source, :revision, false)
                """
            ),
            {
                "org": org.organization_id,
                "resource": candidate.resource_ref,
                "source": candidate.source_ref,
                "revision": revision_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO context_revision "
                "(organization_id, resource_ref, revision_id) "
                "VALUES (:org, :resource, :revision)"
            ),
            {
                "org": org.organization_id,
                "resource": candidate.resource_ref,
                "revision": revision_id,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO context_fragment (
                    organization_id, resource_ref, revision_id,
                    fragment_ref, ordinal, content, embedding,
                    embedding_profile_digest
                ) VALUES (
                    :org, :resource, :revision, :fragment, 0, :content,
                    CAST(:embedding AS vector), :embedding_profile_digest
                )
                """
            ),
            {
                "org": org.organization_id,
                "resource": candidate.resource_ref,
                "revision": revision_id,
                "fragment": candidate.fragment_ref,
                "content": org.authorized_body,
                "embedding": "[" + ",".join(repr(item) for item in embedding) + "]",
                "embedding_profile_digest": provider.provider_profile.profile_digest,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO exact_phrase_candidate (
                    organization_id, phrase_digest, source_ref, resource_ref,
                    revision_id, fragment_ref
                ) VALUES (:org, :phrase_digest, :source, :resource,
                          :revision, :fragment)
                """
            ),
            {
                "org": org.organization_id,
                "phrase_digest": exact_phrase_digest(
                    "same policy epoch revocation probe"
                ),
                "source": candidate.source_ref,
                "resource": candidate.resource_ref,
                "revision": revision_id,
                "fragment": candidate.fragment_ref,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO membership_resource_field_right (
                    organization_id, membership_id, membership_version,
                    resource_ref, field_ref
                ) VALUES (:org, :membership, 1, :resource, 'body')
                """
            ),
            {
                "org": org.organization_id,
                "membership": org.membership_id,
                "resource": candidate.resource_ref,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO article_access_policy (
                    organization_id, resource_ref, policy_version,
                    local_policy_kind, local_group_refs, policy_kind,
                    group_refs, published, resolution_rung,
                    source_evidence_mode, source_observation_status,
                    source_observation_version, source_version_ref,
                    source_acl_as_of, source_declared_lag_seconds,
                    fixed_at_policy_epoch
                ) VALUES (:org, :resource, 1, 'organization', ARRAY[]::text[],
                          NULL, ARRAY[]::text[], false, 'source_default',
                          'mirrored', 'missing', NULL, :version,
                          statement_timestamp(), 0, 1)
                """
            ),
            {
                "org": org.organization_id,
                "resource": candidate.resource_ref,
                "version": accepted.scenario.source_version_id,
            },
        )
