import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { inspect } from "node:util";
import { test } from "node:test";

import canonicalize from "canonicalize";
import pg from "pg";

import {
  AuthorizedModelInput,
  DeterministicModelGatewayTwin,
  ModelGenerationBoundary,
  PrivateModelGatewayProfile,
  answerPayloadDigest,
  createPrivateModelGenerationBoundary,
  privateModelGatewayProfileV1,
  prepareAuthorizedModelInput,
} from "../dist/index.js";
import { canonicalJsonDigest } from "../dist/canonical-json.js";

const packageDigest = (document) => createHash("sha256")
  .update(canonicalize(document))
  .digest("hex");

const exactProfileOptions = {
  auditRetentionSeconds: 2_592_000,
  consumerRef: "model-gateway-integration",
  issuerRef: "context-runtime-integration",
  maximumCostMicrounits: 10_000,
  maximumElapsedMs: 2_000,
  maximumInputBytes: 32_768,
  maximumInstructionBytes: 512,
  maximumOutputBytes: 2_048,
  maximumProviderCalls: 1,
  maximumQuestionBytes: 1_024,
  modelRef: "deterministic-model-spy",
  profileRef: "file-model-egress-integration-v1",
  providerRef: "deterministic-provider-spy",
  regionRef: "local-test-region",
  retentionPolicyRef: "no-provider-retention-v1",
  sensitivityPolicyRef: "authorized-package-only-v1",
};

const organizationId = "81e18bca-86a1-478a-937d-7675c6fe69b0";
const grant = { kind: "model", value: `egrm_${"1".repeat(64)}` };
const now = new Date(Math.floor(Date.now() / 1_000) * 1_000);
const packageAsOf = new Date(now.getTime() - 1_000).toISOString().replace(".000Z", "Z");
const packageExpiresAt = new Date(now.getTime() + 299_000).toISOString().replace(".000Z", "Z");
const fakeDatabases = new Map();
let fakeDatabaseSequence = 0;

const { Pool } = pg;
Pool.prototype.query = async function query(config) {
  const databaseName = new URL(this.options.connectionString).pathname.slice(1);
  const database = fakeDatabases.get(databaseName);
  if (database === undefined) throw new Error("unregistered test database");
  return database.query(config);
};

function evidence(index) {
  const suffix = String(index).repeat(64);
  return {
    authorizationAsOf: packageAsOf,
    citationOpenRef: `cor_${String(index).repeat(64)}`,
    decisionRef: `dec_${"d".repeat(32)}`,
    evidenceRef: `ev_${suffix}`,
    fragmentRef: `fragment:paragraph:${index}`,
    policyEpoch: 7,
    policySnapshotRef: "policy-snapshot-v7",
    projectedFields: ["body"],
    purpose: "context.answer",
    resourceRef: "resource:handbook",
    revisionRef: "revision:handbook:v1",
    runRef: "run:private-answer",
    sourceAclEvidence: {
      aclAsOf: packageAsOf,
      freshnessProfileRef: "file-source-access-current-transaction-v1",
      kind: "mirrored",
      projectionRef: `source-acl:${index}`,
    },
    sourceRef: "source:handbook",
  };
}

function contextPackage() {
  const first = evidence(1);
  const second = evidence(2);
  const document = {
    asOf: packageAsOf,
    audienceDigest: "a".repeat(64),
    blocks: [
      {
        blockId: `block_${"1".repeat(64)}`,
        evidenceRefs: [first.evidenceRef],
        text: "Authorized handbook fact one.",
      },
      {
        blockId: `block_${"2".repeat(64)}`,
        evidenceRefs: [second.evidenceRef],
        text: "Authorized handbook fact two.",
      },
    ],
    budgetUsage: {
      costMicrounits: 0,
      elapsedMs: 0,
      providerCalls: 0,
      tokens: 58,
    },
    continuation: null,
    coverage: { status: "sufficient" },
    decisionRef: `dec_${"d".repeat(32)}`,
    evidence: [first, second],
    expiresAt: packageExpiresAt,
    gaps: [],
    packageId: `pkg_${"c".repeat(32)}`,
    packageSchemaRef: "context-package-openapi-v0",
    policyEpoch: 7,
    policySnapshotRef: "policy-snapshot-v7",
    purpose: "context.answer",
    releaseManifestRef: "release:private-answer",
    retentionPolicyRef: "package-digest-only-retention-v1",
    runRef: "run:private-answer",
    tokenizerRef: "utf8-byte-budget-v1",
    ttlSeconds: 300,
  };
  return Object.freeze({ ...document, packageDigest: packageDigest(document) });
}

function exactRedemptionDatabase(expectedPackage, expectedGrant = grant) {
  const expectedGrantDigest = createHash("sha256").update(expectedGrant.value).digest();
  const calls = [];
  return {
    calls,
    async query(config) {
      calls.push(config);
      if (config.text.includes("context_egress_redeem_grant")) {
        assert.equal(config.values[0], organizationId);
        assert.deepEqual(config.values[1], expectedGrantDigest);
        assert.equal(config.values[3], "model");
        assert.deepEqual(config.values[4], Buffer.from(expectedPackage.packageDigest, "hex"));
        assert.equal(config.values[6], expectedPackage.purpose);
        assert.deepEqual(config.values[7], Buffer.from(expectedPackage.audienceDigest, "hex"));
        assert.equal(config.values[8], expectedPackage.policyEpoch);
        assert.equal(config.values[9], exactProfileOptions.retentionPolicyRef);
        assert.equal(config.values[10], exactProfileOptions.sensitivityPolicyRef);
        assert.equal(config.values[11], exactProfileOptions.issuerRef);
        assert.equal(config.values[12], exactProfileOptions.consumerRef);
        assert.equal(config.values[13], exactProfileOptions.providerRef);
        assert.equal(config.values[14], exactProfileOptions.modelRef);
        assert.equal(config.values[17], exactProfileOptions.regionRef);
        assert.equal(config.values[18], exactProfileOptions.profileRef);
        return { rows: [{ accepted: true }] };
      }
      if (config.text.includes("context_egress_record_model_outcome")) {
        return { rows: [{ recorded: true }] };
      }
      throw new Error("unexpected model egress query");
    },
  };
}

function configuredBoundary({
  database,
  gateway,
  organization = organizationId,
  profile = privateModelGatewayProfileV1(),
}) {
  fakeDatabaseSequence += 1;
  const databaseName = `context_engine_model_egress_test_${fakeDatabaseSequence}`;
  fakeDatabases.set(databaseName, database);
  return createPrivateModelGenerationBoundary({
    databaseUrl: `postgresql://context_engine_egress:unused@127.0.0.1/${databaseName}`,
    gateway,
    organizationId: organization,
    profile,
  });
}

test("TypeScript and Python share RFC 8785/I-JSON vectors", () => {
  const fixture = JSON.parse(readFileSync(
    new URL("../../../tests/fixtures/canonical-json-cross-language-v1.json", import.meta.url),
    "utf8",
  ));
  assert.equal(fixture.profile, "rfc8785-ijson-cross-language-v1");
  for (const vector of fixture.valid) {
    assert.equal(canonicalJsonDigest(vector.document), vector.sha256, vector.name);
  }
  for (const vector of fixture.invalidJsonDocuments) {
    assert.throws(
      () => canonicalJsonDigest(JSON.parse(vector.json)),
      /Unicode scalar values/,
      vector.name,
    );
  }
  for (const value of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
    assert.throws(
      () => canonicalJsonDigest({ value }),
      /finite IEEE 754 values/,
    );
  }
});

test("AuthorizedModelInput is nominal, redacted, and bound to one complete current Package", () => {
  const packageValue = contextPackage();
  const profile = privateModelGatewayProfileV1();
  const input = prepareAuthorizedModelInput({
    envelope: {
      instructions: "Answer only from the supplied context.",
      question: "What are the two facts?",
    },
    grant,
    now,
    package: packageValue,
    profile,
  });

  assert.equal(input instanceof AuthorizedModelInput, true);
  assert.equal("create" in AuthorizedModelInput, false);
  assert.equal(JSON.stringify(input).includes(grant.value), false);
  assert.equal(inspect(input).includes(grant.value), false);
  assert.throws(() => new AuthorizedModelInput(), /only be constructed/);
  assert.throws(
    () => prepareAuthorizedModelInput({ envelope: {}, grant, now, package: "arbitrary text", profile }),
    /ContextPackage/,
  );
  assert.throws(
    () => prepareAuthorizedModelInput({
      envelope: { instructions: "Use this", question: "Question" },
      grant,
      now,
      package: [packageValue, contextPackage()],
      profile,
    }),
    /ContextPackage/,
  );
  assert.throws(
    () => prepareAuthorizedModelInput({
      envelope: { instructions: "Use this", question: "Question" },
      grant,
      now,
      package: {
        fragmentRef: "fragment:denied",
        resourceRef: "resource:denied",
        snippet: "denied secret",
      },
      profile,
    }),
    /ContextPackage/,
  );
  assert.throws(
    () => prepareAuthorizedModelInput({
      envelope: { instructions: "Use this", question: "Question", extraContext: "denied secret" },
      grant,
      now,
      package: packageValue,
      profile,
    }),
    /question envelope/,
  );
  const symbolEnvelope = { instructions: "Use this", question: "Question" };
  symbolEnvelope[Symbol("extraContext")] = "denied secret";
  assert.throws(
    () => prepareAuthorizedModelInput({
      envelope: symbolEnvelope,
      grant,
      now,
      package: packageValue,
      profile,
    }),
    /invalid field set/,
  );
  for (const envelope of [
    { instructions: "Use this\ud800", question: "Question" },
    { instructions: "Use this", question: "Question\udfff" },
  ]) {
    assert.throws(
      () => prepareAuthorizedModelInput({ envelope, grant, now, package: packageValue, profile }),
      /Unicode scalar values/,
    );
  }
  const { packageDigest: ignoredSurrogateDigest, ...surrogatePackage } = packageValue;
  assert.equal(typeof ignoredSurrogateDigest, "string");
  surrogatePackage.blocks = surrogatePackage.blocks.map((block, index) => (
    index === 0 ? { ...block, text: "invalid\ud800package" } : block
  ));
  assert.throws(
    () => prepareAuthorizedModelInput({
      envelope: { instructions: "Use this", question: "Question" },
      grant,
      now,
      package: { ...surrogatePackage, packageDigest: packageDigest(surrogatePackage) },
      profile,
    }),
    /Unicode scalar values/,
  );
  const malformedPackage = {
    ...packageValue,
    packageId: `pkg_${"z".repeat(32)}`,
  };
  const { packageDigest: ignored, ...malformedDocument } = malformedPackage;
  assert.equal(typeof ignored, "string");
  assert.throws(
    () => prepareAuthorizedModelInput({
      envelope: { instructions: "Use this", question: "Question" },
      grant,
      now,
      package: { ...malformedPackage, packageDigest: packageDigest(malformedDocument) },
      profile,
    }),
    /Package reference/,
  );
  const { packageDigest: ignoredDigest, ...microsecondDocument } = packageValue;
  assert.equal(typeof ignoredDigest, "string");
  microsecondDocument.asOf = now.toISOString().replace(".000Z", ".000500Z");
  microsecondDocument.expiresAt = new Date(now.getTime() + 300_000)
    .toISOString().replace(".000Z", ".000500Z");
  assert.throws(
    () => prepareAuthorizedModelInput({
      envelope: { instructions: "Use this", question: "Question" },
      grant,
      now,
      package: {
        ...microsecondDocument,
        packageDigest: packageDigest(microsecondDocument),
      },
      profile,
    }),
    /current Package/,
  );
  assert.throws(
    () => prepareAuthorizedModelInput({
      envelope: { instructions: "Use this", question: "Question" },
      grant,
      now,
      package: packageValue,
      profile: new PrivateModelGatewayProfile(exactProfileOptions),
    }),
    /only be constructed by BotDelivery/,
  );
});

test("only the exact deterministic ModelGateway twin can own provider bytes", () => {
  const packageValue = contextPackage();
  const profile = privateModelGatewayProfileV1();
  class InheritedGateway extends DeterministicModelGatewayTwin {}
  const inherited = new InheritedGateway({
    citations: [packageValue.evidence[0].evidenceRef],
    costMicrounits: 1,
    elapsedMs: 1,
    profile,
    text: "Subclass must not run.",
  });
  assert.equal("generate" in inherited, false);
  assert.throws(
    () => { inherited.callCount = 99; },
    /getter|read only|Cannot set/u,
  );
  assert.throws(
    () => configuredBoundary({
      database: exactRedemptionDatabase(packageValue),
      gateway: inherited,
      profile,
    }),
    /exact deterministic ModelGateway twin/,
  );
  assert.equal(inherited.outboundBytes, 0);
  assert.throws(
    () => new ModelGenerationBoundary({}),
    /only be constructed by BotDelivery/,
  );
});

test("valid private Package and grant invoke the gateway once and return bounded citation subset", async () => {
  const packageValue = contextPackage();
  const profile = privateModelGatewayProfileV1();
  const input = prepareAuthorizedModelInput({
    envelope: {
      instructions: "Answer only from the supplied context.",
      question: "What are the two facts?",
    },
    grant,
    now,
    package: packageValue,
    profile,
  });
  const database = exactRedemptionDatabase(packageValue);
  const gateway = new DeterministicModelGatewayTwin({
    citations: [packageValue.evidence[1].evidenceRef],
    costMicrounits: 17,
    elapsedMs: 9,
    profile,
    text: "The second authorized fact.",
  });

  const outcome = await configuredBoundary({ database, gateway, profile }).generate(input, grant);

  assert.equal(outcome.kind, "generated");
  assert.equal(gateway.callCount, 1);
  assert.ok(gateway.outboundBytes > 0);
  assert.deepEqual(outcome.answer.citations, [{
    citationOpenRef: packageValue.evidence[1].citationOpenRef,
    evidenceRef: packageValue.evidence[1].evidenceRef,
  }]);
  assert.equal(outcome.answer.text, "The second authorized fact.");
  assert.equal(outcome.usage.providerCalls, 1);
  assert.equal(outcome.usage.costMicrounits, 17);
  assert.equal(outcome.usage.elapsedMs, 9);
  assert.equal(outcome.answer.answerPayloadDigest, answerPayloadDigest({
    citations: outcome.answer.citations,
    text: outcome.answer.text,
  }));
  assert.equal("destinationRef" in outcome.answer, false);
  assert.equal("operation" in outcome.answer, false);
  assert.equal(database.calls.length, 2);
  const auditValues = database.calls[1].values;
  assert.equal(auditValues.includes(grant.value), false);
  assert.equal(auditValues.includes(outcome.answer.text), false);
  assert.throws(
    () => answerPayloadDigest({
      citations: [{
        citationOpenRef: packageValue.evidence[1].citationOpenRef,
        evidenceRef: packageValue.evidence[1].evidenceRef,
        operation: "send",
      }],
      text: outcome.answer.text,
    }),
    /answer citation/,
  );
});

test("provider request contains only declared question, instructions, and authorized Package blocks", async () => {
  const packageValue = contextPackage();
  const profile = privateModelGatewayProfileV1();
  const input = prepareAuthorizedModelInput({
    envelope: { instructions: "Use cited context.", question: "Summarize." },
    grant,
    now,
    package: packageValue,
    profile,
  });
  const gateway = new DeterministicModelGatewayTwin({
    citations: [packageValue.evidence[0].evidenceRef],
    costMicrounits: 1,
    elapsedMs: 1,
    profile,
    text: "Authorized answer.",
  });

  const outcome = await configuredBoundary({
    database: exactRedemptionDatabase(packageValue),
    gateway,
    profile,
  }).generate(input, grant);

  assert.equal(outcome.kind, "generated");
  const request = gateway.requests[0];
  const serialized = JSON.stringify(request);
  assert.deepEqual(Object.keys(request).sort(), ["context", "instructions", "question"]);
  assert.match(serialized, /Authorized handbook fact one/);
  assert.match(serialized, /Authorized handbook fact two/);
  assert.match(serialized, /Summarize/);
  for (const forbidden of [
    grant.value,
    organizationId,
    packageValue.audienceDigest,
    packageValue.decisionRef,
    "denied secret",
    "candidate",
  ]) {
    assert.equal(serialized.includes(forbidden), false);
  }
});

test("tampered or stale Package, wrong grant kind, and replay emit zero provider bytes", async () => {
  const packageValue = contextPackage();
  const profile = privateModelGatewayProfileV1();
  const base = {
    envelope: { instructions: "Use context.", question: "Question?" },
    grant,
    now,
    profile,
  };
  const tampered = {
    ...packageValue,
    blocks: [...packageValue.blocks, {
      blockId: `block_${packageValue.evidence[0].evidenceRef.slice("ev_".length)}`,
      evidenceRefs: [packageValue.evidence[0].evidenceRef],
      text: "denied secret",
    }],
  };
  assert.throws(
    () => prepareAuthorizedModelInput({ ...base, package: tampered }),
    /Package/,
  );
  assert.throws(
    () => prepareAuthorizedModelInput({ ...base, now: new Date(packageValue.expiresAt), package: packageValue }),
    /current Package/,
  );
  assert.throws(
    () => prepareAuthorizedModelInput({
      ...base,
      grant: { kind: "channel", value: `egrc_${"2".repeat(64)}` },
      package: packageValue,
    }),
    /model EgressGrant/,
  );

  const input = prepareAuthorizedModelInput({ ...base, package: packageValue });
  let consumed = false;
  const database = {
    async query(config) {
      if (config.text.includes("context_egress_redeem_grant")) {
        if (consumed) return { rows: [{ accepted: false }] };
        consumed = true;
        return { rows: [{ accepted: true }] };
      }
      return { rows: [{ recorded: true }] };
    },
  };
  const firstGateway = new DeterministicModelGatewayTwin({
    citations: [], costMicrounits: 1, elapsedMs: 1, profile, text: "First.",
  });
  const first = await configuredBoundary({ database, gateway: firstGateway, profile })
    .generate(input, grant);
  assert.equal(first.kind, "generated");
  const replayGateway = new DeterministicModelGatewayTwin({
    citations: [], costMicrounits: 1, elapsedMs: 1, profile, text: "Replay.",
  });
  const replay = await configuredBoundary({ database, gateway: replayGateway, profile })
    .generate(input, grant);
  assert.deepEqual(replay, { kind: "generation_not_available" });
  assert.equal(replayGateway.callCount, 0);
  assert.equal(replayGateway.outboundBytes, 0);
});

test("complete grant/input mutation matrix fails generically before gateway bytes", async () => {
  const packageValue = contextPackage();
  const baseProfile = privateModelGatewayProfileV1();
  const withPackageMutation = (updates, evidenceUpdates = {}) => {
    const { packageDigest: ignored, ...document } = packageValue;
    assert.equal(typeof ignored, "string");
    const mutatedDocument = {
      ...document,
      ...updates,
      evidence: packageValue.evidence.map((item) => ({ ...item, ...evidenceUpdates })),
    };
    return Object.freeze({
      ...mutatedDocument,
      packageDigest: packageDigest(mutatedDocument),
    });
  };
  const otherPackage = withPackageMutation({ packageId: `pkg_${"b".repeat(32)}` });
  const otherAudience = withPackageMutation({ audienceDigest: "b".repeat(64) });
  const otherPurpose = withPackageMutation(
    { purpose: "context.summarize" },
    { purpose: "context.summarize" },
  );
  const otherEpoch = withPackageMutation(
    { policyEpoch: 8 },
    { policyEpoch: 8 },
  );
  const mutations = [
    ["organization", { organizationId: "91e18bca-86a1-478a-937d-7675c6fe69b0" }],
    ["package", { inputPackage: otherPackage }],
    ["audience", { inputPackage: otherAudience }],
    ["purpose", { inputPackage: otherPurpose }],
    ["epoch", { inputPackage: otherEpoch }],
    ["provider", {}],
    ["model", {}],
    ["region", {}],
    ["retention", {}],
    ["sensitivity", {}],
    ["grant expiry", {}],
  ];

  for (const [name, mutation] of mutations) {
    const profile = privateModelGatewayProfileV1();
    const gatewayProfile = privateModelGatewayProfileV1();
    const gateway = new DeterministicModelGatewayTwin({
      citations: [], costMicrounits: 1, elapsedMs: 1, profile: gatewayProfile, text: "No.",
    });
    const database = {
      calls: 0,
      async query(config) {
        this.calls += 1;
        if (config.text.includes("context_egress_redeem_grant")) {
          const expected = {
            model: name === "model" ? "other-model" : exactProfileOptions.modelRef,
            provider: name === "provider" ? "other-provider" : exactProfileOptions.providerRef,
            region: name === "region" ? "other-region" : exactProfileOptions.regionRef,
            retention: name === "retention"
              ? "other-retention"
              : exactProfileOptions.retentionPolicyRef,
            sensitivity: name === "sensitivity"
              ? "other-sensitivity"
              : exactProfileOptions.sensitivityPolicyRef,
          };
          const exactBinding = (
            config.values[0] === organizationId
            && config.values[3] === "model"
            && config.values[4].equals(Buffer.from(packageValue.packageDigest, "hex"))
            && config.values[6] === packageValue.purpose
            && config.values[7].equals(Buffer.from(packageValue.audienceDigest, "hex"))
            && config.values[8] === packageValue.policyEpoch
            && config.values[9] === expected.retention
            && config.values[10] === expected.sensitivity
            && config.values[13] === expected.provider
            && config.values[14] === expected.model
            && config.values[17] === expected.region
            && config.values[18] === exactProfileOptions.profileRef
          );
          return {
            rows: [{ accepted: exactBinding && name !== "grant expiry" }],
          };
        }
        throw new Error("denial cannot write model outcome audit");
      },
    };
    const boundary = configuredBoundary({
      database,
      gateway,
      organization: mutation.organizationId ?? organizationId,
      profile,
    });
    const input = prepareAuthorizedModelInput({
      envelope: { instructions: "Use context.", question: "Question?" },
      grant,
      now,
      package: mutation.inputPackage ?? packageValue,
      profile: baseProfile,
    });
    const outcome = await boundary.generate(input, grant);
    assert.deepEqual(outcome, { kind: "generation_not_available" }, name);
    assert.equal(gateway.callCount, 0, name);
    assert.equal(gateway.outboundBytes, 0, name);
  }
});

test("invalid citations and profile output/cost/time limits return generic failure with restricted audit", async () => {
  const packageValue = contextPackage();
  const profile = privateModelGatewayProfileV1();
  const input = prepareAuthorizedModelInput({
    envelope: { instructions: "Use context.", question: "Question?" },
    grant,
    now,
    package: packageValue,
    profile,
  });
  const outputs = [
    { citations: ["ev_denied"], costMicrounits: 1, elapsedMs: 1, text: "Denied cite." },
    { citations: [], costMicrounits: exactProfileOptions.maximumCostMicrounits + 1, elapsedMs: 1, text: "Cost." },
    { citations: [], costMicrounits: 1, elapsedMs: exactProfileOptions.maximumElapsedMs + 1, text: "Time." },
    { citations: [], costMicrounits: 1, elapsedMs: 1, text: "x".repeat(exactProfileOptions.maximumOutputBytes + 1) },
    { citations: [], costMicrounits: 1, elapsedMs: 1, text: "invalid\ud800output" },
  ];

  for (const output of outputs) {
    const database = exactRedemptionDatabase(packageValue);
    const gateway = new DeterministicModelGatewayTwin({ ...output, profile });
    const result = await configuredBoundary({ database, gateway, profile }).generate(input, grant);
    assert.deepEqual(result, { kind: "generation_not_available" });
    assert.equal(gateway.callCount, 1);
    assert.equal(database.calls.length, 2);
    assert.equal(database.calls[1].values.includes(output.text), false);
  }
});
