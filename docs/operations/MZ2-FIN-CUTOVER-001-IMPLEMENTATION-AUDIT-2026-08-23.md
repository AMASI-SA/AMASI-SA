# MZ2-FIN-CUTOVER-001 — Runtime implementation audit

Date: 2026-08-23  
Repository: `AMASI-SA/AMASI-SA`  
Production source branch inspected: `hotfix/prod-snap-meta-final`  
Baseline: PR `#845`, merge `1e11ef7769d4fe00cbb1895d7a8925527498e04f`

## Scope and evidence rule

This audit compares the approved accounting architecture in
`docs/operations/MZ2-FIN-CUTOVER-001.md` with runtime routes and components at
the baseline commit. The eight PNG files under
`docs/assets/mz2-accounting-ui/` are present and remain design references. The
written workflow, permissions, cutover controls, and accounting rules are the
authoritative implementation contract.

No legacy Mezan financial balance, sale, stored `current_balance`, or financial
position value is accepted as cutover evidence by the new accounting module.
No Production opening journal is created by this change.

## Baseline findings: implemented versus design-only

| Approved page | Baseline runtime finding | Status after this change |
|---|---|---|
| الرئيسية المحاسبية | No dedicated approved page. Financial summaries existed in unrelated legacy pages and could use fallback balances. | Implemented as a fail-closed command center. Balances are hidden until one timezone-aware cutover, signed evidence, complete evidence sections, balanced preview, explicit approval, and a verified posted opening group all exist. Visible amounts are then derived only from `general_ledger` rows scoped to `MZ2-FIN-CUTOVER-001`. |
| التسويات | Salla, BNPL, provider evidence, and settlement-history capabilities existed across several routes. The approved three-step page did not exist. | Collected under the accounting route and truthfully marked partial. Existing workflows remain available while the approved upload → reconcile → post page is the next implementation target. |
| الشحن والتحصيل | External courier ledger, COD settlement, store-driver receivables/payables, and commission-tier rules existed in separate pages. | Collected under the accounting route and marked partial. The approved three-tab unified page is not yet complete. |
| المخزون والمشتريات | Supplier, purchase-invoice, and inventory-receiving capabilities existed separately. | Collected under the accounting route and marked partial. Weighted-average purchase posting and the single approved form remain incomplete. |
| الحركات المالية | A unified-entry screen and movement records existed outside the approved module. | Collected under the accounting route and marked partial. Provider settlements remain routed to their canonical settlement flow. |
| الرواتب والالتزامات | Recurring obligations and employee ledger/payroll capabilities existed separately. | Collected under the accounting route and marked partial. The approved payroll/obligations page is not yet complete. |
| الأرصدة الافتتاحية | Legacy provider cutoff settings existed, but not the approved one-time cutover wizard. | Explicitly blocked. There are no input fields and no posting action. The page only exposes missing cutover/evidence gates. |
| القيود والتقارير | Journal, ledger financial position, and reconciliation pages existed separately. | Collected under the accounting route and marked partial. The approved tabs, exceptional manual-journal flow, and reversal controls remain incomplete. |

## Unified navigation

Mezan 2 now has one top-level section named `المحاسبة`. It owns exactly these
pages, in order:

1. الرئيسية المحاسبية
2. التسويات
3. الشحن والتحصيل
4. المخزون والمشتريات
5. الحركات المالية
6. الرواتب والالتزامات
7. الأرصدة الافتتاحية
8. القيود والتقارير

The former financial-provider link is removed from `التطبيقات` so accounting is
not presented in two unrelated sections.

## Permission contract

Accounting permissions are stored independently in
`users.accounting_permissions`.

- Owner: implicit access to all accounting pages and actions.
- Every other role, including legacy `admin` and `accountant`: no accounting
  access by default.
- Each page has one independent `*.view` permission.
- Draft creation, settlement posting, rule changes, purchase posting, payroll
  posting, opening approval, manual journals, and reversal are independent
  sensitive permissions.
- The owner assigns these permissions from the accounting module. Legacy team
  role defaults and `extra_permissions` do not grant them.
- Navigation shows only pages explicitly assigned to the signed-in employee;
  backend endpoints also enforce the independent assignment.
- Team users are scoped to the merchant owner only through the existing
  `created_by` relationship. An unlinked user fails closed, and the owner-only
  assignment endpoints cannot read or change users outside that team scope.
- The home/readiness endpoint requires the exact page permission (`home` or
  `opening-balances`); possession of another accounting page does not expose
  the financial summary.

## Page 1 balance gate

The main cards do not display zero as a substitute for unknown and do not use
legacy fallbacks. Amounts remain unavailable until all of the following are
verified:

- `operation_id == MZ2-FIN-CUTOVER-001`
- exact timezone-aware `cutover_at`
- signed evidence-sheet reference
- evidence for banks/cash, providers, couriers/COD, inventory, suppliers,
  payroll/obligations, and equity at the same cutover
- balanced opening preview
- explicit approval actor and timestamp
- posted opening `txn_group_id` whose `opening_balance` legs carry this
  operation id and balance debit to credit
- cutover setting status is `active`

After that gate, the cards read only posted, non-reversal, non-orphan
`general_ledger` entries carrying this operation id and created at or after the
approved cutover. Existing legacy account balances are never merged into the
new display.

## Current cutover gate

At this change, cutover time and opening evidence are not approved and no
opening group has been posted. Therefore:

- opening-balance entry remains blocked;
- Page 1 monetary cards remain hidden;
- no Production financial write is performed;
- no deployment claim is made by this source change.

## Next incomplete approved item

Implement Page 2 `التسويات` as the approved three-step workflow, beginning with
actual bank bindings for Salla, Tamara, Tabby, Emkan, and supported couriers.
The official statement remains authoritative for gross, commission, VAT,
adjustments/refunds, and net transfer. Posting must require the independent
`accounting.settlements.post` permission and produce one idempotent auditable
journal group.

## Verification performed for this source change

- Python compilation passed for the new accounting modules.
- Accounting contract/readiness/ledger/router unit suite: `10 passed`.
- All changed JavaScript/JSX files parsed successfully through the TypeScript
  JSX transpiler.
- Frontend contract assertions passed for the exact eight-page order,
  deny-by-default employee access, accounting navigation filtering, removal of
  the duplicate financial-app link, and the restricted outer-route bypass.

These are source-level checks only. They are not a Production deployment or a
`production_release_guard.py verify` result.
