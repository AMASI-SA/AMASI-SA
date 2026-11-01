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

## Complete owner scope / acceptance inventory

| Scope | Existing work | Pending combined acceptance / activation |
|---|---|---|
| Warnings | Aggregated warnings inherited through production #1676 | Investigate source, shipping, cost, fees, advertising day/FX and salary evidence; show cause/reference/action without substituting zero |
| Products/suppliers | Supplier cutoff fix in follow-up; open-cost feature in production | Strict per-piece unreceived statuses, SAR cost basis, received supplier attribution, no duplicate opening-covered liabilities |
| Shipping/drivers/COD | Cutoff delivery and bank/POS review in follow-up; local-only forecast | Current carrier reassignment, actual fulfiller, no duplicated fee/custody/forecast, actual Mongo and browser acceptance |
| Provider balances | Opt-in component model and read-only statement reviewer in #1685 | Post-settlement refunds, original-basis/plan mapping, approved actual policies, opening gross/net proof and read-only balance differences |
| Customers/external people | Existing operational flows and latest #1686 compensation | Four loan/debt directions, debt creation vs settlement, before/after balances, exchanges, returns, shipping and cross-path deduplication |
| Payroll/opening protection | Cutover-day inclusive payroll and reviewed owner UI in follow-up | Preserve full first day, review opening coverage before activation; no automatic activation or financial correction |
| Unified report | Report source in #1675/#1677 | Complete common-period profit and forecast authority, print/download/Excel/mobile/browser checks |
| Release | Owner supplied successful verification of prior production | Latest-head combined tests/security/build, new A/B intent, inactive lease check, one authorized publication, verify three probes and active=false |

## Current actions and boundaries

Created an isolated integration branch from exact latest production. No application code merged yet. A narrowly scoped read-only source-review job may archive pinned tracked sources for local three-way review; it must have contents:read, no deployment environment/secrets, no application execution, no ref writes and no live endpoints. Remove review-only workflow before release source is frozen. Prior workflow or branch test success is not combined-source acceptance.

Next safe action: materialize and hash-check the pinned source inputs, reproduce both compatibility failures on the full reviewed routines, implement minimal remediation without affecting live financial state, then update this inventory with exact test evidence and remaining gates. Keep drafts and production unchanged until all required gates are satisfied.
