---
name: adr-0048-generated-typescript-sdk-facade
version: "1.0.0"
description: >
  Generate the TypeScript resolve contract from OpenAPI v0 and expose it only
  through a narrow metadata-safe facade and package export map.
---

# 0048. Generate the TypeScript SDK behind a closed facade

- Status: accepted
- Date: 2026-07-23
- Refines: ADR-0017, ADR-0045, ADR-0047

## Context

BotDelivery and other TypeScript callers need an installable client for the
frozen `/v0/resolve` contract. Handwritten request, response, Package, or union
types would create a second semantic contract. Directly exporting a generated
fetch client would create a different problem: its generic request options
allow arbitrary headers even when the OpenAPI operation declares only accepted
metadata. That would invite callers to manufacture raw trusted identity,
audience, purpose, or ACL claims.

The repository also pins Node below the minimum supported by the latest tested
generator release. Generator selection therefore has to be explicit,
reproducible, and verified against the repository runtime rather than silently
following a floating latest version.

## Decision

Generate the TypeScript semantic types and internal fetch implementation from
the immutable OpenAPI v0 snapshot with an exact pinned open-source generator.
Pin Node, npm, the generator, TypeScript, and Node declarations in the SDK
package and lockfile. Retain a versioned generated-tree digest and the packaged
OpenAPI checksum. A clean generation runs into a temporary directory and fails
on any byte, file-set, generated-tree digest, or contract-checksum difference.

The package export map exposes only the root facade and the OpenAPI checksum.
It does not export generated implementation subpaths. The facade re-exports the
generated semantic request, outcome, Package, and variant types; it does not
handwrite semantic wire schemas or HTTP serialization. Its only inputs are the
base URL, transport authentication, request id, optional opaque
`DeliveryEvidenceRef`, and generated closed `ResolveWire`. It maps those values
to the generated operation and distinguishes a closed HTTP error from a
transport failure.

The generated SDK carrier is active only for the packaged `/v0/resolve` client.
Acquire is proven end to end against a real local HTTP server, PostgreSQL,
private delivery evidence, File-backed CandidateRef, the sealed
AuthorizationKernel, AuthorizedProjection, ContextPackage, and opaque model
egress grant. Continue and OpenCitation are generated and callable, but their
real issuance and redemption remain inactive; they return their generic
unavailable outcomes through direct authenticated delivery.

Package tests install the produced tarball into a clean temporary consumer.
Compile-negative fixtures prove trusted body fields, unknown union variants,
raw facade headers, and generated-client deep imports remain unavailable. The
tarball and all build, dependency, and test caches stay ignored.

## Rationale

OpenAPI generation gives the request, outcome, and Package types one semantic
source of truth, while the facade closes the generator's deliberately generic
transport options at the supported package boundary. This split keeps codegen
replaceable and auditable without letting callers bypass ingress ownership of
trusted facts. Exact runtime and tool pins make a generated diff a deliberate
review event instead of an environment-dependent artifact.

## Consequences

- TypeScript semantic drift is rejected at generation and package-consumer
  boundaries.
- Callers cannot use the supported package surface to inject arbitrary headers
  or import the raw generated transport.
- Updating the generator or runtime is a reviewed lockfile and generated-tree
  change, while changing OpenAPI v0 still requires the versioning process from
  ADR-0047.
- The SDK remains repository-private until a separate release issue defines
  registry ownership, signing, provenance, and publishing credentials.

## Revisit trigger

Revisit the facade only if the generator can express an operation-scoped client
whose public type and runtime surface structurally forbid undeclared headers
without handwritten semantic types. Revisit the inactive capability boundary
only when Continue or OpenCitation has its own accepted persistence,
authorization, redemption, and security evidence.
