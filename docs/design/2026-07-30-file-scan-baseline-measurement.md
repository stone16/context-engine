# File scan bound measurement

**Measured:** 2026-07-30 on Python 3.13.5, Darwin arm64. The tracked aggregate
result is
[`2026-07-30-file-scan-measurement.json`](../evaluation/2026-07-30-file-scan-measurement.json).

## Reproduction

Run the measurement from the repository root. It generates all inputs beneath
a temporary directory and overwrites the aggregate-only tracked report:

```bash
uv run context-engine-file-scan-measurement \
  --output docs/evaluation/2026-07-30-file-scan-measurement.json
```

The command creates 100 directories and distributes fixed, constant-content
Markdown files across them. It calls the production `FileChangeProvider` at
1,000, 5,000, 10,000, and 15,000 paths with the production singleton page
limit. For each size it measures one initial call and one signed continuation
call with `perf_counter`, and records peak Python allocations with
`tracemalloc`. The full-cycle value is an estimate, not an executed full
cycle: initial-call time plus page count minus one multiplied by the measured
continuation-call time. That calculation exposes ADR-0071's current full-root
revalidation cost without spending many hours executing every singleton page.

## Results

| Paths | Initial wall time | Continuation wall time | Peak Python memory | Pages | Estimated singleton cycle |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 0.261 s | 0.236 s | 0.89 MiB | 1,000 | 236 s |
| 5,000 | 1.188 s | 1.149 s | 4.14 MiB | 5,000 | 5,744 s |
| 10,000 | 2.359 s | 2.433 s | 8.20 MiB | 10,000 | 24,333 s |
| 15,000 | 3.647 s | 3.612 s | 12.23 MiB | 15,000 | 54,181 s |

Peak memory grows approximately linearly across the measured sizes and stays
bounded by the configured path ceiling. Wall time for a single snapshot also
grows approximately linearly. The composed singleton cycle is operationally
material because every continuation revalidates the whole tree; the estimated
cost grows quadratically, which trips ADR-0071's revisit trigger.

## Maintainer-gated options

Both supported configuration paths are measured and neither is selected here:

- **Configurable whole-vault bound:** the 15,000-path measurement uses 12.23
  MiB peak Python memory, takes 3.647 seconds for the initial snapshot and
  3.612 seconds for one continuation, emits 15,000 singleton pages, and yields
  a 54,181-second full-cycle estimate.
- **Curated subtree:** the configured 5,000-path selection uses 4.26 MiB peak
  Python memory, takes 1.165 seconds for the initial snapshot and 1.165 seconds
  for one continuation, emits 5,000 singleton pages, and yields a 5,826-second
  full-cycle estimate. Actual cost follows the selected subtree's path count.

The configurable path does not change ADR-0065's 10,000 default. Changing the
default later requires an ADR that refines ADR-0065. Issue #126 may re-house
File traversal in a connector runner; if it replaces this provider path, rerun
this measurement against that implementation. Making the composed scan fast
remains the selected-upsert or restart-safe-snapshot work owned by ADR-0071,
not this measurement.
The curated option is measured through the explicit curated-subtree registry
configuration while retaining the registered root as the read capability and
preserving root-relative path identities.
