# MCP authorized-resolve end-to-end transcript

**Run date:** 2026-08-02; rerun 2026-08-03 after E-215 repairs

**Carrier:** spawn-per-session stdio MCP child -> locally served loopback HTTP
`POST /v0/resolve` -> sealed Runtime composition -> real PostgreSQL 17/File
Provider path

**Executable oracle:**
`tests/integration/test_dogfood_runtime_activation.py::test_spawned_mcp_stdio_delivers_only_real_http_authorized_evidence`

This is a deliberately sanitized semantic transcript, not raw MCP frames or a
raw `ContextPackage`. The source test retains the exact machine assertions at
[`tests/integration/test_dogfood_runtime_activation.py`](../../tests/integration/test_dogfood_runtime_activation.py#L408).
Bearer material, URL and port, Organization/User/Membership identifiers,
request IDs, resource/revision references, opaque grants, and the raw Package
document are omitted.

## Client session

```text
client -> initialize
server -> initialized: context-engine-mcp 0.1.0

client -> tools/list
server -> tools: [context_resolve]
```

The listed tool accepted the closed HTTP `AcquireWire` shape only. Trusted
identity, audience, delivery-evidence, egress, and credential fields were not
present in the tool schema.

## Authorized resolve

```text
client -> context_resolve
  kind: acquire
  need: repository-context question
  packageBudget: maxTokens=1024, maxProviderCalls=1

server -> resolved
  blocks: 1 authorized Block
  evidence: 1 matching Evidence lineage
  candidate material: absent
  HTTP parity after request-scoped-field removal: exact match
```

The delivered Block contained the fixture's authorized File text. The Evidence
entry matched the published Article revision; its opaque references are not
reproduced here.

## Unauthorized narrowing

```text
client -> context_resolve
  kind: acquire
  need: same repository-context question
  requestNarrowing: one non-authorized resource

server -> resolved
  blocks: 0
  evidence: 0
  coverage: empty / no_authorized_evidence
  authorized fixture text: absent
  HTTP parity after request-scoped-field removal: exact match
```

## Exhausted budget

```text
client -> context_resolve
  kind: acquire
  need: same repository-context question
  packageBudget: maxTokens=1

server -> resolved
  blocks: 0
  evidence: 0
  budgetUsage.tokens: 0
  authorized fixture text: absent
  HTTP parity after request-scoped-field removal: exact match
```

## Fresh command evidence

```text
$ set -a; source .context-engine/database.env; set +a; \
    uv run pytest -q \
    tests/integration/test_dogfood_runtime_activation.py::test_spawned_mcp_stdio_delivers_only_real_http_authorized_evidence
.                                                                        [100%]
1 passed in 3.76s
```

The test initializes the MCP client, asserts the single listed tool, performs
all three calls through one spawned stdio session, compares each result with a
separate call to the same public HTTP seam, and tears down the PostgreSQL/File
fixture afterward.
