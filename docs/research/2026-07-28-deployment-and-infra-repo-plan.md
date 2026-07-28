---
title: 2026-07-28 Daily-Dogfood Deployment and Infra-Repo Plan
date: 2026-07-28
status: proposal; activation boundaries owned by ADR-0068 / ADR-0069 / ADR-0073
---

# 2026-07-28 Daily-dogfood deployment and infra-repo plan

> Scope: how the maintainer runs ContextEngine every day on the maintainer
> machine, what the durable-data story is, and whether a separate infra
> repository is justified. This document proposes; it changes no runtime file,
> creates no repo, and adds no launchd plist. All code claims cite file:line
> against branch `stone16/end2end-walkthrough`. Absolute personal paths and
> secret values are deliberately absent; placeholders such as `<VAULT_ROOT>`
> and `<DEPLOY_CHECKOUT>` stand in for values that live only in local ignored
> configuration.

## Executive recommendation

1. **Topology (a):** launchd-managed native processes (API + Supply worker +
   scheduled control scans) on the maintainer Mac, talking to **one dedicated
   durable instance of the existing in-repo compose PostgreSQL** in a
   **dedicated long-lived checkout** — not an agent worktree. No new compose
   file, no home server, no VPS. A network-reachable topology is a possible
   later phase and is **gated on new ADRs**, not on configuration.
2. **Durable data (b):** the harness volume is already durable across
   `db-up`/`db-down`; what is disposable is the *checkout-scoped identity*.
   Pin the identity by deploying from one stable checkout, then add a nightly
   `pg_dump` launchd job and a written restore drill. Upgrades ride the
   existing Alembic path (`context-engine-control migrate`).
3. **Infra repo (c):** **do not create one now.** No constraint that justifies
   a split currently holds; the two real forces (personal machine detail must
   not enter the public repo; compose is a load-bearing test fixture) are both
   satisfied by keeping product infra in-repo and personal instantiation in
   ignored local files. The split trigger is named below so it can be
   recognized honestly when it arrives.
4. **This week (d):** Phase 1 is executable with commands that already exist —
   `make db-up`, `context-engine-control migrate | register-file-source |
   activate-change-feed | scan | status | promote-release`,
   `context-engine-dogfood-seed`, `context-engine-worker --dispatch-files`,
   `context-engine-api`. The only new artifacts Phase 1 needs are three
   launchd plists and one operator env file, sketched inline.

---

## 0. Verified current state

### Processes and entry points

| Process | Entry point | Notes |
|---|---|---|
| API | `context-engine-api` → `applications/api.py:17` | Dogfood composition refuses non-loopback hosts at argument parse time (`applications/api.py:23-27`); default app is reject-all (`STATUS.md` "No `200` is reachable from the production default") |
| Supply worker | `context-engine-worker` → `applications/worker.py:457` | Modes are mutually exclusive: `--dispatch-files` (continuous until SIGTERM, `applications/worker.py:398`), `--dispatch-file-once`, `--run-file-job`, `--test-mode` |
| Local operator (short-lived) | `context-engine-control` → `applications/control.py:53` | Subcommands: `migrate`, `register-file-source`, `read-source`, `activate-change-feed`, `activate-delete-observations`, `scan`, `status`, `promote-release` (`applications/control.py:42-85`) |
| Identity seed (short-lived) | `context-engine-dogfood-seed` → `applications/dogfood.py` | Idempotent; runs under migrator role only (ADR-0068 §7, ADR-0073) |

This matches the AGENTS.md process topology (API + independent Supply worker;
operator invocations are short-lived processes, not a third daemon).

### Database harness

- `compose.yaml:3` pins `pgvector/pgvector:0.8.5-pg17-bookworm` by exact
  digest; port publishes on `127.0.0.1` only (`compose.yaml:32`); role
  bootstrap runs from `infra/postgres/init/10-security-roles.sh` plus
  `scripts/provision_database_roles.py`.
- `.context-engine/database.env` is generated once per checkout, mode 0600,
  and embeds a random compose project (`context-engine-<16 hex>`), a random
  loopback port, and all twelve role passwords
  (`scripts/database_harness.sh:24-150`).
- **`make db-down` preserves the data volume** — it runs `compose down
  --remove-orphans` without `--volumes` (`scripts/database_harness.sh:663-671`).
  Only `make db-reset` destroys the volume
  (`scripts/database_harness.sh:673-682`). "Disposable" is therefore a
  *policy stance* (no backups, identity scoped to a checkout that agents
  delete), not a technical property of the volume.

### Activation boundaries that constrain deployment

- **Loopback by design.** ADR-0068 activates exactly one loopback,
  single-Membership File pgvector `Acquire` carrier; network exposure beyond
  the maintainer machine, a second human, and every network operator surface
  are `NOT_ACTIVE` (`STATUS.md`, ADR-0068 "Revisit trigger", ADR-0069 §2).
- **Write plane is local-process only.** Operator operations exist on no HTTP
  surface (ADR-0069 §2); each subcommand carries exactly one
  `ControlOperation` (ADR-0069 §4).
- **Four separate secrets** — Control operator, release operator, dogfood
  runtime bearer, worker lease key — must never be shared across planes
  (ADR-0069 §5; env names in `applications/operator_authentication.py:24-29`).
- **Promotion is explicit.** `promote-release` requires an operator evidence
  file and a versioned evaluation signing key
  (`CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_HEX` / `_VERSION`,
  `applications/release_promotion.py`; ADR-0073). Neither has a default and
  the key must be retained across invocations.
- **Embedding is the deterministic twin.** The corpus must be imported with
  the same twin the promoted Release binds; external providers fail
  composition (ADR-0068 §6).

### Corpus reality check (measured 2026-07-28)

- The maintainer vault currently holds **9,283 Markdown files** (52,360 files
  total, 4,900 directories, 6.6 GB).
- **Zero** Markdown files exceed the worker's default 1 MiB read ceiling
  (ADR-0065; configurable 1–64 MiB via
  `CONTEXT_ENGINE_WORKER_MAX_FILE_BYTES`, `applications/file_root_configuration.py:14`).
- ⚠️ **The change-feed baseline bound is 10,000 paths**
  (`MAX_FILE_CHANGE_BASELINE_SIZE`, `engine/control/file_change_pages.py:31`;
  ADR-0065 "roots beyond the retained 10,000-path baseline bound fail
  closed"). The full vault is at **~93% of that bound today** and grows
  daily. Registering the whole vault as one source will fail closed within
  months. Phase 1 must either (i) register a curated subtree as the source
  root, or (ii) precede full-vault registration with a product-lane issue
  that raises the bound with measurement. This is the single largest
  deployment risk found.

### Existing machine infrastructure (read-only inventory)

- **Docker Desktop** is the container runtime. Eleven `context-engine-*`
  per-worktree postgres containers and ~28 postgres-named volumes exist —
  evidence of the orphaned-identity problem this plan fixes. Other
  long-running containers (`leilei-personal-ci-runner`, `nexus-db`,
  `welder-postgres`, `grok-reg-tool`) belong to other projects and are
  untouched by this plan.
- **launchd is already the maintainer's automation standard**: two personal
  `com.stometa.*` LaunchAgents and a Homebrew-managed `postgresql@17`
  LaunchAgent are installed and running. Proposing launchd is therefore
  reusing existing infrastructure, not introducing a platform.
- A **native Homebrew PostgreSQL 17 is already running**. It is *not* the
  recommended database for dogfood — see the named constraint in (a).
- A personal CI runner container already exists; no new CI platform is
  needed or proposed.

---

## (a) Target topology: launchd-native processes + one durable compose Postgres

### Recommendation

Run on the maintainer Mac, from one dedicated long-lived checkout
(`<DEPLOY_CHECKOUT>`, e.g. a plain `git clone` of `main` outside any agent
workspace):

- **PostgreSQL**: the existing `compose.yaml` service, brought up by
  `make db-up` in `<DEPLOY_CHECKOUT>`, whose generated identity is treated as
  durable (never `db-reset` without a backup).
- **API + worker**: native Python processes under `uv run`, managed by two
  launchd LaunchAgents with `KeepAlive`.
- **Control operations** (scan, status, promote): short-lived
  `context-engine-control` invocations — interactive in Phase 1, scheduled by
  launchd in Phase 2.

### Named constraints (why the alternatives lose)

1. **Loopback is constitutional, not configurational.** ADR-0068 §1 and
   `applications/api.py:23-27` refuse non-loopback hosts; ADR-0069 §2 keeps
   every operator operation off the network. A home server or VPS would put a
   network hop between the maintainer and every surface, which is exactly
   what the accepted ADRs prohibit. **This rules out any remote topology now.**
   If daily use eventually demands a remote engine, that is a *later phase
   whose first deliverable is ADRs* (remote ingress, production operator
   authentication, network audience), not a deployment task. Presented
   honestly: nothing in this plan should be designed "so we can flip it
   remote later" — ADR-0068 says the dogfood composition must be deleted or
   replaced, not widened.
2. **Ingestion needs this Mac's filesystem.** The corpus is a local Obsidian
   vault; the worker reads anchored roots via `FileRootRegistry`
   (`applications/worker.py:24`). Containerizing the worker would require
   bind-mounting the vault into Docker Desktop's Linux VM — an extra failure
   mode and an extra trust surface for zero isolation gain. **This rules out
   a fully containerized stack.** (There is also no Dockerfile for the Python
   processes in-repo, so a compose-only stack would be new infrastructure,
   violating simplicity-first.)
3. **Parity with the tested harness rules out the native Homebrew Postgres.**
   The repo's verification contract is proven against the digest-pinned
   `pgvector 0.8.5 / PG17` image plus the in-repo role bootstrap
   (`compose.yaml:3`, `infra/postgres/init/10-security-roles.sh`). The
   running Homebrew `postgresql@17` has neither the pinned pgvector build nor
   the role/`FORCE RLS` provisioning path, and it is shared with other uses.
   Re-proving the security harness against a second topology to save one
   container is negative-value work.
4. **launchd over cron/scripts** because `KeepAlive` gives crash-restart for
   the two daemons, `StartCalendarInterval` covers the scheduled jobs, and it
   is already the maintainer's standard (three existing agents observed).

### Later-phase remote sketch (explicitly ADR-gated)

If a second device (phone, laptop) must query the engine, the honest sequence
is: ADR for remote ingress + production authentication → ADR for operator
surface → only then a deployment change (likely Tailscale-scoped, still
single-human). Listing it here is a roadmap note, not a design.

---

## (b) Durable-data story

### What durability requires

1. **Pin the identity.** The compose project name, port, and all credentials
   live in `<DEPLOY_CHECKOUT>/.context-engine/database.env`. That file — not
   the volume — is the fragile piece: lose it and the volume is orphaned
   (the ~28 orphaned volumes on this machine are the existing failure mode).
   Proposal: after first `make db-up`, back `database.env` up into the
   maintainer's password manager / Keychain. It is a secret store; it never
   enters git or the doc tree.
2. **Nightly logical backup.** A launchd job runs `pg_dump` through the
   published loopback port using credentials sourced from `database.env`:

   ```bash
   # sketch: scripts-local backup step (personal, ignored; see (c))
   set -a; source "<DEPLOY_CHECKOUT>/.context-engine/database.env"; set +a
   /usr/bin/env docker exec "${CONTEXT_ENGINE_COMPOSE_PROJECT}-postgres-1" \
     pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     > "$HOME/Backups/context-engine/context_engine-$(date +%F).dump"
   # rotate: keep 14 daily + 8 weekly
   ```

   Backup files contain tenant content and must live only in a
   maintainer-private, non-synced-to-public location.
3. **Restore drill (written once, rehearsed once).**

   ```bash
   cd <DEPLOY_CHECKOUT>
   make db-reset                       # destroys volume, re-provisions roles
   set -a; source .context-engine/database.env; set +a
   docker exec -i "${CONTEXT_ENGINE_COMPOSE_PROJECT}-postgres-1" \
     pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists \
     < "$HOME/Backups/context-engine/<chosen>.dump"
   uv run context-engine-control migrate     # no-op if schema already head
   uv run context-engine-control status --organization-id <ORG> --source-ref <SRC>
   ```

4. **Upgrade path.** Schema: `uv run context-engine-control migrate` under
   the migrator role (`applications/control.py:56-58`, ADR-0069 §6) — run it
   after every `git pull` of the deploy checkout, before restarting API and
   worker. Postgres image: bump the digest pin in `compose.yaml` via normal
   PR; deploy = backup → `make db-down` → `compose pull`/`make db-up` →
   `migrate`. Same-major image bumps reuse the volume; a future PG major
   upgrade is dump/restore (the backup job above is the mechanism).

### What this deliberately does not add

No streaming replication, no WAL archiving, no second database host. For a
single-human corpus that can be fully re-ingested from the vault in one scan,
`pg_dump` + re-ingest is a sufficient recovery story; anything more is
premature.

---

## (c) The infra-repo question: not yet — and here is the tripwire

"One infra repo" was the starting idea; evaluated honestly, **no current
constraint justifies it**, and one active constraint argues against it.

What a separate infra repo would own, and where each item actually belongs
today:

| Candidate content | Verdict | Reason |
|---|---|---|
| `compose.yaml`, `infra/postgres/init/` | **Stay in-repo** | They are load-bearing *test fixtures*: `STATUS.md` names `compose.yaml` as the owner of the pinned topology, and the security gate re-proves against exactly this stack. Splitting them breaks tested-equals-deployed parity, which is this repo's main asset. |
| Env contract *templates* / bootstrap runbook | **In-repo docs** (`docs/`), placeholder values only | The contract is product knowledge; the values are generated locally and never committed anywhere. |
| launchd plists with real paths, backup scripts with real destinations | **Neither repo** — personal ignored files | The public repo is a cleaned bundle; personal machine detail (absolute vault paths, usernames, backup locations) must not enter it. A new repo created only to hold three plists is a platform without a constituency; the maintainer's existing private dotfiles/setup location already serves this role. |
| Release/promotion runbook | **In-repo docs** | It documents `context-engine-control promote-release`, which is product surface (ADR-0073). |
| Secrets | **Nowhere in git** | Generated per-machine (`database.env`), held 0600, backed up via password manager. A secrets repo is an anti-goal. |

**The constraint that would justify splitting, named:** a second deployment
target with its own lifecycle — a home server or VPS whose provisioning,
secret management, and upgrade cadence diverge from the laptop's — or
CI-driven deployment where infra changes need review/access control separate
from product code. Both are downstream of the remote-topology ADRs in (a);
until those ADRs exist, an infra repo would be an empty room. If that day
comes, the split is: machine provisioning + remote secret management move
out; `compose.yaml` and the role bootstrap **still stay in-repo** because the
test harness needs them regardless.

Interim convention proposed instead of a repo: keep personal instantiations
under `<DEPLOY_CHECKOUT>/.context-engine/` (already git-ignored, already
0600-disciplined) — e.g. `operators.env`, `backup.sh`, and copies of the
plists — so everything machine-specific lives in one ignored directory whose
backup story is the password manager.

---

## (d) Phase plan

### Phase 1 — this week (all commands exist today)

**1. Create the durable deployment checkout** (outside agent workspaces):

```bash
git clone <origin> <DEPLOY_CHECKOUT> && cd <DEPLOY_CHECKOUT>
make install
make db-up          # generates durable identity in .context-engine/database.env
```

Back up `.context-engine/database.env` to the password manager immediately.

**2. Author the operator env file** `<DEPLOY_CHECKOUT>/.context-engine/operators.env`
(0600; four distinct generated secrets per ADR-0069 §5, plus release signing
key and dogfood identity refs):

```bash
CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET=<generated>
CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET=<generated>
CONTEXT_ENGINE_DOGFOOD_SECRET=<generated>
CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX=<generated>
CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_HEX=<generated>
CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_VERSION=1
CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID=<from seed>
CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON=<binding root-ref → <VAULT_ROOT> subtree>
# plus the CONTEXT_ENGINE_DOGFOOD_* identity refs from adapters/http/dogfood.py:42-53
```

**3. Bootstrap schema, identity, and the source** (each a one-shot local
process; `set -a; source` both env files first):

```bash
uv run context-engine-control migrate
uv run context-engine-dogfood-seed ...            # org/user/membership + local release grant
uv run context-engine-control register-file-source \
  --organization-id <ORG> --display-name obsidian-vault \
  --root-ref <ROOT_REF> --idempotency-key <KEY>
uv run context-engine-control activate-change-feed --organization-id <ORG> --source-ref <SRC>
uv run context-engine-control scan               --organization-id <ORG> --source-ref <SRC>
```

⚠️ Per §0, register a **curated subtree** of the vault (or the bound-raise
issue lands first): the full vault is at ~93% of the 10,000-path baseline
bound. A subtree such as the permanent-notes directory keeps Phase 1 inside
proven limits and is itself a better first corpus than 9k mixed files.

**4. Run the two daemons and promote:**

```bash
uv run context-engine-worker --dispatch-files          # daemon 2
uv run context-engine-control promote-release \
  --organization-id <ORG> --evidence-file <reviewed evidence json>
CONTEXT_ENGINE_API_COMPOSITION=<dogfood value> \
  uv run context-engine-api --host 127.0.0.1 --port 8000  # daemon 1
curl -s http://127.0.0.1:8000/health                    # expect runtime_delivery: ACTIVE
```

**5. launchd plists** (sketch; instantiate under `~/Library/LaunchAgents/`,
keep the real files in `.context-engine/` per (c)). One example; the worker
plist is identical apart from label/args:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.stometa.context-engine.api</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd <DEPLOY_CHECKOUT> &amp;&amp; set -a
      &amp;&amp; source .context-engine/database.env
      &amp;&amp; source .context-engine/operators.env &amp;&amp; set +a
      &amp;&amp; exec uv run context-engine-api --host 127.0.0.1 --port 8000</string>
  </array>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string><DEPLOY_CHECKOUT>/.context-engine/api.log</string>
  <key>StandardErrorPath</key><string><DEPLOY_CHECKOUT>/.context-engine/api.err.log</string>
</dict></plist>
```

Both daemons fail closed while Postgres is down (Docker Desktop must be
running); `KeepAlive` restarts them once it returns. That dependency is
acceptable for Phase 1 and is monitored in Phase 3.

### Phase 2 — cadence (next 2–4 weeks)

- **Scheduled rescan**: launchd `StartCalendarInterval` (e.g. 03:00) running
  `context-engine-control scan` then `status`; the worker's `--dispatch-files`
  loop drains the scheduled imports. Scan is checkpoint-idempotent by design
  (ADR-0071), so overlapping/missed runs are safe.
- **Backup job**: the (b) `pg_dump` script on the same nightly schedule,
  before the rescan.
- **Promotion cadence**: keep promotion *explicit and manual* (weekly, or
  after meaningful corpus changes) — ADR-0073 deliberately refuses automatic
  post-scan promotion, and a stale-but-promoted Release still serves. A
  scripted `promote-release` wrapper that re-reads the reviewed evidence file
  makes the manual step one command.
- **Digest integration**: surface `control status` + `/health` output in the
  maintainer's existing nightly digest agent rather than building a new
  reporting channel.

### Phase 3 — monitoring and growth (as pull demands, per ADR-0062)

- **Health probe**: a small launchd interval job curling `/health` and
  `status`, alerting via the same local-notification pattern the maintainer
  already uses; alert on `runtime_delivery != ACTIVE`, scan age exceeding a
  day, or unpublished-path count growth (ADR-0072 gives exactly these
  fields).
- **Baseline-bound remediation**: product-lane issue to raise/partition the
  10,000-path bound with measurement, unlocking full-vault registration.
- **Full-vault + eval loop**: expand roots subtree-by-subtree; wire
  `make dogfood-eval` against the real corpus golden set.
- **Remote access, if ever**: ADRs first (see (a)); nothing in Phases 1–2
  pre-commits to it.

## Success criteria for Phase 1

- `/health` on 127.0.0.1 reports `runtime_delivery: ACTIVE`; a real
  `POST /v0/resolve` `Acquire` returns Evidence from vault notes.
- `launchctl list` shows both agents running; killing either process results
  in automatic restart; `make db-down && make db-up` round-trips with data
  intact.
- A backup file exists and the restore drill has been executed once against a
  scratch reset.
