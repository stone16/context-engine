import { spawnSync } from "node:child_process";

import { ContextEngineResolveClient } from "@context-engine/resolve-sdk";

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

const answerEvents = [
  {
    eventVerificationRef: finalizeFixture.eventVerificationRef,
    kind: "answer",
    question: finalizeFixture.question,
    turnRef: finalizeFixture.turnRef,
  },
  {
    eventVerificationRef: followupFixture.eventVerificationRef,
    kind: "answer",
    question: followupFixture.question,
    turnRef: followupFixture.turnRef,
  },
];
if (process.env.CE_BOT_EVENT_MODE === "unbound_identity") {
  answerEvents[0].eventVerificationRef = "unbound:event-verification";
}
const citationEvent = {
    citationOpenRef,
    eventVerificationRef: citationFixture.eventVerificationRef,
    kind: "open_citation",
    openRef: citationFixture.openRef,
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
