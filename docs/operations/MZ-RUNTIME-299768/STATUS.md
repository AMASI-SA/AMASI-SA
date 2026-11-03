# MZ-RUNTIME-299768 — Worker Isolation / Operational Availability

## Checkpoint 2026-11-04 (analysis only)

- **Repository:** `AMASI-SA/AMASI-SA`
- **Task branch:** `codex/worker-isolation-299768-20261104`
- **Created from reviewed remote production branch HEAD:** `a56dbacd36678566eec3b5a76406db7582c3e7f2`
- **Issue:** https://github.com/AMASI-SA/AMASI-SA/issues/1724
- **Production changed:** **NO**. No merges, releases, leases, external writes, job triggers, or modifications to financial data.
- **Implementation state:** **NOT STARTED**. This checkpoint is a design/inventory preparation, **not** evidence that worker isolation is live or tests pass.

## Observed source evidence

1. Prior PR [#654](https://github.com/AMASI-SA/AMASI-SA/pull/654), merged 2026-08-17, disabled campaign AI, customer AI, ad-account sync, ad-spend catch-up, BNPL hourly auto-sync and Tamara sweeps **within the web process**, not a universal web/worker role split.
2. Current `backend/server.py::_local_startup` on inspected production branch still starts:
   - `salla_orders_v3.worker.start_salla_orders_v3_shadow_runtime`
   - `integrations.qoyod.worker.start_worker(..., interval_sec=5, batch_limit=25)`
   - `integrations.qoyod_manual.auto_send.start_worker(..., interval_sec=15, batch_limit=5)`
   - `salla_token_maintenance_loop`
   - `operational_balance_worker.run`
   - `order_review_resume_worker.start_worker`
   - `assembly_completion_delivery.start_worker`
   - `salla_integration.shipping_label_inbox.start_worker`
   - `salla_integration.payment_update_inbox.start_worker`
   - `operational_push.run`
3. `backend/server.py::on_startup` uses deferred `run_release_startup`, event-loop lag monitor, and process-local readiness, so role separation must retain health/readiness semantics and review what runs in each lifecycle.
4. `backend/integrations/qoyod/worker.py::start_worker` only uses a **module-local** `_WORKER_TASK`; this prevents duplicate tasks **within a single process**, **not across replicas**. Its task loop is eligible for a durable lease review. Verify Plan-B worker's own fences and provider reconciliation independently before changing it.
5. Emergent support readout: one Python process per web replica and about 30 background jobs; 11/12 `/api/health` probes <2s, one 20.8s. Support suggests long Python tasks block interactive service; **do not conflate evidence with proof of the exact Cloudflare/employee incident cause**.
6. The `/api/integrations/qoyod/unsent-orders` 125-day rescan allegation must be reconciled with prior [PR #1001](https://github.com/AMASI-SA/AMASI-SA/pull/1001) before introducing duplicate optimization.

## Architecture decisions to validate (not approved for production)

- Explicit `web` / `worker` process roles, with web **never** running long provider polling, billing automation, AI scans, Salla bulk sync, or periodic accounting tasks.
- Validate Emergent deployment support for a worker process/service with independent scheduling, restart/health and resources **before** implementing a config that could silently disable critical operations.
- Durable Mongo-backed leader/lease ownership with fencing, heartbeat, TTL, and crash takeover; **idempotency and provider-readback reconciliation** for every external write. A lease alone does not guarantee exactly-once delivery.
- No multiple simultaneous Plan-B invoice senders or duplicate webhook/shipment processes under 2 web replicas + any worker replicas.
- Keep web critical fulfillment endpoints available if worker is busy/down; worker events must expose status/lag and avoid uncontrolled backlog.
- If standalone worker unsupported in current Emergent deployment, stop and propose alternatives; do not spawn an unmanaged background process from a web entrypoint.

## Planned checks (not run)

- Static and integration tests: web role creates **zero background worker tasks**, worker role starts only explicitly enabled tasks; unknown/missing role fails closed.
- Replica and failure tests: two web instances, two worker contenders -> exactly one active worker owner; lease expiration, takeover, stale fence, restart.
- Financial fail-closed behavior: Qoyod pipeline/Plan-B and Tamara/Tabby accounting remain no-duplicate; unknown outcome requires reconciliation first.
- Functional tests: employee product workspace, supplier file creation readback, order review, Salla webhook intake, shipping label retrieval.
- Latency and failure tests: synthetic heavy worker activity does not block web; route p95/p99, event loop lag, HTTP errors/Cloudflare correlation.
- Preview CI, independent review, documented rollback and V5 release rehearsal. Production deployment requires separate merchant approval.

## Next safe actions

1. Refresh branch HEAD against GitHub production branch; inspect `AGENTS.md` and verify no unrelated release lease.
2. Inventory all `asyncio.create_task` and worker start sites, including modules imported by `server.py` and non-web cron entries; capture in a task-scoped checklist.
3. Determine supported Emergent process model/worker lifecycle **without changing production or secrets**.
4. Write tests for process-role isolation and distributed-leader semantics; then implement incremental changes on this task branch with a **Draft** PR after tests.
5. Record source SHA, test commands/results, risks and next action at each checkpoint. No publish/merge/Production writes without explicit instruction.
