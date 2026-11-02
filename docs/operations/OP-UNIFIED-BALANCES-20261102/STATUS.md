# OP-UNIFIED-BALANCES-20261102 — native source proof and explicit external loans

**DRAFT / NOT published / NO live financial correction, backfill or activation.**

## Current source and preserved work

Repository AMASI-SA/AMASI-SA; branch integration/operational-balances-unified-20261102; Draft PR1687.

- Latest application checkpoint **62fa255b5a1d99952b94c47672b2cc2029c7940b**, root **b1be82803c126db862adbd29debf395deab3960a**. Exact remote Backend **570c47ccbac15544f2d31d7ab6efc86957eb119c** and Frontend **c1102f0324d0cd74cfa1778d2d07b2d2f176b5c9** match the full local trees, not just a selected diff.
- Normal source progression: verified9c04572f -> native proof **99bd1b96a297cf5aa9dcd71ba6facfbcd6c2f4fd** -> staging8a1076a0 -> exact source62fa255b. This following checkpoint removes the temporary source workflow and changes CI/docs only; application bytes remain62fa.
- Published production/dashboard1690 **f8235d57e90f8a1271df6350d7e7919f5621ea11** remains incorporated; no dashboard, original task branch, release intent or production source was replaced.
- Previous canonical STATUS preserved byte-for-byte as STATUS-PRE-EXTERNAL-LOANS-20261102.md, blob **c5dd2261c0ccd4f87df2dfc7df36a86d3478a637**. Earlier PRE-NATIVE/PRE-SUPPLIER/PRE-REMAINING/COMBINED/PAYMENT/SUPPLIER records remain. Issue1006 is capped; use this record and PR1687 comments.
- Owner clarification remains: opening is the existing saved starter balance, not a new opening to enter. New post-start approved invoices are not excluded merely because customer orders are old or an opening might hypothetically overlap. No such live duplicate was established. Do not rewrite openings or old cash movements.

## Implemented native purchase proof at99 — read only, not operational matching

Supplier invoice preview now calls the public sealed V2 ledger reader in its same consistent snapshot. It verifies the approved native purchase's owner/schema/SAR/line totals, inventory/payable/tax journal and evidence, saved liability and exact supplier-debit/bank-credit payment references. Partial/multiple/full payment totals must agree with the saved liability. Missing audit/groups, reversed/duplicate entries, mismatched owner/account/reference/date/amount and scope limits remain explicit unknowns.

Nested native_ledger_review provides verified SOURCE gross/settled/outstanding and bank/journal evidence digest. It does NOT create or import an operational debt. operational_balance_matched=false, opening_coverage_verified=false, difference_sar=null, posting_allowed=false remain; outer native verified payable stays null. Keep bundle_received_purchase_financial_matching_required. Matching proven source debt/payments to operational identities/opening coverage remains unimplemented. A native proof failure does not erase an independently valid preparation invoice comparison.

## New explicit external-person loan intent in the existing movement flow

A synthetic reproduction exposed the ambiguity: generic incoming100 then generic outgoing40 retained100 payable plus40 receivable. The software cannot know that the second cash movement was repayment. Do not retrospectively reinterpret those old rows.

New explicit operations use the SAME create_movement, owner/actor validation, claim/CAS, audit and exact-retry path:
- borrow: incoming collection creates an independent amount owed to the external person;
- lend: outgoing payment creates an independent amount owed to us;
- repay_borrowing: outgoing settlement reduces existing payable;
- collect_lending: incoming settlement reduces existing receivable.

Creation never auto-settles an opposite existing debt or accepts settlement allocations. Repayment never becomes an unintended new loan: no-debt/overpayment is rejected. Explicit repayment can allocate existing openings, ordinary obligations and stable virtual identities for the SAME prior unallocated external cash contribution. Virtual identities do not post a second debt or mutate old movement/opening records. No matching by name or amount, no automatic historical opposite-movement netting.

The report subtracts explicit allocations exactly once from the original advance. Before/after party receivable/payable and bank/cash balances are saved with the explicit new movement inside its existing CAS. Native mobile WRITE does not imply READ: fresh report permission is rechecked after commit; balances are redacted on absence/revocation without losing the committed movement acknowledgement. Existing web active-membership behavior is unchanged.

Daily Movements, both card and advanced views, has a required explicit external-operation choice for new entries; direction/type follow the operation. General/manual movement remains available explicitly. Incoming labels show الاستلام في, outgoing الدفع من. The confirmation table displays server-provided before/after amounts; missing data is not zero. Persisted old pending requests retry unchanged with no added intent/hash change. No new parallel financial writer or API endpoint.

Eight application/test changes: operational_external_advances.py; operational_balance_service.py; operational_balance_routes.py; test_operational_balance_external_advances.py; DailyMovements.jsx; ExternalOperationControls.jsx; ExternalOperationControls.test.jsx; one existing OperationalBalances.test.jsx setup selects general explicitly. Its original monetary/retry assertions remain unchanged.

## Exact verification and boundaries

### Native99 remote acceptance (prior exact source)
Operational Balance Web **41512254889** completed both jobs successfully. Official ZIPs downloaded, SHA256-checked and independently parsed:
- Backend artifact13174905072, ZIP **14a7c444d690a5cabcd5b13a57f02cd14fc2b05c9420d580d147c9356fca0444**: **1608 testcase elements**,0fail/error/skip, embedded99 source/root79839046 match. Includes72 native proof cases, of which10 use actual isolated Mongo.
- Frontend artifact13173746736, ZIP **ede6ca503a6f5d20d1d62942d725d0011a8c3889dfcc0c0874e2f772037f4032**: **300 tests/25 suites**,0failed/pending/todo.
These are native99 results, NOT acceptance for the newer external-loan UI.

### External62fa local combined acceptance
Exact Backend570c47cc: **1636 passed,0failures/errors/skips,exit0,311.21seconds**, Python3.13.5. The28 new actual-Mongo external loan cases are INCLUDED, not added to1636; focused28 passed11.88seconds. Existing native, supplier, remaining-product, payroll, noncash, provider suites remain collected. Five existing Pydantic deprecation warnings remain.

Command: PYTHONPATH=<isolated-test-deps>:backend:backend/tests with OPERATIONAL_TEST_MONGO_URI and MZ2_TEST_MONGO_URI set to the dedicated localhost replica, python -B -m pytest --noconftest -q backend/tests/test_operational_balance*.py backend/tests/test_operational_open_cost_exposure.py backend/tests/test_operational_bundle_cost_projection.py backend/tests/test_operational_supplier_invoice_cutover.py backend/tests/test_store_delivery_noncash_review.py backend/tests/test_operational_driver_review_mongo.py --tb=short --junitxml=combined-external-review.xml.

JUnit SHA256 **374d0e0190bf92c05e96db480d3b1543d50c71afab3417a98d90c753b4ef8cc3**; log **9a7c852eda9a2ceca9ddca9916e92e6ef18def8e1557b0a532a9531dcccaed3a**. Actual isolated Mongo8.0.12 bound127.0.0.1 only, synthetic UUID databases, no merchant credentials. The dedicated fixture was stopped; six baseline cached UUID test databases were found and the task-owned fixture directory removed after shutdown. No external database was touched.

Cases include four directions/partial/full payment, independent opposite loans, old saved advances explicitly repaid without rewriting, old opposite generic movements unchanged, opening repayment without editing opening, overpayment and concurrent duplicate protection, owner/HTTP/session checks, native READ/WRITE separation and revocation, exact retry and refresh.

Frontend suite is authored but NOT run locally (no node_modules); fresh exact-head GitHub frontend results are mandatory. No browser/mobile visual/print/download acceptance is claimed. Initial backend fixture failures were a too-short request ID and a mistaken web permission assumption; tests now exercise the existing native permission boundary, not a weakened route guard.

Source transfer uses eight reviewed plaintext patches, combined SHA256 **53d512004cfb1c6c090cdf1d8445d3dda7605065be5c2bcf64bd1c1e38194dc5**, exact before/after Git blobs and a clean local application rehearsal. The branch-only one-shot applied only those eight files by normal non-force push, removed its own payload, and performed no application/runtime/financial operation. Full remote Backend/Frontend trees matched; this checkpoint removes the temporary workflow. No retained transfer execution surface is needed.

CI adds positive collection requirements for28 external cases,72 native proof cases and the new UI suite, preserving every previous no-failure/no-error/no-skip/Mongo/supplier/payroll assertion. Fresh combined CI/security/build remains to be read on the new head; never add overlapping counts or reuse earlier300 as new UI acceptance.

Repository-wide static ledger access check had5 pre-existing findings in unchanged accounting_advertising_setup.py, store_delivery_cash_evidence.py and accounting_driver_review_history.py; new native source files had0. Keep that recorded failure. Earlier Release Readiness41506311209 failed A/B classification before final build; native proof/loans do not imply that gate is fixed. Preserve guards and create a fresh finalA/build/intent-onlyB after business gates close.

## Full remaining scope / activation inventory

| Scope | Implemented and preserved | Remaining |
|---|---|---|
| Supplier invoices | Post-start approval on old orders, final-cost replacement, saved payments, snapshot comparison; native source ledger/payment proof | Native-to-operational identity/payment/opening matching and actual owner read-only differences. Explicit financial approval before recognizing omitted amounts, not hypothetical blanket opening exclusion. |
| Remaining products | Exact statuses, SAR, frozen selected/unconditional/nested components, partial receipt source/reducer/report and scoped gaps | Native payable bridge, unsupported pre-invoice bundle receiving, real owner comparison/performance. Do not fabricate supplier debt from stock/assignment. |
| External persons/loans | Explicit four actions, same movement CAS, before/after balances, labels, retries/concurrency/native permissions; backend locally tested and UI authored | Fresh combined frontend/CI and visual acceptance; actual old misclassified movements require separate read-only explanation/approval, no automatic historical repair. This does not complete exchanges/returns/shipping dedup. |
| Customer exchanges/returns | Published compensation/original-platform rules retained | Cross-path replacement/return/shipping monetary and duplicate acceptance remains. |
| Payroll | Inclusive saved cutoff, preview/approval/idempotence retained | Actual missing-day/opening review and owner activation; no day-one posting. |
| Providers | Opt-in components, aliases/explicit plans, original capture/refund protection retained | Actual payment/plan/void/refund source, approved terms/gross-net evidence, postsettlement fee/VAT credits/partial-multiorder bank links. Keep provider_postsettlement_refund_review_required and statement posting_allowed=false. |
| Shipping/drivers/COD | Postcutoff earned old-order fees, executor/cash/POS/bank separation | Current carrier/full coverage, actual matching, expected COD; uncollected COD not cash. Restricted old archive unaccessed. |
| Warnings/report | Aggregation, scoped product/shipping totals and financial tables | Actual source/ad-start/FX/salary evidence, complete common-period position/profit, browser/mobile/actual print/download. |
| One operational release | All tasks preserved, dashboard not rewritten | All business gates and activation boundary, new combined source/security/UI/build, fresh A/build/B, inactive guard before/app, one publish, verify3 and active=false. |

Production changed by this task:NO. No live balance/opening correction, historical replay, invoice/refund/stock/bank/provider operation, payroll/policy activation, /app, lease or publication. Code publication is not financial reconciliation or approval.

Next: read current head, fresh exact combined tests and repair any new UI failure without dropping assertions; then close native operational matching and provider/customer/shipping/report gates. Compare actual owner data read-only before approving changes. Read actual production_release_guard.py status before any later deployment work, preserving all newer production source.
