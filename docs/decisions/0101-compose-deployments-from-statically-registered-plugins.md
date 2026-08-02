---
name: adr-0101-compose-deployments-from-statically-registered-plugins
version: "1.0.0"
description: >
  Compose deployments from pinned statically registered plugins at existing
  authority seams without changing the approved process topology. Use when
  packaging ingress, provider, consumer, model, sender, or presentation
  integrations. Not for tenant-uploaded code, hot loading, plugin-owned policy
  or persistence, or unmeasured service extraction.
---

# 0101. Compose deployments from statically registered plugins

- Status: accepted
- Date: 2026-08-02
- Refines: ADR-0008, ADR-0012, ADR-0017, ADR-0048, ADR-0062, ADR-0074, ADR-0075

## Context

ContextEngine already has deliberate variation seams for trusted ingress,
source providers and owned runners, generated consumers, model gateways,
senders, and presentation. It does not yet have a general plugin product or a
versioned deployment composition contract. As integrations grow, ad-hoc wiring
would make active capabilities, secrets, process placement, dependency
direction, and operational health difficult to review. A conventional dynamic
plugin system would be worse: tenant-uploaded or runtime-loaded code inside a
trusted process would inherit authority far beyond its declared integration.

Deployment convenience cannot weaken the fixed topology or deep Module
ownership. The API, independent Supply worker, owned runner subprocesses, and
trusted Bot application already have distinct identity and failure contracts.
A plugin label must not let an Adapter make authorization decisions, hold an
independent index, publish a release, or execute an external effect outside
ActionPlane.

## Decision

1. Deployment/plugin productization is accepted as a low-priority composition
   direction. Plugins are pinned, reviewed implementations of an existing
   declared port and are registered statically at build or process startup.
   There is no tenant-uploaded executable code, arbitrary package name, git URL,
   runtime download, hot reload, `eval`, or ambient discovery from a filesystem.
   This decision activates no plugin loader or new deployment surface.
2. A future plugin can implement only an already accepted port. It keeps the
   dependency direction and authority owner of that port: it cannot import
   around a public Module seam, call persistence through undeclared privilege,
   construct nominal trusted values, or own authorization, Policy Epoch,
   Package assembly, release publication, or effect-ticket semantics.
3. Existing explicit application wiring, runner registration, and deployment
   templates remain the active composition authority. No generic plugin base,
   loader, registry schema, manifest fields, lifecycle, or conformance framework
   is designed until a second real implementation of one port exposes repeated
   wiring or measured deployment drift. That trigger requires a refining ADR
   based on both implementations; this decision does not pre-allocate plugin
   categories or freeze a speculative manifest.
4. Any future composition contract fails closed for unknown implementation
   identity, provenance mismatch, forbidden process placement, unresolved
   required configuration or secret reference, or undeclared capability
   contribution. Secret values stay in one deployment-owned live source and are
   injected only into the process and adapter that requires them. Exact manifest
   fields and validator mechanics belong to the trigger-driven refining ADR.
5. Process placement remains fixed by ADR-0008 and ADR-0075: API, independent
   Supply worker, trusted Bot application, and ContextEngine-owned bounded
   runner subprocesses with exact WorkerLease binding and no independent
   persistence or index. A plugin never creates a service boundary. Any extra
   network process requires measured isolation, scaling, performance, or
   reliability evidence and a new topology ADR.
6. Third-party implementation reuse follows ADR-0074 path by path. A permissive
   package or plugin API does not approve code loading; shipped artifacts must
   have pinned provenance, license/NOTICE, modification record where applicable,
   SBOM coverage, dependency review, and a closed conformance suite. Restricted
   code remains clean-room even if it implements a compatible port.
7. The active deployment composition is a server-owned input to the operation
   discovery registry from ADR-0098, but neither composition nor an operation
   listing is authorization. Every request still authenticates, constructs
   current trusted context, traverses the sealed Kernel, and consumes exact
   read, egress, lease, or effect authority at its owning boundary.
8. Product sequencing is deliberately behind the first external consumer, the
   structured-acquisition contract, and governed Session Intake. Until a second
   real implementation of one port or repeated deployment drift demonstrates
   the need, explicit native wiring remains the sole active authority.

## Rationale

Static registration can provide product-level composability without turning a
plugin manager into a new trusted computing base. Existing deep ports and
process boundaries contain authority. Deferring the generic contract avoids
freezing an abstraction before real integration repetition shows which seams
are stable.

## Consequences

- A future trigger-driven composition contract can describe and validate
  integrations without forking authorization, Package, release, or effect
  contracts.
- The refining ADR must derive conformance tests from the concrete port and both
  real implementations, including their tenant, process, secret, retry,
  idempotency, publication, and effect boundaries.
- Existing explicit application wiring, runner registrations, and deployment
  templates remain the active source of truth.
- A general plugin SDK, dynamic marketplace, tenant code, remote plugin service,
  and versioned deployment manifest remain `NOT_ACTIVE` and low priority.

## Revisit trigger

Revisit when a second real implementation of one accepted port exposes repeated wiring, when
the same deployment must be reproduced across multiple environments, or when a
measured isolation requirement cannot fit the existing process topology. Any
revision must preserve static provenance, secret minimization, one owner per
authority, and fail-closed composition.
