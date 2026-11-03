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
- **Status:** dashboard-only client admission mitigation implemented; complete backend/process isolation **BLOCKED pending written Emergent confirmation**. **Do not claim incident resolved.**

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

## Test evidence / limits

- `node --test /mnt/data/mezanscope-test/dashboardFetchAdmission.test.mjs` on a local *behaviorally identical pure-JS gate*: **5 PASS / 0 FAIL**. This proves helper state transitions only; it is **not** the repository Jest suite nor end-to-end/CI/Preview performance validation.
- GitHub Jest/CI and Draft PR state: see PR once created. Baseline p95/p99 for employee supplier/label endpoints **not measured yet**. Do not invent them.
- No Production or Preview runtime deployment occurred.

## Emergent platform capability gate

Official public help pages discuss background job workloads at Scale tier and Preview/Production deployment, but do not establish a **separate managed worker service** for *this* project. Asked support on current ticket #299768 to confirm process model, independent launch, restart/health/heartbeat/resources, shared build/secrets and V5 constraints. Sent a second clarification explicitly **excluding Qoyod** and limiting separation to dashboard/ads. **Until support confirms, never disable/relocate production jobs or launch an unmanaged subprocess under Uvicorn**.

If unsupported: evaluate a dedicated separately managed analytics app/service with isolated read-only queries, or managed scheduler/queue, preserving order/employee endpoint and shared auth boundaries; keep all current jobs untouched.

## Next safe steps

1. Await/read written Emergent answer; confirm independent worker lifecycle in Preview, plan and cost.
2. Complete inventory **only of live dashboard/ads workloads**, including work already disabled by #654, and classify analytics-only vs business-critical/ad accounting. Preserve all operational tasks.
3. Run repository Jest/CI on branch, inspect Draft PR test results. Investigate any failures before changing more.
4. If supported, build separate **dashboard/ads-only** worker/service behind disabled-by-default Preview gate, never including Qoyod/finance/orders/shipping/employee providers. Demonstrate restart/2-replica isolation and no duplicate advertising analytics events.
5. Benchmark dashboard and employee summary paths: /api/health, employee workspace, supplier assignment readback, label retrieval p95/p99 and event-loop lag under concurrent *synthetic* heavy dashboard jobs. No live provider writes.
6. Prepare documented rollback and revised guarded V5 release review. **Never merge or publish Production without separate explicit approval.**

## Prior checkpoint

Original checkpoint `e9148d71e4687c03ac6996a3d7535268c71d167a` recorded a broader scope with Qoyod safety work; it is now **superseded** by the owner's explicit narrower decision above. Do not treat former scope as authorized.
