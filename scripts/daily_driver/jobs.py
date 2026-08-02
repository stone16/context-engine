"""Closed scheduled-job runner with durable failure visibility."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from adapters.http.dogfood import (
    DOGFOOD_CONTROL_ENVIRONMENT_VARIABLES,
    DOGFOOD_RUNTIME_ENVIRONMENT_VARIABLES,
)
from applications.file_root_configuration import (
    FILE_ROOT_ENVIRONMENT_VARIABLES,
    WORKER_FILE_ROOTS_ENV,
)
from applications.operator_authentication import (
    CONTROL_OPERATOR_SECRET_ENV,
    DOGFOOD_SECRET_ENV,
    DOGFOOD_SECRET_FINGERPRINT_ENV,
    LOCAL_CONTROL_OPERATOR_ENVIRONMENT_VARIABLES,
    RELEASE_OPERATOR_SECRET_ENV,
    RELEASE_OPERATOR_SECRET_FINGERPRINT_ENV,
    WORKER_SECRET_ENV,
    LocalOperatorConfiguration,
    local_secret_fingerprint,
)
from engine.persistence.configuration import (
    ROLE_ENVIRONMENT_VARIABLES,
    DatabasePurpose,
)
from scripts.daily_driver.backup import create_database_backup
from scripts.daily_driver.environment import (
    EnvironmentRefused,
    load_owner_environment,
    project_environment,
)

SCHEDULED_OPERATION_CATEGORIES: Final = frozenset(
    {"scan", "refresh", "drain", "health", "backup"}
)
RUNNABLE_SCHEDULED_OPERATIONS: Final = (
    SCHEDULED_OPERATION_CATEGORIES - {"refresh"}
)
_JOB = re.compile(r"[a-z][a-z0-9-]*")
_PROVIDER_SIGNING_KEY_ENV = "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX"
_CHECKPOINT_SIGNING_KEY_ENV = (
    "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX"
)


def _database_environment_variables(
    *purposes: DatabasePurpose,
) -> frozenset[str]:
    return frozenset(
        name
        for purpose in purposes
        for name in (
            purpose.environment_variable,
            ROLE_ENVIRONMENT_VARIABLES[purpose],
        )
    )


_API_REQUIRED_DATABASE_ENVIRONMENT = _database_environment_variables(
    DatabasePurpose.API_RUNTIME,
    DatabasePurpose.CONTROL_PLANE,
)
_API_DATABASE_ENVIRONMENT = _API_REQUIRED_DATABASE_ENVIRONMENT
_API_OPERATOR_ENVIRONMENT = (
    DOGFOOD_RUNTIME_ENVIRONMENT_VARIABLES | DOGFOOD_CONTROL_ENVIRONMENT_VARIABLES
)
_WORKER_DATABASE_ENVIRONMENT = _database_environment_variables(
    DatabasePurpose.SUPPLY_SCHEDULER,
    DatabasePurpose.SUPPLY_WORKER,
)
_WORKER_REQUIRED_ENVIRONMENT = frozenset(
    {
        "CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION",
        "CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER",
        WORKER_FILE_ROOTS_ENV,
        "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX",
    }
)
_OPTIONAL_FILE_ROOT_ENVIRONMENT = FILE_ROOT_ENVIRONMENT_VARIABLES - {
    WORKER_FILE_ROOTS_ENV
}
_WORKER_OPTIONAL_ENVIRONMENT = (
    frozenset(
        {
            "CONTEXT_ENGINE_WORKER_EMBEDDING_API_KEY",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_BATCH_SIZE",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_ENDPOINT",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_MODEL_DIR",
            "CONTEXT_ENGINE_WORKER_EMBEDDING_TIMEOUT_SECONDS",
        }
    )
    | _OPTIONAL_FILE_ROOT_ENVIRONMENT
)
_SCAN_DATABASE_ENVIRONMENT = _database_environment_variables(
    DatabasePurpose.CONTROL_PLANE
)
_SCAN_OPERATOR_ENVIRONMENT = (
    LOCAL_CONTROL_OPERATOR_ENVIRONMENT_VARIABLES
    | frozenset(
        {
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID",
            "CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION",
            "CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF",
            "CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_OPERATOR_SOURCE_REF",
            "CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX",
            "CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID",
        }
    )
    | FILE_ROOT_ENVIRONMENT_VARIABLES
)
_PROCESS_ENVIRONMENT_CONTRACTS = {
    "api": (
        _API_DATABASE_ENVIRONMENT | _API_OPERATOR_ENVIRONMENT,
        _API_REQUIRED_DATABASE_ENVIRONMENT | DOGFOOD_RUNTIME_ENVIRONMENT_VARIABLES,
    ),
    "worker": (
        _WORKER_DATABASE_ENVIRONMENT
        | _WORKER_REQUIRED_ENVIRONMENT
        | _WORKER_OPTIONAL_ENVIRONMENT,
        _WORKER_DATABASE_ENVIRONMENT | _WORKER_REQUIRED_ENVIRONMENT,
    ),
    "scan": (
        _SCAN_DATABASE_ENVIRONMENT | _SCAN_OPERATOR_ENVIRONMENT,
        _SCAN_DATABASE_ENVIRONMENT
        | (_SCAN_OPERATOR_ENVIRONMENT - _OPTIONAL_FILE_ROOT_ENVIRONMENT),
    ),
}


def run_visible_job(
    *,
    job: str,
    signal_root: Path,
    action: Callable[[], int],
    recorded_at: datetime | None = None,
) -> int:
    """Run one allowlisted job and durably record every non-zero outcome."""

    if job not in SCHEDULED_OPERATION_CATEGORIES or _JOB.fullmatch(job) is None:
        raise ValueError("scheduled job is outside the closed allowlist")
    try:
        exit_code = action()
    except Exception:
        exit_code = 1
    if exit_code == 0:
        return 0
    instant = datetime.now(UTC) if recorded_at is None else recorded_at.astimezone(UTC)
    signal_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(signal_root, 0o700)
    job_root = signal_root / job
    job_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(job_root, 0o700)
    marker_stem = f"{instant:%Y%m%dT%H%M%S%fZ}"
    marker = job_root / f"{marker_stem}.json"
    collision = 0
    while marker.exists() or marker.is_symlink():
        collision += 1
        marker = job_root / f"{marker_stem}-{collision}.json"
    payload = {
        "attemptedAt": instant.isoformat().replace("+00:00", "Z"),
        "exitCode": exit_code,
        "job": job,
        "status": "FAILED",
    }
    descriptor, temporary_name = tempfile.mkstemp(dir=job_root, prefix=".failure-")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(payload, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, marker)
        _fsync_directory(job_root)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ContextEngine daily-driver runner")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    run = subparsers.add_parser("run")
    run.add_argument(
        "--job",
        choices=sorted(RUNNABLE_SCHEDULED_OPERATIONS),
        required=True,
    )
    run.add_argument("--checkout", type=Path, required=True)
    run.add_argument("--database-environment", type=Path)
    run.add_argument("--operator-environment", type=Path)
    run.add_argument("--failure-root", type=Path, required=True)
    run.add_argument("--backup-root", type=Path)
    run.add_argument("--docker-executable", type=Path)
    run.add_argument("--health-url")

    daemon = subparsers.add_parser("daemon")
    daemon.add_argument("--service", choices=("api", "worker"), required=True)
    daemon.add_argument("--checkout", type=Path, required=True)
    daemon.add_argument("--database-environment", type=Path, required=True)
    daemon.add_argument("--operator-environment", type=Path, required=True)
    daemon.add_argument("--api-port", type=int)

    bootstrap = subparsers.add_parser("bootstrap")
    bootstrap.add_argument("--service", choices=("database",), required=True)
    bootstrap.add_argument("--checkout", type=Path, required=True)
    bootstrap.add_argument("--docker-executable", type=Path, required=True)
    bootstrap.add_argument("--uv-executable", type=Path, required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    if parsed.mode == "bootstrap":
        return _run_database_bootstrap(parsed)
    if parsed.mode == "daemon":
        return _run_daemon(parsed)
    return run_visible_job(
        job=parsed.job,
        signal_root=parsed.failure_root,
        action=lambda: _run_scheduled(parsed),
    )


def _run_scheduled(arguments: argparse.Namespace) -> int:
    python = arguments.checkout / ".venv" / "bin" / "python"
    command: tuple[str, ...]
    if arguments.job == "backup":
        if arguments.backup_root is None or arguments.docker_executable is None:
            return 2
        create_database_backup(
            checkout=arguments.checkout,
            backup_root=arguments.backup_root,
            docker_executable=str(arguments.docker_executable),
        )
        return 0
    if arguments.job == "health":
        if arguments.health_url is None:
            return 2
        with urllib.request.urlopen(arguments.health_url, timeout=10) as response:
            return 0 if response.status == 200 else 1
    if arguments.job == "drain":
        database, operator = _live_environments(arguments)
        environment = process_environment("worker", database, operator)
        command = (
            str(python),
            "-m",
            "applications.worker",
            "--dispatch-file-once",
        )
    elif arguments.job == "scan":
        database, operator = _live_environments(arguments)
        fingerprints = validate_scan_secret_separation(operator)
        organization = operator.get("CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID")
        source = operator.get("CONTEXT_ENGINE_OPERATOR_SOURCE_REF")
        if not organization or not source:
            return 2
        environment = process_environment("scan", database, operator) | fingerprints
        command = (
            str(python),
            "-m",
            "applications.control",
            "scan",
            "--organization-id",
            organization,
            "--source-ref",
            source,
        )
    else:
        return 2
    return subprocess.run(
        command,
        cwd=arguments.checkout,
        env=environment,
        check=False,
    ).returncode


def _run_daemon(arguments: argparse.Namespace) -> int:
    database = load_owner_environment(arguments.database_environment)
    operator = load_owner_environment(arguments.operator_environment)
    python = arguments.checkout / ".venv" / "bin" / "python"
    command: tuple[str, ...]
    if arguments.service == "api":
        if arguments.api_port is None:
            return 2
        environment = process_environment("api", database, operator)
        command = (
            str(python),
            "-m",
            "applications.api",
            "--host",
            "127.0.0.1",
            "--port",
            str(arguments.api_port),
        )
    else:
        environment = process_environment("worker", database, operator)
        command = (str(python), "-m", "applications.worker", "--dispatch-files")
    os.execve(command[0], command, environment)


def _live_environments(
    arguments: argparse.Namespace,
) -> tuple[dict[str, str], dict[str, str]]:
    if arguments.database_environment is None or arguments.operator_environment is None:
        raise ValueError("scheduled process environment is unavailable")
    return (
        dict(load_owner_environment(arguments.database_environment)),
        dict(load_owner_environment(arguments.operator_environment)),
    )


def process_environment(
    process: str,
    database: Mapping[str, str],
    operator: Mapping[str, str],
) -> dict[str, str]:
    """Project the closed API, worker, or scan child-process contract."""

    try:
        allowed, required = _PROCESS_ENVIRONMENT_CONTRACTS[process]
    except KeyError:
        raise ValueError("deployment process is outside the closed set") from None
    validate_local_operator_secret_separation(operator)
    return project_environment(
        database,
        operator,
        allowed=allowed,
        required=required,
    )


def validate_local_operator_secret_separation(operator: Mapping[str, str]) -> None:
    """Validate ADR-0069's four configured planes before child projection."""

    try:
        if LocalOperatorConfiguration.load(operator) is None:
            raise ValueError
    except (TypeError, ValueError, UnicodeError):
        raise EnvironmentRefused("operator secret separation is invalid") from None


def validate_scan_secret_separation(operator: Mapping[str, str]) -> dict[str, str]:
    """Validate ADR-0071 collisions before projecting away release credentials."""

    try:
        configuration = LocalOperatorConfiguration.load(operator)
        if configuration is None:
            raise ValueError
        proof_values = tuple(
            operator[name]
            for name in (_PROVIDER_SIGNING_KEY_ENV, _CHECKPOINT_SIGNING_KEY_ENV)
        )
        if any(
            len(value) != 64
            or len(bytes.fromhex(value)) != 32
            for value in proof_values
        ):
            raise ValueError
        operator_secret_values = (
            operator[CONTROL_OPERATOR_SECRET_ENV],
            operator[RELEASE_OPERATOR_SECRET_ENV],
            operator[DOGFOOD_SECRET_ENV],
        )
        if any(
            hmac.compare_digest(proof_value.lower(), operator_secret.lower())
            for proof_value in proof_values
            for operator_secret in operator_secret_values
        ):
            raise ValueError
        separated = (
            *(bytes.fromhex(value) for value in proof_values),
            bytes.fromhex(operator[WORKER_SECRET_ENV]),
            configuration.control_secret,
            configuration.release_secret,
            operator[DOGFOOD_SECRET_ENV].encode("utf-8"),
        )
        for index, secret in enumerate(separated):
            if any(
                hmac.compare_digest(secret, other)
                for other in separated[index + 1 :]
            ):
                raise ValueError
        return {
            RELEASE_OPERATOR_SECRET_FINGERPRINT_ENV: local_secret_fingerprint(
                operator[RELEASE_OPERATOR_SECRET_ENV]
            ),
            DOGFOOD_SECRET_FINGERPRINT_ENV: local_secret_fingerprint(
                operator[DOGFOOD_SECRET_ENV]
            ),
        }
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
    ):
        raise EnvironmentRefused("scan secret separation is invalid") from None


def _run_database_bootstrap(arguments: argparse.Namespace) -> int:
    docker = _absolute_executable(arguments.docker_executable)
    uv = _absolute_executable(arguments.uv_executable)
    checkout = arguments.checkout.resolve(strict=True)
    harness = checkout / "scripts" / "database_harness.sh"
    if not harness.is_file():
        return 2
    environment = dict(os.environ)
    executable_directories = (docker.parent, uv.parent)
    environment["PATH"] = os.pathsep.join(
        (*map(str, executable_directories), "/usr/bin", "/bin", "/usr/sbin", "/sbin")
    )
    if subprocess.run(
        ("/usr/bin/open", "-gja", "Docker"),
        cwd=checkout,
        env=environment,
        check=False,
    ).returncode != 0:
        return 1
    return subprocess.run(
        ("/bin/bash", str(harness), "up"),
        cwd=checkout,
        env=environment,
        check=False,
    ).returncode


def _absolute_executable(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("bootstrap executable is unavailable") from None
    if (
        not path.is_absolute()
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
    ):
        raise ValueError("bootstrap executable is unavailable")
    return resolved


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
