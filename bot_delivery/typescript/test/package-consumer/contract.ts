import type {
  ContextPackageWire,
  ModelEgressGrantWire,
} from "@context-engine/resolve-sdk";
import {
  AuthorizedModelInput,
  DeterministicModelGatewayTwin,
  ModelGenerationBoundary,
  PrivateModelGatewayProfile,
  createPrivateModelGenerationBoundary,
  privateModelGatewayProfileV1,
  prepareAuthorizedModelInput,
  type PrepareAuthorizedModelInputOptions,
} from "@context-engine/bot-delivery";

const profile = privateModelGatewayProfileV1();

declare const packageValue: ContextPackageWire;
declare const grant: ModelEgressGrantWire;
const options: PrepareAuthorizedModelInputOptions = {
  envelope: { instructions: "Use context.", question: "Question?" },
  grant,
  now: new Date(),
  package: packageValue,
  profile,
};
const input: AuthorizedModelInput = prepareAuthorizedModelInput(options);
void input;

// @ts-expect-error package consumers cannot construct nominal authorized input
new AuthorizedModelInput();
// @ts-expect-error package consumers cannot construct server-owned profiles
new PrivateModelGatewayProfile({});
// @ts-expect-error package consumers cannot construct a boundary or inject database authority
new ModelGenerationBoundary({});
// @ts-expect-error packages are the only content input; extra context is closed
prepareAuthorizedModelInput({ ...options, arbitraryText: "denied" });
declare const gateway: DeterministicModelGatewayTwin;
createPrivateModelGenerationBoundary({
  // @ts-expect-error the public factory accepts a connection URL, never a structural query object
  database: { query: async () => ({ rows: [{ accepted: true }] }) },
  databaseUrl: "postgresql://unused.invalid/context_engine",
  gateway,
  organizationId: "81e18bca-86a1-478a-937d-7675c6fe69b0",
  profile,
});
// @ts-expect-error package consumers cannot import an internal implementation subpath
await import("@context-engine/bot-delivery/internal.js");
