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

## Test Credentials
See `/app/memory/test_credentials.md`.

## Tests
- `/app/backend/tests/test_p15L_bnpl_transfer_block.py` — 11/11 PASS
- `/app/backend/tests/test_p15p_employee_guard_widened.py` — 7/7 PASS
- `/app/backend/tests/test_p15ab_suppliers_unification_forensic.py` — 3/3 PASS
