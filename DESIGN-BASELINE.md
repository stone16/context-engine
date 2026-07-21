---
title: ContextEngine D0 Design Baseline Candidate
date: 2026-07-18
status: candidate-not-approved
hash-algorithm: SHA-256
manifest-state: absent-pending-regeneration
---

# ContextEngine D0 Design Baseline Candidate

> This file records a candidate baseline. It does **not** close D0, publish the
> authority bundle, or authorize production implementation. No integrity
> manifest exists for the current candidate; it must be generated only after
> the bundle is approved and committed.

## Authority by responsibility

| Responsibility | Owning repository material |
|---|---|
| Integrated implementation shape | `docs/design/2026-07-18-context-engine-implementation-design.md` |
| Individual accepted decisions | ADRs under `docs/decisions/` |
| Canonical vocabulary | `CONTEXT.md` |
| Assets, trust boundaries, threats, and hard oracles | `docs/security/context-engine-threat-model.md` |
| Executable security and negative-test contracts | remaining documents under `docs/security/` |
| Implementation-facing summary and scope | `PLAN.md`, `README.md`, the program PRD, and subordinate Tech Spec |

This is responsibility ownership, not a total precedence order. A contradiction
within or across owned scopes blocks baseline approval until it is reconciled
explicitly; no document silently overrides another.

The four-repository evidence report under `docs/research/` supports public
reference claims but owns no ContextEngine design decision. Repository-external
research and legacy decision logs are independent reasoning inputs only; they
are excluded from public authority and provenance.

## Candidate status

- Public-source sanitization and provenance closure passed the current keyword,
  host-allowlist, link-target, and cross-document review on 2026-07-19. This
  candidate still must not be promoted until the manifest is generated,
  maintainer review completes, and all remaining D0 evidence gates pass.
- The candidate documents are uncommitted worktree content. No commit or digest
  currently identifies this bundle.
- Public provenance is limited to the fixed four-repository evidence report,
  first-party product requirements, the versioned ContextEngine threat model,
  current implementation design, and accepted ADRs. Repository-external research may
  inform independent reasoning but cannot be cited, linked, or published as
  evidence.
- The manifest must be generated only after maintainer approval and a commit
  make the candidate immutable; this edit intentionally does not invent hashes
  for uncommitted content.
- Historical note: this candidate predates implementation. The repository now
  has verified install, build, lint, typecheck, unit/catalog/process, and real
  PostgreSQL integration commands recorded in `AGENTS.md` and `README.md`, plus
  RLS transaction-context evidence. Filtered-ANN and Feishu capability evidence
  remain pending; this candidate still does not close D0.

## Pending SHA-256 manifest scope

The future manifest must cover `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md`, `PLAN.md`,
`README.md`, this baseline, and every versioned Markdown file under `docs/` except
the explicitly ignored historical draft. It must record the immutable commit,
sorted relative paths, SHA-256 per file, generation command/version, and an
independent verification result.

## D0 promotion checklist

- [x] Current design authority and accepted ADRs agree after sanitization.
- [x] Security and test contracts agree with the implementation authority after
      sanitization.
- [x] Program PRD has the required headings and 100 consecutive user stories.
- [x] Tracer-bullet child issue draft exists for maintainer review.
- [x] The complete public authority bundle passes the four-repository allowlist
      and provenance scan.
- [ ] This manifest is generated from the sanitized files and independently
      verified.
- [ ] Maintainer approves the Runtime/Provider/BotDelivery/ActionPlane/Learning
      test seams.
- [ ] Maintainer approves the repository PRD and any GitHub issue publication.
- [ ] PRD parent issue identifier and published body digest are recorded.
- [ ] Child issue granularity and dependencies are approved before publication.
- [ ] Feishu, RLS transaction-context, and filtered-ANN evidence spikes pass and
      their reproducible reports are pinned.
- [x] Real install/test/lint/build/report commands are selected and verified.
- [ ] This candidate is committed as an immutable, publication-ready authority
      baseline.
