import { spawnSync } from "node:child_process";
import { createHmac } from "node:crypto";

import { ContextEngineResolveClient } from "@context-engine/resolve-sdk";
import canonicalize from "canonicalize";

function required(name) {
  const value = process.env[name];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`missing installed Bot fixture input: ${name}`);
  }
  return value;
}

function binding({
  citationOpenRef,
  deliveryEvidenceRef,
  eventVerificationRef,
  finalEffect,
  purpose,
  question,
  requestId,
  turnRef,
}) {
  return {
    audienceDigest: process.env.CE_BOT_BINDING_AUDIENCE_DIGEST
      ?? required("CE_BOT_AUDIENCE_DIGEST"),
    authenticatedServiceRef: "application:file-tracer",
    authenticationBindingRef: "binding:file-tracer",
    consumerRef: "consumer:file-tracer",
    ...(citationOpenRef === undefined ? {} : { citationOpenRef }),
    deliveryEvidenceRef,
    destinationRef: "private-chat:file-tracer",
    eventVerificationRef,
    ...(finalEffect === undefined ? { openRef: turnRef } : { finalEffect, turnRef }),
    membershipId: required("CE_BOT_MEMBERSHIP_ID"),
    membershipVersion: 1,
    organizationId: required("CE_BOT_ORGANIZATION_ID"),
    policyEpoch: Number(process.env.CE_BOT_BINDING_POLICY_EPOCH ?? "1"),
    purpose,
    ...(question === undefined ? {} : { question }),
    providerAskerId: "feishu-user:file-tracer",
    requestId,
    userId: required("CE_BOT_USER_ID"),
  };
}

const client = new ContextEngineResolveClient({
  authentication: required("CE_BOT_SDK_AUTHENTICATION"),
  baseUrl: required("CE_BOT_SDK_BASE_URL"),
});
const mode = process.env.CE_BOT_FLOW_MODE ?? "complete";
const question = process.env.CE_BOT_QUESTION ?? "ContextEngine delivers context.";
const finalizeRequestId = process.env.CE_BOT_REQUEST_ID ?? "bot-live-finalize";
const finalizeTurnRef = process.env.CE_BOT_TURN_REF ?? "live-finalize";
let citationOpenRef = `cor_${"0".repeat(64)}`;
if (mode === "complete") {
  const prime = await client.resolve({
    deliveryEvidenceRef: required("CE_BOT_PRIME_EVIDENCE_REF"),
    request: {
      kind: "acquire",
      need: { query: "ContextEngine delivers context." },
    },
    requestId: "bot-live-prime",
  });
  if (
    prime.kind !== "resolved"
    || prime.package.evidence[0]?.citationOpenRef === null
    || prime.package.evidence[0]?.citationOpenRef === undefined
  ) {
    throw new Error("installed Bot fixture could not prime one citation");
  }
  citationOpenRef = prime.package.evidence[0].citationOpenRef;
}

const finalizeFixture = binding({
  deliveryEvidenceRef: required("CE_BOT_FINALIZE_EVIDENCE_REF"),
  eventVerificationRef: `verified:${finalizeTurnRef}`,
  finalEffect: "finalize_reply",
  purpose: "context.answer",
  question,
  requestId: finalizeRequestId,
  turnRef: finalizeTurnRef,
});
const followupFixture = binding({
  deliveryEvidenceRef: required("CE_BOT_FOLLOWUP_EVIDENCE_REF"),
  eventVerificationRef: "verified:live-followup",
  finalEffect: "send_private_followup",
  purpose: "context.answer",
  question,
  requestId: "bot-live-followup",
  turnRef: "live-followup",
});
const citationFixture = binding({
  citationOpenRef,
  deliveryEvidenceRef: required("CE_BOT_CITATION_EVIDENCE_REF"),
  eventVerificationRef: "verified:live-citation",
  purpose: "citation.open",
  requestId: "bot-live-citation",
  turnRef: "live-citation",
});

const feishuVerificationKey = Buffer.alloc(32, 0x45);
const feishuSenderCredential = Buffer.alloc(32, 0x46);
function answerEvent(fixture, eventId) {
  const issuedAt = new Date();
  const event = {
    applicationId: "feishu-app:file-tracer",
    askerProviderId: fixture.providerAskerId,
    consumerRef: fixture.consumerRef,
    destinationKind: "p2p",
    destinationRef: fixture.destinationRef,
    eventId,
    eventKind: "im.message.receive_v1",
    expiresAt: new Date(issuedAt.getTime() + 60_000).toISOString(),
    issuedAt: issuedAt.toISOString(),
    kind: "question",
    organizationId: fixture.organizationId,
    providerTenantKey: "feishu-tenant:file-tracer",
    purpose: "context.answer",
    question: fixture.question,
    requestId: fixture.requestId,
    requestKind: "acquire",
    turnRef: fixture.turnRef,
  };
  return {
    event,
    kind: "answer",
  };
}
const answerEvents = [
  answerEvent(finalizeFixture, "feishu-event:live-finalize"),
  answerEvent(followupFixture, "feishu-event:live-followup"),
];
if (process.env.CE_BOT_EVENT_MODE === "unbound_identity") {
  answerEvents[0].event.askerProviderId = "feishu-user:unbound";
}
for (const envelope of answerEvents) {
  envelope.event.signature = createHmac("sha256", feishuVerificationKey)
    .update(Buffer.from("context-engine.private-feishu-event.v1\0"))
    .update(canonicalize(envelope.event))
    .digest("hex");
}
const citationIssuedAt = new Date();
const citationUnsigned = {
  applicationId: "feishu-app:file-tracer",
  askerProviderId: citationFixture.providerAskerId,
  citationOpenRef,
  consumerRef: citationFixture.consumerRef,
  destinationKind: "p2p",
  destinationRef: citationFixture.destinationRef,
  eventId: "feishu-event:live-citation",
  eventKind: "card.action.trigger_v1",
  expiresAt: new Date(citationIssuedAt.getTime() + 60_000).toISOString(),
  issuedAt: citationIssuedAt.toISOString(),
  kind: "citation_open",
  openRef: citationFixture.openRef,
  organizationId: citationFixture.organizationId,
  providerTenantKey: "feishu-tenant:file-tracer",
  purpose: "citation.open",
  requestId: citationFixture.requestId,
  requestKind: "open_citation",
};
const citationEvent = {
  event: {
    ...citationUnsigned,
    signature: createHmac("sha256", feishuVerificationKey)
      .update(Buffer.from("context-engine.private-feishu-event.v1\0"))
      .update(canonicalize(citationUnsigned))
      .digest("hex"),
  },
  kind: "open_citation",
};
const events = mode === "answer_only"
  ? answerEvents
  : mode === "finalize_only"
    ? answerEvents.slice(0, 1)
    : [...answerEvents, citationEvent];
const botMain = new URL(
  "./node_modules/@context-engine/bot-delivery/dist/main.js",
  import.meta.url,
);
const completed = spawnSync(process.execPath, [botMain.pathname], {
  encoding: "utf8",
  env: {
    ...process.env,
    CONTEXT_ENGINE_BOT_ACTION_DATABASE_URL: required("CE_BOT_ACTION_DATABASE_URL"),
    CONTEXT_ENGINE_BOT_ACTION_SIGNING_KEY_HEX: "71".repeat(32),
    CONTEXT_ENGINE_BOT_FEISHU_EVENT_PROFILE_JSON: JSON.stringify({
      applicationId: "feishu-app:file-tracer",
      askerMappings: [{
        membershipId: required("CE_BOT_MEMBERSHIP_ID"),
        membershipVersion: 1,
        providerAskerId: "feishu-user:file-tracer",
        userId: required("CE_BOT_USER_ID"),
      }],
      consumerRef: "consumer:file-tracer",
      maximumAgeSeconds: 300,
      maximumFutureSkewSeconds: 5,
      maximumLifetimeSeconds: 300,
      providerTenantKey: "feishu-tenant:file-tracer",
    }),
    CONTEXT_ENGINE_BOT_FEISHU_SENDER_CREDENTIAL_HEX: feishuSenderCredential.toString("hex"),
    CONTEXT_ENGINE_BOT_FEISHU_VERIFICATION_KEY_HEX: feishuVerificationKey.toString("hex"),
    CONTEXT_ENGINE_BOT_MODEL_EGRESS_DATABASE_URL: required("CE_BOT_EGRESS_DATABASE_URL"),
    CONTEXT_ENGINE_BOT_ORGANIZATION_ID: required("CE_BOT_ORGANIZATION_ID"),
    CONTEXT_ENGINE_BOT_SDK_AUTHENTICATION: required("CE_BOT_SDK_AUTHENTICATION"),
    CONTEXT_ENGINE_BOT_SDK_BASE_URL: required("CE_BOT_SDK_BASE_URL"),
    CONTEXT_ENGINE_BOT_TWIN_ANSWER: "ContextEngine delivers authorized Package context.",
    CONTEXT_ENGINE_BOT_TWIN_MODEL_MODE: process.env.CE_BOT_MODEL_MODE ?? "generated",
    CONTEXT_ENGINE_BOT_TWIN_SENDER_MODE: process.env.CE_BOT_SENDER_MODE ?? "applied",
    CONTEXT_ENGINE_BOT_TWIN_BINDINGS_JSON: JSON.stringify({
      citationOpens: [citationFixture],
      questionTurns: [finalizeFixture, followupFixture],
    }),
  },
  input: `${events.map((event) => JSON.stringify(event)).join("\n")}\n`,
});
if (completed.status !== 0) {
  throw new Error(`installed Bot binary failed (${JSON.stringify({
    status: completed.status,
    stderr: completed.stderr,
  })})`);
}
const secretValues = [
  required("CE_BOT_ACTION_DATABASE_URL"),
  required("CE_BOT_EGRESS_DATABASE_URL"),
  required("CE_BOT_SDK_AUTHENTICATION"),
  "71".repeat(32),
  feishuSenderCredential.toString("hex"),
  feishuVerificationKey.toString("hex"),
  ...[finalizeFixture, followupFixture, citationFixture]
    .map((fixture) => fixture.deliveryEvidenceRef),
];
if (
  completed.stderr !== ""
  || secretValues.some((secret) =>
    completed.stdout.includes(secret) || completed.stderr.includes(secret))
) {
  throw new Error("installed Bot binary emitted secret-bearing process output");
}
const lines = completed.stdout.trim().split("\n").map((line) => JSON.parse(line));
if (lines.length !== events.length + 1 || lines[0]?.status !== "ready") {
  throw new Error("installed Bot binary did not complete its event lifecycle");
}
const finalized = lines[1];
const followedUp = mode === "finalize_only" ? undefined : lines[2];
const citation = mode === "complete" ? lines[3] : undefined;
process.stdout.write(`${JSON.stringify({
  citation: citation?.outcome ?? null,
  finalized: finalized.outcome,
  followedUp: followedUp?.outcome ?? null,
  gateway: (citation ?? followedUp ?? finalized).gateway,
  sender: (citation ?? followedUp ?? finalized).sender,
})}\n`);
