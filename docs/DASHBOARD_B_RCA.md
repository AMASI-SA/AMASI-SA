# P2 / B — Dashboard baseline RCA

Independent base: `79c47cdfbb95bd9ce944c0e61d639d349f9e7af0`.
A/PR1294 is not merged into this branch. This PR contains measurement/test/CI/docs
only. No runtime changes, request cache, semantics changes or production access.

## Confirmed source paths before measurement

`dashboard_v2_routes.py::make_dashboard_v2_router.dashboard_v2` calls the actual
`server.py::dashboard` aggregator with self-heal and legacy analyses disabled,
while concurrently calling `_filtered_orders`. Both load normalized orders and
projected raw currency/attribution proof separately. Thus current-month selection
has four order reads before cursor getMore. A different selected range additionally
loads current-month orders/proof: six order reads, even when those rows overlap.

`server.dashboard` computes sales, parsed payment totals, shipping, product costs,
monthly trends and operating totals. V2 computes authoritative sales, current-month
sales, product cost and ads breakdown again. Equivalent-looking calculations must
not be deleted blindly: legacy reference universe can precede status filtering;
currency provenance, cost policies and monthly/selected scopes differ.

`build_mezan_v2_product_cost` loads the entire catalog (up to100000), profiles,
option/product bindings and resources before iterating selected order lines. Its
catalog indexing, line costing and product-profit sorting run synchronously inside
the async endpoint. `asyncio.gather` overlaps I/O but does not offload that CPU work.

## Measurement contract

Real MongoDB8.0.12 loopback replica set, Linux/Python3.11 on CI. Deterministic10K,
50K,100K orders, equal catalog/profile cardinalities, one priced SAR line per order.
Current-month and overlapping-range cases; one warmup plus ten samples each.
Provider/expense collections are empty: this measures the order/catalog path with
real empty-source reads, not live sync/external API or large salary/advertising data.

The actual summary router is called. Its legacy dependency is the real function
body/constants extracted from server.py, with real imported collaborators; server
startup/schedulers are not imported/executed. Self-heal remains false as in V2.
Settings are initialized during fixture setup; a read-only DB facade rejects writes
inside measured calls. Non-loopback socket connections are blocked. No production
credentials or environment files are loaded. Setup/cleanup affect a UUID fixture DB.

Capture find/getMore/aggregate counts, identical find signatures, returned documents,
driver-observed Mongo durations, Python main-thread CPU, synchronous computation
counts/row counts/CPU, 5ms heartbeat lag, handler p50/p95, and serialization separately.
Main-thread CPU excludes Motor worker/server CPU. Mongo durations overlap under
gather and cannot simply be subtracted from total. Heartbeat includes driver/OS
scheduling effects; it is not proof of a specific production incident. With10samples,
nearest-rank p95 is the maximum. No performance PASS threshold or optimization yet.

Fixture assertions: exact order count, SAR sales and product cost at every size;
no write commands during measured calls. Existing cost/currency/ad tests also run.
The local100-row smoke is harness validation only, not acceptance performance data.

## Next decision

Wait for the baseline artifact before proposing implementation. First candidates
to assess are request-scoped sharing of equivalent reads and computations, not a
cross-request cache. Prove identity of query/projection/filter/status/currency scopes
before sharing. Do not change limits, truncate data or bypass governor policy.

No A changes, B optimization, C/E/product-filter work, Accounting/Shipping/Review
Completion/Android edits, Intent changes, merge or deployment. Production writes=0.
