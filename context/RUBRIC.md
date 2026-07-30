# ContextEngine Minimal UI review rubric

## Design

- [ ] Calm, precise, trustworthy, and austere in default, empty, and refusal states.
- [ ] Boldness is spent only on the evidence-flip interaction.
- [ ] Information hierarchy uses spacing and weight, not decorative color or cards.
- [ ] Hover, focus, active, disabled, empty, refusal, partial, and success are covered.
- [ ] No gradients, glass, glow, fake dashboard, invented metrics, or dramatic errors.

## Security and domain

- [ ] Every content Block closes over exactly one Evidence reference.
- [ ] Hit Test cannot reveal denied candidates, existence, count, rank, or score gap.
- [ ] Empty and refusal states are unambiguous and non-enumerating.
- [ ] Policy and import mutations require exact preview plus explicit one-shot confirm.
- [ ] Feedback owns no activate/promote/publish/rollback entry point.
- [ ] UI imports only public HTTP/wire seams, never `engine/`.

## UX flow

- [ ] Happy, explicit-change, failure/recovery, and feedback journeys are walkable.
- [ ] Non-secret input survives safe failures; secrets and denied identifiers do not.
- [ ] Evidence disclosure is keyboard-usable and returns focus predictably.

## Engineering

- [ ] UI build/test targets and full repository verification are green.
- [ ] No frontend build system or additional process was introduced.
- [ ] CSS has no raw component colors; focus is visible; targets are at least 44px.
- [ ] Templates escape operator/content data by default.
