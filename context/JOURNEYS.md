# ContextEngine Minimal UI journeys

## Journey 1 — First-visit happy path

- **Actor + entry:** Authenticated local maintainer opens `/ui` directly.
- **Goal:** Confirm operational state, ask one question, and inspect lineage.

| # | Screen | User does | System responds (+ state) | Next |
|---|---|---|---|---|
| 1 | `/ui` | Opens console | SSR success shows source cards and exact active Release generation | 2 |
| 1e | `/ui` | Opens without current context | Content-free refusal names only the safe category and recovery action | 1 |
| 2 | `/ui/ask` | Enters a question and submits | Input is preserved while the public resolve request completes | 3 |
| 3 | `/ui/ask` | Reads authorized Blocks | Success shows coverage and one evidence control per Block | 4 |
| 4 | `/ui/ask` | Activates a Block | Evidence flip reveals Article → Revision → Fragment and ACL lineage | 3 |

- **Success criteria:** A clean answer never appears when its citation closure is
  invalid; no user-visible element contains denied-candidate detail.

## Journey 2 — Explicit-change path

- **Actor + entry:** Returning maintainer opens Import or an Article detail.
- **Goal:** Make one bounded change only after previewing its exact consequences.

| # | Screen | User does | System responds (+ state) | Next |
|---|---|---|---|---|
| 1 | `/ui/import` | Supplies Source and canonical path | Preview renders the actual ordered Fragments and an opaque receipt | 2 |
| 2a | `/ui/import` | Cancels | Receipt is discarded; no publication work is scheduled | `/ui` |
| 2b | `/ui/import` | Confirms | Exact receipt is consumed once; success names only safe job lineage | `/ui` |
| 3 | `/ui/articles/{resourceRef}` | Requests another policy | Preview shows prior/effective setting, rung, and epoch consequence | 4 |
| 4a | same | Cancels | No policy or epoch changes | same |
| 4b | same | Confirms | Receipt is consumed once; policy and Policy Epoch advance atomically | same |

- **Step count defense:** Preview and confirmation are deliberately separate HTTP
  transitions. No shortcut, inline toggle, or optimistic mutation exists.
- **Forms:** Labels remain visible; validate on blur and submit; failed submission
  preserves safe user input but never echoes a secret or refused object.

## Journey 3 — Failure / recovery path

- **The failure:** Tenant/session context is missing or expired, or a required
  provider is unavailable.

| # | Screen | User does | System responds (+ state) | Next |
|---|---|---|---|---|
| 1 | any `/ui` route | Loads or submits | Explicit calm refusal; no empty-list mimicry and no object detail | 2 |
| 2 | same | Restores authenticated context or retries provider | Original non-secret input remains available; request is re-evaluated | success route |

- **What is never lost:** Query text and non-secret form selections. Authentication
  material, denied identifiers, and preview authority are never reflected into HTML.

## Journey 4 — Feedback evidence only

| # | Screen | User does | System responds (+ state) | Next |
|---|---|---|---|---|
| 1 | `/ui/feedback` | Selects helpful/not helpful for a run | Server validates exact current authorized run binding | 2 |
| 2 | same | Submits | Success returns a feedback Evidence receipt, not a Release action | answer |

## Cross-journey rules

- Query strings carry no identity or authority. Preview/confirm authority stays in an
  opaque, expiring, one-shot receipt.
- A successful change returns to its detail/status view, never a dead end.
- Loading is bounded SSR progress; empty means a genuine authorized empty result,
  while unavailable or missing context always renders refusal.
