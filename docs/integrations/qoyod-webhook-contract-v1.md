# Integration Contract v1.0
## Make.com → Mezan — Qoyod Webhook

**Status**: 🟢 ACTIVE — locked 2026-06-26
**Endpoint**: `POST /api/integrations/qoyod/webhook`
**Production base URL**: `https://mezansalla.com`
**Preview base URL**: `https://salla-analytics.preview.emergentagent.com`
**Owner**: Integrations Platform team
**ADR alignment**: ADR-001 (Additive, Idempotent, Canonical, Secrets-Disciplined)

---

## 0. TL;DR for Make.com operators

| Element | Value |
|---|---|
| **Method** | `POST` |
| **URL (Production)** | `https://mezansalla.com/api/integrations/qoyod/webhook` |
| **Content-Type** | `application/json; charset=utf-8` |
| **Required header** | `X-Webhook-Token: <generated in Mezan UI>` |
| **Required header** | `X-Idempotency-Key: salla:order:{{order.id}}:{{event}}` |
| **Body** | JSON object — schema in §3 |
| **Success response** | `200 OK` with `{"ok": true, "pipeline_stage": "...", "inbox_id": "..."}` |
| **Failure response** | `4xx` or `200` with `{"ok": false, "error": {...}, "pipeline_stage": "DEAD_LETTER"}` |

> A correctly-formed request that **fails business rules** (wrong status, no items, etc.) still returns `200 OK` — the row is saved to `integration_inbox` with the appropriate failure stage for audit. Only **malformed transport** (bad token, bad JSON) yields `4xx`.

---

## 1. Authentication

### 1.1 Token (`X-Webhook-Token`)

* **Generation**: Through Mezan UI at `Integrations → Qoyod → Settings → Webhook Token`.
* **Lifecycle**: Plaintext is shown EXACTLY ONCE on generation. Subsequent reads expose only a SHA-256 4…4 fingerprint.
* **Rotation**: Re-generating immediately revokes the previous token. No overlap. Make.com config must be updated BEFORE the next webhook fires.
* **Verification order** (security-critical):
  1. If a DB-stored token exists for tenant `main` → only that token is accepted.
  2. Otherwise → the `QOYOD_WEBHOOK_TOKEN` environment variable is the fallback (preview / CI only).
* **Comparison**: `hmac.compare_digest` — constant-time.

### 1.2 Failure responses

| Code | When |
|---|---|
| `401 missing_webhook_token` | header missing or empty |
| `401 invalid_webhook_token` | header present but doesn't match the active token |
| `503 qoyod_webhook_token_not_configured` | no DB token AND no env fallback — operator misconfiguration |

---

## 2. Idempotency

### 2.1 Header

`X-Idempotency-Key` — **strongly recommended** in every request.

### 2.2 Format

```text
salla:order:<reference_id>:<event>
```

| Component | Source in Make | Example |
|---|---|---|
| `<reference_id>` | `{{order.reference_id}}` (Salla short number) | `268500046` |
| `<event>` | `{{event}}` from the trigger module | `order.status.updated` |

**Full example**: `salla:order:268500046:order.status.updated`

### 2.3 Behaviour

* The first request with a given key is processed and persisted.
* A re-delivery of the SAME key returns `200 OK` with `duplicate: true` and the **original** `pipeline_stage` — no second invoice is ever created.
* When the header is omitted, Mezan derives a fallback key from `salla:order:<id>:<event_or_status>` — but explicit headers are preferred for traceability.

---

## 3. Request Body

### 3.1 Top-level shape

Two shapes are accepted:

* **Canonical Salla shape** — `{"event": "...", "data": { ... }}`
* **Legacy Make-flat shape** — flat fields at root (auto-detected by the Adapter)

The Adapter normalises both into a single canonical DTO downstream.

### 3.2 Required fields (Legacy-flat reference)

| Field | Type | Description |
|---|---|---|
| `event_type` | string | One of `order_created`, `order_updated`, `order_completed`, `order.status.updated` |
| `order_id` | string | Salla internal ID |
| `order_number` | string | Salla short reference (e.g. `268500046`) — used as `reference_id` |
| `created_at` | ISO-8601 string | When the order was placed |
| `order_status_slug` | string | Salla status slug — invoice only when `=== "completed"` |
| `order_status` | string | Arabic display name (e.g. `تم التنفيذ`) |
| `currency` | ISO 4217 | `SAR` only supported by the current Day-1 wiring |
| **`items[]` OR `packages[]`** | array | **At least one of these MUST contain line items with SKU** (see §3.6) |

### 3.3 Required fields when status is `completed`

These trigger **invoice creation** in Qoyod:

| Field | Type | Description |
|---|---|---|
| `completed_at` | ISO-8601 string | Drives `invoice_date` when `invoice_date_source = trigger_status_date` |
| `subtotal` | number | Sub-total of line items, ex-tax |
| `tax` | number | Tax total (informational; Qoyod recomputes from tax_id) |
| `shipping_cost` | number | Shipping fee — added as a separate line if configured |
| `total_amount` | number | Order grand total — must be **strictly positive** |
| `payment_method` | string | Free text; mapped via `payment_method_mapping` to Qoyod account |

### 3.4 Customer block (recommended)

When omitted, the row falls back to a "guest" customer that does NOT match any mapping. For accurate accounting:

| Field | Type | Recommendation |
|---|---|---|
| `customer_name` | string | `"<first> <last>"` — split at first space |
| `customer_mobile` | string | E.164 (`+9665...`) — normalised by the adapter |
| `customer_email` | string | Lower-cased |

### 3.5 Optional / informational fields

`utm_source`, `utm_medium`, `utm_campaign`, `device`, `received_from`, `shipping_company`, `discount`, `source`, `shipping_address`, `billing_address`

These are preserved in `inbox.adapter_meta.legacy_extras` but do NOT influence the invoice.

### 3.6 Line items — REQUIRED structure

Each line item (in either `items[]` or `packages[].items[]`) MUST have:

| Field | Type | Required | Notes |
|---|---|---|---|
| `sku` | string | **YES** | Non-empty; matched against Qoyod product catalogue |
| `name` | string | yes (or via `product.name`) | Display name |
| `quantity` | number | yes | Defaults to 1 if omitted |
| `price.amount` | number | yes | Unit price ex-tax. Also accepted: `price: 50` (flat) |
| `price.currency` | string | optional | Defaults to order currency |
| `options[]` | array | optional | Variant attributes — carried through to Qoyod as line metadata |

> **Hard rule**: an item with an empty `sku` rejects the whole order at `FAILED_VALIDATION` with code `items_missing_sku`. We **never** auto-create an unidentified product in Qoyod.

---

## 4. Business rules — gating logic

Every gate is a hard NO. Crossing them yields `DEAD_LETTER`, NEVER an invoice.

| # | Rule | Code on failure |
|---|---|---|
| 1 | `items[]` (or `packages[].items[]`) MUST be present AND non-empty | `missing_items_no_enricher` |
| 2 | Every item MUST have a non-empty `sku` | `items_missing_sku` |
| 3 | Order status MUST be in `qoyod_settings.invoice_trigger_statuses` (default `["completed"]`) | `status_not_eligible` (SKIPPED, not DEAD_LETTER) |
| 4 | `total_amount` MUST be a strictly positive number | `total_must_be_positive` |
| 5 | `currency` MUST match an enabled currency (currently `SAR` only) | `unsupported_currency` |
| 6 | Idempotency: same key already processed → returns original result without re-firing | n/a |
| 7 | Salla-API enricher is OFF by default (`enrichment_fallback_enabled = false`). When the toggle is enabled, the row enters `NEEDS_ENRICHMENT` but the enricher itself is intentionally NOT implemented in v1.0 | `enricher_not_implemented` |

---

## 5. Full request example — `order_completed`

```json
{
  "event_type": "order_completed",
  "order_id":   "69664233",
  "order_number": "268500046",
  "created_at": "2026-06-26 07:00:16",
  "completed_at": "2026-06-26 14:30:00",
  "order_status": "تم التنفيذ",
  "order_status_slug": "completed",
  "currency": "SAR",
  "payment_method": "mada",
  "subtotal":      105.00,
  "tax":            11.90,
  "shipping_cost":  22.61,
  "total_amount":  139.51,
  "customer_name":   "عميل تجريبي",
  "customer_mobile": "+966500000000",
  "customer_email":  "test@example.com",
  "items": [
    {
      "sku":      "SKU-A",
      "name":     "منتج 1",
      "quantity": 2,
      "price":    { "amount": 50.00, "currency": "SAR" },
      "options":  [ { "name": "Color", "value": "Red" } ]
    },
    {
      "sku":      "SKU-B",
      "name":     "منتج 2",
      "quantity": 1,
      "price":    { "amount": 5.00, "currency": "SAR" }
    }
  ],
  "utm_source":   "snapchat",
  "shipping_company": "iMile للتوصيل",
  "received_from": "make"
}
```

### Alternative — `packages[]` form (also accepted)

Same payload, but items grouped under shipping packages:

```json
{
  /* ...same top-level fields as above except for items... */
  "packages": [
    {
      "id": "pkg1",
      "items": [
        { "sku": "SKU-A", "name": "منتج 1", "quantity": 2,
          "price": { "amount": 50, "currency": "SAR" } }
      ]
    },
    {
      "id": "pkg2",
      "items": [
        { "sku": "SKU-B", "name": "منتج 2", "quantity": 1,
          "price": { "amount": 5, "currency": "SAR" } }
      ]
    }
  ]
}
```

---

## 6. Response

### 6.1 Success — accepted & queued for processing

```json
{
  "ok":             true,
  "inbox_id":       "9f2e4c8b1a3d4e7fb6c0a8d1e2f3b4c5",
  "trace_id":       "trace_5f7c...",
  "pipeline_stage": "COMPLETED",
  "duplicate":      false
}
```

### 6.2 Success — duplicate (idempotency hit)

```json
{
  "ok":             true,
  "duplicate":      true,
  "inbox_id":       "<original-row-id>",
  "pipeline_stage": "<original-stage>",
  "first_received_at": "<ISO>"
}
```

### 6.3 Business-rule failure — still 200, terminal DEAD_LETTER

```json
{
  "ok":             false,
  "inbox_id":       "...",
  "trace_id":       "...",
  "pipeline_stage": "DEAD_LETTER",
  "error": {
    "code":    "items_missing_sku",
    "message": "one or more line items have no SKU"
  }
}
```

### 6.4 Transport / auth failure — 4xx

| Status | Body |
|---|---|
| `400` | `{"detail": "Invalid JSON"}` (body also captured in `webhook_parse_failures`) |
| `401` | `{"detail": "missing_webhook_token"}` or `"invalid_webhook_token"` |
| `503` | `{"detail": "qoyod_webhook_token_not_configured"}` |

---

## 7. Failure-state map

| Reason | `pipeline_stage` | Code | Re-tryable? |
|---|---|---|---|
| Missing items | `DEAD_LETTER` | `missing_items_no_enricher` | No (toggle gated) |
| Item missing SKU | `DEAD_LETTER` | `items_missing_sku` | No |
| Status ≠ completed | `SKIPPED` | `status_not_eligible` | n/a (intentional) |
| `total_amount ≤ 0` | `DEAD_LETTER` | `total_must_be_positive` | No |
| Customer-side failure | `FAILED_CUSTOMER` | varies | Yes (RETRYING) |
| Product-side failure | `FAILED_PRODUCT` | varies | Yes |
| Qoyod invoice POST failed | `FAILED_INVOICE` | varies | Yes |
| Qoyod receipt POST failed | `PARTIAL_FAILURE` | varies | Yes |
| Enricher toggle ON, items missing | `DEAD_LETTER` via `FAILED_ENRICHMENT` | `enricher_not_implemented` | No (v1.0) |
| Idempotency duplicate | n/a (returns original) | n/a | n/a |

---

## 8. Make.com — exact HTTP module configuration

> **DO NOT** modify the existing `/api/webhook/make/{token}` module. Add a SECOND HTTP module pointing at this endpoint.

| Field | Value |
|---|---|
| **URL** | `https://mezansalla.com/api/integrations/qoyod/webhook` |
| **Method** | `POST` |
| **Headers** | `X-Webhook-Token: <paste from Mezan UI>` <br> `X-Idempotency-Key: salla:order:{{1.order.reference_id}}:{{1.event}}` <br> `Content-Type: application/json; charset=utf-8` |
| **Body type** | Raw → JSON |
| **Body** | See §5 (paste-ready) |
| **Parse response** | Yes — parse JSON to read `pipeline_stage` |
| **Timeout** | 30 s |
| **Retry on error** | OFF (Mezan handles idempotency; Make retry would just hit the dedupe path) |

### 8.1 First-test checklist

Before enabling the module in production, send a single test request to **Preview** first:

| Step | What to verify |
|---|---|
| 1. Generate webhook token in Mezan Preview UI | Fingerprint appears, plaintext displayed once |
| 2. Send a test order with status = `under_review` (no items needed) | Response `pipeline_stage = SKIPPED`, code `status_not_eligible` |
| 3. Send the same order with `order_status_slug = "completed"` and full items[] | Response shows `pipeline_stage = COMPLETED` (in Dry Run) |
| 4. Re-send step 3 — same idempotency key | Response shows `duplicate: true` |
| 5. Send a payload with `items[]` having an empty SKU | Response `pipeline_stage = DEAD_LETTER`, code `items_missing_sku` |
| 6. Send a payload with `total_amount: 0` | Response `pipeline_stage = DEAD_LETTER`, code `total_must_be_positive` |
| 7. Send malformed JSON `{"x": }` | Response 400 `Invalid JSON`; row appears in `webhook_parse_failures` |

Only after all 7 pass on Preview → promote token + module to Production.

---

## 9. Operator dashboards (Mezan UI)

| Page | URL | Purpose |
|---|---|---|
| Settings | `/integrations/qoyod/settings` | API key, webhook token, capability flags, **enrichment_fallback_enabled** (read-only by default) |
| Invoices | `/integrations/qoyod/invoices` | Per-row pipeline status, retry, manual override |
| Migration | `/integrations/qoyod/migration` | Pre-flight reconciliation between Mezan SKUs/customers and Qoyod (read-only) |
| Go-Live | `/integrations/qoyod/go-live` | Production-readiness checklist + activation |

---

## 10. Versioning & deprecation

* **v1.0** — current. Adds Legacy Adapter, idempotency, full failure-state map, SKU + total guards.
* **v2.0** (planned) — multi-currency, Salla-API enricher (replaces `enricher_not_implemented`).
* **Breaking changes** will be announced via the contract version bump and a 30-day overlap window before old shapes are rejected.

---

## 11. Code references (Mezan)

| Concern | File | Key symbols |
|---|---|---|
| Token verification | `integrations/qoyod/webhook.py` | `_make_verify_token` |
| Idempotency key derivation | `integrations/qoyod/webhook.py` | `derive_idempotency_key` |
| Legacy → canonical | `integrations/qoyod/legacy_adapter.py` | `adapt`, `is_legacy_shape` |
| Validation | `integrations/qoyod/normalizer.py` | `validate`, `normalize` |
| Eligibility (SKU + total) | `integrations/qoyod/eligibility.py` | `check_invoice_eligibility` (v1.0) |
| Business rules (status, trigger_once_only) | `integrations/qoyod/business_rules.py` | `apply_rules` |
| State machine | `integrations/qoyod/state_machine.py` | `transition`, `ALL_STAGES`, `FAILURE_STAGES` |

---

*This contract is the source of truth for Make.com integrators. Any behavioural divergence in the running code is a bug — please file an issue.*
