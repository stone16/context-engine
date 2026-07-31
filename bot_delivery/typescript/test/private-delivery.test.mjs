import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { test } from "node:test";

import {
  ActionPlane,
  ActionTicketKeyring,
  DeterministicPrivateSenderTwin,
  ExactPrivateFeishuSenderTwin,
  PrivateActionPrepareProfile,
} from "@context-engine/action-plane";
import { ContextEngineResolveClient } from "@context-engine/resolve-sdk";
import canonicalize from "canonicalize";
import pg from "pg";

import {
  BotDelivery,
  DeterministicModelGatewayTwin,
  PrivateFeishuEventIngressTwin,
  VerifiedCitationOpen,
  VerifiedQuestionTurn,
  createPrivateDeliveryAuditBoundary,
  createPrivateModelGenerationBoundary,
  privateModelGatewayProfileV1,
} from "../dist/public.js";
import { PrivateFeishuIdentityTwin } from "../dist/private-delivery.js";

const organizationId = "81e18bca-86a1-478a-937d-7675c6fe69b0";
const userId = "d3d9893f-82d2-4890-8cb2-4c7e57a56f16";
const membershipId = "9c9e9f4c-a5ec-4417-9408-0346e1c6c998";
const audienceDigest = "a".repeat(64);
const now = new Date(Math.floor(Date.now() / 1_000) * 1_000);
const asOf = new Date(now.getTime() - 1_000).toISOString().replace(".000Z", "Z");
const expiresAt = new Date(now.getTime() + 299_000).toISOString().replace(".000Z", "Z");
const evidenceRef = `ev_${"1".repeat(64)}`;
const citationOpenRef = `cor_${"2".repeat(64)}`;
const grantValues = [
  `egrm_${"3".repeat(64)}`,
  `egrm_${"4".repeat(64)}`,
  `egrm_${"5".repeat(64)}`,
];
const feishuVerificationKey = Buffer.alloc(32, 0x45);
const feishuEventDomain = Buffer.from("context-engine.private-feishu-event.v1\0");

function contextPackage(purpose, sequence) {
  const evidence = {
    authorizationAsOf: asOf,
    citationOpenRef: `cor_${String(sequence).repeat(64)}`,
    decisionRef: `dec_${String(sequence).repeat(32)}`,
    evidenceRef,
    fragmentRef: "fragment:paragraph:1",
    policyEpoch: 7,
    policySnapshotRef: "policy-snapshot-v7",
    projectedFields: ["body"],
    purpose,
    resourceRef: "resource:handbook",
    revisionRef: "revision:handbook:v1",
    runRef: `run:private-${sequence}`,
    sourceAclEvidence: {
      aclAsOf: asOf,
      freshnessProfileRef: "file-source-access-current-transaction-v1",
      kind: "mirrored",
      projectionRef: "source-acl:1",
    },
    sourceRef: "source:handbook",
  };
  const document = {
    asOf,
    audienceDigest,
    blocks: [{
      blockId: `block_${"1".repeat(64)}`,
      evidenceRefs: [evidenceRef],
      text: "ContextEngine delivers authorized context.",
    }],
    budgetUsage: { costMicrounits: 0, elapsedMs: 0, providerCalls: 0, tokens: 8 },
    continuation: null,
    coverage: { status: "sufficient" },
    decisionRef: evidence.decisionRef,
    evidence: [evidence],
    expiresAt,
    gaps: [],
    packageId: `pkg_${String(sequence).repeat(32)}`,
    packageSchemaRef: "context-package-openapi-v0",
    policyEpoch: 7,
    policySnapshotRef: evidence.policySnapshotRef,
    purpose,
    releaseManifestRef: "release:private-answer",
    retentionPolicyRef: "package-digest-only-retention-v1",
    runRef: evidence.runRef,
    tokenizerRef: "utf8-byte-budget-v1",
    ttlSeconds: 300,
  };
  return {
    ...document,
    packageDigest: createHash("sha256").update(canonicalize(document)).digest("hex"),
  };
}

function questionFixture(turnRef, deliveryEvidenceRef, finalEffect, requestId, question) {
  return {
    audienceDigest,
    authenticatedServiceRef: "application:private-bot",
    authenticationBindingRef: "binding:private-bot",
    consumerRef: "consumer:private-bot",
    deliveryEvidenceRef,
    destinationRef: "private-chat:42",
    eventVerificationRef: `verified:${turnRef}`,
    finalEffect,
    membershipId,
    membershipVersion: 7,
    organizationId,
    policyEpoch: 7,
    purpose: "context.answer",
    question,
    providerAskerId: "feishu-user:42",
    requestId,
    turnRef,
    userId,
  };
}

function signedFeishuQuestionEvent(overrides = {}, key = feishuVerificationKey) {
  const unsigned = {
    applicationId: "feishu-app:private-bot",
    askerProviderId: "feishu-user:42",
    consumerRef: "consumer:private-bot",
    destinationKind: "p2p",
    destinationRef: "private-chat:42",
    eventId: "feishu-event:answer-1",
    eventKind: "im.message.receive_v1",
    expiresAt: new Date(now.getTime() + 60_000).toISOString(),
    issuedAt: now.toISOString(),
    kind: "question",
    organizationId,
    providerTenantKey: "feishu-tenant:private-bot",
    purpose: "context.answer",
    question: "What does ContextEngine deliver?",
    requestId: "bot-answer-1",
    requestKind: "acquire",
    turnRef: "turn-finalize",
    ...overrides,
  };
  return {
    ...unsigned,
    signature: createHmac("sha256", key)
      .update(feishuEventDomain)
      .update(canonicalize(unsigned))
      .digest("hex"),
  };
}

function signedFeishuCitationEvent(overrides = {}, key = feishuVerificationKey) {
  const unsigned = {
    applicationId: "feishu-app:private-bot",
    askerProviderId: "feishu-user:42",
    citationOpenRef,
    consumerRef: "consumer:private-bot",
    destinationKind: "p2p",
    destinationRef: "private-chat:42",
    eventId: "feishu-event:citation-1",
    eventKind: "card.action.trigger_v1",
    expiresAt: new Date(now.getTime() + 60_000).toISOString(),
    issuedAt: now.toISOString(),
    kind: "citation_open",
    openRef: "citation-open-1",
    organizationId,
    providerTenantKey: "feishu-tenant:private-bot",
    purpose: "citation.open",
    requestId: "bot-citation-request-1",
    requestKind: "open_citation",
    ...overrides,
  };
  return {
    ...unsigned,
    signature: createHmac("sha256", key)
      .update(feishuEventDomain)
      .update(canonicalize(unsigned))
      .digest("hex"),
  };
}

function feishuIngress(identityTwin, overrides = {}) {
  return new PrivateFeishuEventIngressTwin({
    applicationId: "feishu-app:private-bot",
    askerMappings: [{
      membershipId,
      membershipVersion: 7,
      providerAskerId: "feishu-user:42",
      userId,
    }],
    clock: () => now,
    consumerRef: "consumer:private-bot",
    identityTwin,
    maximumAgeSeconds: 120,
    maximumFutureSkewSeconds: 5,
    maximumLifetimeSeconds: 120,
    organizationId,
    providerTenantKey: "feishu-tenant:private-bot",
    verificationKey: feishuVerificationKey,
    ...overrides,
  });
}

function citationFixture(deliveryEvidenceRef) {
  return {
    audienceDigest,
    authenticatedServiceRef: "application:private-bot",
    authenticationBindingRef: "binding:private-bot",
    citationOpenRef,
    consumerRef: "consumer:private-bot",
    deliveryEvidenceRef,
    destinationRef: "private-chat:42",
    eventVerificationRef: "verified:citation-open-1",
    membershipId,
    membershipVersion: 7,
    openRef: "citation-open-1",
    organizationId,
    policyEpoch: 7,
    purpose: "citation.open",
    providerAskerId: "feishu-user:42",
    requestId: "bot-citation-request-1",
    userId,
  };
}

function actionDatabase() {
  let nextTicket = 10;
  let nextReceipt = 30;
  const attempts = new Map();
  const prepares = [];
  return {
    prepares,
    async connect() {
      return {
        async query(query) {
          if (query.text.includes("pg_advisory_unlock")) {
            return { rows: [{ unlocked: true }] };
          }
          if (query.text.includes("context_action_begin_private_effect")) {
            const providerAttemptRef = query.values[20];
            attempts.set(query.values[1], {
              audience_digest: query.values[13],
              delivery_attempt_ref: query.values[2],
              destination_digest: query.values[12],
              idempotency_digest: query.values[6],
              operation: query.values[3],
              organization_id: query.values[0],
              payload_digest: query.values[5],
              provider_attempt_ref: providerAttemptRef,
              ticket_ref: query.values[1],
            });
            return { rows: [{
              destination_ref: "private-chat:42",
              outcome: "sender_required",
              provider_attempt_ref: providerAttemptRef,
            }] };
          }
          if (query.text.includes("context_action_complete_private_effect")) {
            if (query.values[3] === "ambiguous") {
              return { rows: [{
                outcome: "reconciliation_required",
                provider_attempt_ref: query.values[2],
              }] };
            }
            if (query.values[3] === "rejected") {
              return { rows: [{ outcome: "rejected" }] };
            }
            return { rows: [{
              ...attempts.get(query.values[1]),
              applied_at: query.values[5],
              outcome: "applied",
              provider_effect_digest: query.values[4],
              receipt_ref: `acr_${(nextReceipt++).toString(16).padStart(32, "0")}`,
            }] };
          }
          throw new Error("unexpected action session query");
        },
        release() {},
      };
    },
    async query(query) {
      if (query.text.includes("context_action_bind_private_delivery_effect")) {
        return { rows: [{
          audience_digest: Buffer.from(audienceDigest, "hex"),
          authenticated_service_ref: "application:private-bot",
          authentication_binding_ref: "binding:private-bot",
          consumer_ref: "consumer:private-bot",
          destination_ref: "private-chat:42",
          membership_id: membershipId,
          membership_version: 7,
          organization_id: organizationId,
          outcome: "bound",
          policy_epoch: 7,
          purpose: "context.answer",
          user_id: userId,
        }] };
      }
      assert.match(query.text, /context_action_prepare_private_effect/);
      prepares.push({
        deliveryAttemptRef: query.values[19],
        idempotencyDigest: Buffer.from(query.values[16]).toString("hex"),
        operation: query.values[13],
      });
      return { rows: [{
        delivery_attempt_ref: query.values[19],
        expires_at: new Date(now.getTime() + 60_000),
        idempotent: false,
        issued_at: now,
        outcome: "prepared",
        ticket_ref: `act_${(nextTicket++).toString(16).padStart(32, "0")}`,
      }] };
    },
  };
}

function configuredDelivery(options = {}) {
  const requests = [];
  let resolveSequence = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (request) => {
    const body = JSON.parse(await request.clone().text());
    requests.push({ body, headers: Object.fromEntries(request.headers.entries()) });
    resolveSequence += 1;
    const purpose = body.kind === "open_citation" ? "citation.open" : "context.answer";
    const outcome = options.resolveOutcome?.({ body, purpose, resolveSequence }) ?? {
      egressGrant: { kind: "model", value: grantValues[resolveSequence - 1] },
      kind: "resolved",
      package: contextPackage(purpose, resolveSequence),
    };
    return new Response(JSON.stringify(outcome), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    });
  };

  const modelProfile = privateModelGatewayProfileV1();
  const gateway = new DeterministicModelGatewayTwin({
    citations: [evidenceRef],
    costMicrounits: 7,
    elapsedMs: 5,
    profile: modelProfile,
    text: "Authorized answer.",
  });
  const originalQuery = pg.Pool.prototype.query;
  const originalEnd = pg.Pool.prototype.end;
  const auditRecords = [];
  pg.Pool.prototype.query = async function query(config) {
    if (config.text.includes("context_egress_redeem_grant")) {
      return { rows: [{ accepted: options.egressAccepted ?? true }] };
    }
    if (config.text.includes("context_egress_record_model_outcome")) {
      return { rows: [{ recorded: options.modelAuditRecorded ?? true }] };
    }
    if (config.text.includes("context_action_record_private_delivery_outcome")) {
      auditRecords.push(config);
      return { rows: [{ recorded: options.deliveryAuditRecorded ?? true }] };
    }
    throw new Error("unexpected model database query");
  };
  pg.Pool.prototype.end = async function end() {};

  const action = actionDatabase();
  const sender = options.sender ?? new DeterministicPrivateSenderTwin({
    mode: options.senderMode ?? "applied",
  });
  const actionPlane = new ActionPlane({
    database: action,
    keyring: new ActionTicketKeyring({
      activeVersion: 1,
      keys: new Map([[1, Buffer.alloc(32, 0x71)]]),
    }),
    profile: new PrivateActionPrepareProfile({
      approvalTier: "preapproved_private_delivery_v1",
      authenticatedServiceRef: "application:private-bot",
      consumerRef: "consumer:private-bot",
      maximumPayloadBytes: 4_096,
      organizationId,
      profileRef: "private-action-prepare-v1",
      purpose: "context.answer",
      retentionPolicyRef: "action-digest-audit-retention-v1",
      retentionSeconds: 2_592_000,
      ticketTtlSeconds: 60,
    }),
    sender,
  });
  const questionTurns = options.questionTurns ?? [
    questionFixture("turn-finalize", `der_${"6".repeat(64)}`, "finalize_reply", "bot-answer-1", "What does ContextEngine deliver?"),
    questionFixture("turn-followup", `der_${"7".repeat(64)}`, "send_private_followup", "bot-answer-2", "Send a private follow-up."),
  ];
  const citationOpens = [citationFixture(`der_${"8".repeat(64)}`)];
  const identityTwin = new PrivateFeishuIdentityTwin({ citationOpens, questionTurns });
  const eventIngress = options.feishuIngressOptions === undefined
    ? undefined
    : feishuIngress(identityTwin, options.feishuIngressOptions);
  const auditBoundary = createPrivateDeliveryAuditBoundary({
    databaseUrl: "postgresql://context_engine_action:unused@127.0.0.1/action-unit",
    organizationId,
  });
  let deliveryAttempt = 1;
  const deliveryAttempts = [];
  let auditRef = 1;
  const delivery = new BotDelivery({
    actionPlane,
    auditBoundary,
    client: new ContextEngineResolveClient({
      authentication: "private-bot-credential",
      baseUrl: "https://context-engine.invalid",
    }),
    clock: () => now,
    deliveryAttemptRefFactory: () => {
      const value = `dla_${(deliveryAttempt++).toString(16).padStart(32, "0")}`;
      deliveryAttempts.push(value);
      return value;
    },
    ...(eventIngress === undefined ? {} : { eventIngress }),
    identityTwin,
    modelBoundary: createPrivateModelGenerationBoundary({
      databaseUrl: "postgresql://context_engine_egress:unused@127.0.0.1/model-unit",
      gateway,
      organizationId,
      profile: modelProfile,
    }),
    modelProfile,
    restrictedAuditRefFactory: () =>
      `bda_${(auditRef++).toString(16).padStart(32, "0")}`,
  });
  return {
    action,
    auditRecords,
    citationOpens,
    delivery,
    deliveryAttempts,
    eventIngress,
    gateway,
    identityTwin,
    questionTurns,
    requests,
    restore() {
      globalThis.fetch = originalFetch;
      pg.Pool.prototype.query = originalQuery;
      pg.Pool.prototype.end = originalEnd;
    },
    sender,
  };
}

test("BotDelivery completes private finalize/follow-up and citation through SDK boundaries", async () => {
  const fixture = configuredDelivery();
  try {
    const finalizeTurn = fixture.identityTwin.verifyQuestionTurn({
      eventVerificationRef: fixture.questionTurns[0].eventVerificationRef,
      question: "What does ContextEngine deliver?",
      turnRef: fixture.questionTurns[0].turnRef,
    });
    const followupTurn = fixture.identityTwin.verifyQuestionTurn({
      eventVerificationRef: fixture.questionTurns[1].eventVerificationRef,
      question: "Send a private follow-up.",
      turnRef: fixture.questionTurns[1].turnRef,
    });
    assert.equal(finalizeTurn instanceof VerifiedQuestionTurn, true);
    assert.equal(followupTurn instanceof VerifiedQuestionTurn, true);

    const [finalized, concurrentFinalized] = await Promise.all([
      fixture.delivery.answer(finalizeTurn),
      fixture.delivery.answer(finalizeTurn),
    ]);
    const followedUp = await fixture.delivery.answer(followupTurn);
    const finalizedReplay = await fixture.delivery.answer(finalizeTurn);
    const citationOpen = fixture.identityTwin.verifyCitationOpen({
      citationOpenRef,
      eventVerificationRef: fixture.citationOpens[0].eventVerificationRef,
      openRef: fixture.citationOpens[0].openRef,
    });
    assert.equal(citationOpen instanceof VerifiedCitationOpen, true);
    const citation = await fixture.delivery.openCitation(citationOpen);

    assert.equal(finalized.kind, "delivered", JSON.stringify({
      actionPrepares: fixture.action.prepares,
      auditRecords: fixture.auditRecords.length,
      finalized,
      gatewayCalls: fixture.gateway.callCount,
      requests: fixture.requests.length,
      senderEffects: fixture.sender.effectCount,
    }));
    assert.deepEqual(concurrentFinalized, finalized);
    assert.deepEqual(finalizedReplay, finalized);
    assert.equal(finalized.finalStatus, "finalized");
    assert.equal(followedUp.kind, "delivered");
    assert.equal(followedUp.finalStatus, "private_followup");
    assert.equal(fixture.gateway.callCount, 2);
    assert.equal(fixture.sender.effectCount, 4);
    assert.deepEqual(fixture.action.prepares.map((entry) => entry.operation), [
      "create_placeholder",
      "finalize_reply",
      "create_placeholder",
      "send_private_followup",
    ]);
    assert.equal(new Set(fixture.action.prepares.map((entry) => entry.idempotencyDigest)).size, 4);
    assert.equal(fixture.auditRecords.length, 2);
    for (const receipt of [finalized, followedUp]) {
      const serialized = JSON.stringify(receipt);
      assert.equal(serialized.includes("Authorized answer."), false);
      assert.equal(serialized.includes("egrm_"), false);
      assert.equal(serialized.includes("der_"), false);
      assert.equal(Object.keys(receipt.operationReceiptRefs).length, 2);
    }
    assert.equal(citation.kind, "opened");
    assert.equal(citation.package.purpose, "citation.open");
    assert.equal("sourceUrl" in citation, false);
    assert.deepEqual(fixture.requests.map((request) => request.body.kind), [
      "acquire",
      "acquire",
      "open_citation",
    ]);
    assert.deepEqual(
      fixture.requests.map((request) => Object.keys(request.body).sort()),
      [["kind", "need"], ["kind", "need"], ["citationOpenRef", "kind"]],
    );
    assert.deepEqual(
      fixture.identityTwin.verifyQuestionTurn({
        eventVerificationRef: fixture.questionTurns[0].eventVerificationRef,
        question: "mutated unverified question",
        turnRef: fixture.questionTurns[0].turnRef,
      }),
      { kind: "identity_not_bound" },
    );
    assert.deepEqual(
      fixture.identityTwin.verifyCitationOpen({
        citationOpenRef: `cor_${"f".repeat(64)}`,
        eventVerificationRef: fixture.citationOpens[0].eventVerificationRef,
        openRef: fixture.citationOpens[0].openRef,
      }),
      { kind: "identity_not_bound" },
    );
    assert.deepEqual(
      fixture.requests.map((request) => request.headers["x-context-delivery-evidence-ref"]),
      [
        fixture.questionTurns[0].deliveryEvidenceRef,
        fixture.questionTurns[1].deliveryEvidenceRef,
        fixture.citationOpens[0].deliveryEvidenceRef,
      ],
    );
  } finally {
    await fixture.delivery.close();
    fixture.restore();
  }
});

test("unbound inputs and unavailable citation are generic before model or Sender", async () => {
  const fixture = configuredDelivery();
  try {
    assert.deepEqual(
      fixture.identityTwin.verifyQuestionTurn({
        eventVerificationRef: "forged",
        question: "probe",
        turnRef: fixture.questionTurns[0].turnRef,
      }),
      { kind: "identity_not_bound" },
    );
    assert.deepEqual(await fixture.delivery.answer({}), { kind: "delivery_not_available" });
    assert.deepEqual(await fixture.delivery.openCitation({}), { kind: "citation_not_available" });
    assert.equal(fixture.gateway.outboundBytes, 0);
    assert.equal(fixture.sender.effectCount, 0);
    assert.equal(fixture.requests.length, 0);
    assert.throws(() => new VerifiedQuestionTurn(), /identity-adapter constructed/);
    assert.throws(() => new VerifiedCitationOpen(), /identity-adapter constructed/);
  } finally {
    await fixture.delivery.close();
    fixture.restore();
  }
});

test("ADR-0089 valid private event mints one nominal turn and one opaque evidence issuance", async () => {
  const fixture = configuredDelivery({ feishuIngressOptions: {} });
  const ingress = fixture.eventIngress;
  try {
    const outcome = await fixture.delivery.answerFeishuEvent(signedFeishuQuestionEvent());
    assert.equal(ingress.acceptedEventCount, 1);
    assert.equal(ingress.evidenceIssuanceCount, 1);
    assert.equal(ingress.verifiedTurnCount, 1);
    assert.deepEqual(outcome, {
      deliveryAttemptRef: `dla_${"1".padStart(32, "0")}`,
      finalStatus: "finalized",
      kind: "delivered",
      operationReceiptRefs: {
        final: `acr_${(31).toString(16).padStart(32, "0")}`,
        placeholder: `acr_${(30).toString(16).padStart(32, "0")}`,
      },
      packageDigest: contextPackage("context.answer", 1).packageDigest,
      restrictedAuditRef: `bda_${"1".padStart(32, "0")}`,
    });
    assert.deepEqual(Object.keys(fixture.requests[0].body).sort(), ["kind", "need"]);
    assert.equal(JSON.stringify(fixture.requests[0].body).includes("feishu-"), false);
    assert.equal(
      fixture.requests[0].headers["x-context-delivery-evidence-ref"],
      fixture.questionTurns[0].deliveryEvidenceRef,
    );
  } finally {
    await fixture.delivery.close();
    fixture.restore();
  }
});

test("ADR-0089 invalid events are generic before every trusted mint point", async () => {
  const poisonedSecret = Buffer.from("provider-secret-material-must-not-leak");
  const cases = [
    ["forged signature", {}, Buffer.alloc(32, 0x66)],
    ["envelope integrity", { question: "tampered after signing" }, undefined, true],
    ["expired", { expiresAt: new Date(now.getTime() - 1).toISOString() }],
    ["stale", { issuedAt: new Date(now.getTime() - 121_000).toISOString() }],
    ["future", { issuedAt: new Date(now.getTime() + 6_000).toISOString() }],
    ["wrong application", { applicationId: "feishu-app:wrong" }],
    ["wrong tenant", { providerTenantKey: "feishu-tenant:wrong" }],
    ["wrong Organization", { organizationId: "c938bc5b-16d4-49f1-befd-75ce8f15cf73" }],
    ["wrong asker", { askerProviderId: "feishu-user:wrong" }],
    ["wrong consumer", { consumerRef: "consumer:wrong" }],
    ["wrong purpose", { purpose: "citation.open" }],
    ["wrong request", { requestId: "bot-answer-wrong" }],
    ["wrong request kind", { requestKind: "continue" }],
    ["wrong event kind", { eventKind: "im.message.recalled_v1" }],
    ["group destination", { destinationKind: "group" }],
    ["wrong destination", { destinationRef: "private-chat:wrong" }],
    ["question contains secret", { question: poisonedSecret.toString("utf8") }],
  ];
  for (const [name, mutation, key, tamperAfterSigning] of cases) {
    const fixture = configuredDelivery({
      feishuIngressOptions: {
        verificationKey: name === "question contains secret"
          ? poisonedSecret
          : feishuVerificationKey,
      },
    });
    const ingress = fixture.eventIngress;
    try {
      let event = signedFeishuQuestionEvent(
        tamperAfterSigning ? {} : mutation,
        key ?? (name === "question contains secret" ? poisonedSecret : feishuVerificationKey),
      );
      if (tamperAfterSigning) event = { ...event, ...mutation };
      assert.deepEqual(
        await fixture.delivery.answerFeishuEvent(event),
        { kind: "delivery_not_available" },
        name,
      );
      assert.deepEqual({
        acceptedEvents: ingress.acceptedEventCount,
        actionTickets: fixture.action.prepares.length,
        deliveryAttempts: fixture.deliveryAttempts.length,
        evidenceIssuances: ingress.evidenceIssuanceCount,
        externalEffects: fixture.sender.effectCount,
        modelBytes: fixture.gateway.outboundBytes,
        providerAttempts: fixture.sender.callCount,
        trustedDeliveryContexts: fixture.requests.length,
        trustedEffectIntents: fixture.action.prepares.length,
        trustedTurns: ingress.verifiedTurnCount,
      }, {
        acceptedEvents: 0,
        actionTickets: 0,
        deliveryAttempts: 0,
        evidenceIssuances: 0,
        externalEffects: 0,
        modelBytes: 0,
        providerAttempts: 0,
        trustedDeliveryContexts: 0,
        trustedEffectIntents: 0,
        trustedTurns: 0,
      }, name);
      const rendered = [String(ingress), assert.throws(() => JSON.stringify(ingress))?.message]
        .join("\n");
      assert.equal(rendered.includes(poisonedSecret.toString("utf8")), false, name);
    } finally {
      await fixture.delivery.close();
      fixture.restore();
    }
  }
});

test("ADR-0089 event replay is refused before a second mint or any delivery authority", async () => {
  const questionTurns = [questionFixture(
    "turn-finalize",
    `der_${"6".repeat(64)}`,
    "finalize_reply",
    "bot-answer-1",
    "What does ContextEngine deliver?",
  )];
  const identityTwin = new PrivateFeishuIdentityTwin({ citationOpens: [], questionTurns });
  const ingress = feishuIngress(identityTwin);
  const event = signedFeishuQuestionEvent();
  assert.equal(ingress.verifyQuestionEvent(event) instanceof VerifiedQuestionTurn, true);
  assert.deepEqual(ingress.verifyQuestionEvent(event), { kind: "identity_not_bound" });
  assert.equal(ingress.evidenceIssuanceCount, 1);
  assert.equal(ingress.verifiedTurnCount, 1);
});

test("ADR-0089 malformed and duplicate event identities mint no authority", async () => {
  for (const [name, mutation] of [
    ["empty event id", { eventId: "" }],
    ["extra envelope field", { unexpected: "field" }],
  ]) {
    const fixture = configuredDelivery();
    const ingress = feishuIngress(fixture.identityTwin);
    try {
      assert.deepEqual(ingress.verifyQuestionEvent(signedFeishuQuestionEvent(mutation)), {
        kind: "identity_not_bound",
      }, name);
      assert.equal(ingress.evidenceIssuanceCount, 0, name);
      assert.equal(ingress.verifiedTurnCount, 0, name);
      assert.equal(fixture.action.prepares.length, 0, name);
      assert.equal(fixture.sender.callCount, 0, name);
    } finally {
      await fixture.delivery.close();
      fixture.restore();
    }
  }
});

test("ADR-0089 mismatched Membership mapping cannot mint a verified turn", async () => {
  const fixture = configuredDelivery();
  const ingress = feishuIngress(fixture.identityTwin, {
    askerMappings: [{
      membershipId,
      membershipVersion: 8,
      providerAskerId: "feishu-user:42",
      userId,
    }],
  });
  try {
    assert.deepEqual(ingress.verifyQuestionEvent(signedFeishuQuestionEvent()), {
      kind: "identity_not_bound",
    });
    assert.equal(ingress.evidenceIssuanceCount, 0);
    assert.equal(ingress.verifiedTurnCount, 0);
  } finally {
    await fixture.delivery.close();
    fixture.restore();
  }
});

test("ADR-0089 citation open uses the same authenticated private event boundary", async () => {
  const fixture = configuredDelivery({ feishuIngressOptions: {} });
  try {
    const opened = await fixture.delivery.openFeishuCitationEvent(
      signedFeishuCitationEvent(),
    );
    assert.equal(opened.kind, "opened");
    assert.equal(fixture.eventIngress.evidenceIssuanceCount, 1);
    assert.equal(fixture.eventIngress.verifiedCitationCount, 1);
    assert.deepEqual(Object.keys(fixture.requests[0].body).sort(), [
      "citationOpenRef",
      "kind",
    ]);
    assert.equal(JSON.stringify(fixture.requests[0].body).includes("feishu-"), false);
    assert.deepEqual(
      await fixture.delivery.openFeishuCitationEvent(signedFeishuCitationEvent()),
      { kind: "citation_not_available" },
    );
    assert.equal(fixture.requests.length, 1);
  } finally {
    await fixture.delivery.close();
    fixture.restore();
  }
});

test("denied, empty, wrong-egress, stale-audience, and stale-epoch Packages stop before model and final effect", async () => {
  const cases = [
    ["denied", () => ({ kind: "request_not_available", retryable: false })],
    ["empty", ({ purpose, resolveSequence }) => ({
      egressGrant: null,
      kind: "resolved",
      package: {
        ...contextPackage(purpose, resolveSequence),
        blocks: [],
        coverage: { reason: "no_authorized_evidence", status: "empty" },
        evidence: [],
      },
    })],
    ["wrong-egress", ({ purpose, resolveSequence }) => ({
      egressGrant: { kind: "channel", value: `egrc_${"9".repeat(64)}` },
      kind: "resolved",
      package: contextPackage(purpose, resolveSequence),
    })],
    ["stale-audience", ({ purpose, resolveSequence }) => ({
      egressGrant: { kind: "model", value: grantValues[resolveSequence - 1] },
      kind: "resolved",
      package: { ...contextPackage(purpose, resolveSequence), audienceDigest: "b".repeat(64) },
    })],
    ["stale-epoch", ({ purpose, resolveSequence }) => ({
      egressGrant: { kind: "model", value: grantValues[resolveSequence - 1] },
      kind: "resolved",
      package: { ...contextPackage(purpose, resolveSequence), policyEpoch: 6 },
    })],
  ];
  for (const [name, resolveOutcome] of cases) {
    const fixture = configuredDelivery({ resolveOutcome });
    try {
      const turn = fixture.identityTwin.verifyQuestionTurn({
        eventVerificationRef: fixture.questionTurns[0].eventVerificationRef,
        question: fixture.questionTurns[0].question,
        turnRef: fixture.questionTurns[0].turnRef,
      });
      assert.deepEqual(await fixture.delivery.answer(turn), {
        kind: "delivery_not_available",
      }, name);
      assert.equal(fixture.gateway.outboundBytes, 0, name);
      assert.equal(fixture.sender.effectCount, 1, name);
      assert.deepEqual(fixture.action.prepares.map((entry) => entry.operation), [
        "create_placeholder",
      ], name);
      assert.equal(fixture.auditRecords.length, 0, name);
    } finally {
      await fixture.delivery.close();
      fixture.restore();
    }
  }
});

test("model or final delivery audit failure is closed after only its already-completed work", async () => {
  for (const [name, options, expectedEffects, expectedPrepares] of [
    ["model-audit", { modelAuditRecorded: false }, 1, ["create_placeholder"]],
    ["delivery-audit", { deliveryAuditRecorded: false }, 2, [
      "create_placeholder",
      "finalize_reply",
    ]],
  ]) {
    const fixture = configuredDelivery(options);
    try {
      const turn = fixture.identityTwin.verifyQuestionTurn({
        eventVerificationRef: fixture.questionTurns[0].eventVerificationRef,
        question: fixture.questionTurns[0].question,
        turnRef: fixture.questionTurns[0].turnRef,
      });
      const outcome = await fixture.delivery.answer(turn);
      assert.equal(
        outcome.kind,
        name === "delivery-audit"
          ? "delivery_reconciliation_required"
          : "delivery_not_available",
        name,
      );
      assert.equal(fixture.gateway.callCount, 1, name);
      assert.equal(fixture.sender.effectCount, expectedEffects, name);
      assert.deepEqual(
        fixture.action.prepares.map((entry) => entry.operation),
        expectedPrepares,
        name,
      );
      if (name === "delivery-audit") {
        assert.match(outcome.providerAttemptRef, /^pat_[0-9a-f]{32}$/);
        assert.deepEqual(await fixture.delivery.answer(turn), outcome);
        assert.equal(fixture.gateway.callCount, 1);
        assert.equal(fixture.sender.effectCount, expectedEffects);
      }
    } finally {
      await fixture.delivery.close();
      fixture.restore();
    }
  }
});

test("long verified turn refs stay closed and produce bounded derived idempotency", async () => {
  const longFixture = questionFixture(
    "t".repeat(256),
    `der_${"9".repeat(64)}`,
    "finalize_reply",
    "bot-answer-long",
    "Bounded long turn.",
  );
  longFixture.eventVerificationRef = "verified:long-turn";
  const fixture = configuredDelivery({ questionTurns: [longFixture] });
  try {
    const turn = fixture.identityTwin.verifyQuestionTurn({
      eventVerificationRef: longFixture.eventVerificationRef,
      question: longFixture.question,
      turnRef: longFixture.turnRef,
    });
    assert.ok(turn instanceof VerifiedQuestionTurn);
    assert.equal((await fixture.delivery.answer(turn)).kind, "delivered");
    assert.equal(fixture.action.prepares.length, 2);
  } finally {
    await fixture.delivery.close();
    fixture.restore();
  }
});

test("Sender rejection is zero-effect and ambiguity preserves the original reconciliation identity", async () => {
  for (const [mode, expectedKind, expectedEffects] of [
    ["rejected", "delivery_not_available", 0],
    ["ambiguous", "delivery_reconciliation_required", 1],
  ]) {
    const fixture = configuredDelivery({ senderMode: mode });
    try {
      const turn = fixture.identityTwin.verifyQuestionTurn({
        eventVerificationRef: fixture.questionTurns[0].eventVerificationRef,
        question: fixture.questionTurns[0].question,
        turnRef: fixture.questionTurns[0].turnRef,
      });
      const outcome = await fixture.delivery.answer(turn);
      assert.equal(outcome.kind, expectedKind, mode);
      if (mode === "ambiguous") {
        assert.match(outcome.providerAttemptRef, /^pat_[0-9a-f]{32}$/);
        assert.match(outcome.deliveryAttemptRef, /^dla_[0-9a-f]{32}$/);
      }
      assert.equal(fixture.gateway.outboundBytes, 0, mode);
      assert.equal(fixture.requests.length, 0, mode);
      assert.equal(fixture.sender.effectCount, expectedEffects, mode);
      assert.deepEqual(fixture.action.prepares.map((entry) => entry.operation), [
        "create_placeholder",
      ], mode);
      if (mode === "ambiguous") {
        assert.deepEqual(await fixture.delivery.answer(turn), outcome);
        assert.equal(fixture.sender.callCount, 1);
      }
    } finally {
      await fixture.delivery.close();
      fixture.restore();
    }
  }
});

test("ADR-0089 exact Feishu Sender receives distinct exact placeholder and final effects", async () => {
  const sender = new ExactPrivateFeishuSenderTwin({
    applicationId: "feishu-app:private-bot",
    clock: () => now,
    credential: Buffer.from("sender-provider-secret-never-rendered"),
    destinationRef: "private-chat:42",
    mode: "applied",
    providerTenantKey: "feishu-tenant:private-bot",
  });
  const fixture = configuredDelivery({ sender });
  try {
    const turn = fixture.identityTwin.verifyQuestionTurn({
      eventVerificationRef: fixture.questionTurns[0].eventVerificationRef,
      question: fixture.questionTurns[0].question,
      turnRef: fixture.questionTurns[0].turnRef,
    });
    const outcome = await fixture.delivery.answer(turn);
    assert.equal(outcome.kind, "delivered");
    assert.equal(sender.callCount, 2);
    assert.equal(sender.effectCount, 2);
    const effects = sender.observations();
    assert.deepEqual(effects.map((effect) => effect.operation), [
      "create_placeholder",
      "finalize_reply",
    ]);
    assert.equal(new Set(effects.map((effect) => effect.payloadDigest)).size, 2);
    assert.equal(new Set(effects.map((effect) => effect.providerIdempotencyDigest)).size, 2);
    assert.equal(new Set(effects.map((effect) => effect.providerAttemptRef)).size, 2);
    assert.equal(JSON.stringify(effects).includes("sender-provider-secret"), false);
  } finally {
    await fixture.delivery.close();
    fixture.restore();
  }
});

test("ADR-0089 ambiguous Feishu Sender preserves original attempt without replacement authority", async () => {
  const sender = new ExactPrivateFeishuSenderTwin({
    applicationId: "feishu-app:private-bot",
    credential: Buffer.from("sender-provider-secret-never-rendered"),
    destinationRef: "private-chat:42",
    mode: "ambiguous",
    providerTenantKey: "feishu-tenant:private-bot",
  });
  const fixture = configuredDelivery({ sender });
  try {
    const turn = fixture.identityTwin.verifyQuestionTurn({
      eventVerificationRef: fixture.questionTurns[0].eventVerificationRef,
      question: fixture.questionTurns[0].question,
      turnRef: fixture.questionTurns[0].turnRef,
    });
    const original = await fixture.delivery.answer(turn);
    assert.equal(original.kind, "delivery_reconciliation_required");
    assert.equal(sender.callCount, 1);
    assert.equal(sender.effectCount, 1);
    assert.deepEqual(await fixture.delivery.answer(turn), original);
    assert.equal(sender.callCount, 1);
    assert.equal(fixture.action.prepares.length, 1);
  } finally {
    await fixture.delivery.close();
    fixture.restore();
  }
});
