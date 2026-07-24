#!/usr/bin/env bash
set -Eeuo pipefail

required_environment=(
  POSTGRES_DB
  POSTGRES_USER
  CONTEXT_ENGINE_MIGRATOR_ROLE
  CONTEXT_ENGINE_MIGRATOR_PASSWORD
  CONTEXT_ENGINE_CONTROL_ROLE
  CONTEXT_ENGINE_CONTROL_PASSWORD
  CONTEXT_ENGINE_IDENTITY_ROLE
  CONTEXT_ENGINE_IDENTITY_PASSWORD
  CONTEXT_ENGINE_EGRESS_ROLE
  CONTEXT_ENGINE_EGRESS_PASSWORD
  CONTEXT_ENGINE_ACTION_ROLE
  CONTEXT_ENGINE_ACTION_PASSWORD
  CONTEXT_ENGINE_RUNTIME_ROLE
  CONTEXT_ENGINE_RUNTIME_PASSWORD
  CONTEXT_ENGINE_WORKER_ROLE
  CONTEXT_ENGINE_WORKER_PASSWORD
  CONTEXT_ENGINE_LEARNING_ROLE
  CONTEXT_ENGINE_LEARNING_PASSWORD
  CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE
  CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD
)

for variable_name in "${required_environment[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    printf 'required database bootstrap variable is missing: %s\n' \
      "$variable_name" >&2
    exit 1
  fi
done

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" <<'SQL'
\getenv database_name POSTGRES_DB
\getenv migrator_role CONTEXT_ENGINE_MIGRATOR_ROLE
\getenv migrator_password CONTEXT_ENGINE_MIGRATOR_PASSWORD
\getenv control_role CONTEXT_ENGINE_CONTROL_ROLE
\getenv control_password CONTEXT_ENGINE_CONTROL_PASSWORD
\getenv identity_role CONTEXT_ENGINE_IDENTITY_ROLE
\getenv identity_password CONTEXT_ENGINE_IDENTITY_PASSWORD
\getenv egress_role CONTEXT_ENGINE_EGRESS_ROLE
\getenv egress_password CONTEXT_ENGINE_EGRESS_PASSWORD
\getenv action_role CONTEXT_ENGINE_ACTION_ROLE
\getenv action_password CONTEXT_ENGINE_ACTION_PASSWORD
\getenv runtime_role CONTEXT_ENGINE_RUNTIME_ROLE
\getenv runtime_password CONTEXT_ENGINE_RUNTIME_PASSWORD
\getenv worker_role CONTEXT_ENGINE_WORKER_ROLE
\getenv worker_password CONTEXT_ENGINE_WORKER_PASSWORD
\getenv learning_role CONTEXT_ENGINE_LEARNING_ROLE
\getenv learning_password CONTEXT_ENGINE_LEARNING_PASSWORD
\getenv security_operator_role CONTEXT_ENGINE_SECURITY_OPERATOR_ROLE
\getenv security_operator_password CONTEXT_ENGINE_SECURITY_OPERATOR_PASSWORD

CREATE ROLE :"migrator_role"
  LOGIN
  PASSWORD :'migrator_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

CREATE ROLE :"runtime_role"
  LOGIN
  PASSWORD :'runtime_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

CREATE ROLE :"control_role"
  LOGIN
  PASSWORD :'control_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

CREATE ROLE :"worker_role"
  LOGIN
  PASSWORD :'worker_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

CREATE ROLE :"identity_role"
  LOGIN
  PASSWORD :'identity_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

CREATE ROLE :"egress_role"
  LOGIN
  PASSWORD :'egress_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

CREATE ROLE :"action_role"
  LOGIN
  PASSWORD :'action_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

CREATE ROLE :"learning_role"
  LOGIN
  PASSWORD :'learning_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

CREATE ROLE :"security_operator_role"
  LOGIN
  PASSWORD :'security_operator_password'
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS;

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name"
  TO :"migrator_role", :"control_role", :"runtime_role", :"worker_role",
     :"identity_role", :"egress_role", :"action_role", :"learning_role", :"security_operator_role";
ALTER DATABASE :"database_name" OWNER TO :"migrator_role";

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO :"migrator_role";
GRANT USAGE ON SCHEMA public
  TO :"control_role", :"runtime_role", :"worker_role",
     :"identity_role", :"egress_role", :"action_role", :"learning_role", :"security_operator_role";

-- pgvector is an untrusted extension, so only the disposable bootstrap
-- superuser creates it. Application schema objects remain migrator-owned.
CREATE EXTENSION vector WITH SCHEMA public;
-- WorkerLease nonce redemption uses only pgcrypto's SHA-256 digest primitive.
-- The bootstrap superuser, never an application role, owns the extension.
CREATE EXTENSION pgcrypto WITH SCHEMA public;
SQL
