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

## Critical Accounting Rules
- **Iter-120 — Refund-Date-Based Aggregation**: Sales by `created_at_provider`, refunds by `refunded_at`. Cross-period refunds land in the period of the refund, not the order.
- **Iter-121 — Weekday-Based Settlement Cycle**: Each provider's settlement period boundaries are driven by `invoice_weekdays` (multi-select) and `transfer_weekdays` (multi-select). Replaces the legacy `settlement_period_days = 7` numeric model.

## User Profile
- Arabic-speaking merchant (عرفات — amasi.jewelery@gmail.com).
- Tests on production (https://mezansalla.com).
- Always remind: **Save to Github → Redeploy** before testing.

## Architecture
- React + Tailwind frontend (RTL Arabic UI)
- FastAPI backend (motor / async MongoDB)
- Strict double-entry accounting
- Background asyncio tasks (BNPL hourly auto-sync)
- SSOT Balance service (`bnpl/balance_service.py`)
- mezan-table global UI standard

## Key Modules
- **BNPL Suite**: Tabby & Tamara clients, auto-sync, refund audit, weekly settlements with **refund-date-based aggregation (Iter-120)** + **weekday-based cycle (Iter-121)**, SSOT balances, **auto-matching (Iter-119)**.
- **Financial Input Hub**: search-based counterparty + cumulative balance card.
- **Reconciliation + Accounts + Transfers + المركز المالي**: All bound to BNPL SSOT.
- **Auth**: JWT + httpOnly cookie + Authorization header.

## Completed Work (timeline)
- **Iter-121 (Feb 2026)**: Weekday-based settlement cycle.
  - `bnpl_settings` now has `invoice_weekdays` + `transfer_weekdays` (canonical lowercase English: monday…sunday).
  - `compute_weekly_settlements` iterates by invoice weekdays — each period = (prev_invoice+1) → invoice_date.
  - `_count_settlements_in_period` counts actual weekday occurrences in window.
  - Default: Tabby = Monday close, Tue/Wed payouts; Tamara = Sunday close, Tuesday payout.
  - Each row includes new `expected_transfer_date` (first matching transfer weekday after invoice).
  - Frontend (`BnplIntegrations.jsx`): Two new checkbox groups (`WeekdayCheckboxes`) for invoice & transfer days.
  - Frontend (`BnplSettlements.jsx`): New "تحويل متوقع" column in weekly table.
  - Legacy `settlement_period_days` retained as fallback only.
  - 6/6 pytest in `test_bnpl_iter121_weekday_cycle.py`.
- **Iter-120 (Feb 2026)**: Refund-Date-Based Settlement Aggregation + two-table drill-down per period.
- Iter-119: BNPL SSOT + auto-matching engine.
- Iter-118: Search-based counterparty + cumulative balance card.
- Iter-117: BNPL SSOT unification.
- Iter-116: Weekly settlements UI + dynamic computation.
- Iter-115: Configurable `settlement_fee_per_invoice`.
- Iter-114: Tabby MDR 5% + 1 SAR fixed fee.
- Iter-113: Refund Audit module.
- Iter-112: Hourly auto-sync.
- Iter-110: mezan-table standard.

## Pending / Roadmap

### P1
- **Apply Iter-120 + Iter-121 rules to other settlement engines** (Salla/Mada/Apple Pay/STC Pay/إمكان/Bank Transfer/COD) when their settlement engines are built. The vocabulary + algorithm in `settlements_service.py` are provider-agnostic.
- **Iter-119 Phase 4-C**: persist matches in `bnpl_settlement_matches` with manual override + audit trail.
- Iter-99 Phase 3: per-counterparty balance display inside dropdowns.
- Iter-99 Phase 4: migrate legacy string-based supplier/ad-account names → `counterparty_id`.

### P2
- Unify payment-methods commission settings UI.
- Smart Settlement Alerts (Iter-90 Phase C/D).
- "الطلبات غير المتطابقة" page.
- Cache `get_bnpl_provider_balance` per request.

### P3
- Source priority matching rules (Salla > Make > Tamara > Tabby > Excel).
- Import actual Tamara / Tabby settlement files for secondary verification.

## Critical Notes for Next Agent
- **Language**: respond in Arabic.
- **BNPL balances**: ALWAYS via `get_bnpl_provider_balance` (SSOT).
- **Refund aggregation (Iter-120)**: pull from `payment_refunds.refunded_at`, NEVER from `payment_transactions.refunded_amount`.
- **Weekday cycle (Iter-121)**: keys are canonical lowercase English. UI maps to Arabic. `date.weekday()` = 0=Mon … 6=Sun.
- **Cloudflare 524**: wrap long-running endpoints in `try/except` returning JSON `{ success:false, error:... }`.
- **Production access**: agent edits Preview only. User redeploys to push to mezansalla.com.
- **Projection trap**: SSOT requires `account_type`, `provider_name`, `normalized_payment_method` in the doc projection.
