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
The main environment deliberately contains no model framework: each local,
pinned model directory supplies a benchmark-only `context_engine_provider.py`
plugin and its fully resolved identity (repository id, immutable revision,
artifact digest, dimension, normalization, pooling, prompt prefixes, reduction,
precision, and batching).

The input follows `embedding-benchmark/input.schema.json`; the output follows
`embedding-benchmark/report.schema.json` and must be written below the ignored
`.context-engine/` directory. Retrieval scores are not implemented by this
runner. `--judge module:factory` injects the retrieval judge owned by issue #129,
which is solely responsible for case hit, macro/micro Evidence recall, and slice
breakdowns.

The maintainer corpus is private and pending delivery to a durable,
maintainer-controlled location outside disposable Git worktrees. Once available,
an operator may run:

```bash
uv run context-engine-embedding-benchmark run \
  --dataset "$DURABLE_GOLDEN_ROOT/embedding-benchmark-v1.json" \
  --primary-model-dir "$PINNED_MODEL_ROOT/qwen3-embedding-0.6b" \
  --baseline-model-dir "$PINNED_MODEL_ROOT/multilingual-e5-small" \
  --judge eval.retrieval_judge:create_judge \
  --output .context-engine/eval/embedding-benchmark-v1.json
```

The tracked frozen result contains metrics only. It currently records
`pending_corpus`; it must never contain queries, note titles, paths, excerpts,
or model weights. A losing primary is a valid benchmark outcome and is frozen as
`lose`, not treated as a runner error.
