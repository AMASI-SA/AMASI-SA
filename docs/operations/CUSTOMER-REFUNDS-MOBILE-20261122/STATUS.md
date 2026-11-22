# Shared employee refund permission and mobile adapter

Date: 2026-11-22 (Asia/Riyadh). Branch: `codex/assembly-queues-fast-read-20261121`. [PR2050](https://github.com/AMASI-SA/AMASI-SA/pull/2050); mobile [PR373](https://github.com/AMASI-SA/amasi-mobile/pull/373).

## Authority and integration

The user authorized implementation/publication and subsequently allowed the employee app to publish first while Mezan waits. Backend publication is deferred.

This checkpoint starts from `92db6fed7d25caff6fc3f7a42770d78e72f9c098` (already containing production `9b4fb716b430c32073b5ea7abaeaff62646d9633`) and integrates frozen canonical source `4c308b19bb5d972bea5fff092bc0efbe9b70b29f`, [PR2067](https://github.com/AMASI-SA/AMASI-SA/pull/2067). Automatic merge had no conflicts. Other-conversation checkout, source branch, financial writer ownership and release lease are untouched. Earlier employee queue/monitoring/notification work is retained.

## Added contract

- `customer_refund_access.py`: shared owner-managed employee-bound none/read/write grant with explicit bank/provider sources; derives mobile page key and Mezan refund access. Manager wildcard is not financial authority.
- Owner-only GET/PUT `/employees-v2/management/employees/{id}/customer-refund-access`: current actor/employee validation and atomic policy/page/session compare-and-set. Unrelated source rights are preserved; explicit null/malformed grants fail closed.
- Mezan employee management opens a dedicated lazy editor. Generic policy updates preserve nested grants; revocation remains authoritative.
- `customer.refund_read` permits only refund inbox/order/context, without execute, collection or ledger rights. Resource projection uses exact refund paths or explicit `refund_context=true`.
- Canonical order READ returns 404 if no usable authorized source/method. This closes the independently reproduced Tamara-only-reader/Tabby-order disclosure; execution semantics are unchanged.
- GET `/operational-balances/workspace/mobile-refunds` and `/order/{number}` reuse canonical amounts, pending cases, basis and source authorization. Eight rows/page, exact search without inbox scan, skinny status enrichment for displayed rows, no-store, cancellable 12-second deadline and final authorization check.
- `/mobile/operations-monitoring/preparation-pending-counts` fixes the existing count-only arguments. Misleading dates/directory parameters cannot invoke historical metrics or product details.

The adapter owns no financial writer or provider/bank call. Final confirmation uses the canonical `/workspace/refunds/record-execution`. Its required actual reference and exact documentary/reservation constraints are projected faithfully. The upstream 20,000-candidate ceiling remains; UI pagination does not imply eight-row database cost.

## Paired mobile behavior

Form → local save/review → separate review → final confirmation → refreshed table and green 3000ms popup. Failures use a red 3000ms popup and preserve entered values. Initial save makes no request/reservation. Confirmed uncertain writes keep one immutable secure command for explicit manual retry. Serialized compare-and-clear prevents stale screens deleting newer attempts. No automatic replay or background list polling.

For app-first publication, new refund menu/editor require literal server capability even for owners. Legacy push/count/queue behavior remains supported. Only the exact unsupported-field rejection permits push downgrade; missing financial coverage is unknown. Monitoring fallback is 404-only to the existing cheap directory. Optional unread preview and detailed pending-stage projection wait for the server update.

## Verification evidence

- Root integrated real-Mongo/HTTP run: **213 passed +7 subtests, 3 failures**. Failures were Motor/server-selection timeouts. A fresh isolated rerun of exactly those three cases, unchanged source: **3 passed, no skips, 2.59s, exit0**. Do not describe the original combined run as fully green. Resource contention is plausible, not proved; no writer/test/timeout was changed.
- That run covers new grant/bridge/adapter, operational access/refund execution/collection, mobile/web authorization and financial notice/push. Both operational and fixture Mongo URIs referenced isolated loopback Mongo, never live data.
- Root integrated web: **84 passed**, exit0 in ApprovedFinance, ApprovedEntry, simpleBalancesApi, CustomerRefundAccessDialog and EmployeesV2Management.
- Monitoring alias/count/read-budget suite: **21 passed**, zero skips, including authorization before storage and fixed count-only routing.
- Previous production/task merge: **76 passed +7 subtests** with real isolated Mongo.
- Mobile root: complete locked dependency install, full TypeScript exit0, nine fresh integrated release/behavior suites pass, including 19 refund groups and immutable auth preservation. Independent stale-journal race checks pass. See mobile matching status for details.

CI includes the new grant, bridge, adapter and web editor. Inspect remote checks on the new exact SHA; local results do not establish CI or publication success.

## Release and continuation

No new release intent/lease, production merge, shared `/app` modification or publish occurred. This integration merge is **not** a final governed source/intent pair. PR2067 remains separately owned; recheck its current state before preparing a release.

Next: preserve both checkpoints and publish the employee app after its exact bundle/build gates and Expo access are available. New server capabilities remain hidden meanwhile. Later inspect current production/other release, verify actual shared `/app` guard inactive, then follow `AGENTS.md` v5: reviewed source A, intent-only B, clean-clone reproducible build, explicit deployment success and three identity probes. Never alter another conversation's lease or infer publication from CI.

Issue1006 reached its comment limit; this document and PR handoff are durable continuation. Production changed: **no**. No live customer refund, provider/bank action, permission grant, external notification or financial adjustment.
