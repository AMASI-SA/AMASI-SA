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
- `/app/backend/tests/test_p15L_bnpl_transfer_block.py` — 11/11 PASS
- `/app/backend/tests/test_p15p_employee_guard_widened.py` — 7/7 PASS
- `/app/backend/tests/test_p15ab_suppliers_unification_forensic.py` — 3/3 PASS
- `/app/backend/tests/test_iter251_v12_scheduler_diagnostics.py` — 3/3 PASS
