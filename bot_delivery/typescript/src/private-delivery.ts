import { createHash, randomBytes } from "node:crypto";

import {
  ActionPlane,
  type ActionReceipt,
  type ActionExecutionOutcome,
  type ActionOperation,
  type ActionPreparationOutcome,
} from "@context-engine/action-plane";
import {
  ContextEngineResolveClient,
  type ContextPackageWire,
  type ModelEgressGrantWire,
} from "@context-engine/resolve-sdk";
import pg from "pg";

import {
  ModelGenerationBoundary,
  PrivateModelGatewayProfile,
  prepareAuthorizedModelInput,
} from "./index.js";

const { Pool } = pg;

const SHA256_HEX = /^[0-9a-f]{64}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DELIVERY_ATTEMPT_PATTERN = /^dla_[0-9a-f]{32}$/;
const DELIVERY_AUDIT_PATTERN = /^bda_[0-9a-f]{32}$/;
const DELIVERY_EVIDENCE_PATTERN = /^der_[0-9a-f]{64}$/;
const CITATION_OPEN_PATTERN = /^cor_[0-9a-f]{64}$/;
const DELIVERY_AUDIT_PROFILE = "private-delivery-audit-v1";
const DELIVERY_AUDIT_RETENTION_SECONDS = 2_592_000;

function requireExactKeys(
  name: string,
  value: unknown,
  expected: readonly string[],
): Readonly<Record<string, unknown>> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${name} must be one closed object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new TypeError(`${name} must be one closed object`);
  }
  const ownKeys = Reflect.ownKeys(value);
  if (ownKeys.some((key) => typeof key !== "string")) {
    throw new TypeError(`${name} has an invalid field set`);
  }
  const observed = (ownKeys as string[]).sort();
  const required = [...expected].sort();
  if (observed.length !== required.length || observed.some((key, index) => key !== required[index])) {
    throw new TypeError(`${name} has an invalid field set`);
  }
  return value as Readonly<Record<string, unknown>>;
}

function requireRef(name: string, value: unknown, maximum = 256): string {
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

export interface PrivateIdentityBinding {
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
  readonly purpose: "citation.open" | "context.answer";
  readonly requestId: string;
  readonly userId: string;
}

export interface PrivateQuestionTurnFixture extends PrivateIdentityBinding {
  readonly eventVerificationRef: string;
  readonly finalEffect: "finalize_reply" | "send_private_followup";
  readonly question: string;
  readonly turnRef: string;
}

export interface PrivateCitationOpenFixture extends PrivateIdentityBinding {
  readonly citationOpenRef: string;
  readonly eventVerificationRef: string;
  readonly openRef: string;
}

export interface VerifyQuestionTurnInput {
  readonly eventVerificationRef: string;
  readonly question: string;
  readonly turnRef: string;
}

export interface VerifyCitationOpenInput {
  readonly citationOpenRef: string;
  readonly eventVerificationRef: string;
  readonly openRef: string;
}

export interface IdentityNotBound {
  readonly kind: "identity_not_bound";
}

interface QuestionTurnState {
  readonly binding: PrivateQuestionTurnFixture;
  readonly question: string;
}

interface CitationOpenState {
  readonly binding: PrivateCitationOpenFixture;
  readonly citationOpenRef: string;
}

const verifiedQuestionTurns = new WeakMap<VerifiedQuestionTurn, QuestionTurnState>();
const verifiedCitationOpens = new WeakMap<VerifiedCitationOpen, CitationOpenState>();
const questionTurnConstruction = Object.freeze({});
const citationOpenConstruction = Object.freeze({});
let mintVerifiedQuestionTurn: (state: QuestionTurnState) => VerifiedQuestionTurn;
let mintVerifiedCitationOpen: (state: CitationOpenState) => VerifiedCitationOpen;

export class VerifiedQuestionTurn {
  private constructor(state: QuestionTurnState, authority?: object) {
    if (authority !== questionTurnConstruction) {
      throw new TypeError("VerifiedQuestionTurn is identity-adapter constructed only");
    }
    verifiedQuestionTurns.set(this, state);
    Object.freeze(this);
  }

  static {
    mintVerifiedQuestionTurn = (state: QuestionTurnState): VerifiedQuestionTurn =>
      new VerifiedQuestionTurn(state, questionTurnConstruction);
  }

  toJSON(): Readonly<Record<string, never>> {
    return Object.freeze({});
  }

  toString(): string {
    return "<VerifiedQuestionTurn redacted>";
  }
}

export class VerifiedCitationOpen {
  private constructor(state: CitationOpenState, authority?: object) {
    if (authority !== citationOpenConstruction) {
      throw new TypeError("VerifiedCitationOpen is identity-adapter constructed only");
    }
    verifiedCitationOpens.set(this, state);
    Object.freeze(this);
  }

  static {
    mintVerifiedCitationOpen = (state: CitationOpenState): VerifiedCitationOpen =>
      new VerifiedCitationOpen(state, citationOpenConstruction);
  }

  toJSON(): Readonly<Record<string, never>> {
    return Object.freeze({});
  }

  toString(): string {
    return "<VerifiedCitationOpen redacted>";
  }
}

Object.freeze(VerifiedQuestionTurn.prototype);
Object.freeze(VerifiedCitationOpen.prototype);

function validateBinding<T extends PrivateIdentityBinding>(
  binding: T,
  expectedPurpose: PrivateIdentityBinding["purpose"],
): T {
  requireSha256("private audience", binding.audienceDigest);
  requireRef("authenticated service", binding.authenticatedServiceRef);
  requireRef("authentication binding", binding.authenticationBindingRef);
  requireRef("consumer", binding.consumerRef);
  if (!DELIVERY_EVIDENCE_PATTERN.test(binding.deliveryEvidenceRef)) {
    throw new TypeError("DeliveryEvidenceRef is invalid");
  }
  requireRef("private destination", binding.destinationRef);
  requireUuid("Membership", binding.membershipId);
  requirePositiveInteger("Membership version", binding.membershipVersion);
  requireUuid("Organization", binding.organizationId);
  requirePositiveInteger("Policy Epoch", binding.policyEpoch);
  if (binding.purpose !== expectedPurpose) {
    throw new TypeError("identity binding purpose is invalid");
  }
  requireRef("resolve request", binding.requestId);
  requireUuid("User", binding.userId);
  return binding;
}

interface PrivateFeishuIdentityTwinOptions {
  readonly citationOpens: readonly PrivateCitationOpenFixture[];
  readonly questionTurns: readonly PrivateQuestionTurnFixture[];
}

interface PrivateFeishuIdentityTwinState {
  readonly citationOpens: ReadonlyMap<string, PrivateCitationOpenFixture>;
  readonly questionTurns: ReadonlyMap<string, PrivateQuestionTurnFixture>;
  readonly verifiedCitationOpens: Map<string, VerifiedCitationOpen>;
  readonly verifiedQuestionTurns: Map<string, VerifiedQuestionTurn>;
}

const identityTwinStates = new WeakMap<PrivateFeishuIdentityTwin, PrivateFeishuIdentityTwinState>();

export class PrivateFeishuIdentityTwin {
  constructor(options: PrivateFeishuIdentityTwinOptions) {
    const record = requireExactKeys(
      "private Feishu identity twin",
      options,
      ["citationOpens", "questionTurns"],
    );
    if (!Array.isArray(record.questionTurns) || !Array.isArray(record.citationOpens)) {
      throw new TypeError("identity twin fixtures must be arrays");
    }
    const questionTurns = new Map<string, PrivateQuestionTurnFixture>();
    for (const item of record.questionTurns as readonly PrivateQuestionTurnFixture[]) {
      const binding = validateBinding(item, "context.answer");
      requireRef("question turn", binding.turnRef);
      requireRef("question event verification", binding.eventVerificationRef);
      if (typeof binding.question !== "string" || binding.question.trim().length === 0 || Buffer.byteLength(binding.question) > 1_024) {
        throw new TypeError("question turn must bind bounded nonblank text");
      }
      if (binding.finalEffect !== "finalize_reply" && binding.finalEffect !== "send_private_followup") {
        throw new TypeError("question final effect is outside the closed union");
      }
      if (questionTurns.has(binding.turnRef)) throw new TypeError("question turn refs must be unique");
      questionTurns.set(binding.turnRef, Object.freeze({ ...binding }));
    }
    const citationOpens = new Map<string, PrivateCitationOpenFixture>();
    for (const item of record.citationOpens as readonly PrivateCitationOpenFixture[]) {
      const binding = validateBinding(item, "citation.open");
      requireRef("citation open event", binding.openRef);
      requireRef("citation event verification", binding.eventVerificationRef);
      if (!CITATION_OPEN_PATTERN.test(binding.citationOpenRef)) {
        throw new TypeError("citation event must bind one CitationOpenRef");
      }
      if (citationOpens.has(binding.openRef)) throw new TypeError("citation open refs must be unique");
      citationOpens.set(binding.openRef, Object.freeze({ ...binding }));
    }
    identityTwinStates.set(this, {
      citationOpens,
      questionTurns,
      verifiedCitationOpens: new Map(),
      verifiedQuestionTurns: new Map(),
    });
    Object.freeze(this);
  }

  verifyQuestionTurn(
    input: VerifyQuestionTurnInput,
  ): IdentityNotBound | VerifiedQuestionTurn {
    let record: Readonly<Record<string, unknown>>;
    try {
      record = requireExactKeys(
        "question turn event",
        input,
        ["eventVerificationRef", "question", "turnRef"],
      );
    } catch {
      return { kind: "identity_not_bound" };
    }
    const turnRef = typeof record.turnRef === "string" ? record.turnRef : "";
    const binding = identityTwinStates.get(this)?.questionTurns.get(turnRef);
    if (
      binding === undefined
      || record.eventVerificationRef !== binding.eventVerificationRef
      || record.question !== binding.question
    ) {
      return { kind: "identity_not_bound" };
    }
    const twinState = identityTwinStates.get(this) as PrivateFeishuIdentityTwinState;
    const existing = twinState.verifiedQuestionTurns.get(turnRef);
    if (existing !== undefined) return existing;
    const verified = mintVerifiedQuestionTurn({ binding, question: binding.question });
    twinState.verifiedQuestionTurns.set(turnRef, verified);
    return verified;
  }

  verifyCitationOpen(
    input: VerifyCitationOpenInput,
  ): IdentityNotBound | VerifiedCitationOpen {
    let record: Readonly<Record<string, unknown>>;
    try {
      record = requireExactKeys(
        "citation open event",
        input,
        ["citationOpenRef", "eventVerificationRef", "openRef"],
      );
    } catch {
      return { kind: "identity_not_bound" };
    }
    const openRef = typeof record.openRef === "string" ? record.openRef : "";
    const binding = identityTwinStates.get(this)?.citationOpens.get(openRef);
    if (
      binding === undefined
      || record.eventVerificationRef !== binding.eventVerificationRef
      || record.citationOpenRef !== binding.citationOpenRef
    ) {
      return { kind: "identity_not_bound" };
    }
    const twinState = identityTwinStates.get(this) as PrivateFeishuIdentityTwinState;
    const existing = twinState.verifiedCitationOpens.get(openRef);
    if (existing !== undefined) return existing;
    const verified = mintVerifiedCitationOpen({
      binding,
      citationOpenRef: binding.citationOpenRef,
    });
    twinState.verifiedCitationOpens.set(openRef, verified);
    return verified;
  }
}

Object.freeze(PrivateFeishuIdentityTwin.prototype);

interface DeliveryAuditDatabaseResult {
  readonly rows: readonly Readonly<Record<string, unknown>>[];
}

interface DeliveryAuditDatabase {
  query(config: {
    readonly text: string;
    readonly values: readonly unknown[];
  }): Promise<DeliveryAuditDatabaseResult>;
}

const RECORD_DELIVERY_AUDIT_SQL = `
SELECT context_action_record_private_delivery_outcome(
  $1::uuid, $2::text, $3::text, $4::bytea, $5::text, $6::text,
  $7::text, $8::bigint, $9::text
) AS recorded`;

export type DeliveryFinalStatus = "finalized" | "private_followup";

interface DeliveryAuditRecord {
  readonly auditRef: string;
  readonly deliveryAttemptRef: string;
  readonly finalReceiptRef: string;
  readonly finalStatus: DeliveryFinalStatus;
  readonly packageDigest: string;
  readonly placeholderReceiptRef: string;
}

interface InternalPrivateDeliveryAuditBoundaryOptions {
  readonly close: () => Promise<void>;
  readonly database: DeliveryAuditDatabase;
  readonly organizationId: string;
}

const auditBoundaryConstruction = Object.freeze({});
let mintPrivateDeliveryAuditBoundary: (
  options: InternalPrivateDeliveryAuditBoundaryOptions,
) => PrivateDeliveryAuditBoundary;

export class PrivateDeliveryAuditBoundary {
  readonly #closeDatabase: () => Promise<void>;
  readonly #database: DeliveryAuditDatabase;
  readonly #organizationId: string;
  #closed = false;

  private constructor(options: InternalPrivateDeliveryAuditBoundaryOptions, authority?: object) {
    if (authority !== auditBoundaryConstruction) {
      throw new TypeError("delivery audit boundaries are composition-root constructed only");
    }
    this.#closeDatabase = options.close;
    this.#database = options.database;
    this.#organizationId = requireUuid("delivery audit Organization", options.organizationId);
    Object.freeze(this);
  }

  static {
    mintPrivateDeliveryAuditBoundary = (
      options: InternalPrivateDeliveryAuditBoundaryOptions,
    ): PrivateDeliveryAuditBoundary => new PrivateDeliveryAuditBoundary(
      options,
      auditBoundaryConstruction,
    );
  }

  async record(record: DeliveryAuditRecord): Promise<boolean> {
    if (this.#closed) return false;
    try {
      const row = (await this.#database.query({
        text: RECORD_DELIVERY_AUDIT_SQL,
        values: [
          this.#organizationId,
          record.auditRef,
          record.deliveryAttemptRef,
          Buffer.from(record.packageDigest, "hex"),
          record.placeholderReceiptRef,
          record.finalReceiptRef,
          record.finalStatus,
          DELIVERY_AUDIT_RETENTION_SECONDS,
          DELIVERY_AUDIT_PROFILE,
        ],
      })).rows[0];
      return row?.recorded === true;
    } catch {
      return false;
    }
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    await this.#closeDatabase();
  }
}

Object.freeze(PrivateDeliveryAuditBoundary.prototype);

function requirePostgresUrl(value: unknown): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 4_096 || value.trim() !== value) {
    throw new TypeError("delivery audit database URL is invalid");
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new TypeError("delivery audit database URL is invalid");
  }
  if ((parsed.protocol !== "postgresql:" && parsed.protocol !== "postgres:") || parsed.username !== "context_engine_action") {
    throw new TypeError("delivery audit requires the dedicated action database role");
  }
  return value;
}

export function createPrivateDeliveryAuditBoundary(options: {
  readonly databaseUrl: string;
  readonly organizationId: string;
}): PrivateDeliveryAuditBoundary {
  const record = requireExactKeys(
    "private delivery audit boundary",
    options,
    ["databaseUrl", "organizationId"],
  );
  const pool = new Pool({
    application_name: "context-engine-private-delivery-audit",
    connectionString: requirePostgresUrl(record.databaseUrl),
    connectionTimeoutMillis: 5_000,
    max: 2,
    statement_timeout: 5_000,
  });
  return mintPrivateDeliveryAuditBoundary({
    close: async () => pool.end(),
    database: pool,
    organizationId: requireUuid("delivery audit Organization", record.organizationId),
  });
}

export interface DeliveryReceipt {
  readonly deliveryAttemptRef: string;
  readonly finalStatus: DeliveryFinalStatus;
  readonly kind: "delivered";
  readonly operationReceiptRefs: {
    readonly final: string;
    readonly placeholder: string;
  };
  readonly packageDigest: string;
  readonly restrictedAuditRef: string;
}

export interface DeliveryNotAvailable {
  readonly kind: "delivery_not_available";
}

export interface DeliveryReconciliationRequired {
  readonly deliveryAttemptRef: string;
  readonly kind: "delivery_reconciliation_required";
  readonly providerAttemptRef?: string;
}

export type DeliveryOutcome =
  | DeliveryNotAvailable
  | DeliveryReceipt
  | DeliveryReconciliationRequired;

export interface CitationOpened {
  readonly egressGrant: ModelEgressGrantWire;
  readonly kind: "opened";
  readonly package: ContextPackageWire;
}

export interface CitationNotAvailable {
  readonly kind: "citation_not_available";
}

export type CitationOpenOutcome = CitationNotAvailable | CitationOpened;

interface BotDeliveryOptions {
  readonly actionPlane: ActionPlane;
  readonly auditBoundary: PrivateDeliveryAuditBoundary;
  readonly client: ContextEngineResolveClient;
  readonly clock?: () => Date;
  readonly deliveryAttemptRefFactory?: () => string;
  readonly identityTwin: PrivateFeishuIdentityTwin;
  readonly modelBoundary: ModelGenerationBoundary;
  readonly modelProfile: PrivateModelGatewayProfile;
  readonly restrictedAuditRefFactory?: () => string;
}

function actionReceipt(outcome: PrivateBotEffectOutcome): ActionReceipt | undefined {
  return outcome.kind === "applied" || outcome.kind === "already_applied"
    ? outcome.receipt
    : undefined;
}

type PrivateBotEffectOutcome = ActionExecutionOutcome | ActionPreparationOutcome;

function effectIdempotencyKey(turnRef: string, operation: ActionOperation): string {
  return `bot_${createHash("sha256")
    .update("context-engine.private-bot-effect.v1\0", "utf8")
    .update(operation, "utf8")
    .update("\0", "utf8")
    .update(turnRef, "utf8")
    .digest("hex")}`;
}

function reconciliationOutcome(
  outcome: PrivateBotEffectOutcome,
  deliveryAttemptRef: string,
): DeliveryNotAvailable | DeliveryReconciliationRequired {
  return outcome.kind === "reconciliation_required"
    ? {
        deliveryAttemptRef,
        kind: "delivery_reconciliation_required",
        providerAttemptRef: outcome.providerAttemptRef,
      }
    : { kind: "delivery_not_available" };
}

function exactCurrentPackage(
  value: ContextPackageWire,
  binding: PrivateIdentityBinding,
  now: Date,
): boolean {
  return (
    value.audienceDigest === binding.audienceDigest
    && value.policyEpoch === binding.policyEpoch
    && value.purpose === binding.purpose
    && value.coverage.status === "sufficient"
    && value.blocks.length > 0
    && value.evidence.length > 0
    && typeof value.packageDigest === "string"
    && SHA256_HEX.test(value.packageDigest)
    && new Date(value.asOf).getTime() <= now.getTime()
    && now.getTime() < new Date(value.expiresAt).getTime()
  );
}

export class BotDelivery {
  readonly #actionPlane: ActionPlane;
  readonly #auditBoundary: PrivateDeliveryAuditBoundary;
  readonly #client: ContextEngineResolveClient;
  readonly #clock: () => Date;
  readonly #deliveryAttemptRefFactory: () => string;
  readonly #identityTwin: PrivateFeishuIdentityTwin;
  readonly #modelBoundary: ModelGenerationBoundary;
  readonly #modelProfile: PrivateModelGatewayProfile;
  readonly #restrictedAuditRefFactory: () => string;
  readonly #attemptRefs = new WeakMap<VerifiedQuestionTurn, string>();
  readonly #inFlightAnswers = new WeakMap<VerifiedQuestionTurn, Promise<DeliveryOutcome>>();
  readonly #successfulReceipts = new WeakMap<VerifiedQuestionTurn, DeliveryReceipt>();
  readonly #terminalReconciliations = new WeakMap<
    VerifiedQuestionTurn,
    DeliveryReconciliationRequired
  >();

  constructor(options: BotDeliveryOptions) {
    if (!(options.actionPlane instanceof ActionPlane)) {
      throw new TypeError("BotDelivery requires the co-resident ActionPlane");
    }
    if (!(options.auditBoundary instanceof PrivateDeliveryAuditBoundary)) {
      throw new TypeError("BotDelivery requires its restricted delivery audit boundary");
    }
    if (
      !(options.client instanceof ContextEngineResolveClient)
      || Object.getPrototypeOf(options.client) !== ContextEngineResolveClient.prototype
    ) {
      throw new TypeError("BotDelivery requires the installed generated SDK client");
    }
    if (!(options.identityTwin instanceof PrivateFeishuIdentityTwin)) {
      throw new TypeError("BotDelivery requires the trusted private identity twin");
    }
    if (!(options.modelBoundary instanceof ModelGenerationBoundary)) {
      throw new TypeError("BotDelivery requires the controlled model boundary");
    }
    if (!(options.modelProfile instanceof PrivateModelGatewayProfile)) {
      throw new TypeError("BotDelivery requires the active model profile");
    }
    this.#actionPlane = options.actionPlane;
    this.#auditBoundary = options.auditBoundary;
    this.#client = options.client;
    this.#clock = options.clock ?? (() => new Date());
    this.#deliveryAttemptRefFactory = options.deliveryAttemptRefFactory
      ?? (() => `dla_${randomBytes(16).toString("hex")}`);
    this.#identityTwin = options.identityTwin;
    this.#modelBoundary = options.modelBoundary;
    this.#modelProfile = options.modelProfile;
    this.#restrictedAuditRefFactory = options.restrictedAuditRefFactory
      ?? (() => `bda_${randomBytes(16).toString("hex")}`);
    Object.freeze(this);
  }

  async #performEffect(
    binding: PrivateQuestionTurnFixture,
    operation: ActionOperation,
    deliveryAttemptRef: string,
    payload: Readonly<Record<string, string>>,
  ): Promise<PrivateBotEffectOutcome> {
    try {
      const prepared = await this.#actionPlane.preparePrivateDeliveryEffect({
        deliveryAttemptRef,
        deliveryEvidenceRef: binding.deliveryEvidenceRef,
        destinationRef: binding.destinationRef,
        idempotencyKey: effectIdempotencyKey(binding.turnRef, operation),
        operation,
        payload,
        requestId: binding.requestId,
      });
      if (prepared.kind !== "prepared") return prepared;
      return await this.#actionPlane.perform(payload, prepared.ticket);
    } catch {
      return { effectCount: 0, kind: "generic_denied" };
    }
  }

  async answer(turn: VerifiedQuestionTurn): Promise<DeliveryOutcome> {
    const state = verifiedQuestionTurns.get(turn);
    const twinState = identityTwinStates.get(this.#identityTwin);
    if (
      state === undefined
      || twinState?.questionTurns.get(state.binding.turnRef) !== state.binding
    ) {
      return { kind: "delivery_not_available" };
    }
    const successfulReceipt = this.#successfulReceipts.get(turn);
    if (successfulReceipt !== undefined) return successfulReceipt;
    const terminalReconciliation = this.#terminalReconciliations.get(turn);
    if (terminalReconciliation !== undefined) return terminalReconciliation;
    const inFlight = this.#inFlightAnswers.get(turn);
    if (inFlight !== undefined) return inFlight;
    const answer = this.#answerVerifiedTurn(turn, state);
    this.#inFlightAnswers.set(turn, answer);
    try {
      return await answer;
    } finally {
      if (this.#inFlightAnswers.get(turn) === answer) {
        this.#inFlightAnswers.delete(turn);
      }
    }
  }

  async #answerVerifiedTurn(
    turn: VerifiedQuestionTurn,
    state: QuestionTurnState,
  ): Promise<DeliveryOutcome> {
    const deliveryAttemptRef = this.#attemptRefs.get(turn)
      ?? this.#deliveryAttemptRefFactory();
    if (!DELIVERY_ATTEMPT_PATTERN.test(deliveryAttemptRef)) {
      return { kind: "delivery_not_available" };
    }
    this.#attemptRefs.set(turn, deliveryAttemptRef);
    const placeholder = await this.#performEffect(
      state.binding,
      "create_placeholder",
      deliveryAttemptRef,
      { text: "Working…" },
    );
    const placeholderReceipt = actionReceipt(placeholder);
    if (placeholderReceipt === undefined) {
      const outcome = reconciliationOutcome(placeholder, deliveryAttemptRef);
      if (outcome.kind === "delivery_reconciliation_required") {
        this.#terminalReconciliations.set(turn, outcome);
      }
      return outcome;
    }

    let resolved;
    try {
      resolved = await this.#client.resolve({
        deliveryEvidenceRef: state.binding.deliveryEvidenceRef,
        request: { kind: "acquire", need: { query: state.question } },
        requestId: state.binding.requestId,
      });
    } catch {
      return { kind: "delivery_not_available" };
    }
    const now = this.#clock();
    if (
      resolved.kind !== "resolved"
      || resolved.egressGrant?.kind !== "model"
      || !exactCurrentPackage(resolved.package, state.binding, now)
    ) {
      return { kind: "delivery_not_available" };
    }

    let generated;
    try {
      const input = prepareAuthorizedModelInput({
        envelope: {
          instructions: "Answer only from the supplied Package.",
          question: state.question,
        },
        grant: resolved.egressGrant,
        now,
        package: resolved.package,
        profile: this.#modelProfile,
      });
      generated = await this.#modelBoundary.generate(input, resolved.egressGrant);
    } catch {
      return { kind: "delivery_not_available" };
    }
    if (generated.kind !== "generated") {
      return { kind: "delivery_not_available" };
    }

    const finalEffect = state.binding.finalEffect === "finalize_reply"
      ? await this.#performEffect(
          state.binding,
          "finalize_reply",
          deliveryAttemptRef,
          {
            messageRef: placeholderReceipt.providerAttemptRef,
            text: generated.answer.text,
          },
        )
      : await this.#performEffect(
          state.binding,
          "send_private_followup",
          deliveryAttemptRef,
          { text: generated.answer.text },
        );
    const finalReceipt = actionReceipt(finalEffect);
    if (finalReceipt === undefined) {
      const outcome = reconciliationOutcome(finalEffect, deliveryAttemptRef);
      if (outcome.kind === "delivery_reconciliation_required") {
        this.#terminalReconciliations.set(turn, outcome);
      }
      return outcome;
    }
    const finalStatus: DeliveryFinalStatus = state.binding.finalEffect === "finalize_reply"
      ? "finalized"
      : "private_followup";
    const restrictedAuditRef = this.#restrictedAuditRefFactory();
    if (!DELIVERY_AUDIT_PATTERN.test(restrictedAuditRef)) {
      const outcome = Object.freeze({
        deliveryAttemptRef,
        kind: "delivery_reconciliation_required" as const,
        providerAttemptRef: finalReceipt.providerAttemptRef,
      });
      this.#terminalReconciliations.set(turn, outcome);
      return outcome;
    }
    const audited = await this.#auditBoundary.record({
      auditRef: restrictedAuditRef,
      deliveryAttemptRef,
      finalReceiptRef: finalReceipt.receiptRef,
      finalStatus,
      packageDigest: resolved.package.packageDigest,
      placeholderReceiptRef: placeholderReceipt.receiptRef,
    });
    if (!audited) {
      const outcome = Object.freeze({
        deliveryAttemptRef,
        kind: "delivery_reconciliation_required" as const,
        providerAttemptRef: finalReceipt.providerAttemptRef,
      });
      this.#terminalReconciliations.set(turn, outcome);
      return outcome;
    }
    const receipt = Object.freeze({
      deliveryAttemptRef,
      finalStatus,
      kind: "delivered",
      operationReceiptRefs: Object.freeze({
        final: finalReceipt.receiptRef,
        placeholder: placeholderReceipt.receiptRef,
      }),
      packageDigest: resolved.package.packageDigest,
      restrictedAuditRef,
    });
    this.#successfulReceipts.set(turn, receipt);
    return receipt;
  }

  async openCitation(open: VerifiedCitationOpen): Promise<CitationOpenOutcome> {
    const state = verifiedCitationOpens.get(open);
    const twinState = identityTwinStates.get(this.#identityTwin);
    if (
      state === undefined
      || twinState?.citationOpens.get(state.binding.openRef) !== state.binding
    ) {
      return { kind: "citation_not_available" };
    }
    let resolved;
    try {
      resolved = await this.#client.resolve({
        deliveryEvidenceRef: state.binding.deliveryEvidenceRef,
        request: {
          citationOpenRef: state.citationOpenRef,
          kind: "open_citation",
        },
        requestId: state.binding.requestId,
      });
    } catch {
      return { kind: "citation_not_available" };
    }
    if (
      resolved.kind !== "resolved"
      || resolved.egressGrant?.kind !== "model"
      || !exactCurrentPackage(resolved.package, state.binding, this.#clock())
    ) {
      return { kind: "citation_not_available" };
    }
    return Object.freeze({
      egressGrant: resolved.egressGrant,
      kind: "opened",
      package: resolved.package,
    });
  }

  async close(): Promise<void> {
    await Promise.all([
      this.#modelBoundary.close(),
      this.#auditBoundary.close(),
    ]);
  }
}

Object.freeze(BotDelivery.prototype);
