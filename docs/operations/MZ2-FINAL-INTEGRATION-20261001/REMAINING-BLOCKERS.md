# Cutover blocker classification — current authorization

**Latest authorization: MZ2_C_IMPLEMENTATION_AUTHORIZED.** The user explicitly
authorized cumulative closure of all five C gates, beginning with
`rich_shipping_approval`, while retaining zero Production financial writes and
all economic/identity/write-control guards. The older STOP AT C and historical
scope statements below are superseded, not permission to fabricate acceptance.
See [C-IMPLEMENTATION.md](C-IMPLEMENTATION.md) for the current source contract.

| C gate | Current implementation / evidence state |
|---|---|
| rich_shipping_approval | IMPLEMENTED: Native review authority/setup CAS/rich fee and Stage7 UI reuse delivered contracts. Root148 backend +529subtests and53 frontend tests pass; f3be64352 CI39/39 PASS; connected browser5/5 + unchanged default23/23 PASS after mobile wrapping fix, new exact-head CI pending |
| complete_h2_review_history | IMPLEMENTED: native-only sealed revision history, verified journals/reversals, owner filters/cursors, coverage gaps and H2 UI. Root94 backend/43 UI PASS; actual C2 browser6/6 PASS with full unchanged DB fingerprint; exact-head CI pending |
| native_physical_cash_reconciliation | AUTHORIZED DRIVER SOURCE: actual cash confirmed by driver at delivered; immutable evidence and explicit handover matching in progress. Earlier accountant attestation proposal superseded; no historical backfill or new financial writer |
| smoke_b_proof | Acceptance/Preview authorized conditionally; existing safe 423 probe exists, but delivered Production-only acceptance/proof consumption contract requires reconciliation. No Smoke B PASS claimed |
| full_16_stage_business_uat | NOT PASS; actual complete business evidence remains required |

No C is yet certified closed. Release Ready=NO. Frozen PR1236 remains Draft;
successor source work is Draft PR1237. Production/base/rollback:
`901568ccaaf510dc1f84d9c28f38d368d07dc64d`. Production writes=0;
Merge/Deploy/Opening Post/Activation=NO; write-control unchanged.

**Current A/B closure review:** PR #1232's exact source has one additionally
reproduced B gap: stale courier evidence can post a new fee after a carrier
change. The delivered #1234 adapter and necessary #1231 operational delta are
integrated; seven focused suites pass 159 tests. See
[AB-CLOSURE-REVIEW.md](AB-CLOSURE-REVIEW.md). Full regression, fresh scoped SSOT
and exact-source CI remain pending at source freeze; final results are in the
canonical Issue #1006/successor PR checkpoint. The only held C/acceptance items
are rich_shipping_approval, complete_h2_review_history,
native_physical_cash_reconciliation, smoke_b_proof and
full_16_stage_business_uat. Historical tables below are not a new authorization
or a claim that previously closed native writers are missing. Release Ready=NO.

**Latest B regression correction:** the broad run on reviewed `ba743862` found
two transient commit conflicts in the original Track E concurrency tests
(1769passed/2failed plus900subtests). The existing transaction boundary now
retries only labelled, definitely aborted server transactions with finite
admission/attempt limits; unknown commit outcomes are never blindly replayed.
See [COMMIT-RETRY-INTEGRATION.md](COMMIT-RETRY-INTEGRATION.md). Fresh focused/full
regression and the new governed A/B pair are required; old green CI is not proof
for this changed source. The C/acceptance boundaries below remain BLOCKED.

**Authoritative POS correction:** the user accepts the existing manual accountant
review of a bound POS receipt; an external processor API/source is not required.
POS is now existing-path wiring, not an independent C source blocker. The user
approved explicit selection of the existing documented
`asset / exact typed-fact identity / other_receivable` contract, with no default.
See [MANUAL-POS-WIRING.md](MANUAL-POS-WIRING.md) for the actual adapter, audit,
separate journal cycle and verification. Earlier processor-source statements
below are historical and superseded by this decision. Final exact-source test
and CI outcomes remain in STATUS and the canonical Issue1006 checkpoint.

Baseline: `0a9f1ac30d2b1a3e8aee21aab1da0ae364a74697` / tree `c8421a4cfc434ca747727d4673f25766d4ba2fef`. The latest user instruction authorizes completing missing V2 producers/adapters for **existing operational behavior**. It supersedes the earlier wiring-only scope below. Implemented adapters and their tests are recorded below; release remains **BLOCKED** while complete acceptance gates are unresolved. No new business feature is authorized.

| Existing operation / route (P defined below) | Legacy source at baseline | Delivered MZ2 receiving contract | Missing integration only | Class / basic accounting relevance |
|---|---|---|---|---|
| Sale: P/receivables/execute, P/order-recognition/recognize-ready | receivable_service._execute_transaction; order_recognition.execute_order_recognition → post_txn_group | sealed post_journal_v2 + existing recognition event, original tax, payment_gateway receivable | exact event key/legs/date, verified native duplicate linkage | A/C; core |
| Transfer receipt / delivery: P/bank-transfer-receipts/{id}/approve, /convert-delivered | bank_transfer_receipts.approve_receipt / _post_sale_from_advance | Track A bank identity + native journal + existing arrival and delivery evidence | native receipt and liability-to-sale adapters in same transaction | A/B/C; core |
| Customer advance: P/customer-advances and cancel/payments | customer_advances.post_group | native journal + existing no-tax advance/refund liability economics | native helper, verified journal replay, execution duplicate checks | A/C; core |
| Refund entitlement: P/customer-refunds/{id}/recognize | refund_entitlements.recognize_entitlement | native journal + existing original-sale tax split and payable | deterministic entitlement adapter and verified original journal | A/C; core |
| Refund execution: P/customer-refunds/bank-payments/{id}/approve | customer_refunds.post_bank_payment | native journal + Track A bank + provider execution contracts | native payment adapter and native-only duplication/reconciliation | A/B/C; core |
| Provider statement: P/settlements/drafts/{id}/post | settlement_service._post_reviewed_settlement_transaction | native journal + existing signed fee/refund/receipt preview | native posting, immutable audit, exact bank receipt consumption | A/C; core |
| Employee finance: P/payroll/accrue, /movements/{id}/classify | employee_finance._post_accrual / classify | Track B employee/contract identities + native journal | existing accrual/pay/advance/custody legs and native sources | A/B/C; core |
| Driver bank/POS: /api/store-delivery/payment-review/{id} | no Legacy fallback; stub destination port | Track F driver writer + typed verified destination | original native bank source verification/consume now implemented; no POS processor-success source exists | Bank B: FIXED + tested; POS: NEW_SCOPE_REQUIRED below |
| Advertising: /api/accounting-module/advertising-v2/bank-movement | no Legacy fallback; bridge409 after identity | Track E bank_movement_legs + wallet original units + sealed journal | verified original movement + separate fee proof + atomic posting/consume | A/B; existing finance |
| Expense: P/daily-movements/{id}/classify-outgoing | daily_movements.classify_outgoing_movement → post_txn_group | native journal + Track A bank and approved expense classes | movement-derived event and native legs | A/C; core |
| Supplier payment: same classify-outgoing route | daily_movements Legacy supplier lookup/post | delivered C1 settle(PaymentIn) + canonical C supplier | bind canonical supplier and exact payable-only event, consume same transaction | B/C; core |

No entry is classified NEW_SCOPE_REQUIRED merely because a native producer was absent from a frozen track. A genuinely absent operational source/behavior must be proved separately; it cannot be used to declare readiness if necessary for basic accounting. Production writes=0; Deploy/Opening Post/Activation=NO; write-control unchanged.

## Current implementation and acceptance

All former `post_txn_group` financial paths below now use the delivered sealed `post_journal_v2` core through thin adapters; no new journal storage engine, default financial account, economic rule, Legacy fallback or write-control change was introduced. Missing identities and invalid/reversed native evidence fail closed. Former Legacy target was `general_ledger`; native producer tests use isolated real Mongo and deliberate Legacy sentinels.

| Classified gap | Integration delivered | Fresh acceptance tests (backend/tests) | Status |
|---|---|---|---|
| Sale/order A/C | `accounting_recognition_native` preserves original recognition event, tax and effective date | `test_mz2_receivable_workflow`, `test_mz2_order_recognition`, `test_mz2_recognition_cutover` | FIXED + tested |
| Bank receipt/delivery A/B/C | `accounting_customer_native` + existing arrival, exact bank, advance and delivery contracts | `test_mz2_bank_transfer_receipts`, `test_mz2_bank_cod_cutover` | FIXED + tested |
| Customer advance A/C | Existing capture/cancel/payment calls use native customer adapter, verified event replay | `test_mz2_customer_advances` | FIXED + tested |
| Refund entitlement A/C | Native recognition adapter, original journal/tax and evidence identity verification | `test_mz2_refund_entitlements`, `test_mz2_closed_periods`; period reader includes native linked reversals at their own effective date | FIXED + tested |
| Refund execution A/B/C | Native payable/bank/provider legs, verified execution and original-journal binding | `test_mz2_daily_refunds`, `test_mz2_order_refunds`, `test_mz2_report_isolation`, `test_mz2_recognition_cutover` | FIXED + tested |
| Provider settlement A/C | Native recognition adapter, unchanged signed fee/refund/receipt economics; draft/lifecycle audit now uses native operational `mz2_settlement_audit` in the same caller transaction | `test_mz2_atomic_recovery`, `test_mz2_receipt_intake`, `test_mz2_tabby_signed_credits`, `test_mz2_settlements_p01`, `test_mz2_settlement_native_audit` | FIXED + tested (including actual HTTP audit failure rollback) |
| Employee finance A/B/C | `accounting_employee_outgoing_native`, exact Track B employee/contract, same accrual/proration/pay/advance/custody legs | `test_mz2_employee_finance`, `test_mz2_employee_outgoing_reports` | FIXED + tested |
| Driver bank B | `accounting_bank_statement_proof` verifies original preserved XLSX/hash/row/owner/amount/date; existing Track F writer consumes exact movement atomically | `test_mz2_driver_bank_evidence`, `test_mz2_driver_payment_review` | FIXED + tested |
| Advertising bank A/B | Existing Track E plan/core + separate principal/fee proof, atomic evidence consumption, FX original units; explicit foreign zero-opening evidence supported without invented FX | `test_mz2_bank_evidence_adapters`, `test_mz2_advertising_zero_opening`, `test_mz2_advertising_native_reports` | FIXED + tested |
| Daily expense A/C | Existing approved category and movement-derived event through employee/outgoing native adapter | `test_mz2_daily_movements`, `test_mz2_employee_outgoing_reports` | FIXED + tested |
| Daily supplier payment B/C | Existing C1 `settle(PaymentIn)` in same owner transaction; exact canonical supplier, payable-only policy and single movement consumption | `test_mz2_daily_movements`, `test_mz2_employee_outgoing_reports` | FIXED + tested |
| Native report/onboarding identity B | Verified customer liabilities, explicit existing expense identities and exact Track E confirmed financial binding; no external-reference guessing | `test_mz2_recognition_cutover`, `test_mz2_employee_outgoing_reports`, `test_mz2_advertising_native_reports`, `test_accounting_onboarding_ad_binding` | FIXED + tested |

## Superseded external POS source requirement — historical record

**POS processor-success ingestion: NEW_SCOPE_REQUIRED.** Existing route `/api/store-delivery/payment-review/{assignment_id}` offers `card_terminal`, but the delivered source contains only `store_delivery_payment_reviews`/collections and a bound `store_delivery_receipts` image. No native terminal/processor-success transaction producer exists. `accounting_driver_payment_port.require_driver_payment_destination` therefore returns 503 `mz2_driver_payment_destination_not_integrated` before any native journal; pending review and full driver receivable are preserved. Receipt metadata cannot substitute for processor success. Proof: `test_mz2_driver_payment_review::test_absent_verified_evidence_leaves_pending_and_full_balance[card_terminal]` and `StoreDeliveryPaymentReview.test.jsx`. Future closure requires an actual processor/terminal success source, exact receivable/destination identity and evidence-consumption adapter. This operational source is unnecessary for basic cash/bank accounting: `test_mz2_driver_bank_evidence::test_real_imported_arrival_approves_once_and_consumes_bank_movement` and existing Track F cash tests succeed independently. POS remains unavailable, not silently approved.

## Accepted governance A/B/C - current execution

The user's accepted plan now permits only reuse of existing writers/contracts and A/B integration corrections; no new writer or economic contract may be created. The financial implementations above were already present at this execution's starting checkpoint23def6e and were re-audited rather than recreated. Mixed A/B/C labels above record earlier discovery history, not authorization to implement C.

| Remaining or closed item | Final class | Status / basis |
|---|---|---|
| Existing sale, receipt, advance, refund, settlement, employee, daily expense, C1, TrackE and TrackF bank wiring | A | FIXED + existing native tests; fresh full regression recorded separately |
| Nine frontend regression suites | B | FIXED; meaningful behavior retained; full integrated235 suites/1297 tests PASS |
| Onboarding missing-writer labels and blanket bank/POS readiness | A/B | FIXED; source-connected metadata with production verification holds; root50 backend/30 frontend PASS |
| POS manual accountant review and canonical destination | A existing-path wiring | External processor-source requirement superseded by explicit user decision. Existing documented receivable selected without default; existing review/settlement writers reused. See MANUAL-POS-WIRING.md and exact-source verification |
| Stage7 full courier draft persistence | C_NEW_SCOPE_REQUIRED | BLOCKED; rich economics/model and isolated review-candidate service exist, but its save/approve are unconditionally423, installer registers no endpoints, approved evidence authority503 and retained posting sink Legacy. Active TrackF RateInput is flat and cannot represent full editor terms. Wiring the closed candidate would require prohibited guard opening plus approved native integration; no model/contract absence is falsely claimed. Existing TrackF setup API works independently |
| H2 review-history / independent physical cash-custody reads | C_NEW_SCOPE_REQUIRED | BLOCKED; complete review-history read absent. Operational cash summary exists, but no delivered authoritative reconciliation links it to native remittances; explicit UI blockers retained |
| Governed source A / intent B lineage | B | In progress; original integration history preserved and guards unchanged. Existing ancestry includes intent edits/reverts; a clean reviewed lineage is required |
| Complete16-stage business UAT / SmokeB / physical owner approval | Acceptance gate | BLOCKED; isolated23 assertions are not full business UAT or production proof. Stage7 unsupported contract and Stage16 live holds remain explicit |

Proof: test_mz2_shipping_contract_isolation tests draft/approval closed before access and installer registers no endpoints; the new real-HTTP UAT rejects all six unsupported editor fields with422 and leaves the full native setup unchanged.

Stage7's advanced draft persistence is not required by existing TrackF flat-rate setup or core journals: real HTTP courier/rate/binding setup and independent COD/fee balances pass in the expanded isolated fixture. This does not waive the user's requested complete onboarding acceptance gate.

Latest integrated runtime checkpoint a58624ccb13c1e24319be6d1cac27c8558489ada; prior exact241055 CI37 workflows:36success/1ReleaseReadiness failure. Broad backend result and fixture/platform closure are recorded in STATUS.json; root23 UAT assertions passed with business limits retained. Next governed candidate receives its own exact-head CI; no pending result counts as passing. Historical external live-HTTP/shared-DB tests remain unchanged and unexecuted; their explicit inventory prevents them being silently counted in isolated regression.

The following historical record describes earlier immutable sources and superseded authorization; its missing-writer conclusions are not the current implementation status.

## Historical wiring-only register (superseded scope, retained evidence)

Source inspected: `a8fbeac75356a5d0f6376c457c498f1afbdd96d6`, 2026-10-01. This register covers the current **existing-writer wiring only** scope. No writer, economics, evidence rule, Legacy fallback or write-control change is authorized by this document. Source locations below were freshly traced. Prior runtime evidence is labelled historical; new customer/bank-receipt/expense probe results were executed on source `9d0c41b1d0d6f5e51c337dc51e3dddea1e31a25c`, whose only production changes are shipping connection metadata/UI. Financial writer sources are unchanged.

The eleven frozen delivered heads and unchanged-file comparisons are recorded in [DELIVERED-WRITER-PROVENANCE.md](DELIVERED-WRITER-PROVENANCE.md). They contain no replacement native producer for the Legacy-only operations below. A generic `post_journal_v2` API is not a delivered domain writer. Legacy posting reaches `ledger_core.py:259–260` and is denied by `accounting_writer_transition.py:118–119` with **423 `accounting_legacy_writer_disabled`** after `v2_active`; its target collection remains `general_ledger`. That guard must remain enabled.

## Exact consuming routes

`P` = `/api/financial-provider-apps/accounting-module`. Financial-provider installers append routes with that prefix; driver child routes are appended flat by `order_engine/__init__.py`, and advertising is installed directly under `/api` in `server.py`.

| Operation | Registered route (POST) |
|---|---|
| General sale / order recognition | `P/receivables/execute`; `P/order-recognition/recognize-ready` |
| Bank receipt / delivered advance conversion | `P/bank-transfer-receipts/{review_id}/approve`; `P/bank-transfer-receipts/convert-delivered` |
| Customer advances | `P/customer-advances`; `P/customer-advances/{advance_id}/cancel`; `P/customer-advances/{advance_id}/payments` |
| Refund entitlement | `P/customer-refunds/{case_id}/recognize` |
| Refund payment | `P/customer-refunds/bank-payments/{payment_id}/approve` |
| Provider settlement | `P/settlements/drafts/{draft_id}/post` |
| Employee finance | `P/payroll/accrue`; `P/payroll/movements/{movement_id}/classify` |
| Driver bank/POS approval | `/api/store-delivery/payment-review/{assignment_id}` |
| Advertising funding/payment | `/api/accounting-module/advertising-v2/bank-movement` |
| Daily expense / supplier-payment classification | `P/daily-movements/{movement_id}/classify-outgoing` |

## Actionable register

All source paths in this table are under `backend/`; test paths under `backend/tests/` unless otherwise stated. **Direct** means the named operation's stopping condition was actually executed. **Prerequisite** means the test stopped before reaching that operation. **Source only** is not a claimed runtime failure.

| Remaining path | Current function and exact stop | Why existing delivered wiring cannot complete it | Strongest proving evidence and limit | Required future closure |
|---|---|---|---|---|
| General Salla/provider/non-COD sale | `accounting_receivable_service.py:153` `_execute_transaction`, Legacy call at 182. Order-export route: `accounting_order_recognition.py:367` `execute_order_recognition`, Legacy call at 448. Both ultimately hit 423 above. | Managed BNPL → durable ingress → receivable `execute` is already connected. No frozen native general-sale producer exists. Native delivered COD recognition has a different source/responsibility contract. | **Direct receivable failure:** `test_mz2_receivable_workflow.py::WorkflowTests::test_new_sale_to_existing_settlement_service_and_balance_guard` and `test_partial_full_refund_uses_original_rate` fail initial sale423 in historical source `3bf348a2` CI. Separate order-export route is **source only** here; its existing `test_mz2_order_recognition.py::MZ2OrderRecognitionTests::test_tabby_needs_provider_capture_then_posts_same_order` is an acceptance target, not new native-success evidence. | Separately deliver/authorize general sale producer with original tax/evidence, native duplicate linkage, atomic replay and V2-only acceptance tests. |
| Bank-transfer receipt and delivered advance conversion | `accounting_bank_transfer_receipts.py:647` `approve_receipt`, Legacy call774; `_post_sale_from_advance`533, Legacy call600. | Canonical bank identity is delivered; receipt/advance/sale posting remains Legacy. No substitute native domain writer was delivered. | **Direct approval failure (new):** `customer_blocker_probe.py`, attempt `bank_transfer_receipt_approval`, returns423 with verified native opening, real Salla XLSX/receipt draft, canonical binding and real bank movement; every persisted document unchanged. Delivered conversion is **source only** because receipt approval cannot commit. Existing downstream target: `BankTransferReceiptTests.test_delivery_reupload_preserves_review_then_converts_advance_to_sale`. | Approved native receipt and conversion producer, exact bank movement consumption, delivery evidence, no duplicate advance/sale, and native-opening end-to-end tests. |
| Customer advance capture/cancellation/payment | `accounting_customer_advances.py:53` `post_group` calls Legacy at55; callers `recognize_advance`70, `cancel_advance`130, `pay_advance`158. | Existing shared helper and routes are present; helper is not a V2 writer. | **Direct capture failure (new):** `customer_blocker_probe.py`, attempt `customer_advance_capture`, returns423 with verified native opening and captured-payment facts; every persisted document unchanged. Cancellation/payment are **source only** because capture cannot commit. Existing downstream target: `AdvanceTests.test_capture_cancel_partial_refunds_duplicate_and_no_tax_or_sales`. | Native advance producer preserving no premature sale/tax, remaining balance, cancellation/payment evidence, transactional idempotency and V2-only tests. |
| Customer refund entitlement | `accounting_refund_entitlements.py:11` `recognize_entitlement`, Legacy call46. | Salla refund observation intentionally creates review drafts, not financial entitlement. Native entitlement producer was not found. | **Prerequisite failure:** `WorkflowTests.test_partial_full_refund_uses_original_rate`; `test_mz2_closed_periods.py::ClosedPeriodTests::test_closed_entitlement_and_payment_no_partial_or_automatic_redating` stops at initial sale423. Entitlement itself is **source-traced**, not independently reached in those failures. | Native entitlement tied to verified original sale/tax, refund case, remaining refundable amount, original-date/period controls and direct native-book tests. |
| Customer refund payment | `accounting_customer_refunds.py:139` `post_bank_payment`, Legacy call242. | Existing reviewed-payment service has no delivered native replacement. A refund notification or selected bank account does not establish executed payment. | **Prerequisite failure:** `test_mz2_report_isolation.py::ReportIsolationTests::test_actual_refund_month_end_partial_and_final_payment_with_legacy_sentinels` fails its initial sale; no partial/final refund payment was reached. Sink is **source-traced**. | Native payment producer preserving entitlement balance, verified execution evidence, partial/final payment, anti-duplication, rollback and report isolation tests. |
| Provider settlement | `accounting_settlement_service.py:426` `_post_reviewed_settlement_transaction`, Legacy call558. | Canonical bank identity is connected. Delivered native **shipping** settlement cannot replace provider statement/refund/fee reconciliation. | **Prerequisite failure:** `WorkflowTests.test_new_sale_to_existing_settlement_service_and_balance_guard` fails sale before settlement. Settlement sink is **source only** in that result. | Native provider-settlement producer with reviewed statement, fee policy, original receivable/refund reconciliation, exact bank receipt and direct native acceptance. |
| Payroll accrual and employee money movements | `accounting_employee_finance.py:189` `_post_accrual`, Legacy call260; `classify_employee_movement`403, Legacy call644. | Employee identity/contracts are delivered and routes are registered. Frozen employee PR explicitly excludes native payroll migration; these files were not lost during integration. | **Direct service blocker probe:** committed `payroll_blocker_probe.py:89–116` in this evidence directory uses verified native opening and real imported daily movement; accrual, salary payment, advance grant and custody grant each return423 with all documents unchanged. Historical execution is recorded in WIRING-VERIFICATION; exit0 means blocker assertions passed. Other employee actions are **source-traced**, not independently probed. | Approved native accrual/classification producers preserving employee contract/proration, payable/advance/custody separation, movement consumption, periods and direct native-book tests. |
| Driver bank-transfer / POS payment approval | `accounting_driver_payment_port.py:32` `require_driver_payment_destination`, unconditional **503 `mz2_driver_payment_destination_not_integrated`** at43. | Native review/settlement writer exists. Missing piece is verified destination proof: exact owner, destination, SAR amount, receipt hash/reference, source namespace/record/revision; bank arrival plus native movement, or successful canonical POS transaction/receivable. Bank selection and receipt upload do not satisfy this contract. No delivered production proof resolver was found. | **Direct blocked-port test:** `test_mz2_driver_payment_review.py::test_absent_integration_leaves_pending_and_full_balance`; actual HTTP canonical-bank selection case `test_driver_bank_selection_uses_canonical_owner_active_sar_bank_without_approving_payment` preserves pending and returns503. Successful `test_accept_exact_destination_once_no_fee_netting` uses the explicit destination fixture; consumer success is not production proof verification. | Deliver an adapter under the existing `DriverPaymentDestination` contract and test unmocked bank/POS evidence through approval, single consumption/replay, receipt revision and rollback. No new journal writer needed, but inventing a proof resolver is outside wiring-only scope. |
| Advertising bank wallet funding / payable payment | `accounting_advertising_bridge.py:137` `bank_movement`, unconditional **409 `track_a_bank_evidence_and_posting_integration_required`** at146 after real canonical identity resolution. | Native spend writer and pure `bank_movement_legs` plan exist. Funding/payable-payment verification, posting and one-time evidence-consumption orchestration do not. A plan or caller `bank_evidence_id` is not a delivered producer. | **Direct HTTP barrier:** `test_mz2_advertising_v2.py::test_canonical_bank_reaches_only_the_remaining_evidence_posting_barrier` for both kinds; 409, no native journal/posting and unchanged transaction revision. `test_funding_and_settlement_are_transfers_and_fee_is_separate` proves only plan economics. | Explicitly delivered/authorized evidence-to-posting orchestration preserving actual outbound amount, separate fee evidence, FX units, owner binding, atomic consumption and retry tests. Existing identity connection alone cannot enable posting. |
| Daily general expense classification | `accounting_daily_movements.py:926` `classify_outgoing_movement`, action `expense`, Legacy call1108. | Active route at1349, registered by `financial_provider_apps.py:148` and offered by `AccountingDailyMovements.jsx`; not quarantined. No delivered native general-expense producer. Module is byte-identical to frozen Track A (`git diff 92eda46c HEAD -- backend/accounting_daily_movements.py` exit0). | **Direct new service probe:** `customer_blocker_probe.py`, attempt `daily_general_expense_rent`, real imported outflow and verified native bank opening ->423; every persisted document unchanged. Older `DailyMovementTests.test_general_expense_posts_from_actual_outgoing_movement_once` uses the old ledger fixture and is not native acceptance. | Separately deliver/authorize native expense producer and expense identity/evidence contract; preserve actual movement consumption, periods, replay and rollback. |
| Daily movement classified as supplier payment | Same `classify_outgoing_movement`, action `supplier_payment`; Legacy supplier lookup906 and posting1108. | C1 native supplier writer **exists**, but no delivered adapter maps this route’s movement-derived event/replay and atomic consumption into C1. C1 PaymentIn has operation/date/bank/invoice/advance fields, no movement consumption contract; substituting it blindly can duplicate or consume evidence incorrectly. | **Source only**, not a newly executed supplier-classification failure. Existing `DailyMovementTests.test_supplier_payment_reduces_existing_payable_and_blocks_overpayment` uses old ledger/identity fixtures; identity tests mocking the sink are not native acceptance. | Deliver the bridge contract for explicit canonical supplier, payable-only policy, movement-derived operation identity, replay and atomic single consumption; use existing C1 writer, not a new journal writer or Legacy fallback. |

## Evidence identity and interpretation

- Historical exact-source CI is `3bf348a236de0c04c6bea40926f19fe2111ec8e0`: [Accounting failure](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36813526628/job/110213532750), [refund report prerequisite failure](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36813526517/job/110213532121), [closed-period refund prerequisite failure](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36813526484/job/110213531550). Logs and totals are retained in [WIRING-CI-FAILURES.md](WIRING-CI-FAILURES.md). Skipped downstream work is not a pass.
- Historical local follow-up reproduced three initial-sale failures in4.16s and executed the payroll blocker probe. Driver/shipping/identity215 tests plus6 subtests and advertising253 tests passed, with the absent-adapter/remaining-barrier assertions included. Those passes prove their specified contracts and blocking behavior, not complete financial acceptance. Exact logs/hashes are in [WIRING-EVIDENCE.json](WIRING-EVIDENCE.json).
- Old tests may fail before domain behavior because their fixture does not supply verified native opening. Such setup failures are not evidence that a domain writer is absent. This register uses explicit source traces and labels the actually reached failure. No existing tests were weakened or renamed to imply closure.

## Functional delivered paths and current metadata repair

Track F native delivered COD sale recognition, delivery fee accrual and shipping settlement exist; driver operational delivery now reaches its delivered observer. Supplier native invoice/payment and advertising native spend also exist. They must not be categorized as wholly missing or repurposed for other economics.

At the inspected source, `accounting_shipping_native_routes.py:53` still reports `bank_port.ready=false` with `mz2_shipping_bank_port_not_integrated` despite the connected Track A shipping identity port; the integration owner is repairing this stale readiness/UI connection. This is a genuine metadata wiring seam, **not** the driver verified-destination blocker at the following line. Its repair cannot claim bank arrival/POS proof, enable advertising funding, or waive any opening/period/pause gate. The completion addendum below records its implemented correction and executed tests.

This bounded read-only trace found no additional delivered replacement for the blocked domain producers. It is not an exhaustive audit of unknown/unpublished branches. The initial register was source-only; the subsequent runtime addendum below records the new bounded blocker probe. No financial writer was changed. Production writes, merge, deployment, Opening Post and activation: **none**. Complete business acceptance and release readiness remain blocked; the detailed nonfinancial release/UI/UAT constraints remain in the existing verification reports.

## Shipping connection correction

The stale bank-port status described above is now repaired in the current patch: the context reports the connected Track A resolver and H2 hides only the obsolete bank-wiring message. Backend HTTP regression:3 failed before ->3 passed after. H2:1 failed/15 passed before ->16 passed after. Actual settlement still returns409 for missing bank identity and423 for paused writes, with financial documents, movement and owner revision unchanged. Driver proof stays blocked, P02 activation gate remains locked and no write control is modified. Full affected regression:218 passed plus6 subtests in139.64s. H2 full:45 passed in5 suites. Independent source/test review found no material issues. Exact-source CI is recorded in the completion evidence.

## New direct blocker probe

Runner [customer_blocker_probe.py](customer_blocker_probe.py), source `9d0c41b1d0d6f5e51c337dc51e3dddea1e31a25c`; log [customer-blocker-probe.log](evidence/customer-blocker-probe.log). Run with `PYTHONPATH=backend;backend/tests` and `MZ2_TEST_MONGO_URI` pointing only at the task-owned loopback replica, then `.venv/Scripts/python.exe docs/operations/MZ2-FINAL-INTEGRATION-20261001/customer_blocker_probe.py`. Root independently reran it: exit0 means all three expected423/no-mutation assertions succeeded, not business acceptance. No mocks, historical activation or invented posted prerequisite records. Scratch database removed. Cancellation, refund payment and delivered conversion were explicitly not executed because prerequisite capture/approval cannot commit.

## Reviewed-source candidate B follow-up

Independent candidate verification exposed a first-use concurrent driver review failure: the review was approved but the same snapshot could not resolve its newly created event collection. The existing Track F event storage is now provisioned by the public startup index initializer, before business readiness. No writer, accounting semantics, idempotency guard or transaction core changed. The original concurrency assertions remain. Direct production-bootstrap and repeat-initialization tests preserve all paused/draft controls and data. Focused4 tests and20 fresh-database concurrency scenarios pass; root combined108 tests also pass. Broad131-file regression and exact-source CI determine final acceptance. See CANDIDATE-SHIPPING-BOOTSTRAP-REPAIR.md and the canonical Issue1006 checkpoint. This is B, not new financial scope. All C and live acceptance holds above remain.
