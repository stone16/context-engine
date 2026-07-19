---
name: adr-0010-policy-epoch-revocation
version: "1.1.0"
description: >
  Record the revocation mechanism: synchronous Policy Epoch bump invalidates the
  next request immediately; index/cache/blob cleanup is asynchronous. Use when
  designing any permission change, offboarding, or cache invalidation path.
---

# 0010. Revocation = synchronous epoch bump, asynchronous cleanup

- Status: accepted
- Date: 2026-07-18

## Context

Index deletion, blob deletion, and cache expiry are asynchronous maintenance
operations and cannot provide a deterministic next-request revocation guarantee.
ContextEngine's REVOCATION-006 invariant requires a durably observed access
change to block subsequent reads without waiting for those cleanup paths.

## Decision

Revoking access **synchronously bumps the Policy Epoch**; every ticket/cache
entry carries the epoch it was issued under, and a stale epoch fails the
request. Physical cleanup of indexes, caches, and blobs proceeds asynchronously.
Deletion semantics split three ways: content delete (tombstone, immediately
invisible), access revoke (epoch bump), source offboarding (disable
SourceVersion + revoke credential + org epoch bump).

## Consequences

Revocation latency is one epoch check, not one cleanup queue. Epoch granularity
(organization vs source/resource level) remains an open question with a
prototype gate — the mechanism itself is fixed.

The next-request guarantee applies after the engine has durably observed and
committed the access change and epoch bump. It cannot cover an upstream change
that has not arrived through event, poll, or live authorization, and it cannot
recall bytes already delivered to an external system. ADR-0012 and ADR-0013
define those source-freshness and delivery consequences.
