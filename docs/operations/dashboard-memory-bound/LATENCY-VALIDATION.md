# Dashboard latency validation — WIP, not ready

Runtime measured: `9358ab134025685a64f30492f18c15663a37ff83`.

All datasets are synthetic, on loopback MongoDB. Fresh worker processes ran sequentially with no competing local test workload. Same-key concurrency shares in-flight work; independent tenants are reported separately. Memory is process peak RSS, not incremental allocation.

| Orders per tenant | Concurrency | Before s | After s | Before MiB | After MiB | Financial parity |
|---:|---:|---:|---:|---:|---:|---|
| 10000 | 1 | 3.017 | 3.705 | 198.87 | 107.67 | True |
| 10000 | 2 | 5.637 | 3.764 | 199.17 | 107.81 | True |
| 10000 | 4 | 12.836 | 4.575 | 200.71 | 107.85 | True |
| 50000 | 1 | 17.494 | 23.989 | 627.51 | 107.50 | True |
| 50000 | 2 | 31.911 | 22.187 | 624.79 | 107.71 | True |
| 50000 | 4 | 58.743 | 18.433 | 623.55 | 107.82 | True |
| 100000 | 1 | 34.850 | 43.973 | 1157.49 | 107.67 | True |
| 100000 | 2 | 61.383 | 42.798 | 1157.33 | 107.83 | True |
| 100000 | 4 | 130.823 | 48.821 | 1158.98 | 107.82 | True |

## Three independent tenants

| Orders per tenant | Concurrency | Before s | After s | Before MiB | After MiB | Financial parity |
|---:|---:|---:|---:|---:|---:|---|
| 10000 | 3 | 11.791 | 16.148 | 197.16 | 113.80 | True |
| 100000 | 3 | 114.432 | 140.206 | 1160.19 | 113.64 | True |

The 100k independent-tenant latency remains a blocker (140.206 s). Memory is bounded across measured collection sizes, but this is not a readiness claim. Full raw evidence includes Mongo document/command counts, serialization time, response size and private storage cleanup.

## Stage profile

Instrumented 100k total: 50.324 s. Inclusive async intervals overlap and must not be summed. This profile adds overhead; the table above is the uninstrumented latency evidence.

| Stage | Seconds |
|---|---:|
| Legacy summary (inclusive) |34.103|
| Product aggregation (inclusive) |12.562|
| Order snapshot (inclusive) |8.816|
| Mongo batch fetch waits |2.309|
| Canonical singleton parsing |5.580|
| Private decode (exclusive) |6.997|
| Private SQL (exclusive) |2.566|
| Private encoding (exclusive) |2.415|
| compute_balances (inclusive) |1.130|

## Next measured slice

Fuse primary and electronic cohort reductions, reusing unchanged canonical singleton calculations. Do not rewrite money rules. A new regression reproduced 336 vs 240 and 288 vs 192 repeated parses; after fusion, full-response parity and single-parse assertions pass. Focused summary/carrier/cardinality suite: 21 passed. Accumulator reuse suite: 23 passed.

## Explicit scope exclusion

`product-cost-summary` and `sold-products` are excluded by owner decision and require separate measurement/PRs. No Shipping, Supplier, Mobile, financial writer or Production change.

CI on measured 9358: 31 passed, 0 failed/pending, 4 skipped (existing manual release/rehearsal and Snapchat scope jobs). The new slice needs its own exact-HEAD CI and benchmarks.
