# Salla Orders V3 — P0 shadow release

Production base: `1de6118484ac4fe1d0981e230618dbb573d8c58c`

This change deliberately stops before cutover. V3 is an isolated observer and
compatibility producer. Existing Order Review, Fulfillment, supplier files,
Qoyod, Snapchat attribution, campaign revenue, and dashboard totals continue to
read the current production order path.

## Root causes confirmed in the production base

1. `salla_integration/auto_sync.py::_discover_recent_orders` requests 60 rows,
   reads only page 1, and slices the response to 60. The documented List Orders
   maximum is 30, so newer orders can hide older updated orders on later pages.
2. `auto_sync.py::_reconcile_status_page` treats
   `len(rows) < requested_per_page` as exhaustion. A Salla cap below the
   requested size therefore resets the cursor after page 1.
3. `auto_sync.py::_sync_light_order` returns immediately for every locally
   existing order. An existing order with missing products or customer options
   is never repaired by that path.
4. `salla_integration/sync.py::run_orders_sync` persists List Orders Light rows
   without calling List Order Items. It can discover orders, but cannot provide
   authoritative products or customer choices.
5. The verified order webhook stores the base order snapshot but does not place
   a durable Order Items enrichment job. A one-time enrichment failure therefore
   has no order-scoped retry state.
6. Single-order implementations are duplicated across `sync.py`,
   `order_engine/salla_refresh.py`, and
   `order_commerce_enrichment.py`. Their parameters and shipment behavior differ;
   two Order Details calls omit explicit `format=light`.
7. `orders_db.py` preserves non-empty historical item arrays, but the caller
   cannot express four different states: authoritative empty success, endpoint
   failure, invalid payload, and endpoint not called. The current order upsert is
   also a read-merge-write sequence rather than a stale-versioned order write.
8. The page-triggered scheduler is called from Order Review and Order Engine
   reads. It is not an independent recovery service and uses in-process task and
   cursor dictionaries rather than a distributed lease.

## Existing paths audit

| Path | Discovery/internal id | Products/options | Persistence risk | Trigger |
|---|---|---|---|---|
| `auto_sync._discover_recent_orders` | `GET /orders`, Light, `row.id` | `GET /orders/items` only for a new local order | existing incomplete orders skipped; page 1 only | Order Review/Order Engine page reads |
| `auto_sync._reconcile_status_pages` | sequential `GET /orders` Light | never fetches items | short-page exhaustion uses row count | page-triggered background task |
| `sync.run_orders_sync` | paginated `GET /orders` Light | does not fetch items | Light row is mapped and saved | manual sync/background task |
| `sync.resync_single_order` | List Light then Order Details | `GET /orders/items`, plus shipment APIs | independent mapper/merge implementation | manual consumers, Qoyod preflight helpers, shipping helpers |
| `order_engine.salla_refresh` | List Light then Order Details | `GET /orders/items` | separate merge and freshness fields | order detail/review/engine routes |
| `order_commerce_enrichment` | List Light then Details Light | `GET /orders/items`, plus shipment path | fourth independent implementation | diagnostic/enrichment caller |
| verified business webhook | webhook payload id | no Order Items call | base order can remain incomplete | immediate webhook |
| `orders_db.upsert_order` | n/a | raw rich-item preservation | no authoritative-empty state or stale revision condition | every current Salla writer |

## V3 canonical boundary

- `salla_orders_v3/gateway.py`
  - `GET /orders?format=light&per_page=30`
  - sequential pagination driven by `currentPage`, `totalPages`, and `links.next`
  - `GET /orders/{internal_order_id}?format=light`
  - `GET /orders/items?order_id={internal_order_id}` as the only item source
  - bounded retry with exponential backoff
- `salla_orders_v3/normalizer.py`
  - one normalizer for list/dict option containers and scalar/dict/list values
  - supports `value`, `name`, `label`, `text`, `option_value`, `selected`,
    `choice`, and `answer`
  - preserves `0`, `false`, multiple selections, attachments, files,
    customizations, and personalization
  - stores both `raw_item` and a display-safe normalized item
  - uses the provider line id as the primary identity; SKU is never an identity
- `salla_orders_v3/compatibility.py`
  - emits the current top-level order fields and current `products/options` shape
  - keeps current campaign and UTM fields
  - adds audit-only V3 metadata without requiring a consumer schema migration
- `salla_orders_v3/worker.py`
  - independent recovery loop, Mongo lease, ten-minute overlap, bounded
    concurrency, metadata pagination, durable retry queue, and capped backoff
- `salla_orders_v3/shadow.py`
  - writes only isolated V3 snapshots with a 30-day TTL
  - snapshots are explicitly marked `shadow_only` and excluded from operational
    reads
  - optimistic `sync_revision` compare-and-swap retries prevent a slower writer
    from overwriting a newer snapshot
- `salla_orders_v3/diagnostics.py` and `parity.py`
  - strict Fulfillment, Qoyod dry-run, Attribution, and regression gates

Official Salla references used for this design:

- [List Orders](https://docs.salla.dev/5394146e0) — sequential pages and
  `per_page=30`
- [Order Details](https://docs.salla.dev/5394147e0) — Light excludes Items
- [List Order Items](https://docs.salla.dev/order-items/list) — complete items by
  `order_id`, requiring `orders.read`

## Shadow safety contract

`SALLA_ORDERS_V3_SHADOW_ENABLED` defaults to false. When explicitly enabled,
the startup worker and verified webhook observer can write only:

- `salla_orders_v3_shadow`
- `salla_orders_v3_events`
- `salla_orders_v3_jobs`
- `salla_orders_v3_sync_state`
- `salla_orders_v3_leases`

No V3 code mutates current order storage, inbox storage, accounting jobs,
preparation data, Salla order state, or advertising attribution. Static tests
enforce the two operational-collection prohibitions.

## Required pre-cutover proof

Cutover remains closed unless one report shows all of the following as passed:

1. Fulfillment parity: product count, quantity, `order_item_id`, SKU, options,
   and custom fields.
2. Qoyod parity: unchanged eligibility, exact dry-run invoice payload, and exact
   idempotency key; `provider_write_reached=false`.
3. Attribution parity: identical attributed/unattributed counts, campaign id,
   UTM fields, per-campaign revenue, and zero duplicate order numbers.
4. Fresh regressions for Order Review, Fulfillment, Qoyod, Snapchat attribution,
   and dashboard order totals.

The gate implementation is `salla_orders_v3.diagnostics.build_parity_report`.
No cutover switch or operational adapter registration exists in this change.

## Scope and real-data evidence status

On 2026-09-01 a signed-in, read-only inspection of the production Salla
integration page confirmed that the integration is connected and that its
stored scope includes `orders.read_write`. The scope diagnostic accepts either
the official read-only spelling `orders.read` or Salla's currently stored
combined spelling `orders.read_write`, reports which spelling was observed, and
never returns token fields. No OAuth reconnect is required for this change.

The same read-only page showed completed historical Orders sync runs, including
multi-page runs, but the V3 code is intentionally not deployed or enabled.
Therefore no real List/Details/Items triplet or Shadow-vs-current parity sample
exists yet. Those comparisons require enabling the isolated observer after
review and waiting for Shadow rows; they remain mandatory PR/cutover blockers
and are not inferred as passing from the existing sync history.

## Rollback

Rollback is operationally small because there is no cutover:

1. Set `SALLA_ORDERS_V3_SHADOW_ENABLED=false` and restart the backend.
2. Existing order readers and writers continue unchanged.
3. Retain the isolated shadow collections for audit until their TTL expires, or
   remove them later under a separately approved data-retention operation.
4. Do not delete or alter current order, accounting, preparation, or attribution
   records.
