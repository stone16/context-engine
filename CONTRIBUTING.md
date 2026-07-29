# Contributing to ContextEngine

Thanks for taking the time to look at this project.

Before you write code, please read this page. ContextEngine holds a stricter
evidence bar than most repositories its size, and the difference is not style —
it is the point of the project. A contribution that adds a useful feature but
weakens an invariant will be declined.

## The one rule that explains all the others

**Security is a veto, not a score.**

Three invariants must hold, and no feature benefit offsets a failure in any of
them:

- Unauthorized Evidence leaked = **0**
- Wrong-Organization effect = **0**
- Missing tenant context = **fail closed, always**

A second rule follows from the first: **a capability is not "done" because it
runs — it is done when executable evidence proves its exact boundary.**
Anything unproven stays labeled `NOT_ACTIVE`, including in the service's own
`/health` response. Please do not "helpfully" remove a `NOT_ACTIVE` marker; it
is load-bearing. See [STATUS.md](./STATUS.md).

## Before you start

1. **Check the issue tracker.** Issues and PRDs live in
   [GitHub Issues](https://github.com/stone16/context-engine/issues). Read the
   full body, labels, and comments before acting on an existing issue.
2. **Open an issue before a large PR.** External pull requests are not a triage
   surface for feature requests — an unsolicited large PR is likely to be
   declined on scope alone, however good the code is.
3. **Read the domain glossary.** [CONTEXT.md](./CONTEXT.md) is the repository's
   authority on terms like `CandidateRef`, `AuthorizedProjection`,
   `SourceAclEvidence`, and `TrustedDeliveryContext`. Using these words loosely
   in code or review is a real source of bugs here.

### Triage labels

| Label | Meaning |
|---|---|
| `needs-triage` | Maintainer evaluation is required |
| `needs-info` | Waiting for reporter information |
| `ready-for-agent` | Fully specified and AFK-agent ready |
| `ready-for-human` | Human implementation or judgment is required |
| `wontfix` | The work will not be actioned |

## Development setup

Prerequisites and their sources of truth are listed under
[Quick start](./README.md#quick-start) in the README.

```bash
make install
make db-up
```

## The verification contract

**Never claim a change works without running the commands and reading the real
output.** Fabricated or assumed verification output is the one contribution
behavior that will get a PR closed without further review.

Run the full gate — the same one CI runs — before opening a PR:

```bash
make check
```

`make check` requires `make db-up` first, and covers: build, Ruff, strict mypy,
TypeScript typecheck, OpenAPI freeze check, SDK generate/build/test/pack,
ActionPlane and BotDelivery build and tests, Python unit tests, the security
catalog, the process smoke suite, the real-PostgreSQL integration harness, and
the M0 security gate.

For faster inner loops:

```bash
make lint          # Ruff
make typecheck     # strict mypy + TS
make test          # Python unit tests
make integration   # real-PostgreSQL integration/security harness
make security-gate # M0 security veto gate
```

When you are done, stop the harness:

```bash
make db-down
```

## Writing tests

Tests are not a coverage exercise here — they are the evidence the project
ships on. Two expectations go beyond the usual:

- **A test must encode the business invariant it protects,** not merely the
  current behavior. If a test still passes after the meaningful rule changes,
  it is a shallow test.
- **Runtime tests use the highest public seam available** — HTTP or the
  generated SDK — and must prove the chain
  `CandidateRef → AuthorizationKernel → AuthorizedProjection`. A test that lets
  a raw candidate reach a content-bearing consumer is testing the wrong thing.

Negative cases matter as much as positive ones: cross-Organization, denied
same-Organization, nonexistent candidates, tampering, replay, expiry, and
concurrent losers should all be provably zero-effect.

## Architectural boundaries

These are not preferences. Changes that cross them will be asked for an ADR
first, or declined.

- **The Runtime path is sealed, not merely wired.** No feature flag, alternate
  composition, no-op dependency, or direct retriever-to-assembler path may
  bypass the `AuthorizationKernel`, PackageBudget, provenance, or audit gates.
- **Authorization precedes anything content-bearing.** Indexes return
  `CandidateRef` only. Hydration, reranking, relevance models, and assembly
  accept `AuthorizedProjection` only. Every parent/neighbor expansion is
  re-authorized.
- **`Weak` ACL evidence is never a fallback.** It is permitted only where a
  source genuinely lacks finer-grained ACL semantics. A failed `Live` or
  `Mirrored` check fails closed.
- **External effects go through `ActionPlane.prepare` then `perform`,** each
  with its own org-scoped, audience- and payload-bound, one-shot ticket. Never
  reuse a create ticket for an edit or send.
- **Index and cache filters never make authorization decisions.**
- **No secrets, `.env` values, or credentials in commits** — reference a single
  live source. Do not hardcode volatile values (URLs, ports, versions) in prose;
  point to their source of truth.

### Controlled third-party reuse

The design draws on architectural study of **Dify**, **RAGFlow**, **MaxKB**, and
**Onyx**. Code reuse follows
[ADR-0074](./docs/decisions/0074-adopt-controlled-third-party-code-reuse.md),
per source region, never per product: Apache-2.0 regions (RAGFlow) and MIT
regions (Onyx outside every `ee/` directory; separately-licensed SDK subtrees)
may be copied and patched only after path-level license verification at a
pinned commit, registered under `third_party/` with upstream license, exact
provenance (`UPSTREAM.toml`), and modification notices, and shipped with
complete attribution and SBOM coverage. Dify root-licensed code, MaxKB GPLv3
code, and Onyx `ee/` code must never be copied — reuse them only through
clean-room behavior specifications and test oracles produced by an observer
who does not implement. Public reference claims must trace to the
[evidence baseline](./docs/research/2026-07-19-four-public-repositories-evidence.md).
Research from outside this repository may inform your reasoning, but must never
be cited or linked as public provenance.

## Architecture Decision Records

Any non-obvious decision is recorded as an ADR under
[`docs/decisions/`](./docs/decisions/README.md); the ADRs are the authority on
boundaries, dependency direction, forbidden shortcuts, and revisit triggers.

Write a new ADR when your change alters a boundary, introduces a dependency
direction, or closes off an option that a future contributor might reasonably
want. Follow the numbering and shape of the most recent accepted ADRs.

## Pull requests

**Commits.** One concern per commit. Use the prefixes already in the history:
`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `style:`, or a domain prefix
such as `supply:`, `runtime:`, `delivery:`, `bot:`.

**Before you open the PR, confirm:**

- [ ] The change does what the issue asked, and edge cases are considered.
- [ ] `make check` passes, and you have the real output — not an assumption.
- [ ] Runtime tests prove `CandidateRef → AuthorizationKernel →
      AuthorizedProjection`; no raw candidate reaches a content-bearing consumer.
- [ ] No secrets or volatile values baked into code or docs.
- [ ] Any non-obvious decision is recorded as an ADR.
- [ ] Capability claims match reality — anything unproven is still `NOT_ACTIVE`,
      and [STATUS.md](./STATUS.md) is updated if a boundary moved.

**In the PR description**, state plainly what you verified and what you did not.
If you skipped something, say so. Surfacing uncertainty is always preferred to
hiding it.

## Scope discipline

Touch only what the task requires. Please do not "improve" adjacent code,
comments, or formatting in the same PR — it makes the security-relevant diff
harder to review, which is a real cost in this repository. Match existing
conventions even where you would have chosen differently; if you think a
convention is harmful, raise it as its own issue rather than forking it
silently.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](./LICENSE).
