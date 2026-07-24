import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ActionPlane,
  ActionTicketKeyring,
  CreatePlaceholderActionTicket,
  createTrustedActionReconciliation,
  DeterministicPrivateSenderTwin,
  FinalizeReplyActionTicket,
  PrivateActionPrepareProfile,
} from "../dist/index.js";
import {
  actionPayloadDigest,
  createPlaceholderEffectIntent,
  createTrustedPrivateEffectAuthority,
  inspectPreparedActionTicket,
} from "../dist/internal.js";

const exactFacts = {
  audienceDigest: "a".repeat(64),
  authenticatedServiceRef: "service:bot-delivery",
  authenticationBindingRef: "binding:bot-delivery",
  consumerRef: "consumer:bot-delivery",
  deliveryEvidenceRef: `der_${"b".repeat(64)}`,
  destinationRef: "private-chat:42",
  membershipId: "9c9e9f4c-a5ec-4417-9408-0346e1c6c998",
  membershipVersion: 7,
  organizationId: "81e18bca-86a1-478a-937d-7675c6fe69b0",
  policyEpoch: 11,
  purpose: "context.answer",
  userId: "d3d9893f-82d2-4890-8cb2-4c7e57a56f16",
};

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
  keys: new Map([[1, Buffer.alloc(32, 0x11)]]),
});

function sessionDatabase(query) {
  return {
    query,
    async connect() {
      return {
        async query(config) {
          if (config.text.includes("pg_advisory_unlock")) {
            return { rows: [{ unlocked: true }] };
          }
          return query(config);
        },
        release() {},
      };
    },
  };
}

test("RFC 8785 payload binding has an independent known digest", () => {
  assert.equal(
    actionPayloadDigest("create_placeholder", { text: "Working…" }),
    "a7b1734f5419699728721a4482c1d020be048e1899ed4b6dd40b7a8e9f8d8ebe",
  );
});

test("prepare returns one signed create ticket and has no Sender seam", async () => {
  const observed = [];
  const database = sessionDatabase(async (query) => {
      observed.push(query);
      return {
        rows: [
          {
            delivery_attempt_ref: `dla_${"c".repeat(32)}`,
            expires_at: new Date("2026-07-24T08:01:00.000Z"),
            idempotent: false,
            issued_at: new Date("2026-07-24T08:00:00.000Z"),
            outcome: "prepared",
            ticket_ref: `act_${"d".repeat(32)}`,
          },
        ],
      };
  });
  const plane = new ActionPlane({
    database,
    keyring,
    profile,
  });
  const trusted = createTrustedPrivateEffectAuthority(exactFacts);
  const intent = createPlaceholderEffectIntent(trusted, {
    deliveryAttemptRef: `dla_${"c".repeat(32)}`,
    idempotencyKey: "turn-42:create-placeholder",
    payload: { text: "Working…" },
  });

  const outcome = await plane.prepare(intent);

  assert.equal(outcome.kind, "prepared");
  assert.equal(outcome.effectCount, 0);
  assert.equal(outcome.operation, "create_placeholder");
  assert.equal(outcome.deliveryAttemptRef, `dla_${"c".repeat(32)}`);
  assert.equal(observed.length, 1);
  assert.equal("sender" in plane, false);
  const claims = inspectPreparedActionTicket(outcome.ticket, keyring);
  assert.equal(claims.type, "CE-CreatePlaceholderActionTicket");
  assert.equal(claims.operation, "create_placeholder");
  assert.equal(claims.destinationDigest.length, 64);
  assert.equal(claims.payloadDigest, actionPayloadDigest(
    "create_placeholder",
    { text: "Working…" },
  ));
  assert.equal(outcome.ticket.toString(), "<ActionTicket redacted>");
  assert.equal(outcome.ticket.serialize().includes(exactFacts.destinationRef), false);
});

test("perform invokes Sender once and exact replay returns the stored receipt", async () => {
  let executionState = "prepared";
  const receiptRow = {
    applied_at: new Date("2099-07-24T08:00:02.000Z"),
    audience_digest: exactFacts.audienceDigest,
    delivery_attempt_ref: `dla_${"e".repeat(32)}`,
    destination_digest: "1".repeat(64),
    idempotency_digest: "2".repeat(64),
    operation: "create_placeholder",
    organization_id: exactFacts.organizationId,
    payload_digest: actionPayloadDigest("create_placeholder", { text: "Working…" }),
    provider_attempt_ref: `pat_${"3".repeat(32)}`,
    provider_effect_digest: "4".repeat(64),
    receipt_ref: `acr_${"5".repeat(32)}`,
    ticket_ref: `act_${"6".repeat(32)}`,
  };
  const database = sessionDatabase(async (query) => {
      if (query.text.includes("context_action_prepare_private_effect")) {
        return {
          rows: [{
            delivery_attempt_ref: receiptRow.delivery_attempt_ref,
            expires_at: new Date("2099-07-24T08:01:00.000Z"),
            idempotent: false,
            issued_at: new Date("2099-07-24T08:00:00.000Z"),
            outcome: "prepared",
            ticket_ref: receiptRow.ticket_ref,
          }],
        };
      }
      if (query.text.includes("context_action_begin_private_effect")) {
        if (executionState === "prepared") {
          executionState = "in_flight";
          return { rows: [{
            destination_ref: exactFacts.destinationRef,
            outcome: "sender_required",
            provider_attempt_ref: receiptRow.provider_attempt_ref,
          }] };
        }
        return { rows: [{ outcome: "already_applied", ...receiptRow }] };
      }
      if (query.text.includes("context_action_complete_private_effect")) {
        executionState = "applied";
        return { rows: [{ outcome: "applied", ...receiptRow }] };
      }
      throw new Error("unexpected ActionPlane query");
  });
  const sender = new DeterministicPrivateSenderTwin({ mode: "applied" });
  const plane = new ActionPlane({
    database,
    keyring,
    profile,
    providerAttemptRefFactory: () => receiptRow.provider_attempt_ref,
    sender,
  });
  const trusted = createTrustedPrivateEffectAuthority(exactFacts);
  const payload = { text: "Working…" };
  const prepared = await plane.prepare(createPlaceholderEffectIntent(trusted, {
    deliveryAttemptRef: receiptRow.delivery_attempt_ref,
    idempotencyKey: "turn-68:create-placeholder",
    payload,
  }));
  assert.equal(prepared.kind, "prepared");
  const preparedClaims = inspectPreparedActionTicket(prepared.ticket, keyring);
  receiptRow.destination_digest = preparedClaims.destinationDigest;
  receiptRow.idempotency_digest = preparedClaims.idempotencyDigest;

  const applied = await plane.perform(payload, prepared.ticket);
  const replay = await plane.perform(payload, prepared.ticket);

  assert.equal(applied.kind, "applied");
  assert.equal(applied.effectCount, 1);
  assert.equal(applied.receipt.providerAttemptRef, receiptRow.provider_attempt_ref);
  assert.equal(applied.receipt.providerEffectDigest, receiptRow.provider_effect_digest);
  assert.equal(replay.kind, "already_applied");
  assert.deepEqual(replay.receipt, applied.receipt);
  assert.equal(replay.effectCount, 0);
  assert.equal(sender.callCount, 1);
  assert.equal(sender.effectCount, 1);
});

test("pre-Sender authority rejection is generic while terminal provider rejection is explicit", async () => {
  let beginOutcome = "rejected";
  const database = sessionDatabase(async (query) => {
      if (query.text.includes("context_action_prepare_private_effect")) {
        return {
          rows: [{
            delivery_attempt_ref: `dla_${"a".repeat(32)}`,
            expires_at: new Date("2099-07-24T08:01:00.000Z"),
            idempotent: false,
            issued_at: new Date("2099-07-24T08:00:00.000Z"),
            outcome: "prepared",
            ticket_ref: `act_${"b".repeat(32)}`,
          }],
        };
      }
      if (query.text.includes("context_action_begin_private_effect")) {
        return { rows: [{ outcome: beginOutcome }] };
      }
      throw new Error("rejected begin must not reach another database seam");
  });
  const sender = new DeterministicPrivateSenderTwin({ mode: "applied" });
  const plane = new ActionPlane({ database, keyring, profile, sender });
  const payload = { text: "Reject before Sender" };
  const prepared = await plane.prepare(createPlaceholderEffectIntent(
    createTrustedPrivateEffectAuthority(exactFacts),
    {
      deliveryAttemptRef: `dla_${"a".repeat(32)}`,
      idempotencyKey: "turn-68:rejection-category",
      payload,
    },
  ));
  assert.equal(prepared.kind, "prepared");

  const generic = await plane.perform(payload, prepared.ticket);
  assert.deepEqual(generic, {
    effectCount: 0,
    kind: "rejected",
    reasonCategory: "not_available",
  });
  assert.equal(sender.callCount, 0);

  beginOutcome = "provider_rejected";
  const terminal = await plane.perform(payload, prepared.ticket);
  assert.deepEqual(terminal, {
    effectCount: 0,
    kind: "rejected",
    reasonCategory: "provider_rejected",
  });
  assert.equal(sender.callCount, 0);
});

test("begin query failure discards its dedicated session before Sender", async () => {
  const releases = [];
  const database = {
    async connect() {
      return {
        async query(query) {
          if (query.text.includes("context_action_begin_private_effect")) {
            throw new Error("indeterminate post-lock begin failure");
          }
          throw new Error("begin failure reached an unexpected session query");
        },
        release(discard = false) {
          releases.push(discard);
        },
      };
    },
    async query(query) {
      assert.match(query.text, /context_action_prepare_private_effect/);
      return { rows: [{
        delivery_attempt_ref: `dla_${"f".repeat(32)}`,
        expires_at: new Date("2099-07-24T08:01:00.000Z"),
        idempotent: false,
        issued_at: new Date("2099-07-24T08:00:00.000Z"),
        outcome: "prepared",
        ticket_ref: `act_${"e".repeat(32)}`,
      }] };
    },
  };
  const sender = new DeterministicPrivateSenderTwin({ mode: "applied" });
  const plane = new ActionPlane({ database, keyring, profile, sender });
  const payload = { text: "Fail begin after an unknown lock state" };
  const prepared = await plane.prepare(createPlaceholderEffectIntent(
    createTrustedPrivateEffectAuthority(exactFacts),
    {
      deliveryAttemptRef: `dla_${"f".repeat(32)}`,
      idempotencyKey: "turn-68:discard-failed-begin",
      payload,
    },
  ));
  assert.equal(prepared.kind, "prepared");

  const outcome = await plane.perform(payload, prepared.ticket);

  assert.deepEqual(outcome, {
    effectCount: 0,
    kind: "rejected",
    reasonCategory: "not_available",
  });
  assert.deepEqual(releases, [true]);
  assert.equal(sender.callCount, 0);
});

test("reconciliation cannot race an in-process Sender call", async () => {
  let releaseSender;
  const gate = new Promise((resolve) => { releaseSender = resolve; });
  let state = "prepared";
  let reconcileQueries = 0;
  const receiptRow = {
    applied_at: new Date("2099-07-24T08:00:02.000Z"),
    audience_digest: exactFacts.audienceDigest,
    delivery_attempt_ref: `dla_${"c".repeat(32)}`,
    destination_digest: "1".repeat(64),
    idempotency_digest: "2".repeat(64),
    operation: "create_placeholder",
    organization_id: exactFacts.organizationId,
    payload_digest: actionPayloadDigest("create_placeholder", { text: "Race fence" }),
    provider_attempt_ref: `pat_${"3".repeat(32)}`,
    provider_effect_digest: "4".repeat(64),
    receipt_ref: `acr_${"5".repeat(32)}`,
    ticket_ref: `act_${"6".repeat(32)}`,
  };
  const database = sessionDatabase(async (query) => {
      if (query.text.includes("context_action_prepare_private_effect")) {
        return { rows: [{
          delivery_attempt_ref: receiptRow.delivery_attempt_ref,
          expires_at: new Date("2099-07-24T08:01:00.000Z"),
          idempotent: false,
          issued_at: new Date("2099-07-24T08:00:00.000Z"),
          outcome: "prepared",
          ticket_ref: receiptRow.ticket_ref,
        }] };
      }
      if (query.text.includes("context_action_begin_private_effect")) {
        return state === "prepared"
          ? { rows: [{
              destination_ref: exactFacts.destinationRef,
              outcome: "sender_required",
              provider_attempt_ref: receiptRow.provider_attempt_ref,
            }] }
          : { rows: [{ outcome: "already_applied", ...receiptRow }] };
      }
      if (query.text.includes("context_action_complete_private_effect")) {
        state = "applied";
        return { rows: [{ outcome: "applied", ...receiptRow }] };
      }
      if (query.text.includes("context_action_reconcile_private_effect")) {
        reconcileQueries += 1;
      }
      throw new Error("unexpected action race query");
  });
  const sender = new DeterministicPrivateSenderTwin({ gate, mode: "applied" });
  const plane = new ActionPlane({
    database,
    keyring,
    profile,
    providerAttemptRefFactory: () => receiptRow.provider_attempt_ref,
    sender,
  });
  const payload = { text: "Race fence" };
  const prepared = await plane.prepare(createPlaceholderEffectIntent(
    createTrustedPrivateEffectAuthority(exactFacts),
    {
      deliveryAttemptRef: receiptRow.delivery_attempt_ref,
      idempotencyKey: "turn-68:reconciliation-race",
      payload,
    },
  ));
  assert.equal(prepared.kind, "prepared");
  const claims = inspectPreparedActionTicket(prepared.ticket, keyring);
  receiptRow.destination_digest = claims.destinationDigest;
  receiptRow.idempotency_digest = claims.idempotencyDigest;

  const pendingPerform = plane.perform(payload, prepared.ticket);
  while (sender.callCount === 0) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  const premature = await plane.reconcile(createTrustedActionReconciliation({
    disposition: "rejected",
    organizationId: exactFacts.organizationId,
    providerAttemptRef: receiptRow.provider_attempt_ref,
    reconciliationRef: "operator:premature-reconciliation",
  }));
  assert.deepEqual(premature, {
    effectCount: 0,
    kind: "rejected",
    reasonCategory: "not_available",
  });
  assert.equal(reconcileQueries, 0);

  releaseSender();
  const applied = await pendingPerform;
  const replay = await plane.perform(payload, prepared.ticket);
  assert.equal(applied.kind, "applied");
  assert.equal(replay.kind, "already_applied");
  assert.deepEqual(replay.receipt, applied.receipt);
  assert.equal(sender.callCount, 1);
});

test("perform rejects absent Sender, tamper, and cross-kind use before database work", async () => {
  const observed = [];
  const database = {
    async query(query) {
      observed.push(query);
      if (!query.text.includes("context_action_prepare_private_effect")) {
        throw new Error("perform reached the database before local validation");
      }
      return {
        rows: [{
          delivery_attempt_ref: `dla_${"7".repeat(32)}`,
          expires_at: new Date("2099-07-24T08:01:00.000Z"),
          idempotent: false,
          issued_at: new Date("2099-07-24T08:00:00.000Z"),
          outcome: "prepared",
          ticket_ref: `act_${"8".repeat(32)}`,
        }],
      };
    },
  };
  const plane = new ActionPlane({ database, keyring, profile });
  const trusted = createTrustedPrivateEffectAuthority(exactFacts);
  const payload = { text: "Working…" };
  const prepared = await plane.prepare(createPlaceholderEffectIntent(trusted, {
    deliveryAttemptRef: `dla_${"7".repeat(32)}`,
    idempotencyKey: "turn-68:local-rejections",
    payload,
  }));
  assert.equal(prepared.kind, "prepared");

  const serialized = prepared.ticket.serialize();
  const segments = serialized.split(".");
  segments[2] = `${segments[2][0] === "A" ? "B" : "A"}${segments[2].slice(1)}`;
  const tampered = Object.create(Object.getPrototypeOf(prepared.ticket));
  Object.defineProperty(tampered, "serialize", { value: () => segments.join(".") });
  const wrongKind = Object.create(FinalizeReplyActionTicket.prototype);
  Object.defineProperty(wrongKind, "serialize", { value: () => serialized });

  for (const ticket of [prepared.ticket, tampered, wrongKind, {}]) {
    const outcome = await plane.perform(payload, ticket);
    assert.deepEqual(outcome, {
      effectCount: 0,
      kind: "rejected",
      reasonCategory: "not_available",
    });
  }
  assert.equal(observed.length, 1);
});

test("plain objects and caller wire values cannot become trusted intents", async () => {
  const database = { query: async () => ({ rows: [] }) };
  const plane = new ActionPlane({
    database,
    keyring,
    profile,
  });
  for (const untrusted of [
    {},
    { action_required: true },
    { kind: "resolved", package: { organizationRef: "caller-authored" } },
    { text: "model output" },
  ]) {
    await assert.rejects(
      plane.prepare(untrusted),
      /TrustedEffectIntent/,
    );
  }
});

test("public package exposes no lower-level prepare authority", async () => {
  const publicApi = await import("../dist/index.js");

  assert.equal("PostgresActionPrepareAuthority" in publicApi, false);
});

test("ActionPlane rejects arbitrary or inherited Sender implementations", () => {
  assert.throws(
    () => new ActionPlane({
      database: { query: async () => ({ rows: [] }) },
      keyring,
      profile,
      sender: { send: async () => ({ kind: "applied" }) },
    }),
    /deterministic private Sender twin/,
  );
  class NetworkSender extends DeterministicPrivateSenderTwin {
    async send() {
      throw new Error("must never be reachable");
    }
  }
  assert.throws(
    () => new ActionPlane({
      database: { query: async () => ({ rows: [] }) },
      keyring,
      profile,
      sender: new NetworkSender({ mode: "applied" }),
    }),
    /deterministic private Sender twin/,
  );
});

test("exported ticket nominal types cannot mint effect authority", () => {
  assert.throws(
    () => new CreatePlaceholderActionTicket("caller-authored"),
    /issuer-constructed/,
  );
});
