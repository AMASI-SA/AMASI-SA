# H2 / Track F contract boundary

Inspected PR #1220 at `6ee615508fdb0153a626341cbaeee08ca247ddf7` (unmerged).
No backend source copied. Parent central daily workspace embeds `DriverPanel`.

- GET `/accounting-module/shipping-v2/context`: route is flat, unlike legacy financial-provider router paths. Source `accounting_shipping_native_routes.py`; `store_drivers`, confirmed `couriers`, `bank_port`, `driver_payment_destination`. Context unavailable stops all dependent requests.
- GET `/accounting-module/shipping-v2/statements/{kind}/{identity}`: native `accounting_shipping_native.statement`, requires `ledger_source=accounting_v2` and exact party identity. Displays backend `cod_receivable`, `payable`, `collections`, `payments`, and native entries without summing/netting. Missing values remain unavailable.
- GET `/store-delivery/payment-review/pending?limit=250`: exact owner-scoped operational review evidence contract from `store_delivery_payment_review_routes.py`. This is evidence ONLY, not accounting balance. Filter bank_transfer/card_terminal locally within explicitly bounded results. Pending never appears collected. No approved/rejected history endpoint exists; other statuses are rejected from this pending-only contract.

## Exact blockers

- `mz2_driver_payment_destination_not_integrated`: F hard-coded fail-closed bank/POS destination port; approval controls unavailable. No destination input or inferred bank/POS identity.
- `mz2_shipping_bank_port_not_integrated`: cash receive/pay, courier/driver payments and later POS-bank settlement blocked. No mutation calls implemented.
- `driver_payment_review_history_read_contract_missing`: no native approved/rejected review-history GET route. Review presentation helper recognizes the backend posted + journal result, but does not fabricate history or use the legacy UI.
- `driver_cash_custody_read_contract_missing`: no separate native cash custody read result in this contract. COD responsibility is never equated with physical cash.
- Context missing (404), native ledger not ready (409), pause (423), unavailable port (503), or malformed native contract -> BLOCKED_BY_BACKEND / not_ready. Server/access errors -> error state, no alternate source.

The existing `/store-delivery/payment-review/bank-accounts` reads legacy `accounts`; H2 NEVER calls it. Existing operational summary is not used for financial values. Pending evidence bank name snapshots are not rendered as financial destinations. P02, Opening, recognition, approval and activation remain backend gates; H2 performs no writes.

Tests cover exact route sequence, pending semantics, readonly source boundary, 404 no fallback, error/empty/loading, native balances vs mismatching entry sums, missing values, unsupported ledger, invalid pending statuses and backend journal result requirements.
