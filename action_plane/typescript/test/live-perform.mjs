import {
  ActionPlane,
  ActionTicketKeyring,
  DeterministicPrivateSenderTwin,
  PrivateActionPrepareProfile,
} from "../dist/index.js";
import {
  createFinalizeReplyEffectIntent,
  createPlaceholderEffectIntent,
  createPrivateFollowupEffectIntent,
  createTrustedActionReconciliation,
  createTrustedPrivateEffectAuthority,
  privateAudienceDigestForBinding,
} from "../dist/internal.js";
import pg from "pg";

function requiredEnvironment(name) {
  const value = process.env[name];
  if (value === undefined || value.length === 0) {
    throw new Error(`missing live action perform input: ${name}`);
  }
  return value;
}

const organizationId = requiredEnvironment("CE_ACTION_ORGANIZATION_ID");
const userId = requiredEnvironment("CE_ACTION_USER_ID");
const membershipId = requiredEnvironment("CE_ACTION_MEMBERSHIP_ID");
const deliveryEvidenceRef = requiredEnvironment("CE_ACTION_DELIVERY_EVIDENCE_REF");
const databaseUrl = requiredEnvironment("CE_ACTION_DATABASE_URL");
const staleMutation = requiredEnvironment("CE_ACTION_STALE_MUTATION");
if (staleMutation !== "epoch" && staleMutation !== "membership") {
  throw new Error("CE_ACTION_STALE_MUTATION must be epoch or membership");
}
const referenceOffset = Number.parseInt(
  process.env.CE_ACTION_REF_OFFSET ?? "100",
  10,
);
if (!Number.isSafeInteger(referenceOffset) || referenceOffset < 1) {
  throw new Error("CE_ACTION_REF_OFFSET must be a positive safe integer");
}
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

const profile = new PrivateActionPrepareProfile({
  approvalTier: "preapproved_private_delivery_v1",
  authenticatedServiceRef: exactFacts.authenticatedServiceRef,
  consumerRef: exactFacts.consumerRef,
  maximumPayloadBytes: 4096,
  profileRef: "private-action-prepare-v1",
  purpose: exactFacts.purpose,
  retentionPolicyRef: "action-digest-audit-retention-v1",
  retentionSeconds: 2_592_000,
  ticketTtlSeconds: 60,
});
const keyring = new ActionTicketKeyring({
  activeVersion: 1,
  keys: new Map([[1, Buffer.alloc(32, 0x29)]]),
});
const pool = new pg.Pool({
  application_name: "context-engine-action-perform-integration",
  connectionString: databaseUrl,
  connectionTimeoutMillis: 5_000,
  max: 2,
  statement_timeout: 5_000,
});
const database = {
  async query(query) {
    return pool.query(query);
  },
};
const trusted = createTrustedPrivateEffectAuthority(exactFacts);
let nextDeliveryAttempt = referenceOffset;
let nextTicket = referenceOffset;
let nextProviderAttempt = referenceOffset;
let nextReceipt = referenceOffset;

function deliveryAttemptRef() {
  return `dla_${(nextDeliveryAttempt++).toString(16).padStart(32, "0")}`;
}

function plane(sender, actionDatabase = database, actionProfile = profile) {
  return new ActionPlane({
    database: actionDatabase,
    keyring,
    profile: actionProfile,
    providerAttemptRefFactory: () =>
      `pat_${(nextProviderAttempt++).toString(16).padStart(32, "0")}`,
    receiptRefFactory: () =>
      `acr_${(nextReceipt++).toString(16).padStart(32, "0")}`,
    sender,
    ticketRefFactory: () =>
      `act_${(nextTicket++).toString(16).padStart(32, "0")}`,
  });
}

const placeholderAttempt = deliveryAttemptRef();
const cases = [
  {
    attempt: placeholderAttempt,
    idempotency: "turn-68:create-placeholder",
    makeIntent: createPlaceholderEffectIntent,
    operation: "create_placeholder",
    payload: { text: "Working…" },
  },
  {
    attempt: placeholderAttempt,
    idempotency: "turn-68:finalize-reply",
    makeIntent: createFinalizeReplyEffectIntent,
    operation: "finalize_reply",
    payload: { messageRef: "message:placeholder-68", text: "Done" },
  },
  {
    attempt: deliveryAttemptRef(),
    idempotency: "turn-68:private-followup",
    makeIntent: createPrivateFollowupEffectIntent,
    operation: "send_private_followup",
    payload: { text: "More context" },
  },
];

try {
  const applied = {};
  let totalSenderCalls = 0;
  let totalEffects = 0;
  for (const operationCase of cases) {
    const sender = new DeterministicPrivateSenderTwin({ mode: "applied" });
    const actionPlane = plane(sender);
    const prepared = await actionPlane.prepare(operationCase.makeIntent(trusted, {
      deliveryAttemptRef: operationCase.attempt,
      idempotencyKey: operationCase.idempotency,
      payload: operationCase.payload,
    }));
    if (prepared.kind !== "prepared") {
      throw new Error(`${operationCase.operation} did not prepare (${prepared.kind})`);
    }
    const first = await actionPlane.perform(operationCase.payload, prepared.ticket);
    const replay = await actionPlane.perform(operationCase.payload, prepared.ticket);
    if (
      first.kind !== "applied"
      || first.effectCount !== 1
      || replay.kind !== "already_applied"
      || replay.effectCount !== 0
      || replay.receipt.receiptRef !== first.receipt.receiptRef
      || sender.callCount !== 1
      || sender.effectCount !== 1
    ) {
      throw new Error(`${operationCase.operation} exact apply/replay contract failed: ${JSON.stringify({
        first,
        replay,
        senderCalls: sender.callCount,
        senderEffects: sender.effectCount,
      })}`);
    }
    const wrongPayload = operationCase.operation === "finalize_reply"
      ? { messageRef: "message:placeholder-68", text: "wrong" }
      : { text: "wrong" };
    const wrong = await actionPlane.perform(wrongPayload, prepared.ticket);
    if (
      wrong.kind !== "rejected"
      || wrong.effectCount !== 0
      || wrong.reasonCategory !== "not_available"
      || sender.callCount !== 1
    ) {
      throw new Error(`${operationCase.operation} wrong payload reached Sender`);
    }
    totalSenderCalls += sender.callCount;
    totalEffects += sender.effectCount;
    applied[operationCase.operation] = {
      first: first.kind,
      replay: replay.kind,
      receiptRef: first.receipt.receiptRef,
      wrongPayload: wrong.kind,
      wrongPayloadReason: wrong.reasonCategory,
    };
  }

  let releaseConcurrentSender;
  const concurrentSenderGate = new Promise((resolve) => {
    releaseConcurrentSender = resolve;
  });
  const concurrentSender = new DeterministicPrivateSenderTwin({
    gate: concurrentSenderGate,
    mode: "applied",
  });
  const concurrentPlane = plane(concurrentSender);
  const concurrentPayload = { text: "Concurrent exact replay" };
  const concurrentPrepared = await concurrentPlane.prepare(
    createPrivateFollowupEffectIntent(trusted, {
      deliveryAttemptRef: deliveryAttemptRef(),
      idempotencyKey: "turn-68:concurrent-followup",
      payload: concurrentPayload,
    }),
  );
  if (concurrentPrepared.kind !== "prepared") {
    throw new Error("concurrent case did not prepare");
  }
  const firstConcurrentPerform = concurrentPlane.perform(
    concurrentPayload,
    concurrentPrepared.ticket,
  );
  while (concurrentSender.callCount === 0) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  const overlappingConcurrentPerform = await concurrentPlane.perform(
    concurrentPayload,
    concurrentPrepared.ticket,
  );
  if (overlappingConcurrentPerform.kind !== "reconciliation_required") {
    throw new Error(
      `overlapping live perform did not observe in-flight state (${overlappingConcurrentPerform.kind})`,
    );
  }
  releaseConcurrentSender();
  const firstConcurrentResult = await firstConcurrentPerform;
  const concurrentKinds = [
    firstConcurrentResult.kind,
    overlappingConcurrentPerform.kind,
  ];
  if (
    firstConcurrentResult.kind !== "applied"
    || firstConcurrentResult.effectCount !== 1
    || concurrentSender.callCount !== 1
    || concurrentSender.effectCount !== 1
  ) {
    throw new Error("concurrent exact replay was not fenced to one Sender effect");
  }

  const ambiguousSender = new DeterministicPrivateSenderTwin({ mode: "ambiguous" });
  const ambiguousPlane = plane(ambiguousSender);
  const ambiguousPayload = { text: "Ambiguous private delivery" };
  const ambiguousPrepared = await ambiguousPlane.prepare(
    createPrivateFollowupEffectIntent(trusted, {
      deliveryAttemptRef: deliveryAttemptRef(),
      idempotencyKey: "turn-68:ambiguous-followup",
      payload: ambiguousPayload,
    }),
  );
  if (ambiguousPrepared.kind !== "prepared") {
    throw new Error(`ambiguous case did not prepare (${ambiguousPrepared.kind})`);
  }
  const ambiguous = await ambiguousPlane.perform(
    ambiguousPayload,
    ambiguousPrepared.ticket,
  );
  const ambiguousReplay = await ambiguousPlane.perform(
    ambiguousPayload,
    ambiguousPrepared.ticket,
  );
  if (
    ambiguous.kind !== "reconciliation_required"
    || ambiguousReplay.kind !== "reconciliation_required"
    || ambiguousReplay.providerAttemptRef !== ambiguous.providerAttemptRef
    || ambiguousSender.callCount !== 1
  ) {
    throw new Error("ambiguous retry minted or sent a second provider attempt");
  }
  const appliedAt = new Date();
  const reconciliation = createTrustedActionReconciliation({
    appliedAt,
    disposition: "applied",
    organizationId,
    providerAttemptRef: ambiguous.providerAttemptRef,
    providerEffectDigest: "9".repeat(64),
    reconciliationRef: "operator:action-reconciliation-68",
  });
  const reconciled = await ambiguousPlane.reconcile(reconciliation);
  const reconciliationReplay = await ambiguousPlane.reconcile(reconciliation);
  const reconciledReplay = await ambiguousPlane.perform(
    ambiguousPayload,
    ambiguousPrepared.ticket,
  );
  const conflicting = await ambiguousPlane.reconcile(createTrustedActionReconciliation({
    disposition: "rejected",
    organizationId,
    providerAttemptRef: ambiguous.providerAttemptRef,
    reconciliationRef: "operator:conflicting-action-reconciliation-68",
  }));
  if (
    reconciled.kind !== "already_applied"
    || reconciliationReplay.kind !== "already_applied"
    || reconciledReplay.kind !== "already_applied"
    || conflicting.kind !== "rejected"
    || ambiguousSender.callCount !== 1
  ) {
    throw new Error("ambiguous reconciliation is not monotonic and replay-safe");
  }

  const reconcileRejectedSender = new DeterministicPrivateSenderTwin({
    mode: "ambiguous",
  });
  const reconcileRejectedPlane = plane(reconcileRejectedSender);
  const reconcileRejectedPayload = { text: "Reconcile this attempt as rejected" };
  const reconcileRejectedPrepared = await reconcileRejectedPlane.prepare(
    createPrivateFollowupEffectIntent(trusted, {
      deliveryAttemptRef: deliveryAttemptRef(),
      idempotencyKey: "turn-68:reconcile-rejected-followup",
      payload: reconcileRejectedPayload,
    }),
  );
  if (reconcileRejectedPrepared.kind !== "prepared") {
    throw new Error("reconcile-rejected case did not prepare");
  }
  const reconcileRejectedAmbiguous = await reconcileRejectedPlane.perform(
    reconcileRejectedPayload,
    reconcileRejectedPrepared.ticket,
  );
  if (reconcileRejectedAmbiguous.kind !== "reconciliation_required") {
    throw new Error("reconcile-rejected case was not ambiguous");
  }
  const rejectedReconciliation = createTrustedActionReconciliation({
    disposition: "rejected",
    organizationId,
    providerAttemptRef: reconcileRejectedAmbiguous.providerAttemptRef,
    reconciliationRef: "operator:rejected-action-reconciliation-68",
  });
  const reconciledRejected = await reconcileRejectedPlane.reconcile(
    rejectedReconciliation,
  );
  const rejectedReconciliationReplay = await reconcileRejectedPlane.reconcile(
    rejectedReconciliation,
  );
  const reconciledRejectedReplay = await reconcileRejectedPlane.perform(
    reconcileRejectedPayload,
    reconcileRejectedPrepared.ticket,
  );
  const rejectedConflict = await reconcileRejectedPlane.reconcile(
    createTrustedActionReconciliation({
      appliedAt: new Date(),
      disposition: "applied",
      organizationId,
      providerAttemptRef: reconcileRejectedAmbiguous.providerAttemptRef,
      providerEffectDigest: "7".repeat(64),
      reconciliationRef: "operator:conflicting-rejected-reconciliation-68",
    }),
  );
  if (
    reconciledRejected.kind !== "rejected"
    || rejectedReconciliationReplay.kind !== "rejected"
    || reconciledRejectedReplay.kind !== "rejected"
    || rejectedConflict.kind !== "rejected"
    || reconcileRejectedSender.callCount !== 1
  ) {
    throw new Error("rejected reconciliation is not monotonic and replay-safe");
  }

  const rejectedSender = new DeterministicPrivateSenderTwin({ mode: "rejected" });
  const rejectedPlane = plane(rejectedSender);
  const rejectedPayload = { text: "Provider rejects this private delivery" };
  const rejectedPrepared = await rejectedPlane.prepare(
    createPrivateFollowupEffectIntent(trusted, {
      deliveryAttemptRef: deliveryAttemptRef(),
      idempotencyKey: "turn-68:rejected-followup",
      payload: rejectedPayload,
    }),
  );
  if (rejectedPrepared.kind !== "prepared") {
    throw new Error("provider-rejected case did not prepare");
  }
  const rejected = await rejectedPlane.perform(rejectedPayload, rejectedPrepared.ticket);
  const rejectedReplay = await rejectedPlane.perform(
    rejectedPayload,
    rejectedPrepared.ticket,
  );
  if (
    rejected.kind !== "rejected"
    || rejected.reasonCategory !== "provider_rejected"
    || rejectedReplay.kind !== "rejected"
    || rejectedReplay.reasonCategory !== "provider_rejected"
    || rejectedSender.callCount !== 1
    || rejectedSender.effectCount !== 0
  ) {
    throw new Error("provider rejection did not become one terminal zero-effect result");
  }

  let failCompletion = true;
  const crashDatabase = {
    async query(query) {
      if (
        failCompletion
        && query.text.includes("context_action_complete_private_effect")
      ) {
        failCompletion = false;
        throw new Error("simulated process loss after Sender response");
      }
      return pool.query(query);
    },
  };
  const crashSender = new DeterministicPrivateSenderTwin({ mode: "applied" });
  const crashPlane = plane(crashSender, crashDatabase);
  const crashPayload = { text: "Crash after provider response" };
  const crashPrepared = await crashPlane.prepare(createPrivateFollowupEffectIntent(trusted, {
    deliveryAttemptRef: deliveryAttemptRef(),
    idempotencyKey: "turn-68:crash-followup",
    payload: crashPayload,
  }));
  if (crashPrepared.kind !== "prepared") {
    throw new Error("crash case did not prepare");
  }
  const crashed = await crashPlane.perform(crashPayload, crashPrepared.ticket);
  const crashReplay = await crashPlane.perform(crashPayload, crashPrepared.ticket);
  if (
    crashed.kind !== "reconciliation_required"
    || crashReplay.kind !== "reconciliation_required"
    || crashed.providerAttemptRef !== crashReplay.providerAttemptRef
    || crashSender.callCount !== 1
  ) {
    throw new Error("post-Sender crash did not fence retry under the original attempt");
  }
  const crashReconciled = await crashPlane.reconcile(createTrustedActionReconciliation({
    appliedAt: new Date(),
    disposition: "applied",
    organizationId,
    providerAttemptRef: crashed.providerAttemptRef,
    providerEffectDigest: "8".repeat(64),
    reconciliationRef: "operator:crash-action-reconciliation-68",
  }));
  if (crashReconciled.kind !== "already_applied" || crashSender.callCount !== 1) {
    throw new Error("post-Sender crash could not reconcile under the original attempt");
  }

  const mutationMatrix = {};
  const mutationSpecs = {
    approval: (values) => { values[7] = Buffer.alloc(32, 0x31); },
    attempt: (values) => { values[2] = deliveryAttemptRef(); },
    audience: (values) => { values[13] = Buffer.alloc(32, 0x32); },
    destination: (values) => { values[12] = Buffer.alloc(32, 0x33); },
    epoch: (values) => { values[8] += 1; },
    idempotency: (values) => { values[6] = Buffer.alloc(32, 0x34); },
    operation: (values) => { values[3] = "finalize_reply"; },
    organization: (values) => {
      values[0] = "00000000-0000-4000-8000-000000000068";
    },
    payload: (values) => { values[5] = Buffer.alloc(32, 0x35); },
    service: (values) => { values[11] = Buffer.alloc(32, 0x36); },
  };
  for (const [name, mutate] of Object.entries(mutationSpecs)) {
    const sender = new DeterministicPrivateSenderTwin({ mode: "applied" });
    const hostileDatabase = {
      async query(query) {
        if (!query.text.includes("context_action_begin_private_effect")) {
          return pool.query(query);
        }
        const values = [...query.values];
        mutate(values);
        return pool.query({ ...query, values });
      },
    };
    const actionPlane = plane(sender, hostileDatabase);
    const payload = { text: `Mutation ${name}` };
    const prepared = await actionPlane.prepare(createPrivateFollowupEffectIntent(trusted, {
      deliveryAttemptRef: deliveryAttemptRef(),
      idempotencyKey: `turn-68:mutation-${name}`,
      payload,
    }));
    if (prepared.kind !== "prepared") {
      throw new Error(`mutation ${name} did not prepare`);
    }
    const outcome = await actionPlane.perform(payload, prepared.ticket);
    if (
      outcome.kind !== "rejected"
      || outcome.effectCount !== 0
      || outcome.reasonCategory !== "not_available"
      || sender.callCount !== 0
    ) {
      throw new Error(`mutation ${name} reached Sender`);
    }
    mutationMatrix[name] = `${outcome.kind}:${outcome.reasonCategory}`;
  }

  const expiryProfile = new PrivateActionPrepareProfile({
    approvalTier: "preapproved_private_delivery_v1",
    authenticatedServiceRef: exactFacts.authenticatedServiceRef,
    consumerRef: exactFacts.consumerRef,
    maximumPayloadBytes: 4096,
    profileRef: "private-action-prepare-v1",
    purpose: exactFacts.purpose,
    retentionPolicyRef: "action-digest-audit-retention-v1",
    retentionSeconds: 2_592_000,
    ticketTtlSeconds: 1,
  });
  const expirySender = new DeterministicPrivateSenderTwin({ mode: "applied" });
  const expiryPlane = plane(expirySender, database, expiryProfile);
  const expiryPayload = { text: "This ticket must expire before Sender" };
  const expiryPrepared = await expiryPlane.prepare(createPrivateFollowupEffectIntent(trusted, {
    deliveryAttemptRef: deliveryAttemptRef(),
    idempotencyKey: "turn-68:expired-followup",
    payload: expiryPayload,
  }));
  if (expiryPrepared.kind !== "prepared") {
    throw new Error("expiry case did not prepare");
  }
  await new Promise((resolve) => setTimeout(resolve, 1_100));
  const expired = await expiryPlane.perform(expiryPayload, expiryPrepared.ticket);
  if (
    expired.kind !== "rejected"
    || expired.effectCount !== 0
    || expired.reasonCategory !== "not_available"
    || expirySender.callCount !== 0
  ) {
    throw new Error("expired ticket reached Sender");
  }

  const staleSender = new DeterministicPrivateSenderTwin({ mode: "applied" });
  const stalePlane = plane(staleSender);
  const stalePayload = { text: "Must be rejected after epoch change" };
  const stalePrepared = await stalePlane.prepare(createPrivateFollowupEffectIntent(trusted, {
    deliveryAttemptRef: deliveryAttemptRef(),
    idempotencyKey: "turn-68:stale-epoch-followup",
    payload: stalePayload,
  }));
  if (stalePrepared.kind !== "prepared") {
    throw new Error("stale-epoch case did not prepare");
  }
  process.stdout.write(
    staleMutation === "epoch"
      ? "READY_FOR_EPOCH_CHANGE\n"
      : "READY_FOR_MEMBERSHIP_CHANGE\n",
  );
  await new Promise((resolve, reject) => {
    process.stdin.once("data", resolve);
    process.stdin.once("error", reject);
  });
  const stale = await stalePlane.perform(stalePayload, stalePrepared.ticket);
  if (
    stale.kind !== "rejected"
    || stale.effectCount !== 0
    || stale.reasonCategory !== "not_available"
    || staleSender.callCount !== 0
  ) {
    throw new Error("stale Policy Epoch reached Sender");
  }

  process.stdout.write(JSON.stringify({
    ambiguous: {
      first: ambiguous.kind,
      providerAttemptRef: ambiguous.providerAttemptRef,
      reconcile: reconciled.kind,
      reconciliationReplay: reconciliationReplay.kind,
      replay: ambiguousReplay.kind,
      senderCalls: ambiguousSender.callCount,
      terminalReplay: reconciledReplay.kind,
    },
    applied,
    crash: {
      first: crashed.kind,
      reconcile: crashReconciled.kind,
      replay: crashReplay.kind,
      senderCalls: crashSender.callCount,
    },
    concurrent: {
      outcomes: concurrentKinds,
      senderCalls: concurrentSender.callCount,
      senderEffects: concurrentSender.effectCount,
    },
    expired: {
      outcome: expired.kind,
      reasonCategory: expired.reasonCategory,
      senderCalls: expirySender.callCount,
    },
    mutationMatrix,
    rejected: {
      first: rejected.kind,
      firstReasonCategory: rejected.reasonCategory,
      replay: rejectedReplay.kind,
      replayReasonCategory: rejectedReplay.reasonCategory,
      senderCalls: rejectedSender.callCount,
      senderEffects: rejectedSender.effectCount,
    },
    reconciledRejected: {
      conflict: rejectedConflict.kind,
      first: reconcileRejectedAmbiguous.kind,
      reconcile: reconciledRejected.kind,
      reconciliationReplay: rejectedReconciliationReplay.kind,
      replay: reconciledRejectedReplay.kind,
      senderCalls: reconcileRejectedSender.callCount,
    },
    senderCalls: totalSenderCalls,
    senderEffects: totalEffects,
    stale: {
      outcome: stale.kind,
      reasonCategory: stale.reasonCategory,
      senderCalls: staleSender.callCount,
    },
  }));
} finally {
  await pool.end();
}
