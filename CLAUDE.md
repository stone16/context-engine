---
name: claude-bridge
version: "0.2.0"
description: >
  Bridge Claude Code into the canonical AGENTS.md charter and route the
  Claude-only surfaces under `.claude/`. Use when Claude Code loads repository
  context or needs repository-installed tooling that Codex and OpenCode never
  see. Not for repository guardrails or tool-agnostic routing, which belong in
  AGENTS.md.
---

@AGENTS.md

<!-- Claude-specific delta only — nothing duplicated from AGENTS.md. -->

## Claude-only skills

Claude Code is the only agent that loads `.claude/skills/`. Each skill owns its
own rules; this table routes.

| When you are… | Read first |
|---|---|
| answering from the maintainer's corpus, not just the checked-out files | `.claude/skills/context-engine/SKILL.md` |
