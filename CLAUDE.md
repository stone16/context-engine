---
name: claude-bridge
version: "0.1.0"
description: >
  Bridge Claude Code into the canonical AGENTS.md charter for ContextEngine. Use
  when Claude loads repository context; this file imports AGENTS.md so a single
  source of truth stays authoritative. Not for storing guardrails directly — edit
  AGENTS.md instead.
---

@AGENTS.md

<!-- Claude-specific delta only — nothing duplicated from AGENTS.md. -->

## Agent skills

### Issue tracker

Issues and PRDs live in GitHub Issues for `stone16/context-engine`; external pull
requests are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five canonical triage roles mapped to same-named GitHub labels. See
`docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository: read root `CONTEXT.md` and relevant ADRs in
`docs/decisions/`. See `docs/agents/domain.md`.
