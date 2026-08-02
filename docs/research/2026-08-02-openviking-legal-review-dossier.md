# OpenViking conditional-admission legal-review dossier

> Prepared: 2026-08-02
>
> Status: **PREPARED_AWAITING_MAINTAINER**
>
> Decision owner: ContextEngine maintainer/legal reviewer
>
> Scope: issue #205, OpenViking snapshot
> `49b182045b42d34ad530948ad77d9d0226897da8`

## 1. Authority and requested decision

This dossier records source facts and the exact questions required for GitHub
issue #205. It is not legal advice and does not approve OpenViking admission. An agent
cannot supply the maintainer/legal sign-off.

Repository `git log` at this branch shows one unique author email under two
display names. The same maintainer therefore performed the Room-A evaluation
and ContextEngine implementation work: personnel separation cannot be claimed,
and the legal decision must determine whether documentary and temporal
separation is sufficient or whether this repository needs a different protocol.

The proposed admission is behavior-observation-only:

- observe pinned public documentation, source shapes, and tests;
- publish bounded factual claims through the versioned evidence baseline;
- use clean-room behavior specifications and test oracles;
- copy, vendor, link, execute, import, or depend on **zero** OpenViking code,
  services, schemas, SDKs, generated artifacts, models, or assets.

The whitelist is context filesystem/L0–L2 tiering, session-to-candidate UX,
observable trajectory, and agent exposure. The blacklist is multi-tenant
authorization/security proof, Runtime foundation, compliance proof, and every
copy+patch path. The blacklist remains closed even if upstream later clarifies
a permissive subtree.

## 2. Fixed identity and permalink audit

| Fact | Evidence |
|---|---|
| Repository | [`volcengine/OpenViking`](https://github.com/volcengine/OpenViking) |
| Snapshot | [`49b182045b42d34ad530948ad77d9d0226897da8`](https://github.com/volcengine/OpenViking/commit/49b182045b42d34ad530948ad77d9d0226897da8) |
| Commit time and subject | 2026-07-31T03:38:57Z; `refactor(parser): Refactor code summaries to fixed skeleton-first routing (#3568)` |
| Permalink audit | The tracked evaluation contains 66 OpenViking `blob`/`tree` links covering 41 unique paths; all 66 carry the full snapshot SHA, and all 41 paths exist in that snapshot's Git tree. Floating `main`, `master`, `develop`, and `HEAD` evidence links: 0. |

The permalink audit establishes traceability only. It does not approve the
claims or their legal use.

## 3. File-level license facts

| Region | Pinned file-level evidence | Observed ambiguity or boundary |
|---|---|---|
| Root/main Python project | Root [`LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/LICENSE#L1-L12) is GNU AGPLv3; [`pyproject.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/pyproject.toml#L12-L20) declares `AGPL-3.0` | Treat as AGPL-covered unless legal identifies a more specific controlling grant for an exact file set |
| AGPL remote-network clause | [`LICENSE` §13](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/LICENSE#L541-L560) requires a modified version supporting remote interaction to offer its Corresponding Source to remote users | Whether pure behavior observation and independently authored implementation implicate any obligation is a legal decision, not an agent conclusion |
| `crates/` and Rust CLI | [`crates/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/LICENSE#L1-L5) is Apache-2.0; [`crates/ov_cli/Cargo.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/crates/ov_cli/Cargo.toml#L1-L8) declares MIT; [`README.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/README.md#L233-L240) calls the Rust CLI Apache-2.0 | MIT-versus-Apache metadata conflict; no license choice may be inferred |
| `examples/` parent | [`examples/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/LICENSE#L1-L5) is Apache-2.0 | Child manifests and SPDX identifiers differ, so the parent file is not treated as an unconditional subtree conclusion |
| OpenWebUI example | [`pyproject.toml`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openwebui-plugin/pyproject.toml#L7-L12) declares AGPL-3.0; [`tools.py`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openwebui-plugin/openviking_openwebui/tools.py#L1-L3) carries an AGPL-3.0 SPDX identifier | Explicit child override/fact |
| OpenCode example | [`package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/opencode-plugin/package.json#L40-L46) declares Apache-2.0 | Exact manifest fact only |
| OpenClaw example | [`package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/examples/openclaw-plugin/package.json#L56-L65) declares MIT | Exact manifest fact only |
| npm CLI | [`npm/cli/package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/npm/cli/package.json#L19-L24) declares Apache-2.0 | Exact manifest fact only; excluded from reuse |
| TypeScript SDK | [`sdk/typescript/package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/sdk/typescript/package.json#L1-L7) declares Apache-2.0 | Exact manifest fact only; excluded from reuse |
| `bot/` | [`bot/package.json`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/bot/package.json#L1-L17) declares ISC; [`bot/license/LICENSE`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/bot/license/LICENSE#L1-L13) is a nanobot MIT text | No coherent subtree license is inferred; excluded from reuse |
| Imported query material | [`THIRD_PARTY_NOTICES.md`](https://github.com/volcengine/OpenViking/blob/49b182045b42d34ad530948ad77d9d0226897da8/openviking/parse/parsers/code/ast/queries/THIRD_PARTY_NOTICES.md#L1-L26) records mixed provenance and Aider-derived Apache-2.0 material | Nested third-party grants do not change the project-wide boundary |

Several other child manifests have no license field. Missing metadata is not
treated as permission.

## 4. Region disposition proposed for maintainer review

| Region/use | Engineering disposition | Legal status |
|---|---|---|
| Root project, Python implementation, tests, and implementation-bearing docs | Room-A observation only; zero copying, execution, dependency, or integration | Awaiting maintainer/legal approval of observation-only protocol |
| `crates/ov_cli/**` | Excluded, regardless of upstream answer | Upstream metadata clarification pending |
| `examples/**`, `bot/**`, SDK/npm surfaces, imported/third-party assets | Excluded | No legal interpretation needed for current no-reuse decision; reassess only if scope changes |
| Four admitted behavioral claim families | Pinned factual descriptions through the v2 baseline | Awaiting maintainer/legal approval |
| Multi-tenant security, Runtime foundation, compliance proof, copy+patch | Prohibited | Not offered for approval |

This disposition is stricter than ADR-0074's generic path-level permissive reuse
option. The constraint ruling out that alternative is maintainer decision D9:
OpenViking copy+patch is blacklisted even after license disambiguation.

## 5. Exact maintainer/legal questions

The decision record must answer each question explicitly:

1. Does reading the fixed public OpenViking snapshot solely to extract behavior,
   Interface shapes, and test oracles—with no copying, dependency, execution,
   generated artifact, service integration, or Runtime use—satisfy
   ContextEngine's clean-room policy for an AGPLv3 source?
2. Given one maintainer performing both roles, is documentary and temporal
   separation—dated Room-A artifacts, no upstream-source access during an
   implementation pass, and a specification/test-oracle-only handoff—sufficient?
   If not, is the two-room protocol unavailable for this repository, and what
   enforceable replacement is required?
3. May ContextEngine publish commit-pinned factual behavior descriptions and
   permalinks from the AGPL repository as public provenance under that no-copy
   scope?
4. Does any current ContextEngine build, dependency, generated artifact, or
   deployed process incorporate, link, execute, modify, or communicate with
   OpenViking-covered material? If the answer is no, which future changes must
   trigger a fresh legal review?
5. How does AGPLv3 §13 apply, if at all, to the proposed observation-only
   protocol and independently authored ContextEngine behavior? Record the
   reasoning and conditions; do not rely on the agent's summary.
6. For `crates/ov_cli/**`, which grant does upstream intend: Cargo's MIT
   declaration, the README/parent Apache-2.0 declaration, a dual-license choice,
   or another grant? What exact source paths and notice obligations would that
   cover?
7. For `examples/**`, does the parent Apache-2.0 file operate only as a default
   subject to child declarations? How should paths with AGPL, MIT, Apache, or
   missing child metadata be classified?
8. Should all ambiguous and nonessential paths remain excluded even after
   clarification? The engineering recommendation is yes because generated SDK
   and native ContextEngine implementations already remove any reuse need.
9. Is approval limited to snapshot `49b1820…` and exactly the four whitelist
   claim families, with security proof, Runtime foundation, compliance proof,
   and all copying expressly excluded?
10. Who owns re-review, and what events trigger it: upstream snapshot change,
    claim-family expansion, Room-A/Room-B protocol change, any new dependency,
    any OpenViking process execution, connector proposal, or deployment change?

## 6. Upstream clarification request

The metadata disambiguation request required by issue #205 is open as
[`volcengine/OpenViking#3689`](https://github.com/volcengine/OpenViking/issues/3689).
It asks which license governs the exact `crates/ov_cli/**` source set and what
notices apply. It also states that ContextEngine will not copy or vendor the code
regardless of the answer.

The upstream response is useful provenance, but it cannot replace
ContextEngine's maintainer/legal decision.

## 7. Maintainer decision record

The maintainer/legal reviewer should replace the pending cells in a review
commit or issue #205 comment. Do not merge the candidate baseline while they
remain pending.

| Decision | Required record |
|---|---|
| Observation-only AGPL clean-room protocol | `APPROVED` or `REJECTED`, reviewer, date, conditions, rationale |
| Public pinned behavior claims | `APPROVED` or `REJECTED`, exact claim families and snapshot |
| Room-A/Room-B controls | Required separation, allowed artifacts, retention, reviewer |
| §13 analysis | Reviewer-authored conclusion and re-review triggers |
| `ov_cli`/examples | Excluded paths plus treatment of any upstream response |
| Copy+patch | Must remain `PROHIBITED` under D9 |
| Re-review owner | Named maintainer/legal owner and triggering events |
| `AGENTS.md` wording | Apply through the maintainer doc-steward workflow or reject with rationale |

Current checkbox status:

- [x] Fixed repository identity and path-level source facts prepared.
- [x] Per-claim permalink coverage mechanically verified.
- [x] Upstream `ov_cli` metadata disambiguation request sent.
- [x] Exact maintainer/legal questions prepared.
- [ ] Maintainer/legal decision recorded.
- [ ] `AGENTS.md` semantic amendment applied through maintainer doc-steward or
  explicitly rejected.

## 8. Prepared `AGENTS.md` amendment

Issue #205 reserves this write for the maintainer doc-steward workflow. A fresh
deterministic doc-steward evaluation on 2026-08-02 passed at 10.0/10.0 with no
findings, so there is no mechanical ENFORCE disposition that can authorize this
semantic change. `AGENTS.md` is intentionally unchanged in this PR.

Apply all three replacements atomically; applying only one would leave the
charter internally inconsistent.

### Pair 1 — public-evidence authority path

Current text:

> `CONTEXT.md` (glossary). Public reference claims must trace to
> `docs/research/2026-07-19-four-public-repositories-evidence.md` or first-party
> ContextEngine requirements and `docs/security/context-engine-threat-model.md`.

Prepared replacement:

> `CONTEXT.md` (glossary). Public reference claims must trace to
> `docs/research/2026-08-02-five-public-repositories-evidence.md` or first-party
> ContextEngine requirements and `docs/security/context-engine-threat-model.md`.

### Pair 2 — controlled-reuse safety rail

Current text:

> **Controlled third-party reuse (ADR-0074)** — copying is permitted only from
> license-verified permissive regions at pinned commits (RAGFlow Apache-2.0;
> Onyx outside every `ee/` directory, MIT; separately-licensed MIT SDK
> subtrees), registered under `third_party/` with full attribution and SBOM
> coverage in shipped artifacts. Dify root-licensed code, MaxKB GPLv3 code,
> and Onyx `ee/` code remain clean-room only: behavior observations, interface
> shapes, and test oracles via the two-room protocol. Every public reference
> claim still traces through the four-repository evidence report;
> repository-external research inputs must never be cited, linked, or
> presented as public provenance.

Prepared replacement:

> **Controlled third-party reuse (ADR-0074)** — copying is permitted only from
> license-verified permissive regions at pinned commits (RAGFlow Apache-2.0;
> Onyx outside every `ee/` directory, MIT; separately-licensed MIT SDK
> subtrees), registered under `third_party/` with full attribution and SBOM
> coverage in shipped artifacts. Dify root-licensed code, MaxKB GPLv3 code,
> Onyx `ee/` code, and all OpenViking source regions remain clean-room only:
> behavior observations, interface shapes, and test oracles through the
> applicable maintainer-approved clean-room protocol. D9 additionally prohibits
> every OpenViking copy+patch path even if upstream later clarifies a permissive
> region. Every public prior-art reference claim still traces through the
> five-repository evidence baseline; ContextEngine's own claims trace to its
> first-party requirements and threat model. Repository-external research
> inputs must never be cited, linked, or presented as public provenance.

### Pair 3 — tracked repository-external research

Current text:

> Repository-external research may inform independent reasoning, but it is
> neither public authority nor publishable provenance.

Prepared replacement:

> Repository-external research may inform independent reasoning and may be
> tracked under `docs/research/` as maintainer-local input, but it is never
> citable as public authority or claim provenance; public prior-art reference
> claims still trace only to the versioned repository-evidence baseline, while
> ContextEngine's own claims trace to first-party requirements and the threat
> model.

The maintainer must use the doc-steward write workflow to apply this wording, or
record an explicit rejection and rationale in issue #205. Current status:
**PREPARED_AWAITING_MAINTAINER_DOC_STEWARD**.
