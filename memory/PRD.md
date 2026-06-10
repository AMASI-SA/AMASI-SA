# MEZAN — Smart E-commerce Accounting App (PRD)

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية (MEZAN).
يحلّل ملفات Excel من سلة، ويستقبل بيانات من Make.com، ويتتبّع التسويات،
ويدير الأصول والالتزامات بمنطق القيد المزدوج، ويحسب المركز المالي.

## Critical Accounting Rules
- **Iter-120 — Refund-Date-Based Aggregation**: Sales by `created_at_provider`, refunds by `refunded_at`. Cross-period refunds land in the period of the refund, not the order.
- **Iter-121 — Weekday-Based Settlement Cycle**: Each provider's settlement period boundaries are driven by `invoice_weekdays` (multi-select) and `transfer_weekdays` (multi-select).
- **Iter-122 — Strict Separation between Issue Days & Transfer Days**: ONLY `invoice_weekdays` creates settlements. `transfer_weekdays` is read-only metadata used to compute `expected_transfer_date` and for matching — it never opens a new period or creates a row.

## User Profile
- Arabic-speaking merchant (عرفات — amasi.jewelery@gmail.com).
- Tests on production (mezansalla.com).
- Reminder: always **Save to Github → Redeploy** before testing.

## Architecture
- React + Tailwind frontend (RTL Arabic)
- FastAPI backend (motor / async MongoDB)
- Strict double-entry accounting
- Background asyncio tasks (BNPL hourly auto-sync)
- SSOT Balance service (`bnpl/balance_service.py`)

## Key Modules
- **BNPL Suite**: Tabby & Tamara clients, auto-sync, refund audit, weekly settlements with refund-date aggregation + invoice-weekday cycle, SSOT balances, auto-matching engine, period drill-down.
- **Financial Input Hub**: search-based counterparty + cumulative balance.
- **Reconciliation + Accounts + Transfers + المركز المالي**: All bound to BNPL SSOT.

## Completed Work
- **Iter-122 (Feb 2026 — this session)**: Strict issue-vs-transfer-day separation + empty-list bug fix.
  - Fixed: `transfer_weekdays=[]` was previously falling back to defaults (because `[]` is falsy in Python). Now distinguishes "field absent in DB" from "field present but empty list" — empty list is respected.
  - Test scenarios (6/6 pass): invoice=[Mon] → 1 settlement/week; invoice=[Mon,Thu] → 2 settlements/week; transfer days never create settlements; empty transfer=[] yields null expected_transfer_date; no invoice day in window → 0 rows.
- **Iter-121 (Feb 2026)**: Weekday-based settlement cycle.
- **Iter-120 (Feb 2026)**: Refund-Date-Based Aggregation + drill-down tables.
- **Iter-119 (Feb 2026)**: BNPL SSOT + auto-matching engine.
- Iter-118: Search-based counterparty + cumulative balance card.
- Iter-117: BNPL SSOT unification.
- Iter-116: Phase 4 weekly settlements UI.
- Iter-115: Configurable `settlement_fee_per_invoice`.
- Iter-114: Tabby MDR 5% + 1 SAR fixed fee.
- Iter-113: Refund Audit module.
- Iter-112: Hourly auto-sync.

## Outstanding User Notes
- **Tabby actual MDR for this merchant = 6.99%** (confirmed via real Tabby invoice 27 Apr → 3 May 2026). Default in code remains 5% but merchant overrides via settings UI.
- **Tabby Payout fee = 6 SAR** per invoice (also confirmed). Default in code = 5 SAR.

## Pending / Roadmap

### P0
- (none — Iter-122 verified)

### P1
- Apply Iter-120/121/122 rules to other settlement engines (Salla/Mada/Apple Pay/STC Pay/إمكان/Bank/COD) when their settlement engines are built.
- Iter-119 Phase 4-C: persist matches in `bnpl_settlement_matches` with manual override + audit trail.
- Iter-99 Phase 3: per-counterparty balance display inside dropdowns.
- Iter-99 Phase 4: migrate legacy string-based supplier names → `counterparty_id`.

### P2
- Provider invoice comparison tool — user pastes Tabby/Tamara invoice totals, system shows per-field diff.
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
- **Refunds**: from `payment_refunds.refunded_at`, NEVER `payment_transactions.refunded_amount`.
- **Settlement creation**: driven SOLELY by `invoice_weekdays`. `transfer_weekdays` is read-only metadata.
- **Empty list vs absent**: in DB, `[]` means user explicitly cleared. `key missing` means use defaults.
- **Cloudflare 524**: wrap long-running endpoints in `try/except` returning JSON.
- **Production access**: agent edits Preview only. User redeploys to push to mezansalla.com.
