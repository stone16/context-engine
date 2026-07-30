# Worker batch progress and operator ceremony measurement

**Measured:** 2026-07-30 against the issue #136 worktree based on
`origin/main` at `7cd5616`, before this issue's implementation. This is a fresh
source audit of the current entry points, not a transcription of the earlier
dogfood walkthrough.

## Measurement boundary

The measured recurring refresh is: scan every already registered active File
source, let the dispatcher drain scheduled jobs, read every source's status,
then run the separate explicit Release refresh. Registration and one-time
capability activation are bootstrap and are excluded. The cold-shell command
count includes starting `--dispatch-files`; the already-running-daemon count is
also shown because that is the documented production mode.

An environment variable counts only when the current Python entry points read
it for this boundary. Role-specific database URLs and their role assertions
count separately. Optional settings, every unrelated `CONTEXT_ENGINE_*` name,
and the CLI evidence-file argument do not count. A hand-copied value is a value
read from one command's output and inserted into later recurring commands;
stable configured values and ordinary command arguments do not count.

## Current-tree environment audit

The exact required set is **27 variables**, unchanged by this issue:

- Operator opt-in (6): `CONTEXT_ENGINE_CONTROL_OPERATOR_SECRET`,
  `CONTEXT_ENGINE_RELEASE_OPERATOR_SECRET`,
  `CONTEXT_ENGINE_OPERATOR_ORGANIZATION_ID`,
  `CONTEXT_ENGINE_CONTROL_OPERATOR_OPERATIONS`,
  `CONTEXT_ENGINE_DOGFOOD_SECRET`, and
  `CONTEXT_ENGINE_WORKER_LEASE_SIGNING_KEY_HEX`.
- Database contracts (10): the URL and role variables for Control, Scheduler,
  Worker, Learning, and Release operator purposes. Their exact names are owned
  by `DatabasePurpose` and `ROLE_ENVIRONMENT_VARIABLES` in
  `engine/persistence/configuration.py`.
- File scan composition (7): `CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON`,
  `CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID`,
  `CONTEXT_ENGINE_DOGFOOD_PRINCIPAL_REF`,
  `CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID`,
  `CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_VERSION`,
  `CONTEXT_ENGINE_FILE_CHANGE_PROVIDER_SIGNING_KEY_HEX`, and
  `CONTEXT_ENGINE_FILE_CHANGE_CHECKPOINT_SIGNING_KEY_HEX`.
- Worker embedding composition (2):
  `CONTEXT_ENGINE_WORKER_EMBEDDING_PROVIDER` and
  `CONTEXT_ENGINE_WORKER_EMBEDDING_DIMENSION`.
- Explicit Release evaluation (2):
  `CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_VERSION` and
  `CONTEXT_ENGINE_RELEASE_EVALUATION_SIGNING_KEY_HEX`.

This count is higher than the historical approximate count because it includes
the current role-assertion variables and the current explicit embedding pair.
No credential is combined or removed.

The current-tree set can be re-derived from the owning constants without
rendering values:

```bash
uv run python - <<'PY'
from applications.file_root_configuration import WORKER_FILE_ROOTS_ENV
from applications.file_scan import (
    CHECKPOINT_SIGNING_KEY_ENV,
    DOGFOOD_MEMBERSHIP_ENV,
    DOGFOOD_MEMBERSHIP_VERSION_ENV,
    DOGFOOD_PRINCIPAL_ENV,
    PROVIDER_SIGNING_KEY_ENV,
    WORKER_SERVICE_PRINCIPAL_ENV,
)
from applications.operator_authentication import OPERATOR_ENVIRONMENT_VARIABLES
from applications.release_promotion import (
    RELEASE_EVALUATION_SIGNING_KEY_ENV,
    RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV,
)
from applications.worker import (
    _WORKER_EMBEDDING_DIMENSION_ENV,
    _WORKER_EMBEDDING_PROVIDER_ENV,
)
from engine.persistence.configuration import (
    DatabasePurpose,
    ROLE_ENVIRONMENT_VARIABLES,
)

purposes = (
    DatabasePurpose.CONTROL_PLANE,
    DatabasePurpose.SUPPLY_SCHEDULER,
    DatabasePurpose.SUPPLY_WORKER,
    DatabasePurpose.LEARNING,
    DatabasePurpose.RELEASE_OPERATOR,
)
names = set(OPERATOR_ENVIRONMENT_VARIABLES)
names.update(
    {
        WORKER_FILE_ROOTS_ENV,
        WORKER_SERVICE_PRINCIPAL_ENV,
        DOGFOOD_PRINCIPAL_ENV,
        DOGFOOD_MEMBERSHIP_ENV,
        DOGFOOD_MEMBERSHIP_VERSION_ENV,
        PROVIDER_SIGNING_KEY_ENV,
        CHECKPOINT_SIGNING_KEY_ENV,
        _WORKER_EMBEDDING_PROVIDER_ENV,
        _WORKER_EMBEDDING_DIMENSION_ENV,
        RELEASE_EVALUATION_SIGNING_KEY_VERSION_ENV,
        RELEASE_EVALUATION_SIGNING_KEY_ENV,
    }
)
names.update(purpose.environment_variable for purpose in purposes)
names.update(ROLE_ENVIRONMENT_VARIABLES[purpose] for purpose in purposes)
print(len(names))
print("\n".join(sorted(names)))
PY
```

## Before and after

The representative topology is the current three-Source dogfood topology.
`N` below is the number of already registered active Sources.

| Measure | Before (`N=3`) | After (`N=3`) | General form |
|---|---:|---:|---|
| Required environment variables | 27 | 27 | constant |
| Cold-shell commands | 8 | 4 | before `2N + 2`; after 4 |
| Commands with dispatcher already running | 7 | 3 | before `2N + 1`; after 3 |
| Distinct hand-copied `sourceRef` values | 3 | 0 | before `N`; after 0 |

Before, the cold-shell cycle is three `scan` commands, one worker start, three
`status --source-ref` commands, and one explicit `promote-release`. After, it
is one `scan-all`, one worker start, one source-wide `status`, and the same
explicit `promote-release`. The Release command is intentionally unchanged and
is never called as a side effect of the first three commands.

The command and hand-copy totals can be re-derived from the formulas used by
the table for the representative topology:

```bash
N=3
printf 'cold-before=%d cold-after=4\n' "$((2 * N + 2))"
printf 'daemon-before=%d daemon-after=3\n' "$((2 * N + 1))"
printf 'copied-before=%d copied-after=0\n' "$N"
```

The reduction is therefore command and locator ceremony only: **27 → 27
environment variables, 8 → 4 cold-shell commands (or 7 → 3 with the daemon
already running), and 3 → 0 hand-copied values**.
