# OP-UNIFIED-BALANCES-20261102

Status: WIP, pre-integration remediation. NOT release-ready.

## Authority and immutable inputs

The owner requested one combined release, not separate deployments, preserving all subsequent production work. Financial corrections, refund execution, opening rewrites and historical replay are NOT authorized by code-publication permission.

- Production baseline freshly read: `3d8720860fb524d729f6c83a8c56c5b4de3a2afb` (includes customer partial-refund release #1686).
- Provider PR #1685: `88c7872072e6e6a149a92153a732ae4c0551ae21`.
- Financial follow-up PR #1677: `df17ef3ff21e2515ed24ec61d5d8019cf9cb2fe5`, inheriting #1675 at `a8ffd5a81637bed8c4462f7bb4c2b059782cd520`.
- Common historical base of production and follow-up: `3bac875cfbcbc7f66849edae83c478dd194d9c12`.
- Product PR #1678: `d5b301506912a481196af295b0812f3759d7a27f`; only a review workflow is committed there. Do not call its runtime requirements implemented.
- Local COD review attachment: `mezan_cod_forecast_hardened_20261101.zip`, SHA256 `5001eaa156a758bd8d200b9e1738f6b3816d5eb5791abec0ab074276c6f13a74`. Not in a remote source commit. Preserve the earlier upload restriction; do not retry or bypass the blocked engine upload through another route.

## Owner-supplied live verification evidence

During this continuation the owner supplied the output of the unchanged v5 `verify --url https://mezansalla.com` command. It reports `verified=true`, `checks=3`, source `d0532a057a33776ebe1b40f0d7cbf87a39bf7e4d`, source base `064ba0d0af06a29d947b63cf0c72a8da157264ed`, deployment `3d8720860fb524d729f6c83a8c56c5b4de3a2afb`, release `rg5-e8d8970b2ab29f4358b856dad1facc6e35e464d1091ad800c0e8df13d844a396`.

This is owner-executed production evidence, not an assistant-executed probe. Final release-guard `status` output showing `active=false` has NOT been supplied or independently read. No /app command or lease action is performed by this task. This successful prior publication does not publish the unmerged follow-up/provider candidates, validate their combined behavior or authorize financial corrections.

## Compatibility findings to remediate before combined acceptance

1. PR1685 order_view rejects captured 170/current total 150 after a 20 refund; current production preserves read access. Separate original capture basis, current net order total and executed refund proof. Do not remove validation or regress #1686 observation/compensation/original-platform restrictions.
2. Follow-up collect_sources omits plan_id, while plan-specific policies require it. Propagate only canonical proved plan identity; missing/contradictory evidence must remain explicit.
3. Preserve provider_postsettlement_refund_review_required until actual statement-linked fee/tax-credit and partial/multi-order settlement reconciliation is independently implemented and tested. The statement arithmetic reviewer must remain posting_allowed=false.
4. Hunk-level integration is required for engine, sources, routes, service, returns, operational pages and tests. Never overwrite newer files wholesale or use git reset --hard.
5. Native Salla payment-source normalization is additionally incomplete: see the 2026-11-02 diagnosis below. Fixing aliases alone does not establish captured-money or settlement accuracy.

## Complete owner scope / acceptance inventory

| Scope | Existing work | Pending combined acceptance / activation |
|---|---|---|
| Warnings | Aggregated warnings inherited through production #1676 | Investigate source, shipping, cost, fees, advertising day/FX and salary evidence; show cause/reference/action without substituting zero |
| Products/suppliers | Supplier cutoff fix in follow-up; open-cost feature in production | Strict per-piece unreceived statuses, SAR cost basis, received supplier attribution, no duplicate opening-covered liabilities |
| Shipping/drivers/COD | Cutoff delivery and bank/POS review in follow-up; local-only forecast | Current carrier reassignment, actual fulfiller, no duplicated fee/custody/forecast, actual Mongo and browser acceptance |
| Provider balances | Opt-in component model and read-only statement reviewer in #1685 | Native aliases/payment evidence and plan propagation; post-settlement refunds; approved actual policies; opening gross/net proof and read-only balance differences |
| Customers/external people | Existing operational flows and latest #1686 compensation | Four loan/debt directions, debt creation vs settlement, before/after balances, exchanges, returns, shipping and cross-path deduplication |
| Payroll/opening protection | Cutover-day inclusive payroll and reviewed owner UI in follow-up | Preserve full first day, review opening coverage before activation; no automatic activation or financial correction |
| Unified report | Report source in #1675/#1677 | Complete common-period profit and forecast authority, print/download/Excel/mobile/browser checks |
| Release | Owner supplied successful verification of prior production | Latest-head combined tests/security/build, new A/B intent, inactive lease check, one authorized publication, verify three probes and active=false |

## 2026-11-02: payroll and missing provider balances diagnosis

The owner reported that daily salaries first appeared on November 2 rather than November 1, and that provider balances were missing while orders remained awaiting review. Fresh GitHub reads confirm the same production SHA and integration head 70bff0ad88172e00c7285baa1196ed74359c712c before this documentation checkpoint. No live Mezan database read or mutation was performed.

Read-only Salla connector checks inspected the merchant's actual status identities, one page of new awaiting-review Tamara/Tabby orders, and one complete order response. The first page is a sample, not an exhaustive balance reconciliation. No customer details, order references, transaction identifiers, amounts or contractual rates are included in this repository record or its fixtures.

Observed native shapes use root payment_method values tamara_installment/tabby_installment, a payment_methods array containing amount and transaction_reference, and payment_actions refund_action/remaining_action paid_amount objects. The detailed sample did not supply the nested payment.status/paid_at contract consumed by the operational source adapter, or a proved plan_id. These observations do not establish the exact saved Mezan snapshot, provider settlement eligibility, bank receipt, plan or fee policy. Do not manufacture capture time from order creation/update time or infer a contractual plan from an installment alias.

Pinned production defects reproduced with complete source-adapter/pure-reducer calls and a read-only fake source database:

- PAYMENT_METHODS omits tamara_installment and tabby_installment. It also omits credit_card, present in Salla's documented order response. Such methods lose the payment projection entirely, even with complete synthetic nested capture proof, and do not emit a provider-specific missing-evidence warning.
- Recognized methods with only native payment_actions/payment_methods evidence have no captured fact. Valid fee policy yields an unconfirmed estimate and provider_capture_evidence_missing, not confirmed funds.
- Awaiting-review status itself is not a provider gate: recognized Tamara, Tabby and mada with complete canonical capture proof and a valid policy produce one confirmed obligation without delivery, bank credit or duplicate capture.
- Explicit payment.plan_id is dropped by the current adapter.
- Production payroll uses the Riyadh cutover day plus one. A synthetic November 1 cutoff and SAR 3,000 salary yields no November 1 salary and exactly SAR 100 for November 2. Existing openings and movements remain unchanged.

Diagnosis source: diagnostics/published_payment_payroll_checks.py, checkpoint d5f1b5ac9f7950d3500ba42e808195ed1c027d53. Its 15 assertions passed locally against exact production source 3d872086. Passing them reproduces the current defects; it is NOT correction acceptance. Fixtures are wholly synthetic and all source write attempts are rejected.

Separately reran the existing follow-up payroll suites test_operational_balance_salary_cutover_day.py and test_operational_balance_salary_start.py against df17ef3f: 64 passed, zero failures/errors/skips. These are isolated local unit/mock tests, not new real-Mongo, live activation or combined-source acceptance. The approved cutoff-day fix remains unpublished and requires reviewed opening coverage. Do not add the diagnostic and payroll counts together as a combined validation.

Reproduction uses Python 3.13 and locally hash-checked isolated review wheels, with no production connection:

```bash
PYTHONPATH=<isolated-deps>:<pinned-production>/backend:<pinned-production>/backend/tests \
python -B -m pytest --noconftest -q \
  docs/operations/OP-UNIFIED-BALANCES-20261102/diagnostics/published_payment_payroll_checks.py
```

Official schema references checked: https://docs.salla.dev/5394146e0 (native order fields); https://docs.salla.dev/transactions/details (separate transaction status/reference/date evidence). Documentation does not prove that transactions.read or every provider transaction is available to the merchant's current connection.

## Current actions and boundaries

Created an isolated integration branch from exact latest production. No application code merged yet. A narrowly scoped read-only source-review job may archive pinned tracked sources for local three-way review; it must have contents:read, no deployment environment/secrets, no application execution, no ref writes and no live endpoints. Remove review-only workflow before release source is frozen. Prior workflow or branch test success is not combined-source acceptance.

This continuation changed only scoped documentation and a synthetic diagnostic file on the integration branch. No runtime source, source-collection allowlist, workflow, application dependency, production ref, opening, salary, financial movement, provider policy, /app, lease or deployment changed. It did not retry the blocked COD engine upload. The original branches are preserved.

Next safe action: implement reviewed native payment normalization and authoritative capture/plan mapping, with per-order missing-evidence diagnostics rather than invented dates or zero balances; preserve current compensation behavior and the post-settlement guard. Combine the existing inclusive-cutover payroll fix without silently activating it. Before any financial activation/correction, obtain a read-only saved-state comparison and opening coverage, then seek owner approval for the explicit differences. Continue the full inventory and combined acceptance on the latest production base before a single new v5 release.
