---
version: alpha
name: ContextEngine Evidence Console
description: A flight-recorder evidence docket: exact, quiet, and built for inspection.
colors:
  background: "oklch(0.985 0.004 225)"
  foreground: "oklch(0.205 0.012 225)"
  muted: "oklch(0.955 0.006 225)"
  muted-foreground: "oklch(0.445 0.016 225)"
  card: "oklch(0.998 0.002 225)"
  card-foreground: "oklch(0.205 0.012 225)"
  primary: "oklch(0.43 0.095 195)"
  primary-foreground: "oklch(0.985 0.004 225)"
  secondary: "oklch(0.935 0.008 225)"
  secondary-foreground: "oklch(0.255 0.014 225)"
  destructive: "oklch(0.43 0.13 25)"
  destructive-foreground: "oklch(0.985 0.004 225)"
  border: "oklch(0.875 0.008 225)"
  input: "oklch(0.875 0.008 225)"
  ring: "oklch(0.43 0.095 195)"
typography:
  display: { fontFamily: "IBM Plex Sans, Avenir Next, ui-sans-serif, system-ui, sans-serif", fontSize: 38px, fontWeight: 600, lineHeight: 1.12, letterSpacing: -0.02em }
  heading: { fontFamily: "IBM Plex Sans, Avenir Next, ui-sans-serif, system-ui, sans-serif", fontSize: 24px, fontWeight: 600, lineHeight: 1.25, letterSpacing: -0.01em }
  body-md: { fontFamily: "IBM Plex Sans, Avenir Next, ui-sans-serif, system-ui, sans-serif", fontSize: 16px, fontWeight: 400, lineHeight: 1.6 }
  mono: { fontFamily: "IBM Plex Mono, SFMono-Regular, Consolas, ui-monospace, monospace", fontSize: 13px, fontWeight: 400, lineHeight: 1.55 }
spacing: { base: 4px, gutter: 24px, section: 64px }
rounded: { sm: 3px, md: 5px, lg: 7px, full: 9999px }
breakpoints: { sm: 640px, md: 768px, lg: 1024px, xl: 1280px }
motion: { feedback: 100ms, content: 180ms, easing: "cubic-bezier(0.2, 0, 0, 1)" }
components:
  button-primary: { backgroundColor: "{colors.primary}", textColor: "{colors.primary-foreground}", rounded: "{rounded.md}", height: 44px, padding: 20px }
  button-primary-hover: { backgroundColor: "{colors.foreground}", textColor: "{colors.primary-foreground}" }
  button-primary-active: { backgroundColor: "{colors.foreground}", textColor: "{colors.primary-foreground}" }
  button-primary-disabled: { backgroundColor: "{colors.muted}", textColor: "{colors.muted-foreground}" }
  button-secondary: { backgroundColor: "{colors.secondary}", textColor: "{colors.secondary-foreground}", rounded: "{rounded.md}", height: 44px, padding: 20px }
  input-default: { backgroundColor: "{colors.card}", textColor: "{colors.card-foreground}", rounded: "{rounded.md}", height: 44px, padding: 12px }
  input-focus: { backgroundColor: "{colors.card}", textColor: "{colors.card-foreground}" }
  input-error: { backgroundColor: "{colors.card}", textColor: "{colors.destructive}" }
  evidence-block: { backgroundColor: "{colors.card}", textColor: "{colors.card-foreground}", rounded: "{rounded.lg}", padding: 24px }
  evidence-block-open: { backgroundColor: "{colors.muted}", textColor: "{colors.foreground}", rounded: "{rounded.lg}", padding: 24px }
  refusal-state: { backgroundColor: "{colors.muted}", textColor: "{colors.foreground}", rounded: "{rounded.lg}", padding: 24px }
---

# ContextEngine Evidence Console

## Overview

The console feels like a flight-recorder evidence docket: exact labels, bounded
facts, restrained rules, and no decorative urgency. The governing rule is that
security state must be legible without becoming enumerable. **Signature:** the
evidence flip—activating a content Block replaces the reading emphasis with its
Article → Revision → Fragment lineage, single Evidence ref, and visibility rung.
Everything else stays conventional and quiet.

## Colors

Near-achromatic cool paper and ink carry the interface. Deep teal is reserved for
actions, links, disclosure controls, and focus rings. It never colors a status for
decoration. Refusal uses the same muted surface family as other operational states;
its semantics come from heading, icon/text, and recovery copy, not alarming color.
Dark red is restricted to destructive confirmation copy and field validation.

## Typography

IBM Plex Sans or the locally available Avenir Next gives operational labels a
technical but human cadence; system sans fallbacks keep the console network-free.
IBM Plex Mono/SFMono holds opaque refs, digests, generations, scores, and timestamps.
Long content remains in the sans body face at a readable 60–75 character measure.
Use weights 400, 500, and 600 only.

## Layout

The desktop shell is a 240px navigation rail beside a fluid work column capped at
1120px. A 4px base and 24px gutters create dense, repeatable rhythm; related facts
align in definition grids rather than nested cards. Pages have one primary work
surface. Provenance expands in normal document flow so no evidence is obscured.

## Elevation & Depth

Depth comes from tonal surfaces and one-pixel borders. Resting records have no
floating shadow; an interactive evidence Block may gain a quiet one-pixel inset
rule. No blur, glow, glass, gradient, or layered card stack.

## Shapes

Radii stay tight—3px to 7px—to read as an engineered instrument. Pills are allowed
only for genuine closed status values. Tables, forms, and disclosure records share
the same corner family.

## Components

- Primary and secondary buttons are at least 44px high; focus uses a two-pixel teal
  ring with a background offset. Disabled controls retain explanatory adjacent copy.
- Inputs use persistent top-aligned labels, validate on blur and submit, and preserve
  non-secret values after safe failure.
- `evidence-block` renders the authorized content first. Its single disclosure
  control opens `evidence-block-open`, which exposes lineage in an ordered definition
  list. Missing or inconsistent lineage flags/refuses the Block instead.
- `refusal-state` always names a tenant-safe category and one recovery action. It is
  never represented as an empty list, blank table, toast-only error, or spinner.
- Loading, authorized-empty, partial, refusal, and success are real server-rendered
  components with stable headings; they never infer facts the response did not carry.

## Do's and Don'ts

- **Do spend boldness in exactly one place: the evidence-flip interaction.**
- Do make every rendered content Block close over exactly one Evidence ref.
- Do distinguish authorized empty from refusal in words and document structure.
- Do reserve teal for interaction and visible focus; keep status facts neutral.
- Do use real Release, source, Article, and ContextPackage facts only.
- Do preserve keyboard order, semantic headings, labels, and 44px touch targets.
- Don't render denied candidates, denied counts, original denied ranks, or score gaps.
- Don't add gradients, glass, glows, fake metrics, decorative charts, or nested cards.
- Don't add an inline policy toggle, automatic publish action, or feedback promotion.
- Don't fetch remote fonts/assets or introduce a frontend runtime/build process.

## Responsive Behavior

- Below `lg`: the side rail becomes a wrapped top navigation; page context precedes
  all actions; no functionality hides behind hover.
- Below `md`: definition grids and two-column comparisons become one column; tables
  become labeled record stacks, never viewport-wide horizontal body scroll.
- Below `sm`: gutters become 16px; primary actions use full available width; evidence
  refs wrap safely; every action/disclosure target remains at least 44px.

## Motion

Use the 100ms feedback token for hover/focus response and 180ms for evidence reveal.
No page-load choreography, bounce, elastic easing, or attention pulse. Under
`prefers-reduced-motion`, evidence changes instantly and focus remains explicit.

## Accessibility

All normal text/background pairs target WCAG AA contrast. Every form control has a
visible label, errors are text plus semantics rather than color alone, and refusal
headings receive focus after a submitted request. Native disclosure semantics and
keyboard activation are the evidence-flip baseline. Status updates use a polite live
region only when enhanced navigation is present.

## Iconography & Imagery

Use simple inline line icons only where their meaning is independently labeled.
Operational states prefer text and definition lists. No hero art, stock imagery,
provider logos, emoji navigation, or illustrative dashboards.

## Iteration Guide

Change semantic tokens here before changing CSS. Add a component token only when a
new local role cannot be expressed by the existing surface/action/state vocabulary.
Any richer interaction must preserve the native HTML path and public-seam tests.

## Known Gaps

The M1 surface has no data-visualization language, bulk-selection pattern, workflow
canvas, or mobile-native navigation; those are intentionally outside issue #130.
