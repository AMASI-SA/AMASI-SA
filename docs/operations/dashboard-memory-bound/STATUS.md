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


## 2026-10-04 integrated WIP checkpoint — NOT READY
Task branch codex/dashboard-memory-bound / Draft PR1253. Production untouched.
Integrated request-local bounded spill, global FX/attribution alias replay,
canonical fee reducer, paged product/missing details and Web pagination.
Optional balances collect_details=False is explicitly user-approved for Dashboard;
default financial callers retain exact original behavior/calculation/rounding.
Ads dashboard containers now spill; canonical account fee formulas unchanged.
Root fresh tests:35PASS spill/reads/legacy-fullresponse Mongo/productpages/reducer;
10PASS balances(50k distinct labels)/ads/fullresponse Mongo;15PASS Web paging.
Full CI NOT final. Previous a41c frontend comment-contract failures fixed locally;
base setData(null) source assertion remains documented pre-existing, not weakened.

Actual V2 10k orders +1000catalogproducts +1KiBitem padding benchmark:
c1 before199.91MiB peak/5.44s/37366wire docs; after105.62MiB/84.89s/19187docs.
c2/c4 financial signatures also equal; duplicate requests coalesced.
c4 after105.38MiB/93.04s/19187docs. Response609993->53621bytes, productrows800->50.
Temporary disk132276224bytes at10k: extrapolation exceeds256MiB budget at50k.
This is a confirmed unacceptable latency/disk regression, NOT performancePASS.
Raw summary-10k-wip.json preserved. Profile directtimers1000 orders:
SQLite40687calls5.855s, encoding67849calls2.137s, decoding26290calls1.412s;
Mongo0.120s. Redundant map writebacks and full snapshot copies need removal.
Raw cProfile caller attribution unreliable across threads; direct timers authoritative.

NEXT: optimize private spill transactions/read-only index eviction/ordinal counters,
avoid duplicate full payload buffers, batch canonical fees preserving per-order rounding;
rerun exact parity then complete10k/50k/100k x1/2/4 matrix and finalCI.
Remaining known risks: recurring financial reader buffers, distinct raw financiallabel
cardinality, cart legacy date edge compatibility, fullsummary finite disk allowance.
No merge, prepare, prepublish, deploy, Production requests or financial writes.


## Optimized replay checkpoint — still WIP, not final readiness
Explicit private SQLite transaction, read-only index caching, namespace counters,
reference-only filtered cohorts, in-place snapshot hydration, native JSON fastpath
with tagged special-type fallback and lossless compressed large payloads are now wired.
Disk budget remains hard; incompressible deterministic failure fixtures still prove
cleanup/failure rather than silent truncation. No Production database involved.
Canonical fees replay in <=128 batches; same calculators/per-order rounding reused.
Optional Dashboard balance-detail suppression wired; default callers unchanged.
Fresh integrated suite51PASS (spill, orders Mongo, productpages, fullresponse Mongo,
fee reducer, balances, ads). Earlierintegrationmissingasyncio import fixed andrerun.
Before native-JSON finaloptimization,10kfullV2 improved84.9->37.43s,132->11.9MiB
spill;RSS105.09MiB vsbaseline200.54MiB andfinancialsignatureexact. Still latency
regressionvsbaseline7.36s, so noREADY. NativeJSONprofilemotivated bycodec27kdecodes.
Next safe action: benchmark current exactcheckpoint10k/50k/100k x1/2/4, inspect
remainingDashboardbuffers/pagination/datecompatibility; fullCI andreview onlyafter.
User explicitly authorized collect_details=False Dashboard-only balances option;
Ledger/Journal/Settlement and allmonetaryformulas remain unchanged.


## 2026-10-04 final-scope approvals and active matrix
Runtime-source checkpoint3f8d4027be823cc01c6cb66ccb4277df9448edac has29CI PASS,
0FAIL/0PENDING/4SKIP (manual Emergentdeploy/Host20 rehearsal notrequested;
Snapchatsettings tests excluded bytheirscopeguard). NewdedicatedP0CIadded next.
Full10k/50k/100k x1/2/4 actualV2 benchmarkrunning unifiedexecsession37892;
output summary-matrix-3f8d4027.json. Do nottreatintermediateresult asfinal.
Productioncode filesfrozen duringmatrix; adaptertests/docs/CI additionsonly.

Userconfirmedcurrentcarrier counting ALREADYworks andmustnotchange.
AddedisolatedMongo preservationtestonly: baseline/currentfullresponsesidentical,
iMile2 ->iMile1/StoreCourier1 aftercurrentnormalizedfieldchange, oldrawiMileremains.
Rootfresh1PASS3.54s; noShippingcodechanges.

UserexplicitlyapprovedDashboard-only boundedread options forsharedsettlement/
recurring readers; exactmath/defaultfinancialcallers remainunchanged.
Independent work underway in C:/Users/amasi/dashboard-memory-readers detached
at3f8d4027 (NOT mainbenchmarktree): audit_integrations_memory ownssettlement
optionalrow_loader+dashboard_settlement_reads.py+tests; dashboard_summary_memory_audit
ownsrecurringoptionalinputs+dashboard_recurring_reads.py+tests. Do notapplyuntil
activebenchmarkends; theninspect/applyfilesandwireDashboardcalleronly.
Largefinancialreaderfixturesnotrunconcurrentlywithbenchmark; smallparityonly.
RemainingblockingfullRAMpaths documented: recurring5k/20k,settlements50k,
wallet20k; rawdistinctfinanciallabel cardinality also unboundedinmaps/response.
NoLedger/Journal/Settlementwriterchanges authorized. Read-onlyloaderoptionsonly.
Production unchanged; financialwrites0; noMerge/Prepare/Prepublish/Deploy.


## Dashboard-only financial readers integrated — WIP
User explicitly approved these read options, not financial calculation changes.
Projected cursor batches <=128 replace Dashboard settlement 50k/wallet20k lists.
Recurring obligations/invoices use a request-local private spool and the unchanged
canonical daily calculator with at most one actual or three historical invoices.
Shared default callers retain their original loader and calculations.
No writer, Ledger, Journal, settlement mutation, Supplier, Shipping or Mobile code changed.
Fresh root tests:31 PASS including >50k settlements and >20k recurring invoices,
plus5 integrated Dashboard/default-caller/AST-boundary tests PASS (36 total).
Existing current-carrier iMile -> Store Courier preservation test remains PASS.

Full summary matrix finished:10k/50k/100k x1/2/4 all totals signatures match;
100k RSS1127-1158MiB ->~105MiB, but108-130s elapsed remains a blocker.
See SUMMARY-MATRIX.md and raw summary-matrix-3f8d4027.json. Runtime predates
new financial readers; do not label these numbers final-head performance.
Financial-reader benchmark:10k/50k/100k real Mongo, fresh processes, common-cohort
exact parity plus full-cohort completeness; RSS~80MiB, observed batch<=128.
Legacy caps truncate at20k invoices/50k settlements; Dashboard full-cohort totals
can correct old truncation, while formulas/default accounting are unchanged.
Benchmark latency includes possible overlap with focused tests; rerun without
contention before any final latency claim. Source hashes are in raw evidence.

NEXT: exact-head CI, additional cancellation/many-obligation coverage and isolated
latency/profile work. Remaining blockers: summary replay latency, raw distinct
financial-label cardinality and cart legacy date edge parity. NOT READY.
No merge/prepare/prepublish/deploy. Production unchanged, financial writes0.

Independent review reproduced a failure-path task ownership issue: gather raised
while a sibling still used the request spill. Added explicit cancel-and-drain
for both summary parallel read groups, preserving original exception propagation.
Focused integration7PASS including delayed reader + sibling error/cancellation.
Recurring11PASS includes all cursor failures/cancellation, encoding failure,
5001 obligations and20001 invoices. Full18-file dedicated suite running before
checkpoint CI; do not treat this paragraph as its result.

## Verified reader milestone, still NOT READY
Runtime HEAD b1b5522c379e86ad0f3a9cea386242b1c2a895ab
TREE7540d61ca3d706c20c80e96a244425e5dc90ffd5.
Full18-file integrated suite128PASS/0FAIL/0SKIP (128.63s); session55767 ended0.
Exact-head remoteCI31PASS/0FAIL/0PENDING/4SKIP. All CodeQL/Security and
dedicated Dashboard Mongo/frontend page jobs passed. Skips are unchanged
manualdeployment/Host20 rehearsal and two Snapchatsettings scope jobs.
Uncontended reader benchmark session84519 ended0:10/50/100k common-cohort
parity and full-cohort completeness;~80MiB peak allsizes versus117/238/254MiB.
See FINANCIAL-READERS.md and financial-readers-benchmark-uncontended.json.
Current-source1000order directprofile wall1.61s; serialization and map writes
remain the largest measured private-spill boundaries. No production requests.

This next documentation checkpoint changes no runtime code. Its CI must not
be inferred from the runtime checkpoint; consult Issue1006/PR1253 forlatest.
Next safe implementation: reduce summary replay/serialization CPU using
canonical calculators, retain parity, then final uncontended fullmatrix.
Address distinct-group cardinality and cart-date compatibility beforeREADY.
Productionunchanged/financialwrites0. No running local jobs remain.
