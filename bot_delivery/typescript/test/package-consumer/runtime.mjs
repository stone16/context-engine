import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import {
  AuthorizedModelInput,
  DeterministicModelGatewayTwin,
  ModelGenerationBoundary,
  PrivateModelGatewayProfile,
  createPrivateModelGenerationBoundary,
  privateModelGatewayProfileV1,
  prepareAuthorizedModelInput,
} from "@context-engine/bot-delivery";
import canonicalize from "canonicalize";

const now = new Date("2026-07-24T08:00:00.000Z");
const organizationId = "81e18bca-86a1-478a-937d-7675c6fe69b0";
const evidenceRef = `ev_${"1".repeat(64)}`;
const citationOpenRef = `cor_${"2".repeat(64)}`;
const grant = { kind: "model", value: `egrm_${"3".repeat(64)}` };
const profile = privateModelGatewayProfileV1();
assert.throws(
  () => new PrivateModelGatewayProfile({}),
  /only be constructed by BotDelivery/,
);
const evidence = {
  authorizationAsOf: "2026-07-24T08:00:00Z",
  citationOpenRef,
  decisionRef: "decision:package-consumer",
  evidenceRef,
  fragmentRef: "fragment:package-consumer",
  policyEpoch: 1,
  policySnapshotRef: "policy-snapshot-v1",
  projectedFields: ["body"],
  purpose: "context.answer",
  resourceRef: "resource:package-consumer",
  revisionRef: "revision:package-consumer",
  runRef: "run:package-consumer",
  sourceAclEvidence: {
    aclAsOf: "2026-07-24T08:00:00Z",
    freshnessProfileRef: "file-source-access-current-transaction-v1",
    kind: "mirrored",
    projectionRef: "source-acl:package-consumer",
  },
  sourceRef: "source:package-consumer",
};
const document = {
  asOf: "2026-07-24T08:00:00Z",
  audienceDigest: "a".repeat(64),
  blocks: [{
    blockId: `block_${"1".repeat(64)}`,
    evidenceRefs: [evidenceRef],
    text: "Authorized installed-package context.",
  }],
  budgetUsage: { costMicrounits: 0, elapsedMs: 0, providerCalls: 0, tokens: 12 },
  continuation: null,
  coverage: { status: "sufficient" },
  decisionRef: "decision:package-consumer",
  evidence: [evidence],
  expiresAt: "2026-07-24T08:05:00Z",
  gaps: [],
  packageId: `pkg_${"c".repeat(32)}`,
  packageSchemaRef: "context-package-openapi-v0",
  policyEpoch: 1,
  policySnapshotRef: "policy-snapshot-v1",
  purpose: "context.answer",
  releaseManifestRef: "release:package-consumer",
  retentionPolicyRef: "package-digest-only-retention-v1",
  runRef: "run:package-consumer",
  tokenizerRef: "utf8-byte-budget-v1",
  ttlSeconds: 300,
};
const packageValue = {
  ...document,
  packageDigest: createHash("sha256").update(canonicalize(document)).digest("hex"),
};
const input = prepareAuthorizedModelInput({
  envelope: { instructions: "Use context only.", question: "What is available?" },
  grant,
  now,
  package: packageValue,
  profile,
});
assert.equal(input instanceof AuthorizedModelInput, true);
const gateway = new DeterministicModelGatewayTwin({
  citations: [evidenceRef],
  costMicrounits: 2,
  elapsedMs: 3,
  profile,
  text: "Authorized installed-package answer.",
});
assert.throws(
  () => new ModelGenerationBoundary({}),
  /only be constructed by BotDelivery/,
);
assert.throws(
  () => createPrivateModelGenerationBoundary({
    database: { async query() { return { rows: [{ accepted: true }] }; } },
    databaseUrl: "postgresql://context_engine_egress:unused@127.0.0.1/context_engine",
    gateway,
    organizationId,
    profile,
  }),
  /invalid field set/,
);
const boundary = createPrivateModelGenerationBoundary({
  databaseUrl: "postgresql://context_engine_egress:unused@127.0.0.1/context_engine",
  gateway,
  organizationId,
  profile,
});
await boundary.close();
assert.equal(gateway.callCount, 0);
assert.equal(input instanceof AuthorizedModelInput, true);
await assert.rejects(
  import("@context-engine/bot-delivery/internal.js"),
  (error) => error?.code === "ERR_PACKAGE_PATH_NOT_EXPORTED",
);
await assert.rejects(
  import("@context-engine/bot-delivery/index.js"),
  (error) => error?.code === "ERR_PACKAGE_PATH_NOT_EXPORTED",
);
const publicUrl = import.meta.resolve("@context-engine/bot-delivery");
const fileUrlSibling = await import(new URL("./index.js", publicUrl));
assert.equal("createModelGenerationBoundaryForTest" in fileUrlSibling, false);
assert.equal("PrivateFeishuIdentityTwin" in fileUrlSibling, false);
assert.equal("PrivateQuestionTurnFixture" in fileUrlSibling, false);
assert.throws(
  () => new fileUrlSibling.ModelGenerationBoundary({
    close: async () => undefined,
    database: { async query() { return { rows: [{ accepted: true }] }; } },
    gateway,
    organizationId,
    profile,
  }),
  /only be constructed by BotDelivery/,
);
const actionApi = await import("@context-engine/action-plane");
assert.equal("createPrivateBotActionBridge" in actionApi, false);
assert.equal("TrustedPrivateEffectFacts" in actionApi, false);
