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
        "runtime_role",
        "worker_role",
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


def test_runtime_role_guard_rejects_every_set_capable_membership() -> None:
    guard = repository_text("engine/persistence/role_guard.py")

    assert "FROM pg_auth_members AS membership" in guard
    assert "membership.member = role.oid" in guard
    assert "membership.set_option" in guard
    assert "AS has_no_set_capable_memberships" in guard
    assert '"has_no_set_capable_memberships": True' in guard


def test_ci_runs_the_same_make_database_contract_as_local() -> None:
    workflow = repository_text(".github/workflows/ci.yml")
    makefile = repository_text("Makefile")

    assert "make db-up" in workflow
    assert "make check" in workflow
    assert "if: always()" in workflow
    assert "make db-down" in workflow
    assert "check: build lint typecheck test catalog smoke integration" in makefile
    assert "./scripts/database_harness.sh integration" in makefile
