# ContextEngine ActionPlane

This private TypeScript module owns the trusted `ActionPlane.prepare` and
`ActionPlane.perform` boundaries for one private delivery effect. Prepare
accepts only module-created `TrustedEffectIntent` values and returns one
operation-specific `ActionTicket`. Perform redeems that exact ticket and
canonical payload through a dedicated PostgreSQL action login before invoking
the deterministic private Sender twin at most once.

Applied effects retain a digest-only immutable receipt for zero-effect replay.
Ambiguous outcomes retain the original provider-attempt identity until a
trusted reconciliation records one monotonic terminal result. Real Sender
network access, group delivery, and external effects remain inactive.

Run `npm test` for the contract, type, and runtime checks. Real PostgreSQL
prepare/perform/RLS/idempotency/reconciliation evidence is exercised by the
repository integration suite.
