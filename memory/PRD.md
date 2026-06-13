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

