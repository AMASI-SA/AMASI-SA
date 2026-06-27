# Make.com → Mezan — Building `items[]` Safely

**Status**: 🟢 ACTIVE — published 2026-02-27
**Companion to**: `make-runbook-qoyod-dry-run.md`, `qoyod-webhook-contract-v1.md`
**Owner**: Integrations Platform team

---

## 0. TL;DR

The HTTP module's "Raw Request Content" treats `{{1.data.items}}` as a **string interpolation** of an internal collection object — Make stringifies it as `[Collection]` / `[object Object]`, NOT as a JSON array. The resulting body is malformed JSON and Mezan returns:

```text
HTTP 422 {"detail":"json_invalid","message":"Expecting property name enclosed in double quotes"}
```

**Never** inject a Make Array directly into a Raw JSON body. Build the payload with a **Create JSON** module so Make is responsible for the JSON encoding, not you.

> **Iter-275 update (2026-02-27)**: Mezan's normalizer now accepts Salla's native nested `amounts` shape directly — you don't need to flatten `unit_price`/`tax_amount`/`total` inside Make. The Array Aggregator can pass the entire Salla item bundle through with minimal field selection. See §2.3 for the simpler aggregator setup.

---

## 1. What "wrong" looks like (do NOT do this)

A typical broken HTTP module body:

```jsonc
{
  "order_id":     "{{1.data.id}}",
  "order_number": "{{1.data.reference_id}}",
  "subtotal":     {{1.data.amounts.sub_total.amount}},
  "total_amount": {{1.data.amounts.total.amount}},
  "currency":     "SAR",

  // ❌ This line breaks the body:
  "items": {{1.data.items}}
}
```

Make replaces `{{1.data.items}}` with something like `[[object Object]]` or `omap{...}`, neither of which is valid JSON. Mezan never reaches the totals guard — it rejects the request at parse time.

Mezan logs every parse failure to `webhook_parse_failures` with:
- `occurred_at`
- `token_prefix` (so you can attribute to the right scenario)
- `body_preview` (first 2 KB of what Make sent)
- `parser_error`

If your Make scenario keeps hitting `422 json_invalid`, ask the Mezan operator to share the latest `webhook_parse_failures.body_preview` — you'll see exactly the malformed text Make produced.

---

## 2. The correct Make scenario (5 modules)

```
┌─────────────────┐   ┌──────────┐   ┌────────────────┐   ┌─────────────┐   ┌──────────────────┐
│ 1. Salla        │ → │ 2. Iter- │ → │ 3. Array       │ → │ 4. Create   │ → │ 5. HTTP — POST  │
│    Webhook      │   │   ator    │   │   Aggregator   │   │   JSON       │   │   to Mezan       │
│  (incoming)     │   │ on items │   │ (collect mapped │   │ (build the   │   │  Content-Type   │
│                 │   │          │   │  rows)          │   │  whole body) │   │  application/   │
│                 │   │          │   │                 │   │              │   │  json            │
└─────────────────┘   └──────────┘   └────────────────┘   └─────────────┘   └──────────────────┘
```

### Module 2 — Iterator

* **Module**: `Tools › Iterator`.
* **Array**: `{{1.data.items}}` (the whole array, NOT indexed).
* Result: Make emits one bundle per item downstream.

### Module 3 — Array Aggregator

* **Module**: `Tools › Array aggregator`.
* **Source Module**: select the Iterator (Module 2).
* **Target structure type**: `Custom`.

#### Option A (RECOMMENDED post-Iter-275) — pass Salla item bundle through

The Array Aggregator's `Custom` mode only lets you pick existing fields
from the iterator bundle. That's perfect — pick the fields you need and
let Mezan's normalizer handle the layered `amounts` shape. Map:

| Field | Map from |
|---|---|
| `sku` | `{{2.sku}}` |
| `name` | `{{2.name}}` |
| `quantity` | `{{2.quantity}}` |
| `amounts` | `{{2.amounts}}`  ← whole sub-object, parses cleanly |

After Iter-275 the normalizer reads:
- `unit_price` from `item.amounts.price_without_tax.amount`
- `tax_amount` from `item.amounts.tax.amount.amount` (double-nested money node Salla emits)
- `total` from `item.amounts.total.amount`

So `amounts` is enough — no need to synthesise flat keys.

#### Option B (legacy — pre-Iter-275 flat keys)

If you prefer the flat Mezan-canonical shape, map per-field:

| Field | Map from |
|---|---|
| `sku` | `{{2.sku}}` |
| `name` | `{{2.name}}` |
| `quantity` | `{{2.quantity}}` (parse to Number if Salla sends string: `{{parseNumber(2.quantity)}}`) |
| `unit_price` | `{{2.price.amount}}` (Number) — Salla's per-item ex-tax price. |
| `tax_amount` | `{{ifempty(2.tax_amount; 0)}}` |
| `total` | `{{2.total.amount}}` |

> Salla's `data.items[].price` is an object `{amount, currency}` — drill into `.amount`.

Both options produce the same canonical DTO downstream. Pick whichever feels less brittle for your Make scenario.

After this module Make has a single bundle containing an `array` field — the fully-formed `items[]` list.

### Module 4 — Create JSON

* **Module**: `JSON › Create JSON`.
* **Data structure**: create once, reuse forever. Define it as a JSON Schema matching Mezan's webhook contract (see §3 below for the exact one).
* **Map fields**:

```text
order_id          ← {{1.data.id}}
order_number      ← {{1.data.reference_id}}
order_status      ← {{1.data.status.slug}}      // e.g. "completed"
order_status_native ← {{1.data.status.name}}    // Arabic display name
order_date        ← {{1.data.date.date}}
completed_at      ← {{1.data.date.date}}        // or status_changed_at if available
currency          ← {{1.data.currency}}
subtotal          ← {{1.data.amounts.sub_total.amount}}
tax_amount        ← {{1.data.amounts.tax.amount}}
shipping_amount   ← {{1.data.amounts.shipping_cost.amount}}
discount_amount   ← {{ifempty(1.data.amounts.discounts.amount; 0)}}
total_amount      ← {{1.data.amounts.total.amount}}
customer.name     ← {{1.data.customer.full_name}}
customer.phone    ← {{1.data.customer.mobile}}
customer.email    ← {{1.data.customer.email}}
items             ← {{3.array}}                  // ★ THE FIX ★
payment_method    ← {{1.data.payment_method}}
```

Create JSON returns a single text field `json` — that's the validated, safe body.

### Module 5 — HTTP — Make a request

* **URL**: `https://mezansalla.com/api/integrations/qoyod/webhook` (production) or the preview equivalent.
* **Method**: `POST`.
* **Headers**:
  | Name | Value |
  |---|---|
  | `Content-Type` | `application/json; charset=utf-8` |
  | `X-Webhook-Token` | `{{the token generated in Mezan UI}}` |
  | `X-Idempotency-Key` | `salla:order:{{1.data.id}}:order.payment.updated:{{1.data.status.slug}}` |
* **Body type**: `Raw`.
* **Content type**: `JSON (application/json)`.
* **Request content**: `{{4.json}}` — just the Create JSON output, nothing else.

**Important**: Do NOT type any JSON braces around `{{4.json}}`. The Create JSON module already produced a complete object. Surrounding it with `{ "data": {{4.json}} }` would double-wrap it.

---

## 3. Create JSON data-structure schema (copy-paste)

In Module 4, click **Create a data structure → Generator** and paste this sample. Make will infer types automatically.

```json
{
  "order_id":           "12345",
  "order_number":       "12345",
  "order_status":       "completed",
  "order_status_native": "تم التنفيذ",
  "order_date":         "2026-02-27T10:00:00",
  "completed_at":       "2026-02-27T10:05:00",
  "currency":           "SAR",
  "subtotal":           105.00,
  "tax_amount":         3.45,
  "shipping_amount":    23.15,
  "discount_amount":    0,
  "total_amount":       131.60,
  "customer": {
    "name":  "أحمد",
    "phone": "+966500000000",
    "email": "ahmed@example.com"
  },
  "items": [
    {
      "sku":        "AMS11961",
      "name":       "تغليف انيق مع الورد",
      "quantity":   1,
      "unit_price": 5.00,
      "tax_amount": 0,
      "total":      5.00
    }
  ],
  "payment_method":  "mada"
}
```

After Make parses this, set `items` to type **Array of Collections** and verify the per-item fields are: `sku` (Text), `name` (Text), `quantity` (Number), `unit_price` (Number), `tax_amount` (Number), `total` (Number).

---

## 4. Verification (mandatory after edit)

Send ONE real order through the new scenario. In Mezan:

1. Open `🩺 مراقب أول مزامنة`.
2. Find the new row by `salla_order_number`.
3. Click it and verify on the **right pane**:
   - `pipeline_stage = CUSTOMER_RESOLVED` (or later) — confirms Totals Guard passed.
   - `canonical_payload.items[]` length matches the order's item count on the Salla dashboard.
   - `canonical_payload.subtotal` matches Salla's subtotal.
   - **No** `totals_guard` block in the row (it's only persisted on refusal).

Or via API:
```bash
curl -s "$API_URL/api/integrations/qoyod/first-sync-monitor/<trace_id>" \
  -H "Authorization: Bearer $TOKEN" \
| jq '{
    stage:        .pipeline_stage,
    items_count:  (.canonical_payload.items | length),
    items_sum:    ([.canonical_payload.items[] | (.unit_price * .quantity)] | add),
    subtotal:     .canonical_payload.subtotal,
    guard:        .totals_guard
  }'
```

A passing row looks like:
```json
{
  "stage":       "COMPLETED",
  "items_count": 3,
  "items_sum":   105.00,
  "subtotal":    105.00,
  "guard":       null
}
```

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `422 json_invalid` on Make HTTP step | Raw JSON injection — `"items": {{...}}` | Switch to Create JSON module (§2.4). |
| `items_count: 1` in Mezan even though Salla shows more | Iterator step is iterating something other than `1.data.items` | Re-check Module 2's `Array` field. |
| `items_count: N` but `items_sum != subtotal` | `unit_price` is being read from the wrong field | Salla's per-item `price.amount` is ex-tax. Don't use `subtotal.amount` or `total.amount` per item. |
| `line_items_total_mismatch` on tax-inclusive orders | Salla store has "tax-inclusive prices" enabled | Send `tax_amount: 0` per item and let Mezan treat the total as inclusive. Open a ticket — we may auto-detect this. |
| Make module 4 says "incomplete bundle" | Iterator emitted zero items because `1.data.items` was empty/null | Salla didn't send an items array (rare). Mezan will mark the row `missing_items_no_enricher`. Check Salla webhook payload directly. |

---

## 6. Why this approach is safer than text-injection

Make's text engine treats any value inside `{{...}}` as a **string** when it's placed in a Raw Body. Collections and Arrays are converted via Make's internal `toString()`, which produces:
- Arrays → `omap{1=[object],2=[object]}` (Make's internal representation, NOT JSON)
- Numbers → safe (`5.00`)
- Strings → safe (auto-quoted? **NO** — you must wrap in quotes yourself)

By contrast, the **Create JSON** module uses Make's JSON encoder which knows how to handle each type. The trade-off is one extra module per scenario, but you gain:
- Schema validation at design time.
- Automatic type-coercion.
- Correct handling of nested arrays / objects.
- No "string vs object" pitfalls.

---

## 7. Verified payload shapes

Mezan's webhook contract (`qoyod-webhook-contract-v1.md`, §3) accepts either:

### Shape A — flat `items[]`
```json
{
  "order_id": "...",
  "items": [ { "sku": "...", "quantity": 1, "unit_price": 100, "total": 100 } ]
}
```

### Shape B — `packages[].items[]`
```json
{
  "order_id": "...",
  "packages": [
    { "items": [ { "sku": "...", "quantity": 1, "unit_price": 100, "total": 100 } ] }
  ]
}
```

Use Shape A unless you're integrating multi-shipping logic. The runbook above produces Shape A.

---

## 8. Related runbooks
- `qoyod-webhook-contract-v1.md` — the canonical webhook schema.
- `make-runbook-qoyod-dry-run.md` — token rotation, dry-run mode, status semantics.
- Mezan Totals Guard error codes (`line_items_incomplete`, `line_items_total_mismatch`, `order_total_mismatch`) — see Iter-273 section of the dry-run runbook.
