import assert from "node:assert/strict";

import {
  ActionPlane,
  ActionTicketKeyring,
  createTrustedActionReconciliation,
  PrivateActionPrepareProfile,
} from "@context-engine/action-plane";

const organizationId = "81e18bca-86a1-478a-937d-7675c6fe69b0";
const providerAttemptRef = `pat_${"a".repeat(32)}`;
const observedQueries = [];
const plane = new ActionPlane({
  database: {
    async query(config) {
      observedQueries.push(config);
      return { rows: [{ outcome: "provider_rejected" }] };
    },
  },
  keyring: new ActionTicketKeyring({
    activeVersion: 1,
    keys: new Map([[1, Buffer.alloc(32, 0x11)]]),
  }),
  profile: new PrivateActionPrepareProfile({
    approvalTier: "preapproved_private_delivery_v1",
    authenticatedServiceRef: "service:bot-delivery",
    consumerRef: "consumer:bot-delivery",
    maximumPayloadBytes: 4096,
    profileRef: "private-action-prepare-v1",
    purpose: "context.answer",
    retentionPolicyRef: "action-digest-audit-retention-v1",
    retentionSeconds: 2_592_000,
    ticketTtlSeconds: 60,
  }),
});
const decision = createTrustedActionReconciliation({
  disposition: "rejected",
  organizationId,
  providerAttemptRef,
  reconciliationRef: "operator:package-runtime",
});

assert.equal(Object.isFrozen(decision), true);
assert.deepEqual(await plane.reconcile(decision), {
  effectCount: 0,
  kind: "rejected",
  reasonCategory: "provider_rejected",
});
assert.equal(observedQueries.length, 1);
await assert.rejects(
  plane.reconcile({}),
  /requires trusted reconciliation authority/,
);

await assert.rejects(
  import("@context-engine/action-plane/internal.js"),
  (error) => error?.code === "ERR_PACKAGE_PATH_NOT_EXPORTED",
);
