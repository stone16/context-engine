# ContextEngine Minimal UI page specification

## Shared application-shell contract

- **Single goal:** Keep seven fixed operator jobs reachable without implying a
  general administration canvas.
- **Section order:** skip link → product/header state → primary navigation → page
  title/context → one primary work surface → safe status/refusal → footer.
- **States:** Every data surface has loading, authorized empty, refusal/error,
  success, and partial/degraded variants. Refusal never reuses empty styling or copy.
- **Data strategy:** Server-side render from public HTTP responses; `Cache-Control:
  no-store`; no optimistic security or publication state.
- **Responsive:** Below `lg`, navigation becomes a wrapped top rail; below `md`,
  split details become one column and tables become labeled record stacks; below
  `sm`, all actions and evidence summaries remain at least 44px tall.
- **SEO:** `noindex, nofollow`; this is an authenticated local tool.

## Page: `/ui`

- **Goal:** Establish source health and exact active Release generation.
- **Sections:** Release header → source health list → fixed pipeline/job links.
- **Primary action:** “Ask ContextEngine.”
- **States:** Authorized zero sources is an explicit first-run state; failed/stale
  sources show safe refusal category; Release unavailability is a refusal.
- **Acceptance:** Generation matches the promoted Release; status includes counts and
  never turns an unavailable source into a blank card.

## Page: `/ui/ask`

- **Goal:** Render an authorized answer with complete, resolvable citation lineage.
- **Sections:** Question form → coverage → Blocks → evidence details → feedback link.
- **Primary action:** “Ask.”
- **Signature:** Each Block is paired with one disclosure control that flips focus
  from content to Evidence lineage without a page navigation.
- **States:** Empty coverage names `no_authorized_evidence`; unresolvable citation
  shows flagged/refused answer state, never clean content.
- **Form:** One required question; validate on submit; preserve it after safe refusal.

## Page: `/ui/import`

- **Goal:** Publish no ingestion work until exact Fragment preview is confirmed.
- **Sections:** Source/path form → ordered Fragment preview → consequence summary →
  Confirm and Cancel actions → receipt.
- **Primary action:** Preview first; Confirm exists only on a valid preview.
- **States:** Cancel is success-with-no-effect; stale/changed preview refuses confirm.
- **Form:** Source and canonical path; no upload or filesystem path leakage.

## Page: `/ui/hit-test`

- **Goal:** Show authorized Fragment hits and scores for one acting Principal.
- **Sections:** Query form → coverage → authorized hit list with Evidence controls.
- **Primary action:** “Run Hit Test.”
- **Forbidden:** Raw CandidateRef, denied count, denied rank, score gaps, SQL/log detail,
  or wording that implies how many results were filtered.
- **States:** Authorized empty is one result category; refusal is visually and
  semantically distinct but equally calm.

## Page: `/ui/articles`

- **Goal:** Explain one effective Article policy/rung and require preview + confirm
  for any existing-Article change.
- **Sections:** Exact Article identity → effective/local policy → resolution rung →
  change preview → confirm/cancel → epoch receipt.
- **Forbidden:** Inline toggle, bulk action, Source-container permission, or mutation
  without expected policy version and expected Policy Epoch.

## Page: `/ui/profiles`

- **Goal:** Reproduce active provider/retrieval identities and preview profile-change
  consequences.
- **Sections:** Release generation → content/index/runtime profile identities and
  digests → proposed embedding profile → re-embed warning.
- **Primary action:** “Preview profile change.” M1 exposes no activation action.
- **States:** Every embedding change preview states full-corpus re-embed required.

## Page: `/ui/feedback`

- **Goal:** Record minimal answer evidence bound to an authorized ContextRun.
- **Sections:** Run/package identity → helpful/not helpful → optional bounded note →
  receipt.
- **Forbidden:** Activate, promote, publish, rollback, curate, or change a profile.

## Acceptance criteria

- [ ] Every route and action works through the public HTTP seam.
- [ ] Every declared state is reachable, keyboard-usable, and content-safe.
- [ ] Forms preserve non-secret input on failure and cannot double-confirm.
- [ ] Responsive reflow holds below `lg`, `md`, and `sm`.
- [ ] No placeholder, fabricated record, secret, or refused-candidate detail renders.
- [ ] Components consume only semantic tokens from root `DESIGN.md`.
- [ ] WCAG AA contrast and visible focus hold; touch targets are at least 44px.
