import { ContextEngineResolveClient } from "@context-engine/resolve-sdk";
import {
  DeterministicModelGatewayTwin,
  createPrivateModelGenerationBoundary,
  privateModelGatewayProfileV1,
  prepareAuthorizedModelInput,
} from "@context-engine/bot-delivery";

const requiredEnvironment = [
  "CONTEXT_ENGINE_SDK_BASE_URL",
  "CONTEXT_ENGINE_SDK_DELIVERY_EVIDENCE_REF",
  "CONTEXT_ENGINE_SDK_CITATION_DELIVERY_EVIDENCE_REF",
  "CONTEXT_ENGINE_SDK_REQUEST_ID",
  "CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION",
  "CONTEXT_ENGINE_SDK_TEST_DIRECT_AUTHENTICATION",
  "CONTEXT_ENGINE_MODEL_EGRESS_DATABASE_URL",
  "CONTEXT_ENGINE_MODEL_EGRESS_ORGANIZATION_ID",
];
for (const name of requiredEnvironment) {
  if (!process.env[name]) {
    throw new Error(`missing live SDK fixture variable ${name}`);
  }
}

const client = new ContextEngineResolveClient({
  authentication: process.env.CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION,
  baseUrl: process.env.CONTEXT_ENGINE_SDK_BASE_URL,
});
const directClient = new ContextEngineResolveClient({
  authentication: process.env.CONTEXT_ENGINE_SDK_TEST_DIRECT_AUTHENTICATION,
  baseUrl: process.env.CONTEXT_ENGINE_SDK_BASE_URL,
});
const common = {
  deliveryEvidenceRef: process.env.CONTEXT_ENGINE_SDK_DELIVERY_EVIDENCE_REF,
  requestId: process.env.CONTEXT_ENGINE_SDK_REQUEST_ID,
};

const acquire = await client.resolve({
  ...common,
  request: {
    kind: "acquire",
    need: { query: "ContextEngine delivers context." },
  },
});
const modelProfile = privateModelGatewayProfileV1();
const modelInput = prepareAuthorizedModelInput({
  envelope: {
    instructions: "Answer only from the supplied Package.",
    question: "What does ContextEngine deliver?",
  },
  grant: acquire.egressGrant,
  now: new Date(),
  package: acquire.package,
  profile: modelProfile,
});
const gateway = new DeterministicModelGatewayTwin({
  citations: [acquire.package.evidence[0].evidenceRef],
  costMicrounits: 7,
  elapsedMs: 5,
  profile: modelProfile,
  text: "ContextEngine delivers authorized Package context.",
});
const modelBoundary = createPrivateModelGenerationBoundary({
  databaseUrl: process.env.CONTEXT_ENGINE_MODEL_EGRESS_DATABASE_URL,
  gateway,
  organizationId: process.env.CONTEXT_ENGINE_MODEL_EGRESS_ORGANIZATION_ID,
  profile: modelProfile,
});
let generation;
let generationReplay;
try {
  generation = await modelBoundary.generate(modelInput, acquire.egressGrant);
  generationReplay = await modelBoundary.generate(modelInput, acquire.egressGrant);
} finally {
  await modelBoundary.close();
}
const continuation = await directClient.resolve({
  request: {
    continuationToken: "continuation_sdk_live_inactive",
    kind: "continue",
  },
  requestId: process.env.CONTEXT_ENGINE_SDK_REQUEST_ID,
});
const citation = await client.resolve({
  deliveryEvidenceRef: process.env.CONTEXT_ENGINE_SDK_CITATION_DELIVERY_EVIDENCE_REF,
  request: {
    citationOpenRef: acquire.package.evidence[0].citationOpenRef,
    kind: "open_citation",
  },
  requestId: `${process.env.CONTEXT_ENGINE_SDK_REQUEST_ID}-citation`,
});

process.stdout.write(`${JSON.stringify({
  acquire,
  citation,
  continuation,
  gateway: {
    callCount: gateway.callCount,
    outboundBytes: gateway.outboundBytes,
    requests: gateway.requests,
  },
  generation,
  generationReplay,
})}\n`);
