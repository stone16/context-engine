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
const ticketConstructionAuthority = Object.freeze({});

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
  if (!(ticket instanceof RedactedTicket)) {
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
  const header = JSON.parse(Buffer.from(encodedHeader, "base64url").toString("utf8")) as {
    keyVersion?: unknown;
    typ?: unknown;
  };
  const version = requirePositiveInteger("ticket key version", header.keyVersion);
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
  if (observed.byteLength !== expected.byteLength || !timingSafeEqual(observed, expected)) {
    throw new TypeError("ActionTicket is not available");
  }
  const claims = JSON.parse(Buffer.from(encodedClaims, "base64url").toString("utf8")) as TicketClaims;
  const operationContract = OPERATION_CONTRACT[claims.operation];
  if (
    operationContract === undefined
    || claims.type !== operationContract.type
    || header.typ !== operationContract.type
    || claims.audience !== operationContract.audience
  ) {
    throw new TypeError("ActionTicket operation binding is invalid");
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
  readonly ticketRefFactory?: () => string;
}

export class ActionPlane {
  readonly #authority: PostgresActionPrepareAuthority;
  readonly #keyring: ActionTicketKeyring;
  readonly #profile: Readonly<ProfileOptions>;
  readonly #ticketRefFactory: () => string;

  constructor(options: ActionPlaneOptions) {
    keyringState(options.keyring);
    const profile = profiles.get(options.profile);
    if (profile === undefined) {
      throw new TypeError("ActionPlane requires a trusted prepare profile");
    }
    this.#authority = new PostgresActionPrepareAuthority(options.database);
    this.#keyring = options.keyring;
    this.#profile = profile;
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
      destinationDigest: sha256Hex(`${ACTION_BINDING_DOMAIN}destination\0`, authority.destinationRef),
      expiresAt: result.expiresAt.toISOString(),
      idempotencyDigest,
      identityDigest: authority.identityDigest,
      issuedAt: result.issuedAt.toISOString(),
      operation: trusted.operation,
      organizationId: authority.organizationId,
      payloadDigest: trusted.payloadDigest,
      policyEpoch: authority.policyEpoch,
      profileRef: profile.profileRef,
      purposeDigest: sha256Hex(`${ACTION_BINDING_DOMAIN}purpose\0`, authority.purpose),
      serviceDigest: sha256Hex(`${ACTION_BINDING_DOMAIN}service\0`, authority.authenticatedServiceRef),
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
}

export const actionDigestProfiles = Object.freeze({
  payload: ACTION_PAYLOAD_DIGEST_PROFILE,
  ticket: ACTION_TICKET_DIGEST_PROFILE,
});
