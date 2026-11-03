# OP-UNIFIED-BALANCES-20261102 — consistent supplier invoice preview

**DRAFT / NOT published / NO live financial correction or activation.**

## Current recoverable source and preserved production

Repository AMASI-SA/AMASI-SA; branch integration/operational-balances-unified-20261102; Draft PR1687.

- Application checkpoint **446404698c14b260900550bac3f63e5dbdcaf5b2**, root tree **7ed664afabc89602562f7a65c9762fff15f7a681**. It follows staging6879ead6dff8733f5b81c06483496a8868a411f7 and previous verified head6e7aca914d2cc230b4a95146fb48f46f15c59b3b by normal fast-forward only.
- Full Backend tree **7dadd09d09caf9f1bde0c39cdcbe187bf82a2064** independently read back from GitHub equals the entire locally tested Backend, not merely selected file names. Frontend tree remains **2ed74cb8d188606549a3c43ff23a10a835131d06**. This following checkpoint removes the temporary source-transfer workflow and changes documentation only; it does not change either tested application tree.
- Latest incorporated production remains **f8235d57e90f8a1271df6350d7e7919f5621ea11**, published dashboard1690. Original supplier/payroll/provider work, customer compensation1686 and driver status1688 remain preserved. No dashboard, release-intent or production branch replacement.
- The previous canonical status is archived byte-for-byte as STATUS-PRE-SUPPLIER-PREVIEW-20261102.md, blob **bb7996ab33c3e5dfab4d6038a65dbc55b029f554**. Earlier source/test identities and scope remain in the PRE-REMAINING/PRE-BUNDLE and COMBINED/PAYMENT/SUPPLIER documents. Issue1006 is full; use this record and the latest PR1687 evidence comment.

## New implementation: saved-versus-proposed supplier invoices, read only

GET `/api/operational-balances/supplier-invoice-review` now connects the existing pure comparator to real source readers and the SAVED operational state in one Mongo snapshot transaction. It never calls refresh, mutate, a journal writer, provider API, stock operation or financial approval. A snapshot failure returns an explicit error rather than a non-snapshot fallback. No new UI screen was added.

For eligible preparation invoices, the result shows invoice gross, current confirmed/settled/outstanding, proposed values, exact difference, component coverage, source reason and required action. It uses the saved operational startup instant, not a new date or old customer-order creation cutoff. Saved openings, payments, returns and unrelated salary/provider facts are retained unchanged. Missing or conflicting supplier evidence blocks arithmetic completeness; unrelated shipping warnings remain visible without being relabelled as missing product cost. Opening coverage and financial correction approval remain false.

Native purchase invoices are a SEPARATE UNVERIFIED inventory. Their declared gross, supplier, document, liability and journal references are returned, with possible duplicate references to preparation, operational inventory or exchange purchases. Exact supplier plus supplier-document reference is only a collision warning, not proof of matching. Never match by amount/name/SKU or substitute a generated internal sequence for a supplier reference. `verified_native_payable_sar` and the native proposed difference remain null. No native invoice is added again to the preparation subtotal.

This advances the read-only reconciliation prerequisite, **not the native posting/payment/opening financial bridge**. `bundle_received_purchase_financial_matching_required` is preserved. Real owner data has not yet been queried by this new endpoint, and no current owner balance is certified.

Actor isolation requires current authenticated operational reports access and `suppliers.read`. Owner scope cannot be overridden by query strings. Permissions, owner association and native-accounting activation are rechecked before response, including mid-request revocation. GET has no correction/approval counterpart.

## Verified local application and remote boundary

The exact application changes are only:
- backend/operational_supplier_invoice_preview.py — blob25234f3669fa16e807e0876810df0f4c85648f05
- backend/tests/test_operational_balance_supplier_invoice_preview.py — blobae90201cfebd9b984872d13e062e9eb6bc630cde
- backend/operational_balance_routes.py — blob28e96faf069107b99d1dba92899c702bd22287a0 (22 additions/1 deletion)

Local Python3.13.5 combined operational selection, including existing balance/open-cost/bundle/supplier/delivery/Mongo suites: **1536 passed,0 failures/errors/skips,156.72seconds,exit0**. JUnit independently parsed1536 testcase elements. The37 new invoice-preview cases are INCLUDED, not extra. Focused37 also passed in7.02seconds. Five existing Pydantic deprecation warnings remain. Actual isolated Mongo8.0.12 replica used127.0.0.1 only with UUID synthetic databases; no production credentials or customer data. Tests prove snapshot consistency under concurrent invoice edits, unchanged persisted collections, exact retry, missing/conflicting evidence, original financial facts and HTTP/permission/late-revocation boundaries.

Synthetic example: invoice80, saved40, prior payment25 -> current outstanding15, proposed55, **difference40**, not another80. Existing overpayment/return cases are also preserved. This is not a live merchant reconciliation result.

Combined JUnit SHA256 **1cb1e31e8d970dd95853831c5c26ec054aaf9d8e6dc3e824852565a36a392d05**; log **6a4e42929ae59e7b8f04a9895bcb98843d2e9200877806ffd291237878b0b4dc**. Focused JUnit **fe42daed11d509b7d0f9c230d063eea4cea767949f1e7392270b32b8ef29053f**. New tests match the existing CI `test_operational_balance*.py` selection. Fresh exact-head GitHub results must be read after this checkpoint; do not call earlier1499/300 results new acceptance. No local frontend/browser/print/export acceptance is claimed.

The source-only route transfer was hash-locked to before/after blobs and an exact plaintext patch, limited to this task branch and normal non-force push. The patch was removed by44640469; this following checkpoint removes its temporary workflow. The entire remote Backend matches local7dadd09d. No repository app or live environment was executed by that transfer.

## Full scope and remaining activation gates

| Scope | Preserved / implemented | Still required |
|---|---|---|
| Remaining ordinary/bundle products | SAR/exact awaiting-review and in-progress states, frozen selections, per-component receipt coverage, pre/postcutoff source/reducer/report wiring and scoped warnings retained | Actual owner read-only data comparison, native invoice/payment/opening matching and unsupported pre-invoice native receiving; no new debt from stock or assignment. |
| Supplier invoices | Old-order/new-approval cutoff, final-price estimate replacement, existing payments, pure comparison and now consistent-snapshot read endpoint | UI integration if needed; real source/opening differences and explicit owner approval before recognizing omitted amounts. Native purchase document inventory is NOT complete payable matching. |
| Payroll | Saved-cutoff day inclusive and owner preview/approval/idempotence retained | Opening coverage and missing-day comparison/approval; no day-one posting. |
| Providers | Opt-in fee components, method aliases, explicit plan and capture/refund compatibility retained | Actual dated capture/plan/void/refund sources, approved merchant terms and gross/net openings, postsettlement fee/tax credits, partial/multiorder allocation and bank matching. Keep provider_postsettlement_refund_review_required and statement posting_allowed=false. |
| Shipping/drivers/COD | Postcutoff actual delivery on old orders, executor proof and cash/POS/bank/fee separation retained | Current-carrier/full coverage, actual settlement matching, expected COD. Uncollected COD is not cash. Restricted old forecast archive remains unaccessed. |
| Customers/external persons/loans | Published compensation/original-platform restrictions retained | Four lending/borrowing/repayment directions and before/after balances; exchange/return/shipping cross-path dedup acceptance. |
| Warnings and report | Aggregation and truthful scoped product/shipping completeness retained | Real Salla/payment/advertising-start/FX/salary evidence, same-period complete expected position/profit, browser/mobile/actual print/download tests. Do not hide gaps or replace them withzero. |
| One operational release | All other task work preserved; published dashboard is not republished by this task | All business gates, approved financial activation boundary, fresh exact combined CI/security/UI/build, new final sourceA/build/intent-onlyB, inactive lease before/app, one publish, three live probes and active=false. |

Previous readiness41500260610 failed A/B source classification and skipped final build/rehearsal; it is not waived or fixed by this preview. The previous exact6e7 operational CI1499/300 succeeded but belongs to its source. Read latest current-head runs and evidence.

## Safety and next safe action

Production changed by this task:NO. No database/opening reset, financial replay, native invoice, stock, refund, bank/provider operation, policy/payroll activation, /app, lease, release-intent or publication. No actual supplier amount was added. Source previews cannot approve their own differences.

Next: read current PR/source and fresh combined CI. Complete native invoice-to-liability/journal/payment/opening read-only matching without assuming document existence equals debt, then obtain actual owner-scoped reconciliation evidence and separately approved differences. Preserve provider postsettlement protections, clients/loans/shipping/profit/visual gates. Before later release work, read actual production_release_guard.py status; never cancel another task's lease or reuse the dashboard identity.
