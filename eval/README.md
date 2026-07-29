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
promotion authority. Production composition reads one dedicated opaque local
credential from the process environment and uses the fixed local authenticator;
callers cannot inject an authenticator or construct its verified identity. M1 is single-tenant,
and the maintainer is its sole privacy-responsible party. A designated
privacy-reviewer role does not exist,
and release-operator authority is deliberately insufficient: ReleaseManifest
publication authority must not acquire privacy authority implicitly.

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
the pilot content digest, authority, reason, and time. This co-located,
operator-editable record provides accidental-edit detection: ordinary later
edits are refused until the explicit `relock` operation appends a history entry.
It is not forgery-proof and does not authenticate a malicious operator who can
rewrite both the private corpus and its co-located lock; M1 deliberately adds no
signing/keyring boundary. Prior digest records remain intact during normal lock
and re-lock operations.

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
is pending preregistration, not part of this implementation. The report CLI
always reads that tracked file and exposes no caller-selected threshold option.
Any non-tracked threshold fixture used through the internal test seam is marked
`non_authoritative` in the report and can never render `PASS`; any configured
value without the recorded event is refused by the loader.

Security never waits for calibration. Any unauthorized Evidence,
wrong-Organization effect, or missing-context fallback forces the entire report
to `FAIL`, regardless of every quality score or sample count. These totals are
derived only by the in-process `SecurityHarness` from the run executor's typed
events, never accepted as caller-authored counters or reloaded JSON. The closed
non-violation states are `observed_clean`, `not_observed`, and `malformed`.
Only the harness can construct `observed_clean`; `not_observed`, an absent field,
malformed evidence, and any serialized claim of `observed` render the report
`REFUSED`, with counters left null rather than coerced to zero. A harness-observed
violation remains an absolute `FAIL` veto independent of thresholds and slices.

## Offline report

The CLI consumes a locked v1 set and a closed per-case observation document. It
requires the observation `caseRef` set to exactly equal the golden set, records
the blind judge model/profile identity, and refuses partial runs. Serialized
input cannot establish the security precondition, so the file-only command
emits `REFUSED`; an authoritative non-refused report requires the actual run
executor to carry its `SecurityHarness` results in-process into report assembly.
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

The first 26-case maintainer corpus is pending delivery. Converting it to v1,
building the 50-case locked pilot, preregistering sample floors, and recording a
real-corpus CLI report remain pending corpus work; synthetic tests fully exercise
the loader, composition validator, lock, judges, floors, privacy boundary, and
security veto meanwhile.
