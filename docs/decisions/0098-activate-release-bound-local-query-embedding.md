---
name: adr-0098-activate-release-bound-local-query-embedding
version: "1.0.0"
description: >
  Activate one hash-verified local Qwen query-embedding carrier with exact
  Release and Fragment profile lineage, mixed-profile refusal, internal budget
  enforcement, and promote-only activation. Use when composing or reviewing
  the local dogfood embedding carrier. Not for network providers or public
  cumulative model-accounting semantics.
---

# 0098. Activate release-bound local query embedding

- Status: accepted
- Date: 2026-08-02
- Refines: ADR-0066, ADR-0067, ADR-0068, ADR-0073, ADR-0096
- Related: ADR-0033, ADR-0047, [issue #147](https://github.com/stone16/context-engine/issues/147), [issue #217](https://github.com/stone16/context-engine/issues/217)
- Decision input: the tracked registry at `eval/embedding-benchmark/model-registry.json` and the owner-recorded real result on issue #128 (30 cases; Qwen 17 hits, e5 13 hits)

## Context

ADR-0066 requires every published Fragment to carry a validated stored vector,
ADR-0067 requires query embedding and vector discovery to remain in the current
Runtime transaction, and ADR-0073 fixes one explicit candidate/evaluate/promote
path as the only production publication authority. The loopback carrier admitted
by ADR-0068 still uses a deterministic embedding twin. It binds only that twin's
model/input identity to the Index profile and reports zero external provider
usage.

The offline benchmark has now produced a winner. On the frozen 30-case run,
`Qwen/Qwen3-Embedding-0.6B` retrieved 17 cases and
`intfloat/multilingual-e5-small` retrieved 13. Qwen won all three frozen Pareto
metrics; e5 was materially faster. The tracked model registry pins the Qwen
artifact and its 384-dimensional transformation. Activating that profile closes
the measured-recommendation-to-Runtime loop, but it also creates the dangerous
state where a Qwen query can be compared with vectors produced by the twin (or
vice versa). Dimension equality does not make those vector spaces compatible.

ADR-0096 independently requires a Release-bound tokenizer, one cumulative
resolve meter, and a reviewed new public contract version before model-backed
carriers publish cumulative Package/ContextRun usage. Frozen OpenAPI v0 rejects
nonzero provider-call, cost, and elapsed values. Issue #147 does not own that
contract migration. A narrow local activation is possible only if v0 stays
truthful and the local query-embedding debit remains an internal enforcement
fact until issue #217 introduces the reviewed public version.

## Decision

1. **The only newly active profile is the pinned local Qwen winner.** Its exact
   identity is: model id `Qwen/Qwen3-Embedding-0.6B`; immutable revision
   `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`; artifact digest
   `8cb25677d5be69ce6ac88ebbdfb5dad30980fee39c35c6324a583e325917eddc`;
   stored dimension 384; `last_token` pooling; query prefix `Instruct: Given a
   web search query, retrieve relevant passages that answer the query\nQuery:`;
   empty document prefix; float32 precision; and transformation pipeline
   `l2 -> truncate 1024->384 -> l2`. The tracked registry remains the
   machine-readable source. A local adapter verifies every registered artifact
   digest before and after backend construction, disables remote code, and uses
   local files only. Missing or changed bytes refuse composition; model weights
   are never committed.
2. **Provider profile identity is a closed value.** A production
   `EmbeddingProviderProfile` is constructible only with a bounded nonblank
   model id, immutable revision, artifact digest, dimension, pooling, query and
   document prefixes, precision, transformation pipeline, and batch size. Its
   canonical document produces one domain-separated profile digest. An omitted,
   malformed, mutable, or unknown identity field is a construction refusal;
   dimension alone is never a compatibility identity.
3. **The Release and every stored vector carry the same identity.** The
   ReleaseManifest's Index profile contains the complete embedding-provider
   profile document and digest. Each Fragment vector records the profile digest
   that produced it. Runtime observes the complete active Release profile in the
   retained current-UserActor transaction, and ContextPackage provenance resolves
   through its opaque release-manifest activation reference to that immutable
   profile. Two otherwise identical Releases selecting different embedding
   profiles have different lineage and manifest digests.
4. **Any mixed profile refuses before ANN ordering.** Runtime validates the
   composed query provider's profile against the active Release before embedding
   the query. The retained database transaction then verifies that every
   in-scope active Fragment carrying a vector has the exact active profile digest
   before executing HNSW discovery. A missing, unknown, or different digest —
   including a same-dimension digest — raises the content-free embedding
   unavailability category and returns the generic closed resolve refusal. It
   never filters mismatched rows and continues, never falls back to the twin or a
   second model, and never presents lexical-only retrieval as complete.
5. **A profile change requires a complete re-embed before promotion.** File
   publication identity includes the embedding profile digest, so unchanged
   source bytes under a different profile create a new immutable Revision rather
   than taking the content no-op. During the re-embed interval, an old active
   Release observing new-profile active Fragments refuses under clause 4; this
   bounded local availability cost is accepted instead of serving mixed vectors.
   Candidate construction and promotion both verify that every Fragment of every
   candidate active Revision has exactly one non-null vector under the candidate
   profile, and that the residual count for any prior/missing profile is zero.
   A failed or partial re-embed cannot promote. Historical Revisions may retain
   their historical vectors; "full corpus" means the exact current active
   Revision set selected by the candidate snapshot.
6. **Local provider failures fail closed.** Provider load failure, timeout,
   inference exception, wrong cardinality, wrong dimension, non-finite values,
   all-zero vectors, or malformed output yields
   `EmbeddingProviderUnavailable`. Supply leaves the acquired publication
   recoverable without activating it. Runtime returns a generic refused resolve
   and performs no ANN lookup, fallback embedding, or degraded success.
7. **Query embedding is budgeted internally while v0 remains truthful.** One
   internal `PackageBudgetMeter` is created from the effective PackageBudget
   before query embedding. The query stage reserves its maximum provider-call,
   cost, and elapsed bounds before inference, refuses without a provider call on
   exhaustion, commits actual local usage on success, and commits the reserved
   maximum when a call occurred but its output is unusable. For frozen v0 only,
   `budgetUsage.tokens` retains `utf8-byte-budget-v1` Package packing semantics,
   while `providerCalls`, `costMicrounits`, and `elapsedMs` describe the v0
   generation/egress carriers represented by that public contract. The local
   internal query-embedding ledger is deliberately outside those v0 fields, so
   their emitted zeros remain truthful rather than falsified execution totals.
   No public or ordinary ContextRun claim describes those zeros as cumulative
   resolve usage. Issue #217 owns the new public cumulative meaning required by
   ADR-0096 clauses 2–4.
8. **Only ADR-0073 promotion activates the profile.** Supply may publish
   re-embedded immutable Revisions and evaluation may inspect a candidate, but
   neither changes the active embedding profile. The explicit local release
   operator observes the current corpus, constructs one Qwen-bound candidate,
   evaluates it, and calls `ContextLearning.promote`; the same atomic promotion
   transaction performs the final full-corpus profile check before advancing the
   active pointer. Ingestion, scan, re-embed, evaluation, bootstrap, migration,
   ContextControl, and API startup have no alternate activation write.
9. **Network provider activation remains blocked.** This decision activates
   only local inference over hash-verified on-disk Qwen artifacts. External or
   network embedding endpoints remain `NOT_ACTIVE` in Runtime until issue #217
   lands the ADR-0096 public contract version and a later explicit activation
   decision records credentials, egress, timeout, cost, and settlement evidence.

## Consequences

- Query/document vector compatibility becomes a Release invariant instead of a
  convention hidden behind the shared dimension.
- Re-embedding identical source content creates replacement Revisions and may
  make local Runtime unavailable between the first replacement and the final
  promotion. That downtime is observable and fail-closed; a shadow vector store
  and zero-downtime atomic corpus swap are deferred until measured need justifies
  a second persistence representation.
- Frozen v0 remains byte-for-byte and semantically truthful, at the price of
  keeping local query-inference accounting internal and unavailable to ordinary
  consumers until issue #217.
- Rollback to a Release whose active corpus/profile no longer matches refuses;
  the operator must first restore a complete verified corpus under that profile
  and promote through the same path.
- The real-corpus measured acceptance run and numeric pilot artifact remain in
  the separate dogfood-e2e worktree. This decision records the #128 winner but
  does not claim that issue #147's activated-corpus acceptance has executed.

## Revisit trigger

Revisit when issue #217 introduces the reviewed cumulative-accounting contract,
when a network embedding provider is proposed, when measured re-embed downtime
requires a shadow-vector atomic swap, when a second local embedding profile is
considered, or when rollback must retain two simultaneously queryable profile
generations. Each case requires explicit compatibility, budget, and promotion
evidence; none may weaken mixed-profile refusal.
