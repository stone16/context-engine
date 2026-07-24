# ContextEngine BotDelivery model egress

This private TypeScript module owns the only active model-generation boundary
for BotDelivery. It accepts a nominal `AuthorizedModelInput` built from exactly
one complete, current, audience-bound `ContextPackage` returned by the generated
resolve SDK, one opaque model `EgressGrant`, and the module-owned
`privateModelGatewayProfileV1()` policy. Callers cannot construct or modify the
registered profile. Before any provider bytes leave, the sealed
`createPrivateModelGenerationBoundary` factory creates and owns the PostgreSQL
client; no public constructor or structural query object can supply authority.
It redeems the grant through the dedicated non-owner, function-only PostgreSQL
egress authority with exact Organization, Package, audience, purpose, Policy Epoch,
provider, model, region, retention, sensitivity, issuer, consumer, and profile
bindings.

Issue #70 activates only `DeterministicModelGatewayTwin`. The twin receives the
authorized Package blocks plus the declared question and instructions—never the
grant, trusted identity, denied details, audit data, or arbitrary extra text. A
successful result is bounded by the versioned profile, cites only Evidence from
that Package, and is recorded in digest-only restricted audit before release.
All binding, replay, provider, output, or audit failures return one generic
unavailable result. Real model providers, streaming, group delivery, and action
effects remain inactive; effects must use `ActionPlane.prepare` and
`ActionPlane.perform`.

Repository verification builds the generated SDK before this package. Run the
`bot-typecheck`, `bot-build`, and `bot-test` Make targets for the standalone
contract, runtime, and installed-package checks. Real PostgreSQL and local API
evidence is exercised by the repository integration suite.

All hashed JSON first passes the same I-JSON Unicode-scalar domain used by the
Python Package digest implementation. A shared fixture proves RFC 8785 Unicode,
UTF-16 property ordering, number serialization, and lone-surrogate rejection
across both runtimes.
