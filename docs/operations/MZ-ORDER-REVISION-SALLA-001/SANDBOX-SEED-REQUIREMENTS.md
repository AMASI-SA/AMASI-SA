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

Secrets belong in the existing encrypted `salla_integrations` record, not this
manifest, Git, or a plaintext Salla token variable. The P0 runner locates
exactly one integration owner by this manifest's Store id and obtains the
credential only through
`salla_integration.service.ensure_fresh_access_token`. `MONGO_URL` and
`DB_NAME` must be available to the runner, but their values must never appear
in evidence or logs.

The CLI needs a case-specific JSON webhook log that continues receiving
events during the reviewed attempt. An export frozen before the write cannot
prove its resulting events. Initialize the file with an array, set the case's
`client_request_id`, and preserve that correlation in the reviewed export.
Update the file atomically to avoid partially written JSON. The CLI observes
it after the mutation for `--webhook-wait-seconds` (default 5, maximum 30).
Missing events make the final evidence inconclusive; a successful item readback
alone does not complete the webhook requirement. Do not retry a commercial
mutation merely to collect a missing event.

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

Order ids and source item ids must be unique. Every order row's `product_id`
and `sku` pair must resolve to exactly one product fixture. A write case must
repeat the same top-level/body order id and may reference only the item,
product, variant, SKU, option/value and branch identifiers belonging to that
resolved Seed relationship. Unknown case/body fields are rejected before any
credential DB or Salla call.

Transaction and Branch scopes are deliberately out of scope. No transaction-
level behavior may be claimed. Payment evidence is limited to Order API, Order
Items, and correlated webhooks (`order.updated`, `order.products.updated`,
`order.payment.updated`, and `order.total.price.updated`).

## Relational product tuples

Flat `variant_ids`, `option_ids`, and `value_ids` remain useful inventory, but
they do not authorize a write by membership alone. The filled external Seed
must add:

- `option_value_tuples`: every allowed option/value relationship. A text option
  uses `{"option_id": "...", "value_kind": "text"}`.
- `variant_tuples`: every variant as one object containing `product_id`,
  `variant_id`, `sku`, and that variant's exact `option_value_tuples`.

For example (identifiers remain placeholders here):

```json
{
  "variant_ids": ["variant-red-small"],
  "option_ids": ["color", "size"],
  "value_ids": ["red", "small"],
  "option_value_tuples": [
    {"option_id": "color", "value_id": "red"},
    {"option_id": "size", "value_id": "small"}
  ],
  "variant_tuples": [
    {
      "product_id": "demo-product",
      "variant_id": "variant-red-small",
      "sku": "DEMO-RED-S",
      "option_value_tuples": [
        {"option_id": "color", "value_id": "red"},
        {"option_id": "size", "value_id": "small"}
      ]
    }
  ]
}
```

The readiness sweep must observe those same tuples in Product Details. A
crossed option/value, variant/SKU, or product/variant combination blocks the
entire write before the attempt starts.

## Required products

| Fixture | Required identifiers |
|---|---|
| Simple product | product id, SKU, branch id, unit price, inventory quantity |
| Size/color variants | product id, every variant id/SKU, size and color option ids, value ids, prices and inventory |
| Text option | product id/SKU, option id, type, required flag if exposed, max length if exposed |
| Checkbox/yes-no | product id/SKU, option id, yes/no value ids and price effects |
| Multi-quantity | product id/SKU, stock greater than the largest test quantity |
| Replacement target | original and replacement product/variant ids and SKUs |

Before deleting an old item, record an ordered three-step proof with the same
order id, old item id and newly created replacement item id throughout:
successful replacement creation, successful fresh-fetch verification, then
the intended old-item deletion. The replacement product id/SKU must match the
`replacement` fixture, and the replacement item must be visible in the final
pre-delete baseline. A DELETE case without this proof is invalid.

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
  cookies and personal data. The runner also removes the exact resolved token
  value recursively from dictionary keys, generic nested fields and lists
  before persistence; a resulting key collision fails closed. Key/bearer-
  pattern sanitization is an additional layer, not the only layer.
- Restore or recreate fixtures after destructive cells; never make later cells
  depend silently on an earlier mutated order.
- Evidence is append-only local research output: one immutable intent record
  before a write and a separate terminal record afterward. If no terminal
  record exists, treat the attempt as `UNKNOWN`; these files are not a durable
  database audit claim.
- Set a fresh `SALLA_P0_WRITE_APPROVAL_ID` and timezone-aware
  `SALLA_P0_WRITE_APPROVAL_ISSUED_AT` only after one case has been explicitly
  reviewed. The approval expires after five minutes and is atomically reserved
  before an UNKNOWN-capable attempt lease; a reused approval creates no orphan
  lease. The sealed capability has at most a 60-second lifetime and rechecks
  expiry, digests, approval, correlation and remaining budget immediately
  before every non-GET attempt or authorized retry.
- Preserve the canonical clone-local state root
  `.git/mz-p0-local-state/MZ-ORDER-REVISION-SALLA-001` beneath the resolved Git
  common directory. Every linked worktree of the clone therefore shares the
  approval reservations, operation replay records and per-order locks. The
  root is intentionally independent of `SALLA_SANDBOX_EVIDENCE_DIR`, the Seed
  path, environment overrides, current working directory and repository-
  relative aliases. Directories are `0700`, files are `0600`, and unsafe Git
  pointers, paths or symlinks fail closed. An absent terminal or `UNKNOWN`
  outcome quarantines that disposable order from replay. Use a newly reviewed
  disposable order after reconciliation; do not delete or rewrite local state
  to force a retry.
- The one-use/replay guarantee applies only within the same repository clone on
  the same host while that local state remains intact. It is not a global or
  cross-host guarantee, and P0 does not add or claim trusted shared/DB state.
- A successful HTTP response is insufficient. Preserve both fresh snapshots:
  fixed create/update/delete postconditions must prove the targeted semantic
  delta and stability of all unrelated items before a cell is marked `PASS`.
  UPDATE must request at least one mutable field, prove at least one requested
  baseline-to-requested change, prove every requested final value, and reject
  any unrequested mutable-field drift.

## Verification host and schema dependency

This P0 evidence runner supports Linux/POSIX only. It fails with
`SALLA_P0_LINUX_POSIX_REQUIRED` before loading `fcntl` on an unsupported host;
do not infer equivalent lock, owner, or no-follow behavior on another OS.

Full Draft 2020-12 verification uses `jsonschema==4.26.0`, pinned in
`backend/requirements.txt`. Missing that dependency fails verification with
`FULL_DRAFT202012_VALIDATION=BLOCKED_MISSING_DEPENDENCY`; JSON parsing alone and
a skipped validator must never be reported as full schema validation.
