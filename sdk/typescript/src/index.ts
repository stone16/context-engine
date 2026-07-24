import { createClient, type Client } from "./generated/client/index.js";
import { resolveContextV0 } from "./generated/sdk.gen.js";
import type {
  AcquireWire,
  ApplicationForbiddenWire,
  AuthenticationFailureWire,
  BlockWire,
  BudgetUsageWire,
  ChannelEgressGrantWire,
  CitationNotAvailableWire,
  ContextNeedWire,
  ContextPackageWire,
  ContinuationOfferWire,
  ContinueWire,
  CoverageWire,
  EvidenceWire,
  GapWire,
  InvalidRequestWire,
  LiveSourceAclEvidenceWire,
  MirroredSourceAclEvidenceWire,
  ModelEgressGrantWire,
  OpenCitationWire,
  PackageBudgetWire,
  RateLimitedWire,
  RequestNarrowingWire,
  RequestNotAvailableWire,
  ResolutionOutcomeWire,
  ResolveContextV0Error,
  ResolveWire,
  ResolvedWire,
  ServiceUnavailableWire,
  SourceAclEvidenceWire,
  WeakSourceAclEvidenceWire,
} from "./generated/types.gen.js";

export type ContextEngineAuthentication =
  | string
  | (() => Promise<string | undefined> | string | undefined);

export interface ContextEngineResolveClientOptions {
  readonly authentication: ContextEngineAuthentication;
  readonly baseUrl: string;
}

interface ResolveContextOptionsBase {
  readonly requestId: string;
}

export interface AcquireContextOptions extends ResolveContextOptionsBase {
  readonly deliveryEvidenceRef?: string;
  readonly request: Extract<ResolveWire, { kind: "acquire" }>;
}

export interface DirectContextOptions extends ResolveContextOptionsBase {
  readonly deliveryEvidenceRef?: never;
  readonly request: Exclude<ResolveWire, { kind: "acquire" }>;
}

export type ResolveContextOptions = AcquireContextOptions | DirectContextOptions;

export class ContextEngineHttpError extends Error {
  readonly body: ResolveContextV0Error;
  readonly status: number;

  constructor(status: number, body: ResolveContextV0Error) {
    super(`ContextEngine resolve failed with HTTP ${status}`);
    this.name = "ContextEngineHttpError";
    this.status = status;
    this.body = body;
  }
}

export class ContextEngineTransportError extends Error {
  constructor(cause: unknown) {
    super("ContextEngine resolve transport failed", { cause });
    this.name = "ContextEngineTransportError";
  }
}

export class ContextEngineResolveClient {
  readonly #client: Client;

  constructor(options: ContextEngineResolveClientOptions) {
    this.#client = createClient({
      auth: options.authentication,
      baseUrl: options.baseUrl,
      responseStyle: "fields",
      throwOnError: false,
    });
  }

  async resolve(options: ResolveContextOptions): Promise<ResolutionOutcomeWire> {
    const headers = options.deliveryEvidenceRef === undefined
      ? { "X-Context-Request-Id": options.requestId }
      : {
          "X-Context-Delivery-Evidence-Ref": options.deliveryEvidenceRef,
          "X-Context-Request-Id": options.requestId,
        };
    const result = await resolveContextV0({
      body: options.request,
      client: this.#client,
      headers,
    });

    if (result.data !== undefined) {
      return result.data;
    }
    if (result.response === undefined) {
      throw new ContextEngineTransportError(result.error);
    }
    throw new ContextEngineHttpError(result.response.status, result.error);
  }
}

export type {
  AcquireWire,
  ApplicationForbiddenWire,
  AuthenticationFailureWire,
  BlockWire,
  BudgetUsageWire,
  ChannelEgressGrantWire,
  CitationNotAvailableWire,
  ContextNeedWire,
  ContextPackageWire,
  ContinuationOfferWire,
  ContinueWire,
  CoverageWire,
  EvidenceWire,
  GapWire,
  InvalidRequestWire,
  LiveSourceAclEvidenceWire,
  MirroredSourceAclEvidenceWire,
  ModelEgressGrantWire,
  OpenCitationWire,
  PackageBudgetWire,
  RateLimitedWire,
  RequestNarrowingWire,
  RequestNotAvailableWire,
  ResolutionOutcomeWire,
  ResolveContextV0Error,
  ResolveWire,
  ResolvedWire,
  ServiceUnavailableWire,
  SourceAclEvidenceWire,
  WeakSourceAclEvidenceWire,
};
