"""One-shot orchestration for the complete M0 security veto."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy import text as sql_text

from engine.persistence import load_harness_database_configurations
from scripts.security_gate.artifacts import atomic_write_json
from scripts.security_gate.pytest_plugin import (
    RAW_EVIDENCE_VERSION,
    RAW_EXECUTION_ID_ENVIRONMENT,
)
from scripts.security_gate.report import (
    build_release_gate_report,
    canonical_digest,
    reconcile_execution,
)
from scripts.security_gate.rls import audit_live_rls
from scripts.validate_security_catalog import (
    DEFAULT_CATALOG_PATH,
    DEFAULT_SCHEMA_PATH,
    REPOSITORY_ROOT,
    CatalogValidationError,
    load_document,
    validate_catalog,
)

DEFAULT_REGISTRY_PATH = REPOSITORY_ROOT / "eval/catalogs/m0-security-evidence.yaml"
DEFAULT_REGISTRY_SCHEMA_PATH = (
    REPOSITORY_ROOT / "eval/catalogs/m0-security-evidence.schema.json"
)
DEFAULT_MANIFEST_PATH = (
    REPOSITORY_ROOT / "engine/persistence/schema_security_manifest.yaml"
)
DEFAULT_DATABASE_ENVIRONMENT_PATH = REPOSITORY_ROOT / ".context-engine/database.env"
RAW_ARTIFACT_NAME = "raw-evidence.json"
REPORT_ARTIFACT_NAME = "release-gate-report.json"
RUNNER_VERSION = "1.0.0"
_PRIVATE_ARTIFACT_PREFIXES = (".context-engine/", ".harness/")
_GIT_EXCLUDE_PATHS = (
    ":(exclude).context-engine/**",
    ":(exclude).harness/**",
)
_AMBIENT_PYTEST_CONTROL_KEYS = ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")
_ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ALLOWED_DATABASE_ENVIRONMENT_KEYS = frozenset(
    {
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "CONTEXT_ENGINE_POSTGRES_PORT",
        "CONTEXT_ENGINE_COMPOSE_PROJECT",
        "CONTEXT_ENGINE_MIGRATOR_ROLE",
        "CONTEXT_ENGINE_MIGRATOR_PASSWORD",
        "CONTEXT_ENGINE_CONTROL_ROLE",
        "CONTEXT_ENGINE_CONTROL_PASSWORD",
        "CONTEXT_ENGINE_IDENTITY_ROLE",
        "CONTEXT_ENGINE_IDENTITY_PASSWORD",
        "CONTEXT_ENGINE_RUNTIME_ROLE",
        "CONTEXT_ENGINE_RUNTIME_PASSWORD",
        "CONTEXT_ENGINE_WORKER_ROLE",
        "CONTEXT_ENGINE_WORKER_PASSWORD",
        "CONTEXT_ENGINE_LEARNING_ROLE",
        "CONTEXT_ENGINE_LEARNING_PASSWORD",
        "CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE",
        "CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD",
        "CONTEXT_ENGINE_MIGRATION_DATABASE_URL",
        "CONTEXT_ENGINE_CONTROL_DATABASE_URL",
        "CONTEXT_ENGINE_IDENTITY_DATABASE_URL",
        "CONTEXT_ENGINE_RUNTIME_DATABASE_URL",
        "CONTEXT_ENGINE_WORKER_DATABASE_URL",
        "CONTEXT_ENGINE_LEARNING_DATABASE_URL",
        "CONTEXT_ENGINE_SECURITY_OPERATOR_DATABASE_URL",
        "CONTEXT_ENGINE_TEST_DATABASE_URL",
    }
)


class DatabaseEnvironmentError(ValueError):
    """The private database contract cannot safely be loaded."""


class GateRunError(RuntimeError):
    """The M0 gate could not complete or did not satisfy Security."""


class RegistryValidator(Protocol):
    def __call__(
        self,
        registry: Mapping[str, Any],
        schema: Mapping[str, Any],
        catalog: Mapping[str, Any],
        *,
        repository_root: str | Path = ...,
    ) -> object: ...


class PytestExecutor(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> int: ...


class RlsAuditor(Protocol):
    def __call__(
        self,
        *,
        environment: Mapping[str, str],
        manifest: Mapping[str, object],
        passed_evidence_ids: Sequence[str],
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class GatePaths:
    repository_root: Path
    catalog: Path
    catalog_schema: Path
    registry: Path
    registry_schema: Path
    manifest: Path
    database_environment: Path
    output_directory: Path

    @classmethod
    def defaults(cls, output_directory: Path) -> GatePaths:
        return cls(
            repository_root=REPOSITORY_ROOT,
            catalog=DEFAULT_CATALOG_PATH,
            catalog_schema=DEFAULT_SCHEMA_PATH,
            registry=DEFAULT_REGISTRY_PATH,
            registry_schema=DEFAULT_REGISTRY_SCHEMA_PATH,
            manifest=DEFAULT_MANIFEST_PATH,
            database_environment=DEFAULT_DATABASE_ENVIRONMENT_PATH,
            output_directory=output_directory,
        )


def load_database_environment(path: Path) -> dict[str, str]:
    """Parse a strict generated KEY=value file without shell evaluation."""

    try:
        metadata = path.lstat()
    except OSError as error:
        raise DatabaseEnvironmentError(
            "database environment is unavailable; run the database harness first"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DatabaseEnvironmentError(
            "database environment must be a regular non-symbolic-link file"
        )
    if metadata.st_uid != os.getuid():
        raise DatabaseEnvironmentError(
            "database environment must be owned by the current user"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise DatabaseEnvironmentError("database environment permissions must be 0600")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise DatabaseEnvironmentError("database environment cannot be read") from error
    environment: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line or "=" not in line:
            raise DatabaseEnvironmentError(
                f"database environment line {line_number} must be KEY=value"
            )
        key, value = line.split("=", 1)
        if _ENVIRONMENT_KEY.fullmatch(key) is None:
            raise DatabaseEnvironmentError(
                f"database environment line {line_number} must be KEY=value"
            )
        if key not in _ALLOWED_DATABASE_ENVIRONMENT_KEYS:
            raise DatabaseEnvironmentError(
                f"database environment contains unexpected variable {key!r}"
            )
        if key in environment:
            raise DatabaseEnvironmentError(
                f"database environment contains duplicate variable {key!r}"
            )
        if not value or "\x00" in value or "\n" in value or "\r" in value:
            raise DatabaseEnvironmentError(
                f"database environment variable {key!r} has an invalid value"
            )
        environment[key] = value
    missing = sorted(_ALLOWED_DATABASE_ENVIRONMENT_KEYS - environment.keys())
    if missing:
        raise DatabaseEnvironmentError(
            "database environment is missing required variables: " + ", ".join(missing)
        )
    load_harness_database_configurations(environment)
    return environment


def _mapping_sequence(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def deduplicate_selectors(registry: Mapping[str, object]) -> tuple[str, ...]:
    """Return the registry's exact selectors once, retaining declared order."""

    selectors: list[str] = []
    for evidence in _mapping_sequence(registry.get("evidence")):
        selector = evidence.get("selector")
        if isinstance(selector, str) and selector not in selectors:
            selectors.append(selector)
    return tuple(selectors)


def build_pytest_command(
    selectors: Sequence[str], *, raw_path: Path, python_executable: str = sys.executable
) -> tuple[str, ...]:
    """Build the one and only exact pytest invocation; no retry options exist."""

    return (
        python_executable,
        "-m",
        "pytest",
        "-p",
        "scripts.security_gate.pytest_plugin",
        "--security-gate-raw",
        str(raw_path),
        "--strict-markers",
        "--strict-config",
        *selectors,
    )


def _execute_pytest(
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str]
) -> int:
    return subprocess.run(command, cwd=cwd, env=env, check=False).returncode


def _audit_rls(
    *,
    environment: Mapping[str, str],
    manifest: Mapping[str, object],
    passed_evidence_ids: Sequence[str],
) -> Mapping[str, object]:
    configurations = load_harness_database_configurations(environment)
    engine = create_engine(configurations.security_test.url)
    try:
        with engine.connect() as connection:
            return audit_live_rls(connection, manifest, passed_evidence_ids)
    finally:
        engine.dispose()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "unavailable"
    return result.stdout.strip()


def _git_bytes(repository_root: Path, arguments: Sequence[str]) -> bytes | None:
    result = subprocess.run(
        ["git", "-C", str(repository_root), *arguments],
        check=False,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def _untracked_state(repository_root: Path) -> tuple[str, int] | None:
    listed = _git_bytes(
        repository_root,
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    if listed is None:
        return None
    paths = sorted(path for path in listed.split(b"\0") if path)
    digest = hashlib.sha256()
    count = 0
    for encoded_path in paths:
        try:
            relative = encoded_path.decode("utf-8", errors="surrogateescape")
            path = repository_root / relative
            metadata = path.lstat()
        except OSError:
            return None
        normalized = relative.replace(os.sep, "/")
        if normalized.startswith(_PRIVATE_ARTIFACT_PREFIXES):
            continue
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return None
        content_digest = _file_digest(path)
        digest.update(encoded_path)
        digest.update(b"\0")
        digest.update(content_digest.encode("ascii"))
        digest.update(b"\0")
        count += 1
    return digest.hexdigest(), count


def _git_state(repository_root: Path) -> dict[str, object]:
    commit = _git_commit(repository_root)
    staged = _git_bytes(
        repository_root,
        ("diff", "--cached", "--binary", "--", ".", *_GIT_EXCLUDE_PATHS),
    )
    unstaged = _git_bytes(
        repository_root,
        ("diff", "--binary", "--", ".", *_GIT_EXCLUDE_PATHS),
    )
    untracked = _untracked_state(repository_root)
    if staged is None or unstaged is None or untracked is None:
        return {
            "commit": commit,
            "trackedDirty": "unavailable",
            "trackedDiffDigest": "unavailable",
            "stagedDiffDigest": "unavailable",
            "unstagedDiffDigest": "unavailable",
            "untrackedContentDigest": "unavailable",
            "untrackedFileCount": "unavailable",
            "contentStateDirty": "unavailable",
            "contentStateDigest": "unavailable",
        }
    untracked_digest, untracked_count = untracked
    staged_digest = hashlib.sha256(staged).hexdigest()
    unstaged_digest = hashlib.sha256(unstaged).hexdigest()
    tracked_digest = canonical_digest(
        {"staged": staged_digest, "unstaged": unstaged_digest}
    )
    content_digest = canonical_digest(
        {
            "commit": commit,
            "stagedDiffDigest": staged_digest,
            "unstagedDiffDigest": unstaged_digest,
            "untrackedContentDigest": untracked_digest,
            "untrackedFileCount": untracked_count,
        }
    )
    return {
        "commit": commit,
        "trackedDirty": bool(staged or unstaged),
        "trackedDiffDigest": tracked_digest,
        "stagedDiffDigest": staged_digest,
        "unstagedDiffDigest": unstaged_digest,
        "untrackedContentDigest": untracked_digest,
        "untrackedFileCount": untracked_count,
        "contentStateDirty": bool(staged or unstaged or untracked_count),
        "contentStateDigest": content_digest,
    }


def _alembic_head(paths: GatePaths) -> str:
    configuration = Config(str(paths.repository_root / "alembic.ini"))
    configuration.set_main_option(
        "script_location", str(paths.repository_root / "migrations")
    )
    head = ScriptDirectory.from_config(configuration).get_current_head()
    if head is None:
        raise GateRunError("Alembic migration history has no unique current head")
    return head


def _live_database_revision(environment: Mapping[str, str]) -> str:
    configurations = load_harness_database_configurations(environment)
    engine = create_engine(configurations.migration.url)
    try:
        with engine.connect() as connection:
            values = connection.execute(
                sql_text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).scalars().all()
    finally:
        engine.dispose()
    if len(values) != 1 or not isinstance(values[0], str):
        raise GateRunError("live database must expose one exact Alembic revision")
    return values[0]


def _provenance(
    paths: GatePaths,
    registry: Mapping[str, object],
    catalog: Mapping[str, object],
    *,
    selectors: Sequence[str],
    live_database_revision: str,
    alembic_head: str | None = None,
) -> dict[str, object]:
    migration_files = sorted(
        (paths.repository_root / "migrations/versions").glob("*.py")
    )
    migration_state = {
        str(path.relative_to(paths.repository_root)): _file_digest(path)
        for path in migration_files
    }
    compose_digest = _file_digest(paths.repository_root / "compose.yaml")
    alembic_config_digest = _file_digest(paths.repository_root / "alembic.ini")
    registry_digest = canonical_digest(registry)
    manifest_digest = _file_digest(paths.manifest)
    configuration_digest = canonical_digest(
        {
            "composeDigest": compose_digest,
            "alembicConfigDigest": alembic_config_digest,
            "executionRegistryDigest": registry_digest,
            "schemaManifestDigest": manifest_digest,
        }
    )
    fixtures = catalog.get("fixtures", [])
    return {
        "runnerVersion": RUNNER_VERSION,
        **_git_state(paths.repository_root),
        "executionCommand": list(
            build_pytest_command(
                selectors,
                raw_path=paths.output_directory / RAW_ARTIFACT_NAME,
            )
        ),
        "catalogDigest": _file_digest(paths.catalog),
        "fixtureDigest": canonical_digest(fixtures),
        "catalogSchemaDigest": _file_digest(paths.catalog_schema),
        "executionRegistryDigest": registry_digest,
        "executionRegistrySchemaDigest": _file_digest(paths.registry_schema),
        "schemaManifestDigest": manifest_digest,
        "migrationStateDigest": canonical_digest(migration_state),
        "alembicHead": alembic_head or _alembic_head(paths),
        "liveDatabaseRevision": live_database_revision,
        "composeDigest": compose_digest,
        "alembicConfigDigest": alembic_config_digest,
        "configurationDigest": configuration_digest,
    }


def _best_effort_provenance(
    paths: GatePaths,
    *,
    registry: Mapping[str, object] | None = None,
    catalog: Mapping[str, object] | None = None,
    selectors: Sequence[str] = (),
    alembic_head: str | None = None,
    live_database_revision: str | None = None,
) -> dict[str, object]:
    """Retain only safe aggregate provenance facts available at failure time."""

    provenance: dict[str, object] = {"runnerVersion": RUNNER_VERSION}
    try:
        provenance.update(_git_state(paths.repository_root))
    except OSError:
        provenance.update(
            {
                "commit": "unavailable",
                "contentStateDirty": "unavailable",
                "contentStateDigest": "unavailable",
            }
        )
    public_files = {
        "catalogDigest": paths.catalog,
        "catalogSchemaDigest": paths.catalog_schema,
        "executionRegistrySchemaDigest": paths.registry_schema,
        "schemaManifestDigest": paths.manifest,
        "composeDigest": paths.repository_root / "compose.yaml",
        "alembicConfigDigest": paths.repository_root / "alembic.ini",
    }
    for field, path in public_files.items():
        try:
            provenance[field] = _file_digest(path)
        except OSError:
            continue
    if registry is not None:
        provenance["executionRegistryDigest"] = canonical_digest(registry)
    else:
        with suppress(OSError):
            provenance["executionRegistryFileDigest"] = _file_digest(paths.registry)
    if catalog is not None:
        provenance["fixtureDigest"] = canonical_digest(catalog.get("fixtures", []))
    try:
        migration_files = sorted(
            (paths.repository_root / "migrations/versions").glob("*.py")
        )
        migration_state = {
            str(path.relative_to(paths.repository_root)): _file_digest(path)
            for path in migration_files
        }
        if migration_files:
            provenance["migrationStateDigest"] = canonical_digest(migration_state)
    except OSError:
        pass
    resolved_head = alembic_head
    if resolved_head is None:
        try:
            resolved_head = _alembic_head(paths)
        except Exception:
            resolved_head = None
    if resolved_head is not None:
        provenance["alembicHead"] = resolved_head
    if live_database_revision is not None:
        provenance["liveDatabaseRevision"] = live_database_revision
    if selectors:
        provenance["executionCommand"] = list(
            build_pytest_command(
                selectors,
                raw_path=paths.output_directory / RAW_ARTIFACT_NAME,
            )
        )
    configuration_fields = {
        field: provenance[field]
        for field in (
            "composeDigest",
            "alembicConfigDigest",
            "executionRegistryDigest",
            "schemaManifestDigest",
        )
        if field in provenance
    }
    if configuration_fields:
        provenance["configurationDigest"] = canonical_digest(configuration_fields)
    return provenance


def _registry_validator() -> RegistryValidator:
    from scripts import validate_security_catalog as validator

    candidate = getattr(validator, "validate_execution_registry", None)
    if candidate is None or not callable(candidate):
        raise GateRunError("execution registry validator is unavailable")
    return cast(RegistryValidator, candidate)


def _load_gate_inputs(
    paths: GatePaths, registry_validator: RegistryValidator | None
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    catalog = load_document(paths.catalog)
    catalog_schema = load_document(paths.catalog_schema)
    registry = load_document(paths.registry)
    registry_schema = load_document(paths.registry_schema)
    manifest = load_document(paths.manifest)
    validate_catalog(catalog, catalog_schema)
    selected_validator = registry_validator or _registry_validator()
    selected_validator(
        registry,
        registry_schema,
        catalog,
        repository_root=paths.repository_root,
    )
    return catalog, catalog_schema, registry, registry_schema, manifest


def _validate_raw_execution(
    raw: Mapping[str, object],
    *,
    runner_execution_id: str,
    selectors: Sequence[str],
    exit_code: int,
) -> None:
    """Reject stale, runner-authored, or structurally spoofed raw evidence."""

    if raw.get("rawEvidenceVersion") != RAW_EVIDENCE_VERSION:
        raise GateRunError("pytest raw evidence version is missing or unsupported")
    if raw.get("rawProducer") != "scripts.security_gate.pytest_plugin":
        raise GateRunError("pytest raw evidence producer is not the gate plugin")
    if raw.get("runnerExecutionId") != runner_execution_id:
        raise GateRunError("pytest raw evidence is stale or belongs to another run")
    pytest_section = raw.get("pytest")
    if not isinstance(pytest_section, Mapping):
        raise GateRunError("raw evidence has no pytest result object")
    if pytest_section.get("exitCode") != exit_code:
        raise GateRunError("pytest raw exit code differs from the executed process")
    selected = pytest_section.get("selectedSelectors")
    if not isinstance(selected, list) or selected != list(selectors):
        raise GateRunError("pytest raw selectors differ from the executed command")


def _failure_report(
    failure_name: str,
    *,
    provenance: Mapping[str, object],
    raw_result_digest: str,
) -> dict[str, object]:
    return {
        "reportVersion": "1.0.0",
        "m0SecurityDecision": "fail",
        "releaseDecision": "fail",
        "promotionReadiness": "not-evaluated",
        "provenance": {
            **dict(provenance),
            "rawResultDigest": raw_result_digest,
        },
        "gates": {
            "Security": {
                "status": "fail",
                "veto": True,
                "failures": [failure_name],
            },
            "Reliability": {"status": "not-evaluated"},
            "Quality": {"status": "not-evaluated"},
            "Budget": {"status": "not-evaluated"},
        },
    }


def run_gate(
    paths: GatePaths,
    *,
    pytest_executor: PytestExecutor = _execute_pytest,
    rls_auditor: RlsAuditor = _audit_rls,
    registry_validator: RegistryValidator | None = None,
) -> dict[str, object]:
    """Execute registered tests once, reconcile observations, audit RLS, report."""

    paths.output_directory.mkdir(parents=True, exist_ok=True)
    raw_path = paths.output_directory / RAW_ARTIFACT_NAME
    report_path = paths.output_directory / REPORT_ARTIFACT_NAME
    catalog: dict[str, Any] | None = None
    registry: dict[str, Any] | None = None
    selectors: tuple[str, ...] = ()
    alembic_head: str | None = None
    live_database_revision: str | None = None
    raw: dict[str, object] = {
        "rawEvidenceVersion": RAW_EVIDENCE_VERSION,
        "runnerFailure": "gate did not reach pytest execution",
        "pytest": {
            "exitCode": None,
            "selectedSelectors": [],
            "collectedNodeIds": [],
            "collectionErrors": [],
            "tests": [],
        },
    }
    pytest_execution_retained = False
    atomic_write_json(raw_path, raw)
    try:
        (
            catalog,
            _catalog_schema,
            registry,
            _registry_schema,
            manifest,
        ) = _load_gate_inputs(paths, registry_validator)
        environment = load_database_environment(paths.database_environment)
        selectors = deduplicate_selectors(registry)
        if not selectors:
            raise GateRunError("execution registry contains no selectors")
        alembic_head = _alembic_head(paths)
        live_database_revision = _live_database_revision(environment)
        if live_database_revision != alembic_head:
            raise GateRunError(
                "live Alembic revision does not match the repository head: "
                f"{live_database_revision!r} != {alembic_head!r}"
            )
        runner_execution_id = secrets.token_hex(32)
        command = build_pytest_command(selectors, raw_path=raw_path)
        process_environment = dict(os.environ)
        for key in _AMBIENT_PYTEST_CONTROL_KEYS:
            process_environment.pop(key, None)
        process_environment.update(environment)
        process_environment[RAW_EXECUTION_ID_ENVIRONMENT] = runner_execution_id
        with suppress(FileNotFoundError):
            raw_path.unlink()
        exit_code = pytest_executor(
            command, cwd=paths.repository_root, env=process_environment
        )
        try:
            raw = load_document(raw_path)
        except (CatalogValidationError, OSError) as error:
            raise GateRunError("pytest did not produce valid raw evidence") from error
        _validate_raw_execution(
            raw,
            runner_execution_id=runner_execution_id,
            selectors=selectors,
            exit_code=exit_code,
        )
        pytest_execution_retained = True

        reconciliation = reconcile_execution(registry, raw, catalog=catalog)
        passed_ids = reconciliation.get("passedEvidenceIds", [])
        passed_evidence_ids = [
            value for value in passed_ids if isinstance(value, str)
        ] if isinstance(passed_ids, list) else []
        rls_audit = rls_auditor(
            environment=environment,
            manifest=manifest,
            passed_evidence_ids=passed_evidence_ids,
        )
        raw_result_digest = _file_digest(raw_path)
        report = build_release_gate_report(
            reconciliation=reconciliation,
            rls_audit=rls_audit,
            provenance=_provenance(
                paths,
                registry,
                catalog,
                selectors=selectors,
                live_database_revision=live_database_revision,
                alembic_head=alembic_head,
            ),
            raw_result_digest=raw_result_digest,
        )
        atomic_write_json(report_path, report)
        return report
    except Exception as error:
        failure_name = type(error).__name__
        if pytest_execution_retained:
            retained_raw = {**raw, "runnerFailure": failure_name}
        else:
            retained_raw = {
                "rawEvidenceVersion": RAW_EVIDENCE_VERSION,
                "runnerFailure": failure_name,
                "pytest": {
                    "exitCode": None,
                    "selectedSelectors": list(selectors),
                    "collectedNodeIds": [],
                    "collectionErrors": [],
                    "tests": [],
                },
            }
        atomic_write_json(raw_path, retained_raw)
        failure_report = _failure_report(
            failure_name,
            provenance=_best_effort_provenance(
                paths,
                registry=registry,
                catalog=catalog,
                selectors=selectors,
                alembic_head=alembic_head,
                live_database_revision=live_database_revision,
            ),
            raw_result_digest=_file_digest(raw_path),
        )
        atomic_write_json(report_path, failure_report)
        raise
