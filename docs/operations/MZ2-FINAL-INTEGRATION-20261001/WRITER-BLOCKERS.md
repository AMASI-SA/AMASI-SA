# MZ2 native writer blockers — integration evidence

Current completion register: [REMAINING-BLOCKERS.md](REMAINING-BLOCKERS.md) and [WIRING-COMPLETION.md](WIRING-COMPLETION.md). These add exact routes, direct native-book423 proof for customer capture/bank receipt/general expense, and the distinct daily supplier-payment movement adapter gap. Shipping bank connectivity metadata/UI is corrected; its financial gates remain unchanged. Historical evidence below retains its stated source and limits.

Current decision: **repair and wire existing delivered V2 writers; genuine missing implementations remain RELEASE BLOCKERS**. The latest owner instruction authorizes completing existing wiring, while explicitly excluding new accounting paths, new writers, changed economics and Legacy fallback. The expanded audit of all eleven frozen deliveries is in [DELIVERED-WRITER-PROVENANCE.md](DELIVERED-WRITER-PROVENANCE.md). Track F already supplies native COD sale recognition; the missing sales producer below means general non-COD/provider recognition. Existing endpoints are registered but their native financial posting is unimplemented. This is not a claim that the endpoints are absent or return HTTP 404.

Inspected source/CI commit: `8dd823006832a89517422fb216acb15a44cff5c6` on `codex/mz2-final-integration-20261001`, 2026-10-01. This document adds no runtime behavior. Concurrent unrelated integration edits are not included in the source claims below. The four writer files, write-opening fixture and three locally reproduced tests matched this HEAD at execution.

Latest runtime addendum: [WIRING-VERIFICATION.md](WIRING-VERIFICATION.md) records a fresh3-test initial-sale423 reproduction and a new direct payroll blocker probe with verified native opening, canonical employee/salary and real imported movements. Accrual, salary payment, advance grant and custody grant each fail423 with persisted documents unchanged. This supersedes the historical payroll runtime-coverage gap below; it is service-level evidence, not a claimed full HTTP payroll acceptance run. Advertising identity is now wired; its separate evidence/posting barrier remains. Driver native delivery hook and canonical bank selection are repaired; verified-destination approval remains blocked.

## Routes and actual financial sinks

The production factory mounts these installers under `/api/financial-provider-apps`; let **P** mean `/api/financial-provider-apps/accounting-module`. This follows `server.py`, `financial_provider_apps.py` and `financial_provider_apps_legacy.py:354`, rather than the shortened prefixes used by test harnesses.

| Registered route | Current producer and storage | Native contract available / missing | Blocker after native cutover |
| --- | --- | --- | --- |
| POST P/receivables/execute | `accounting_receivable_service._execute_transaction`, lines 181–182, calls `ledger_core.post_txn_group`; duplicate lookup at line 105 reads `general_ledger`. Workflow evidence lives in `mz2_recognition_events`. | Existing qualification, tax snapshot and canonical event identity are implemented. Generic `accounting_ledger_v2.post_journal_v2` exists. No native receivable domain adapter is present. | Owner state `v2_active` forbids the legacy sink. Qualifying evidence cannot authorize posting to the old book. |
| POST P/customer-refunds/{case_id}/recognize | `accounting_refund_entitlements.recognize_entitlement`, lines 45–46, calls the legacy sink for revenue/VAT debit and refund-payable credit; uses `mz2_customer_refunds` and `mz2_refund_entitlements` as domain records. | Existing explicit entitlement evidence and original-tax economics are implemented. No native entitlement posting/binding contract is implemented in the integrated source. | Legacy posting is rejected. Fixing the sale producer alone would still leave entitlement posting unavailable. |
| POST P/customer-refunds/bank-payments/{payment_id}/approve | `accounting_customer_refunds.post_bank_payment`, lines 225/242, calls the legacy sink; domain execution evidence lives in `mz2_customer_refund_payments`. | Existing execution identity, remaining-amount, balance, provider-evidence and liability-reconciliation checks are implemented. No native refund-payment producer is present. | Depending on evidence/readiness, the request can reject before posting; if it reaches the legacy sink under `v2_active`, the transition guard rejects it. Do not promise one universal error for all inputs. |
| POST P/settlements/drafts/{draft_id}/post | Active lifecycle installer calls `accounting_settlement_service.post_reviewed_settlement`; the retained route installer also contains a handler targeting this same service. Service line 451 checks `general_ledger`, line 558 calls `post_txn_group`, line 574 appends `ledger_core.write_audit`. Domain drafts use `accounting_settlements_v2`. | Existing statement review, amounts, bank identity, receipt and refund reconciliation contracts are implemented. Track G supplies fee-policy resolution, explicitly without modifying settlement financial posting. No native provider-settlement adapter is present. | Native readiness/balance guards can reject first. A qualifying request still reaches a forbidden legacy sink. A collection/function name ending in `v2` is not proof of a native ledger write. |

`ledger_core.post_txn_group` writes `general_ledger` and legacy `accounting_audit_log`; none of these four domain producers writes `accounting_general_ledger_v2`, `accounting_journal_groups_v2` or `accounting_audit_log_v2`. The native sink is implemented, but its existence does not supply missing business adapters.

Preview, case creation, notification reconciliation and payment-draft creation are separate evidence operations. Their registration or successful draft response does not prove financial recognition/payment. This audit does not label all those nonposting endpoints as unimplemented.

## Why this is a missing implementation, not a lost merge hook

- `git log --all -S post_journal_v2 --` for the four producer files returned no matching commit in the locally available integrated history. This is evidence about available history, not a claim about every unpublished branch.
- Track A commit `92eda46c71e039800a7e91db8a814e5b86161b74` retains `ledger_core.post_txn_group` in receivable/refund services. [Track A status](../MZ2-TRACK-A-CUTOVER-20260930/STATUS.md) explicitly preserves existing ledger writers/collections and excludes atomic-writer/transition changes.
- [Track G README](../MZ2-TRACK-G-20260930/README.md), integration dependency 4: settlement must consume the fee resolver; the track supplies the port and does not modify settlement financial posting. [Track G contracts](../../track-g-contracts.md) describe native setup facts, identities and policy evidence, not these missing runtime producers.
- [Track G route reachability](../MZ2-TRACK-G-20260930/track-g-old-routes.md) deliberately quarantines public alternate opening mutations and activation. P01 opening engines remain importable for historical synthetic tests; their existence cannot authorize a production/native opening.

No new API, ledger source identifier, conversion rule or domain posting contract is defined by this document.

## Frozen-scope writer register and release impact

Reconfirmed against integration checkpoint `c6fb59d2341a0a701714df1c10a8443cb06bfac4`. The required writers below are descriptions of missing capabilities, not newly approved APIs or implementation names. **Every row is a RELEASE BLOCKER.** Route prefix **P** and current Legacy sinks are defined above; precise test reach and source provenance are retained below.

| Consumer route | Required writer and financial identity/contract | Why unavailable in the frozen tracks | Failure evidence and its limit | Effect on Release Readiness |
| --- | --- | --- | --- | --- |
| POST P/receivables/execute | Native sale/receivable recognition into the verified V2 journal: exact owner, canonical provider/customer receivable identity, recognition-event/source identity, approved original tax snapshot, opening and period, atomic idempotency. | The delivered `_execute_transaction` still calls `ledger_core.post_txn_group` and queries `general_ledger` for duplicates. Track A preserved the old domain writers; available history contains no native replacement for this producer. The generic V2 sink does not supply this domain writer. | `WorkflowTests.test_new_sale_to_existing_settlement_service_and_balance_guard` directly fails at sale execution with423 `accounting_legacy_writer_disabled`; `test_partial_full_refund_uses_original_rate` fails at the same prerequisite. | Native sales cannot be recognized; sale-to-settlement and sale-to-refund acceptance remain blocked. Accounting Module and A+B cannot pass. |
| POST P/customer-refunds/{case_id}/recognize | Native refund entitlement writer linking original verified sale and tax evidence to the exact refund case, customer refund-payable identity, remaining refundable amount and sealed journal. | `recognize_entitlement` still posts through the Legacy sink. No native entitlement posting/binding adapter was delivered in the frozen A/G source; the native journal sink alone is insufficient. | `WorkflowTests.test_partial_full_refund_uses_original_rate` and `ClosedPeriodTests.test_closed_entitlement_and_payment_no_partial_or_automatic_redating` fail at initial sale before entitlement. The entitlement's forbidden sink is source-traced; a directly reached native entitlement HTTP failure is **not claimed**. | Refund liability recognition and original-tax/closed-period acceptance cannot be completed. Supplier Native's shared refund regression also remains red. |
| POST P/customer-refunds/bank-payments/{payment_id}/approve | Native refund payment writer: exact canonical Track A bank identity, original refund-payable identity, one-time receipt/execution evidence, partial/final remaining liability and atomic period/idempotency checks. | `post_bank_payment` retains the Legacy writer; neither Track A's financial identity resolver nor Track G's setup contracts include the missing payment producer. | `ReportIsolationTests.test_actual_refund_month_end_partial_and_final_payment_with_legacy_sentinels` fails at initial sale; downstream payment assertions are unreached. Source confirms the Legacy call; no universal payment endpoint error is inferred. | Native refund payment, partial/final reconciliation and report isolation are unverified/blocked; Track G and A+B acceptance remain red. |
| POST P/settlements/drafts/{draft_id}/post | Native provider-settlement writer: exact provider receivable and canonical bank identities, reviewed statement, effective fee policy, bank-arrival evidence, refund reconciliation and sealed one-time posting. | `post_reviewed_settlement` still checks/writes `general_ledger` and Legacy audit. Track G explicitly delivered the fee-policy port without settlement posting. No native settlement adapter is present to wire. | `WorkflowTests.test_new_sale_to_existing_settlement_service_and_balance_guard` fails before settlement at initial sale. The settlement Legacy sink is independently source-traced; successful settlement is **not exercised**. | Provider receivables cannot be proven settled into the native bank ledger; settlement readiness and accounting end-to-end acceptance stay blocked. |
| POST P/payroll/accrue; POST P/payroll/movements/{movement_id}/classify | Native payroll and employee-movement writer using `mezan_employees_v2.id`, effective salary contracts, exact salary/advance/custody identities, canonical bank/daily-movement evidence and verified opening; balances remain separate. | `_post_accrual` and `classify_employee_movement` still call `ledger_core.post_txn_group`. Frozen employee delivery supplies canonical setup/contracts, not a native ledger producer. No `post_journal_v2` replacement appears in available producer history. Database wrappers only bind transactions. | One-writer guard test proves Legacy rejection in V2-active state; the real sink is exercised by native P02's Legacy-rejection test. Existing payroll tests use Legacy-active fixtures. No dedicated native payroll-route rejection test was found: this is an explicit additional coverage gap, not an asserted route-level result. | Native payroll accrual/payments/advances/custody cannot be accepted. Green Employees Foundation setup CI does not close this blocker or make SSOT/business UAT ready. |

Release consequences above are business acceptance blockers. They are distinct from the separate `Mezan Release Readiness` workflow's source/reviewed-intent classification failure; this document does not claim that workflow emits one error per missing writer. All blockers must remain open until separately authorized implementation and complete native regression evidence exist. Final Integration will not implement them, revive Legacy fallback, change writer state or redesign the accounting model to make tests green.

### Per-route reproduction and future closure

| Gap | Existing test and observed reach | Work required later |
| --- | --- | --- |
| Sale recognition | `WorkflowTests.test_new_sale_to_existing_settlement_service_and_balance_guard`: initial sale fails 423. Directly reproduced. | Separately authorize native recognition producer, sealed journal binding and native duplicate lookup; retain evidence, original tax and atomic replay semantics. |
| Refund entitlement | `WorkflowTests.test_partial_full_refund_uses_original_rate` and `ClosedPeriodTests.test_closed_entitlement_and_payment_no_partial_or_automatic_redating`: sale prerequisite fails 423 before entitlement. Entitlement's forbidden sink is proven by source trace, not a separately reached failing HTTP assertion. | Native entitlement producer linked to original verified sale/tax evidence and remaining refundable amounts; no automatic re-dating. |
| Refund payment | `ReportIsolationTests.test_actual_refund_month_end_partial_and_final_payment_with_legacy_sentinels`: sale prerequisite fails 423 before refund payment. No claim of directly reached payment failure. | Native payment producer with exact bank identity, receipt consumption, partial/final liability reconciliation, period/rollback/replay verification. |
| Provider settlement | `WorkflowTests.test_new_sale_to_existing_settlement_service_and_balance_guard`: sale prerequisite fails before settlement. Settlement Legacy sink is independently source-traced. | Native provider-settlement producer with reviewed statement, fee policy, bank receipt, original receivable/refund reconciliation, idempotency and sealed evidence. |

These are actual unresolved acceptance tests, not new tests that expect missing features forever. Downstream runtime evidence must be completed when the prerequisite native writers are separately authorized. No skipped or unreached assertion is counted as PASS.

## Additional missing writer: payroll financial operations

The final bounded source audit at `257ef6eb49b176a6af2a11d12f68d83b1f708e36` identified this additional release blocker. Canonical employee setup is delivered; native payroll ledger posting is not.

- **Routes:** POST P/payroll/accrue and POST P/payroll/movements/{movement_id}/classify, installed by `financial_provider_apps.py` and `accounting_employee_finance.install_employee_finance_routes`.
- **Current writers:** `_post_accrual` and `classify_employee_movement`, `accounting_employee_finance.py:260,644`, call `ledger_core.post_txn_group`.
- **Legacy collections:** `general_ledger` and `accounting_audit_log`.
- **Documented V2 contract:** canonical `mezan_employees_v2.id`, salary contracts, owner transaction and existing salary/advance/custody economics are available. No delivered `post_journal_v2` payroll producer was found. The module's “MZ2-native” description does not prove a native ledger sink.
- **Cutover barrier:** `ledger_core.py:376` asserts writer `legacy`; `v2_active` rejects with `accounting_legacy_writer_disabled`. `AccountingDatabase` and `SessionDatabase` bind request/transaction context; neither translates collections or installs a native adapter.
- **Tests/evidence:** `test_financial_accounts.py::test_transition_contract_is_fail_closed_and_one_writer_only` freshly passed (1 test/1.65s); native P02's `test_old_shipping_ledger_writer_is_explicitly_rejected_in_v2` exercises the same real sink and verifies no Legacy writes. Existing `test_mz2_employee_finance.py` fixtures default to Legacy-active and seed Legacy accounts, so their passing identity tests do not establish post-cutover native payroll. No dedicated native payroll-route rejection test was found; this runtime coverage gap remains explicit.
- **Future closure:** separately authorize a native payroll/movement producer, preserve canonical employee/bank/opening identities and salary/advance/custody separation, then prove end-to-end accrual and movement classification, rollback, closed-period behavior, replay and zero Legacy access. No such writer is created by this integration.

## Actual CI evidence for the inspected HEAD

[MZ2 Accounting Module run 36807898929](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36807898929), run number 379, completed **failure**. [Job 110196245850](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36807898929/job/110196245850), “Manual tax and atomic recovery (real isolated Mongo),” failed step 7, “Test accountant API through bridge and ledger to settlement.” Logs at `2026-10-01T02:54:35Z` show **18 tests run, 2 failures, process exit 1**:

1. `backend/tests/test_mz2_receivable_workflow.py::WorkflowTests::test_new_sale_to_existing_settlement_service_and_balance_guard`: line 367 calls `preview_and_post`; line 89 receives **423**, expected 200; error code `accounting_legacy_writer_disabled`.
2. `backend/tests/test_mz2_receivable_workflow.py::WorkflowTests::test_partial_full_refund_uses_original_rate`: line 180 calls `preview_and_post`; same **423** and error code.

Both failures occur on initial sale posting. CI did **not** reach the refund entitlement/payment or successful settlement assertions; source tracing, not these two failed assertions alone, establishes those downstream missing ports. Later job steps 8–25 were skipped, including shipping, refund suites, report isolation and write-balance tests. They are not CI passes or independent CI failures in this run. The accounting frontend/backend contract jobs passed; they do not negate the failed real-Mongo job.

## Fresh local reproduction

Existing tests were run without changing source or fixtures, using `.venv/Scripts/python.exe`, `PYTHONPATH=backend;backend/tests`, and the dedicated loopback Mongo replica `mongodb://127.0.0.1:27128/?replicaSet=mz2test`. Each fixture creates a unique `mz2_atomic_test_<uuid>` synthetic database. No Production or Preview connection was used.

```powershell
.venv/Scripts/python.exe -m pytest `
  backend/tests/test_mz2_receivable_workflow.py::WorkflowTests::test_new_sale_to_existing_settlement_service_and_balance_guard `
  backend/tests/test_mz2_receivable_workflow.py::WorkflowTests::test_partial_full_refund_uses_original_rate `
  backend/tests/test_mz2_report_isolation.py::ReportIsolationTests::test_actual_refund_month_end_partial_and_final_payment_with_legacy_sentinels `
  -q --tb=short
```

Result: **3 failed in 3.18s**, exit **1**. All three fail at `preview_and_post` with **423 / accounting_legacy_writer_disabled**. The report-isolation case fails at line 57 through `DailyRefundTests.setup_sale`, before actual refund/report assertions. This third failure is local evidence; its CI step was skipped in the cited run.

Durable local log: `C:/Users/amasi/mz2-final-integration-evidence-20261001/writer-blockers-focused.log`.
SHA256: `6ff694808b4a8a7887f64d784dbdc6dd5ad8005e653c7e5a705146cc7bed0412`.
The Windows log preserves the English error code; Arabic message rendering is lossy, so the CI log is the authoritative readable message.

## Fixture incompatibility versus missing writer

`provision_write_opening` currently builds a legacy `general_ledger` opening through `provision_report_opening`, then explicitly sets `ledger_backend_state=v2_active`. It does not create the verified native opening, active/root journal identity and native opening evidence required by the V2 gate. This is an independent fixture defect. Removing `v2_active` would only exercise the old writer and would not validate MZ2; substituting genuine native opening evidence alone would still leave the four legacy producers above blocked.

Shipping P02 had a different situation at the earlier `8dd823` checkpoint: its historical fixture called P01 opening approval/activation and asserted Legacy rows. The twelve setup failures did not reach shipping behavior. The existing native shipping/COD/fee/settlement implementations were already delivered, so this fixture was repaired within scope. At the current checkpoint the thirteen native P02 cases pass, as do the combined193-test Shipping/Accounting selection and Track F CI. COD collection and fee payment remain separate; paying a fee cannot close COD. See [VERIFICATION.md](VERIFICATION.md) for executed evidence. No missing domain writer was created by that fixture repair.

## Required future closure, not authorized here

1. Obtain explicit scope approval for any missing native domain writer. Preserve existing evidence, economics, idempotency, tax snapshots, permissions, owner transaction, pause, period and opening gates; do not infer domain authorization from the generic V2 sink.
2. Review each producer's native journal provenance/binding and its consumers, including duplicate detection, original-sale/refund links, statement reconciliation and report metadata. Native opening fixtures must represent actual native evidence; historical isolation fixtures must remain clearly historical.
3. Demonstrate sale → entitlement → partial/final refund and sale → provider settlement using isolated native books, rollback, concurrency and duplicate rejection, with no legacy financial writes. Then rerun the complete exact-head CI matrix; skipped steps remain unverified until executed.
4. Keep these blockers visible in integration/SSOT/RC status until that separately authorized work is implemented and verified. No release, activation or financial readiness is established here.

## Additional adapter audit: driver payment approval and advertising bank movement

This bounded source audit adds no implementation. Its classification differs from the four legacy-only producers above: an existing native writer can still be unreachable because the required proof adapter is absent. Wiring an account ID is not proof that funds arrived or that a POS transaction succeeded.

### Driver bank-transfer/POS approval: native writer exists, verified destination adapter absent

POST `/api/store-delivery/payment-review/{assignment_id}` calls `accounting_driver_payment_review.review_driver_payment`. The approved branch already has a native journal writer through `accounting_shipping_native._post`, preserves the driver's separate delivery-fee payable, checks sealed delivery responsibility, and atomically consumes one bank movement or one canonical payment source identity. Rejection has no financial journal. POST `/{assignment_id}/pos-bank-settlement` separately settles previously approved POS receivables after bank arrival; it does not authorize initial POS approval.

The approval branch calls `accounting_driver_payment_port.require_driver_payment_destination` at line 144. The production port at `accounting_driver_payment_port.py:32` unconditionally raises **503 / mz2_driver_payment_destination_not_integrated**. This remains a real blocker even after the canonical shipping bank-identity adapter is connected.

The existing `DriverPaymentDestination` contract requires the exact owner, canonical financial account and ledger tuple, SAR amount, `status=verified`, reviewed evidence reference, receipt SHA256, immutable source namespace/record/revision, and method-specific verification:

- Bank transfer: `destination_kind=bank`, `verification=bank_arrival_confirmed`, and a native `bank_movement_id`. The consumer requires one unclassified inbound SAR movement for the exact bank and amount, without an existing receipt, accounting event or provider classification, and consumes it in the same transaction.
- Card terminal: `destination_kind=pos_receivable`, `verification=pos_transaction_successful`, and a canonical successful processor transaction plus approved POS receivable identity. It must not return a bank tuple or turn a receipt-upload token into transaction authority.

This is the already documented Track F contract: [CONTRACT-GAPS.md](../MZ2-TRACK-F-20260930/CONTRACT-GAPS.md), driver-payment section, explicitly says successful tests replace the seam only in fixtures. The integration must not invent a broader proof rule.

Evidence from `backend/tests/test_mz2_driver_payment_review.py`:

- `test_accept_exact_destination_once_no_fee_netting` uses the `destination` fixture, which monkeypatches the production resolver at line 80. It proves the native consumer behavior when supplied synthetic verified proof; it does not prove production evidence verification.
- `test_absent_integration_leaves_pending_and_full_balance` uses the real blocked port for both bank-transfer and card-terminal methods. It asserts the pending review, full driver responsibility and zero review journal remain intact.
- `test_pos_later_bank_arrival_separate_journal_and_retry` also starts through the substituted destination fixture; later bank settlement does not close the missing initial POS-success adapter.

**Do existing producers suffice?** Canonical delivered evidence already suffices for recognizing driver/COD responsibility under Track F. It does not establish bank arrival or processor success. Existing `accounting_daily_movements.import_daily_movement_file` produces `mz2_daily_movements` with bank ID, amount, direction, currency, reference, file hash and row number; `mz2_daily_movement_files` and `accounting_source_files` preserve source identity and original bytes. The canonical bank-identity resolver already exists. These are reusable bank-side inputs, so the ledger writer need not be recreated. However, no existing production adapter was found that verifies those inputs against the reviewed receipt and emits the complete `DriverPaymentDestination` proof with source revision and exact source identity. A manual/unclassified movement or uploaded receipt alone cannot be asserted to supply that proof. No canonical successful-POS-transaction producer and approved POS receivable mapping was located for this seam. Therefore production closure is **unproven and blocked**, not a claim that all input collections are missing. Implementing verified linkage would require explicit scope, preservation of this contract and an unmocked end-to-end test; this audit does not silently approve it.

### Advertising bank funding/payment: native spend exists, funding orchestration remains absent

POST `/api/accounting-module/advertising-v2/bank-movement` calls `accounting_advertising_bridge.bank_movement`. `post_spend` in the same file already writes native advertising spend. `accounting_advertising_contract.bank_movement_legs` is an existing **pure journal plan**: debit wallet balance or advertising debt; credit bank by amount plus separately evidenced fee; optionally debit the exact fee expense. It does not post, verify a bank movement or consume evidence.

The bank endpoint has two independent holds:

1. Local `require_financial_ledger_identity` at bridge line 136 is still a fail-closed stub (`track_a_require_financial_ledger_identity_not_integrated`). Track A's real canonical identity resolver exists, so this identity connection is an identifiable integration seam.
2. Even if that identity call succeeds, bridge line 154 unconditionally raises `track_a_bank_evidence_and_posting_integration_required`. There is no bank-funding/payable-settlement posting and evidence-consumption orchestration in this endpoint. Removing or wiring the first hold alone must not enable it.

The existing `BankMovement` input specifies platform/integration account, wallet-funding versus payable-settlement kind, canonical bank ID, SAR amount, `bank_evidence_id`, effective timestamp, optional separate fee/evidence, and original-wallet currency/units/FX snapshot. [Track E delivery](../../MZ2_ADVERTISING_V2_ACCOUNTING_TRACK_E.md), “API and integration dependencies,” explicitly leaves bank identity/evidence/posting blocked. It does not define a delivered bank-evidence resolver that can be substituted here. Existing native daily-movement inputs are potentially reusable, but neither caller-supplied `bank_evidence_id` nor canonical identity proves the outbound amount, fee, currency conversion or one-time consumption. No new evidence-to-posting contract is inferred by this audit.

`test_funding_and_settlement_are_transfers_and_fee_is_separate` in `test_mz2_advertising_v2.py` tests the pure plan for both kinds, not bank execution. `test_track_a_port_fails_closed` tests the real first hold and zero native journal rows. Thus existing green tests intentionally prove economics and blocking; they do not prove the bank endpoint is complete.

### Fresh adapter checks

Executed the two driver tests above, the advertising pure-plan test and the advertising blocked-port test against dedicated random synthetic databases. First run: **6 passed, 1 skipped in 7.42s**, exit 0; the advertising DB fixture requires `MZ2_AD_TEST_MONGO_URI` separately from `MZ2_TEST_MONGO_URI`. Reran that skipped blocked-port test with its explicit loopback-only variable: **1 passed in 2.03s**, exit 0. No remaining skip in the seven selected cases. Logs remain outside the repository as `adapter-blockers-focused.log` and `ad-bank-blocker-focused.log` in `C:/Users/amasi/mz2-final-integration-evidence-20261001/`.

These are local consumer/blocker checks, not new CI evidence or proof of an unmocked successful bank/POS adapter. Existing financial source and test files were not modified by this audit.

The previously drafted implementation remains **excluded**, untested and uncommitted, in `C:/Users/amasi/mz2-final-integration-evidence-20261001/native-writers-WIP.patch` (SHA256 `8ce022390a833192e846515d381980917a7d84a9ffc786bfd8749d978384a78a`). Its four source edits and new test were removed from the working tree after reversible-patch verification. Do not apply it under this blockers-only decision.

Production writes by this task: **0**. Production merge/deploy/opening post/activation by this task: **none**. Write-control: **unchanged**. No release lease was created or changed. The task remains **BLOCKED**; existing green checks do not waive missing-writer, SSOT, UAT, Smoke B or release-intent gates.
