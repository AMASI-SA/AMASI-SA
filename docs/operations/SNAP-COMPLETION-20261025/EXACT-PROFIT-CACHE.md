# Exact campaign profitability — WIP, not release-ready

Base: production `72ab1aee0fa9520b3a5f91f924906a3be3337531`. This adapter uses exact locally matched Salla orders and selected-account Snapchat spend. It does not certify upstream synchronization completeness or full campaign net profit.

## Final behavior

Cache reuse is disabled, with effective `CACHE_TTL_SECONDS=0` and no cache lookup or store. The durable product-cost revision does not cover every dependency: a catalog SKU/name change can alter line identity resolution without increasing that revision, and a general component update can change a resource's `unit_cost` without increasing it. Policy content and order/spend fingerprints therefore cannot make this cache safe. Every request now loads the current cost context and recalculates. The report explicitly returns `cache_reuse_enabled=false` and the reason; there is no dormant context fingerprint claimed as complete.

Actual policy content (defaults plus overrides) and cost-revision observations still bracket context loading and calculation. An observed change discards the attempt and retries once; continued churn raises `campaign_profitability_snapshot_unproven` without returning a known mixed result. These are optimistic fences, not an atomic Mongo snapshot. Missing upstream Salla provenance still blocks scaling. No timestamp or positive readiness certificate is generated.

The original exact-match wrapper queried orders a second time after the visible sales/count report had already been calculated. The two Salla outcome producers now accept an optional internal `matched_orders_out` sink and populate it in the same matcher loop that computes visible sales and counts. The adapter consumes those exact local records, without a second order query. Captured empty cohorts replace older report-layer profitability too. A missing capture is an explicit snapshot error rather than permission to retain another query's profit. The normal report structure and visible financial metrics remain available. Installers preserve their existing markers using `functools.wraps`; repeated runtime composition does not nest conflicting exact wrappers. Tests cover the full Salla/base-profit/exact installer sequence three times plus overlapping reports with different cohorts and ContextVar cleanup.

The exported mutable-dict compatibility seam detaches caller-owned inputs synchronously before the first await, including before admission waits. Callers must not mutate them concurrently from OS threads during that capture. The private production report path owns its local sink records exclusively and exposes no mutable records to callers; it can safely copy those owned inputs in the bounded executor after admission. If they cannot be copied safely, a controlled `campaign_profitability_snapshot_unproven` error is raised. Removing cache serialization also removes BSON/string fingerprint collision risk. ObjectId/datetime and other normal BSON inputs remain calculable without cache reuse. No order/customer payload or digest is logged or persisted.

## Evidence

- Five original regressions demonstrated stale sales, line cost, added/removed orders and changed spend on the original cache.
- Four policy/serialization regressions demonstrated update/reset, observed policy races and BSON Decimal128/text collision on the intermediate cache design.
- Independent review then reproduced two real-calculator failures on the previous checkpoint: changing SKU with a fixed cost revision retained cost 50 instead of unknown; changing a bound resource cost retained 55 instead of 65. Both regressions fail against the exact previous code and pass with reuse disabled.
- Same-query tests cover both outcome producers, a changed later cohort, identical order counts but different records, and an empty first cohort. A forbidden second-query sentinel proves that profitability uses the original capture.
- Latest related suites: exact reuse, campaign profitability, current catalog costs, result source, created-order semantics and unified campaign contract: **61 passed**, exit 0, 1.96 seconds. No provider/database mutation or production deployment was performed.
- The existing `prod-campaign-profitability-exact-reuse-v2.yml` workflow runs the exact-reuse suite. Its path selection now also includes the two changed Salla outcome producers; no other workflow is changed.

## Performance and limits

A local synthetic comparison used the real line-cost calculator, three lines per order, 100 catalog products, and immediate in-memory dependency reads. It is not a production benchmark and excludes real Mongo latency. At 1,000 orders, the previous design took 0.084 seconds cold / 0.026 warm; recomputation took 0.101 / 0.069. At 10,000 orders, the previous design took 0.888 cold / 0.303 warm; recomputation took 0.703 / 0.738. Reuse removal adds approximately 0.435 seconds to the measured 10,000-order warm call and reloads the actual cost context each time. Large production reports require observation; no claim of zero performance cost is made.

A future cache requires authoritative versioning covering all catalog identities, profiles, product/option bindings, resource costs, policies and committed order-cohort mutations, plus a coherent read contract. This patch deliberately does not create that system or promote shadow Salla state. The separate account-timezone `created_order_semantics` cache is owned by another independent task; this document does not claim to fix it.

Production unchanged. Root owns review, integration and governed publication.


## Bounded CPU isolation

The exact calculation loop runs in a dedicated executor capped at two worker threads per Python process. Admission is capped at two calculations per event loop before context loading; a cancelled request retains its slot until its running worker completes, including repeated cancellation. No database or provider seam runs in the calculation worker. Concurrent requests retain separate captured cohorts. This does not claim a process-wide input-memory admission cap across multiple event loops; the executor worker cap remains process-wide.

The public compatibility seam must synchronously copy external mutable input to prevent a waiting caller from replacing the captured cohort. The private report path uses exclusive request-owned records and performs both its copy and pure calculation off-loop. A new 20,000-order synthetic run with real line-cost calculation and immediate in-memory dependency reads measured public compatibility wall time 1.333 seconds / maximum heartbeat gap 0.166 seconds, and private report wall time 1.407 seconds / maximum heartbeat gap 0.033 seconds (133 heartbeat samples). This excludes real Mongo latency and is not a claim that every operation in the complete report handler is non-blocking.

Tests additionally prove bounded admission under three concurrent requests, loop responsiveness while two workers are active, preservation of the third request's exported inputs when its caller mutates them during admission waiting, repeated cancellation without early slot release, and off-loop copying of private owned records. Independent review identified the marker-loss and input-ownership risks; the corresponding deterministic regressions now pass.


## WIP checkpoint — direct duplicate layer removed, report aggregation still blocks

Consumer review found the base presentation layer's policy flags and profitability fields can be supplied by the exact-cohort presenter without rerunning `build_campaign_profitability`. Its direct wrapper now carries an identity marker pointing to itself. The exact installer removes only that identified direct layer; an unknown outer middleware that merely inherits markers is preserved. The underlying standalone calculator and its direct AI consumers remain unchanged. A shared pure response-policy helper retains all five read-only/write-reached flags, while exact source provenance retains allocation exclusions, cost source, matching/coverage counters and cache-disabled evidence. Repeated installer composition remains idempotent.

The runtime-composition regression failed before this change because a sentinel detected the redundant legacy calculation. After correction the sentinel is never reached, including repeated compositions and overlapping reports. Added report-level failure/cancellation tests prove ContextVar restoration, continued usability on the next request and restoration of the same admission semaphore to two slots. Semaphore release itself is synchronous and has no cancellable await; repeated cancellation waits for the running calculation worker before freeing admission.

A 20,000-order report-composition harness using the real matching and line-cost functions, with in-memory dependency reads, measured wall time **1.605 seconds**, maximum heartbeat gap **0.247 seconds**, 133 heartbeat samples and **zero legacy calculation calls**. This reveals the remaining synchronous Salla first-cohort matching/aggregation path. Mongo/provider latency and actual production behavior were not measured. Despite 61 passing related tests, this checkpoint is explicitly **WIP / not release-ready** pending that responsiveness gap and further review. It must not be merged or published as a completed performance fix. Production remains unchanged.
