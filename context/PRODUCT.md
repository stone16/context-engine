# ContextEngine Minimal UI product context

## One-liner

A calm, server-rendered daily-driver for operating ContextEngine through the same
public HTTP seam that every other delivery client must use.

## Target user

- **Primary:** The single-tenant maintainer/operator who currently switches among
  CLI commands, `curl`, logs, and SQL to understand one ingestion or answer.
- **Secondary:** A reviewer verifying that the M1 carrier stays sealed and
  fail-closed.

## Value proposition

The operator can see what is healthy, what was authorized, what a pending change
will do, and why a request was refused without receiving any denied content or
bypassing the AuthorizationKernel.

## Jobs to be done

- Inspect source health, ingest counts, refusal categories, and current Release
  generation.
- Ask for context and resolve every rendered citation to Article, Revision, and
  Fragment lineage.
- Preview an import's actual Fragments, then explicitly confirm or cancel it.
- Run a retrieval Hit Test whose result contains authorized hits only.
- View an Article policy and resolution rung; preview and explicitly confirm any
  existing-Article change with a Policy Epoch advance.
- Inspect versioned provider/retrieval profiles and understand re-embedding impact
  before requesting a change.
- Record answer feedback as evidence without acquiring promotion, activation, or
  rollback authority.

## Non-goals

- No workflow canvas, bulk policy editing, feedback triage, golden-set curation, or
  general administration framework.
- No separate UI process, JavaScript framework, frontend package manager, or client
  build system.
- No container-, folder-, dataset-, or knowledge-base-level permissions.
- No new publication owner; release promotion remains the existing explicit
  release-operator path.
- No rendering of refused candidates, their identifiers, existence, counts, or
  original rank.

## Positioning

- **Category:** Local authenticated operator console.
- **Alternative today:** CLI plus `curl`, logs, and direct SQL inspection.
- **Why this surface:** It makes ContextEngine's evidence closure and refusal
  behavior visible while preserving the public-seam architecture.
