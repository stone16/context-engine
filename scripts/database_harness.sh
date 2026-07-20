#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT_DIR
readonly STATE_DIR="$ROOT_DIR/.context-engine"
readonly ENV_FILE="$STATE_DIR/database.env"
readonly PROJECT_FILE="$STATE_DIR/compose-project"
readonly COMPOSE_FILE="$ROOT_DIR/compose.yaml"
COMPOSE_PROJECT=''

usage() {
  printf 'usage: %s {up|down|reset|integration}\n' "$0" >&2
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'required command is unavailable: %s\n' "$1" >&2
    exit 1
  fi
}

generate_environment() {
  if [[ -L "$STATE_DIR" ]]; then
    printf 'refusing to use a symbolic-link database state directory: %s\n' \
      "$STATE_DIR" >&2
    exit 1
  fi
  if [[ -L "$ENV_FILE" ]]; then
    printf 'refusing to use a symbolic-link database environment: %s\n' \
      "$ENV_FILE" >&2
    exit 1
  fi

  require_command python3
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"

  if [[ ! -f "$ENV_FILE" ]]; then
    local bootstrap_password
    local migrator_password
    local runtime_password
    local worker_password
    local postgres_port
    bootstrap_password="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    migrator_password="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    runtime_password="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    worker_password="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    postgres_port="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

    local temporary_file
    temporary_file="$(mktemp "$STATE_DIR/database.env.tmp.XXXXXX")"
    trap 'rm -f "$temporary_file"' EXIT
    (
      umask 077
      {
        printf 'POSTGRES_DB=context_engine\n'
        printf 'POSTGRES_USER=context_engine_bootstrap\n'
        printf 'POSTGRES_PASSWORD=%s\n' "$bootstrap_password"
        printf 'CONTEXT_ENGINE_POSTGRES_PORT=%s\n' "$postgres_port"
        printf 'CONTEXT_ENGINE_MIGRATOR_ROLE=context_engine_migrator\n'
        printf 'CONTEXT_ENGINE_MIGRATOR_PASSWORD=%s\n' "$migrator_password"
        printf 'CONTEXT_ENGINE_RUNTIME_ROLE=context_engine_runtime\n'
        printf 'CONTEXT_ENGINE_RUNTIME_PASSWORD=%s\n' "$runtime_password"
        printf 'CONTEXT_ENGINE_WORKER_ROLE=context_engine_worker\n'
        printf 'CONTEXT_ENGINE_WORKER_PASSWORD=%s\n' "$worker_password"
        printf 'CONTEXT_ENGINE_MIGRATION_DATABASE_URL=postgresql+psycopg://context_engine_migrator:%s@127.0.0.1:%s/context_engine\n' \
          "$migrator_password" "$postgres_port"
        printf 'CONTEXT_ENGINE_RUNTIME_DATABASE_URL=postgresql+psycopg://context_engine_runtime:%s@127.0.0.1:%s/context_engine\n' \
          "$runtime_password" "$postgres_port"
        printf 'CONTEXT_ENGINE_WORKER_DATABASE_URL=postgresql+psycopg://context_engine_worker:%s@127.0.0.1:%s/context_engine\n' \
          "$worker_password" "$postgres_port"
        printf 'CONTEXT_ENGINE_TEST_DATABASE_URL=postgresql+psycopg://context_engine_runtime:%s@127.0.0.1:%s/context_engine\n' \
          "$runtime_password" "$postgres_port"
      } >"$temporary_file"
    )
    chmod 600 "$temporary_file"
    if ! ln "$temporary_file" "$ENV_FILE" 2>/dev/null && \
        [[ ! -f "$ENV_FILE" || -L "$ENV_FILE" ]]; then
      printf 'could not publish database environment atomically: %s\n' \
        "$ENV_FILE" >&2
      exit 1
    fi
    rm -f "$temporary_file"
    trap - EXIT
  fi

  if [[ -L "$ENV_FILE" || ! -O "$ENV_FILE" ]]; then
    printf 'database environment must be a current-user-owned regular file: %s\n' \
      "$ENV_FILE" >&2
    exit 1
  fi
  chmod 600 "$ENV_FILE"
}

load_project_identity() {
  if [[ -L "$PROJECT_FILE" ]]; then
    printf 'refusing to use a symbolic-link Compose project identity: %s\n' \
      "$PROJECT_FILE" >&2
    exit 1
  fi

  if [[ ! -f "$PROJECT_FILE" ]]; then
    local project_name
    local temporary_file
    project_name="context-engine-$(python3 -c \
      'import secrets; print(secrets.token_hex(8))')"
    temporary_file="$(mktemp "$STATE_DIR/compose-project.tmp.XXXXXX")"
    trap 'rm -f "$temporary_file"' EXIT
    (
      umask 077
      printf '%s\n' "$project_name" >"$temporary_file"
    )
    chmod 600 "$temporary_file"
    if ! ln "$temporary_file" "$PROJECT_FILE" 2>/dev/null && \
        [[ ! -f "$PROJECT_FILE" || -L "$PROJECT_FILE" ]]; then
      printf 'could not publish Compose project identity atomically: %s\n' \
        "$PROJECT_FILE" >&2
      exit 1
    fi
    rm -f "$temporary_file"
    trap - EXIT
  fi

  if [[ -L "$PROJECT_FILE" || ! -O "$PROJECT_FILE" ]]; then
    printf 'Compose project identity must be a current-user-owned regular file: %s\n' \
      "$PROJECT_FILE" >&2
    exit 1
  fi
  chmod 600 "$PROJECT_FILE"
  COMPOSE_PROJECT="$(<"$PROJECT_FILE")"
  if [[ ! "$COMPOSE_PROJECT" =~ ^context-engine-[0-9a-f]{16}$ ]]; then
    printf 'Compose project identity failed its generated-value contract\n' >&2
    exit 1
  fi
}

load_environment() {
  generate_environment
  load_project_identity

  local variable_name
  local variable_value
  local loaded_variable_names=' '
  local allowed_variables=' POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD CONTEXT_ENGINE_POSTGRES_PORT CONTEXT_ENGINE_MIGRATOR_ROLE CONTEXT_ENGINE_MIGRATOR_PASSWORD CONTEXT_ENGINE_RUNTIME_ROLE CONTEXT_ENGINE_RUNTIME_PASSWORD CONTEXT_ENGINE_WORKER_ROLE CONTEXT_ENGINE_WORKER_PASSWORD CONTEXT_ENGINE_MIGRATION_DATABASE_URL CONTEXT_ENGINE_RUNTIME_DATABASE_URL CONTEXT_ENGINE_WORKER_DATABASE_URL CONTEXT_ENGINE_TEST_DATABASE_URL '

  while IFS='=' read -r variable_name variable_value; do
    if [[ -z "$variable_name" || "$allowed_variables" != *" $variable_name "* ]]; then
      printf 'database environment contains an unexpected variable: %s\n' \
        "$variable_name" >&2
      exit 1
    fi
    if [[ "$loaded_variable_names" == *" $variable_name "* ]]; then
      printf 'database environment contains a duplicate variable: %s\n' \
        "$variable_name" >&2
      exit 1
    fi
    loaded_variable_names+="$variable_name "
    export "$variable_name=$variable_value"
  done <"$ENV_FILE"

  local required_variable
  for required_variable in ${allowed_variables}; do
    if [[ "$loaded_variable_names" != *" $required_variable "* ]]; then
      printf 'database environment is missing variable: %s\n' \
        "$required_variable" >&2
      exit 1
    fi
  done

  if [[ "$POSTGRES_DB" != 'context_engine' || \
        "$POSTGRES_USER" != 'context_engine_bootstrap' || \
        "$CONTEXT_ENGINE_MIGRATOR_ROLE" != 'context_engine_migrator' || \
        "$CONTEXT_ENGINE_RUNTIME_ROLE" != 'context_engine_runtime' || \
        "$CONTEXT_ENGINE_WORKER_ROLE" != 'context_engine_worker' || \
        ! "$CONTEXT_ENGINE_POSTGRES_PORT" =~ ^[0-9]+$ || \
        ! "$POSTGRES_PASSWORD" =~ ^[0-9a-f]{64}$ || \
        ! "$CONTEXT_ENGINE_MIGRATOR_PASSWORD" =~ ^[0-9a-f]{64}$ || \
        ! "$CONTEXT_ENGINE_RUNTIME_PASSWORD" =~ ^[0-9a-f]{64}$ || \
        ! "$CONTEXT_ENGINE_WORKER_PASSWORD" =~ ^[0-9a-f]{64}$ ]]; then
    printf 'database environment failed its generated-value contract\n' >&2
    exit 1
  fi

  local database_endpoint="127.0.0.1:$CONTEXT_ENGINE_POSTGRES_PORT/context_engine"
  if [[ "$CONTEXT_ENGINE_MIGRATION_DATABASE_URL" != \
          "postgresql+psycopg://context_engine_migrator:$CONTEXT_ENGINE_MIGRATOR_PASSWORD@$database_endpoint" || \
        "$CONTEXT_ENGINE_RUNTIME_DATABASE_URL" != \
          "postgresql+psycopg://context_engine_runtime:$CONTEXT_ENGINE_RUNTIME_PASSWORD@$database_endpoint" || \
        "$CONTEXT_ENGINE_WORKER_DATABASE_URL" != \
          "postgresql+psycopg://context_engine_worker:$CONTEXT_ENGINE_WORKER_PASSWORD@$database_endpoint" || \
        "$CONTEXT_ENGINE_TEST_DATABASE_URL" != \
          "$CONTEXT_ENGINE_RUNTIME_DATABASE_URL" ]]; then
    printf 'database environment failed its role-isolated URL contract\n' >&2
    exit 1
  fi
}

compose() {
  docker compose \
    --project-name "$COMPOSE_PROJECT" \
    --env-file "$ENV_FILE" \
    --file "$COMPOSE_FILE" \
    "$@"
}

wait_for_database() {
  uv run python "$ROOT_DIR/scripts/wait_for_database.py"
}

database_up() {
  require_command docker
  require_command uv
  load_environment
  compose up --detach --wait
  wait_for_database
  printf 'PostgreSQL harness is ready; connection contract: %s\n' "$ENV_FILE"
}

database_down() {
  require_command docker
  if [[ ! -f "$ENV_FILE" ]]; then
    printf 'PostgreSQL harness has no generated state; nothing to stop.\n'
    return
  fi
  load_environment
  compose down --remove-orphans
}

database_reset() {
  require_command docker
  require_command uv
  load_environment
  compose down --volumes --remove-orphans
  compose up --detach --wait
  wait_for_database
  printf 'PostgreSQL harness was rebuilt from an empty data volume.\n'
}

run_integration() {
  require_command uv
  load_environment
  wait_for_database
  uv run pytest -q -m integration tests/integration
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

case "$1" in
  up)
    database_up
    ;;
  down)
    database_down
    ;;
  reset)
    database_reset
    ;;
  integration)
    run_integration
    ;;
  *)
    usage
    exit 2
    ;;
esac
