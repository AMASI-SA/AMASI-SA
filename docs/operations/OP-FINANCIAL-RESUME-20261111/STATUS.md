# Operational accounting resume — 2026-11-11

## Status and scope

- Task: `OP-FINANCIAL-RESUME-20261111`.
- Repository: `AMASI-SA/AMASI-SA`.
- Evidence-only branch: `codex/operational-accounting-resume-20261111`.
- User returned to the operational-accounting review after the dashboard performance release.
- **Review checkpoint only. Production changed: no.** No application edits, financial writes, settlement approvals, source sync, historical backfill, lease, merge or deployment were performed.
- This document contains code and workflow findings only. No live account balances, order identifiers, receipts, employee details or source records are retained here.

## Exact reviewed source

- Production branch: `hotfix/prod-snap-meta-final`.
- Fresh explicit fetch, repeated after the review, returned `245a0beccbdbc2585a3ec2214e808eeef3734b57`.
- Its tree is `d5240e4fe0e72890d4e06d6da43e07f5ae680962`, identical to the frozen PR1884 intent commit `ff648cf5ebcc29acdf25fa855a8c80481b529f6c` used for read-only code inspection.
- Do not rely on the common repository's stale remote-tracking alias: use the exact explicit fetch result when resuming.
- Preserve PR1884 dashboard cadence/catalogue changes and PR1883 Snapchat spend-only refresh. Preparation batch/file generation remains excluded.

## Findings verified in current code

### 1. Table completeness is too broadly coupled

`backend/operational_financial_position_core.py::build_financial_position` computes one `actual_complete` flag from all projection issues lacking a currency or matching the selected currency. `_actual_tables` passes that flag to banks/POS, cash, suppliers, employees, custody, shipping, couriers, other parties and advertising accounts.

Several source issues created by `backend/operational_balance_sources.py::issue` carry no currency. A product-cost or shipment issue can therefore mark unrelated tables incomplete. This is not evidence that those unrelated balances are wrong or that they can be certified complete without their own source coverage.

Required repair: give each table a proven coverage contract and relevant issue scope, while keeping overall position completeness conservative. Do not erase warnings or promote unknown values to zero. `_cost_table` already has partial `bucket_completeness` support; reuse it where applicable.

### 2. Shipping still requires a shipment/waybill

- `backend/operational_prepaid_shipping.py::source_event` calls `_current_shipping` and `bind_completed_waybill` before creating an eligible prepaid charge.
- `backend/operational_delivery_evidence.py::_current_shipping` requires shipment ID and tracking number; the deferred path also calls `_order_waybill`.
- The saved owner rule is prepaid on an evidenced completed event and deferred/COD on an evidenced delivered event, after the currently saved start, without a label prerequisite.

Restore only the extra label eligibility condition. Retain actual event timestamps, reliable carrier identity, effective approved tariff, ownership/currency/cash-account validation, conflict detection and idempotency. Existing recorded monetary legs must remain unchanged.

**Historical processing is a separate release blocker:** expanding eligibility can activate old unposted charges on the automatic worker. A read-only impact inventory and reviewed gate must precede any release of that eligibility change. Do not reuse an old cutoff from conversation memory; the current saved configuration is authoritative.

### 3. Daily movements and courier approval already exist

- Daily movement entry/storage is present in `frontend/src/pages/operational/DailyMovements.jsx` and `backend/operational_balance_routes.py`.
- The current GET movements path returns reversed full saved history; `MovementHistory.jsx` remains a simple table. The restored scoped filtering/pagination/historical after-balances from PR1846 are absent from this reviewed production tree.
- `backend/operational_driver_payment_review.py` is wired to approval options and decisions in `store_delivery_payment_review_routes.py`. It can create evidenced bank receipts or POS receivables and protects against duplicate source/receipt consumption. `operational_driver_review_report.py` validates linkage to recorded effects.
- Despite that implementation, `operational_delivery_report_coverage.py` always emits `operational_driver_noncash_posting=incomplete`. Replace this stale blanket statement with evidence-based diagnostics. Do not rebuild the existing bridge or assert every live receipt is reconciled.

### 4. Forecast and profit are incomplete implementations

In `operational_financial_position_core.py`:

- `_cod_table` always returns `complete=False` and unknown overall totals.
- `complete_collection_forecast` is fixed to incomplete and `profit_period_coverage` to unavailable.
- `_profit_rows` creates unavailable profit components; salary may have only a recorded partial subtotal.
- `completeness.forecast` and `completeness.profit` remain false.

`operational_provider_report_coverage.py` likewise enumerates provider evidence but does not certify complete provider-statement coverage. Repairing a tariff or label alone cannot complete these sections. They need a defined same-period population and complete source coverage, with genuine unknowns preserved.

### 5. Source warnings need precise scope and explanation

| Code | Meaning and required handling |
| --- | --- |
| `canonical_salla_source_missing` | Missing canonical Salla origin; emitted before eligibility/date filtering. Identify relevant unresolved coverage before calling every row a missing operational debt. |
| `mz2_product_cost_incomplete` | Product/service cost or quantity could not be established; preserve the specific cause rather than replacing it with zero or blocking unrelated bank tables. |
| `operational_delivery_current_shipment_required` | Current label-dependent evidence rule; distinguish the excessive prerequisite from genuine carrier/event conflicts. |
| `operational_delivery_timestamp_missing` | Missing dated delivery evidence; do not substitute creation/update dates. |
| `provider_fee_estimate_unavailable` | Gross evidence may exist while fee/net evidence does not. Expose the policy/plan/effective-date failure and reconcile saved fee policy. |

The review did not verify saved Tabby/Emkan tariff configuration or all live transaction evidence. Existing owner decisions must be checked against actual saved policies before changing them.

## Recovery PRs: fresh remote audit

All three remained open Drafts and unmerged at review. GitHub's transient mergeability result differed between reads, so it is not a release decision or recorded blocker here. All are based on old `cedd7158ba1a7fbae13d1503a503c61b19ad7ae6`; their exact-head verification is not verification of integration with current production.

| PR | Exact head | Current evidence and remaining work |
| --- | --- | --- |
| [1844](https://github.com/AMASI-SA/AMASI-SA/pull/1844) | `413681f5ba77d327c54952e166052fb6a865d8c1` | 11 successful exact-head workflows. Narrow incoming-COD allocation indexing is still absent from current `operational_balance_service.py`, which rescans movements/allocations per claim. Integrate the small read optimization on current production and repeat complete report/input parity plus required CI. |
| [1846](https://github.com/AMASI-SA/AMASI-SA/pull/1846) | `aee4bc778155aba317f64aec19bfe426602f8b4b` | 18 successful and 2 failed workflows. Both failing real-Mongo jobs report the same 16 permission/assignment failures. Resolve the intended role/account contract and tests together; do not waive failures or weaken access. Daily read still scans full history synchronously. |
| [1847](https://github.com/AMASI-SA/AMASI-SA/pull/1847) | `dcb5ad08a1bdb905215d4f6233a0627ca9096089` | 11 successful exact-head workflows. Historical impact inventory and processing gate remain explicitly unimplemented. Scope is prepaid only; deferred owner-rule completion remains separate. Do not merge/deploy on CI success alone. |

PR1846 current failing jobs:

- [Operational backend](https://github.com/AMASI-SA/AMASI-SA/actions/runs/42639813733/job/129237596714): 16 failed / 3194 passed.
- [Operational stock](https://github.com/AMASI-SA/AMASI-SA/actions/runs/42639813459/job/129237595264): 16 failed / 3103 passed.
- Failures concern staff consolidated-report authorization, explicit movement-import permission, account-scoped results and read-only bank assignments.
- Employees and operational frontend CI passed. The old local salary-runner failure is not the current remote CI blocker; the stale STATUS text must not override current results.

Courier prerequisite [1843](https://github.com/AMASI-SA/AMASI-SA/pull/1843) merged as `9b98bc1817ffce7d644f408c63e78bedf915686f` and is an ancestor of the reviewed production. Do not repeat that release. No newer superseding PR was named by the three recovery PRs' fetched metadata/timelines.

## Verification and practical limits

- Read complete root `AGENTS.md`, current ledger and relevant historical recovery records.
- Explicit production `git fetch --no-tags origin hotfix/prod-snap-meta-final` and `git rev-parse FETCH_HEAD` succeeded twice with the reviewed SHA.
- `git status --short` on the frozen reviewed worktree was clean; its HEAD/tree were verified.
- Root and an independent read-only source reviewer inspected the critical report, delivery, source and courier-review paths. A separate read-only reviewer checked exact PR heads, current workflow results and task STATUS records.
- Authenticated browser read-only inspection of `/operational-balances` and `/operational-balances/financial-position` confirmed saved-report warnings and affected sections. Only navigation/disclosure controls were used; no manual recalculation, financial review, source sync or reconciliation action was invoked. Snapshot values are not independently reconciled balances and are intentionally omitted here.
- This was an audit: no new application test suite, financial write or performance benchmark was executed. Historical synthetic speedups are not live-production measurements.

## Next safe continuation

1. Fetch production again and verify this checkpoint branch plus latest ledger before edits. If production has moved, inspect its changes before integrating recovery code.
2. Extract PR1844's narrow read optimization onto a fresh source branch and verify exact report/input parity and required CI; preserve the recent performance releases.
3. Prepare table-specific coverage and accurate courier-review diagnostics as independent read changes. They must measure coverage, not simply remove incomplete badges.
4. Reconcile PR1846's permission matrix and real-Mongo failures before restoring its daily-history enhancements.
5. Prepare a read-only inventory and explicit historical gate for the shipping-rule correction; preserve recorded monetary history. Separate any later historical processing authorization from source repair.
6. Complete relevant source evidence/costs/fees and design same-period forecast/profit coverage. Do not infer profit from liquidity or fill unknown amounts with zero.

This evidence-only branch is a continuation record, **not a deployable candidate**. No v5 intent was prepared and no production acceptance is claimed. Any later release follows the normal new source A / deterministic build / intent B / review / authorized publication / verification process.
