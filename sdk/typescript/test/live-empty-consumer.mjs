import { ContextEngineResolveClient } from "@context-engine/resolve-sdk";

for (const name of [
  "CONTEXT_ENGINE_SDK_BASE_URL",
  "CONTEXT_ENGINE_SDK_REQUEST_ID",
  "CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION",
]) {
  if (!process.env[name]) {
    throw new Error(`missing live SDK fixture variable ${name}`);
  }
}

const client = new ContextEngineResolveClient({
  authentication: process.env.CONTEXT_ENGINE_SDK_TEST_AUTHENTICATION,
  baseUrl: process.env.CONTEXT_ENGINE_SDK_BASE_URL,
});
const acquire = await client.resolve({
  request: {
    kind: "acquire",
    need: { query: "ContextEngine delivers context." },
  },
  requestId: process.env.CONTEXT_ENGINE_SDK_REQUEST_ID,
});

process.stdout.write(`${JSON.stringify(acquire)}\n`);
