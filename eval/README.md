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
promotion mechanism. M1 is single-tenant and the maintainer is its sole
privacy-responsible party. A designated privacy-reviewer role does not exist,
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
is pending preregistration, not part of this implementation.

Security never waits for calibration. Any unauthorized Evidence,
wrong-Organization effect, or missing-context fallback forces the entire report
to `FAIL`, regardless of every quality score or sample count.

## Offline report

The CLI consumes a locked v1 set and a closed per-case observation document. It
requires the observation `caseRef` set to exactly equal the golden set, records
the blind judge model/profile identity, and refuses partial runs.

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
