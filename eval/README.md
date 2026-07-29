# Dogfood quality evaluation

`golden/v0/schema.json` is the frozen schema for the first maintainer-provided
query set. A completed tracked `golden/v0/golden-set.json` must contain 20–50
real queries; agents must not invent or paraphrase entries to fill the set.

Each expected Evidence item carries:

- `path`: the maintainer's repository-relative note path, for human review;
- `sourceRef`, `resourceRef`, `revisionRef`, and `fragmentRef`: the exact opaque
  lineage copied from a successful ContextPackage, which is used for scoring.

The path is never converted into authority or re-derived into a Runtime
identifier. Within a case, the complete four-part lineage must be unique even
when paths differ; this semantic uniqueness check supplements JSON Schema's
whole-object `uniqueItems` check.

Capture the refs with the real caller after importing and releasing the exact
corpus. Set `CONTEXT_ENGINE_DOGFOOD_BASE_URL` to the loopback host and port used
by `context-engine-api`; `context-engine-api --help` is the source of truth for
its CLI configuration:

```bash
uv run context-engine-dogfood-eval query 'the real recurring question'
```

The command calls only `POST /v0/resolve` and renders the authorized block plus
its public source/resource/revision/fragment citation. The bearer secret comes
from `CONTEXT_ENGINE_DOGFOOD_SECRET` and is never accepted as an argument,
printed, or stored in the golden set.

Replay the full set and write a deterministic report:

```bash
uv run context-engine-dogfood-eval run \
  --golden-set eval/golden/v0/golden-set.json \
  --output .context-engine/eval/dogfood-v0-report.json
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
dimension, normalization, pooling, prompt prefixes, reduction, precision, and
batching. Unknown models and local identity overrides are refused, and the
runner hashes every on-disk artifact against the tracked expected digests before
it embeds anything.

The input follows `embedding-benchmark/input.schema.json`; the output follows
`embedding-benchmark/report.schema.json` and must be written below the ignored
`.context-engine/` directory. The input includes the typed RFC 8785/SHA-256
`sha256-rfc8785-accidental-edit-detection-v1` lock. It detects accidental edits,
truncation, and re-serialization performed without relocking, but it is not a
defense against deliberate forgery: the M1 threat model is one trusted local
operator who can recompute a colocated digest. If the boundary changes to an
untrusted caller or remote runner, signing returns as its own ADR. Retrieval
scores are not implemented by this runner: it imports the one fixed judge
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
