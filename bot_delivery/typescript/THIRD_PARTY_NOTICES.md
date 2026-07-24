# Third-party notices

Production dependencies are pinned by `package-lock.json`.

- `canonicalize` 3.0.0 — Apache License 2.0. Used only to encode RFC 8785 JSON
  documents before hashing.
- `pg` 8.22.0 and its pinned transitive dependencies — MIT or compatible
  licenses. Used by the sealed boundary to reach the dedicated PostgreSQL
  egress authority; callers cannot inject a database implementation.
- `@context-engine/resolve-sdk` 0.0.0-v0 — private peer package from this
  repository, used only for the frozen public resolve wire types.

Complete third-party license texts are included with installed packages and
their published distributions.
