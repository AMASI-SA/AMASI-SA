# Manual production execution follow-up (WIP)

The prevention release in PR #1908 is merged, published and officially verified.
The already reviewed legacy projection has not been repaired. The operator's
/app environment uses Preview data and is not a production execution channel.

## Change contract

Expose the existing `build_products_projection_repair_plan` and
`apply_products_projection_repair` through the existing owner order detail.
The server must authenticate the real actor with `require_security_owner`,
including denial of a native employee principal shaped like its owner.

- `POST /api/orders-v2/{order_number}/products-projection-repair/preview`
- `POST /api/orders-v2/{order_number}/products-projection-repair/apply`
- Preview accepts only an absent/empty body; apply accepts only
  `{"manifest_sha":"<64 lowercase hex>"}`. No query/scope/force parameters.
- The owner comes from authentication; only the path order is passed to the
  existing repair functions. Preserve provider proof, manifest, CAS and owner
  transaction. Do not modify the engine or add resync/cache/fulfillment calls.
- Responses omit owner identity, customer text, raw/provider bodies and error
  internals. Return `Cache-Control: no-store` and stable refusal codes.
- Manual preview shows saved reporting item IDs/quantities versus current Salla
  items. Native order detail can already show the current smaller item list.
- Apply requires a ready preview with a pending change. Changing the order or
  account invalidates it; stale responses are ignored. A lost apply response
  requires a fresh preview, never automatic replay, including auth-refresh replay.
- Success refreshes only the local order read. No scheduler or automatic scan.

## Planned source ownership

Backend: `backend/salla_products_projection_repair_routes.py`, minimal wiring
in `backend/order_engine/routes.py`, and focused route tests. Frontend: a small
`ProductsProjectionRepair` component, existing order-detail/service wiring, and
component/service/page tests. Root owns workflow gates, documentation, integration
and release. No unrelated authorization allowlist changes are required.

## Verification and recovery

The missing routes were reproduced by three expected 404 test failures. The
execution environment then became unavailable; no follow-up green suite, governed
build, publication or live repair is claimed. Restore the remote checkpoint,
inspect any recovered source, complete tests and independent review, and create a
fresh protocol-v5 source/intent release. After verified publication: reviewed
one-order preview, explicit manifest-bound apply, second preview no-op, and the
original filtered report. Never substitute broader refresh/resync or a manual
DB edit for the existing repair's guarantees.
