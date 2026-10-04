# Dashboard memory P0 â€” WIP, NOT READY

Base: 3a2cc4baab7119e59b66a9d667486e118a527e7a.
Branch: codex/dashboard-memory-bound. Draft PR #1253.
Scope: dashboard reads and Web pagination only. No Production requests/writes,
no merge, prepare, prepublish, deploy, resource changes, or Mobile changes.

## Owner decisions
- Cart period uses provider cart_created_at only. Old carts renewed today excluded.
- Full-period totals must remain complete; detail pages may be bounded/paginated.
- Monetary computations may use bounded batches with EXISTING canonical helpers.
  Do not rewrite fee/shipping rounding as Mongo $round or sum rounded page totals.

## Verified milestone
- Mongo metadata facet produces scalar counters and limit+1 sorted page keys;
  detail/enrichment reads only requested page. Two Mongo commands; 51 wire
  documents incl one facet envelope for page50 (50 full cart documents).
- Explicit Web cursor pagination, period race protection, retry without discarding
  prior rows; single-flight per authenticated owner/filter while work is in flight.
- Request-local order cohort reuse removes duplicate summary reads. IMPORTANT:
  this is still O(N), capped100k as before, and is NOT the final summary solution.
- Narrow product raw cost projection matches original catalog resolution in Mongo.
- Historical order-number aliases exposed a parity bug in draft batching. Fixed
  by retaining original whole-cohort hydration semantics pending bounded redesign;
  no per-128 proof N+1 remains. Do not claim full-summary memory is bounded.

## Fresh local verification
Python3.13, dedicated local Mongo8.0.12 on127.0.0.1:27261; no application .env.
36 PASS, zero skipped: cart pages, order cohort reads, coordinator, product cost.
26 PASS /2 suites: AdvancedDashboard cart pagination and latest orders (Node24).
A broader earlier run:67PASS/1FAIL. The source-text assertion in
 test_dashboard_v2_live_refresh_contract.py expects setData(null), absent even in
base3a2. Not edited/suppressed; still needs explicit baseline reproduction/evidence.
Old CI at5292 is not proof for this new work. Final full applicable CI pending.

## Isolated cart benchmarks (synthetic1KiB items; helpers, not HTTP/enrichment)
Fresh process samples, Mongo data cache warm/uncontrolled: latency is indicative.
N       old RSS MiB   new RSS MiB   old query ms  new query ms  old docs/new docs
10000   122.38        76.95         212.32        863.91        10000/51
50000   305.95        76.51         1010.05       3583.19       50000/51
100000  531.72        76.46         1992.98       5958.37       100000/51
100k x4 old RSS1194.10MiB/query7504.51ms/JSON4922.53ms/400000full docs.
100k x4 independent newRSS78.00MiB/query4792.19ms/JSON2.27ms/200full docs.
100k x4 shared newRSS76.82MiB/query6249.41ms/JSON1.53ms/50full docs.
Counters still cover all100k; returnedpage50; otherdata available throughcursor.
Single-request Mongo latency REGRESSED despite flat Backend RSS. Must optimize
or explicitly review, not disguise as latency PASS. JSON time now<1ms single.
Raw measurements retained alongside this file. Early10k/50k shared samples predate
expanded date parser; remeasure those before finalcomparison. New samples hashcode.

## Known blockers / next safe work
1. Full/dashboard-v2 still keeps orders, parsed individuals, electronic lists,
   cost maps and product rows. Implement bounded canonical reducers + detail pages;
   exact totals parity first, then10k/50k/100k full-endpoint benchmarks.
2. Cart parser supports tested ISO/Unixseconds/ms/envelopes/Riyadh/JS GMT forms,
   but known DST-fold discrepancy, submillisecond ordering, ISOweekdates and
   second-offset ISO compatibility remain unproven. Do not claim exact parity.
3. A standalone dashboard order accumulator is being developed (not integrated)
   to reuse unchanged canonical fee/shipping helpers with two boundedpasses.
4. Missing-product navigation currently serializes allIDs; paging must preserve
   full cohort navigation rather than silently treating the firstpage ascomplete.
5. FinalCI and independent integratedreview pending. This is NOT rootfix-ready.

Local benchmark DBs dashboard_memory_benchmark_p0_{10k,50k,100k}_1c62 remain
on dedicated localhost27261 for additional measurements; no Production database.
Accounting/Supplier/Shipping/Mobile modules and financial writers unchanged.
Production data unchanged. Production financial writes=0.

## Canonical reducer checkpoint
Root independently reran standalone dashboard_order_accumulator tests:7PASS.
Reuses unchanged orders_to_parsed, match_settings, order_total_sar and
shipping_breakdown. First pass collects rawgroups/scalars; second pass invokes
canonical fees under finalgroup configuration. Digest rejects changed replay.
Not yet wired into server.dashboard. Groupcardinality still grows withdistinct
labels. Do not call this a complete bounded-summary implementation.
Base source-text contract failure reproduced by extracting the actual test
function and running it against gitshow3a2cc4ba frontendfile: AssertionError;
setData(null) absent onbase. No test or application assertion was weakened.
Next action: integrate bounded replay/context and remaining electronic/balance/
monthly/product reducers, then exactfullresponse tests andpagedproduct contracts.
The current loader deliberately retains originalwholecohort hydration to avoid
changing alias FX/attribution semantics. This must be solved before finalREADY.
Additional verified buffer removal: V2 no longer reads recent analysis report
blobs that its response discarded. Legacy dashboard keeps the exact display
fields through a narrow projection. Real Mongo test with a1MiB report passed;
owner isolation and outputsummary preserved. Latest focused reads+reducer15PASS.
No summary-wide boundedness or final CI claim. Next implement the approved
bounded summary/detail contract, preserving all canonical rounding rules.
