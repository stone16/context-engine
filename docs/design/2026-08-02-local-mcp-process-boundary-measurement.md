# Local MCP process-boundary measurement

**Measured:** 2026-08-02 against the issue #215 worktree based on
`origin/main` at `1a391c8`, after the MCP contract import boundary was made
transitively engine-independent.

## Measurement boundary

Issue #215 names a real MCP-native coding-agent host. That host requires stdio
MCP framing and starts the configured server as a child process; co-resident
API routing is not an available host transport. The candidate boundary is
therefore one spawn-per-host-session child that translates MCP to the already
active loopback HTTP process, versus adding the MCP SDK and its session state to
the API process.

The import measurement runs each application import in a fresh Python 3.13
interpreter. Counts are process-local loaded-module counts immediately after
import, not installed-package counts or a request-latency benchmark. Provider
modules are the concrete repository adapters named by the boundary test;
database clients are `sqlalchemy` and `psycopg` module trees.

## Observed isolation

| Fresh application import | Total modules | MCP SDK | `engine` | database clients | concrete Provider adapters |
|---|---:|---:|---:|---:|---:|
| `applications.mcp` | 606 | 107 | 0 | 0 | 0 |
| `applications.api` | 776 | 0 | 84 | 172 | API-owned |

The exact MCP boundary is executable as
`MCP-IMPORT-BOUNDARY-215`. It imports `applications.mcp` in a fresh child and
fails if any engine, database-client, concrete Provider, or dogfood-evaluation
module is transitively loaded. The HTTP `AcquireWire` and
`ResolutionOutcomeWire` remain the one semantic contract; the frozen JSON
validation primitives they share with Runtime live in one transport-neutral,
engine-independent `context_engine_contracts` owner.

## Decision evidence

The separate process is justified by measured isolation, not throughput:

- the real host requires child-process stdio and supplies the process lifetime;
- MCP protocol/session dependencies load in the child and add zero modules to
  the API import graph;
- the child loads zero engine, database-client, or Provider modules, so it
  cannot become a second Runtime composition;
- all content work remains one authenticated loopback `POST /v0/resolve` in the
  existing API process.

No latency or capacity benefit is claimed. The boundary adds one process start
and loopback request per host session/question; that overhead is accepted only
for the measured dependency and authority isolation above. Remote/shared MCP,
a long-lived MCP service, and any second human require a new measurement and
topology decision.
