# Spike: is the pi coding agent the right first consumer of ContextEngine?

> Date: 2026-07-28
>
> Status: research spike; not an implementation authorization and not an ADR
>
> Scope: evaluates pi (`pi.dev`) as the first dogfood consumer of the loopback
> `POST /v0/resolve` carrier (ADR-0068), against the alternatives already in
> the maintainer's stack. Repository-external facts below are research inputs
> under AGENTS.md's evidence rules — they inform reasoning and must not be
> republished as public provenance.

Evidence grades follow the four-repository evidence report's discipline:

- **[local]** — verified on the maintainer machine on 2026-07-28, cited as
  `file:line`; machine-local paths outside this repository are point-in-time
  observations, not durable repo facts.
- **[docs]** — pi's official documentation (`pi.dev/docs/latest`), read
  2026-07-28, pi v0.82.1 current.
- **[repo]** — this repository, cited as `file:line`.

## 1. Verdict first

**pi is not the right *first* consumer. The first consumer should be a Claude
Code skill in this repository that wraps the existing dogfood HTTP caller. pi
is the right *second* consumer, and the strongest long-term extension target.**

The constraint that rules the alternatives in and out is ADR-0062's own test:
the first caller must be "the maintainer's own tooling over the maintainer's
real workloads" (`docs/decisions/0062-pull-development-through-dogfood-workloads.md:31-33`),
because real recurring traffic is the only feedback source that seeds golden
set v0. pi fails that constraint today on a hard fact: **pi is not installed
on the maintainer's machine** (§2). A consumer integrated into an agent with
zero daily traffic produces zero golden-set pull, which is precisely the
"completeness without a pulling workload" failure mode ADR-0062 prohibits
(`0062:55-57`). Claude Code inside Orca is where the maintainer's real
questions already happen — this spike itself ran there — so a skill there
starts generating real queries on day one with roughly one file of
integration effort.

## 2. (a) What pi is here, and how it is launched

### What pi is

pi is a deliberately minimal open-source terminal coding agent by Mario
Zechner / Earendil Inc (MIT): four built-in tools (read, write, edit, bash), a
sub-1000-token system prompt, and everything else — MCP, sub-agents,
permission gates, memory — added through a typed TypeScript extension system.
**[docs]** npm package: `@earendil-works/pi-coding-agent` (docs recommend
`npm install -g --ignore-scripts @earendil-works/pi-coding-agent`; pnpm/bun
and curl installers also offered). Verified runnable without global install:
`npx -y @earendil-works/pi-coding-agent@latest --version` → `0.82.1`
**[local]** (run in an isolated scratch directory).

### How Orca launches it

Orca treats pi as one of its known TUI agents and launches the bare `pi`
command from the user's `PATH` — it does not bundle a pi binary:

- `/Applications/Orca.app/Contents/Resources/app.asar.unpacked/out/shared/tui-agent-config.js:77-83`
  **[local]**: the `pi` agent entry is `detectCmd: 'pi'`, `launchCmd: 'pi'`,
  `expectedProcess: 'pi'`, with prompt injection via the
  `ORCA_PI_PREFILL` environment variable because "pi has no `--prefill`".
- `orca worktree create --agent pi` resolves through this table ("Launch a
  known TUI agent in the first terminal", `orca worktree create --help`)
  **[local]**.

### The binary does not exist on this machine

`which pi` → not found **[local]**. A sweep of every plausible install
location found no pi binary: all six nvm node versions' global `bin`/
`node_modules`, `~/.bun/bin` and bun's global installs, pnpm's global root,
Homebrew, `~/.local/bin`, `/usr/local/bin`, cargo, and uv tools **[local]**.
The `~/.pi` tree that exists is explained by two other actors:

- `~/.pi/agent/extensions/` (created 2026-07-27) holds exactly three
  Orca-managed extensions, each headed `// @orca-managed-pi-extension`
  (`~/.pi/agent/extensions/orca-prefill.ts:1`,
  `orca-agent-status.ts:1-2`) **[local]**. Orca prepares this pi
  integration scaffolding by default even for bare-shell panes
  (`.../out/shared/pi-agent-kind.js:38-40`: the no-command fallback resolves
  to `'pi'` because "Orca prepared Pi integration by default") **[local]**.
- `~/.pi/agent/skills/` (created 2026-07-11) contains only symlinks into
  `~/.agents/skills/` — a cross-agent skill installer's shared store, the
  same `dbs-*` skill set visible to other agents — not pi-authored content
  **[local]**.

So the honest picture: the maintainer's environment is pi-*ready* (Orca
scaffolding, cross-agent skills), but the maintainer has never actually run
pi day to day. Day-to-day agent usage on this machine is Claude Code inside
Orca.

## 3. (b) pi's extensibility surface

### Extensions (the primary surface)

An extension is a TypeScript module whose default export receives the
`ExtensionAPI`; pi loads `~/.pi/agent/extensions/*.ts` (global) and
`.pi/extensions/*.ts` (project) via the jiti TypeScript loader, plus explicit
`--extension/-e` paths and `pi install <source>` packages **[docs]**;
`pi --help` confirms `-e`, `--no-extensions`, and the
`pi install/remove/update/list/config` extension-management commands
**[local]** (npx run, v0.82.1).

The local Orca extensions independently confirm the API shape **[local]**:

- `export default function (pi)` receiving the API object
  (`orca-prefill.ts:2`).
- Event subscription `pi.on(...)` for `session_start`,
  `before_agent_start`, `agent_start`, `tool_execution_start`, `tool_call`,
  `tool_execution_end`, `message_end`, `agent_settled`, `agent_end`
  (`orca-agent-status.ts:326-446`).
- Handler context: `ctx.ui.setEditorText` (`orca-prefill.ts:10`),
  `ctx.sessionManager.getSessionId()/getSessionFile()`
  (`orca-agent-status.ts:17-19`), `ctx.isIdle()`
  (`orca-agent-status.ts:437`).
- Extensions are awaited on the critical path — the status extension
  deliberately moves posting off it (`orca-agent-status.ts:7-9`).

The official docs add the rest of the surface **[docs]**:

- **Custom tools**: `pi.registerTool({name, description, parameters
  (TypeBox), execute(...), renderCall, renderResult, ...})` — registered
  tools are model-visible alongside read/bash/edit/write.
- **Context injection**: a `before_agent_start` handler may return
  `{message: {customType, content, display}, systemPrompt}` to inject a
  message and/or extend the system prompt for that run; `pi.sendMessage` /
  `pi.sendUserMessage` deliver mid-session messages
  (`deliverAs: "steer" | "followUp" | "nextTurn"`); a `context` event lets an
  extension filter/rewrite the message array before each LLM call (the RAG
  hook); `pi.appendEntry` persists non-LLM entries.
- **Commands/UX**: `pi.registerCommand("name", {handler})` (slash commands),
  `pi.registerShortcut`, `pi.registerFlag`, `ctx.ui`
  (notify/confirm/select/input/setStatus/setWidget).
- **Tool interception**: `tool_call` can block a call; `tool_result` can
  rewrite results.

### Skills

pi implements the cross-vendor Agent Skills standard (`SKILL.md` packages,
loaded on demand from `~/.pi/agent/skills` and `.pi/skills`; `--skill`,
`--no-skills` flags confirmed in `pi --help`) **[docs][local]**. The same
skill directory format Claude Code uses — which is why the shared
`~/.agents/skills` symlink store works.

### MCP

pi has **no built-in MCP client**; this is an explicit design position
("no built-in MCP... build CLI tools with READMEs, or build an extension that
adds MCP support") **[docs]**. Consequence for this spike: an MCP server is
*not* a shortcut into pi; for pi, HTTP-from-an-extension is the native path.

### Programmatic modes

`--mode json` / `--mode rpc` (JSONL over stdio) and an embeddable Node SDK
exist for driving pi from other programs **[docs]**, confirmed as CLI flags
in `pi --help` **[local]** — relevant later if Orca or scripts want to drive
a ContextEngine-augmented pi headlessly.

## 4. (c) How pi would call the loopback dogfood API

### The wire contract (repo facts)

- Endpoint: `POST /v0/resolve`, frozen in `openapi/v0/openapi.json`
  (`README.md:123-124`) **[repo]**.
- Auth: HTTP `Authorization: Bearer <secret>` (`adapters/http/app.py:279-281`,
  scheme `ContextEngineBearer`, opaque format; constant-time check per
  ADR-0068 §2) **[repo]**. The secret lives only in
  `CONTEXT_ENGINE_DOGFOOD_SECRET` and is never an argument, printed, or
  stored (`eval/README.md:28-30`) **[repo]**.
- Composition: `CONTEXT_ENGINE_API_COMPOSITION=dogfood-local-v1`, loopback
  host only, seeded identity, deterministic-twin embeddings
  (`README.md:126-155`; ADR-0068
  `docs/decisions/0068-activate-loopback-dogfood-runtime.md:31-77`) **[repo]**.
- Request: `{"kind": "acquire", "need": {"query": "..."}}` with optional
  `packageBudget` / `requestNarrowing` (`AcquireWire`/`ContextNeedWire` in
  `openapi/v0/openapi.json`); the need "carries no identity or authority"
  **[repo]**.
- Response: `ResolutionOutcomeWire` — `resolved` (with `package` and
  `egressGrant`), `request_not_available`, or `citation_not_available`
  **[repo]**. The package carries `blocks[]` (each block bound to exactly one
  `evidenceRef` — `BlockWire.evidenceRefs` has `minItems`/`maxItems` 1),
  `evidence[]` lineage, `expiresAt`, `packageId`, `coverage`, `gaps`,
  `budgetUsage` **[repo]**.
- Purpose is fixed server-side to `context.answer`
  (`adapters/http/app.py:214`, `adapters/http/dogfood.py:273`) — the caller
  cannot and must not choose a purpose **[repo]**.
- `Continue` and `OpenCitation` are `NOT_ACTIVE` in this carrier
  (ADR-0068, `0068:79-83`) — a consumer sends only `acquire` and treats
  `citationOpenRef` as display-only lineage **[repo]**.

### Sketch A (recommended shape): a model-invoked tool in one extension file

Dropped into `.pi/extensions/context-engine.ts` of a workspace (or installed
via `pi install`). The model decides when it needs maintainer context and
pulls it; the tool result lands in context through pi's normal tool-result
channel, and the block↔evidence binding is preserved in the rendered text.
Sketch only — not installed anywhere.

```typescript
// .pi/extensions/context-engine.ts — sketch, not installed
import { Type } from "@sinclair/typebox";

const BASE = process.env.CONTEXT_ENGINE_DOGFOOD_BASE_URL; // e.g. http://127.0.0.1:8137
const SECRET = process.env.CONTEXT_ENGINE_DOGFOOD_SECRET;  // never echoed anywhere

export default function (pi) {
  if (!BASE || !SECRET) return; // fail closed: no config, no tool registered

  pi.registerTool({
    name: "acquire_context",
    label: "ContextEngine",
    description:
      "Fetch authorized, citation-bearing context for a question about the " +
      "maintainer's notes and repositories. Returns quoted blocks with " +
      "evidence refs; the package expires and must be re-acquired per question.",
    parameters: Type.Object({
      query: Type.String({ minLength: 1 }),
    }),
    async execute(_id, { query }, signal) {
      const res = await fetch(`${BASE}/v0/resolve`, {
        method: "POST",
        signal,
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${SECRET}`,
        },
        body: JSON.stringify({ kind: "acquire", need: { query } }),
      });
      if (!res.ok) {
        // Auth/validation failures surface as closed refusals; never retry
        // with a mutated credential and never print the secret.
        return { content: [{ type: "text", text: `resolve refused: HTTP ${res.status}` }], isError: true };
      }
      const outcome = await res.json();
      if (outcome.kind !== "resolved") {
        // request_not_available is a closed refusal envelope, not an error to "fix".
        return { content: [{ type: "text", text: `no authorized context (${outcome.kind})` }] };
      }
      const pkg = outcome.package;
      // Consumer obligations (ADR-0061/0068 + CONTEXT.md `ContextPackage`):
      // expiring, request-scoped, evidence kept attached — so render blocks
      // WITH their evidence refs and the expiry; never cache across turns.
      const evidenceByRef = new Map(pkg.evidence.map((e) => [e.evidenceRef, e]));
      const lines = pkg.blocks.map((b) => {
        const ev = evidenceByRef.get(b.evidenceRefs[0]);
        return `> ${b.text}\n  — ${ev?.resourceRef ?? "?"} / ${ev?.fragmentRef ?? "?"} (${b.evidenceRefs[0]})`;
      });
      return {
        content: [{
          type: "text",
          text:
            `ContextPackage ${pkg.packageId} (purpose context.answer, ` +
            `expires ${pkg.expiresAt}; do not reuse after expiry):\n\n` +
            lines.join("\n\n") +
            (pkg.gaps.length ? `\n\nGaps: ${pkg.gaps.length} declared` : ""),
        }],
        details: { packageId: pkg.packageId, expiresAt: pkg.expiresAt },
      };
    },
  });

  // Optional human-invoked path: /ctx <question> steers the answer with a fresh resolve.
  pi.registerCommand("ctx", {
    description: "Acquire ContextEngine context for a question",
    handler: async (args, ctx) => {
      pi.sendUserMessage(
        `Use the acquire_context tool for: ${args}`,
        { deliverAs: "followUp" },
      );
    },
  });
}
```

Why a tool rather than unconditional `before_agent_start` injection: ADR-0068's
carrier is bounded and each resolve opens a full Kernel transaction; firing a
resolve on every turn regardless of need wastes the budget and pollutes
context, while a model-invoked tool matches the "recurring questions"
workload of ADR-0062 and yields exactly one golden-set-shaped query per real
need. A `before_agent_start` variant (resolve the user's prompt once and
return it as an injected message) is a five-line change on the same skeleton
if pull-per-turn proves wanted.

### Sketch B (lower-effort fallback): a pi skill, no extension

Because pi speaks the same Agent Skills standard, the identical skill
proposed for Claude Code (a `SKILL.md` that instructs the agent to call the
eval CLI `uv run context-engine-dogfood-eval query '<question>'` — the
already-existing caller, `eval/README.md:22-30`) drops into
`~/.pi/agent/skills/` unchanged. Zero TypeScript, but the agent must choose
to invoke it and output parsing is prose-level. This is the cheapest bridge
and it is *shared* — which is itself an argument that the first consumer
artifact should be a skill, not a pi-only extension.

### Obligations on a local read-only consumer

Explicitly, per the glossary and ADRs:

- **Expiry/TTL**: `ContextPackage` is "self-contained and expiring; any later
  use is evaluated under current authority" (`CONTEXT.md:290-302`). The
  consumer must not cache a package across questions or reuse it past
  `expiresAt`; every question is a fresh resolve.
- **Purpose**: fixed `context.answer`; the consumer must not present the
  package as authority for anything else — it is "authorized output, not
  reusable authority" (`CONTEXT.md:51`).
- **Citations**: blocks stay bound to their single evidence ref; a consumer
  that strips lineage violates the package invariant ("required security
  fields and Evidence lineage cannot be removed", `CONTEXT.md:300-301`).
  `citationOpenRef` is display-only while dogfood `OpenCitation` is
  `NOT_ACTIVE` (ADR-0068 `0068:79-83`).
- **Secret hygiene**: bearer secret only from the environment, never in
  arguments, logs, session transcripts, or rendered output
  (`eval/README.md:28-30`; ADR-0068 §2). Note pi persists tool results into
  its session JSONL — the sketch therefore never places the secret in any
  tool result or message.
- **What does NOT apply**: BotDelivery's TCB rules (`TrustedDeliveryContext`,
  `DeliveryEvidenceRef`, EgressGrant redemption, ActionTickets) govern the
  trusted IM delivery process and external effects (AGENTS.md Safety-Rails).
  A loopback, read-only, single-maintainer console consumer performs no IM
  egress and no external effect, so it redeems nothing; the dogfood
  `egressGrant` field arrives in the envelope but a local display consumer
  has no grant-gated hop to spend it on. The moment a consumer forwards
  package content into a chat platform or performs a write, it stops being
  this category and the BotDelivery/ActionPlane rules bind.

## 5. (d) Verdict, challenge-first

### The strongest case FOR pi (steelman, argued first)

1. **Cleanest injection surface in the stack.** A typed `registerTool` +
   `before_agent_start` + `context` event is a genuinely better consumer API
   than anything Claude Code exposes: Claude Code skills are prose
   instructions plus bash; pi extensions are code with guaranteed hooks. The
   "consumer contract" questions ContextEngine needs answered (when to
   resolve, how to render evidence, how expiry behaves mid-session) are
   *testable* in pi.
2. **Philosophical fit.** pi's "harness you extend" thesis mirrors
   ADR-0061's context-layer thesis: pi deliberately has no context layer,
   ContextEngine is exactly that missing layer. A pi extension is a clean
   demonstration artifact for external audiences.
3. **No MCP detour.** pi's native path is HTTP-from-TypeScript — precisely
   the frozen `POST /v0/resolve` seam plus the generated TS SDK
   (ADR-0048) this repo already ships. No new adapter surface needed.
4. **Orca already scaffolds pi.** Launch config, prefill, status hooks exist;
   `orca worktree create --agent pi` makes trial friction low.

### Why that case fails for FIRST consumer

- **The dogfood constraint is about traffic, not API elegance.** ADR-0062
  chose the maintainer's workloads because they are "the only immediately
  available, zero-coordination feedback source" (`0062:58-60`). pi has zero
  installed base on this machine (§2): no binary anywhere, `~/.pi` populated
  only by Orca scaffolding and a cross-agent symlink store. Making pi the
  first consumer means first adopting a new daily driver *and* integrating
  it — two adoption bets stacked in front of the feedback loop the roadmap
  depends on. If the pi habit doesn't stick, the consumer is stranded and
  golden set v0 stays empty.
- **The integration polish pi rewards is premature.** Until golden set v0
  exists, the open questions are corpus and retrieval quality, not consumer
  ergonomics. A prose skill wrapping the existing
  `context-engine-dogfood-eval query` caller (`eval/README.md:22-30`)
  answers "does the package help with real questions" at near-zero cost.
- **Slice A explicitly wants "one real caller through the generated SDK or
  HTTP" (`0062:42-45`)** — singular. The narrowest cut that serves the
  workload end to end is the tool the maintainer is already inside all day:
  Claude Code in Orca.

### The other alternatives, and the constraint that rules each out

| Alternative | Ruled out as first consumer by |
|---|---|
| **MCP server** consumed by Claude Code/other clients | Builds a new long-lived adapter surface (auth forwarding, server lifecycle) ahead of any workload that needs it — ADR-0062's prohibited breadth-first shortcut (`0062:50-52`); AGENTS.md keeps MCP an "optional future" adapter. It also does nothing for pi, which has no MCP client **[docs]**. |
| **Orca itself** | Closed third-party application; its ContextEngine-relevant integration points are exactly the per-agent extension/skill dirs it scaffolds (§2), so "integrate with Orca" reduces to integrating with an agent anyway — with none of the levers under the maintainer's control. |
| **pi extension** | No installed base / no daily traffic on the maintainer machine (§2) — fails ADR-0062's pulling-workload test today. |
| **Claude Code skill calling the HTTP API** | Not ruled out: daily-driver traffic, ~one `SKILL.md` file of effort, uses only the frozen HTTP seam and existing eval caller, and the same skill file is reusable by pi via the shared Agent Skills standard (§4, Sketch B). |

### Recommendation

1. **Now**: ship the first consumer as a Claude Code skill (repo-local
   `SKILL.md`) that performs an Acquire resolve via the existing
   `context-engine-dogfood-eval query` CLI (or `curl` against
   `POST /v0/resolve` with the env-held bearer), renders blocks with their
   evidence refs, and honors the §4 obligations. Every real question it
   serves is a golden-set v0 candidate.
2. **Next, pulled not scheduled**: when recurring usage proves the loop (and
   if pi enters actual daily use), promote the pattern to the pi extension of
   Sketch A — the first *programmatic* consumer, exercising the generated TS
   SDK behind its closed facade (ADR-0048) instead of raw fetch.
3. **Record the consumer contract**: whichever consumer lands first, the §4
   obligations (fresh resolve per question, expiry honored, lineage kept,
   secret never persisted) belong in a short ADR when the first consumer is
   implemented, since they bind every future local consumer including pi.
