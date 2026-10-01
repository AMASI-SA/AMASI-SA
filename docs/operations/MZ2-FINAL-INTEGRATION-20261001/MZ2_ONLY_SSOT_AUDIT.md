# MZ2 native SSOT audit - governance execution

Current POS addendum: the user's authoritative manual-review correction removes
the external processor-source requirement. Existing review and bank-settlement
writers now use an explicitly selected documented typed receivable
(`asset / exact identity / other_receivable`), bound receipt review and atomic
evidence consumption. POS source/identity is not an independent C blocker under
this contract. See MANUAL-POS-WIRING.md and the new exact-source checkpoint for
acceptance evidence. Historical processor-source statements below are superseded.
Operational cash summaries exist, but do not reconcile native remittances and
are not substituted for an authoritative H2 physical-cash read.

Inspected runtime: a58624ccb13c1e24319be6d1cac27c8558489ada (tree f5368740246868849edb8acb6130d2d9439926f7). The earlier missing-writer conclusions in this document were superseded by CUTOVER-COMPLETION.md; Git history retains that evidence. This audit covers the converted financial chains below, not every historical/operational route in the application.

## Source authority and tested boundaries

| Existing operation | Current source to native journal | Relevant isolated evidence |
|---|---|---|
| General/provider sale and Salla order recognition | accounting_receivable_service and accounting_order_recognition -> accounting_recognition_native -> sealed post_journal_v2 | test_mz2_receivable_workflow, test_mz2_order_recognition, test_mz2_recognition_cutover |
| Bank receipt and delivered advance conversion | accounting_bank_transfer_receipts -> accounting_customer_native -> post_journal_v2 | test_mz2_bank_transfer_receipts: native receipt/delivery Legacy isolation, rollback and retry |
| Advance capture/cancellation/payment | accounting_customer_advances -> post_customer_journal | test_mz2_customer_advances: native capture/cancel/payment, reversed/forged evidence |
| Refund entitlement/payment | accounting_refund_entitlements and accounting_customer_refunds -> post_recognition_journal | daily refunds, entitlement, report isolation, historical reversal and closed-period tests |
| Provider settlement | accounting_settlement_service -> post_recognition_journal; accounting_settlement_audit shares caller transaction | test_mz2_settlement_native_audit: actual HTTP lifecycle, command monitor, replay, injected audit failure rollback and owner isolation |
| Payroll, employee cash and daily expense | accounting_employee_finance/accounting_daily_movements -> accounting_employee_outgoing_native -> post_journal_v2 | employee finance, daily movements and employee/outgoing report tests |
| Daily supplier payment | accounting_daily_movements -> delivered C1 settle(PaymentIn), same transaction/evidence consumption | daily movement and supplier financial-port integration tests |
| Supplier invoice/payment and G47 | supplier_native_invoice_v2 / accounting_supplier_payments_v2 -> post_journal_v2 | C1/C2 and G47 production-router, canonical identity, atomic stock/payment and replay tests |
| Advertising spend/funding/payment | accounting_advertising_bridge -> post_journal_v2; original bank-statement and separate fee evidence consumed atomically | bank evidence adapters, advertising V2, wallet/zero opening and native report tests |
| Shipping COD/fees/driver bank | accounting_shipping_native and driver review -> original Track F writer; exact verified bank arrival proof | native shipping, driver bank evidence, no-netting P02 and settlement tests |

Root source search found no post_txn_group/general_ledger sink in these converted entry modules. Native event replay verifies sealed journal metadata; unknown/foreign/reversed identities fail closed. A separate independent trace corroborated this map. Dynamic zero-Legacy claims are limited to the monitored test chains; merely grepping source does not prove all runtime branches. The final regression record reports the freshly executed suite and its limits.

Native reports use accounting_mz2_reports' verified opening and V2-only entries. They never substitute Legacy balances or invent zero for unavailable identities. COD receivables, courier/driver fees, customer advances/refunds, supplier advances/payables and advertising wallets/payables retain their separate meanings. Full frontend on this integrated runtime:235 suites/1297 tests passed. Backend combined run:1695pass,86failureevents,927subtests passed. Four fixture failures were corrected and root52tests/484subtests passed; the remaining82 platform events in66parentcases are classified Windows limitations and covered by156 passing Linux release/guard tests. See STATUS.json and committed XML; no single all-green Windows run is claimed.

## Deliberate boundaries

- POS successful processor transaction ingestion/canonical binding is absent; accounting_driver_payment_port returns503. A receipt image or review metadata cannot substitute for successful processor evidence. Bank proof is implemented independently.
- Stage7 full courier draft cannot map losslessly to the active flat RateInput. The richer economics DO exist in accounting_shipping_contracts, and a retained review-candidate persistence/approval service also exists. They are not an active native setup service: save/approve immediately hit unconditional423 shipping_contract_native_path_locked; the installer registers no endpoints; approved evidence authority remains503; the retained posting code calls Legacy post_txn_group. Active TrackF deliberately uses its independent flat setup/quote. Reusing the isolated candidate would require new approved evidence/native-consumer integration and opening guards, explicitly prohibited here. C concerns that absent operational integration, not absence of all model/source code. Existing TrackF APIs work; no lossy mapping, new form or guard opening is introduced.
- H2 review history and physical cash-custody independent read contracts are absent. Their UI stays explicit about this; operational pending rows are not authoritative financial statements.
- store_delivery_settlement_routes retains Legacy identity reads in an operational-only/pending_mz2_driver_balance_link contract. It is not one of the converted financial paths above. Dormant shipping_evidence.assert_file_unlinked also contains a Legacy lookup, with no external deletion-helper caller found in this audit. Neither is evidence that the converted native writer chain falls back; neither permits an application-wide zero-Legacy assertion.
- Historical opening/ledger/P02 routes remain quarantined by existing transition, phase and write guards. Their presence must not be erased or used as a fallback.

## Acceptance decision

Financial source wiring audit: no remaining converted-path Legacy fallback found. Final acceptance is still BLOCKED: native POS source, Stage7 complete contract, complete business UAT/physical approval, production Smoke B and governed Release Readiness are distinct unresolved gates. Isolated assertions that a gate stays closed are not live acceptance.

Onboarding now truthfully distinguishes connected source writers from unverified production. ready_for_live_post=false, stage_16_locked=true, production_verified=false, Smoke B environment hold, owner authorization, opening, pause, P02 and G47 holds remain. Root tests:50 backend and30 frontend passed for metadata correction, with zero readiness mutations. Write-control, writer-transition, atomic and production-release-guard files remain unchanged from the supplied baseline0a9f1ac.

Production financial writes=0; Merge to Production/Deploy/Opening Post/Activation=NO; write-control unchanged. Synthetic disposable test fixtures do not constitute production activation or production opening posting.
