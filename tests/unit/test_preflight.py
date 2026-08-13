from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

import adapters.local_embedding_model as local_model
from applications import preflight
from engine.database_roles import (
    CONTROL_ROLE,
    LEARNING_ROLE,
    MIGRATOR_ROLE,
    RELEASE_OPERATOR_ROLE,
    RUNTIME_ROLE,
    SCHEDULER_ROLE,
    WORKER_ROLE,
    expected_database_role_facts,
)


def valid_environment() -> dict[str, str]:
    roles = {
        "MIGRATOR": "context_engine_migrator",
        "CONTROL": "context_engine_control",
        "SCHEDULER": "context_engine_scheduler",
        "WORKER": "context_engine_worker",
        "LEARNING": "context_engine_learning",
        "RELEASE_OPERATOR": "context_engine_release_operator",
        "RUNTIME": "context_engine_runtime",
    }
    environment: dict[str, str] = {}
    for ordinal, (name, role) in enumerate(roles.items(), start=1):
        environment[f"CONTEXT_ENGINE_{name}_ROLE"] = role
        purpose = "MIGRATION" if name == "MIGRATOR" else name
        environment[f"CONTEXT_ENGINE_{purpose}_DATABASE_URL"] = (
            f"postgresql+psycopg://{role}:password-{ordinal}@127.0.0.1/context_engine"
        )
    environment.update(
        {
            "CONTEXT_ENGINE_API_COMPOSITION": "dogfood-local-v1",
            "CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID": (
                "10000000-0000-4000-8000-000000000001"
            ),
            "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET": "c" * 32,
            "CONTEXT_ENGINE_CONTROL_OPERATOR_OPERATIONS": (
                "register_source,read_source,read_source_progress,"
                "activate_file_change_feed,activate_file_delete_observations,"
                "accept_file_change_page,schedule_file_change_page"
            ),
            "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET": "r" * 32,
            "CONTEXT_ENGINE_DOGFOOD_BASE_URL": "http://127.0.0.1:8000",
            "CONTEXT_ENGINE_DOGFOOD_SECRET": "d" * 32,
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX": "11" * 32,
            "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX": "22" * 32,
            "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX": "33" * 32,
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON": (
                '{"maintainer-notes":"/private/maintainer-notes"}'
            ),
            "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID": (
                "20000000-0000-4000-8000-000000000001"
            ),
            "CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID": (
                "10000000-0000-4000-8000-000000000001"
            ),
            "CONTEXT_ENGINE_DOGFOOD_USER_ID": ("30000000-0000-4000-8000-000000000001"),
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID": (
                "40000000-0000-4000-8000-000000000001"
            ),
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION": "1",
            "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF": "principal:dogfood:v1",
            "CONTEXT_ENGINE_DOGFOOD_AGENT_VERSION_REF": "agent-version:dogfood:v1",
            "CONTEXT_ENGINE_DOGFOOD_APPLICATION_REF": "application:dogfood:v1",
            "CONTEXT_ENGINE_DOGFOOD_AUTHENTICATION_BINDING_REF": ("binding:dogfood:v1"),
            "CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER": "qwen-local",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION": "384",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR": "/private/models/qwen",
            "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_PROVIDER": (
                "qwen3-embedding-0.6b-local-v1"
            ),
            "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_MODEL_DIR": "/private/models/qwen",
            "CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_VERSION": "1",
            "CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_HEX": "44" * 32,
        }
    )
    return environment


def test_configuration_inventory_accepts_exact_bounded_composition() -> None:
    configuration = preflight.load_preflight_configuration(valid_environment())

    assert configuration.selected_planes == frozenset(preflight.PLANES)
    assert not hasattr(configuration, "environment")
    assert repr(configuration) == "LocalPreflightConfiguration(<redacted>)"


def test_database_purpose_inventory_uses_the_authoritative_role_registry() -> None:
    assert {
        plane: tuple(purpose[2] for purpose in purposes)
        for plane, purposes in preflight._DATABASE_PURPOSES.items()
    } == {
        "migration": (MIGRATOR_ROLE,),
        "control": (CONTROL_ROLE,),
        "supply": (SCHEDULER_ROLE, WORKER_ROLE),
        "release": (LEARNING_ROLE, RELEASE_OPERATOR_ROLE),
        "runtime": (RUNTIME_ROLE,),
        "caller": (),
    }


def test_preflight_database_engine_has_a_server_owned_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    sentinel = object()

    def create_engine(url: object, **kwargs: object) -> object:
        observed["url"] = url
        observed.update(kwargs)
        return sentinel

    monkeypatch.setattr(preflight, "create_engine", create_engine)
    url = make_url(
        "postgresql+psycopg://context_engine_migrator:secret@db/context_engine"
    )

    assert preflight._create_preflight_engine(url) is sentinel
    assert observed == {
        "url": url,
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": 5},
    }


@pytest.mark.parametrize("missing", sorted(preflight.REQUIRED_ENVIRONMENT_NAMES))
def test_configuration_inventory_classifies_every_missing_name(missing: str) -> None:
    environment = valid_environment()
    environment.pop(missing)

    with pytest.raises(preflight.PreflightConfigurationMissing):
        preflight.load_preflight_configuration(environment)


@pytest.mark.parametrize(
    ("name", "unsafe"),
    [
        ("CONTEXT_ENGINE_API_COMPOSITION", "production"),
        ("CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET", "short"),
        ("CONTEXT_ENGINE_DOGFOOD_SECRET", "c" * 32),
        ("CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX", "not-hex"),
        ("CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID", "not-a-uuid"),
        ("CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION", "0"),
        ("CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER", "twin"),
        ("CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION", "1024"),
        ("CONTEXT_ENGINE_DOGFOOD_EMBEDDING_PROVIDER", "network"),
        ("CONTEXT_ENGINE_DOGFOOD_EMBEDDING_MODEL_DIR", "/different/model"),
        ("CONTEXT_ENGINE_DOGFOOD_BASE_URL", "https://private.example:8443"),
        ("CONTEXT_ENGINE_DOGFOOD_BASE_URL", "http://127.0.0.1:8000//"),
        ("CONTEXT_ENGINE_MIGRATOR_ROLE", "context_engine_runtime"),
        (
            "CONTEXT_ENGINE_MIGRATION_DATABASE_URL",
            "postgresql+psycopg://context_engine_runtime:secret@db/context_engine",
        ),
    ],
)
def test_configuration_inventory_classifies_unsafe_values(
    name: str,
    unsafe: str,
) -> None:
    environment = valid_environment()
    environment[name] = unsafe

    with pytest.raises(preflight.PreflightConfigurationMalformed) as failure:
        preflight.load_preflight_configuration(environment)

    assert unsafe not in str(failure.value)


def test_closed_plane_selection_requires_only_selected_plane_names() -> None:
    environment = {
        "CONTEXT_ENGINE_MIGRATOR_ROLE": "context_engine_migrator",
        "CONTEXT_ENGINE_MIGRATION_DATABASE_URL": (
            "postgresql+psycopg://context_engine_migrator:secret@db/context_engine"
        ),
    }

    configuration = preflight.load_preflight_configuration(
        environment,
        selected_planes=("migration",),
    )

    assert configuration.selected_planes == frozenset({"migration"})
    with pytest.raises(preflight.PreflightConfigurationMalformed):
        preflight.load_preflight_configuration(
            environment,
            selected_planes=("migration", "migration"),
        )


def test_configuration_loader_uses_diagnostics_as_its_validation_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = {
        "name": "CONTEXT_ENGINE_MIGRATOR_ROLE",
        "category": "configuration_malformed",
    }
    monkeypatch.setattr(
        preflight,
        "_configuration_failures",
        lambda _environment, _selected: (failure,),
    )

    with pytest.raises(preflight.PreflightConfigurationMalformed):
        preflight.load_preflight_configuration(
            valid_environment(),
            selected_planes=("migration",),
        )


@pytest.mark.parametrize(
    "selected_planes",
    ((), ("unknown",), ("migration", "migration")),
    ids=("empty", "unknown", "duplicate"),
)
def test_invalid_plane_selection_is_non_variable_configuration_failure(
    selected_planes: tuple[str, ...],
) -> None:
    result = preflight.run_preflight(
        valid_environment(),
        selected_planes=selected_planes,
        schema_probe=lambda _configuration: "ready",
        database_probe=lambda _configuration, _role: "ready",
        model_probe=lambda _configuration: "ready",
        release_probe=lambda _configuration: "ready",
        caller_probe=lambda _configuration: "ready",
    )

    assert result.exit_code == 10
    document = json.loads(result.rendered)
    assert document["checks"][0] == {
        "check": "configuration",
        "status": "failed",
        "category": "configuration_malformed",
    }
    assert all(
        row["status"] == "not_run" and row["category"] == "dependency_not_ready"
        for row in document["checks"][1:]
    )


def test_environment_name_template_mechanically_matches_inventory() -> None:
    template = Path("deploy/local-preflight.env.example").read_text(encoding="utf-8")
    rows = template.splitlines()

    assert rows == [f"{name}=" for name in sorted(preflight.ENVIRONMENT_TEMPLATE_NAMES)]
    assert all(row.endswith("=") for row in rows)
    assert all(row.count("=") == 1 for row in rows)


def test_configuration_diagnostics_name_every_failure_and_probe_valid_planes() -> None:
    environment = valid_environment()
    environment.pop("CONTEXT_ENGINE_CONTROL_ROLE")
    environment.pop("CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET")
    environment["CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION"] = "1024"
    calls: list[str] = []

    def observe(name: str) -> str:
        calls.append(name)
        return "ready"

    result = preflight.run_preflight(
        environment,
        selected_planes=("migration", "control", "supply"),
        schema_probe=lambda _configuration: observe("migration"),
        database_probe=lambda _configuration, role: observe(role),
        model_probe=lambda _configuration: observe("model"),
        release_probe=lambda _configuration: observe("release"),
        caller_probe=lambda _configuration: observe("caller"),
    )

    assert result.exit_code == 10
    document = json.loads(result.rendered)
    assert document["checks"][0] == {
        "check": "configuration",
        "status": "failed",
        "category": "configuration_missing",
        "failures": [
            {
                "name": "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET",
                "category": "configuration_missing",
            },
            {
                "name": "CONTEXT_ENGINE_CONTROL_ROLE",
                "category": "configuration_missing",
            },
            {
                "name": "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION",
                "category": "configuration_malformed",
            },
        ],
    }
    assert document["checks"][1] == {
        "check": "migration_schema",
        "status": "ready",
        "category": "ready",
    }
    assert all(
        row["status"] == "not_run" and row["category"] == "dependency_not_ready"
        for row in document["checks"][2:6]
    )
    assert calls == ["migration"]


def test_model_probe_uses_only_public_registered_model_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = preflight.load_preflight_configuration(
        valid_environment(), selected_planes=("supply",)
    )
    calls: list[str] = []

    def registered_snapshot() -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
        calls.append("manifest")
        return (
            "Qwen/Qwen3-Embedding-0.6B",
            "0" * 40,
            "1" * 64,
            (("model.safetensors", "2" * 64),),
        )

    def verify_registered(_path: Path) -> None:
        calls.append("artifacts")

    monkeypatch.setattr(
        local_model,
        "registered_qwen_snapshot_contract",
        registered_snapshot,
    )
    monkeypatch.setattr(
        local_model,
        "verify_registered_qwen_artifacts",
        verify_registered,
    )
    monkeypatch.setattr(
        local_model,
        "_registered_qwen_artifacts",
        lambda: (_ for _ in ()).throw(AssertionError("private helper used")),
    )

    assert preflight.probe_model_readiness(configuration) == "ready"
    assert calls == ["manifest", "artifacts"]


def test_model_probe_classifies_an_invalid_registered_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = preflight.load_preflight_configuration(
        valid_environment(), selected_planes=("supply",)
    )
    invalid_registry = tmp_path / "model-registry.json"
    invalid_registry.write_text('{"schemaVersion":"wrong"}', encoding="utf-8")
    monkeypatch.setattr(local_model, "MODEL_REGISTRY_PATH", invalid_registry)

    assert preflight.probe_model_readiness(configuration) == "model_manifest_invalid"


def test_model_probe_classifies_an_unavailable_registered_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = preflight.load_preflight_configuration(
        valid_environment(), selected_planes=("supply",)
    )
    monkeypatch.setattr(
        local_model,
        "MODEL_REGISTRY_PATH",
        tmp_path / "missing-model-registry.json",
    )

    assert (
        preflight.probe_model_readiness(configuration)
        == "model_manifest_unavailable"
    )


def test_model_probe_classifies_unavailable_registered_artifacts(
    tmp_path: Path,
) -> None:
    environment = valid_environment()
    environment["CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR"] = str(
        tmp_path / "missing-model"
    )
    configuration = preflight.load_preflight_configuration(
        environment, selected_planes=("supply",)
    )

    assert (
        preflight.probe_model_readiness(configuration)
        == "model_artifacts_unavailable"
    )


def test_model_probe_classifies_invalid_registered_artifacts(tmp_path: Path) -> None:
    model_dir = tmp_path / "invalid-model"
    model_dir.mkdir()
    environment = valid_environment()
    environment["CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR"] = str(model_dir)
    configuration = preflight.load_preflight_configuration(
        environment, selected_planes=("supply",)
    )

    assert preflight.probe_model_readiness(configuration) == "model_artifacts_invalid"


def test_orchestrator_reports_all_independent_failures_and_earliest_exit() -> None:
    environment = valid_environment()

    result = preflight.run_preflight(
        environment,
        selected_planes=preflight.PLANES,
        schema_probe=lambda _configuration: "schema_not_at_head",
        database_probe=lambda _configuration, _role: "database_unavailable",
        model_probe=lambda _configuration: "model_artifacts_invalid",
        release_probe=lambda _configuration: "active_release_absent",
        caller_probe=lambda _configuration: "ready",
    )

    assert result.exit_code == 11
    assert json.loads(result.rendered)["checks"] == [
        {"check": "configuration", "status": "ready", "category": "ready"},
        {
            "check": "migration_schema",
            "status": "failed",
            "category": "schema_not_at_head",
        },
        {
            "check": "control_database",
            "status": "failed",
            "category": "database_unavailable",
        },
        {
            "check": "supply_scheduler_database",
            "status": "failed",
            "category": "database_unavailable",
        },
        {
            "check": "supply_worker_database",
            "status": "failed",
            "category": "database_unavailable",
        },
        {
            "check": "supply_model",
            "status": "failed",
            "category": "model_artifacts_invalid",
        },
        {
            "check": "release_learning_database",
            "status": "failed",
            "category": "database_unavailable",
        },
        {
            "check": "release_operator_database",
            "status": "failed",
            "category": "database_unavailable",
        },
        {
            "check": "runtime_release",
            "status": "failed",
            "category": "active_release_absent",
        },
        {
            "check": "runtime_model",
            "status": "failed",
            "category": "model_artifacts_invalid",
        },
        {"check": "caller_configuration", "status": "ready", "category": "ready"},
    ]


def test_selected_planes_execute_every_applicable_observation() -> None:
    calls: list[str] = []

    def ready_database(
        _configuration: preflight.LocalPreflightConfiguration,
        role: str,
    ) -> str:
        calls.append(role)
        return "ready"

    def ready_caller(_configuration: preflight.LocalPreflightConfiguration) -> str:
        calls.append("caller")
        return "ready"

    result = preflight.run_preflight(
        valid_environment(),
        selected_planes=("control", "release", "caller"),
        schema_probe=lambda _configuration: (_ for _ in ()).throw(
            AssertionError("unselected migration probe ran")
        ),
        database_probe=ready_database,
        model_probe=lambda _configuration: (_ for _ in ()).throw(
            AssertionError("unselected model probe ran")
        ),
        release_probe=lambda _configuration: (_ for _ in ()).throw(
            AssertionError("unselected Runtime probe ran")
        ),
        caller_probe=ready_caller,
    )

    assert result.exit_code == 0
    assert calls == ["control", "learning", "release_operator", "caller"]
    document = json.loads(result.rendered)
    assert document["status"] == "ready"
    assert document["checks"] == [
        {"check": "configuration", "status": "ready", "category": "ready"},
        {"check": "migration_schema", "status": "not_run", "category": "not_selected"},
        {"check": "control_database", "status": "ready", "category": "ready"},
        {
            "check": "supply_scheduler_database",
            "status": "not_run",
            "category": "not_selected",
        },
        {
            "check": "supply_worker_database",
            "status": "not_run",
            "category": "not_selected",
        },
        {"check": "supply_model", "status": "not_run", "category": "not_selected"},
        {"check": "release_learning_database", "status": "ready", "category": "ready"},
        {"check": "release_operator_database", "status": "ready", "category": "ready"},
        {"check": "runtime_release", "status": "not_run", "category": "not_selected"},
        {"check": "runtime_model", "status": "not_run", "category": "not_selected"},
        {"check": "caller_configuration", "status": "ready", "category": "ready"},
    ]


def test_schema_probe_uses_read_only_migration_session_and_closed_head_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = valid_environment()
    configuration = preflight.load_preflight_configuration(
        environment, selected_planes=("migration",)
    )
    events: list[object] = []

    class _Result:
        def __init__(self, value: object) -> None:
            self._value = value

        def one(self) -> object:
            return self._value

        def scalar_one(self) -> object:
            return self._value

    class _Transaction:
        def __enter__(self) -> None:
            events.append("begin")

        def __exit__(self, *_args: object) -> None:
            events.append("rollback")

    class _Connection:
        def __enter__(self) -> _Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def execution_options(self, **options: object) -> _Connection:
            events.append(options)
            return self

        def begin(self) -> _Transaction:
            return _Transaction()

        def execute(self, statement: object) -> _Result:
            sql = str(statement)
            events.append(sql)
            if "transaction_read_only" in sql:
                return _Result("on")
            if "current_user" in sql:
                return _Result(
                    ("context_engine_migrator", "context_engine_migrator", False, False)
                )
            return _Result("head")

    class _Engine:
        def connect(self) -> _Connection:
            return _Connection()

        def dispose(self) -> None:
            events.append("dispose")

    monkeypatch.setattr(
        preflight,
        "_create_preflight_engine",
        lambda _url: _Engine(),
    )
    monkeypatch.setattr(preflight, "packaged_schema_head", lambda: "head")

    assert preflight.probe_schema_readiness(configuration) == "ready"
    assert events[0] == {
        "isolation_level": "READ COMMITTED",
        "postgresql_readonly": True,
    }
    assert events.index("SHOW transaction_read_only") < next(
        index
        for index, event in enumerate(events)
        if "SELECT version_num" in str(event)
    )
    assert events[-2:] == ["rollback", "dispose"]


def test_database_probe_uses_every_selected_exact_role_in_read_only_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = preflight.load_preflight_configuration(
        valid_environment(), selected_planes=("control", "supply", "release")
    )
    expected_roles = {
        "control": "context_engine_control",
        "scheduler": "context_engine_scheduler",
        "worker": "context_engine_worker",
        "learning": "context_engine_learning",
        "release_operator": "context_engine_release_operator",
    }

    class _Result:
        def __init__(self, value: object) -> None:
            self._value = value

        def one(self) -> object:
            return self._value

        def scalar_one(self) -> object:
            return self._value

    class _Transaction:
        def __enter__(self) -> None:
            pass

        def __exit__(self, *_args: object) -> None:
            pass

    options_events: list[dict[str, object]] = []
    disposed_roles: list[str] = []

    class _Connection:
        def __init__(self, role: str) -> None:
            self._role = role

        def __enter__(self) -> _Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def execution_options(self, **options: object) -> _Connection:
            options_events.append(options)
            return self

        def begin(self) -> _Transaction:
            return _Transaction()

        def execute(self, _statement: object) -> _Result:
            return _Result("on")

    class _Engine:
        def __init__(self, role: str) -> None:
            self._role = role

        def connect(self) -> _Connection:
            return _Connection(self._role)

        def dispose(self) -> None:
            disposed_roles.append(self._role)

    by_url = {
        str(url): expected_roles[role]
        for role, url_name in preflight._ROLE_DATABASE_URLS.items()
        if (url := configuration.database_urls.get(url_name)) is not None
    }
    monkeypatch.setattr(
        preflight,
        "_create_preflight_engine",
        lambda url: _Engine(by_url[str(url)]),
    )
    monkeypatch.setattr(
        preflight,
        "observe_database_role_facts",
        lambda connection: expected_database_role_facts(connection._role),
    )
    monkeypatch.setattr(
        preflight,
        "observe_sensitive_database_role_facts",
        lambda _connection: (True, True),
    )

    assert {
        role: preflight.probe_database_readiness(configuration, role)
        for role in expected_roles
    } == {role: "ready" for role in expected_roles}
    assert all(
        options
        == {
            "isolation_level": "READ COMMITTED",
            "postgresql_readonly": True,
        }
        for options in options_events
    )
    assert set(disposed_roles) == set(expected_roles.values())


def test_model_probe_maps_registered_verifier_failure_without_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = preflight.load_preflight_configuration(
        valid_environment(), selected_planes=("supply",)
    )
    monkeypatch.setattr(
        local_model,
        "verify_registered_qwen_artifacts",
        lambda _path: (_ for _ in ()).throw(RuntimeError("/private/model/file")),
    )

    assert preflight.probe_model_readiness(configuration) == "model_artifacts_invalid"


def test_release_probe_binds_user_actor_in_read_only_runtime_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration = preflight.load_preflight_configuration(
        valid_environment(), selected_planes=("runtime",)
    )
    events: list[object] = []
    expected_release = preflight._expected_qwen_release_bindings()

    class _Result:
        def __init__(self, *, scalar: object = None, row: object = None) -> None:
            self._scalar = scalar
            self._row = row

        def scalar_one(self) -> object:
            return self._scalar

        def one(self) -> object:
            return self._row

        def one_or_none(self) -> object:
            return self._row

        def mappings(self) -> _Result:
            return self

    class _Transaction:
        def __enter__(self) -> None:
            events.append("begin")

        def __exit__(self, *_args: object) -> None:
            events.append("rollback")

    class _Connection:
        def __enter__(self) -> _Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def execution_options(self, **options: object) -> _Connection:
            events.append(options)
            return self

        def begin(self) -> _Transaction:
            return _Transaction()

        def execute(
            self,
            statement: object,
            parameters: object = None,
        ) -> _Result:
            sql = str(statement)
            events.append((sql, parameters))
            if "transaction_read_only" in sql:
                return _Result(scalar="on")
            if "owns_no_public_relations" in sql:
                return _Result(row=preflight._expected_runtime_role_facts())
            if "set_config" in sql:
                assert isinstance(parameters, dict)
                return _Result(scalar=parameters["setting_value"])
            if "FROM membership" in sql:
                return _Result(row=(configuration.identity["user_id"],))
            if "FROM active_release_manifest" in sql:
                return _Result(
                    row={
                        **expected_release,
                        "organization_id": configuration.identity["organization_id"],
                        "active_generation": 1,
                        "active_revision_refs": ["revision:one"],
                    }
                )
            return _Result(scalar=1)

    class _Engine:
        def connect(self) -> _Connection:
            return _Connection()

        def dispose(self) -> None:
            events.append("dispose")

    monkeypatch.setattr(
        preflight,
        "_create_preflight_engine",
        lambda _url: _Engine(),
    )
    monkeypatch.setattr(
        preflight,
        "observe_database_role_facts",
        lambda _connection: preflight._expected_runtime_role_facts(),
    )
    monkeypatch.setattr(
        preflight,
        "_utc_now",
        lambda: datetime(2026, 8, 13, 0, 0, tzinfo=UTC),
    )

    assert preflight.probe_release_readiness(configuration) == "ready"
    assert events[0] == {
        "isolation_level": "READ COMMITTED",
        "postgresql_readonly": True,
    }
    statements = [str(event[0]) for event in events if isinstance(event, tuple)]
    assert statements.index("SHOW transaction_read_only") < next(
        index
        for index, statement in enumerate(statements)
        if "FROM membership" in statement
    )
    assert events[-2:] == ["rollback", "dispose"]


def test_selected_migration_plane_marks_unselected_checks_without_failure() -> None:
    environment = {
        "CONTEXT_ENGINE_MIGRATOR_ROLE": "context_engine_migrator",
        "CONTEXT_ENGINE_MIGRATION_DATABASE_URL": (
            "postgresql+psycopg://context_engine_migrator:secret@db/context_engine"
        ),
    }

    result = preflight.run_preflight(
        environment,
        selected_planes=("migration",),
        schema_probe=lambda _configuration: "ready",
        database_probe=lambda _configuration, _plane: "not_selected",
        model_probe=lambda _configuration: "not_selected",
        release_probe=lambda _configuration: "not_selected",
        caller_probe=lambda _configuration: "not_selected",
    )

    assert result.exit_code == 0
    document = json.loads(result.rendered)
    assert document["status"] == "ready"
    assert document["checks"][1] == {
        "check": "migration_schema",
        "status": "ready",
        "category": "ready",
    }
    assert all(
        row["status"] == "not_run" and row["category"] == "not_selected"
        for row in document["checks"][2:]
    )


def test_probe_exception_text_never_reaches_any_output_channel(
    capsys: pytest.CaptureFixture[str],
) -> None:
    private = (
        "postgresql+psycopg://secret@private.example:5432/tenant "
        "/private/models/qwen model.safetensors "
        "10000000-0000-4000-8000-000000000001"
    )

    def noisy(_configuration: preflight.LocalPreflightConfiguration) -> str:
        raise RuntimeError(private)

    result = preflight.run_preflight(
        valid_environment(),
        selected_planes=preflight.PLANES,
        schema_probe=noisy,
        database_probe=lambda configuration, _plane: noisy(configuration),
        model_probe=noisy,
        release_probe=noisy,
        caller_probe=noisy,
    )
    print(result.rendered)
    captured = capsys.readouterr()

    assert result.exit_code == 11
    assert private not in captured.out + captured.err
    assert captured.err == ""
    assert [row["category"] for row in json.loads(captured.out)["checks"]] == [
        "ready",
        "schema_probe_refused",
        "database_probe_refused",
        "database_probe_refused",
        "database_probe_refused",
        "model_artifacts_invalid",
        "database_probe_refused",
        "database_probe_refused",
        "runtime_probe_refused",
        "model_artifacts_invalid",
        "caller_configuration_invalid",
    ]
