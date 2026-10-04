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

Latency continuation: stage profiler now records async inclusive stages and synchronous exclusive boundaries. 1000-row smoke succeeded (1.54s instrumented); 100k uncontended profile next. No other local workload allowed. Financial-cardinality agent planning only; cart-date agent edits isolated owned files, tests deferred. No Production access.

## Latency/cardinality/date continuation — verified WIP, NOT READY
Uncontended instrumented 100k baseline125.692s recorded in PROFILE-baseline-uncontended-100000.json.
Private spill decode33.260s, store14.761s exclusive, encode9.922s, SQLite9.276s;
async inclusive timings overlap and must not be summed. Canonical balances1.956s,
productcatalog0.247s, cursor fetch waits1.864s. Instrumented, not finalbenchmark.
Compact lossless codec tags only exceptionaltypes; stringkeys avoidJSON;
batchdecode<=128 preserves native/tagged distinction and exact financialtypes.
Financial rawgroups and sortindices nowspill; detailpages<=50 include metadata,
fulltotals calculated beforepaging. Salla rawalias replay scheduled onlywhere
canonical count condition can succeed; at most onegroupperrecognizedrail.
Webpayment/shipping/nestedaccount pages replace rows andretainwholeperiodtotals.
Independent bounded readers admit up to4 without cross-tenant heavyqueue,
stillusing sharedmemorythresholds; overcapacityfails explicitly, nohiddenqueue.
Legacy cartformats use canonical parser on metadata<=128 andfinalpage-only
payloadfetch; creationdate only, neverrenewal/update as periodcriterion.
Root integrated focused8files83PASS/0FAIL/0SKIP in25.36s. Frontendagent4suites35PASS.
NoProductionreads/writes, nocodeoutsideapprovedDashboardpaths changed.
NEXT: freshuncontended10k profile,then100k andfullmatrix10/50/100k x1/2/4,
independenttenants, remoteexactHEADCI. No readinessclaimbeforelatency evidence.

## Latency iteration checkpoint — NOT READY
Uncontended 10k fresh-process summary improved11.377s ->7.959s at~105MiB;
canonical full monetary signature unchanged. See summary-fused-10k.json.
BSON non-executable native-value codec, writeback accumulators, fused metadata
passes and resolved-money reuse reduce repeated serialization/calculation.
Larger lookup/product cache experiments gave no improvement and were removed.
Dashboard-only operating reader preserves canonical calculator/default behavior;
all cursor batches<=128 and unused raw prepaid label details omitted only forV2.
Integrated dedicated local Mongo suite184PASS/0FAIL/0SKIP in130.30s;
frontend6suites42PASS. Followup operating distinct-label suite11PASS.
c3b1511 CI had two genuine failures: static payment JSX contract required old
immediate closing tag; reader integration compared additive pagination metadata.
Both assertions adapted narrowly, no security assertion change. New HEAD CI pending.
SAFETY INCIDENT: extra legacy test_operating_expenses.py invoked external Preview
default salla-analytics.preview.emergentagent.com because backend URL env unset.
Synthetic registration attempts returned422 validation errors before test CRUD.
Extra suite8PASS/50SKIP/16ERROR; do not count as successful regression evidence.
No authenticated CRUD or financial operation ran; no retry. Subsequent commands
are explicit localhost27261 synthetic Mongo only. Production not targeted.
NEXT: uncontended100k profiler then meaningful latency reduction/fullmatrix,
multi-tenant concurrency and finalexactHEADCI. No merge orreleaseactions.

Measured b37740d7c100k instrumented97.655s (not final latency):
order snapshot23.351s,product23.153s,decode24.185s exclusive,store15.757s,
SQLite8.406s,balances1.994s,batchfetch1.543s,JSONresponse0.0038s.
Peak106.02MiB,182825Mongo docs across bounded batches. Mainremainingcost
is repeated private serialization/replay, not Mongo query waits.
Next slice batches private SQLite inserts/updates/references<=128 and excludes
only unused product descriptions (SKU/options/service/money intact).
Currentmonth reuses exact alreadycomputed currency summary. Focused38PASS.
NOT READY;uncontended100k without profiler next.

660ded8a79bab70d587375fe00c8c3be39abea2c remoteCI31PASS/0FAIL/0PENDING/4SKIP.
Uncontended100k sameHEAD summary before34.125s/1155MiB ->74.094s/105MiB.
This is NOT READY: memory fixed but private replay overhead regresses latency
versus original RAM-heavy implementation. Full monetary signatures match.
Next verified slice49PASS: FX/attribution shared encoded proof table with
independent lastvalid semantics, bounded128 lookups/bulkwrites; compression
threshold1024 avoids zlib for tiny mutable groups. Product detail contributions
append/reduce in original group/line order instead of perpiece map rewrites;
all canonicalcost/FX/rounding functions unchanged. Includes>128interleavedgroups,
missing/cancel/zero/duplicateorder and paginatedfullparity tests.
Next uncontended100k measurement, then fullmatrix only iflatencyconvincing.
NoProductionaccess or financialwrites; no merge/prepare/prepublish/deploy.

fd26b0b59bc72b80f795795822a0e95b1fabbaa1 remoteCI31PASS/0FAIL/0PENDING/4SKIP.
Uncontended100k after55.333s/108.262MiB vs original29.707s/1154.64MiB;
monetaryparitytrue. Temporarydisk177.37MiB, cleanedsuccessfully.
10k profiler exposed repeated payment normalization (16k calls/600k Arabic
folds); add Dashboard-request-local128entry cache for shortlabels only, calling
the identical canonical normalizer onmiss. Cachecannot survive/request or grow
with rawlabelcardinality. Focusedfinancial/cardinality/carrier19PASS, reproduced
1532calls beforecache with1027orders; test verifies totalsandfreshrequestcalls.
NEXT:uncontendedfinal10/50/100k x1/2/4 plus independenttenants andstageprofile.
StillNOTREADY until these gatesandexactHEADCI complete.

## Scope and latency checkpoint — NOT READY
Owner explicitly excludes standalone product-cost-summary and sold-products.
9358 final matrix: 100k/c1 43.973s/107.67MiB; c4 shared-key48.821s/107.82MiB.
Independent tenants (100k each, c3):140.206s/113.64MiB vs baseline114.432s.
Financial parity true throughout. See LATENCY-VALIDATION.md and raw JSON.
Latest profile identifies repeated Python/private-buffer work, Mongo wait2.309s.
Next slice fuses electronic and primary reducer traversals, reuses canonical
parsed/shipping outputs and keeps independent fee group counts/math unchanged.
Focused summary/carrier/cardinality21PASS and accumulator23PASS.
NEXT: exact-HEAD CI and uncontended100k benchmark for this slice. No readiness
claim until latency and independent-tenant gates pass. Production unchanged.

## Checkpoint 06c7a365e — not ready

The singleton reuse change is covered by 44 focused passing cases. The two
new reuse cases first failed with 336 vs 240 and 288 vs 192 parsing calls.
After the change, they match the number of eligible orders and the complete
canonical financial response remains identical.

Fresh uncontended 100k: original 37.297 s / 1156.11 MiB; current
44.702 s / 108.12 MiB. Mongo documents consumed: 364642 -> 182825.
The timing does not establish an additional gain over 9358; do not claim one.
The full 10k/50k/100k and multi-tenant matrix above belongs to 9358, not06c7.
The new HEAD requires the final acceptance matrix after remaining blockers.

Exact06c7 CI: 30 success, 1 failure, 4 skipped, 0 pending. CodeQL Python/JS,
Dashboard Real Mongo, frontend pagination and frontend build pass.
Failure: Frontend dependency and CSP checks, run37235850209/job111534667071.
The job checked out06c7 and executed `node scripts/verify-braces-backport.cjs`,
which fails MODULE_NOT_FOUND. That file is absent from06c7; its tracked
security-gate.yml does not contain this command. No security file, dependency
or assertion was changed by this Dashboard slice. The source of the workflow
revision mismatch needs coordination with the separate security work. No
retry, suppression or Security change was performed.

Local cProfile (10k, profiling overhead) shows repeated canonical currency
resolution remains expensive: order_total_sar65448 calls/2.281 s cumulative,
_stored_original_currency81810 calls/1.952 s cumulative. These are overlapping
instrumented times, not additive latency. The shared currency calculators
remain unchanged. Do not modify financial semantics to improve this profile.

No Merge, Prepare, Prepublish, Deploy or Production access in this slice.

NEXT: coordinate unexplained Security workflow/file mismatch; continue measured summary latency work. Do not merge adjacent security work without owner scope decision.

Security mismatch traced: Production Git base advanced to1ff836914 (#1254),
whose PR-merge workflow calls backport tooling absent from task HEAD. No merge
or dependency change. Source-matched workflow_dispatch37236506469 atb35478347
PASS, using unchanged task Security workflow. Preserve failed PR-event evidence.
Next: isolated Ubuntu/Python3.12 RealMongo benchmark matrix, separate runners
perdataset, serial before/after workers. Added manual-only benchmark job to
existing Dashboard workflow. Multi-tenant fixtures now have distinct monetary
values and compare signatures bytenant, so tenant swaps cannot pass parity.
No application code changes in this checkpoint; runtime remains06c7.

## Isolated Linux matrix complete — NOT READY
Runtime06c7 unchanged; measured source4d7fa3953/treeaf7bea78.
All10k/50k/100k x1/2/4 and independent3tenant10k/100k requests succeeded,
with tenant-keyed monetaryparity and temporarybuffer cleanup.
100k1:35.306s/1240.8MiB ->33.738s/112.1MiB.
100k4samekey:141.239s/1736.4MiB ->33.681s/110.9MiB.
Independent3x100k:109.867s/1516.1MiB ->116.147s/118.8MiB.
This last latency/throughput remains blocker; no READY claim.
See LINUX-VALIDATION.md and linux-4d7fa3953/*.json.
DedicatedMongo198PASS0skip, frontendpaginationPASS, CodeQLPASS.
CorrectsourceSecurity workflow_dispatch37236752263 PASS. PR-event Security
uses mergedbase#1254 workflow and fails on missingbackport script; nothidden,
no#1254/dependency/securitypolicy import.
Local4d7 instrumented profile76.431s separatefromLinuxacceptance. Pureproduct
resolver memo proposal notimplemented without hit-rate proof.
Next: reduce remaining Dashboard Python/replay latency, then fresh finalCI
and benchmarks. Documentarycheckpoint only; no new appcode after06c7.
NoProductionaccess/financialwrites/releaseactions.


## Final blockers continuation from bd8e1a4 — instrumentation first

User authorized same-run Linux attribution and only evidence-backed minimal
Dashboard optimization. Added an isolated manual workflow mode for 100k orders
per tenant with 1/2/3/4 independent tenants, paired fresh control/profile workers
on each same fixture. The task driver measures disjoint event-loop dispatch wall
and thread CPU; nested synchronous categories subtract their measured children.
Async stage/driver/suspended intervals are explicitly nonadditive. Counters are
bounded by tenants/categories, not orders. No application code changed in this
checkpoint. Existing untracked profiling artifacts were preserved.

Security provenance is a separate unresolved blocker. Candidate workflow,
checker, exception and lockfiles are unchanged from merge-base 3a2cc4baab7119e59b66a9d667486e118a527e7a.
The newer PR-event base workflow requires the #1254 braces backport and a
strict audit checker; copying the missing verifier alone cannot satisfy it.
Dispatching the old source workflow is supplemental evidence of its old policy,
not a replacement for the strict effective gate. No dependency, exception,
suppression or Security workflow alteration is made to hide this mismatch.

Next: run this checkpoint's manual tenant_profile workflow, preserve all raw
metrics/source identities, identify CPU/replay/IO residuals without summing
inclusive timers, and only then decide whether a minimal runtime patch is
supported. Report actual exact-head CI, including the unresolved Security gate.
No Merge/Prepare/Prepublish/Deploy. Production data unchanged; financial writes0.
