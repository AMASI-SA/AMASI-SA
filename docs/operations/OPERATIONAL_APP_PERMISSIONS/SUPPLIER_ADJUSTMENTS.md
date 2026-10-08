# Operational supplier accepted adjustments

Scope: recorded operational inventory and replacement purchase invoices only. No Accounting, warehouse stock, bank/cash, or canonical MZ2 invoice write. This is not a general correction endpoint.

- Exact invoice lookup: `GET /api/operational-balances/supplier-adjustments/entry/{supplier_id}/{invoice_number}`. Native callers require `operational_balance_movements_write`; no list/report privilege is granted.
- Save: `POST /api/operational-balances/supplier-adjustments`. Required request/session scope, invoice identity, supplier acceptance reference/date and `accepted=true`. Action `discount` takes `amount`; action `return` takes exact invoice lines (`kind`, `item_id`, integer accepted quantity). Return rejects an arbitrary amount.
- Discount amount is the **gross total operational liability reduction**, including its proportion of the recorded invoice tax. It is not an extra net amount plus tax. Return values are derived from remaining recorded line net/tax/gross and accepted quantities. Sequential proportional allocation preserves the final cent; no invented tax rate or external tax policy is used.
- Discounts reduce the remaining value available to a subsequent return, so discount plus return cannot credit the invoice twice. Cumulative accepted quantity cannot exceed the recorded quantity. Fully discounted goods can still record a physical return at zero remaining monetary credit.
- Invoice + supplier acceptance reference is unique across actors. Retry uses the existing durable operation claim and atomic aggregate mutation. Concurrent payment either completes before the adjustment (creating supplier credit) or is capped/rejected against the reduced liability; no partial payment is persisted.
- Original invoice totals and purchased quantity remain historical facts. Invoice views expose adjusted gross, paid, outstanding, supplier credit, returned quantity and remaining quantity. A paid invoice adjustment becomes a supplier receivable; no new bank movement is generated. Accepted replacement returns reduce confirmed cost but do not automatically authorize or create a second purchase.
- Both inventory and replacement projections reapply accepted credits during resync. Catalogue identities and supplier identity remain MZ2-backed; actual warehouse available stock is not touched.

Related narrow native entry parity: exact-order lookup includes an already-refunded return only while a non-free-form shipment remains unfinished. Its immutable refund fields are supplied for the existing shipment completion API; generic reports remain separately permission-gated. Completion and retries cannot repeat the refund.

Validation uses temporary `operational_balance_test_<uuid>` databases at loopback Mongo 27316, dropped by the existing test harness. Production writes = 0.

Replacement line display joins only the parent case's recorded order-item identity. It exposes that snapshot's product name/product ID and an existing safe image URL; missing image remains null for the UI placeholder. It does not guess an image or product, query Legacy, alter procurement authorization, or reopen remaining-to-buy quantities. `quantity` remains the original purchased quantity; `returned_quantity` records accepted physical returns and `remaining_quantity` is their difference.
