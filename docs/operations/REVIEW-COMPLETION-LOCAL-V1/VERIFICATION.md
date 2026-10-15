# Local Review verification

Application evidence commit: `e8435fe9f049a18ae2d8d81b421060db93a6de61`.
Measured baseline: `128bfc1c2b4670d853a8bce33f15a667d2fdb120`. Current candidate base: `c411e6b48705fdd30a150630070eabacaa09cee0`.
Subsequent local-v1 changes correct test imports/fixtures/runner configuration and retain evidence only. Production advanced externally via #1302; its Shipping/Printing changes are retained on the current-base branch. Review completion/provider implementation is unchanged from the measured path. Current-base compatibility needs fresh CI, recorded in the Draft PR and Issue #1006.

## Acceptance evidence

| Contract | Evidence |
| --- | --- |
| Local HTTP200, completed operation, one workflow/event, visible products, provider stays pending | `test_review_local_completion.py`, real Mongo and ASGI |
| Zero provider calls including completion details/images/queue reload | External HTTP transport tripwire and explicit Salla scheduling/refresh/provider spies; frontend local-only request tests |
| Product/SKU/variant/options/selections/quantity/payment/unknown change rejection | Local completion mutation cases before transaction claim |
| Acceptance/source revision/cancellation/generation/component guards | Local mutation cases and retained G47 suites |
| Double-click/retry, rollback and timeout/restart | Real Mongo owner transaction; event validator failure; cancellation before commit; immutable completed identity |
| Pending Salla cannot demote local approval, queue pagination/count | `test_review_local_queues.py`, seven real-Mongo tests |
| Supplier dispatch/employee workspace/allocation/start/receiving | `test_review_local_downstream.py`, exact tenant/order/operation proof; unknown/unproven modes fail closed |
| Assembly/direct and mixed orders, current address and component/source fences | `test_review_local_assembly.py`, nine real-Mongo tests including late mandatory instruction retry |
| Historic operations not adopted/migrated/redispatched | Legacy prepared/syncing/provider_confirmed documents remain unchanged through worker restart and rejected new-local requests |
| No financial/provider/printing implementation changes | Restricted operational transaction, side-effect collection checks and unchanged source boundaries |

Local final application suites: 47 local +53 subtests; 178 Review +73 subtests; 136 G47 +78 subtests. All zero failures/errors/skips. Extended preparation/shipping/legacy image suites after runner/fixture corrections: 213 PASS, zero failures/errors/skips. Frontend affected suite: 61 PASS, Vite build PASS. MongoDB 8.0.12 replica set, loopback; no Production fallback.

Official runs at the application evidence commit:

- [Review Completion](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37947064769): 244 PASS +126 subtests, acceptance 1 PASS; exact checkout recorded. Artifact 11624915896.
- [Security Gate](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37947072258): PASS; public dependency strict audit, frontend audit/build, 1503 frontend tests, existing security contracts. No Security workflow/dependency/suppression changes.
- [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37947068375): Python and JS/TS PASS; workflow_dispatch checkout `e8435fe...`, no pull merge ref.

The first wider [G47 run](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37947802648) failed test collection because a test helper was imported as a top-level module. The first wider [Fulfillment run](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37947807668) found an incomplete legacy mock and an obsolete assertion requiring queue-triggered Salla synchronization. These are recorded failures, not passes. Package-qualified test imports, the explicit legacy fixture and the newly approved local queue contract address them. Current rerun results belong to the Draft PR/Issue #1006 continuation ledger.

## Performance boundaries

`PERFORMANCE.json` and both CSV files retain 80 measurements per path (three warm-ups excluded). Provider latency is simulated at 50ms/request. Baseline uses the Production-identical TREE; local uses the exact clean application evidence commit. No application server or Production endpoint was started/called.

| Measurement | Provider-backed baseline | Local v1 |
| --- | ---: | ---: |
| Salla requests per completion | 8 | 0 |
| Mongo commands per completion, including index/transaction commands | 255 | 107 |
| Transactions per completion | 13 | 1 |
| HTTP p50 / p95 / p99, ms | 982.6 / 1265.1 / 1349.2 | 247.0 / 290.7 / 375.4 |
| Products API visibility p50 / p95 / p99, ms | 1021.0 / 1316.2 / 1391.9 | 284.9 / 324.4 / 424.2 |
| Python CPU p50 / p95 / p99, ms | 296.9 / 485.2 / 617.5 | 140.6 / 187.5 / 257.3 |
| Total transaction time per request p50, ms | 360.3 | 134.0 |
| Individual transaction p50 / p95 / p99, ms | 15.1 / 73.6 / 105.8 | 134.0 / 157.3 / 225.6 |
| Distribution of request-local event-loop p99 lag, p50 / p95 / p99, ms | 13.7 / 17.8 / 24.6 | 11.2 / 13.6 / 40.3 |

The single transaction is longer than an individual old transaction, while total transaction time and total commands decrease. A local scheduling outlier reached 136.7ms (old maximum 28.0ms); do not claim uniformly improved loop-lag tails. These are small synthetic fresh-database measurements on Windows, not load capacity, browser paint, or a Production latency guarantee. A later separately authorized staging load test is needed for an operational SLA.

No migrations, recovery, historical replay, deployment, Intent change, lease action or Production writes were performed. See `ROLLBACK.md`: after local approvals exist, a provider-only rollback cannot safely interpret Salla pending as unreviewed.
