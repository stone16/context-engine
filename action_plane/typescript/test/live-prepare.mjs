import {
  ActionPlane,
  ActionTicketKeyring,
  PrivateActionPrepareProfile,
} from "../dist/index.js";
import {
  createFinalizeReplyEffectIntent,
  createPlaceholderEffectIntent,
  createPrivateFollowupEffectIntent,
  createTrustedPrivateEffectAuthority,
  inspectPreparedActionTicket,
  privateAudienceDigestForBinding,
} from "../dist/internal.js";
import pg from "pg";
import { randomUUID } from "node:crypto";

function requiredEnvironment(name) {
  const value = process.env[name];
  if (value === undefined || value.length === 0) {
    throw new Error(`missing live action test input: ${name}`);
  }
  return value;
}

function mutateFacts(facts, field, otherOrganizationId) {
  switch (field) {
    case "service":
      return { ...facts, authenticatedServiceRef: `${facts.authenticatedServiceRef}-wrong` };
    case "binding":
      return { ...facts, authenticationBindingRef: `${facts.authenticationBindingRef}-wrong` };
    case "destination":
      return { ...facts, destinationRef: `${facts.destinationRef}-wrong` };
    case "consumer":
      return { ...facts, consumerRef: `${facts.consumerRef}-wrong` };
    case "purpose":
      return { ...facts, purpose: `${facts.purpose}-wrong` };
    case "audience":
      return { ...facts, audienceDigest: "f".repeat(64) };
    case "epoch":
      return { ...facts, policyEpoch: facts.policyEpoch + 1 };
    case "membership_version":
      return { ...facts, membershipVersion: facts.membershipVersion + 1 };
    case "organization":
      return { ...facts, organizationId: otherOrganizationId };
    default:
      throw new Error("unknown live mutation");
  }
}

const organizationId = requiredEnvironment("CE_ACTION_ORGANIZATION_ID");
const otherOrganizationId = requiredEnvironment("CE_ACTION_OTHER_ORGANIZATION_ID");
const userId = requiredEnvironment("CE_ACTION_USER_ID");
const membershipId = requiredEnvironment("CE_ACTION_MEMBERSHIP_ID");
const deliveryEvidenceRef = requiredEnvironment("CE_ACTION_DELIVERY_EVIDENCE_REF");
const expiredDeliveryEvidenceRef = requiredEnvironment("CE_ACTION_EXPIRED_EVIDENCE_REF");
const sourceId = requiredEnvironment("CE_ACTION_SOURCE_ID");
const sourceVersionId = requiredEnvironment("CE_ACTION_SOURCE_VERSION_ID");
const expectedActiveSource = process.env.CE_ACTION_EXPECT_ACTIVE_SOURCE ?? "prepared";
const databaseUrl = requiredEnvironment("CE_ACTION_DATABASE_URL");
const exactFacts = {
  authenticatedServiceRef: "application:private-bot",
  authenticationBindingRef: "binding:private-bot",
  consumerRef: "consumer:private-bot",
  deliveryEvidenceRef,
  destinationRef: "private-chat:same-label",
  membershipId,
  membershipVersion: 1,
  organizationId,
  policyEpoch: 1,
  purpose: "context.answer",
  userId,
};
exactFacts.audienceDigest = privateAudienceDigestForBinding(exactFacts);

const profileOptions = {
  approvalTier: "preapproved_private_delivery_v1",
  authenticatedServiceRef: exactFacts.authenticatedServiceRef,
  consumerRef: exactFacts.consumerRef,
  maximumPayloadBytes: 4096,
  profileRef: "private-action-prepare-v1",
  purpose: exactFacts.purpose,
  retentionPolicyRef: "action-digest-audit-retention-v1",
  retentionSeconds: 2_592_000,
  ticketTtlSeconds: 60,
};
const keyring = new ActionTicketKeyring({
  activeVersion: 1,
  keys: new Map([[1, Buffer.alloc(32, 0x29)]]),
});
const pool = new pg.Pool({
  application_name: "context-engine-action-prepare-integration",
  connectionString: databaseUrl,
  connectionTimeoutMillis: 5_000,
  max: 1,
  statement_timeout: 5_000,
});
let nextTicket = 1;
function plane(profile = profileOptions) {
  return new ActionPlane({
    database: pool,
    keyring,
    profile: new PrivateActionPrepareProfile(profile),
    ticketRefFactory: () => `act_${(nextTicket++).toString(16).padStart(32, "0")}`,
  });
}

const nominalPlane = plane();
const trusted = createTrustedPrivateEffectAuthority(exactFacts);
const placeholderIntent = createPlaceholderEffectIntent(trusted, {
  deliveryAttemptRef: `dla_${"1".repeat(32)}`,
  idempotencyKey: "turn-67:create-placeholder",
  payload: { text: "Working…" },
});

try {
  const first = await nominalPlane.prepare(placeholderIntent);
  const retry = await nominalPlane.prepare(placeholderIntent);
  if (first.kind !== "prepared" || retry.kind !== "prepared") {
    throw new Error(
      `nominal prepare did not produce Prepared (${first.kind}, ${retry.kind})`,
    );
  }
  if (first.ticket.serialize() !== retry.ticket.serialize() || retry.idempotent !== true) {
    throw new Error("exact retry did not return the same logical ticket");
  }

  const finalize = await nominalPlane.prepare(createFinalizeReplyEffectIntent(trusted, {
    deliveryAttemptRef: `dla_${"1".repeat(32)}`,
    idempotencyKey: "turn-67:finalize-reply",
    payload: { messageRef: "message:placeholder-67", text: "Done" },
  }));
  const followup = await nominalPlane.prepare(createPrivateFollowupEffectIntent(trusted, {
    deliveryAttemptRef: `dla_${"2".repeat(32)}`,
    idempotencyKey: "turn-67:private-followup",
    payload: { text: "More context" },
  }));
  if (finalize.kind !== "prepared" || followup.kind !== "prepared") {
    throw new Error("closed operation variants did not prepare");
  }
  const types = [first, finalize, followup].map((outcome) =>
    inspectPreparedActionTicket(outcome.ticket, keyring).type
  );
  if (new Set(types).size !== 3) {
    throw new Error("operation-specific ticket types are not distinct");
  }

  const operationCases = [
    {
      conflictPayload: { text: "Conflicting placeholder" },
      deliveryAttemptRef: `dla_${"1".repeat(32)}`,
      idempotencyKey: "turn-67:create-placeholder",
      makeIntent: createPlaceholderEffectIntent,
      operation: "create_placeholder",
      payload: { text: "Working…" },
    },
    {
      conflictPayload: { messageRef: "message:placeholder-67", text: "Conflicting final" },
      deliveryAttemptRef: `dla_${"1".repeat(32)}`,
      idempotencyKey: "turn-67:finalize-reply",
      makeIntent: createFinalizeReplyEffectIntent,
      operation: "finalize_reply",
      payload: { messageRef: "message:placeholder-67", text: "Done" },
    },
    {
      conflictPayload: { text: "Conflicting follow-up" },
      deliveryAttemptRef: `dla_${"2".repeat(32)}`,
      idempotencyKey: "turn-67:private-followup",
      makeIntent: createPrivateFollowupEffectIntent,
      operation: "send_private_followup",
      payload: { text: "More context" },
    },
  ];
  const payloadConflicts = {};
  for (const operationCase of operationCases) {
    const conflict = await nominalPlane.prepare(operationCase.makeIntent(trusted, {
      deliveryAttemptRef: operationCase.deliveryAttemptRef,
      idempotencyKey: operationCase.idempotencyKey,
      payload: operationCase.conflictPayload,
    }));
    if (conflict.kind !== "generic_denied" || conflict.effectCount !== 0) {
      throw new Error(`${operationCase.operation} payload conflict did not fail closed`);
    }
    payloadConflicts[operationCase.operation] = conflict.kind;
  }

  const denied = {};
  let matrixEffectCount = 0;
  let mutationNumber = 10;
  for (const operationCase of operationCases) {
    denied[operationCase.operation] = {};
    for (const field of [
      "organization",
      "service",
      "binding",
      "destination",
      "consumer",
      "purpose",
      "audience",
      "epoch",
      "membership_version",
    ]) {
      const mutation = mutateFacts(exactFacts, field, otherOrganizationId);
      if (field === "audience") {
        mutation.audienceDigest = "f".repeat(64);
      } else {
        mutation.audienceDigest = privateAudienceDigestForBinding(mutation);
      }
      const outcome = await nominalPlane.prepare(operationCase.makeIntent(
        createTrustedPrivateEffectAuthority(mutation),
        {
          deliveryAttemptRef: `dla_${(mutationNumber++).toString(16).padStart(32, "0")}`,
          idempotencyKey: `turn-67:${operationCase.operation}:wrong-${field}`,
          payload: operationCase.payload,
        },
      ));
      if (![
        "generic_denied",
        "audience_changed",
        "retryable_unavailable",
      ].includes(outcome.kind) || outcome.effectCount !== 0) {
        throw new Error(
          `${operationCase.operation} wrong ${field} did not return a closed refusal`,
        );
      }
      matrixEffectCount += outcome.effectCount;
      denied[operationCase.operation][field] = outcome.kind;
    }
  }

  const approvalOutcome = await plane({
    ...profileOptions,
    approvalTier: "approval:wrong",
  }).prepare(placeholderIntent);
  if (approvalOutcome.kind !== "generic_denied") {
    throw new Error("wrong approval tier did not fail closed");
  }

  const expiredFacts = {
    ...exactFacts,
    deliveryEvidenceRef: expiredDeliveryEvidenceRef,
  };
  const expired = await nominalPlane.prepare(createPlaceholderEffectIntent(
    createTrustedPrivateEffectAuthority(expiredFacts),
    {
      deliveryAttemptRef: `dla_${"3".repeat(32)}`,
      idempotencyKey: "turn-67:expired-evidence",
      payload: { text: "Working…" },
    },
  ));
  if (expired.kind !== "generic_denied" || expired.effectCount !== 0) {
    throw new Error("expired evidence did not fail closed");
  }

  const staleSource = await nominalPlane.prepare(createPlaceholderEffectIntent(trusted, {
    deliveryAttemptRef: `dla_${"4".repeat(32)}`,
    idempotencyKey: "turn-67:stale-source",
    payload: { text: "Working…" },
    sourceContext: { sourceRef: sourceId, sourceVersionRef: randomUUID() },
  }));
  const activeSource = await nominalPlane.prepare(createPlaceholderEffectIntent(trusted, {
    deliveryAttemptRef: `dla_${"5".repeat(32)}`,
    idempotencyKey: "turn-67:active-source",
    payload: { text: "Working…" },
    sourceContext: { sourceRef: sourceId, sourceVersionRef: sourceVersionId },
  }));
  if (
    staleSource.kind !== "generic_denied"
    || activeSource.kind !== expectedActiveSource
  ) {
    throw new Error("source context revalidation is not exact");
  }

  process.stdout.write(JSON.stringify({
    denied,
    distinctTicketTypes: types,
    effectCount: first.effectCount,
    exactRetryIdempotent: retry.idempotent,
    expiredEvidence: expired.kind,
    matrixEffectCount,
    operation: first.operation,
    payloadConflicts,
    prepared: first.kind,
    sourceContext: { active: activeSource.kind, stale: staleSource.kind },
  }));
} finally {
  await pool.end();
}
