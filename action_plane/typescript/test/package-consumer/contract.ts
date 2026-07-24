import {
  createTrustedActionReconciliation,
  type ActionReconciliationDecisionOptions,
  type TrustedActionReconciliation,
} from "@context-engine/action-plane";

const options: ActionReconciliationDecisionOptions = {
  disposition: "rejected",
  organizationId: "81e18bca-86a1-478a-937d-7675c6fe69b0",
  providerAttemptRef: `pat_${"a".repeat(32)}`,
  reconciliationRef: "operator:package-contract",
};
const decision: TrustedActionReconciliation = createTrustedActionReconciliation(options);
void decision;

createTrustedActionReconciliation({
  ...options,
  // @ts-expect-error dispositions are a closed contract
  disposition: "unknown",
});

// @ts-expect-error package consumers cannot import the internal implementation subpath
await import("@context-engine/action-plane/internal.js");
