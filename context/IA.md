# ContextEngine Minimal UI information architecture

## Sitemap

```text
/ui                         operational overview and source health
/ui/ask                     Ask with citation lineage
/ui/import                  import preview and explicit confirmation
/ui/hit-test                authorized-only retrieval inspection
/ui/articles                Article lookup, visibility policy, rung, and confirmed edit
/ui/profiles                active versioned profiles and re-embed preview
/ui/feedback                answer feedback capture
```

## Page types

| Route | Pattern | Primary goal |
|---|---|---|
| `/ui` | operator overview | Establish current source and Release state |
| `/ui/ask` | answer workbench | Obtain authorized context with inspectable citations |
| `/ui/import` | preview/confirm form | Inspect exact Fragments before scheduling publication work |
| `/ui/hit-test` | diagnostic tool | Inspect authorized hits without observing refusals |
| `/ui/articles` | lookup + record detail | Understand and explicitly change one Article policy without placing tenant refs in URLs |
| `/ui/profiles` | configuration detail | Reproduce active model identities and preview re-embed impact |
| `/ui/feedback` | evidence form | Attach minimal feedback to one authorized ContextRun |

## Functional modules

- Public HTTP client and session/refusal boundary.
- Source/Release operational projection.
- ContextPackage answer, coverage, and citation projection.
- Import preview receipt and one explicit confirm action.
- Authorized Hit Test projection.
- Article policy detail and policy-change receipt.
- Active profile detail and consequence preview.
- Feedback evidence receipt with no publication operations.

## Primary user paths

1. `/ui` → inspect source/Release → `/ui/ask` → activate evidence flip.
2. `/ui/import` → preview → confirm or cancel → `/ui` status.
3. `/ui/hit-test` → query → inspect authorized hits only.
4. `/ui/articles` → exact Article → preview policy → confirm epoch-changing edit.
5. `/ui/profiles` → inspect active identity → preview re-embed consequence.
6. Answer → adjacent feedback form → record evidence → return to Ask.

## Navigation

- **Primary:** Overview, Ask, Import, Hit Test, Articles, Profiles, Feedback.
- **Contextual:** Evidence lineage, Article policy, and feedback links remain adjacent
  to the authorized item that produced them.
- **Footer:** Build identity and public-seam statement; no marketing links.
