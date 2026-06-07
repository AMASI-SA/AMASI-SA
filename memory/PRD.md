# PRD — MEZAN / ميزان (تطبيق محاسبي ذكي لمنصة سلة)

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية يدمج بيانات من Salla Excel + Make.com webhooks + Salla Direct API، يحسب المبيعات والعمولات والشحن وCOD، يدير الأصول والتسويات بشكل ذكي.

## Current Brand
- Name: **MEZAN / ميزان**
- Tagline: «منصة التحليلات والمحاسبة للتجارة الإلكترونية»
- Stack: React + FastAPI + MongoDB

---

## ✅ ITERATION 93 — Financial Position screen (Feb 2026)

### Scope
Read-only frontend page that aggregates existing endpoints. Zero new
collections, zero new logic, zero new calculations.

### Files added/edited
- `frontend/src/pages/FinancialPosition.jsx` — NEW (~270 LOC).
- `frontend/src/App.js` — +import +route `/financial-position`.
- `frontend/src/components/Sidebar.jsx` — +nav entry under
  "العمليات المالية" between `/reconciliation` and
  `/payment-settlements`.

### Data sources (existing endpoints only)
- `GET /api/liabilities/summary` → assets, liabilities, net_position,
  ad_provider breakdown, overdue_total.
- `GET /api/reconciliation/summary` → collection_rate, total pending,
  total expected, total transferred.
- `GET /api/liabilities?status=unpaid|partial` (count-only) → open
  liabilities count.

### KPIs shown
1. Net position banner (assets − liabilities = net).
2. Assets (banks / payment platforms / total).
3. Liabilities (salaries / ad accounts with by-provider hint / total).
4. Quick indicators (collection rate / pending / open count + overdue
   hint).
5. Quick links to Accounts, Reconciliation, Operating Expenses pages.

### Verified
- All 12 data-testids render on Preview.
- Numbers match `/api/liabilities/summary` exactly:
  net 432,214.24 = assets 455,314.24 − liabilities 23,100 (Preview env).
- No new backend code → no regression.

---

## ✅ ITERATION 92 — Liabilities Center Phase 1 (Feb 2026)

### Scope
- Single new collection `liabilities` modelling 3 obligation kinds:
  `salary`, `ad_account`, `salary_advance`.
- Reuses `operating_salaries`, `accounts`, `account_transactions`.
- No frontend, no extra pages, no prepaid/subscription/insurance support
  (deferred per merchant request).

### Endpoints (`/api/liabilities`)
- `POST /generate-salaries[?period=YYYY-MM]` — idempotent monthly generator.
- `POST /` — manual create (ad_account bill or salary_advance).
- `GET /` — list with filters (kind, status, ad_provider, period_key,
  employee_salary_id, from_due, to_due, pagination).
- `GET /summary` — Assets − Liabilities = Net financial position.
- `PUT /{id}` — edit expected/due_date/notes/description/label.
- `POST /{id}/pay` — record a payment, posts an `account_transactions`
  row, updates the bank balance.
- `DELETE /{id}` — safe delete (paid_amount must be 0 except for advances).

### Key invariants
- Idempotent salary generation via partial unique index
  `(user_id, kind="salary", employee_salary_id, period_key)`.
- Advances are recorded with `paid_amount = expected_amount` (cash left
  the bank already) and `advance_status: open|fully_consumed`.
- When a new salary obligation is generated for an employee, any open
  advances for that employee are auto-deducted (treated as pre-paid on
  the salary).
- Every `pay()` creates an `account_transactions` row of type
  `debt_payment` with `peer_liability_id` for traceability.

### Net Position formula
```
assets       = banks(current_balance) + payment_platforms(expected_orders_balance)
liabilities  = SUM(remaining_amount) over kind in {salary, ad_account}
                AND status != paid
net_position = assets - liabilities
```

### Files changed
- `backend/liabilities_routes.py` — NEW (450 LOC).
- `backend/server.py` — import + attach + index setup.
- `backend/tests/test_liabilities_iter92.py` — NEW (17 tests, all PASS).

### Verified
- 17/17 pytest PASS.
- Live test on merchant `amasi.jewelery@gmail.com`:
  - Bank: 40,000 SAR + Platforms: 415,314.24 SAR = **Assets 455,314.24 SAR**
  - Generated 7 salary obligations totalling 23,100 SAR
  - **Net position: 432,214.24 SAR** ✓

---

## ✅ ITERATION 91 — Refund-Aware COGS + Order-Adjustments Audit (Feb 2026)

See prior PRD entries.

---

## ✅ Previous Iterations (Iter-81 → Iter-90)

See git history + earlier PRD versions.

---

## Backlog

### P0 — Active
- None.

### P1 — Deferred (user paused)
- Smart Settlement Alerts UI Phase C/D (Iter-90).
- Reconciliation/SettlementHealth diagnostic report.

### P2 — Liabilities Phase 2 (future)
- Prepaid expenses with amortization (insurances, licenses, subscriptions).
- Subscription cycles (recurring liabilities for hosting/SaaS).
- Auto-fetch ad spend from Snap/TikTok/Meta APIs (currently manual).
- Liabilities UI page (currently API-only).

### P3
- Refactor `frontend/src/pages/Reports.jsx`.

---

## Key Files Reference
- `backend/server.py` — main FastAPI app
- `backend/liabilities_routes.py` — Iter-92 Liabilities Center
- `backend/order_status_policy.py` — policy + effective_product_cost
- `backend/payment_gateway_metrics.py` — single source of truth
- `backend/reconciliation_routes.py` — reconciliation summary
- `backend/orders_explorer_routes.py` — /orders + /order-adjustments
- `backend/salla_integration/sync.py` — Salla API resync + diff
- `backend/product_costs.py` — product cost catalog + recompute
- `backend/accounts_routes.py` — payment platform account sync
- `backend/expenses_routes.py` — operating_salaries / rentals / prepaids

## Test Credentials
See `/app/memory/test_credentials.md`.
