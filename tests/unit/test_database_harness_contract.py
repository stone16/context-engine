from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def repository_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_compose_pins_postgresql_17_pgvector_0_8_5_by_digest() -> None:
    compose = repository_text("compose.yaml")

    assert (
        "pgvector/pgvector:0.8.5-pg17-bookworm@"
        "sha256:d2ef61f42ef767baa5a1475393303cc235bcd92febd9d7014eddb48b41f3bad0"
    ) in compose
    assert "127.0.0.1:${CONTEXT_ENGINE_POSTGRES_PORT" in compose
    assert "./infra/postgres/init:/docker-entrypoint-initdb.d:ro" in compose


def test_bootstrap_owns_the_exact_extensions_required_by_migrations() -> None:
    bootstrap = repository_text("infra/postgres/init/10-security-roles.sh")
    provisioner = repository_text("scripts/provision_database_roles.py")

    assert "CREATE EXTENSION vector WITH SCHEMA public" in bootstrap
    assert "CREATE EXTENSION pgcrypto WITH SCHEMA public" in bootstrap
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public" in provisioner
    assert 'extension != ("public", contract.bootstrap_role)' in provisioner


def test_compose_project_identity_is_generated_per_checkout() -> None:
    compose = repository_text("compose.yaml")
    script = repository_text("scripts/database_harness.sh")

    assert "name: context-engine-dev" not in compose
    assert 'readonly COMPOSE_PROJECT="context-engine-dev"' not in script
    assert "CONTEXT_ENGINE_COMPOSE_PROJECT" in script
    assert '--project-name "$COMPOSE_PROJECT"' in script


@pytest.mark.parametrize(
    "sql_variable",
    [
        "migrator_role",
        "control_role",
        "runtime_role",
        "worker_role",
        "learning_role",
        "security_operator_role",
    ],
)
def test_role_bootstrap_keeps_each_login_nonsuperuser_nobypass_noinherit(
    sql_variable: str,
) -> None:
    bootstrap = repository_text("infra/postgres/init/10-security-roles.sh")
    declaration = bootstrap.split(f'CREATE ROLE :"{sql_variable}"', maxsplit=1)[1]
    declaration = declaration.split(";", maxsplit=1)[0]

    assert "LOGIN" in declaration
    assert "NOSUPERUSER" in declaration
    assert "NOBYPASSRLS" in declaration
    assert "NOINHERIT" in declaration


def test_database_harness_generates_secret_state_and_never_sources_it() -> None:
    script = repository_text("scripts/database_harness.sh")

    assert "umask 077" in script
    assert 'chmod 600 "$temporary_file"' in script
    assert 'source "$ENV_FILE"' not in script
    assert 'ln "$temporary_file" "$ENV_FILE"' in script
    assert 'mv "$temporary_file" "$ENV_FILE"' not in script
    assert "unexpected variable" in script
    assert "role-isolated URL contract" in script
    assert "CONTEXT_ENGINE_CONTROL_ROLE=context_engine_control" in script
    assert "CONTEXT_ENGINE_CONTROL_DATABASE_URL" in script
    assert (
        "CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE=context_engine_security_operator"
        in script
    )
    assert "CONTEXT_ENGINE_SECURITY_OPERATOR_DATABASE_URL" in script
    assert script.count("\n  generate_environment\n") == 1


def test_compose_passes_dedicated_operator_credentials_to_bootstrap() -> None:
    compose = repository_text("compose.yaml")

    assert "CONTEXT_ENGINE_CONTROL_ROLE" in compose
    assert "CONTEXT_ENGINE_CONTROL_PASSWORD" in compose
    assert "CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE" in compose
    assert "CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD" in compose
    assert "CONTEXT_ENGINE_LEARNING_ROLE" in compose
    assert "CONTEXT_ENGINE_LEARNING_PASSWORD" in compose


def test_readiness_probe_includes_dedicated_operator_configuration() -> None:
    wait_script = repository_text("scripts/wait_for_database.py")

    assert "configurations.control" in wait_script
    assert "configurations.learning" in wait_script
    assert "configurations.operator" in wait_script
    assert (
        "                    if configuration.purpose is "
        "DatabasePurpose.SECURITY_OPERATOR:"
    ) in wait_script
    assert "                        assert_security_operator_role(connection)" in (
        wait_script
    )
    assert "                        assert_learning_role(connection)" in wait_script
    assert "migration, control, runtime, worker, learning, security-operator" in (
        wait_script
    )


def test_harness_provisions_post_init_roles_before_readiness() -> None:
    harness = repository_text("scripts/database_harness.sh")
    provisioner = repository_text("scripts/provision_database_roles.py")
    configuration = repository_text("engine/persistence/configuration.py")

    assert harness.count("  provision_database_roles\n  wait_for_database") == 3
    integration_body = harness.split("run_integration() {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]
    assert (
        "  load_environment\n"
        "  compose up --detach --wait\n"
        "  provision_database_roles\n"
        "  wait_for_database\n" in integration_body
    )
    assert (
        'CONTEXT_RUN_READER_DEFINER_ROLE = "context_engine_context_run_reader_definer"'
    ) in configuration
    assert "ACCESS_POLICY_DEFINER_ROLE" in provisioner
    assert "WORKER_LEASE_DEFINER_ROLE" in provisioner
    assert "CONTEXT_RUN_READER_DEFINER_ROLE" in provisioner
    assert "contract.context_run_reader_definer_role" in provisioner
    assert "RELEASE_DEFINER_ROLE" in provisioner
    assert "contract.release_definer_role" in provisioner
    assert "LEARNING_ROLE" in provisioner
    assert "contract.learning_role" in provisioner
    assert "OPERATOR_ROLE" in provisioner
    assert "NOLOGIN NOSUPERUSER" in provisioner
    assert "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE" in provisioner
    assert "database security-role provisioning failed" in provisioner


@pytest.mark.parametrize(
    ("catalog_attribute", "guard_alias"),
    [
        ("rolcreaterole", "can_create_roles"),
        ("rolcreatedb", "can_create_databases"),
        ("rolreplication", "can_replicate"),
    ],
)
def test_runtime_role_guard_checks_every_role_escalation_attribute(
    catalog_attribute: str, guard_alias: str
) -> None:
    guard = repository_text("engine/persistence/role_guard.py")

    assert f"role.{catalog_attribute} AS {guard_alias}" in guard
    assert f'"{guard_alias}": False' in guard


def test_runtime_role_guard_rejects_every_membership() -> None:
    guard = repository_text("engine/persistence/role_guard.py")

    assert "FROM pg_auth_members AS membership" in guard
    assert "membership.member = role.oid" in guard
    assert "AS has_no_role_memberships" in guard
    assert '"has_no_role_memberships": True' in guard


def test_role_guard_rejects_any_database_object_ownership() -> None:
    guard = repository_text("engine/persistence/role_guard.py")

    assert "FROM pg_shdepend AS dependency" in guard
    assert "dependency.deptype = 'o'" in guard
    assert "AS owns_no_database_objects" in guard
    assert "tuple(operator_facts) != (True, True)" in guard
    assert "assert_learning_role" in guard
    learning_guard = guard.split("def assert_learning_role", maxsplit=1)[1]
    assert "_assert_no_owned_objects_or_role_members(connection)" in learning_guard


def test_ci_runs_the_same_make_database_contract_as_local() -> None:
    workflow = repository_text(".github/workflows/ci.yml")
    makefile = repository_text("Makefile")

    assert "make db-up" in workflow
    assert "make check" in workflow
    assert "if: always()" in workflow
    assert "make db-down" in workflow
    assert "check: build lint typecheck test catalog smoke integration" in makefile
    assert "./scripts/database_harness.sh integration" in makefile
