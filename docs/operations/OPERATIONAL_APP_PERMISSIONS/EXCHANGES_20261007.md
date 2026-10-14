# Temporary operational replacements — 2026-10-07

Companion Draft #1271. Original-order-linked Web operation; **no new sales order**, external carrier booking, inventory movement, native build, or accounting entry.

The owner explicitly approved recording an actual supplier invoice in the operational register only after discovery that `supplier_native_invoice_v2.post_native_invoice` calls `post_journal_v2`. That native path is neither called nor changed. The temporary record is a transcription of the actual supplier document, not a second native invoice or an operational receipt-confirmation event.

## Delivered flow

1. Search the old order number using canonical MZ2 order evidence, including orders predating the operational start. Select all available products or individual quantities.
2. Copy the existing MZ2 product cost components as a frozen per-unit estimate. Missing/ambiguous cost prevents creation; sales price is never substituted for cost. This increment replaces the selected original products, not a new catalog/product configuration flow.
3. Select the shipping company. Create its operational shipment immediately with expected cost from the original shipping proof/tariff, as defined by the customer returns contract. Shipment reference may be supplied later. Confirmation of actual shipment execution promotes expected to confirmed; it is not a bank payment. No carrier API is invoked.
4. Ask whether the customer has already contributed. No means no bank movement. Yes requires the actual amount, approved MZ2 bank, payment time and reference. A new bank credit is part of the same atomic operation. An eligible existing bank-only manual incoming collection can be linked instead, with exact bank/amount/reference match and no additional credit.
5. The case stays visible as pending purchase. `شراء بدل` opens its remaining product quantities. Enter the actual supplier invoice number/date, MZ2 supplier, selected quantities, line net/tax/gross and matching document totals. Partial purchases reduce only the covered estimated quantities; the supplier's confirmed obligation uses actual gross, never the estimate or net alone.
6. Supplier payment uses the existing operational payment/allocation flow. Creating a purchase invoice does not debit the bank. Existing invoice identity cannot be reused in this register, and a matching native MZ2 invoice number/supplier is rejected pending a future explicit reconciliation contract.
7. Customer contribution is separately displayed. It reduces the case's net cost, not supplier or shipping liabilities. Additional actual contributions may be recorded later.

## Identity and atomicity

`customer_exchanges` is inside the existing owner CAS aggregate, alongside ordinary movements, obligations and audit. Case ID, original order/item IDs, invoice identity, shipment reference and contribution movement IDs are durable. `future_replacement_order_id` is reserved as null; a future independent replacement producer must explicitly link these records rather than reproduce their financial effects. That future integration is **not** implemented or auto-enabled here.

- Expected product obligations: `exchange-expected:<case>:<original-item>`; unassigned supplier until purchase.
- Confirmed invoice obligation: `exchange-purchase:<supplier/document-derived-id>` with net, tax and gross.
- Shipping obligation: `exchange-shipping:<case>`.
- Quantity limits and invoice/payment identities are rechecked inside each CAS attempt. Owner-bound request claims, replay result and audit use the existing store contract.
- Contribution movement uses `original_order_number` and `exchange_id`, with ordinary `order_number` null. This deliberately keeps a replacement contribution distinct from original order payment recognition. Concurrent resync tests prove the original bank payment and contribution coexist exactly once each.
- UI stores unresolved requests before sending and retries the exact body/identity after reopening. Definitively rejected requests may be corrected with a fresh identity.

## Boundaries

All parties and estimates use existing MZ2 readers. The new API routes are Web-only, denied by the unchanged native route gate. No Mobile grants, Android code, frozen #1263, Release Intent, Accounting, Production branch, source orders, native supplier invoices or lease changed.

No automatic refund, inventory receipt, supplier return, cancellation workflow or external shipment booking is implied. The shipment is an operational record. Partial invoices must contain the selected replacement lines and their actual tax/gross evidence; tax rates are not guessed. Invoice import from an already-issued native invoice is currently rejected, not duplicated. Future native/order integration requires an explicit identity mapping and reconciliation design.

## Verification and evidence

- Fresh full regression: **243 backend PASS**, 0 FAIL/SKIP, exit 0 (`test_operational_balance*.py` plus `test_employees_v2_management.py`). **88 Web PASS**, 7 suites, exit 0 (previous six suites plus CustomerExchanges). New exchange-specific cases: 16 backend and 7 Web.

- New real local Mongo/ASGI tests cover old-order creation, partial invoice + supplier payment, customer contribution, exact retries, concurrent quantity competition, invoice reuse, net/tax/gross mismatch, foreign entity rejection, missing costs, native invoice collision, shipment execution, linked existing credit, native denial, owner scope, source immutability, original-order concurrent bank resync.
- Runtime collection guard allows only the independent source allowlist plus existing accounting control metadata reads; source collections permit find/find_one only. Writes allowed only to operational state and operation-claim collections. No journal/ledger collection reached.
- New React tests cover old-order/full selection, contribution and bank, partial invoice, reload/retry identity, correction after definite rejection, read-only/error state and linking an existing movement.
- Browser command: `node scripts/verify-customer-exchanges-browser.cjs <output-dir>`. Desktop 1440x1100 and mobile 390x844: create old-order exchange with contribution → reload → partial purchase → shipment confirmation → reload. RTL/no overflow, all API calls intercepted with synthetic data and external network blocked. Separate real Mongo/ASGI tests prove financial persistence; browser fixture is not connected end-to-end or Android acceptance.
- Screenshots: `D:/codex-evidence/operational-customer-exchanges-20261007/{desktop,mobile}-{exchange,purchase,readback}.png`.
- No production environment/data used. Mongo restricted to `127.0.0.1:27316`, disposable `operational_balance_test_<uuid>` databases.

No Merge/Deploy/Prepare/Prepublish. Production writes = 0.
