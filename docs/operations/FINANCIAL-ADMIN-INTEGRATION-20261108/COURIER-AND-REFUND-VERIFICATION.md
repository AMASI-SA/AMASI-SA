# Courier compatibility, Emkan bank refunds and Aramex verification — 2026-11-08

Synthetic isolated verification only. No production receipt decisions, live money writes, tariff reapproval, opening changes, historical backfill, APK build, OTA or courier-app changes occurred.

## Existing courier lineage and API evidence

GitHub was read through the authenticated connector. Repository `main` is older than the actual courier release lineage and was not used as the compatibility authority.

- Known signed release: [mobile PR #311](https://github.com/AMASI-SA/amasi-mobile/pull/311), package `com.amasi.sa.delivery`, version 1.0.17/code18, source merge `d5fdda1e6a141703c56f9a0c82b971fe2a3a5ee0`. The PR records signed workflow 41461059770 and APK SHA256 `438f108d2883ace40b613a015f38b0df6208903ba186ac31987d25e86a0ae94f`.
- Current release-line source: `codex/courier-ui-20261030` at `d8a396dabafd25c8d85ea1834e4fe08ce31a2086`, merged [PR #324](https://github.com/AMASI-SA/amasi-mobile/pull/324), version 1.0.18/code19. This source read does not prove which artifact is currently installed on a phone or distribution of code19.
- [PR #344](https://github.com/AMASI-SA/amasi-mobile/pull/344) remains open/draft, head `5fe6844bb3a601e97dfa84a3b6e92af47818b221`, base `codex/android73-monitoring-selection-20261106`. Its management login changes target `delivery-app`; they were not merged into the courier application for this task. Management entry points instead use the existing Mezan web shell.

Read `delivery-app/src/api.ts` from both exact source SHAs. Current branch bytes were rechecked against its immutable SHA. Downloaded source SHA256s:

| Source | api.ts SHA256 |
| --- | --- |
| Signed code18 source | `580b43d49ac1b1fb8eb0addc0f2ceec21b1b846146e8247defa0820afd7575b4` |
| Current code19 source | `f3602aebea569bd950e5c3478c39227c8309027513ba19bedc9ebca8b0ff695e` |

Executed each actual TypeScript module via TypeScript transpileModule/CommonJS in a Node VM with synthetic SecureStore/fetch boundaries. Called `setDeliveryStatus` for bank_transfer, card_terminal and cash. Recorded emitted bodies in `backend/tests/courier_release_status_payloads.json`; both versions emit the same request contract. Tokens were synthetic and excluded from the fixture.

`POST /store-delivery/app/deliveries/status` retains barcode, target_status, payment_method, receipt_reference, delivery_proof_reference, conversation_evidence_reference, bank_account_id, physical_cash_amount and physical_cash_confirmed. No new transfer-owner selector, request field or app update is required. Existing upload endpoints retain multipart `assignment_id` and `file`; receipt, proof and conversation evidence remain distinct. Driver read routes for home, summary, bank accounts, payment reviews and settlements are retained. Code19 receiving-session read route is also retained.

New regression suite validates all six emitted bodies against the strict integrated Pydantic model and checks real router/OpenAPI paths and upload field names. Existing suites exercise current-status filtering, evidence separation, Salla write/readback guards, noncash review and summary semantics.

```sh
PYTHONPATH=backend:backend/tests python -m pytest \
  backend/tests/test_store_delivery_released_client_compatibility.py \
  backend/tests/test_store_delivery_driver_app_v2_contract.py \
  backend/tests/test_store_delivery_current_status.py \
  backend/tests/test_store_delivery_noncash_review.py \
  backend/tests/test_store_delivery_collection_review_summary.py \
  -q --disable-warnings --tb=short
```

Result: **115 passed**, 8 existing warnings, 3.15 seconds, exit 0. This is API/source compatibility evidence, not physical Android camera/layout, installed-device or post-publication production acceptance.

## Emkan bank refunds and Aramex prepaid charge

New file: `backend/tests/test_operational_balance_emkan_aramex_release.py`.

- Emkan fixture uses saved approved 6.99% + SAR1.50, exclusive 15% VAT, effective 2026-11-01. A SAR300 capture has calculated fee SAR25.84 and provider receivable SAR274.16.
- Partial SAR100 and full SAR300 bank refunds preserve original Emkan provider, capture, fee and receivable. Four concurrent identical requests and two refreshes preserve a single return and debit only the selected bank.
- Salla totals already reduced to SAR200 or SAR0 stay unchanged after explicit bank-refund confirmation/retry. An observed value/evidence change alone does not debit bank; the bank source confirmation does. Provider receivable/fee remains unchanged and provider refunded amount remains zero.
- Aramex fixture uses saved prepaid SAR30 + 15%=SAR34.50, exact selected Salla cash account and effective 2026-11-01 00:30 Asia/Riyadh. Completion one second before is excluded; exact boundary and later completion are included even for an old-created order. Reconciliation replay and subsequent delivered status do not add another debit. Courier expense, cash balance and financial-position delivery details reconcile to SAR34.50. Opening and tariff records remain unchanged.

A dedicated loopback-only MongoDB8.0.12 process was started with a unique temporary dbpath, port27326, `--nounixsocket`; only synthetic UUID-isolated test databases were used. Mongo and pytest ran within one parent process lifetime; owned Mongo was terminated in finally. No production environment or database was read. Mongo binary SHA256: `f28b48b5f86b58a874978c90cedddd473d9a8befe6b445c3cc0520beeb004b45`.

```sh
OPERATIONAL_TEST_MONGO_URI=mongodb://127.0.0.1:27326 \
PYTHONPATH=backend:backend/tests python -m pytest \
  backend/tests/test_operational_balance_emkan_aramex_release.py \
  backend/tests/test_operational_balance_customer_returns.py \
  backend/tests/test_operational_balance_prepaid_shipping.py \
  backend/tests/test_operational_balance_prepaid_position.py \
  -q --disable-warnings --tb=short
```

Result: **116 passed**, 8.15 seconds, exit 0 (new release cases alone: 7 passed). No source defect surfaced for these tested rules. Net order value preservation is proven; complete recognized P&L and production balances are not certified by these tests.

## Administrative profit evidence presentation

The existing financial-position page now renders `profit_rows.period_evidence` using one Arabic formatter shared with the Excel summary. It separates the supported subtotal from unconfirmed cost estimates, states the source record count, calculation basis (including gross VAT or saved FX where relevant), specific evidence issue and source/date reference. Incomplete evidence keeps the final row amount unavailable even if an inconsistent response supplies a number. Missing subtotals remain unavailable. Print uses the same bound report DOM; export uses that same report object without refetching.

Regression evidence: the actual React test renders an exact decimal beyond Number precision, separate estimated subtotal and a source issue, then checks those identical lines in print and workbook with exactly one API read. Unit cases cover missing evidence, unknown issue labels, VAT/FX explanations and preventing subtotal promotion.

Frontend final focused integration run: **115 passed / 10 suites**, 5.31 seconds, exit 0. Uses existing Babel/Jest/React dependencies read-only and the ordinary jsdom runtime. Files: `profitEvidencePresentation.test.js`, `DailyFinancialMovements.test.jsx`, `dailyFinancialMovementsApi.test.js`, `OperationalFinancialPosition.test.jsx`, `financialPositionPresentation.test.js`, `DriverPaymentReview.test.jsx`, `StoreDeliveryPaymentReview.test.jsx`, `StoreDeliverySettlements.test.jsx`, `MezanV2NavigationShell.accountingFirstRun.test.jsx`, `Layout.deferredWorkspaces.test.jsx`. This is separate from the 115 backend courier checks above.
