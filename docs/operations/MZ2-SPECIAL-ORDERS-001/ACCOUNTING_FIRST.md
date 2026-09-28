# Accounting-first integration and release dependency

Task MZ2-SPECIAL-ORDERS-001, owner direction dated 2026-09-28.
**IN_PROGRESS_NOT_READY_TO_DEPLOY. All live activation remains OFF.**

## Binding owner decision

Prepare special orders to integrate with Mezan Accounting while Accounting work proceeds separately. **Do not deploy special orders before Mezan Accounting has been deployed and its actual runtime identity verified.** Accounting publication is a necessary dependency, not automatic permission to deploy or activate this feature. A later explicit owner approval bound to the special-order candidate/environment is still required.

We do not amend Codex's accounting branch, frozen source/intent pair, review scope, pause controls or release leases. The current development reference is PR1180/B7 `121fbcf7d7c3f1dc9317e3edf108f512cbcb37e3`, tree `2a6a6a611c2222595212dac9317aeed0f1963c5c`. It is a pinned candidate contract, NOT proof of deployment or a requirement that Codex ship that exact SHA. Refresh reference and tests if it changes.

## Integration ownership

Accounting is the authority for owner transaction, global pause/transition, safe active opening, accounts, journal posting/verification, balances and periods. The special-order package owns order purpose/current options, operational snapshots, evidence linkage, agreed contribution allocation and its stricter local write scopes. Its own journal event registry is only provenance/replay evidence, never another ledger.

The prior `ledger_adapter.py` calls `ledger_core.post_txn_group` and reads `general_ledger`. At the accounting reference this is a LEGACY writer, blocked by `v2_active`. The former description as MZ2-native must not be treated as final V2 compatibility. Issue1006 comment5875915298 records this mismatch. No relabeling, legacy fallback or loosening of the transition gate is allowed.

R7 adds an isolated `mz2_v2_port.py`: immutable integer-minor plans, canonical two-decimal strings, deterministic owner/order/event idempotency key, fixed special-order source/type, public `post_journal_v2`, public opening and journal verification. It accepts only a live `accounting_atomic.SessionDatabase` for the same owner; fresh persisted actor permission, global pause and V2 transition are rechecked. Posting and read-back share the SAME actual Mongo session. No index setup, new transaction, opening, resume, activation, raw ledger collection access or router is added in the port.

This port is a transport boundary, **not complete bank/receipt/supplier/COD business validation**. Callers must verify evidence ownership, approved tax/classification snapshot, actual account and balance, event eligibility, local writer admission and policy authority. The existing action registry remains authoritative; server business wrappers fix the appropriate posting permission, never an HTTP body. Automatic courier integration must preserve the native authorized actor/event contract rather than grant couriers accounting permissions.

## Required final transaction composition

1. Authenticate actor and enter the existing global accounting owner transaction.
2. Bind the special-order/native operation to the SAME session and acquire its local scopes in a consistent lock order (global before local). No nested independent supplier or delivery transaction.
3. Re-read persisted permissions, current order/evidence/policy, global transition and local epoch. A local resume can never undo a global pause; a global resume cannot implicitly open local scopes.
4. Call only the public V2 posting/verification services; write operational result/event evidence in that transaction; return only after commit.
5. Any evidence, permission, posting, read-back, inventory or workflow failure aborts all related writes. No receipt-only 'paid', half supplier invoice, or second collection on remittance/retry.

The new port DOES NOT yet replace legacy post/read-back calls in ledger_adapter, supplier/inventory/delivery/reassignment/settlement bridges or their shared native routes. Those migrations and combined tests remain required. The standalone transport fixture is not end-to-end Mezan acceptance.

## Preparation and release gates (must stay separate)

| Gate | Required evidence | What is NOT sufficient |
| --- | --- | --- |
| Accounting dependency | Separate approved Accounting release, Deployment Succeeded and real runtime source/release identity verification | PR merged, green CI, a candidate SHA or a user-supplied deployed=true |
| Compatibility | Exact published source/API/contract matches the tested integration, including accounts, native routes, permissions, global pause and report readers | Tests against a now-stale candidate |
| Special-order code release | All source overlaps reconciled cumulatively; independent review, complete required CI, compatible recovery build and explicit owner release approval | Dependency becomes deployed |
| Financial activation | Accounting safe_active + verified opening/cutover + global open + local permitted scopes + current permissions and evidence | Code published or an isolated test control set to open |
| Rollback acceptance | Preserves the already-published Accounting version/schema and all special-order history, fences all writers, verifies conservation and restore/recovery drill | Old Salla-only reader or resetting database history |

Before deployment verification the feature is WAITING_FOR_VERIFIED_ACCOUNTING_DEPLOYMENT. After dependency verification it is WAITING_FOR_FINAL_COMPATIBILITY_AND_APPROVAL, not automatically ON. An unavailable/mismatched dependency leaves the feature blocked and readable; it never boots Accounting or posts catch-up events automatically.

## Compatibility coverage to finish

- Ordinary Salla behavior unchanged. Replacement/free gift/creator rows do not increment sales count, AOV or CPA denominator. Creator costs belong in total marketing cost separately from paid-platform spend; accounting revenue/tax must follow the approved classification rather than KPI exclusion.
- Bank receipt: claim versus approved movement; same owner/currency/account; before/after cutover; rejections, retries and partial bank + COD; reports use verified V2 entries.
- Supplier/stock/service/shipping: same native invoice and piece/physical stock semantics, recognized once, correct payable and recoveries; mixed ordinary/special atomicity.
- Courier: cash custody, noncash review, fees, partial/net remittance, reassignment, refund/cancellation and stale device state; no sales re-recognition.
- Persisted Salla remainder sidecar, no duplicate existing debt or original sale; preserve source-paid reconciliation.
- Pause races: global pause after local resume; local pause after global resume; read-only history while blocked; worker/client/epoch changes and mixed-table operations. Exercise both controls together, not one separately.
- Posted V2 balances and reporting provenance; legacy history remains unimported. Include V2 groups/legs/audit/sequences and their controls in recovery evidence, not only legacy general-ledger totals.
- Web and both actual Android apps, native PDFs/carrier labels, all-worker inventory and recovery/load drills.

Six known shared paths with Accounting1180: fulfillment_v2_routes.py, order_engine/salla_refresh.py, order_review_routes.py, preparation_piece_operations.py, store_delivery_settlement_routes.py, supplier_receiving_routes.py. Other evolving supplier/courier candidates such as1183 are additional compatibility inputs, not automatically copied. Keep every current Production fix and frozen Accounting source intact.

## Current proof boundary

Local pure payload tests:30 PASS, compileall PASS. Dedicated CI is configured to checkout Accounting B7 separately and test its real public API with a disposable loopback Mongo replica set, while keeping all Accounting tracked files unchanged. No original Accounting code is copied into this task branch. Record actual run/artifact results after completion; do not infer them from workflow existence.

Existing311/14/55 R6.1 results are historical own-branch tests and do not prove this V2 migration. Existing PR supplier fingerprint blocker remains. None of the code in this checkpoint is a deployed release, live pause/resume, accounting activation or accepted rollback drill.
