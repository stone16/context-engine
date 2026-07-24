# ContextEngine resolve SDK

This package is generated from the immutable ContextEngine OpenAPI v0 snapshot.
Its public facade accepts only transport authentication, a request id, an
optional opaque DeliveryEvidenceRef, and the closed generated resolve request.
It does not expose raw trusted Organization, Principal, Membership, purpose,
audience, or ACL inputs.

The accepted contract checksum is packaged at
`@context-engine/resolve-sdk/contract/openapi-v0.sha256`. Generation provenance
and exact tool versions are locked by this directory's `package.json` and
`package-lock.json`.

The supported runtime API is `ContextEngineResolveClient.resolve`. It accepts a
generated `ResolveWire` plus `X-Context-Request-Id` and, for an eligible private
Acquire, an opaque `DeliveryEvidenceRef`. The generated fetch implementation is
an internal package detail and is intentionally absent from the export map.

Repository commands are `make sdk-generate`, `make sdk-check`,
`make sdk-build`, `make sdk-test`, and `make sdk-pack`. Packing writes only to
the ignored `.context-engine/sdk/` directory. Publishing to a package registry
is not part of this carrier activation.
