# OP-UNIFIED-BALANCES-20261102 — remaining-product scope and CI checkpoint

Status: DRAFT / NOT production-ready / NOT published / NO financial activation.

## Latest continuation — remaining products, not dashboard cost of sales

Owner clarified the required destination: the operational accounting bucket for remaining products NOT YET RECEIVED from suppliers. Include the unreceived base/child product, unconditional or selected-option bundle leaves and required independent resources once. Receiving the shawl does not imply receiving its rose. A received component leaves the unreceived estimate even when its invoice is pending; its received-uninvoiced/confirmed supplier treatment needs separate native evidence. Apply piece-level awaiting-review/in-progress eligibility to orders created both before and after saved operational startup. Already-SAR costs remain SAR, independently of customer currency. Do not change the other conversation's dashboard implementation.

Latest CI collection checkpoint: **7ca114df1503c687f1bf780e332b743c2a0d1fd1**, directly after a70a3a059abe3a0afed8be104c887b727035a332. This subsequent status-only commit preserves application code. The read-back commit diff contains exactly three added workflow lines and no deletions: explicitly select test_operational_bundle_cost_projection.py and require at least its67 existing testcase elements. Existing no-failure/no-error/no-skip, supplier, payroll and real-Mongo coverage guards are unchanged. No application, dashboard, release-intent or production file changed.

Verification on this continuation:
- Re-read PR1687, canonical status, AGENTS.md, workflow, module/test blob SHAs and related PR1678/1685/1690. Actual PR1687 HEAD before the write matched a70. The old PR body still names f7 and is not the latest source authority.
- Local bytes were checked against current remote module blob2fec6cd0fac34b60b9c9b1889004793b8d10b564 and test bloba6319a8363458c14931f2fdaf80ba4b26aaa0895 before execution.
- `PYTHONPATH=backend python -B -m pytest --noconftest -q backend/tests/test_operational_bundle_cost_projection.py --tb=short --junitxml=bundle-tests.xml` returned exit0: **67 passed**, zero failures/errors/skips. JUnit independently parsed67 cases. These are pure synthetic tests, not runtime or database acceptance.
- The prior a70 workflow list returned22 successful workflows, including Operational Balance Web41470760539, Security and CodeQL; Release Readiness41470760702 failed in `Classify reviewed B or source candidate A`, with final frontend build/rehearsal skipped. This is not a successful publication gate.
- Important coverage correction: the prior operational workflow glob `test_operational_balance*.py` did NOT collect the new `test_operational_bundle_cost_projection.py`. That prior green operational run cannot substantiate bundle coverage. The three-line workflow change closes collection only, not source/reducer/report wiring.
- Fresh combined results after7ca and this status commit must be read from their exact runs; do not infer1430 tests, retained artifact identity, or success by adding old counts.

Related task read snapshot: #1690 advanced to ready-for-review intent-onlyB f953c374239ae89a9311b3e311945ffa4e7933a5 with sourceA dc96a12e72ba3e1e1766c44e37e494ba3b69250f, still unmerged at the read. Its publication remains the other conversation's responsibility. #1678 is still d5b301506912a481196af295b0812f3759d7a27f with one review workflow, not implemented runtime product fixes. #1685 remains88c7872072e6e6a149a92153a732ae4c0551ae21; do not merge its older branch independently over the integrated code.

Next safe action: finish operational remaining-product source/reducer/report wiring and ordinary-product SAR/status corrections using the final shared cost authority contract, with per-leaf partial-receipt/invoice mapping and no new payable from assignment/stock evidence. Run the explicitly expanded exact-head combined suite. Keep every business gate below, including postsettlement refunds, actual payment evidence, customers/loans, shipping, warnings, read-only opening/invoice differences and owner approval before financial recognition. No lease, /app, prepare/prepublish, production merge or publish in this continuation.

## Earlier recoverable source — preserved history

Repository AMASI-SA/AMASI-SA; branch integration/operational-balances-unified-20261102; Draft PR1687. Application checkpoint **9919af53c6762dc2766eb2421af29932deac7955**, tree **070f53e440fddd0ecd3228bc8545b1846c938684**, directly descends from f7a2480bd3cabcf880dfe7fcb746c820551e3fad. The a70 status-only checkpoint followed that application commit. Latest incorporated production remains70576e2a06237eb193b1c927f11ced92e2229589. Read current remote state before continuing.

**No earlier work was replaced.** The complete previous canonical record is preserved byte-for-byte as STATUS-PRE-BUNDLE-20261102.md (original blob7f79d7530cdf33a3c3ce95038868da0d44823bf4). It retains exact source/merge/CI artifact identities, failed release-readiness evidence and all financial gates. Detailed prior history remains in COMBINED-REGRESSION-20261102.md, COMBINED-NATIVE-UI-20261102.md, PAYMENT-COMPATIBILITY.md and SUPPLIER-RECONCILIATION.md.

## Owner scope clarification

The other conversation owns the dashboard bundle-cost fix and its publication (#1690). This task owns OPERATIONAL balances only. Do not modify the dashboard's files, reuse its aggregate totals as supplier debts, republish its release, or drop either task. At the original bundle-core read,1690 was Draft at3e6ea31764b194432e10027c03d82c5ccf502901; the newer read is recorded above. The owner's statement that it is being published is not a live v5 verification. Re-read its eventual final authority contract before wiring.

## New work and exact limits

Added backend/operational_bundle_cost_projection.py and backend/tests/test_operational_bundle_cost_projection.py. The new core projects frozen expanded bundle leaves once per sale unit, including unconditional and customer-selected child products and nested quantities. SAR cost does not inherit customer currency. Unreceived eligibility is restricted to awaiting-review/in-progress aliases. Missing/mismatched evidence remains unknown, not zero. Stock/receipt/consumption/segment evidence requires financial matching; supplier assignment cannot create a payable. posting_allowed=false remains intentional.

Synthetic checks: four(shawl15+rose40)=220SAR; four shawls without rose=60SAR; optional independent embroidery10 makes260SAR. One received rose with unmatched financial proof leaves180SAR as a known unreceived subtotal while the complete total is explicitly unavailable. No40SAR payable is invented.

Original local command: PYTHONPATH=backend python -m pytest backend/tests/test_operational_bundle_cost_projection.py -q --junitxml=tests.xml. **67 passed,0 failures/errors/skips**. These are pure synthetic Python tests, NOT Mongo/HTTP/runtime/production acceptance. Remote module blob2fec6cd0fac34b60b9c9b1889004793b8d10b564 and test bloba6319a8363458c14931f2fdaf80ba4b26aaa0895 were read back and exactly match tested local bytes.

**The projection is NOT wired into operational source readers, the reducer or the report.** This checkpoint does not fix live balances. Detailed contract and remaining wiring: bundle-cost/STATUS.md. The earlier1363 backend/300 frontend combined success belongs to5b16ea6b, not the new checkpoints; do not add counts or claim fresh full acceptance without new evidence.

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
