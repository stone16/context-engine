from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import Engine, text

from adapters.file_source import FileChangeProvider, FileReadLimits, FileRootRegistry
from applications.operator_authentication import (
    CONTROL_OPERATOR_OPERATIONS_ENV,
    CONTROL_OPERATOR_SECRET_ENV,
    DOGFOOD_SECRET_ENV,
    OPERATOR_ORGANIZATION_ENV,
    RELEASE_OPERATOR_SECRET_ENV,
    WORKER_SECRET_ENV,
    LocalOperatorConfiguration,
)
from engine.control import (
    ChangeLimit,
    ContextControl,
    ControlOperation,
    FileChangeControlProofs,
    FileChangeProviderProofs,
    FileChangeSource,
    FileImportReceiver,
    InitialScan,
    ProviderOk,
    SourceRef,
)
from engine.persistence import (
    DatabaseConfiguration,
    PostgreSQLControlStore,
    create_database_engine,
)
from engine.supply import CONTEXT_FRAGMENT_EMBEDDING_DIMENSION
from tests.integration.test_file_change_pages import (
    _SCENARIOS,
    _delete_scenarios,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
CONTROL_SECRET = "issue-112-control-operator-secret-0001"
RELEASE_SECRET = "issue-112-release-operator-secret-0001"
DOGFOOD_SECRET = "issue-112-dogfood-runtime-secret-0001"
WORKER_KEY = bytes.fromhex("ab" * 32)
PROVIDER_KEY = bytes.fromhex("cd" * 32)
CHECKPOINT_KEY = bytes.fromhex("ef" * 32)


@pytest.fixture
def file_scan_scenario(
    migration_configuration: DatabaseConfiguration,
    tmp_path: Path,
) -> Iterator[tuple[UUID, UUID, UUID, Path, dict[str, str]]]:
    organization_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    receiver_id = uuid4()
    root = tmp_path / "operator-scan-root"
    root.mkdir()
    scenarios: list[tuple[UUID, UUID]] = []
    _SCENARIOS.append(scenarios)
    scenarios.append((organization_id, user_id))
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (organization_id) VALUES (:org)"),
                {"org": organization_id},
            )
            connection.execute(
                text("INSERT INTO user_account (user_id) VALUES (:user)"),
                {"user": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO membership (
                        organization_id, membership_id, user_id, status,
                        membership_version, valid_from
                    ) VALUES (
                        :org, :membership, :user, 'active', 1,
                        statement_timestamp() - interval '1 day'
                    )
                    """
                ),
                {
                    "org": organization_id,
                    "membership": membership_id,
                    "user": user_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO service_principal (
                        organization_id, service_principal_id, workload,
                        worker_audience, operation, enabled
                    ) VALUES (
                        :org, :receiver, 'supply.file-import',
                        'context-engine-worker', 'file.import', true
                    )
                    """
                ),
                {"org": organization_id, "receiver": receiver_id},
            )
        environment = {
            **os.environ,
            OPERATOR_ORGANIZATION_ENV: str(organization_id),
            CONTROL_OPERATOR_SECRET_ENV: CONTROL_SECRET,
            RELEASE_OPERATOR_SECRET_ENV: RELEASE_SECRET,
            DOGFOOD_SECRET_ENV: DOGFOOD_SECRET,
            WORKER_SECRET_ENV: WORKER_KEY.hex(),
            CONTROL_OPERATOR_OPERATIONS_ENV: (
                "register_source,read_source,read_source_progress,"
                "activate_file_change_feed,"
                "activate_file_delete_observations,"
                "accept_file_change_page,schedule_file_change_page"
            ),
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID": str(membership_id),
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION": "1",
            "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF": "principal:file-reader",
            "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID": str(receiver_id),
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": json.dumps(
                {"operator-scan-root": str(root)}
            ),
            "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX": (PROVIDER_KEY.hex()),
            "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX": (
                CHECKPOINT_KEY.hex()
            ),
            "CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER": "twin",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION": str(
                CONTEXT_FRAGMENT_EMBEDDING_DIMENSION
            ),
        }
        yield organization_id, membership_id, receiver_id, root, environment
    finally:
        engine.dispose()
        _SCENARIOS.remove(scenarios)
        _delete_scenarios(migration_configuration, scenarios)


def _control(
    arguments: list[str],
    *,
    environment: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["context-engine-control", *arguments],
        cwd=ROOT,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def _worker(environment: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        ["context-engine-worker", "--dispatch-file-once"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""
    return cast(dict[str, object], json.loads(completed.stdout))


def _register_activated_source(
    organization_id: UUID,
    environment: dict[str, str],
) -> UUID:
    registered = _control(
        [
            "register-file-source",
            "--organization-id",
            str(organization_id),
            "--display-name",
            "Operator scan fixture",
            "--root-ref",
            "operator-scan-root",
            "--idempotency-key",
            "operator-scan-fixture-v1",
        ],
        environment=environment,
    )
    source_ref = UUID(json.loads(registered.stdout)["sourceRef"])
    for subcommand in (
        "activate-change-feed",
        "activate-delete-observations",
    ):
        _control(
            [
                subcommand,
                "--organization-id",
                str(organization_id),
                "--source-ref",
                str(source_ref),
            ],
            environment=environment,
        )
    return source_ref


def _register_change_feed_source(
    organization_id: UUID,
    environment: dict[str, str],
) -> UUID:
    registered = _control(
        [
            "register-file-source",
            "--organization-id",
            str(organization_id),
            "--display-name",
            "Operator v3 scan fixture",
            "--root-ref",
            "operator-scan-root",
            "--idempotency-key",
            "operator-v3-scan-fixture-v1",
        ],
        environment=environment,
    )
    source_ref = UUID(json.loads(registered.stdout)["sourceRef"])
    _control(
        [
            "activate-change-feed",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=environment,
    )
    return source_ref


def _scan(
    organization_id: UUID,
    source_ref: UUID,
    environment: dict[str, str],
) -> dict[str, object]:
    completed = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=environment,
    )
    assert completed.stderr == ""
    return cast(dict[str, object], json.loads(completed.stdout))


def _status(
    organization_id: UUID,
    source_ref: UUID,
    environment: dict[str, str],
) -> dict[str, object]:
    completed = _control(
        [
            "status",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=environment,
    )
    assert completed.stderr == ""
    return cast(dict[str, object], json.loads(completed.stdout))


def test_status_reports_progress_freshness_and_current_compilation_refusals(
    migration_configuration: DatabaseConfiguration,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "good.md").write_text("# Good\n\nPublished.\n", encoding="utf-8")
    (root / "refused.md").write_text(
        "# Refused\n\n> category only\n",
        encoding="utf-8",
    )
    source_ref = _register_activated_source(organization_id, environment)
    never_scanned = _status(organization_id, source_ref, environment)
    assert never_scanned == {
        "acquisitionCheckpoint": None,
        "activeResourceCount": 0,
        "changeScanHead": None,
        "completeChangeBaselineScanBound": None,
        "completeChangeBaselineSize": 0,
        "lastSuccessfulAcquisition": {"state": "never"},
        "publishWatermark": None,
        "refusals": [],
        "scanRefusal": None,
        "sourceRef": str(source_ref),
    }
    scanned = _scan(organization_id, source_ref, environment)

    before_publication = _status(organization_id, source_ref, environment)
    assert before_publication == {
        "acquisitionCheckpoint": before_publication["acquisitionCheckpoint"],
        "activeResourceCount": 0,
        "changeScanHead": before_publication["changeScanHead"],
        "completeChangeBaselineScanBound": 10_000,
        "completeChangeBaselineSize": 2,
        "lastSuccessfulAcquisition": {"state": "never"},
        "publishWatermark": None,
        "refusals": [],
        "scanRefusal": None,
        "sourceRef": str(source_ref),
    }
    assert before_publication["acquisitionCheckpoint"] is not None
    scan_head = cast(dict[str, object], before_publication["changeScanHead"])
    assert scan_head == {
        "checkpointRef": scanned["advancedCursor"],
        "complete": True,
        "pageLimit": 1,
        "pageRef": scan_head["pageRef"],
        "scanEpoch": scan_head["scanEpoch"],
        "scanRef": scan_head["scanRef"],
        "scanBound": 10_000,
        "sequence": scan_head["sequence"],
        "sourceVersionRef": scan_head["sourceVersionRef"],
    }

    assert _worker(environment)["outcome"] == "dispatched"
    assert _worker(environment)["outcome"] == "refused"
    after_workers = _status(organization_id, source_ref, environment)
    assert after_workers["activeResourceCount"] == 1
    assert after_workers["completeChangeBaselineSize"] == 2
    assert after_workers["refusals"] == [
        {"category": "unsupported_construct", "path": "refused.md"}
    ]
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            retained = connection.execute(
                text(
                    "SELECT acquisition.relative_path, "
                    "job.compilation_refusal_category "
                    "FROM file_import_job AS job "
                    "JOIN file_acquisition AS acquisition "
                    "ON acquisition.organization_id = job.organization_id "
                    "AND acquisition.acquisition_id = job.acquisition_id "
                    "WHERE job.organization_id = :organization_id "
                    "AND job.source_id = :source_id "
                    "AND job.compilation_refusal_category IS NOT NULL"
                ),
                {"organization_id": organization_id, "source_id": source_ref},
            ).one()
        assert retained._tuple() == ("refused.md", "unsupported_construct")
        assert "category only" not in repr(retained._tuple())
    finally:
        engine.dispose()
    successful = cast(dict[str, object], after_workers["lastSuccessfulAcquisition"])
    assert successful["state"] == "succeeded"
    assert type(successful["at"]) is str
    assert type(successful["ageSeconds"]) is int
    assert successful["ageSeconds"] >= 0
    assert after_workers["publishWatermark"] is not None

    (root / "good.md").write_text(
        "# Good changed\n\n> current content is refused\n",
        encoding="utf-8",
    )
    _scan(organization_id, source_ref, environment)
    assert _worker(environment)["outcome"] == "refused"
    after_published_path_refusal = _status(
        organization_id,
        source_ref,
        environment,
    )
    assert after_published_path_refusal["activeResourceCount"] == 1
    assert after_published_path_refusal["refusals"] == [
        {"category": "unsupported_construct", "path": "good.md"},
        {"category": "unsupported_construct", "path": "refused.md"},
    ]

    (root / "refused.md").unlink()
    deleted = _scan(organization_id, source_ref, environment)
    after_delete = _status(organization_id, source_ref, environment)
    deleted_head = cast(dict[str, object], after_delete["changeScanHead"])
    assert deleted_head["checkpointRef"] == deleted["advancedCursor"]
    assert after_delete["completeChangeBaselineSize"] == 2
    assert after_delete["activeResourceCount"] == 1
    assert after_delete["refusals"] == [
        {"category": "unsupported_construct", "path": "good.md"}
    ]


def test_scan_process_schedules_only_changed_upserts_and_existing_worker_consumes(
    migration_configuration: DatabaseConfiguration,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "a.md").write_text("# A\n\nFirst note.\n", encoding="utf-8")
    (root / "refused.md").write_text(
        "# Refused\n\n> blockquotes remain unsupported\n",
        encoding="utf-8",
    )
    source_ref = _register_activated_source(organization_id, environment)

    first = _scan(organization_id, source_ref, environment)

    assert first == {
        "advancedCursor": first["advancedCursor"],
        "changesAccepted": 2,
        "deletesObserved": 0,
        "importsScheduled": 2,
        "pathsObserved": 2,
        "scanBound": 10_000,
        "sourceRef": str(source_ref),
    }
    assert type(first["advancedCursor"]) is str
    assert str(first["advancedCursor"]).startswith("facp_")
    assert [_worker(environment)["outcome"] for _ in range(3)] == [
        "dispatched",
        "refused",
        "no_work",
    ]

    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            first_snapshot = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM file_import_job
                           WHERE organization_id = :org),
                          (SELECT count(*) FROM context_fragment
                           WHERE organization_id = :org),
                          (SELECT count(*) FROM context_fragment
                           WHERE organization_id = :org
                             AND vector_dims(embedding) = 384)
                        """
                    ),
                    {"org": organization_id},
                ).one()
            )
        assert first_snapshot == (2, 1, 1)

        unchanged = _scan(organization_id, source_ref, environment)
        assert unchanged == {
            "advancedCursor": first["advancedCursor"],
            "changesAccepted": 0,
            "deletesObserved": 0,
            "importsScheduled": 0,
            "pathsObserved": 2,
            "scanBound": 10_000,
            "sourceRef": str(source_ref),
        }
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM file_import_job "
                        "WHERE organization_id = :org"
                    ),
                    {"org": organization_id},
                ).scalar_one()
                == 2
            )

        (root / "new.md").write_text("# New\n\nSecond note.\n", encoding="utf-8")
        changed = _scan(organization_id, source_ref, environment)
        assert changed == {
            "advancedCursor": changed["advancedCursor"],
            "changesAccepted": 1,
            "deletesObserved": 0,
            "importsScheduled": 1,
            "pathsObserved": 3,
            "scanBound": 10_000,
            "sourceRef": str(source_ref),
        }
        assert changed["advancedCursor"] != first["advancedCursor"]
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM file_import_job "
                        "WHERE organization_id = :org"
                    ),
                    {"org": organization_id},
                ).scalar_one()
                == 3
            )

        assert _worker(environment)["outcome"] == "dispatched"
        assert _worker(environment)["outcome"] == "no_work"
        with engine.connect() as connection:
            final_snapshot = tuple(
                connection.execute(
                    text(
                        """
                        SELECT count(*),
                               count(*) FILTER (
                                 WHERE vector_dims(embedding) = 384
                               )
                        FROM context_fragment
                        WHERE organization_id = :org
                        """
                    ),
                    {"org": organization_id},
                ).one()
            )
        assert final_snapshot == (2, 2)

        with engine.connect() as connection:
            delete_effects_before = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT policy_epoch
                           FROM organization_policy_epoch
                           WHERE organization_id = :org),
                          (SELECT count(*)
                           FROM file_resource_cleanup_intent
                           WHERE organization_id = :org),
                          (SELECT count(*)
                           FROM file_delete_observation_execution
                           WHERE organization_id = :org),
                          (SELECT count(*)
                           FROM context_resource
                           WHERE organization_id = :org
                             AND tombstoned IS TRUE),
                          (SELECT count(*)
                           FROM context_resource
                           WHERE organization_id = :org
                             AND tombstoned IS FALSE),
                          (SELECT count(*)
                           FROM context_revision
                           WHERE organization_id = :org)
                        """
                    ),
                    {"org": organization_id},
                ).one()
            )

        (root / "a.md").unlink()
        deleted = _scan(organization_id, source_ref, environment)
        assert deleted == {
            "advancedCursor": deleted["advancedCursor"],
            "changesAccepted": 1,
            "deletesObserved": 1,
            "importsScheduled": 0,
            "pathsObserved": 2,
            "scanBound": 10_000,
            "sourceRef": str(source_ref),
        }
        assert deleted["advancedCursor"] != changed["advancedCursor"]
        with engine.connect() as connection:
            after_delete = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT policy_epoch
                           FROM organization_policy_epoch
                           WHERE organization_id = :org),
                          (SELECT count(*)
                           FROM file_resource_cleanup_intent
                           WHERE organization_id = :org),
                          (SELECT count(*)
                           FROM file_delete_observation_execution
                           WHERE organization_id = :org),
                          (SELECT count(*)
                           FROM context_resource
                           WHERE organization_id = :org
                             AND tombstoned IS TRUE),
                          (SELECT count(*)
                           FROM context_resource
                           WHERE organization_id = :org
                             AND tombstoned IS FALSE),
                          (SELECT count(*)
                           FROM context_revision
                           WHERE organization_id = :org),
                          (SELECT count(*)
                           FROM file_import_job
                           WHERE organization_id = :org)
                        """
                    ),
                    {"org": organization_id},
                ).one()
            )
        assert after_delete[:-1] == delete_effects_before
        assert after_delete[-1] == 3

        unchanged_after_delete = _scan(
            organization_id,
            source_ref,
            environment,
        )
        assert unchanged_after_delete == {
            "advancedCursor": deleted["advancedCursor"],
            "changesAccepted": 0,
            "deletesObserved": 0,
            "importsScheduled": 0,
            "pathsObserved": 2,
            "scanBound": 10_000,
            "sourceRef": str(source_ref),
        }
        with engine.connect() as connection:
            unchanged_counts = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM file_import_job
                           WHERE organization_id = :org),
                          (SELECT count(*) FROM context_revision
                           WHERE organization_id = :org)
                        """
                    ),
                    {"org": organization_id},
                ).one()
            )
        assert unchanged_counts == (3, 2)
    finally:
        engine.dispose()


def test_scan_process_recovers_a_complete_accepted_page_missing_its_schedule(
    migration_configuration: DatabaseConfiguration,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, receiver_id, root, environment = file_scan_scenario
    (root / "recover.md").write_text(
        "# Recover\n\nSchedule this accepted note.\n",
        encoding="utf-8",
    )
    source_ref = _register_activated_source(organization_id, environment)
    engine = create_database_engine(migration_configuration)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE service_principal
                    SET enabled = false
                    WHERE organization_id = :org
                      AND service_principal_id = :receiver
                    """
                ),
                {"org": organization_id, "receiver": receiver_id},
            )

        interrupted = _control(
            [
                "scan",
                "--organization-id",
                str(organization_id),
                "--source-ref",
                str(source_ref),
            ],
            environment=environment,
            check=False,
        )

        assert interrupted.returncode != 0
        assert interrupted.stdout == ""
        assert interrupted.stderr == "context-engine-control: operation refused\n"
        with engine.connect() as connection:
            stranded = tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM file_source_change_page
                           WHERE organization_id = :org
                             AND source_id = :source
                             AND complete IS TRUE),
                          (SELECT count(*) FROM file_import_job
                           WHERE organization_id = :org
                             AND source_id = :source)
                        """
                    ),
                    {"org": organization_id, "source": source_ref},
                ).one()
            )
        assert stranded == (1, 0)

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE service_principal
                    SET enabled = true
                    WHERE organization_id = :org
                      AND service_principal_id = :receiver
                    """
                ),
                {"org": organization_id, "receiver": receiver_id},
            )

        recovered = _scan(organization_id, source_ref, environment)

        assert recovered == {
            "advancedCursor": recovered["advancedCursor"],
            "changesAccepted": 0,
            "deletesObserved": 0,
            "importsScheduled": 1,
            "pathsObserved": 1,
            "scanBound": 10_000,
            "sourceRef": str(source_ref),
        }
        assert _worker(environment)["outcome"] == "dispatched"
        assert _worker(environment)["outcome"] == "no_work"
        with engine.connect() as connection:
            assert tuple(
                connection.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM file_import_job
                           WHERE organization_id = :org
                             AND source_id = :source),
                          (SELECT count(*) FROM context_fragment
                           WHERE organization_id = :org)
                        """
                    ),
                    {"org": organization_id, "source": source_ref},
                ).one()
            ) == (1, 1)
    finally:
        engine.dispose()


def test_scan_process_does_not_reconcile_a_foreign_larger_mixed_page(
    migration_configuration: DatabaseConfiguration,
    guarded_control_engine: Engine,
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, receiver_id, root, environment = file_scan_scenario
    (root / "a.md").write_text("# A\n\nOriginal.\n", encoding="utf-8")
    (root / "b.md").write_text("# B\n\nUnchanged.\n", encoding="utf-8")
    source_ref = _register_activated_source(organization_id, environment)
    baseline = _scan(organization_id, source_ref, environment)
    assert baseline["importsScheduled"] == 2

    (root / "a.md").write_text("# A\n\nChanged.\n", encoding="utf-8")
    configuration = LocalOperatorConfiguration.load(environment)
    assert configuration is not None

    def clock() -> datetime:
        return datetime.now(UTC)

    authority = configuration.authorities(clock=clock).control
    provider_key = Ed25519PrivateKey.from_private_bytes(PROVIDER_KEY)
    checkpoint_key = Ed25519PrivateKey.from_private_bytes(CHECKPOINT_KEY)
    control = ContextControl(
        store=PostgreSQLControlStore(
            guarded_control_engine,
            clock=clock,
            file_import_receiver=FileImportReceiver(receiver_id),
            file_change_checkpoint_signing_key=checkpoint_key,
        ),
        authority=authority,
        clock=clock,
        file_change_proofs=FileChangeControlProofs(
            provider_verification_key=provider_key.public_key()
        ),
    )
    source = SourceRef(source_ref)
    with authority.authorize(
        opaque_credential=CONTROL_SECRET,
        operation=ControlOperation.READ_SOURCE,
        request_id="foreign-larger-read-source",
    ) as call:
        manifest = control.read_source(call, source)
    with authority.authorize(
        opaque_credential=CONTROL_SECRET,
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id="foreign-larger-read-baseline",
    ) as call:
        progress = control.read_file_source_progress(call, source)
    provider_source = FileChangeSource(
        organization_id,
        manifest.active_version,
        scan_head=progress.change_scan_head,
        complete_baseline=progress.complete_change_baseline,
    )
    with FileRootRegistry(
        {manifest.active_version.root_ref: root},
        limits=FileReadLimits(max_file_bytes=1_048_576),
    ) as roots:
        page = FileChangeProvider(
            roots,
            proofs=FileChangeProviderProofs(
                provider_signing_key=provider_key,
                checkpoint_verification_key=checkpoint_key.public_key(),
            ),
        ).read_changes(provider_source, InitialScan(), ChangeLimit(2))
    assert type(page) is ProviderOk
    assert page.value.page_limit == 2
    assert page.value.complete is True
    assert tuple(change.path.value for change in page.value.changes) == (
        "a.md",
        "b.md",
    )
    with authority.authorize(
        opaque_credential=CONTROL_SECRET,
        operation=ControlOperation.ACCEPT_FILE_CHANGE_PAGE,
        request_id="foreign-larger-accept-page",
    ) as call:
        control.accept_file_change_page(call, page.value)
    with authority.authorize(
        opaque_credential=CONTROL_SECRET,
        operation=ControlOperation.READ_SOURCE_PROGRESS,
        request_id="foreign-larger-read-pending",
    ) as call:
        accepted_progress = control.read_file_source_progress(call, source)

    assert accepted_progress.pending_change_schedules == ()
    refused = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=environment,
        check=False,
    )
    assert refused.returncode != 0
    assert refused.stdout == ""
    assert refused.stderr == "context-engine-control: operation refused\n"
    engine = create_database_engine(migration_configuration)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM file_import_job "
                        "WHERE organization_id = :org AND source_id = :source"
                    ),
                    {"org": organization_id, "source": source_ref},
                ).scalar_one()
                == 2
            )
    finally:
        engine.dispose()


def test_scan_process_refuses_absent_configuration_generically(
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, _root, environment = (
        file_scan_scenario
    )
    source_ref = uuid4()
    absent = environment.copy()
    del absent["CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX"]

    refused = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=absent,
        check=False,
    )

    assert refused.returncode != 0
    assert refused.stdout == ""
    assert refused.stderr == "context-engine-control: operation refused\n"
    rendered = refused.stdout + refused.stderr
    assert str(organization_id) not in rendered
    assert str(source_ref) not in rendered

    no_operator = environment.copy()
    for name in (
        OPERATOR_ORGANIZATION_ENV,
        CONTROL_OPERATOR_SECRET_ENV,
        RELEASE_OPERATOR_SECRET_ENV,
        DOGFOOD_SECRET_ENV,
        WORKER_SECRET_ENV,
        CONTROL_OPERATOR_OPERATIONS_ENV,
    ):
        del no_operator[name]
    refused_without_operator = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=no_operator,
        check=False,
    )
    assert refused_without_operator.returncode != 0
    assert refused_without_operator.stdout == ""
    assert (
        refused_without_operator.stderr == "context-engine-control: operation refused\n"
    )

    wrong_organization = uuid4()
    wrong_org = _control(
        [
            "scan",
            "--organization-id",
            str(wrong_organization),
            "--source-ref",
            str(source_ref),
        ],
        environment=environment,
        check=False,
    )
    assert wrong_org.returncode != 0
    assert wrong_org.stdout == ""
    assert wrong_org.stderr == "context-engine-control: operation refused\n"
    assert str(wrong_organization) not in wrong_org.stderr
    assert str(source_ref) not in wrong_org.stderr

    reused_proof_key = environment.copy()
    reused_proof_key["CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX"] = (
        reused_proof_key["CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX"]
    )
    refused_reuse = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=reused_proof_key,
        check=False,
    )
    assert refused_reuse.returncode != 0
    assert refused_reuse.stdout == ""
    assert refused_reuse.stderr == "context-engine-control: operation refused\n"

    reused_across_planes = environment.copy()
    reused_across_planes[CONTROL_OPERATOR_SECRET_ENV] = reused_across_planes[
        "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX"
    ]
    refused_cross_plane = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=reused_across_planes,
        check=False,
    )
    assert refused_cross_plane.returncode != 0
    assert refused_cross_plane.stdout == ""
    assert refused_cross_plane.stderr == "context-engine-control: operation refused\n"


def test_scan_process_refuses_a_v3_source_without_complete_baseline_carrier(
    file_scan_scenario: tuple[UUID, UUID, UUID, Path, dict[str, str]],
) -> None:
    organization_id, _membership_id, _receiver_id, root, environment = (
        file_scan_scenario
    )
    (root / "v3.md").write_text("# V3\n\nNot scan-active.\n", encoding="utf-8")
    source_ref = _register_change_feed_source(organization_id, environment)

    refused = _control(
        [
            "scan",
            "--organization-id",
            str(organization_id),
            "--source-ref",
            str(source_ref),
        ],
        environment=environment,
        check=False,
    )

    assert refused.returncode != 0
    assert refused.stdout == ""
    assert refused.stderr == "context-engine-control: operation refused\n"
