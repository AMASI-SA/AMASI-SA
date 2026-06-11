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
- **Iter-137 (Feb 12 2026 — this session)**: Root-cause fix for the 12.34 SAR Tabby gap after Iter-134 redeploy.
  - **Problem**: After Iter-134 was deployed, the merchant's settlement still showed +12.34 SAR off (14,730.14 vs Tabby's actual 14,717.80) for the May 4-10 invoice.
  - **Root cause**: Two parts.
    1. `DEFAULT_FEE_RATES` in `settlements_service.py` was out of sync with `DEFAULTS` in `config_store.py` (commission_pct 5.00 vs 6.99 ; settlement_fee 5.0 vs 6.0 ; missing refundable_commission_pct).
    2. The line `rates.setdefault("refundable_commission_pct", commission_pct)` defaulted refundable rebate to FULL MDR (6.99%) for any bnpl_settings doc that predated Iter-134 — over-rebating every refund by 2.00 percentage points and inflating net_payable.
  - **Fix**:
    1. Synced `DEFAULT_FEE_RATES` with config_store.DEFAULTS (canonical Tabby: 6.99% / 4.99% / 1.00 / 6.00 / 15% / VAT-on-fee true).
    2. Fallback now uses per-provider canonical refundable_commission_pct (Tabby = 4.99%, Tamara = 7%) instead of full MDR.
    3. NEW: when `commission_mode == 'auto'` (default for all users), the engine DELIBERATELY ignores stored fee values in both `payment_methods` and `bnpl_settings` and uses the canonical defaults instead. This means any future Tabby contract changes reach every merchant on next sync without requiring them to re-save.
    4. Switching to `commission_mode = 'manual'` honours the saved values for the 6 lockable fields.
  - **Verification (live API on preview)**: GET `/api/bnpl/settlements/summary?provider=tabby` now returns `commission_pct=6.99`, `refundable_commission_pct=4.99`, `settlement_fee_per_invoice=6.0`, `fee_source=auto_canonical_defaults` — even though merchant's bnpl_settings doc still has stale 5.00 / 5.0 values from before Iter-134.
  - **Math validation**: Replaying the May 4-10 invoice (69 orders × 16,646.29 SAR, 534.72 SAR refunds) now yields net_payable 14,717.88 SAR vs Tabby's 14,717.80 → diff +0.08 SAR (within rounding tolerance).
  - Regression suite: 5 new pytest in `test_bnpl_iter137_refundable_fallback.py` + updated iter-126 tests for the new auto-mode contract. 20/20 BNPL pytest pass.
- **Iter-136 (Feb 12 2026 — this session)**: Admin purge endpoint for BNPL historical cleanup.
  - New endpoint `POST /api/bnpl/{provider}/admin/purge-before?cutoff=YYYY-MM-DD&dry_run=true`. Deletes rows STRICTLY before the Asia/Riyadh cutoff across `payment_transactions`, `payment_refunds`, `bnpl_settlements` and `unified_orders` (filtered by `source` or `payment_provider` = provider, AND user_id = caller).
  - Riyadh-local midnight is converted to UTC ISO upper bound (same Iter-130 convention).
  - Always defaults to dry_run=true; supports `dry_run=false` for real deletion.
  - Preview DB sanity check: 0 Tabby docs (all real data lives on production mezansalla.com). User must redeploy + call endpoint on production for any actual deletion.
  - 4/4 pytest in `test_bnpl_iter136_admin_purge.py` pass (dry-run counts, unknown provider 404, bad date 400/422, requires auth 401/403).
- **Iter-135 (Feb 12 2026 — this session)**: Asia/Riyadh default-dates across every input form.
  - New helper `/app/frontend/src/lib/dates.js` exposing `todaySA()` + `monthStartSA()` that add the +3h Riyadh offset before slicing the ISO date — eliminates the off-by-one-day bug between 00:00 and 03:00 KSA.
  - Replaced `new Date().toISOString().slice(0, 10)` and the legacy `monthStart` UTC slicing in 14 page components: AccountDetails, Accounts, Advances, AdAccounts (helper + 2× monthStart), BnplDiagnostics, BnplIntegrations, Dashboard, FinancialInputHub, OrdersDiagnostics, PurchaseInvoices, Receivables, SallaSourceComparison (`todayISO` + `daysAgoISO`), Settlements (modal + table), Transfers.
  - `format.js::todayISO()` had already been patched for Riyadh in a previous iter — leaving it as the back-compat wrapper.
  - Validated by testing agent on the preview URL: 7/7 hub date inputs, transfers modal, accounts add-account modal all prefill with the Riyadh date.
- **Iter-134-Auto (Feb 12 2026 — this session)**: BNPL commission_mode Auto/Manual toggle.
  - `bnpl_settings.commission_mode` ∈ {auto, manual}, default 'auto'. Persisted via `PUT /api/bnpl/settings/{provider}`; invalid values are dropped server-side.
  - `BnplIntegrations.jsx` new toggle row inside the "إعدادات الرسوم العقدية" card with `bnpl-{provider}-mode-auto` / `-mode-manual` testids. Two emerald/amber colored states + contextual hint banner.
  - On "Auto": frontend applies vendor-canonical `AUTO_PRESETS` to all 5 rate inputs (Tabby: 0.0699/1/0.15/0.0499/6 ; Tamara: 0.07/0/0.15/0.07/0) and disables them. On "Manual": inputs re-enable so the merchant can override per their specific contract.
  - Tabby preset MDR bumped from 0.06 → 0.0699 (matches the official Tabby invoice this merchant signed for).
  - 5/5 unit tests in `test_bnpl_iter134_auto_manual_mode.py` + 8/8 live API round-trip tests pass (verified by testing agent).
- **Iter-134 (Feb 11 2026 — previous session)**: Per-order commission + KSA VAT on settlement fee → MEZAN matches Tabby invoice to the cent (±0.05 SAR vs the ±13.29 SAR before).
  - Root cause of the residual 13.29 SAR gap was three things stacked: (1) aggregate commission rounding vs Tabby's per-order rounding; (2) Tabby refunds only the *refundable* slice of the commission on returns, not the full MDR; (3) Tabby's 6 SAR settlement transfer fee carries 15% KSA VAT that MEZAN never deducted.
  - Backend `settlements_service.py::compute_settlement_for_provider`: now iterates raw `payment_transactions` + `payment_refunds`, computing commission & VAT PER ROW with 2-dp rounding at every step. Added `settlement_fee_vat` line item to totals.
  - New settings on `bnpl_settings`: `refundable_commission_percent` (default 0.0499 for tabby, 0.07 for tamara) and `settlement_fee_vat_applicable` (default true).
  - `config_store.py` reads + persists the new fields; `_merchant_fee_rates` plumbs them to the settlement engine.
  - UI: new "🔬 الدقة المحاسبية المتقدمة (Iter-134)" section in `BnplIntegrations.jsx` with both editable fields; new "ض. رسوم التسوية" column in the `BnplSettlements.jsx` weekly table.
  - Simulated against the official Tabby May-4-10 Excel: 14,717.85 SAR vs actual bank deposit 14,717.80 SAR → diff 0.05 SAR (99.9997% accuracy).
  - 5/5 new pytest in `test_bnpl_iter134_per_order_commission.py` + 59/59 cumulative BNPL pytest pass without regression.
- **Iter-133b (Feb 11 2026)**: One-shot cleanup endpoint for the duplications that accumulated BEFORE Iter-133.
  - New `POST /api/ad-accounts/migration/cleanup-duplicates?dry_run=…`
  - Pass A: per (counterparty, date), keep ONLY the newest `breakdown.migration=True` ledger row; reverse impact (restore balance, shrink/delete migration liability by `uncovered`) of every older copy.
  - Pass B: per counterparty, MERGE duplicate open `source=ad_account_migration` liabilities into the newest (sum expected + paid).
  - Partially-paid liabilities: clamp `expected` to `paid` + status `paid` — no audit loss.
  - UI: new pink "🧹 تنظيف الترحيلات المُكرّرة" button in AdAccounts header (next to ترحيل المديونيات). First click → dry-run + confirm dialog. Confirm → apply.
  - 5/5 pytest in `test_ad_account_cleanup_iter133b.py` (dry-run, ledger pass, liability pass, partial-paid clamp, clean account).
- **Iter-133 (Feb 11 2026)**: Idempotent historical migration for ad-account debts.
  - Bug: re-running "ترحيل المديونيات التاريخية" on the same date range stacked duplicate ledger rows and liabilities → spend & debt doubled each retry.
  - Fix in `/app/backend/ad_account_routes.py::migration_apply`: before posting new rows, REVERSE any prior `breakdown.migration=True` ledger rows in the same range — restore the consumed `from_balance`, shrink (or delete) the open `source=ad_account_migration` liability by the prior `uncovered`, drop the old ledger rows. Same exact pattern as the existing `force=True` auto-sync reversal.
  - Liabilities that were partially paid off in the meantime are preserved: `expected_amount` is clamped to `paid_amount` and status flips to `paid` — no audit history is lost.
  - API now returns `reversed_prior_rows` per account; UI shows an amber notice + a "سُحب سابق" column in the result table.
  - Explainer card text updated to declare idempotency.
  - 2/2 new pytest in `test_ad_account_migration_iter133_idempotent.py` + 44/44 cumulative ad-account pytest still pass.
- **Iter-131 (Feb 11 2026)**: Weekly table المُحوَّل/المتبقّي show the matched bank transfer (not the cumulative 14-day window).
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
