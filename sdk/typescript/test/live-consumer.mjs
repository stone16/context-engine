import { ContextEngineResolveClient } from "@context-engine/resolve-sdk";

const requiredEnvironment = [
  "CONTEXT_ENGINE_SDK_BASE_URL",
  "CONTEXT_ENGINE_SDK_DELIVERY_EVIDENCE_REF",
  "CONTEXT_ENGINE_SDK_CITATION_DELIVERY_EVIDENCE_REF",
  "CONTEXT_ENGINE_SDK_REQUEST_ID",
  "CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION",
  "CONTEXT_ENGINE_SDK_TEST_DIRECT_AUTHENTICATION",
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

process.stdout.write(`${JSON.stringify({ acquire, citation, continuation })}\n`);
