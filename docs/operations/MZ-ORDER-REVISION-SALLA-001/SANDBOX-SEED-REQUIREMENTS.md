# Salla Sandbox Seed Requirements

## Purpose

This manifest is required before any live P0 write test. All records must
belong to a non-production Salla store created specifically for destructive
contract testing. Never provide production tokens or production order ids.

## Store-level information

Provide:

- Sandbox store id and display alias.
- Sandbox API base URL.
- OAuth app/client identity used for the Demo Store.
- Confirmation that the token has `orders.read_write` and product access via
  `products.read` or the granted `products.read_write` scope. P0 performs
  product reads only.
- Webhook receiver URL or an exported sanitized webhook event log.
- Currency and the `branch_id` already exposed by the order, product, or Seed.
  P0 does not call a Branch API.

Secrets belong in environment variables, not this manifest or Git.

## Required orders

Create one disposable order for every state below. Each should already contain
at least one replaceable test item and must not contain real customer data.

| Required state | Required identifiers and facts |
|---|---|
| `pending` | order id/number, item id, product id, SKU, state id/slug, payment method |
| `under_review` | order id/number, item id, product id, SKU, state id/slug, payment method |
| `in_progress` | order id/number, item id, product id, SKU, state id/slug, payment method |
| `paid` | all above plus paid amount, total and checkout/payment URLs if exposed by Order API |
| `partially_paid` | all above plus paid/outstanding amounts and payment URLs if exposed by Order API |
| `completed` | order id/number, item id, product id, SKU, state id/slug, payment method |
| `cancelled` | order id/number, item id, product id, SKU, state id/slug, payment method |

Record the initial order total, payment status and relevant branch inventory so
the runner can compare before/after effects.

Transaction and Branch scopes are deliberately out of scope. No transaction-
level behavior may be claimed. Payment evidence is limited to Order API, Order
Items, and correlated webhooks (`order.updated`, `order.products.updated`,
`order.payment.updated`, and `order.total.price.updated`).

## Required products

| Fixture | Required identifiers |
|---|---|
| Simple product | product id, SKU, branch id, unit price, inventory quantity |
| Size/color variants | product id, every variant id/SKU, size and color option ids, value ids, prices and inventory |
| Text option | product id/SKU, option id, type, required flag if exposed, max length if exposed |
| Checkbox/yes-no | product id/SKU, option id, yes/no value ids and price effects |
| Multi-quantity | product id/SKU, stock greater than the largest test quantity |
| Replacement target | original and replacement product/variant ids and SKUs |

If Salla exposes custom-field ids or conditional dependency metadata, include
the raw identifiers and a sanitized response excerpt. Do not map dependencies
by Arabic or English display labels.

## Seed manifest delivery

Copy
`fixtures/sandbox-seed-manifest.example.json` outside the repository, fill it
with Sandbox identifiers, and point `SALLA_SANDBOX_SEED_MANIFEST` to that local
file. Do not commit the filled file.

## Evidence hygiene

- Use synthetic customer name, mobile and email.
- Do not include card numbers, bank receipts or real addresses.
- Preserve Salla request/event ids and timestamps, but redact authorization,
  cookies and personal data.
- Restore or recreate fixtures after destructive cells; never make later cells
  depend silently on an earlier mutated order.
