# MEZAN — Smart E-commerce Accounting App (PRD)

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية (MEZAN). يحلّل ملفات Excel من سلة،
يستقبل بيانات من Make.com، يتتبّع التسويات، يدير الأصول والالتزامات، ويحسب المركز المالي.

## Critical Accounting Rules
- **Iter-120 — Refund-Date Aggregation**: Sales by `created_at_provider`, refunds by `refunded_at`.
- **Iter-121 — Weekday-Based Settlement Cycle**: `invoice_weekdays` + `transfer_weekdays`.
- **Iter-122 — Strict Issue-vs-Transfer Separation**: Only `invoice_weekdays` creates settlements.
- **Iter-123 — Period Start Convention**: `invoice_weekday` is the FIRST day of a period. Period spans `[invoice_weekday, next_invoice_weekday - 1]`. Issue date = next invoice_weekday (when provider generates statement).
- **Iter-130 — Asia/Riyadh Local Time Window**: Saudi local YYYY-MM-DD is converted to a UTC ISO window (`-3h` on each side) before filtering Mongo. Matches how Tabby/Tamara cut off invoices at Saudi midnight.

## User Profile
- Arabic merchant (عرفات — amasi.jewelery@gmail.com).
- Tests on production (mezansalla.com).
- Always remind: **Save to Github → Redeploy** before testing.

## Architecture
- React + Tailwind frontend (RTL Arabic)
- FastAPI backend (motor / async MongoDB)
- Strict double-entry accounting
- Background asyncio tasks (BNPL hourly auto-sync)
- SSOT Balance service

## Key Modules
- **BNPL Suite**: clients, auto-sync, refund audit, weekly settlements (refund-date + weekday cycle + period-start), SSOT balances, auto-matching, period drill-down.
- **Financial Input Hub**: search-based counterparty + cumulative balance.
- **Reconciliation + Accounts + Transfers + المركز المالي**: bound to BNPL SSOT.

## Completed Work
- **Iter-131 (Feb 11 2026 — this session)**: Weekly table المُحوَّل/المتبقّي now reflect the SPECIFIC matched bank transfer (not the 14-day cumulative sum).
  - Problem: row showed `transferred_amount = 27,141.91` (cumulative window) while the actual single matched transfer was 14,717.80 → confused users into thinking there's a 12k overpayment.
  - Fix in `BnplSettlements.jsx`: when `matchByInv[invoice_no].match_status === 'matched'`, override `transferred_amount` with `matched_transfer.amount`, recompute remaining as `net_payable - matched_transfer.amount`. Totals row sums `matchTotals.matched_amount` instead of the backend's cumulative `totals.transferred_amount`.
  - Added `data-testid` attributes: `bnpl-weekly-transferred-{n}`, `bnpl-weekly-remaining-{n}`, `bnpl-weekly-transferred-total`, `bnpl-weekly-remaining-total`.
  - Verified: page renders, all test-ids present in DOM, no lint issues.
- **Iter-130 (Feb 11 2026)**: Asia/Riyadh timezone fix for settlements.
  - Reproduced production discrepancy: Tabby invoice (4-10 May) gross = 16,646.29 / 69 orders, MEZAN showed 16,232.46 / 68 orders → exact gap = 413.83 SAR.
  - Root cause: `settlements_service` filtered Mongo `created_at_provider` / `refunded_at` strings with the raw user date treated as UTC midnight, dropping orders captured in the last 3 UTC hours of the prior Saudi day.
  - Added `_local_date_window_utc(date_from, date_to)` helper (`-3h` on each side, no DST).
  - Applied to `_compute_provider_totals` (sales + refunds) and `_compute_period_items` (sales + refunds).
  - Simulation against the official Tabby Excel: 69 sales / 16,646.29 SAR / 4 refunds / 534.72 SAR — matches official invoice **exactly**.
  - 9/9 new pytest in `test_bnpl_iter130_riyadh_timezone.py` + 54/54 cumulative BNPL pytest still pass.
  - Awaiting user redeploy on `mezansalla.com`.
- **Iter-123 (Feb 2026)**: Period Start Convention.
  - Period now spans `[invoice_weekday, next_invoice_weekday - 1]` (Mon→Sun for default Tabby/Tamara).
  - New row field `issue_date` = next invoice_weekday (the day provider generates the statement file).
  - `expected_transfer_date` is computed from `issue_date`, not `period_end`.
  - 4/4 new pytest in `test_bnpl_iter123_period_start.py`.
  - 22/22 cumulative pytest passes (Iter-120 + 121 + 122 + 123).
  - Frontend: new "تاريخ الإصدار" column in BnplSettlements weekly table.
- **Iter-122 (Feb 2026)**: Strict separation + empty-list bug fix.
- **Iter-121 (Feb 2026)**: Weekday-based settlement cycle.
- **Iter-120 (Feb 2026)**: Refund-Date-Based Aggregation + period drill-down.
- **Iter-119 (Feb 2026)**: BNPL SSOT + auto-matching engine.
- Iter-118: Search-based counterparty + cumulative balance.
- Iter-117: BNPL SSOT unification.
- Iter-116: Phase 4 weekly settlements UI.
- Iter-115: Configurable `settlement_fee_per_invoice`.
- Iter-114: Tabby MDR 5% + 1 SAR fixed fee.
- Iter-113: Refund Audit module.
- Iter-112: Hourly auto-sync.

## Outstanding User Notes
- **Tabby actual MDR for this merchant = 6.99%** (confirmed via real Tabby invoice). Default in code = 5%.
- **Tabby Payout fee = 6 SAR** per invoice. Default in code = 5 SAR.

## Pending / Roadmap

### P1
- Build "Provider Invoice Comparison Tool" — paste Tabby/Tamara invoice numbers → system shows per-field diff + suggests setting corrections.
- Apply Iter-120/121/122/123 rules to other settlement engines (Salla/Mada/Apple Pay/STC Pay/إمكان/Bank/COD).
- Iter-119 Phase 4-C: persist matches in `bnpl_settlement_matches`.
- Iter-99 Phase 3: per-counterparty balance in dropdowns.
- Iter-99 Phase 4: migrate legacy string-based supplier names → `counterparty_id`.

### P2
- Unify payment-methods commission settings UI.
- Smart Settlement Alerts.
- "الطلبات غير المتطابقة" page.

### P3
- Source priority matching rules.
- Import actual Tamara / Tabby settlement files for secondary verification.

## Critical Notes for Next Agent
- **Language**: respond in Arabic.
- **BNPL balances**: ALWAYS via `get_bnpl_provider_balance` (SSOT).
- **Refunds**: from `payment_refunds.refunded_at`, NEVER `payment_transactions.refunded_amount`.
- **Settlement creation**: driven SOLELY by `invoice_weekdays`. `transfer_weekdays` is metadata.
- **Period convention** (Iter-123): `invoice_weekday` is the START of the period. `to` is the day BEFORE the next invoice weekday. `issue_date` is the next invoice weekday after `to`.
- **Timezone** (Iter-130): All BNPL date-window filters go through `_local_date_window_utc()`. Inputs are Saudi-local YYYY-MM-DD; outputs are UTC ISO. **Never** filter `created_at_provider` / `refunded_at` with raw YYYY-MM-DD again.
- **Empty list vs absent in DB**: `[]` = user cleared. `key missing` = use defaults.
- **Cloudflare 524**: wrap long-running endpoints in `try/except` returning JSON.
- **Production access**: agent edits Preview only. User redeploys.
