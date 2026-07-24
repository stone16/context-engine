import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

import canonicalize from "canonicalize";

export type ActionOperation =
  | "create_placeholder"
  | "finalize_reply"
  | "send_private_followup";

export type JsonValue =
  | boolean
  | number
  | string
  | null
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

export type EffectPayload = Readonly<Record<string, JsonValue>>;

const ACTION_PAYLOAD_DOMAIN = Buffer.from("context-engine.action-payload.v1\0");
const ACTION_TICKET_DOMAIN = Buffer.from("context-engine.action-ticket.v2\0");
const ACTION_BINDING_DOMAIN = "context-engine.action-binding.v1\0";
const PRIVATE_AUDIENCE_DOMAIN = Buffer.from("context-engine.private-delivery-audience.v1\0");
const PRIVATE_AUDIENCE_PROFILE = "private-delivery-audience-rfc8785-sha256-v1";
const ACTION_TICKET_DIGEST_PROFILE = "action-ticket-sha256-v1";
const ACTION_PAYLOAD_DIGEST_PROFILE = "action-payload-rfc8785-sha256-v1";
const MEDIA_TYPE = "application/json";
const MAX_REF_LENGTH = 256;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DELIVERY_ATTEMPT_PATTERN = /^dla_[0-9a-f]{32}$/;
const TICKET_REF_PATTERN = /^act_[0-9a-f]{32}$/;
const PROVIDER_ATTEMPT_PATTERN = /^pat_[0-9a-f]{32}$/;
const RECEIPT_REF_PATTERN = /^acr_[0-9a-f]{32}$/;
const ticketConstructionAuthority = Object.freeze({});
const trustedTickets = new WeakSet<object>();
const ACTION_TICKET_HEADER_KEYS = ["alg", "domain", "keyVersion", "typ"] as const;
const ACTION_TICKET_CLAIM_KEYS = [
  "approvalTier",
  "audience",
  "audienceDigest",
  "deliveryAttemptRef",
  "destinationDigest",
  "expiresAt",
  "idempotencyDigest",
  "identityDigest",
  "issuedAt",
  "operation",
  "organizationId",
  "payloadDigest",
  "policyEpoch",
  "profileRef",
  "purposeDigest",
  "serviceDigest",
  "signingKeyVersion",
  "sourceContextDigest",
  "ticketRef",
  "type",
] as const;

const OPERATION_CONTRACT = {
  create_placeholder: {
    audience: "private-effect:create-placeholder",
    type: "CE-CreatePlaceholderActionTicket",
  },
  finalize_reply: {
    audience: "private-effect:finalize-reply",
    type: "CE-FinalizeReplyActionTicket",
  },
  send_private_followup: {
    audience: "private-effect:send-private-followup",
    type: "CE-SendPrivateFollowupActionTicket",
  },
} as const satisfies Record<ActionOperation, { audience: string; type: string }>;

function requireRef(name: string, value: unknown, maximum = MAX_REF_LENGTH): string {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > maximum
    || value.trim() !== value
    || /\s/u.test(value)
  ) {
    throw new TypeError(`${name} must be a bounded opaque reference`);
  }
  return value;
}

function requireUuid(name: string, value: unknown): string {
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    throw new TypeError(`${name} must be a canonical UUID`);
  }
  return value;
}

function requirePositiveInteger(name: string, value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new TypeError(`${name} must be a positive safe integer`);
  }
  return value as number;
}

function requireSha256(name: string, value: unknown): string {
  if (typeof value !== "string" || !SHA256_HEX.test(value)) {
    throw new TypeError(`${name} must be lowercase SHA-256`);
  }
  return value;
}

function requireDate(name: string, value: unknown): Date {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
    throw new TypeError(`${name} must be a valid Date`);
  }
  return value;
}

function requireCanonicalDateString(name: string, value: unknown): string {
  if (typeof value !== "string") {
    throw new TypeError(`${name} must be one canonical timestamp`);
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString() !== value) {
    throw new TypeError(`${name} must be one canonical timestamp`);
  }
  return value;
}

function requireExactKeys(
  name: string,
  value: unknown,
  expected: readonly string[],
): Readonly<Record<string, unknown>> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${name} must be one closed object`);
  }
  const observed = Object.keys(value).sort();
  const required = [...expected].sort();
  if (observed.length !== required.length || observed.some((key, index) => key !== required[index])) {
    throw new TypeError(`${name} has an invalid field set`);
  }
  return value as Readonly<Record<string, unknown>>;
}

function canonicalJson(value: JsonValue | Record<string, unknown>): Buffer {
  const encoded = canonicalize(value);
  if (encoded === undefined) {
    throw new TypeError("value is outside the RFC 8785 JSON domain");
  }
  return Buffer.from(encoded, "utf8");
}

function sha256Hex(domain: string, ...values: readonly string[]): string {
  const digest = createHash("sha256");
  digest.update(domain, "utf8");
  for (const value of values) {
    const encoded = Buffer.from(value, "utf8");
    digest.update(String(encoded.byteLength), "ascii");
    digest.update("\0", "ascii");
    digest.update(encoded);
  }
  return digest.digest("hex");
}

export function actionPayloadDigest(
  operation: ActionOperation,
  payload: EffectPayload,
): string {
  if (!(operation in OPERATION_CONTRACT)) {
    throw new TypeError("action operation is outside the closed union");
  }
  const canonical = canonicalJson({ mediaType: MEDIA_TYPE, operation, payload });
  return createHash("sha256").update(ACTION_PAYLOAD_DOMAIN).update(canonical).digest("hex");
}

export function privateAudienceDigestForBinding(facts: {
  readonly consumerRef: string;
  readonly destinationRef: string;
  readonly membershipId: string;
  readonly membershipVersion: number;
  readonly organizationId: string;
}): string {
  const document = canonicalJson({
    consumerRef: requireRef("consumer", facts.consumerRef),
    destinationRef: requireRef("private destination", facts.destinationRef),
    membershipId: requireUuid("Membership", facts.membershipId),
    membershipVersion: requirePositiveInteger("Membership version", facts.membershipVersion),
    organizationId: requireUuid("Organization", facts.organizationId),
    profile: PRIVATE_AUDIENCE_PROFILE,
  });
  return createHash("sha256").update(PRIVATE_AUDIENCE_DOMAIN).update(document).digest("hex");
}

function validatePayload(operation: ActionOperation, payload: EffectPayload): EffectPayload {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new TypeError("effect payload must be one closed JSON object");
  }
  const keys = Object.keys(payload).sort();
  if (operation === "finalize_reply") {
    if (
      keys.join("\0") !== "messageRef\0text"
      || typeof payload.messageRef !== "string"
      || typeof payload.text !== "string"
    ) {
      throw new TypeError("finalize_reply payload must contain messageRef and text");
    }
    requireRef("finalize message", payload.messageRef);
  } else if (keys.join("\0") !== "text" || typeof payload.text !== "string") {
    throw new TypeError("private message payload must contain only text");
  }
  const text = payload.text;
  if (typeof text !== "string" || text.length === 0 || text.trim().length === 0) {
    throw new TypeError("effect text must be nonblank");
  }
  canonicalJson(payload);
  return Object.freeze({ ...payload });
}

export interface TrustedPrivateEffectFacts {
  readonly audienceDigest: string;
  readonly authenticatedServiceRef: string;
  readonly authenticationBindingRef: string;
  readonly consumerRef: string;
  readonly deliveryEvidenceRef: string;
  readonly destinationRef: string;
  readonly membershipId: string;
  readonly membershipVersion: number;
  readonly organizationId: string;
  readonly policyEpoch: number;
  readonly purpose: string;
  readonly userId: string;
}

interface TrustedPrivateEffectAuthority extends TrustedPrivateEffectFacts {
  readonly identityDigest: string;
}

const trustedAuthorities = new WeakSet<object>();

export function createTrustedPrivateEffectAuthority(
  facts: TrustedPrivateEffectFacts,
): TrustedPrivateEffectAuthority {
  const validated: TrustedPrivateEffectFacts = {
    audienceDigest: requireSha256("private audience", facts.audienceDigest),
    authenticatedServiceRef: requireRef("authenticated service", facts.authenticatedServiceRef),
    authenticationBindingRef: requireRef("authentication binding", facts.authenticationBindingRef),
    consumerRef: requireRef("consumer", facts.consumerRef),
    deliveryEvidenceRef: requireRef("DeliveryEvidenceRef", facts.deliveryEvidenceRef, 4096),
    destinationRef: requireRef("private destination", facts.destinationRef),
    membershipId: requireUuid("Membership", facts.membershipId),
    membershipVersion: requirePositiveInteger("Membership version", facts.membershipVersion),
    organizationId: requireUuid("Organization", facts.organizationId),
    policyEpoch: requirePositiveInteger("Policy Epoch", facts.policyEpoch),
    purpose: requireRef("purpose", facts.purpose),
    userId: requireUuid("User", facts.userId),
  };
  const authority = Object.freeze({
    ...validated,
    identityDigest: sha256Hex(
      `${ACTION_BINDING_DOMAIN}identity\0`,
      validated.organizationId,
      validated.userId,
      validated.membershipId,
      String(validated.membershipVersion),
      validated.authenticationBindingRef,
    ),
  });
  trustedAuthorities.add(authority);
  return authority;
}

export interface ActiveSourceContext {
  readonly sourceRef: string;
  readonly sourceVersionRef: string;
}

interface EffectIntentOptions {
  readonly approvalTier?: string;
  readonly deliveryAttemptRef: string;
  readonly idempotencyKey: string;
  readonly payload: EffectPayload;
  readonly sourceContext?: ActiveSourceContext;
}

declare const trustedEffectIntentBrand: unique symbol;

export interface TrustedEffectIntent {
  readonly [trustedEffectIntentBrand]: true;
}

interface PrivateEffectIntent extends TrustedEffectIntent {
  readonly approvalTier: string | undefined;
  readonly authority: TrustedPrivateEffectAuthority;
  readonly deliveryAttemptRef: string;
  readonly idempotencyKey: string;
  readonly operation: ActionOperation;
  readonly payload: EffectPayload;
  readonly payloadDigest: string;
  readonly sourceContext: ActiveSourceContext | undefined;
}

const trustedIntents = new WeakSet<object>();

function createIntent(
  authority: TrustedPrivateEffectAuthority,
  operation: ActionOperation,
  options: EffectIntentOptions,
): TrustedEffectIntent {
  if (!trustedAuthorities.has(authority)) {
    throw new TypeError("TrustedEffectIntent requires trusted private authority");
  }
  const deliveryAttemptRef = requireRef("DeliveryAttemptRef", options.deliveryAttemptRef);
  if (!DELIVERY_ATTEMPT_PATTERN.test(deliveryAttemptRef)) {
    throw new TypeError("DeliveryAttemptRef has the wrong shape");
  }
  const idempotencyKey = requireRef("action idempotency key", options.idempotencyKey, 128);
  const payload = validatePayload(operation, options.payload);
  let sourceContext: ActiveSourceContext | undefined;
  if (options.sourceContext !== undefined) {
    sourceContext = Object.freeze({
      sourceRef: requireUuid("source", options.sourceContext.sourceRef),
      sourceVersionRef: requireUuid("source version", options.sourceContext.sourceVersionRef),
    });
  }
  const intent = Object.freeze({
    approvalTier: options.approvalTier,
    authority,
    deliveryAttemptRef,
    idempotencyKey,
    operation,
    payload,
    payloadDigest: actionPayloadDigest(operation, payload),
    sourceContext,
  }) as unknown as PrivateEffectIntent;
  trustedIntents.add(intent);
  return intent;
}

export function createPlaceholderEffectIntent(
  authority: TrustedPrivateEffectAuthority,
  options: EffectIntentOptions,
): TrustedEffectIntent {
  return createIntent(authority, "create_placeholder", options);
}

export function createFinalizeReplyEffectIntent(
  authority: TrustedPrivateEffectAuthority,
  options: EffectIntentOptions,
): TrustedEffectIntent {
  return createIntent(authority, "finalize_reply", options);
}

export function createPrivateFollowupEffectIntent(
  authority: TrustedPrivateEffectAuthority,
  options: EffectIntentOptions,
): TrustedEffectIntent {
  return createIntent(authority, "send_private_followup", options);
}

interface ProfileOptions {
  readonly approvalTier: string;
  readonly authenticatedServiceRef: string;
  readonly consumerRef: string;
  readonly maximumPayloadBytes: number;
  readonly profileRef: string;
  readonly purpose: string;
  readonly retentionPolicyRef: string;
  readonly retentionSeconds: number;
  readonly ticketTtlSeconds: number;
}

const profiles = new WeakMap<object, Readonly<ProfileOptions>>();

export class PrivateActionPrepareProfile {
  constructor(options: ProfileOptions) {
    const validated = Object.freeze({
      approvalTier: requireRef("approval tier", options.approvalTier),
      authenticatedServiceRef: requireRef("profile service", options.authenticatedServiceRef),
      consumerRef: requireRef("profile consumer", options.consumerRef),
      maximumPayloadBytes: requirePositiveInteger("maximum payload bytes", options.maximumPayloadBytes),
      profileRef: requireRef("prepare profile", options.profileRef),
      purpose: requireRef("profile purpose", options.purpose),
      retentionPolicyRef: requireRef("retention policy", options.retentionPolicyRef),
      retentionSeconds: requirePositiveInteger("retention seconds", options.retentionSeconds),
      ticketTtlSeconds: requirePositiveInteger("ticket TTL", options.ticketTtlSeconds),
    });
    if (validated.ticketTtlSeconds > 300) {
      throw new TypeError("ticket TTL exceeds the active safety ceiling");
    }
    if (
      validated.retentionPolicyRef !== "action-digest-audit-retention-v1"
      || validated.retentionSeconds > 31_536_000
    ) {
      throw new TypeError("action retention profile is not active");
    }
    profiles.set(this, validated);
    Object.freeze(this);
  }
}

interface KeyringOptions {
  readonly activeVersion: number;
  readonly keys: ReadonlyMap<number, Uint8Array>;
}

interface KeyringState {
  readonly activeVersion: number;
  readonly keys: ReadonlyMap<number, Buffer>;
}

const keyrings = new WeakMap<object, KeyringState>();

export class ActionTicketKeyring {
  constructor(options: KeyringOptions) {
    const activeVersion = requirePositiveInteger("active signing key version", options.activeVersion);
    const copied = new Map<number, Buffer>();
    for (const [version, key] of options.keys) {
      requirePositiveInteger("signing key version", version);
      if (!(key instanceof Uint8Array) || key.byteLength < 32) {
        throw new TypeError("action signing keys must contain at least 256 bits");
      }
      copied.set(version, Buffer.from(key));
    }
    if (!copied.has(activeVersion)) {
      throw new TypeError("active action signing key is missing");
    }
    keyrings.set(this, { activeVersion, keys: copied });
    Object.freeze(this);
  }
}

function keyringState(keyring: ActionTicketKeyring): KeyringState {
  const state = keyrings.get(keyring);
  if (state === undefined) {
    throw new TypeError("action keyring has invalid provenance");
  }
  return state;
}

abstract class RedactedTicket {
  readonly #serialized: string;

  protected constructor(serialized: string) {
    this.#serialized = serialized;
    trustedTickets.add(this);
    Object.freeze(this);
  }

  serialize(): string {
    return this.#serialized;
  }

  toString(): string {
    return "<ActionTicket redacted>";
  }

  toJSON(): never {
    throw new TypeError("ActionTicket is not JSON serializable");
  }
}

export class CreatePlaceholderActionTicket extends RedactedTicket {
  declare private readonly createPlaceholderTicketBrand: true;

  constructor(serialized: string, authority: object) {
    if (authority !== ticketConstructionAuthority) {
      throw new TypeError("ActionTicket is issuer-constructed only");
    }
    super(serialized);
  }
}

export class FinalizeReplyActionTicket extends RedactedTicket {
  declare private readonly finalizeReplyTicketBrand: true;

  constructor(serialized: string, authority: object) {
    if (authority !== ticketConstructionAuthority) {
      throw new TypeError("ActionTicket is issuer-constructed only");
    }
    super(serialized);
  }
}

export class SendPrivateFollowupActionTicket extends RedactedTicket {
  declare private readonly sendPrivateFollowupTicketBrand: true;

  constructor(serialized: string, authority: object) {
    if (authority !== ticketConstructionAuthority) {
      throw new TypeError("ActionTicket is issuer-constructed only");
    }
    super(serialized);
  }
}

export type ActionTicket =
  | CreatePlaceholderActionTicket
  | FinalizeReplyActionTicket
  | SendPrivateFollowupActionTicket;

interface TicketClaims {
  readonly approvalTier: string;
  readonly audience: string;
  readonly audienceDigest: string;
  readonly deliveryAttemptRef: string;
  readonly destinationDigest: string;
  readonly expiresAt: string;
  readonly idempotencyDigest: string;
  readonly identityDigest: string;
  readonly issuedAt: string;
  readonly operation: ActionOperation;
  readonly organizationId: string;
  readonly payloadDigest: string;
  readonly policyEpoch: number;
  readonly profileRef: string;
  readonly purposeDigest: string;
  readonly serviceDigest: string;
  readonly signingKeyVersion: number;
  readonly sourceContextDigest: string | null;
  readonly ticketRef: string;
  readonly type: string;
}

function base64url(value: Buffer): string {
  return value.toString("base64url");
}

function mintTicket(claims: TicketClaims, keyring: ActionTicketKeyring): ActionTicket {
  const state = keyringState(keyring);
  if (claims.signingKeyVersion !== state.activeVersion) {
    throw new TypeError("prepare authority returned an inactive signing version");
  }
  const header = canonicalJson({
    alg: "HS256",
    domain: ACTION_TICKET_DOMAIN.toString("utf8").replace(/\0$/u, ""),
    keyVersion: claims.signingKeyVersion,
    typ: claims.type,
  });
  const body = canonicalJson(claims as unknown as Record<string, unknown>);
  const unsigned = `${base64url(header)}.${base64url(body)}`;
  const key = state.keys.get(claims.signingKeyVersion);
  if (key === undefined) {
    throw new TypeError("action signing key is unavailable");
  }
  const signature = createHmac("sha256", key)
    .update(ACTION_TICKET_DOMAIN)
    .update(unsigned, "ascii")
    .digest();
  const serialized = `${unsigned}.${base64url(signature)}`;
  switch (claims.operation) {
    case "create_placeholder":
      return new CreatePlaceholderActionTicket(serialized, ticketConstructionAuthority);
    case "finalize_reply":
      return new FinalizeReplyActionTicket(serialized, ticketConstructionAuthority);
    case "send_private_followup":
      return new SendPrivateFollowupActionTicket(serialized, ticketConstructionAuthority);
  }
}

function parseTicket(ticket: ActionTicket, keyring: ActionTicketKeyring): TicketClaims {
  if (!(ticket instanceof RedactedTicket) || !trustedTickets.has(ticket)) {
    throw new TypeError("ActionTicket has invalid nominal type");
  }
  const segments = ticket.serialize().split(".");
  if (segments.length !== 3) {
    throw new TypeError("ActionTicket is malformed");
  }
  const [encodedHeader, encodedClaims, encodedSignature] = segments;
  if (encodedHeader === undefined || encodedClaims === undefined || encodedSignature === undefined) {
    throw new TypeError("ActionTicket is malformed");
  }
  if (
    !/^[A-Za-z0-9_-]+$/u.test(encodedHeader)
    || !/^[A-Za-z0-9_-]+$/u.test(encodedClaims)
    || !/^[A-Za-z0-9_-]{43}$/u.test(encodedSignature)
  ) {
    throw new TypeError("ActionTicket encoding is not canonical base64url");
  }
  const parsedHeader: unknown = JSON.parse(
    Buffer.from(encodedHeader, "base64url").toString("utf8"),
  );
  const header = requireExactKeys(
    "ActionTicket header",
    parsedHeader,
    ACTION_TICKET_HEADER_KEYS,
  );
  const version = requirePositiveInteger("ticket key version", header.keyVersion);
  if (
    header.alg !== "HS256"
    || header.domain !== ACTION_TICKET_DOMAIN.toString("utf8").replace(/\0$/u, "")
    || base64url(canonicalJson(header)) !== encodedHeader
  ) {
    throw new TypeError("ActionTicket header binding is invalid");
  }
  const state = keyringState(keyring);
  const key = state.keys.get(version);
  if (key === undefined) {
    throw new TypeError("ActionTicket key is unavailable");
  }
  const expected = createHmac("sha256", key)
    .update(ACTION_TICKET_DOMAIN)
    .update(`${encodedHeader}.${encodedClaims}`, "ascii")
    .digest();
  const observed = Buffer.from(encodedSignature, "base64url");
  if (
    base64url(observed) !== encodedSignature
    || observed.byteLength !== expected.byteLength
    || !timingSafeEqual(observed, expected)
  ) {
    throw new TypeError("ActionTicket is not available");
  }
  const parsedClaims: unknown = JSON.parse(
    Buffer.from(encodedClaims, "base64url").toString("utf8"),
  );
  const rawClaims = requireExactKeys(
    "ActionTicket claims",
    parsedClaims,
    ACTION_TICKET_CLAIM_KEYS,
  );
  if (base64url(canonicalJson(rawClaims)) !== encodedClaims) {
    throw new TypeError("ActionTicket claims are not canonical");
  }
  const operation = rawClaims.operation;
  if (typeof operation !== "string" || !(operation in OPERATION_CONTRACT)) {
    throw new TypeError("ActionTicket operation binding is invalid");
  }
  const operationContract = OPERATION_CONTRACT[operation as ActionOperation];
  const issuedAt = requireCanonicalDateString("ticket issued-at", rawClaims.issuedAt);
  const expiresAt = requireCanonicalDateString("ticket expiry", rawClaims.expiresAt);
  const sourceContextDigest = rawClaims.sourceContextDigest === null
    ? null
    : requireSha256("ticket source-context digest", rawClaims.sourceContextDigest);
  const claims: TicketClaims = Object.freeze({
    approvalTier: requireRef("ticket approval tier", rawClaims.approvalTier),
    audience: requireRef("ticket audience", rawClaims.audience),
    audienceDigest: requireSha256("ticket audience digest", rawClaims.audienceDigest),
    deliveryAttemptRef: requireRef("ticket DeliveryAttemptRef", rawClaims.deliveryAttemptRef),
    destinationDigest: requireSha256(
      "ticket destination digest",
      rawClaims.destinationDigest,
    ),
    expiresAt,
    idempotencyDigest: requireSha256(
      "ticket idempotency digest",
      rawClaims.idempotencyDigest,
    ),
    identityDigest: requireSha256("ticket identity digest", rawClaims.identityDigest),
    issuedAt,
    operation: operation as ActionOperation,
    organizationId: requireUuid("ticket Organization", rawClaims.organizationId),
    payloadDigest: requireSha256("ticket payload digest", rawClaims.payloadDigest),
    policyEpoch: requirePositiveInteger("ticket Policy Epoch", rawClaims.policyEpoch),
    profileRef: requireRef("ticket profile", rawClaims.profileRef),
    purposeDigest: requireSha256("ticket purpose digest", rawClaims.purposeDigest),
    serviceDigest: requireSha256("ticket service digest", rawClaims.serviceDigest),
    signingKeyVersion: requirePositiveInteger(
      "ticket signing key version",
      rawClaims.signingKeyVersion,
    ),
    sourceContextDigest,
    ticketRef: requireRef("ActionTicket ref", rawClaims.ticketRef),
    type: requireRef("ticket type", rawClaims.type),
  });
  if (
    claims.type !== operationContract.type
    || header.typ !== operationContract.type
    || claims.audience !== operationContract.audience
    || claims.signingKeyVersion !== version
    || !DELIVERY_ATTEMPT_PATTERN.test(claims.deliveryAttemptRef)
    || !TICKET_REF_PATTERN.test(claims.ticketRef)
    || new Date(claims.expiresAt) <= new Date(claims.issuedAt)
  ) {
    throw new TypeError("ActionTicket operation binding is invalid");
  }
  const nominallyMatches = (
    (claims.operation === "create_placeholder" && ticket instanceof CreatePlaceholderActionTicket)
    || (claims.operation === "finalize_reply" && ticket instanceof FinalizeReplyActionTicket)
    || (
      claims.operation === "send_private_followup"
      && ticket instanceof SendPrivateFollowupActionTicket
    )
  );
  if (!nominallyMatches) {
    throw new TypeError("ActionTicket nominal operation binding is invalid");
  }
  return claims;
}

export function inspectPreparedActionTicket(
  ticket: ActionTicket,
  keyring: ActionTicketKeyring,
): TicketClaims {
  return parseTicket(ticket, keyring);
}

interface DatabaseQueryResult {
  readonly rows: readonly Record<string, unknown>[];
}

export interface ActionPrepareDatabase {
  query(config: { readonly text: string; readonly values: readonly unknown[] }): Promise<DatabaseQueryResult>;
  connect?(): Promise<ActionDatabaseSession>;
}

interface ActionDatabaseSession {
  query(config: { readonly text: string; readonly values: readonly unknown[] }): Promise<DatabaseQueryResult>;
  release(discard?: boolean): void;
}

interface PrepareRequest {
  readonly approvalTier: string;
  readonly audienceDigest: string;
  readonly authenticationBindingRef: string;
  readonly authenticatedServiceRef: string;
  readonly consumerRef: string;
  readonly deliveryAttemptRef: string;
  readonly deliveryEvidenceRef: string;
  readonly destinationRef: string;
  readonly idempotencyDigest: string;
  readonly identityDigest: string;
  readonly membershipId: string;
  readonly membershipVersion: number;
  readonly operation: ActionOperation;
  readonly organizationId: string;
  readonly payloadDigest: string;
  readonly policyEpoch: number;
  readonly profileRef: string;
  readonly proposedTicketRef: string;
  readonly purpose: string;
  readonly retentionPolicyRef: string;
  readonly retentionSeconds: number;
  readonly signingKeyVersion: number;
  readonly sourceContext: ActiveSourceContext | undefined;
  readonly ticketTtlSeconds: number;
  readonly userId: string;
}

interface PrepareAuthorityResult {
  readonly deliveryAttemptRef?: string;
  readonly expiresAt?: Date;
  readonly idempotent?: boolean;
  readonly issuedAt?: Date;
  readonly kind: "audience_changed" | "generic_denied" | "prepared" | "retryable_unavailable";
  readonly ticketRef?: string;
}

const PREPARE_SQL = `
SELECT * FROM context_action_prepare_private_effect(
  $1::uuid, digest($2::text, 'sha256'), digest($3::text, 'sha256'),
  digest($4::text, 'sha256'), $5::uuid, $6::uuid, $7::bigint,
  digest($8::text, 'sha256'), digest($9::text, 'sha256'),
  digest($10::text, 'sha256'), $11::bytea, $12::bytea, $13::bigint,
  $14::text, $15::text, $16::bytea, $17::bytea, $18::bytea,
  $19::text, $20::text, $21::text, $22::text, $23::integer,
  $24::bigint, $25::uuid, $26::uuid, $27::text, $28::bigint
)`;

class PostgresActionPrepareAuthority {
  readonly #database: ActionPrepareDatabase;

  constructor(database: ActionPrepareDatabase) {
    if (typeof database?.query !== "function") {
      throw new TypeError("ActionPlane prepare requires a database query authority");
    }
    this.#database = database;
    Object.freeze(this);
  }

  async prepare(request: PrepareRequest): Promise<PrepareAuthorityResult> {
    try {
      const row = (await this.#database.query({
        text: PREPARE_SQL,
        values: [
          request.organizationId,
          request.authenticatedServiceRef,
          request.deliveryEvidenceRef,
          request.authenticationBindingRef,
          request.userId,
          request.membershipId,
          request.membershipVersion,
          request.destinationRef,
          request.consumerRef,
          request.purpose,
          Buffer.from(request.audienceDigest, "hex"),
          Buffer.from(request.identityDigest, "hex"),
          request.policyEpoch,
          request.operation,
          OPERATION_CONTRACT[request.operation].audience,
          Buffer.from(request.payloadDigest, "hex"),
          Buffer.from(request.idempotencyDigest, "hex"),
          Buffer.from(sha256Hex(`${ACTION_BINDING_DOMAIN}approval\0`, request.approvalTier), "hex"),
          request.approvalTier,
          request.deliveryAttemptRef,
          request.proposedTicketRef,
          request.profileRef,
          request.signingKeyVersion,
          request.ticketTtlSeconds,
          request.sourceContext?.sourceRef ?? null,
          request.sourceContext?.sourceVersionRef ?? null,
          request.retentionPolicyRef,
          request.retentionSeconds,
        ],
      })).rows[0];
      if (row === undefined) {
        return { kind: "retryable_unavailable" };
      }
      const kind = row.outcome;
      if (kind === "generic_denied" || kind === "audience_changed") {
        return { kind };
      }
      if (kind !== "prepared") {
        return { kind: "retryable_unavailable" };
      }
      return {
        deliveryAttemptRef: requireRef("persisted DeliveryAttemptRef", row.delivery_attempt_ref),
        expiresAt: requireDate("persisted ticket expiry", row.expires_at),
        idempotent: row.idempotent === true,
        issuedAt: requireDate("persisted ticket issuance", row.issued_at),
        kind,
        ticketRef: requireRef("persisted ActionTicket ref", row.ticket_ref),
      };
    } catch {
      return { kind: "retryable_unavailable" };
    }
  }
}

export interface PreparedAction {
  readonly deliveryAttemptRef: string;
  readonly effectCount: 0;
  readonly idempotent: boolean;
  readonly kind: "prepared";
  readonly operation: ActionOperation;
  readonly ticket: ActionTicket;
}

export interface GenericDenied {
  readonly effectCount: 0;
  readonly kind: "generic_denied";
}

export interface AudienceChanged {
  readonly effectCount: 0;
  readonly kind: "audience_changed";
}

export interface RetryableUnavailable {
  readonly effectCount: 0;
  readonly kind: "retryable_unavailable";
}

export type ActionPreparationOutcome =
  | AudienceChanged
  | GenericDenied
  | PreparedAction
  | RetryableUnavailable;

interface ActionPlaneOptions {
  readonly database: ActionPrepareDatabase;
  readonly keyring: ActionTicketKeyring;
  readonly profile: PrivateActionPrepareProfile;
  readonly providerAttemptRefFactory?: () => string;
  readonly receiptRefFactory?: () => string;
  readonly sender?: DeterministicPrivateSenderTwin;
  readonly ticketRefFactory?: () => string;
}

interface SenderEffect {
  readonly destinationRef: string;
  readonly operation: ActionOperation;
  readonly payload: EffectPayload;
  readonly providerAttemptRef: string;
  readonly ticket: ActionTicket;
}

interface SenderApplied {
  readonly appliedAt: Date;
  readonly kind: "applied";
  readonly providerEffectDigest: string;
}

interface SenderAmbiguous {
  readonly kind: "ambiguous";
}

interface SenderRejected {
  readonly kind: "rejected";
}

type SenderOutcome = SenderAmbiguous | SenderApplied | SenderRejected;

export interface PrivateSender {
  send(effect: SenderEffect): Promise<SenderOutcome>;
}

interface DeterministicPrivateSenderOptions {
  readonly clock?: () => Date;
  readonly gate?: Promise<void>;
  readonly mode: "ambiguous" | "applied" | "rejected";
}

const trustedPrivateSenderTwins = new WeakSet<object>();

export class DeterministicPrivateSenderTwin implements PrivateSender {
  readonly #clock: () => Date;
  readonly #gate: Promise<void> | undefined;
  readonly #mode: "ambiguous" | "applied" | "rejected";
  #callCount = 0;
  #effectCount = 0;

  constructor(options: DeterministicPrivateSenderOptions) {
    if (!(["ambiguous", "applied", "rejected"] as const).includes(options.mode)) {
      throw new TypeError("Sender twin mode is outside the closed union");
    }
    if (
      options.gate !== undefined
      && Object.getPrototypeOf(options.gate) !== Promise.prototype
    ) {
      throw new TypeError("Sender twin gate must be one native Promise");
    }
    this.#clock = options.clock ?? (() => new Date());
    this.#gate = options.gate;
    this.#mode = options.mode;
    trustedPrivateSenderTwins.add(this);
    Object.freeze(this);
  }

  get callCount(): number {
    return this.#callCount;
  }

  get effectCount(): number {
    return this.#effectCount;
  }

  async send(effect: SenderEffect): Promise<SenderOutcome> {
    this.#callCount += 1;
    await this.#gate;
    if (this.#mode === "rejected") {
      return { kind: "rejected" };
    }
    this.#effectCount += 1;
    if (this.#mode === "ambiguous") {
      return { kind: "ambiguous" };
    }
    const appliedAt = requireDate("Sender applied-at", this.#clock());
    const providerEffectDigest = sha256Hex(
      `${ACTION_BINDING_DOMAIN}provider-effect\0`,
      effect.providerAttemptRef,
      effect.operation,
      actionPayloadDigest(effect.operation, effect.payload),
    );
    return { appliedAt, kind: "applied", providerEffectDigest };
  }
}

Object.freeze(DeterministicPrivateSenderTwin.prototype);

export interface ActionReceipt {
  readonly appliedAt: string;
  readonly audienceDigest: string;
  readonly deliveryAttemptRef: string;
  readonly destinationDigest: string;
  readonly idempotencyDigest: string;
  readonly operation: ActionOperation;
  readonly organizationId: string;
  readonly payloadDigest: string;
  readonly providerAttemptRef: string;
  readonly providerEffectDigest: string;
  readonly receiptRef: string;
  readonly ticketRef: string;
}

export interface AppliedAction {
  readonly effectCount: 1;
  readonly kind: "applied";
  readonly receipt: ActionReceipt;
}

export interface AlreadyAppliedAction {
  readonly effectCount: 0;
  readonly kind: "already_applied";
  readonly receipt: ActionReceipt;
}

export interface RejectedAction {
  readonly effectCount: 0;
  readonly kind: "rejected";
  readonly reasonCategory: "not_available" | "provider_rejected";
}

export interface ReconciliationRequired {
  readonly kind: "reconciliation_required";
  readonly providerAttemptRef: string;
}

export type ActionExecutionOutcome =
  | AlreadyAppliedAction
  | AppliedAction
  | ReconciliationRequired
  | RejectedAction;

export interface ActionReconciliationDecisionOptions {
  readonly appliedAt?: Date;
  readonly disposition: "applied" | "rejected";
  readonly organizationId: string;
  readonly providerAttemptRef: string;
  readonly providerEffectDigest?: string;
  readonly reconciliationRef: string;
}

export interface TrustedActionReconciliation {
  readonly trustedActionReconciliationBrand: unique symbol;
}

interface ActionReconciliationDecision {
  readonly appliedAt: Date | undefined;
  readonly disposition: "applied" | "rejected";
  readonly organizationId: string;
  readonly providerAttemptRef: string;
  readonly providerEffectDigest: string | undefined;
  readonly reconciliationRef: string;
}

const trustedReconciliations = new WeakSet<object>();
const activeProviderAttempts = new Set<string>();

function activeProviderAttemptKey(organizationId: string, providerAttemptRef: string): string {
  return `${organizationId}\0${providerAttemptRef}`;
}

function actionTicketSenderSessionLockKey(
  organizationId: string,
  ticketRef: string,
): string {
  return `action-ticket-sender-session:${organizationId}:${ticketRef}`;
}

export function createTrustedActionReconciliation(
  options: ActionReconciliationDecisionOptions,
): TrustedActionReconciliation {
  const disposition = options.disposition;
  if (disposition !== "applied" && disposition !== "rejected") {
    throw new TypeError("reconciliation disposition is outside the closed union");
  }
  const providerAttemptRef = requireRef(
    "reconciliation provider attempt",
    options.providerAttemptRef,
  );
  if (!PROVIDER_ATTEMPT_PATTERN.test(providerAttemptRef)) {
    throw new TypeError("reconciliation provider attempt is malformed");
  }
  const decision: ActionReconciliationDecision = Object.freeze({
    appliedAt: disposition === "applied"
      ? requireDate("reconciliation applied-at", options.appliedAt)
      : undefined,
    disposition,
    organizationId: requireUuid("reconciliation Organization", options.organizationId),
    providerAttemptRef,
    providerEffectDigest: disposition === "applied"
      ? requireSha256("reconciliation provider effect digest", options.providerEffectDigest)
      : undefined,
    reconciliationRef: requireRef("reconciliation authority", options.reconciliationRef),
  });
  if (
    disposition === "rejected"
    && (options.appliedAt !== undefined || options.providerEffectDigest !== undefined)
  ) {
    throw new TypeError("rejected reconciliation cannot claim an applied effect");
  }
  trustedReconciliations.add(decision);
  return decision as unknown as TrustedActionReconciliation;
}

const BEGIN_EFFECT_SQL = `
SELECT * FROM context_action_begin_private_effect(
  $1::uuid, $2::text, $3::text, $4::text, $5::text,
  $6::bytea, $7::bytea, $8::bytea, $9::bigint, $10::integer,
  $11::text, $12::bytea, $13::bytea, $14::bytea, $15::bytea,
  $16::bytea, $17::bytea, $18::timestamptz, $19::timestamptz,
  $20::bytea, $21::text, $22::bigint, $23::bytea
)`;

const COMPLETE_EFFECT_SQL = `
SELECT * FROM context_action_complete_private_effect(
  $1::uuid, $2::text, $3::text, $4::text, $5::bytea,
  $6::timestamptz, $7::text, $8::text, $9::bigint, $10::bytea
)`;

const RECONCILE_EFFECT_SQL = `
SELECT * FROM context_action_reconcile_private_effect(
  $1::uuid, $2::text, $3::text, $4::bytea, $5::timestamptz,
  $6::text, $7::bytea, $8::text, $9::bigint
)`;

const RELEASE_SENDER_SESSION_LOCK_SQL = `
SELECT pg_advisory_unlock(hashtextextended($1::text, 0)) AS unlocked`;

function databaseDigest(name: string, value: unknown): string {
  if (Buffer.isBuffer(value) && value.byteLength === 32) {
    return value.toString("hex");
  }
  return requireSha256(name, value);
}

type ReceiptExpectation = Partial<Pick<
  TicketClaims,
  | "audienceDigest"
  | "deliveryAttemptRef"
  | "destinationDigest"
  | "idempotencyDigest"
  | "operation"
  | "organizationId"
  | "payloadDigest"
  | "ticketRef"
>>;

function actionReceiptFromRow(
  row: Readonly<Record<string, unknown>>,
  expected: ReceiptExpectation,
): ActionReceipt {
  const operation = row.operation;
  if (typeof operation !== "string" || !(operation in OPERATION_CONTRACT)) {
    throw new TypeError("stored ActionReceipt has an invalid operation");
  }
  const receipt: ActionReceipt = Object.freeze({
    appliedAt: requireDate("receipt applied-at", row.applied_at).toISOString(),
    audienceDigest: databaseDigest("receipt audience digest", row.audience_digest),
    deliveryAttemptRef: requireRef("receipt DeliveryAttemptRef", row.delivery_attempt_ref),
    destinationDigest: databaseDigest("receipt destination digest", row.destination_digest),
    idempotencyDigest: databaseDigest("receipt idempotency digest", row.idempotency_digest),
    operation: operation as ActionOperation,
    organizationId: requireUuid("receipt Organization", row.organization_id),
    payloadDigest: databaseDigest("receipt payload digest", row.payload_digest),
    providerAttemptRef: requireRef("receipt provider attempt", row.provider_attempt_ref),
    providerEffectDigest: databaseDigest(
      "receipt provider effect digest",
      row.provider_effect_digest,
    ),
    receiptRef: requireRef("ActionReceipt ref", row.receipt_ref),
    ticketRef: requireRef("receipt ActionTicket ref", row.ticket_ref),
  });
  if (
    !PROVIDER_ATTEMPT_PATTERN.test(receipt.providerAttemptRef)
    || !RECEIPT_REF_PATTERN.test(receipt.receiptRef)
    || !TICKET_REF_PATTERN.test(receipt.ticketRef)
    || !DELIVERY_ATTEMPT_PATTERN.test(receipt.deliveryAttemptRef)
    || (expected.organizationId !== undefined
      && receipt.organizationId !== expected.organizationId)
    || (expected.deliveryAttemptRef !== undefined
      && receipt.deliveryAttemptRef !== expected.deliveryAttemptRef)
    || (expected.ticketRef !== undefined && receipt.ticketRef !== expected.ticketRef)
    || (expected.operation !== undefined && receipt.operation !== expected.operation)
    || (expected.destinationDigest !== undefined
      && receipt.destinationDigest !== expected.destinationDigest)
    || (expected.audienceDigest !== undefined
      && receipt.audienceDigest !== expected.audienceDigest)
    || (expected.payloadDigest !== undefined
      && receipt.payloadDigest !== expected.payloadDigest)
    || (expected.idempotencyDigest !== undefined
      && receipt.idempotencyDigest !== expected.idempotencyDigest)
  ) {
    throw new TypeError("stored ActionReceipt does not match the exact ticket");
  }
  return receipt;
}

export class ActionPlane {
  readonly #authority: PostgresActionPrepareAuthority;
  readonly #database: ActionPrepareDatabase;
  readonly #keyring: ActionTicketKeyring;
  readonly #profile: Readonly<ProfileOptions>;
  readonly #providerAttemptRefFactory: () => string;
  readonly #receiptRefFactory: () => string;
  readonly #sender: DeterministicPrivateSenderTwin | undefined;
  readonly #ticketRefFactory: () => string;

  constructor(options: ActionPlaneOptions) {
    keyringState(options.keyring);
    const profile = profiles.get(options.profile);
    if (profile === undefined) {
      throw new TypeError("ActionPlane requires a trusted prepare profile");
    }
    this.#database = options.database;
    this.#authority = new PostgresActionPrepareAuthority(options.database);
    this.#keyring = options.keyring;
    this.#profile = profile;
    this.#providerAttemptRefFactory = options.providerAttemptRefFactory
      ?? (() => `pat_${randomBytes(16).toString("hex")}`);
    this.#receiptRefFactory = options.receiptRefFactory
      ?? (() => `acr_${randomBytes(16).toString("hex")}`);
    if (
      options.sender !== undefined
      && (
        !trustedPrivateSenderTwins.has(options.sender)
        || Object.getPrototypeOf(options.sender) !== DeterministicPrivateSenderTwin.prototype
      )
    ) {
      throw new TypeError("Issue #68 permits only the deterministic private Sender twin");
    }
    this.#sender = options.sender as DeterministicPrivateSenderTwin | undefined;
    this.#ticketRefFactory = options.ticketRefFactory ?? (() => `act_${randomBytes(16).toString("hex")}`);
    Object.freeze(this);
  }

  async prepare(intent: TrustedEffectIntent): Promise<ActionPreparationOutcome> {
    if (typeof intent !== "object" || intent === null || !trustedIntents.has(intent)) {
      throw new TypeError("ActionPlane.prepare requires TrustedEffectIntent");
    }
    const trusted = intent as PrivateEffectIntent;
    const authority = trusted.authority;
    const profile = this.#profile;
    const approvalTier = trusted.approvalTier ?? profile.approvalTier;
    const canonicalPayloadBytes = canonicalJson({
      mediaType: MEDIA_TYPE,
      operation: trusted.operation,
      payload: trusted.payload,
    });
    if (
      !trustedAuthorities.has(authority)
      || authority.authenticatedServiceRef !== profile.authenticatedServiceRef
      || authority.consumerRef !== profile.consumerRef
      || authority.purpose !== profile.purpose
      || approvalTier !== profile.approvalTier
      || canonicalPayloadBytes.byteLength > profile.maximumPayloadBytes
      || actionPayloadDigest(trusted.operation, trusted.payload) !== trusted.payloadDigest
    ) {
      return { effectCount: 0, kind: "generic_denied" };
    }
    const proposedTicketRef = this.#ticketRefFactory();
    if (!TICKET_REF_PATTERN.test(proposedTicketRef)) {
      return { effectCount: 0, kind: "retryable_unavailable" };
    }
    const signing = keyringState(this.#keyring);
    const idempotencyDigest = sha256Hex(
      `${ACTION_BINDING_DOMAIN}idempotency\0`,
      authority.organizationId,
      trusted.operation,
      trusted.idempotencyKey,
    );
    const result = await this.#authority.prepare({
      approvalTier,
      audienceDigest: authority.audienceDigest,
      authenticationBindingRef: authority.authenticationBindingRef,
      authenticatedServiceRef: authority.authenticatedServiceRef,
      consumerRef: authority.consumerRef,
      deliveryAttemptRef: trusted.deliveryAttemptRef,
      deliveryEvidenceRef: authority.deliveryEvidenceRef,
      destinationRef: authority.destinationRef,
      idempotencyDigest,
      identityDigest: authority.identityDigest,
      membershipId: authority.membershipId,
      membershipVersion: authority.membershipVersion,
      operation: trusted.operation,
      organizationId: authority.organizationId,
      payloadDigest: trusted.payloadDigest,
      policyEpoch: authority.policyEpoch,
      profileRef: profile.profileRef,
      proposedTicketRef,
      purpose: authority.purpose,
      retentionPolicyRef: profile.retentionPolicyRef,
      retentionSeconds: profile.retentionSeconds,
      signingKeyVersion: signing.activeVersion,
      sourceContext: trusted.sourceContext,
      ticketTtlSeconds: profile.ticketTtlSeconds,
      userId: authority.userId,
    });
    if (result.kind !== "prepared") {
      return { effectCount: 0, kind: result.kind };
    }
    if (
      result.deliveryAttemptRef === undefined
      || !DELIVERY_ATTEMPT_PATTERN.test(result.deliveryAttemptRef)
      || result.ticketRef === undefined
      || !TICKET_REF_PATTERN.test(result.ticketRef)
      || result.issuedAt === undefined
      || result.expiresAt === undefined
      || result.expiresAt <= result.issuedAt
    ) {
      return { effectCount: 0, kind: "retryable_unavailable" };
    }
    const operationContract = OPERATION_CONTRACT[trusted.operation];
    const claims: TicketClaims = {
      approvalTier,
      audience: operationContract.audience,
      audienceDigest: authority.audienceDigest,
      deliveryAttemptRef: result.deliveryAttemptRef,
      destinationDigest: createHash("sha256").update(authority.destinationRef, "utf8").digest("hex"),
      expiresAt: result.expiresAt.toISOString(),
      idempotencyDigest,
      identityDigest: authority.identityDigest,
      issuedAt: result.issuedAt.toISOString(),
      operation: trusted.operation,
      organizationId: authority.organizationId,
      payloadDigest: trusted.payloadDigest,
      policyEpoch: authority.policyEpoch,
      profileRef: profile.profileRef,
      purposeDigest: createHash("sha256").update(authority.purpose, "utf8").digest("hex"),
      serviceDigest: createHash("sha256")
        .update(authority.authenticatedServiceRef, "utf8")
        .digest("hex"),
      signingKeyVersion: signing.activeVersion,
      sourceContextDigest: trusted.sourceContext === undefined
        ? null
        : sha256Hex(
            `${ACTION_BINDING_DOMAIN}source\0`,
            trusted.sourceContext.sourceRef,
            trusted.sourceContext.sourceVersionRef,
          ),
      ticketRef: result.ticketRef,
      type: operationContract.type,
    };
    return {
      deliveryAttemptRef: result.deliveryAttemptRef,
      effectCount: 0,
      idempotent: result.idempotent === true,
      kind: "prepared",
      operation: trusted.operation,
      ticket: mintTicket(claims, this.#keyring),
    };
  }

  async perform(payload: EffectPayload, ticket: ActionTicket): Promise<ActionExecutionOutcome> {
    let claims: TicketClaims;
    let validatedPayload: EffectPayload;
    try {
      claims = parseTicket(ticket, this.#keyring);
      validatedPayload = validatePayload(claims.operation, payload);
      if (
        actionPayloadDigest(claims.operation, validatedPayload) !== claims.payloadDigest
        || claims.profileRef !== this.#profile.profileRef
        || claims.approvalTier !== this.#profile.approvalTier
      ) {
        return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
      }
    } catch {
      return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
    }
    if (this.#sender === undefined) {
      return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
    }
    const proposedProviderAttemptRef = this.#providerAttemptRefFactory();
    const completionCapability = randomBytes(32);
    if (!PROVIDER_ATTEMPT_PATTERN.test(proposedProviderAttemptRef)) {
      return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
    }
    const connect = this.#database.connect;
    if (typeof connect !== "function") {
      return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
    }
    const senderSessionLockKey = actionTicketSenderSessionLockKey(
      claims.organizationId,
      claims.ticketRef,
    );
    let session: ActionDatabaseSession | undefined;
    try {
      session = await connect.call(this.#database);
    } catch {
      try {
        session?.release(true);
      } catch {
        // A failed session is discarded best-effort before failing closed.
      }
      return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
    }
    let discardSession = false;
    let senderSessionLockHeld = false;
    try {
      let beginRow: Readonly<Record<string, unknown>> | undefined;
      try {
        beginRow = (await session.query({
          text: BEGIN_EFFECT_SQL,
          values: [
          claims.organizationId,
          claims.ticketRef,
          claims.deliveryAttemptRef,
          claims.operation,
          claims.audience,
          Buffer.from(claims.payloadDigest, "hex"),
          Buffer.from(claims.idempotencyDigest, "hex"),
          Buffer.from(claims.approvalTier.length === 0
            ? ""
            : sha256Hex(`${ACTION_BINDING_DOMAIN}approval\0`, claims.approvalTier), "hex"),
          claims.policyEpoch,
          claims.signingKeyVersion,
          claims.profileRef,
          Buffer.from(claims.serviceDigest, "hex"),
          Buffer.from(claims.destinationDigest, "hex"),
          Buffer.from(claims.audienceDigest, "hex"),
          Buffer.from(claims.identityDigest, "hex"),
          Buffer.from(claims.purposeDigest, "hex"),
          claims.sourceContextDigest === null
            ? null
            : Buffer.from(claims.sourceContextDigest, "hex"),
          new Date(claims.issuedAt),
          new Date(claims.expiresAt),
          createHash("sha256").update(ACTION_TICKET_DOMAIN).update(ticket.serialize()).digest(),
          proposedProviderAttemptRef,
          this.#profile.retentionSeconds,
          createHash("sha256").update(completionCapability).digest(),
          ],
        })).rows[0];
      } catch {
        discardSession = true;
        return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
      }
      if (beginRow === undefined) {
        return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
      }
      if (beginRow.outcome === "already_applied") {
        try {
          return {
            effectCount: 0,
            kind: "already_applied",
            receipt: actionReceiptFromRow(beginRow, claims),
          };
        } catch {
          return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
        }
      }
      if (beginRow.outcome === "reconciliation_required") {
        try {
          const providerAttemptRef = requireRef(
            "provider attempt",
            beginRow.provider_attempt_ref,
          );
          if (!PROVIDER_ATTEMPT_PATTERN.test(providerAttemptRef)) {
            throw new TypeError("provider attempt is malformed");
          }
          return { kind: "reconciliation_required", providerAttemptRef };
        } catch {
          return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
        }
      }
      if (beginRow.outcome === "provider_rejected") {
        return { effectCount: 0, kind: "rejected", reasonCategory: "provider_rejected" };
      }
      if (beginRow.outcome === "rejected") {
        return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
      }
      if (beginRow.outcome !== "sender_required") {
        return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
      }
      senderSessionLockHeld = true;
      let providerAttemptRef: string;
      let destinationRef: string;
      try {
        providerAttemptRef = requireRef("provider attempt", beginRow.provider_attempt_ref);
        destinationRef = requireRef("private destination", beginRow.destination_ref);
        if (
          !PROVIDER_ATTEMPT_PATTERN.test(providerAttemptRef)
          || providerAttemptRef !== proposedProviderAttemptRef
        ) {
          throw new TypeError("provider attempt is malformed");
        }
      } catch {
        return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
      }
      const activeAttemptKey = activeProviderAttemptKey(
        claims.organizationId,
        providerAttemptRef,
      );
      if (activeProviderAttempts.has(activeAttemptKey)) {
        return { kind: "reconciliation_required", providerAttemptRef };
      }
      activeProviderAttempts.add(activeAttemptKey);
      try {
        let senderOutcome: SenderOutcome;
        try {
          senderOutcome = await this.#sender.send({
            destinationRef,
            operation: claims.operation,
            payload: validatedPayload,
            providerAttemptRef,
            ticket,
          });
        } catch {
          senderOutcome = { kind: "ambiguous" };
        }
        const proposedReceiptRef = this.#receiptRefFactory();
        if (!RECEIPT_REF_PATTERN.test(proposedReceiptRef)) {
          return { kind: "reconciliation_required", providerAttemptRef };
        }
        let completeRow: Readonly<Record<string, unknown>> | undefined;
        try {
          completeRow = (await session.query({
            text: COMPLETE_EFFECT_SQL,
            values: [
              claims.organizationId,
              claims.ticketRef,
              providerAttemptRef,
              senderOutcome.kind,
              senderOutcome.kind === "applied"
                ? Buffer.from(senderOutcome.providerEffectDigest, "hex")
                : null,
              senderOutcome.kind === "applied" ? senderOutcome.appliedAt : null,
              proposedReceiptRef,
              this.#profile.retentionPolicyRef,
              this.#profile.retentionSeconds,
              completionCapability,
            ],
          })).rows[0];
        } catch {
          return { kind: "reconciliation_required", providerAttemptRef };
        }
        if (completeRow?.outcome === "applied") {
          try {
            return {
              effectCount: 1,
              kind: "applied",
              receipt: actionReceiptFromRow(completeRow, claims),
            };
          } catch {
            return { kind: "reconciliation_required", providerAttemptRef };
          }
        }
        if (completeRow?.outcome === "rejected") {
          return { effectCount: 0, kind: "rejected", reasonCategory: "provider_rejected" };
        }
        return { kind: "reconciliation_required", providerAttemptRef };
      } finally {
        activeProviderAttempts.delete(activeAttemptKey);
      }
    } finally {
      if (senderSessionLockHeld && !discardSession) {
        try {
          const unlockRow = (await session.query({
            text: RELEASE_SENDER_SESSION_LOCK_SQL,
            values: [senderSessionLockKey],
          })).rows[0];
          discardSession = unlockRow?.unlocked !== true;
        } catch {
          discardSession = true;
        }
      }
      try {
        session.release(discardSession);
      } catch {
        // A failed release cannot change the already-classified effect outcome.
      }
    }
  }

  async reconcile(
    decision: TrustedActionReconciliation,
  ): Promise<AlreadyAppliedAction | RejectedAction> {
    if (
      typeof decision !== "object"
      || decision === null
      || !trustedReconciliations.has(decision)
    ) {
      throw new TypeError("ActionPlane.reconcile requires trusted reconciliation authority");
    }
    const trusted = decision as unknown as ActionReconciliationDecision;
    if (
      activeProviderAttempts.has(
        activeProviderAttemptKey(trusted.organizationId, trusted.providerAttemptRef),
      )
    ) {
      return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
    }
    const decisionDigest = sha256Hex(
      `${ACTION_BINDING_DOMAIN}reconciliation\0`,
      trusted.organizationId,
      trusted.providerAttemptRef,
      trusted.disposition,
      trusted.reconciliationRef,
      trusted.providerEffectDigest ?? "",
      trusted.appliedAt?.toISOString() ?? "",
    );
    const proposedReceiptRef = this.#receiptRefFactory();
    if (!RECEIPT_REF_PATTERN.test(proposedReceiptRef)) {
      return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
    }
    let row: Readonly<Record<string, unknown>> | undefined;
    try {
      row = (await this.#database.query({
        text: RECONCILE_EFFECT_SQL,
        values: [
          trusted.organizationId,
          trusted.providerAttemptRef,
          trusted.disposition,
          trusted.providerEffectDigest === undefined
            ? null
            : Buffer.from(trusted.providerEffectDigest, "hex"),
          trusted.appliedAt ?? null,
          proposedReceiptRef,
          Buffer.from(decisionDigest, "hex"),
          this.#profile.retentionPolicyRef,
          this.#profile.retentionSeconds,
        ],
      })).rows[0];
    } catch {
      return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
    }
    if (row?.outcome === "already_applied") {
      try {
        return {
          effectCount: 0,
          kind: "already_applied",
          receipt: actionReceiptFromRow(row, {
            organizationId: trusted.organizationId,
          }),
        };
      } catch {
        return { effectCount: 0, kind: "rejected", reasonCategory: "not_available" };
      }
    }
    return {
      effectCount: 0,
      kind: "rejected",
      reasonCategory: row?.outcome === "provider_rejected"
        ? "provider_rejected"
        : "not_available",
    };
  }

}

export const actionDigestProfiles = Object.freeze({
  payload: ACTION_PAYLOAD_DIGEST_PROFILE,
  ticket: ACTION_TICKET_DIGEST_PROFILE,
});
