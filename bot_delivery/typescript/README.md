# ContextEngine private BotDelivery application

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

Issue #71 composes the complete private deterministic-twin flow in one
independent Bot process. Exact twin-verified question turns create one
placeholder, resolve a File-backed Package through the installed generated SDK,
generate under the model boundary, and finalize by edit or private follow-up
through distinct ActionPlane prepare/perform pairs. The resulting
`DeliveryReceipt` contains only Package/attempt/receipt/audit refs and status;
the restricted FORCE-RLS audit retains no answer, Package body, or bearer.
Citation events use a separate exact current evidence binding through the same
SDK. Live Feishu/model/Sender providers, streaming, group delivery,
compensation/delete, Continue, and MCP remain inactive.

The installed package root intentionally does not export the process-private
identity twin constructor or its fixture shapes. The shipped Bot binary loads
the deterministic binding document from its credential/configuration boundary,
then consumes closed newline-delimited answer and citation events from standard
input. It dispatches only twin-minted nominal values. Citation responses emitted
by the process contain status, purpose, and Package digest—not the Package or
EgressGrant bearer.

The process configuration contract consists of the generated-SDK base URL and
authentication, dedicated action and egress database URLs, action signing key,
Organization ID, deterministic answer/citation settings, and one closed twin
binding document. Environment variable names are defined and validated in
`src/main.ts`; live values belong only in the ignored generated database/config
sources and must never be committed. The twin binding document has exactly
`questionTurns` and `citationOpens` arrays. Stdin accepts exactly an `answer`
event (`kind`, `turnRef`, `eventVerificationRef`, `question`) or an
`open_citation` event (`kind`, `openRef`, `eventVerificationRef`,
`citationOpenRef`) per line. A normal startup emits one readiness record; each
line then emits one body/bearer-free outcome and deterministic counter record.
The deterministic process also accepts closed `generated | invalid_output`
model and `applied | rejected | ambiguous` Sender twin modes; they are bounded
conformance modes, not live-provider configuration.

Repository verification builds the generated SDK before this package. Run the
`bot-typecheck`, `bot-build`, and `bot-test` Make targets for the standalone
contract, runtime, and installed-package checks. Real PostgreSQL and local API
evidence is exercised by the repository integration suite.

All hashed JSON first passes the same I-JSON Unicode-scalar domain used by the
Python Package digest implementation. A shared fixture proves RFC 8785 Unicode,
UTF-16 property ordering, number serialization, and lone-surrogate rejection
across both runtimes.
