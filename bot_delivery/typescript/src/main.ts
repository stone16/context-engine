#!/usr/bin/env node

import {
  ActionPlane,
  ActionTicketKeyring,
  DeterministicPrivateSenderTwin,
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
  PrivateFeishuIdentityTwin,
  VerifiedCitationOpen,
  VerifiedQuestionTurn,
  createPrivateDeliveryAuditBoundary,
  type PrivateCitationOpenFixture,
  type PrivateQuestionTurnFixture,
} from "./private-delivery.js";

const { Pool } = pg;
const SERVICE = "context-engine-bot";
const PROCESS_TOPOLOGY = "BotDelivery + ActionPlane";

interface BotApplicationConfig {
  readonly actionDatabaseUrl: string;
  readonly actionSigningKey: Buffer;
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
  identityTwin: PrivateFeishuIdentityTwin,
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
    const event = exactEventKeys(
      value,
      ["eventVerificationRef", "kind", "question", "turnRef"],
    );
    if (event === undefined) return { kind: "delivery_not_available" };
    const turn = identityTwin.verifyQuestionTurn({
      eventVerificationRef: event.eventVerificationRef as string,
      question: event.question as string,
      turnRef: event.turnRef as string,
    });
    if (!(turn instanceof VerifiedQuestionTurn)) return { kind: "delivery_not_available" };
    return await delivery.answer(turn) as unknown as Readonly<Record<string, unknown>>;
  }
  if (kind === "open_citation") {
    const event = exactEventKeys(
      value,
      ["citationOpenRef", "eventVerificationRef", "kind", "openRef"],
    );
    if (event === undefined) return { kind: "citation_not_available" };
    const open = identityTwin.verifyCitationOpen({
      citationOpenRef: event.citationOpenRef as string,
      eventVerificationRef: event.eventVerificationRef as string,
      openRef: event.openRef as string,
    });
    if (!(open instanceof VerifiedCitationOpen)) return { kind: "citation_not_available" };
    const outcome = await delivery.openCitation(open);
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
  const sender = new DeterministicPrivateSenderTwin({ mode: config.twinSenderMode });
  const identityTwin = new PrivateFeishuIdentityTwin({
    citationOpens: config.twinCitationOpens,
    questionTurns: config.twinQuestionTurns,
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
        authenticatedServiceRef: "application:file-tracer",
        consumerRef: "consumer:file-tracer",
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
    processTopology: PROCESS_TOPOLOGY,
    service: SERVICE,
    status: "ready",
  })}\n`);
  const lines = createInterface({ crlfDelay: Infinity, input: process.stdin });
  const consume = (async (): Promise<void> => {
    for await (const line of lines) {
      if (line.length === 0) continue;
      const outcome = await dispatchTwinEvent(delivery, identityTwin, line);
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
