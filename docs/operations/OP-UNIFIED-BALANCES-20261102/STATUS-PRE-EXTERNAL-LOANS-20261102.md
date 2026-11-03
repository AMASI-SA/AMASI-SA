# OP-UNIFIED-BALANCES-20261102 — native purchase journal evidence

**DRAFT / NOT published / NO live financial correction or activation.**

## Recoverable source and owner clarification

Repository AMASI-SA/AMASI-SA; branch integration/operational-balances-unified-20261102; Draft PR1687. This checkpoint descends normally from verified head **9c04572f38487937338ddcd573b0df6dfac1e9fa**, root9b1259511185d33719192b749c2ccbccbe14ed0d. Read the current PR head and exact CI before continuing.

- Exact locally tested new Backend tree: **b0ee4e2615dfabd3450b54f62197711263361e03**. Base Backend7dadd09d09caf9f1bde0c39cdcbe187bf82a2064 was independently reconstructed and matched before edits. Frontend2ed74cb8d188606549a3c43ff23a10a835131d06 is unchanged.
- Published dashboard production **f8235d57e90f8a1271df6350d7e7919f5621ea11** and all prior supplier/payroll/provider/remaining-product work remain preserved. No original branch, dashboard implementation, shared ledger writer, release intent or production source was replaced.
- Prior canonical STATUS is preserved byte-for-byte as STATUS-PRE-NATIVE-PROOF-20261102.md, original blobd094bef8cd00c5c0d2956f476522c7ba357eb523. Earlier PRE/COMBINED/PAYMENT/SUPPLIER records and their evidence remain.
- Owner clarification: opening means the saved balance at operational startup, NOT a new opening to enter. A newly approved supplier invoice after saved startup is not excluded merely because its customer order is old, or because an opening might hypothetically contain it. No overlap was established in live merchant data. Preserve the saved opening and existing payments; show any actual duplicate evidence separately. This turn does not change invoice eligibility or existing saved opening amounts.

## New read-only implementation

The existing GET supplier-invoice-review now includes a nested native_ledger_review for each native purchase invoice. This proves source-journal evidence only; it is NOT an operational payable importer or an opening reconciliation.

1. Uses the public sealed accounting_ledger_v2.read_reporting_entries_v2 reader and its whole-group/audit verification in the SAME Mongo snapshot as saved state/invoices/liabilities. No raw/private ledger access or production writer call.
2. Validates invoice owner/schema/approval/SAR/supplier identities, line/subtotal/gross equations, tax treatment and deductible-tax evidence, saved liability source and group links, and exact inventory/payable/input-VAT legs and dates. The saved operational start classifies approval period; customer-order creation never supplies eligibility.
3. Supplier settlements require the matching supplier-debit/bank-credit pair, exact invoice/liability/supplier/operation references, date and amount. Partial/multiple/full payment sums must equal the saved liability projection. Same supplier/amount or an unrelated invoice is not identity proof.
4. Reversals, duplicate operations/legs, missing whole groups/audits, owner/account/reference mismatches, incomplete payment or tax evidence and reader limits remain explicit failures with unknown monetary fields, never a confirmed zero.
5. The successful source proof exposes native_invoice_gross_sar, native_settled_sar, native_outstanding_sar, bank/journal references and an evidence digest. **operational_balance_matched=false, opening_coverage_verified=false, difference_sar=null and posting_allowed=false remain unconditional.** The outer verified_native_payable_sar stays null and native_financial_matching_complete stays false when native candidates exist. Source-ledger proof must not be summed into operational balances a second time.
6. Native ledger proof is included in the preview manifest so changes to bank/evidence alter the review hash. A native verification failure does not erase an independently valid preparation-invoice comparison. Existing route actor/permission/late-revocation restrictions are unchanged.
7. Ledger entries are loaded once per preview via the existing bounded public API, then indexed by group/invoice/liability rather than scanned once per component. The public API still performs its own per-group checks and enforces its existing bound; no live performance or unlimited coverage is claimed.

Only three application/test files changed:
- backend/operational_native_purchase_review.py — blobffc0c16032cb0139cad605e4ec332a030b61a289
- backend/operational_supplier_invoice_preview.py — blob8e12daa235426a1589eba06ee32739de6c628940 (8 additions/1 deletion)
- backend/tests/test_operational_balance_native_purchase_review.py — bloba84eea4964c7003277da23790a9e0ef093104c90

## Exact local verification and limitations

Python3.13.5, isolated Mongo8.0.12 replica bound to127.0.0.1:27305. UUID test databases only; no merchant credentials, customer rows or production connection. Test-only native journals were created through the existing public writer to test genuine sealed group/audit proof; the review itself is read-only. Fixtures restored their test mode before operational preview. All test databases were dropped and this task's own Mongo fixture was stopped after testing.

Focused command: PYTHONPATH=<isolated-test-deps>:backend:backend/tests python -B -m pytest --noconftest -q backend/tests/test_operational_balance_native_purchase_review.py backend/tests/test_operational_balance_supplier_invoice_preview.py --tb=short --junitxml=native-focused-final.xml. **109 passed,0 failure/error/skip**, including72 new and37 existing cases;19.43seconds.

Combined command retains the complete existing operational selection: test_operational_balance*.py, test_operational_open_cost_exposure.py, test_operational_bundle_cost_projection.py, test_operational_supplier_invoice_cutover.py, test_store_delivery_noncash_review.py, test_operational_driver_review_mongo.py, --noconftest -q --tb=short --junitxml=combined-native-review.xml. **1608 passed,0 failures/errors/skips,exit0,263.27seconds.** JUnit independently parsed1608 testcase elements and72 in the new module. The72 new cases include10 real-Mongo cases; all are subsets, NOT added to1608. Existing five Pydantic deprecation warnings remain.

Combined JUnit SHA256 **faf4193bfa71e86d1321ce18206bf895569ae4c8167b5bd77e1cb0434d48844b**; log SHA256 **e6443c547bcb042ca5bf38ddf2e947face8ae7fcda4212944b8e7d9b30739fc4**. Initial test-fixture attempts failed due to noncanonical zero-opening timestamps and amount precision; fixtures were corrected, no financial guard weakened. Original logs retained in the review archive. Synthetic purchase80 and bank-backed payment25 yields source outstanding55, not a new operational55 or80 debt.

Static ledger-access review: the new/modified production files have zero findings. The repository-wide scripts/check_accounting_ledger_v2_access.py currently fails on five pre-existing private-constant imports in unchanged accounting_advertising_setup.py, store_delivery_cash_evidence.py and accounting_driver_review_history.py. Their bytes match the base. This failure is retained, not labelled a security pass or bypassed; assess it with the applicable full release gates.

Fresh exact-head GitHub operational/frontend/security/build acceptance must be read after this checkpoint. Earlier1536/300 on9c and readiness41506311209 are historical: the former succeeded, the latter FAILED at sourceA/intentB classification before final build. This new proof does not resolve that release gate, and no old dashboard intent may be reused. No frontend visual/print/download acceptance is claimed.

## Full scope and remaining gates

| Scope | Implemented / preserved | Remaining before completion or activation |
|---|---|---|
| Supplier invoices | Saved cutoff independent of old order creation, final-component estimate replacement, unchanged payments, consistent-snapshot preparation comparison; native journal/payment source proof now added | Match proven native debt and payments to existing operational source identities and opening coverage without duplicating them. Read actual owner data, explain each difference, obtain explicit approval before omitted financial recognition. No new opening or blanket hypothetical-duplicate block. |
| Remaining ordinary/bundle products | Exact awaiting-review/in-progress states, original SAR, frozen selected/unconditional/nested components, partial receipt source/reducer/report wiring and scoped completeness retained | Native invoice-to-operational matching and supported pre-invoice native receiving; live read-only comparison/performance. Keep bundle_received_purchase_financial_matching_required; stock/assignment is not new debt. |
| Payroll | Inclusive saved-cutoff day, preview/approval and idempotence retained | Saved opening/missing-day review and approval; no day-one posting performed. |
| Payment providers | Optional fee components, native aliases/plans and capture/refund protections retained | Actual dated capture/plan/void/refund source, approved merchant policies and gross/net openings; postsettlement actual fee/tax credits, partial/multiorder allocation and bank reconciliation. Keep provider_postsettlement_refund_review_required and statement posting_allowed=false. |
| Shipping/drivers/COD | Postcutoff delivery on old orders, executor evidence, fee/cash/POS/bank separation retained | Current-carrier/full coverage and actual settlement matching, expected COD. Uncollected COD is not cash. Restricted old forecast archive remains unaccessed. |
| Customers/external persons/loans | Published compensation and original-platform rules retained | Four loan/repayment directions, before/after balances and receive-in/pay-from labels; exchange/return/shipping cross-path dedup acceptance. |
| Warnings/financial report | Existing aggregation, scoped product/shipping completeness and tables retained | Resolve actual source/payment/advertising start/FX/salary evidence without hiding gaps; common-period complete expected position/profit; browser/mobile/actual print/download tests. |
| One operational release | All earlier source preserved, no independent deployment | Close business and financial-activation gates, current exact combined CI/security/UI/build, new finalA/build/intent-onlyB, actual inactive lease before/app, one publication and three identity probes then active=false. |

## Safety and continuation

Production changed by this task:NO. No live opening/balance correction, financial replay, invoice/refund/stock/bank/provider operation, payroll/policy activation, /app, lease, prepare/prepublish or publication. All approved source facts remain unchanged. Issue1006 is capped; use this canonical status and PR1687 evidence comments.

Next: verify this exact remote Backend tree and fresh CI. Complete the native source-to-operational matching with existing identity/payment evidence, then actual read-only merchant differences and the provider/customer/shipping/report gates. Do not equate source proof with operational correction approval. Before later deployment work, read actual production_release_guard.py status and preserve every newer production change.
