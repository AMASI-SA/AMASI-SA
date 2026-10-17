# My products: persistent stop history

Owner request (2026-10-14): add a future mobile icon `المنتجات الموقوفة`
under `إدارة منتجاتي`. Keep records visible after the stop is resolved.
This change supplies the server contract only; mobile UI is a separate delivery.

## Read API

`GET /api/fulfillment-experiments-v1/holds/history`

- Authenticated existing session; no new credentials or permissions.
- `scope=mine` (default): only stops whose recorded employee IDs include the
  caller, and only that employee's product snapshots. `scope=all` requires owner
  or existing `fulfillment.stop.manage` permission.
- `status=all` (default), `active`, or `released`. The mobile icon MUST default
  to `all`, otherwise resolved products appear to disappear.
- `page=1`, `page_size=20` (maximum 100). `total` counts stop episodes, not unique
  products; repeated stops remain separate dated history entries. Each entry's
  `products` contains the affected physical pieces, grouped by `order_item_id`
  for a product card. Do not count physical pieces as distinct catalog products.
- Response: `items`, `total`, `page`, `page_size`, `has_more`, `scope`, `status`.
- Each entry includes stop ID, order number, type, reason, status, creation and
  release time/name/note, plus `products` and `snapshot_available`.
- Snapshots include product identity, image, options/specifications, physical
  piece ID, order item ID, preparation file, employee and supplier at stop time.
  They are historical display data, never authority to mutate current work.
- New stops from both customer-service tracking and direct operational-stop
  routes freeze the snapshots. Release leaves them intact. Reassignment or
  later removal of the live piece does not erase the new historical snapshot.
- Older stops remain visible without a data migration. `snapshot_available=false`
  means details are a best-effort current-piece lookup, not a historical claim.
  If pieces are unavailable or belong to another employee, `products` may be
  empty; retain the dated stop card and show `تفاصيل المنتج التاريخية غير متاحة`.

## Mobile behavior to implement later

Active badge `موقوف` / released badge `انتهى الإيقاف`. Show the reason, time,
responsible employee, supplier when present, and release details. Opening a card
uses `order_number` + `order_item_id` / `piece_id`; gracefully handle deleted
products. Historical entries must not disable ready actions; live operational
guards remain authoritative. There is no delete-history action and no TTL.

This does not implement Android navigation/icons, push delivery, Salla item
mutations, shipment checks, or accounting changes.
