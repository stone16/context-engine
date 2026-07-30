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

The original full-file hashes and post-patch hashes are both registered. The patch
directory is reserved for future machine-readable refresh patches; this initial cut
is an intentionally narrow manual cut, not whole-file vendoring of dependency-
entangled upstream modules.
