# MZ-RUNTIME-299768 — Dashboard/Ads isolation from employee and order APIs

## Latest owner instruction: 2026-11-04 — authoritative scope

**NO QOYOD CHANGES.** This instruction supersedes the earlier broad worker-isolation specification. Keep Qoyod Plan-B and legacy pipeline auto-send, provider reconciliation, reports, credentials, leases, accounting, and all current Qoyod jobs **completely unchanged**. Do not move, stop, start, retime, enhance or test live financial writes as part of this issue. Keep all existing employee workflow, shipping dispatch, payment update, Salla webhooks and order operations intact.

Goal: dashboard summary / reporting, advertising analytics / synchronization and CPU-heavy analysis should not contend with employee preparation, supplier files, orders or shipment/label APIs.

## Status and source

- **Repository:** `AMASI-SA/AMASI-SA`
- **Issue:** https://github.com/AMASI-SA/AMASI-SA/issues/1724
- **Branch:** `codex/worker-isolation-299768-20261104`
- **Branch baseline:** `a56dbacd36678566eec3b5a76406db7582c3e7f2` (must refresh before any proposed release)
- **Production changed:** **NO**. No merges, deployments, release leases, Production env/config writes, provider writes, or operational data mutations.
- **Status:** Draft [#1726](https://github.com/AMASI-SA/AMASI-SA/pull/1726). Client admission and cooperative dashboard sales aggregation are implemented. Physical worker isolation awaits written confirmation from Emergent.

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

## Earlier test evidence and limits

- `node --test /mnt/data/mezanscope-test/dashboardFetchAdmission.test.mjs` on a local *behaviorally identical pure-JS gate*: **5 PASS / 0 FAIL**. This proves helper state transitions only; it is **not** the repository Jest suite nor end-to-end/CI/Preview performance validation.
- Initial commit evidence only. See the November 4 checkpoint and the current PR for the newer results. Baseline p95/p99 for employee supplier/label endpoints **not measured yet**. Do not invent them.
- No Production or Preview runtime deployment occurred.

## Emergent platform capability gate

Official public help pages discuss background job workloads at Scale tier and Preview/Production deployment, but do not establish a **separate managed worker service** for *this* project. Asked support on current ticket #299768 to confirm process model, independent launch, restart/health/heartbeat/resources, shared build/secrets and V5 constraints. Sent a second clarification explicitly **excluding Qoyod** and limiting separation to dashboard/ads. **Until support confirms, never disable/relocate production jobs or launch an unmanaged subprocess under Uvicorn**.

If unsupported: evaluate a dedicated separately managed analytics app/service with isolated read-only queries, or managed scheduler/queue, preserving order/employee endpoint and shared auth boundaries; keep all current jobs untouched.

## Next safe steps

1. Await/read written Emergent answer; confirm independent worker lifecycle in Preview, plan and cost.
2. Compare the completed source inventory with the actual Preview job configuration once a branch-matched environment is available.
3. Run repository Jest/CI on branch, inspect Draft PR test results. Investigate any failures before changing more.
4. If supported, build separate **dashboard/ads-only** worker/service behind disabled-by-default Preview gate, never including Qoyod/finance/orders/shipping/employee providers. Demonstrate restart/2-replica isolation and no duplicate advertising analytics events.
5. Benchmark dashboard and employee summary paths: /api/health, employee workspace, supplier assignment readback, label retrieval p95/p99 and event-loop lag under concurrent *synthetic* heavy dashboard jobs. No live provider writes.
6. Prepare documented rollback and revised guarded V5 release review. **Never merge or publish Production without separate explicit approval.**

## Prior checkpoint

Original checkpoint `e9148d71e4687c03ac6996a3d7535268c71d167a` recorded a broader scope with Qoyod safety work; it is now **superseded** by the owner's explicit narrower decision above. Do not treat former scope as authorized.
