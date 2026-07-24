# ContextEngine ActionPlane

This private TypeScript module owns the trusted `ActionPlane.prepare` boundary
for one private delivery effect. It accepts only module-created
`TrustedEffectIntent` values, revalidates exact authority through the dedicated
PostgreSQL action login, and returns a closed zero-effect outcome or one
operation-specific `ActionTicket`.

`perform`, Sender/provider access, group delivery, and external effects are not
part of this package revision and remain inactive.

Run `npm test` for the contract, type, and runtime checks. Real PostgreSQL
prepare/RLS/idempotency evidence is exercised by the repository integration
suite.
