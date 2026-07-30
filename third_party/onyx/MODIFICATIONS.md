# Onyx connector framework modifications

The registered source is copied and aggressively patched from the four MIT-region
files named in `UPSTREAM.toml`. The patch retains the checkpoint-return generator,
batch runner, connector interface, wire-model, and lazy-registry shapes while
removing all Onyx control-plane, database, index, Redis, Celery, tenant, file-store,
Pydantic, logging, hierarchy, heartbeat, and enterprise permission dependencies.

The registry is closed to the File/Obsidian connector. The wire types are reduced
to dependency-free transient values and are translated immediately into
ContextEngine's Supply contracts. Exception handling avoids logging local variables
or credentials, and no code or behavior was copied from an `ee/` path.

The governance registration records each post-patch vendored hash. These independently
verified hashes identify the corresponding original files at pinned commit
`2fb3dd10493b3883870fa8adced5b1a0e114feff`:

| Upstream path | Original SHA-256 |
|---|---|
| `backend/onyx/connectors/interfaces.py` | `293c0dcca9230b75ea3eef1475262e0b4010ca4df9321880f41a9dad05561756` |
| `backend/onyx/connectors/connector_runner.py` | `dc41c82425287c039b0897c135bc45f520eeb88be9b2ef16df159f835a63f311` |
| `backend/onyx/connectors/models.py` | `8edcf633de61d2c769c0959ce744e9efae436083917b7ddb1d692f89eaa4f44b` |
| `backend/onyx/connectors/registry.py` | `439c49bcb7dcc522545176d015dde73ce93d991e4b3e875db4b4d23d94cad9c4` |

The patch directory is reserved for future machine-readable refresh patches; this
initial cut is an intentionally narrow manual cut, not whole-file vendoring of
dependency-entangled upstream modules.
