import { createHash, timingSafeEqual } from "node:crypto";

import type {
  ContextPackageWire,
  ModelEgressGrantWire,
} from "@context-engine/resolve-sdk";
import pg from "pg";

import { canonicalJson, contextPackageDocumentDigest } from "./canonical-json.js";

const { Pool } = pg;

const SHA256_HEX = /^[0-9a-f]{64}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const PACKAGE_REF_PATTERN = /^pkg_[0-9a-f]{32}$/;
const EVIDENCE_REF_PATTERN = /^ev_[0-9a-f]{64}$/;
const BLOCK_REF_PATTERN = /^block_[0-9a-f]{64}$/;
const MODEL_GRANT_PATTERN = /^egrm_[0-9a-f]{64}$/;
const UTC_TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$/;
const MODEL_INPUT_DOMAIN = Buffer.from("context-engine.authorized-model-input.v1\0");
const ANSWER_PAYLOAD_DOMAIN = Buffer.from("context-engine.answer-payload.v1\0");
const QUESTION_DIGEST_DOMAIN = Buffer.from("context-engine.model-question.v1\0");
const EGRESS_GRANT_DIGEST_PROFILE = "egress-grant-locator-sha256-v1";
const MODEL_AUDIT_PROFILE = "model-generation-audit-v1";

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
  for (const key of observed) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor?.enumerable !== true || !("value" in descriptor)) {
      throw new TypeError(`${name} must use enumerable data fields`);
    }
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

function requireSha256(name: string, value: unknown): string {
  if (typeof value !== "string" || !SHA256_HEX.test(value)) {
    throw new TypeError(`${name} must be lowercase SHA-256`);
  }
  return value;
}

function requirePattern(name: string, value: unknown, pattern: RegExp): string {
  if (typeof value !== "string" || !pattern.test(value)) {
    throw new TypeError(`${name} is invalid`);
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

function requireNonnegativeInteger(name: string, value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new TypeError(`${name} must be a nonnegative safe integer`);
  }
  return value as number;
}

function requireCanonicalTimestamp(name: string, value: unknown): bigint {
  if (typeof value !== "string") {
    throw new TypeError(`${name} must be a canonical timestamp`);
  }
  const match = UTC_TIMESTAMP_PATTERN.exec(value);
  if (match === null) {
    throw new TypeError(`${name} must be a canonical timestamp`);
  }
  const parsed = new Date(value);
  const [, year, month, day, hour, minute, second, fraction = ""] = match;
  const millisecond = Number(fraction.padEnd(3, "0").slice(0, 3));
  if (
    Number.isNaN(parsed.getTime())
    || parsed.getUTCFullYear() !== Number(year)
    || parsed.getUTCMonth() + 1 !== Number(month)
    || parsed.getUTCDate() !== Number(day)
    || parsed.getUTCHours() !== Number(hour)
    || parsed.getUTCMinutes() !== Number(minute)
    || parsed.getUTCSeconds() !== Number(second)
    || parsed.getUTCMilliseconds() !== millisecond
  ) {
    throw new TypeError(`${name} must be a canonical timestamp`);
  }
  const subMillisecondMicroseconds = BigInt(fraction.padEnd(6, "0").slice(3));
  return BigInt(parsed.getTime()) * 1_000n + subMillisecondMicroseconds;
}

function requireDate(name: string, value: unknown): Date {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
    throw new TypeError(`${name} must be a valid Date`);
  }
  return value;
}

function safeEqual(left: string, right: string): boolean {
  return Buffer.byteLength(left) === Buffer.byteLength(right)
    && timingSafeEqual(Buffer.from(left), Buffer.from(right));
}

const PROFILE_KEYS = [
  "auditRetentionSeconds",
  "consumerRef",
  "issuerRef",
  "maximumCostMicrounits",
  "maximumElapsedMs",
  "maximumInputBytes",
  "maximumInstructionBytes",
  "maximumOutputBytes",
  "maximumProviderCalls",
  "maximumQuestionBytes",
  "modelRef",
  "profileRef",
  "providerRef",
  "regionRef",
  "retentionPolicyRef",
  "sensitivityPolicyRef",
] as const;

const PRIVATE_MODEL_GATEWAY_PROFILE_V1 = Object.freeze({
  auditRetentionSeconds: 2_592_000,
  consumerRef: "model-gateway-integration",
  issuerRef: "context-runtime-integration",
  maximumCostMicrounits: 10_000,
  maximumElapsedMs: 2_000,
  maximumInputBytes: 32_768,
  maximumInstructionBytes: 512,
  maximumOutputBytes: 2_048,
  maximumProviderCalls: 1,
  maximumQuestionBytes: 1_024,
  modelRef: "deterministic-model-spy",
  profileRef: "file-model-egress-integration-v1",
  providerRef: "deterministic-provider-spy",
  regionRef: "local-test-region",
  retentionPolicyRef: "no-provider-retention-v1",
  sensitivityPolicyRef: "authorized-package-only-v1",
});

interface PrivateModelGatewayProfileOptions {
  readonly auditRetentionSeconds: number;
  readonly consumerRef: string;
  readonly issuerRef: string;
  readonly maximumCostMicrounits: number;
  readonly maximumElapsedMs: number;
  readonly maximumInputBytes: number;
  readonly maximumInstructionBytes: number;
  readonly maximumOutputBytes: number;
  readonly maximumProviderCalls: number;
  readonly maximumQuestionBytes: number;
  readonly modelRef: string;
  readonly profileRef: string;
  readonly providerRef: string;
  readonly regionRef: string;
  readonly retentionPolicyRef: string;
  readonly sensitivityPolicyRef: string;
}

const registeredProfiles = new WeakSet<PrivateModelGatewayProfile>();
const profileConstruction = Object.freeze({});
let mintPrivateModelGatewayProfile: (
  options: PrivateModelGatewayProfileOptions,
) => PrivateModelGatewayProfile;

export class PrivateModelGatewayProfile {
  readonly auditRetentionSeconds: number;
  readonly consumerRef: string;
  readonly issuerRef: string;
  readonly maximumCostMicrounits: number;
  readonly maximumElapsedMs: number;
  readonly maximumInputBytes: number;
  readonly maximumInstructionBytes: number;
  readonly maximumOutputBytes: number;
  readonly maximumProviderCalls: number;
  readonly maximumQuestionBytes: number;
  readonly modelRef: string;
  readonly profileRef: string;
  readonly providerRef: string;
  readonly regionRef: string;
  readonly retentionPolicyRef: string;
  readonly sensitivityPolicyRef: string;

  private constructor(options: PrivateModelGatewayProfileOptions, authority?: object) {
    if (authority !== profileConstruction) {
      throw new TypeError("private model profiles can only be constructed by BotDelivery");
    }
    requireExactKeys("model gateway profile", options, PROFILE_KEYS);
    this.auditRetentionSeconds = requirePositiveInteger(
      "model audit retention seconds",
      options.auditRetentionSeconds,
    );
    this.consumerRef = requireRef("model consumer", options.consumerRef);
    this.issuerRef = requireRef("model issuer", options.issuerRef);
    this.maximumCostMicrounits = requirePositiveInteger(
      "model maximum cost",
      options.maximumCostMicrounits,
    );
    this.maximumElapsedMs = requirePositiveInteger(
      "model maximum elapsed time",
      options.maximumElapsedMs,
    );
    this.maximumInputBytes = requirePositiveInteger(
      "model maximum input bytes",
      options.maximumInputBytes,
    );
    this.maximumInstructionBytes = requirePositiveInteger(
      "model maximum instruction bytes",
      options.maximumInstructionBytes,
    );
    this.maximumOutputBytes = requirePositiveInteger(
      "model maximum output bytes",
      options.maximumOutputBytes,
    );
    this.maximumProviderCalls = requirePositiveInteger(
      "model maximum provider calls",
      options.maximumProviderCalls,
    );
    if (this.maximumProviderCalls !== 1) {
      throw new TypeError("private model profile permits exactly one provider call");
    }
    this.maximumQuestionBytes = requirePositiveInteger(
      "model maximum question bytes",
      options.maximumQuestionBytes,
    );
    this.modelRef = requireRef("model", options.modelRef);
    this.profileRef = requireRef("model profile", options.profileRef);
    this.providerRef = requireRef("model provider", options.providerRef);
    this.regionRef = requireRef("model region", options.regionRef);
    this.retentionPolicyRef = requireRef(
      "model retention policy",
      options.retentionPolicyRef,
    );
    this.sensitivityPolicyRef = requireRef(
      "model sensitivity policy",
      options.sensitivityPolicyRef,
    );
    registeredProfiles.add(this);
    Object.freeze(this);
  }

  static {
    mintPrivateModelGatewayProfile = (
      options: PrivateModelGatewayProfileOptions,
    ): PrivateModelGatewayProfile => new PrivateModelGatewayProfile(
      options,
      profileConstruction,
    );
  }
}

export function privateModelGatewayProfileV1(): PrivateModelGatewayProfile {
  return mintPrivateModelGatewayProfile(PRIVATE_MODEL_GATEWAY_PROFILE_V1);
}

function sameProfile(
  left: PrivateModelGatewayProfile,
  right: PrivateModelGatewayProfile,
): boolean {
  return PROFILE_KEYS.every((key) => left[key] === right[key]);
}

const PACKAGE_KEYS = [
  "asOf",
  "audienceDigest",
  "blocks",
  "budgetUsage",
  "continuation",
  "coverage",
  "decisionRef",
  "evidence",
  "expiresAt",
  "gaps",
  "packageDigest",
  "packageId",
  "packageSchemaRef",
  "policyEpoch",
  "policySnapshotRef",
  "purpose",
  "releaseManifestRef",
  "retentionPolicyRef",
  "runRef",
  "tokenizerRef",
  "ttlSeconds",
] as const;
const BLOCK_KEYS = ["blockId", "evidenceRefs", "text"] as const;
const EVIDENCE_KEYS = [
  "authorizationAsOf",
  "citationOpenRef",
  "decisionRef",
  "evidenceRef",
  "fragmentRef",
  "policyEpoch",
  "policySnapshotRef",
  "projectedFields",
  "purpose",
  "resourceRef",
  "revisionRef",
  "runRef",
  "sourceAclEvidence",
  "sourceRef",
] as const;

interface ValidatedEvidence {
  readonly citationOpenRef: string | null;
  readonly evidenceRef: string;
}

interface ValidatedPackage {
  readonly canonicalPayload: Buffer;
  readonly evidence: readonly ValidatedEvidence[];
  readonly package: ContextPackageWire;
}

function requirePackage(
  value: unknown,
  now: Date,
  profile: PrivateModelGatewayProfile,
): ValidatedPackage {
  const packageRecord = requireExactKeys("ContextPackage", value, PACKAGE_KEYS);
  const packageDigest = requireSha256("Package digest", packageRecord.packageDigest);
  requirePattern("Package reference", packageRecord.packageId, PACKAGE_REF_PATTERN);
  const audienceDigest = requireSha256("Package audience", packageRecord.audienceDigest);
  const policyEpoch = requirePositiveInteger("Package Policy Epoch", packageRecord.policyEpoch);
  const purpose = requireRef("Package purpose", packageRecord.purpose);
  const decisionRef = requireRef("Package decision", packageRecord.decisionRef);
  const runRef = requireRef("Package run", packageRecord.runRef);
  const policySnapshotRef = requireRef("Package policy snapshot", packageRecord.policySnapshotRef);
  requireRef("Package release manifest", packageRecord.releaseManifestRef);
  requireRef("Package retention policy", packageRecord.retentionPolicyRef);
  requireRef("Package tokenizer", packageRecord.tokenizerRef);
  requireRef("Package schema", packageRecord.packageSchemaRef);
  const ttlSeconds = requirePositiveInteger("Package TTL", packageRecord.ttlSeconds);
  const asOf = requireCanonicalTimestamp("Package asOf", packageRecord.asOf);
  const expiresAt = requireCanonicalTimestamp("Package expiry", packageRecord.expiresAt);
  const current = requireDate("model input clock", now);
  const currentMicroseconds = BigInt(current.getTime()) * 1_000n;
  if (
    currentMicroseconds < asOf
    || currentMicroseconds >= expiresAt
    || expiresAt - asOf !== BigInt(ttlSeconds) * 1_000_000n
  ) {
    throw new TypeError("model input requires one current Package");
  }
  if (packageRecord.continuation !== null) {
    throw new TypeError("active private generation Package cannot contain continuation");
  }
  if (!Array.isArray(packageRecord.gaps) || packageRecord.gaps.length !== 0) {
    throw new TypeError("private generation Package gaps must be empty");
  }
  const coverage = requireExactKeys("Package coverage", packageRecord.coverage, ["status"]);
  if (coverage.status !== "sufficient") {
    throw new TypeError("model input requires sufficient Package coverage");
  }
  const budget = requireExactKeys(
    "Package budget usage",
    packageRecord.budgetUsage,
    ["costMicrounits", "elapsedMs", "providerCalls", "tokens"],
  );
  for (const key of ["costMicrounits", "elapsedMs", "providerCalls", "tokens"] as const) {
    requireNonnegativeInteger(`Package ${key}`, budget[key]);
  }
  if (!Array.isArray(packageRecord.evidence) || packageRecord.evidence.length === 0) {
    throw new TypeError("model input requires Package Evidence");
  }
  const evidenceRefs = new Set<string>();
  const evidence: ValidatedEvidence[] = packageRecord.evidence.map((item, index) => {
    const record = requireExactKeys(`Package Evidence ${index}`, item, EVIDENCE_KEYS);
    const evidenceRef = requirePattern("Evidence reference", record.evidenceRef, EVIDENCE_REF_PATTERN);
    if (evidenceRefs.has(evidenceRef)) {
      throw new TypeError("Package Evidence refs must be unique");
    }
    evidenceRefs.add(evidenceRef);
    requireRef("Evidence source", record.sourceRef);
    requireRef("Evidence resource", record.resourceRef);
    requireRef("Evidence revision", record.revisionRef);
    requireRef("Evidence fragment", record.fragmentRef);
    if (!Array.isArray(record.projectedFields) || record.projectedFields.length === 0) {
      throw new TypeError("Evidence projected fields must be a non-empty array");
    }
    for (const field of record.projectedFields) requireRef("Evidence projected field", field);
    if (
      record.runRef !== runRef
      || record.purpose !== purpose
      || record.decisionRef !== decisionRef
      || record.policySnapshotRef !== policySnapshotRef
      || record.policyEpoch !== policyEpoch
    ) {
      throw new TypeError("Package Evidence lineage must match the Package");
    }
    requireCanonicalTimestamp("Evidence authorizationAsOf", record.authorizationAsOf);
    const sourceAcl = record.sourceAclEvidence;
    if (typeof sourceAcl !== "object" || sourceAcl === null || Array.isArray(sourceAcl)) {
      throw new TypeError("Evidence source ACL must be one closed object");
    }
    const sourceAclRecord = sourceAcl as Readonly<Record<string, unknown>>;
    if (sourceAclRecord.kind === "mirrored") {
      requireExactKeys("mirrored source ACL", sourceAcl, [
        "aclAsOf", "freshnessProfileRef", "kind", "projectionRef",
      ]);
      requireCanonicalTimestamp("mirrored source ACL asOf", sourceAclRecord.aclAsOf);
      requireRef("mirrored source ACL freshness profile", sourceAclRecord.freshnessProfileRef);
      requireRef("mirrored source ACL projection", sourceAclRecord.projectionRef);
    } else if (sourceAclRecord.kind === "live") {
      requireExactKeys("live source ACL", sourceAcl, [
        "aclAsOf", "providerDecisionRef", "providerRef", "providerVersionRef", "kind",
      ]);
      requireCanonicalTimestamp("live source ACL asOf", sourceAclRecord.aclAsOf);
      requireRef("live source ACL provider decision", sourceAclRecord.providerDecisionRef);
      requireRef("live source ACL provider", sourceAclRecord.providerRef);
      requireRef("live source ACL provider version", sourceAclRecord.providerVersionRef);
    } else if (sourceAclRecord.kind === "weak") {
      requireExactKeys("weak source ACL", sourceAcl, [
        "aclAsOf", "kind", "reason", "sourceRef",
      ]);
      requireCanonicalTimestamp("weak source ACL asOf", sourceAclRecord.aclAsOf);
      requireRef("weak source ACL reason", sourceAclRecord.reason);
      if (sourceAclRecord.sourceRef !== record.sourceRef) {
        throw new TypeError("weak source ACL source must match Evidence");
      }
    } else {
      throw new TypeError("Evidence source ACL kind is outside the closed union");
    }
    const citationOpenRef = record.citationOpenRef;
    if (
      citationOpenRef !== null
      && (typeof citationOpenRef !== "string" || !/^cor_[0-9a-f]{64}$/.test(citationOpenRef))
    ) {
      throw new TypeError("Evidence CitationOpenRef is invalid");
    }
    return Object.freeze({ citationOpenRef, evidenceRef });
  });
  if (!Array.isArray(packageRecord.blocks) || packageRecord.blocks.length === 0) {
    throw new TypeError("model input requires Package blocks");
  }
  const usedEvidence = new Set<string>();
  const blockRefs = new Set<string>();
  for (const [index, item] of packageRecord.blocks.entries()) {
    const block = requireExactKeys(`Package block ${index}`, item, BLOCK_KEYS);
    const blockRef = requirePattern("Package block reference", block.blockId, BLOCK_REF_PATTERN);
    if (blockRefs.has(blockRef)) {
      throw new TypeError("Package block refs must be unique");
    }
    blockRefs.add(blockRef);
    if (typeof block.text !== "string" || block.text.length === 0) {
      throw new TypeError("Package block text must be non-empty");
    }
    if (
      !Array.isArray(block.evidenceRefs)
      || block.evidenceRefs.length !== 1
      || !evidenceRefs.has(block.evidenceRefs[0] as string)
    ) {
      throw new TypeError("Package block must cite one carried Evidence");
    }
    const blockEvidenceRef = block.evidenceRefs[0] as string;
    if (block.blockId !== `block_${blockEvidenceRef.slice("ev_".length)}`) {
      throw new TypeError("Package block reference must derive from its Evidence");
    }
    usedEvidence.add(blockEvidenceRef);
  }
  if (usedEvidence.size !== evidenceRefs.size) {
    throw new TypeError("Package blocks and Evidence must form an exact closure");
  }
  const digestDocument = { ...packageRecord };
  delete digestDocument.packageDigest;
  const computedDigest = contextPackageDocumentDigest(digestDocument);
  if (!safeEqual(packageDigest, computedDigest)) {
    throw new TypeError("Package digest does not match its complete document");
  }
  const canonicalPayload = canonicalJson(packageRecord);
  if (canonicalPayload.byteLength > profile.maximumInputBytes) {
    throw new TypeError("Package exceeds the model input profile");
  }
  requireSha256("Package audience", audienceDigest);
  return {
    canonicalPayload,
    evidence: Object.freeze(evidence),
    package: value as ContextPackageWire,
  };
}

function requireModelGrant(value: unknown): ModelEgressGrantWire {
  const record = requireExactKeys("model EgressGrant", value, ["kind", "value"]);
  if (record.kind !== "model" || typeof record.value !== "string" || !MODEL_GRANT_PATTERN.test(record.value)) {
    throw new TypeError("model input requires one opaque model EgressGrant");
  }
  return value as ModelEgressGrantWire;
}

interface QuestionEnvelope {
  readonly instructions: string;
  readonly question: string;
}

function requireEnvelope(
  value: unknown,
  profile: PrivateModelGatewayProfile,
): QuestionEnvelope {
  const record = requireExactKeys("question envelope", value, ["instructions", "question"]);
  if (
    typeof record.instructions !== "string"
    || record.instructions.trim().length === 0
    || Buffer.byteLength(record.instructions) > profile.maximumInstructionBytes
    || typeof record.question !== "string"
    || record.question.trim().length === 0
    || Buffer.byteLength(record.question) > profile.maximumQuestionBytes
  ) {
    throw new TypeError("question envelope exceeds its versioned profile");
  }
  return Object.freeze({
    instructions: record.instructions,
    question: record.question,
  });
}

interface AuthorizedModelInputState {
  readonly evidence: readonly ValidatedEvidence[];
  readonly grantDigest: Buffer;
  readonly package: ContextPackageWire;
  readonly payloadDigest: string;
  readonly profile: PrivateModelGatewayProfile;
  readonly providerRequest: ModelProviderRequest;
  readonly questionDigest: string;
}

const authorizedInputs = new WeakMap<AuthorizedModelInput, AuthorizedModelInputState>();
const authorizedConstruction = Object.freeze({});
let mintAuthorizedModelInput: (state: AuthorizedModelInputState) => AuthorizedModelInput;

export class AuthorizedModelInput {
  private constructor(authority?: object) {
    if (authority !== authorizedConstruction) {
      throw new TypeError("AuthorizedModelInput can only be constructed by BotDelivery");
    }
    Object.freeze(this);
  }

  static {
    mintAuthorizedModelInput = (state: AuthorizedModelInputState): AuthorizedModelInput => {
      const input = new AuthorizedModelInput(authorizedConstruction);
      authorizedInputs.set(input, state);
      return input;
    };
  }

  toJSON(): Readonly<Record<string, never>> {
    return Object.freeze({});
  }

}

export interface PrepareAuthorizedModelInputOptions {
  readonly envelope: QuestionEnvelope;
  readonly grant: ModelEgressGrantWire;
  readonly now: Date;
  readonly package: ContextPackageWire;
  readonly profile: PrivateModelGatewayProfile;
}

export function prepareAuthorizedModelInput(
  options: PrepareAuthorizedModelInputOptions,
): AuthorizedModelInput {
  const record = requireExactKeys(
    "authorized model input request",
    options,
    ["envelope", "grant", "now", "package", "profile"],
  );
  if (!(record.profile instanceof PrivateModelGatewayProfile)) {
    throw new TypeError("model input requires PrivateModelGatewayProfile");
  }
  const profile = record.profile;
  if (!registeredProfiles.has(profile)) {
    throw new TypeError("model input requires one registered versioned profile");
  }
  const now = requireDate("model input time", record.now);
  const grantValue = requireModelGrant(record.grant);
  const validated = requirePackage(record.package, now, profile);
  const envelope = requireEnvelope(record.envelope, profile);
  const payloadDigest = createHash("sha256")
    .update(MODEL_INPUT_DOMAIN)
    .update(validated.canonicalPayload)
    .digest("hex");
  const questionDigest = createHash("sha256")
    .update(QUESTION_DIGEST_DOMAIN)
    .update(canonicalJson(envelope))
    .digest("hex");
  const providerRequest = Object.freeze({
    context: Object.freeze(validated.package.blocks.map((block) => Object.freeze({
      evidenceRefs: Object.freeze([...block.evidenceRefs]),
      text: block.text,
    }))),
    instructions: envelope.instructions,
    question: envelope.question,
  });
  if (canonicalJson(providerRequest).byteLength > profile.maximumInputBytes) {
    throw new TypeError("complete model request exceeds its versioned profile");
  }
  return mintAuthorizedModelInput(Object.freeze({
    evidence: validated.evidence,
    grantDigest: createHash("sha256").update(grantValue.value).digest(),
    package: validated.package,
    payloadDigest,
    profile,
    providerRequest,
    questionDigest,
  }));
}

export interface ModelProviderRequest {
  readonly context: readonly {
    readonly evidenceRefs: readonly string[];
    readonly text: string;
  }[];
  readonly instructions: string;
  readonly question: string;
}

interface ModelProviderOutput {
  readonly citations: readonly string[];
  readonly costMicrounits: number;
  readonly elapsedMs: number;
  readonly text: string;
}

interface DeterministicModelGatewayTwinOptions extends ModelProviderOutput {
  readonly profile: PrivateModelGatewayProfile;
}

const trustedGatewayTwins = new WeakSet<DeterministicModelGatewayTwin>();
const gatewayProfiles = new WeakMap<DeterministicModelGatewayTwin, PrivateModelGatewayProfile>();
interface DeterministicGatewayState {
  callCount: number;
  outboundBytes: number;
  readonly output: ModelProviderOutput;
  readonly requests: ModelProviderRequest[];
}
const gatewayStates = new WeakMap<DeterministicModelGatewayTwin, DeterministicGatewayState>();

export class DeterministicModelGatewayTwin {
  constructor(options: DeterministicModelGatewayTwinOptions) {
    const record = requireExactKeys(
      "deterministic model gateway",
      options,
      ["citations", "costMicrounits", "elapsedMs", "profile", "text"],
    );
    if (!(record.profile instanceof PrivateModelGatewayProfile)) {
      throw new TypeError("deterministic gateway requires PrivateModelGatewayProfile");
    }
    if (!Array.isArray(record.citations) || record.citations.some((item) => typeof item !== "string")) {
      throw new TypeError("deterministic gateway citations must be an array of refs");
    }
    if (typeof record.text !== "string") {
      throw new TypeError("deterministic gateway text must be a string");
    }
    const output = Object.freeze({
      citations: Object.freeze([...record.citations] as string[]),
      costMicrounits: requireNonnegativeInteger("provider cost", record.costMicrounits),
      elapsedMs: requireNonnegativeInteger("provider elapsed time", record.elapsedMs),
      text: record.text,
    });
    trustedGatewayTwins.add(this);
    gatewayProfiles.set(this, record.profile);
    gatewayStates.set(this, { callCount: 0, outboundBytes: 0, output, requests: [] });
    Object.freeze(this);
  }

  get callCount(): number {
    return gatewayStates.get(this)?.callCount ?? 0;
  }

  get outboundBytes(): number {
    return gatewayStates.get(this)?.outboundBytes ?? 0;
  }

  get requests(): readonly ModelProviderRequest[] {
    return Object.freeze([...(gatewayStates.get(this)?.requests ?? [])]);
  }
}

async function invokeDeterministicGateway(
  gateway: DeterministicModelGatewayTwin,
  request: ModelProviderRequest,
): Promise<ModelProviderOutput> {
  const state = gatewayStates.get(gateway);
  if (state === undefined || !trustedGatewayTwins.has(gateway)) {
    throw new TypeError("deterministic ModelGateway twin is not trusted");
  }
  const bytes = canonicalJson(request);
  state.callCount += 1;
  state.outboundBytes += bytes.byteLength;
  state.requests.push(request);
  return state.output;
}

export interface AnswerCitation {
  readonly citationOpenRef: string;
  readonly evidenceRef: string;
}

export interface BoundedAnswerArtifact {
  readonly answerPayloadDigest: string;
  readonly citations: readonly AnswerCitation[];
  readonly text: string;
}

export interface ModelUsage {
  readonly costMicrounits: number;
  readonly elapsedMs: number;
  readonly outputBytes: number;
  readonly providerCalls: 1;
}

export interface GeneratedAnswer {
  readonly answer: BoundedAnswerArtifact;
  readonly kind: "generated";
  readonly usage: ModelUsage;
}

export interface GenerationNotAvailable {
  readonly kind: "generation_not_available";
}

export type ModelGenerationOutcome = GeneratedAnswer | GenerationNotAvailable;

export function answerPayloadDigest(input: {
  readonly citations: readonly AnswerCitation[];
  readonly text: string;
}): string {
  const record = requireExactKeys("answer payload", input, ["citations", "text"]);
  if (
    typeof record.text !== "string"
    || record.text.trim().length === 0
    || !Array.isArray(record.citations)
  ) {
    throw new TypeError("answer payload is invalid");
  }
  const seenEvidence = new Set<string>();
  const seenCitationOpenRefs = new Set<string>();
  for (const [index, value] of record.citations.entries()) {
    const citation = requireExactKeys(`answer citation ${index}`, value, [
      "citationOpenRef",
      "evidenceRef",
    ]);
    const evidenceRef = requirePattern(
      "answer Evidence reference",
      citation.evidenceRef,
      EVIDENCE_REF_PATTERN,
    );
    const citationOpenRef = requirePattern(
      "answer CitationOpenRef",
      citation.citationOpenRef,
      /^cor_[0-9a-f]{64}$/,
    );
    if (seenEvidence.has(evidenceRef) || seenCitationOpenRefs.has(citationOpenRef)) {
      throw new TypeError("answer citations must be unique");
    }
    seenEvidence.add(evidenceRef);
    seenCitationOpenRefs.add(citationOpenRef);
  }
  return createHash("sha256")
    .update(ANSWER_PAYLOAD_DOMAIN)
    .update(canonicalJson(record))
    .digest("hex");
}

interface DatabaseQueryResult {
  readonly rows: readonly Readonly<Record<string, unknown>>[];
}

interface ModelEgressQueryAuthority {
  query(config: {
    readonly text: string;
    readonly values: readonly unknown[];
  }): Promise<DatabaseQueryResult>;
}

const REDEEM_SQL = `
SELECT context_egress_redeem_grant(
  $1::uuid, $2::bytea, $3::text, $4::text, $5::bytea, $6::bytea,
  $7::text, $8::bytea, $9::bigint, $10::text, $11::text, $12::text,
  $13::text, $14::text, $15::text, $16::text, $17::text, $18::text,
  $19::text
) AS accepted`;

const RECORD_OUTCOME_SQL = `
SELECT context_egress_record_model_outcome(
  $1::uuid, $2::bytea, $3::bytea, $4::bytea, $5::bytea, $6::bytea,
  $7::text, $8::bigint, $9::bigint, $10::bigint, $11::bigint,
  $12::text, $13::bigint, $14::text
) AS recorded`;

interface InternalModelGenerationBoundaryOptions {
  readonly close: () => Promise<void>;
  readonly clock?: () => Date;
  readonly database: ModelEgressQueryAuthority;
  readonly gateway: DeterministicModelGatewayTwin;
  readonly organizationId: string;
  readonly profile: PrivateModelGatewayProfile;
}

export interface CreatePrivateModelGenerationBoundaryOptions {
  readonly databaseUrl: string;
  readonly gateway: DeterministicModelGatewayTwin;
  readonly organizationId: string;
  readonly profile: PrivateModelGatewayProfile;
}

const boundaryConstruction = Object.freeze({});
let mintModelGenerationBoundary: (
  options: InternalModelGenerationBoundaryOptions,
) => ModelGenerationBoundary;

export class ModelGenerationBoundary {
  readonly #clock: () => Date;
  readonly #closeDatabase: () => Promise<void>;
  readonly #database: ModelEgressQueryAuthority;
  readonly #gateway: DeterministicModelGatewayTwin;
  readonly #organizationId: string;
  readonly #profile: PrivateModelGatewayProfile;
  #closed = false;

  private constructor(options: InternalModelGenerationBoundaryOptions, authority?: object) {
    if (authority !== boundaryConstruction) {
      throw new TypeError("model generation boundaries can only be constructed by BotDelivery");
    }
    const record = requireExactKeys(
      "model generation boundary",
      options,
      options.clock === undefined
        ? ["close", "database", "gateway", "organizationId", "profile"]
        : ["clock", "close", "database", "gateway", "organizationId", "profile"],
    );
    if (!(record.profile instanceof PrivateModelGatewayProfile)) {
      throw new TypeError("model generation boundary requires a profile");
    }
    if (typeof (record.database as ModelEgressQueryAuthority | undefined)?.query !== "function") {
      throw new TypeError("model generation boundary requires a database authority");
    }
    if (typeof record.close !== "function") {
      throw new TypeError("model generation boundary requires database lifecycle authority");
    }
    if (
      !(record.gateway instanceof DeterministicModelGatewayTwin)
      || Object.getPrototypeOf(record.gateway) !== DeterministicModelGatewayTwin.prototype
    ) {
      throw new TypeError("Issue #70 permits only the exact deterministic ModelGateway twin");
    }
    this.#clock = (record.clock as (() => Date) | undefined) ?? (() => new Date());
    this.#closeDatabase = record.close as () => Promise<void>;
    this.#database = record.database as ModelEgressQueryAuthority;
    this.#gateway = record.gateway;
    this.#organizationId = requireUuid("model Organization", record.organizationId);
    this.#profile = record.profile;
    Object.freeze(this);
  }

  static {
    mintModelGenerationBoundary = (
      options: InternalModelGenerationBoundaryOptions,
    ): ModelGenerationBoundary => new ModelGenerationBoundary(
      options,
      boundaryConstruction,
    );
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    await this.#closeDatabase();
  }

  async #recordOutcome(
    state: AuthorizedModelInputState,
    category: "generated" | "output_rejected" | "provider_unavailable",
    usage: Omit<ModelUsage, "providerCalls"> & { readonly providerCalls: number },
    answerDigest: string | null,
  ): Promise<boolean> {
    try {
      const row = (await this.#database.query({
        text: RECORD_OUTCOME_SQL,
        values: [
          this.#organizationId,
          state.grantDigest,
          Buffer.from(state.package.packageDigest, "hex"),
          Buffer.from(state.payloadDigest, "hex"),
          Buffer.from(state.questionDigest, "hex"),
          answerDigest === null ? null : Buffer.from(answerDigest, "hex"),
          category,
          usage.providerCalls,
          usage.costMicrounits,
          usage.elapsedMs,
          usage.outputBytes,
          this.#profile.profileRef,
          this.#profile.auditRetentionSeconds,
          MODEL_AUDIT_PROFILE,
        ],
      })).rows[0];
      return row?.recorded === true;
    } catch {
      return false;
    }
  }

  async generate(
    input: AuthorizedModelInput,
    grant: ModelEgressGrantWire,
  ): Promise<ModelGenerationOutcome> {
    if (!(input instanceof AuthorizedModelInput) || !authorizedInputs.has(input)) {
      throw new TypeError("model generation requires AuthorizedModelInput");
    }
    let grantValue: ModelEgressGrantWire;
    try {
      grantValue = requireModelGrant(grant);
    } catch {
      return { kind: "generation_not_available" };
    }
    const state = authorizedInputs.get(input) as AuthorizedModelInputState;
    const gatewayProfile = gatewayProfiles.get(this.#gateway);
    if (
      this.#closed
      ||
      !trustedGatewayTwins.has(this.#gateway)
      || gatewayProfile === undefined
      || !registeredProfiles.has(this.#profile)
      || !registeredProfiles.has(gatewayProfile)
      || !sameProfile(gatewayProfile, this.#profile)
      || !sameProfile(state.profile, this.#profile)
      || !safeEqual(
        state.grantDigest.toString("hex"),
        createHash("sha256").update(grantValue.value).digest("hex"),
      )
      || BigInt(requireDate("model generation clock", this.#clock()).getTime()) * 1_000n
        >= requireCanonicalTimestamp("Package expiry", state.package.expiresAt)
    ) {
      return { kind: "generation_not_available" };
    }
    try {
      const redemption = (await this.#database.query({
        text: REDEEM_SQL,
        values: [
          this.#organizationId,
          state.grantDigest,
          EGRESS_GRANT_DIGEST_PROFILE,
          "model",
          Buffer.from(state.package.packageDigest, "hex"),
          Buffer.from(state.payloadDigest, "hex"),
          state.package.purpose,
          Buffer.from(state.package.audienceDigest, "hex"),
          state.package.policyEpoch,
          this.#profile.retentionPolicyRef,
          this.#profile.sensitivityPolicyRef,
          this.#profile.issuerRef,
          this.#profile.consumerRef,
          this.#profile.providerRef,
          this.#profile.modelRef,
          null,
          null,
          this.#profile.regionRef,
          this.#profile.profileRef,
        ],
      })).rows[0];
      if (redemption?.accepted !== true) {
        return { kind: "generation_not_available" };
      }
    } catch {
      return { kind: "generation_not_available" };
    }

    let output: ModelProviderOutput;
    try {
      output = await invokeDeterministicGateway(this.#gateway, state.providerRequest);
    } catch {
      await this.#recordOutcome(
        state,
        "provider_unavailable",
        { costMicrounits: 0, elapsedMs: 0, outputBytes: 0, providerCalls: 1 },
        null,
      );
      return { kind: "generation_not_available" };
    }
    const outputBytes = Buffer.byteLength(output.text);
    const usage = {
      costMicrounits: output.costMicrounits,
      elapsedMs: output.elapsedMs,
      outputBytes,
      providerCalls: 1 as const,
    };
    const evidenceByRef = new Map(state.evidence.map((item) => [item.evidenceRef, item]));
    const seen = new Set<string>();
    const citations: AnswerCitation[] = [];
    let valid = (
      output.text.trim().length > 0
      && outputBytes <= this.#profile.maximumOutputBytes
      && output.costMicrounits <= this.#profile.maximumCostMicrounits
      && output.elapsedMs <= this.#profile.maximumElapsedMs
      && output.citations.length <= state.evidence.length
    );
    for (const evidenceRef of output.citations) {
      const evidence = evidenceByRef.get(evidenceRef);
      if (seen.has(evidenceRef) || evidence?.citationOpenRef === null || evidence === undefined) {
        valid = false;
        break;
      }
      seen.add(evidenceRef);
      citations.push(Object.freeze({
        citationOpenRef: evidence.citationOpenRef,
        evidenceRef,
      }));
    }
    if (!valid) {
      await this.#recordOutcome(state, "output_rejected", usage, null);
      return { kind: "generation_not_available" };
    }
    let answer: BoundedAnswerArtifact;
    try {
      answer = Object.freeze({
        answerPayloadDigest: answerPayloadDigest({ citations, text: output.text }),
        citations: Object.freeze(citations),
        text: output.text,
      });
    } catch {
      await this.#recordOutcome(state, "output_rejected", usage, null);
      return { kind: "generation_not_available" };
    }
    if (!(await this.#recordOutcome(state, "generated", usage, answer.answerPayloadDigest))) {
      return { kind: "generation_not_available" };
    }
    return Object.freeze({ answer, kind: "generated", usage });
  }
}

function requirePostgresUrl(value: unknown): string {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > 4_096
    || value.trim() !== value
  ) {
    throw new TypeError("model egress database URL must be a bounded PostgreSQL URL");
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new TypeError("model egress database URL must be a bounded PostgreSQL URL");
  }
  if (
    !["postgres:", "postgresql:"].includes(parsed.protocol)
    || parsed.hostname.length === 0
    || parsed.pathname.length <= 1
  ) {
    throw new TypeError("model egress database URL must be a bounded PostgreSQL URL");
  }
  return value;
}

export function createPrivateModelGenerationBoundary(
  options: CreatePrivateModelGenerationBoundaryOptions,
): ModelGenerationBoundary {
  const record = requireExactKeys(
    "private model generation boundary factory",
    options,
    ["databaseUrl", "gateway", "organizationId", "profile"],
  );
  const pool = new Pool({
    connectionString: requirePostgresUrl(record.databaseUrl),
    max: 2,
  });
  const database: ModelEgressQueryAuthority = {
    async query(config): Promise<DatabaseQueryResult> {
      const result = await pool.query({
        text: config.text,
        values: [...config.values],
      });
      return { rows: result.rows as readonly Readonly<Record<string, unknown>>[] };
    },
  };
  try {
    return mintModelGenerationBoundary({
      close: async () => { await pool.end(); },
      database,
      gateway: record.gateway as DeterministicModelGatewayTwin,
      organizationId: record.organizationId as string,
      profile: record.profile as PrivateModelGatewayProfile,
    });
  } catch (error) {
    void pool.end();
    throw error;
  }
}
