#!/usr/bin/env node

import {
  ActionPlane,
  ActionTicketKeyring,
  ExactPrivateFeishuSenderTwin,
  PrivateActionPrepareProfile,
} from "@context-engine/action-plane";
import { ContextEngineResolveClient } from "@context-engine/resolve-sdk";
import { createInterface } from "node:readline";
import pg from "pg";

import {
  DeterministicModelGatewayTwin,
  createPrivateModelGenerationBoundary,
  privateModelGatewayProfileV1,
} from "./index.js";
import {
  BotDelivery,
  PrivateFeishuEventIngressTwin,
  PrivateFeishuIdentityTwin,
  createPrivateDeliveryAuditBoundary,
  type PrivateCitationOpenFixture,
  type PrivateQuestionTurnFixture,
} from "./private-delivery.js";

const { Pool } = pg;
const SERVICE = "context-engine-bot";
const PROCESS_TOPOLOGY = "BotDelivery + ActionPlane";

interface PrivateFeishuEventProfileConfig {
  readonly applicationId: string;
  readonly askerMappings: readonly {
    readonly membershipId: string;
    readonly membershipVersion: number;
    readonly providerAskerId: string;
    readonly userId: string;
  }[];
  readonly consumerRef: string;
  readonly maximumAgeSeconds: number;
  readonly maximumFutureSkewSeconds: number;
  readonly maximumLifetimeSeconds: number;
  readonly providerTenantKey: string;
}

interface BotApplicationConfig {
  readonly actionDatabaseUrl: string;
  readonly actionSigningKey: Buffer;
  readonly feishuEventProfile: PrivateFeishuEventProfileConfig;
  readonly feishuSenderCredential: Buffer;
  readonly feishuVerificationKey: Buffer;
  readonly modelEgressDatabaseUrl: string;
  readonly organizationId: string;
  readonly sdkAuthentication: string;
  readonly sdkBaseUrl: string;
  readonly twinAnswer: string;
  readonly twinCitationOpens: readonly PrivateCitationOpenFixture[];
  readonly twinModelMode: "generated" | "invalid_output";
  readonly twinQuestionTurns: readonly PrivateQuestionTurnFixture[];
  readonly twinSenderMode: "ambiguous" | "applied" | "rejected";
}

function requiredEnvironment(environment: NodeJS.ProcessEnv, name: string): string {
  const value = environment[name];
  if (typeof value !== "string" || value.length === 0 || value.trim() !== value) {
    throw new TypeError("Bot application configuration is not available");
  }
  return value;
}

function closedEnvironment<T extends string>(
  environment: NodeJS.ProcessEnv,
  name: string,
  allowed: readonly T[],
  fallback: T,
): T {
  const value = environment[name] ?? fallback;
  if (!(allowed as readonly string[]).includes(value)) {
    throw new TypeError("Bot application configuration is not available");
  }
  return value as T;
}

function requireDatabaseRole(value: string, role: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new TypeError("Bot application configuration is not available");
  }
  if (
    (parsed.protocol !== "postgres:" && parsed.protocol !== "postgresql:")
    || parsed.username !== role
  ) {
    throw new TypeError("Bot application configuration is not available");
  }
  return value;
}

function loadTwinBindings(environment: NodeJS.ProcessEnv): {
  readonly citationOpens: readonly PrivateCitationOpenFixture[];
  readonly questionTurns: readonly PrivateQuestionTurnFixture[];
} {
  let document: unknown;
  try {
    document = JSON.parse(
      requiredEnvironment(environment, "CONTEXT_ENGINE_BOT_TWIN_BINDINGS_JSON"),
    );
  } catch {
    throw new TypeError("Bot application configuration is not available");
  }
  if (typeof document !== "object" || document === null || Array.isArray(document)) {
    throw new TypeError("Bot application configuration is not available");
  }
  const record = document as Readonly<Record<string, unknown>>;
  if (
    Object.keys(record).sort().join("\0") !== "citationOpens\0questionTurns"
    || !Array.isArray(record.citationOpens)
    || !Array.isArray(record.questionTurns)
  ) {
    throw new TypeError("Bot application configuration is not available");
  }
  return {
    citationOpens: record.citationOpens as readonly PrivateCitationOpenFixture[],
    questionTurns: record.questionTurns as readonly PrivateQuestionTurnFixture[],
  };
}

function loadFeishuEventProfile(
  environment: NodeJS.ProcessEnv,
): PrivateFeishuEventProfileConfig {
  let document: unknown;
  try {
    document = JSON.parse(
      requiredEnvironment(environment, "CONTEXT_ENGINE_BOT_FEISHU_EVENT_PROFILE_JSON"),
    );
  } catch {
    throw new TypeError("Bot application configuration is not available");
  }
  const record = exactEventKeys(document, [
    "applicationId",
    "askerMappings",
    "consumerRef",
    "maximumAgeSeconds",
    "maximumFutureSkewSeconds",
    "maximumLifetimeSeconds",
    "providerTenantKey",
  ]);
  if (record === undefined || !Array.isArray(record.askerMappings)) {
    throw new TypeError("Bot application configuration is not available");
  }
  return Object.freeze(record as unknown as PrivateFeishuEventProfileConfig);
}

function secretBytes(environment: NodeJS.ProcessEnv, name: string): Buffer {
  const value = requiredEnvironment(environment, name);
  if (!/^[0-9a-f]{64,}$/.test(value) || value.length % 2 !== 0) {
    throw new TypeError("Bot application configuration is not available");
  }
  return Buffer.from(value, "hex");
}

function requireProcessOwnedOrganization(
  bindings: ReturnType<typeof loadTwinBindings>,
  organizationId: string,
): void {
  for (const binding of [...bindings.questionTurns, ...bindings.citationOpens]) {
    if (binding.organizationId !== organizationId) {
      throw new TypeError("Bot application configuration is not available");
    }
  }
}

export function loadBotApplicationConfig(
  environment: NodeJS.ProcessEnv,
): BotApplicationConfig {
  const twinBindings = loadTwinBindings(environment);
  const sdkBaseUrl = requiredEnvironment(environment, "CONTEXT_ENGINE_BOT_SDK_BASE_URL");
  let parsedSdkUrl: URL;
  try {
    parsedSdkUrl = new URL(sdkBaseUrl);
  } catch {
    throw new TypeError("Bot application configuration is not available");
  }
  if (parsedSdkUrl.protocol !== "http:" && parsedSdkUrl.protocol !== "https:") {
    throw new TypeError("Bot application configuration is not available");
  }
  const organizationId = requiredEnvironment(
    environment,
    "CONTEXT_ENGINE_BOT_ORGANIZATION_ID",
  );
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(organizationId)) {
    throw new TypeError("Bot application configuration is not available");
  }
  requireProcessOwnedOrganization(twinBindings, organizationId);
  const feishuEventProfile = loadFeishuEventProfile(environment);
  if (
    [...twinBindings.questionTurns, ...twinBindings.citationOpens].some((eventBinding) =>
      eventBinding.consumerRef !== feishuEventProfile.consumerRef
      || eventBinding.organizationId !== organizationId
      || !feishuEventProfile.askerMappings.some((mapping) =>
        mapping.providerAskerId === eventBinding.providerAskerId
        && mapping.membershipId === eventBinding.membershipId
        && mapping.membershipVersion === eventBinding.membershipVersion
        && mapping.userId === eventBinding.userId
      )
    )
  ) {
    throw new TypeError("Bot application configuration is not available");
  }
  const signingKeyHex = requiredEnvironment(
    environment,
    "CONTEXT_ENGINE_BOT_ACTION_SIGNING_KEY_HEX",
  );
  if (!/^[0-9a-f]{64}$/.test(signingKeyHex)) {
    throw new TypeError("Bot application configuration is not available");
  }
  return Object.freeze({
    actionDatabaseUrl: requireDatabaseRole(
      requiredEnvironment(environment, "CONTEXT_ENGINE_BOT_ACTION_DATABASE_URL"),
      "context_engine_action",
    ),
    actionSigningKey: Buffer.from(signingKeyHex, "hex"),
    feishuEventProfile,
    feishuSenderCredential: secretBytes(
      environment,
      "CONTEXT_ENGINE_BOT_FEISHU_SENDER_CREDENTIAL_HEX",
    ),
    feishuVerificationKey: secretBytes(
      environment,
      "CONTEXT_ENGINE_BOT_FEISHU_VERIFICATION_KEY_HEX",
    ),
    modelEgressDatabaseUrl: requireDatabaseRole(
      requiredEnvironment(environment, "CONTEXT_ENGINE_BOT_MODEL_EGRESS_DATABASE_URL"),
      "context_engine_egress",
    ),
    organizationId,
    sdkAuthentication: requiredEnvironment(
      environment,
      "CONTEXT_ENGINE_BOT_SDK_AUTHENTICATION",
    ),
    sdkBaseUrl,
    twinAnswer: requiredEnvironment(environment, "CONTEXT_ENGINE_BOT_TWIN_ANSWER"),
    twinCitationOpens: twinBindings.citationOpens,
    twinModelMode: closedEnvironment(
      environment,
      "CONTEXT_ENGINE_BOT_TWIN_MODEL_MODE",
      ["generated", "invalid_output"],
      "generated",
    ),
    twinQuestionTurns: twinBindings.questionTurns,
    twinSenderMode: closedEnvironment(
      environment,
      "CONTEXT_ENGINE_BOT_TWIN_SENDER_MODE",
      ["ambiguous", "applied", "rejected"],
      "applied",
    ),
  });
}

function exactEventKeys(
  value: unknown,
  expected: readonly string[],
): Readonly<Record<string, unknown>> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const record = value as Readonly<Record<string, unknown>>;
  return Object.keys(record).sort().join("\0") === [...expected].sort().join("\0")
    ? record
    : undefined;
}

async function dispatchTwinEvent(
  delivery: BotDelivery,
  line: string,
): Promise<Readonly<Record<string, unknown>>> {
  if (Buffer.byteLength(line, "utf8") > 65_536) {
    return { kind: "event_not_available" };
  }
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    return { kind: "event_not_available" };
  }
  const kind = typeof value === "object" && value !== null
    ? (value as Readonly<Record<string, unknown>>).kind
    : undefined;
  if (kind === "answer") {
    const envelope = exactEventKeys(value, ["event", "kind"]);
    const event = envelope?.event;
    if (event === undefined) return { kind: "delivery_not_available" };
    return await delivery.answerFeishuEvent(event as never) as unknown as Readonly<Record<string, unknown>>;
  }
  if (kind === "open_citation") {
    const envelope = exactEventKeys(value, ["event", "kind"]);
    const event = envelope?.event;
    if (event === undefined) return { kind: "citation_not_available" };
    const outcome = await delivery.openFeishuCitationEvent(event as never);
    return outcome.kind === "opened"
      ? {
          kind: "opened",
          packageDigest: outcome.package.packageDigest,
          purpose: outcome.package.purpose,
        }
      : outcome as unknown as Readonly<Record<string, unknown>>;
  }
  return { kind: "event_not_available" };
}

async function runApplication(config: BotApplicationConfig): Promise<void> {
  const actionPool = new Pool({
    application_name: "context-engine-bot-action-plane",
    connectionString: config.actionDatabaseUrl,
    connectionTimeoutMillis: 5_000,
    max: 4,
    statement_timeout: 5_000,
  });
  const modelProfile = privateModelGatewayProfileV1();
  const gateway = new DeterministicModelGatewayTwin({
    citations: [],
    costMicrounits: 0,
    elapsedMs: 0,
    profile: modelProfile,
    text: config.twinModelMode === "invalid_output" ? "" : config.twinAnswer,
  });
  const identityTwin = new PrivateFeishuIdentityTwin({
    citationOpens: config.twinCitationOpens,
    questionTurns: config.twinQuestionTurns,
  });
  const eventIngress = new PrivateFeishuEventIngressTwin({
    ...config.feishuEventProfile,
    identityTwin,
    organizationId: config.organizationId,
    verificationKey: config.feishuVerificationKey,
  });
  const sender = new ExactPrivateFeishuSenderTwin({
    applicationId: config.feishuEventProfile.applicationId,
    credential: config.feishuSenderCredential,
    mode: config.twinSenderMode,
    providerTenantKey: config.feishuEventProfile.providerTenantKey,
  });
  const delivery = new BotDelivery({
    actionPlane: new ActionPlane({
      database: actionPool,
      keyring: new ActionTicketKeyring({
        activeVersion: 1,
        keys: new Map([[1, config.actionSigningKey]]),
      }),
      profile: new PrivateActionPrepareProfile({
        approvalTier: "preapproved_private_delivery_v1",
        authenticatedServiceRef: config.twinQuestionTurns[0]?.authenticatedServiceRef
          ?? "application:file-tracer",
        consumerRef: config.feishuEventProfile.consumerRef,
        maximumPayloadBytes: 4_096,
        organizationId: config.organizationId,
        profileRef: "private-action-prepare-v1",
        purpose: "context.answer",
        retentionPolicyRef: "action-digest-audit-retention-v1",
        retentionSeconds: 2_592_000,
        ticketTtlSeconds: 60,
      }),
      sender,
    }),
    auditBoundary: createPrivateDeliveryAuditBoundary({
      databaseUrl: config.actionDatabaseUrl,
      organizationId: config.organizationId,
    }),
    client: new ContextEngineResolveClient({
      authentication: config.sdkAuthentication,
      baseUrl: config.sdkBaseUrl,
    }),
    eventIngress,
    identityTwin,
    modelBoundary: createPrivateModelGenerationBoundary({
      databaseUrl: config.modelEgressDatabaseUrl,
      gateway,
      organizationId: config.organizationId,
      profile: modelProfile,
    }),
    modelProfile,
  });

  process.stdout.write(`${JSON.stringify({
    delivery: "private-file-twin",
    feishu: "private-event-and-sender-twins",
    processTopology: PROCESS_TOPOLOGY,
    service: SERVICE,
    status: "ready",
  })}\n`);
  const lines = createInterface({ crlfDelay: Infinity, input: process.stdin });
  const consume = (async (): Promise<void> => {
    for await (const line of lines) {
      if (line.length === 0) continue;
      const outcome = await dispatchTwinEvent(delivery, line);
      process.stdout.write(`${JSON.stringify({
        gateway: { callCount: gateway.callCount, outboundBytes: gateway.outboundBytes },
        outcome,
        sender: { callCount: sender.callCount, effectCount: sender.effectCount },
      })}\n`);
    }
  })();
  const terminated = new Promise<void>((resolve) => {
    process.once("SIGINT", resolve);
    process.once("SIGTERM", resolve);
  });
  await Promise.race([consume, terminated]);
  lines.close();
  await Promise.allSettled([consume, delivery.close(), actionPool.end()]);
}

async function main(): Promise<void> {
  if (process.argv.slice(2).includes("--test-mode")) {
    process.stdout.write(`${JSON.stringify({
      delivery: "private-file-twin",
      feishu: "private-event-and-sender-twins",
      processTopology: PROCESS_TOPOLOGY,
      service: SERVICE,
      status: "test-complete",
    })}\n`);
    return;
  }
  await runApplication(loadBotApplicationConfig(process.env));
}

main().catch(() => {
  process.stderr.write(`${JSON.stringify({ service: SERVICE, status: "configuration-error" })}\n`);
  process.exitCode = 1;
});
