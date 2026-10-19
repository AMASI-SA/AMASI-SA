# P2 / B — Dashboard baseline RCA

## Approved experiment (test-only; runtime adoption is not authorized)

The next checkpoint compares Current with a private request-scoped candidate.
`dashboard_b_request_scope_experiment.py` compiles the existing function ASTs
with three asserted substitutions; no production source is modified:

1. Identical tenant/date query AND currency/attribution projection share one
   single-flight order load and hydration. Different overlapping ranges are
   NOT combined. Legacy and V2 retain their distinct filters (including Arabic
   normalization differences) and pre-status reference universe.
2. The second electronic-only `orders_to_parsed` uses a copy of the first result
   only when every input object and its order are identical. Mixed payment or
   excluded-status inputs still execute their own conversion.
3. Selected-period SAR summary reuses current-month summary only when both
   input lists are the same object. Different periods remain independent.

The catalog/product-cost index already executes once: it is measured, not
memoized. No Resource Governor, formulas, pagination, response or API changes.
The summary endpoint has no pagination parameters; all output lists/order are
included in full JSON comparisons. No cross-request cache exists; scope objects
are created/disposed per invocation. A new request after a fixture DB change
must observe it. Small equivalence cases fingerprint shared hydrated inputs to
detect any consumer mutation. Consumers must remain read-only after hydration.

Twenty-four mixed correctness cases cover currency/null behavior, returns,
filters, inclusive boundaries, inferred dates, empty data, legacy/V2 Arabic
status semantics, variants/options/components/fallback/missing/zero costs,
rounding, advertising and tenant/owner boundaries. Existing helper regressions
are complementary, not a replacement for end-to-end equality. The fixture does
not exercise every possible provider provenance state: Snapchat successful
strict provenance is not seeded, and no live provider behavior is claimed.

Performance pairs alternate Current/Shared order at10K/50K/100K, one warmup and
ten samples each, month and overlap. Every pair compares complete canonical
JSON bytes/digest without deleting fields; business totals are also independently
asserted. Capture commands/documents, execution counts, CPU/wall by helper,
handler p50/p95, heartbeat maximum lag, serialization and response bytes.
Sample process RSS every10ms on a background thread; this is approximate process
memory, affected by allocator history and GIL scheduling, not isolated retained
object bytes. Do not sum overlapping timings. Query wire bytes are not measured;
response bytes are standalone JSON, not HTTP compressed payload size.

Local smoke is harness validation only. Linux/Mongo8.0.12 CI measurements are
required before an experiment PASS. No runtime adoption or release is implied.

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
