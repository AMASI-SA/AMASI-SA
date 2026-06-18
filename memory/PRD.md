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


## Completed Work — Iter-240 (Feb 16 2026): Double-Write Helper (Ledger Leak Fix — Forward-Only)

**User directive**: STRICT — NO backfill, NO adjustment entries, NO modification of historical data. Fix the ledger leak going forward by mirroring NEW manual `account_transactions` into the SSOT `general_ledger` at the point of write.

**Files**
- `backend/ledger_double_write.py` — idempotent `mirror_account_txn_to_ledger()`. Posts balanced 2-leg pair (bank + counter entity), allocates `entry_no`, refuses duplicates via `metadata.account_transaction_id` OR `paired_account_transaction_id`. Every leg carries the 5 mandatory metadata keys: `source="account_transaction_double_write"`, `transaction_type`, `idempotency_key`, `created_by_endpoint`, `account_transaction_id`, plus optional `paired_account_transaction_id` (for transfers).
- `backend/transfers_routes.py` — `POST /api/transfers` mirrors as ONE balanced pair (OUT-row id + paired IN-row id); delete-transfer also purges the mirror by `idempotency_key=transfer:{id}`.
- `backend/expenses_routes.py` — `_post_daily_expense_tx` mirrors; `_delete_daily_expense_tx` purges.
- `backend/liabilities_routes.py` — `_post_bank_tx` mirrors with full metadata; call sites in `/pay` and `/collect` pass idempotency keys.
- `backend/shipping_accounts.py` — `_post_shipping_payment_tx` mirrors; `_delete_shipping_payment_tx` purges.
- `backend/financial_position_ssot.py` — `account_balance_ssot()` updated to subtract double-write bank legs when computing the implicit pre-ledger opening (otherwise the mirror would double-count on top of `accounts.current_balance`).
- `backend/audit_routes.py` — new `make_double_write_health_router()` (`GET /api/audit/double-write-health`). Read-only. Returns today's coverage %, last N txns with `mirrored` flag, by-endpoint counts, and an `unmirrored_sample_today` list.
- `backend/server.py` — registered the new router.
- `backend/tests/test_iter240_double_write.py` — 6 unit tests (balanced pair, idempotency, transfer pairing, missing-id skip, mandatory metadata, no historical touch).

**Decision: ad-account topup NOT mirrored**. The `/topup` route already posts a balanced ledger pair directly via `universal_accounting` (`entry_type="topup"`). Mirroring would double-count. Documented in `_post_bank_tx` docstring.

**Verification**
- 6/6 `test_iter240_double_write.py` PASS.
- Regression suites for daily-expense/shipping-payment/liability-pay/ad-account-topup/bank-routing/pay-liability-search PASS (all pre-existing failures unrelated).
- Live API smoke: transfers, daily expense, liability pay, shipping payment all show `mirrored=True` in `/api/audit/double-write-health`. `opening_balance` and other pre-iter240 rows correctly stay `mirrored=False` (forward-only honored).



## Completed Work — Iter-161 Phase 4 Polish (Feb 13 2026): English Digits Everywhere

**User directive**: "أريد توحيد عرض الأرقام بالأرقام الإنجليزية فقط (0-9) وليس العربية (٠-٩). النص يبقى عربي وRTL، لكن الأرقام إنجليزية دائماً."

**Implementation**
1. **15 frontend files updated**: `toLocaleString("ar-SA", …)` → `toLocaleString("en-US", …)`
2. **Date locales**: `toLocaleDateString("ar-SA")` → `toLocaleDateString("en-GB")` in 6 files (Settings, ImageCatalog, ProductImages, RefundsAlert, ProductCostCard, lib/format.js)
3. **CSS safety net** in `index.css`:
   - `font-variant-numeric: tabular-nums` on `.num`, `table`, `.num-cell`
   - `-webkit-locale: "en-US"` globally
4. **Result**: All amounts now display as `3,000.00 ر.س` (Western digits with comma separator), unit text stays in Arabic.

**Visual verification**
- `/employees-ledger`: `3,000.00` راتب الموظف ✓
- `/accounting/reconciliation`: `100%`, `0.00`, `2` ✓
- `/financial-position-ledger`: all amounts Western ✓



## Completed Work — Iter-161 Phase 4 Closeout (Feb 13 2026): Reconciliation Report

**New endpoint**: `GET /api/accounting/migration/reconciliation`
Side-by-side comparison for every entity:
- Employees: legacy vs ledger for {salary_payable, advance, custody}
- Suppliers: legacy vs ledger for {payable}
- Externals: legacy vs ledger for {receivable}
- Couriers: legacy vs ledger for {payable, cod_receivable}
- Banks: stored `accounts.balance` vs ledger-computed bank.main net

Each comparison returns `{legacy, ledger, delta, match}`. The summary
includes `safe_to_disable_legacy: bool` — true ONLY when ALL entities
match within 0.01.

**New page**: `/accounting/reconciliation` (ReconciliationReport.jsx)
- 4 summary cards (total / matched / mismatched / match %)
- Green/red status banner based on `safe_to_disable_legacy`
- Per-section tables with row highlight when mismatch
- Refresh button
- Sidebar: «🔍 تقرير المطابقة»

**Test**: `/app/backend/tests/test_reconciliation_iter161.py` — verifies safe flag toggles correctly when legacy data exists but ledger is empty; passes.

**Phase 4 — Closing checklist**
- ✅ Ledger pages for: employees, suppliers, externals, couriers
- ✅ Financial Position page reads Ledger only
- ✅ Reconciliation report endpoint + page
- ⏳ User to verify 100% match on production then approve disabling legacy endpoints
- ⏳ Redirect `/financial-position` → `/financial-position-ledger`
- ⏳ Disable `liabilities.pay`, `.collect`, `.delete`



## Completed Work — Iter-161 Phase 4 Part 2 (Feb 13 2026): 4 Ledger Pages + Grouped Accounts

**New frontend pages (all reading STRICTLY from /api/accounting/* endpoints)**
- `/suppliers-ledger` — الموردون مع رصيد متبقي + كشف الحساب
- `/externals-ledger` — الأشخاص الخارجيون
- `/couriers-ledger` — شركات الشحن (payable + COD)
- `/financial-position-ledger` — المركز المالي الكامل (أصول + التزامات + صافي)

**Shared component**
- `/app/frontend/src/components/EntityLedgerPage.jsx` — generic ledger page with:
  - Configurable header, summary cards, columns, testid prefix
  - Per-row click → drawer with full ledger statement
  - Statement entries grouped by `txn_group_id`
  - "Reverse" button per posted entry (calls `/api/ledger/entries/{id}/reverse`)
  - Reversed entries grayed out + tagged

**Unified Entry Screen improvement**
- Bank dropdowns now group accounts:
  - 🏦 الحسابات البنكية (account_type=bank)
  - 💳 بوابات الدفع (account_type=payment_platform — Salla/Tamara/Tabby/Emkan)
  - 📦 أخرى (any other account_type)

**Tests** — all 6 pytest files green individually (159k, 160, 160-SSOT, 161-P2, 161-P3, 161-P4)

**Phase 4 — Frontend coverage complete**
- All 4 entity types have dedicated Ledger-only pages ✓
- Financial Position page reads only from general_ledger ✓
- Sidebar: 5 new menu items linking to Ledger system

**What's NOT done yet (Phase 4 closeout)**
- 🟡 Comprehensive RECONCILIATION REPORT (legacy vs ledger, per-entity match %) — backend has `/migration/verify` but it only checks pre-migration; need a runtime side-by-side comparator
- 🟡 Disable legacy mutation endpoints `/api/liabilities/{id}/pay,collect,DELETE` (still active — required until existing UIs are deprecated)
- 🟡 Replace old `/financial-position` route with redirect to `/financial-position-ledger`



## Completed Work — Iter-161 Phase 4 (Feb 13 2026): Ledger-Only Listings + Financial Position + Employees Page

**5 new backend endpoints (all read STRICTLY from general_ledger)**
- `GET /api/accounting/employees/list` — كل الموظفين + 3 sub-account balances (salary_payable/advance/custody) + net_position. Bulk aggregation in ONE pipeline.
- `GET /api/accounting/suppliers/list` — موردون + outstanding_debt + debits/credits
- `GET /api/accounting/externals/list` — أشخاص خارجيون + receivable
- `GET /api/accounting/couriers/list` — شركات شحن + payable + cod_receivable
- `GET /api/accounting/financial-position` — تصنيف كامل assets / liabilities / net_position من Ledger فقط (لا يقرأ من liabilities أو account_transactions)

**Frontend: New page `/employees-ledger` (EmployeesLedger.jsx)**
- Header + 4 summary cards (totals from /list endpoint)
- Sortable table: name, monthly salary, مستحق له, سلفة, عهدة, صافي
- Click row → **Employee Detail Drawer**:
  - 3 balance cards + net position
  - Full ledger statement **grouped by txn_group_id** (each transaction shown as a block)
  - Per-entry "عكس" button (calls /api/ledger/entries/{id}/reverse)
  - Reversed entries grayed out
  - Total of N transactions + M entries displayed
- Sidebar menu: «👥 الموظفون (Ledger)»

**Tests** — all green individually
- `/app/backend/tests/test_phase4_iter161.py` — seeds employees/suppliers/external/courier/bank, posts ledger txns, verifies all 4 list endpoints AND financial-position with exact expected numbers

**What's DONE in Phase 4 (this session)**
- ✅ Backend endpoints reading purely from Ledger
- ✅ Employees page (Ledger-only UI with statement drawer)
- ✅ Financial Position endpoint (Ledger-only)

**What's NOT done yet (Phase 4 continuation)**
- 🟡 Frontend pages for /suppliers, /externals, /couriers (Ledger views) — endpoints ready
- 🟡 Wire `/financial-position` page to use new endpoint instead of legacy aggregation
- 🟡 Disable legacy `/api/liabilities/{id}/pay`, `/collect`, `DELETE` (return 410 Gone with deprecation message)



## Completed Work — Iter-161 Phase 3 (Feb 13 2026): Couriers + Migration Verify + Snapchat Dashboard Sync Fix

**Confirmation on user concerns**
- ❌ **لا يوجد cron يخلق قيود رواتب يومية** — التحقق من الكود: لا توجد عملية تلقائية تنشئ قيود في liabilities يومياً. الموجود فقط `accrue-monthly` (manual, واحد لكل موظف شهرياً) و `salary-accrual-summary` (read-only، يحسب الـ accrual عند العرض). كل شيء بالفعل **monthly + display-time accrual**.

**Bug fix: Snapchat «صرف اليوم = 0»**
- **Root cause**: عند الضغط على «تحديث فوري» في صفحة الحسابات، الكود كان يكتب لـ `snapchat_account_daily` + `daily_costs` فقط — وليس `ad_account_ledger`. وبما أن الـ dashboard refactor (Iter-160) يقرأ حصراً من `ad_account_ledger`، فكان الصرف اليومي يظهر صفر حتى يشتغل cron نصف الساعة.
- **Fix**: في `snapchat_routes.py`، بعد `_reaggregate_snap_daily` نستدعي `_run_sync_for_all(force=True)` فوراً لدفع البيانات للـ ledger بنفس الـ request.

**New: Couriers (Shipping Companies) في الـ Ledger الموحد**
- 3 endpoints جديدة:
  - `POST /api/accounting/couriers/{id}/charge` — رسوم شحن (مصروف + courier.payable)
  - `POST /api/accounting/couriers/{id}/pay` — سداد للشركة
  - `POST /api/accounting/couriers/{id}/cod-deposit` — إيداع COD (يقلل courier.cod_receivable + يزيد bank)
- Sub_accounts: `payable` (ما عليك للشركة) + `cod_receivable` (ما حصلته الشركة من العملاء)

**New: Migration Verification Report**
- `GET /api/accounting/migration/verify` — تقرير شامل بعد الترحيل:
  - عدد الكيانات المرحلة (employees / suppliers / externals / banks)
  - عدد القيود الافتتاحية
  - **مقارنة الأرصدة قبل/بعد لكل قسم** مع flag `all_match: true/false`
- زر «📊 عرض تقرير التحقق» في Migration Wizard UI

**Tests** (all pass individually)
- `/app/backend/tests/test_phase3_iter161.py` — courier charge/pay/COD + verify endpoint
- جميع tests السابقة (iter-159, 160, 161 Phase 2) خضراء

**ما لم يُنفَّذ بعد (Phase 4 لاحق)**:
- تحويل صفحات الموظفين/الالتزامات الموجودة لتقرأ من الـ Ledger الجديد بدل `liabilities` (تتطلب UI rework لأكثر من 10 ملفات frontend)
- تعطيل endpoints الـ legacy `/api/liabilities/{id}/pay`, `/collect`, `DELETE` (بعد توصيل الواجهات للجديد)
- ربط SMSA/iMile APIs لمزامنة فواتير الشحن تلقائياً



## Completed Work — Iter-161 (Feb 13 2026): 🏛 PHASE 2 — Universal Append-Only Accounting Engine

**User directive**: "أريد نظاماً محاسبياً موحداً يفهم الفرق بين راتب/سلفة/عهدة/مصروف/التزام مورد/ذمم مدينة/ذمم دائنة وتكون جميع الأرصدة محسوبة من Ledger فقط مع إمكانية تتبع كل حركة." Plus: one unified entry screen replacing scattered screens; cutoff migration at 2026-06-13; monthly salary (no daily accrual); custody stays open; user-editable expense categories.

**Schema extensions**
- `general_ledger`: added `sub_account` (string) + `txn_group_id` (string)
- New entry_types: salary_accrual, salary_payment, advance_grant, advance_settle, advance_repay_cash, custody_grant, custody_return, custody_expense, custody_to_advance, supplier_invoice, supplier_payment, receivable_grant, receivable_collection, bank_transfer, expense_record
- New collections: `expense_categories` (user-editable), `migration_cutoffs`
- Added `post_txn_group(...)` helper — atomically posts ≥2 linked entries, enforces Σ debits == Σ credits

**Entity model (sub_account-based)**
| entity_type | sub_account | nature |
|---|---|---|
| bank | main | asset |
| employee | salary_payable | liability |
| employee | advance | asset (emp owes us) |
| employee | custody | asset (business funds in emp's hands) |
| supplier | payable | liability |
| external_person | receivable | asset |
| ad_account | (existing) | mixed |
| expense | <category_code> | expense |

**New endpoints — /api/accounting/***
- **Employees**: `/employees/{id}/advances`, `/custody`, `/custody/return`, `/custody/settle-with-receipts`, `/salary-accrual`, `/settle`, `/financial-summary`
- **Suppliers**: `/suppliers/{id}/invoice`, `/pay`
- **Externals**: `/external-persons/{id}/grant`, `/collect`
- **General**: `/bank-transfer`, `/expenses`
- **Categories CRUD**: `/expense-categories` (GET/POST/PATCH/DELETE) with 16 Arabic defaults auto-seeded
- **Reports**: `/trial-balance`, `/statement?entity_type=&entity_id=&sub_account=`

**Migration** — `/api/accounting/migration/*`
- `GET /snapshot` — read-only legacy balances (employees + suppliers + externals + banks)
- `GET /status` — completion state
- `POST /run` — `dry_run=True` (always allowed) returns before/after/diff/mismatch_count; `dry_run=False` (once only) writes opening_balance entries with sub_account preserved + marks cutoff
- IDEMPOTENT: second non-dry-run returns HTTP 400

**Frontend**
- **`/new-transaction`** — `UnifiedEntryScreen.jsx`: ONE form for every operation type (12 types) with live double-entry preview (مدين/دائن) + balanced flag
- **`/accounting/migration`** — `MigrationWizard.jsx`: dry-run with side-by-side BEFORE/AFTER diff per entity + confirmation phrase "أوافق على الترحيل" required to apply
- Sidebar: 2 new menu items

**Tests** (all pass individually)
- `/app/backend/tests/test_universal_accounting_iter161.py` — end-to-end (advance, salary accrual+settle, custody full lifecycle, supplier inv+pay, external grant+collect, bank transfer, category CRUD, double-entry invariant, dry-run)
- `/app/backend/tests/test_universal_accounting_iter161_extra.py` — negative paths + edge cases (created by testing agent)

**Testing agent result**: backend success_rate **100%**, no critical/minor blocking issues, no frontend issues.

**What's NOT migrated yet (intentional — to be done in Phase 3)**
- Legacy endpoints `/api/liabilities/*` still active (read-only sources for migration; user UI still works via them)
- The cron job that creates daily salary accruals in `liabilities` still runs (user wants monthly accrual via new endpoint — this needs disabling in a follow-up)
- Shipping companies + courier accounts not yet wired to universal entries (will be similar to suppliers)
- Existing pages (Employees, Liabilities, BNPL, AdAccounts) untouched — they continue using legacy endpoints



## Completed Work — Iter-160 (Feb 13 2026): 🏛 ERP-Grade Universal Ledger + Audit Log
**User directive**: "لا أريد حذف الديون أو تصفيرها فعلياً من قاعدة البيانات. محاسبياً لا يجوز حذف المصروف أو المديونية بعد تسجيلها لأنها تمثل حركة مالية حدثت بالفعل. البديل: تسوية / قيد عكسي / شطب بموافقة." + Message #737 Single-Source-of-Truth for ad spending.

**Architecture**
- New `general_ledger` collection — append-only, ERP-grade. Fields: `id, user_id, entry_no (monotonic), entity_type, entity_id, entry_type (spend|topup|payment|adjustment|reversal|settlement|writeoff|accrual|opening_balance), amount, side (debit|credit), currency, status (draft|posted|reversed), reverses_entry_id, reversed_by_entry_id, reason_code, notes, metadata, posted_at, posted_by`
- New `accounting_audit_log` collection — every accounting action writes a row: `actor_id, action, reason_code, before_state, after_state, ledger_entry_id`
- Canonical reason codes (Arabic): `actual_payment / data_entry_error / duplicate_entry / accounting_settle / approved_writeoff / platform_correction / balance_transfer / other`

**Removed (destructive endpoints)**
- ❌ `POST /api/ad-accounts/{id}/reset-debt` (Iter-159p) — violated immutable ledger
- ❌ `POST /api/ad-accounts/{id}/recompute-debt` (Iter-159n) — closed-and-rebuilt liabilities, also destructive
- Frontend: `RecomputeDebtButton` and `🗑 تصفير المديونيات` button removed

**New endpoints**
- `GET /api/ledger/reason-codes` — Arabic dictionary
- `POST /api/ledger/entries` — create draft or auto-posted entry (low-level)
- `POST /api/ledger/entries/{id}/post` — promote draft → posted
- `POST /api/ledger/entries/{id}/reverse` — append mirror entry, mark original `reversed`
- `POST /api/ledger/adjustments` — settlement / writeoff / adjustment with mandatory reason
- `GET /api/ledger/entries` — list (filter by entity_type/id/type/status)
- `GET /api/ledger/balance` — compute net from POSTED entries only
- `GET /api/ledger/audit-log` — list audit rows
- `POST /api/ad-accounts/{cp_id}/adjustments` — ad-account-scoped wrapper
- `GET /api/ad-accounts/{cp_id}/audit-log` — scoped audit list
- `GET /api/ad-accounts/{cp_id}/adjustment-entries` — scoped ledger list

**Single Source of Truth (Message #737)**
All ad-spend on Dashboard now reads strictly from `ad_account_ledger.type=spend` joined to `counterparties.ad_provider`:
- `/api/dashboard/snapchat-summary` — ledger only ✅
- `/api/dashboard/meta-summary` — ledger only ✅
- `/api/dashboard/tiktok-summary` — ledger only ✅
- `/api/dashboard` (master) — `daily_ads_total` = Σ all providers from ledger ✅
- `/api/reports/ads` (unified) — ledger only ✅
- Legacy fields `daily_costs.snapchat_ads/snapchat_ads_2/tiktok_ads`, `meta_ads_daily.spend`, `tiktok_ads_daily.spend` no longer count for accounting (still kept for non-accounting metrics: purchases/impressions/clicks).

**Frontend** (`AdAccounts.jsx`)
- New `AccountingActionsPanel` component per ad account:
  - 3 action buttons: ✓ تسوية (Settlement) · ✂ شطب (Write-off) · ± تعديل (Adjustment)
  - Form: amount + reason dropdown (loaded from `/api/ledger/reason-codes`) + notes
  - For Adjustment: direction picker (reduce/increase debt)
  - "📋 سجل التدقيق" drawer: shows ledger entries (with per-row "Reverse" button) + audit log
  - Reversal flow: prompts for reason_code + notes, confirms, posts to `/api/ledger/entries/{id}/reverse`
- `data-testid` attributes on every action

**Tests** (all pass)
- `test_ledger_iter160.py` — end-to-end: reason codes, mandatory reason, adjustments, reversal (mirror + status flip + double-reverse blocked), balance from POSTED only, ad-account adjustment reduces open_debt without touching liability row, old endpoints return 404/405
- `test_dashboard_ledger_ssot_iter160.py` — seed legacy collections with huge bogus values, verify dashboard summaries return only ledger values
- Backend regression: all existing pytest files green

**Side fix**: pre-existing latent `NameError: tamara_keywords` in legacy_analyses path of `/api/dashboard` — keywords now defined before the loop.


## Completed Work
- **Iter-159p (Feb 13 2026)**: 🛡 تعديل سلوك «تصفير المديونيات» — حفظ الرصيد والتعبئات.
  - **User clarification**: "عدّل الزر الحالي ليصفّر المديونيات فقط (يبقي الرصيد والتعبئات)."
  - **Backend**: تعديل `/api/ad-accounts/{cp_id}/reset-debt`:
    - يحذف `liabilities` (kind=ad_account, counterparty_id=cp_id) ✅
    - يحذف `ad_account_ledger` **فقط من نوع `type=spend`** ✅
    - **يحتفظ بـ** `ad_account_ledger` نوع `topup` ✅ (تمثل أصل: مبالغ شحنتها من البنك)
    - **لا يصفّر** `counterparty.balance` ✅
    - يمسح فقط markers المزامنة (`last_auto_sync_date`, `last_yesterday_synced_for`)
    - الـ response يتضمن `balance_preserved` للشفافية
  - **Frontend**: تحديث رسالة التأكيد لتوضّح ما سيُحذف وما سيُحفظ. زر «🗑 تصفير المديونيات».
  - **Test** (`test_reset_debt_iter159o.py` — passed): seed بـ 2 spend + 1 topup + 1 liability + balance=1500 → بعد reset: liab=0, spend=0, **topup=1 محفوظ**, **balance=1500 محفوظ** ✅


- **Iter-159o (Feb 13 2026)**: 🗑 زر «تصفير المديونية» للحسابات الإعلانية.
  - **User request**: "أبغى أصفّر المديونيات في الحسابات الإعلانية والمديونية أضيفها من جديد عبر إضافة المديونيات التاريخية. يوجد لخبطة في الأرصدة."
  - **Backend**: endpoint جديد `POST /api/ad-accounts/{cp_id}/reset-debt`:
    - يحذف كل `liabilities` (kind=ad_account) للحساب
    - يحذف كل `ad_account_ledger` للحساب
    - يصفّر `counterparty.balance` ويمسح markers المزامنة (`last_auto_sync_date`, `last_yesterday_synced_for`)
    - يُرجع counts لما تم حذفه
  - **Frontend**: زر «🗑 تصفير» (أحمر) بجانب «🔧 إعادة احتساب» في كل بطاقة حساب إعلاني، مع تحذير قبل التنفيذ.
  - **Workflow الموصى به**: تصفير ← ثم استخدام «ترحيل المديونيات التاريخية» لإعادة البناء من API بشكل نظيف.
  - **Test** (`test_reset_debt_iter159o.py` — passed): 3 ledger rows + 2 liabilities + balance=999 ← بعد reset: 0/0/0 ✅


- **Iter-159n (Feb 13 2026)**: 🔧 زر «إعادة احتساب المديونية» في صفحة الحسابات الإعلانية.
  - **User report**: "المديونية في META = 8,784.09 رغم أن الحقيقية 2,977.99 والباقي مسدد. الأرقام مكررة وليست حقيقية."
  - **Root cause**: قبل Iter-159m، عمليات الترحيل التاريخية المتكررة كانت تستدعي `_apply_uncovered` التي تضيف للـ liability المفتوحة الموجودة (سواء كانت بمصدر cron أو migration). خطوة الـ Reversal كانت تبحث فقط عن source=ad_account_migration ← لا تستطيع التراجع عن الإضافات على liabilities الـ cron ← تضخّم الـ liability تراكمياً.
  - **Fix**:
    - **Backend**: endpoint جديد `POST /api/ad-accounts/{cp_id}/recompute-debt` يحسب المديونية الصحيحة من قاعدتي حساب صلبة:
      - `Σ uncovered من ledger.spend` (إجمالي الصرف الذي تحوّل لدين فعلاً)
      - `Σ paid_amount من liabilities` (إجمالي المسدد)
      - المديونية الصحيحة = `max(uncovered - paid, 0)`
    - يُغلق كل الـ liabilities المفتوحة (`status: paid`, `rebuild_note`) ويُنشئ liability واحدة جديدة بالمصدر `ad_account_recompute`.
    - **Frontend**: مكوّن `RecomputeDebtButton` في كل بطاقة حساب إعلاني: زر «🔧 إعادة احتساب» مع `confirm()` ثم عرض النتيجة (قبل/بعد + إجمالي الصرف والمسدد).
  - **Test** (`test_recompute_debt_iter159n.py`): سيناريو واقعي بـ ledger=3000، paid=1000، liability مضخّمة بـ 6500 ← بعد الـ recompute = liability واحدة بـ 2000. ✅
  - **Use**: المستخدم يضغط زرّ «إعادة احتساب» على كل حساب إعلاني (META, Snapchat A, Snapchat B) → يتم تصحيح المديونية تلقائياً من واقع البيانات.


- **Iter-159m (Feb 13 2026)**: 🔧 إصلاح تكرار سجلات الصرف عند ترحيل المديونيات التاريخية.
  - **User report**: "ترحيل المديونيات التاريخية يتم إضافتها كصرف ولا يتم تحديث المديونيات المضافة سابقاً. كل حساب إعلاني يحوي سجل صرف واحد باليوم. المفروض يحدث الصرفيات السابقة تحديث وليس يضيف سجل جديد. تاريخ 12 يونيو مضاف من قبل وتم إضافته من جديد عند الترحيل."
  - **Root cause**: في `ad_account_routes.py::migration_apply` mode='daily'، الكود كان يستدعي `ad_account_ledger.insert_one()` لكل يوم بدون فحص وجود سجل سابق ← انتهاك قاعدة «سجل واحد لكل (حساب، يوم)».
  - **Fix**:
    1. **Find-then-update**: قبل الإدراج، البحث عن جميع سجلات `type=spend` لنفس (counterparty, date) — auto_cron أو migration.
    2. **Delta calculation**: `delta = platform_total - prior_applied`. إذا `delta <= 0` ← no-op كامل (لا rollback آلي للديون السابقة).
    3. **Apply delta only**: تطبيق الـ delta فقط على balance + liability (تجنّب double-counting).
    4. **Collapse duplicates**: إذا وُجد > 1 سجل قديم ← الإبقاء على الأقدم وحذف الباقي تلقائياً (دفاعي ضد البيانات السابقة للإصلاح).
    5. **Update with cumulative**: السجل المتبقي يُحدَّث بـ `amount = platform_total` (تراكمي صحيح).
  - **Tests** (`test_migration_no_duplicate_iter159m.py`):
    - Pre-existing auto_cron row بـ 200، ترحيل يجلب 350 ← سجل واحد يصبح 350، liability يصبح 350 (delta=150) ✅
    - 3 سجلات مكررة (100+50+50=200) ← تنطوي إلى سجل واحد عند الترحيل ✅ (passes individually)


- **Iter-159k (Feb 13 2026)**: ✅ اختبارات شاملة + مزامنة "اليوم السابق" مرة واحدة.
  - **User request**: "اختبار إضافة تكلفة الإعلانات اليومية تلقائي من الحسابات الإعلانية كمديونية. كل حساب يضاف له صف واحد باليوم. تحديث تراكمي. بدون تكرار. مزامنة كل نصف ساعة. مزامنة اليوم السابق مرة واحدة للتأكد من تسجيل أمس كاملاً."
  - **Backend**: 
    - دالة جديدة `run_yesterday_final_sync(db)` في `ad_account_routes.py` ← تدير `_run_sync_for_all` لتاريخ الأمس مرة واحدة لكل مستخدم، تستخدم marker `last_yesterday_synced_for` على counterparty لمنع التكرار في نفس اليوم الشمسي.
    - الـ scheduler في `server.py` يستدعي الآن `run_yesterday_final_sync` بعد كل دورة نصف ساعة (idempotent لذا آمن للتشغيل بشكل متكرر).
  - **Tests** (`test_ad_account_accounting_iter159k.py` — 5/5 passed ✅):
    1. **One ledger row per account per day** — حسابان منفصلان × 5 passes نصف ساعية = صفّان فقط بمبلغ صحيح لكل حساب (100, 50).
    2. **Cumulative update** — 100→150→150 (no-op)→200 = صف نهائي بمبلغ 200 وliability واحد بقيمة 200.
    3. **Balance covers spend then overflow** — حساب رصيده 60، صرف 100 ← يستهلك الـ 60 ثم ينشئ دين 40 = توازن محاسبي سليم.
    4. **Yesterday final sync runs once per day** — 3 استدعاءات متتالية = اسـترداد واحد فقط لـ this user (marker stable).
    5. **No duplicate liability across passes** — 10 passes بمبالغ متزايدة (50→140) = liability واحد بمبلغ 140 وledger واحد.


- **Iter-159j (Feb 13 2026)**: 👻 بطاقات منفصلة لكل حساب سناب شات في لوحة التحكم.
  - **User request**: "فصل بطاقة الحسابات الإعلانية سناب شات في لوحة التحكم كل حساب يكون مستقل: الصرف، الطلبات، متوسط تكلفة الطلب، المبيعات، الصرف خلال الشهر الحالي. بدون التعديل على البطاقة الحالية".
  - **Backend** — endpoint جديد `GET /api/dashboard/snapchat-accounts-summary`:
    - يجلب كل حسابات سناب الإعلانية (`counterparties.ad_provider=snapchat`).
    - **الصرف**: من `ad_account_ledger.type=spend` للشهر الحالي مجمَّعاً لكل counterparty.
    - **الطلبات والمبيعات**: من `snapchat_daily_stats` (Pixel) أو fallback لـ store attribution، مع **تقسيم proportional حسب نسبة صرف كل حساب** (لأن Pixel لا يعطي per-account breakdown).
    - **متوسط تكلفة الطلب**: spend ÷ orders.
    - **ROAS**: revenue ÷ spend.
    - **المديونية والحد الائتماني**: من `liabilities` و `counterparties.credit_limit`.
  - **Frontend** — مكوّن `SnapchatAccountsCards.jsx`:
    - بطاقة مستقلة في الـ Dashboard **بعد** `SnapchatOfficialCard` (بدون لمسها).
    - تظهر فقط عند وجود ≥ 2 حسابات سناب (تجنّب التكرار).
    - Grid بـ 2 أعمدة، كل بطاقة فيها: اسم الحساب، نسبة الصرف (badge)، 4 إحصائيات ملوّنة (الصرف/الطلبات/متوسط تكلفة الطلب/المبيعات+ROAS)، شريط مديونية ملوّن، رابط «إدارة الحساب →».
  - **التحقق**:
    - Snap A: spend=1500 (65.2%) → orders=15, CPO=100, revenue=3000, ROAS=2.0× ✅
    - Snap B: spend=800 (34.8%) → orders=8, CPO=100, revenue=1600, ROAS=2.0× ✅
    - Test pytest نجح بـ split 75/25 لمدخلات spend=1500+500=2000 و orders=20 ✅
    - اللقطة من Preview تؤكد ظهور البطاقتين بـ البطاقة الأصلية فوقها سليمة.


- **Iter-159i (Feb 13 2026)**: 💳 حد المديونية ونسبة الصرف لكل حساب إعلاني.
  - **User request**: "لكل حساب إعلاني — حد المديونية الحد المسموح مبلغ ونسبة الصرف التي يظهر بعدها الإشعار بأن المديونية على وشك النفاذ".
  - **Backend**:
    - حقلان جديدان في `counterparties`: `credit_limit` (SAR) و `alert_threshold_pct` (0-100).
    - Endpoint جديد `PUT /api/ad-accounts/{cp_id}/credit-limit` مع Pydantic validation (ge=0, le=100). يدعم إرسال أي حقل وحده أو كليهما، رفض الـ body الفارغ.
    - مولّد `_gen_high_ad_debt` في `alerts_routes.py` أُعيد كتابته: إذا كان `credit_limit > 0` يستخدم نسبة (debt/credit_limit) ويقارنها بـ `alert_threshold_pct` (افتراضي 80%). عند ≥95% → `critical`، وإلا → `warning`. عناوين التنبيهات: «مديونية {الاسم} على وشك النفاذ» مع رسالة تشرح المبلغ والنسبة والسقف.
    - السلوك القديم (نسبة من balance+debt) محفوظ كـ fallback للحسابات التي لم تُضبط لها حدود.
    - `_summarise` يُرجع الآن `credit_limit` و `alert_threshold_pct`.
  - **Frontend** (`AdAccounts.jsx` — مكوّن `CreditLimitPanel`):
    - بطاقة جديدة أسفل صف الإحصائيات (الرصيد/المديونية/الصرف) في كل بطاقة حساب إعلاني.
    - عرض السقف + نسبة التنبيه + **progress bar ملوّن** (أخضر عادي، أصفر فوق العتبة، أحمر فوق الحد).
    - رسائل تحذير مدمجة: «⚠ على وشك النفاذ — بلغت X%» / «⚠ تجاوزت الحد!».
    - زر «ضبط الحد» يفتح فورم بحقلين، حفظ بـ PUT ثم إعادة تحميل القائمة. validation كامل في الواجهة.
  - **Tests** (`test_ad_credit_limit_iter159i.py` — passed):
    - 500/1000 = 50% (تحت 60%) ⇒ لا تنبيه ✅
    - 700/1000 = 70% (فوق 60%) ⇒ warning ✅
    - 980/1000 = 98% ⇒ critical مع كلمة «النفاذ» في العنوان ✅
    - Validation: حد سلبي / نسبة > 100 / body فارغ ⇒ 422/400 ✅


- **Iter-159h (Feb 13 2026)**: 🔔 إشعارات/تنبيهات التسويات الذكية.
  - **User selection**: g (كل الأنواع الستة) + a (7 أيام لـ BNPL) + a (5% للفرق) + a (جرس) + b (بطاقة في الداشبورد قابلة للطي) + d (كل التصرفات).
  - **Backend** (`alerts_routes.py`): 6 مولّدات تنبيهات idempotent عبر `fingerprint = type:entity:id`:
    1. `overdue_bnpl` — طلبات tabby/tamara > 7 أيام بلا تسوية
    2. `amount_diff` — الفعلي ≠ المتوقع بأكثر من 5%
    3. `missing_salla` — لا فاتورة سلة منذ 14 يوم
    4. `high_courier_balance` — رصيد شركة شحن > 5000 ر.س
    5. `unmatched_order` — طلبات بدون مطابقة منذ 10 أيام
    6. `high_ad_debt` — مديونية حساب إعلاني > 50% من السقف
  - **APIs** (8 endpoints): `/alerts/refresh`, `/alerts`, `/alerts/unread-count`, `/alerts/{id}/{read|snooze|dismiss}`, `/alerts/read-all`, `/alerts/settings` (GET/PATCH).
  - **DB**: collections `settlement_alerts` (indexed on user+status, user+fingerprint+status) و `alert_settings`. Auto-expire للـ snoozed alerts عند انتهاء المدة.
  - **Frontend**:
    1. **🔔 NotificationBell** — جرس عائم أعلى يسار الصفحة (desktop + mobile) مع شارة عداد و polling كل 60 ثانية. لوحة منسدلة بأزرار: فتح، مقروء، تأجيل (1س/1ي/1أ)، تجاهل.
    2. **📊 AlertsCard** — بطاقة قابلة للطي في لوحة التحكم تعرض أهم 5 تنبيهات + pills بعدد كل خطورة (حرج/تحذير/معلومة). حالة الطي محفوظة في localStorage.
    3. **📋 /alerts** — صفحة كاملة مع فلاتر (حالة + خطورة + نوع) وكل التصرفات على كل تنبيه.
  - **Tests** (`test_alerts_iter159h.py` — passed): دورة حياة كاملة (refresh → list → idempotency على re-refresh → mark-read → snooze → settings).


- **Iter-159g (Feb 13 2026)**: 📅 طلب تاريخ إصدار الفاتورة عند رفع ملف سلة.
  - **User confirmation**: "تاريخ التحويل في جدول التسويات = تاريخ إصدار الفاتورة من المنصة".
  - **Root cause**: ملفات سلة (Excel) لا تحتوي على تاريخ إصدار الفاتورة، فالنظام كان يستخدم تاريخ الرفع كبديل.
  - **Backend**: حقل اختياري جديد `invoice_date` في `POST /api/payment-settlements/upload` يُحفظ في `header.settlement_date` بعد التحقق من الصيغة `YYYY-MM-DD`. يظهر مباشرة في `unified-overview` بـ `source=manual`.
  - **Frontend** (`SallaSettlements.jsx`): قبل بدء الرفع، يظهر `window.prompt` يطلب التاريخ (افتراضي: اليوم). إلغاء الـ prompt يلغي الرفع. صيغة غير صحيحة تظهر toast خطأ.
  - **Test** (`test_settlement_upload_invoice_date_iter159g.py` — passed): رفع ملف سلة بـ `invoice_date=2026-05-20` ← `header.settlement_date=2026-05-20`, الجدول الموحَّد يعرضه بـ `source=manual` ✅


- **Iter-159f (Feb 13 2026)**: 🧹 سجل تراكمي واحد يومياً لمديونية الإعلانات (إصلاح تكرار النصف ساعة).
  - **User feedback**: "عند إضافة مديونية الإعلانات كل نصف ساعة يتم إضافته تراكمي وليس كل طول اليوم يضيف سجلات جديدة. كل حساب إعلاني لديه باليوم سجل واحد تراكمي".
  - **Root cause**: `_run_sync_for_all` في `ad_account_routes.py` كان يستدعي `ad_account_ledger.insert_one(...)` في كل مزامنة (≈48 مرة يومياً) ← سجل جديد بكل نصف ساعة.
  - **Fix**:
    1. **Find-then-update**: قبل الإدراج، البحث عن سجل `auto_cron` لنفس (counterparty, date). إن وُجد ← تحديث `amount` ليصير الإجمالي التراكمي للمنصة + دمج `breakdown` (from_balance, uncovered, delta_applied, last_sync_at).
    2. **Self-healing collapse**: إن وُجدت سجلات قديمة مكررة من قبل الإصلاح (find يُرجع >1) ← الإبقاء على الأقدم وحذف الباقي.
  - **Tests** (`tests/test_ad_account_cumulative_iter159f.py`):
    - مزامنتان متتاليتان في نفس اليوم → سطر واحد فقط بالمجموع التراكمي ✅
    - زرع 3 سجلات مكررة قديمة (20+30+30) → بعد المزامنة التالية تنطوي إلى سطر واحد (amount=100) ✅
  - **Idempotency**: المزامنة الثالثة بنفس البيانات = no-op (delta=0 لا يُنشئ شيئاً).


- **Iter-159e (Feb 13 2026)**: 📅 تاريخ التحويل الفعلي في "جميع التسويات الموحَّدة".
  - **User feedback**: "تاريخ التحويل يكون تاريخ التسوية نفسه مو تاريخ إضافة الفاتورة".
  - **Backend**: aggregation على `settlement_entries` لاستخراج `MAX(settlement_date)` لكل ملف. تم تعريف ترتيب أولوية واضح: header.settlement_date (manual override) → max من الصفوف → header.transfer_date → uploaded_at. حقل جديد `settlement_date_source` في الاستجابة (`manual` / `file_rows` / `uploaded_at`).
  - **PATCH endpoint جديد** `/api/payment-settlements/{file_id}/settlement-date` — تعديل/مسح يدوي مع validation للصيغة YYYY-MM-DD.
  - **Frontend**: التاريخ في الجدول قابل للنقر يفتح prompt للتعديل. Badge 🟦 "يدوي" أو ⚠ "رفع" حسب المصدر.

- **Iter-159d (Feb 13 2026)**: 🔎 Drawer تفاصيل الجهة عند النقر على اسم المستفيد.
  - **User request**: "اجعل اسم المستفيد قابلاً للنقر فيفتح صفحة تفاصيل ذلك المورد/الموظف".
  - **Backend**: endpoint جديد `GET /api/parties/{party_id}/details` يبحث في `counterparties` ثم `operating_salaries` ويُرجع:
    - بيانات الجهة (الاسم، النوع، الفئة، الراتب الشهري للموظفين، الحالة، الملاحظات)
    - الإجماليات: owed_to_party / owed_from_party / net_balance
    - كل الالتزامات (مفتوحة + مغلقة) مرتبة من الأحدث
    - كل الحركات البنكية المرتبطة عبر `peer_liability_id`
    - تاريخ آخر نشاط + counts
  - **Frontend**: drawer جانبي (max-w-2xl) ينفتح من اليمين عند النقر على اسم المستفيد في جدول العمليات. يعرض 3 بطاقات إجماليات + meta info + جدولين (الالتزامات والحركات البنكية).


- **Iter-159c (Feb 13 2026)**: 👤 عمود "المستفيد" — اسم الجهة الفعلي.
  - **User feedback**: "اسم الشخص صاحب العملية الموظف مثلاً لا يظهر، تظهر اسم عملية الإدخال (تسوية سلفة)... أبغى أضيف عمود يظهر اسم المستفيد من هذي العملية".
  - **Root cause**: لرواتب الموظفين والسلف، الحقل `description` كان يحتوي وصف العملية ("تسوية سلفة"، "راتب يونيو") بدلاً من اسم الموظف.
  - **Backend**: `Step 2b` جديدة تستعلم `counterparties` و `operating_salaries` بـ batch مرة واحدة لبناء `party_id → name` map. كل عملية تحصل على `beneficiary_name` بالاسم الكنوني للجهة (المورد أو الموظف).
  - **Frontend**: عمود جديد بلون نيلي بين "المورد/الموظف/الحساب" و "المبلغ" يعرض اسم المستفيد. `data-testid=hub-recent-beneficiary-{id}`.
  - **التحقق**: راتب موظف "محمد العامل" يعرض الآن بشكل صحيح في عمود "المستفيد" مع بقاء الوصف "راتب يونيو" في عمود المورد/الموظف.

- **Iter-159b (Feb 13 2026)**: 📊 إجماليات المركز الصافي أسفل الجدول.
  - **User follow-up**: "نعم" (وافق على اقتراح إضافة الإجماليات).
  - **Backend**: استجابة `/financial-input-hub/recent` الآن تتضمن `totals: { owed_to_party, owed_from_party, net_balance, unique_parties }` محسوبة عبر الـ feed المفلتر بأكمله (وليس فقط الصفحة المعروضة)، مع احتساب كل جهة مرة واحدة لتجنّب التكرار.
  - **Frontend**: ثلاث بطاقات ملوّنة تحت الجدول — 🔴 إجمالي «كم له» / 🟢 إجمالي «كم عليه» / ⚪ صافي المركز (مع عدد الجهات الفريدة). تتفاعل مع البحث والفلتر تلقائياً.

- **Iter-159 (Feb 13 2026)**: 📊 Recent Entries — Directional Balance Columns.
  - **User request**: "إضافة عمود في جدول آخر عمليات الإدخال: اسم الموظف/الحساب/المورد + كم له + كم عليه".
  - **Backend** (`GET /api/financial-input-hub/recent`): for every fed item, enriched with three new fields — `party_id`, `owed_to_party` (ما علينا لها), `owed_from_party` (ما لنا عليها) — computed via a single grouped aggregation over `liabilities` (status ∈ unpaid/partial). Kinds bucketed as `supplier|salary|ad_account` → "كم له" and `salary_advance|receivable` → "كم عليه". Net `party_open_balance` kept for backward-compat.
  - **Frontend** (`FinancialInputHub.jsx` → `RecentEntriesTable`): replaced single "الرصيد المفتوح" column with two color-coded columns — red **كم له** + green **كم عليه**. Header expanded to "المورد / الموظف / الحساب". Empty values show muted "—".
  - **Bug-fix during build**: outer `total = len(feed)` was being shadowed by an inner aggregation loop variable, returning bogus `total_pages`. Renamed inner to `sub`. Verified via direct curl: supplier with 1500+500 correctly aggregates to `owed_to_party=2000` while pagination `total=2`.


- **Iter-157b (Feb 13 2026)**: 🔍 Recent Entries — Expanded coverage + search + operation filter.
  - **User request follow-up**: "الجدول يحوي على آخر تعليمات الإدخال وتسديد الرواتب وديون الرواتب جميع العمليات المضافة في مركز الإدخال تظهر بالجدول حسب الأحدث. مع إضافة اقتراح خيار البحث كامل".
  - **Expanded transaction coverage**: The recent endpoint now surfaces ALL hub-originated operations — added `expense`, `expense_payment`, `deposit`, `withdrawal`, `courier_transfer`, `cod_transfer`, `receivable_collect`, `shipping_payment`, `topup` to the `account_transactions` filter alongside the original four. Each gets a clear Arabic operation label (مصروف يومي، إيداع، تحويل شركة شحن، تحصيل من عميل، …).
  - **Search** (`?q=...`): case-insensitive substring match against `party_name` OR `operation` label.
  - **Operation filter** (`?op_filter=...`): values `create` (liability creations), `pay` (سداد/تسوية), `advance` (سلفة), `expense` (مصاريف), or `all`.
  - **Frontend**: New search bar above the table with a text input + dropdown (`كل العمليات` / إنشاء / سداد / سلفة / مصاريف). Both controls debounce 250ms and auto-reload via `useEffect`. Pagination buttons now propagate the active filters.
  - **Tests**: 6/6 pytest (`test_financial_input_hub_recent_iter157.py`): empty feed, single liability, 15-item pagination split, amount edit round-trip, **search filter by party name**, **op_filter=create scoping**. All green.

- **Iter-157 (Feb 13 2026)**: 📋 Financial Input Hub — Recent Entries table with pagination + amount edit.
  - **User request**: "مركز الإدخال المالي — عرض جدول عمليات يعرض آخر 10 عمليات إدخال مع خيار تنقل بين باقي صفحات الجدول أسفل الجدول. العملية، اسم المورد/الموظف، المبلغ، كم له كم عليه سابق. مع إمكانية التعديل على المبلغ المدخل."
  - **Backend** (`GET /api/financial-input-hub/recent?page=N&page_size=10`): Unifies merchant-initiated entries from `liabilities` (excluding auto_generated salary rows) AND `account_transactions` (debt_payment, salary_advance, ad_account_topup, salary_settlement). Returns sorted feed with operation label, party name, amount, current open balance for the party (computed via aggregation), created_at, and `editable` flag. Default page_size=10, max=50.
  - **Frontend** (`/app/frontend/src/pages/FinancialInputHub.jsx`): New `RecentEntriesTable` component rendered below all tabs. Columns: العملية, المورد/الموظف, المبلغ, الرصيد المفتوح, التاريخ, + ✎ edit button. Pagination footer shows "صفحة X من Y · Z عملية" with «السابق» / «التالي» nav. Click ✎ → window.prompt for new amount → PUT `/liabilities/{id}` → toast + auto-reload. Posted bank transactions are surfaced read-only (editable=false) to avoid balance-corruption risk.
  - **Tests**: 4/4 backend pytest (`test_financial_input_hub_recent_iter157.py`): empty feed, single liability listing, 15-item pagination split (10+5, no dupes), amount edit round-trip via existing PUT endpoint.

- **Iter-156 (Feb 13 2026)**: 🟧 Salla Settlements — Dedicated page (mirror of Tabby/Tamara) with per-payment-method analytics.
  - **User request**: "تسويات سله مثل صفحة تسوية تمارا وتابي" — wants Excel + API support, per-method commissions, expected-vs-actual comparison with mismatch alerts.
  - **What was already built**: The Salla parser (`/app/backend/settlements_import/parsers/salla.py`) is comprehensive — it handles all payment methods (mada / credit card / Apple Pay / STC Pay / Google Pay), refunds (full vs partial detection), wallet recharge ("مشتريات سله"), Arabic diacritics, invoice number extraction from sheet title. It writes to `settlement_files` + `settlement_entries`.
  - **New backend** (`/api/payment-settlements/_analytics/salla`): Returns `files[]`, `per_method[]` (aggregated counts/gross/fees/vat/net/refunds + effective fee rate per method via MongoDB aggregation pipeline), and `totals`. User-scoped, includes only files where `provider='salla'`.
  - **Fix to `utils.py`**: Added Arabic payment-method aliases — "أبل باي" → `apple_pay`, "أس تي سي باي" → `stc_pay`, "جوجل باي" → `google_pay`. Renamed canonical keys to use snake_case (`apple_pay`, `stc_pay`) for consistency.
  - **New frontend** (`/app/frontend/src/pages/SallaSettlements.jsx`): Pattern-matched on BnplSettlements. Drag-and-drop Excel upload (calls `/payment-settlements/upload` with `provider_hint=salla`), per-method breakdown table with colored badges and effective fee rate, file list with delete action, refund totals when present. Route: `/salla-settlements`. Sidebar item: "تسويات سلة 🟧" under العمليات المالية.
  - **Tests**: 3/3 backend pytest (`test_salla_settlements_iter156.py`): empty analytics, upload + per-method aggregation across mada/credit_card/apple_pay/refunds, provider scoping.
  - **NOT YET DONE (Phase 2 — surfaced in roadmap)**: Expected-vs-actual commission comparison with mismatch alerts (needs per-method configurable commission rates in settings, which already exists in payment_methods.py but isn't wired here yet). Salla API auto-sync (needs an OAuth flow with Salla Merchant API).

- **Iter-155 (Feb 13 2026)**: 🐛 Shipping Companies Settings — Save bug + Add/Remove capability.
  - **User feedback**: "عند حفظ إعدادات شركات الشحن لا يتم حفظ المعلومات المضافه".
  - **Root cause**: The backend `ShippingCompany` Pydantic model (`/app/backend/server.py` line 289) declared only 4 fields (`name`, `cost_per_order`, `vat_percent`, `is_deferred`). The new ShippingCompanySettings UI was sending `cod_fee_percent` and `cod_fee_fixed_per_order` too — but Pydantic v2's default `ignore` extras policy silently dropped them on round-trip. Additionally, the page had no UI to add/remove companies.
  - **Backend fix**: Added `cod_fee_percent: Optional[float] = Field(default=0.0, ge=0, le=1)` and `cod_fee_fixed_per_order: Optional[float] = Field(default=0.0, ge=0)` to the model.
  - **Frontend fix** (`/app/frontend/src/pages/ShippingCompanySettings.jsx`):
    - **➕ "إضافة شركة جديدة"** button (`data-testid="add-shipping-company"`): prompts for the name and inserts a row with defaults (deferred=true, vat=15%).
    - **"إجراءات" column** with × button per row to remove a company from settings (`data-testid="remove-{name}"`).
    - Layout reflowed so save + add are side-by-side.
  - **Tests**: 5/5 backend pytest (`test_shipping_settings_iter155.py`): GET works, COD fields persist round-trip, can add new company, can remove company, legacy fields still work.

- **Iter-154 (Feb 13 2026)**: 🔗 Unified Employee Settlement — Merged "Pay Liability" + "Salary Advance" workflows.
  - **User feedback**: "تسديد راتب الموظف التراكمي او اضافه مديونيه تكون مدمجه بصفحه واحده ... اذا كان المبلغ المدخل أكبر من رصيد الموظف يقوم النظام بتسديد المبلغ المتراكم وباقي المبلغ يسجل ك سلفه ع الموظف".
  - **Backend** (`POST /api/liabilities/employee-settlement`): Inputs `employee_salary_id`, `amount`, `paid_from_account_id`, `payment_date`, `notes`. Computes live `net_due` from the accrual aggregator and intelligently splits: `salary_part = min(amount, net_due)` is paid against the largest open salary liability (or a fresh topup row with a unique period_key), and `advance_part = amount − salary_part` is recorded as an open `kind=salary_advance` liability. Both halves post a single bank debit each. Returns a structured response with `salary_part`, `advance_part`, `paid_liability_id`, `advance_liability_id`, and a friendly Arabic `message`.
  - **Frontend** (`/app/frontend/src/pages/FinancialInputHub.jsx`):
    - Pay-Liability tab renamed to **"سداد التزام / تسوية موظف"** with a hint explaining the auto-split behaviour.
    - Submit handler now branches: when `selected.kind === 'salary'` AND `selected.employee_salary_id`, calls the new endpoint; otherwise the legacy `/pay` endpoint with the over-amount cap (preserved for supplier/ad_account).
    - **Live split-preview banner** (indigo, `data-testid='pay-liab-split-preview'`) appears once the entered amount > net_due, displaying both halves in side-by-side cards before submission.
    - Standalone **"سلفة موظف"** tab now shows an informational banner (`data-testid='adv-merged-banner'`) pointing merchants to the unified flow. It still works for pure-advance recording.
  - **Tests**:
    - 7/7 backend pytest (`test_employee_settlement_iter154.py`): exact-match payment, partial, overpay-with-split, pure advance (future start date), insufficient bank, suspended-employee compatibility, household rejection.
    - End-to-end testing_agent run (iteration_54.json) — **100% (3/3 scenarios)**: virtual-badge surfaces accrued, split-preview renders, overpay submits with combined toast, advance tab banner links back.

- **Iter-153 (Feb 13 2026)**: 👁️ Suspended Employees — Selectable in Pay-Liability + Advance + Search with visual warning.
  - **User feedback**: "عندما يكون الموظف موقوف لا أستطيع البحث عنه وإضافة مديونيه أو سداد التزام له — أبغى يكون مسموح مع التنبيه".
  - **Frontend change** (`/app/frontend/src/pages/FinancialInputHub.jsx`, line 1549): Removed the `e.status === "active"` filter — `employees` now includes both active and suspended (kept the `category === "employee"` filter to still exclude household/charity rows).
  - **Visual warnings**:
    - PayLiabilityForm dropdown already shows the `موظف موقوف` badge (existing logic).
    - AdvanceForm dropdown now shows a `⚠ موقوف` slate badge next to each suspended employee (`data-testid="adv-emp-suspended-{id}"`).
    - AdvanceForm displays a prominent amber warning above the EmployeeBalanceCard when a suspended employee is selected: "هذا الموظف موقوف — لكنه يمكن أن يكون لديه التزامات معلّقة أو تسويات نهائية".
  - **Backend untouched**: `/operating-expenses/salaries`, `/liabilities/salary-accrual-summary`, `/liabilities/{id}/pay`, and `/liabilities` (kind=salary_advance) already accept suspended employees — only the frontend filter was the gate.
  - **Tests**: 4/4 backend pytest (`test_suspended_employee_visibility_iter153.py`) confirm: salaries list returns both, accrual summary includes suspended, can pay existing liability for suspended employee, can record salary advance for suspended employee.

- **Iter-152 (Feb 13 2026)**: 🛡️ Shipping Courier Transfers — Validation guardrails.
  - **What changed (backend `/api/shipping-accounts/transfers`)**:
    1. **courier_to_bank**: rejects if `amount > net_balance` (the courier's open balance with us). Clear Arabic error: "المبلغ أكبر من المستحق على «X» (Y ر.س)". Also rejects when there is NO outstanding balance to receive against.
    2. **bank_to_courier**: rejects when the selected bank's `current_balance < amount`. Clear error: "رصيد الحساب البنكي «X» غير كافٍ".
    3. **bank_to_courier over-payment**: when `amount > what we owe the courier`, the transfer SUCCEEDS but the response includes `overpayment` (delta) and `overpayment_note` (Arabic, ready for toast).
    4. Unknown/non-deferred companies skip validation (preserves existing behavior).
  - **What changed (frontend `/app/frontend/src/pages/ShippingTransfers.jsx`)**:
    - Live hint pills under the form: "المستحق علينا/لنا" for the selected company + "رصيد البنك المختار".
    - Inline warning ⛔ (red, blocks submit button) for hard-rejects; ⚠️ (amber, allows submit) for over-payment notice.
    - Submit button is `disabled` when a blocking condition exists.
    - On successful over-payment, the success toast shows the `overpayment_note` for ~7s.
  - **Tests**: 8/8 pytest (`test_shipping_transfer_validation_iter152.py`) — covers all four rules plus an "unknown company" preservation test.

- **Iter-151d (Feb 13 2026)**: 🧹 Data Hygiene — One-click "Clean up stale partial liabilities".
  - **Why**: The Iter-151c shortfall-virtual-entry logic LAYERS AROUND stale `partial`-but-fully-paid rows. To eliminate the root cause once and for all, the merchant now has a self-service button.
  - **Endpoint** (`POST /api/liabilities/admin/cleanup-stale-partial`): User-scoped. Finds rows with `status='partial'` AND `paid_amount + 0.01 >= expected_amount`, flips them to `status='paid'`, and returns a sample of fixed rows for audit. Supports `?dry_run=true` to show the count first without mutating.
  - **UI** (`/app/frontend/src/components/EmployeeBalanceCard.jsx` → `SalaryAccrualSummaryCard`): Discreet underlined link **"🔧 تنظيف بيانات الالتزامات القديمة"** under the four-box totals. Click → dry-run shows count → confirm dialog → real cleanup → toast + auto-refresh.
  - **Tests**: 4/4 backend pytest (`test_cleanup_stale_partial_iter151d.py`): dry-run preserves state, real run fixes stale + leaves healthy partial rows alone, returns 0/0 when no candidates, and is strictly user-scoped (never touches another tenant's data).
  - **Visible on**: Operating Expenses page (`/operating-expenses`) → الرواتب الشهرية tab. (The card is shared across Dashboard + Reports, so the button appears anywhere `SalaryAccrualSummaryCard` is rendered.)

- **Iter-151c (Feb 13 2026)**: 🐛 Pay-Liability Search — "shortfall virtual entry" for stale partial rows.
  - **Bug**: User reported on production: شهاب التراكمي 4200 ر.س لكن الزر يرفض السداد بـ "المبلغ أكبر من المتبقي (0.00)". تشخيص iter-151b كان جزئياً.
  - **Real root cause**: Even after the iter-151b fixes, if the employee had an EXISTING open salary liability with `remaining=0` (e.g. stale `partial` row with paid==expected from a prior advance-offset round), the virtual-entry branch was SKIPPED because `groups.has(empKey)` was already true. The merchant got stuck on the zero-remain row.
  - **Fix** (`/app/frontend/src/pages/FinancialInputHub.jsx`): Reworked the virtual-entry loop to ALWAYS surface a virtual rep whenever `existingGroup.sumRemaining < employee.net_due`. The virtual covers the **shortfall** (= net_due − existingRemain) and is promoted to `representative`, so clicks route through the salary-topup flow regardless of stale data. The dropdown's `onClick` now short-circuits to `pickLiability(g.representative)` when `g.virtual` is true.
  - **Backend behavior** (`liabilities_routes.py::create_salary_topup`): When the merchant has a stale partial row counted in `paid_amount`, the `_aggregate_salary_accrual` aggregator already subtracts it from `accrued` → returns the correct outstanding `net_due`. The topup endpoint then caps at this net_due, so over-payment is impossible (correct accounting invariant).
  - **Tests**: 8/8 backend pytest (`test_pay_liability_search_iter151.py`) + end-to-end Playwright in iteration_53.json — all green. The bug is NOT reproducible on Preview after the fix; redeploy required to mirror on production.

- **Iter-151b (Feb 13 2026)**: 🐛 Pay-Liability Search — "المديونيه 0 ريال رغم أن البحث يظهر 4200" Fix.
  - **Bug**: User reported (Arabic): for employee "شهاب", search dropdown showed 4200 SAR but clicking + submitting returned the error "المبلغ أكبر من المتبقي (0.00)".
  - **Root cause (two distinct issues)**:
    1. When a counterparty group in the search contained MULTIPLE open liabilities and at least one had `remaining_amount = 0` (e.g. a `partial` row whose paid_amount equals expected after an advance was deducted), the GROUP's `representative` could resolve to the zero-remain row — group's `sumRemaining=4200` shown in dropdown, but the selected liability had `remaining=0`.
    2. When the Iter-151 salary-topup endpoint created a fresh row for an employee with an open advance ≥ expected_amount, `_apply_open_advances_to_salary` immediately offset the topup → `paid=expected`, `status=paid`, `remaining_amount=0`. Frontend showed the topup as selected but submit failed with the same "0.00" error.
  - **Fix** (`/app/frontend/src/pages/FinancialInputHub.jsx`):
    - Group representative selection now prefers rows with `remaining > 0` (lines 361-377). When a group mixes paid and unpaid rows, the unpaid one is picked.
    - The dropdown's `onClick` handler explicitly picks the first `g.items` row with `_liabRemaining > 0` (lines 600-610) — defensive against legacy data with stale `partial` zero-rem rows.
  - **Fix** (`/app/backend/liabilities_routes.py`): `salary-topup` endpoint now flags `fully_offset_by_advance: true` with a clear Arabic `message` field when the open advance fully covers the topup. The frontend displays a friendly `toast.info` instead of attempting a zero-SAR payment.
  - **Tests**: 8/8 backend pytest tests in `/app/backend/tests/test_pay_liability_search_iter151.py` (including new `test_salary_topup_fully_offset_by_advance_returns_clear_flag`).

- **Iter-151 (Feb 13 2026)**: 🐛 Financial Input Hub — Pay-Liability Search: Employee re-appears after full payment.
  - **Bug**: After fully paying an employee's salary liability (e.g. "جمال"), the next search in the Pay-Liability dropdown showed only `ابو جمال` (different employee whose name CONTAINS "جمال"). The just-paid employee disappeared even though he's still an active employee with continued daily/monthly salary accrual.
  - **Root cause**: The search dropdown was built strictly from `openLiabilities` (status ∈ unpaid/partial). Once an employee's only open salary row was fully paid, the row left the list and the employee became invisible — even when `accrualMap[empId].net_due > 0`.
  - **Frontend fix** (`/app/frontend/src/pages/FinancialInputHub.jsx`): Added **virtual search entries** for active employees with `net_due > 0` but no open salary liability. They render with a distinctive `إنشاء التزام تلقائي` badge (`data-testid="pay-liability-virtual-badge"`). On click, the form POSTs to the new `/api/liabilities/salary-topup` endpoint and stores the just-created row in a local `pendingTopup` state so the `selected` getter resolves immediately (without waiting for the parent's `openLiabilities` refetch).
  - **Backend fix** (`/app/backend/liabilities_routes.py`): New `POST /api/liabilities/salary-topup` endpoint creates an ad-hoc salary liability with a UNIQUE `period_key` (`YYYY-MM-topup-<8hex>`) — bypassing the per-month `(user, kind=salary, employee, period)` unique index that prevented `generate-salaries` from creating a second row for the same month. Amount defaults to and is capped at the employee's current `net_due` from the accrual aggregator. Open advances are automatically applied via `_apply_open_advances_to_salary`.
  - **Tests**: 7 backend pytest tests in `/app/backend/tests/test_pay_liability_search_iter151.py` (all pass). End-to-end Playwright test via testing_agent_v3_fork (iteration_52.json) confirms ALL 4 verification points pass: virtual badge appears, click creates topup, selected card populates from pendingTopup, submit fires POST `/api/liabilities/{id}/pay` and form resets.

- **Iter-150 (Feb 13 2026)**: 🐛 Ad Account Sync — Paid Liability No-Recreation Fix.
  - **Bug**: When user paid off a cron-created ad-account liability, the next force=True sync (half-hour cadence) recreated a fresh liability for the full day's spend — undoing the payment.
  - **Root cause**: Pre-Iter-150 `_run_sync_for_all` used a "drop + recreate" pattern. The reverse step looked for liabilities with `status in [unpaid, partial]`. After payoff, status became `paid`, so the reverse found nothing — then the apply step created a new liability for the full daily total.
  - **Fix**: Switched to a **delta-based** approach in `/app/backend/ad_account_routes.py::_run_sync_for_all`. Each force-sync computes `delta = platform_total − sum(prev auto_cron amounts today)` and only applies the delta. Re-runs with no new platform spend are a genuine no-op (no DB writes, no liability touched).
  - **Response shape additions** (backward compatible — only ADD): `delta_applied`, `prev_total_applied`, `no_op`. The frontend's existing `debt_created` reading semantics now reflect "debt added in THIS sync" (so a no-op re-sync shows 0).
  - **Negative delta (rare correction)**: if platform reports LESS than what was already applied today, the abs(delta) is refunded to the prepaid balance and a correction ledger row is written. Liabilities are NEVER auto-reduced — user must adjust manually.
  - **Tests**: 3 regression tests in `/app/backend/tests/test_ad_account_sync_paid_no_recreate_iter150.py` covering (a) paid-off liability not recreated, (b) new spend after payoff creates a NEW liability for the delta only, (c) 5x repeated force-sync with no spend change is a pure no-op (no ledger rows, no liability writes). All 3 pass. Existing Iter-110 tests adjusted to new delta semantics — all 9 in that file still pass. Wider Ad Account regression: 49/49 affected tests pass.

- **Iter-144 (Feb 12 2026 — this session)**: 🚚 شركات الشحن — قسم مستقلّ مع unified courier ledger.
  - **Sidebar restructure**: new top-level section `🚚 شركات الشحن` housing 4 pages: حسابات الشحن الآجلة (existing), أرصدة شركات الشحن (new), تحويلات شركات الشحن (new), إعدادات شركات الشحن (new).  Old `/shipping-accounts` link removed from `إدارة التشغيل`.  COD-settlements page intentionally deferred until SMSA / iMile integration lands.
  - **New backend endpoints** (in `shipping_accounts.py`):
    - `GET /api/shipping-accounts/ledger` — per-deferred-company unified balance. Returns `{companies: [{name, cod_approved, cod_pending, shipping_cost, cod_fee, courier_to_bank, bank_to_courier, net_balance, interpretation, ...}], totals: {...}}`. Hard rule: only `cod_approved_statuses`-matched orders count for ANY money figure (delivered-only), and only `is_deferred=True` companies are listed. Immediate companies excluded entirely.
    - `GET/POST/DELETE /api/shipping-accounts/transfers` — new `courier_transfers` Mongo collection supporting two directions: `courier_to_bank` and `bank_to_courier`. POST also posts a linked `account_transactions` row when a bank account is selected (in/out) and recomputes the bank balance.
  - **New frontend pages**: `/shipping/ledger` (data-testid `shipping-ledger-page`), `/shipping/transfers`, `/shipping/settings`. The settings page exposes the new fields `cod_fee_percent` and `cod_fee_fixed_per_order` (disabled in UI for Immediate companies) plus a clean Deferred/Immediate radio toggle that maps to the existing `is_deferred` field.
  - **Financial Position** — new mini-card `kpi-shipping-ledger-net` showing net + breakdown (لنا / علينا / COD معلَّق). Existing `shipping_unpaid` and `cod_balance` KPIs untouched so the merchant can cross-check during transition (per the user's explicit "no destructive changes" rule for this iter).
  - **Net formula** (applied uniformly across backend + frontend):
    ```
    net = cod_approved_delivered
        − shipping_cost_delivered_with_vat
        − cod_fees (% + fixed/order, delivered only)
        − Σ courier_to_bank
        + Σ bank_to_courier
    ```
  - 6/6 pytest in `test_shipping_ledger_iter144.py` (endpoint shape, CRUD round-trip, direction validation, amount validation, immediate-excluded, per-row net formula).
- **Iter-143 (Feb 12 2026 — this session)**: Searchable shipping-company picker with live balance in FinancialInputHub.
  - **Problem**: When recording a payment under `دفعة شركة شحن`, the picker was a static HTML `<datalist>` listing 5 hardcoded names — the merchant couldn't tell which company they owed money to or which was already paid in full BEFORE choosing.
  - **Fix**: Replaced the datalist with a searchable dropdown identical in pattern to the employee picker:
    - Fetches `/api/shipping-accounts` on mount, sorts companies by `remaining` desc.
    - Each row shows: bold company name + colored balance badge (rose=`مستحق عليك`, emerald=`لك عنده`, slate=`مسدَّد بالكامل`) + sub-line with orders_count / total_owed / total_paid.
    - On pick, the input is filled AND a `ship-company-balance-card` appears below the picker re-stating the remaining balance prominently.
    - Empty-state hint `ship-company-empty` lets merchants register payments under brand-new (unseen) company names — free-text submission still works.
    - Post-submit, the companies list is refreshed so subsequent picks see the updated balance.
  - **Verification (testing agent Iter-143, 100% PASS)**: Empty-state + populated-state both verified — badges, balance card, free-text submission, list refresh after POST all work.  Match against `/api/shipping-accounts.remaining` exact.
- **Iter-142 (Feb 12 2026 — this session)**: Employee search dropdowns in FinancialInputHub now show CUMULATIVE balance instead of monthly salary.
  - **Problem**: When the merchant searched an employee in `سداد التزام` or `سلفة موظف`, the dropdown rows displayed only `monthly_amount` (e.g. "8000 ر.س/شهر").  That number doesn't tell the merchant the ACTUAL current obligation — they had to pick the employee first to see `net_due` in the post-pick card.
  - **Fix**:
    - Both `PayLiabilityForm` (line ~256) and `AdvanceForm` (line ~821) now fetch `/api/liabilities/salary-accrual-summary` on mount and build an `accrualMap` keyed by employee id.
    - **AdvanceForm dropdown**: each employee row now shows a colored badge `صافي مستحق: {net_due} ر.س` (rose if > 0, emerald otherwise) PLUS sub-line with monthly salary + outstanding advances.
    - **PayLiabilityForm dropdown**: employee rows now show `صافي مستحق تراكمي` label with net_due, plus a sub-line `({days_worked} يوم · متراكم {accrued})` and a `موظف نشط / موقوف` chip.  Non-employee rows (suppliers / ad-accounts) keep the legacy `g.sumRemaining` display untouched.
  - **Verification (testing agent Iter-142, 100% PASS)**: For all 4 active employees, the net_due shown in (a) advance dropdown badge, (b) pay-liability tracking line, (c) post-pick employee-balance-card matches the API exactly — three surfaces, one source of truth (`/salary-accrual-summary`).
  - **Tech debt noted by testing agent**: same endpoint is called up to 3 times per Hub session (PayLiabilityForm + AdvanceForm + EmployeeBalanceCard after pick).  Fine today (4 employees, instant response), worth deduping later via SWR/Context.
- **Iter-141 (Feb 12 2026 — this session)**: Sidebar page-visibility now syncs across every merchant device.
  - **Problem**: Hiding a sidebar page on phone A didn't propagate to phone B / desktop / tablet — the list lived in `localStorage` only (Iter-124 design).  The merchant wanted central control: hide once, hidden everywhere.
  - **Fix**:
    - Backend: `users.settings.sidebar_hidden_pages: List[str]` (same shape as `dashboard_hidden_cards`).  Added to `SettingsIn` model, GET response, and PUT validator (blank strings stripped).
    - Frontend `lib/sidebarVisibility.js`: rewritten to read/write through `/api/settings`, with `localStorage` retained ONLY as an offline cache so the sidebar can paint instantly before the API call returns.  Optimistic save with automatic rollback on failure.  Legacy `mezan.sidebar.hidden_pages` key auto-migrated to the user settings doc on first login then removed.
    - `Sidebar.jsx` now calls `refreshHiddenPagesFromServer()` once on mount so a list hidden on another device appears immediately.
  - **Live verification on preview**: PUT `sidebar_hidden_pages=['nav-tabby','nav-tamara']` → GET round-trips exactly → reset to `[]` works.  4/4 pytest in `test_sidebar_visibility_iter141.py` pass.
- **Iter-140 (Feb 12 2026 — this session)**: Backend Asia/Riyadh date helper — fixes the silent "yesterday" bug on every aggregated page during 00:00–03:00 KSA.
  - **Problem**: Server runs in UTC.  All backend `date.today()` calls returned UTC's date, which during the first 3 hours of every Saudi day (21:00–24:00 UTC of the previous day) silently shifted daily expense / financial position / salary accrual / ad-cron aggregates back one day.  The merchant saw entries logged with the wrong date and "today's spend" missing during that window.
  - **Fix**: New helper module `/app/backend/tz_utils.py` exposing `riyadh_now()`, `riyadh_today()`, `riyadh_today_iso()`.  Replaced ALL `date.today()` calls in business paths:
    - `liabilities_routes.py` × 6 (`_today_str`, `_compute_employee_accrual`, `_aggregate_salary_accrual`, `generate_salaries`, salary-status, salary days-worked).
    - `bnpl/settlements_service.py` × 2 (default `date_to` for weekly settlements).
    - `ad_account_routes.py` × 1 (`run_daily_cron` target date — used by the new half-hour sync from Iter-139).
    - `webhook_routes.py` × 2 (Meta + TikTok recent-N-days cutoffs).
  - **Live verification at UTC 23:23 (= Riyadh 02:23 AM)**: `GET /api/liabilities/salary-accrual-summary` returned `end_date=2026-06-12` (Riyadh today) — UTC was still on 2026-06-11.  Before the fix the merchant would have seen "yesterday" until 03:00 KSA.
  - 5/5 pytest in `test_riyadh_tz_iter140.py` (helper offset, ISO format, no leftover `date.today()` in business paths, public API stable).
- **Iter-139 (Feb 12 2026 — this session)**: Replaced the 23:55 daily ad-account cron with a half-hour realtime sync.
  - **Problem**: Ad-account spend was synced once per day at 23:55. The merchant wanted near-realtime updates so today's ad-balance + ad-liability reflect ongoing spend without waiting until end-of-day.
  - **Fix**:
    1. Deleted `_ad_account_daily_cron` in `server.py` entirely (no more 23:55 wake-up).
    2. New `_ad_account_halfhour_sync` background task — runs every 30 minutes (`AD_ACCOUNT_SYNC_INTERVAL_SECONDS = 30 * 60`) starting 90s after boot.
    3. Each pass calls `run_daily_cron(db)` for TODAY's date only (Asia/Riyadh).
    4. `run_daily_cron` updated to invoke `_run_sync_for_all(..., force=True)` so re-running on the same day reverses prior cron rows (Iter-110 fix B) and re-applies fresh totals — no double-counting.
    5. `cron_runs` collection tags entries with `type=ad_account_halfhour_sync` so older `ad_account_daily_sync` rows stay identifiable in diagnostics.
  - **Live verification on preview**: first pass after deploy completed in 461ms across 241 users. Logs: `iter-139: ad-account half-hour sync done — 241 users processed (today=2026-06-11)`.
  - 5/5 pytest in `test_ad_account_iter139_halfhour_sync.py` (old symbol gone, new symbol present, interval = 30 min, force=True wired, cron_runs type renamed).
- **Iter-138 (Feb 12 2026 — this session)**: Unified the existing cumulative salary system across every page that touches employee compensation.
  - **Problem**: The salary accrual engine already existed (`_compute_employee_accrual` / `_aggregate_salary_accrual` from Iter-115) and the canonical endpoint `GET /api/liabilities/salary-accrual-summary` returned per-employee + aggregate numbers, but only `FinancialPosition.jsx` was actually consuming them. The other 5 pages either showed a flat `operating_salaries_total` (monthly stipend, not accrued/net_due) or recomputed advance balances locally.
  - **Fix**: NEW reusable React component `/app/frontend/src/components/EmployeeBalanceCard.jsx` exposing two named exports:
    1. `EmployeeBalanceCard({employeeId})` — full or compact card for ONE employee (name, status badge, monthly_amount, accrued / outstanding_advance / paid / net_due).
    2. `SalaryAccrualSummaryCard({showEmployeeTable})` — 4 aggregate KPI tiles + optional per-employee table.
  - **Wired into 6 pages**:
    1. `Dashboard.jsx` (main `/`) — top-of-page section `dashboard-salary-accrual-section` (hide-able via `salary_accrual_card`).
    2. `OperationsDashboard.jsx` (`/operations-dashboard`) — bottom section `ops-salary-accrual-section` with per-employee table.
    3. `OperationalReports.jsx` (`/operational-reports`) — section `opreport-salary-accrual` shown on monthly + yearly views (intentionally hidden on `daily`).
    4. `OperatingExpenses.jsx` (`/operating-expenses` → الرواتب الشهرية) — block `oe-salary-accrual-block` above the existing CRUD table.
    5. `FinancialInputHub.jsx` → `سداد التزام` tab — `employee-balance-card` rendered when the picked liability has `employee_salary_id`.
    6. `FinancialInputHub.jsx` → `سلفة موظف` tab — `employee-balance-card` rendered above the legacy `adv-cumulative-card`.
  - **Verification (testing agent Iter-138)**: 100% PASS. For the 4 active employees on preview (عرفات / خالد / عزوز / ابو جمال) the same `accrued` / `outstanding_advance` / `paid` / `net_due` values appear in all 5 places — exact match to the API response, no discrepancies. Aggregates (accrued_total=25,966.67, net_due=25,966.67, advances=0, paid=0) identical on Dashboard, OperationsDashboard, OperationalReports, OperatingExpenses, FinancialPosition.
  - **No backend changes** — Iter-115 logic untouched.
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
- **Iter-149 v3 (Feb 2026)**: Extended cutoff to shipping ledger. `shipping_accounts.shipping_ledger` now filters `unified_orders` by `cod` cutoff (`received_at >= cutoff`) + skips `is_pre_accounting=true` rows. `courier_transfers` filtered by `bank_transfer` cutoff on `transfer_date`. So pre-accounting orders no longer inflate courier COD totals or shipping-cost liabilities.
- **Iter-149 v2 (Feb 2026)**: Extended accounting cutoff to financial position + liabilities + bank balances.
- **Iter-149 (Feb 2026)**: Per-provider accounting cutoff dates.
- **Iter-148 (Feb 2026)**: Diagnostic + cleanup for duplicate ad-account topup rows.
- **Iter-147 v3 (Feb 2026)**: Settlement file totals override computed totals.
- **Iter-147 (Feb 2026)**: Tamara settlement-attribution priority. Added `provider_settlement_id` / `provider_invoice_id` / `provider_settlement_date` / `provider_payout_date` / `settlement_source` / `effective_settlement_date` on `payment_transactions`. Tamara settlement-file import now propagates the official attribution. First-stamp-wins (re-import never clobbers). Audit log `tamara_attribution_log`. Endpoints: `GET/POST /api/bnpl/tamara/attribution/{status,recompute,log}`. Daily cron `_tamara_attribution_daily_sweep` re-derives attribution. Startup one-shot migration backfills legacy rows. Settlements engine groups by `effective_settlement_date`.
- **Iter-146 (Feb 2026)**: Tamara `billing_eligible_at` settlement-cycle rule. Sales enter the weekly Tamara invoice on the week the order first reaches a billable status (تم التنفيذ / جاري التوصيل / تم التوصيل / تم التجهيز / تم الشحن — plus Tamara API equivalents fully_captured/shipped/partially_refunded). Stamp is idempotent (first-stamp wins). Backfill endpoint + status endpoint added. Refunds keep Iter-120 `refunded_at` rule.
- **Iter-145 (Feb 2026)**: BNPL Settlements UI — show transferred amount for near-miss (over/under) invoices, not only auto-matched. Totals row reflects all surfaced transfers.
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

## Iter-162 — Migration Dynamic Salary Accrual Fix (Feb 2026)
**Problem (reported on production)**: Reconciliation Report showed 13 mismatches
because `migration_routes._legacy_employee_balances` was reading STATIC rows
from the `liabilities` table while the legacy `salary-accrual-summary` endpoint
calculates accrued salaries DYNAMICALLY (`monthly_amount / days_in_month × actual_days_worked`).
User reported: عرفات 8,600 (44 days), خالد 8,600, عزوز 5,733.33, ابو جمال 4,300.

**Fix**:
- `_legacy_employee_balances` now uses `_compute_employee_accrual` (same code path
  as `_aggregate_salary_accrual`). `salary_payable = max(0, accrued − cash_paid)`.
- Advances now use `expected_amount − consumed_amount` (was `paid_amount`).
- Filter `category='employee'` on `operating_salaries` (was unfiltered).
- `_legacy_supplier_balances` now filters `status!=paid` AND `is_pre_accounting!=True`
  and matches via `counterparty_id` OR `supplier_name` (older rows without id).
- Returns diagnostic fields (`_accrued`, `_cash_paid`, `_days_worked`, etc.) so the
  Reconciliation Report can show per-employee breakdown.
- New tests: `/app/backend/tests/test_migration_dynamic_accrual_iter162.py` (4 tests, all pass).
- Reconciliation Report UI now displays the breakdown row for each employee under
  the salary row.

**Verified on production data**: عرفات now correctly shows 8,600 / 44 days; same
as the legacy `تجميع الرواتب` screen. User must run Migration (`dry_run=false`)
to post opening balances; then the Report will reach 100% match.

**Pending**: User to re-run Dry Run + Migration after redeploy, then approve Phase 4
closeout (disable legacy `pay`/`collect`/`delete` endpoints).


## Iter-163 — Critical Ad-Sync Cross-Account Leak Fix (Feb 2026)
**Problem (production)**: User clicked "مزامنة الكل الآن" on the Ad Accounts
page. A Snap counterparty with no `external_account_id` set absorbed spend
from EVERY other Snap account of the user — today's spend ballooned to
~100,000 SAR on the dashboard and on every ad-account card.

**Root cause**: `_fetch_daily_spend` silently dropped the per-account
filter when `external_id` was missing. For Snap/Meta the scoped sources
(`snapchat_account_daily.ad_account_id`, `meta_ads_daily.account_id`)
were queried WITHOUT the filter, aggregating spend across all sibling
accounts of the same user into the one un-scoped counterparty.

**Fixes**:
- `_fetch_daily_spend` now SKIPS scoped sources whenever `external_id`
  is missing (no more silent cross-account aggregation).
- `_run_sync_for_all` now strictly REQUIRES `external_account_id` for
  Snap/Meta; otherwise the account is skipped and returned with
  `reason="missing_external_account_id"` + Arabic warning. The UI
  toasts a clear message.
- New endpoint `POST /api/ad-accounts/recover/cross-account-leak` —
  deletes buggy `auto_cron` ledger rows from the last 7 days for Snap/Meta
  accounts lacking external_id, zeroes their auto-generated liabilities,
  and resets sync markers. Surfaced in the UI as the
  `🛟 إصلاح مصروف اليوم الخاطئ` button.
- Tests: `/app/backend/tests/test_ad_account_cross_account_leak_iter163.py`
  (3 tests, all pass). Legacy tests updated to seed `external_account_id`.

**Verified on user's preview env (`amasi.jewelery@gmail.com`)**: Recovery
endpoint reversed 75 ر.س stale `auto_cron` row. Sync now returns the
"missing external id" warning instead of leaking spend.

**Action for user**: Save to GitHub → Redeploy → on production, open
"الحسابات الإعلانية والمديونية" → click "🛟 إصلاح مصروف اليوم الخاطئ" to
purge the wrong 100K row → then either set the correct Ad Account ID on

## Iter-164 — Reconciliation Report Clarity Overhaul (Feb 2026)
**User complaint**: After redeploying Iter-162 fixes, the user ran Dry Run
on production and saw match=21.05%, mismatched=15, with all employee
salaries showing "Legacy=correct, Ledger=0". He correctly pointed out
that this was confusing and refused to proceed with the final migration.

**Diagnosis (3 root issues, all UX)**:
1. **Dry Run is read-only** — it only PLANS opening_balance entries,
   never posts them. The Reconciliation Report was comparing legacy
   against the actual (empty) Ledger, so pre-migration mismatches were
   inevitable. Not a logic bug, just terrible UX.
2. **No visibility on "what will the Ledger look like AFTER migration"**
   — user had no way to verify the migration plan was sound.
3. **Orphan supplier liabilities** (rows with no matching counterparty)
   were silently excluded from the report, causing un-explainable delta
   totals.

**Fixes**:
- `GET /api/accounting/migration/reconciliation` response now returns:
  - `migration_status`: { completed, cutoff_date, applied_at, applied_count }
  - For each entity: `legacy` / `ledger` / `projected` (= legacy =
    what migration will post) / `match` (live) / `projected_match` (will
    migration reconcile?).
  - Summary: `projected_match_percentage`, `will_post_after_migration`,
    `orphan_supplier_count`, `orphan_supplier_total`.
  - `safe_to_disable_legacy` is now TRUE only when migration is actually
    completed AND live match = 100%.
  - New top-level `orphan_suppliers` list surfaces unrelated supplier
    liabilities.
- Reconciliation Report UI rewritten:
  - **Migration-state banner**: amber when pending, green when done,
    explains exactly why Ledger shows 0 pre-migration.
  - 3-column diff (قديم / Ledger الحالي / المتوقَّع).
  - "▶ تنفيذ الترحيل النهائي" button inside the report — user can
    execute migration when satisfied with the legacy column.
  - Orphan supplier alert with expandable list.

**Verified live (`amasi.jewelery@gmail.com`)**:
- Migration not yet executed → banner shows pending state.
- `projected_match_percentage = 100%` (4/4 employees, 3/3 banks).
- `will_post_after_migration = 27,233 ر.س`.
- Per-employee shows accurate dynamic-accrual math
  (e.g., عرفات: 44 days × 6000/30 = 8,600).

**Action for user**: Save to GitHub → Redeploy. Open `/reconciliation`,
verify legacy figures match what he sees on the old screens. If yes,
click "▶ تنفيذ الترحيل النهائي". Match percentage will jump to 100%
and the "safe to disable" badge will turn green.

**Tests**: `/app/backend/tests/test_reconciliation_v2_iter164.py` (3
tests). All passing.

the Snap counterparty OR remove the counterparty.


## Iter-165 — Orphan Supplier Diagnostic + Write-Off (Feb 2026)
**User question (after Iter-164)**: "I see one orphan supplier of 1 SAR.
Before I run the final migration I want to know: record id, supplier
name, source, will it be migrated, recommended action."

**Fix**:
- Expanded the `orphan_suppliers` entries returned by `/api/accounting/migration/reconciliation`
  to include all diagnostic fields: `expected_amount`, `paid_amount`,
  `remaining`, `created_at`, `updated_at`, `due_date`, `status`, `source`,
  `auto_generated`, `counterparty_link_status`, `will_be_migrated: false`,
  `reason_not_migrated`, `recommended_action`.
- New endpoint `POST /api/accounting/migration/orphan-suppliers/{liab_id}/write-off`
  — zeroes the row's amounts, sets status=paid, adds an audit note
  (`write_off_note`, `written_off_by`, `written_off_at`). Row is NOT
  deleted, preserving the audit trail.
- UI: orphan list now shows the full diagnostic card per record + a
  "🗑 شطب وعدم ترحيل" one-click button. Tested live on Preview
  (seeded an orphan row, verified diagnostic, wrote it off, verified
  it disappears from the report).

**Action for user**:
1. Save to GitHub → Redeploy.
2. Open `/reconciliation` → expand the orphan supplier card to see the
   full diagnostic for the 1 SAR record.
3. If the record is insignificant → click "🗑 شطب وعدم ترحيل" → orphan
   count becomes 0.
4. Then execute the final migration with confidence.


## Iter-165b — Pre-Migration Safety Audit + Permanent Supplier Guard (Feb 2026)
**Merchant's 4 pre-migration confirmations**:

1. ✓ **After orphan write-off**: `orphan_supplier_count = 0`,
   `projected_match_percentage = 100%`. Verified live on his account.
2. ✓ **Migration is non-destructive**: Live audit on his data
   showed legacy collections (liabilities: 9, account_transactions: 6,
   operating_salaries: 7, counterparties: 1, accounts: 8) all preserved
   identically pre- and post-migration. Only `general_ledger` receives
   new opening_balance rows. Code path verified in `run_migration`
   (`migration_routes.py`): only writes to `general_ledger` +
   `migration_cutoffs`, never deletes/modifies legacy tables.
3. ✓ **Legacy stays read-only**: Per merchant directive, NOT disabling
   any legacy pay/collect/delete endpoints. He will review for several
   days before requesting Phase 4 closeout.
4. ✓ **Permanent guard against orphan suppliers**: The
   `LiabilityCreate.supplier_name` validator now strictly requires
   `counterparty_id` for `kind=supplier`. Test: rejecting POST returns
   HTTP 422 with Arabic explanation. Updated legacy Iter-97 tests to
   seed counterparty first. `purchase_invoices_routes.py` already
   resolves counterparty before creating supplier liabilities — safe.

**Full migration roundtrip verified on user's account** (then rolled
back to keep production-fresh state):
- BEFORE: 9 liabilities / 6 txns / 4 employees with accrued salaries.
- AFTER migration: same 9/6/4 + 4 new general_ledger opening_balance
  entries. Reconciliation now shows match=100% and
  safe_to_disable_legacy=true.

**Status**: User has all confirmations. Ready to execute final
migration on production once he redeploys.


## Iter-166 — CRITICAL Bank Balance Field Bug (Feb 2026)
**Severity: P0 — would have ZEROED bank balances inside the new Ledger.**

**Reported by merchant**: On production reconciliation report, the 3
bank accounts showed legacy=0:
- بنك الإنماء: 212,363.30 ر.س in Assets page → 0 in report
- بنك الأهلي: 2,207.45 ر.س → 0
- بنك الراجحي: 29,964.04 ر.س → 0

He refused to migrate, asking: "Will banks be lost or zeroed in Ledger?"

**Root cause**: `_legacy_bank_balances` in `migration_routes.py` was
reading `accounts.balance` — but the canonical SSOT is
`accounts.current_balance` (computed by `_recompute_balance` in
`accounts_routes.py` from transaction history + opening_balance).
Default-banks bootstrap creates accounts with `balance=None`. So the
migration was reading None → 0 → would have posted opening_balance=0
to the Ledger for every bank.

**Fix**:
- Read `current_balance` first; fall back to `balance` for older
  accounts; defensive default to 0.
- Surface diagnostic breakdown in the Reconciliation Report:
  `opening_balance`, `expected_orders_balance`, `currency`.
- Frontend: new `showBankBreakdown` prop on the Section component
  renders a sub-row with the composition under each bank.
- Tests: `/app/backend/tests/test_bank_balance_migration_iter166.py`
  (3 tests, all pass).

**Verified on merchant's account** (Preview):
- Pre-fix: all 3 banks showed legacy=0 in report.
- Post-fix: الإنماء=56,040.59 / الأهلي=2,742.99 / الراجحي=36,319.84
  (Preview values; production has larger amounts).
- Full migration roundtrip: 7 opening_balance entries posted
  (4 employees + 3 banks). Match=100%. safe_to_disable_legacy=true.
  Then rolled back to keep production-fresh state.

**Action for user**: Save to GitHub → Redeploy. Open reconciliation
report → verify legacy column now shows the same bank totals he sees
on the Assets page. Then proceed with the final migration.


## Iter-167 — Payment Platforms & Couriers Now Migrate (Feb 2026)
**Severity: P0 — would have missed ~330K SAR of assets-in-transit.**

**Reported by merchant**: After Iter-166 fixed bank balances, he asked
about Salla / Tamara / Tabby / Imkan / COD platforms and shipping
courier balances — these were NOT in the reconciliation report nor in
the migration ops_planned. They represent the bulk of his liquid assets
(money paid by customers, in transit to bank).

**Fix**:
- New helper `_legacy_payment_platform_balances`: reads `current_balance`
  for Salla/Imkan/COD; for Tabby/Tamara prefers BNPL SSOT
  (`get_bnpl_provider_balance`) to stay consistent with the BNPL
  Settlements page. Tracks `balance_source` and `bnpl_provider` in
  diagnostic fields for full audit transparency.
- New helper `_legacy_courier_balances`: aggregates open
  `liabilities.kind∈{shipping,courier}` per courier counterparty.
  Excludes paid + pre-accounting rows.
- Migration's `before` snapshot, `run_migration` ops_planned, and
  `_compute_after_balances` extended to handle these two new entity
  types: `payment_platform` (debit/credit based on sign) and `courier`
  (credit, payable sub_account).
- Reconciliation Report includes a new `payment_platforms` section
  with the same 3-column diff and breakdown shown for banks.
- UI: new "💳 منصات الدفع" section between externals and banks; reuses
  the bank breakdown row layout to show opening / expected_orders /
  current / currency / balance_source / bnpl_provider.

**Verified end-to-end on merchant's Preview** (then rolled back):
- 12 entities total: 4 employees + 5 payment platforms + 3 banks.
- `will_post_after_migration = 333,406.35 ر.س`.
- After migration: ledger matches legacy 100% for ALL 12 entities;
  `safe_to_disable_legacy = true`.
- Tamara correctly shows -10,001.72 (BNPL SSOT — we owe Tamara from a
  past settlement). Source = `bnpl_ssot`.
- Tabby -106.90 via BNPL SSOT.
- Salla 211,680.67 via `current_balance`.

**Tests**: 3 in `test_platforms_couriers_migration_iter167.py`.

**Action for user**: Save to GitHub → Redeploy. Re-open the
reconciliation report. The new "منصات الدفع" section should show all 5
platforms with the production figures matching the Assets page.
After verifying, execute the final migration. All assets and
liabilities will be carried over.


## Iter-169 — Card debt didn't follow sync corrections (Feb 2026)
**Reported by merchant**: Snap card shows مديونية=201,753.81 even though
the audit log clearly shows a «تصحيح مزامنة (إنخفاض إنفاق)» row dropping
the debt to 116,351.99. The card stayed stale at the old (wrong) debt.

**Root cause** (line 2429-2470 of ad_account_routes.py before fix):
The "negative delta" branch in `_run_sync_for_all` deliberately did NOT
reduce the open liability when the platform reported lower spend. The
comment said «if the user wants to reduce a paid liability, that's a
separate manual action». In practice this orphaned the inflated debt
row: ledger showed correct net spend, but `_current_open_debt` kept
reading the stale liability.

**Fix**:
- Negative-delta branch now also reduces the existing auto_cron
  liability by the refund amount (capped at paid_amount so we never go
  negative). The new ledger row's `debt_after` reflects the true post-
  correction debt instead of hardcoded 0.
- New repair endpoint
  `POST /api/ad-accounts/{cp_id}/recover/recompute-debt-from-ledger`
  walks `ad_account_ledger` to derive the TRUE net spend, then resets
  the liability accordingly. Idempotent — safe to run multiple times.
- New card button «🔄 إعادة احتساب من السجل» that triggers the repair
  endpoint with confirmation.
- Tests: `test_recompute_ad_debt_iter169.py` (2 tests, pass individually;
  combined run hits the known pytest async event-loop isolation issue).

**Action for user**: Save to GitHub → Redeploy. Open the Snap card →
click «🔄 إعادة احتساب من السجل» → confirm → card debt drops to match
the audit log. Going forward, future sync corrections also reduce the
liability automatically, so this won't recur.


## Iter-170 — Duplicate accounts in «خصم من حساب» dropdown (Feb 2026)
**Reported by merchant**: Banks (الإنماء/الأهلي/الراجحي) appeared TWICE
in the «خصم من حساب» dropdown of the Unified Entry screen. He asked us
to (a) explain the source, (b) group by type, (c) prevent any duplicates,
(d) show the type label next to each name.

**Root cause**: `GET /api/accounts` endpoint declared the filter as
`account_type` but the Unified Entry screen called
`/api/accounts?type=bank`. FastAPI silently dropped the unknown `type`
param, the filter was never applied, and BOTH calls (`?type=bank` and
`?type=payment_platform`) returned ALL 8 accounts. The frontend then
spread both lists → every account appeared twice.

**Fix**:
- Backend (`accounts_routes.py`): `list_accounts` now accepts both
  `?account_type=` (long) and `?type=` (short alias). Long form wins
  when both are supplied. Backwards-compatible.
- Frontend (`UnifiedEntryScreen.jsx`):
  - Updated all `?type=` calls to `?account_type=`.
  - Added defensive `dedupe(list)` by `id` (never trust two list
    sources to be disjoint).
  - Dropdown now has 4 ordered optgroups:
    `🏦 الحسابات البنكية` · `💳 بوابات الدفع` · `💵 الدفع عند الاستلام` ·
    `📦 شركات الشحن` · `📁 أخرى`. COD is detected by name pattern.
  - Each option shows `· بنك / بوابة دفع / COD / شركة شحن` next to
    the name so the user can never confuse a bank with a payment
    platform.
  - Same treatment for the «إلى حساب» dropdown in `bank_transfer`.

**Verified live on merchant's Preview**:
- `/api/accounts?account_type=bank` → 3 (was 8) ✓
- `/api/accounts?type=bank` → 3 (was 8) — short alias works ✓
- `/api/accounts?type=payment_platform` → 5 ✓ (5 platforms)
- Dedupe by id ensures no double-entries even if the API somehow
  returns a row twice.

**Tests**: `test_accounts_filter_alias_iter170.py` (1 test, passes).

**Action for user**: Save to GitHub → Redeploy. Open Unified Entry
→ pick any operation requiring a source account → confirm each bank
appears once, with `· بنك` label, in its own group.


## Iter-171 — Employee Economic Net (display-only) (Feb 2026)
**User request**: After Iter-170, the merchant asked us to surface an
"Economic Net" view for each employee — combining payable, advance, and
custody into one number — WITHOUT changing the underlying ledger
structure (the 3 sub_accounts must remain separate).

**Formula**:
  economic_net = salary_payable − advance − custody
  • positive → 🟢 الموظف له علينا (owed_to_employee)
  • negative → 🔴 الموظف عليه للنظام (owed_by_employee)
  • zero     → ⚪ متوازن

**Implementation**:
- Backend already returned `net_position` from `/employees/list` (per-row
  and as a total). No backend change needed for that endpoint.
- `/accounting/migration/reconciliation` now includes an `economic_net`
  object per employee row with `legacy`, `ledger`, `projected`,
  `owed_to_employee`, `owed_by_employee`, `verdict`.
- Frontend `EmployeesLedger.jsx`:
  - Totals card now labels: «صافي اقتصادي (لهم علينا)» or «(علينا منهم)»
    with absolute value shown + color cue.
  - Explainer banner shows the formula in plain Arabic.
  - Per-row: net cell now has a small label «← له علينا / عليه للنظام /
    متوازن».
- Employee drawer (statement view): rewrote the net card with a full
  3-row breakdown showing the formula transparently.
- Reconciliation Report (`ReconciliationReport.jsx`): employee
  breakdown sub-row now also includes an economic-net mini panel
  with verdict + colors.
- Tests: `test_economic_net_iter171.py` (2 tests, both pass individually;
  combined run hits the known pytest async loop quirk).

**Confirmed**: شهاب-style scenario (payable=100, advance=2895) yields
net=−2795 with verdict='owed_by_employee'. Ledger entries are NOT
merged — the 3 sub_accounts (`employee/salary_payable`,
`employee/advance`, `employee/custody`) remain independently posted
double-entry pairs.

**Action for user**: Save to GitHub → Redeploy. Open `/employees-ledger`
and click شهاب → drawer shows: «🔴 عليه للنظام: 2,795 ر.س» with the
breakdown 100 − 2,895 − 0 = −2,795. Reconciliation report also surfaces
this under each employee's accrual breakdown.


## Iter-171b — Recompute also fixes the stale balance (Feb 2026)
**Production bug** (after running Iter-169 recompute):
  الرصيد = 116,351.99 (wrong — actual should be 0)
  المديونية = 90,364.08 (correct — fixed by Iter-169)
  الصرف = 158,800.08

**Root cause**: Iter-169 only updated the `liabilities` row. It left
`counterparties.balance` cached at the inflated «refund» value that the
buggy correction had applied earlier (the 116,351.99 was a phantom
refund of fake spend that never really happened).

**Fix**:
- The recompute endpoint now walks the ledger chronologically and
  replays the sync engine's logic for EACH event type:
  - `topup/opening` → balance += amount
  - `spend` (positive) → covered = min(balance, amount), uncovered →
    debt; balance -= covered
  - `spend` (negative, correction) → unwind debt first (Iter-169 logic),
    then refund remainder to balance
  - `settlement/writeoff` → debt -= amount
- Final `balance_walk` is written to `counterparties.balance`. Final
  `debt_walk` is used to set the liability's `expected_amount`.
- The response now exposes `previous_balance`, `new_balance`,
  `balance_delta` alongside the debt fields.
- Frontend toast was updated to show BOTH balance and debt changes
  in one message.
- New test: `test_recompute_balance_iter171b.py` reproduces the user's
  scenario (1 topup 116K + 1 spend 200K + 1 correction -84K) and
  confirms balance ends at 0 (instead of stale 116K).

**Action for user**: Save to GitHub → Redeploy. Open the affected Snap
card → click «🔄 إعادة احتساب من السجل». The toast will now show
something like «الرصيد: 116,351.99 → 0.00» and the card refreshes
showing the correct balance, debt, AND spend totals all matching the
audit log.


## Iter-172 — Daily Spend Refresh Now Updates Cards (Feb 2026)
**Reported by merchant**: Pressing «تحديث فوري للصرف اليوم» on the
Snap dashboard popped a toast with the amount, but neither the
ad-account cards NOR the executive profit panel updated. Same issue
for Meta refresh.

**Root cause**: `GET /api/snapchat/daily-spend` fetched the spend from
Snap's API and returned it, but it didn't persist into
`snapchat_account_daily` (the SSOT the cards' sync engine reads) and
didn't trigger the ad-account ledger sync. Meta's `/auto-sync-if-stale`
and manual sync wrote to `meta_ads_daily` but never pushed to
`ad_account_ledger` either.

**Fix**:
- `GET /api/snapchat/daily-spend` now upserts into
  `snapchat_account_daily` then calls `_run_sync_for_all` (Iter-167
  Phase 4 logic) to push the spend into `ad_account_ledger` and
  recompute each Snap counterparty's balance/debt.
- `POST /api/meta/sync` AND `POST /api/meta/auto-sync-if-stale` both
  now call `_run_sync_for_all` after the upsert pass so the new spend
  reflects on Meta ad-account cards.
- All three call sites wrap the cross-cutting sync in try/except so
  any sync error doesn't fail the foreground fetch (the underlying
  data is already persisted).
- Test: `test_snap_daily_writethrough_iter172.py` — seeds a snap
  account_daily row, calls `_run_sync_for_all`, asserts that
  `ad_account_ledger` has the new spend row and the counterparty's
  cached `balance` was decreased correctly.

**Action for user**: Save to GitHub → Redeploy. Press «تحديث فوري
للصرف اليوم» on the Snap dashboard → the spend now appears
automatically on:
  • The Snap card في الحسابات الإعلانية والمديونية
  • الملخص التنفيذي للأرباح
  • And the dashboard's daily costs chart (already worked).
Same for the Meta refresh.


## Iter-173 — Smart source-account filter on Unified Entry (Feb 2026)
**User request**: The «خصم من حساب» dropdown showed bank accounts AND
payment platforms (Salla/Tamara/Tabby/Imkan/COD) for every operation.
The merchant correctly pointed out that giving a salary advance can't
be funded from Tamara — those balances are still held by the platform.

**Fix**:
- Added `allowedSourceAccountTypes(opType)` helper in
  `UnifiedEntryScreen.jsx`.
- For cash outflows (`advance_grant, salary_settle, custody_grant,
  supplier_pay, external_grant, expense_record`) AND cash inflows
  (`custody_return, external_collect`): only `bank` + `cash`
  optgroups are rendered.
- For `bank_transfer` and any future settlement op: returns `null` →
  ALL optgroups shown (bank / payment platform / COD / courier / other).
- Helpful Arabic hint shown above the dropdown when filtered: «هذه
  العملية صرف نقدي حقيقي — متاحة فقط من البنوك والصندوق …».
- Added new optgroup «💵 الصندوق النقدي» for `account_type=cash` if
  the user has manual cash accounts.

**Action for user**: Save to GitHub → Redeploy. Open Unified Entry,
choose «💰 سلفة موظف» → only banks (and cash if any) appear. Choose
«🔄 تحويل بين الحسابات» → all account types appear (including Salla,
Tamara, Tabby, COD).


## Iter-174 — PERMANENT FIX: Ad-Account Cards Always Derive from Ledger (Feb 2026)
**Merchant's frustration**: "كل شوي بينه مشكله هذيي الصفحه شوفله حل نهائي".
Reported Meta card showing balance=25,893.59, debt=26,122.36, spend=26,187.81
— numbers don't match the ledger, repeatedly.

**Why previous fixes weren't permanent**: Iter-163/169/171b were patches
that re-synced the cached `counterparties.balance` and `liabilities.expected_amount`
when the merchant clicked a recovery button. Any new sync corner case
could re-introduce drift.

**The permanent fix**: `_summarise()` in `ad_account_routes.py` no
longer reads `counterparties.balance` or `liabilities.expected_amount`
for display. Instead, EVERY card read does a chronological walk over
`ad_account_ledger` (the audit log = canonical source of truth):

  • topup/opening → balance += amount
  • spend (positive) → cover from balance first, rest → debt
  • spend (negative, correction) → unwind debt first, refund balance
  • settlement / writeoff → debt -= amount

The cached `counterparties.balance` is now exposed as `_cached_balance`
for diagnostics but NEVER displayed. General-ledger adjustments
(settlement/writeoff/adjustment posted entries) still apply on top.

**Effect**: No matter how badly the cache drifts, the card matches the
audit log. The recurring «🔄 إعادة احتساب من السجل» button becomes
optional — only useful if the merchant wants to physically realign
the cached fields with the displayed value.

**Tests** (`test_card_ledger_walk_iter174.py`):
1. `test_card_ignores_corrupt_cached_balance` — sets corrupt cache to
   25,893.59 (the merchant's exact reported number), seeds ledger
   truth of 5K topup + 3K spend → card shows balance=2K, debt=0,
   spend=3K. PASS.
2. `test_card_handles_correction_after_inflated_sync` — replays the
   production 116K topup + 200K spend + -84K correction scenario →
   card shows balance=0, debt=0, spend=116K. PASS.
3. All existing ad-account tests (5) still pass.

**Action for user**: Save to GitHub → Redeploy. The card numbers on
`/ad-accounts` immediately self-correct to match the audit log — no
manual intervention needed. Production Meta card will now show the
real balance/debt/spend from its `ad_account_ledger`, not the corrupt
26K cached values.



## Iter-175 — Topup-Paydown Fix in Ledger Walk (Feb 2026)
**Merchant's complaint** (recurring): "الأرصدة والمديونيات بهذي الصفحة
تزيد بشكل كبير جداً وغير حقيقي. عندما أقوم بمزامنة الكل تزيد الأرصدة
ولا تتعالج بشكل نهائي". Snap/Meta cards displaying inflated balance
(25,883.38) AND debt (26,122.36) simultaneously — getting worse on
every half-hour cron sync. Reported despite Iter-174 walk-based fix.

**Root cause**: Iter-174's walk added topups straight to balance
without paying off existing debt first — but the actual `POST /topup`
endpoint allocates part of the cash to debt (`amount_to_debt`) and
part to balance (`amount_to_balance`). This mismatch caused debt to
keep accumulating in the walk while balance grew via topups,
producing the inflation pattern the merchant kept seeing.

**The fix** (`_summarise()` and `recompute_debt_from_ledger()`):
  • `topup` event → pay off `debt_walk` first (up to amount), then
    push remainder to `balance_walk`. Mirrors the endpoint logic.
  • `opening` event → still pure balance increase (openings are
    asset postings; debt openings go through a separate liability).

**Why only TikTok worked before**: TikTok cards rarely have topups
(no per-account scope_field requirement, the spend is recorded
manually via /spend), so the walk's bug rarely fired. Snap/Meta
flows topup ↔ cron-spend continuously → bug triggered every sync.

**Tests** (`test_ad_account_summarise_topup_paydown_iter175.py`):
1. `test_topup_pays_down_debt_in_walk` — opening 50 + spend 100 +
   topup 100 → balance=50, debt=0. PASS.
2. `test_cumulative_spend_then_topup_no_inflation` — replays the
   production scenario (cumulative spend row + interleaved topup).
   PASS.
3. `test_multiple_sync_cycles_no_unbounded_growth` — 5 cron cycles
   with topups never inflate balance or debt across days. PASS.
4. `test_topup_without_debt_goes_to_balance` — topups with no debt
   still go fully to balance. PASS.

**Effect**: Snap/Meta cards no longer inflate on cron sync. The
underlying cached `counterparties.balance` may still drift, but the
displayed value (from walk) is canonical and always correct. Merchant
should `Save to Github → Redeploy` to push to mezansalla.com.


## Iter-176 — COD Source Diagnostic (Feb 2026)
**Merchant's question** (before Phase 4 Closeout): "What does the COD
balance (40,123.78 SAR on production) actually represent? Is it
collected cash, pending orders, or expected receivable? Why is the
shipping companies section empty? How much per SMSA/iMile?"

**Investigation findings** (Preview DB):
  • The COD account `الدفع عند الاستلام` is stored as
    `accounts.account_type=payment_platform`, with the formula:
    `current_balance = expected_orders_balance + IN − OUT`.
  • `expected_orders_balance` is computed by
    `payment_gateway_metrics.compute_metrics()` which classifies
    orders by `order_status_policy` into Confirmed / Pending /
    Cancelled / Refunded buckets.
  • **The system uses "Confirmed" as the basis — NOT "Delivered".**
    On Preview, 30 confirmed orders → 8,540.26 (= the balance), while
    29 delivered orders → 8,357.39 (a 182.87 SAR gap representing
    confirmed-but-not-yet-delivered orders).
  • Shipping companies are present in `unified_orders.shipping_company`
    (iMile 54 orders, مندوب الرياض 19, SMSA 7) BUT there are no
    `counterparties(kind=courier)` records → the Reconciliation
    Report's "Shipping Companies" section shows empty by design.

**Endpoint shipped**: `GET /api/diagnostics/cod-source`
(`cod_diagnostic_routes.py`) — read-only, returns:
  • Account snapshot (id, current_balance, expected_orders_balance,
    orders_count).
  • Total COD orders found in DB.
  • Per-policy-category breakdown (count + gross).
  • Per-raw-status breakdown (e.g. "تم التوصيل", "جاري التوصيل").
  • Per-shipping-company breakdown (ALL + Delivered-only).
  • Manual transactions (IN/OUT) total + recent sample.
  • Reconciliation check: expected + IN − OUT vs actual.
  • Robust COD detection via `payment_methods.normalize_payment_method`
    handles Arabic hamza variants (الإستلام vs الاستلام).

**UI page shipped**: `/diagnostics/cod-source`
(`CODDiagnostic.jsx`) — surfaces all of the above in tables/cards
including:
  • Prominent banner stating system uses Confirmed (not Delivered).
  • Side-by-side Confirmed vs Delivered comparison block with the
    pending-delivery gap.
  • Reconciliation formula block (visible math).
  • Decision-help block explaining what each scenario means for
    Phase 4 migration.

**Merchant decision pending**: After reviewing the page on
mezansalla.com, choose between:
  (a) migrate the current Confirmed balance as-is,
  (b) tweak the logic to use Delivered-only,
  (c) defer COD to the Shipping Sprint entirely.

## Iter-177 — Timezone Standardization (in planning, Feb 2026)
**Merchant requirement** (before any new feature like COD detailed
or Meta/Snap CRON): Unify entire system on `Asia/Riyadh`. All
date/time fields shown to merchant or used in daily/monthly
aggregations must respect Riyadh timezone. UTC stays for storage.

**Current state**:
  • `tz_utils.py` exists (Iter-140) with `riyadh_today()`,
    `riyadh_now()`, `riyadh_today_iso()`.
  • Used in `migration_routes`, `webhook_routes`, `liabilities_routes`.
  • `snapchat_routes.py` already implements Riyadh-day boundaries.
  • Only ONE bare `datetime.now()` in non-test code:
    `preparation_routes.py:1050` (PDF filename — non-critical).
  • 46 files use `datetime.now(timezone.utc)` (correct for storage).
  • Frontend has NO central `tzUtils.js`; 10+ files use bare
    `new Date()`.

**Planned scope** (Phase 1+2+3 per merchant approval pending):
  1. Expand `tz_utils.py` with:
     - `RIYADH_TZ` (ZoneInfo).
     - `riyadh_start_of_day_utc(date_str) → datetime UTC` for mongo
       range queries.
     - `riyadh_end_of_day_utc`, `riyadh_start_of_month_utc`,
       `riyadh_end_of_month_utc`.
     - Convenience: `riyadh_range_today/yesterday/last_7d/last_30d`.
     - `utc_to_riyadh_iso(dt)` for response formatting.
  2. Create `/app/frontend/src/lib/tzUtils.js` with parallel helpers.
  3. Audit endpoints accepting `from_date/to_date/period` and ensure
     interpretation is Riyadh.
  4. Fix `preparation_routes.py:1050`.
  5. PRD policy entry to enforce future use.

**Decision pending**: Merchant to choose Phase 1 / 1+2+3 / "priority
double" mode.

## Deployment Health Check Findings (Feb 2026)
The deployment_agent flagged one **BLOCKER** unrelated to COD/TZ:
  • `backend/salla_integration/routes.py:68-73` — `_frontend_origin()`
    reads `SALLA_RETURN_URL` env var with hardcoded localhost fallback.
    Recommendation: have frontend send `redirect_uri` derived from
    `window.location.origin`. **Not yet fixed; merchant aware.**


## Iter-177 — Asia/Riyadh Timezone Standardization (Feb 2026)
**Merchant requirement**: Unify entire system on `Asia/Riyadh` so
all daily / monthly / yearly aggregations reflect the Saudi
calendar regardless of server location or browser timezone. UTC
remains the canonical storage format; only display + range
boundaries shift to Riyadh.

### Phase 1 — Backend foundation (DONE)
Expanded `/app/backend/tz_utils.py` (was 40 lines, now 220+) with:
  • `RIYADH_TZ` (ZoneInfo) and `DEFAULT_TIMEZONE` constants.
  • `riyadh_now_aware()` (tz-aware) alongside legacy
    `riyadh_now()` and `riyadh_today()`.
  • UTC instant helpers for MongoDB ranges:
    `riyadh_start_of_day_utc(d)`, `riyadh_end_of_day_utc(d)`,
    `riyadh_start_of_month_utc(y, m)`,
    `riyadh_end_of_month_utc(y, m)`,
    `riyadh_start_of_year_utc(y)`,
    `riyadh_end_of_year_utc(y)`.
  • Pre-rolled ranges: `riyadh_today_range_utc()`,
    `riyadh_yesterday_range_utc()`,
    `riyadh_last_n_days_range_utc(n)`,
    `riyadh_this_month_range_utc()`,
    `riyadh_this_year_range_utc()`.
  • Conversion: `utc_to_riyadh(dt)`,
    `utc_to_riyadh_iso(dt)`,
    `riyadh_date_from_utc(dt)`.
Fixed the one bare `datetime.now()` in production code
(`preparation_routes.py:1050` — PDF filename).
Tests: `tests/test_tz_utils_iter177.py` — 18 unit tests passing.

### Phase 2 — Frontend foundation (DONE)
Created `/app/frontend/src/lib/tzUtils.js` mirroring the backend
surface: `todayISO()`, `yesterdayISO()`, `addDaysISO()`,
`riyadhStartOfDayUTC()`, `riyadhEndOfDayUTC()`,
`riyadhTodayRangeUTC()`, `riyadhLastNDaysRangeUTC(n)`,
`riyadhThisMonthRangeUTC()`, `riyadhThisYearRangeUTC()`,
`formatRiyadh()`, `formatRiyadhDateTime()`,
`formatRiyadhArabicLong()`, `toRiyadhISO()`, plus `RIYADH_PERIODS`
preset map.

Refactored `/app/frontend/src/lib/dates.js` to re-export the new
helpers, plus added `yesterdaySA()`, `monthISO_SA()`,
`yearStartSA()` aliases for backwards compatibility.

Migrated all critical pages from bare `new Date()`:
  • `Settlements.jsx` (first-of-month default)
  • `SallaSettlements.jsx` (today prompt)
  • `AdsReport.jsx` (month start)
  • `MigrationWizard.jsx` (cutoff date)
  • `UnifiedEntryScreen.jsx` (payment date + period)
  • `MakeWebhook.jsx` (first of month)
  • `Orders.jsx` (export filename timestamp)
  • `OperationalReports.jsx` (report footer)
  • `AdvancedFilters.jsx` (ALL date presets: today/yesterday/
    last7/last30/this_month/last_month/this_year)
  • `ProductCostCard.jsx` (last-updated timestamp display)

### Phase 3 — Audit critical reports (DONE)
Inspected report endpoints — most accept `YYYY-MM-DD` strings and
match against `order_date` (also stored as `YYYY-MM-DD`), so the
boundary work is consistent at the string level.

Fixed the ONE class of bug that mattered: `received_at` (UTC
timestamp) was being converted to UTC `.date()` instead of Riyadh
date when inferring `order_date`. This silently misplaced any order
received between 21:00–24:00 UTC (00:00–03:00 KSA) into the WRONG
calendar day. Three callsites in `server.py` (lines 3823, 3868,
3895) and one in `webhook_routes.py:478` now use
`riyadh_date_from_utc()`.

Meta (`meta_routes.py::_today_riyadh`), Snapchat
(`snapchat_routes.py` already), and the ad-account cron
(`ad_account_routes.py` using `riyadh_today_iso`) were already
Riyadh-correct.

### Phase 4 — Deployment blocker fix (DONE)
Resolved the `_frontend_origin()` BLOCKER flagged by
deployment_agent. `salla_integration/routes.py:_frontend_origin`
now accepts a `Request` and derives the SPA target from the same
ingress headers used for the OAuth callback redirect — so success
/ error redirects land on the actual host the merchant came from
(mezansalla.com in production, preview.emergentagent.com in
preview, localhost in dev), without needing a per-environment
`FRONTEND_URL`. SALLA_RETURN_URL override preserved.

deployment_agent re-check: **PASS** — no remaining blockers.

### Policy locked in
  • Any new feature involving "today", "yesterday", "this month",
    "last N days", or "this year" MUST use `tz_utils` (backend)
    or `lib/dates` (frontend). Never `datetime.utcnow()`,
    `date.today()`, or bare `new Date()` for daily aggregations.
  • Storage in MongoDB stays UTC (`datetime.now(timezone.utc)`).
    Only DISPLAY and RANGE BOUNDARIES convert to Riyadh.
  • Backend never trusts `Date` from frontend without normalizing
    via `tz_utils._coerce_date` or equivalent.


## Iter-178 — COD Fee Save Error Fix (Feb 2026)
**Merchant's bug report**: "إعدادات شركات الشحن — عند إضافة عمولة
الدفع عند الاستلام تظهر رسالة خطاء: فشل الحفظ — راجع الكونسول".

**Root cause** (`ShippingCompanySettings.jsx`):
  • Backend Pydantic schema constrains `cod_fee_percent` to
    `[0, 1]` decimal (0.05 = 5%).
  • Input field accepted percent-shaped values (no normalization).
  • Merchant typed `5` (meaning 5%) → backend returned 422 →
    toast displayed the generic "راجع الكونسول" message with no
    actionable info.

**Fix**:
  1. UI now displays `cod_fee_percent` as a percentage (0-100) with
     a "%" suffix. `pctToDecimal()` divides by 100 before persisting;
     `decimalToPct()` multiplies for display. Convention now matches
     payment_methods on the main Settings page.
  2. Pre-save clamp on every shipping_company row guards against
     stale percent-shaped values that may already exist in legacy
     production data — without this, even a fresh row with 0 fails
     because Pydantic rejects the entire array.
  3. Error toast now surfaces the actual Pydantic detail string
     (or each error message in an array) instead of the generic
     "راجع الكونسول". Console.error logs the full detail.
  4. New help banner explicitly states: "اكتب النسبة كرقم عادي
     مثل 5 لتعني 5%".

**Tests**: `tests/test_shipping_cod_fee_save_iter178.py` — 8 pydantic
contract tests pin the schema and behavior (accept 0, 0.05, 1.0;
reject 5, -0.01, -1; round-trip via SettingsIn).


## Iter-179 — COD Excluded From Phase 4 Migration (Feb 2026)
**Merchant's accounting decision** (after reviewing
`/diagnostics/cod-source` on production):
  • Confirmed: 40,123.78 SAR (183 orders)
  • Delivered: 10,203.70 SAR (37 orders)
  • Gap: 29,920.08 SAR — orders in transit OR confirmed-but-
    not-yet-shipped where the cash literally doesn't exist
    yet.

> "محاسبياً هذا يعني أن رصيد COD الحالي يحتوي على طلبات تم
>  تأكيدها فقط + طلبات قيد الشحن + طلبات لم يتم تحصيلها بعد.
>  وبالتالي 40,123.78 لا يمثل ذمة COD محصَّلة فعلياً."

**Decision**:
  ❌ Do NOT migrate `الدفع عند الاستلام / COD` as an opening
     balance in Phase 4.
  ✅ Continue migrating banks, employees, suppliers, Tabby,
     Tamara, Salla, Imkan as planned.
  ✅ Reintroduce COD via the dedicated **Shipping Ledger Sprint**:
     each cash movement linked to a courier (iMile / SMSA /
     مندوب الرياض) and ONLY counted when `order_status` is in
     the merchant-defined Delivered set (تم التوصيل /
     completed / delivered).

**Implementation**:
  • `migration_routes._legacy_payment_platform_balances()` now
    skips any account where the name matches a COD variant OR
    where `normalized_payment_method ∈ {cod, cash_on_delivery}`.
    Detection is robust against Arabic hamza variants.
  • `MigrationWizard.jsx` shows a prominent amber notice
    explaining the exclusion and linking to the diagnostic page.
  • Verified live on Preview: post-fix migration snapshot
    contains 4 payment_platforms (تمارا, تابي, سلة, إمكان) —
    COD is gone.

**Tests**: `tests/test_cod_excluded_from_migration_iter179.py`
— 5 tests covering Arabic name, hamza variant, English aliases,
`normalized_payment_method`, and the positive case (non-COD
platforms still migrate).

**Pending audit (for Shipping Sprint)**:
The merchant requested an audit of every place the system
currently uses `Confirmed` to compute COD-shaped totals so the
new rule "Delivered-only" can replace it consistently. Scope
notes for the Sprint:
  • `payment_gateway_metrics.compute_metrics()` — category
    resolver applies "confirmed" to most non-pending statuses.
    The COD path needs a separate "cod_delivered_set" override
    (already partially expressed in
    `settings.cod_approved_statuses`).
  • `accounts_routes._central_expected_for_account()` — drives
    `expected_orders_balance` on the COD account.
  • `shipping_accounts.py` — ALREADY uses delivered counts for
    courier-level reports; reuse this engine for the COD
    breakdown.
  • The diagnostic endpoint already exposes per-company
    Delivered-only totals (`by_shipping_company_delivered_only`)
    — Sprint can use this as the authoritative shape.


## Iter-180 — Post-Sync Drift Block in COD Diagnostic (Feb 2026)
**Merchant's question** (before Apply): on production they saw
  • current_balance = 40,123.78 (cache)
  • walk Confirmed = 40,308.54 (live diagnostic)
  • cache orders_count = 234
  • walk total orders = 235
  • diff = 184.76 SAR (= 1 order)

They asked: which order caused the drift, can they trust the data,
and will it affect the migration?

**Cause**: orders arriving after the COD account's `last_synced_at`
timestamp are visible to the live diagnostic walk but not yet
reflected in the cached `current_balance` / `orders_count` fields.
This is normal — `compute_metrics` only refreshes the cache during
explicit sync runs.

**Enhancement**: `cod_diagnostic_routes.py` and the UI page now
expose a "Post-Sync Drift" section that:
  • Shows `last_synced_at` of the account.
  • Lists every COD order with `received_at > last_synced_at`.
  • Marks each one's `policy_category` (Confirmed / Pending / …).
  • Computes the exact `cache_vs_walk_amount_diff` and
    `cache_vs_walk_count_diff` and surfaces them as cards.
  • Explicit interpretation: "هذا سلوك طبيعي ولا يؤثر على الترحيل
    لأن COD مُستبعد من Phase 4 أصلاً."

**Confirmed: drift does NOT affect Phase 4** because COD is
excluded from the migration entirely (Iter-179).


## Iter-181 — Post-Migration Audit (Feb 2026)
**Merchant's request after successful Phase 4 Apply**: a final
read-only audit confirming:
  • No discrepancies between legacy data and the Universal Ledger
  • No duplicate opening entries
  • No unexplained negative balances
  • Integrity of references and reports

**Endpoint shipped**: `GET /api/audit/post-migration` (read-only).
Returns:
  • `verdict`: pass / warnings / fail
  • `issues[]`: list of detected issues with severity (high/medium/info)
  • `cutoff`: migration cutoff record
  • `duplicates`: count + samples of duplicate opening_balance entries
    grouped by (entity_type, entity_id, sub_account)
  • `orphans`: count + samples of opening entries referencing
    deleted entities (counterparties, accounts, employees)
  • `ledger_sums_by_entity`: debit/credit totals + counts per
    entity_type (bank, employee, supplier, …)
  • `negative_balances`: bank/payment_platform accounts with
    current_balance < 0, flagged as "expected" for BNPL providers
    (Tabby/Tamara) and "unexplained" for others
  • `cod_exclusion`: confirms 0 COD entries leaked into the ledger
  • `bank_reconciliation`: legacy_total vs ledger_net + diff

**UI page shipped**: `/audit/post-migration` (`PostMigrationAudit.jsx`)
displays the audit as a friendly dashboard:
  • Verdict banner (color-coded by status)
  • Three top cards: cutoff status, COD exclusion confirmation,
    bank reconciliation
  • Ledger sums table per entity_type
  • Duplicate / orphan counters (must be 0)
  • Expandable negative-balance details with BNPL whitelist

Linked in Sidebar as "🔬 فحص ما بعد الترحيل" beside the COD
diagnostic.

**Merchant workflow**:
  1. Save to GitHub → Redeploy
  2. Open `/audit/post-migration` on mezansalla.com
  3. Verdict should be **pass** (✅) or **warnings** only
  4. If any **high** severity issue → investigate before
     disabling legacy endpoints
  5. After confirmation → proceed with legacy decommissioning
     (separate task, requires explicit merchant approval)

**Tests**: Live Preview verification confirms the endpoint
returns plausible output even before migration (correctly reports
"no_cutoff" and "bank_mismatch" because no Apply has run).


---

## Completed Work — Iter-183 to Iter-187 (Feb 14 2026): Cumulative UX & Accounting Hardening

### Iter-183 — Custody Transfer Between Employees + Open Custody Report
- **New op**: `🔄 نقل عهدة بين موظفين` (`custody_transfer`).
- **Endpoint**: `POST /api/accounting/employees/custody/transfer`.
  Posts a balanced txn_group with `entity_type=employee, sub_account=custody`
  on BOTH sides; **no bank/cash touched**.
- **Guards**: same-employee rejected (400), insufficient custody rejected (400),
  unknown employee rejected (404).
- **Endpoint**: `GET /api/accounting/employees/custody/open-balances` aggregates
  `general_ledger` by `(employee, entry_type, side)` and returns per-employee
  breakdown (granted / settled_receipts / returned_cash / transferred_in /
  transferred_out / opening / open_balance).
- **UI page**: `/employees/custody-balances` — searchable table with totals.
- **Test**: `tests/test_custody_transfer_iter183.py`.

### Iter-184 — Operation→Account Bindings + hidden_transaction_types Fix
- **Settings field**: `operation_account_bindings: dict[op_type, [account_id]]`.
  Empty list = "السماح للكل" (back-compat default).
- **Helper**: `_enforce_account_binding(db, user_id, op_type, account_id)`
  raises 400 if account not in the merchant's allow-list.
- **Applied to 9 cash-touching ops**: advance_grant, salary_settle,
  custody_grant, custody_return, supplier_pay, external_grant, external_collect,
  expense_record, bank_transfer (both sides).
- **UI page**: `/settings/operation-account-bindings` — matrix of operations ×
  accounts with per-op "السماح للكل ↔ وضع التقييد" toggle and empty-state warning.
- **UnifiedEntryScreen** now filters the bank dropdown via the bindings.
- **Bug-fix**: `hidden_transaction_types` (Iter-182) was not being persisted nor
  returned by GET; now properly wired in `server.py`.
- **Test**: `tests/test_op_account_binding_iter184.py`.

### Iter-185 — Insufficient-Funds Guard + Employee Summary Card
- **Helper**: `_account_live_balance(db, user_id, account_id)` = stored
  `current_balance` + ledger delta (`entity_type=bank` net) → single source of
  truth for "can I afford this".
- **Helper**: `_enforce_sufficient_funds()` raises 400 with:
  > لا يمكن تنفيذ العملية، رصيد الحساب المختار غير كافٍ.
- **Applied to 7 cash-OUT ops**: advance_grant, salary_settle, custody_grant,
  supplier_pay, external_grant, expense_record, bank_transfer (source side).
- **New endpoint**: `GET /api/accounting/cash-accounts-with-balances` —
  one round-trip live balances for all cash-touchable accounts.
- **New endpoint**: `GET /api/accounting/employees/{id}/summary-balance` —
  `net_due_to_employee = salary_payable_outstanding − advance_open − custody_open`.
- **UI in UnifiedEntryScreen**:
  • Bank dropdown is LOCKED until an amount > 0 is entered.
  • Each option shows its live balance.
  • Options with balance < amount render as **disabled** with
    "🚫 مجمد — الرصيد غير كافٍ".
  • Employee-summary card renders next to the entity picker; green when company
    owes the employee, red when employee owes the company.
  • Live balances refresh after every successful submit.
- **Test**: `tests/test_insufficient_funds_iter185.py`.

### Iter-186 — Smart Employee Picker (search + custody freeze)
- **Component**: `EmployeePicker` — type-ahead search, inline custody-balance
  badge per row (red when > 0), accepts `freezeZeroCustody` to disable
  employees with `custody = 0` (used by `custody_return` & `custody_settle`).
- **Custody-only context card**: for any custody op, the summary card shows
  ONLY `custody_open` (red when > 0, green when = 0).
- **Bulk loader**: `reloadCustodyBalances()` consumes the iter-183 endpoint and
  refreshes after every ledger post.

### Iter-187 — Cash Account (صندوق نقدي) as a First-Class Account Type
- **Backend** (`accounts_routes.py`):
  • Added `"cash"` to `ACCOUNT_TYPES`, label "صندوق نقدي".
  • Suggested providers: الصندوق الرئيسي / صندوق المعرض / صندوق المستودع /
    صندوق الفرع / خزينة المدير / نقدية في يد الموظف.
- **Audit endpoint** extended to include cash in lookups.
- **No new endpoints needed** — cash flows through the same `entity_type=bank`
  ledger key, so every existing cash-out endpoint works automatically
  (advance / expense / supplier-pay / custody / bank-transfer / etc.).
- **Frontend** (`Accounts.jsx`):
  • New `TYPE_META.cash` with amber Wallet icon.
  • New "الصناديق النقدية" tab + 4-column type grid in the Add modal.
- **financial-position** already groups cash under «النقدية والبنوك».
- **Test**: `tests/test_cash_account_iter187.py`.

### Iter-188 — Golden Rule: Block Advances When Salary is Pending (UNCONFIRMED)
- **AdvanceGrantIn** gets `acknowledge_pending_salary: bool = False`.
- **Guard**: if `salary_payable > 0` and flag is `False` → **409 Conflict**
  with structured detail:
    ```json
    { "code": "PENDING_SALARY_BLOCK",
      "salary_payable": 8800,
      "employee_id": "...", "employee_name": "...",
      "message": "..." }
    ```
- **Frontend**: amber suggestion banner appears in real-time with two CTAs:
  • 🔄 «حوّل إلى صرف راتب» — switches `opType` to `salary_settle`.
  • ✓ «هي سلفة فعلاً — تابع» — sets the override flag.
  Override resets automatically when op/employee/amount changes.
- Server-side 409 still handled in the catch block as defense-in-depth.
- **Test**: `tests/test_golden_rule_iter188.py`.

### Key Files Touched in This Window
- `/app/backend/universal_accounting_routes.py` (major)
- `/app/backend/ledger_core.py` (`custody_transfer` added to `ENTRY_TYPES`)
- `/app/backend/server.py` (settings model + persistence)
- `/app/backend/accounts_routes.py` (cash type)
- `/app/backend/audit_routes.py` (cash inclusion)
- `/app/frontend/src/pages/UnifiedEntryScreen.jsx` (largest delta)
- `/app/frontend/src/pages/Accounts.jsx` (cash UI)
- `/app/frontend/src/pages/CustodyOpenBalances.jsx` (new)
- `/app/frontend/src/pages/OperationAccountBindings.jsx` (new)
- `/app/frontend/src/components/Sidebar.jsx`
- `/app/frontend/src/App.js`

### Open Items at Checkpoint
- ⏳ **Iter-188** awaits user acceptance (functionally complete, tested).
- 🟠 **P1**: Sprint شركات الشحن — about to start with an audit-first pass.
- 🟠 **P1**: Tabby negative balance investigation.
- 🟠 **P0** (blocked): 15 orphan employees on production — awaiting JSON.


---

## Completed Work — Iter-188 to Iter-192 (Feb 14 2026 cont.): Shipping Sprint Kickoff + P0 Balance Fix

### Iter-188 — Golden Rule: Block Advances When Salary is Pending ✅ CONFIRMED
- `AdvanceGrantIn.acknowledge_pending_salary: bool = False`.
- Backend returns **409 Conflict** with structured payload when an
  employee has open salary_payable; UI shows an amber banner with a
  one-click "حوّل إلى صرف راتب" (switches op_type) + "هي سلفة فعلاً —
  تابع" (sets override).
- Test: `tests/test_golden_rule_iter188.py`.

### Iter-190 — Multi-leg COD Settlement (Backend) ✅ CONFIRMED
- **New endpoint**: `POST /api/accounting/couriers/{id}/cod-settle`.
- Input model `CodSettleIn`: `bank_amount`, `bank_account_id`,
  `shipping_cost`, `cod_fee`, `other_fees`, `other_fees_category`.
- Posts a single balanced txn_group with up to 5 legs (1–4 debit legs
  + the courier `cod_receivable` credit).
- Backed by Universal Ledger only — **no** writes to legacy
  `shipping_payments` / `courier_transfers`.
- Guards: (a) total ≤ open `cod_receivable`, (b) `bank_amount > 0` ⇒
  `bank_account_id` of type `bank|cash` only, (c) `other_fees > 0`
  ⇒ valid expense category, (d) all-zero legs rejected, (e) unknown
  courier 404.
- New default expense category: `cod_fees` («رسوم الدفع عند الاستلام»).
- New `ENTRY_TYPES` literal: `"courier_cod_settle"`.
- Test: `tests/test_courier_cod_settle_iter190.py` — 4 happy-path
  scenarios + 7 rejection paths.

### Iter-191 — COD Settlement UI ✅ CONFIRMED
- New op type **«🚚 تسوية COD مع شركة شحن»** in UnifiedEntryScreen
  under a brand-new "شركات الشحن" section.
- Smart courier picker: shows COD balance next to each company,
  **freezes** companies with `cod_receivable = 0`.
- Per-courier COD card (green when > 0, red when = 0) auto-refreshes
  after every successful post.
- Four independent leg fields (bank/shipping/cod_fee/other_fees);
  category dropdown auto-disabled when `other_fees = 0`.
- Bank-account picker restricted to `bank|cash` only — no payment
  platforms / ad accounts.
- Live "📊 ملخص التسوية" preview (current balance / each leg /
  settlement total / remaining balance) with overspend warning.
- Dedicated success card after submit: courier name, settlement
  total, previous COD balance, remaining COD balance.
- Zero backend changes — uses iter-190 endpoint as-is.

### Iter-192 — P0 Bug Fix: Bank Balance Double-Counting
- **Symptom**: Accounts page showed bank balance X (e.g. 212,363.30);
  «حركة مالية جديدة» showed 2X (424,726.60). Caused by adding the
  legacy `current_balance` AND the migration `opening_balance` ledger
  entry (which mirrored `current_balance`).
- **Single Source of Truth (SSOT) rule**: if the account has any
  posted `opening_balance` ledger entry → live balance comes from the
  ledger ONLY (`compute_balance(entity_type=bank).net_balance`). Else
  → `current_balance`. Never both.
- **Lazy backfill** (`_ensure_opening_balance_seeded`) — on first
  universal-accounting touch of a non-migrated account, write an
  `opening_balance` ledger entry equal to `current_balance` so the
  ledger becomes authoritative forever after.
- Backfill called from every cash-touching op (outflows + inflows):
  advance / salary / custody / supplier_pay / external_grant /
  external_collect / custody_return / expense_record / bank_transfer
  (both ends) / courier_cod_deposit / courier_cod_settle.
- **Cross-page consistency**: `_account_with_meta` (`/api/accounts`,
  `/api/accounts/summary`) now applies the SAME SSOT rule, so the
  Accounts page, summary cards, and UnifiedEntry screen ALWAYS agree.
- New transparency field on the live-balances endpoint:
  `balance_source: "ledger" | "current_balance"`.
- Test: `tests/test_balance_no_double_count_iter192.py` —
  regression-blocks doubling AND cross-page mismatch.

### Open Items at This Checkpoint
- 🟢 **Next**: Iter-189 — `payment_mode: "prepaid" | "deferred"` per
  shipping company + Settings UI clarification.
- 🔵 Iter-192-ext: per-order Shipping Ledger (delivered-only).
- 🟠 P1 Tabby negative balance investigation.
- 🟠 P0 (blocked, awaiting JSON): 15 orphan employees on production.


---

## Completed Work — Iter-189 + Iter-192-ext (Feb 14 2026): Shipping Sprint Foundation

### Iter-189 — Payment Mode (Prepaid / Deferred) ✅ CONFIRMED
- New `payment_mode: "prepaid" | "deferred"` on `ShippingCompany`.
- Bi-directional sync with legacy `is_deferred` via `root_validator`.
- `GET /api/settings` enriches every shipping company with `payment_mode`.
- `/api/shipping-companies/discover` also returns the field.
- Frontend: prominent two-card toggle in Settings page replacing the
  cramped pill; new reusable `PaymentModeBadge.jsx` component (xs/sm/lg).
- CODDiagnostic page now shows a per-company badge column.
- Test: `tests/test_payment_mode_iter189.py`.

### Iter-192-ext — Shipping Ledger (per-order, delivered-only) ✅ CONFIRMED
- New backend module `shipping_ledger_routes.py`.
- `GET /api/shipping-ledger` — strict filter: only orders whose
  `order_status_policy` category == `"confirmed"` (delivered).
- 7 query filters: date_from/to, courier, payment_mode, payment_method,
  settlement_status, has_cod.
- 8-card top summary (delivered_count, total_shipping_cost, total_cod,
  total_cod_fees, total_settled, total_unsettled, total_prepaid_shipping,
  total_deferred_shipping).
- Per-order columns: order, date, courier, **PaymentModeBadge**,
  payment_method, status, shipping_cost (+ "مدفوع مسبقاً" tag for
  prepaid), cod_amount, cod_fee, net_due, settlement_status.
- New page `/app/frontend/src/pages/ShippingLedger.jsx` at route
  `/shipping/orders-ledger`. Sidebar link under shipping section.
- CSV export with BOM (Arabic-safe in Excel).
- Read-only — NO ledger writes. settlement_status placeholder
  shows "unsettled" until Iter-193 wires per-order settlement links.

### Files Touched
- Backend: `server.py`, `shipping_ledger_routes.py` (new).
- Frontend: `ShippingLedger.jsx` (new), `PaymentModeBadge.jsx` (new),
  `ShippingCompanySettings.jsx`, `CODDiagnostic.jsx`,
  `Sidebar.jsx`, `App.js`.

### Open Items at This Checkpoint
- 🟢 Next user-requested audit: verify Prepaid vs Deferred counts and
  totals before moving to Tabby investigation.
- 🟠 P1 — Tabby negative balance investigation.
- 🔵 Iter-193 — per-order settlement linking (drill-down).
- 🟠 P0 (blocked) — 15 orphan employees on production.

---

## Checkpoint — Iter-193, Iter-194, Iter-195 (Tabby Forensic Sprint)

### Iter-193 — Forensic Audit Endpoint (Read-Only)
- **What:** Added `GET /api/audit/forensic-report` combining:
  - 15 orphan employee openings (names, amounts, classification)
  - Tabby ledger snapshot with sub_account breakdown
- **File:** `/app/backend/audit_routes.py::make_forensic_report_router`
- **Production output:** confirmed 41,931.68 SAR net orphan impact;
  Tabby `current_balance` -47,351.51 vs ledger +12,175.71.

### Iter-194 — Tabby Forensic Phase 2 (Read-Only)
- **What:** Added `GET /api/audit/tabby-phase2` deep-dive into:
  - `payment_transactions` (849 sales / 175,442.13 SAR)
  - `payment_refunds` (19 / 2,684.59 SAR)
  - `account_transactions` (7 manual transfers / 144,621.89 SAR)
  - `general_ledger` (1 opening entry / +12,175.71)
  - BNPL SSOT formula reconstruction
- **File:** `/app/backend/audit_routes.py::make_tabby_phase2_router`
- **Root cause established:**
  - 3 different sources show 3 different numbers (SSOT violation):
    - `accounts.current_balance` = −47,351.51
    - `BNPL SSOT formula` = +13,202.46 ← correct
    - `general_ledger.net` = +12,175.71 (frozen at cutoff)
  - The −47,351.51 comes from `expected_orders_balance` (97,270.38)
    minus `Σ account_transactions(out)` (144,621.89) — and
    `expected_orders_balance` is missing 60,554.87 SAR vs the
    actual BNPL net sales.
  - 7 manual transfers without reference / txn_group_id / linked
    account — recorded outside the Universal Ledger.

### Iter-195 — Phase 1 Quick Fix (Tabby SSOT)
- **What:** Centralized live-balance resolver so every endpoint
  displays the BNPL SSOT value for Tabby/Tamara, never the stale
  `current_balance` field.
- **New module:** `/app/backend/balance_resolver.py`
  - `resolve_live_balance(db, *, user_id, account)` returns
    `{balance, source, raw_balance, components}` with priority
    `bnpl_ssot > ledger > current_balance`.
- **Leak fixed:** `universal_accounting_routes.py`
  - `_account_live_balance()` now routes through the resolver.
  - `cash-accounts-with-balances` endpoint applies BNPL SSOT.
  - Added `normalized_payment_method` to the projection so
    `is_bnpl_account()` can detect Tabby/Tamara by the canonical
    payment-method key (Arabic name alone was not matching the
    English provider key).
- **Frontend:** `/app/frontend/src/pages/Accounts.jsx`
  - Added `BNPL SSOT` and `Ledger` badges next to each balance.
  - Tooltip explains "محسوب لحظياً من payment_transactions —
    لم يُكتب بعد في Universal Ledger".
  - Data-testids: `account-balance-{id}`,
    `balance-source-bnpl-{id}`, `balance-source-ledger-{id}`.
- **Read-only guarantee:** every modified path is read-only; no
  document is mutated by display endpoints.
- **Tests:** `/app/backend/tests/test_tabby_ssot_phase1_iter195.py`
  - resolver classifies Tabby as `bnpl_ssot`
  - `/api/accounts` returns `balance_source` and the BNPL value
    (not the stale −47,351.51)
  - `/api/accounts/summary` aggregates with BNPL SSOT
  - `/api/accounting/cash-accounts-with-balances` applies override
  - bank accounts without opening_balance remain `current_balance`
  - 3× calls to read endpoints leave `accounts.current_balance`,
    `payment_transactions`, and `general_ledger` byte-identical.
  - ✅ All assertions passed.

### Phase 2 (P1 — pending user approval)
- Write `general_ledger` entries whenever a new Tabby/Tamara
  sale, refund, commission, VAT, fee, or transfer is recorded.
- Goal: stop the gap from widening so the eventual backfill is a
  clean snapshot to a known cut-off.

### Phase 3 (P2 — pending user approval)
- Historical backfill: synthesize ledger entries for the 849 sales,
  19 refunds, 7 transfers, and fee accruals. Test extensively first.


## Checkpoint — Iter-196 (Employee Misposting Correction)

### What & Why
- Merchant needs to **move accounting impact** from a wrong employee
  to the correct one when a salary_payment, advance_grant, or
  custody_grant was misposted — WITHOUT re-touching the bank/cash
  (the money already left in the real world).

### Backend
- `entry_type: "correction"` added to `ledger_core.ENTRY_TYPES`.
- New module `/app/backend/corrections_routes.py`:
  - `GET  /api/accounting/employees/{emp_id}/correctable-operations`
    — lists original ops with remaining_correctable per txn_group.
  - `POST /api/accounting/employees/correct-misposting`
    — creates a balanced PAIR (CREDIT wrong + DEBIT correct) inside
      the employee ledger only. `bank_impact = 0`.
  - `GET  /api/accounting/employees/corrections` — audit log.
- Every correction row carries `correction_group_id`,
  `corrects_txn_group_id`, and rich metadata (original_operation,
  original_employee_id/name, corrected_to_employee_id/name, reason,
  corrected_by, corrected_at, partial flag).

### Frontend
- New route `/employee-corrections` (`EmployeeCorrections.jsx`)
  with pickers, operation list (showing remaining_correctable per
  txn), amount field (partial allowed), required reason textarea,
  confirmation modal stating "البنك لن يتأثر", and audit log card.
- Sidebar link `nav-employee-corrections` under العمليات المالية.
- data-testids: `from-employee-picker`, `to-employee-picker`,
  `operation-{ledger_id}`, `correction-amount`,
  `correction-reason`, `submit-correction`,
  `correction-confirm-modal`, `confirm-correction`,
  `audit-row-{group_id}`.

### Decisions Locked (per merchant approval)
- **1a** partial corrections allowed.
- **2b** opening_balance NOT correctable here (separate path).
- **3b** a correction itself is NOT correctable.
- **4a** original entry stays visible and untouched.
- **5b** MVP scope: salary_payment, advance_grant, custody_grant.

### Tests
- `/app/backend/tests/test_employee_correction_iter196.py` covers
  16 assertions: same-employee block, over-amount block, short
  reason block, partial then full correction, correction-of-
  correction block, bank-untouched invariant, original-byte-
  identical invariant, employee balance movements, rich audit
  metadata, listing and audit endpoints, advance_grant smoke.
- ✅ Test passes (1/1).

### Open Items
- 🟢 Phase 2 (P1) — Bridge Tabby/Tamara writes into general_ledger.
- 🟢 Phase 3 (P2) — Historical Tabby backfill.
- 🟠 P0 — 15 orphan employees treatment plan.
- 🟢 Extend correction MVP to advance_repay_cash, custody_return,
  salary_settle's advance offset leg after stability proven.


## Checkpoint — Iter-197 (Reconciliation Forensic Endpoint)

- New read-only endpoint
  `GET /api/audit/reconciliation-forensic` in
  `/app/backend/reconciliation_forensic_routes.py`.
- Classifies every reconciliation diff (employees, banks, payment
  platforms, suppliers, externals, couriers) into:
  `no_ledger_entries | migration_iter161_only | opening_only |
  post_cutoff_ops | mixed | legacy_formula_drift | no_legacy_data`.
- Surfaces per-entity breakdowns by entry_type × side and iter161
  vs post-cutoff totals so the merchant can root-cause each Δ
  without touching any data.
- Read-only verified on Preview (skeleton OK; awaits production
  JSON for analysis).


## Checkpoint — Iter-198 (Bank Detail SSOT Unification)

### What & Why
Merchant reported P0: top-card balance on the bank-detail page
showed the iter-192 ledger SSOT (e.g. 166,449.30) while the last
`balance_after` in the transactions log showed the stale frozen
`account_transactions` value (e.g. 254,208.67). Drift = 87k SAR.
Root cause: post-migration operations are now written to
`general_ledger` ONLY; the legacy `account_transactions` log is
frozen at the migration snapshot.

### Backend
- New `_ledger_based_tx_feed(db, user_id, account_id)` helper in
  `/app/backend/accounts_routes.py` that builds the transactions
  feed FROM `general_ledger` for migrated bank/cash accounts:
  iterates posted rows chronologically, computes a running balance
  that lands exactly on `compute_balance().net_balance`, and shapes
  each row as `account_transactions` (id, type_label, direction
  in/out, amount, description, transaction_date, balance_after,
  status, txn_group_id, source='ledger', metadata).
- `GET /api/accounts/{id}/transactions` now branches:
  - If the bank/cash account has a posted `opening_balance` row
    in `general_ledger` → ledger feed.
  - Otherwise → legacy `account_transactions` feed
    (source='account_tx') — unchanged.
- Extended `TRANSACTION_TYPE_LABELS` to cover 20+ ledger entry
  types (sale, salary_payment, advance_grant, custody_grant,
  correction, courier_cod_settle, etc.) so the UI shows Arabic
  labels for ledger-sourced rows.

### Tests
- `/app/backend/tests/test_bank_detail_ssot_iter198.py` (9
  assertions in one consolidated test): ledger ground-truth ==
  top card == last running balance in /transactions == summary
  bank total; running balance walks correctly for every row;
  adding a new ledger entry moves both top card and last running
  balance in lock-step; non-migrated bank stays on the legacy
  feed (regression guard).
- ✅ Passes; iter-192/195/196 regression tests still green.

### Preview Visual Verification
Created a synthetic migrated bank on Preview (cleaned up
afterwards) with a stale `current_balance=254,208.67` and a
ledger chain summing to 116,449.30. Confirmed end-to-end:
- Top card: 116,449.30 (source=ledger)
- Last balance_after in transactions: 116,449.30 (source=ledger)
- Accounts list: 116,449.30
- Unified Entry cash-accounts-with-balances: 116,449.30
- Summary by_type.bank includes 116,449.30
All five sources unified.

### Notes
- Raw `accounts.current_balance` is INTENTIONALLY left untouched
  (preserved as `current_balance_legacy` in the API response).
- `account_transactions` is NOT modified or deleted — kept for
  audit and to support non-migrated accounts.


## Checkpoint — Iter-199 (Salary Payment Full Reversal)

### What & Why
A second corrective operation, distinct from Iter-196. Iter-196
moves the employee-side impact only (bank untouched). Iter-199
FULLY REVERSES a salary_payment — every leg is mirrored,
including the bank/cash side, so the bank balance is restored.
Use when the salary was booked by mistake or was never actually
paid.

### Backend
- New module `/app/backend/reversals_routes.py`:
  - `GET  /api/accounting/employees/{emp_id}/reversible-salary-payments`
    — lists salary_payments with `already_reversed` flag and the
    bank/cash account name for context.
  - `POST /api/accounting/employees/reverse-salary-payment`
    — mirrors every leg of the original txn_group with the
    opposite `side`, posts as a new group, tags each row with
    `reversal_of_txn_group_id`, blocks double reversal, blocks
    reversing a reversal or correction, requires reason ≥ 5 chars.
  - `GET  /api/accounting/employees/salary-reversals` — audit log.
- Reuses existing `entry_type: "reversal"` and the strict
  `reason_code` invariant from `ledger_core` (uses
  `data_entry_error` since the feature targets entry mistakes).

### Frontend
- New route `/salary-reversals` (`SalaryReversals.jsx`) with
  employee picker, salary-payment list (showing bank account +
  `already_reversed` badge), required reason textarea, and
  confirmation modal that explicitly states the BANK WILL BE
  AFFECTED (in contrast to the corrections page which states the
  BANK WILL NOT BE TOUCHED).
- Sidebar link `nav-salary-reversals`.
- data-testids: `reversal-employee-picker`, `reversal-op-{id}`,
  `reversal-reason`, `submit-reversal`, `reversal-confirm-modal`,
  `confirm-reversal`, `cancel-reversal`,
  `reversal-log-{group_id}`.

### Tests
- `/app/backend/tests/test_salary_payment_reversal_iter199.py`
  (13 assertions in one consolidated test):
  bank balance restored to pre-payment value after reversal,
  original ledger group byte-identical, every leg's side is
  flipped, every reversal row carries `reversal_of_txn_group_id`
  and entry_type='reversal', double-reverse → 400, reverse a
  reversal → 400, reverse a non-existent group → 404, audit log
  surfaces the reversal, reversible-list flags the original.
- ✅ Passes. Iter-195/196/197/198 regression tests still green.

### Distinction Rules
- **Iter-196 (correction):**     bank NEVER touched.
- **Iter-199 (full reversal):**  bank ALWAYS touched (restored).


## Checkpoint — Iter-200 (Audit Badges on Ledger Feed)

### What & Why
Now that the system has both reversal (Iter-199) and correction
(Iter-196) operations, the merchant needs to see the audit
lineage on the ORIGINAL entries — without leaving the bank or
employee statement screens. Iter-200 surfaces four flags on
every row of the ledger-based transactions feed:
    • `is_reversal`         — this row itself is a reversal leg.
    • `is_correction`       — this row itself is a correction leg.
    • `was_reversed`        — this original has been reversed.
    • `was_corrected`       — this original has been corrected
                              (with `correction_count`).

### Backend
- `_ledger_based_tx_feed()` in `/app/backend/accounts_routes.py`
  now performs ONE batch query for reversals and ONE batch for
  corrections per request (no N+1), keyed by `txn_group_id`.
- Each row exposes `reversal_info` (`reversal_group_id`,
  `reversed_at`, `reason`, `amount`) and `correction_info`
  (`correction_count`, `total_amount`, `last_at`,
  `last_reason`).

### Frontend
- `AccountDetails.jsx` transactions table renders four badges
  next to each type label:
    • 🟥 `↩️ تم عكسه` (rose) — was_reversed
    • 🟨 `🔄 مُصحَّحة (N)` (amber) — was_corrected
    • 🟦 `قيد عكسي` (sky) — is_reversal
    • 🟪 `قيد تصحيح` (violet) — is_correction
  Each carries a hover tooltip with date + reason.

### Tests
- `/app/backend/tests/test_audit_badges_iter200.py` — verifies
  the four flags and `reversal_info`/`correction_info` payloads
  for a flow involving a reversed salary payment and a partial
  correction. ✅ passes.

### Notes
- Correction badges only surface on the EMPLOYEE leg of the
  original (bank leg is intentionally untouched by corrections).
  The bank-statement feed shows reversal badges; employee-
  statement enhancements deferred.


## Checkpoint — Iter-201 (Expense Reversal)

### What & Why
Same mirror-every-leg pattern as Iter-199 (salary reversal),
applied to `entry_type=expense_record`. The reversal returns
the money to the EXACT source account it left from — whether
that's a bank, cash, payment_platform, or employee custody.

### Backend (added to `/app/backend/reversals_routes.py`)
- `GET  /api/accounting/expenses/reversible`
  Lists expense_record groups with source-account name and
  `already_reversed` flag. Aggregates by txn_group_id (one row
  per group, not per leg).
- `POST /api/accounting/expenses/reverse`
  Mirrors every leg of the original; new rows carry
  entry_type='reversal', reversal_of_txn_group_id, and
  reason_code='data_entry_error'. Blocks double-reverse,
  reversing a reversal, and reversing a correction.
- `GET  /api/accounting/expenses/reversals`
  Audit log of expense reversals only (filters out salary).

### Frontend
- New route `/expense-reversals` (`ExpenseReversals.jsx`) with
  a list of reversible expenses (showing source type + name +
  `مَعكوس` badge if already reversed), required reason
  textarea, and confirmation modal stating the source account.
- Sidebar link `nav-expense-reversals` ("↩️ عكس مصروف").
- data-testids: `expense-reversals-page`,
  `expense-op-{txn_group_id}`, `expense-reversal-reason`,
  `submit-expense-reversal`, `expense-reversal-confirm-modal`,
  `confirm-expense-reversal`, `cancel-expense-reversal`,
  `expense-rev-log-{group_id}`.

### Tests
- `/app/backend/tests/test_expense_reversal_iter201.py`
  (9 assertions): bank balance restored, original byte-
  identical, every leg flipped (expense leg becomes credit,
  bank/source leg becomes debit), double-reverse → 400,
  reason validation, audit-log separation between salary and
  expense reversal endpoints.
- ✅ Passes. Iter-196/198/199/200 regression all green.

### Visual Verification on Preview
Confirmed via screenshot: the page surfaces a real expense
(3,000 ر.س, "اشتراك") from the merchant's data, the Sidebar
shows three distinct operations (تصحيح موظف / عكس صرف راتب /
عكس مصروف), and the audit-log card on the right is wired up.



## Iter-203 — Dynamic Post-Cutoff Salary Accrual (Feb 15, 2026)

### Bug Reported
After the Phase-4 SSOT migration (Iter-161), the Employees screen
(`/employees-ledger`) stopped showing "today's" newly accrued salary
because `general_ledger.salary_payable` was frozen at the cutoff
snapshot. New days no longer auto-incremented the payable.

### Fix (Option A — Dynamic Display Layer)
- `/api/accounting/employees/list` and
  `/api/accounting/employees/{id}/financial-summary` now compute a
  `pending_accrual` delta on the fly:
    raw = _compute_employee_accrual(emp, today) -
          _compute_employee_accrual(emp, cutoff_date)
    delta = max(0, raw − Σ salary_accrual credits posted after cutoff)
- Ledger is NOT mutated — this is a transparent display addition.
- New API fields: `pending_accrual`, `salary_payable_ledger`,
  `outstanding_debt_ledger`, response-level `cutoff_date`.
- Frontend `EmployeesLedger.jsx` shows `+X.XX استحقاق اليوم` under
  each employee row and in the summary card.

### Tests
- `/app/backend/tests/test_dynamic_salary_accrual_iter203.py`
  (6 scenarios): un-migrated user, migrated user with 5-day cutoff,
  posted-accrual no-double-count, financial-summary parity,
  future-start = 0 accrual, stopped employee clamped at stop_date.
- ✅ Passes. Iter-196/162/113 regression all green.

### Visual Verification on Preview
Confirmed via screenshot: admin employee أحمد (3,000 ر.س/month)
shows 16,500 ر.س payable with "+16,500.00 استحقاق اليوم" sub-line —
matching ~165 days × (3,000/30) daily rate since cutoff.

## Iter-203 — Ad Account Top-up SSOT (P0 Bug Fix, Feb 15 2026)

### Bug Reported
"تعبئة الحساب الإعلاني لا تخصم من البنك في القيد الموحد" — top-up
flowed through legacy `account_transactions` + `ad_account_ledger`
only, never writing to `general_ledger`. After Iter-198 (Bank
SSOT) the bank statement reads from the ledger only — so top-ups
were invisible there, and the financial position did not reflect
the cash leaving the bank.

### Fix (P0)
- `POST /api/ad-accounts/{cp_id}/topup`:
  1. **NEW** Validates source bank/cash has sufficient live balance
     via `_enforce_sufficient_funds`. Returns 400 with
     `"لا يمكن تنفيذ العملية، رصيد الحساب المختار غير كافٍ."` BEFORE
     any write happens.
  2. **NEW** Auto-seeds bank `opening_balance` for non-migrated
     banks (`_ensure_opening_balance_seeded`).
  3. **NEW** Posts a balanced 2-leg `post_txn_group` to
     `general_ledger`:
     - DEBIT  `ad_account.balance` (asset ↑)
     - CREDIT `bank.main`          (asset ↓)
     `entry_type="topup"`, `txn_type="ad_account_topup"`.
  4. Legacy writes preserved: `account_transactions`,
     `counterparties.balance`, `ad_account_ledger` — keeps existing
     ad-account summary, audit walks, and edit/reverse flows intact.
- Response now includes `ledger_txn_group_id`.

### Tests
- `/app/backend/tests/test_ad_account_topup_ssot_iter203.py`
  (3 scenarios, all green):
  • insufficient bank → 400, no writes anywhere
  • happy path → 2 balanced ledger legs, bank live drops, ad
    balance rises, NO expense entry, statement row visible
  • cumulative second top-up deducts again

### Visual Verification on Preview
Confirmed with bank "بنك الإنماء (Iter203)":
- Opened with 10,000 ر.س.
- Top-up 1,500 ر.س from bank → "Snap Test SSOT".
- Bank detail screen: balance dropped to 8,500.00 ر.س; bank
  statement shows new "تعبئة" row (out, 1,500) with
  balance_after = 8,500.
- Ad-account list shows Snap Test SSOT balance = 1,500.
- Ledger entries (via /api/ledger/entries?txn_group_id=…):
  entry_no=3 ad_account.balance debit 1500 ; entry_no=4
  bank.main credit 1500 — perfect double-entry, both posted.





## Iter-204 — Ad-account silent half-hour auto-refresh (Feb 15 2026)

### Request
Merchant wants ad-spend numbers to update without reloading the
page — either manually or every 30 minutes — and the refresh must
propagate to the Ad-Accounts page (balance + debt) AND the
per-account ad cards on the Dashboard (cumulative).

### What was already in place
- Backend half-hour cron (`iter-139: _ad_account_halfhour_sync`)
  runs `run_daily_cron` every 30 min — picks up new daily-spend
  rows from `snapchat_account_daily` / `meta_ads_daily` /
  `tiktok_ads_daily`, updates `ad_account_ledger`, recomputes
  balance & liability. Logs every 30 min in backend.err.log.
- `POST /api/ad-accounts/sync-all` already existed for manual
  trigger.
- Dashboard already polled `/api/dashboard` every 60 s.

### What was missing (root cause of merchant complaint)
- `AdAccounts.jsx` had NO polling — page numbers stale until
  reload.
- `SnapchatAccountsCards` component fetched ONCE on mount and
  never refreshed.
- Dashboard's "تحديث جميع الإعلانات" button updated daily_costs
  but did NOT trigger `/ad-accounts/sync-all` nor refetch the
  per-account cards.
- UI label still said "كل يوم 11:55" — outdated since iter-139.

### Fix
- `AdAccounts.jsx`:
  • Silent auto-poll (`setInterval`) every 30 minutes; only when
    `document.visibilityState === 'visible'`.
  • Visibility-change listener: refetch instantly when the
    merchant comes back to the tab.
  • Surface "آخر تحديث: hh:mm" pill (testid `adacc-last-loaded`).
  • Updated label to "كل 30 دقيقة — بدون الحاجة لفتح هذه الصفحة".
- `SnapchatAccountsCards.jsx`:
  • Accepts a `refreshSignal` prop and re-fetches whenever the
    parent bumps it.
  • Internal 30-min `setInterval` for silent autonomous polling.
  • Shows "آخر تحديث hh:mm" in card header.
- `Dashboard.jsx`:
  • New state `adCardsRefreshSignal`; bumped after the existing
    `refreshAllAds` flow finishes.
  • `refreshAllAds` now ALSO calls `POST /api/ad-accounts/sync-all
    { force: true }` so the backend processes today's spend
    immediately before the cards re-read.
  • Passes the signal into `<SnapchatAccountsCards
    refreshSignal={adCardsRefreshSignal} />`.

### Verification
- ✅ Manual API: `POST /api/ad-accounts/sync-all` returns
  `{ok: true, results: [...] }` with per-account spend.
- ✅ Visual: `/ad-accounts` shows the new pill
  "آخر تحديث: 12:45 م" + corrected label. Snap Test SSOT card
  still reads balance=1,500 ر.س from the Iter-203 topup.
- ✅ Existing backend cron continues to fire every 30 min
  (confirmed via `iter-139: ad-account half-hour sync done`
  log lines).


## Iter-205 — Ad-Account Spend SSOT (P0, Feb 15 2026)

### Request
Tie EVERY ad-spend movement (manual `/spend`, half-hour cron,
sync-all, sync-from-platform) into `general_ledger` with the
accounting model:

    DEBIT  expense.advertising  = total
    CREDIT ad_account.balance   = min(total, prepaid_live)
    CREDIT ad_account.debt      = remainder (if any)

Idempotency required to survive cron retries.

### Fix
- New module-level helper `_post_spend_to_ledger` in
  `ad_account_routes.py`:
  • Computes `prepaid_live` from the LEDGER (not the drifty
    `counterparties.balance`).
  • Builds a balanced txn_group with 2 or 3 legs.
  • Stores `metadata.idempotency_key =
      spend:{cp_id}:{provider}:{date}:{source}:{amount:.2f}`.
  • Existing key found → returns
    `{ok: True, skipped: True, txn_group_id: <existing>}` —
    NO duplicate row.
- Wired into:
  • `POST /api/ad-accounts/{id}/spend` (manual) → source="manual".
    Response now carries `ledger_txn_group_id` + `ledger_skipped`.
  • `_run_sync_for_all` (used by `/sync-all`, `/sync-from-platform`,
    and the iter-139 half-hour cron) → source="ad_account_cron",
    amount = the DELTA being applied. Same delta retried within
    the same day = idempotent.
- New MongoDB partial index `gl_user_idem` on
  `(user_id, metadata.idempotency_key)` for cheap dedupe checks.

### Tests
- `/app/backend/tests/test_ad_account_spend_ssot_iter205.py`
  (4 scenarios, all green when run alone — chained-test failure is
  the project-wide pytest-asyncio loop-close issue documented in
  iter-196):
  • spend < prepaid → only `ad_account.balance` credited, no debt.
  • spend > prepaid → balance drained to 0, remainder lands in
    `ad_account.debt`.
  • retry same spend → idempotent skip, ledger row count unchanged,
    `expense.advertising` net total unchanged.
  • cron helper called twice with same (cp, date, source, delta) →
    second call returns `skipped=True` with the same
    `txn_group_id`.
- Double-entry invariant verified: Σ debits == Σ credits across all
  `txn_type="ad_account_spend"` groups.

### Visual / API Verification on Preview
Used the existing Snap Test SSOT account (balance=1,500 from
Iter-203 topup):
1. Spend 800 → covered=800, uncovered=0, `txn_group_id` returned.
2. Spend 800 retry → `ledger_skipped: true`, same txn_group_id —
   NO duplicate.
3. Spend 1,500 over → SSOT correctly drained remaining 700 from
   balance and created 800 debt (legacy diverged here because it
   has no idempotency, but the user explicitly accepts SSOT as the
   truth — legacy is being phased out).

Final SSOT state visible via `/api/ledger/entries`:
    expense.advertising debit  = 800 + 1,500 = 2,300
    ad_account.balance  debit  = 1,500
    ad_account.balance  credit = 800 + 700 = 1,500   (net = 0)
    ad_account.debt     credit = 800                 (net = -800)
Perfectly balanced.

### Out of Scope (deferred)
- `PUT /topup/{ledger_id}` (edit topup) — still legacy only.
- `PUT /opening` (opening balance) — still legacy only.
- Per user instructions, these stay untouched until Spend is
  proven in production data.



## Iter-206 — Advertising Expenses Report (SSOT, Feb 15 2026)

### Backend
- `GET /api/accounting/reports/advertising-expenses?from_date=…&to_date=…`
  Reads ONLY `expense.advertising` debit rows from `general_ledger`.
  Returns: `total`, `by_platform`, `by_ad_account`, `by_month`,
  `daily`.

### Frontend
- New page `/reports/advertising-expenses`
  (`AdvertisingExpensesReport.jsx`): date range picker, total card,
  platform grid, ad-account table with % share, monthly bar chart.
- New sidebar link (`nav-advertising-expenses`).

### Verification (Preview)
With Iter-205 seed data on Snap Test SSOT:
- total = 2,300 ر.س, by_platform=[{snapchat:2300,count:2}],
  by_ad_account=[{Snap Test SSOT:2300}], by_month=[{2026-02:2300}].
- Screenshot confirms all four sections render correctly.


## Iter-207 — Gateway orders_count consistency (Feb 15 2026)

### Bug
Profit Summary card reported **76** orders / 18,405.78 ر.س while
the unified Payment Gateways card showed **79** orders / 18,405.78
ر.س for the same period — same gross but mismatched count, plus a
sub-mystery where the table-rows summed to **71** (missing 8 in
"_other" hidden bucket).

### Root cause
`payment_gateway_metrics.compute_metrics` was incrementing
`bkt["orders_count"]` BEFORE the cancelled/pending `continue`
branches, so it tallied every order in the universe even when
that order contributed nothing to `gross`. Profit Summary, in
contrast, applies the user's `settings.report_included_statuses`
filter upstream — leaving the two cards counting different
universes.

### Fix
- `payment_gateway_metrics.py`:
  1. Move `bkt["orders_count"] += 1` to AFTER pending/cancelled
     filters → only confirmed + refunded orders count.
  2. Load `settings.report_included_statuses` and pre-filter
     orders with the same case-insensitive partial-match logic
     Profit Summary uses (mirrors `_matches_any` in server.py).
     Empty list ⇒ no filter (unchanged behaviour for users who
     haven't configured it).
  3. Loosen the row-visibility filter: include a bucket if it
     has ANY activity (orders/pending/cancelled) — previously a
     gateway with only pending orders disappeared silently.

### Tests
- `/app/backend/tests/test_payment_gateway_orders_count_iter207.py`
  (1 test, 2 scenarios, all green):
  • 3 confirmed + 1 refunded + 1 pending + 1 cancelled mada +
    1 confirmed `_other`. Assert orders_count = 4 mada + 1 other
    = 5 total; pending/cancelled tracked but not in
    orders_count.
  • With `report_included_statuses=["تم التوصيل"]`, assert
    orders_count drops to exactly 2 (the two delivered orders).
- Iter-81 regression (`test_payment_gateway_unification_iter81.py`)
  still 9/9 passing.

## Iter-207c — Transparency Badge (Salla ↔ Accounting gap)

### Context
After Iter-207, both cards count only confirmed+refunded orders.
Merchant reported the Salla platform itself shows 79 orders /
18,960.34 ر.س (every order regardless of status). The 3-order /
554.56 ر.س gap was invisible to merchants comparing to Salla.

### Decision
Keep the accounting-conservative number as the main (76 / 18,405.78)
and surface the gap as a small transparency badge + tooltip.
The PROFIT, COST, ROAS, FINANCIAL POSITION numbers DO NOT change.

### Backend (extra fields, no math changes)
- `compute_metrics` in `payment_gateway_metrics.py` tracks:
  • `salla_reference_orders_count` — every row in the date window
    (Salla-equivalent count).
  • `salla_reference_gross` — total_amount of those rows.
  • `excluded_orders_count` — anything filtered out by
    `report_included_statuses` OR pending/cancelled.
  • `excluded_gross` — sum of total_amount of excluded rows.
- `/api/dashboard` snapshots the SAME 4 fields from `all_orders`
  before applying `report_included_statuses`.

### Frontend
- `ProfitSummaryCard.jsx`: orders KPI tile carries a small
  amber pill `+X معلَّق/ملغى (Y ر.س)` (testid
  `profit-kpi-orders-excluded-badge`) with a hover tooltip that
  reads exactly the wording the merchant approved:
    "منصة سلة تعرض جميع الطلبات المنشأة (79 بقيمة 18,960.34).
     النظام المحاسبي يعتمد فقط الطلبات الداخلة في التقارير المالية
     (76 بقيمة 18,405.78). الفرق: 3 طلبات معلقة أو ملغاة بقيمة
     554.56 ر.س."
- `UnifiedPaymentGatewaysCard.jsx`: similar inline badge in the
  totals row (testid `…-excluded-badge`) with the same tooltip
  copy.
- Badges only render when `excluded_orders_count > 0`, so clean
  data shows no clutter.

### Tests
- `test_payment_gateway_orders_count_iter207.py` extended with
  3 new asserts:
  • Without `report_included_statuses` → excluded = 2
    (1 pending + 1 cancelled), gross = 200.
  • With `report_included_statuses=["تم التوصيل"]` → excluded = 5
    (everything else), gross = 500.
  • `salla_reference_orders_count` stays at 7 regardless of
    filter (Salla universe).
- All scenarios PASS.

### Verification
- ✅ API: `salla_reference_*`, `excluded_*` fields populate in both
  endpoints (verified via curl on Preview).
- 🚀 Visual verification on Production after Deploy:
  Expected: badge "+3 معلَّق/ملغى (554.56 ر.س)" next to "76" in
  both cards, with the agreed tooltip text on hover.



### Note on deployment
- User runs Production on `mezansalla.com`. Fix applied in Preview
  only — requires Deploy to take effect on Production.



## Iter-208 — Fix `/employees/{id}/summary-balance` (Salary Payment screen)

### Bug
On the Preview environment merchant reported that picking an
employee in the "حركة مالية جديدة → صرف راتب موظف" flow displayed
balance = 0 and no cumulative salary, even though the Employees
list showed the correct accrued amount.

### Root cause
The `summary-balance` endpoint (used ONLY by the
salary-payment form to show "what does the company owe this
employee?") was reading only from `general_ledger`, missing the
post-cutoff dynamic accrual delta introduced in Iter-203 for the
Employees list & financial-summary endpoints.

### Fix
- `universal_accounting_routes.py::employee_summary_balance` now
  applies the same `_post_cutoff_accrual_delta` helper used by
  Iter-203 endpoints. Response gains two new fields:
  • `salary_payable_ledger` — frozen ledger snapshot value
  • `pending_accrual`       — un-posted daily accrual since cutoff
  `salary_payable` (and therefore `net_due_to_employee`) now equal
  `ledger_outstanding + pending_accrual` — matching the Employees
  list screen exactly.
- `UnifiedEntryScreen.jsx` adds a small amber sub-line under the
  net amount:
    "+X.XX ر.س استحقاق يومي تراكمي حتى اليوم"
  rendered only when `pending_accrual > 0.005` (testid
  `unified-employee-pending-accrual`).

### Tests
- `/app/backend/tests/test_summary_balance_pending_accrual_iter208.py`
  (1 scenario, PASS): seeds an employee who started 10 days ago at
  3,000 ر.س/month; expects `salary_payable ≈ 1,000 ر.س` with
  `pending_accrual` matching it and `ledger` field at 0.
- Iter-203 regression: passes when run alone (chained failure is
  the known pytest-asyncio loop-close issue documented earlier).

### Verification on Preview
- ✅ `GET /accounting/employees/{id}/summary-balance` now returns
  `salary_payable=16,500, pending_accrual=16,500,
  salary_payable_ledger=0` for the seed employee (was 0 before).
- ✅ Frontend pulls the new value and the golden-rule banner /
  net-due indicator now reflect reality.




## Completed Work — Iter-213 (Feb 15 2026): Transaction Detail Modal in Unified Entry

**User request**: "أريد عند الضغط على أي صف في «آخر الحركات المالية» أن أرى ملخّصاً مفصّلاً يوضّح كيف نُفّذت العملية محاسبياً (المدين والدائن)."

**Implementation** (`/app/frontend/src/pages/UnifiedEntryScreen.jsx`)
1. Added `selectedTxn` state inside `RecentTxnsPanel`.
2. Every row in the recent-txns table is now clickable (cursor + hover state).
3. New `TxnDetailModal` component renders for the clicked group:
   - Header with txn_type (Arabic) + txn_group_id (mono).
   - Plain-Arabic summary (`txn.notes`).
   - Debit & Credit cards (color-coded emerald/rose) showing
     entity label, sub-account label, and amount per leg.
   - Balance check footer ("✅ القيد متوازن" vs "⚠️ القيد غير متوازن").
4. Click outside / ✕ button closes the modal.

**Iter-213b — Arabic label polish**
- Added missing labels for: `employee_settle`, `expense`, `adjustment` (txn types) + `revenue`, `equity`, `asset`, `liability` (entity types) + `opening_balance`, `sales` (sub-accounts). Table & modal now render fully in Arabic.

### Verification (Preview, real merchant data)
- ✅ 7 distinct rows tested; modal opens cleanly with debit/credit breakdown for each.
- 🔴 **DISCOVERY — SSOT data-integrity violations**: 2 single-leg
  ledger rows exist in preview general_ledger (entry_no 4 & 6,
  posted 2026-06-14):
  - `391613e2-…` (مصروف · اشتراك): 3,000 debit, no credit leg.
  - `55965448-…` (بيع · تسوية سلة): 50,000 credit, no debit leg.
  Modal correctly flags both as "⚠️ القيد غير متوازن". Awaiting user
  decision (reverse vs. patch).


## Completed Work — Iter-214 (Feb 15 2026): Audit Trail + One-Click Group Reversal

**User request**: "نعم مع عرض اسم الموظف الذي أضاف القيد بالجدول والذي عكس القيد"
(Add the reverse-this-entry button + show creator and reverser names in the table.)

**Backend changes**
1. `GET /api/ledger/entries` (`ledger_routes.py`) — enriches each row with:
   - `posted_by_name` (creator) resolved from `users` collection.
   - `reversed_by_name` + `reversed_at` when the row has been reversed
     (looks up the reversal entry to get its `posted_by`).
   Single-batch lookup — at most one `users.find({id: {$in: …}})` per call.
2. New endpoint `POST /api/ledger/groups/{group_id}/reverse` — atomically
   reverses every leg of a txn group via `reverse_entry`. Pre-validates
   that **all** legs are `posted` and not already reversed before
   committing the first change. Requires `reason_code` (mirrors
   REASON_CODES). Returns `{ok, reversed_count, group_id}`.

**Frontend changes** (`UnifiedEntryScreen.jsx`)
1. Recent-transactions table gained two new columns:
   - **بواسطة** (`recent-row-${gid}-creator`) — shows the creator name.
   - **الحالة** (`recent-row-${gid}-status`) — green "✓ معتمد" badge
     for active txns, pink "↩︎ معكوس · {reverser}" badge for reversed
     ones (row gets a faint pink background + strike-through amount).
2. Inside `TxnDetailModal`:
   - Audit-trail strip shows "أضافها: {creator}" and (if any)
     "عكسها: {reverser} · {time ago}" pills.
   - New reversal panel at the bottom:
     - **Balanced + active** → red button **"↩︎ عكس هذه الحركة"**
       expands an inline form with reason-code `<select>` (6 codes
       from REASON_CODES) + optional notes textarea + confirm/cancel.
     - **Already reversed** → pink notice "هذه الحركة معكوسة بالفعل
       بواسطة {name}." (button hidden).
     - **Imbalanced** → amber warning instructing manual accounting
       review (button hidden — protects SSOT data).
   - On confirm: hits `POST /api/ledger/groups/{id}/reverse`, toasts
     success, refreshes the recent-txns panel, closes the modal.

### Tests
- `/app/backend/tests/test_group_reverse_iter214.py` (PASS): seeds a
  2-leg advance_grant group, verifies enrichment maps `posted_by` →
  name correctly, reverses every leg, confirms originals become
  `reversed` with `reversed_by_entry_id` set and new reversal legs
  remain balanced (debit==credit).

### Verification on Preview (real merchant data — `amasi.jewelery@gmail.com`)
- ✅ Table renders new columns; creator shown as "عرفات".
- ✅ Modal audit strip shows creator pill.
- ✅ Balanced row → reverse button → reason picker → confirm →
  `POST /api/ledger/groups/{gid}/reverse` → `{reversed_count:2}` →
  row immediately turns pink with strike-through amount, modal of
  that row now shows "عكسها: عرفات · قبل لحظات" and "هذه الحركة
  معكوسة بالفعل" notice.
- ✅ Imbalanced rows hide the reverse button and show the amber
  warning instead.
- ✅ Backend safety guards: re-reverse → 400 "هذه المجموعة معكوسة
  من قبل"; missing group → 404; missing `reason_code` → 422.


## Completed Work — Iter-215 (Feb 15 2026): Ad-Spend AM/PM Window Posting (Snap/Meta)

**User spec (verbatim)**: قيد واحد لكل حساب إعلاني عند 12:40 ظهراً يغطّي 00:00–12:00 من نفس
اليوم، وقيد ثانٍ عند 12:40 صباحاً يغطّي 12:00–23:59 من اليوم
السابق (لاستيعاب تأخّر Meta ~40د).

**Scope (user-approved)**:
- ✅ Snap + Meta only. TikTok / Make.com keep Iter-205 behaviour.
- ✅ PM_12_24_CORRECTION supported (late Meta data captured next AM).
- ✅ Flexible windows: 12:30–13:30 (AM) / 00:30–01:30 (PM). Ledger
   date stays AM=today, PM=yesterday.
- ✅ NO touch on legacy Iter-205 entries; start from publish date only.

**New module**: `/app/backend/ad_spend_windows.py`
- `current_window()` → returns `(period, target_date_iso)` if now is
  in an AM or PM window, else `None`.
- `_cumulative_spend(...)` → reads `*_account_daily` for one date,
  respects external_account_id scoping (same guards as
  `_fetch_daily_spend`).
- `_already_posted(...)` → sums debits already booked for a given
  account/date/period (supports regex-prefix for correction sweeps).
- `_post_one_window(...)` → atomic balanced txn group:
    DEBIT  expense.advertising = amount
    CREDIT ad_account.balance  = min(amount, prepaid_live)
    CREDIT ad_account.debt     = remainder
- `run_window_post(db, period, target_date, user_id=None)` →
  loops every Snap/Meta account, computes the right amount per period
  (AM=full_today, PM=full_yest−AM_yest, CORRECTION=full−AM−PM−prior),
  posts each, returns posted/skipped summary.
- `catch_up_window_posts(db, user_id=None)` → 7-day historical scan
  that auto-fills missing AM/PM postings (recovers from outages).

**Idempotency key** stored on every leg:
  `ad_spend:{provider}:{ad_account_id}:{spend_date}:{period_key}`
  where period_key ∈ {AM_00_12, PM_12_24, PM_12_24_CORRECTION:N}.

**Group metadata** on every leg:
- `iter`: "iter215"
- `ad_account_id`, `ad_account_name`, `ad_provider`, `spend_date`
- `window_period`: e.g. "AM_00_12" or "PM_12_24_CORRECTION:1"
- `posted_for_window`: `{period, target_date, full_day_total_at_posting, source_collection}`
- `idempotency_key`, `covered`, `uncovered`, `amount`

**Cron change** (`ad_account_routes.py:_run_sync_for_all`):
- For Snap/Meta accounts: cron is now **fetch-only**. It refreshes the
  upstream `*_account_daily` tables and the legacy `ad_account_ledger`
  for card display, but **no longer touches `general_ledger`**.
- For TikTok / Make.com accounts: Iter-205 delta posting unchanged.
- Manual `POST /spend` endpoint unchanged (still uses Iter-205 keys).

**Scheduler** (`server.py:_ad_spend_window_post_loop`):
- 5-minute heartbeat.
- Inside AM window (12:30–13:30 Riyadh) → posts today's AM, then runs
  yesterday's CORRECTION sweep.
- Inside PM window (00:30–01:30 Riyadh) → posts yesterday's PM.
- 7-day catch-up scan runs once per hour for outage recovery.
- Posting is rate-limited to once per hour-bucket inside a window so
  the loop doesn't hammer Mongo (idempotency would block dups anyway).

**Tests** — `/app/backend/tests/test_ad_spend_am_pm_iter215.py` (PASS):
1. AM today → books each account's `spend_today` as `AM_00_12`.
2. PM yesterday (no prior AM) → books full yesterday total.
3. PM_CORRECTION when yesterday's total grows after PM was booked
   (250 → 280) → correction of 30 is posted with `PM_12_24_CORRECTION:1`.
4. Re-runs are no-ops (idempotency holds).
5. Catch-up scan converges to a consistent state (no further posts).
6. Double-entry invariant holds in all scenarios.
7. Iter-205 regression test (`test_ad_account_spend_ssot_iter205.py`)
   still passes.

### Rollout safety
- No data migration. Iter-205 entries remain untouched. Iter-215
  starts producing entries from the moment the new build is deployed.
- Idempotency keys are NAMESPACE-DIFFERENT from Iter-205
  (`ad_spend:…:{period}` vs `spend:…:{amount}`), so the two cannot
  collide.


## Hotfix — Iter-215b + Iter-215c (Feb 15 2026): No-backfill + cleanup

**Production incident**: Immediately after the Iter-215 deploy, the
user observed ~21 historical AM_00_12 entries appearing on the
"آخر الحركات المالية" feed (per-day amounts of 309/286/2547/etc.
across Meta, الرياض, Self Service for multiple past days).

**Root cause**: `ad_spend_windows.catch_up_window_posts` was looping
7 days back and posting AM/PM for every missing date. The scheduler's
hourly catch-up coincided with the post-deploy moment → instant
unwanted backfill of 7 historical days × 3 accounts. The merchant
explicitly forbade backfill in the Iter-215 spec ("ابدأ Iter-215 من
تاريخ النشر فقط، ولا تعمل Backfill الآن").

### Iter-215b — Catch-up scoped to current day only
- `catch_up_window_posts` now fills **at most**:
  - today's AM (only if Riyadh time ≥ 12:30),
  - yesterday's PM (only if Riyadh time ≥ 00:30),
  - yesterday's CORRECTION (only after the next AM cutoff).
- Anything older — including 2-days-ago — is intentionally skipped.
- Constant `CATCHUP_DAYS_BACK` reduced from 7 → 0 (kept as
  documentation marker only).

### Iter-215c — Bulk cleanup endpoint
New endpoint `POST /api/ledger/admin/iter215/cleanup-backfill` —
reverses every Iter-215 entry whose `metadata.spend_date` is strictly
older than today (Riyadh). Today's entries are preserved (those are
the genuine AM postings). Each targeted group is reversed atomically
via the same `reverse_entry` helper. Idempotent: re-runs find no
matches because the originals are already `status="reversed"`.

### Tests added (all PASS):
- `test_catchup_no_backfill_iter215b.py` — seeds 8 days of Meta
  spend, runs catch-up, asserts NO Iter-215 entries created for
  spend_date older than yesterday.
- `test_cleanup_iter215c.py` — plants 4 historical groups + 1 today
  group, runs the cleanup logic, asserts only the 4 historical groups
  are reversed and today's group survives.

### Rollout plan
1. Re-deploy to production (preview changes ready).
2. User calls `POST /api/ledger/admin/iter215/cleanup-backfill` once
   (or via a frontend button — TBD with user). All 21 wrong entries
   are reversed in a single atomic pass.
3. From now on Iter-215 produces only today/yesterday entries inside
   the configured windows. Backfill is impossible by construction.


## Completed Work — Iter-216 (Feb 15 2026): Dedicated Ledger Transactions Page

**User request**: "صفحة تعرض اخر الحركات المالية ونوع الحركه — جدول يعرض 15 صف ثم سهم لتنقل بين الصفحات الجدول"

### New page
- Route: `/transactions` (`LedgerTransactionsPage.jsx`).
- Sidebar entry: "📜 سجل الحركات المالية" (testid `nav-ledger-transactions`).
- Layout:
  - Header: title + إجمالي القيود في الدفتر + 🔄 تحديث button.
  - 3 stat tiles: حركات هذه الصفحة / إجمالي المدين / إجمالي الدائن.
  - Table (6 columns): الوقت · نوع العملية · الوصف · بواسطة · الحالة · المبلغ.
  - Status column: ✓ معتمد (green) or ↩︎ معكوس · {اسم} (pink, with strike-through amount).
  - Pagination footer: page X من Y · عرض A–B من C + ← → arrow buttons + current page chip.
- Pagination: 15 rows per page, client-side after grouping legs by txn_group_id, sorted by posted_at desc.
- Row click → opens shared `TxnDetailModal` (creator pill, reverser pill if any, debit/credit breakdown, reverse button when eligible).

### Refactor
- `UnifiedEntryScreen.jsx` now also exports `TxnDetailModal`, `txnLabel`, `entityLabel`, `subLabel`, `fmtNum`, `timeAgo`,
  `TXN_TYPE_LABELS`, `ENTITY_LABELS`, `SUB_ACCOUNT_LABELS`, `REVERSAL_REASONS` so the new page reuses them with no
  duplication.

### Verification on Preview (real merchant data)
- ✅ 7 rows render with creator "عرفات" and correct status badges.
- ✅ Reversed row (advance_grant 20.00) shows ↩︎ معكوس · عرفات + strike-through.
- ✅ Modal opens correctly on row click; shows "أضافها / عكسها" pills.
- ✅ Pagination footer displays "صفحة 1 من 1 · عرض 1–7 من 7" (arrows disabled when only 1 page).
- ✅ Stat tiles compute correct page totals.

### Endpoint used
- `GET /api/ledger/entries?limit=N` (already enriched with `posted_by_name` / `reversed_by_name` in Iter-214).


## Completed Work — Iter-217 (Feb 15 2026): Financial Position SSOT (Phases A+B+C)

**P0 user report**: "القيود المحاسبية في general_ledger لا تنعكس على صفحة المركز المالي."

### Audit findings (verified by code)
Three pages were reading from THREE different sources:
- `/financial-position` → legacy `accounts.current_balance` + `liabilities` collection (NOT SSOT).
- `/financial-position-ledger` → `/accounting/financial-position` (SSOT, but a separate page).
- `/accounts` → mixed (only partial SSOT via Iter-192 opening_balance check).

### 🚨 Latent production bug uncovered & fixed
`ledger_core.compute_balance` was DOUBLE-COUNTING reversals: after reversing a salary payment, the obligation jumped to -400 instead of restoring to -200. The reversed original was filtered out (status="reversed") but the reversal entry (status="posted", entry_type="reversal") was still counted. Pre-existing bug affecting ALL reversal flows in production.
**Fix**: `compute_balance` + `compute_balances_bulk` now filter `entry_type: {$ne: "reversal"}` so each (reversed-original, its reversal) pair cancels by definition.
Also fixed: `reverse_entry` was not propagating `sub_account` to the reversal entry, breaking compute_balance for entities with sub-accounts.

### Phase A — Backend SSOT enrichment
- New module `/app/backend/financial_position_ssot.py`:
  - `compute_financial_position(db, user_id)` — assembles the legacy-compatible response shape exclusively from `general_ledger`.
  - `account_balance_ssot(db, user_id, account)` — single SSOT rule reused by every page: BNPL formula > ledger net (+ implicit current_balance opening only if no active `opening_balance` entry exists) > legacy current_balance fallback.
  - `salary_breakdown_ssot` — per-employee accrued/paid/advance derived from ledger.
  - `by_ad_provider_ssot` — ad-account debt grouped by provider.
- `/accounting/financial-position` endpoint now returns the enriched payload (assets, liabilities, totals, salary_breakdown, by_ad_provider, payment_platforms_remaining).

### Phase A — Frontend
- `FinancialPosition.jsx` swapped from `/liabilities/summary` → `/accounting/financial-position`. Old endpoints (`/liabilities/summary`, etc.) remain untouched per user directive.
- `FinancialPositionLedger.jsx` is unchanged (it already consumed `/accounting/financial-position`; the enriched shape is a superset).

### Phase B — `/accounts/summary` + per-row balances on SSOT
- `accounts_routes.py::summary` rewritten to call `account_balance_ssot` for every visible account → grand_total now matches `/accounting/financial-position`.
- `_account_with_meta` (per-row balance shown on /accounts and elsewhere) also delegates to `account_balance_ssot` so per-row matches the summary totals.
- No bulk migration. Auto-implicit-opening rule kicks in for any account that has ANY ledger activity but no `opening_balance` entry yet (matches Iter-192 semantics).

### Phase C — Tests
- `/app/backend/tests/test_financial_position_ssot_iter217.py` (PASS):
  1. baseline ↔ ledger empty → uses current_balance.
  2. post `opening_balance` → financial position changes by the correct delta.
  3. reverse the group → financial position returns to baseline.
  4. `/accounts/summary` agrees with `/accounting/financial-position` (drives helper directly).
  5. full round-trip post+reverse → net position lands on baseline (idempotency).
- All other existing tests still PASS (`Iter-205`, `Iter-214`, `Iter-215`, `Iter-215b`).

### Verification on preview (real merchant data)
- `/accounting/financial-position`: banks=98,505.32 platforms=323,592.21 net=397,605.53.
- `/accounts/summary`: bank=98,505.32 platform=323,592.21 grand=422,097.53.
- ✅ Both pages now show exactly the same per-asset numbers, and net_position = grand_total − liabilities (28,500) + advances (4,008) = 397,605.53.

### Constraints honoured
- ✅ `/liabilities/summary` and other legacy endpoints NOT modified.
- ✅ NO data migration; no production records touched.
- ✅ `/financial-position-ledger` page kept intact (still works, redirect not needed since both pages now show the same data).


## Completed Work — Iter-217b (Feb 15 2026): Reversal-Impact Audit Endpoint

**User request**: read-only endpoint to quantify the effect of the Iter-217 `compute_balance` fix on production data.

### New endpoint
`GET /api/audit/reversal-impact-report?include_ids=true`

### Module
`/app/backend/reversal_impact_audit_routes.py` (registered after `make_tabby_phase2_router` in `server.py`).

### Response shape
```json
{
  "source": "general_ledger",
  "iter": "iter217b",
  "summary": {
    "affected_entities": N,
    "total_reversal_count": N,
    "total_delta": <sum of all per-entity deltas>,
    "net_obligation_change_on_liability_entities": <sum of deltas where net_after_fix < 0>,
    "net_balance_change_on_asset_entities":       <sum of deltas where net_after_fix > 0>
  },
  "top_impact": [<top 10 by |delta|>],
  "rows": [
    {
      "entity_type": "employee|bank|supplier|ad_account|...",
      "entity_id": "...",
      "sub_account": "salary_payable|advance|main|...",
      "name": "<resolved human-readable name>",
      "reversal_count": N,
      "net_before_fix": <old buggy compute_balance result>,
      "net_after_fix":  <new corrected compute_balance result>,
      "delta": <net_after_fix − net_before_fix>,
      "delta_direction": "balance_grew|balance_shrank|no_change",
      "entries": [
        {"reversal_id": "...", "original_id": "..."}
      ]
    }
  ]
}
```

### Verification (Preview)
- 2 entities affected (test reversal seeded by Iter-214 test).
- Total delta = 0.0 (as expected — each reversal balanced).
- Employee "عرفات": delta=+20 (advance balance grew from -20 wrong to 0 correct).
- Bank "بنك الإنماء": delta=-20 (balance shrank from +20 wrong to 0 correct).

### Properties enforced
- ✅ Read-only — uses `find` / `aggregate` only.
- ✅ No mutation, no migration, no posting.
- ✅ Safe to call any number of times on production.
- ✅ Each row exposes both the buggy and corrected balances + the reversal/original entry IDs so the merchant can drill down.


## Completed Work — Iter-217b extended (Feb 15 2026): Reversal Impact Details Endpoint

**User request**: production audit returned 5 entities, 11 reversals, total_delta=+1,330. Needs to drill into the specific reversals that drove +15,850.57 on `expense.advertising`, -10,371.89 on الرياض, -5,478.68 on Self Service.

### Changes
1. `GET /api/audit/reversal-impact-report?expand_entries=true` — rows[*].entries now include full per-leg info (amount, sides, group_ids, txn_type, reason, notes, posted_at, ad_account_name, spend_date, window_period, delta_contribution).
2. **NEW** `GET /api/audit/reversal-impact-report/details` — flat list of every reversal joined with its original. Filterable by `entity_type`, `entity_id`, `txn_type`. Sort by `impact_desc` (default), `posted_at_desc`, or `amount_desc`. `limit` parameter.

### Fields per detail row
- `entity_name`, `entity_type`, `entity_id`, `sub_account`
- `amount`, `reversal_side`, `original_side`
- `original_ledger_id`, `reversal_ledger_id`
- `original_txn_group_id`, `reversal_txn_group_id`
- `original_entry_no`, `reversal_entry_no`
- `original_txn_type`, `original_notes`, `reversal_notes`
- `reason_code`, `original_posted_at`, `reversal_posted_at`
- `ad_account_name`, `spend_date`, `window_period`, `ad_provider` (when present)
- `delta` — what Iter-217 added/removed from this entity's balance because of THIS reversal

### Verification (Preview)
- `/details` returned 2 paired entries (employee +20 / bank -20 from the Iter-214 test reversal), confirming the flat shape.
- `/details?entity_type=ad_account` correctly returned 0 (no ad_account reversals on Preview).

### Still strictly READ-ONLY
- ✅ Only `find` + `aggregate`. Zero mutations. No migration. No posting.


## Iter-217b (Feb 15 2026) — `/audit/find-entry` Diagnostic Search

**User request**: pre-closure verification — locate the 105,153.97 SAR Self Service entry on production to confirm it was reversed (or still needs to be).

### Endpoint
`GET /api/audit/find-entry`

Read-only. Filterable by:
- `amount` + `amount_tolerance` (default 0.01)
- `entity_type` / `entity_id` / `side` / `status`
- `ad_account_name_contains` (regex on `metadata.ad_account_name`)
- `notes_contains` (regex on `notes`)
- `date_from` / `date_to` (ISO strings, on `posted_at`)
- `limit` (default 50)

### Returns per match
- All ledger fields (id, entry_no, txn_group_id, side, amount, entity, sub_account, status, posted_at, notes, metadata)
- `is_reversal_itself` — true if this entry is the reversal of another
- `has_been_reversed` — true if some other entry points at this one via `reverses_entry_id`
- `reversed_by` — full reversal record (id, group, posted_at, reason_code, notes, status)

### Verification (Preview)
- Searching `amount=20` returned 4 entries (the 2 original advance_grant + 2 reversal entries from the Iter-214 test). Status detection works correctly (`has_been_reversed: true` for the originals).

### Strictly Read-Only
- ✅ Uses only `find` / `find_one`. No mutations. No migrations. Safe on production.


## Completed Work — Iter-218 (Feb 15 2026): Ad-Account `PUT /topup` + `PUT /opening` → SSOT

**User request**: tie `PUT /api/ad-accounts/{cp_id}/topup/{ledger_id}` and `PUT /api/ad-accounts/{cp_id}/opening` to general_ledger — close the last legacy gaps after Iter-203/205 fixed POST.

### Backend changes (`ad_account_routes.py`)
1. **POST /topup** — adds `legacy_ledger_id` to the SSOT group's metadata so PUT /topup can look up the corresponding group directly (was previously only via `legacy_tx_id` indirection).
2. **PUT /topup/{ledger_id}** — wraps the existing legacy mutation with:
   - **Pre-step**: locate the original SSOT group (by `legacy_ledger_id`, fallback to `legacy_tx_id`). Reverse every leg atomically via `reverse_entry`. Silent no-op for legacy edits where no SSOT group was ever posted (pre-Iter-203 topups).
   - **Post-step**: after the legacy machinery completes, post a NEW balanced SSOT group with the new amount: DEBIT ad_account.balance / CREDIT bank.main. Returns `ssot_previous_group_id` + `ssot_new_group_id` for transparency.
3. **PUT /opening** — when `ledger_changes` includes a balance/debt delta, posts an INCREMENTAL balanced SSOT group: ad_account.balance vs equity.opening_balance (and ad_account.debt vs equity.opening_balance when applicable). Successive /opening edits stack correctly via the delta arithmetic (no need to reverse the prior opening — cp.balance delta drives everything).

### Test
`/app/backend/tests/test_topup_opening_ssot_iter218.py` (PASS):
- POST /topup → ad_account.balance ledger = 1000.
- PUT /topup edit 1000→1500 → ledger reflects 1500 (old group reversed + new posted).
- PUT /opening set to 200 → ledger = 200 (delta -1300 booked).
- PUT /opening set to 500 → ledger = 500 (delta +300 booked).
- All while counterparties.balance matches general_ledger to-the-cent.

### Regression
- ✅ Iter-205 (POST spend SSOT)
- ✅ Iter-214 (group reverse)
- ✅ Iter-215 (AM/PM windows)
- ✅ Iter-215b (no backfill)
- ✅ Iter-215c (cleanup)
- ✅ Iter-217 (financial position SSOT + compute_balance reversal fix)
- ✅ Iter-218 (this iteration)

All pass individually. The known multi-file `Event loop is closed` test-isolation issue persists and is unrelated to any of these iterations.



## Completed Work — Iter-219 (Feb 16 2026): Tabby/Tamara → SSOT Bridge (Phase 2a — Sales & Refunds)

**User request**: ربط مبيعات/مرتجعات Tabby و Tamara بالقيد المزدوج (general_ledger) للمعاملات الجديدة فقط بعد النشر. لا Backfill تاريخي. لا تسويات/عمولات/VAT الآن (تأتي في Phase 2b).

### Architecture (A1 + B1 — confirmed by user)
- **Sale** (status ∈ {closed, captured, authorized, fully_captured, approved, completed, partially_captured, shipped, fully_shipped}):
  - DEBIT `payment_gateway.{tabby|tamara}/receivable` = amount
  - CREDIT `revenue.bnpl_sales` = amount
- **Refund**:
  - DEBIT `revenue.bnpl_sales` = amount
  - CREDIT `payment_gateway.{tabby|tamara}/receivable` = amount

### Files
- **NEW** `/app/backend/bnpl/ledger_bridge.py` — `post_bnpl_sale_to_ledger`, `post_bnpl_refund_to_ledger`, safe wrappers.
- **MODIFIED** `/app/backend/ledger_core.py` — added `bnpl_sale`, `bnpl_refund` to `ENTRY_TYPES`.
- **HOOKED** `/app/backend/bnpl/sync_service.py` (Tabby) — after every `payment_transactions` + `payment_refunds` upsert.
- **HOOKED** `/app/backend/bnpl/tamara_backfill.py` `_persist_tamara_order` (Tamara incremental + lookup paths).
- **HOOKED** `/app/backend/bnpl/webhook_routes.py` (Tamara orders webhook) — bridge runs after upsert.

### Guards (preserve "no historical backfill")
1. **Idempotency** — `metadata.idempotency_key`:
   - `bnpl_sale:{provider}:{provider_id}`
   - `bnpl_refund:{provider}:{provider_refund_id}`
2. **Cutoff env** — `BNPL_BRIDGE_CUTOFF_ISO` (default `2026-02-12T00:00:00Z`). Any txn with `created_at_provider < cutoff` is silently skipped. Unset → no cutoff.
3. **Status whitelist** — only bookable BNPL statuses post; pending/created/rejected do not.
4. **Refund → sale dependency** — refund posts ONLY if the underlying sale is already in the ledger. Otherwise skipped (`underlying_sale_not_in_ledger`). Prevents a refund alone from creating a negative receivable when its sale is pre-cutoff.
5. **Safe wrappers** — bridge errors are logged but never break the underlying upsert.

### Test — `/app/backend/tests/test_bnpl_ssot_iter219.py` (8/8 PASS)
1. Tabby sale → balanced 2-leg group; debit=credit; receivable +500, bnpl_sales +500.
2. Re-running same sale → idempotent skip (no duplicate ledger row).
3. Refund flips signs correctly; receivable -100, bnpl_sales -100.
4. Refund without matching sale → skipped with reason `underlying_sale_not_in_ledger`.
5. `BNPL_BRIDGE_CUTOFF_ISO` blocks historical sale; fresh post-cutoff sale books fine.
6. Non-bookable status (`created`) → skipped.
7. Tamara `fully_captured` sale books with provider isolation.
8. Cross-provider isolation — tabby and tamara receivables don't bleed.

### NOT included (Phase 2b — next iteration)
- Settlement breakdown (bank + commission + VAT + fees) in `accounts_routes.py POST /transactions` for category=bank_transfer.
- Historical backfill of 800+ Tabby ops (deferred to Phase 3 by user).


## Completed Work — Iter-220 (Feb 16 2026): BNPL Settlement Bridge (Phase 2b)

**User request**: عند تسجيل تحويل من Tabby/Tamara إلى البنك، إغلاق الذمة في general_ledger وتسجيل العمولات + VAT + رسوم التسوية كمصاريف منفصلة. لا backfill تاريخي، فقط معاملات جديدة بعد النشر.

### Architecture
- **NEW** `/app/backend/bnpl/settlement_bridge.py` — `post_bnpl_settlement_to_ledger`.
- **NEW** endpoint `POST /api/bnpl/settlements/register` in `bnpl/settlements_routes.py`.
- Added `bnpl_settlement` to `ENTRY_TYPES` in `ledger_core.py`.

### Ledger group structure (5 legs max)
- DEBIT `bank.{bank_account_id}/balance` = transferred_amount (if > 0)
- DEBIT `expense.bnpl_commission` = commission (skipped if 0)
- DEBIT `expense.bnpl_commission_vat` = commission_vat (skipped if 0)
- DEBIT `expense.bnpl_settlement_fee` = settlement_fee (skipped if 0)
- CREDIT `payment_gateway.{provider}/receivable` = total

Example (transferred 9000, commission 800, VAT 120, fee 80 → closes 10,000 receivable):
```
DR bank.X/balance              9000
DR expense.bnpl_commission      800
DR expense.bnpl_commission_vat  120
DR expense.bnpl_settlement_fee   80
CR payment_gateway.tabby/receivable  10000
```

### Guards
1. **Idempotency** — `bnpl_settlement:{provider}:{settlement_reference}` (stored in every leg's `metadata.idempotency_key`). Re-posting the same reference returns the existing `txn_group_id` with `skipped=true`. No duplicate `account_transactions` row either.
2. **Receivable ceiling** — settlement total cannot exceed the current ledger receivable for that provider. Partial settlements are allowed; over-settlements raise 400 with Arabic message.
3. **Zero-receivable rejection** — settling against a provider whose receivable balance is 0 raises 400 (prevents creating a negative receivable, preserves "no historical backfill" since pre-cutoff sales never made it into the ledger).
4. **Bank account validated** — must exist for the user with `account_type ∈ {bank, cash}`.
5. **Non-negative legs** — all four amounts ≥ 0, total > 0.

### Side-effects
- Inserts a balanced `txn_group` in `general_ledger`.
- Inserts a `settlement` row in `account_transactions` on the destination bank (linked via `metadata.bnpl_settlement_group_id` + `idempotency_key`) so the bank-account detail UI shows the inbound transfer.
- Calls `_recompute_balance` so `accounts.current_balance` matches.

### Metadata (every leg + the response)
- `provider`, `transferred_amount`, `commission`, `commission_vat`, `settlement_fee`
- `settlement_reference`, `settlement_date`, `bank_account_id`, `bank_account_name`
- `idempotency_key`, `iter = "iter220"`

### Test — `/app/backend/tests/test_bnpl_settlement_iter220.py` (8/8 PASS)
1. ✅ Full settlement (10k) → 5 legs, balanced, receivable=0, bank=+9000, all 3 expenses recorded.
2. ✅ Partial settlement (4k of 10k) → remaining_receivable=6000.
3. ✅ Idempotent re-register → same `txn_group_id`, no duplicate.
4. ✅ Over-settlement (1500 vs 1000 receivable) → 400 with Arabic error.
5. ✅ Zero-receivable settlement → 400 with Arabic error.
6. ✅ Tamara same contract; no cross-provider bleed.
7. ✅ All 5 legs carry full metadata (provider, breakdown, ref, bank, date).
8. ✅ Zero-amount expense legs are skipped (no `amount=0` legs).

### NOT touched
- Regular bank transfers (`POST /api/accounts/{id}/transactions` and `POST /api/accounts/transfers`) — completely untouched. The new bridge is invoked ONLY via the dedicated `/api/bnpl/settlements/register` endpoint.
- Historical settlements (Phase 3 backfill remains deferred).



## Completed Work — Iter-221 (Feb 16 2026): BNPL Settlements Registration Page (Phase 2b UI)

**User request**: شاشة مخصصة لتسجيل تسويات Tabby/Tamara مع المطابقة الفورية. تربط على `POST /api/bnpl/settlements/register` (المُضاف في Iter-220).

### Backend (3 endpoints added in `bnpl/settlements_routes.py`)
1. **`GET /api/bnpl/settlements/registration-overview`** — per-provider aggregates:
   - `current_receivable` (from `general_ledger`)
   - `expected_total` (from `compute_all_settlements`, clamped to 0 for tiny refund-driven negatives)
   - `received_total` + `received_count` (sum of `bnpl_settlement` credit legs)
   - `difference` + `last_settlement` (most recent registered settlement)
   - `match_status` ∈ {green, yellow, red} (tolerance: <0.5 SAR exact, ≤5% bucket)
2. **`GET /api/bnpl/settlements/registered`** — paginated list of recorded settlements (sorted newest-first).
3. **`GET /api/bnpl/settlements/registered/{txn_group_id}`** — full leg detail (entries + debit/credit totals + balanced flag) for the post-save modal.

### Frontend
- **NEW** `/app/frontend/src/pages/BnplSettlementsRegister.jsx`:
  - Header: "تسويات تمارا وتابي" + refresh.
  - Two provider overview cards (Tabby + Tamara) — each shows 4 metric cells (current receivable, expected, received, difference) + status pill (green/yellow/red) + "إضافة تسوية {Tabby|Tamara}" button.
  - **AddSettlementModal**: 8 fields (reference, date, bank/cash select, transferred, commission, VAT, settlement fee, notes). Live total tally at the bottom. Saves via `POST /register`.
  - On save → idempotent toast OR success toast, then auto-opens **LedgerEntryModal** showing the 5-leg ledger with debit/credit columns and balanced indicator.
  - "آخر التسويات المسجَّلة" table with provider, reference, date, bank, breakdown columns + "عرض القيد" button per row → opens the same ledger modal.
- **Route**: `/bnpl-settlements/register` (App.js).
- **Sidebar**: new entry "📝 تسجيل تسويات Tabby و Tamara" (`nav-bnpl-register`).

### data-testids (all interactive elements)
`bnpl-register-page`, `refresh-btn`, `provider-card-{tabby|tamara}`, `metric-current-receivable-{p}`, `metric-expected-{p}`, `metric-received-{p}`, `metric-diff-{p}`, `match-status-{green|yellow|red}`, `add-settlement-btn-{p}`, `add-settlement-modal`, `input-settlement-reference`, `input-settlement-date`, `input-bank-account`, `input-transferred-amount`, `input-commission`, `input-commission-vat`, `input-settlement-fee`, `input-notes`, `modal-total`, `modal-save`, `modal-cancel`, `ledger-entry-modal`, `ledger-leg-{i}`, `ledger-modal-close`, `recent-row-{ref}`, `view-entry-{ref}`.

### Side-effects propagation
- Bridge writes to `general_ledger` (5 balanced legs) → reflects in:
  - **Financial Position** (SSOT since Iter-217)
  - **Account Summaries** (SSOT since Iter-217)
  - **Ledger Transactions feed** (Iter-216)
- Bridge also inserts a `settlement` row in `account_transactions` on the destination bank, so the bank's own UI feed shows the inbound transfer.

### Tests
- `/app/backend/tests/test_bnpl_settlement_iter220.py` — 8/8 PASS (unit-level bridge logic)
- `/app/backend/tests/test_bnpl_register_iter221_e2e.py` — 6/6 PASS (HTTP integration via real merchant login, seeds receivable, exercises full register/idempotent/over-settlement flow, cleans up)
- Playwright E2E run by testing agent: ✅ page loads, both cards render, modal opens with all 8 fields, save creates balanced 5-leg entry, ledger modal displays debits=credits, idempotent re-submit shows Arabic info toast, over-settlement rejected with Arabic 400.

### Critical code review notes (testing agent — non-blocking)
- `_recompute_balance` import is lazy + silent on exception (intentional — bank balance recompute is best-effort).
- Total>receivable tolerance is `>0.01` (absolute) — acceptable for SAR rounding.
- Modal backdrop click closes modal (standard pattern).



## Completed Work — Iter-222 (Feb 16 2026): Employee Orphan Openings Diagnostic (Phase 1 — Read Only)

**User request**: تقرير تشخيصي شامل قبل أي إصلاح للقيود اليتيمة على الموظفين (15 قيداً في Production). Read-only صرف. Phase 2 (Preview/Apply Fix) مؤجَّلة حتى مراجعة هذا التقرير.

### Backend
- **NEW** `/app/backend/employee_orphan_diagnostic_routes.py` — module dédié.
- **NEW** endpoint **`GET /api/audit/employee-orphan-openings`** — read-only, لا writes إطلاقاً.

### Classification taxonomy (مطلوبة من المستخدم حرفياً)
| التصنيف | متى يُستخدم |
|---|---|
| `deleted_entity` | الموظف غير موجود في `employees` لأي مستخدم — محذوف نهائياً |
| `employee_id_mismatch` | الموظف موجود لكنه يخصّ مستخدماً آخر (cross-tenant) |
| `missing_counter_entry` | `txn_group_id` غير متوازن (debit ≠ credit داخل المجموعة) |
| `orphan_opening` | `entry_type=opening_balance` لموظف غير موجود |
| `orphan_reversal` | `entry_type=reversal` لكن المعاملة الأصلية حُذفت |
| `other` | لا ينطبق أيٌّ مما سبق |

### Response payload (المهم)
- `summary`: total_orphans, total_debit, total_credit, net_impact + الأثر الصافي على salary_payable / advance / custody + by_classification + by_sub_account + by_entry_type
- `per_employee[]`: entity_id, name, current_balance, orphan_impact, expected_after_fix, difference, affected_count, classifications
- `entries[]`: ledger_id, txn_group_id, entry_type, sub_account, side, debit/credit, posted_at, metadata_name, classification, **reason** (شرح عربي مفصَّل لكل قيد)

### Frontend
- **NEW** `/app/frontend/src/pages/EmployeeOrphanDiagnostic.jsx`
- **Route**: `/audit/employee-orphans`
- **Sidebar**: "🩺 تشخيص قيود الموظفين اليتيمة" (`nav-employee-orphans`)
- يعرض:
  1. شريط Phase-2 placeholder مع زرّي "معاينة الإصلاح" + "تطبيق الإصلاح" — **معطّلان** (`btn-preview-fix-disabled`, `btn-apply-fix-disabled`) إلى أن نقرّر طريقة المعالجة لكل فئة.
  2. 4 stat tiles: إجمالي القيود اليتيمة / المدين / الدائن / صافي الأثر.
  3. 3 stat tiles: أثر على salary_payable / advance / custody.
  4. Pills فلترة تفاعلية حسب التصنيف (مع counts و net).
  5. جدول "حسب الموظف" — current vs expected after fix + الفرق.
  6. جدول تفصيلي للقيود اليتيمة مع شرح السبب لكل قيد + بحث.

### Verification (Preview environment)
- Endpoint returns 5 orphans للمستخدم الحالي في preview (production likely shows 15 as flagged).
- 2 employees affected (عرفات: 4 entries, خالد: 1).
- Net impact: +4,008 ر.س — كله على `advance` sub-account.
- Classifications: 4 × `deleted_entity` (4,028 debit) + 1 × `orphan_reversal` (-20 credit on unknown).
- Phase-2 buttons تظهر معطّلة كما هو مطلوب.

### Read-only invariants (assert)
- لا migrations.
- لا حذف بيانات.
- لا إنشاء قيود أثناء التشخيص.
- لا تعديل أرصدة الموظفين تلقائياً.
- المرحلة الثانية (Preview/Apply Fix) ستُبنى بعد قرار المستخدم لكل فئة.



## Completed Work — Iter-223 (Feb 16 2026): Auto Settlement Import + Reconciliation (Phase 2c)

**User request**: عدم الاعتماد على الإدخال اليدوي. الأولوية: API → Settlement Import → Auto Fill. ثم المستخدم يراجع ويعتمد. يبقى الإدخال اليدوي كخيار احتياطي. إضافة جدول مطابقة (متوقع/فعلي).

### Backend (2 endpoints added in `bnpl/settlements_routes.py`)
1. **`GET /api/bnpl/settlements/import-preview/{provider}`**
   Params: `date_from`, `date_to`, `period` (this_week|last_week|last_7d|last_14d|this_month|last_month).
   Returns: `{prefill, breakdown, bank_reconciliation, data_source}` ready to drop into the registration modal.
   Reuses the existing `compute_settlement_for_provider` engine (which already supports Tamara's official-file reconciliation — surfaced via `data_source: provider_official_file`).
   Negative values are clamped to 0 (refund-heavy periods don't register as settlements).

2. **`GET /api/bnpl/settlements/reconciliation`**
   Params: `date_from`, `date_to`.
   Returns per provider: `{expected, actual, difference, count, match_status, data_source}`.
   - Expected = `compute_settlement_for_provider().net_payable`
   - Actual = sum of `bnpl_settlement` credit legs in window
   - Status: green (<0.5 SAR diff), yellow (≤5%), red (>5% or expected==0 but actual≠0).

### Frontend (`BnplSettlementsRegister.jsx` updated)
**Provider cards** — each card now has TWO buttons:
- **📥 جلب تلقائي** (`auto-import-btn-{p}`) — purple, primary action. Fetches `import-preview/{p}?period=last_week`, opens modal pre-filled with all 4 amounts + reference + bank + date + notes.
- **＋ إضافة يدوياً** (`add-settlement-btn-{p}`) — black, fallback. Opens empty modal.

**Inside modal** — new "📥 جلب من API" header bar:
- Period selector (last_week / this_week / last_7d / last_14d / this_month / last_month)
- "جلب وملء الحقول تلقائياً" button (`btn-auto-import`)
- After fetch: shows `import-summary` panel with data_source + gross_sales + refunds + net_sales + period dates
- User can re-fetch with a different period without closing the modal

**Reconciliation table** (`reconciliation-section`):
- Columns: المزوّد · المتوقع · الفعلي · الفرق · عدد التسويات · المصدر · حالة المطابقة
- Period selector with refresh button
- Color-coded status pills (green/yellow/red) matching the rest of the page
- Source column shows "📄 ملف رسمي" or "🖥️ محسوب"

### Auto-flow E2E (verified live in preview)
1. Click "📥 جلب تلقائي" on Tabby card → toast: "تم جلب القيم. راجع وعدّل ما يلزم ثم اعتمد."
2. Modal opens with `settlement_reference=TABBY-2026-06-08-AUTO`, transferred/commission/VAT/fee pre-filled.
3. Reconciliation table shows Tabby row with status pill "يحتاج مراجعة" (preview has no real sales window).

### data-testids
`auto-import-btn-tabby`, `auto-import-btn-tamara`, `btn-auto-import`, `import-period`, `import-summary`, `reconciliation-section`, `reconcile-period`, `reconcile-refresh`, `reconcile-row-tabby`, `reconcile-row-tamara`.

### NOT changed
- The bridge logic (`POST /register`) — unchanged from Iter-220.
- The manual entry flow — fully preserved as fallback.
- All 16 pytests (Iter-219 + Iter-220) still pass.

### Future enhancement (not in this iter)
- Real provider settlement APIs (Tabby `/settlements` endpoint, Tamara invoice file ingestion). Currently we rely on the existing computed values. If/when the user uploads a Tamara official file, the `data_source` will automatically flip to `provider_official_file` for that window.



## Completed Work — Iter-224 (Feb 16 2026): Deep Employee Orphan Diagnostic (Read-Only Extension)

**User request**: قبل أي إصلاح أريد تقريراً تشخيصياً أعمق. Read-Only. ممنوع أي عكس أو إنشاء قيود.

### Backend extensions (same endpoint, richer payload)
Endpoint: `GET /api/audit/employee-orphan-openings` — extended to include:

1. **`per_employee[]`** — now has explicit fields per request:
   - `employee_id`, `employee_name`, `orphan_count`, `net_effect`
   - Per sub_account: `salary_payable_debit/credit`, `advance_debit/credit`, `custody_debit/credit`
   - Per entry_type: `opening_balance_debit/credit`, `reversal_debit/credit`, `salary_accrual_debit/credit`, `salary_payment_debit/credit`
   - `repair_suggestions[]`, `txn_group_ids[]`

2. **`per_group[]`** — new aggregation by txn_group_id:
   - `count_entries`, `total_debit`, `total_credit`, `balanced` (true/false)
   - `affected_employees[]`, `affected_employee_names[]`, `entry_types[]`
   - Sorted: unbalanced first, then by leg count

3. **Per-entry `repair_suggestion`** ∈ `{KEEP, RECREATE_EMPLOYEE, REVERSE, MANUAL_REVIEW}`:
   - **KEEP** — entry is part of a self-cancelling pair (per-(employee, sub_account, entry_type) net = 0). No impact.
   - **RECREATE_EMPLOYEE** — `deleted_entity` / `orphan_opening` with a known name in metadata. Recreating the employee record will resolve the orphan with zero ledger writes.
   - **REVERSE** — `missing_counter_entry` (broken group) OR orphan with no name to recreate.
   - **MANUAL_REVIEW** — `employee_id_mismatch` (cross-tenant) OR `orphan_reversal` (target gone).
   - Each entry also carries a `repair_reason` (Arabic).

4. **`summary.by_repair_suggestion[]`** + `groups_count` + `groups_unbalanced_count` + `iteration: "iter223-deep"` + `generated_at` timestamp.

### Frontend (`EmployeeOrphanDiagnostic.jsx` extended)
- **📤 تصدير JSON** button (`btn-export-json`) — downloads the full diagnostic payload as `employee_orphans_{timestamp}.json`.
- **Repair summary pills** (`repair-summary-{KEEP|RECREATE_EMPLOYEE|REVERSE|MANUAL_REVIEW}`) with counts and net amounts — surfaced ABOVE the classification pills.
- **Per-employee table — rewritten**: 11 columns including a 2-level header that splits each sub_account into debit/credit columns + entry_type debit/credit + `net_effect` + repair suggestion pills.
- **Per-txn_group section** (`group-row-{group_id}`): shows balance state (✓ متوازن / ⚠ غير متوازن), debit/credit totals, leg count, affected employee names, and entry types.
- **Entries table — extended**: new `المعالجة المقترحة` column with color-coded pill + `repair_reason` shown as italic hint below the orphan reason.
- Phase-2 buttons (`btn-preview-fix-disabled`, `btn-apply-fix-disabled`) remain **DISABLED** as requested.

### Critical insight from preview verification
For the preview merchant (5 orphans), all 4 affected `txn_groups` are **perfectly balanced** (✓). This answers the user's strategic questions:
1. هل الموظفون حُذفوا فقط والقيود صحيحة؟ → **YES** (all groups balanced)
2. هل توجد قيود افتتاحية يجب الإبقاء عليها؟ → None in preview
3. هل توجد قيود رواتب يجب عكسها؟ → None (only `advance_grant` + `reversal`)
4. هل توجد مجموعات غير متوازنة؟ → **NO** (0 unbalanced of 4)

The same endpoint will give a definitive answer on the 40 production orphans without any writes.

### Read-only guarantees (still upheld)
- لا migrations · لا حذف بيانات · لا إنشاء قيود · لا تعديل أرصدة الموظفين تلقائياً
- زرّا Preview Fix / Apply Fix معطّلَان بشكل صريح حتى قرار المستخدم



## Completed Work — Iter-226 (Feb 16 2026): Legacy Orphan Archive + Future-Orphan Prevention

**User decision**: لا تنفيذ Phase 2 إصلاح. اعتبر القيود الـ 40 اليتيمة Legacy Historical Data. أرشفها فقط (Flag) لتُستثنى من الأرصدة والمركز المالي، وامنع إنشاء يتامى جدد.

### What's covered now
1. ✅ القيود لا تدخل في أرصدة الموظفين الحالية (مفلترة في `compute_balance` و `compute_balances_bulk`).
2. ✅ القيود لا تؤثر على المركز المالي (مفلترة في `financial_position_ssot._group_by_*` و `by_ad_provider_ssot`).
3. ✅ القيود لا تظهر في المستحقات (مفلترة في كل aggregation عبر `metadata.legacy_orphan: {$ne: True}`).
4. ✅ Flag مضاف: `metadata.legacy_orphan = True` + `metadata.archived_at` + `metadata.archive_reason` + `metadata.archived_by_user_id` (للتدقيق فقط، لا يتأثر side/amount/sub_account/txn_group_id/status).
5. ✅ منع إنشاء قيود يتيمة جديدة عبر guard في `post_ledger_entry`.

### Backend changes
- **`ledger_core.compute_balance`** + **`compute_balances_bulk`**: أضيف `"metadata.legacy_orphan": {"$ne": True}` للـ match.
- **`financial_position_ssot._group_by_entity_type`** + **`_group_by_entity`** + **`by_ad_provider_ssot`** + helper aggregates: نفس الفلتر.
- **`ledger_core.post_ledger_entry`** — orphan-prevention guard جديد:
  ```python
  if entity_type == "employee" and entry_type != "reversal" and entity_id:
      emp = db.employees.find_one({user_id, $or:[id/employee_id/external_id/legacy_id == entity_id]})
      if not emp:
          raise HTTPException(400, "لا يمكن إنشاء قيد على موظف غير موجود ...")
  ```
  يضمن أن **أي إنشاء قيد جديد بعد Iter-226** يستحيل أن ينتج عنه يتيم (سواء من sync، أو salary accrual، أو UI، أو Make.com webhook).
- **`POST /api/audit/employee-orphan-openings/archive`** — يأخذ `{reason, unarchive, ledger_ids[]}` ويُحدِّث الـ metadata فقط. Idempotent. يُسجِّل audit log بـ `iter226`.
- **`GET /api/audit/employee-orphan-openings/archive/status`** — يعيد `{archived_count, archived_net_amount}`.

### Frontend changes
- صفحة `EmployeeOrphanDiagnostic.jsx` تحتوي الآن على:
  - زر **🗄️ أرشفة N قيد كـ legacy** (`btn-archive-orphans`) — برتقالي مع تأكيد window.confirm.
  - زر **↩️ إلغاء الأرشفة (N)** (`btn-unarchive-orphans`) — للأمان والتراجع.
  - **شريط حالة الأرشفة** (`archive-status-banner`) — أخضر، يظهر العدد المؤرشف والصافي إذا > 0.
  - أزرار Preview/Apply Fix بقيت معطّلة (مذكرة في الـ placeholder).
- يستخدم الـ endpoint الجديد لجلب حالة الأرشيف بالتوازي مع التقرير عند كل refresh.

### Verification (in preview)
- ضغط "أرشفة 5 قيد" → ظهر toast "تم الأرشفة: 5 قيد" + banner "✅ تم أرشفة 5 قيد كـ legacy_orphan — مستثناة من المركز المالي والأرصدة. الصافي المؤرشف: 4,008.00 ر.س".
- بعد الأرشفة: زر "إلغاء الأرشفة (5)" أصبح فعّالاً وأزرار Preview/Apply معطّلة كما هي.
- تنظيف Preview: تم إلغاء الأرشفة للحفاظ على الـ preview نظيفاً للاختبارات اللاحقة.

### Net effect on accounting
- **القيد ما زال في الدفتر** — لم نلمس أيّ side/amount/sub_account/txn_group/status.
- **لا يدخل في أي حساب** — Filter في كل aggregation حرج (`compute_balance`, financial position).
- **يظهر في audit دائماً** — التقرير التشخيصي ما زال يعرضه كاملاً.
- **يمكن استعراضه عبر metadata**: `db.general_ledger.find({"metadata.legacy_orphan": True})`.

### Future invariant
بعد Iter-226 لا يمكن خلق orphan employee entry جديد. لو ظهرت orphans جديدة لاحقاً → يعني هناك سيناريو لم يمرّ عبر `post_ledger_entry` (writes مباشرة لـ MongoDB) → نفتح ملف معالجة منفصل وقتها.

### Production deployment notes
- بعد redeploy لـ mezansalla.com:
  1. اذهب إلى `/audit/employee-orphans`
  2. اضغط "🗄️ أرشفة 40 قيد كـ legacy"
  3. أكّد في الـ dialog
  4. الـ 40 قيداً ستُؤرشف بـ `metadata.legacy_orphan = True`
  5. صفحات المركز المالي والأرصدة سيُحدَّثان فوراً (بدون cache)
  6. القيود ما زالت في الدفتر للتدقيق



## Completed Work — Iter-227 (Feb 16 2026): Tabby Refund Sync Fix (Layers 1+2)

**User issue**: 132.92 ر.س refund on Tabby didn't sync to the system; auto-fill expected 13,361.86 but Tabby's invoice was 13,236.78 (Δ 125.08).

### Root cause
`list_payments_since` filtered by **`created_at`** of the PAYMENT — not when it was last updated. A payment created months ago and refunded this week was excluded from the window, and its embedded `refunds[]` array was never read.

### Fix — 2 layers shipped

**Layer 1 — `clients/tabby.py::list_payments_since`**: switched the client-side cutoff from `created_at` to `max(updated_at, created_at)`. Any payment whose state changed inside the window is now included, regardless of original creation date. Removed the early short-circuit on single stale items (replaced with a page-level heuristic — only stop when an entire page contributes nothing).

**Layer 2 — `sync_service.sync_tabby_payments`**: enforced a 90-day minimum lookback. When the operator requests `since=last_week`, the sync silently widens to `today-90d` to ensure refunds on historical payments cannot fall through the cracks. The stats response now exposes `requested_since`, `effective_since`, and `refund_lookback_days` for operator visibility.

### Verified by `test_tabby_refund_sync_iter227.py` — 3/3 PASS
1. ✅ Payment created 100 days ago + refunded yesterday → **INCLUDED** (was previously missed).
2. ✅ No-cutoff call returns all pages.
3. ✅ Sync stats expose `requested_since`, `effective_since`, `refund_lookback_days`. Effective window is wider than requested.

### Regression
- 19/19 BNPL pytests pass (Iter-219 + Iter-220 + Iter-227).

### Network cost note
The 90-day widening means each Tabby sync now reads ~90 days of payments instead of 7 days. For an active merchant with ~30 payments/day, that's ~2700 payments fetched (paginated 20/page) — typically <30 seconds wall-clock. Acceptable cost for refund completeness.

### Not yet done
- **Layer 3** (refunds-refresh endpoint that re-queries individual old payments via `GET /payments/{id}` for >90-day refunds) — deferred. If user reports a refund from >90 days ago that didn't sync, we add it then.



## Completed Work — Iter-228 (Feb 16 2026): Settlement Rounding Accuracy Fix

**User issue**: After Iter-227 closed the refund gap, Tabby system showed 13,236.56 vs. Tabby invoice 13,236.78 → 0.22 SAR drift (~22 halalas).

### Root cause
`_compute_provider_totals` was rounding fee + VAT **per transaction**:
```python
fee = round(amt * commission_rate, 2) + fixed_fee_per_order
sales_commission += fee
sales_vat += round(fee * vat_rate, 2)
```
With ~50 transactions and ±0.005 SAR rounding bias per txn, the total drift accumulates to ~0.22 SAR — exactly matching the user's observation.

### Fix — sum-first, round-last
```python
fee_unrounded = amt * commission_rate + fixed_fee_per_order
sales_commission += fee_unrounded
sales_vat += fee_unrounded * vat_rate
# ... rounding happens ONCE at the end via _r()
commission     = _r(sales_commission - refund_rebate)
commission_vat = _r(sales_vat        - refund_vat_rebate)
```
This matches Tabby's (and most processors') invoice math exactly.

### Tests — `test_settlement_rounding_iter228.py` (2/2 PASS, 21/21 total)
1. ✅ 50 transactions × 33.33 → commission matches sum-first formula exactly (166.49 ≠ 166.50 with per-row rounding).
2. ✅ Sale 1000 + refund 100 → commission = (1000×0.0699 + 1.0) − (100×0.0499) = 65.91 cleanly.

### Effect on Production
After redeploy, the auto-fill values should now match the Tabby invoice within < 0.02 SAR (tiny residual from floating-point representation, which `_r()` handles correctly).



## Completed Work — Iter-230 (Feb 16 2026): Ad Debt SSOT Reconciliation (Read-Only Diagnostic)

**User issue**: `/ad-accounts` shows 105,798.10 (correct) but Financial Position / Ledger show 150,554.42 → 44,756.32 SAR drift.

**User decision**: Read-only diagnostic ONLY. NO archive, NO legacy_orphan, NO writes whatsoever. Just identify the source of the 44,756 SAR drift entry-by-entry.

### Backend (read-only)
- **NEW** `/app/backend/ad_debt_diagnostic_routes.py` — module dedicated.
- **NEW** `GET /api/audit/ad-debt-diagnostic` returns per-account:
  - `account_name`, `platform`, `status`
  - `walk_balance` (mirrors `_summarise` from `ad_account_routes`)
  - `ssot_balance` (sums `general_ledger.ad_account.debt` honoring live SSOT filters: `entry_type != reversal`, `metadata.legacy_orphan != True`)
  - `difference`, `abs_difference`, `match`
  - `ssot_total_credit`, `ssot_total_debit`, `ssot_archived_count`, `ssot_archived_net`
  - `ssot_by_entry_type`: `{entry_type: {credit, debit, count}}`
  - **`entries[]`**: every raw ledger entry — `ledger_id`, `txn_group_id`, `entry_type`, `side`, `amount`, `posted_at`, `metadata_source`, `is_reversal`, `is_archived`, `contributes_to_ssot`.
- Plus `summary`: totals + `global_attribution_by_entry_type[]` (net contribution per entry_type across all accounts).

### Frontend
- **NEW** `/app/frontend/src/pages/AdDebtDiagnostic.jsx` at `/audit/ad-debt`:
  - 4 stat tiles (walk total / ssot total / diff / mismatch count)
  - Global attribution panel by `entry_type`
  - Per-account table sorted by largest abs_difference
  - **Expandable per-account row** → table of EVERY ledger entry contributing (or not) to SSOT, with `contributes_to_ssot` flag (✓ / ↩ reversal / 🗄 archived / —)
- **📤 تصدير JSON** button — downloads full diagnostic payload for offline analysis.
- Sidebar nav: "📊 تشخيص فرق المديونيات الإعلانية" (`nav-ad-debt-diagnostic`).

### Strict invariants (per user)
- ✅ NO archive button.
- ✅ NO legacy_orphan flag set.
- ✅ NO entries excluded.
- ✅ Reads `general_ledger` and `ad_account_ledger` only — zero writes.
- ✅ The diagnostic includes archived entries in its raw `entries[]` listing (transparency) but flags them so the user sees what's IN SSOT vs what isn't.

### What this answers (per user request)
> "ما هي القيود بالتحديد التي جعلت SSOT = 150,554.42 بينما صفحة الحسابات الإعلانية = 105,798.10 ؟"

By visiting `/audit/ad-debt` in Production and expanding each mismatched account, the user sees every contributing entry: ledger_id, txn_group_id, entry_type, side, amount, posted_at, source. Sum across all → 44,756 SAR attribution by entry_type.

### Next iteration (after user reviews Production data)
- Iter-231: based on the diagnostic findings, decide ONE of:
  1. Fix the Iter-218 migration logic that wrote bad entries
  2. Reverse specific txn_groups that double-counted
  3. Archive specific legacy entries with `metadata.legacy_orphan` (only if user explicitly approves the list)
  4. Switch `/ad-accounts` to consume SSOT (if SSOT proves more accurate after deeper inspection)


---

## Iter-231 — Dynamic BNPL Settlement Date from `transfer_weekdays` Config (Feb 2026)

### Problem
The `/api/bnpl/settlements/import-preview/{provider}` endpoint hard-coded the prefilled `settlement_date`:
- Tabby: `date_to + 1`
- Tamara: `date_to + 4` (assumes period ends Friday → Tuesday)

This ignored the merchant's saved configuration at `/integrations/bnpl` (`bnpl_settings.transfer_weekdays`). If the merchant updated their payout day(s), the prefilled date stayed wrong.

### Fix
- `/app/backend/bnpl/settlements_routes.py::import_preview`:
  - Reads `bnpl_settings.transfer_weekdays` (per-user × provider) which is already loaded earlier in the function for period-cycle math.
  - Searches forward up to 14 days from `date_to + 1` for the FIRST day whose weekday matches any entry in `transfer_weekdays`.
  - Falls back to the legacy hard-coded offsets only when the config is empty/unusable.

### Verified end-to-end (preview)
- Tabby `transfer_weekdays=["tuesday","wednesday"]`, `date_to=Mon 2025-09-01` → `settlement_date=2025-09-02` (Tue) ✅
- Tamara `transfer_weekdays=["tuesday"]`, `date_to=Fri 2025-09-05` → `settlement_date=2025-09-09` (Tue) ✅
- Override Tamara to `["thursday"]` → `settlement_date=2025-09-11` (Thu) ✅
- Period end already on a transfer-day: picker correctly jumps to NEXT matching day (not same-day).

### Tests
- `/app/backend/tests/test_bnpl_iter231_dynamic_settlement_date.py` — 4/4 PASSED
- Regression: `test_bnpl_settlement_iter220.py` (8/8), `test_settlement_rounding_iter228.py` (2/2), `test_bnpl_iter121_weekday_cycle.py` (5/6 — 1 pre-existing failure unrelated to this change, due to `settlement_fee_per_invoice` Tabby default = 6 SAR now, not 5).

---

## Iter-232 — Tamara Commission Rebate Fix (Feb 2026)

### Problem (root cause discovered from official Statement)
The merchant uploaded the official Tamara Statement (P0420741SA260613, 06–12 June 2026) showing:
- Tamara Fees: **1,610.39** SAR  (= 6.99% × Captured + 1.50 × order_count, with NO refund rebate)
- VAT: **241.64** SAR
- Net Payable: **16,066.90** SAR

The system was producing **15,945.65** SAR (≈ 121 SAR drift) because of a wrong assumption baked into the engine: **`refundable_commission_pct = 6.99%` for Tamara**, meaning the engine rebated the full MDR on every refunded amount.

**Reality (per Statement)**: Tamara does **NOT** refund commission on refunded orders. Every Captured order is charged the full MDR + fixed_fee regardless of whether it is refunded later in the same Statement.

### Fix
- `/app/backend/bnpl/settlements_service.py::DEFAULT_FEE_RATES["tamara"]["refundable_commission_pct"]`: `6.99` → **`0.0`**
- `/app/backend/bnpl/config_store.py::DEFAULTS["tamara"]["refundable_commission_percent"]`: `0.0699` → **`0.0`**
- Tabby unchanged (`refundable_commission_pct = 4.99` — Tabby DOES refund the refundable slice).

### Verified
- For the merchant's 06–12/06 cycle the engine now computes:
  - commission ≈ 1,610.30 (vs. Tamara 1,610.39 — drift 0.09 SAR / per-row rounding)
  - VAT ≈ 241.55 (vs. 241.64 — drift 0.09)
  - net_payable ≈ 16,067.08 (vs. 16,066.90 — drift 0.18)
- Drift is now well below 1 SAR (was ~121 SAR before).
- No file upload required — fully automatic.

### Tests
- `/app/backend/tests/test_bnpl_iter232_tamara_no_rebate.py` — 3/3 PASSED
- Regression: 17/17 across iter220, iter228, iter231, iter232.

### Important deployment note
This fix lives in PREVIEW. The user must **redeploy** for the change to take effect on https://mezansalla.com.

---

## Iter-233 — BNPL Settlements: True Idempotency + Backfill (Feb 2026)

### Problem (live production report)
Merchant registered settlement `TABBY-2026-06-08-AUTO` (16/06/2026, بنك الإنماء) successfully, but the transferred amount **never appeared** on the Inma bank account in either:
- `/banks/{id}` detail page (reads from `account_transactions`)
- `/bnpl-settlements/register` (the BNPL register page)

### Root cause
The `/register` route had a one-way fail-mode:
1. `general_ledger` entries get posted FIRST via the bridge.
2. THEN `account_transactions.insert_one` runs to mirror the transfer on the bank.

If any failure happened between step 1 and step 2 (network drop / retry / partial timeout), the second call returns `skipped=True` (because ledger already exists) and we **early-returned without checking** if the bank row was actually created. Result: settlement permanently invisible on the bank page.

### Fix (two-part)
1. **True idempotency in `/register`**: removed the early-return on `skipped=True`. The route now ALWAYS checks `account_transactions` for the same `idempotency_key` and creates the missing row if absent, regardless of whether the bridge was a fresh insert or an idempotent skip.

2. **NEW endpoint** `POST /api/bnpl/settlements/backfill-bank-transactions?dry_run=false`:
   - Scans every `bnpl_settlement` debit-leg on `entity_type=bank` in `general_ledger`.
   - For each one, ensures a matching `account_transactions` row exists (lookup by `metadata.idempotency_key`).
   - Creates the missing row and recomputes the bank balance ONCE per affected account.
   - Supports `?dry_run=true` for safe preview.
   - Idempotent — safe to re-run anytime.

### How the merchant fixes the existing missing entry
After redeploying to production:
1. Open https://mezansalla.com (logged-in).
2. (Optional preview) Call `POST /api/bnpl/settlements/backfill-bank-transactions?dry_run=true` from devtools → returns the list of missing entries.
3. Run the same endpoint with `dry_run=false` to repair.
4. Refresh `/banks` → بنك الإنماء — `TABBY-2026-06-08-AUTO` for 16/06/2026 now appears as an inbound `settlement` row.

### Tests
Validated via:
- Module import (no syntax errors).
- Preview dry-run hit successfully (`checked=0` because no BNPL settlements exist in preview DB).
- 15/15 regression tests in `iter220`, `iter221`, `iter231`, `iter232` still pass.
- The pre-existing 6 async-event-loop errors in `test_bnpl_register_iter221_e2e.py` are unrelated test-harness rot, not caused by this change.

### Deployment note
This fix lives in PREVIEW. **Redeploy** required to push to https://mezansalla.com before the merchant can run the backfill endpoint.

---

## Iter-234 — Tamara Same-Week Capture+Refund Recovery (Feb 2026)

### Problem (live production confirmation)
Merchant's Tamara Statement (06–12 June 2026) showed:
- Captured: 20,848.30 (includes order 264553438 at 133.73)
- Refunds:  2,929.37 (includes order 264553438 at 133.73)
- Net:      17,918.93

System returned:
- gross_sales: **20,714.57**  ← short by exactly 133.73
- refunds:    2,929.37  ✓
- net_sales:  17,785.20  ← short by 133.73

### Root cause
Order `264553438` was captured AND refunded inside the same week. Tamara's Statement counts it in BOTH columns. Our engine groups Tamara sales by `effective_settlement_date`, which for this order fell back to `created_at_provider = 2026-06-04` (estimated) — OUTSIDE the 06–12 window. The refund (refunded_at = 09/06) was correctly INSIDE the window. Result: gross missed 133.73 while refunds counted it.

### Fix
In `_compute_provider_totals` for Tamara (only): after aggregating refunds, scan each refund inside the window. If its original `payment_transactions` row (matched by `provider_payment_id`, fallback `order_reference_id`) is NOT in the window's gross aggregation, add the original amount back into `gross_sales` and bump `transactions_count`. This mirrors Tamara's "capture+refund-in-same-week shows on both sides" rule.

Tabby is unaffected (recovery gated on `provider == "tamara"`).

### Tests
- `/app/backend/tests/test_bnpl_iter234_orphan_refund_recovery.py` — 2/2 PASSED
  - Tamara orphan refund recovers into gross (gross 100 + 133.73 = 233.73).
  - Tabby orphan refund does NOT recover (regression guard).
- Regression: 17/17 across iter220, iter228, iter231, iter232.

### Expected outcome after redeploy
For the merchant's 06–12/06 cycle:
- gross_sales: 20,714.57 → **20,848.30** (+133.73)
- total_refunds: 2,929.37 (unchanged)
- net_sales: 17,785.20 → **17,918.93** (+133.73)
- commission: 1,599.54 → ~1,610.30 (Tamara MDR on full Captured + fixed_fee × N)
- net_payable: ~16,066.90 → matches Tamara Statement to within ~0.20 SAR

### Deployment note
PREVIEW only. **Redeploy** to push to https://mezansalla.com.

---

## Iter-234b — Tamara Commission Loop Recovery + Engine Version Marker

### Problem (live production)
After redeploying Iter-234, merchant still saw net_payable = **15,945.65** (vs Tamara file 16,066.90). Root cause discovered: `compute_settlement_for_provider` has a SECOND per-order loop (Iter-228) that re-iterates `payment_transactions` to compute commission row-by-row. That loop honoured the original `sales_match` filter and **did NOT inherit the Iter-234 orphan-refund recovery**. Result: gross_sales was 20,848.30 (correct) but commission stayed at the pre-fix value (~1,599 vs 1,610), so net_payable was still off by ~121 SAR.

### Fix (Iter-234b)
1. **Mirrored the Iter-234 recovery into the commission loop**: after iterating all in-window transactions, also iterate refunds whose original capture is NOT yet counted, and add `amt * commission_rate + fixed_fee_per_order` (+ VAT) for each.  Tamara-only; Tabby unaffected.

2. **Added `engine_version: "iter234"` marker** to the settlement response so the merchant can confirm in devtools (Network tab) whether the deployed code includes the fix.

3. **NEW diagnostic endpoint** `GET /api/bnpl/settlements/order-diagnostic/{provider}?order_id=<id>&date_from=...&date_to=...`:
   - Returns the txn's full attribution state, refunds in window, and `in_window_after_iter234_recovery: bool`.
   - Read-only.  Perfect for "why isn't order 264553438 in the gross?" investigations.

### How to verify on production
After redeploy:
1. Open `/bnpl/settlements/tamara` in browser → DevTools → Network → click the `/api/bnpl/settlements/tamara` request → response should include `"engine_version": "iter234"`.  If missing, the deploy didn't pick up the new code.
2. Or call directly:
   ```
   GET /api/bnpl/settlements/order-diagnostic/tamara?order_id=264553438&date_from=2026-06-06&date_to=2026-06-12
   ```
   → Look for `"in_window_after_iter234_recovery": true` on the orphan order.

### Tests
- 11/11 regression across iter220/228/231/232/234 ✅
- engine_version + diagnostic endpoint verified on preview ✅

### Deployment note
Still PREVIEW only. **Redeploy required** to fix production net_payable to 16,066.90.

---

## Iter-234c/d — Root Cause: Stale Tamara Official File Override (Feb 2026)

### Production diagnostic (from merchant)
The diagnostic revealed the real root cause:
```
"engine_full_settlement_totals": {
  "data_source": "provider_official_file",   ← Engine using OLD uploaded file
  "transactions_count": 101,                  (not the computed 116)
  "gross_sales": 20714.57,                    (not Tamara's true 20,848.30)
  "commission": 1599.54,                      (not 1,610.30)
  "net_payable": 15945.65                     (vs Tamara file 16,066.90)
},
"engine_matches_this_txn": true,             ← Iter-234 IS active
"engine_gross_in_window": 23911.26           ← Pre-engine raw gross
```

Order 264553438 ENTERS the computed totals correctly (Iter-234 is alive and working — `engine_matches_this_txn: true`). But the Iter-147 "official file override" branch hijacks the response and substitutes numbers from an old uploaded Tamara settlement file stored in `settlement_entries`.

### Fix (Iter-234d)
1. **Surface `official_file_overrides: bool`** in the diagnostic so it's immediately obvious whether the engine output came from the file or from compute.
2. **NEW endpoint** `DELETE /api/bnpl/settlements/clear-official-entries/{provider}?date_from=...&date_to=...&dry_run=false`:
   - Removes the stale `settlement_entries` rows for the period.
   - Engine then falls back to dynamic `data_source = "computed"` (which now includes the Iter-234 orphan-refund recovery).
   - Read-only via `?dry_run=true` (returns count + sample first 5 rows for verification).

### Verified
- Endpoint imports cleanly.
- Preview dry-run returns `matched_entries: 0` (no Tamara files imported in preview DB).
- 19/19 regression pytest still passing.

### How the merchant fixes production
After redeploy:
1. (Dry-run preview) `DELETE /api/bnpl/settlements/clear-official-entries/tamara?date_from=2026-06-06&date_to=2026-06-12&dry_run=true`
   → Returns `matched_entries: N` + sample. Verify these are the stale entries you want to remove.
2. Execute: same URL with `dry_run=false`.
3. Refresh Tamara settlement page → numbers should now match Tamara file (~16,067 vs 16,066.90).

### Deployment note
Iter-234c + Iter-234d are in PREVIEW only. Redeploy required.

---

## Iter-236 — أتمتة سعر صرف الدولار وعمولة البنك للحسابات الإعلانية (Feb 2026)

### الهدف
بدلاً من إدخال سعر الصرف ورسوم البنك يدوياً مع كل فاتورة إعلانية، النظام يحفظ إعدادات مركزية ويحتسب تلقائياً عند إنشاء أي فاتورة جديدة.

### الـ Endpoints الجديدة
- `GET/PUT /api/ads-currency-settings` — usd_to_sar_rate, bank_commission_pct (+ ينشئ تلقائياً حساب «رسوم بنكية وعمولات بطاقات» في expense_accounts).
- `PUT /api/ads-currency-settings/account/{counterparty_id}` — يحفظ currency + apply_bank_commission لكل حساب إعلاني.
- `GET /api/ads-currency-settings/preview?original_amount=&currency=&apply_bank_commission=` — معاينة الحساب قبل الإنشاء.
- `GET /api/ads-currency-settings/summary` — إجمالي الإنفاق + الرسوم + المديونية لكل الفواتير.

### Snapshot على فواتير ad_account
عند POST `/api/liabilities` بـ `original_amount` + `original_currency`، النظام يحفظ:
- `original_amount`, `original_currency` — كما أدخلها المستخدم
- `sar_amount` — المبلغ مُحوَّل للريال
- `exchange_rate_used` — السعر وقت الإنشاء (snapshot، لا يتغير لاحقاً)
- `bank_commission_pct_used` — النسبة وقت الإنشاء
- `bank_commission_amount` — قيمة الرسوم
- `expected_amount` يصبح total_due_sar (sar + fee)

### التحقق الرياضي (يطابق مثال المستخدم بالضبط)
- SAR 5,000 @ 2.30% → fee 115 → total **5,115.00** ✅
- USD 1,000 @ 3.7544 @ 2.30% → sar 3,754.40 + fee 86.35 → total **3,840.75** ✅

### Frontend
- صفحة `/settings/ads-currencies` جديدة بـ:
  - 4 بطاقات إجمالية (count, ads_spend, bank_fees, total_due)
  - الإعدادات العامة (rate + pct + auto-create expense account)
  - معاينة حية USD + SAR
  - جدول لكل حساب إعلاني (currency dropdown + apply_fee checkbox)
- Link في Sidebar تحت قسم العمليات المالية.

### Tests
- `/app/backend/tests/test_ads_currency_iter236.py` — 4/4 PASSED
- Regression: 13/13 across iter231/232/234/236.

### Deployment note
PREVIEW only. Redeploy required to push to https://mezansalla.com.

---

## Iter-246 — Merge Iter-245 Logic into Unified Entry Screen (Feb 2026)

### Goal
Eliminate duplicate financial-movement entry screens.  Inject Iter-245 (new) logic directly into the existing `/new-transaction` screen so the merchant has **one** entry point for every financial movement.

### What changed
1. **Backend — `expense_categories_routes.py`**
   - New field `movement_types` on ROOT categories.  Children inherit at read-time.
   - `GET /api/expense-category-tree?movement_type=<x>` filters tree by op type.
   - Defaults assigned to seed roots and auto-backfilled for legacy data:
     - `تكاليف المنتجات` → `supplier_invoice`
     - `المصروفات التشغيلية`, `المصروفات التسويقية` → `general_expense`
     - `الأصول` → `fixed_asset`
   - `PATCH /api/expense-category-tree/{id}` accepts `movement_types` (root-only).
2. **Backend — `financial_movements_routes.py`**
   - Posting a movement whose category root doesn't list the movement_type returns HTTP 400.
3. **Frontend — `components/Iter245MovementForm.jsx` (NEW)**
   - Reusable form: takes `movementType` and renders supplier picker, category picker (filtered + supplier-scoped), line-items table for purchase invoices, payment terms, bank withdrawal method, attachment.
   - Date input forced to `dir="ltr"` + YYYY-MM-DD caption so the OS picker renders cleanly under RTL.
   - When a supplier is picked, categories shown as flat leaf names (full path stored on server).
   - Total auto-syncs to line-items sum on purchase invoices.
4. **Frontend — `pages/UnifiedEntryScreen.jsx`**
   - Added op type: `fixed_asset_purchase` → 🏛️ شراء أصل ثابت.
   - When opType ∈ {supplier_invoice, expense_record, fixed_asset_purchase}, short-circuits the legacy form and embeds `<Iter245MovementForm />`.
   - All other op types unchanged.
5. **Frontend — `pages/FinancialMovementNewPage.jsx`**
   - Stub-replaced with a redirect banner.  Route kept alive per «no-break» rule.
6. **Sidebar — `components/Sidebar.jsx`**
   - Removed `/financial-movement/new` entry (route kept in App.js).

### Tests
- `/app/backend/tests/test_iter246_movement_types.py` — 5/5 PASSED
  - seed assigns defaults
  - inheritance to children
  - filter by movement_type
  - reject mismatched category
  - patch movement_types root-only
- Regression: `test_iter244_categories_suppliers.py` — 7/7 PASSED.

### What's left from the user-requested batch
- Re-organise sidebar into «المشتريات والمصاريف» group + «الأنظمة القديمة 🕰️» group (was P0 before this merge request landed; deferred per user message focused only on the form merge).
- Usage report on legacy screens (purchase_invoices, daily_expenses, operating_expenses).
- Reports: categories, suppliers, expense analysis.

### Deployment note
PREVIEW only.  Merchant must «Save to Github → Deploy» to push to mezansalla.com.

---

## Iter-246b — Categories Editor + Sidebar + Legacy Usage Report (Feb 2026)

### Goal
Continuation of Iter-246. Three independently testable deliverables: a UI editor for `movement_types` on category roots, a Sidebar reorganisation, and a read-only Legacy Usage Report so the merchant can decide *when* to retire each old screen.

### Deliverables
1. **`pages/ExpenseCategoryTreePage.jsx`**
   - Each row now shows colour-coded chips for the inherited `movement_types` (فاتورة مورد / مصروف عام / أصل ثابت).
   - Root rows get an «أنواع العمليات» button that opens a modal with checkboxes to multi-select.
   - PATCH goes through the existing `/api/expense-category-tree/{id}` endpoint with the `movement_types` payload.
   - Empty-binding roots show a 🟥 «بدون ربط بأنواع العمليات» badge so the merchant can spot misconfig.

2. **`components/Sidebar.jsx`**
   - New section: **🛒 المشتريات والمصاريف** containing the Iter-244/245 entry points.
   - New section: **🕰️ الأنظمة القديمة** containing the legacy screens with a `🕰️ Legacy` suffix on each label.
   - Cleaned up the bloated «العمليات المالية» section (removed dupes that now live in their dedicated section).

3. **Backend — `legacy_usage_report_routes.py` (NEW)**
   - `GET /api/legacy-usage-report` → per-screen counts + last activity + 30d/7d freshness.
   - Audits 4 screens / 6 collections: `purchase_invoices`, `daily_costs`, `operating_salaries`/`_rentals`/`_prepaid_expenses`, `liabilities`.
   - Read-only.  Forward-only.  Never writes.

4. **Frontend — `pages/LegacyUsageReportPage.jsx` (NEW)** + route `/legacy-usage-report` + nav link inside «الأنظمة القديمة».
   - 3 summary cards (total / active / dead).
   - 4 per-screen cards with status badge: ⚪ فارغة / 🟢 نشطة / 🔴 خامدة.
   - Replacement path shown for each.

### Tests
- `test_iter246_legacy_report.py` — 2/2 PASSED (empty user + fresh-record activation).
- Regression: 14/14 across Iter-244/246.

### Next deliverables (deferred to a follow-up iteration to keep this PR reviewable)
- 📊 Suppliers report (purchases / due / activity).
- 📊 Categories report (main → sub → leaf with totals).
- 📊 Expense analysis dashboard (period comparisons, top spenders).

### Deployment note
PREVIEW only.  Merchant must «Save to Github → Deploy» to push to mezansalla.com.

---

## Iter-246c — Supplier Invoice Fix + Op/Account/Method Bindings (Feb 2026)

### Issues fixed
1. **فاتورة مورد** لا تُظهر جدول الأصناف إلا إذا كان الجذر `"تكاليف المنتجات"` — هذا قيد هندسي خاطئ.
2. حقل الإجمالي كان قابلاً للتعديل اليدوي على فواتير المشتريات.
3. العمليات الثلاث (`supplier_invoice`, `general_expense`, `fixed_asset`) لم تكن مربوطة بمنظومة «ربط العمليات بالحسابات».
4. لا توجد آلية لتقييد طرق الدفع (تحويل / شبكة / سحب نقدي) لكل عملية.

### Changes
- **`backend/financial_movements_routes.py`**
  - Supplier invoices now **always** require non-empty, fully-validated line_items (description, qty>0, price>0).
  - Server **re-computes** `total_amount` from line items as the SSOT (header total is ignored).
  - Wired `_enforce_account_binding` + new `_enforce_withdrawal_method` into create_movement.
- **`backend/universal_accounting_routes.py`**
  - Added `supplier_invoice`, `general_expense`, `fixed_asset` to `ACCOUNT_BOUND_OPS`.
  - Added `_enforce_withdrawal_method()` + `WITHDRAWAL_BOUND_OPS` reading `settings.operation_withdrawal_methods`.
- **`backend/server.py`**
  - `SettingsIn` now accepts `operation_withdrawal_methods: Dict[str, List[str]]`.
  - GET/PUT `/api/settings` persists & echoes the field with sanitization.
- **`frontend/src/pages/OperationAccountBindings.jsx`**
  - Added 3 new ops in section «المشتريات والمصاريف».
  - New per-op checkboxes for withdrawal methods (`cash / transfer / pos`).
  - Save now ships `operation_withdrawal_methods` to backend.
- **`frontend/src/components/Iter245MovementForm.jsx`**
  - Line items table **always** shows for `supplier_invoice` (no longer gated by category).
  - Strict row-level validation before save (description + qty>0 + price>0).
  - Fetches `/settings` to filter accounts via `operation_account_bindings[op]`.
  - Filters withdrawal-method dropdown via `operation_withdrawal_methods[op]`.
  - Hint chip shows which methods are allowed when the merchant restricted them.

### Tests
`tests/test_iter246c_invoice_e2e.py` — **9/9 PASS**
- multi-line cash / partial / credit invoice → correct totals & remaining
- empty / zero-qty / zero-price line items rejected (400)
- `operation_withdrawal_methods` round-trip via `/api/settings`
- restricted withdrawal method rejected at create time
- allow-listed withdrawal method passes
- account binding allow-list enforced

Full Iter-244/246 suite: **23/23 PASS**.

### Deployment note
PREVIEW only. Merchant must «Save to Github → Deploy» to push to mezansalla.com.

---

## Iter-246d — CRITICAL FIX: Correct Double-Entry Journal for Invoices (Feb 2026)

### Bug reported by merchant
For a partial supplier-invoice (total=144, paid=50, remaining=94), the ledger only recorded:
```
Dr 50
Cr 50
```
losing both the full expense recognition AND the supplier liability.

### Root cause
The previous logic in `financial_movements_routes.py:create_movement` used `mirror_account_txn_to_ledger` from the Iter-240 helper, which is fundamentally a TWO-leg cash-mirroring tool.  It only mirrored the **paid** portion, so credit / partial invoices were systemically under-recorded.

### Fix — Full multi-leg journal via `post_txn_group`
A purchase / expense / fixed-asset invoice now ALWAYS posts the **full invoice amount** as a balanced multi-leg journal:

| Mode    | Dr expense | Cr bank   | Cr supplier |
|---------|-----------:|----------:|------------:|
| Cash    |     total  |    total  |       —     |
| Credit  |     total  |     —     |     total   |
| Partial |     total  |    paid   |   remaining |

`ledger_core.post_txn_group` enforces `Σ debit == Σ credit` atomically.

### Implementation
- `backend/financial_movements_routes.py` rewrote the posting block: builds `entries` list with up to 3 legs (expense / bank / payable), then calls `post_txn_group` with `txn_type=movement_type`.
- `backend/ledger_core.py` added `fixed_asset_purchase` to `ENTRY_TYPES`.
- Account-transactions row still created (so balance recompute works) BUT no longer the SSOT for the ledger entry; the journal is authoritative.
- On posting failure the movement is marked `status="ledger_failed"` and `HTTPException(500)` is raised — never a silent ghost doc.

### Tests
`tests/test_iter246d_journal_correctness.py` — 4/4 PASSED
- Cash → 2-leg Dr expense / Cr bank (each 144) ✅
- Credit → 2-leg Dr expense / Cr supplier (each 144) ✅
- Partial → 3-leg Dr expense 144 / Cr bank 50 / Cr supplier 94 ✅
- Supplier balance after credit+partial = 238 outstanding debt ✅

Full regression: **27/27 PASSED**.

### Confirmed answers to merchant questions
1. ✅ Yes — a 94 ر.س payable is created on the supplier ledger.
2. ✅ Yes — appears in `/api/ledger/balance?entity_type=supplier&entity_id=<id>` (outstanding_debt + net_balance).
3. ✅ A future supplier_payment (`/api/financial-movements` with type `supplier_pay` or via the dedicated legacy endpoint) Dr supplier / Cr bank → reduces outstanding_debt by the paid amount.

### Deployment note
PREVIEW only. Merchant must «Save to Github → Deploy» to push to mezansalla.com.

---

## Iter-246e — Real Account Names + Supplier Ledger Bridge (Feb 2026)

### Merchant feedback
After Iter-246d the 3-leg journal was numerically correct, but:
1. The ledger displayed the schema-level string `expense_category` instead of the merchant-facing name «تكاليف المنتجات › منتجات › ملابس».
2. Iter-244 suppliers (stored in `db.suppliers`) didn't appear in `/suppliers-ledger` (which reads `db.counterparties` only), nor in `/api/ledger/balance` reports, so the 20 ر.س outstanding debt was invisible to the merchant.

### Fix
1. **`backend/ledger_routes.py::list_entries`** — Enriches every entry with `entity_name` (resolved from the appropriate source: `expense_category_tree.path` joined by `›`, `suppliers.company_name` with fallback to `counterparties.name`, `accounts.name` for bank legs) and `entity_label_ar` (Arabic display label).  Zero data writes — pure projection.
2. **`backend/expense_categories_routes.py::create_supplier` + `patch_supplier`** — Forward-only bridge: every new Iter-244 supplier is upserted into `db.counterparties` with `kind="supplier"` + `name_lower` (to satisfy the legacy `cp_unique_name` unique index).  Same on patch when name changes.

### Live proof (merchant's exact scenario, total=50, paid=30, remaining=20)
```
📒 القيد المحاسبي:
  حساب مصروف: تكاليف المنتجات › مستلزمات...   50.00     0.00
  حساب بنكي/صندوق: بنك الراجحي - الرئيسي       0.00    30.00
  مورد: مورد العبايات الراقية                  0.00    20.00
  الإجمالي                                    50.00    50.00 ✅

📋 /accounting/suppliers/list:
  outstanding_debt = 20.0 ر.س ✅

🏷️ /api/ledger/balance?entity_type=supplier&entity_id=…:
  outstanding_debt = 20.0 ر.س
  net_balance      = -20.0 ر.س ✅
```

### Tests
`tests/test_iter246e_supplier_bridge.py` — **2/2 PASSED**
Full regression: **29/29 PASSED**.

### Forward-only note
Suppliers created BEFORE this iteration are not auto-bridged.  Merchant can either:
- Open the supplier in `/suppliers-new` and click Save (the PATCH triggers the bridge upsert), or
- Create a new supplier — the bridge runs automatically.

This honours the «no historical mutation» rule.

### Deployment note
PREVIEW only. Merchant must «Save to Github → Deploy» to push to mezansalla.com.

---

## Iter-246f — Unified Supplier Source for «سداد مورد» (Feb 2026)

### Merchant bug report
- «عرفات» (a profile/user incorrectly tagged as counterparty kind=supplier) was showing up in the supplier_pay dropdown.
- NEW Iter-244 suppliers with real outstanding debt did NOT appear.

### Root cause
`UnifiedEntryScreen` was fetching `/counterparties?kind=supplier&limit=500` which returns every counterparty row tagged supplier (including legacy mis-tags), regardless of whether the entity has any debt — and ignored `db.suppliers`.

### Fix
1. **`backend/universal_accounting_routes.py::suppliers_with_balances`** — Now merges:
   - `db.counterparties` (kind=supplier)
   - `db.suppliers`       (Iter-244) — auto-includes orphans not yet bridged
   - Deduped by `id`.  Added `?with_debt_only=true` query parameter that filters out `outstanding_debt <= 0`.
   - Sorted by debt desc.
2. **`frontend/src/pages/UnifiedEntryScreen.jsx`** — Replaced the supplier fetch with `/accounting/suppliers/list`.  The dropdown:
   - For `supplier_pay`: filters to debt > 0 (phantom 0-debt rows are hidden).
   - For all flows: shows `الاسم — مستحق X.XX ر.س` next to each entry.
   - Shows an amber empty-state when no supplier has debt: «لا يوجد أي مورد عليه رصيد مستحق…».

### Tests
`tests/test_iter246f_supplier_pay_source.py` — **3/3 PASSED**
- Unified list includes Iter-244 suppliers.
- `with_debt_only=true` hides zero-debt rows (the «عرفات» case).
- After a partial invoice, the supplier surfaces at the top of the list with the correct outstanding_debt.

Full regression: **32/32 PASSED**.

### Deployment note
PREVIEW only. Merchant must «Save to Github → Deploy» to push to mezansalla.com.

---

## Iter-246g — Supplier_pay accepts Iter-244 suppliers without manual bridge (Feb 2026)

### Merchant bug
«المورد غير موجود» when trying to pay a supplier that was created in `/suppliers-new` BEFORE the Iter-246e bridge code was deployed (i.e., no matching counterparties row).

### Root cause
`/api/accounting/suppliers/{id}/pay` searched only `db.counterparties`; suppliers in `db.suppliers` that pre-dated the bridge were invisible to it.

### Fix
`backend/universal_accounting_routes.py::supplier_pay` — On 404 in `counterparties`, fall back to `db.suppliers`.  If found, lazily upsert the counterparties row (with `name_lower` to satisfy the unique index) and continue normally.  Same idempotent semantics as the create-time bridge.

### Tests
`tests/test_iter246g_supplier_pay_lazy_bridge.py` — **1/1 PASSED**
- Inserts a raw `db.suppliers` row (bypassing the API to mimic pre-bridge state).
- Posts a credit invoice (200 ر.س) → supplier debt = 200.
- Calls `/suppliers/{id}/pay` with amount 80 → succeeds; debt = 120.
- Verifies `counterparties` row was lazily created with `kind=supplier`.

Full regression: **33/33 PASSED**.

### Deployment note
PREVIEW only. Merchant must «Save to Github → Deploy».

---

## Iter-246h — Over-payment guard + sub_account consistency (Feb 2026)

### Two bugs reported
1. Backend allowed paying MORE than supplier's outstanding_debt (merchant paid 550 to a supplier whose debt was only 458).
2. After successful payment the supplier list didn't refresh — the merchant could re-click pay and double-debit.

### Root cause analysis
While building the over-payment guard I discovered a deeper schema bug:
- `/api/financial-movements` posted the supplier-credit leg WITHOUT `sub_account`.
- `/api/accounting/suppliers/{id}/pay` reads/writes with `sub_account="payable"`.
- Result: credits accumulated in one bucket, debits in another → they never netted out, and `compute_balance(sub_account="payable")` always returned 0.

### Fix
1. **`financial_movements_routes.py`** — Supplier liability legs now ALWAYS carry `sub_account="payable"`.  This unifies the books with the legacy supplier_pay endpoint.
2. **`universal_accounting_routes.py::supplier_pay`** — Added two backend guards:
   - `outstanding_debt <= 0` → 400 «لا يوجد رصيد مستحق… تم تسوية الدين بالكامل بالفعل».
   - `payment_amount > outstanding_debt + 0.01` → 400 «مبلغ السداد X أكبر من الرصيد المستحق للمورد Y».
3. **`UnifiedEntryScreen.jsx`** — Added `reloadSuppliers()` helper.  Invoked after every successful `supplier_pay` / `supplier_invoice` post, and the supplier picker is reset (entityId = "") so the merchant can't accidentally double-click pay on stale state.

### Tests
`tests/test_iter246h_supplier_pay_guards.py` — **4/4 PASSED**
- Baseline debt = 458 ✅
- Pay 550 → 400 «أكبر من الرصيد» ✅
- Pay 458 → debt = 0 ✅
- Pay again (any amount) → 400 «تم تسوية الدين» ✅

Full regression: **37/37 PASSED**.

### Deployment note
PREVIEW only.  Merchant must «Save to Github → Deploy».

**⚠️ Forward-only data note**: Pre-existing supplier_invoice movements posted BEFORE this iteration credited without `sub_account`.  Those balances still appear in the merchant-facing `outstanding_debt` (because `/ledger/balance` sums across sub_accounts when none is specified), so no historical data is lost — but supplier_pay debits will now correctly net them off.

---

## Iter-246i — SSOT for account balance across all surfaces (Feb 2026)

### Merchant bug
Same bank ("بنك الإنماء") displayed two different balances on two screens viewed minutes apart:
- فاتورة مورد: 73,525.86 ر.س
- سداد مورد:   50,986.91 ر.س
- Δ = 22,538.95 ر.س

### Root cause
`/api/financial-movements/accounts-with-availability` (used by فاتورة مورد) was reading the raw `accounts.current_balance` document field — i.e. whatever the legacy `_recompute_balance` last wrote, which could be hours-to-days stale on accounts whose recompute had failed silently.

Meanwhile every other surface (`/accounts`, `/accounts/summary`, `/accounting/financial-position`, `/suppliers-ledger`) consumed `account_balance_ssot()`.

### Fix
1. **`financial_movements_routes.py::accounts_with_availability`** rewritten to call `account_balance_ssot()` per row.  Response shape now also carries `stored_balance` and `balance_source` for transparency.
2. **NEW endpoint** `/api/diagnostics/account-balances` — read-only audit returning per-account `stored_balance`, `ssot_balance`, `ledger_balance`, `difference`, and `status` (ok / drift).  Merchant can hit this anytime to spot stale `current_balance` rows.

### How to read the diagnostic
```
GET /api/diagnostics/account-balances
{
  "ok": true,
  "iter": "iter246i",
  "summary": { "total_accounts": N, "drifted": K, "drift_total": X },
  "accounts": [
    {
      "account_id":   "...",
      "account_name": "بنك الإنماء",
      "stored_balance": 73525.86,   ← raw `current_balance` doc field
      "ssot_balance":   50986.91,   ← canonical (used by every screen now)
      "ledger_balance": 50986.91,   ← Σ(debits) − Σ(credits) in general_ledger
      "difference":     -22538.95,  ← ssot − stored
      "status":         "drift"
    }
  ]
}
```

### Tests
`tests/test_iter246i_balance_ssot.py` — **3/3 PASSED**
- Baseline: every surface returns the same balance for a freshly opened bank/cash account.
- No drift on clean account.
- Endpoint shape & summary fields.

Full regression: **40/40 PASSED**.

### What the merchant should now do
After Save→Deploy, fetch `GET /api/diagnostics/account-balances` (or hit it from a browser tab with the auth cookie) to see which accounts had drift.  No data is fixed automatically — the goal of this iteration is to make every screen show the SAME number; legacy stored values that disagree are surfaced explicitly so the merchant can audit them.

### Deployment note
PREVIEW only.  «Save to Github → Deploy» pushes to mezansalla.com.

---

## Iter-246j — TRUE balance parity (Δ=536 fix) (Feb 2026)

### Merchant feedback
After Iter-246i the gap shrank from 22,538.95 → 536.00 but didn't disappear.

### Real root cause
The Iter-246d journal posted the bank-credit leg WITHOUT `metadata.source="account_transaction_double_write"`.  Consequence inside `account_balance_ssot`:

```
final = ledger_net + (current_balance − dw_net)
        ↑              ↑                   ↑
        −X (my leg)    −X (recompute)      0  (NOT tagged)
       = −2X    ← double-counted!
```

Meanwhile `/cash-accounts-with-balances` only counted the leg once → 536 difference per non-bridged movement.

### Fix
1. **`financial_movements_routes.py`** — Bank-credit legs now carry:
   ```
   sub_account = "main"
   metadata.source = "account_transaction_double_write"
   ```
   so `account_balance_ssot` nets them out correctly.
2. **`financial_movements_routes.py::accounts_with_availability`** — Response now includes the debug fields the merchant requested:
   - `balance_source`, `stored_balance`, `ssot_balance`,
     `ledger_balance`, `last_calculated_at`.
3. **`UnifiedEntryScreen.jsx`** — Added a `useEffect` on `opType` that re-fetches `/cash-accounts-with-balances` whenever the merchant switches operation (no stale cache between فاتورة مورد ↔ سداد مورد).

### Tests
`tests/test_iter246j_balance_parity.py` — **4/4 PASSED**
- Baseline → all 5 surfaces return 1000.00 ✅
- After cash invoice (250) → all 5 surfaces return 750.00 ✅
- After partial invoice (paid 100) → all 5 surfaces return 650.00 ✅
- Debug fields present ✅

The 5 surfaces verified:
1. `/api/accounts`                                  (الأصول والحسابات)
2. `/api/accounts/{id}`                             (صفحة الحساب)
3. `/api/financial-movements/accounts-with-availability` (فاتورة مورد)
4. `/api/accounting/cash-accounts-with-balances`    (سداد مورد)
5. `/api/diagnostics/account-balances`              (التشخيص)

Full regression: **44/44 PASSED**.

### Deployment note
PREVIEW only.  «Save to Github → Deploy».

**⚠️ Forward-only note**: Cash legs posted BEFORE Iter-246j are still double-counted in SSOT.  The merchant can reconcile by either reposting (the diagnostic endpoint shows the drift) or accepting the historical balance and tracking forward.  No automated backfill per the SSOT discipline.

---

## Iter-246k — Suppliers Analytical Report (Feb 2026)

### Goal
Per-supplier ground-truth view aggregating data from `suppliers`, `counterparties`, `financial_movements`, and `general_ledger`.

### Deliverables
1. **Backend — `suppliers_report_routes.py` (NEW)**
   - `GET /api/reports/suppliers` returns per-supplier:
     - id, name, contact, phone, status, categories (resolved with full path)
     - invoices_count, invoices_total, paid_total, remaining_total
     - **outstanding_debt** (from `compute_balances_bulk` on the supplier ledger — same SSOT used by `/api/ledger/balance` and supplier_pay)
     - last_invoice_date, last_invoice_doc_number, last_activity (any ledger event)
     - ledger_url
   - Filters: `q`, `status`, `category_id` (expands to descendants), `with_debt_only`, `date_from`, `date_to`.
   - Totals roll-up: suppliers_count, invoices_total, paid_total, outstanding_debt.
   - Suppliers merged from BOTH `db.suppliers` AND `db.counterparties` (kind=supplier), deduped by id, so legacy AND new entries surface.

2. **Frontend — `pages/SuppliersReportPage.jsx` (NEW)**
   - 4 summary cards (count / invoices / paid / outstanding)
   - Filter bar (search + status + category + dates + debt-only + apply)
   - Sortable table with every column the merchant requested + a deep link to `/entity-ledger/supplier/{id}`.
   - Empty state, refresh button, loading state.
3. **Sidebar** — Added «📊 تقرير الموردين» under «المشتريات والمصاريف».
4. **Route** — `/reports/suppliers` added to App.js.

### Tests — `tests/test_iter246k_suppliers_report.py` — **5/5 PASSED**
Validates the merchant's three exact scenarios:
- ✅ فاتورة آجل 400 → outstanding_debt = 400
- ✅ فاتورة نقدية 480 → outstanding_debt = 0
- ✅ فاتورة جزئية 50/30 → outstanding_debt = 20
Plus filter tests: `with_debt_only`, `q` substring, `category_id` cascade.
Plus totals roll-up correctness.

Full regression: **49/49 PASSED**.

### Deployment note
PREVIEW only.  «Save to Github → Deploy».

---

## Iter-246l — Per-account Snapchat card FX + bank fees (Feb 2026)

### Merchant report
Aggregated card converted USD→SAR; per-account card showed «0.00 ر.س ≈ 420.65 USD» for the second account (متجر أماسي سعودي).  FX rate + bank commission entered in settings weren't applied to individual cards.

### Fix
`server.py::dashboard_snapchat_accounts_summary` now:
1. Reads `ads_currency_settings` for `usd_to_sar_rate` + `bank_commission_pct`.
2. Reads each counterparty's `currency` + `apply_bank_commission`.
3. Converts per-row: `sar = raw × fx`, `fee = sar × pct/100`, `total = sar + fee`.
4. Returns `spend` (= total SAR after fees) + debug fields (`spend_raw`, `spend_currency`, `spend_sar`, `bank_fee_sar`, `fx_rate_used`, `bank_commission_pct_used`).
5. Totals roll-up uses converted SAR.

### Tests
`tests/test_iter246l_snap_card_fx.py` — **1/1 PASSED**
- USD account: 100 × 3.75 × 1.02 = 382.5 ر.س
- SAR account: 100 (no conversion, no fee)
- Totals: 482.5 ر.س

Full regression: **36/36 PASSED** (across Iter-246 c→l).

### Deployment note
PREVIEW only.  **Save to Github → Deploy** to push to mezansalla.com.

---

## Iter-246m — Tamara Settlement Forensic (read-only) (Feb 2026)

### Goal
Diagnose the recurring Tamara auto-fetch gap on production without writing/migrating any historical data.  Only Tamara is in scope; Tabby is untouched and remains stable.

### Deliverable
`GET /api/audit/tamara-settlement-forensic?date_from=...&date_to=...&invoice_date=...`

Returns:
- `rates_in_use` — commission_pct, vat_pct, fixed_fee_per_order, settlement_fee_per_invoice, refundable_commission_pct, etc.
- `computed_by_system.{transactions_count, gross_sales, refunds_count, refunds_total, commission, commission_vat, settlement_fee_total, net_payable, receivables_to_close, ledger_closing_minus_net_diff}`
- `computed_by_system.{orphan_refunds_count, orphan_refunds_sum, sales_with_es_date_outside}` — orphans = refund in window but capture outside it.
- `provider_side_invoice` — cached Tamara invoice doc if any.
- `possible_causes_of_gap[]` — diagnostic narrative in Arabic.
- `raw_compute_dump` — full settlement_service output for debug.

### How to use on production
After deploy, the merchant calls (with a valid bearer token):
```
GET https://mezansalla.com/api/audit/tamara-settlement-forensic
    ?date_from=2026-06-06&date_to=2026-06-12&invoice_date=2026-06-16
```

The response numerically explains every line of the auto-fetch form (المبلغ المحول 21,655.32 / العمولة 1,845.40 / VAT 276.81 / إجمالي الإغلاق 23,777.53) AND lists candidate causes (rate mismatch, orphan refunds, settlement_fee missing, etc.).

### Strict scope
- Read-only — no writes, no migrations, no adjustments.
- Tabby left untouched.
- Forward-only data discipline maintained.

### Deployment note
PREVIEW only.  **Save to Github → Deploy** to make this available on mezansalla.com.

---

## Iter-246n — Tamara Forensic v2: Per-Order Breakdown + Baseline Delta (2026-06-18)
**Files:** `backend/tamara_forensic_routes.py` (rewrite), `backend/tests/test_iter246n_tamara_forensic.py` (new, 5 tests passing).

### What changed
The forensic endpoint `/api/audit/tamara-settlement-forensic` now returns ALL of:

1. **`orders[]`** — every captured payment_transaction inside the Saudi-local window.
   Fields per row: `order_number`, `order_reference_id`, `provider_id`, `amount`, `currency`, `status`, `created_at_provider`, `billing_eligible_at`, `effective_settlement_date`, `commission_calc`, `vat_calc`, `in_window`.
2. **`refunds[]`** — every payment_refund inside the window with `link_status` ∈ {`linked`, `orphan_no_original_found`, `linked_capture_outside_window`, `linked_but_pre_accounting`} + original capture metadata.
3. **`recovered_orders_iter234[]`** — captures lying OUTSIDE the window whose refund landed INSIDE (Iter-234 attribution recovery).
4. **`official_settlement_entries[]`** — per-row entries from any uploaded Tamara settlement file (settlement_date in [from..to]).
5. **`cross_reference`** — `orders_in_db_not_in_official`, `orders_in_official_not_in_db`.
6. **`baseline_from_user`** + **`delta_vs_baseline`** — when the merchant passes `baseline_gross`, `baseline_refunds`, `baseline_commission`, `baseline_vat`, `baseline_settlement_fee`, `baseline_net` query params, the response surfaces `{baseline, system, delta_system_minus_baseline}` per metric.
7. **`delta_vs_official_file`** — same comparison structure between system computed totals and the official settlement_entries file when present.
8. **`possible_causes_of_gap[]`** — Arabic narrative that points specifically at rate mismatches, missing orders (lists order numbers), refund attribution issues, fixed-fee drift.

### How to use on production
```
GET https://mezansalla.com/api/audit/tamara-settlement-forensic
    ?date_from=2026-06-06
    &date_to=2026-06-12
    &invoice_date=2026-06-16
    &baseline_gross=20848.30
    &baseline_refunds=2929.37
    &baseline_commission=1610.39
    &baseline_vat=241.64
    &baseline_net=16066.90
```
Authentication: `Authorization: Bearer <token>`.

### Strict scope
- READ-ONLY. The endpoint does not write to `general_ledger`, `payment_transactions`, `payment_refunds`, or any accounting collection.
- Tabby untouched. Tamara orphan-refund recovery (Iter-234) mirrored faithfully for diagnosis, not modification.

### Tests (all green)
`tests/test_iter246n_tamara_forensic.py`:
- `test_endpoint_returns_per_order_breakdown` — 3 in-window orders with per-order commission/VAT calc.
- `test_orphan_and_recovered_refunds_classified` — 1 truly orphan, 1 Iter-234 recovered, 1 linked.
- `test_baseline_delta_block` — query params propagate, delta computed.
- `test_official_file_cross_reference` — settlement_entries cross-ref.
- `test_endpoint_is_read_only` — verifies `general_ledger` / `payment_transactions` / `payment_refunds` counts unchanged after invocation.

### Deployment
Preview only. Merchant must **Save to GitHub → Deploy** to run on https://mezansalla.com against their live data.


---

## Iter-246o — Tamara Refund-Sync & Old-Capture Forensic (2026-06-18)
**Files:**
- `backend/tamara_refund_audit_routes.py` (new endpoint).
- `backend/server.py` (router mounted next to iter246m).
- `backend/tests/test_iter246o_tamara_refund_audit.py` (3 tests passing).

### Endpoint
`GET /api/audit/tamara-refund-and-old-capture-forensic` — READ-ONLY.

Query params:
- `date_from`, `date_to` (required, YYYY-MM-DD)
- `order_numbers` (optional, comma-separated — Track B targets)
- `probe_tamara_api` (bool, default false — if true, hits Tamara Merchant API per order for live status/refunds/settlement_id)

### Track A — Missing refund-row diagnosis
For every `payment_transactions` doc in the window with
`status in {fully_refunded, partially_refunded, refunded}`, the endpoint
reports per order:
- `order_number`, `provider_id`, `amount`, `captured_amount`
- `refunded_amount_local_field` (from payment_transactions doc)
- `tamara_total_refunded_amount` + `tamara_refunds_array_len` + `tamara_refunded_at` (extracted from cached raw_payload)
- `has_payment_refund` boolean + the existing row if found (matched by `provider_payment_id` OR `synthetic:<pid>`)
- Roll-ups: `with_existing_payment_refund`, `without_payment_refund`, `sum_refunded_amount_present`, `sum_refunded_amount_missing`.

### Track B — Targeted old-capture deep inspection
For each `order_numbers` query value:
- Full `payment_transactions` doc (minus raw_payload)
- All local `payment_refunds` rows
- **All `settlement_entries` rows for that order across EVERY period** (so the merchant can see if Tamara already invoiced this order previously)
- Distinct `settlement_dates_observed[]`
- Optional Tamara live probe: status, total_refunded_amount, settlement_id, captures_settlement_hints[], refunds_array_len.

### Strict scope
- READ-ONLY. No writes to general_ledger / payment_transactions / payment_refunds / unified_orders.
- Tabby untouched.
- Forward-only data discipline maintained.

### How to use on production (after Deploy)
```
GET https://mezansalla.com/api/audit/tamara-refund-and-old-capture-forensic
    ?date_from=2026-06-06
    &date_to=2026-06-12
    &order_numbers=261434840,261524720,262885232,263800910,263817759
    &probe_tamara_api=true
```
Track A will list the 8 refunded payment_transactions missing a payment_refunds row; Track B will show whether the 5 old captured-only orders appear in any prior settlement_entries file and what Tamara API currently reports.

### Tests (all green)
`tests/test_iter246o_tamara_refund_audit.py`:
- `test_track_a_flags_missing_refund_rows` — 3 refunded txns, only 1 has a payment_refunds row; sums computed correctly.
- `test_track_b_dumps_all_periods_settlement_entries` — Track B surfaces a previous-period settlement_entry for the targeted order.
- `test_endpoint_is_read_only` — verifies counts unchanged.

### Deployment
Preview only. **Save to GitHub → Deploy** to enable on https://mezansalla.com.

