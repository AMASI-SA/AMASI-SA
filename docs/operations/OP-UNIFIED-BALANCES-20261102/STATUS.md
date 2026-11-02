# OP-UNIFIED-BALANCES-20261102 — operational bundle subtask added

Status: DRAFT / NOT production-ready / NOT published / NO financial activation.

## Latest recoverable source

Repository AMASI-SA/AMASI-SA; branch integration/operational-balances-unified-20261102; Draft PR1687. Application checkpoint **9919af53c6762dc2766eb2421af29932deac7955**, tree **070f53e440fddd0ecd3228bc8545b1846c938684**, directly descends from f7a2480bd3cabcf880dfe7fcb746c820551e3fad. This following status-only commit changes documentation, not application code. Latest incorporated production remains70576e2a06237eb193b1c927f11ced92e2229589. Read current remote state before continuing.

**No earlier work was replaced.** The complete previous canonical record is preserved byte-for-byte as STATUS-PRE-BUNDLE-20261102.md (original blob7f79d7530cdf33a3c3ce95038868da0d44823bf4). It retains exact source/merge/CI artifact identities, failed release-readiness evidence and all financial gates. Detailed prior history remains in COMBINED-REGRESSION-20261102.md, COMBINED-NATIVE-UI-20261102.md, PAYMENT-COMPATIBILITY.md and SUPPLIER-RECONCILIATION.md.

## Owner scope clarification

The other conversation owns the dashboard bundle-cost fix and its publication (#1690). This task owns OPERATIONAL balances only. Do not modify the dashboard's files, reuse its aggregate totals as supplier debts, republish its release, or drop either task. At the last read,1690 was Draft at3e6ea31764b194432e10027c03d82c5ccf502901; the owner's statement that it is being published is not a live v5 verification. Re-read its eventual final authority contract before wiring.

## New work and exact limits

Added backend/operational_bundle_cost_projection.py and backend/tests/test_operational_bundle_cost_projection.py. The new core projects frozen expanded bundle leaves once per sale unit, including unconditional and customer-selected child products and nested quantities. SAR cost does not inherit customer currency. Unreceived eligibility is restricted to awaiting-review/in-progress aliases. Missing/mismatched evidence remains unknown, not zero. Stock/receipt/consumption/segment evidence requires financial matching; supplier assignment cannot create a payable. posting_allowed=false remains intentional.

Synthetic checks: four(shawl15+rose40)=220SAR; four shawls without rose=60SAR; optional independent embroidery10 makes260SAR. One received rose with unmatched financial proof leaves180SAR as a known unreceived subtotal while the complete total is explicitly unavailable. No40SAR payable is invented.

Local command: PYTHONPATH=backend python -m pytest backend/tests/test_operational_bundle_cost_projection.py -q --junitxml=tests.xml. **67 passed,0 failures/errors/skips**. These are pure synthetic Python tests, NOT Mongo/HTTP/runtime/production acceptance. Remote module blob2fec6cd0fac34b60b9c9b1889004793b8d10b564 and test bloba6319a8363458c14931f2fdaf80ba4b26aaa0895 were read back and exactly match tested local bytes.

**The projection is NOT wired into operational source readers, the reducer or the report.** This checkpoint does not fix live balances. Detailed contract and remaining wiring: bundle-cost/STATUS.md. The earlier1363 backend/300 frontend combined success belongs to5b16ea6b, not this new checkpoint; do not add counts or claim fresh full acceptance without new evidence.

## Full business scope retained

| Scope | Preserved implementation / remaining gate |
|---|---|
| Supplier invoices #1675/#1677 | Independent approval cutoff for old orders, final-price estimate replacement and saved payments retained. Need actual read-only invoice/opening differences and owner approval before recognizing omitted amounts. |
| Products #1678 + bundle subtask | Pure bundle projection now tested. Need shared authority source wiring, current/precutover piece coverage, general-product SAR/status fixes and native bundle receipt/invoice mapping. Ordinary product source defects are NOT fixed by the new unused helper. |
| Shipping/drivers/COD #1677/#1688 | Old-order postcutoff earned fees, executor proof, bank/POS/cash separation and published courier visibility retained. Need current-carrier/full coverage and actual reconciliation; never call uncollected COD cash. |
| Providers #1685 | Opt-in component fee model and capture/refund compatibility retained. Actual dated capture/plan/void/refund sources, merchant policies, gross/net openings, postsettlement fee/tax credits and partial/multiorder bank allocation remain. Keep provider_postsettlement_refund_review_required and statement posting_allowed=false. |
| Customers/external persons/loans #1686 | Published compensation and original-platform restrictions retained. Need all four borrowing/lending/repayment directions, before/after balances, receive-in/pay-from labels and full exchange/return/shipping dedup evidence. |
| Payroll/openings #1677 | Inclusive saved-cutoff day and owner approval UI retained. Opening review and missing-day approval still required. No live salary backfill. |
| Warnings #1676 | Aggregation retained. Resolve real Salla source, product, carrier/rate, payment fee, advertising start/FX and salary-contract gaps; do not blanket-hide or substitute zero. |
| Financial report/release | Existing tables retained. Complete common-period expected position/profit and visual/mobile/actual print/download checks; business gates then fresh unified A/build/intent-onlyB and one separately coordinated v5 publish. |

## Safety / deployment state

Production changed by this task:NO. No database, opening reset, historical replay, invoice/return, stock, balance correction, payroll/policy activation, /app, lease or release intent changed. Existing branches and dashboard files untouched. Restricted COD archive remains excluded and was not accessed. Issue1006 is capped; use this record and PR1687.

Earlier Release Readiness41465056371 onf7 failed source/intent classification (frontend source mismatch and sourceA changing intent); do not relabel it as success. This new unused helper does not resolve that release gate. Remove the temporary source-export workflow before final release freeze. Read actual production_release_guard.py status before touching/app or release preparation; never clear another task's lease. Publishing code is not financial reconciliation or authorization for hidden backfill.

## Next safe action

Read current1687/1690/production. Wire the pure core to the shared frozen cost authority using batched owner/order/line reads and fingerprint validation, never current-catalog repricing or per-component N+1 reads. Bundle parents are excluded from legacy preparation supplier handling, so map native procurement receipts/invoices explicitly rather than treating a parent invoice as every child invoice. Preserve immutable saved financial facts and calculate approved-source differences read-only. Test source+engine+preview, mixed/partial receipts, exact invoice replacement, retries, SAR and both sides of cutoff on the combined tree before activation or one final v5 release.
