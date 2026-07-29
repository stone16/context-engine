# Golden evaluation governance

ContextEngine has two schema generations. `golden/v0/schema.json` remains the
frozen contract for the first dogfood evaluator. `golden/v1/schema.json` adds
answerability, expected answers, claim-to-Evidence support lineage, partitions,
topic clusters, same-topic hard negatives, and the single-document,
cross-document, and temporal slices used by layered evaluation.

## Private corpus boundary

The maintainer corpus is private and untracked. It must live in a durable,
backed-up location outside every disposable repository worktree; this document
does not prescribe a machine-specific path. Golden files, lock history, lineage
maps, judge observations, and raw reports from that corpus remain outside Git.
The repository-local `.context-engine/` directory is ignored and may hold only
regenerable report output, never the sole durable corpus copy.

Files under tracked `eval/golden/` are schemas or wholly invented synthetic
fixtures. A tracked fixture may use only explicit `synthetic-` or
`placeholder-` values for query, answer, claim, path, and Evidence lineage. It
may never contain real, anonymized, lightly edited, or excerpted personal
content, note titles, paths, or Evidence references. A unit test enforces this
boundary over the tracked tree.

## Sanitized public subset

Moving a case from the private corpus to a public subset is a privacy decision.
The only configured authority is `maintainer`, recorded in
`eval/public-subset-governance.json`; every other principal is refused by the
promotion mechanism. The mechanism authenticates an opaque local credential and
accepts only its construction-sealed nominal maintainer identity, never a
caller-authored `"maintainer"` claim. M1 is single-tenant,
and the maintainer is its sole privacy-responsible party. A designated
privacy-reviewer role does not exist,
and release-operator authority is deliberately insufficient: ReleaseManifest
publication authority must not acquire privacy authority implicitly.

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
the pilot content digest, authority, reason, and time. Later edits are refused
until the explicit `relock` operation appends a new history entry; prior digest
records remain intact.

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
is pending preregistration, not part of this implementation.

Security never waits for calibration. Any unauthorized Evidence,
wrong-Organization effect, or missing-context fallback forces the entire report
to `FAIL`, regardless of every quality score or sample count. These totals are
derived from the harness's closed per-case event observations, never accepted
as caller-authored counters. Every case has a required typed security state:
`observed` with zero events is clean, while the distinct `not_observed` sentinel,
an absent field, or malformed evidence refuses report creation as an
unestablished precondition. None can be coerced to zero or rendered green.

## Offline report

The CLI consumes a locked v1 set and a closed per-case observation document. It
requires the observation `caseRef` set to exactly equal the golden set, records
the blind judge model/profile identity, requires the harness security
precondition for every case, and refuses partial runs. Report output must remain
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

The report exposes per-case hit/miss, exact `evidenceRecall` over the complete
authorized Package, and case pass rate. Package Evidence has no relevance-rank
contract, so this version does not report recall@k. Quality is `measured`;
Reliability and Budget remain `not-evaluated`. This report does not modify or
feed the release-gate report, and there is no CI threshold.

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
are not implemented by this runner: it imports the one fixed judge
factory owned by issue #129, which is solely responsible for case hit,
macro/micro Evidence recall, and slice breakdowns. Until #129 lands, the real
CLI deliberately fails closed with `retrieval judge is unavailable` rather
than degrading to a second metric implementation.

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
