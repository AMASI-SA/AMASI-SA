# PRD — MEZAN / ميزان (تطبيق محاسبي ذكي لمنصة سلة)

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية يدمج بيانات من Salla Excel + Make.com webhooks + Salla Direct API، يحسب المبيعات والعمولات والشحن وCOD، يدير الأصول والتسويات بشكل ذكي.

## Current Brand
- Name: **MEZAN / ميزان**
- Tagline: «منصة التحليلات والمحاسبة للتجارة الإلكترونية»
- Stack: React + FastAPI + MongoDB

---

## ✅ ITERATION 101 — Shipping liability in Financial Position (Feb 2026)

### Bug
The Financial Position screen showed `0` for shipping liabilities even when many delivered orders were waiting to be settled with deferred couriers (سمسا, جندل …). Also, `_owed_per_company` defaulted to **no status filter** when the user hadn't customised `report_included_statuses`, which would have inflated the figure if it were exposed.

### Fix
- **`backend/shipping_accounts.py`**:
  - New `DELIVERED_STATUSES_DEFAULT = ["تم التوصيل","تم الاستلام","تم التنفيذ","delivered","completed"]`.
  - `compute_owed_per_company(db, uid)` extracted to module level and **always** filters by delivered status (defaulting to `DELIVERED_STATUSES_DEFAULT` when the user has no custom filter).
  - Legacy `analyses.report.shipping_breakdown` path removed (it lacked per-order status and would have leaked non-delivered orders into the figure).
  - `compute_paid_per_company(db, uid)` extracted likewise.
- **`backend/liabilities_routes.py`**:
  - `/api/liabilities/summary` now imports the two helpers and adds:
    - `liabilities.shipping_unpaid` (total remaining across all deferred couriers).
    - `liabilities.by_shipping_company` (per-courier breakdown: owed / paid / remaining / orders_count).
    - `liabilities.total` and `net_position` updated accordingly.

### Frontend (`FinancialPosition.jsx`)
- New KPI card **"مستحقات شركات الشحن"** (Truck icon, amber tone) — shows total + per-company breakdown.
- Updated subtitle on total liabilities: "الرواتب + الإعلانات + الشحن + الموردين".
- New quick-link row to `/shipping-accounts`: "مستحقة فعلياً من الطلبات المسلَّمة فقط".

### Live verification
Admin account shows سمسا = 19,323 ر.س (matches `/shipping-accounts`). ✅

### Tests — `tests/test_shipping_liability_in_fp_iter101.py` (5/5 ✅)
1. **Status filter strict**: 9 orders (4 delivered + 5 of various other statuses) → only 4 counted. cancelled / in-transit / refunded ignored.
2. **Summary exposes shipping**: `shipping_unpaid` appears + included in `liabilities.total` + reduces `net_position`.
3. **Payment reduces liability**: inserting a `shipping_payments` row of 30 cuts the remaining from 100 → 70 automatically.
4. **Cross-source agreement**: `liabilities.summary.by_shipping_company[X].remaining == /shipping-accounts[X].remaining`.
5. **COD-net method end-to-end**: a 40-fee deducted via the Iter-98 atomic transfer reduces shipping liability from 100 → 60 (single ledger).

---


## ✅ ITERATION 100 — Financial-Position double-counting fix (Feb 2026)

### Bug
`/api/liabilities/summary` was summing `expected_orders_balance` (the GROSS historical order amount, never decremented) for `payment_platform` accounts.
That caused **double-counting**: e.g., Tamara orders 100k + a 90k transfer to bank showed both as platform (100k) AND bank (90k) → total assets = 190k.

### Fix (`backend/liabilities_routes.py`)
- `payment_platform` accounts now contribute their **`current_balance`** (running ledger balance after every transfer/refund/settlement via `account_transactions`).
- New, clearer key in the response: **`assets.payment_platforms_remaining`**.
- Legacy key `assets.payment_platforms_expected` is kept with the **SAME (new) value** for backward compatibility.

### Frontend (`FinancialPosition.jsx`)
- KPI card renamed to "رصيد المنصات (لم يُحوَّل بعد)".
- Reads `payment_platforms_remaining` with fallback to legacy key.
- "إجمالي الأصول" subtitle updated to "البنوك + المنصات + المديونيات (بدون تكرار)".

### Tests — `tests/test_financial_position_double_counting_iter100.py` (4/4 ✅)
1. **Tamara example**: Sales 100k − transfer 90k ⇒ `payment_platforms_remaining = 10k`, banks = 90k, total = 100k (no inflation).
2. **Cross-check**: `assets.total` from `/liabilities/summary` equals `grand_total` from `/accounts/summary` (single source of truth).
3. **Invariance**: Bank↔platform transfers do NOT change `net_position` (it stays 80k before and after a 60k transfer).
4. **Reconciliation agreement**: `payment_platforms_remaining` equals `accounts/{id}.current_balance` for the same platform.

---


## ✅ ITERATION 99 — Counterparties registry + list-pollution fix (Feb 2026)

### Phase 1 — Frontend list filtering (FinancialInputHub.jsx)
- Salary-advance dropdown now filters `category === "employee"` (excludes household / charity / contractor rows from `operating_salaries`).
- "Pay liability" dropdown excludes any open salary whose linked employee is non-employee category.
- No backend change required for Phase 1.

### Phase 2 — Counterparties collection
- New file `backend/counterparties_routes.py` (CRUD + check-duplicate).
- Collection `counterparties` — `{ id, user_id, kind, name, name_lower, ad_provider?, notes, created_at, updated_at }`.
- Unique index on `(user_id, kind, name_lower)`.
- **Three kinds**: `supplier`, `ad_account` (with `ad_provider ∈ snapchat|tiktok|meta`), `general`.
- **Fuzzy duplicate detection** via `difflib` (cutoff 0.82) — returns **WARNING ONLY** (409 `similar_name_exists`). Pass `force=true` to bypass and create a distinct row. **Never auto-merges.** This means "Snapchat Account 1", "Snapchat Account 2", "سناب الرئيسي" all remain SEPARATE counterparties when the user chooses.
- `liabilities` POST now accepts `counterparty_id` (alternative to `supplier_name` / `ad_account_label`) for kinds `supplier` and `ad_account` — name is sourced from counterparty record.
- Delete refuses if any unpaid liability still references the counterparty.

### Frontend
- New page `/counterparties` (`Counterparties.jsx`) — list + create with inline fuzzy warning, force-create, edit, delete + filter by kind + search.
- Sidebar link "قائمة الأطراف الموحَّدة" under "العمليات المالية".
- `FinancialInputHub.jsx` → new-liability tab loads counterparties and offers inline quick-add with same fuzzy warning UX.

### Tests
- `backend/tests/test_counterparties_iter99.py` — 7 tests, all passing:
  CRUD basic, exact-dup blocked, fuzzy WARNING (no auto-merge), force-create separate, check-duplicate preview, supplier+ad_account creation via counterparty_id, delete refusal when in use.

### Live verification (screenshot)
- Created "Snapchat Account 1" → got fuzzy warning when adding "Snapchat Account 2" → clicked "أنشئ منفصلاً" → both kept as 2 distinct rows (verified in UI counter: حساب إعلاني (2)). ✅

---


## ✅ ITERATION 98 — COD net method + shipping company unification

### Three improvements (all live-tested on Preview)

#### 1) Auto-populated shipping companies list
- New endpoint `GET /api/shipping-accounts/companies` aggregates
  `unified_orders.shipping_company` + `shipping_payments.company_name`
  + `transfers.shipping_company`, runs each through
  `normalize_shipping_company()`, dedupes via canonical key, sorts by
  usage frequency, and appends curated defaults.
- Live result on Preview merchant: 7 companies discovered
  (iMile 1799× / مندوب الرياض 870× / سمسا 235× / Aramex 19× / …).

#### 2) Normalisation on save + one-off migration
- `transfers_routes.py` + `shipping_accounts.py` now call
  `scrub_shipping_company()` on every save → SMSA / سمسا / smsa all
  collapse to canonical "سمسا" going forward.
- `scripts/migrate_shipping_company_names.py` (dry-run by default).
  Applied to Production-style data on Preview: 1 row updated
  (Aramex → أرامكس). 12 transfers + 222 shipping_payments already
  canonical thanks to webhook flow.

#### 3) Net-COD method (gross − fee = net)
- `POST /api/transfers` accepts 3 new optional fields:
  `cod_gross_collected`, `shipping_fee_deducted`,
  `shipping_fee_settles_against` (`shipping_payable` default | `expense`).
- Validates the math: `gross − fee == amount` (±0.01).
- Atomic writes when the math holds:
  - OUT from COD = **gross** (full cash the courier collected)
  - IN to bank = **net** (what actually arrived)
  - Fee leg:
    - `shipping_payable` → row in `shipping_payments` with
      `paid_from_account_id=null`, `settled_via_cod_withholding=true`
      → reduces the courier's outstanding shipping debt.
    - `expense` → row in `operating_daily_expenses` (no bank link).

### Files changed (3 prod + 2 new)
- `backend/transfers_routes.py` — schema + validation + 3-leg write +
  `_post_shipping_fee_leg()` helper.
- `backend/shipping_accounts.py` — normalize on save + new `/companies`
  endpoint.
- `frontend/src/pages/FinancialInputHub.jsx` — COD tab now drives from
  the new endpoint + 3 input fields with auto-calculated net + fee
  settlement selector.
- `backend/tests/test_cod_net_and_dedupe_iter98.py` — NEW, 6 tests.
- `backend/scripts/migrate_shipping_company_names.py` — NEW.

### Verified
- 6/6 pytest PASS.
- Live: 10,000 gross − 2,000 fee = 8,000 net flows correctly across
  COD account (−10,000) + bank (+8,000) + shipping_payable settlement
  (−2,000 debt). Net position unchanged ✓.

---

## ✅ ITERATION 97 — Financial Input Hub (one-stop data entry)

### Scope
Single new page `/financial-input-hub` consolidating 7 daily operations
into one tabbed UI. Plus 2 new `liabilities` kinds (`supplier`,
`receivable`) so the existing collection covers every flow the
merchant listed — no new collections, no extra screens beyond the hub.

### Files changed (4)
- `backend/liabilities_routes.py` — extended `LIABILITY_KINDS` with
  `supplier` and `receivable`; `LiabilityCreate` accepts them with the
  appropriate metadata (`supplier_name`, `counterparty_name`,
  `counterparty_type`); `/summary` aggregates `suppliers_unpaid` (under
  liabilities) and `receivables` (under assets — current receivables).
- `frontend/src/pages/FinancialInputHub.jsx` — NEW (~570 LOC). 7 tabs
  reusing existing endpoints:
    1. التزام جديد            → POST /api/liabilities
    2. سداد التزام            → POST /api/liabilities/{id}/pay
    3. مصروف يومي             → POST /api/operating-expenses/daily
    4. مديونية على الغير      → POST /api/liabilities (kind=receivable)
    5. سلفة موظف              → POST /api/liabilities (kind=salary_advance)
    6. دفعة شركة شحن          → POST /api/shipping-accounts/{co}/payments
    7. تحويل COD              → POST /api/transfers (with shipping_company)
- `frontend/src/App.js` + `frontend/src/components/Sidebar.jsx` —
  +route + nav entry "مركز الإدخال المالي" (testid `nav-financial-input-hub`).
- `backend/tests/test_liabilities_supplier_receivable_iter97.py` —
  NEW, 5 tests, all PASS.

### Verified
- 5/5 pytest PASS (supplier + receivable + summary math + guards).
- UI: all 7 tab testids present; navigation entry present; forms
  render correctly in RTL Arabic.
- Live screenshot confirms tabs + form layout on Preview.

### What the merchant gains
- مدخل بيانات واحد بدلاً من التنقّل بين 4 صفحات.
- المديونيات على الغير (receivables) أصبحت تظهر كأصل مستحق التحصيل
  في `/api/liabilities/summary` وفي شاشة المركز المالي تلقائياً.
- موردون عامون (مطبعة، تغليف، خدمات) لهم تصنيف رسمي.

---

## ✅ ITERATION 96 — Tag COD → Bank transfers with the shipping company

### Scope
Capture which courier (سمسا / أيميل / مندوب الرياض / Aramex / …) remitted
the cash when transferring out of the COD bucket. No new collection.

### Files changed (3)
- `backend/transfers_routes.py` — `TransferIn` gains optional
  `shipping_company`. The field is persisted **only** when the source
  account's `normalized_payment_method == "cash_on_delivery"`. Stored
  on the `transfers` envelope and on both linked
  `account_transactions` rows (out + in) for full traceability.
- `frontend/src/pages/Transfers.jsx` — conditional amber section that
  appears only when source is COD, with an Arabic carrier datalist
  (سمسا / أيميل / مندوب الرياض / Aramex / SPL / DHL / J&T) +
  client-side required check + 🚚 chip under the source column in the
  list table.
- `backend/tests/test_cod_transfer_shipping_tag_iter96.py` — NEW,
  4 tests, all PASS.

### Verified
- 4/4 pytest PASS.
- For non-COD sources the field is silently dropped (ledger semantics
  preserved).
- For COD sources, list view shows by-courier amounts directly:
  `{ سمسا: 1000, أيميل: 2000, مندوب الرياض: 500 }`.

### What the merchant gains
"كم حوّلت سمسا هذا الشهر؟" is now answerable by filtering
`/api/transfers` rows where `shipping_company == "سمسا"`. No double
entry — the same transfer doc carries the tag.

---

## ✅ ITERATION 95 — Shipping payments linked to bank accounts (F2 fix)

### Scope
Same pattern as Iter-94 (F1) applied to shipping company deferred debts.

### Files changed (3)
- `backend/accounts_routes.py` — +`shipping_debt_payment` to
  `TRANSACTION_TYPES` and label catalogue.
- `backend/shipping_accounts.py` — `PaymentIn` gains optional
  `paid_from_account_id`; POST/DELETE keep an `account_transactions`
  row in sync via 3 new helpers (`_post_shipping_payment_tx`,
  `_delete_shipping_payment_tx`,
  `_recompute_shipping_account_balance`).
- `frontend/src/pages/ShippingAccounts.jsx` — modal gains bank
  selector + amber warning banner when no account chosen + dynamic
  success/warning toast.
- `backend/tests/test_shipping_payments_bank_link_iter95.py` — NEW,
  6 tests, all PASS.

### Posted bank movement schema
```
{
  transaction_type: "shipping_debt_payment",
  direction: "out",
  amount: <payment.amount>,
  description: "سداد مستحقات شركة الشحن — <name> (فاتورة <inv>)",
  reference: <invoice_number>,
  peer_shipping_payment_id: <shipping_payment.id>,
}
```

### Behaviour
- With bank: payment recorded + bank debited + financial position
  reflects the drop. Success toast confirms the deduction.
- Without bank: payment still recorded (paper-only). Warning toast
  shows: "تم تسجيل الدفعة بدون ربطها بحساب بنكي، لذلك لن تؤثر على
  رصيد البنك." The modal also shows an amber inline banner.
- Delete: rolls back the linked tx and restores the bank balance.

### Verified
- 6/6 pytest PASS.
- Live curl: bank 10,000 → 9,250 after 750 SAR linked payment;
  delete restores to 10,000.
- Financial-position summary deduction = exact payment amount.

---

## ✅ ITERATION 94 — Daily expenses linked to bank accounts (F1 fix)

### Scope (minimum-change F1 closure)
- Daily operating expenses now accept `paid_from_account_id`. When set,
  the system auto-posts an `account_transactions` row (type=expense,
  direction=out) so the bank balance, accounts page, and
  financial-position screen all stay in sync.
- Backward-compatible: a daily expense without an account remains a
  cash entry (no bank impact) — existing rows untouched.

### Files changed (4)
- `backend/expenses_routes.py` — +2 fields on schemas, +2 helpers
  (`_post_daily_expense_tx`, `_delete_daily_expense_tx`,
  `_recompute_account_balance_for_expense`), POST/PUT/DELETE handle
  the linked tx in lock-step. Update detects explicit null via
  `__fields_set__` to support unlinking.
- `frontend/src/pages/OperatingExpenses.jsx` — +accounts fetch,
  +select in `DailyFormFields`, +column in `DailyPanel` table.
- `backend/tests/test_daily_expenses_bank_link_iter94.py` — NEW, 8 tests.

### Behaviour
| Action | Bank balance impact | account_transactions row |
|---|---|---|
| Create cash daily expense (no account) | none | none |
| Create linked daily expense | −amount | inserted (type=expense) |
| Update amount/date/type | reposted | old removed, new inserted |
| Switch from bank A → bank B | A +restored, B −amount | old removed from A, new on B |
| Unlink (set null) | restored | removed |
| Delete | restored | removed |

### Verified
- 8/8 pytest PASS on Preview.
- Live curl: 40,000 → 39,850 after creating 150 SAR expense → 40,000
  after deleting → exact penny-perfect rollback.
- UI: account selector with live balances + cash fallback + hint.

### F1 closed
The financial position screen now correctly reflects daily expenses
paid from bank accounts (assets total drops by exactly the amount).
The cash-payment path remains supported for paper-only expenses.

---

## ✅ ITERATION 93 — Financial Position screen (Feb 2026)

### Scope
Read-only frontend page that aggregates existing endpoints. Zero new
collections, zero new logic, zero new calculations.

### Files added/edited
- `frontend/src/pages/FinancialPosition.jsx` — NEW (~270 LOC).
- `frontend/src/App.js` — +import +route `/financial-position`.
- `frontend/src/components/Sidebar.jsx` — +nav entry under
  "العمليات المالية" between `/reconciliation` and
  `/payment-settlements`.

### Data sources (existing endpoints only)
- `GET /api/liabilities/summary` → assets, liabilities, net_position,
  ad_provider breakdown, overdue_total.
- `GET /api/reconciliation/summary` → collection_rate, total pending,
  total expected, total transferred.
- `GET /api/liabilities?status=unpaid|partial` (count-only) → open
  liabilities count.

### KPIs shown
1. Net position banner (assets − liabilities = net).
2. Assets (banks / payment platforms / total).
3. Liabilities (salaries / ad accounts with by-provider hint / total).
4. Quick indicators (collection rate / pending / open count + overdue
   hint).
5. Quick links to Accounts, Reconciliation, Operating Expenses pages.

### Verified
- All 12 data-testids render on Preview.
- Numbers match `/api/liabilities/summary` exactly:
  net 432,214.24 = assets 455,314.24 − liabilities 23,100 (Preview env).
- No new backend code → no regression.

---

## ✅ ITERATION 92 — Liabilities Center Phase 1 (Feb 2026)

### Scope
- Single new collection `liabilities` modelling 3 obligation kinds:
  `salary`, `ad_account`, `salary_advance`.
- Reuses `operating_salaries`, `accounts`, `account_transactions`.
- No frontend, no extra pages, no prepaid/subscription/insurance support
  (deferred per merchant request).

### Endpoints (`/api/liabilities`)
- `POST /generate-salaries[?period=YYYY-MM]` — idempotent monthly generator.
- `POST /` — manual create (ad_account bill or salary_advance).
- `GET /` — list with filters (kind, status, ad_provider, period_key,
  employee_salary_id, from_due, to_due, pagination).
- `GET /summary` — Assets − Liabilities = Net financial position.
- `PUT /{id}` — edit expected/due_date/notes/description/label.
- `POST /{id}/pay` — record a payment, posts an `account_transactions`
  row, updates the bank balance.
- `DELETE /{id}` — safe delete (paid_amount must be 0 except for advances).

### Key invariants
- Idempotent salary generation via partial unique index
  `(user_id, kind="salary", employee_salary_id, period_key)`.
- Advances are recorded with `paid_amount = expected_amount` (cash left
  the bank already) and `advance_status: open|fully_consumed`.
- When a new salary obligation is generated for an employee, any open
  advances for that employee are auto-deducted (treated as pre-paid on
  the salary).
- Every `pay()` creates an `account_transactions` row of type
  `debt_payment` with `peer_liability_id` for traceability.

### Net Position formula
```
assets       = banks(current_balance) + payment_platforms(expected_orders_balance)
liabilities  = SUM(remaining_amount) over kind in {salary, ad_account}
                AND status != paid
net_position = assets - liabilities
```

### Files changed
- `backend/liabilities_routes.py` — NEW (450 LOC).
- `backend/server.py` — import + attach + index setup.
- `backend/tests/test_liabilities_iter92.py` — NEW (17 tests, all PASS).

### Verified
- 17/17 pytest PASS.
- Live test on merchant `amasi.jewelery@gmail.com`:
  - Bank: 40,000 SAR + Platforms: 415,314.24 SAR = **Assets 455,314.24 SAR**
  - Generated 7 salary obligations totalling 23,100 SAR
  - **Net position: 432,214.24 SAR** ✓

---

## ✅ ITERATION 91 — Refund-Aware COGS + Order-Adjustments Audit (Feb 2026)

See prior PRD entries.

---

## ✅ Previous Iterations (Iter-81 → Iter-90)

See git history + earlier PRD versions.

---

## Backlog

### P0 — Active
- None.

### P1 — Deferred (user paused)
- Smart Settlement Alerts UI Phase C/D (Iter-90).
- Reconciliation/SettlementHealth diagnostic report.

### P2 — Liabilities Phase 2 (future)
- Prepaid expenses with amortization (insurances, licenses, subscriptions).
- Subscription cycles (recurring liabilities for hosting/SaaS).
- Auto-fetch ad spend from Snap/TikTok/Meta APIs (currently manual).
- Liabilities UI page (currently API-only).

### P3
- Refactor `frontend/src/pages/Reports.jsx`.

---

## Key Files Reference
- `backend/server.py` — main FastAPI app
- `backend/liabilities_routes.py` — Iter-92 Liabilities Center
- `backend/order_status_policy.py` — policy + effective_product_cost
- `backend/payment_gateway_metrics.py` — single source of truth
- `backend/reconciliation_routes.py` — reconciliation summary
- `backend/orders_explorer_routes.py` — /orders + /order-adjustments
- `backend/salla_integration/sync.py` — Salla API resync + diff
- `backend/product_costs.py` — product cost catalog + recompute
- `backend/accounts_routes.py` — payment platform account sync
- `backend/expenses_routes.py` — operating_salaries / rentals / prepaids

## Test Credentials
See `/app/memory/test_credentials.md`.
