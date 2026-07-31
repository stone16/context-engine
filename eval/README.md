# Golden evaluation governance

ContextEngine has two schema generations. `golden/v0/schema.json` remains the
frozen contract for the first dogfood evaluator. `golden/v1/schema.json` adds
answerability, expected answers, claim-to-Evidence support lineage, partitions,
topic clusters, same-topic hard negatives, and the single-document,
cross-document, and temporal slices used by layered evaluation.

## Private corpus boundary

The maintainer corpus is private and untracked. It must live in a durable,
backed-up location outside every disposable repository worktree, configured as
`CONTEXT_ENGINE_GOLDEN_ROOT`; no worktree-local default exists. The CLI refuses
golden-set and lock paths outside that root and refuses repository or
`.context-engine` corpus storage, including roots under any Git worktree.
Golden files, lock history, lineage maps,
judge observations, and raw reports from that corpus remain outside Git.
The repository-local `.context-engine/` directory is ignored and may hold only
regenerable report output, never the sole durable corpus copy.

Files under tracked `eval/golden/` are schemas or wholly invented synthetic
fixtures. A tracked fixture may use only explicit `synthetic-` or
`placeholder-` values for query, answer, claim, path, and Evidence lineage. It
may never contain real, anonymized, lightly edited, or excerpted personal
content, note titles, paths, or Evidence references. A unit test enforces this
boundary over the tracked tree.

The repo-local Claude Code consumer appends each real question as a golden-set
candidate to `claude-code-candidates-v1.jsonl` under the configured durable
root. Each owner-only record contains only a generated candidate ref, capture
instant, exact question, and closed resolve disposition. It deliberately omits
Package content and every corpus path; the maintainer reviews and annotates
expected Evidence separately before admitting a candidate to a golden set.

## Durable storage, backup, and recovery

The corpus root and its backup root are two separate durable locations, each
configured in the environment and each outside every Git worktree
(ADR-0082). Neither may contain the other, so one deletion cannot remove both
copies, and neither has a worktree-local default:

| Variable | Holds |
|---|---|
| `CONTEXT_ENGINE_GOLDEN_ROOT` | the working corpus: golden set, lock history, lineage map, judge observations |
| `CONTEXT_ENGINE_GOLDEN_BACKUP_ROOT` | immutable timestamped snapshots of that whole root |

Both roots are refused when they are relative, missing, a symlink, inside any
Git worktree, or under an ignored `.context-engine` directory. The only ignored
in-repository location is `.context-engine/`, which holds regenerable report
output and never the sole durable copy; the durable roots live outside the
repository, so no `.gitignore` rule can — or should — name them.

`context-engine-golden-backup` takes no path argument. Roots come from the
environment, and every line it prints is counts, a snapshot instant, and
digests, so no corpus path or filename can reach a terminal, a transcript, or a
refusal message.

```bash
uv run context-engine-golden-backup backup \
  --recorded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
uv run context-engine-golden-backup list
uv run context-engine-golden-backup verify
```

Re-running `backup` on unchanged content records nothing and reports
`unchanged`. Changed content records a new snapshot. A backup that is not newer
than the newest recorded snapshot is refused; `--allow-older` records it as
history, and even then the newer snapshot remains the one recovery restores. An
already recorded snapshot instant is never overwritten, with or without that
flag.

Each snapshot is staged, verified byte for byte, fsynced, and then renamed into
place, so an interrupted run leaves neither a snapshot nor a staging directory.
A leftover staging entry refuses the next backup until the operator inspects
it. `verify` refuses truncation, corruption, missing content, unexpected
content, a manifest that disagrees with its own record, and any file or
directory readable beyond its owner.

Recovery verifies before it writes, refuses a corrupted snapshot, and refuses a
non-empty destination, so it can never overwrite a working copy:

```bash
mkdir -p "$CONTEXT_ENGINE_GOLDEN_ROOT"
chmod 700 "$CONTEXT_ENGINE_GOLDEN_ROOT"
uv run context-engine-golden-backup recover
uv run context-engine-eval validate \
  --golden-set "$GOLDEN_SET" \
  --lock "$GOLDEN_LOCK"
```

The recovered corpus must report the same case count and set digest as before
the loss. `recover` restores the newest snapshot; `--snapshot` names an older
one. These digest guarantees detect accident and corruption under M1's single
trusted local operator threat model. They are not forgery-proof: an operator
who deliberately rewrites both a snapshot and its manifest can produce a
self-consistent backup, exactly as the co-located lock chain below can be
recomputed.

## Lineage recapture after Release promotion

Golden expectations bind to exact `source/resource/revision/fragment` refs.
Publication is immutable (ADR-0018) and promotion advances one Release
(ADR-0033), so a promotion can leave an expectation pointing at a Revision
that no longer resolves. Scoring such a case would report `evidence_recall = 0`
and look like a quality regression, so the loader reports it as
`stale_lineage`, excludes it from every judge input, and refuses the report.

The lineage map records exactly the Evidence lineage that resolves in one
promoted Release. It lives beside the corpus in the durable root, is never
tracked, and follows `context-engine-golden-lineage-map-v1`: `schemaVersion`,
`releaseRef`, an aware-UTC `capturedAt`, and a nonempty unique `entries` array
of four-field lineage objects.

After **every** Release promotion, run these steps before evaluating anything:

1. Back up first, so recapture is reversible.

   ```bash
   uv run context-engine-golden-backup backup \
     --recorded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   ```

2. Recapture the map for the promoted Release into
   `$GOLDEN_LINEAGE_MAP`, writing one entry per Fragment that the promoted
   Release resolves, with `releaseRef` set to that Release and `capturedAt` set
   to the capture instant.

3. Check the corpus against the recaptured map. Exit status 0 means every case
   still resolves; a nonzero status names how many cases went stale and no
   ref value is printed.

   ```bash
   uv run context-engine-eval lineage-check \
     --golden-set "$GOLDEN_SET" \
     --lock "$GOLDEN_LOCK" \
     --lineage-map "$GOLDEN_LINEAGE_MAP"
   ```

4. For each stale case, repoint its expected Evidence at the current Revision
   in the corpus, then rerun step 3 until it passes. A stale case is a
   bookkeeping repair, never a score.

5. Pass the map to the report so a stale set refuses instead of producing a
   number. Re-lock the pilot only when the corpus content itself changed.

   ```bash
   uv run context-engine-eval report \
     --golden-set "$GOLDEN_SET" \
     --lock "$GOLDEN_LOCK" \
     --run "$EVAL_RUN" \
     --lineage-map "$GOLDEN_LINEAGE_MAP" \
     --output .context-engine/eval/golden-v1-report.json \
     --generated-at "$GENERATED_AT"
   ```

The map stays captured rather than derived from a live index. The run executor
replays queries through a deliberately non-enumerating seam, so it observes
which Evidence one resolve delivered but cannot ask whether an expectation's
Revision still resolves. Replacing the captured map with a live check needs a
seam that can answer that question.

## Sanitized public subset

Moving a case from the private corpus to a public subset is a privacy decision.
The only configured authority is `maintainer`, recorded in
`eval/public-subset-governance.json`; every other principal is refused by the
promotion authority. Production composition reads one dedicated opaque local
credential from the process environment and uses the fixed local authenticator;
callers cannot inject an authenticator or construct its verified identity. M1 is single-tenant,
and the maintainer is its sole privacy-responsible party. A designated
privacy-reviewer role does not exist,
and release-operator authority is deliberately insufficient: ReleaseManifest
publication authority must not acquire privacy authority implicitly.

The verified-identity and authority types reject ordinary direct construction
and subclassing, and production composition supplies the fixed local verifier.
These seals prevent accident and misuse through every supported path. They are
not tamper-proof against an in-process adversary deliberately bypassing Python
constructors, importing private module objects, mutating instances, or
monkeypatching code. That adversary is outside M1's single trusted local
operator threat model.

No public-subset promotion effect or command exists in M1. The authority check
and tracked configuration are an enforced preparatory seam only; they do not
copy, rewrite, publish, or activate a case. Adding that effect requires its own
privacy-reviewed operation, and the release-operator path remains outside it.

An approved public case still may not publish or transform personal material.
It must be independently rewritten as a wholly invented synthetic case, pass
the tracked-tree privacy check, and contain no original title, path, excerpt,
query, expected answer, claim, or lineage. This evaluation mechanism produces
reports and candidates only; it has no ReleaseManifest activation authority.

## Composition and locking

A complete v1 set is loaded atomically and fails closed on any malformed case.
Its counted composition is enforced: at least 20 dev cases, exactly 50 pilot
cases, at least five unanswerable pilot cases, and at least one same-topic hard
negative in every pilot topic cluster.

The pilot cannot be evaluated without a lock record. Initial locking records
the pilot content digest, authority, reason, and time. Every history entry
validates its values and binds the preceding entry digest, so edits to prior
history are refused by this accidental-edit detection. This co-located,
operator-editable chain is not forgery-proof and does not authenticate a malicious operator who can rewrite
both the private corpus and its co-located lock; M1 deliberately adds no
signing/keyring boundary.

```bash
uv run context-engine-eval lock \
  --golden-set "$GOLDEN_SET" \
  --lock "$GOLDEN_LOCK" \
  --authority maintainer \
  --reason initial-pilot-lock \
  --recorded-at "$RECORDED_AT"

uv run context-engine-eval validate \
  --golden-set "$GOLDEN_SET" \
  --lock "$GOLDEN_LOCK"
```

The shell variables above deliberately point to the operator's durable private
storage; no worktree-local default is provided.

## Feedback triage and governed case intake

Captured feedback becomes a curation proposal only after the Learning database
role resolves its Organization and feedback reference through the narrow inbox
function. That trusted projection binds the item to its exact ContextRun,
ContextPackage reference and digest, generation-bound Release reference, and
complete citation Evidence lineage. Missing or partial binding refuses the item;
the workflow never substitutes a DecisionAudit denial or denied object detail.
Triage uses only the closed `source`, `visibility`, `retrieval`, `assembly`, and
`evaluation` categories.

`context-engine-eval feedback-candidate` accepts the feedback locator plus one
private v1 case from either the durable corpus root or an ignored
`.context-engine/` directory and writes only a mode-0600 `CurationCandidate`
under the same two private storage boundaries. Its terminal output contains a
digest, never an input or output path. Caller-authored feedback projections are
not accepted. A reviewed candidate's dev case enters the durable corpus with
`context-engine-eval feedback-intake`; the command verifies the candidate digest
and existing pilot lock, validates the new case, proves the locked pilot digest
is unchanged, then replaces the corpus atomically. A pilot case is refused here:
admitting or changing pilot cases still requires the explicit existing `relock`
ceremony. Any fixture tracked under `eval/golden/` remains subject to the stricter
placeholder-only privacy scan.

Authoritative evaluation reports that ran with a lineage map now record the
exact `release.releaseRef` established by that map. `compare-releases` accepts
only tracked-threshold reports with executor-observed security and a resolved
lineage check over the same golden digest, then renders per-layer/per-slice
counts, scores, and deltas. `REFUSED` and `NON_AUTHORITATIVE` reports are rejected.
While thresholds are pending preregistration, the same slice observations may be
reported only with `PENDING_PREREGISTRATION` status and without a verdict.

This workflow owns no scheduler, ReleaseManifest operation, active pointer,
rollback, promotion call, or release-operator grant surface. It produces only
candidates and reports; every current manifest continues to select
`curation_off`, and publication remains the explicit ContextLearning promotion
transaction described by ADR-0033.

## Layers, floors, and thresholds

Retrieval reports case hit plus macro- and micro-averaged Evidence recall.
Citation reports lineage resolvability, claim support, and required-claim
completeness from exact content-free lineage. Answer reports attributable blind
0/1/2 judgments normalized to a fraction, with a critical contradiction forced
to zero and explicit refusal semantics.

Every layer is also reported independently for every slice. Each slice record
includes the observed case count, point estimate, and Wilson 95% confidence
interval. Too little data is `insufficient_data`; a threshold awaiting
preregistration is `pending_preregistration`. Neither status is green.

M1 intentionally defines no quality threshold numbers: the M1 epic exit is
daily local use plus a green security gate, while score thresholds belong to a
later milestone. `eval/thresholds/v1.json` therefore stores typed pending values
for answer/refusal and all nine `(layer, slice)` floors. Pending is distinct
from zero and absence and cannot be coerced to a permissive default. Once the
pilot composition is known, the maintainer preregisters sample sizes and scores
and may record exactly one post-pilot calibration event. That maintainer action
must bind the pilot digest, old and new threshold values, reason, and UTC time;
the latest event must exactly bind the active tracked configuration. The action
is pending preregistration, not part of this implementation. The report CLI
always reads that tracked file and exposes no caller-selected threshold option.
Any non-tracked threshold fixture used through the internal test seam is marked
`non_authoritative` in the report and can never render `PASS`; any configured
value without the recorded event is refused by the loader.

Threshold and security-result types reject subclassing, and their normal
constructors require private module-owned construction inputs. These seals
prevent accident and misuse through supported paths: callers cannot choose a
threshold document, omit a calibration event for active values, obtain `PASS`
from non-tracked thresholds, inject a run seam, or mint an observed-clean
result through the CLI or another supported entry point. They are not
unforgeable or tamper-proof
against an in-process adversary deliberately using `object.__new__`, importing
private module objects, mutating instances, monkeypatching code, or standing up
a counterfeit Runtime on the configured loopback address. The single
trusted local operator threat model does not include such an adversary.

Security never waits for calibration. Any unauthorized Evidence,
wrong-Organization effect, or missing-context fallback forces the entire report
to `FAIL`, regardless of every quality score or sample count. Those totals are
derived by the run executor from typed hard-oracle events it observed itself
(see the `execute` command below). Caller-authored or reloaded JSON can
establish neither clean counters nor a violation. `not_observed`, an absent
field, malformed evidence, and any serialized claim of `observed` render the
overall report `REFUSED` with reason
`no_run_executor_security_observation`, while retrieval, citation, and answer
metrics still compute normally. This refusal is a deliberate typed precondition
outcome, not a transient error or score failure. The closed non-violation states
remain `observed_clean`, `not_observed`, and `malformed`, and one observed
violation is an absolute `FAIL` veto independent of thresholds and slices.

If evaluation later accepts untrusted callers, becomes multi-tenant, or moves
to a remote runner, the supported-path trust boundary above is no longer
sufficient. That change requires its own ADR and threat model.

## Executed report

`execute` is the only command that can produce a non-refused report, because it
is the only one that runs the cases. It replays every golden query through the
tracked run seam — `dogfood-loopback-resolve-acquire-v1`, the frozen resolve
caller over the active loopback dogfood composition (ADR-0068) — and derives
from each delivered ContextPackage what a run establishes: the observed Evidence
lineage, whether the Runtime refused, and the typed security events. The seam is
composed from the process environment inside the executor, so the command takes
no transport, callback, or counter, and `observed_clean` cannot exist without
responses the executor fetched itself.

The executor observes three hard oracles. Delivered Evidence that does not carry
its complete enclosing decision binding — lineage, projected fields,
`sourceAclEvidence` kind, and a matching `decisionRef`, `policyEpoch`,
`policySnapshotRef`, `purpose`, `authorizationAsOf`, and `runRef` — is
`unauthorized_evidence`. Content that is not grounded in delivered Evidence, or
a Package whose coverage disagrees with what it delivered, is
`missing_context_fallback`. A resolve delivered under a different audience
binding than the rest of the run, or under none, is
`wrong_organization_effect`. What the seam cannot see is stated rather than
guessed: the public wire is non-enumerating, so no Organization identifier is
visible and audience-binding consistency is the observable form of the third
oracle. An unreachable seam, a structurally unusable response, or a coverage
state this seam does not produce refuses the whole run rather than scoring it.

Refusal is observed rather than declared: an evidence-free Package means the
Runtime refused. That fail-closed refusal is a quality signal, never a security
event, so revoking access to an expected Resource collapses recall and scores
the answer layer as an incorrect refusal while the run stays `observed_clean`.

Because the seam delivers a ContextPackage and not a generated answer, the blind
answer layer still comes from a judge. `--judgments` takes a closed
`context-engine-eval-judgment-v1` document holding only judge output — blind
score, critical contradiction, and produced claims — and is refused if it
carries an `observedEvidence`, `refused`, `securityObservation`, or counter
field, because those are what the run observes. It lives beside the corpus in
the durable root and is never tracked:

```json
{
  "schemaVersion": "context-engine-eval-judgment-v1",
  "answerJudge": {"modelRef": "...", "profileRef": "..."},
  "cases": [
    {
      "caseRef": "...",
      "blindScore": 2,
      "criticalContradiction": false,
      "claims": [
        {
          "claimRef": "...",
          "citedEvidence": [
            {
              "sourceRef": "...",
              "resourceRef": "...",
              "revisionRef": "...",
              "fragmentRef": "..."
            }
          ]
        }
      ]
    }
  ]
}
```

The command reads the seam destination and bearer from
`CONTEXT_ENGINE_DOGFOOD_BASE_URL` and `CONTEXT_ENGINE_DOGFOOD_SECRET`, the same
loopback contract the dogfood caller uses, and refuses a golden set that retains
that bearer value anywhere.

```bash
uv run context-engine-eval execute \
  --golden-set "$GOLDEN_SET" \
  --lock "$GOLDEN_LOCK" \
  --judgments "$EVAL_JUDGMENTS" \
  --lineage-map "$GOLDEN_LINEAGE_MAP" \
  --output .context-engine/eval/golden-v1-report.json \
  --generated-at "$GENERATED_AT"
```

The executor produces reports and nothing else. It holds no ReleaseManifest
activation, promotion, or rollback authority, and acquires none by running.

## Offline report

The `report` command consumes a locked v1 set and a closed per-case observation
document. It requires the observation `caseRef` set to exactly equal the golden
set, records the blind judge model/profile identity, and refuses partial runs.
Serialized input cannot establish the security precondition, so this file-only
command emits `REFUSED` for `no_run_executor_security_observation` no matter how
its metrics score. Every report records which seam produced it under
`run.executedSeamRef`; a file-only report records `null`.
Report output must remain
within a real ignored `.context-engine/` directory; path traversal and symlink
escapes are refused.

```bash
uv run context-engine-eval report \
  --golden-set "$GOLDEN_SET" \
  --lock "$GOLDEN_LOCK" \
  --run "$EVAL_RUN" \
  --output .context-engine/eval/golden-v1-report.json \
  --generated-at "$GENERATED_AT"
```

Both commands expose per-case hit/miss, exact `evidenceRecall` over the complete
authorized Package, and case pass rate. Package Evidence has no relevance-rank
contract, so this version does not report recall@k. Quality is `measured`;
Reliability and Budget remain `not-evaluated`. Neither report modifies or
feeds the release-gate report, and there is no CI threshold.

## Offline embedding comparison

`context-engine-embedding-benchmark` is an offline evaluation CLI. It compares
the exact 384-dimensional primary and baseline model identities required by
issue #128 and never composes an `EmbeddingProvider` into Supply or Runtime.
The main environment deliberately contains no model framework. The closed
`sentence-transformers` backend is installed explicitly with
`uv sync --extra benchmark`. Each local pinned model directory is the exact
complete `SentenceTransformer` snapshot declared by the tracked registry: its
weight, model/config, tokenizer, modules, and pooling files must match the
registry's closed path-and-SHA-256 artifact list, with no missing or extra
files. The tracked
`embedding-benchmark/model-registry.json`, not run input or a local manifest,
owns the expected repository id, immutable revision, artifact digest,
dimension, complete transformation pipeline, pooling, prompt prefixes,
precision, and batching. See that registry for the exact ordered pipelines;
both emit unit-norm 384-dimensional vectors under the same geometry. Unknown
models and local identity overrides are refused, and the runner hashes every
on-disk artifact against the tracked expected digests before and after the
backend loads the model.

`SentenceTransformer` requires a directory path and reopens its artifacts, so
the two checks do not create a tamper-proof snapshot. A concurrent writer could
swap files during backend construction and restore the registered bytes before
the post-load check. That adversary is outside M1's single trusted local operator
threat model: the runner provides provenance under that trusted operator, not
protection against adversarial concurrent writes. If an untrusted party gains
concurrent write access to model storage, stronger snapshot or locking semantics
require their own ADR.

The input follows `embedding-benchmark/input.schema.json`; the output follows
`embedding-benchmark/report.schema.json` and must be written below the ignored
`.context-engine/` directory. The input includes the typed RFC 8785/SHA-256
`sha256-rfc8785-accidental-edit-detection-v1` lock. It detects accidental edits,
truncation, and re-serialization performed without relocking, but it is not a
defense against deliberate forgery: the M1 threat model is one trusted local
operator who can recompute a colocated digest. If the boundary changes to an
untrusted caller or remote runner, signing returns as its own ADR. All benchmark
JSON entry points reject out-of-range or non-finite numbers and bound document
size, nesting, strings, and containers with a typed refusal. Retrieval scores
are provided through the one fixed adapter to the judge owned by issue #129,
which is solely responsible for case hit, macro/micro Evidence recall, and
slice breakdowns. The real CLI fails closed with
`retrieval judge is unavailable` if that adapter cannot be loaded rather than
degrading to a second metric implementation.

The maintainer corpus is private and pending delivery to a durable,
maintainer-controlled location outside disposable Git worktrees. Once available,
an operator may run:

```bash
uv run context-engine-embedding-benchmark run \
  --dataset "$DURABLE_GOLDEN_ROOT/embedding-benchmark-v1.json" \
  --backend sentence-transformers \
  --primary-model-dir "$PINNED_MODEL_ROOT/qwen3-embedding-0.6b" \
  --baseline-model-dir "$PINNED_MODEL_ROOT/multilingual-e5-small" \
  --output .context-engine/eval/embedding-benchmark-v1.json
```

The tracked frozen result contains metrics only. It currently records
`pending_corpus`; it must never contain queries, note titles, paths, excerpts,
or model weights. The model verdict uses Pareto dominance across case hit,
macro Evidence recall, and micro Evidence recall: one model wins only when it is
no worse on all three and strictly better on at least one. Exact equality is a
tie; mixed wins are `inconclusive`. Per-slice results remain diagnostic and no
weighted composite or tiebreak manufactures a winner. A losing or inconclusive
primary is a valid benchmark outcome, not a runner error.

The issue remains open while the corpus is pending. A real-model run, frozen
numeric result, actual model verdict, and measured comparison to the standing
3.8% twin baseline are not complete until that durable corpus arrives.

The first 26-case maintainer corpus is pending delivery. Converting it to v1,
building the 50-case locked pilot, preregistering sample floors, and recording a
real-corpus CLI report remain pending corpus work; synthetic tests fully exercise
the loader, composition validator, lock, judges, floors, privacy boundary, and
security veto meanwhile.
