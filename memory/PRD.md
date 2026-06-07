# PRD — MEZAN / ميزان (تطبيق محاسبي ذكي لمنصة سلة)

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية يدمج بيانات من Salla Excel + Make.com webhooks + Salla Direct API، يحسب المبيعات والعمولات والشحن وCOD، يدير الأصول والتسويات بشكل ذكي.

## Current Brand
- Name: **MEZAN / ميزان**
- Tagline: «منصة التحليلات والمحاسبة للتجارة الإلكترونية»
- Stack: React + FastAPI + MongoDB

---

## ✅ ITERATION 91 — Refund-Aware COGS + Order-Adjustments Audit (Feb 2026)

### Phase 1 — Effective Product Cost
- New helper `effective_product_cost(order, policy_overrides)` in
  `order_status_policy.py`. Rules:
  - cancelled / refunded → 0 COGS
  - full refund (actual_refund_amount > 0) → 0 COGS
  - partial refund → proportional reduction
  - confirmed / pending → unchanged
- Used by `server.py` Dashboard (line 1627) and
  `product_costs.py /summary` so refunded/cancelled orders no longer
  inflate the "تكلفة المنتجات" KPI nor depress reported profit.
- 12 pytests in `test_effective_product_cost_iter91.py`.

### Phase 2 — Resync Diff & Adjustments Log
- `resync_single_order` now:
  - snapshots `total_amount` + `products` before upsert,
  - calls `attach_cost_to_order_doc` after upsert (COGS recompute),
  - writes a row to a new `order_adjustments` collection when
    total_amount or items list differ (added / removed / modified).
- New endpoint `GET /api/order-adjustments` with pagination + filters
  (`order_number`, `reason`, date range).
- 12 pytests in `test_order_adjustments_iter91.py`.

### Phase 3 — Refund/Cancel deduction from expected_orders_balance
- Verified the chain `compute_metrics → reconciliation/summary →
  accounts.expected_orders_balance`. Refunds and cancellations are
  correctly excluded from `net` at the central source, propagating to
  every consumer (Reports / Reconciliation / Accounts).
- 5 integration pytests in `test_refund_assets_deduction_iter91.py`
  guard the behaviour against future regressions.

### Verified
- 29/29 new pytests PASS.
- Dashboard / Reconciliation / Orders / Adjustments / ProductCosts all
  respond 200 OK on Preview.
- No frontend changes — backend-only as requested by the merchant.

### New collection
`order_adjustments { id, user_id, order_number, reason, old_total,
new_total, delta_total, old_cogs, new_cogs, delta_cogs, items_changed,
total_changed, items_diff{added,removed,modified}, created_at }`

### New endpoint
`GET /api/order-adjustments?order_number=&reason=&from_date=&to_date=&page=&limit=`

---

## ✅ Previous Iterations (Iter-81 → Iter-90)

See git history + earlier PRD versions for full detail. Highlights:

- **Iter-90**: Settlement Cycle Settings + Health endpoint (PAUSED by
  user — diagnostic report task abandoned in favour of refund/COGS work).
- **Iter-89**: CPO on platform ad cards (Snap/Meta/TikTok).
- **Iter-88**: Webhook token health diagnostics + rotate UI.
- **Iter-87**: Order status update + manual resync.
- **Iter-86**: Orders Excel export with filters.
- **Iter-85**: Orders Explorer page (`/orders`).
- **Iter-84**: Rebranding to MEZAN.
- **Iter-83**: Order Status Policy + 4-category bucketing.
- **Iter-82**: Status-driven refunds (Tamara/Tabby).
- **Iter-81**: Centralized Payment Gateway Metrics.
- **Iter-74**: Phase 80 — Salla/Tamara/Tabby settlement file imports.
- **Iter-73**: Salla Direct OAuth + Sync.
- **Iter-68**: Phase 2.2 Reconciliation Screen.

---

## Backlog (User-Acknowledged Priority)

### P0 — Active
- None.

### P1 — On hold
- Smart Settlement Alerts UI (Phase C + D of Iter-90) — PAUSED by user.
- Full diagnostic report comparing `/api/reconciliation/summary` vs
  `/api/settlement-cycle/health` `transferred` data sources — PAUSED.

### P2 — Future
- Import actual settlement files for Tamara/Tabby (Phase 80 extension).
- UI for the new `order_adjustments` collection (currently API-only).
- Auto-detect order changes from Make.com webhook (not only via manual
  resync).

### P3 — Long-term
- Refactor `/app/frontend/src/pages/Reports.jsx` into smaller modules.

---

## Key Files Reference
- `backend/server.py` — main FastAPI app
- `backend/order_status_policy.py` — policy + `effective_product_cost`
- `backend/payment_gateway_metrics.py` — single source of truth
- `backend/reconciliation_routes.py` — reconciliation summary
- `backend/orders_explorer_routes.py` — /orders + /order-adjustments
- `backend/salla_integration/sync.py` — Salla API resync + diff
- `backend/product_costs.py` — product cost catalog + recompute
- `backend/accounts_routes.py` — payment platform account sync
- `backend/settlement_cycle.py` — Iter-90 cycle health (paused)

## Test Credentials
See `/app/memory/test_credentials.md`.
