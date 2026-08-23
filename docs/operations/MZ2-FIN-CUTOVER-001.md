# MZ2-FIN-CUTOVER-001 — Mezan 2 clean financial cutover

Status: **implementation in progress**

Owner: AMASI / Mezan owner

Started: 2026-08-23

Production source branch: `hotfix/prod-snap-meta-final`

## Purpose

Replace the untrusted financial position and sales history in legacy Mezan
with a clean, evidence-based accounting start in Mezan 2. Legacy Mezan remains
read-only archive material and must never feed Mezan 2 balances, sales,
profit, tax, settlement, or financial-position reports.

This operation ID is the stable handoff reference for every later session:

> `MZ2-FIN-CUTOVER-001`

## Decisions already approved

1. Do not migrate historical sales, balances, journals, or the legacy
   financial position.
2. Trusted sources only: Salla, real Qoyod invoices, bank statements,
   provider settlement statements, provider tax invoices, courier statements,
   physical inventory count, employee contracts/payments, supplier evidence,
   and advertising-platform facts.
3. Salla, Tamara, Tabby, Emkan, and every shipping company are independent
   financial provider accounts/apps in Mezan 2.
4. A provider tax invoice is evidence attached to the provider account; it is
   not a separate account and does not post a journal merely because it was
   saved.
5. Merchant fee settings are estimates. A verified provider invoice or
   settlement statement is authoritative and overrides the estimate for the
   covered transaction/period.
6. All manual financial activity starts from **New unified financial
   movement**. The selected operation determines the canonical double-entry
   workflow; not every cash movement is a generic transfer.
7. The cutover date and signed opening-balance sheet are not yet approved.
   No production opening balance may be posted before that approval.
8. Each provider has one **current settlement bank**. The accountant changes
   that bank before recording a settlement; the change applies immediately to
   the next unposted settlement and requires no date range. Posted settlements
   keep their original bank.
9. A newly observed payment method is attached to Salla only when the gateway
   or settlement evidence identifies Salla as the settler. Unknown methods are
   held as unclassified with an unapproved fee rule; the system must not guess
   the provider or commission.

## Provider account model

Each provider account owns separate accounting dimensions where applicable:

- receivable from provider;
- payable to provider;
- provider fees;
- VAT on provider fees;
- settlements/transfers;
- refunds and adjustments;
- attached provider tax invoices and statements.

Provider groups:

| Provider | Account behavior | Authoritative evidence |
|---|---|---|
| Salla and its payment methods | One Salla provider account with method-level fee rules | Salla tax invoice / settlement statement |
| Tamara | Independent BNPL receivable and settlement account | Tamara invoice and settlement |
| Tabby | Independent BNPL receivable and settlement account | Tabby invoice and settlement |
| Emkan | Independent BNPL receivable and settlement account | Emkan invoice and settlement |
| Each shipping company | COD receivable plus shipping/fee payable | Courier invoice and COD statement |

### Store-delivery drivers (مندوب المتجر)

- ``مندوب المتجر`` is a presentation group only and never owns an aggregate
  accounting balance.
- Every active store driver keeps the existing ``store_drivers.id`` as the
  accounting identity. Do not duplicate the person in counterparties.
- The delivery fee is configured per driver and snapshotted on assignment, so
  a later price change cannot rewrite a delivered shipment.
- On successful delivery, cash COD is debited to that driver's
  ``cod_receivable`` and the snapshotted delivery fee is credited to that
  driver's ``delivery_fee_payable``. Prepaid/non-cash orders do not create COD
  custody on the driver.
- The driver screen must show, per individual: **عليه لنا (COD)**, **له علينا
  (أجرة التوصيل)**, and the signed net balance. Driver 1 and Driver 2 may never
  be netted together.
- A normal COD remittance and a fee payment are separate movements. Explicit
  net settlement is allowed as one balanced journal that preserves the gross
  COD cleared, fee offset, and bank/cash amount. No silent auto-netting.
- SMSA, Aramex, iMile, and other external couriers remain one account per
  company; they are not children of the store-driver group.
- This bridge posts only new canonical delivered/settlement events. It does
  not scan or backfill old orders or legacy Mezan balances.
- Delivery posting fails closed until tenant setting
  ``mezan2_financial_cutover`` carries this operation ID, ``status=active``,
  and the approved timezone-aware ``cutover_at``. This implementation does not
  activate that setting or invent the timestamp.

### Courier COD commission tiers and netting

- A courier may retain all or part of COD proceeds against shipping charges,
  COD commission, commission VAT, and other evidenced adjustments. Therefore
  a valid courier settlement may have **zero bank transfer**.
- Never record only the net. Keep gross COD receivable, shipping cost,
  commission before VAT, commission input VAT, other adjustments, and bank
  transfer as separate legs of one balanced settlement.
- COD commission tiers are calculated per delivered COD shipment amount, not
  on the aggregate courier balance, unless the signed provider contract says
  otherwise.
- Each tier stores: lower bound/operator, upper bound/operator, percentage,
  fixed fee, and VAT percentage. Shared boundaries must be unambiguous.
- Any COD amount not covered by a configured tier is flagged for review and is
  never silently assigned a zero commission.
- Merchant-stated SMSA example awaiting invoice/contract confirmation:

| Per-shipment COD amount | Commission before VAT | Boundary behavior |
|---|---:|---|
| SAR 50 to SAR 1,000 | 1% + SAR 2 | includes 50 and 1,000 |
| Above SAR 1,000 to SAR 3,000 | 2% + SAR 5 | excludes 1,000; includes 3,000 |
| Above SAR 3,000 | 3% | fixed component not stated; confirm from evidence |

Commission VAT is calculated separately on the commission result for the
matched tier. The official courier invoice/statement remains authoritative.

## Movement routing contract

| Real event | Canonical operation | Accounting meaning |
|---|---|---|
| Bank/cash account A to bank/cash account B | Internal account transfer | Debit destination, credit source; no revenue or expense |
| Salla pays the bank | Salla provider settlement | Debit bank, credit Salla receivable; fees/VAT use statement facts |
| Tamara/Tabby/Emkan pays the bank | BNPL provider settlement | Debit bank, debit fees/VAT/adjustments, credit provider receivable |
| Courier remits COD and/or withholds shipping/fees | Courier COD settlement | Debit bank (possibly zero), shipping/fee legs and fee input VAT; credit courier COD receivable |
| Merchant pays courier invoice | Courier payment | Debit courier payable, credit bank/cash |
| Bank charges a fee | General expense / bank fees | Debit bank-fee expense, credit bank |
| Salary is earned after cutover | Salary accrual | Debit salary expense, credit salary payable |
| Salary is paid | Salary settlement | Debit salary payable, credit bank/cash |

Direct bank/cash transfers into Tamara or Tabby accounts are prohibited
because they bypass the canonical BNPL settlement bridge. COD and generic
bank-transfer labels are also not transferable wallets.

## Opening-balance rules

Opening balances are a separate, signed cutover operation and are never
derived from legacy Mezan.

### Employees

- Opening salary liability = earned and unpaid salary **as of the cutover
  instant**, per employee.
- Do not carry historical gross salary or amounts already paid.
- Open employee advances and custody balances are separate opening assets and
  require employee-level evidence and confirmation.
- After cutover, salary expense/payable accrues through normal journals and
  payment clears the payable.

### Orders at cutover

- Unpaid/unprepared order or undelivered COD: no journal.
- Prepaid but unfulfilled order: customer advance and VAT treatment according
  to the verified tax point.
- Delivered but unsettled order: opening receivable from the payment provider
  or shipping company.
- Order created before cutover but delivered after cutover: normal journal at
  the post-cutover recognition event.
- Existing real Qoyod invoice before cutover: link/reclassify only; do not
  duplicate the invoice.

### Other balances

- Bank: statement closing balance at the cutover timestamp.
- Providers: official unsettled statement at cutover.
- Couriers: signed COD receivable and shipping payable reconciliation.
- Inventory: approved physical count multiplied by approved unit cost.
- Suppliers, annual obligations, rent, and other liabilities: only open,
  evidenced amounts as of cutover.

## VAT control

The Saudi standard VAT rate is 15%. Tax timing must be checked against the
actual supply/invoice/payment event; do not infer VAT solely from order status.
Implementation references:

- https://zatca.gov.sa/ar/RulesRegulations/Taxes/Pages/VATImplementingRegulations.aspx
- https://zatca.gov.sa/ar/RulesRegulations/VAT/Pages/default.aspx

## Salla payment-fee evidence — 2026-08-23

Seven Salla payment-detail invoices were analysed: `6585798`, `6610174`,
`6613353`, `6616833`, `6620818`, `6640541`, and `6643529`.  They contain 528
transaction rows: 516 positive sales and 12 negative refund rows.  Aggregate
evidence totals are SAR 107,099.16 gross, SAR 2,014.93 fees before VAT, SAR
302.23 fee VAT, and SAR 104,782.00 net after fee VAT.

Observed positive rows reproduce exactly with these per-order rules:

| Salla invoice label | Positive rows | Commission before VAT | Evidence status |
|---|---:|---:|---|
| مدى | 359 | 1.00% of order + SAR 1.00 | Verified 359/359 |
| البطاقة الائتمانية | 149 | 2.20% of order + SAR 1.00 | Verified 149/149 |
| أس تي سي باي | 8 | 1.30% of order + SAR 1.00 | Verified 8/8 |

Salla first calculates the unrounded per-order fee. The displayed fee is that
value rounded to halalas, while fee VAT is 15% of the **unrounded** fee and is
then rounded independently. This matters on half-halala boundaries; VAT must
not be calculated from the already rounded displayed fee. The 12 negative
refund rows in this evidence set carry zero new fee and zero fee VAT.

Apple Pay, Google Pay, Visa, MasterCard, and generic bank card did not appear
as separate invoice labels. They use the observed generic credit-card rule
(2.20% + SAR 1.00, VAT 15%) only as an editable fallback estimate. It must not
be described as a directly verified rail-specific rate. A later invoice row
with one of those explicit labels supersedes the fallback.

Implementation uses settings version `salla-invoices-2026-08-v1`. Existing
rows that still equal the old bundled defaults are upgraded safely; explicitly
merchant-edited rows are preserved. Missing Google Pay is added as a Salla
sub-method. Fee estimates use per-order rounding when individual orders are
available and retain aggregate calculation only for legacy aggregate-only
callers.

## Implemented in this change

- Added tenant-scoped endpoint `GET /api/financial-provider-apps`.
- Added provider cards for Salla, Tamara, Tabby, Emkan, and configured shipping
  companies.
- Added tax-invoice evidence endpoints under
  `/api/financial-provider-apps/{provider_id}/tax-invoices`.
- Enforced invoice arithmetic and verified-document evidence references.
- Rejected migration-shaped invoice payloads, including `opening_balance`.
- Marked all provider cards `legacy_financial_data_included=false`.
- Kept provider invoices `unposted_evidence`; no automatic ledger write.
- Routed each provider to its existing canonical settlement flow.
- Added Mezan 2 workspace **Accounts for shipping and payment** under Apps.
- Added deep-link support to the unified movement screen for courier COD
  settlement.
- Added a visible transfer-vs-settlement rule to the unified movement screen.
- Added per-shipment courier COD commission tiers with explicit inclusive/
  exclusive boundaries, percentage, fixed fee, and VAT rate.
- Added uncovered-tier review protection and tier-aware courier ledgers.
- Split courier COD commission from recoverable input VAT in the settlement
  journal and financial position.
- Kept courier settlement valid when the bank leg is zero because the courier
  retained the whole COD amount against shipping/fees.
- Added backend/frontend regression tests for provider grouping, invoice
  guards, routing, navigation, and absence of legacy balances.
- Connected successful store-driver delivery to the general ledger per
  individual driver: cash COD receivable plus snapshotted delivery-fee payable.
- Connected driver COD remittance, fee payment, and explicit net settlement to
  balanced bank/driver journals with idempotency metadata.
- Added separate store-driver COD assets and delivery-fee liabilities to the
  financial position, plus a per-driver debit/credit settlement view.
- Reconstructed Salla payment fees from seven merchant invoices and replaced
  the old STC Pay/card estimates with the verified per-order rules above.
- Added Google Pay as a Salla sub-method and made Apple Pay/Google Pay/card
  rails inherit the documented generic-card fallback until direct evidence is
  available.
- Added versioned default migration that upgrades untouched legacy defaults,
  appends missing methods, and preserves merchant-edited fee rows.
- Changed fee estimation to Salla's per-order fee/VAT rounding semantics and
  prevented one known card rail from silently taking another rail's rule.
- Routed the central gateway metrics, Salla reconciliation rollup, and Salla
  settlement classifier through the expanded rail set, with estimates loaded
  from the unified payment-method settings instead of their old hardcoded
  Mada/card/STC rates.

Primary files:

- `backend/financial_provider_apps.py`
- `frontend/src/pages/FinancialProviderAppsWorkspace.jsx`
- `frontend/src/services/financialProviderApps.js`
- `frontend/src/pages/UnifiedEntryScreen.jsx`
- `frontend/src/lib/integrationWorkspaces.js`
- `frontend/src/components/MezanV2NavigationShell.jsx`
- `backend/tests/test_financial_provider_apps_v2.py`
- `backend/store_delivery_accounting.py`
- `backend/store_delivery_driver_app_routes.py`
- `backend/store_delivery_settlement_routes.py`
- `backend/tests/test_store_delivery_accounting.py`
- `frontend/src/pages/StoreDeliverySettlements.jsx`
- `frontend/src/services/financialProviderApps.test.js`
- `backend/payment_methods.py`
- `backend/excel_parser.py`
- `backend/payment_gateway_metrics.py`
- `backend/reconciliation_routes.py`
- `backend/tests/test_salla_payment_fee_defaults.py`

## Remaining work, in order

### Gate 1 — approve cutover evidence

1. Select the exact Riyadh cutover timestamp.
2. Export and archive trusted bank/provider/courier/Qoyod/inventory/payroll
   evidence at that timestamp.
3. Prepare an opening-balance worksheet with source reference per row.
4. Reconcile debit total to credit total and obtain owner approval.

### Gate 2 — post opening balances

1. Add a dedicated preview/approve/post workflow with idempotency key tied to
   this operation ID.
2. Post bank, provider, courier, inventory, supplier, employee, tax, and equity
   opening entries as one controlled cutover batch.
3. Block legacy Mezan collections from all Mezan 2 financial readers.
4. Run trial-balance and financial-position reconciliation.

### Gate 3 — event journals after cutover

1. Journal trusted Salla order/payment/fulfilment/refund events once each.
2. Complete Salla settlement posting from official files.
3. Keep Tamara/Tabby/Emkan on their idempotent settlement bridge; add Emkan
   connector coverage where official data is available.
4. Journal courier COD receivable, shipping payable, fees, VAT, remittances,
   and payments from the canonical shipping workflow.
5. Complete payroll accrual schedule and payment clearing after cutover.

### Gate 4 — production acceptance

1. Trial balance is balanced.
2. Provider subledgers reconcile to official statements.
3. Bank balances reconcile to statements.
4. Employee salary payable, advances, and custody reconcile per employee.
5. Inventory reconciles to approved count/cost.
6. No legacy sales or financial-position numbers appear in Mezan 2.
7. Financial position contains assets, liabilities, VAT, equity, retained
   result/current result, and the result is reproducible from journals.

## Safety constraints for future sessions

- Do not post production financial data from this document alone.
- Do not guess a cutover date or an opening balance.
- Do not convert every provider movement into a generic transfer.
- Do not let saving a tax invoice double-post a settlement.
- Do not delete or rewrite posted journals; use reversal/adjustment workflows.
- Follow `AGENTS.md` and the production release guard before deployment.

## Handoff prompt for the next conversation

Use this exact message:

> Continue GitHub operation `MZ2-FIN-CUTOVER-001`. Read
> `docs/operations/MZ2-FIN-CUTOVER-001.md`, inspect the latest merged commit
> and tests, report the current gate, then continue only the next incomplete
> item. Do not import legacy Mezan balances or sales and do not post production
> opening entries without the approved cutover evidence sheet.
