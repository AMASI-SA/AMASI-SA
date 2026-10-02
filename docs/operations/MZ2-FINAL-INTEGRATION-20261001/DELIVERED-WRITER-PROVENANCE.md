# Delivered writer provenance — 2026-10-01

## Scope and conclusion

Read-only source/history audit against integration checkpoint `a53eb99178e92fe5d1d698fe8bf7e5a150239c6e`, plus the eleven frozen delivered PR heads below. This report authorizes no new writer and contains no implementation. The excluded `native-writers-WIP.patch` was not reapplied.

General non-COD/provider sale recognition, customer refund entitlement/payment, provider settlement, bank-transfer receipt/advance conversion and payroll still invoke the legacy journal writer. No delivered native replacement for these operations was found in the audited frozen source trees. Their existing dispatch paths are present; changing the sink and associated domain contracts would be implementation, not merely restoring a dropped hook.

There is a significant narrower exception: Track F already delivers native **COD sale recognition** from canonical delivery evidence, together with shipping fee and settlement writers. Thus “all sales lack a native writer” would be inaccurate. That writer is not a generic card/provider/prepaid sale, customer-refund or payroll adapter.

The integration owner is separately repairing confirmed existing driver-review and advertising bank-identity seams. Those working-tree changes were pending during this audit; this document does not claim final CI, deployment or production closure.

## Frozen source provenance

PR metadata was read from GitHub and each complete backend tree searched locally at its exact SHA. Searches covered native API names, private native posting paths and native storage names, not only filenames containing “native”.

| Delivered PR | Track | Frozen head |
|---|---|---|
| [1209](https://github.com/AMASI-SA/AMASI-SA/pull/1209) | Employee identity | `c005315245cc69afa036a30286a1fdd059b76f46` |
| [1212](https://github.com/AMASI-SA/AMASI-SA/pull/1212) | A financial identity | `92eda46c71e039800a7e91db8a814e5b86161b74` |
| [1213](https://github.com/AMASI-SA/AMASI-SA/pull/1213) | Dependencies | `8da0e56afa4c7c696c7e2a48ed63ad779828616a` |
| [1214](https://github.com/AMASI-SA/AMASI-SA/pull/1214) | D | `18f615806bb65f00bd44f277abe80c52f5c2943a` |
| [1215](https://github.com/AMASI-SA/AMASI-SA/pull/1215) | C1 supplier payments | `49cf4fa967fa17792b45d94613e5ba89111c2b60` |
| [1216](https://github.com/AMASI-SA/AMASI-SA/pull/1216) | C2 supplier invoices | `d1b1b79135fbc9f82c12dc01323d80521383ebc2` |
| [1217](https://github.com/AMASI-SA/AMASI-SA/pull/1217) | H | `be1540582c32760c6308c975d6ebf6308feeb7aa` |
| [1218](https://github.com/AMASI-SA/AMASI-SA/pull/1218) | E advertising | `8c91ab2a9faa59978ac6ccdb4b0818e3c65d1344` |
| [1219](https://github.com/AMASI-SA/AMASI-SA/pull/1219) | G quarantine | `41cfa2b55ee3398190cc4e37395ba297f9ce49e4` |
| [1220](https://github.com/AMASI-SA/AMASI-SA/pull/1220) | F shipping | `6ee615508fdb0153a626341cbaeee08ca247ddf7` |
| [1221](https://github.com/AMASI-SA/AMASI-SA/pull/1221) | H2 | `3e4113cec75f6f10f81956e34f3d858ed20bc675` |

For each SHA, the audit used `git grep -l -E 'post_journal_v2|_post_prepared_v2|accounting_general_ledger_v2' <sha> -- backend ':!backend/tests'`. The employee/A/dependencies/D/H/G/H2 trees contain the ledger core and the existing purchase-receiving/supplier-payment callers. C1 additionally contains `accounting_supplier_payments_v2`; C2 `supplier_native_invoice_v2`; E the two advertising callers; F `accounting_shipping_native`. None contains a general Salla/customer-refund/payroll native producer. Opening was additionally traced through its separate `post_opening_journal_v2` API.

This is a claim about these eleven delivered heads and the integration checkpoint, not about unknown unpublished work.

## Actual native producers

Source locations are repository-relative and refer to the checkpoint unless stated otherwise. The native ledger core is an infrastructure API; its availability alone does not supply a domain writer.

| Producer | Actual native call | Delivered behavior |
|---|---|---|
| Financial opening | `backend/accounting_financial_accounts.py:1177`, `post_opening_journal_v2` | Controlled opening journal |
| Purchase receiving | `backend/purchase_receiving_service.py:424`, `post_journal_v2` | Purchase/receiving journal |
| Supplier payment | `backend/supplier_payment_service.py:320`, `post_journal_v2` | Supplier payment journal |
| Supplier payment V2 | `backend/accounting_supplier_payments_v2.py:404`, `post_journal_v2` | Reviewed supplier payment |
| Native supplier invoice | `backend/supplier_native_invoice_v2.py:185`, `post_journal_v2` | Supplier invoice |
| Advertising automation | `backend/accounting_advertising_automation.py:165`, `post_journal_v2` | Advertising automation posting |
| Advertising bridge | `backend/accounting_advertising_bridge.py:114`, `post_journal_v2` | Native spend; caller/evidence seam separately under repair (working-tree call shifted to line 115) |
| Shipping | `backend/accounting_shipping_native.py:71–73`, `_post` → `post_journal_v2` | COD recognition, delivery fee accrual, shipping settlement |

`accounting_shipping_native.py:90` (`recognition_legs`) supports first-sale recognition or exact custody reclassification. `recognize_cod` at line 132 seals canonical delivery evidence and posts via `_seal_delivery` (line 182); first sale credits sales/VAT and recognizes courier/store-driver COD receivable. `recognize_fee_delivery` at line 137 allows non-COD delivery evidence for the fee flow, not general prepaid/provider sale recognition. `accrue_fee` starts at line 193 and `settle` at line 226. The delivered source hook is already present at `backend/fulfillment_v2_routes.py:1039–1040`, calling `accounting_shipping_native_observer.observe_delivery` after source commit.

## General Salla/payment/refund paths: existing wiring, legacy target

| Existing operation / alias | Actual source chain | Assessment |
|---|---|---|
| Managed BNPL/provider sale | `backend/bnpl/ledger_bridge.py:109–116` → `accounting_ingress.ingest(kind="sale")`; `backend/accounting_ingress.py:49–50` → receivable `execute`; `backend/accounting_receivable_service.py:181–182` → `ledger_core.post_txn_group` | Dispatch exists. Native domain writer absent in audited delivered sources. |
| Salla order-export recognition | `backend/accounting_order_recognition.py:367` (`execute_order_recognition`), legacy call at 448; router `/accounting-module/order-recognition`, `/recognize-ready` | “MZ2-native” module description does not mean V2 journal storage. Writes `bnpl_sale` through legacy core. |
| Bank-transfer receipt approval / delivered conversion | `backend/accounting_bank_transfer_receipts.py:647` (`approve_receipt`), legacy call at 774; `_post_sale_from_advance` at 533, legacy call at 600; `convert_confirmed_deliveries` at 926 | Canonical account binding is delivered; native receipt/sale journal conversion is not. |
| Customer advance recognition/cancel/payment | `backend/accounting_customer_advances.py:53–55` (`post_group`) → legacy core | Existing domain routes share legacy helper. |
| Salla refund notification | `backend/bnpl/ledger_bridge.py:189–195` → `backend/accounting_order_refunds.py:14–15` → `accounting_refund_drafts.observe_refund` | Deliberately creates/updates nonfinancial refund review evidence; it is not a payment or journal producer. |
| Refund entitlement | `backend/accounting_refund_entitlements.py:45–46` → legacy core | Native entitlement writer absent in audited delivered sources. |
| Customer refund payment | `backend/accounting_customer_refunds.py:225,242` → legacy core | Native payment writer absent in audited delivered sources. |
| Provider settlement | `backend/accounting_settlement_service.py:426` (`_post_reviewed_settlement_transaction`), legacy call at 558 | Canonical destination identity exists; native provider-settlement writer absent. Distinct from delivered native shipping settlement. |

Unmanaged BNPL bridge paths also explicitly call legacy core (`ledger_bridge.py:148–154` for sale; 232–238 for refund). There is no hidden native dispatch there. Durable ingress/refund drafts are useful existing evidence flows, but do not themselves close a posting gap.

The following exact comparison produced **no diff** for all six paths, proving these retained source files were not replaced by an older implementation during integration:

```text
git diff 92eda46c71e039800a7e91db8a814e5b86161b74 a53eb99178e92fe5d1d698fe8bf7e5a150239c6e --
  backend/accounting_order_recognition.py backend/accounting_bank_transfer_receipts.py
  backend/accounting_receivable_service.py backend/accounting_customer_refunds.py
  backend/accounting_refund_entitlements.py backend/accounting_settlement_service.py
```

## Payroll provenance

`backend/accounting_employee_finance.py:28` imports legacy `post_txn_group`. `_post_accrual` at line 189 calls it at line 260 (`mz2_salary_accrual`). `classify_employee_movement` at line 403 calls it at line 644 (`mz2_employee_<action>`), covering salary payment, advances/recovery and custody. The routes `/accounting-module/payroll/accrue` and `/accounting-module/payroll/movements/{movement_id}/classify` already reach these functions. Employee identity, employment/contract status and proration are delivered; native payroll journal posting is not.

This exact comparison produced **no diff**:

```text
git diff c005315245cc69afa036a30286a1fdd059b76f46 a53eb99178e92fe5d1d698fe8bf7e5a150239c6e -- backend/accounting_employee_finance.py backend/employee_payroll_status.py
```

PR1209's description explicitly scopes the delivery to employee identity enforcement and excludes complete native-book migration of payroll producers. Track A's checked-in `docs/operations/MZ2-FINANCIAL-IDENTITY-FOUNDATION/CONTRACT.md:56–58` likewise says that the track does not port writers and retains existing writer/history behavior. Canonical bank/cash identity resolution is not authorization to reinterpret that delivery as a native payroll writer.

## Existing adapters versus missing orchestration

These are distinct from the missing legacy-to-native domain writers:

| Gap | Existing delivered pieces | Missing piece and scope limit |
|---|---|---|
| Driver bank/POS approval | `accounting_driver_payment_review.review_driver_payment` already calls native shipping `_post`; `DriverPaymentDestination` is defined in `backend/accounting_driver_payment_port.py:13–29`. | Production `require_driver_payment_destination` still raises 503 `mz2_driver_payment_destination_not_integrated` at line 43. Requires verified bank arrival or successful canonical POS evidence tied to exact owner, destination, SAR amount, reviewed receipt hash, reference and immutable source namespace/record/revision. Bank proof must identify the consumed native daily movement; POS proof must identify a successful processor transaction and POS receivable, not a bank tuple. Restoring an early review hook does not create this proof adapter. |
| Advertising bank funding / debt payment | Native `post_spend`, canonical Track A financial identity resolver, and pure `accounting_advertising_contract.bank_movement_legs` plan already exist. | `accounting_advertising_bridge.bank_movement` unconditionally raises `track_a_bank_evidence_and_posting_integration_required` after identity resolution. No delivered funding/payable-settlement evidence-verification, posting and consumption orchestration was found. The owner's pending identity connection can resolve the first seam only. |

Existing bank-side inputs include `mz2_daily_movements`, import identity in `mz2_daily_movement_files`, and original bytes/provenance in `accounting_source_files`. They are reusable inputs, not an existing resolver that emits the complete driver destination proof. No production canonical successful-POS producer for that seam was located. Advertising `BankMovement` supplies `bank_evidence_id`, amount, timestamp, bank identity, optional separately evidenced fee and original-wallet FX units; its pure plan does not verify or consume that evidence. Wiring-only authority cannot justify inventing either missing verification adapter or advertising funding orchestration.

Recorded contract identifiers remain exactly those in source: the ledger operation is `MZ2-FIN-CUTOVER-001` (`accounting_ledger_v2.py:32`), native journal groups/entries/audit are `accounting_journal_groups_v2`, `accounting_general_ledger_v2`, `accounting_audit_log_v2` (lines 27–29); shipping `SOURCE` is `mz2_shipping_native_v1`, evidence `mz2_courier_delivery_evidence_v1`, events `mz2_shipping_native_events_v2` (`accounting_shipping_native_contract.py:12–15`). Advertising uses the `BankMovement` schema and `mz2_ad_postings_v2`, `mz2_ad_spend_snapshots_v2`, `mz2_ad_fx_snapshots_v2` (`accounting_advertising_contract.py:14–19`). Driver proof is the `DriverPaymentDestination` typed contract, not a newly invented contract ID. The general sales/refunds/payroll consumers above still target legacy `general_ledger`; no native domain contract ID for their missing replacement is asserted here.

## Existing execution evidence versus this source audit

- Existing CI [Accounting run 36807898929](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36807898929), job 110196245850: `WorkflowTests.test_new_sale_to_existing_settlement_service_and_balance_guard` and `test_partial_full_refund_uses_original_rate` fail at initial `preview_and_post`, HTTP 423 `accounting_legacy_writer_disabled`; 18 tests, 2 failures. Refund and settlement steps were not reached.
- Existing local reproduction, recorded in `WRITER-BLOCKERS.md`: those two cases plus `ReportIsolationTests.test_actual_refund_month_end_partial_and_final_payment_with_legacy_sentinels` produced 3 failures in 3.18s, all at the initial sale prerequisite. This is not independent direct refund/payment execution evidence.
- Driver `test_accept_exact_destination_once_no_fee_netting` and `test_pos_later_bank_arrival_separate_journal_and_retry` substitute the destination proof fixture. `test_absent_integration_leaves_pending_and_full_balance` tests the real blocked production port and preserves pending status, full responsibility and zero review journal. They distinguish functional native consumer from absent production proof.
- Advertising `test_funding_and_settlement_are_transfers_and_fee_is_separate` tests only the pure journal plan. At the earlier audited checkpoint `test_track_a_port_fails_closed` proves the first identity hold and no journal. The integration owner's pending change/test may supersede that first hold; the separate evidence/posting hold remains a different claim.
- Source/provenance tracing, not new independently executed runtime failures, establishes the payroll, bank receipt/advance, entitlement and provider-settlement sink findings. No runtime coverage is invented for those paths.

The existing adapter checks in `WRITER-BLOCKERS.md` record 6 passes/1 skip, then an explicit rerun of the skipped advertising case yielding 1 pass. Those are prior local blocker/consumer checks, not final CI for the current working tree. The durable external logs and hashes are recorded there.

## Guard, limits and closure

`backend/ledger_core.py:259–260` checks `assert_writer_allowed(..., "legacy")` before inserting into `general_ledger` at line 315. `backend/accounting_writer_transition.py:118–119` rejects legacy writers after `v2_active` with `accounting_legacy_writer_disabled`. That is intentional fail-closed behavior, not a missing fallback. Track G quarantine must remain intact.

Additional history search for introduction of `post_journal_v2` in the employee, order recognition, bank receipt, advance, order refund, entitlement, customer refund and receivable files returned no matching commits (`git log --all -S post_journal_v2 -- <paths>`). Native collection/private posting searches found no alternate direct producer outside the native ledger core and listed callers. These searches support the delivered-source conclusion; they do not prove the absence of unpublished implementations elsewhere.

This audit executed source/history reads only, not new runtime tests. Existing proving tests and their precise limits are recorded in [WRITER-BLOCKERS.md](WRITER-BLOCKERS.md): failures at initial sale do **not** establish an independently executed downstream refund/settlement failure. Old fixtures without a valid native opening are a separate fixture issue and cannot prove that a domain writer is absent.

Future closure for the missing general sale, bank receipt/advance, refund, provider-settlement and payroll paths requires an explicitly authorized delivered domain writer/contract plus end-to-end native-opening tests proving economics, evidence, idempotency, transactional rollback, closed-period controls and V2-only writes. No such implementation is authorized by this report. Existing COD recognition should continue to be assessed against its delivered shipping evidence contract, not broadened into an unrelated writer.
