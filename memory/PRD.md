# PRD — Hesab (تطبيق محاسبي ذكي لمنصة سلة)

## Original Problem Statement
أريد بناء تطبيق محاسبي ذكي للتجارة الإلكترونية يقوم بتحليل ملفات Excel المصدرة من منصة سلة واستخراج وتحليل البيانات المالية تلقائياً.

## Architecture
- **Backend**: FastAPI + Motor (MongoDB async) — JWT auth (cookies + bearer), openpyxl Excel parsing, xlsxwriter Excel export, reportlab + arabic-reshaper for PDF export, httpx for Snapchat Marketing API.
- **Frontend**: React 19 + React Router 7 + TailwindCSS + Shadcn/UI + Recharts + @phosphor-icons/react.
- **Database**: MongoDB collections: `users`, `settings`, `daily_costs`, `analyses`, `snapchat_connections`.

## User Personas
1. **تاجر إلكتروني** يدير متجر على منصة سلة ويحتاج لتحليل الأرباح الحقيقية.

## Core Requirements (static)
- تحليل ملف Excel من سلة (المبيعات، عدد الطلبات، طرق الدفع، شركات الشحن).
- إدخال نسب عمولات الدفع وتكاليف الشحن من الإعدادات.
- حساب الأرباح الصافية بعد العمولات والشحن والإعلانات والمنتجات.
- تقارير تفصيلية لكل طريقة دفع ولكل شركة شحن.
- إضافة التكاليف اليومية (إعلانات سناب/تيك توك/إنستقرام + منتجات).
- حسابات منفصلة لكل مستخدم (auth + isolation).
- تصدير التقارير إلى PDF و Excel.

## Implemented (2026-05 — Per-Order Date Filtering Across Dashboard)
- ✅ **Excel parser:** picks up the order-creation date column even when the header reads "تاريخ إنشاء الطلب" (now in `DATE_COLS`). Falls back to **column Q (index 16)** when no header matches, matching Salla's standard layout.
- ✅ **Parser bug fix:** `_match_col` no longer false-matches empty header cells (an empty string is no longer treated as a substring of every candidate).
- ✅ **Parser bug fix:** preserves Excel-native date cells (datetime/date) by emitting ISO format instead of `str(datetime)` so the normalizer always succeeds.
- ✅ **Dashboard SSOT shift:** `GET /api/dashboard` now aggregates ALL KPIs (sales, fees, BNPL splits, electronic net, total shipping, deferred shipping, expected Salla transfer, VAT, balances, monthly trend) directly from `unified_orders` filtered by per-order `order_date`, NOT from `analyses.date`. A single upload spanning Jan/Feb/Mar correctly splits across months.
- ✅ **Startup backfill:** any pre-existing unified_orders documents with `order_date_raw` but missing `order_date` get their dates normalized on next backend start (idempotent, logs count).
- ✅ Three new regression tests cover: (1) header label "تاريخ إنشاء الطلب" detection, (2) column-Q fallback when header is unknown, (3) dashboard split-by-order-date when one upload contains multi-month orders. **107/107 backend tests pass.**

## Implemented (2026-05 — Phase 2: Advanced Filters)
- ✅ New shared component `AdvancedFilters.jsx`: date presets (today/yesterday/7d/30d/this-month/last-month/this-year/custom) + payment-methods multi-select + shipping-companies multi-select. Reusable across Dashboard and Reports.
- ✅ Backend `/api/dashboard` and `/api/balances` accept comma-separated `payment_methods` and `shipping_companies` query params and apply case-insensitive partial-match filtering.

## Implemented (2026-05 — Bug fix: Make.com orders inflating current month)
- 🐛 **Root cause** (found via Production diagnostic with the user's account): the webhook previously fell back to `datetime.now()` when Make.com sent a payload without `created_at`. This silently labeled March/April orders that Make.com forwarded today as "May orders", inflating the current month's KPIs by ~138 orders for the user.
- ✅ **Fix in `webhook_routes.py`**: removed the today fallback. Orders without `created_at` are now stored with `order_date=None` (still visible on the Make.com page, but excluded from date-filtered dashboard/reports queries).
- ✅ **Startup migration in `server.py`**: detects rows where `data_source=make` + `order_date_raw=''` + `order_date == received_at[:10]` (i.e. previously got the today-fallback) and clears their `order_date` to None. Idempotent; logs `cleared` count.
- ✅ **New endpoint** `GET /api/webhook/orders-missing-date` returns the orders that need attention.
- ✅ **`GET /api/webhook/stats` now exposes** `orders_missing_date` counter; ingest response exposes `without_date`.
- ✅ **UI warning banner** on `/make-webhook`: yellow banner displays count + Make.com fix instructions whenever `orders_missing_date > 0`.
- ✅ **5 new regression tests** in `tests/test_no_date_fallback.py` covering: no-fallback behavior, date-filter exclusion, stats counter, missing-date endpoint, correct-month routing.
- ✅ **126/126 backend tests pass.**

## Implemented (2026-05 — Phase 3: Net Sales Configuration)
- ✅ New Pydantic model `NetSalesConfig` (server.py) with 7 independent flags: `deduct_payment_fees`, `deduct_shipping`, `deduct_deferred_shipping`, `deduct_ads`, `deduct_product_costs`, `deduct_vat`, `deduct_daily_expenses`. Defaults reflect typical Salla seller workflow (deduct payment fees + regular shipping + ads + product costs; don't deduct VAT or deferred shipping).
- ✅ `GET /api/settings` now exposes `net_sales_config`; `PUT /api/settings` accepts and persists it. Backwards compatible (None preserves prior value; missing → defaults applied at read-time).
- ✅ `GET /api/dashboard` computes `totals.net_sales` based on the merchant's config and returns the active `net_sales_config` for the UI to show what's deducted.
- ✅ New Settings page section "حساب صافي المبيعات" with 6 toggles (deduct_daily_expenses kept hidden — folded into product_costs for now) and a live equation preview showing exactly what gets subtracted in real time.
- ✅ New KPI card `net_sales` in `dashboardCards.js` (group "sales") with accent styling and tooltip "حسب إعدادات الخصم". Auto-included in the dashboard-customization toggle list (21 cards now).
- ✅ Backend regression tests `tests/test_net_sales_config.py` (4 tests): default exposure, persistence, dashboard inclusion, custom-flag math verification. **121/121 backend tests pass.**


- ✅ **Single source of truth: `unified_orders` collection.** Both Excel uploads and Make.com webhook write here.
- ✅ New module `orders_db.py` with intelligent merge logic:
  - `_merge_into(existing, incoming, source)` — field-level merge.
  - Empty incoming never overwrites existing; empty existing accepts incoming.
  - Critical fields (`total_amount`, `order_status`, `payment_status`) → newer source wins.
  - Non-critical fields → first writer wins (preserve manual data).
  - `field_sources` dict tags each scalar with its writing source.
  - `data_sources` array records every source touching the order (capped 20 entries).
  - `data_source` field = last writer.
- ✅ **Excel parser extended**: `parse_salla_excel` now returns `orders_individual[]` with full per-row fields (customer_name, customer_mobile, subtotal, shipping_cost, discount, currency, source, status). 6 new column matchers added.
- ✅ **Upload-excel endpoint**: after report generation, upserts every parsed order to `unified_orders` with `data_source="excel"`. Returns `orders_imported` + `orders_updated` counters.
- ✅ **Make webhook** rewritten to use `upsert_order()` with `data_source="make"`. UTM fields (utm_source, utm_medium, utm_campaign, device) now persisted.
- ✅ **Build-analysis** reads from `unified_orders` so analytics aggregate across BOTH sources naturally (no double-counting via order_number dedup).
- ✅ **Stats endpoint** returns `by_source: {excel: N, make: M}` breakdown.
- ✅ **DELETE webhook settings** only deletes Make-sourced orders (preserves Excel rows).
- ✅ **One-time migration** on startup copies legacy `webhook_orders` → `unified_orders` (idempotent).
- ✅ **Frontend**: `MakeWebhook.jsx` renamed table to "آخر الطلبات الموحَّدة (Excel + Make.com)"; per-row colored Make/Excel badges + sky-blue "مدمج" chip when an order has been touched by both sources; new "Make / Excel" KPI card.
- ✅ **Testing**: **80/80 backend tests pass** (9 new tests covering bidirectional merge, field provenance, source isolation, build-analysis cross-source aggregation). Frontend Playwright fully green.

## Implemented (2026-05 — Make.com Webhook Source)
- ✅ **Second data source: Make.com webhook integration.** Salla → Make.com → /api/webhook/make/{token} → same DB → same reports.
- ✅ Backend module `webhook_routes.py`:
  - `POST /api/webhook/make/{token}` (PUBLIC, token-authed) — accepts single object, array, or `{orders: [...]}`. Upsert by `(user_id, order_number)` ensures no duplicates and supports updates.
  - `GET /api/webhook/settings` (JWT) — auto-creates token; returns webhook_url + sample payload.
  - `POST /api/webhook/settings/rotate-token` — invalidates old token immediately.
  - `DELETE /api/webhook/settings` — disconnect: removes token + all stored orders for the user.
  - `GET /api/webhook/orders` — list received orders (date_from/date_to/limit), DESC by order_date.
  - `GET /api/webhook/stats` — total_orders_in_db, total_received_ever, last_sync_at, date_range (earliest/latest).
  - `POST /api/webhook/build-analysis` — aggregates orders in [date_from, date_to] → `analyses` document with `source: "make"`, using the EXACT same `match_settings()` + `_build_report()` pipeline as Excel.
- ✅ MongoDB collections: `webhook_tokens` (unique on user_id + token), `webhook_orders` (unique on (user_id, order_number), index on order_date).
- ✅ `_orders_to_parsed()` bridges raw orders → `parse_salla_excel`-compatible dict, so the rest of the pipeline (dashboard, reports, daily costs, shipping accounts, BNPL fees, KPI cards) works unchanged.
- ✅ Liberal date parsing: handles YYYY-MM-DD, ISO 8601, DD/MM/YYYY, etc.
- ✅ Pydantic `Config.extra="allow"` + full `raw` JSON preserved on each order — no data loss from unknown Make.com mapping fields.
- ✅ Frontend page `/make-webhook` (`MakeWebhook.jsx`): copyable webhook URL, token rotate/disconnect, sample JSON payload, build-analysis form (date range + ads/products costs), recent-orders table, stats KPIs (stored/received/last sync/date range).
- ✅ Sidebar: new `ربط Make.com` link.
- ✅ Testing: **71/71 backend tests pass** (16 new webhook tests + 55 prior). Frontend Playwright fully green.

## Implemented (2026-05 — Deferred Shipping Companies)
- ✅ **Two-tier shipping**: each shipping company can be marked `is_deferred=true` in Settings.
  - Regular companies: cost deducted directly from sales (default behavior).
  - Deferred companies: cost still counted as expense (net_profit), but **not** deducted from the projected Salla→bank transfer.
- ✅ **New backend module** `shipping_accounts.py`:
  - `GET /api/shipping-accounts` — list each deferred company with total_owed (from analyses) + total_paid (from ledger) + remaining.
  - `GET /api/shipping-accounts/{company}/payments` — payment history (DESC by date).
  - `POST /api/shipping-accounts/{company}/payments` — record a payment {amount, payment_date, invoice_number, note}.
  - `DELETE /api/shipping-accounts/payments/{payment_id}` — undo a payment.
  - MongoDB collection `shipping_payments` with index `(user_id, company_name, payment_date desc)`.
- ✅ **Excel parser & report**: `match_settings()` now propagates `is_deferred` into each `shipping_breakdown` row and computes `deferred_shipping_cost` aggregate.
- ✅ **Dashboard**: new fields/KPIs `deferred_shipping_cost`, `regular_shipping_cost`, and `expected_salla_transfer = total_sales − total_payment_fees − regular_shipping_cost`.
- ✅ **Frontend page** `/shipping-accounts` (`ShippingAccounts.jsx`): summary KPIs (Owed/Paid/Remaining), per-company cards with progress bar, expandable payment ledger (with delete), and "add payment" modal.
- ✅ **Settings UI**: 14-column grid with a dedicated "آجل" checkbox per shipping row.
- ✅ **Sidebar**: new `حسابات الشحن الآجلة` link.
- ✅ Testing: **55/55 backend tests pass** (13 new shipping-accounts tests, 19 snapchat, 23 base). Frontend Playwright smoke green.

## Implemented (2026-02)
- ✅ JWT custom auth (register/login/logout/me) with httpOnly cookies + bearer token.
- ✅ Sidebar layout (RTL) — 6 صفحات: Dashboard, Upload, History, Daily Costs, Reports, Settings.
- ✅ Excel parser (auto-detects Arabic/English column names from Salla).
- ✅ Settings: edit payment commissions + shipping costs per company + VAT.
- ✅ Analysis creation with file upload + costs + matched commissions.
- ✅ Analysis result page: KPIs + Pie chart (payments) + Bar chart (shipping) + tables.
- ✅ Daily costs page: add/edit/delete by date (incl. Snapchat ×2, TikTok, Instagram, Google, مصاريف يومية).
- ✅ Reports page: aggregated across all analyses with charts and tables.
- ✅ History page with search & deletion.
- ✅ PDF and Excel export endpoints + frontend buttons.
- ✅ Dashboard with monthly trend (LineChart) and recent analyses + date range filter.
- ✅ BNPL (Tamara/Tabby) fees separated into a distinct KPI card.

## Implemented (2026-05 — Snapchat Integration)
- ✅ **Snapchat Marketing API OAuth integration** — per-user account connect:
  - Backend module `snapchat_routes.py` (separate from `server.py`):
    - `POST /api/snapchat/config` — save client_id / client_secret / redirect_uri (upsert)
    - `GET  /api/snapchat/config` — return status (without leaking client_secret)
    - `DELETE /api/snapchat/config` — disconnect
    - `GET  /api/snapchat/authorize-url` — build Snapchat OAuth URL with signed JWT state
    - `GET  /api/snapchat/oauth/callback` — handle code → exchange → store refresh_token; always redirects to `/settings?snapchat=success|error`
    - `GET  /api/snapchat/adaccounts` — list ad accounts (auto-refresh access_token)
    - `POST /api/snapchat/select-adaccount` — persist selected ad account
    - `GET  /api/snapchat/daily-spend?date=YYYY-MM-DD` — fetch daily spend (handles micro-currency conversion ÷1,000,000)
  - MongoDB: `snapchat_connections` collection, unique index on `user_id`.
  - State CSRF defense: signed JWT (10 min TTL) embedding user_id — no cookies/headers needed on callback.
  - Frontend Settings page: dedicated "ربط Snapchat Ads" card with App ID / App Secret (password) / Redirect URI fields + Connect/Disconnect/Re-connect + ad account picker.
  - Frontend Daily Costs page: small "Snap" button next to سناب شات input → calls `/snapchat/daily-spend` for selected date and auto-fills the field.
- ✅ **Bug fix — logout cookie deletion**: `clear_auth_cookies` now mirrors `set_auth_cookies` attributes (Secure, SameSite=None, HttpOnly, Path=/). Previously the deletion Set-Cookie had `SameSite=lax` → browsers ignored it → logout was a no-op. Verified end-to-end with a real browser via Playwright.
- ✅ **Bug fix — Recharts width(-1) warnings**: ResponsiveContainer now uses `width="99%" minWidth={0} minHeight={0}` across Dashboard / Reports / AnalysisResult.

## Backlog / Next
### P1
- Snapchat campaign creation (P1 from user; user explicitly deferred this).
- TikTok / Instagram Ads API direct integration (mirror Snapchat flow).
- Auto-fill Daily Costs for all platforms by date (one-click fetch).
- Multi-user team workspaces / sharing.
- Forgot password + email-based reset (currently console-logged).
- Currency localization (currently SAR hard-coded).

### P2
- Compare two analyses side-by-side.
- Profit per product analysis (requires line-items in Excel).
- Mobile responsive sidebar (currently hidden on small screens).
- Server-side refresh token revocation/blacklist (defense-in-depth on logout).

## Test Credentials
See `/app/memory/test_credentials.md`.
