# PRD — MEZAN E-commerce Accounting App

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية (MEZAN) يحلل ملفات Excel من سلة، يستقبل بيانات Make.com، يتتبع التسويات، ويدير الأصول والالتزامات.

## Architecture
- **Backend:** FastAPI + Motor (Async MongoDB)
- **Frontend:** React + react-router + Tailwind + shadcn/ui
- **SSOT:** `general_ledger` (double-entry accounting). All balances computed from GL via `compute_balance()`.
- **Storage:** `MONGO_URL` / `DB_NAME` from env. No defaults.
- **Auth:** JWT (role-based: owner/admin/accountant/operations/viewer/user)
- **Language:** Arabic (RTL UI)

## Key Collections
- `general_ledger` — SSOT (double-entry, txn_group_id)
- `financial_movements` — detailed movements (supplier_invoice, general_expense, fixed_asset)
- `operating_salaries` — modern employee storage
- `employees` — legacy employee collection
- `counterparties` — suppliers / externals / couriers
- `accounts` — bank / cash / payment_platform
- `expense_categories` — hierarchical
- `unified_orders` — Salla orders pipeline
- `settlement_files` / `settlement_entries` — Salla/BNPL reconciliation

## Strict Rules from User
- READ-ONLY on existing financial data
- No migrations / no recompute / no cleanup without explicit permission
- All balances from `general_ledger` only
- `financial_movements` is detail-enrichment layer, never balance source
- Drift detected MUST be surfaced, never hidden

## Ads V2 Bank Commission Display (2026-06-25 · iter-257)
**User directive (strict):** FREEZE the Snapchat spend sync logic — current
USD spend + FX conversion is canonical. NO changes to:
- FX rate fetching
- Spend calculation
- Snapchat API call (post the recent adapter fix)
- Sync mechanism

**What user needs visible in the report:**
1. الصرف بالدولار (Spend USD)
2. الصرف بالريال قبل العمولة (Spend SAR pre-commission)
3. نسبة العمولة البنكية (Bank commission %)
4. قيمة العمولة البنكية (Bank commission SAR)
5. إجمالي التكلفة بعد العمولة (Total after commission)

**Implementation (display-only — sync untouched):**
- `/app/backend/ads_v2/data_layer/reports.py`:
  - `get_spend_by_account` now exposes `spend_native`, `bank_fee_pct`
    (derived = bank_fee_sar / spend_sar × 100), and `configured_bank_fee_pct`
    (from settings, audit-only). `totals.spend_native_by_currency` for
    multi-currency aggregation.
  - `get_spend_by_provider` similarly extended with `bank_fee_pct` and
    per-currency native breakdown.
  - All derived from `ads_daily` SSOT — no recomputation, no new
    sync paths.
- `/app/frontend/src/pages/AdsV2Report.jsx`:
  - 5 StatCards (USD card auto-hides when no USD spend).
  - Per-account table: 10 columns including `spend_native` (USD/native)
    and `bank_fee_pct` (effective %).
  - Per-provider table: includes `bank_fee_pct`.
  - `ReportTable` now has `renderCell()` that formats pct and native
    currency with proper suffixes and tooltips.

**Verified scenario (user-provided Snapchat numbers):**
Given `spend_native=105.41 USD`, `spend_sar=395.76`, `bank_fee.rate_pct=0.023`:
- `bank_fee_sar` = round(395.76 × 0.023, 2) = **9.10** ✅
- `gross_sar`    = 395.76 + 9.10        = **404.86** ✅
- Effective `bank_fee_pct` derived from SSOT = **2.299** ≈ 2.30% ✅

**Tests** (`/app/backend/tests/test_ads_v2_bank_fee_report.py` — 5/5 ✅):
- `test_snapchat_bank_fee_matches_user_scenario`
- `test_effective_bank_fee_pct_matches_configured_rate`
- `test_bank_fee_disabled_returns_zero`
- `test_pct_plus_flat_method`
- `test_report_layer_returns_new_fields`

**Live aggregation curl test:** Inserted a temp `ads_daily` row matching
the user scenario; `get_spend_by_account` returned exactly the 6
required fields with the correct numbers.

**⚠️ Sync code is frozen — any future change to spend or FX requires
explicit user approval.**



## Dashboard Shipping SSOT Consolidation + Accordion UX (2026-06-25 · iter-256)
**User report:** ProfitSummaryCard's "إجمالي تكاليف الشحن" total included VAT
but the inline table only showed unit price WITHOUT tax (e.g. iMile 21×15 was
shown but total was 362.25). Also: clicking operating-expenses tooltip felt
like content jumped below the page.

**Fix — Backend (`/app/backend/server.py` ~line 1939):**
- `/api/dashboard` now passes `all_orders` through
  `shipping_cost_ssot.aggregate_breakdown()` and OVERRIDES
  `matched_all["shipping_breakdown"]` / `["total_shipping_cost"]` /
  `["deferred_shipping_cost"]` with the SSOT result.
- Each shipping row now carries SSOT-canonical per-unit fields:
  `cost_per_unit`, `tax_per_unit`, `total_per_unit`, `vat_rate`
  (alongside the legacy fields kept for backward-compat).
- Net effect: the dashboard and `/api/shipping-ledger` now use the
  SAME math source — never any drift.

**Fix — Frontend (`/app/frontend/src/components/ProfitSummaryCard.jsx`):**
- Replaced the hover-only tooltips on الشحن & المصروفات التشغيلية with
  **inline accordion sections** (`expandable`/`expanded` props on `Line`).
- Click the row → accordion expands inline directly below the row.
  Click again → collapses. The rest of the summary stays visible.
- Shipping table columns are now identical to ShippingLedger:
  الشركة · الشحنات · سعر الوحدة (بدون الضريبة) · ضريبة الوحدة (VAT) ·
  إجمالي الوحدة (سعر + ضريبة) · الإجمالي.
- Footer reads: "نفس مصدر دفتر الشحن التفصيلي (shipping_cost_ssot.py)".

**Regression tests** (`/app/backend/tests/test_dashboard_shipping_ssot.py` — 3/3 ✅):
- Verifies SSOT per-unit math matches the user-reported numbers (21 × 15
  → 362.25; 2 × 15 → 34.50).
- Asserts the dashboard source code contains the consolidation block and
  all SSOT canonical fields.
- Verifies `is_deferred` summation stays correct after SSOT consolidation.

**Smoke verified on PREVIEW:**
- `/api/dashboard` returns rows with `cost_per_unit`, `tax_per_unit`,
  `total_per_unit`, `vat_rate` populated. Example:
  سمسا (2040 oc, cpu=25.0, tpu=3.75, total_per_unit=28.75, total=58,539.60).
- Clicking the shipping line in ProfitSummaryCard expands the breakdown
  inline directly below.

**Note for user:** PREVIEW only. Redeploy via Emergent to push to
`mezansalla.com`.



## Snapchat Adapter Bug Fixes (2026-06-25)
**Reported by user via Diagnose UI on two Snapchat accounts:**
1. `efcdd251 (Self Service)` → API error:
   `Unsupported Stats Query: Only field 'spend' should be used when querying AdAccount stats.`
2. `cf8ea7c9 (السعودي)` → API error:
   `Invalid query parameters in request URL: [Invalid StartDateTime, 2026-06-24T00:00:00.000 03:00]`
   (the '+' in the +03:00 timezone offset became a space)

**Root cause (both bugs in `/app/backend/ads_v2/sync/adapters.py::fetch_snapchat_day`):**
- The Snapchat API endpoint `/adaccounts/{id}/stats` ONLY accepts the
  `spend` field at the account level. Asking for impressions/swipes is rejected.
- The URL was built with f-string interpolation
  (`f"...&start_time={start_iso}&end_time={end_iso}..."`), so the
  `+03:00` timezone offset stayed as a literal `+` in the URL — which
  is decoded by HTTP servers as a space character (RFC 3986 query rules).

**Fix:**
- Removed `impressions,swipes` from the field list; account-level
  endpoint now requests `fields=spend` only.
- Switched from f-string URL to httpx `params=` dict so `start_time`
  and `end_time` are URL-encoded correctly (`+` → `%2B`).
- `impressions`/`clicks` set to `0` in the returned row (Snapchat does
  not expose those at account level — would need campaign-level later).

**Regression tests:**
- `/app/backend/tests/test_snapchat_adapter_fix.py` (3 tests, all PASS):
  - asserts source no longer has the buggy URL shape
  - end-to-end captures the final httpx URL and verifies `%2B` encoding
  - verifies spend parsing from a stub response works

**Note for user:** This fix is on PREVIEW. To apply on PRODUCTION
(`mezansalla.com`), the app must be redeployed from the Emergent
platform.


- Arabic-only UX (RTL)

## Implemented in this Session (Iter-250b · P1.5)

### P1.5.L — BNPL Internal-Transfer Block (deployed)
- Block bank/cash → Tamara/Tabby in `/new-transaction`
- Backend guard in `universal_accounting_routes._account_blocks_internal_transfer`
- Frontend filter in `UnifiedEntryScreen.isInternalTransferIneligible`
- Salla NOT blocked

### P1.5.n — Employee Lookup Forensic (deployed)
- `GET /api/audit/employee-lookup?entity_id=...&name_hint=...`
- Read-only diagnostic: matches across `operating_salaries`, `employees`, `employees_archive`, `employees_legacy`

### P1.5.o — Preview Debug Overlay (deployed)
- Shows selected employee {id, name, monthly_amount, source} ONLY on Preview/localhost
- Helps verify frontend bindings

### P1.5.p — Widened Employee Guard (deployed)
- `ledger_core.create_entry` guard now checks BOTH `operating_salaries` AND `employees`
- Rejects `archived=true / deleted=true`
- Accepts `status=active|stopped`
- 7/7 unit-test PASS

### P1.5.q — Custody as Payment Source for Operating Expenses (deployed)
- New perm `accounting.custody.spend_any` (owner/admin/accountant/user role)
- `POST /api/financial-movements` accepts `custody_employee_id` for `general_expense`
- New endpoint `GET /api/accounting/custody/spendable-sources`
- UI toggle in `Iter245MovementForm`: bank/cash vs employee custody
- Custody balance check, no overdraft, single source per transaction
- Custody-funded movements credit `employee.custody` in GL (no bank touched)
- Reversal restores custody balance via standard GL reversal

### P1.5.r — Entity Ledger Deep-Link Route (deployed)
- Route `/entity-ledger/:type/:id`
- Page `EntityLedgerByIdPage` auto-opens drawer for the matching entity
- Backward-compat: `/entity-ledger/supplier/:id` now redirects to `/suppliers/:id/ledger-detail`

### P1.5.s — Supplier Ledger Detail Page (deployed)
- Backend: `GET /api/accounting/suppliers/{id}/ledger-detail?from=&to=`
- 7 sections: supplier card, drift banner, period summary, chronological timeline, invoice cards (with line items + GL legs + payments), manual entries, drift diagnostic
- Frontend: `/suppliers/:id/ledger-detail` page
- Print/PDF via `react-to-print`
- Filters: YTD (default), current month, last 90d, all, custom
- SSOT-strict: all balances from GL, `financial_movements` for detail only

### P1.5.t — Movements↔GL Drift Analyzer (deployed)
- `GET /api/audit/movements-gl-drift?from=&to=&movement_type=`
- Categorises every drifted movement into 6 causes:
  legacy_pre_gl / gl_creation_failed / no_group_id_at_all / voided_or_draft / import_batch / manual_legacy_data
- Roll-ups by cause, supplier, year
- Read-only — pure diagnostic, no writes

## P1.5.s.fix — Supplier Ledger Cash-Invoice Reclassification (2026-02 · READ-ONLY)
Backend: `supplier_ledger_detail_routes.py` reconciliation block now classifies orphans into 3 buckets:
  - `cash_invoices` — paid_amount ≥ total_amount AND GL exists for the group_id (just no payable leg). Valid postings, NOT drift.
  - `drift_credit` — paid_amount < total_amount AND no supplier-payable leg in GL. Real drift, needs review.
  - `ledger_failed` — no GL row at all for group_id (or status=ledger_failed). True GL post failure.
Period block exposes `total_cash_purchases` + `cash_invoices_count`. `drift_detected` no longer fires on cash invoices.
Frontend: `SupplierLedgerDetailPage.jsx` now renders 3 separate sections (📗 / 🟠 / 🔴) instead of one «orphan» bucket. New summary card «إجمالي مشتريات نقدية». Excel export splits to 3 sheets.

## Phase 2A.5 — Provider Invoice Calendar (2026-02 · CORE FIX)
**Problem solved:** Tamara Dry-Run used arbitrary ISO-week buckets from order_date, so simulated invoice_date diverged from real Tamara dates (23/05, 30/05, 06/06, 13/06, 20/06).

**User-confirmed Tamara cycle:** invoice issued Saturday → period covers Saturday → next Friday (`invoice_date` is the FIRST day of the 7-day period, not the last). All real invoice dates are Saturdays.

Backend:
  - New module `provider_invoice_calendar.py`:
      • Per-provider `_PERIOD_LAYOUTS`:
          – `tamara` → `"invoice_as_start"` (Sat → Fri).
          – `tabby`/`imkan`/`salla` → `"invoice_as_end"` (legacy).
      • Overridable via `settings.calendar_period_layout_<provider>`.
      • `extract_calendar_from_settlement_entries`: walks distinct `settlement_date` rows. For `invoice_as_start`, period_start=invoice_date, period_end=invoice_date+6. For `invoice_as_end`, period_start=prev_invoice+1 (or invoice-6 for first), period_end=invoice_date.
      • Transfer offset depends on layout: Tamara `invoice_as_start` defaults to 9 days (Mon after Fri end). Tabby `invoice_as_end` defaults to 1.
      • `rebuild_calendar` (idempotent, preserves manual entries).
      • `upsert_manual_entry`, `delete_entry`.
  - New collection: `provider_invoice_calendar` with `(user_id, provider, invoice_date)` unique key.
  - Modified `_simulate_weekly` (Dry-Run): when calendar exists → uses calendar periods exactly; orders bucket by `period_start ≤ order_date ≤ period_end`. Surfaces `invoice_date` + `expected_transfer_date` per invoice. Falls back to ISO-week buckets only when calendar is empty.
  - Modified `_build_bnpl_periods` (Phase 2B): identical change — calendar → `compute_settlement_for_provider(period_start, period_end)` per entry.
  - Rule resolution (commission/VAT) **still** comes from `_merchant_fee_rates` — calendar only governs period boundaries.

Endpoints:
  - `GET    /api/settlement-engine/calendar?provider=&from_date=&to_date=`
  - `POST   /api/settlement-engine/calendar/rebuild` body `{provider, dry_run}`
  - `POST   /api/settlement-engine/calendar/manual` body `{provider, invoice_date, period_start, period_end, expected_transfer_date}`
  - `DELETE /api/settlement-engine/calendar/{id}`

Frontend: `SettlementDashboard.jsx`
  - New tab "📅 تقويم الفواتير" — provider picker, **dynamic layout badge** (Tamara: "تاريخ الفاتورة = أول يوم الفترة (السبت → الجمعة)" vs Tabby: "= آخر يوم الفترة"), rebuild button, manual-add form, per-invoice table with source badge, delete action.
  - Dry-Run modal table now shows columns: تاريخ الفاتورة + تاريخ التحويل المتوقع (real calendar dates).

Tests: `tests/test_iter251_phase2a5_invoice_calendar.py` — 5/5 PASS.
  - Layout-aware extraction (2026-05-23 → period 23-29), idempotent rebuild, manual-entry protection, end-to-end Dry-Run uses calendar with Sat→Fri buckets, delete.

## Phase 2B — Settlement Engine Generation (2026-02 · FEATURE-FLAG GATED)
Backend:
  - New module `settlement_engine_generation.py` — pure generation logic that delegates rule resolution to:
      • `bnpl.settlements_service.compute_weekly_settlements` + `_merchant_fee_rates` for Tamara/Tabby (same source as `/bnpl-settlements/register`).
      • `db.settlement_entries` grouped by `settlement_reference` for Salla.
      • `imkan` returns `rule_source_missing` (no central rules yet — no hard-coded fallback).
  - New collections: `settlement_periods`, `settlement_invoices`, `expected_transfers` (linked via FK ids).
  - Invoice lifecycle: draft / generated / waiting_transfer / pending_review / confirmed / confirmed_with_difference / cancelled.
  - All writes gated by `settings.settlement_engine_enabled` (defaults OFF). 403 returned when disabled.
  - `dry_run=true` ALWAYS allowed (no persistence) — for the merchant to preview output safely.
  - Idempotent on `(user_id, provider, period_from, period_to)`: re-runs reuse existing ids.
  - No GL writes, no bank_transfer_review creation here. Phase 2C will wire those in.

Endpoints (under `/api/settlement-engine`):
  - `POST /generate` — body `{provider, date_from, date_to, dry_run}` → counts + ids
  - `GET  /periods?provider=&status=&from_date=&to_date=`
  - `GET  /invoices?provider=&status=&from_date=&to_date=`
  - `GET  /invoices/{id}` → invoice + period + expected_transfer
  - `POST /invoices/{id}/cancel` body `{reason}`
  - `GET  /expected-transfers?provider=&status=`
  - `GET  /stats` → totals + per-provider counts

Frontend: `SettlementDashboard.jsx`
  - Two tabs: 🔬 Dry-Run | 📦 Generated Invoices (Phase 2B)
  - Generated tab shows: feature-flag banner (ON / OFF), counts, generation form (provider, date range, Dry-Run / Generate buttons), invoices table with status badges, cancel action.
  - "Generate" button disabled when `settlement_engine_enabled` flag is OFF.

Tests: `tests/test_iter251_phase2b_settlement_generation.py` — 6/6 PASS.
  - Block when flag OFF, dry-run persistence-free, Salla persistence + linking, idempotency, cancel transitions, unknown-provider rule_source_missing.

## Pending / Backlog
- [P0] Analyze Tamara settlement JSON (26,279.64 vs 10,509.12 SAR discrepancy) — waiting for user to re-paste
- [P1] Analyze Production drift report from P1.5.t (waiting for user output)
- [P1] Phase 2 — Custody as payment source for supplier_invoice (cash mode)
- [P1] Phase 3 — Unified Employee Custody Ledger (chronological timeline of all custody movements per employee)
- [P1] Ad-Account sync stopped for Snapchat (Riyadh) — needs forensic
- [P1] Read-only forensic for `/purchase-invoices` and `/shipping/transfers`
- [P1] Walid / Khatai employee balance analysis (read-only)
- [P2] Execute Financial Reset / Ad-Account Recompute (postponed)
- [P2] Category Reports & Expense Analysis Dashboard
- [P2] Product Linkage (Inventory, SKUs)
- [P2] Phase 2 of Supplier Unification — provide a (gated) "Link Ledger-only supplier to db.suppliers" action once user reviews the forensic report

## P1.5.ab — Suppliers Unification (2026-02 · READ-ONLY)
Backend: `suppliers_unification_forensic_routes.py`
  - `GET /api/suppliers-unified` — merged list (db.suppliers + db.counterparties + GL/FM ghosts) with `link_status` ∈ {new_only, linked, ledger_only}, `editable` flag, GL balance per row
  - `GET /api/audit/suppliers-unification-forensic` — full diagnostic dump: counts, lists per category, ghosts (GL/FM IDs missing from both tables), duplicate suspects by name/phone/email
Frontend:
  - `SuppliersPage.jsx` Management tab now calls `/suppliers-unified` instead of `/suppliers` and shows badges + summary cards + link-status filter
  - `SuppliersUnificationForensicModal.jsx` — modal with 6 tabs (نظرة عامة، مورد جديد، Ledger فقط، مربوط، GL/FM أيتام، تكرارات مُشتبه بها)
Tests: `tests/test_p15ab_suppliers_unification_forensic.py` — 3/3 PASS

## Phase 4 — Product Cost Auto-Update on Supplier Invoice (2026-02)
Backend: `financial_movements_routes._apply_product_cost_updates`
  - Hook fires after a `supplier_invoice` movement is successfully posted to GL.
  - Walks every `line_items[i].product_id`; for each match:
    - Appends a new `cost_history` record with `{supplier_id, supplier_invoice_id, invoice_date, quantity, unit_cost, total_cost, source: "supplier-invoice", amount, at}`.
    - Sets `cost_current = unit_cost` (latest).
    - Recomputes `cost_avg` as quantity-weighted average across all history entries that carry `quantity`+`unit_cost`.
    - Sets `needs_cost = false`.
  - APPEND-ONLY: never deletes or overwrites prior history entries (excel-import / quick-create seeds preserved).
  - Failures are logged but never break invoice creation.
Frontend: `Iter245MovementForm.jsx` payload now sends `product_id` + `product_sku` per line item.
Tests: `tests/test_iter250b_phase4_product_cost_update.py` — 5/5 PASS. End-to-end curl verified weighted avg ((10×15 + 30×7) / 40 = 9.0).


## Test Credentials
See `/app/memory/test_credentials.md`.

## Shipping Cost SSOT — Priority Flip + Warning Banner (2026-06-25, iter-254/255)
**Bug fix:** User reported that the detailed shipping ledger was using
Salla's per-order shipping_cost even for companies that had a
configured `cost_per_order` in `/shipping/settings`. The new policy:

**Priority 1 — company-config `cost_per_order`** (the rate the merchant
maintains in `/shipping/settings`).
**Priority 2 — Salla `shipping_cost`** ONLY when no system cost is
configured (temporary fallback; UI surfaces a warning).

**SSOT change** (`shipping_cost_ssot.py::shipping_breakdown`):
   The `if order_ship>0 else cfg_cost` branch was flipped to
   `if cfg_cost>0 else salla_ship`. Source field now reports
   `company_config | salla | none`.

**Warning system** (`shipping_ledger_routes.py`):
   Per-company breakdown now sets `uses_salla_fallback = (
   from_salla_count > 0 AND configured_cost <= 0)`. Each affected
   company yields a structured warning emitted in the top-level
   `warnings` array:
       {shipping_company, orders_affected, reason,
        message: "شركة الشحن … لا يوجد لها سعر في إعدادات شركات الشحن…"}
   The frontend renders an amber banner above the per-company table
   with a "الانتقال إلى إعدادات شركات الشحن" link.

**Coverage** — all 4 consumers now go through SSOT:
   1. ✅ `/api/shipping-ledger` (detailed orders + per-company)
   2. ✅ `/api/shipping-accounts` (deferred-liability accrual) —
        `compute_owed_per_company` rewritten to call
        `shipping_breakdown` per order (replacing the inline
        `cost*(1+vat)` formula that bypassed SSOT priority).
   3. ✅ `/api/balances` (Phase-1 splits via `compute_balances`)
   4. ✅ `/api/financial-position` (same balances wiring)

**Frontend** (`ShippingLedger.jsx`):
   - Amber warning banner with per-company message + settings link
   - "من سلة (مؤقت)" badge on Salla-fallback rows
   - Per-company row gets amber tint when fallback active

**Tests:**
   - `test_shipping_cost_ssot.py` — 15/15 PASS (priority flip
     verified in unit tests).
   - `test_shipping_accounts_ssot_iter255.py` — 6/6 PASS
     (legacy `/shipping-accounts` path now SSOT-consistent).
   - Verified by `testing_agent_v3_fork` iter-254 (100% backend +
     frontend) and iter-255 (100% backend, 21/21 tests). Critical
     proof scenario: Salla=999, settings=20 @ 15% VAT → owed=23.00
     per order (not 1148.85).

## Shipping Cost SSOT — Base + Tax + Total (2026-06-25)
**User mandate:** every shipping-cost figure in the app uses
`total = base + tax`, with the three values visible separately. No
default VAT% is fabricated for historical data; the actual configured
`vat_percent` per shipping company in `/shipping/settings` is the only
source of truth.

**New module:** `/app/backend/shipping_cost_ssot.py` exposes:
   - `shipping_breakdown(order, company_cfgs) → {base, tax, total,
     vat_rate, source}`  – one shipment.
   - `aggregate_breakdown(orders, company_cfgs)` – list aggregation
     with per-company stats (cost_per_unit, tax_per_unit,
     total_per_unit).
   - `get_company_configs(db, user_id)` – reads from the canonical
     `settings.shipping_companies[]` array and accepts BOTH
     `vat_rate` (decimal) AND `vat_percent` (0–100). Name lookup is
     resilient to casing + accidental quoting (`'مندوب الرياض'`).

**Latent bug fixed:** previous `shipping_accounts.py` read
`cfg.get("vat_rate")` but settings stored `vat_percent`. So VAT was
silently treated as 0 everywhere. Now both fields are recognised.

**Refactored consumers (all calls go through SSOT):**
   - `balances.py::compute_balances` — new optional `company_cfgs`
     param. Without it, no fake default VAT (legacy callers unchanged).
   - `shipping_accounts.py::compute_owed_per_company` — returns
     `shipping_base`, `shipping_tax`, `shipping_cost` (= base+tax)
     per company + totals.
   - `shipping_ledger_routes.py::shipping_ledger` — rows now expose
     `shipping_base`, `shipping_tax`, `shipping_cost`,
     `shipping_vat_rate`. Per-company block adds `cost_per_unit`,
     `tax_per_unit`, `total_per_unit`.
   - `server.py` — both `/balances` and `/financial-position` callers
     pass `company_cfgs` so FP, P&L, executive summary, and balances
     align with the rule.

**Frontend:**
   - **Shipping Ledger detail page** (`ShippingLedger.jsx`): 6-column
     per-company table — الشركة · عدد الشحنات · سعر الوحدة (بدون الضريبة)
     · ضريبة الوحدة (مع %) · إجمالي الوحدة (سعر + ضريبة) · الإجمالي.
     Order rows split shipping_base / shipping_tax / shipping_cost.
     Summary cards split: "إجمالي سعر الشحن" + "إجمالي ضريبة الشحن"
     + "إجمالي تكلفة الشحن (شامل الضريبة)".
   - **Profit Executive Summary** (`ProfitSummaryCard.jsx`): same
     6-column table for the analysis-report shipping breakdown.

**Historical-data preservation:** the SSOT helper recomputes live
reports from the current `vat_percent` setting. POSTED `general_ledger`
entries are never mutated — they keep whatever VAT was applied when
they were originally written. Reports = dynamic, journal = immutable.

**Tests:** `tests/test_shipping_cost_ssot.py` — 13/13 PASS,
   including the bug-fix cases (`vat_percent` recognised, malformed
   inputs, historical preservation when no cfg present).

## Ads V2 — Snapchat Safe Re-link Flow (2026-06-25)
**Resolved user request:** "زر إعادة ربط Snapchat داخل تقرير التشخيص"
with 7 explicit safety constraints. All 7 are enforced + tested.

**New collection:** `ads_v2_pending_tokens`
   - Stores new tokens in isolation from V1 until the merchant
     explicitly approves them. Schema: `{id, user_id, provider,
     status (awaiting_callback|pending|approved|discarded),
     access_token, refresh_token, expires_at, source (oauth|
     manual_paste), comparison_snapshot, created_at, updated_at}`.

**New backend module:** `/app/backend/ads_v2/relink.py`
   Routes (all under `/api/ads-v2/settings/snapchat/relink`):
   - `POST /start` → returns Snapchat OAuth URL (state JWT carries the
     V2 purpose marker `ads_v2_snapchat_relink`).
   - `POST /manual` → fallback path for pasting tokens directly.
   - `GET /pending` → list (never returns the secret tokens).
   - `POST /{id}/compare` → live probes both old V1 token and new
     pending token; returns side-by-side identity + organizations +
     ad_accounts + can_access_self_service + can_access_riyadh + diff.
   - `POST /{id}/approve` → backs up V1 doc into `legacy_versions[]`
     array, then atomically swaps `access_token`/`refresh_token` to
     the new pending values. Audit logged in `ads_sync_logs` as
     event `account_relinked_v1`.
   - `POST /{id}/discard` → soft-marks discarded (kept for audit).

**OAuth handshake (zero new redirect URI needed):**
   The V2 flow reuses V1's `client_id`/`client_secret`/`redirect_uri`.
   The V1 OAuth callback (`/api/snapchat/oauth/callback`) was extended
   with a single dispatch check: if the JWT state has
   `purpose=ads_v2_snapchat_relink`, the request is handed off to
   `relink.handle_v2_relink_callback()` which writes ONLY to
   `ads_v2_pending_tokens`. V1 callback's existing logic untouched
   for legacy states.

**Snapchat API probe (`_probe_snapchat_token`):**
   Queries `/me`, `/me/organizations`, `/organizations/{id}/adaccounts`.
   Heuristically detects "Self Service" and "Riyadh" access by
   matching name patterns. Returns a normalized snapshot used by
   both `/compare` and the cached `comparison_snapshot` field.

**Frontend (`AdsV2Settings.jsx`):**
   - `RelinkSnapchatPanel` — shown inside the Diagnose dialog
     only when `provider==='snapchat' && token in
     ['needs_relink','expired','missing']`. Two CTAs: "بدء OAuth"
     and "إدخال يدوي (احتياطي)".
   - `RelinkComparisonView` — two-column side-by-side compare with
     org/account lists, Self Service / Riyadh access indicators,
     diff summary (added/removed orgs and accounts), red callout if
     the new token loses any access, then "اعتماد" / "تجاهل"
     buttons. The approve button is disabled if new token isn't
     valid (probe returned `unauthorized`).
   - `useEffect` reads `?relink_pending_id=...` from URL after OAuth
     round-trip and auto-loads comparison.

**Safety invariants (all in pytest):**
   1. ✅ V1 NOT touched by `/start` — verified `test_relink_start_does_not_touch_v1`
   2. ✅ V1 NOT touched by `/manual` — verified `test_relink_manual_stores_pending_v1_untouched`
   3. ✅ V1 NOT touched by `/compare` — verified `test_compare_does_not_modify_v1`
   4. ✅ V1 NOT touched by `/discard` — soft-discard only — verified `test_relink_discard_is_soft`
   5. ✅ `/approve` appends `legacy_versions[]` AND atomically swaps —
        verified `test_approve_appends_legacy_and_swaps`
   6. ✅ Pending without access_token CANNOT be approved (returns 404)
        — verified `test_approve_awaiting_callback_returns_404`
   7. ✅ Tokens never leak in list endpoints — verified
        `test_relink_pending_omits_secrets`

**Tests:** `/app/backend/tests/test_ads_v2_snapchat_relink.py` — 8/8 PASS.
   Total ads_v2 tests: 39/39 passing (relink + diagnose + auto-reconcile
   + drift + phase1).

## Ads V2 — Phase 1 (3-Tier Status + Diagnostics) (2026-06-25)
**User complaint resolved:** Snapchat row showed "Token: OK" but
"Status: خطأ" — paradoxical and uninformative. Replaced with a
3-tier per-account status model + a Diagnose button.

**Backend:**
   - `data_layer/settings.py::_compute_account_status()` — returns
     `{token, connection, connection_reason, sync_run, reason,
       days_with_data_30d, last_sync_finished_at, last_sync_error}`
     where each tier has its own controlled vocabulary:
       - **token:** ok / expired / needs_relink / missing
       - **connection:** connected / unreachable / timeout / api_error / unknown
       - **sync_run:** synced / awaiting_first / no_data / last_failed / disabled
   - `_compute_account_status` mixes V1 token health + recent sync_logs
     api_status + ads_daily row count to produce a structured `reason`
     code (e.g. `token_no_access_to_account`, `no_data_for_account`,
     `awaiting_first_sync`, `api_rate_limit`). Translated to Arabic
     in the UI dictionary `REASON_AR`.
   - `data_layer/settings.py::diagnose_account()` — comprehensive
     read-only diagnostic. Includes:
       - Token check (V1 doc presence)
       - **Live API probe** — calls `adapters.fetch_day` for yesterday
         and records the result (code, body excerpt, fetched spend)
       - ads_daily stats: days_in_last_30d, days_with_spend,
         total_daily_rows, last_synced_date, last sync started/finished
       - Last 10 ads_sync_logs events for the account
   - **POST /api/ads-v2/settings/accounts/{id}/diagnose** — Returns
     the full diagnostic in one payload.

**Frontend (AdsV2Settings.jsx):**
   - Accounts table replaced bare "Status / Token" columns with:
     **حالة التوكن / حالة الاتصال / حالة المزامنة / السبب الحقيقي**
     (4 columns, colored badges, never says bare "خطأ").
   - New **"تشخيص"** button per account → opens a Dialog displaying:
     3-tier badges, primary reason callout, stats grid, live API probe
     result + raw response body excerpt, last 10 events.
   - `EVENT_AR` dictionary translates event names (sync_run → "مزامنة
     ناجحة", reconciliation_checked → "مطابقة من المنصة", etc.).
   - `ActivityRow` component renders each event as a clean Arabic
     summary instead of raw JSON dump.
   - `REASON_AR` translates 17 specific reason codes (e.g.
     `token_no_access_to_account` → "التوكن لا يملك صلاحية هذا الحساب",
     `no_data_for_account` → "الحساب لا يحتوي على بيانات صرف").

**Tests:** `tests/test_ads_v2_account_diagnose.py` — 8/8 PASS,
   including the exact "Token OK + no data" case the user described,
   which now produces `reason='no_data_for_account'` instead of
   bare "error". Total 31/31 ads_v2 tests pass.

## Ads V2 — Phase 1 (Auto-Reconcile, Final) (2026-06-25)
**Resolved User 5-point Conditional Approval:**
1. ✅ **API-driven auto-fetch, manual demoted to fallback** —
   New endpoint **POST /api/ads-v2/report/auto-reconcile** body
   `{dates:[...], account_ids?:[...]}` re-queries every enabled
   (account × date) from its provider API and stores the freshly-
   fetched figure in **shadow** fields `platform_authoritative_native`,
   `platform_authoritative_sar`, `platform_last_checked_at` — without
   touching `spend_native` (the SSOT row stays stable for Phase 2
   review). Manual entry endpoint kept but UI button renamed
   "إدخال يدوي (احتياطي)".
2. ✅ **Enhanced reconciliation report fields** — Per (account, date):
   `spend_native/sar` (ads_daily), `platform_authoritative_*` (current
   API), `diff_native`, `diff_sar` (signed), `drift_pct_vs_platform`,
   `drift_reason.likely_causes` (Arabic), `confidence`,
   `last_synced_at`, `platform_last_checked_at`, `match_status`.
3. ✅ **Unified Meta/Snapchat/TikTok** — Single `auto_reconcile_user()`
   loop dispatches through `adapters.fetch_day()`. Token-missing path
   degrades to `match_status='sync_failed'` (no 500).
4. ✅ **Phase 2 boundary intact** — Zero writes to `general_ledger` and
   zero `ledger_txn_group_id` on any ads_daily row. Verified by
   dedicated invariant tests post auto-reconcile.
5. ✅ **Status indicators 🟢🟡🟠🔴⚪** — New `_compute_match_status()`
   returns one of `matched / pending_platform / drift_review /
   sync_failed / no_data` (priority order: failed > no_data >
   drift_review > pending_platform > matched). UI renders 5-card
   legend at top of reconciliation tab + colored badge per row with
   emoji icon.

**Backend additions:**
   - `core.py`: `_compute_match_status`, `auto_reconcile_for_day`,
     `auto_reconcile_user`. `run_sync_for_account` now also sets
     `match_status` on every sync (and `sync_failed` when fetch fails).
   - `reports.py`: reconciliation rows expose new fields + summary
     histogram (`match_matched`, `match_pending_platform`,
     `match_drift_review`, `match_sync_failed`, `match_no_data`).
   - `routes.py`: POST `/report/auto-reconcile` (bulk) and
     `/report/auto-reconcile/account/{id}/day/{date}` (single).

**Frontend (AdsV2Report.jsx):**
   - Blue button "إعادة المطابقة من المنصات" beside green sync button.
   - Default tab is now "المطابقة" (recon).
   - 5-card legend (MatchStatCard) showing counts per status with
     colored borders matching the indicator color.
   - 6 new table columns: الحالة (with emoji badge), قيمة المنصة الآن,
     قيمة Ads Manager (يدوي), الفرق (SAR), سبب الفرق, آخر مزامنة,
     آخر فحص للمنصة.
   - Dictionary `MATCH_STATUS_AR` maps backend status → icon + Arabic
     label + Tailwind color classes.
   - Manual dialog re-labeled "إدخال يدوي (احتياطي)" + explanatory
     banner pointing users to the auto-reconcile button.

**Tests:** `tests/test_ads_v2_auto_reconcile.py` — 6/6 PASS;
   `tests/test_ads_v2_auto_reconcile_invariants_iter253.py` — 5/5 PASS;
   Phase 1 + drift regressions — 17/17 still PASS. Total 28/28.
**Verified by testing_agent_v3_fork (iter-253):** Backend 100%,
   Frontend 100%, all 5 demands satisfied. **Phase 1 ready for final
   user sign-off.**

## Ads V2 — Phase 1 (Final, post-rejection fix) (2026-06-25)
**Resolved User Rejection (3 demands):**
1. ✅ **Full Arabic UI** — Replaced all English UI terms (Reconciliation,
   Drift, Flags, Confidence, Status, Pending, Provisional, Final, Source,
   Layer, Sync, Token, OK, active, paused, discovered) with proper Arabic
   via dictionaries in `AdsV2Report.jsx` (PROVIDER_AR, REVIEW_STATUS_AR,
   CONFIDENCE_AR, ANOMALY_AR, DRIFT_CAUSE_AR) and `AdsV2Settings.jsx`
   (`statusAr()` helper). Only platform names (Meta/Snapchat/TikTok)
   remain in English.
2. ✅ **Contrast & font-weight upgrade** — Stat cards: `text-3xl
   font-extrabold tabular-nums text-zinc-50` (was text-2xl font-bold
   text-zinc-100). Table cells: `text-zinc-50 font-semibold`. Backgrounds
   stay zinc-900/950. Verified by test agent.
3. ✅ **Meta discrepancy 36.06 SAR** — Adopted "merchant-as-ground-truth"
   model:
   - **POST /api/ads-v2/report/manual-value** `{account_id, date,
     manual_value_native, note?}` → records the Ads Manager value
     entered by the merchant and **recomputes drift instantly** (no
     provider re-fetch). Audit row appended to `ads_sync_logs`.
   - Reconciliation rows now expose `platform_manual_value_native/_sar`,
     `has_manual_value`, `drift_pct_vs_manual`, and structured
     `drift_reason.likely_causes` (sync_before_close,
     late_reporting_window, ads_manager_value_differs,
     post_close_provider_update, missing_fx_rate).
   - `_compute_anomaly_flags` returns **`None` (not 0.0)** for drift
     when there is no comparison anchor → frontend renders "—".
     Eliminates the "false 0% drift" issue.
   - Meta adapter (`adapters.py`) upgraded with
     `use_account_attribution_setting=true`,
     `use_unified_attribution_setting=true`, `limit=500`,
     `account_currency` & `date_start/date_stop` echoed back, ensuring
     numbers track Ads Manager's stated attribution.
**Frontend additions:**
   - `ManualValueDialog` component — Per-row "إدخال قيمة Ads Manager"
     button → modal entry with native-currency value + optional note;
     on save calls the new endpoint and refreshes recon view.
   - `ReconRow` shows colored drift % (emerald/amber/red) ONLY when a
     comparison exists; em-dash otherwise; likely-causes printed as
     Arabic captions beneath the % value.
**Tests:** `tests/test_ads_v2_drift_logic.py` — 7/7 PASS (drift NULL
   when no anchor, manual-value endpoint persistence, reconciliation
   field exposure, no_drift_inflation invariant). Phase 1 regression
   `tests/test_ads_v2_phase1.py` — 10/10 still PASS.
**Verified by testing_agent_v3_fork (iter-252):** Backend 100%
   (17/17), Frontend 95% (all flows pass). Phase 1 ready for user
   sign-off.

## Ads V2 — Phase 1 (2026-06-24) — superseded by post-rejection fix above
Backend (new):
  - `ads_v2/sync/adapters.py` — Meta/Snap/TikTok day-fetchers (read-only,
    use V1 access_token via v1_token_ref). Snap uses TZ-anchored TOTAL
    granularity; Meta uses level=account `time_increment=1`.
  - `ads_v2/sync/core.py` — `run_sync_for_account()` and
    `run_sync_user()`. Idempotent upsert into `ads_daily` keyed by
    `idempotency_key`. Reconciliation drift + anomaly flags embedded
    on the same row. Tracks `sources_count` (re-sync increments).
  - `ads_v2/data_layer/reports.py` — SSOT readers
    (`get_spend_by_day`, `get_spend_by_account`, `get_spend_by_provider`,
    `get_daily_rows`, `get_reconciliation_report`, `get_sync_health`).
    Every response carries `meta.source_layer` + `meta.ssot`.
  - `ads_v2/routes.py` — `/sync/run`, `/sync/account/{id}/day/{date}`,
    `/sync/health`, `/report?group_by=day|account|provider`,
    `/report/reconciliation`, `/report/daily`.
Frontend (new):
  - `pages/AdsV2Report.jsx` — 4 tabs (by day/account/provider/
    reconciliation) + "مزامنة الفترة الآن" trigger + SSOT footer
    badge.
  - UI fixes: `FInput`/`FLabel`/`FSelect*` wrappers enforce white text
    on dark backgrounds across all Ads V2 forms.
Tests: `tests/test_ads_v2_phase1.py` — 10/10 PASS.
Verified: 5 days of Meta data fetched, totals match across all three
groupings (3203.9 SAR), idempotency holds, V1 untouched, no GL writes.
Pending: Snap & TikTok sync — Snap token on preview is `token_invalid`
so live verification awaits production deploy.

## Ads V2 — Phase 0 (2026-06-24)
Approved design: `/app/memory/ADS_V2_FINAL_DESIGN.md` (simplified, 4-collection).
Backend (new):
  - `ads_v2/__init__.py` · `ads_v2/models.py` · `ads_v2/routes.py`
  - `ads_v2/data_layer/discovery.py` — reads V1 tokens read-only,
    lists Meta/Snap/TikTok ad accounts; falls back to V1 cached
    collections when token call fails.
  - `ads_v2/data_layer/settings.py` — CRUD for `ads_accounts`, FX
    & bank_fee patches, audit log to `ads_sync_logs`.
  - `server.py` — `_ads_v2_ensure_indexes()` on startup; mounts
    `/api/ads-v2/*` router.
Frontend (new):
  - `pages/AdsV2Settings.jsx` — 4 tabs (الحسابات/العملة/العمولات/المراجعة)
  - `App.js` route `/ads-v2/settings`, `Sidebar.jsx` entry under
    "إدارة التشغيل".
Collections created (Phase 0): `ads_accounts`, `ads_daily`, `ads_sync_logs`.
Invariants (verified by tests): NO writes to general_ledger, NO writes
to ads_daily, NO modification to snapchat_connections / meta_connections,
NO OAuth flow triggered.
Tests: `tests/test_ads_v2_phase0.py` — 11/11 PASS.

## Iter-251 v12 — Ad-Spend Scheduler Diagnostics (2026-06-24, READ-ONLY)
  - `ad_spend_scheduler_diagnostics.py` — new `/api/ad-spend-rca/scheduler-diagnostics`
    endpoint returning: (1) heartbeat history from `cron_runs` filtered by
    iter-215 types, (2) per-counterparty dry-run preview computing
    cumulative_spend / would-be AM & PM amounts / skip reasons WITHOUT
    writing to GL, (3) selected snapchat ad accounts state,
    (4) ads_currency_settings snapshot, (5) raw source row samples.
  - `server.py` — instrumented `_ad_spend_window_post_loop` to persist
    heartbeat rows into `cron_runs` on every loop tick (types:
    `ad_spend_window_post_loop_start`, `ad_spend_window_catchup`,
    `ad_spend_window_post`) with per-row `skipped_reasons` histogram.
Tests: `tests/test_iter251_v12_scheduler_diagnostics.py` — 3/3 PASS
Purpose: Conclusively determine WHY iter-215 is skipping all 486
counterparties in Production (per-account blocker / reason histogram).

## Tests
- `/app/backend/tests/test_shipping_accounts_ssot_iter255.py` — 6/6 PASS (priority flip + accrual SSOT)
- `/app/backend/tests/test_shipping_cost_ssot.py` — 15/15 PASS
- `/app/backend/tests/test_ads_v2_snapchat_relink.py` — 8/8 PASS (safe re-link flow)
- `/app/backend/tests/test_ads_v2_account_diagnose.py` — 8/8 PASS (3-tier status + diagnose)
- `/app/backend/tests/test_ads_v2_auto_reconcile.py` — 6/6 PASS (Phase 1 auto-reconcile)
- `/app/backend/tests/test_ads_v2_auto_reconcile_invariants_iter253.py` — 5/5 PASS
- `/app/backend/tests/test_ads_v2_drift_logic.py` — 7/7 PASS
- `/app/backend/tests/test_ads_v2_phase1.py` — 10/10 PASS
- `/app/backend/tests/test_ads_v2_phase0.py` — 11/11 PASS
- `/app/backend/tests/test_p15L_bnpl_transfer_block.py` — 11/11 PASS
- `/app/backend/tests/test_p15p_employee_guard_widened.py` — 7/7 PASS
- `/app/backend/tests/test_p15ab_suppliers_unification_forensic.py` — 3/3 PASS
- `/app/backend/tests/test_iter251_v12_scheduler_diagnostics.py` — 3/3 PASS
