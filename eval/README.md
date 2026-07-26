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
