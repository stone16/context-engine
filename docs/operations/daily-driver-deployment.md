# Durable daily-driver deployment

This runbook installs one local, loopback-only ContextEngine daily driver from
a dedicated plain Git checkout. The tracked setup and launchd files are
templates only: they do not install or load a service, handle a real vault, or
write to a password manager. The maintainer performs every machine-changing
step below.

The deployment keeps the existing topology: one pinned compose PostgreSQL, one
API process, and one independent Supply worker. Scheduled work is limited by
[`scheduled-jobs.json`](../../deploy/daily-driver/scheduled-jobs.json) to scan,
refresh, drain, health, and backup categories. Release promotion, profile
activation, and rollback remain explicit operator actions under ADR-0073.

## 1. Choose durable locations

Set these values in the shell that performs installation. Both roots must be
absolute existing directories, must not be symlinks, must live outside every
Git worktree, and must never be below a `.context-engine` directory. The setup
script imports this convention from `engine.learning.golden_storage`, which is
the same durable-root contract used by the private golden corpus.

```bash
export CONTEXT_ENGINE_DEPLOY_CHECKOUT='<absolute dedicated checkout>'
export CONTEXT_ENGINE_DATABASE_BACKUP_ROOT='<absolute private backup directory>'
export CONTEXT_ENGINE_DOCKER_EXECUTABLE="$(command -v docker)"
export CONTEXT_ENGINE_UV_EXECUTABLE="$(command -v uv)"
export CONTEXT_ENGINE_ORIGIN="$(git -C '<reviewed-source-checkout>' remote get-url origin)"
export CONTEXT_ENGINE_DEPLOY_BRANCH='main'
export CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX='<reverse-DNS label prefix>'
export CONTEXT_ENGINE_API_PORT='<loopback API port>'
export CONTEXT_ENGINE_BACKUP_HOUR='<nightly backup hour, 0-23>'
export CONTEXT_ENGINE_SCAN_HOUR='<nightly scan hour, 0-23>'
export CONTEXT_ENGINE_HEALTH_INTERVAL_SECONDS='<health interval, at least 60>'
```

Create the two parent-owned roots with restrictive permissions. Run these from
outside every Git worktree; the setup script refuses otherwise.

```bash
install -d -m 700 "$(dirname "$CONTEXT_ENGINE_DEPLOY_CHECKOUT")"
install -d -m 700 "$CONTEXT_ENGINE_DATABASE_BACKUP_ROOT"
cd /private/tmp
python3 '<reviewed-source-checkout>/scripts/daily_driver_setup.py' \
  --checkout "$CONTEXT_ENGINE_DEPLOY_CHECKOUT" \
  --origin "$CONTEXT_ENGINE_ORIGIN" \
  --branch "$CONTEXT_ENGINE_DEPLOY_BRANCH" \
  --backup-root "$CONTEXT_ENGINE_DATABASE_BACKUP_ROOT" \
  --docker-executable "$CONTEXT_ENGINE_DOCKER_EXECUTABLE" \
  --uv-executable "$CONTEXT_ENGINE_UV_EXECUTABLE" \
  --label-prefix "$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX" \
  --api-port "$CONTEXT_ENGINE_API_PORT" \
  --backup-hour "$CONTEXT_ENGINE_BACKUP_HOUR" \
  --scan-hour "$CONTEXT_ENGINE_SCAN_HOUR" \
  --health-interval-seconds "$CONTEXT_ENGINE_HEALTH_INTERVAL_SECONDS"
```

Re-running that command is the update path: it requires a matching origin,
clean checkout, and fast-forward-only branch update; then it re-runs the locked
install, brings up the same compose project, and atomically re-renders identical
plists under `.context-engine/launchd/`. Never run `make db-reset` in this
checkout: it is the one command that deletes the durable compose volume.
Keep `CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX` stable across reruns. The owner-only
render manifest refuses a prefix change and refuses unknown plists instead of
deleting state it cannot prove it owns.
The setup also writes an owner-only durable-deployment marker; the tracked
database harness refuses `make db-reset` whenever that marker exists, before it
invokes Docker. `make db-down` remains the non-destructive stop command.

## 2. Preserve the single live connection contract

`make db-up` generated
`$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/database.env`. It is the only
database connection source and must remain a current-user-owned mode-`0600`
file. Do not copy individual credentials into a plist, another env file, this
runbook, an issue, or a pull request.

The password-manager operation is deliberately manual. The following uses the
installed 1Password CLI; select the private vault explicitly. Record only the
returned item ID in the maintainer's private inventory. The repository never
runs these commands:

```bash
export CONTEXT_ENGINE_PASSWORD_VAULT='<private 1Password vault>'
op document create \
  "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/database.env" \
  --vault "$CONTEXT_ENGINE_PASSWORD_VAULT" \
  --title 'ContextEngine durable database.env' \
  --file-name 'database.env'
export CONTEXT_ENGINE_DATABASE_ENV_ITEM='<returned document item ID>'
export CONTEXT_ENGINE_DATABASE_ENV_RETRIEVAL="$(mktemp /private/tmp/context-engine-database-env.XXXXXX)"
op document get "$CONTEXT_ENGINE_DATABASE_ENV_ITEM" \
  --vault "$CONTEXT_ENGINE_PASSWORD_VAULT" \
  --out-file "$CONTEXT_ENGINE_DATABASE_ENV_RETRIEVAL" \
  --file-mode 0600
test "$(stat -f '%Lp' "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/database.env")" = 600
cmp -s \
  "$CONTEXT_ENGINE_DATABASE_ENV_RETRIEVAL" \
  "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/database.env"
rm -f "$CONTEXT_ENGINE_DATABASE_ENV_RETRIEVAL"
unset CONTEXT_ENGINE_DATABASE_ENV_RETRIEVAL
```

No repository script writes to the password manager. If the comparison fails,
delete the password-manager item and repeat before continuing.

## 3. Configure and bootstrap the bounded local composition

The setup command created an empty ignored `operators.env` without overwriting
any existing values. Populate it from the complete variables documented in the
repository README sections “Run the bounded dogfood API”, “Run the worker”, and
“Scan a local File source”. Add
`CONTEXT_ENGINE_OPERATOR_SOURCE_REF` after source registration; the scheduled
scan wrapper uses that identifier. Keep the file at mode `0600`:

```bash
test "$(stat -f '%Lp' "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/operators.env")" = 600
${EDITOR:?set EDITOR} \
  "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/operators.env"
```

Use plain `KEY=value` records. Shell-sensitive values such as the root-registry
JSON must be enclosed in single quotes so the same file is both sourceable for
interactive commands and parsed without evaluation by the launchd wrapper:

```text
CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON='{"<root-ref>":"<absolute source root>"}'
```

The value of `CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON` must bind a curated,
bounded subtree rather than a disposable mirror or worktree. The current source
baseline bound and active Markdown contract are documented in the end-to-end
dogfood walkthrough; do not silently widen them.

Load both ignored sources for interactive bootstrap only:

```bash
cd "$CONTEXT_ENGINE_DEPLOY_CHECKOUT"
set -a
source .context-engine/database.env
source .context-engine/operators.env
set +a
uv run context-engine-control migrate
uv run context-engine-dogfood-seed \
  --organization-id "$CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID" \
  --user-id "$CONTEXT_ENGINE_DOGFOOD_USER_ID" \
  --membership-id "$CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID" \
  --provision-release-operator-grant \
  --file-import-service-principal-id \
    "$CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID"
uv run context-engine-control register-file-source \
  --organization-id "$CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID" \
  --display-name '<bounded source display name>' \
  --root-ref '<configured root ref>' \
  --idempotency-key '<stable private idempotency key>'
```

Put the returned `sourceRef` in `CONTEXT_ENGINE_OPERATOR_SOURCE_REF`, then run
the README's explicit change-feed/delete-observation activation, first scan,
queue drain, status, and reviewed release-promotion sequence. Promotion is
intentionally absent from every scheduled unit.

## 4. Validate, install, and load the launchd units

The renderer never writes `~/Library/LaunchAgents`. Review its ignored output,
prove every file parses, and search only for credential variable names (never
for live values):

```bash
find "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/launchd" \
  -name '*.plist' -type f -exec plutil -lint {} +
! rg -n \
  'POSTGRES_PASSWORD|CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET|CONTEXT_ENGINE_DOGFOOD_SECRET' \
  "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/launchd"
```

The tracked database agent opens Docker Desktop at login and retries the
existing idempotent `db-up` harness until Docker is available; it creates no
second database topology. Install idempotently as the logged-in user, then
bootstrap the six rendered agents. `install -m 600` overwrites only the exact
chosen labels.

```bash
export CONTEXT_ENGINE_LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
install -d -m 700 "$CONTEXT_ENGINE_LAUNCH_AGENTS"
for source in "$CONTEXT_ENGINE_DEPLOY_CHECKOUT"/.context-engine/launchd/*.plist; do
  install -m 600 "$source" "$CONTEXT_ENGINE_LAUNCH_AGENTS/$(basename "$source")"
done
for service in database api worker backup scan health; do
  plist="$CONTEXT_ENGINE_LAUNCH_AGENTS/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.$service.plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
done
```

Verify both daemons and the health carrier:

```bash
launchctl print "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.api"
launchctl print "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.worker"
launchctl print "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.database"
curl --fail --silent --show-error \
  "http://127.0.0.1:$CONTEXT_ENGINE_API_PORT/health" | \
  "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.venv/bin/python" -c \
  'import json, sys; health = json.load(sys.stdin); assert health["status"] == "ready"; assert health["runtime_delivery"] == "ACTIVE"'
```

## 5. Prove failure restart and nightly backup visibility

Force-kill each daemon by launchd label and confirm launchd assigns a new PID:

```bash
launchctl kill SIGKILL \
  "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.api"
launchctl kill SIGKILL \
  "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.worker"
launchctl print "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.api"
launchctl print "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.worker"
launchctl print "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.database"
curl --fail --silent --show-error \
  "http://127.0.0.1:$CONTEXT_ENGINE_API_PORT/health" | \
  "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.venv/bin/python" -c \
  'import json, sys; health = json.load(sys.stdin); assert health["status"] == "ready"; assert health["runtime_delivery"] == "ACTIVE"'
```

Kick the backup once instead of waiting for its calendar interval:

```bash
launchctl kickstart -k \
  "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.backup"
launchctl print \
  "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.backup"
for dump in "$CONTEXT_ENGINE_DATABASE_BACKUP_ROOT"/*.dump; do
  test -f "$dump" || continue
  test "$(stat -f '%Lp' "$dump")" = 600
  printf '%s\n' "$dump"
done
```

A nonzero scheduled exit persists an owner-only marker at
`.context-engine/scheduled-failures/<job>/<UTC instant>.json`, and launchd also
records the last exit status. Standard output/error logs live under
`.context-engine/logs/`. Inspect all three surfaces:

```bash
find "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/scheduled-failures" \
  -name '*.json' -type f -print 2>/dev/null || true
tail -n 100 "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/logs/backup.error.log"
launchctl print \
  "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.backup" | \
  rg 'last exit code|state'
```

## 6. Prove volume persistence and rehearse a scratch restore

First prove the live Organization rows survive the non-destructive harness
round trip. The durable-deployment marker makes `make db-reset` unavailable,
while `make db-down` and `make db-up` retain the exact compose volume:

```bash
cd "$CONTEXT_ENGINE_DEPLOY_CHECKOUT"
set -a
source .context-engine/database.env
set +a
CONTEXT_ENGINE_ORGANIZATION_COUNT_BEFORE="$(docker compose \
  --env-file .context-engine/database.env \
  --project-name "$CONTEXT_ENGINE_COMPOSE_PROJECT" exec -T postgres \
  psql --tuples-only --no-align --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --command 'SELECT count(*) FROM organization')"
make db-down
make db-up
CONTEXT_ENGINE_ORGANIZATION_COUNT_AFTER="$(docker compose \
  --env-file .context-engine/database.env \
  --project-name "$CONTEXT_ENGINE_COMPOSE_PROJECT" exec -T postgres \
  psql --tuples-only --no-align --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" --command 'SELECT count(*) FROM organization')"
test "$CONTEXT_ENGINE_ORGANIZATION_COUNT_BEFORE" = \
  "$CONTEXT_ENGINE_ORGANIZATION_COUNT_AFTER"
unset CONTEXT_ENGINE_ORGANIZATION_COUNT_BEFORE CONTEXT_ENGINE_ORGANIZATION_COUNT_AFTER
```

Do not use `make db-reset` and do not target the live database. Select one dump
privately, then run the tracked restore helper against the reserved scratch
namespace. The helper drops and recreates only the exact
`context_engine_restore_*` database, restores through the running pinned PG17
container, and refuses success unless the restored Alembic schema is visible.

```bash
export CONTEXT_ENGINE_RESTORE_DUMP='<absolute selected .dump path>'
cd "$CONTEXT_ENGINE_DEPLOY_CHECKOUT"
uv run python -c \
  'import os; from pathlib import Path; from scripts.daily_driver.backup import restore_database_backup; restore_database_backup(checkout=Path.cwd(), dump_path=Path(os.environ["CONTEXT_ENGINE_RESTORE_DUMP"]), scratch_database="context_engine_restore_drill")'
set -a
source .context-engine/database.env
set +a
docker compose --env-file .context-engine/database.env \
  --project-name "$CONTEXT_ENGINE_COMPOSE_PROJECT" exec -T postgres \
  psql --username "$POSTGRES_USER" --dbname context_engine_restore_drill \
  --command 'SELECT count(*) FROM organization'
docker compose --env-file .context-engine/database.env \
  --project-name "$CONTEXT_ENGINE_COMPOSE_PROJECT" exec -T postgres \
  dropdb --if-exists --force --username "$POSTGRES_USER" \
  context_engine_restore_drill
```

Record the date, selected dump's private inventory reference, row count, and
successful schema revision in the maintainer's private operational log. Never
put a dump path, credential, or tenant content in a pull request.

## 7. Reboot-survival drill and deployed security veto

Reboot only when the maintainer is ready, then run:

```bash
launchctl print "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.api"
launchctl print "gui/$(id -u)/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.worker"
curl --fail --silent --show-error \
  "http://127.0.0.1:$CONTEXT_ENGINE_API_PORT/health" | \
  "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.venv/bin/python" -c \
  'import json, sys; health = json.load(sys.stdin); assert health["status"] == "ready"; assert health["runtime_delivery"] == "ACTIVE"'
cd "$CONTEXT_ENGINE_DEPLOY_CHECKOUT"
make security-gate
```

The deployed-instance `make security-gate` and reboot confirmation are
maintainer-owned evidence. Repository verification cannot substitute for them.

## Uninstall

This stops and removes only the six exact launchd labels; it preserves the
checkout, database volume, backups, logs, and failure markers:

```bash
for service in database api worker backup scan health; do
  plist="$CONTEXT_ENGINE_LAUNCH_AGENTS/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.$service.plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  rm -f "$plist"
done
```

Removing the durable checkout, compose volume, backup root, or password-manager
item is a separate destructive operator decision and is deliberately not part
of this uninstall procedure.

To change the label prefix after uninstalling, keep the old prefix set while
removing the six exact ignored renders plus their renderer-owned manifest; then
set the new prefix and rerun setup:

```bash
for service in database api worker backup scan health; do
  rm -f "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/launchd/$CONTEXT_ENGINE_LAUNCHD_LABEL_PREFIX.$service.plist"
done
rm -f "$CONTEXT_ENGINE_DEPLOY_CHECKOUT/.context-engine/launchd/render-manifest.json"
```
