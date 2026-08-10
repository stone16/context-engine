import {
  ContextEngineResolveClient,
  type ContextPackageV1Wire,
  type DeliveryBoundContextOptions,
  type DirectContextOptions,
  type ResolutionOutcomeV1Wire,
  type ResolveWire,
} from "@context-engine/resolve-sdk-v1";

const client = new ContextEngineResolveClient({
  authentication: "opaque-test-token",
  baseUrl: "https://context-engine.invalid",
});

const request: ResolveWire = {
  kind: "acquire",
  need: { query: "generated contract" },
};

const outcome: Promise<ResolutionOutcomeV1Wire> = client.resolve({
  deliveryEvidenceRef: "deliv_test_opaque",
  request,
  requestId: "sdk-package-contract",
});
void outcome;

const packageSchemaRef: ContextPackageV1Wire["packageSchemaRef"] =
  "context-package-openapi-v1";
void packageSchemaRef;

// @ts-expect-error v1 Packages require the exact reviewed v1 schema identity
const foreignPackageSchemaRef: ContextPackageV1Wire["packageSchemaRef"] =
  "context-package-openapi-foreign";
void foreignPackageSchemaRef;

const citationDelivery: DeliveryBoundContextOptions = {
  deliveryEvidenceRef: "deliv_citation_opaque",
  request: {
    citationOpenRef: "cor_opaque",
    kind: "open_citation",
  },
  requestId: "sdk-citation-contract",
};
void citationDelivery;

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
  // @ts-expect-error delivery evidence is valid for Acquire/OpenCitation only
  deliveryEvidenceRef: "deliv_forbidden_for_continue",
  request: {
    continuationToken: "continuation_inactive",
    kind: "continue",
  },
  requestId: "forbidden-delivery-evidence-combination",
};
void forbiddenContinueEvidence;

// @ts-expect-error raw generated transports are sealed by package exports
await import("@context-engine/resolve-sdk-v1/generated/sdk.gen.js");
