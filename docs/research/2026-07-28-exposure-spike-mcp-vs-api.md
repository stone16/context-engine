# Spike: how ContextEngine exposes `resolve()` to its first real consumer — HTTP+SDK vs MCP server vs consumer-side shim

> Date: 2026-07-28
>
> Status: research spike; not an implementation authorization and not an ADR
>
> Scope: decides the exposure shape for the first real consumer of the
> loopback dogfood carrier (ADR-0068). Companion to the pi consumer spike
> (`docs/research/2026-07-28-pi-agent-consumer-spike.md`), whose findings
> about the consumer side are taken as input here. Repository-external facts
> (MCP protocol properties, pi capabilities) are research inputs under
> AGENTS.md's evidence rules, not public provenance.

## 1. Verdict first

**Recommendation: shape (3) — a consumer-side shim calling the existing
loopback HTTP API with the dogfood bearer. No new engine surface.** Shape (1)
is not actually a rival to (3): the frozen HTTP contract plus generated SDK
*is* the engine's only exposure surface and stays that way; (3) is simply the
cheapest legitimate consumer of it. The real decision is whether to build (2),
an MCP server adapter, now — and the answer is no.

Named constraints (house rule):

- **(2) MCP server adapter is ruled out** by the repo's own activation rule —
  "MCP 在真实 caller 出现前保持 NOT_ACTIVE" (`PLAN.md:24`), restated in the
  seam table as "真实 caller 激活后的 MCP" (`PLAN.md:92`) and in the M2 exit
  criteria (`PLAN.md:108`) — combined with ADR-0062's prohibited shortcut:
  breadth-first construction of surfaces "ahead of any workload that
  exercises them" (`docs/decisions/0062:50-52`). There is no MCP-native
  caller in the maintainer's daily loop today (§4.2), so building the server
  first inverts the pull rule the roadmap runs on. Secondarily, a new
  authentication-bearing network surface is a kernel-lane change requiring
  full ADR-0064 ceremony — the maximum-cost path for the zero-traffic option.
- **(1) "generated-SDK consumer first" is ruled out as the *first* step** by
  ADR-0062's speed criterion: the SDK is a TypeScript client artifact
  (`sdk/typescript/README.md:3-7`) and needs a TS host process, and the
  natural host — a pi extension — has no installed daily-use base yet (pi
  spike §2). It becomes the *second* consumer, where it also proves the SDK
  facade in anger before M2 BotDelivery depends on it (`PLAN.md:78,108`).
- **(3) survives**: it needs no new engine code, no new credential holder, no
  new ADR ceremony, and produces real dogfood queries — golden-set v0 input —
  on day one.

## 2. The three shapes, stated precisely

| # | Shape | Engine-side change | Consumer-side change |
|---|---|---|---|
| 1 | Plain HTTP + generated TS SDK | none (exists: `openapi/v0/openapi.json` frozen with sha256; SDK builds/packs, closed facade per ADR-0048, `sdk/typescript/README.md:4-7,14-17`) | a TS host embedding `ContextEngineResolveClient.resolve` |
| 2 | MCP server adapter | new `adapters/` server process/transport exposing resolve as an MCP tool; new authentication story for MCP sessions | any MCP client configures the server |
| 3 | Consumer-side shim | **none** | one Claude Code `SKILL.md` (or pi extension) doing an Acquire resolve via the eval CLI or `curl` with the env-held bearer (`eval/README.md:22-30`) |

The served surface today: `POST /v0/resolve` behind constant-time bearer
authentication (`adapters/http/app.py:279-281`), module-level composition
reject-all (`adapters/http/app.py:252`), content delivery active only for the
explicit `dogfood-local-v1` loopback composition (ADR-0068 §1–2;
`README.md:126-155`).

## 3. Security posture, option by option

### 3.1 What an MCP server would mean for the sealed path

An MCP server adapter does not — and must not — replace any of
`AuthenticatedInvocation → AuthorizationKernel → AuthorizedProjection`; it
would sit in front of the same ingress. The problems are what it adds around
that path:

- **Identity: an MCP session carries the wrong kind of identity.** MCP
  clients authenticate the *transport* (stdio child process inherits local
  env; remote HTTP transports use an OAuth-style client flow). Neither
  produces the ADR-0068 binding — one env-held secret mapping to one fixed
  Organization/User/Membership-version/Principal/Agent/application/
  authentication-binding tuple (`docs/decisions/0068:42-46`). A local MCP
  server can satisfy the binding only by *holding the dogfood bearer itself*
  and forwarding every session to it. That invents no new authority — but it
  **widens the credential boundary**: the secret's effective holder changes
  from "the one process configured with the env var" to "any local MCP
  client that can connect to (or spawn) the server." Under the threat model's
  caller boundary ("request bodies… are untrusted even after caller
  authentication", `docs/security/context-engine-threat-model.md:49`) that is
  a new class-2 actor — a confused or buggy first-party caller — created
  wholesale, with TM-01 exposure and no compensating control.
- **A second secret-holding component.** ADR-0068 §2 forbids the secret from
  representations, logs, and audit rows; ADR-0069 shows the repo's posture
  for new planes: "No operator operation is added to the HTTP ingress, the
  OpenAPI contract, the generated SDK, or any other network-reachable
  surface" (`docs/decisions/0069:50-53`). An MCP server is precisely a new
  network-reachable surface whose whole job is redistributing the output of
  a credential it holds. That demands kernel-lane ceremony (ADR-0064) —
  catalog evidence, refusal tests, non-enumeration — for a surface with zero
  users.
- **The M2 hazard: a second, unsealed delivery path.** In the M2 world,
  cleartext-package handling is what makes BotDelivery TCB
  (`PLAN.md:78`). An MCP server that hands `ContextPackage` cleartext to
  arbitrary connected clients is functionally a delivery hop with none of
  the delivery-plane controls — no `DeliveryEvidenceRef` redemption, no
  audience binding, no EgressGrant semantics for the onward hop. It would
  not bypass the Kernel (the safety rail holds), but it would normalize a
  delivery path whose egress side is ungoverned — exactly the "second
  unsealed delivery path" the longevity criterion warns about.

### 3.2 HTTP + SDK (the existing surface)

Already sealed and already bounded: one ingress, bearer at the edge,
reject-all default, loopback-only activation validated at CLI construction
(ADR-0068 §1). The SDK's closed facade refuses raw trusted inputs
(`sdk/typescript/README.md:4-7`), so even the client artifact cannot express
a forged trusted fact. No change → no new security analysis owed.

### 3.3 Consumer-side shim

The shim is an ordinary untrusted caller. That is its virtue: the wire
contract already assumes callers are untrusted after authentication
(threat-model boundary 1), `ContextNeedWire` "carries no identity or
authority" (`openapi/v0/openapi.json`), and the hard oracles do not depend on
caller correctness. A buggy shim's worst case is mishandling bytes the
single-Membership maintainer was already authorized to read on the same
machine — the blast radius ADR-0068 deliberately bounded. The secret stays
where it already is: one env var in one consumer process, never in tool
output or session transcripts (pi spike §4 obligations).

### 3.4 Where purpose/TTL/citation obligations land

| Obligation | (1) SDK consumer | (2) MCP server | (3) shim |
|---|---|---|---|
| Purpose (`context.answer`, server-fixed, `adapters/http/app.py:214`) | enforced server-side in all three shapes — no caller can choose a purpose |||
| TTL (`expiresAt`; fresh resolve per question, `CONTEXT.md:298-299`) | consumer code | **split**: server must not cache across sessions *and* each client must not cache across turns — two enforcement points | shim convention, documented + ADR'd with the first consumer |
| Citations (block↔evidence 1:1 kept, `CONTEXT.md:300-301`) | consumer rendering | server rendering, then re-trusted to every client's rendering | shim rendering |
| Secret hygiene | consumer env | **server env + a reachable surface redistributing its output** | consumer env |

The MCP column is strictly worse on every row that isn't server-enforced:
obligations split across two components, one of which serves many clients.

## 4. Effort and velocity (ADR-0062 first-class criterion)

- **(3) shim**: one `SKILL.md`; the reference caller already exists
  (`uv run context-engine-dogfood-eval query …`, `eval/README.md:22-30`).
  Real maintainer queries — golden-set v0 candidates — start the same day.
- **(1) SDK consumer**: SDK exists and builds, but needs a TS host; the
  natural host is the pi extension sketched in the pi spike §4, and pi has
  no daily-use base yet. Days-to-weeks, second consumer.
- **(2) MCP server**: new adapter process, transport + session-auth design,
  duplicate rendering of package semantics as MCP tool results, catalog
  activation evidence, a kernel-lane ADR. Weeks, before the first real query
  flows. Slowest to feedback by an order of magnitude.

## 5. Longevity into M2

- **(1)** is the surface that M2 is already specified against: BotDelivery
  consumes `resolve()` only through the generated client (`PLAN.md:78,108`).
  Keeping it the *only* surface preserves "one wire contract, one ingress"
  into the BotDelivery world.
- **(3)** is disposable by design — consumer-side, zero engine footprint,
  discarded or promoted without touching the engine; it matches ADR-0068's
  "deleted or replaced, not widened" posture for the whole dogfood carrier
  (`0068:108-109`).
- **(2)** is the only option with negative longevity: either it stays a thin
  proxy to the same ingress (in which case it bought nothing a shim didn't)
  or it accretes delivery-plane behavior without delivery-plane controls
  (§3.1). If MCP ever activates, it must terminate at the same
  authenticated ingress as every other caller — which is also why deferring
  it costs nothing architecturally.

## 6. Challenge-first: the strongest case against each option

### Against (3), the recommended shim

A shim enforces purpose/TTL/citation obligations by convention only — prose
in a skill file. A sloppy consumer could cache an expired package, strip
lineage, or paste the secret into a transcript, and nothing server-side would
notice, because those obligations live past the wire boundary. It also
exercises none of the SDK artifact M2 depends on, and invites per-agent shim
multiplication (Claude Code skill, pi extension, next agent's variant)
drifting apart. *Mitigations*: consumer count is one, maintainer-operated;
the obligations get recorded as a short ADR alongside the first consumer (pi
spike §5.3); the SDK gets exercised deliberately by the second consumer; the
hard oracles never depended on caller behavior, so no shim defect can produce
unauthorized Evidence or a wrong-Organization effect.

### Against (1) as first mover

The frozen HTTP surface pushes all consumer obligations onto every caller
with zero enforcement and no non-TS typing; the SDK facade has never been
exercised by a real consumer, so its ergonomics are unproven exactly where
M2 will lean on them; and demanding "first consumer = SDK consumer" delays
feedback behind a TS host that doesn't exist in daily use. *Standing
answer*: the surface is still correct — these are reasons the shim comes
first and the SDK consumer second, not reasons to change the surface.

### Against (2) — steelman first

The strongest honest case *for* MCP: it is the lingua franca of 2026 agent
tooling; Claude Code speaks it natively; one server would serve every
MCP-capable client without per-agent shims; typed tool schemas give models a
discoverable, self-describing affordance that a prose skill lacks; and
resolve-as-a-tool is arguably the product's destiny, so building it early
compounds. The rebuttal that survives contact: the destiny argument has no
falsifier today — the only real caller in the maintainer's loop (Claude
Code) can already reach the HTTP API through bash with *less* machinery, and
the most likely second consumer, pi, **has no MCP client at all by design**
(pi spike §3) — so the "serve every client" benefit currently serves zero
additional clients while costing a kernel-lane surface, a second secret
holder, and the §3.1 credential-boundary widening. MCP-first optimizes area,
not usefulness — ADR-0062's named failure mode (`0062:55-57`).

## 7. What would change this decision

- **A real MCP-native caller in daily use** — the maintainer adopting an
  MCP-only client for real work, or a committed design partner requiring
  MCP — satisfies `PLAN.md:24`'s activation trigger and reopens (2) via the
  ADR outlined below.
- **Consumer multiplication**: three or more shims drifting on the same
  obligations would flip the maintenance balance toward one server-side
  adapter (or a shared consumer library extracted from the SDK).
- **Remote or multi-user exposure**: ADR-0068 already requires deleting or
  replacing the dogfood composition then; production authentication design
  would subsume the MCP session-identity question rather than inherit this
  spike's answer.
- **pi shipping a native MCP client** would *not* change it: pi's native
  extension path (HTTP + SDK) remains strictly cheaper and typed end-to-end.

## 8. Conditional draft ADR outline (only if/when MCP activates — not now)

Recorded so the future decision starts from the constraints found here; this
spike explicitly does **not** recommend creating this ADR today, and no new
adapter surface is recommended.

> **ADR-00XX. Activate one MCP adapter terminating at the authenticated
> ingress** (draft outline)
>
> - *Context*: name the real MCP-native caller and its observed workload
>   (ADR-0062 evidence), and the shim-multiplication or partner constraint
>   that HTTP+SDK could not serve.
> - *Decision sketch*: MCP server lives in `adapters/`, is a pure protocol
>   translator, and terminates at the same `POST /v0/resolve` ingress with
>   its own configured credential — never sharing the dogfood, worker, or
>   operator secrets (ADR-0069 §5 pattern). One MCP session maps to one
>   pre-registered authentication binding; the server manufactures no
>   trusted fact and holds no package cache. Tool results carry
>   `expiresAt` + evidence refs verbatim; `Continue`/`OpenCitation` stay
>   excluded until their carriers activate.
> - *Ceremony*: kernel-lane under ADR-0064 (new authentication
>   composition): catalog activation evidence, refusal-by-default tests,
>   non-enumeration parity with the HTTP surface, secret-exclusion checks.
> - *Revisit/prohibited*: no cleartext fan-out to unauthenticated local
>   clients; no delivery-plane behavior (that is BotDelivery's TCB); no
>   widening of the dogfood composition to serve it.

## 9. Recommended sequence

1. **Now**: first consumer = Claude Code skill shim over the existing
   loopback HTTP API (pi spike §5 recommendation stands); record the
   consumer obligations as a short ADR when it lands.
2. **Second**: pi extension using the generated TS SDK — first programmatic
   consumer, proving the ADR-0048 facade before M2 BotDelivery relies on it.
3. **MCP**: remains `NOT_ACTIVE` with the §7 triggers and §8 outline on
   file; no `adapters/` MCP code, no new ADR under `docs/decisions/` today.
