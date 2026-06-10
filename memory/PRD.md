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

## Key Modules (Implemented)
- **BNPL Suite**: Tabby & Tamara clients, auto-sync, refund audit, weekly settlements, SSOT balances.
- **Financial Input Hub** (`/financial-input-hub`): Unified entry for new liabilities, pay liability, daily expense, transfers, COD, شركة شحن, etc.
- **Cumulative Liability Balance**: Search-based counterparty picker + aggregated open-liabilities card (Feb 2026).
- **Reconciliation + Accounts + Transfers**: All bound to BNPL SSOT.
- **Auth**: JWT + httpOnly cookie + Authorization header.

## Completed Work (timeline of significant items)
- Iter-118 (Feb 2026): Removed working-days salary calc; added search-autocomplete for counterparty; cumulative balance card for selected counterparty (DONE & verified on Preview).
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
- (NONE) Cumulative balance feature verified on Preview ✅. Production redeploy pending user action.

### P1 — In Progress
- Phase 4 BNPL Automation: auto-match settlements against actual bank transfers (`settlements_service.py`).
- Iter-99 Phase 3: per-counterparty balance display inside dropdowns.
- Iter-99 Phase 4: migrate legacy string-based supplier / ad-account names in `liabilities` → `counterparty_id`.

### P2 — Backlog
- Unify payment-methods commission settings UI + add `settlement_fee_per_invoice` field.
- Smart Settlement Alerts UI (Iter-90 Phases C & D).
- "الطلبات غير المتطابقة" page (orders in BNPL but missing in `unified_orders`).

### P3 — Future
- Source priority matching rules (Salla > Make > Tamara > Tabby > Excel).
- Import actual Tamara / Tabby settlement files for secondary verification.

## Critical Notes for Next Agent
- **Language**: respond in Arabic.
- **BNPL balances**: always go through `get_bnpl_provider_balance` (SSOT), never recompute.
- **Cloudflare 524**: wrap long-running endpoints in `try/except` returning JSON `{ success:false, error:str(e) }`.
- **Production access**: agent can ONLY edit Preview. User redeploys to push to mezansalla.com.
