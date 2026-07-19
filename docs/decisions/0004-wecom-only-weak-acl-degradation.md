---
name: adr-0004-wecom-only-weak-acl-degradation
version: "1.2.0"
description: >
  Record that the WeChat connector targets WeCom (企业微信) only, personal WeChat is
  out of scope, and the weak-ACL-source degradation semantics become a first-class
  standard. Use when scoping any messaging-source connector or a source without
  native ACLs.
---

# 0004. WeCom only; weak-ACL degradation is a first-class standard

- Status: accepted
- Date: 2026-07-18

## Context

ContextEngine requires every production connector to expose supportable identity,
authorization, freshness, deletion, and audit semantics. A personal-account
integration cannot satisfy that product and compliance contract through an
approved enterprise integration surface. Only official enterprise integration
surfaces are in scope. WeCom is the candidate connector for this product family,
but archive access, source ACL strength, retention, regional terms, and event
behavior must still be proven before implementation is scheduled.

## Decision

- The WeChat connector targets **WeCom only**; personal WeChat is an explicit
  non-goal recorded in the design doc.
- Sources without native ACLs get a first-class degradation semantic:
  **conversation/group membership acts as the ACL + declared freshness bound +
  sensitive content fails closed**, delivered as a message-type source under the
  standard checkpoint/tombstone contract (PROV-011). Any future weak-ACL source
  (e.g. Discord) enters through this same lane.
- Sequencing: WeCom is feasibility-only in P3. Archive access, ACL evidence,
  events, regional/retention/compliance constraints, official API terms, and
  cost must produce an explicit go/no-go before any implementation milestone is
  promised.

## Considered Alternatives

- Personal WeChat via unofficial protocol, self-use only — rejected: hosts an
  "unsellable" connector inside a product engine, demands edition isolation, muddies
  the compliance narrative.
- Dropping WeChat entirely — rejected: WeCom remains reachable through official APIs
  and matters to the ICP.

## Consequences

The ContextProvider contract gains a documented degradation profile instead of
per-source exceptions. WeCom's session-archive terms, ACL evidence, regional
constraints, retention, and cross-border egress (EGRESS-011) remain evidence
gates, not a scheduled delivery commitment.

ADR-0012 refines the degradation rule: weak membership ACL is legal only for a
source that declares no finer native ACL. It is never a fallback when a live or
mirrored strong-ACL path is unavailable. WeCom implementation is scheduled only
after its separate feasibility gate.
