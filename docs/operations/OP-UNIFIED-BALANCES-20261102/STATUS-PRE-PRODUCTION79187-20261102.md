# OP-UNIFIED-BALANCES-20261102 — native purchase proof and external-loan acceptance

**DRAFT / NOT published / NO live financial correction or activation.**

## Latest source and preservation

Repository AMASI-SA/AMASI-SA, branch integration/operational-balances-unified-20261102, PR1687. Current application source **db6d7fcff142b338133b768a2c8a9e2f7d51b551**, root4598d256bb0f92d3c6175fdf4089eee515a6de84. Complete Backend **4a815d113c75956e1291b032e57471bf24517bee** and Frontend **3ec7b1caaef89bca35ec98d2e232f418b08e7685** were independently matched to local complete trees after remote read-back. This following checkpoint changes documentation and removes its temporary source helper only; it does not change those application trees. Read the actual new HEAD and latest scoped verification comment before continuing.

Normal ancestry preserves verified9c -> native99bd1b96 -> external62fa255b/6cfacc65 -> reviewed repairdb6d7fcf. Published dashboard production **f8235d57e90f8a1271df6350d7e7919f5621ea11** and all original supplier/payroll/provider/remaining-product work remain intact. No original branch, dashboard, shared ledger writer, release intent or production source was replaced.

The previous complete canonical record is preserved byte-for-byte in STATUS-PRE-LOAN-SCOPE-REVIEW-20261102.md, blob8517a91177d73b302267578a96e5efa7b6373349. It retains complete commands, source hashes, native99 official artifacts, local1636 evidence, original failures, CI collection requirements and the entire scope. Earlier PRE-NATIVE/PRE-SUPPLIER/PRE-REMAINING/COMBINED/PAYMENT/SUPPLIER records remain. Issue1006 is full; this record and PR1687 comments are canonical.

## Implemented this continuation

**Supplier evidence:** existing consistent-snapshot supplier-invoice-review now verifies native purchase invoice/liability/inventory-payable-tax journal and exact supplier-debit/bank-credit settlement references through the PUBLIC sealed V2 ledger reader. Partial/full/multiple payments and tax treatment are checked. Tampered, missing, duplicate or reversed proof remains unknown, not zero. Synthetic invoice80/paid25 proves native source outstanding55. This is NOT an additional operational debt: native operational/opening matching flags remain false, correction delta null, posting_allowed=false; bundle_received_purchase_financial_matching_required remains. Native proof does not erase valid preparation-invoice comparison.

**External people:** four explicit intents use the EXISTING movement CAS/audit/idempotence path: borrow(incoming creates payable), lend(outgoing creates receivable), repay_borrowing(outgoing reduces payable), collect_lending(incoming reduces receivable). Creating an independent loan does not auto-settle an opposite debt. Explicit repayments can allocate existing openings/obligations or stable virtual identities for the SAME already-counted unallocated external cash. No duplicate financial fact or historical row rewrite. No-debt/overpayment fails. Old generic opposite movements remain unchanged, not silently reinterpreted.

The UI supports these intents in both card and advanced entry, locks direction/type, uses الاستلام في / الدفع من, and shows server-confirmed before/after party/account balances. General/manual entry is explicit. Old persisted pending requests retry without adding intent or changing hashes. READ permission is independent from WRITE; post-commit report access is rechecked and balances are redacted without losing the successful movement acknowledgement.

**Final reproduced security boundary:** after a native employee is reassigned to another owner during commit, valid READ permission for the NEW owner must not disclose the PREVIOUS owner's snapshot. The new real-Mongo test first failed, then passed after requiring fresh actor AND owner to equal the committing scope. Focused external suite now has29 passing cases. No live employee or balance was used.

Owner clarification: the opening is the saved startup balance, not a new balance to re-enter. New supplier invoices approved after saved startup count independently of customer-order age. No live overlap was proved, so a hypothetical opening duplicate is not a blanket invoice block. Saved openings, historic payments and movements stay unchanged.

## Verification: preserve failed and passed evidence separately

1. Native99 exact remote Operational Balance Web **41512254889** succeeded: independently downloaded/hashed/parsed1608 backend cases and300 frontend tests/25suites, zero failures/skips. Full official artifact IDs/digests are retained in the previous record. Those results do NOT validate the subsequent external-loan UI.
2. External62fa local exact Backend570c47cc passed1636 combined cases, including28 real-Mongo loan cases. That earlier test tree is not the final actor-owner repair. Its command/XML/log hashes remain in the previous record.
3. New UI run **41516946902** on6cf failed:9 tests failed,307 passed,26suites. All9 failed during card navigation BEFORE monetary assertions because the test compared button.textContent including the decorative icon to the plain label. Existing25suites and the new advanced-path/legacy-retry cases passed. Official failure artifact13174859086 SHA256 **216bb164fac006253e31447ed0e8b02c72ae9b7d7794675c490a43bb00eb0601** was downloaded and independently parsed; preserve it, do not relabel as success.
4. Repairdb6 changes only the route actor-owner check, its new29th test, and the test navigation helper. Card selection now asserts exactly ONE button.op-tile whose strong label matches the requested name; the icon is not identity. All amount/direction/type/permission/retry assertions remain. No application label or financial guard changed to satisfy the fixture.
5. Three-file plaintext repair SHA256 **4faa0214ed90e2ac622eb48373e81c6dbeb50f450c45e317052522ddf8362bd6** passed a separate clean apply rehearsal, exact pre/post blob checks and full remote application-tree matching. The temporary branch-only source helper performed no application/runtime/financial commands, removed its payload, and is removed by this checkpoint. No retained source-transfer surface remains.
6. Final focused real-Mongo29 cases passed11.19s. Final full combined local rerun was still executing at this record's write; do not infer1637 or claim final success before parsing its XML. Fresh exact-head GitHub backend/frontend/security/build results are mandatory; later scoped verification comments may supersede this pending state. No local frontend/browser/mobile/print/download execution is claimed.

Local tests use Python3.13.5 and isolated Mongo8.0.12 bound127.0.0.1, synthetic UUID databases only. No merchant credentials/data. Stop ONLY the task-owned final fixture after testing. Prior baseline fixture cleanup is documented; no shared database was touched.

Repository-wide static ledger-access scan previously reported5 existing private-import findings in unchanged accounting_advertising_setup.py, store_delivery_cash_evidence.py and accounting_driver_review_history.py; new native production files had0. Do not call that scan globally passing. Release readiness on6cf (**41516947123**) returned failure; earlier confirmed failures were A/B source-intent classification before final build. Read exact current logs; do not waive the gate, weaken classifiers or reuse the dashboard intent.

## Full outstanding scope and activation boundaries

| Scope | Implemented/preserved | Still required |
|---|---|---|
| Supplier invoices | New approval on old orders, final-cost estimate replacement, unchanged payments, snapshot comparison, native journal/payment proof | Native source-to-operational obligation/payment identity matching and actual owner-scoped differences/coverage; explicit approval before omitted financial recognition. No re-entry or opening reset. |
| Remaining products/bundles | Exact statuses, original SAR, frozen selected/unconditional/nested components, partial receipt source/reducer/report and scoped gaps | Native operational payable bridge and unsupported pre-invoice bundle receiving, live read-only comparison/performance. Keep matching warning; stock/assignment is not new debt. |
| External persons/loans | Four explicit actions, existing CAS, correct settlement vs independent creation, before/after labels, concurrency/retry/permission and actor-owner protection | Exact final combined frontend/security acceptance and visual checks; historical misclassified cash requires separately approved correction, never inference. |
| Customers/exchanges/returns | Published compensation and original-platform restrictions retained | Full cross-path exchange/return/shipping monetary and duplicate acceptance remains, not completed by the loans change. |
| Payroll | Inclusive saved cutoff day with preview/approval/idempotence | Actual missing-day/opening review and owner activation; no day-one posting. |
| Providers | Optional fee components, native method/plan and original capture/refund protection | Actual dated capture/plan/void/refund data, approved merchant policy/gross-net basis, postsettlement actual fee/VAT credits and partial/multiorder bank allocation. Keep provider_postsettlement_refund_review_required and statement posting_allowed=false. |
| Shipping/drivers/COD | Postcutoff delivery earning on old orders, executor proof and cash/POS/bank/fee separation | Current carrier/full coverage, actual settlements and expected COD. Uncollected COD is not cash. Restricted prior forecast archive remains UNACCESSED. |
| Warnings/report/release | Existing warnings/scoped completeness/financial tables and published dashboard preserved | Actual source/ad-start/FX/salary gaps, complete common-period expected position/profit, browser/mobile/print/download; then fresh finalA/build/intent-onlyB and ONE operational publication with verify3 and active=false. |

Production changed by this task:NO. No live balance/opening correction, historical replay, supplier invoice/refund/stock/bank/provider operation, payroll/policy activation, /app, lease, prepare/prepublish or Publish. Code publication is not reconciliation/financial approval.

Next: read current source and exact combined outcomes; fix any remaining real failure without skipping tests. Then continue native operational matching and provider/customer/shipping/report gates, obtaining actual read-only differences before financial activation. Read actual production_release_guard.py status before any later/app/release work and preserve all newer production updates.
