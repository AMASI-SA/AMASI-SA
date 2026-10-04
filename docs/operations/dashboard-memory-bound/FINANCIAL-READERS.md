# Dashboard-only financial input readers — isolated evidence

Runtime checkpoint: `b1b5522c379e86ad0f3a9cea386242b1c2a895ab`.
No competing local test/benchmark workload ran during this measurement. Windows, Python3.13, local Mongo8.0.12. Fresh worker per mode; 2KiB synthetic unused fields. One obligation, N invoices and N adjustments. This is reader evidence, not a full Dashboard benchmark.

| N per collection | Legacy peak MiB | Bounded full peak MiB | Legacy s | Bounded full s | Same-cohort parity | Full cohort complete |
|---:|---:|---:|---:|---:|---|---|
|10000|116.86|79.89|0.962|1.063|PASS|PASS|
|50000|237.67|80.25|5.074|6.327|PASS|PASS|
|100000|253.67|80.24|5.023|14.423|PASS|PASS|

Bounded max wire batch:128. Totals compare identically on the same cohort. Above oldcaps, fullDashboard reads all rows whereas legacy financial callers intentionally retain unchanged20k/50k loader caps. The last actual invoice changes the fixture recurring total2.00->1.00 beyond20k; this is restored completeness, not a changed formula. At100k legacy consumes only50k adjustments and20k invoices, so the full-cohort14.4s versuslegacy5.0s is not a same-work latency comparison. No latency speedup is claimed.

Raw evidence includes actual wire documents (which can exceed to_list cap due cursor prefetch), same-cohort timing, response bytes, per-stage timing and hashes. All seeded databases were dropped.

Fresh integrated local test suite:128PASS/0FAIL/0SKIP. Exact runtime checkpoint CI:31PASS/0FAIL/0PENDING/4SKIP. Skips: manual Emergent redeploy and Host20 release rehearsal notrequested; two Snapchatsettings scoped jobs notapplicable. CodeQL Python/JS, Security and dedicated Dashboard Real Mongo/frontend paging pass.

Other financial functions/classes match the base AST. Current carrier counters match old/current full responses across iMile->StoreCourier. No Shipping/Supplier/Mobile/writer changes.

**PR remains WIP:** summary100k latency108-130s, high-cardinality financial group maps/response, and cart-date edge compatibility remain blockers. Do not merge/release based on this reader improvement.
