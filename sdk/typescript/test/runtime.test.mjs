import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";

import {
  ContextEngineHttpError,
  ContextEngineResolveClient,
  ContextEngineTransportError,
} from "../dist/index.js";

let baseUrl;
let server;
const observed = [];

before(async () => {
  server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) {
      chunks.push(chunk);
    }
    observed.push({
      body: JSON.parse(Buffer.concat(chunks).toString("utf8")),
      headers: request.headers,
      method: request.method,
      url: request.url,
    });
    response.setHeader("Content-Type", "application/json");
    if (request.headers["x-context-request-id"] === "sdk-error") {
      response.statusCode = 503;
      response.end(JSON.stringify({ code: "service_unavailable" }));
      return;
    }
    response.end(JSON.stringify({ kind: "request_not_available", retryable: false }));
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("test server did not bind a TCP address");
  }
  baseUrl = `http://127.0.0.1:${address.port}`;
});

after(async () => {
  await new Promise((resolve, reject) => {
    server.close((error) => (error === undefined ? resolve() : reject(error)));
  });
});

test("generated facade sends only accepted auth and metadata", async () => {
  const client = new ContextEngineResolveClient({
    authentication: "opaque-runtime-token",
    baseUrl,
  });
  const outcome = await client.resolve({
    deliveryEvidenceRef: "deliv_opaque_runtime_test",
    request: {
      kind: "acquire",
      need: { query: "generated runtime" },
    },
    requestId: "sdk-runtime",
  });

  assert.deepEqual(outcome, {
    kind: "request_not_available",
    retryable: false,
  });
  assert.deepEqual(observed[0]?.body, {
    kind: "acquire",
    need: { query: "generated runtime" },
  });
  assert.equal(observed[0]?.headers.authorization, "Bearer opaque-runtime-token");
  assert.equal(observed[0]?.headers["x-context-request-id"], "sdk-runtime");
  assert.equal(
    observed[0]?.headers["x-context-delivery-evidence-ref"],
    "deliv_opaque_runtime_test",
  );
  assert.equal(observed[0]?.method, "POST");
  assert.equal(observed[0]?.url, "/v0/resolve");
});

test("generated facade preserves the closed public error", async () => {
  const client = new ContextEngineResolveClient({
    authentication: "opaque-runtime-token",
    baseUrl,
  });
  await assert.rejects(
    client.resolve({
      request: {
        citationOpenRef: "citation_opaque_runtime_test",
        kind: "open_citation",
      },
      requestId: "sdk-error",
    }),
    (error) => {
      assert.ok(error instanceof ContextEngineHttpError);
      assert.equal(error.status, 503);
      assert.deepEqual(error.body, { code: "service_unavailable" });
      return true;
    },
  );
});

test("generated facade distinguishes transport failure from HTTP outcomes", async () => {
  const client = new ContextEngineResolveClient({
    authentication: "opaque-runtime-token",
    baseUrl: "unsupported-protocol://context-engine.invalid",
  });
  await assert.rejects(
    client.resolve({
      request: {
        kind: "acquire",
        need: { query: "transport failure" },
      },
      requestId: "sdk-transport-error",
    }),
    ContextEngineTransportError,
  );
});
