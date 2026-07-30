from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep

import pytest
from sqlalchemy import Engine, text

from engine.control import (
    ActivateFileChangeFeed,
    ContextControl,
    ControlOperation,
    ControlOperatorAuthority,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLControlStore,
    create_database_engine,
)
from engine.persistence import membership_context as membership_context_module
from engine.persistence.access_policy import (
    PostgreSQLAccessPolicyControl,
    ResourceAccessRevocation,
)
from engine.runtime.package_digest import QueryDigestKeyring
from tests.integration.test_zz_file_revision_replacement import (
    _resolve as _resolve_file,
)
from tests.support.article_access_policy import (
    article_policy,
    fixed_policy_epoch,
    policy_epoch,
)
from tests.support.file_imports import (
    NOW,
    ControlAuthenticator,
    FileImportScenario,
    delete_file_import_scenario,
    prepare_file_import_scenario,
    run_file_import,
)
from tests.support.releases import clear_test_runtime_release

pytestmark = pytest.mark.integration


def _published_file(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> tuple[FileImportScenario, str]:
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
    return scenario, published.candidate_refs[0].resource_ref


def test_first_ingest_records_current_epoch_without_advancing_it(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario, resource_ref = _published_file(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    try:
        assert policy_epoch(engine, scenario.organization_id) == 1
        assert fixed_policy_epoch(engine, scenario.organization_id, resource_ref) == 1
    finally:
        engine.dispose()
        delete_file_import_scenario(migration_configuration, scenario.organization_id)


def test_existing_effective_policy_change_and_epoch_commit_atomically(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario, resource_ref = _published_file(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    try:
        before = article_policy(engine, scenario.organization_id, resource_ref)
        assert before[:2] == ("private", 1)
        result = PostgreSQLAccessPolicyControl(guarded_control_engine).change_access(
            ResourceAccessRevocation(
                organization_id=scenario.organization_id,
                resource_ref=resource_ref,
                principal_ref="principal:file-reader",
                expected_access_version=1,
            )
        )
        assert result.value == 2
        revoked = article_policy(engine, scenario.organization_id, resource_ref)
        assert revoked[0] is None
        assert revoked[1] == 2
        assert policy_epoch(engine, scenario.organization_id) == 2
        assert fixed_policy_epoch(engine, scenario.organization_id, resource_ref) == 2
    finally:
        engine.dispose()
        delete_file_import_scenario(migration_configuration, scenario.organization_id)


def test_policy_and_epoch_rollback_together_after_injected_failure(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
) -> None:
    scenario, resource_ref = _published_file(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    try:
        before_policy = article_policy(engine, scenario.organization_id, resource_ref)
        before_epoch = policy_epoch(engine, scenario.organization_id)
        with (
            pytest.raises(Exception, match="division by zero"),
            guarded_control_engine.begin() as connection,
        ):
            connection.execute(
                text("SELECT set_config('app.organization_id', :org, true)"),
                {"org": str(scenario.organization_id)},
            )
            connection.execute(
                text(
                    "SELECT public.context_control_revoke_resource_access("
                    ":org, :resource, 'principal:file-reader', 1)"
                ),
                {"org": scenario.organization_id, "resource": resource_ref},
            ).scalar_one()
            connection.execute(text("SELECT 1 / 0")).scalar_one()

        assert (
            article_policy(engine, scenario.organization_id, resource_ref)
            == before_policy
        )
        assert policy_epoch(engine, scenario.organization_id) == before_epoch
    finally:
        engine.dispose()
        delete_file_import_scenario(migration_configuration, scenario.organization_id)


def test_source_version_activation_invalidates_an_inflight_article_delivery(
    tmp_path: Path,
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    guarded_worker_engine: Engine,
    guarded_runtime_engine: Engine,
    query_digest_keyring: QueryDigestKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newly active SourceVersion cannot race stale Article bytes to delivery."""

    scenario, _ = _published_file(
        tmp_path,
        migration_configuration,
        guarded_control_engine,
        guarded_worker_engine,
    )
    engine = create_database_engine(migration_configuration)
    final_gate = Event()
    release_final_gate = Event()
    read_lock = Lock()
    read_count = 0
    original_read = (
        membership_context_module._PostgreSQLPolicyEpochPort.read_current_epoch
    )

    def block_final_epoch_read(port: object, organization_id: object) -> object:
        nonlocal read_count
        with read_lock:
            read_count += 1
            current_read = read_count
        if current_read == 3:
            final_gate.set()
            if not release_final_gate.wait(timeout=5):
                raise AssertionError("final Policy Epoch gate barrier timed out")
        return original_read(port, organization_id)  # type: ignore[arg-type]

    monkeypatch.setattr(
        membership_context_module._PostgreSQLPolicyEpochPort,
        "read_current_epoch",
        block_final_epoch_read,
    )
    try:
        with engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM membership WHERE organization_id = :org "
                    "AND membership_id = :membership"
                ),
                {"org": scenario.organization_id, "membership": scenario.membership_id},
            ).scalar_one()
        authority = ControlOperatorAuthority(
            ControlAuthenticator(scenario.organization_id),
            call_ttl=timedelta(minutes=5),
            clock=lambda: NOW,
        )
        control = ContextControl(
            store=PostgreSQLControlStore(guarded_control_engine, clock=lambda: NOW),
            authority=authority,
            clock=lambda: NOW,
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            reader = executor.submit(
                _resolve_file,
                scenario,
                guarded_runtime_engine,
                query_digest_keyring,
                user_id=user_id,
                query="ContextEngine delivers context.",
                request_id="source-version-activation-race",
            )
            assert final_gate.wait(timeout=5)

            def activate() -> object:
                with authority.authorize(
                    opaque_credential="control-secret",
                    operation=ControlOperation.ACTIVATE_FILE_CHANGE_FEED,
                    request_id="activate-version-during-resolve",
                ) as call:
                    return control.activate_file_change_feed(
                        call, ActivateFileChangeFeed(scenario.source_ref)
                    )

            activation = executor.submit(activate)
            try:
                activation_waiting = False
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    with engine.connect() as observer:
                        activation_waiting = observer.execute(
                            text(
                                "SELECT EXISTS ("
                                "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                                "AND mode = 'ExclusiveLock' AND granted IS FALSE)"
                            )
                        ).scalar_one()
                    if activation_waiting or activation.done():
                        break
                    sleep(0.01)
                if activation.done():
                    activation.result(timeout=5)
                assert activation_waiting is True
                assert activation.done() is False
            finally:
                release_final_gate.set()
            package = reader.result(timeout=5)
            assert activation.result(timeout=5) is not None

        assert [block["text"] for block in package["blocks"]] == [
            "ContextEngine delivers context."
        ]
        assert package["evidence"]
        assert policy_epoch(engine, scenario.organization_id) == 2
    finally:
        release_final_gate.set()
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM decision_audit WHERE organization_id = :org"),
                {"org": scenario.organization_id},
            )
            connection.execute(
                text("DELETE FROM context_run WHERE organization_id = :org"),
                {"org": scenario.organization_id},
            )
        clear_test_runtime_release(scenario.organization_id)
        engine.dispose()
        delete_file_import_scenario(migration_configuration, scenario.organization_id)
