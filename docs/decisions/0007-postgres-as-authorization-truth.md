---
name: adr-0007-postgres-as-authorization-truth
version: "1.1.0"
description: >
  Record that PostgreSQL 17 is the non-pluggable authorization truth store:
  composite FKs to Organization, FORCE RLS, non-owner runtime role, fail closed
  on missing tenant context. Use when reviewing schema, storage, or cache design.
---

# 0007. PostgreSQL is the authorization truth store

- Status: accepted
- Date: 2026-07-18

## Context

ContextEngine is multi-tenant, and a bug in application filtering, cache lookup,
index metadata, or background-job context must not turn into cross-organization
access. Tenant ownership and row visibility therefore need relational invariants
that remain effective below application code. The security oracles
TENANT-OWNERSHIP-001, TENANT-FK-002, and RLS-FAIL-CLOSED-003 require one durable
authority that fails closed when organization context is absent.

## Decision

PostgreSQL 17 holds authorization truth and tenant-owned metadata. Every
tenant-owned table carries a composite FK to Organization; **FORCE RLS** is on;
the runtime connects as a **non-owner role**; missing tenant context fails
closed. Index/vector stores and caches are candidate-narrowing optimizations,
never authorization authorities (INDEX-NOT-AUTHORITY-005).

## Consequences

Security integration tests must run against real PostgreSQL 17 with the
non-owner role every commit; in-memory substitutes do not count for the security
gate. Retrieval is PostgreSQL-specific in V1 with an internal test seam
(ADR-0009); any future backend portability contract requires a second real
implementation. The authorization truth store remains PostgreSQL regardless.

ADR-0012 clarifies that PostgreSQL is the engine authorization and enforcement
truth while a live external source may provide current SourceNativeACL evidence.
ADR-0015 defines the transaction-local context and all-table schema manifest
needed to make the RLS claim executable.
