# Domain documentation

ContextEngine is a single-context repository.

Before exploring or changing the system, read:

- root `CONTEXT.md` for the domain glossary;
- `docs/design/2026-07-18-context-engine-implementation-design.md` for the
  current implementation authority;
- relevant ADRs under `docs/decisions/` for accepted architectural decisions;
- `docs/research/2026-07-19-four-public-repositories-evidence.md` for the
  allowlisted public prior-art evidence;
- `PLAN.md` for the public implementation roadmap.

Use glossary terms in issue titles, PRDs, tests, and implementation. If a
proposal contradicts an accepted ADR, surface the conflict and supersede or
refine the ADR explicitly rather than silently changing behavior.

The current design, security contracts, ADRs, glossary, PRD, and Tech Spec are
versioned with the implementation so a clean clone has a self-contained
authority bundle. Repository-external research is non-normative; public work
items must rely on repository requirements, threat-model reasoning, or the
allowlisted four-repository evidence rather than unpublished provenance.
