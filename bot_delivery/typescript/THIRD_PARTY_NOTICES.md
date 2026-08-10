# Third-party notices

Production dependencies are pinned by `package-lock.json`.

- `canonicalize` 3.0.0 — Apache License 2.0. Used only to encode RFC 8785 JSON
  documents before hashing.
- `pg` 8.22.0 and its pinned transitive dependencies — MIT or compatible
  licenses. Used by the sealed boundary to reach the dedicated PostgreSQL
  egress authority; callers cannot inject a database implementation.
- `@context-engine/resolve-sdk-v1` 1.0.0 — private runtime peer package from this
  repository, used for Runtime resolve calls and the frozen public resolve wire
  types.

Complete third-party license texts are included with installed packages and
their published distributions.
