# End-to-end dogfood walkthrough — File provider → Release → loopback Acquire

**Date**: 2026-07-28
**Machine**: maintainer laptop (Darwin), single human, loopback only
**Outcome**: the full Supply → Release → Runtime loop was proven on a bounded,
sanitized mirror of the maintainer's Obsidian vault. **Raw Obsidian ingestion
remains UNPROVEN** — see finding 1. Per STATUS.md discipline, nothing in this
document generalizes beyond exactly what ran.

This walkthrough exercises, in order: ADR-0035 (source registration),
ADR-0070 (change-feed activation), ADR-0056 (delete observations), ADR-0071
(bounded scan cycles), ADR-0059/0060 (leased worker dispatch), ADR-0036/0038
(closed Markdown compilation), ADR-0066 (pre-publication embeddings), ADR-0072
(source status), ADR-0073 (explicit release promotion), ADR-0069 (local
operator composition), ADR-0068 (loopback dogfood runtime), and the Issue #16
closed-capability gate.

## Corpus scoping

The vault (placeholder `$VAULT_ROOT`) holds 8,884 Markdown files. The
walkthrough registered a bounded subset of three top-level folders as three
File sources — an insights folder (25 files), a stock-analysis folder (60
files), and one project-campaign folder (31 files) — 116 files total.
Full-vault ingestion was not attempted.

## Precondition discovered first: the closed grammar refuses real notes

The active File-import grammar (`ACTIVE_FILE_IMPORT_MARKDOWN_CONFIG_VERSION =
"markdown-config-v1"`, `engine/supply/markdown.py:15`) accepts exactly one
`# Heading`, one blank line, and one single-line paragraph
(`adapters/parsers/markdown.py:117`), and classifies every other construct as
an `UnsupportedConstruct`. Measured against the 116-file subset:

| Grammar | Accepted | First refusal construct (count) |
|---|---|---|
| `markdown-config-v1`, raw notes | 0/116 | frontmatter_or_rule 103, blockquote 9, html 2, inline_code 1, nested_heading 1 |
| `markdown-config-v2`, raw notes | 0/116 | frontmatter_or_rule 103, blockquote 9, html 2, inline_code 1, link_or_image 1 |
| v1, frontmatter stripped | 0/116 | nested_heading 77, blockquote 26, emphasis 6, frontmatter_or_rule 3, inline_code 2, html 2 |
| v2, frontmatter stripped | 0/116 | blockquote 53, emphasis 47, html 8, frontmatter_or_rule 3, inline_code 3, link_or_image 2 |

Vault-wide, only 116/8,884 files compile naturally under v1, none of them in
the agreed subset. The coordinator-approved resolution (no engine change) was a
**disposable normalizer evidence spike** — allowed by AGENTS.md and never a
runtime foundation — living untracked at `.context-engine/normalize_vault.py`.
It flattens each note to the exact v1 shape (`# <filename-derived title>`,
blank line, whole note as one scrubbed prose line) and writes a git-ignored
mirror under `.context-engine/dogfood-mirror/`.

**Path mapping rule**: the mirror preserves each note's vault-relative path
exactly (`.context-engine/dogfood-mirror/<relative-path>` ↔
`$VAULT_ROOT/<relative-path>`), so every citation resolves 1:1 to a real note.
All 116 mirrored files (including Chinese-language notes) compile under the
active v1 grammar.

## Configuration

All secrets and identifiers live only in git-ignored, mode-0600 files under
`.context-engine/` (this run used `walkthrough.env`); none appear in this
document or any tracked file. The complete set required by the ADR-0069
operator composition, the scan/worker planes, promotion, and the ADR-0068
dogfood API is enumerated in the README sections "Run the bounded dogfood
API" and "Scan a local File source". Notes from this run:

- Operator opt-in is all-or-nothing: `LocalOperatorConfiguration.load`
  (`applications/operator_authentication.py:107`) requires all six operator
  variables together, each secret ≥ 32 bytes and pairwise distinct.
- The Control allowlist used was exactly the seven operations the README
  records for this workflow.
- `CONTEXT_ENGINE_WORKER_FILE_ROOTS_JSON` bound the three registered logical
  root refs to the three **mirror** directories (absolute paths).
- Embeddings: worker `twin` / dogfood `deterministic-twin-v1`, dimension 384.

## Command sequence (all verified on this machine)

```bash
make install
make db-up                       # worktree-scoped compose project
set -a; source .context-engine/database.env; source <secrets env>; set +a
uv run context-engine-control migrate            # → 20260727_0040
make security-gate               # any volume state; see finding 2 and ADR-0085
uv run context-engine-dogfood-seed \
  --organization-id "$CONTEXT_ENGINE_DOGFOOD_ORGANIZATION_ID" \
  --user-id "$CONTEXT_ENGINE_DOGFOOD_USER_ID" \
  --membership-id "$CONTEXT_ENGINE_DOGFOOD_MEMBERSHIP_ID" \
  --provision-release-operator-grant \
  --file-import-service-principal-id "$CONTEXT_ENGINE_WORKER_SERVICE_PRINCIPAL_ID"
# per source (×3): register → activate both capabilities → scan
uv run context-engine-control register-file-source --organization-id ... \
  --display-name ... --root-ref ... --idempotency-key ...   # ADR-0035
uv run context-engine-control activate-change-feed ...       # ADR-0070
uv run context-engine-control activate-delete-observations ... # ADR-0056
uv run context-engine-control scan ...                       # ADR-0071
# drain the queue (ADR-0059 leases; repeat until no_work)
uv run context-engine-worker --dispatch-file-once
uv run context-engine-control status ...                     # ADR-0072
uv run context-engine-control promote-release --organization-id ... \
  --evidence-file <reviewed evidence JSON>                   # ADR-0073
CONTEXT_ENGINE_API_COMPOSITION=dogfood-local-v1 \
  uv run context-engine-api --host 127.0.0.1 --port 8137     # ADR-0068
```

## Measured results

**Supply loop** (fresh volume, second full run):

- Scan: 25 + 60 + 31 paths observed, 116 changes accepted, 116 imports
  scheduled, 0 compilation refusals, 0 deletes.
- Worker drain: 116 dispatched, 0 refused, 147 s wall clock
  (~1.3 s/job; a first-round drain measured 126 s — see finding 4).
- Status per source: activeResourceCount 25 / 60 / 31, refusals [],
  `lastSuccessfulAcquisition: succeeded`, complete change baselines 25/60/31.
- Unchanged replay scan: same `advancedCursor`, 0 accepted, 0 scheduled —
  the ADR-0071 checkpoint idempotence claim held.

**Security gate**: `make security-gate` on a clean volume → 164 passed,
`M0 SECURITY PASS` (report under `.context-engine/security-gate/`).

**Promotion** (ADR-0073): four-gate evidence JSON assembled from real artifact
digests (security = the M0 release-gate report digest; reliability/quality/
budget = operator-attested walkthrough evidence digests — they are **not**
registered executable gates, which M0 records as not-evaluated). Result:

```json
{"activeGeneration":1,"activeRevisionCount":116,
 "indexProfileRef":"index-file-pgvector-deterministic-twin-v1",
 "manifestRef":"manifest-dogfood-8c78535c…"}
```

**Runtime** (`/health` → `runtime_delivery: ACTIVE`): five genuine Acquire
questions about subjects verified present by filename (a US-equity thesis, an
optical-supply-chain insight, a RAG-evaluation insight, a campaign
implementation plan, a foundry analysis) plus probes, all over
`POST /v0/resolve` with the bearer dogfood secret:

| Probe | Result |
|---|---|
| 5 real questions | 200 `resolved`, 1–4 evidence blocks each, coverage `sufficient` |
| Latency | 283–378 ms per resolve (median ≈ 310 ms, loopback, cold-ish) |
| Citation lineage | every evidence item carries source/resource/revision/fragment refs, `sourceAclEvidence`, policy epoch, decision/run refs |
| Provenance | all 12 cited blocks mapped uniquely back to mirror files whose vault-relative path exists in the real vault (12/12) |
| `packageBudget {"maxTokens":500}` | enforced: usage 238 tokens, evidence trimmed 4 → 1 |
| Wrong bearer | generic 401 `authentication_failed` |
| `continue` request | 200 `request_not_available`, non-retryable (closed capability gate) |
| Negative probe (topic outside the registered subset) | 200 `resolved`, coverage `sufficient`, 2 citations from the registered corpus — see finding 3 |

`context-engine-dogfood-eval query` (the maintainer caller) also worked
end-to-end against the same API. `make dogfood-eval` was **not** runnable:
`eval/golden/v0/golden-set.json` does not exist (only the schema), and the
eval README forbids inventing entries.

## Gaps found (dogfood feedback per ADR-0062)

1. **Markdown grammar vs. real notes (headline gap).** The active v1 grammar
   refuses 100% of the real subset (counts above); even v2 refuses 100%
   (frontmatter, blockquotes, inline emphasis/code, links). The engine's ONE
   real corpus cannot be ingested without an out-of-engine normalizer, so
   **raw Obsidian ingestion remains UNPROVEN**. The v1 three-line shape also
   forces whole-note flattening: every published note is one
   `fragment:paragraph:1`, so citation granularity is the entire note.
2. **Security gate was corpus-sensitive — fixed by ADR-0085.** With the dogfood
   corpus in the harness database, 4 of 164 gate tests failed: the registered
   evidence drove Alembic through a multi-revision downgrade chain, so with
   nested File lineage from another Organization retained, the recursive-path
   guard fired before the guard each test asserted (observed: `recursive File
   path downgrade requires no retained nested lineage` instead of the asserted
   accepted-change guard). Each registered assertion now exercises exactly its
   own revision's downgrade, and the gate records the retained-lineage counts it
   observed under `provenance.retainedFileLineage`, so a reader can tell a pass
   produced on a populated volume from one produced after a reset. No guard was
   deleted, tenant-scoped, or relaxed.
3. **Twin embeddings give no semantic relevance, and coverage cannot say
   "not in corpus".** SHAKE-256 twin vectors are content-hash noise: 0/5 real
   questions cited their topical note, and the negative probe still returned
   `sufficient` coverage with citations. Fail-closed applies to authorization,
   not relevance; ADR-0068 keeps external query embeddings `NOT_ACTIVE`, so
   this is inherent to the current activation, not a defect in the loop.
4. **Worker drain UX.** One process launch per job (~1.1–1.3 s each) made 116
   files take ≈ 2.5 minutes via `--dispatch-file-once`; the long-running
   `--dispatch-files` mode amortizes startup but emits no batch progress, so
   an operator cannot see ingestion advance without polling `status` per
   source.
5. **Operator ceremony.** The end-to-end run requires ~23 environment
   variables across five credential planes, three commands per source, and a
   hand-copied `sourceRef` per registration; there is no multi-source scan or
   status command.
6. **`make dogfood-eval` has no golden set.** The target needs a tracked
   20–50-entry maintainer golden set that does not exist yet.

Recommended issues for the coordinator to propose (none filed from this run):
"File provider: close the real-note Markdown grammar gap (measured 0/116
acceptance)", "Evaluate promoting markdown-config-v1 → v2 for File import",
"Security gate: make downgrade-guard evidence corpus-independent or document
clean-volume requirement", "Worker: batch/progress reporting for file
dispatch", "Operator UX: multi-source scan/status", "Provide the v0 golden
set".

## Evidence

Raw transcripts, package JSON, provenance mapping, and the promotion report
live under git-ignored `.context-engine/walkthrough-evidence/` (contains
personal note paths/content — never to be tracked). `git status` was verified
clean of vault content, personal titles, and secrets before hand-off.
