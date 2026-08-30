# PR 1 memory baseline and startup inventory

Captured 2026-08-30 before code changes. This is evidence for a draft PR, not
a production-release record. No republish, resource-tier change, backfill, or
provider/financial write was performed.

## Release identity baseline

- `origin/hotfix/prod-snap-meta-final`: `37f8c11be87997716e3e9454696b89faddc3a79d`
- Three uncached `GET /api/health` requests returned HTTP 200.
- All three reported live `git_sha` and `source_git_sha`
  `c320c293d1359060ae48931f091046a3e274b309`.
- All three reported release id
  `rg5-5c5bc3036e5025805b14fba5e89ee4358bd1378aa225568422633cfb0e3741ed`,
  protocol 5, identity kind `mezan_runtime_release_identity_v5`, schema 1,
  verified identity available, critical hashes matching, and frontend build
  verified.
- All three reported `boot_started_at=2026-08-30T17:15:30.976315+00:00`.
- GitHub and live differ. This PR does not attempt to reconcile that gap.

The current platform deploys backend and frontend in one image/lifecycle. A
Python subprocess in the same cgroup would not provide memory isolation and is
not proposed here.

## Baseline memory evidence

The pre-change health payload exposes neither cgroup/process memory nor Mongo
pool telemetry. Consequently a truthful historical boot/request memory curve
cannot be reconstructed from that endpoint. The observable timeline is:

| UTC time | Observation |
| --- | --- |
| 17:15:30.976 | live process boot started |
| 17:22:37 | health 200, same verified identity |
| 17:22:38 | health 200, same verified identity |
| 17:22:39 | health 200, same verified identity |

Emergent's incident evidence reported web-process restarts/503s, Mongo checkout
exhaustion in dashboard/product-cost work, large-query timeouts near 5,000 rows,
and `maxPoolSize=5`. The repository constructs `AsyncIOMotorClient(mongo_url)`;
the effective size may therefore come from the deployment URI. PR 1 does not
change it. The new local diagnostics reads the effective driver option without
performing a database operation.

## Startup task inventory

Classification reflects whether a task must block readiness, can safely run
after liveness, is heavy background work, or is periodic.

| Entry point | Classification | PR 1 policy |
| --- | --- | --- |
| FastAPI import/router composition | required-before-liveness | unchanged |
| release identity health route | required-before-liveness | local-only |
| core/auth/index initialization in `server._deferred_startup` | required-before-readiness | after liveness, readiness remains 503 |
| legacy data cleanup/migrations in `server._deferred_startup` | safe-after-liveness; potentially heavy | serialized deferred initialization |
| Qoyod inbox worker | periodic | starts during deferred initialization; existing bounded batches retained |
| Qoyod Plan-B worker | periodic | starts during deferred initialization; existing arming gates retained |
| Salla token maintenance | periodic | starts after deferred initialization and uses existing Mongo lease |
| Ads auto-sync scheduler | heavy-background/periodic | startup delay + scheduler lease + global resource governor |
| Snapchat V2 shadow scheduler | heavy-background/periodic | startup delay + existing per-account lease + global limit 1 |
| Campaign AI monitor | heavy-background | already disabled in web process |
| Customer learning worker | heavy-background | already disabled in web process |
| ad-account half-hour sync | heavy-background | already disabled in web process |
| ad-spend posting loop | periodic financial | already disabled in web process; not changed |
| BNPL hourly sync and Tamara sweep/migration | heavy-background | already disabled in web process |
| product taxonomy recovery | safe-after-readiness | router startup task; retains its recovery fence |
| Snapchat CAPI recovery | safe-after-readiness | router startup task; no reporting-fact authority |
| advertising product watch | periodic | router startup task, delayed loop |
| campaign AI subprocess scheduler | periodic | router startup task; separate existing controls retained |

## Query and concurrency map

| Work | Baseline shape | Baseline concurrency | PR 1 action |
| --- | --- | --- | --- |
| Dashboard V2 | up to 100k orders plus optional second attribution query | legacy/V2 and downstream cost/ads/obligations overlap | separately configurable dashboard governor; query rewrite is PR 2 |
| Product cost | large order list remains live during parallel work | overlaps dashboard downstream calls | observation/admission only; semantic rewrite is PR 2 |
| Abandoned carts | up to 100k then Python filter/sort | request-driven | observation/admission only; Mongo rewrite is PR 2 |
| Ads auto-sync | target coroutines previously allocated through unbounded gather | semaphore 3 | bounded iteration; default global ads limit 2 |
| Snapchat shadow accounts | all account coroutines previously allocated through gather | `MAX_PARALLEL_ACCOUNTS=2` | bounded iteration; default global and local limit 1 |
| Snapchat account sync | existing distributed account lease | one lease holder/account | lease reused; no parallel lease system |
| Startup migrations/indexes | one long blocking startup handler | replicas can boot simultaneously | liveness first, fixed delay+jitter, readiness after completion |

## Metrics and privacy

PR 1 records bounded stage metrics: stage/timestamps/duration, cgroup current,
limit/events/peak, RSS/USS/peak RSS, rows/pages/bytes/concurrency when supplied,
status and reason. Diagnostics include event-loop lag and effective Mongo pool
size. Provider payloads, tokens, customer details, addresses, phone numbers,
and row-level logs are excluded.

The driver now has a bounded pool/command listener for active and checked-out
connections, checkout wait P50/P95/P99, checkout failures, operation duration
P50/P95/P99, and timeout counts. Query evidence retains only command and
collection names; filters, values and returned documents are never retained.

## Independent review hardening

- Cancel evaluation has priority over the blocked hysteresis latch, including
  the tested `81% -> 86%` transition. Diagnostics call the pure `peek()` path
  and cannot change the latch.
- `/health/diagnostics` and `/api/health/diagnostics` require the independent
  `INTERNAL_DIAGNOSTICS_TOKEN`; public `/health` and `/ready` remain minimal.
- A weighted global capacity gate bounds different heavy work classes together,
  in addition to per-kind limits. Dashboard V2, product-cost summary, abandoned
  carts, provider auto-sync, Snapchat runs and deferred startup emit stage logs.
- Normal API traffic receives a retryable 503 until readiness; liveness,
  readiness and authenticated diagnostics remain reachable. ASGI lifecycle
  tests cover success, failure and shutdown cancellation.
- Startup jitter combines a hostname/replica hash with secure randomness, and
  a Mongo lease serializes heavy initialization across replicas.
- Ads auto-sync uses two fixed workers with a bounded queue; Snapchat remains
  fixed at one. Results retain target ordering.
- `memory.peak` is reported explicitly as `cgroup_lifetime_peak_bytes`, not as
  a stage-local peak.
- Mongo checkout failures are separated into timeout, pool-closed,
  connection-error and other counts.
