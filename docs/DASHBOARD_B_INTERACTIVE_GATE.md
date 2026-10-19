# Dashboard B interactive concurrency experiment

Test-only gate above computation-only HEAD b4087ba5ec88c7cfc43ee1f3eb29c51182504ac1.
No runtime adoption, no additional optimization. CI artifacts/PR1301 contain the
completed evidence; this document defines the protocol before measurement.

## Load and isolation

Linux/Python3.11/MongoDB8.0.12 replica set, loopback-only UUID fixture databases.
Actual FastAPI Dashboard route, real permission guard, actual Uvicorn HTTP
serialization and unchanged ResourceGovernor policy (global capacity2,
Dashboard weight2, Dashboard semaphore2). Three submitted dashboards therefore
queue under the existing admission policy; do not increase execution concurrency.

Independent OS-process open-loop client issues three lightweight fixture endpoints
every250ms (12 requests/sec): ten-row indexed list, indexed one-order read, and
non-Mongo ping. These are representative bounded test endpoints, not operational
Production routes or an external-provider simulation. Request timing starts at
the scheduled arrival, not when the busy server loop eventually runs the handler.
Record client dispatch delay separately to detect load-source starvation.

Interactive HTTP timeout15s, Dashboard180s; no retries. Client connection limit256,
overflow recorded and fails the harness. Timeout rates and censored observations
are reported alongside successful-request percentiles; never silently discard them.

12 scenarios:10K/50K/100K times single/same-merchant, single/different-merchant,
three/same-merchant, three/different-merchants. Different-merchant heavy requests
have independently seeded equal-size data, and probes target another tenant.
Each scenario: light-only baseline5s, one warmup pair, six alternating measured
pairs. HTTP Dashboard byte hashes must match pairwise for every merchant/request.
Six single-dashboard samples or18multi samples per design cannot establish a
precise population Dashboard p99; nearest-rank p95/p99 are reported with counts.
Interactive measurements have many more observations but remain burst-correlated.

## Observations and interpretation

- HTTP scheduled/request p50/p95/p99, status/error/timeout, response bytes/hash,
  offered and completed throughput, dispatch delay.
- ASGI execution, pre-ASGI transport/scheduling wait (not pure event-loop wait).
- Observer delegates governor acquire/release unchanged: admission wait,
  admitted execution, post-handler-to-response-headers time including actual
  FastAPI serialization. Lightweight endpoints do not request governor admission.
-10ms server heartbeat including distribution of maximum lag per wave.
- Main API-thread CPU per wave, Mongo driver command latency distribution by
  probe/dashboard, commands/documents, coarse synchronous-function spans.
- Functions remain synchronous in this experiment; spans support RCA only.
  Overlapping Mongo and CPU phases must not be added to derive request time.

Predeclared diagnostic materiality screen: interactive p95 or p99 rises both
more than10% and25ms, or timeout rate rises more than1percentage point. Examine
each endpoint/scenario and paired-wave consistency; a borderline or noisy result
is inconclusive, not an adoption pass. This is an explicit experiment threshold,
not a new business contract or universal performance SLO.

Correctness first: rerun all former mutation gates and mixed full-JSON fixtures,
tenant/permission checks, fresh-request visibility and existing regressions; smoke
the HTTP harness before the large matrix. No DB sharing/cache/snapshot or changes
to queries, filters, formulas, statuses, governor capacity/weights or timeouts.

If interaction materially worsens, stop at RCA. Do not implement isolation,
chunking, C/E or another optimization in this gate. No merge/prepare/deploy or
Production requests/writes. Production writes=0.
