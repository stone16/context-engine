from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from engine.article_access_policy import (
    ArticleAccessPolicyKind,
    ArticleAccessPolicySetting,
    GroupRef,
)
from engine.control import (
    ContextControl,
    ControlOperation,
    ControlOperatorAuthenticationRejected,
    ControlOperatorAuthority,
    ControlStorePort,
    SourceControlUnavailable,
    SourceNotAvailable,
    VerifiedControlOperatorIdentity,
)
from engine.control.bulk_article_policy import (
    BulkArticlePolicyChange,
    BulkArticlePolicyConfirmation,
    BulkArticlePolicyPreview,
    BulkArticlePolicyResult,
)
from engine.control.module import BulkArticlePolicyStorePort
from engine.persistence import DatabaseConfiguration, PostgreSQLControlStore
from engine.persistence.access_policy import PostgreSQLAccessPolicyControl
from tests.support.article_access_policy import (
    article_policy,
    delete_article_policy_scenario,
    ingest_article,
    insert_organization,
    observe_source_acl,
    policy_epoch,
)

pytestmark = pytest.mark.integration
PRIVATE = ArticleAccessPolicySetting(ArticleAccessPolicyKind.PRIVATE)
ORGANIZATION = ArticleAccessPolicySetting(ArticleAccessPolicyKind.ORGANIZATION)
NOW = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)


class _Authenticator:
    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id

    def authenticate(self, opaque_credential: str) -> VerifiedControlOperatorIdentity:
        if opaque_credential != "bulk-policy-credential":
            raise ControlOperatorAuthenticationRejected
        return VerifiedControlOperatorIdentity(
            organization_id=self.organization_id,
            operator_ref="operator:test-bulk-policy",
            authentication_binding_ref="binding:test-bulk-policy",
            authority_ref="authority:test-bulk-policy",
            allowed_operations=frozenset(
                {
                    ControlOperation.PREVIEW_BULK_ARTICLE_POLICY_CHANGE,
                    ControlOperation.COMMIT_BULK_ARTICLE_POLICY_CHANGE,
                }
            ),
            valid_from=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        )


def _control(
    engine: Engine, organization_id: UUID
) -> tuple[ContextControl, ControlOperatorAuthority]:
    store = PostgreSQLAccessPolicyControl(engine)
    authority = ControlOperatorAuthority(
        _Authenticator(organization_id),
        call_ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    return (
        ContextControl(
            store=cast(
                ControlStorePort,
                PostgreSQLControlStore(engine, clock=lambda: NOW),
            ),
            bulk_article_policy_store=cast(BulkArticlePolicyStorePort, store),
            authority=authority,
            clock=lambda: NOW,
        ),
        authority,
    )


def _prepare_articles(
    engine: Engine,
    *,
    organization_id: UUID,
    count: int,
    source_policy: str = "organization",
) -> tuple[str, ...]:
    insert_organization(engine, organization_id)
    refs: list[str] = []
    for index in range(count):
        source_ref = f"source:bulk:{organization_id}:{index}"
        resource_ref = f"article:bulk:{organization_id}:{index}"
        observe_source_acl(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=resource_ref,
            policy_kind=source_policy,
        )
        ingest_article(
            engine,
            organization_id=organization_id,
            source_ref=source_ref,
            resource_ref=resource_ref,
        )
        refs.append(resource_ref)
    return tuple(refs)


def _preview(
    control: ContextControl,
    authority: ControlOperatorAuthority,
    resource_refs: tuple[str, ...],
    target_policy: ArticleAccessPolicySetting = ORGANIZATION,
) -> BulkArticlePolicyPreview:
    with authority.authorize(
        opaque_credential="bulk-policy-credential",
        operation=ControlOperation.PREVIEW_BULK_ARTICLE_POLICY_CHANGE,
        request_id=f"preview-{uuid4().hex}",
    ) as call:
        return control.preview_bulk_article_policy_change(
            call,
            BulkArticlePolicyChange(resource_refs, target_policy),
        )


def _commit(
    control: ContextControl,
    authority: ControlOperatorAuthority,
    preview: BulkArticlePolicyPreview,
    request_id: str = "bulk-policy-test",
) -> BulkArticlePolicyResult:
    command = BulkArticlePolicyChange(
        tuple(item.resource_ref for item in preview.items),
        preview.items[0].target_policy,
    )
    with authority.authorize(
        opaque_credential="bulk-policy-credential",
        operation=ControlOperation.COMMIT_BULK_ARTICLE_POLICY_CHANGE,
        request_id=request_id,
    ) as call:
        return control.commit_bulk_article_policy_change(
            call,
            command,
            BulkArticlePolicyConfirmation(preview.digest),
        )


def _audit_count(engine: Engine, organization_id: UUID) -> int:
    with engine.connect() as connection:
        observed = connection.execute(
            text(
                "SELECT count(*) FROM bulk_article_policy_change_audit "
                "WHERE organization_id = :organization_id"
            ),
            {"organization_id": organization_id},
        ).scalar_one()
    assert type(observed) is int
    return observed


def test_preview_without_confirmation_changes_nothing(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    try:
        refs = _prepare_articles(
            engine, organization_id=organization_id, count=2
        )
        control, authority = _control(guarded_control_engine, organization_id)
        before = tuple(article_policy(engine, organization_id, ref) for ref in refs)

        preview = _preview(control, authority, refs)

        assert tuple(item.current_policy for item in preview.items) == (
            PRIVATE,
            PRIVATE,
        )
        assert tuple(item.resolution_rung.value for item in preview.items) == (
            "tenant_default",
            "tenant_default",
        )
        assert tuple(item.target_policy for item in preview.items) == (
            ORGANIZATION,
            ORGANIZATION,
        )
        assert (
            tuple(article_policy(engine, organization_id, ref) for ref in refs)
            == before
        )
        assert policy_epoch(engine, organization_id) == 1
        assert _audit_count(engine, organization_id) == 0
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


def test_confirmed_selection_commits_all_policies_one_epoch_and_one_safe_audit(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    try:
        refs = _prepare_articles(engine, organization_id=organization_id, count=3)
        control, authority = _control(guarded_control_engine, organization_id)
        preview = _preview(control, authority, refs)

        result = _commit(control, authority, preview)

        assert result.policy_epoch == 2
        assert result.changed_articles == 3
        assert tuple(article_policy(engine, organization_id, ref) for ref in refs) == (
            ("organization", 2, "explicit_article"),
        ) * 3
        assert policy_epoch(engine, organization_id) == 2
        with engine.connect() as connection:
            audit = connection.execute(
                text(
                    """
                    SELECT article_count, preview_digest, target_policy_digest,
                           operator_digest, authority_digest, request_digest,
                           reason_category
                    FROM bulk_article_policy_change_audit
                    WHERE organization_id = :organization_id
                    """
                ),
                {"organization_id": organization_id},
            ).mappings().one()
        assert audit["article_count"] == 3
        assert audit["preview_digest"] == preview.digest
        assert all(
            len(audit[name]) == 64
            for name in (
                "target_policy_digest",
                "operator_digest",
                "authority_digest",
                "request_digest",
            )
        )
        assert audit["reason_category"] == "operator_confirmed_visibility_change"
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


def test_reused_confirmation_digest_is_refused_without_a_second_effect(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    try:
        refs = _prepare_articles(engine, organization_id=organization_id, count=2)
        control, authority = _control(guarded_control_engine, organization_id)
        preview = _preview(control, authority, refs)

        first = _commit(control, authority, preview, "first-confirmation")
        with pytest.raises(SourceNotAvailable):
            _commit(control, authority, preview, "replayed-confirmation")

        assert first.policy_epoch == 2
        assert tuple(article_policy(engine, organization_id, ref) for ref in refs) == (
            ("organization", 2, "explicit_article"),
        ) * 2
        assert policy_epoch(engine, organization_id) == 2
        assert _audit_count(engine, organization_id) == 1
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


def test_bulk_change_refuses_policy_epoch_overflow_without_any_effect(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    try:
        refs = _prepare_articles(engine, organization_id=organization_id, count=1)
        control, authority = _control(guarded_control_engine, organization_id)
        preview = _preview(control, authority, refs)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE organization_policy_epoch "
                    "SET policy_epoch = 9223372036854775807 "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": organization_id},
            )

        with pytest.raises(SourceControlUnavailable):
            _commit(control, authority, preview, "epoch-overflow")

        assert article_policy(engine, organization_id, refs[0]) == (
            "private",
            1,
            "tenant_default",
        )
        assert policy_epoch(engine, organization_id) == 9223372036854775807
        assert _audit_count(engine, organization_id) == 0
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


def test_fault_after_policy_writes_rolls_back_selection_epoch_and_audit(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    try:
        refs = _prepare_articles(engine, organization_id=organization_id, count=2)
        control, authority = _control(guarded_control_engine, organization_id)
        preview = _preview(control, authority, refs)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION public.test_fail_bulk_audit() RETURNS trigger
                    LANGUAGE plpgsql AS $function$ BEGIN
                        PERFORM 1 / 0;
                        RETURN NEW;
                    END $function$
                    """
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER test_fail_bulk_audit BEFORE INSERT ON "
                    "bulk_article_policy_change_audit FOR EACH ROW EXECUTE "
                    "FUNCTION public.test_fail_bulk_audit()"
                )
            )
        try:
            with pytest.raises(SourceControlUnavailable):
                _commit(
                    control, authority, preview, "fault-after-policy-writes"
                )
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DROP TRIGGER IF EXISTS test_fail_bulk_audit ON "
                        "bulk_article_policy_change_audit"
                    )
                )
                connection.execute(
                    text("DROP FUNCTION IF EXISTS public.test_fail_bulk_audit()")
                )

        assert tuple(article_policy(engine, organization_id, ref) for ref in refs) == (
            ("private", 1, "tenant_default"),
        ) * 2
        assert policy_epoch(engine, organization_id) == 1
        assert _audit_count(engine, organization_id) == 0
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


def test_source_native_acl_floor_refuses_widening_and_failed_observation(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    private_org = uuid4()
    failed_org = uuid4()
    try:
        private_refs = _prepare_articles(
            engine,
            organization_id=private_org,
            count=1,
            source_policy="private",
        )
        insert_organization(engine, failed_org)
        failed_source = f"source:bulk:{failed_org}"
        failed_resource = f"article:bulk:{failed_org}"
        observe_source_acl(
            engine,
            organization_id=failed_org,
            source_ref=failed_source,
            resource_ref=failed_resource,
            status="failed",
            policy_kind=None,
        )
        ingest_article(
            engine,
            organization_id=failed_org,
            source_ref=failed_source,
            resource_ref=failed_resource,
        )
        private_control, private_authority = _control(
            guarded_control_engine, private_org
        )
        failed_control, failed_authority = _control(
            guarded_control_engine, failed_org
        )
        widening = _preview(private_control, private_authority, private_refs)
        failed = _preview(
            failed_control, failed_authority, (failed_resource,), PRIVATE
        )

        with pytest.raises(SourceControlUnavailable):
            _commit(
                private_control,
                private_authority,
                widening,
                "refuse-widening",
            )
        with pytest.raises(SourceControlUnavailable):
            _commit(
                failed_control,
                failed_authority,
                failed,
                "refuse-failed-observation",
            )

        assert article_policy(engine, private_org, private_refs[0]) == (
            "private",
            1,
            "tenant_default",
        )
        assert article_policy(engine, failed_org, failed_resource) == (
            None,
            1,
            "tenant_default",
        )
        assert policy_epoch(engine, private_org) == 1
        assert policy_epoch(engine, failed_org) == 1
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, private_org)
        delete_article_policy_scenario(migration_configuration, failed_org)


def test_preview_refuses_unowned_group_target_before_confirmation(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    try:
        refs = _prepare_articles(engine, organization_id=organization_id, count=1)
        control, authority = _control(guarded_control_engine, organization_id)
        missing_group = ArticleAccessPolicySetting(
            ArticleAccessPolicyKind.GROUPS,
            frozenset({GroupRef("group:missing")}),
        )

        with pytest.raises(SourceControlUnavailable):
            _preview(control, authority, refs, missing_group)

        assert article_policy(engine, organization_id, refs[0])[1] == 1
        assert policy_epoch(engine, organization_id) == 1
        assert _audit_count(engine, organization_id) == 0
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


def test_concurrent_bulk_changes_serialize_to_distinct_increasing_epochs(
    guarded_control_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    organization_id = uuid4()
    try:
        refs = _prepare_articles(engine, organization_id=organization_id, count=2)
        control, authority = _control(guarded_control_engine, organization_id)
        previews = tuple(
            _preview(control, authority, (ref,)) for ref in refs
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results: tuple[BulkArticlePolicyResult, ...] = tuple(
                future.result(timeout=10)
                for future in (
                    executor.submit(
                        _commit,
                        control,
                        authority,
                        preview,
                        f"concurrent-{index}",
                    )
                    for index, preview in enumerate(previews)
                )
            )

        assert sorted(result.policy_epoch for result in results) == [2, 3]
        assert policy_epoch(engine, organization_id) == 3
        assert _audit_count(engine, organization_id) == 2
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_id)


@pytest.mark.security_evidence(id="PG-BULK-ARTICLE-POLICY-132", layer="postgres")
def test_cross_organization_selection_is_non_enumerating_and_changes_neither_org(
    guarded_control_engine: Engine,
    guarded_runtime_engine: Engine,
    migration_configuration: DatabaseConfiguration,
) -> None:
    from engine.persistence import create_database_engine

    engine = create_database_engine(migration_configuration)
    organization_a = uuid4()
    organization_b = uuid4()
    try:
        ref_a = _prepare_articles(engine, organization_id=organization_a, count=1)[0]
        ref_b = _prepare_articles(engine, organization_id=organization_b, count=1)[0]
        control, authority = _control(guarded_control_engine, organization_a)

        with pytest.raises(SourceControlUnavailable) as failure:
            _preview(control, authority, (ref_a, ref_b))

        assert str(failure.value) == "Article policy administration is unavailable"
        assert article_policy(engine, organization_a, ref_a)[1] == 1
        assert article_policy(engine, organization_b, ref_b)[1] == 1
        assert policy_epoch(engine, organization_a) == 1
        assert policy_epoch(engine, organization_b) == 1
        assert _audit_count(engine, organization_a) == 0
        assert _audit_count(engine, organization_b) == 0
        with guarded_runtime_engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('app.organization_id', :org, true)"
                ),
                {"org": str(organization_a)},
            )
            with pytest.raises(DBAPIError):
                connection.execute(
                    text("SELECT * FROM bulk_article_policy_change_audit")
                ).all()
    finally:
        engine.dispose()
        delete_article_policy_scenario(migration_configuration, organization_a)
        delete_article_policy_scenario(migration_configuration, organization_b)
