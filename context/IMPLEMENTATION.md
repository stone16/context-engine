# ContextEngine Minimal UI implementation contract

## Stack

- Python 3.13, FastAPI/Starlette, Jinja2 server-side templates, and static semantic
  CSS in the existing API process.
- Small progressive-enhancement JavaScript is allowed only for evidence disclosure
  ergonomics; native HTML behavior remains the functional baseline.
- No Node/TypeScript UI package, frontend build system, HTMX runtime dependency, or
  additional process.

## Directory structure

```text
ui/                 presentation, public HTTP client, templates, static assets
adapters/http/      API composition and public backing routes
tests/unit/ui/      fast presentation/public-seam behavior
tests/integration/  real PostgreSQL public HTTP/UI security behavior
```

The `ui/` tree may not import `engine/`. Adapters own the translation between public
wire contracts and sealed engine/control/learning modules.

## Commands

The UI owns `make ui-build` and `make ui-test`; both are wired into `make check`.
Repository completion uses the sequential verification contract in root `AGENTS.md`.

## Performance budget

- One HTML response plus one cacheable CSS asset; no render-blocking remote assets.
- No client framework or hydration. Enhancement JavaScript stays below 8 KiB
  uncompressed and never carries authorization facts.
- Layout shift is zero for SSR data; evidence disclosures reserve normal document
  flow rather than overlaying content.

## Definition of Done

- [ ] Seven job routes work over the public seam inside the existing API process.
- [ ] `ui/` has no import or dependency on `engine/`.
- [ ] Evidence flip works with keyboard and native HTML fallback.
- [ ] Loading, empty, refusal/error, success, and partial states are reachable.
- [ ] Below-sm, below-md, and below-lg rules from `DESIGN.md` hold.
- [ ] No placeholder/fabricated content or remote asset dependency.
- [ ] `make ui-build`, `make ui-test`, lint, typecheck, unit, integration, and security
  gates pass with fresh evidence.
- [ ] Root `DESIGN.md` contrast pairs pass and CSS uses semantic variables only.
