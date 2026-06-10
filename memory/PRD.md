# MEZAN — Smart E-commerce Accounting App (PRD)

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية (MEZAN) يقوم بـ:
- تحليل ملفات Excel المصدّرة من منصة سلة
- استقبال البيانات من Make.com عبر Webhooks
- تتبع التسويات لـ Tamara و Tabby
- إدارة الأصول والالتزامات بمنطق القيد المزدوج
- لوحة مركز مالي (Assets - Liabilities = Net Position)

## Core Requirements
1. Accurate financial positioning (Assets − Liabilities = Net Position).
2. Unified entry point for all financial data (مركز الإدخال المالي).
3. Automating Ad Account liabilities based on daily spend.
4. Custom App Integration (API) to receive orders, products, and customers.
5. Managing counterparties, purchase invoices, and operational dashboards.
6. Daily salary accrual logic for employees.
7. Precise Bank transfer routing.
8. Comprehensive Operational Reports.
9. Tamara & Tabby BNPL Integration via API & Webhooks to fetch transactions/refunds and compute settlements.

## User Profile
- Arabic-speaking merchant (عرفات — amasi.jewelery@gmail.com).
- Tests on production (https://mezansalla.com), not Preview.
- Reminder: ALWAYS instruct user to "Save to Github → Redeploy" to push Preview changes to production.

## Architecture
- React + Tailwind frontend (RTL Arabic UI)
- FastAPI backend (motor / async MongoDB)
- Strict double-entry accounting
- Background asyncio tasks (BNPL hourly auto-sync)
- SSOT Balance service (`bnpl/balance_service.py`) for canonical BNPL balances
- mezan-table global UI standard for 60+ tables

## Key Modules
- **BNPL Suite**: Tabby & Tamara clients, auto-sync, refund audit, weekly settlements, SSOT balances, **auto-matching (Iter-119)**.
- **Financial Input Hub** (`/financial-input-hub`): Unified entry for new liabilities, pay liability (search-based + cumulative card), daily expense, transfers, COD, شركة شحن.
- **Reconciliation + Accounts + Transfers + المركز المالي**: All bound to BNPL SSOT (Iter-117 + Iter-119).
- **Auth**: JWT + httpOnly cookie + Authorization header.

## Completed Work (timeline of significant items)
- **Iter-119 (Feb 2026 — this session)**:
  - Phase 4-A: Closed all SSOT bypass gaps. `/accounts/summary`, `/liabilities/summary` (المركز المالي), and `/transfers` overdraw guard now consult `get_bnpl_provider_balance()`. Every Tabby/Tamara number is identical across 5 pages.
  - Phase 4-B: New `bnpl/matching_service.py` engine + `GET /api/bnpl/settlements/matching/{provider}`. Auto-matches each weekly invoice with an OUT bank transfer (window 14 days, tolerance max(2%, 3 SAR)). UI: new "المطابقة البنكية" column on Weekly Settlements + "تحويلات غير مُطابقة" section.
  - Bug fix (caught by testing agent): `transfers_routes.py` find_one() projection was missing `account_type` and `provider_name`, causing SSOT override to never trigger. Fixed → BNPL overdraw guard now actually rejects over-balance transfers.
- Iter-118 (Feb 2026): Removed working-days salary calc; added search-autocomplete for counterparty; cumulative balance card for selected counterparty.
- Iter-117: BNPL SSOT — unified balances across Accounts / Transfers / Reconciliation.
- Iter-116: Phase 4 weekly BNPL settlements UI + dynamic computation engine.
- Iter-115: Configurable `settlement_fee_per_invoice`.
- Iter-114: Tabby MDR corrected to 5% + 1.00 SAR fixed fee per order.
- Iter-113: BNPL Refund Audit module with Delta Diagnostics.
- Iter-112: Hourly BNPL Auto-Sync + Dev Mode toggle.
- Iter-111: Tabby backfill using offset/limit pagination.
- Iter-110: mezan-table standard applied to 60+ tables.

## Pending / Roadmap

### P0 — Verification Only
- **Cumulative balance feature** (Iter-118) verified on Preview ✅, awaiting production redeploy verification by user.

### P1 — In Progress
- **Iter-119 Phase 4-C (persisted matches)**: optional follow-up — persist matches in a `bnpl_settlement_matches` collection so the user can manually override / unlink + audit trail. Currently the engine is read-only and recomputes per call.
- Iter-99 Phase 3: per-counterparty balance display inside dropdowns.
- Iter-99 Phase 4: migrate legacy string-based supplier / ad-account names in `liabilities` → `counterparty_id`.

### P2 — Backlog
- Unify payment-methods commission settings UI + add `settlement_fee_per_invoice` field.
- Smart Settlement Alerts UI (Iter-90 Phases C & D).
- "الطلبات غير المتطابقة" page (orders in BNPL but missing in `unified_orders`).
- Cache `get_bnpl_provider_balance` per request (called twice per /accounts/summary today; not a perf issue but cleaner).

### P3 — Future
- Source priority matching rules (Salla > Make > Tamara > Tabby > Excel).
- Import actual Tamara / Tabby settlement files for secondary verification.

## Critical Notes for Next Agent
- **Language**: respond in Arabic.
- **BNPL balances**: always go through `get_bnpl_provider_balance` (SSOT), never recompute. After Iter-119 the five primary touchpoints (`/accounts/summary`, `/liabilities/summary`, `/accounts list`, `POST /transfers`, `/reconciliation/summary`) are all wired.
- **Auto-matching**: `bnpl/matching_service.py` is read-only. Window 14 days, tolerance max(2%, 3 SAR). Greedy, deterministic.
- **Cloudflare 524**: wrap long-running endpoints in `try/except` returning JSON `{ success:false, error:str(e) }`.
- **Production access**: agent can ONLY edit Preview. User redeploys to push to mezansalla.com.
- **Projection trap**: anywhere SSOT is consulted, ensure the doc projection includes `account_type`, `provider_name`, and `normalized_payment_method` — otherwise `is_bnpl_account()` returns None and the override silently fails.
