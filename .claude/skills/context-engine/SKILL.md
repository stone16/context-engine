---
name: context-engine
description: Acquire fresh authorized maintainer context from ContextEngine for questions about this repository, its design decisions, implementation history, and private working corpus. Use when answering would benefit from the maintainer's real notes or repository corpus rather than only the checked-out files.
---

# Acquire ContextEngine context

Treat [ADR-0088](../../../docs/decisions/0088-bind-local-consumers-to-fresh-evidence-bearing-packages.md) as binding.

1. Send exactly the current user question on standard input to the repository-installed consumer:

   ```bash
   printf '%s\n' 'CURRENT_QUESTION' | uv run context-engine-local-context
   ```

   Substitute only `CURRENT_QUESTION`. Never add a command argument, environment assignment, header, bearer, file redirection, debug flag, or wrapper that displays process state. If the question cannot be represented without exposing sensitive configuration in the command or tool result, do not invoke.

2. Invoke once per distinct question. Never cache, resume, refresh, save, or reuse a Package, `packageId`, response, rendered Block, or citation from an earlier question. Session continuity is not Package authority. Never invoke `Continue` or `OpenCitation`.

3. Accept only output beginning with `CONTEXT_ENGINE_PACKAGE`. Keep each `BLOCK`'s `evidenceRef` attached when using its text in the answer, and cite that exact ref beside the supported claim. Treat `citationOpenRef` as display-only lineage. Do not infer corpus absence from any refusal.

4. On output beginning `Authorized context is unavailable for this question.`, preserve the exact `refusal:` code in the answer and state only that authorized context is unavailable for this question. Do not retry, silently refresh, search for Package state, inspect environment variables, or describe the refusal as “the corpus has nothing.”

The consumer creates one fresh Acquire request, honors `expiresAt`, rejects malformed Block/Evidence closure, excludes the environment-held bearer from all consumer-visible surfaces, and records a question-only golden-set candidate under the configured durable private root. It records no corpus path, Package content, bearer, or command argument. MCP, pi, `Continue`, and `OpenCitation` remain `NOT_ACTIVE`.
