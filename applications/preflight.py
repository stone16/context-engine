"""Read-only, content-free local daily-driver readiness preflight."""

from __future__ import annotations

import argparse
import ast
import hmac
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Never
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import ArgumentError, OperationalError, SQLAlchemyError

SCHEMA_VERSION = "context-engine-preflight-v1"
SERVICE = "context-engine-control-preflight"
PLANES = ("migration", "control", "supply", "release", "runtime", "caller")
_HEX_KEY = re.compile(r"[0-9a-fA-F]{64}")
_CONTROL_OPERATIONS = frozenset(
    {
        "register_source",
        "read_source",
        "read_source_progress",
        "activate_file_change_feed",
        "activate_file_delete_observations",
        "accept_file_change_page",
        "schedule_file_change_page",
    }
)

_DATABASE_PURPOSES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "migration": (
        (
            "CONTEXT_ENGINE_MIGRATOR_ROLE",
            "CONTEXT_ENGINE_MIGRATION_DATABASE_URL",
            "context_engine_migrator",
        ),
    ),
    "control": (
        (
            "CONTEXT_ENGINE_CONTROL_ROLE",
            "CONTEXT_ENGINE_CONTROL_DATABASE_URL",
            "context_engine_control",
        ),
    ),
    "supply": (
        (
            "CONTEXT_ENGINE_SCHEDULER_ROLE",
            "CONTEXT_ENGINE_SCHEDULER_DATABASE_URL",
            "context_engine_scheduler",
        ),
        (
            "CONTEXT_ENGINE_WORKER_ROLE",
            "CONTEXT_ENGINE_WORKER_DATABASE_URL",
            "context_engine_worker",
        ),
    ),
    "release": (
        (
            "CONTEXT_ENGINE_LEARNING_ROLE",
            "CONTEXT_ENGINE_LEARNING_DATABASE_URL",
            "context_engine_learning",
        ),
        (
            "CONTEXT_ENGINE_RELEASE_OPERATOR_ROLE",
            "CONTEXT_ENGINE_RELEASE_OPERATOR_DATABASE_URL",
            "context_engine_release_operator",
        ),
    ),
    "runtime": (
        (
            "CONTEXT_ENGINE_RUNTIME_ROLE",
            "CONTEXT_ENGINE_RUNTIME_DATABASE_URL",
            "context_engine_runtime",
        ),
    ),
    "caller": (),
}
_PLANE_ENVIRONMENT_NAMES: dict[str, frozenset[str]] = {
    "migration": frozenset(
        {"CONTEXT_ENGINE_MIGRATOR_ROLE", "CONTEXT_ENGINE_MIGRATION_DATABASE_URL"}
    ),
    "control": frozenset(
        {
            "CONTEXT_ENGINE_CONTROL_ROLE",
            "CONTEXT_ENGINE_CONTROL_DATABASE_URL",
            "CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID",
            "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET",
            "CONTEXT_ENGINE_CONTROL_OPERATOR_OPERATIONS",
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON",
            "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID",
            "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF",
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID",
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION",
            "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX",
        }
    ),
    "supply": frozenset(
        {
            "CONTEXT_ENGINE_SCHEDULER_ROLE",
            "CONTEXT_ENGINE_SCHEDULER_DATABASE_URL",
            "CONTEXT_ENGINE_WORKER_ROLE",
            "CONTEXT_ENGINE_WORKER_DATABASE_URL",
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON",
            "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR",
        }
    ),
    "release": frozenset(
        {
            "CONTEXT_ENGINE_LEARNING_ROLE",
            "CONTEXT_ENGINE_LEARNING_DATABASE_URL",
            "CONTEXT_ENGINE_RELEASE_OPERATOR_ROLE",
            "CONTEXT_ENGINE_RELEASE_OPERATOR_DATABASE_URL",
            "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET",
            "CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_VERSION",
            "CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_HEX",
        }
    ),
    "runtime": frozenset(
        {
            "CONTEXT_ENGINE_RUNTIME_ROLE",
            "CONTEXT_ENGINE_RUNTIME_DATABASE_URL",
            "CONTEXT_ENGINE_API_COMPOSITION",
            "CONTEXT_ENGINE_DOGFOOD_SECRET",
            "CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID",
            "CONTEXT_ENGINE_DOGFOOD_USER_ID",
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID",
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION",
            "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF",
            "CONTEXT_ENGINE_DOGFOOD_AGENT_VERSION_REF",
            "CONTEXT_ENGINE_DOGFOOD_APPLICATION_REF",
            "CONTEXT_ENGINE_DOGFOOD_AUTHENTICATION_BINDING_REF",
            "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_PROVIDER",
            "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_MODEL_DIR",
        }
    ),
    "caller": frozenset({"CONTEXT_ENGINE_DOGFOOD_SECRET"}),
}
REQUIRED_ENVIRONMENT_NAMES = frozenset().union(*_PLANE_ENVIRONMENT_NAMES.values())


class PreflightConfigurationMissing(ValueError):
    """One or more selected names are absent."""

    def __init__(self) -> None:
        super().__init__("preflight configuration missing")


class PreflightConfigurationMalformed(ValueError):
    """Selected configuration is present but unsafe or inconsistent."""

    def __init__(self) -> None:
        super().__init__("preflight configuration malformed")


@dataclass(frozen=True, slots=True)
class LocalPreflightConfiguration:
    """Validated deployment facts without constructed operational authority."""

    selected_planes: frozenset[str]
    environment: Mapping[str, str] = field(repr=False)
    database_urls: Mapping[str, URL] = field(repr=False)
    model_dir: Path | None = field(repr=False)
    identity: Mapping[str, object] = field(repr=False)

    def __repr__(self) -> str:
        return "LocalPreflightConfiguration(<redacted>)"


def _selected_planes(raw_planes: Sequence[str] | None) -> frozenset[str]:
    if raw_planes is None:
        return frozenset(PLANES)
    if (
        not raw_planes
        or any(plane not in PLANES for plane in raw_planes)
        or len(set(raw_planes)) != len(raw_planes)
    ):
        raise PreflightConfigurationMalformed
    return frozenset(raw_planes)


def _required_names(selected: frozenset[str]) -> frozenset[str]:
    return frozenset().union(*(_PLANE_ENVIRONMENT_NAMES[plane] for plane in selected))


def _nonempty(environment: Mapping[str, str], name: str) -> str:
    value = environment[name]
    if type(value) is not str or not value or value != value.strip():
        raise PreflightConfigurationMalformed
    return value


def _uuid(environment: Mapping[str, str], name: str) -> UUID:
    try:
        return UUID(_nonempty(environment, name))
    except (TypeError, ValueError):
        raise PreflightConfigurationMalformed from None


def _positive_int(environment: Mapping[str, str], name: str) -> int:
    raw = _nonempty(environment, name)
    if not raw.isdecimal():
        raise PreflightConfigurationMalformed
    value = int(raw)
    if not 1 <= value < (1 << 63):
        raise PreflightConfigurationMalformed
    return value


def _secret(environment: Mapping[str, str], name: str) -> bytes:
    raw = _nonempty(environment, name)
    if len(raw.encode("utf-8")) < 32 or any(character.isspace() for character in raw):
        raise PreflightConfigurationMalformed
    return raw.encode("utf-8")


def _key(environment: Mapping[str, str], name: str) -> bytes:
    raw = _nonempty(environment, name)
    if _HEX_KEY.fullmatch(raw) is None:
        raise PreflightConfigurationMalformed
    return bytes.fromhex(raw)


def _validate_json_roots(environment: Mapping[str, str]) -> None:
    try:
        document = json.loads(
            _nonempty(environment, "CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON")
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        raise PreflightConfigurationMalformed from None
    if (
        type(document) is not dict
        or not document
        or any(
            type(name) is not str or not name or type(path) is not str or not path
            for name, path in document.items()
        )
    ):
        raise PreflightConfigurationMalformed


def _database_url(
    environment: Mapping[str, str],
    role_name: str,
    url_name: str,
    expected_role: str,
) -> URL:
    if _nonempty(environment, role_name) != expected_role:
        raise PreflightConfigurationMalformed
    try:
        url = make_url(_nonempty(environment, url_name))
    except ArgumentError:
        raise PreflightConfigurationMalformed from None
    if (
        url.drivername != "postgresql+psycopg"
        or url.query
        or url.username != expected_role
        or url.password is None
        or not str(url.password)
        or not url.host
        or not url.database
    ):
        raise PreflightConfigurationMalformed
    return url


def load_preflight_configuration(
    environment: Mapping[str, str],
    *,
    selected_planes: Sequence[str] | None = None,
) -> LocalPreflightConfiguration:
    """Validate names and cross-plane bindings without opening I/O capabilities."""

    try:
        selected = _selected_planes(selected_planes)
        required = _required_names(selected)
        if any(name not in environment for name in required):
            raise PreflightConfigurationMissing
        for name in required:
            _nonempty(environment, name)

        database_urls: dict[str, URL] = {}
        for plane in selected:
            for role_name, url_name, expected_role in _DATABASE_PURPOSES[plane]:
                database_urls[url_name] = _database_url(
                    environment, role_name, url_name, expected_role
                )

        if "control" in selected:
            _uuid(environment, "CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID")
            _uuid(environment, "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID")
            _uuid(environment, "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID")
            _positive_int(environment, "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION")
            operations = _nonempty(
                environment, "CONTEXT_ENGINE_CONTROL_OPERATOR_OPERATIONS"
            ).split(",")
            if frozenset(operations) != _CONTROL_OPERATIONS or len(operations) != len(
                _CONTROL_OPERATIONS
            ):
                raise PreflightConfigurationMalformed
            _validate_json_roots(environment)

        if "runtime" in selected:
            if (
                _nonempty(environment, "CONTEXT_ENGINE_API_COMPOSITION")
                != "dogfood-local-v1"
            ):
                raise PreflightConfigurationMalformed
            for name in (
                "CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID",
                "CONTEXT_ENGINE_DOGFOOD_USER_ID",
                "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID",
            ):
                _uuid(environment, name)
            _positive_int(environment, "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION")
            if (
                _nonempty(environment, "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_PROVIDER")
                != "qwen3-embedding-0.6b-local-v1"
            ):
                raise PreflightConfigurationMalformed

        if "supply" in selected:
            if (
                _nonempty(environment, "CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER")
                != "qwen-local"
                or _positive_int(
                    environment, "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION"
                )
                != 384
            ):
                raise PreflightConfigurationMalformed
            _uuid(environment, "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID")
            _validate_json_roots(environment)

        model_dir: Path | None = None
        if selected & {"supply", "runtime"}:
            worker_model = environment.get("CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR")
            runtime_model = environment.get(
                "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_MODEL_DIR"
            )
            if selected >= {"supply", "runtime"} and worker_model != runtime_model:
                raise PreflightConfigurationMalformed
            model_dir = Path(
                _nonempty(
                    environment,
                    "CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR"
                    if "supply" in selected
                    else "CONTEXT_ENGINE_DOGFOOD_EMBEDDING_MODEL_DIR",
                )
            )

        secret_names = required & {
            "CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET",
            "CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET",
            "CONTEXT_ENGINE_DOGFOOD_SECRET",
        }
        key_names = required & {
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_HEX",
        }
        secrets = [_secret(environment, name) for name in sorted(secret_names)]
        secrets.extend(_key(environment, name) for name in sorted(key_names))
        for index, secret in enumerate(secrets):
            if any(
                hmac.compare_digest(secret, other) for other in secrets[index + 1 :]
            ):
                raise PreflightConfigurationMalformed

        if selected >= {"control", "runtime"} and _uuid(
            environment, "CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID"
        ) != _uuid(environment, "CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID"):
            raise PreflightConfigurationMalformed

        if "release" in selected:
            _positive_int(
                environment, "CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_VERSION"
            )
        return LocalPreflightConfiguration(
            selected_planes=selected,
            environment=dict(environment),
            database_urls=database_urls,
            model_dir=model_dir,
            identity=(
                {
                    "organization_id": _uuid(
                        environment, "CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID"
                    ),
                    "user_id": _uuid(environment, "CONTEXT_ENGINE_DOGFOOD_USER_ID"),
                    "membership_id": _uuid(
                        environment, "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID"
                    ),
                    "membership_version": _positive_int(
                        environment, "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION"
                    ),
                    "principal_ref": _nonempty(
                        environment, "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF"
                    ),
                    "authentication_binding_ref": _nonempty(
                        environment,
                        "CONTEXT_ENGINE_DOGFOOD_AUTHENTICATION_BINDING_REF",
                    ),
                }
                if "runtime" in selected
                else {}
            ),
        )
    except PreflightConfigurationMissing:
        raise
    except PreflightConfigurationMalformed:
        raise
    except Exception:
        raise PreflightConfigurationMalformed from None


def verify_registered_qwen_artifacts(model_dir: Path) -> None:
    from adapters.local_embedding_model import (
        verify_registered_qwen_artifacts as verify,
    )

    verify(model_dir)


def packaged_schema_head() -> str:
    """Resolve the unique packaged migration head without importing Alembic."""

    revisions: set[str] = set()
    parents: set[str] = set()
    try:
        versions = files("migrations").joinpath("versions")
        for resource in versions.iterdir():
            if not resource.name.endswith(".py") or resource.name.startswith("__"):
                continue
            document = ast.parse(resource.read_text(encoding="utf-8"))
            revision: object = None
            down_revision: object = None
            for node in document.body:
                if not isinstance(node, ast.Assign | ast.AnnAssign):
                    continue
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                value = node.value
                for target in targets:
                    if not isinstance(target, ast.Name) or value is None:
                        continue
                    if target.id == "revision":
                        revision = ast.literal_eval(value)
                    elif target.id == "down_revision":
                        down_revision = ast.literal_eval(value)
            if type(revision) is not str or not revision:
                raise ValueError
            revisions.add(revision)
            if type(down_revision) is str:
                parents.add(down_revision)
            elif isinstance(down_revision, tuple | list):
                parent_revisions = list(down_revision)
                if not parent_revisions or any(
                    type(item) is not str for item in parent_revisions
                ):
                    raise ValueError
                parents.update(parent_revisions)
            elif down_revision is not None:
                raise ValueError
        heads = revisions - parents
        if len(heads) != 1 or not parents <= revisions:
            raise ValueError
        return heads.pop()
    except (OSError, SyntaxError, TypeError, ValueError):
        raise RuntimeError("packaged schema graph unavailable") from None


def _create_preflight_engine(url: URL) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def probe_schema_readiness(configuration: LocalPreflightConfiguration) -> str:
    """Observe the Alembic head through a verified read-only migrator transaction."""

    url = configuration.database_urls.get("CONTEXT_ENGINE_MIGRATION_DATABASE_URL")
    if url is None:
        return "not_selected"
    try:
        expected_head = packaged_schema_head()
        engine = _create_preflight_engine(url)
    except (OSError, RuntimeError, SQLAlchemyError):
        return "schema_unreachable"
    try:
        with engine.connect() as raw_connection:
            connection = raw_connection.execution_options(
                isolation_level="READ COMMITTED",
                postgresql_readonly=True,
            )
            with connection.begin():
                if (
                    connection.execute(text("SHOW transaction_read_only")).scalar_one()
                    != "on"
                ):
                    return "schema_probe_refused"
                role = connection.execute(
                    text(
                        """
                        SELECT current_user, session_user, rolsuper, rolbypassrls
                        FROM pg_roles
                        WHERE rolname = current_user
                        """
                    )
                ).one()
                if tuple(role) != (
                    "context_engine_migrator",
                    "context_engine_migrator",
                    False,
                    False,
                ):
                    return "schema_probe_refused"
                observed = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                if type(observed) is not str:
                    return "schema_probe_refused"
                return "ready" if observed == expected_head else "schema_not_at_head"
    except OperationalError:
        return "schema_unreachable"
    except SQLAlchemyError:
        return "schema_probe_refused"
    except Exception:
        return "schema_probe_refused"
    finally:
        engine.dispose()


def probe_model_readiness(configuration: LocalPreflightConfiguration) -> str:
    """Verify registered model bytes without backend load or inference."""

    if configuration.model_dir is None:
        return "not_selected"
    try:
        from adapters import local_embedding_model

        artifacts = local_embedding_model._registered_qwen_artifacts()
    except Exception:
        return "model_manifest_unavailable"
    try:
        local_embedding_model.verify_model_artifacts(
            configuration.model_dir,
            artifacts,
            local_embedding_model.QWEN3_EMBEDDING_PROFILE.artifact_digest,
        )
        return "ready"
    except Exception:
        return "model_artifacts_invalid"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _expected_runtime_role_facts() -> tuple[object, ...]:
    return (
        "context_engine_runtime",
        "context_engine_runtime",
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        False,
        False,
        True,
        True,
        True,
        False,
        False,
        False,
    )


def _expected_qwen_release_bindings() -> dict[str, object]:
    from engine.release_profiles import expected_qwen_release_bindings

    return expected_qwen_release_bindings()


_RUNTIME_ROLE_FACTS = text(
    """
    SELECT current_user, session_user, role.rolsuper, role.rolbypassrls,
           role.rolinherit, role.rolcreaterole, role.rolcreatedb,
           role.rolreplication,
           NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = role.oid),
           pg_has_role(current_user, 'context_engine_migrator', 'MEMBER'),
           pg_has_role(current_user, 'context_engine_migrator', 'USAGE'),
           pg_get_userbyid(database.datdba) <> current_user,
           pg_get_userbyid(namespace.nspowner) <> current_user,
           NOT EXISTS (
               SELECT 1 FROM pg_class relation
               JOIN pg_namespace relation_namespace
                 ON relation_namespace.oid = relation.relnamespace
               WHERE relation_namespace.nspname = 'public'
                 AND relation.relkind IN ('r','p','v','m','S','f')
                 AND relation.relowner = role.oid
           ) AS owns_no_public_relations,
           has_database_privilege(current_user, current_database(), 'CREATE'),
           has_database_privilege(current_user, current_database(), 'TEMPORARY'),
           has_schema_privilege(current_user, 'public', 'CREATE')
    FROM pg_roles role
    JOIN pg_database database ON database.datname = current_database()
    JOIN pg_namespace namespace ON namespace.nspname = 'public'
    WHERE role.rolname = current_user
    """
)


_ACTIVE_RELEASE = text(
    """
    SELECT manifest.organization_id, active.active_generation,
           manifest.content_profile_ref,
           manifest.runtime_content_profile_digest AS content_profile_digest,
           manifest.content_schema_ref, manifest.index_profile_ref,
           manifest.runtime_index_profile_digest AS index_profile_digest,
           manifest.index_schema_ref, manifest.embedding_profile_document,
           manifest.embedding_profile_digest, manifest.runtime_profile_ref,
           manifest.runtime_profile_digest, manifest.runtime_tokenizer_ref,
           manifest.runtime_tokenizer_profile_document,
           manifest.runtime_tokenizer_profile_digest,
           manifest.runtime_package_schema_ref, manifest.curation_profile_ref,
           manifest.curation_profile_digest, manifest.curation_mode,
           manifest.curation_snapshot_ref, manifest.curation_evaluation_digest,
           manifest.compatible_revision_refs, manifest.active_revision_refs
    FROM active_release_manifest active
    JOIN release_manifest manifest
      ON manifest.organization_id = active.organization_id
     AND manifest.manifest_ref = active.manifest_ref
     AND manifest.manifest_digest = active.manifest_digest
    WHERE active.organization_id = :organization_id
    """
)


def probe_release_readiness(configuration: LocalPreflightConfiguration) -> str:
    """Observe current UserActor and active Release in one read-only Runtime session."""

    url = configuration.database_urls.get("CONTEXT_ENGINE_RUNTIME_DATABASE_URL")
    if url is None:
        return "not_selected"
    try:
        engine = _create_preflight_engine(url)
    except SQLAlchemyError:
        return "runtime_unreachable"
    identity = configuration.identity
    checked_at = _utc_now()
    settings = {
        "app.actor_kind": "user",
        "app.authentication_binding_ref": identity["authentication_binding_ref"],
        "app.checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "app.membership_id": str(identity["membership_id"]),
        "app.membership_version": str(identity["membership_version"]),
        "app.organization_id": str(identity["organization_id"]),
        "app.principal_ref": identity["principal_ref"],
        "app.request_id": "context-engine-preflight",
        "app.user_id": str(identity["user_id"]),
    }
    try:
        with engine.connect() as raw_connection:
            connection = raw_connection.execution_options(
                isolation_level="READ COMMITTED", postgresql_readonly=True
            )
            with connection.begin():
                if (
                    connection.execute(text("SHOW transaction_read_only")).scalar_one()
                    != "on"
                ):
                    return "runtime_probe_refused"
                if (
                    tuple(connection.execute(_RUNTIME_ROLE_FACTS).one())
                    != _expected_runtime_role_facts()
                ):
                    return "runtime_probe_refused"
                for setting_name, setting_value in settings.items():
                    observed = connection.execute(
                        text("SELECT set_config(:setting_name, :setting_value, true)"),
                        {"setting_name": setting_name, "setting_value": setting_value},
                    ).scalar_one()
                    if observed != setting_value:
                        return "runtime_probe_refused"
                member = connection.execute(
                    text(
                        """
                        SELECT user_id FROM membership
                        WHERE organization_id = :organization_id
                          AND membership_id = :membership_id
                          AND user_id = :user_id
                          AND membership_version = :membership_version
                          AND status = 'active'
                          AND valid_from <= :checked_at
                          AND (valid_until IS NULL OR :checked_at < valid_until)
                        """
                    ),
                    {**identity, "checked_at": checked_at},
                ).one_or_none()
                if member is None or member[0] != identity["user_id"]:
                    return "runtime_identity_not_current"
                row = (
                    connection.execute(
                        _ACTIVE_RELEASE,
                        {"organization_id": identity["organization_id"]},
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    return "active_release_absent"
                observed = dict(row)
                active_revisions = observed.pop("active_revision_refs", None)
                if (
                    observed.pop("organization_id", None) != identity["organization_id"]
                    or type(observed.pop("active_generation", None)) is not int
                    or type(active_revisions) is not list
                    or not active_revisions
                    or any(type(ref) is not str or not ref for ref in active_revisions)
                    or observed != _expected_qwen_release_bindings()
                ):
                    return "active_release_incompatible"
                return "ready"
    except OperationalError:
        return "runtime_unreachable"
    except SQLAlchemyError:
        return "runtime_probe_refused"
    except Exception:
        return "runtime_probe_refused"
    finally:
        engine.dispose()


@dataclass(frozen=True, slots=True)
class PreflightResult:
    exit_code: int
    rendered: str


Probe = Callable[[LocalPreflightConfiguration], str]


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        self.exit(2, "context-engine-control: preflight refused\n")


def _row(check: str, status: str, category: str) -> dict[str, str]:
    return {"check": check, "status": status, "category": category}


def _document(checks: list[dict[str, str]]) -> str:
    status = (
        "ready"
        if all(
            row["status"] == "ready"
            or (row["status"] == "not_run" and row["category"] == "not_selected")
            for row in checks
        )
        else "not_ready"
    )
    return json.dumps(
        {
            "schemaVersion": SCHEMA_VERSION,
            "service": SERVICE,
            "status": status,
            "checks": checks,
        },
        separators=(",", ":"),
    )


def run_preflight(
    environment: Mapping[str, str],
    *,
    selected_planes: Sequence[str] | None,
    schema_probe: Probe,
    model_probe: Probe,
    release_probe: Probe,
) -> PreflightResult:
    try:
        configuration = load_preflight_configuration(
            environment, selected_planes=selected_planes
        )
    except PreflightConfigurationMissing:
        checks = [_row("configuration", "failed", "configuration_missing")]
        checks.extend(
            _row(check, "not_run", "dependency_not_ready")
            for check in ("schema", "model", "release")
        )
        return PreflightResult(10, _document(checks))
    except PreflightConfigurationMalformed:
        checks = [_row("configuration", "failed", "configuration_malformed")]
        checks.extend(
            _row(check, "not_run", "dependency_not_ready")
            for check in ("schema", "model", "release")
        )
        return PreflightResult(10, _document(checks))

    categories: list[str] = []
    for probe, refusal in (
        (schema_probe, "schema_probe_refused"),
        (model_probe, "model_artifacts_invalid"),
        (release_probe, "runtime_probe_refused"),
    ):
        try:
            category = probe(configuration)
        except Exception:
            category = refusal
        categories.append(category)
    checks = [_row("configuration", "ready", "ready")]
    checks.extend(
        _row(
            check,
            "ready"
            if category == "ready"
            else "not_run"
            if category == "not_selected"
            else "failed",
            category,
        )
        for check, category in zip(
            ("schema", "model", "release"), categories, strict=True
        )
    )
    exit_code = 0
    for code, category in zip((11, 12, 13), categories, strict=True):
        if category not in {"ready", "not_selected"}:
            exit_code = code
            break
    return PreflightResult(exit_code, _document(checks))


def _parser() -> argparse.ArgumentParser:
    parser = _PrivateArgumentParser(
        prog="context-engine-control preflight",
        description="report content-free readiness for bounded local planes",
    )
    parser.add_argument(
        "--plane",
        action="append",
        choices=PLANES,
        help="select a local plane; repeat to select more (default: all)",
    )
    return parser


def _refused_probe(_configuration: LocalPreflightConfiguration) -> str:
    return "runtime_probe_refused"


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    arguments = _parser().parse_args(argv)
    source = os.environ if environment is None else environment
    result = run_preflight(
        source,
        selected_planes=arguments.plane,
        schema_probe=probe_schema_readiness,
        model_probe=probe_model_readiness,
        release_probe=probe_release_readiness,
    )
    print(result.rendered, flush=True)
    raise SystemExit(result.exit_code)


__all__ = [
    "LocalPreflightConfiguration",
    "PLANES",
    "PreflightConfigurationMalformed",
    "PreflightConfigurationMissing",
    "PreflightResult",
    "REQUIRED_ENVIRONMENT_NAMES",
    "load_preflight_configuration",
    "main",
    "packaged_schema_head",
    "probe_model_readiness",
    "probe_release_readiness",
    "probe_schema_readiness",
    "run_preflight",
    "verify_registered_qwen_artifacts",
]
