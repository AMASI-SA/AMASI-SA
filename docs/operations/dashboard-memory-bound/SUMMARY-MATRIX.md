# Actual V2 summary matrix — intermediate, NOT READY

Runtime source checkpoint: `3f8d4027be823cc01c6cb66ccb4277df9448edac`. Later dedicated CI/test checkpoint `1d23eccbe97b2e91ea667bfe00f473582cb82178` did not change runtime code during the matrix. This matrix predates the financial-reader integration.

Local Mongo 8.0.12, Python 3.13, Windows; synthetic 1KiB item padding and 1,000 product identities. Fresh child process per sample. Same request keys exercise duplicate coalescing, not independent tenant saturation. Default legacy admission serializes requests. OS scheduling/CPU contention makes latency noisy; no production extrapolation.

| Dataset orders | Requests | Before peak MiB | After peak MiB | Before wall s | After wall s | Before wire docs | After wire docs | Totals parity |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10000 | 1 | 199.04 | 104.77 | 4.54 | 18.41 | 37366 | 19187 | PASS |
| 10000 | 2 | 198.76 | 104.72 | 9.37 | 23.36 | 74732 | 19187 | PASS |
| 10000 | 4 | 200.29 | 104.67 | 27.43 | 27.06 | 149464 | 19187 | PASS |
| 50000 | 1 | 627.27 | 104.96 | 39.55 | 105.49 | 182822 | 91915 | PASS |
| 50000 | 2 | 627.32 | 105.04 | 75.51 | 95.30 | 365644 | 91915 | PASS |
| 50000 | 4 | 621.40 | 104.88 | 75.27 | 57.20 | 731288 | 91915 | PASS |
| 100000 | 1 | 1126.82 | 104.78 | 38.76 | 118.16 | 364642 | 182825 | PASS |
| 100000 | 2 | 1157.53 | 104.85 | 62.66 | 107.99 | 729284 | 182825 | PASS |
| 100000 | 4 | 1156.91 | 104.75 | 133.96 | 130.06 | 1458568 | 182825 | PASS |

All requests succeeded, all financial signatures match, all private spill directories were cleaned. The full response intentionally differs because detail pages replace full product lists. At 100k, only 50 product detail rows are returned; response size is about 54KiB instead of about 610KiB.

**Blocker:** 100k elapsed 108–130s, close to/exceeding the operational proxy timeout. A flat ~105MiB peak is not readiness. Sequential replay/serialization and duplicate passes still need reduction. Raw distinct financial label cardinality is another unbounded response/group-map risk.

Total documents streamed is not simultaneous memory occupancy. At 100k the current summary reads 182,825 wire documents in batches, rather than retaining them all. Private temporary disk peaked around 121MB under the fixed 256MiB allowance. Different larger documents/cardinalities require additional evidence.

Small isolated tests ran during portions of the matrix; this is disclosed as possible CPU/Mongo contention. Final latency evidence must be measured without competing local workloads. The full machine-readable result is `summary-matrix-3f8d4027.json`.
