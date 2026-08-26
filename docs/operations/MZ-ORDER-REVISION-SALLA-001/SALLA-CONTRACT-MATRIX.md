# MZ-ORDER-REVISION-SALLA-001 — Salla Contract Matrix (P0)

## Status

`BLOCKED_PENDING_SALLA_SANDBOX_ACCESS`

- Production baseline: `48a7781bf3e48d9657e846ed925f74c017c9ecb3`
- Research branch: `research/mz-order-revision-salla-contracts-p0`
- Review date: 2026-08-26 (Asia/Riyadh)
- Production writes performed: **none**
- Product code changed: **none**

This document distinguishes documented API capability from behavior proven in
a Salla Demo Store. An endpoint being documented is not evidence that Salla
accepts it for every order or payment status.

## Environment gate

No Salla Demo access token, verified Demo identity, or seeded Demo
order identifiers were available in the execution environment. The following
dedicated variables are required; no secret value is printed:

- `SALLA_SANDBOX_ACCESS_TOKEN`
- `SALLA_SANDBOX_BASE_URL` (must be `https://api.salla.dev/admin/v2`)
- `SALLA_DEMO_STORE_ID`
- `SALLA_DEMO_TOKEN_SCOPES` (`orders.read_write` plus product read access)
- `SALLA_SANDBOX_SEED_MANIFEST`
- `SALLA_SANDBOX_EVIDENCE_DIR`
- `SALLA_DEMO_STORE_CONFIRMED=true`
- `SALLA_SANDBOX_RUN_WRITES` (must equal `true` for writes)

All were absent. Consequently, the state matrix below is intentionally marked
`NOT_EXECUTED`; filling it with inferred pass/fail results would be false
evidence. P1 must not start until this matrix has real Demo Store evidence or an
official Salla mock environment that models the status restrictions and
payment side effects.

## Documented endpoint contracts

| Operation | Method and path | Scope | Documented request facts | Documented response facts | What remains unproven |
|---|---|---|---|---|---|
| Create item | `POST /admin/v2/orders/items` | `orders.read_write` | `order_id`, identifier, quantity, branch, options, price/cost/weight | Returns order item rows including item `id`, SKU, quantity, amounts and option/value identifiers | Allowed order statuses; paid-order balance; inventory reservation; webhook set; retry semantics |
| Update item | `PUT /admin/v2/orders/items/{item_id}` | `orders.read_write` | `order_id`, quantity, branch, options, price/weight | Returns updated order item representation | Whether options change the resolved variant; whether `item_id` stays stable; paid-order balance |
| Delete item | `DELETE /admin/v2/orders/items/{item_id}` | `orders.read_write` | Path item id | Success/message envelope | Allowed statuses; stock restoration; total/payment/refund behavior; retry semantics |
| Read items | `GET /admin/v2/orders/items?order_id=...` | `orders.read` | Order id | Authoritative item list | Read-after-write convergence time |

Official references reviewed:

- Salla Merchant API, Create Order Item
- Salla Merchant API, Update Order Item
- Salla Merchant API, Delete Order Item
- Salla Merchant API, List Order Items
- Salla order webhook model and changelog (`order.products.updated`,
  `order.total.price.updated`, `order.payment.updated`, `order.updated`)

## Required status matrix

Legend:

- `NOT_EXECUTED`: no Sandbox evidence exists yet.
- `PASS`: request accepted and all recorded postconditions matched.
- `REJECTED_BY_SALLA`: Salla rejected the operation; record sanitized response.
- `AMBIGUOUS`: response succeeded but read-after-write/payment/inventory facts did
  not converge or could not be attributed.

| Order state | Create item | Update item | Delete item | Evidence |
|---|---:|---:|---:|---|
| `pending` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |
| `under_review` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |
| `in_progress` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |
| `paid` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Demo order and Order API payment facts required |
| `partially_paid` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Demo order and Order API payment facts required |
| `completed` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |
| `cancelled` | NOT_EXECUTED | NOT_EXECUTED | NOT_EXECUTED | Sandbox order required |

## Per-case evidence record

Each executed cell must record, with tokens and customer data redacted:

1. Sandbox store and fixture identity.
2. Order id/reference and status id/slug before the call.
3. Method/path, idempotency/retry attempt number, sanitized request body.
4. HTTP status, sanitized response body and response/request correlation headers.
5. Source item id before and after.
6. Product, variant, SKU, quantity, option ids/value ids, price and order total
   before and after a fresh `GET /orders/items` plus Order Details fetch.
7. Inventory/branch quantity or reservation facts before and after.
8. Webhooks received, their ids, timestamps and ordering.
9. Repeated identical call outcome.
10. Simulated lost-response outcome: do not repeat a commercial write until a
    read-after-write reconciliation proves whether the first call committed.

Transactions and Branch APIs are excluded from P0 because those scopes are not
granted. Payment facts are extracted only from Order API, Order Items, and
correlated webhooks. `branch_id` comes from the reviewed Seed/order/product
data. No transaction-level behavior may be marked proven.

## Product configuration cases

The live matrix must repeat relevant cells for:

- Product without options.
- Size and color options.
- Text option.
- Checkbox option.
- Multiple quantity.
- Variant-changing option selection.
- Replacement flow: create replacement first, verify it, then delete the old
  item. `delete-first` is prohibited.
- Different customization per unit, represented as separate commercial lines
  unless Salla proves a per-unit contract.

## Dynamic option evidence boundary

The documented Order Items representation includes option ids,
`product_option_id`, option type and value ids/names. It does not by itself
prove an official conditional-dependency graph such as “yes makes name
required”. P0 must separately capture the Product Details/Options responses for
the Sandbox products and determine whether Salla exposes stable dependency and
requiredness identifiers. Text labels must not be used as relational keys.

## Difference-payment decision

Current decision: `UNPROVEN_ON_ORIGINAL_ORDER`.

The Order Items endpoints document item and amount mutation but do not document
that adding/updating an item on a paid order will:

- create an outstanding balance on the original order,
- produce a checkout/payment URL, or
- let the customer pay only the difference.

No original-order difference-payment flow may be implemented until the paid
and partially-paid Sandbox rows prove those postconditions. If they do not,
the approved fallback is a Salla supplemental order linked operationally to the
original order. Mezan must not fabricate a financial line, payment status or
refund.

## Exit criteria for P0

P0 is complete only when:

- all 21 state/operation cells have sanitized Sandbox evidence;
- the required product/option cases have evidence;
- item-id stability and variant behavior are decided;
- webhooks and retry/lost-response behavior are recorded;
- positive-difference behavior is proven or supplemental order is selected;
- the document status changes from `BLOCKED_PENDING_SALLA_SANDBOX_ACCESS` to
  `COMPLETE` with evidence references.

Until then, P1 is not authorized to start under the approved phase gate.

## Runner commands

From the repository root, populate the dedicated Sandbox variables from
`.env.salla-sandbox.example`, then run the read-only gate:

```powershell
python scripts/research/salla_order_item_contract_runner.py readiness
```

After readiness succeeds and the owner explicitly opts into destructive
Sandbox writes, run one reviewed case at a time:

```powershell
$env:SALLA_SANDBOX_RUN_WRITES='true'
$env:SALLA_DEMO_STORE_CONFIRMED='true'
python scripts/research/salla_order_item_contract_runner.py run --case-file docs/operations/MZ-ORDER-REVISION-SALLA-001/fixtures/sandbox-case.example.json --webhook-events C:\path\to\sanitized-webhook-events.json
```

The runner never loads production variables. A missing Sandbox configuration
returns `SALLA_SANDBOX_NOT_CONFIGURED`. Mock transport output is always
`MOCK_CONTRACT_FIXTURE / NOT_EXECUTED`; only an identity-verified HTTP Demo
Store transport can emit `SALLA_DEMO_STORE_EVIDENCE`.
