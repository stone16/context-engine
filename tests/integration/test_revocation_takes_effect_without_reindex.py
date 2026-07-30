from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from engine.persistence import DatabaseConfiguration, create_database_engine
from engine.persistence.access_policy import (
    PostgreSQLAccessPolicyControl,
    ResourceAccessRevocation,
)
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_runtime_authorized_evidence_integration import (
    HostileCandidateIndex,
    _cleanup_fixture,
    _new_fixture,
    _persistent_content_snapshot,
    _seed_fixture,
)
from tests.integration.test_runtime_policy_epoch_integration import (
    QUERY,
    _assert_authorized,
    _assert_revoked_empty,
    _client,
    _resolve,
)
from tests.integration.test_zz_file_revision_replacement import (
    _resolve as _resolve_file,
)
from tests.support.file_imports import (
    delete_file_import_scenario,
    prepare_file_import_scenario,
    run_file_import,
)
from tests.support.releases import (
    clear_test_runtime_release,
    ensure_test_runtime_release,
)

pytestmark = pytest.mark.integration


def test_next_http_resolve_refuses_after_revoke_without_index_rebuild(
    control_configuration: DatabaseConfiguration,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    control_engine = create_database_engine(control_configuration)
    candidate_index = HostileCandidateIndex(
        fixture.org_a,
        cross_organization=fixture.org_b.authorized,
    )
    client = _client(
        active=fixture.org_a,
        guarded_runtime_engine=guarded_runtime_engine,
        index=candidate_index,
        query_digest_keyring=query_digest_keyring,
    )
    try:
        _seed_fixture(migration_engine, fixture)
        ensure_test_runtime_release(fixture.org_a.organization_id)
        before = _persistent_content_snapshot(migration_engine, fixture)

        _assert_authorized(_resolve(client), fixture.org_a)
        epoch = PostgreSQLAccessPolicyControl(control_engine).change_access(
            ResourceAccessRevocation(
                organization_id=fixture.org_a.organization_id,
                resource_ref=fixture.org_a.authorized.resource_ref,
                principal_ref=(f"principal:authorized-evidence:{fixture.org_a.label}"),
                expected_access_version=1,
            )
        )
        assert epoch.value == 2

        _assert_revoked_empty(_resolve(client), fixture.org_a)
        assert len(candidate_index.calls) == 2
        assert all(call.need.query == QUERY for call in candidate_index.calls)
        assert _persistent_content_snapshot(migration_engine, fixture) == before
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            control_engine.dispose()
            migration_engine.dispose()


def test_source_acl_isolation_refuses_next_http_resolve_without_index_rebuild(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    published = run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    migration_engine = create_database_engine(migration_configuration)
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership "
                    "WHERE organization_id = :org AND membership_id = :membership"
                ),
                {
                    "org": scenario.organization_id,
                    "membership": scenario.membership_id,
                },
            ).scalar_one()
            candidate_snapshot = connection.execute(
                text(
                    "SELECT resource_ref, revision_id, fragment_ref, phrase_digest "
                    "FROM exact_phrase_candidate WHERE organization_id = :org "
                    "ORDER BY resource_ref, revision_id, fragment_ref, phrase_digest"
                ),
                {"org": scenario.organization_id},
            ).all()
        assert type(user_id) is UUID

        before = _resolve_file(
            scenario,
            guarded_runtime_engine,
            query_digest_keyring,
            user_id=user_id,
            query="ContextEngine delivers context.",
            request_id="file-source-acl-before",
        )
        assert before["blocks"]
        result = PostgreSQLAccessPolicyControl(guarded_control_engine).change_access(
            ResourceAccessRevocation(
                organization_id=scenario.organization_id,
                resource_ref=published.candidate_refs[0].resource_ref,
                principal_ref="principal:file-reader",
                expected_access_version=1,
            )
        )
        assert result.value == 2

        after = _resolve_file(
            scenario,
            guarded_runtime_engine,
            query_digest_keyring,
            user_id=user_id,
            query="ContextEngine delivers context.",
            request_id="file-source-acl-after",
        )
        assert after["blocks"] == []
        assert after["evidence"] == []
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT resource_ref, revision_id, fragment_ref, phrase_digest "
                        "FROM exact_phrase_candidate WHERE organization_id = :org "
                        "ORDER BY resource_ref, revision_id, fragment_ref, "
                        "phrase_digest"
                    ),
                    {"org": scenario.organization_id},
                ).all()
                == candidate_snapshot
            )
    finally:
        migration_engine.dispose()
        clear_test_runtime_release(scenario.organization_id)
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


def test_stale_file_source_version_acl_refuses_next_resolve_without_reindex(
    tmp_path: Path,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    migration_configuration: DatabaseConfiguration,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    scenario = prepare_file_import_scenario(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
    )
    assert scenario.token is not None
    run_file_import(
        scenario,
        scenario.prepared,
        scenario.token,
        guarded_worker_engine,
    )
    migration_engine = create_database_engine(migration_configuration)
    replacement_version_id = uuid4()
    try:
        with migration_engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership "
                    "WHERE organization_id = :org AND membership_id = :membership"
                ),
                {
                    "org": scenario.organization_id,
                    "membership": scenario.membership_id,
                },
            ).scalar_one()
            candidate_snapshot = connection.execute(
                text(
                    "SELECT resource_ref, revision_id, fragment_ref, phrase_digest "
                    "FROM exact_phrase_candidate WHERE organization_id = :org "
                    "ORDER BY resource_ref, revision_id, fragment_ref, phrase_digest"
                ),
                {"org": scenario.organization_id},
            ).all()
        assert type(user_id) is UUID

        before = _resolve_file(
            scenario,
            guarded_runtime_engine,
            query_digest_keyring,
            user_id=user_id,
            query="ContextEngine delivers context.",
            request_id="file-source-version-before",
        )
        assert before["blocks"]

        with migration_engine.begin() as connection:
            old_version_id = connection.execute(
                text(
                    "SELECT active_version_id FROM context_source "
                    "WHERE organization_id = :org AND source_id = :source_id"
                ),
                {
                    "org": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                },
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO source_version (
                        organization_id, source_id, version_id, source_kind,
                        root_ref, capability_manifest, created_at
                    )
                    SELECT organization_id, source_id, :replacement_version_id,
                           source_kind, root_ref, capability_manifest,
                           statement_timestamp()
                    FROM source_version
                    WHERE organization_id = :org
                      AND source_id = :source_id
                      AND version_id = :old_version_id
                    """
                ),
                {
                    "org": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                    "old_version_id": old_version_id,
                    "replacement_version_id": replacement_version_id,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE context_source
                    SET active_version_id = :replacement_version_id
                    WHERE organization_id = :org
                      AND source_id = :source_id
                      AND active_version_id = :old_version_id
                    """
                ),
                {
                    "org": scenario.organization_id,
                    "source_id": scenario.source_ref.value,
                    "old_version_id": old_version_id,
                    "replacement_version_id": replacement_version_id,
                },
            )

        after = _resolve_file(
            scenario,
            guarded_runtime_engine,
            query_digest_keyring,
            user_id=user_id,
            query="ContextEngine delivers context.",
            request_id="file-source-version-after",
        )
        assert after["blocks"] == []
        assert after["evidence"] == []
        with migration_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT resource_ref, revision_id, fragment_ref, "
                        "phrase_digest FROM exact_phrase_candidate "
                        "WHERE organization_id = :org ORDER BY resource_ref, "
                        "revision_id, fragment_ref, phrase_digest"
                    ),
                    {"org": scenario.organization_id},
                ).all()
                == candidate_snapshot
            )
    finally:
        migration_engine.dispose()
        clear_test_runtime_release(scenario.organization_id)
        delete_file_import_scenario(
            migration_configuration,
            scenario.organization_id,
        )


@pytest.mark.security_evidence(id="RUNTIME-ARTICLE-ACCESS-141", layer="runtime")
def test_groups_membership_removal_refuses_next_resolve_under_current_membership(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
) -> None:
    fixture = _new_fixture()
    migration_engine = create_database_engine(migration_configuration)
    candidate_index = HostileCandidateIndex(
        fixture.org_a,
        cross_organization=fixture.org_b.authorized,
    )
    client = _client(
        active=fixture.org_a,
        guarded_runtime_engine=guarded_runtime_engine,
        index=candidate_index,
        query_digest_keyring=query_digest_keyring,
    )
    group_ref = "group:runtime-current"
    try:
        _seed_fixture(migration_engine, fixture)
        ensure_test_runtime_release(fixture.org_a.organization_id)
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO article_access_group (organization_id, group_ref) "
                    "VALUES (:org, :group_ref)"
                ),
                {
                    "org": fixture.org_a.organization_id,
                    "group_ref": group_ref,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO article_access_group_membership (
                        organization_id, group_ref, membership_id,
                        membership_version
                    ) VALUES (:org, :group_ref, :membership_id, 1)
                    """
                ),
                {
                    "org": fixture.org_a.organization_id,
                    "group_ref": group_ref,
                    "membership_id": fixture.org_a.membership_id,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE article_access_policy
                    SET local_policy_kind = 'groups',
                        local_group_refs = CAST(:group_refs AS text[]),
                        policy_kind = 'groups',
                        group_refs = CAST(:group_refs AS text[])
                    WHERE organization_id = :org AND resource_ref = :resource_ref
                    """
                ),
                {
                    "org": fixture.org_a.organization_id,
                    "resource_ref": fixture.org_a.authorized.resource_ref,
                    "group_refs": [group_ref],
                },
            )
            connection.execute(
                text(
                    "DELETE FROM resource_access_policy "
                    "WHERE organization_id = :org AND resource_ref = :resource_ref"
                ),
                {
                    "org": fixture.org_a.organization_id,
                    "resource_ref": fixture.org_a.authorized.resource_ref,
                },
            )
        # GROUPS is the Article policy grant and uses current Membership at the
        # Article atom, including the publication trace. There is deliberately
        # no legacy principal grant. Group administration remains deferred to #130.
        _assert_authorized(_resolve(client), fixture.org_a)
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM article_access_group_membership "
                    "WHERE organization_id = :org AND group_ref = :group_ref "
                    "AND membership_id = :membership_id AND membership_version = 1"
                ),
                {
                    "org": fixture.org_a.organization_id,
                    "group_ref": group_ref,
                    "membership_id": fixture.org_a.membership_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE organization_policy_epoch "
                    "SET policy_epoch = policy_epoch + 1 "
                    "WHERE organization_id = :org"
                ),
                {"org": fixture.org_a.organization_id},
            )

        _assert_revoked_empty(_resolve(client), fixture.org_a)
        assert len(candidate_index.calls) == 2
    finally:
        try:
            _cleanup_fixture(migration_engine, fixture)
        finally:
            migration_engine.dispose()
