import {
  ContextEngineResolveClient,
  type DirectContextOptions,
  type ResolutionOutcomeWire,
  type ResolveWire,
} from "@context-engine/resolve-sdk";

const client = new ContextEngineResolveClient({
  authentication: "opaque-test-token",
  baseUrl: "https://context-engine.invalid",
});

const request: ResolveWire = {
  kind: "acquire",
  need: { query: "generated contract" },
};

const outcome: Promise<ResolutionOutcomeWire> = client.resolve({
  deliveryEvidenceRef: "deliv_test_opaque",
  request,
  requestId: "sdk-package-contract",
});
void outcome;

client.resolve({
  requestId: "forbidden-body-field",
  request: {
    kind: "acquire",
    need: { query: "reject authority" },
    // @ts-expect-error trusted Organization fields are absent from ResolveWire
    organizationRef: "forbidden",
  },
});

client.resolve({
  requestId: "unknown-union-kind",
  request: {
    // @ts-expect-error request kinds are a closed generated union
    kind: "unknown",
    need: { query: "reject unknown" },
  },
});

client.resolve({
  // @ts-expect-error raw headers are not part of the public facade
  headers: { "X-Context-Organization-Ref": "forbidden" },
  request,
  requestId: "forbidden-header",
});

const forbiddenContinueEvidence: DirectContextOptions = {
  // @ts-expect-error delivery evidence is currently valid only for Acquire
  deliveryEvidenceRef: "deliv_forbidden_for_continue",
  request: {
    continuationToken: "continuation_inactive",
    kind: "continue",
  },
  requestId: "forbidden-delivery-evidence-combination",
};
void forbiddenContinueEvidence;

// @ts-expect-error raw generated transports are sealed by package exports
await import("@context-engine/resolve-sdk/generated/sdk.gen.js");
