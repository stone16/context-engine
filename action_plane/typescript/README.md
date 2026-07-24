# ContextEngine ActionPlane

This private TypeScript module owns the trusted `ActionPlane.prepare` and
`ActionPlane.perform` boundaries for one private delivery effect. Prepare
accepts only module-created `TrustedEffectIntent` values and returns one
operation-specific `ActionTicket`. Perform redeems that exact ticket and
canonical payload through a dedicated PostgreSQL action login before invoking
the deterministic private Sender twin at most once. Perform holds one
Organization/ActionTicket PostgreSQL session advisory lock on a dedicated
connection across Sender and durable completion; reconciliation cannot race an
active Sender in the same or another ActionPlane process. Post-lock database
failures release that lock inside the authority function, while an indeterminate
begin-query failure causes the SDK to discard the dedicated connection.
Loss of the dedicated database session after Sender releases the lock at the
PostgreSQL backend and leaves the original durable attempt for reconciliation.

Applied effects retain a digest-only immutable receipt for zero-effect replay.
The Sender-supplied applied-at value is retained with a bounded five-second
positive clock-skew allowance; larger future values remain reconcilable and
cannot create a receipt.
Ambiguous outcomes retain the original provider-attempt identity until a
trusted reconciliation records one monotonic terminal result. Real Sender
network access, group delivery, and external effects remain inactive.

Run `npm test` for the contract, type, and runtime checks. Real PostgreSQL
prepare/perform/RLS/idempotency/reconciliation evidence is exercised by the
repository integration suite.
