# OP-UNIFIED-BALANCES-20261102 — remaining products source/report wired

**DRAFT. NOT published. NO live financial activation or reconciliation.**

## Latest recoverable source and preservation

Repository AMASI-SA/AMASI-SA; Draft PR1687; branch integration/operational-balances-unified-20261102.

- Application source checkpoint: **dc965e5bfa6a799aec5adcfc0dabff7c88be6728**; root tree **edc9ca96708af0dd78d3c164a6b05d2f01a22a39**.
- Exact tested Backend tree: **95877afcb873dc585ae2dbdb1b436d4c81035d5a**; remotely read back and matches the local complete Backend tree, not only a selected diff. Frontend remains **2ed74cb8d188606549a3c43ff23a10a835131d06**.
- Source was applied as a hash-locked normal fast-forward after base **86606a698648a4b4a3dfa4382e5cf3c466e94e76**. Its application source equals archive006df1d4c7a914f386ecd7777971362d5e774402 before this change; only temporary workflows differ. No older production was restored, no original task branch rewritten.
- Latest production already incorporated in this branch: **f8235d57e90f8a1271df6350d7e7919f5621ea11**, dashboard1690. Its own published record documents sourceA dc96a12e72ba3e1e1766c44e37e494ba3b69250f, release rg5-0f3fe5bc5d7da01a5dd3c052cb36637a7a49db1536afe476d7ff862d0fac285a, verify3 and active=false. This is the other task's evidence, not a new publication by this task. Dashboard1690 still has live recipe/executor/historical-evidence gaps; do not fabricate missing frozen recipes.
- This following checkpoint changes CI/documentation and removes temporary source/dependency/checkpoint workflows; it does not change the tested Backend/Frontend. The current HEAD must be read, not inferred from the old PR body.

Prior complete handoff is preserved byte-for-byte in STATUS-PRE-REMAINING-WIRING-20261102.md (blob f56b2b5c7282fa4fe8a0f72720a3133ca1b3e50d). Earlier evidence remains in STATUS-PRE-BUNDLE-20261102.md and the COMBINED/PAYMENT/SUPPLIER documents. Do not use their old counts as new acceptance.

## Implemented now — operational unreceived cost, not dashboard cost of sales

The previously unused bundle core is now connected to collect_sources, the pure balance reducer, pre/post-cutover open-cost exposure and the financial-position table.

1. Shared frozen cost authority supplies the actual selected/unconditional expanded recipe. The adapter excludes reporting-only correction records from physical-finance interpretation. It validates owner/order/line, fingerprints, selected option IDs, variants, plan/current unit set and exact leaf identities. No current catalog repricing, parent/base double-addition, SKU join, or sale-quantity multiplication twice.
2. Native purchase receipt/allocation evidence removes only its received fraction. Stock allocations remove already-covered procurement demand but never create another purchase/payable. Split auto sourcing validates each segment. Unknown, duplicated, cross-tenant, overallocated or future evidence remains incomplete and preserves existing valid estimates rather than silently zeroing them.
3. Supplier-assigned-only components stay in the unreceived pool; assignment is not receipt. Normal and bundle estimates use their original SAR cost independent of SAR/QAR/KWD/AED sale currency. Explicit foreign cost requires review, not relabelling or sale-rate conversion.
4. Unreceived estimates require exact awaiting-review/in-progress aliases. Received ordinary pieces stay separate while their invoice is pending, including closed orders. Orders before cutoff remain in read-only cost exposure; their old sale/provider capture is not replayed into operational finance.
5. Unreceived, received-uninvoiced and shipping completeness are now scoped. Missing carrier/rate data keeps its warning and the shipping total unavailable without hiding a separately proven product total. Unknown/global issues still fail closed, and the report retains row-sum, invalid-money and coverage checks.
6. Reads are bounded and owner-scoped, with one shared authority pass and batched native plan/unit/receipt/invoice reads. In the synthetic no-receipt cohort,1 and80 orders both used7 logical reads; this is not a production latency guarantee. No read-path index creation, DB write, stock execution or financial posting.

Synthetic end-to-end source/reducer/report results: four(shawl15+rose40)=220SAR; all shawls received=>160; two roses received=>80; all=>0. No-rose=>60; independent embroidery10=>260. Partial nested rose quantity is reduced fractionally. Catalog repricing to999 leaves the original frozen cost unchanged. Retry reproduces identical obligations and never creates supplier confirmed facts from native inventory evidence.

**Important remaining native financial gate:** a proven received bundle purchase quantity exits the unreceived bucket, but linking its native purchase invoice to existing operational supplier balances/opening coverage is NOT completed. `bundle_received_purchase_financial_matching_required` remains explicit, blocks the received-cost completeness only, and never manufactures a second supplier debt. Ambiguous or unsupported receipt evidence still blocks the relevant total; this is not a complete native purchasing financial importer.

## Exact local verification and remote boundary

Local source archive006 was verified against4803 exported Git blob identities. Changed bytes were recorded with before/after Git SHA and SHA256; a separate clean local apply rehearsal reproduced all11 application/test files. Source checkpoint run41499760086 applied those exact bytes to the task branch only, and subsequent remote Backend tree read-back equals95877afc.

Final local combined command (Python3.13.5):

```
PYTHONPATH=<isolated-test-deps>:backend:backend/tests \
OPERATIONAL_TEST_MONGO_URI='mongodb://127.0.0.1:27305/?replicaSet=operationalprojection' \
MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27305/?replicaSet=operationalprojection' \
python -B -m pytest --noconftest -q backend/tests/test_operational_balance*.py \
 backend/tests/test_operational_open_cost_exposure.py \
 backend/tests/test_operational_bundle_cost_projection.py \
 backend/tests/test_operational_supplier_invoice_cutover.py \
 backend/tests/test_store_delivery_noncash_review.py \
 backend/tests/test_operational_driver_review_mongo.py --tb=short --junitxml=combined.xml
```

**1499 passed,0 failures/errors/skips; exit0;149.56s.** JUnit independently parsed1499 testcase elements. Includes51 new actual isolated Mongo remaining-product cases and18 scoped completeness cases, plus67 existing pure bundle tests. These are subsets, NOT additive. Existing payroll/noncash/provider real-Mongo suites were also collected. Mongo8.0.12 was bound to127.0.0.1 only, every test database UUID-named and dropped. No production credentials or tenant data were used. Five existing Pydantic deprecation warnings remain; they are not failures.

JUnit SHA256 c2b184d00ae83c62f7a0939d4853b29d6d68a9704a2172dd8f059214f65270dd; log SHA256 4eae9287e3d44dbedc6af21fa3d783b6c3fb5fd2d303e884c1a4fc25cc493984. Earlier intermediate1478/1497/1498 runs overlap and must not be summed. The source and financial guards were preserved; populated native cases use real Mongo, not the old empty-source mocks.

CI now explicitly requires at least50 real-Mongo remaining-product cases and the scoped-completeness suite in addition to existing67 bundle/no-skip/payroll/supplier coverage guards. Local application run is not the final GitHub/frontend/security/build acceptance: read fresh exact-head runs after this checkpoint. No local frontend/browser/print/download execution is claimed. Earlier866 readiness41488314233 failed; that failure is not waived or repaired by this work. A new final A/build/intent-onlyB remains required after all business gates close.

## Full outstanding scope / activation inventory

| Scope | Implemented and preserved | Remaining / activation |
|---|---|---|
| Remaining products #1678/#1687 | SAR/exact states, per-piece ordinary receipt, frozen selected/unconditional bundle source/reducer/report, partial quantities and scoped gaps now wired and locally tested | Actual owner data/read-only comparison, native bundle invoice-to-operational-finance matching, supported pre-invoice native receiving, fresh combined CI/UI/performance acceptance. Not activated. |
| Supplier invoices #1675/#1677 | New approved invoices on old orders, final-component estimate replacement, prior payments/facts and account/report retained | Actual invoice/opening differences and explicit owner approval before omitted financial recognition. No backfill. |
| Payroll #1677 | Saved-cutoff day inclusive, preview/approval and idempotence retained | Opening coverage/missing day comparison and owner activation. No day-one posting. |
| Providers #1685 | Optional component fees, explicit plan/native aliases, original capture/refund compatibility retained | Real capture/plan/void/refund sources, merchant policy/opening gross-net evidence, postsettlement fee/tax credit allocation and multiorder bank matching. Keep provider_postsettlement_refund_review_required and statement posting_allowed=false. |
| Shipping/drivers #1677/#1688 | Old-order postcutoff earning, actual executor proof and bank/POS/cash separation retained | Complete current carrier reassignment/coverage, actual settlement matching and expected COD. Uncollected COD is not cash; restricted old COD archive was not accessed. |
| Customers/external loans #1686 | Published compensation/original-platform rules retained | All four loan/debt/repayment directions and before/after labels; cross-path exchange/return/shipping dedup acceptance. |
| Warnings #1676 | Existing aggregation and truthful scoped product/shipping completeness retained | Resolve real Salla/payment/advertising start/FX/salary-contract evidence; do not hide unknown sources or replace them withzero. |
| Financial report/release | Remaining-product table now consumes integrated exposure; other saved report work retained | Common-period full expected position/profit and visual/mobile/actual print/download tests, actual read-only financial reconciliation, all business gates, final unifiedv5 source/intent/build, inactive lease and one publication with verify3/activefalse. |

## Safety and next action

Production changed by this task:NO. No /app, lease, deployment, release intent, policy, payroll, opening, actual supplier invoice, bank/provider API, refund, stock or financial balance write. No original task branch or dashboard file changed. Temporary source-checkpoint payload was removed by the source commit; all temporary source/dependency/checkpoint workflows are removed in this following checkpoint. Issue1006 remains capped; this record and PR1687 are canonical.

Next: read current PR/source and exact new CI outcomes; preserve published1690. Close native bundle purchase financial matching with approved existing-source/opening evidence, then provider postsettlement and actual payment evidence, customers/loans and remaining shipping/report gates. Do not deploy this WIP merely because tests pass or use deployment to silently recognize missing historic financial amounts. Before any later /app/release work, read actual production_release_guard.py status and obey v5; never reuse another task's lease or old intent.
