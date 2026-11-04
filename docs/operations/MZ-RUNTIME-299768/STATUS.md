# MZ-RUNTIME-299768 — Dashboard/Ads isolation from employee and order APIs

## Latest owner instruction: 2026-11-04 — authoritative scope

**NO QOYOD CHANGES.** This instruction supersedes the earlier broad worker-isolation specification. Keep Qoyod Plan-B and legacy pipeline auto-send, provider reconciliation, reports, credentials, leases, accounting, and all current Qoyod jobs **completely unchanged**. Do not move, stop, start, retime, enhance or test live financial writes as part of this issue. Keep all existing employee workflow, shipping dispatch, payment update, Salla webhooks and order operations intact.

Goal: dashboard summary / reporting, advertising analytics / synchronization and CPU-heavy analysis should not contend with employee preparation, supplier files, orders or shipment/label APIs.

## Status and source

- **Repository:** `AMASI-SA/AMASI-SA`
- **Issue:** https://github.com/AMASI-SA/AMASI-SA/issues/1724
- **Branch:** `codex/worker-isolation-299768-20261104`
- The original [task PR #1726](https://github.com/AMASI-SA/AMASI-SA/pull/1726) started from `a56dbacd36678566eec3b5a76406db7582c3e7f2`. [Release PR #1732](https://github.com/AMASI-SA/AMASI-SA/pull/1732) integrated the employee and dashboard performance fixes onto the published #1728 base.
- The web release is live. Issue [#1724](https://github.com/AMASI-SA/AMASI-SA/issues/1724) stays open while Emergent confirms whether the project can run a separately managed dashboard/ads worker.

### Verified production release on November 4, 2026

- Emergent run `382d49e` started at 05:29 and completed at 05:39 Riyadh time. Production merge `251565f7ad40dd464b22dad638ecc9ab7b4029fa` contains source `562f9c14de27058dbdd9a6d067b0054928a2fe3f`. The official v5 guard passed all three verification probes against release `rg5-d8ef5a9e08e75decdec4c25baac60da016726d7cf006748642551550088bf7e0`. The shared release lock was inactive afterward.
- Five requests sampled after deployment returned HTTP 200. Their elapsed times, in order, were 0.433, 0.259, 0.219, 0.803 and 0.586 seconds. This sample describes the verification window. Long-term incident recurrence remains under investigation.
- The official employee Android release is Build 65 (1.0.50). [Android release #330](https://github.com/AMASI-SA/amasi-mobile/pull/330)
- Verification made no provider writes. No separate dashboard/ads worker was activated. Qoyod remained outside the release scope.

## Verification of previous work

- [PR #654](https://github.com/AMASI-SA/AMASI-SA/pull/654) **merged** Aug 17: selected AI/ad/BNPL loops were *disabled* in FastAPI web process. **Never restart these by default**, and do not assume they run in a separately managed process.
- [PR #1001](https://github.com/AMASI-SA/AMASI-SA/pull/1001) **merged**: Qoyod unsent-orders bounded scan/single-flight optimization. **Out of scope**; leave it untouched.
- Existing `backend/dashboard_v2_routes.py` routes have `_heavy_dashboard_stage`, `governor.heavy("dashboard")` and `dashboard_cpu_budget.py` cooperative checkpoints, but in-process admission does not establish cross-process CPU isolation.
- `frontend/src/pages/AdvancedDashboard.jsx` background refreshes every 30s, on focus/online/orders events; it had a background-to-background single-flight guard but could still launch a background summary read while explicit foreground filtering/initial load was in flight.
- `backend/server.py::_local_startup` still launches Qoyod, Salla V3 shadow, token maintenance, operational balance, review resume, assembly completion, shipping/payment inbox and operational push in the web process. **Inventory only; don't edit these modules or starts under this narrower scope**.

## What changed in the task branch

1. `frontend/src/lib/dashboardFetchAdmission.js` (new): per-tab foreground-aware background request admission, explicit loads take priority. No schedule, API contract or financial values altered.
2. `frontend/src/lib/dashboardFetchAdmission.test.js` (new): Jest cases for foreground-vs-background, multiple explicit filters, background single-flight, cancellation/release invariants.
3. `frontend/src/pages/AdvancedDashboard.jsx`: uses admission gate to avoid overlap during initial/explicit period loads; maintains request-sequence check for stale results.
4. This task status document and issue #1724 updated to explicitly prohibit touching Qoyod.

**Result:** This reduces redundant dashboard reads; **not** an independently managed dashboard worker or a solution for Cloudflare 520 itself.

## November 4 implementation checkpoint

- The mounted dashboard regression holds a summary request open across a timer tick, focus and an explicit refresh. The previous page starts two requests on that first tick. The new page starts one and resumes polling after pending reads finish.
- Both dashboard SAR aggregation passes now use the existing cooperative reducer and CPU budget. The current-month selection still computes the same order list once. Two new HTTP regression cases cover the monthly and distinct selected-range paths, exact sales totals, and the event loop getting a turn during each distinct sum.
- Existing CI commands run locally on this change: profit/date race 51/51 and four-platform date 101/101. Backend bundle, currency, governor, source parity and observability suites: 90 tests and 5 subtests passed. Local seeded route tests used test-only cgroup settings to avoid sharing the executor's memory limit with unrelated jobs. Hosted CI status is tracked on the PR.
- [Isolated local contention benchmark](LOCAL-BENCHMARK.md) and [raw results](local-contention.json) compare idle and loaded synthetic business routes. They establish local contention and do not replace branch-matched Preview before/after measurements.
- [Workload inventory](WORKLOADS.md) traces the native and shadow reporting launchers and the campaign AI path that can dispatch live ad actions. No scheduler ownership has been changed.

## Offline comparison and supplier retry

- [Counterbalanced process comparison](LOCAL-ISOLATION.md) uses the same rate and input for every loaded arm. All 3,000 measured operations and 200 validated dashboard cost jobs completed. Supplier-create p95 fell from 103.14 to 24.75 ms and from 68.74 to 20.44 ms across two paired comparisons. This uses a supervised, disposable process in the local harness. It does not launch a service in an application deployment.
- The real-Mongo supplier regression disconnects the client after the first HTTP 201 is ready and before any response is delivered. Two fresh app instances replay the original request ID and return the single committed file. A new request ID for the same fully assigned piece gets HTTP 409. One dispatch, one dispatch event, one piece event and one visible supplier file are checked. All 59 cases in the supplier status-fence module passed. The test covers disconnect after commit; it does not determine the outcome of the November 3 file.
- The [mobile main source reviewed](https://github.com/AMASI-SA/amasi-mobile/blob/f2d7ed60fb569dd226c6b60e16538064c368f822/frontend/src/features/myProducts/repository.ts#L317) generates a fresh request ID for each explicit call. The APK installed on a specific phone was not inspected. Check saved supplier files before retrying after an ambiguous timeout.
- The November 4 support follow-up requested the lifecycle, isolated Preview path, and price for this project. No paid resource or environment change was requested.

## Additional acceptance evidence

- [Full dashboard HTTP A/B](full-summary-ab/README.md) compared 512- and 2,048-order datasets with verified SAR/QAR values. All 64 responses passed. At the larger size the callback ran during all six candidate currency passes and none of the six baseline passes. Both cohorts had slightly higher total response p95 on the candidate. The complete route was not consistently faster.
- [Two-web-process supplier replay](supplier-replay/README.md) demonstrates one saved file after the first server dies with its HTTP confirmation undelivered.
- [Global advertising lease](lease-recovery/README.md) documents the prior long-cycle overlap and the candidate's ownership, cancellation, recovery, and coverage limits. No scheduler was moved. Prior and candidate schedulers must not be assumed safe to mix without an orderly handoff.
- [Native and embedded employee routes](mobile-screens/README.md) cover pending review, reviewed catalog, and assembly with fixed page grants, plus order lists, badges and ready-to-ship. An account with reviewed-preparation access could read the catalog while its optional sales-ranking read returned 403. Push device registration runs separately. These are fixture results, not a diagnosis of one employee device.
- Additional employee symptoms reported early November 4: pending review, reviewed products, assembly/labeling and a separate notification registration banner. Support ticket #299768 has the attachment. The time the screenshot was received is not an individual request timestamp.

## Follow-up for the employee app

- The [staff app draft #329](https://github.com/AMASI-SA/amasi-mobile/pull/329) displays the reviewed physical-piece list as soon as the catalog arrives. On eligible accounts the historical sales read supplies the optional ranking. The screen preserves the employee’s selected pieces when that ranking arrives. Acceptance on an installed employee build and authenticated hosted Preview remains open.
- With the existing opt-in phase-one diagnostics enabled, the server now counts outcomes and records durations by route for pending reviews, reviewed products and orders, native assembly, the web ready-to-ship board, employee workspace, supplier dispatch creation and push device registration. HTTP 401 and 403 can be distinguished from origin-observed 500/502/503/504. The collector accepts only the fixed numeric series. It records no file numbers, product IDs, request parameters or employee details.
- Failures before the request reaches the matched application route require proxy or edge evidence. A single sample is insufficient for a route p95. The existing diagnostics expose p95 beginning at 20 observations and p99 at 100. No environment setting was activated for this experiment.
- The historical product-sales report now reads a smaller projection and gives other requests a turn during CPU-heavy work. In two local Mongo comparisons, the same 9,000 sale lines produced the same full response and used 1,440,534 projected BSON bytes instead of 9,407,377. [Fixture, parity checks and results](mobile-screens/README.md#historical-product-sales-get).
- Both dashboard HTTP handlers now give other requests a turn while applying historical Salla exchange rates. Across ten isolated repetitions on 8,192 orders, the median longest gap between ready callbacks in that step dropped from 56.08 to 5.29 ms. The frozen dashboard gate matched all 164 responses and database-read contracts. The full HTTP comparison on 2,048 orders preserved the complete responses and expected SAR totals. Whole-route timing and employee-device recovery remain separate acceptance checks. [Hydration experiment](full-summary-ab/diagnostic/currency_hydration.json).

In a separate routed test, all 32 full dashboard reads across `ABBA` and `BAAB` blocks returned the same expected totals of SAR 155,136 for the month and SAR 77,568 for the selected date. In ABBA, request p95 was 405.39 ms before and 404.10 ms after; in BAAB it was 427.35 and 417.58 ms. Loop p99 moved in opposite directions across runs (91.24 to 114.42 ms and 80.65 to 74.16 ms). Each number covers eight reads per arm per sequence on a shared local host. To reproduce, use the [full HTTP runner](full-summary-ab/run.sh) with the same frozen fixture and append `2048 ABBA HEAD 939d4a8f7cfce9ca0c93a99cc9d2595490f66b34`, then repeat with `BAAB`.

## Earlier test evidence and limits

- `node --test /mnt/data/mezanscope-test/dashboardFetchAdmission.test.mjs` on a local *behaviorally identical pure-JS gate*: **5 PASS / 0 FAIL**. This proves helper state transitions only; it is **not** the repository Jest suite nor end-to-end/CI/Preview performance validation.
- Initial commit evidence only. See the November 4 checkpoint and the current PR for the newer results. Baseline p95/p99 for employee supplier/label endpoints **not measured yet**. Do not invent them.
- These benchmark runs used local infrastructure. The later production web release and its verification are recorded above.

## Emergent platform capability gate

Official public help pages discuss background job workloads at Scale tier and Preview/Production deployment, but do not establish a **separate managed worker service** for *this* project. Asked support on current ticket #299768 to confirm process model, independent launch, restart/health/heartbeat/resources, shared build/secrets and V5 constraints. Sent a second clarification explicitly **excluding Qoyod** and limiting separation to dashboard/ads. **Until support confirms, never disable/relocate production jobs or launch an unmanaged subprocess under Uvicorn**.

If unsupported: evaluate a dedicated separately managed analytics app/service with isolated read-only queries, or managed scheduler/queue, preserving order/employee endpoint and shared auth boundaries; keep all current jobs untouched.

## Next safe steps

1. Await/read written Emergent answer; confirm independent worker lifecycle in Preview, plan and cost.
2. Compare the completed source inventory with the actual Preview job configuration once a branch-matched environment is available.
3. For any worker proposal, run fresh repository and integration checks on its own candidate.
4. If supported, build separate **dashboard/ads-only** worker/service behind disabled-by-default Preview gate, never including Qoyod/finance/orders/shipping/employee providers. Demonstrate restart/2-replica isolation and no duplicate advertising analytics events.
5. Benchmark dashboard and employee summary paths: /api/health, employee workspace, supplier assignment readback, label retrieval p95/p99 and event-loop lag under concurrent *synthetic* heavy dashboard jobs. No live provider writes.
6. Prepare a separate rollback and governed v5 release review if an independent worker becomes available.

## Prior checkpoint

Original checkpoint `e9148d71e4687c03ac6996a3d7535268c71d167a` recorded a broader scope with Qoyod safety work; it is now **superseded** by the owner's explicit narrower decision above. Do not treat former scope as authorized.
