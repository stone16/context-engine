import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ActionPlane,
  ActionTicketKeyring,
  CreatePlaceholderActionTicket,
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

test("RFC 8785 payload binding has an independent known digest", () => {
  assert.equal(
    actionPayloadDigest("create_placeholder", { text: "Working…" }),
    "a7b1734f5419699728721a4482c1d020be048e1899ed4b6dd40b7a8e9f8d8ebe",
  );
});

test("prepare returns one signed create ticket and has no Sender seam", async () => {
  const observed = [];
  const database = {
    async query(query) {
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
    },
  };
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

test("exported ticket nominal types cannot mint effect authority", () => {
  assert.throws(
    () => new CreatePlaceholderActionTicket("caller-authored"),
    /issuer-constructed/,
  );
});
