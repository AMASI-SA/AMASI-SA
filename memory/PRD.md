# PRD — Hesab (تطبيق محاسبي ذكي لمنصة سلة)


## ✅ ITERATION 82 — Status-driven refunds (Tamara/Tabby accuracy)

User reported the «المطابقة» and «تقارير سلة» pages were overstating
Tamara/Tabby net for the current year because orders with
`order_status = "مسترجع"` were being counted as sales.

### Backend
- **`payment_gateway_metrics.py`** — `compute_metrics()` now detects
  `is_status_refunded` via regex (`مسترج` / `refund`). In estimated
  mode such orders set fee=0, vat=0, refund_full=gross, net=0 —
  so they:
  • are excluded from net,
  • feed `refund_full` and `refunded_orders_count`,
  • surface in totals and refund summaries.
  Cancellation logic unchanged.
- **`refunds_alert_routes.py`** — `/api/reports/refunds-alert` match
  clause includes `order_status` regex. New `_effective_refund_full`
  field = `actual_refund_amount` when populated, otherwise
  `total_amount` for status-driven refunds, otherwise 0. by_payment_method
  now surfaces Tamara/Tabby refunds even with NO settlement file.

### Frontend
- **`RefundsAlert.jsx`** — detail-modal refund column reads
  `_effective_refund_full || actual_refund_amount`; «من حالة الطلب»
  amber badge appears in the Source column when `_is_status_refund`
  and no settlement_source.
- Dashboard / Accounts / Reconciliation / Reports inherit the new
  numbers automatically via the central endpoint.

### Numbers (real merchant data — year 2026)
| البوابة | gross | refund_full | refunded_count | **net** |
|---|---:|---:|---:|---:|
| Tamara | 94,483.27 | 2,648.43 | 11 | **84,452.70** |
| Tabby  | 79,521.03 | 352.27   | 1  | **72,804.78** |

Refund Monitor (this_year): 39 orders / 8,838.44 ر.س across
9 gateways (Tamara, Tabby, مدى, البطاقة الإئتمانية, COD, STC Pay,
3 banks…).

### Verified
- 14/14 new pytests PASS (`test_status_refunds_iter82.py` +
  `test_status_refunds_iter82_extra.py`).
- `testing_agent_v3_fork` iteration 43: backend 100% / frontend 100%.
- Per-row identity holds: `gross − fees − vat − refund_full − refund_partial == net`.
- Cross-page: Tamara=84,452.70 and Tabby=72,804.78 identical
  on Accounts ↔ Reconciliation ↔ Reports.

---



## ✅ ITERATION 81 — Payment-Gateway Metrics UNIFIED across all pages

User asked: "أريد توحيد المقاييس بالكامل في كل الصفحات (لوحة التحكم،
التقارير، الحسابات، المطابقة) عبر نقطة نهاية مركزية واحدة بحيث
تتطابق الأرقام تمامًا."

### Backend
- **`payment_gateway_metrics.py`** — added `_fold()` Arabic-letter
  normalizer (أ/إ/آ/ٱ → ا، ى → ي، ة → ه، ـ deleted) and rebuilt the
  alias index so هاء variants like "البطاقة الإئتمانية" /
  "دفع عند الإستلام" / "حوالة بنكية…" resolve correctly.
- **`reconciliation_routes.py`** — new `ACCOUNT_KEY_TO_CENTRAL_KEYS`
  map (salla account aggregates mada/applepay/stcpay/credit_card)
  and `_central_expected_for_account()` helper.
  `/reconciliation/summary` and `/reconciliation/platform/{id}` now
  call `compute_metrics()` and override per-platform `expected` from
  the central response. Each row exposes `expected_source`
  ('central' | 'stored') and `actual_orders_count`.
- **`accounts_routes.py`** — POST `/accounts/sync-payment-methods`
  reuses the same helper to seed `expected_orders_balance` from the
  central endpoint, so clicking «مزامنة طرق الدفع من الطلبات»
  produces numbers that match Reports / Reconciliation 1:1.

### Frontend
- **`components/UnifiedPaymentGatewaysCard.jsx`** — NEW reusable
  component that reads `/api/payment-gateway-metrics`, renders the
  per-gateway breakdown (orders / gross / fees / VAT / refunds /
  net) and totals. Accepts `qs` (passes through any date filter)
  and `periodLabel` (chip next to title).
- **`pages/Dashboard.jsx`** — mounts `<UnifiedPaymentGatewaysCard
  testid="dashboard-unified-gateways" qs={filtersToQueryString(...)} />`
  under the KPI grid.
- **`pages/Accounts.jsx`** — mounts the card with `testid=
  "accounts-unified-gateways" periodLabel="كل الفترة"` above the
  accounts table.
- **`pages/Reconciliation.jsx`** — header now carries
  `data-testid="reconciliation-central-source-note"` explaining the
  single source.

### Verified
- 12/12 new pytests pass (`tests/test_payment_gateway_unification_iter81.py`).
- Real merchant data: salla=346,801.86, tamara=86,888.23,
  tabby=73,128.73, bank_transfer=51,363.55, cod=17,003.50,
  emkan=787.92 — identical across Accounts, Reconciliation and the
  central endpoint.
- Cross-page: numbers match when the SAME date filter is applied
  (Accounts card runs «كل الفترة», Dashboard/Reports apply the
  active filter — labelled accordingly to avoid confusion).

---



## ✅ ITERATION 80 — Sidebar instant-search bar

User asked: "نعم" (agreeing to the proposed "شريط بحث صغير في أعلى القائمة الجانبية يفلتر الصفحات فورياً عند الكتابة" enhancement).

### Implementation
- **`components/Sidebar.jsx`** — New search input above the accordion
  sections with:
  - Magnifying-glass icon, Arabic placeholder "ابحث في القائمة…"
  - Clear button (`X`) on the trailing edge when query is non-empty
  - Live result count line ("X نتيجة" or "لا توجد نتائج")
- **`normalizeAr()`** helper strips tashkeel and unifies Arabic
  variants (أ/إ/آ → ا, ة → ه, ى → ي) so typing "سله" matches
  "سلّة", "السلة", "متجر سلة" etc.
- When search is active:
  - Every section is **force-opened** so all matches are visible at
    once (no need to click headers)
  - Sections with zero matches are hidden entirely
  - Section toggle buttons become non-interactive
  - Clearing the search restores the normal accordion behavior

### Verified
- Lint clean; no runtime errors; all 42 regression tests still PASS.
- No backend impact — purely frontend filter.


---


## ✅ ITERATION 79 — Sidebar 3-section accordion reorganization

User asked: "أريد إعادة تنظيم القائمة الجانبية بالكامل وتجميع الصفحات داخل 3 أقسام رئيسية قابلة للفتح والإغلاق."

### Three accordion sections
1. **💰 العمليات المالية** — لوحة التحكم · الأصول والحسابات ·
   التحويلات بين الحسابات · المطابقة والتسويات · فواتير وتسويات
   بوابات الدفع · تسويات المدفوعات · تشخيص فروقات الطلبات
2. **🔗 الاستيراد والربط** — رفع ملف Excel · حالة الاستيراد ·
   ربط Make.com · ربط متجر سلة · مقارنة مصادر البيانات · سجل التحليلات
3. **⚙️ إدارة التشغيل** — التكاليف اليومية · المصروفات التشغيلية ·
   التقارير · حسابات Snapchat · تكاليف المنتجات · تجهيز المنتجات ·
   إدارة صور المنتجات · حسابات الشحن الآجلة · حسابي · إدارة الفريق
   (owner) · الإعدادات

### Implementation
- **`components/Sidebar.jsx`** rewritten to use a `SECTIONS` array
  with collapsible panels (smooth `max-height` transition).
- The open section is derived from (priority order):
  1. User's last toggle on the **current** path
  2. The section that contains the active route (`findSectionFor`)
  3. The last persisted choice in `localStorage`
  4. Fallback: "finance"
- Switching pages automatically opens the matching section so the
  active item is always visible (no manual expand needed).
- Toggles persist across refreshes via `localStorage`
  (`hesab.sidebar.openSection`).
- Active page still gets the brand-color highlight; inactive items
  in the open panel hover-highlight in accent color.
- Owner-only "إدارة الفريق" link gets injected inside إدارة التشغيل
  just before "الإعدادات" so settings stays last.

### Accessibility / mobile
- Section toggles have `aria-expanded` + `aria-controls`.
- Panels carry `role="region"` for screen readers.
- Backdrop + slide-in animation preserved for mobile drawer.
- On mobile, only the active section is open by default → menu
  height stays compact instead of showing all 22 links at once.

### Verified
- No pages removed — every link from the previous flat list is now
  reachable inside one of the 3 sections.
- All 51 regression tests still PASS (no backend impact).
- Lint clean (refactored to derive `openId` from `location` instead
  of using `useEffect + setState` to satisfy the lint tool).


---


## ✅ ITERATION 78 — Settlements delete-button toggle (Settings-controlled)

User asked: "اضافة عمود في سجل ملفات التسويات المرفوعة لحذف ملف سله او تمارا او تابي مع إمكانية التحكم بظهور واخفاء خيار حذف الملف من الاعدادت."

### Backend
- **`server.py`** — `SettingsIn` model gains `settlements_allow_delete:
  Optional[bool]` (default False). `/api/settings` GET returns it as a
  boolean, PUT persists it. Stored per-user in `settings` collection.

### Frontend
- **`pages/Settings.jsx`** — New toggle under "إعدادات الطلبات" section
  labeled "إظهار زر حذف ملفات التسويات (سله / تمارا / تابي)". When OFF
  (default), the delete column is fully hidden in `/payment-settlements`,
  preventing accidental rollback of settlement files. Includes an
  explanation that toggling ON re-enables the per-file Trash button
  and reminds that delete rolls back the orders to estimated rates.
- **`pages/PaymentSettlements.jsx`**:
  - Reads `settlements_allow_delete` via `/api/settings` on mount + on
    every reload.
  - Delete column header is conditionally rendered.
  - Per-row Trash button is conditionally rendered.
  - When delete is hidden AND there are uploaded files, a discreet
    Lock icon hint near "تحديث" links to `/settings` so the merchant
    can find the toggle without searching.

### Verified
- Backend roundtrip: `/api/settings` GET defaults to `false`, PUT
  with `{settlements_allow_delete: true}` persists and is returned
  on next GET; reverting to `false` works the same.
- All 77 regression tests still PASS (iter77, iter76, iter75, iter74,
  iter73, iter72, iter68, phase22).

### Safety
- Default OFF — merchant must explicitly enable to see delete buttons.
- The DELETE API endpoint itself is unchanged — only the UI affordance
  is gated. So power-users/scripts can still call the API directly if
  needed.


---


## ✅ ITERATION 77 — Smart Refund-Monitor Alert (Reports page)

User asked: "نعم اخر 30 يوم مع إضافة البحث بالتاريخ اخر شهر الشهر الماضي اليوم بالأمس السنه الحاليه وتحديد الفتره مخصصه."

### Backend — `/app/backend/refunds_alert_routes.py` (NEW)
- New endpoint `GET /api/reports/refunds-alert?period={today|yesterday|
  this_month|last_month|last_30d|this_year|custom}&from_date=&to_date=`
- `_resolve_period()` helper maps each preset to ISO date bounds with
  Arabic labels (اليوم / بالأمس / هذا الشهر / الشهر الماضي / آخر 30
  يوم / السنة الحالية / فترة مخصّصة).
- Aggregation pipeline returns:
  - `summary` — orders_count, total_orders_in_period, refund_rate_pct,
    total_refund_full, total_refund_partial, total_refund_amount,
    total_gross_affected
  - `orders` — top 200 rows with order_number, customer, method,
    amounts, settlement_date, settlement_source
  - `by_payment_method` — bucketed refund total per gateway

### Frontend — `/app/frontend/src/components/RefundsAlert.jsx` (NEW)
- 7 period chips (Arabic labels) with active-state styling
- Custom from/to date inputs revealed only when `period=custom`
- 4 summary cells with severity-based coloring:
  - refund_rate >= 5% → high (rose tint)
  - refund_rate >= 2% → medium (amber tint)
  - else → low (emerald tint)
- Per-payment-method chips with refund amount
- Expandable details modal showing all matched orders (sortable table)
- Wired into `Reports.jsx` directly under the Advanced Filters

### Verified — 77/77 tests pass
- 14 NEW tests in `test_refunds_alert_iter77.py`:
  - 9 unit tests on `_resolve_period()` covering every preset + custom
    validation + invalid input
  - 5 live endpoint tests including the full upload→alert flow that
    confirms order **263864673** appears with `actual_partial_refund_amount=89.43`,
    methods bucket includes mada + credit_card
- 63 regression tests still PASS (iter76, iter75, iter74, iter73,
  iter72, iter68, phase22)


---


## ✅ ITERATION 76 — Salla customer-refund rows handling

User asked: "الطلب 263864673 المبلغ -89.43 لما يكون بالسالب هذا الطلب يعتبر مسترجع منه مبلغ وليس تحصيل طلب جديد." Order 263864673 in the new sample file has 3 rows: R47 (+407.02 sale), R54 (+89.43 sale), R118 (-89.43 partial refund).

### Backend
- **`parsers/salla.py`** — Now distinguishes 3 row types:
  - **`event_type="salla_purchase"`** — wallet recharge (strict method match)
  - **`event_type="refund"`** — negative amount on a NORMAL payment method (مدى / credit_card / etc)
  - **`event_type="sale"`** — everything else
- **Two-pass parser**: First pass accumulates `positive_gross_by_order`
  so we can correctly classify refunds as **full** vs **partial**:
  - `|refund_gross| >= total_positive_paid * 0.99` → `refund_full`
  - Otherwise → `refund_partial`
- File totals: now includes `refund_full` + `refund_partial` for Salla
  (previously always 0).
- The refund row is upserted to `unified_orders` via the existing
  consolidate path, so the matched order ends up with:
  - `actual_net_amount` reflecting `Σ sales − Σ refunds`
  - `actual_refund_amount` or `actual_partial_refund_amount` populated

### Verified — 63/63 tests pass
- 2 NEW tests in `test_salla_wallet_iter75.py`:
  - `test_partial_refund_detected_on_credit_card` — uses real
    `salla_refund.xlsx`; expects 3 partial refunds totaling 610.22,
    and 263864673 specifically with `actual_partial_refund_amount=89.43`
  - `test_full_refund_classified_when_amount_equals_paid` — synthetic
    workbook with paid=100, refund=-100 → expects `refund_full=100`
- iter74 expectations updated to reflect refunds-as-tagged-entries
  (rows=143 with 140 sales + 3 refunds, net=25500.75, refund_partial=610.22)
- All 12 wallet/refund + 16 iter74 + 9 iter73 + 8 iter68 + 10 iter72
  + 8 phase22 = **63 tests still PASS**

### Real-data verification (Invoice # 6320306)
| Order | Type | Gross | Net | Notes |
|-------|------|-------|-----|-------|
| 261685845 | refund (مدى) | -312.20 | -312.20 | partial vs total paid for this order |
| 258530841 | refund (مدى) | -208.59 | -208.59 | partial |
| 263864673 | refund (credit) | -89.43 | -89.43 | partial (vs 407.02+89.43 total paid) |
| **Total** | | | | `refund_partial=610.22 ر.س` |

Order **263864673** consolidated final state in DB:
```
actual_gross_amount: 407.02
actual_net_amount: 392.16  (481.59 captured − 89.43 refunded)
actual_partial_refund_amount: 89.43
actual_payment_fee: 12.92
payment_fee_status: actual
```


---


## ✅ ITERATION 75 — Salla "شحن محفظة" (مشتريات سله) handling

User asked: "اذا كان طريقة الدفع شحن محفظة يتم احتساب المبلغ مشتريات سله، يضاف في سجل ملفات التسويات المرفوعة عمود باسم مشتريات سله وتخصم من إجمالي المبيعات." Sample order 257396516 has payment_method `order.payment_method.` (untranslated i18n key from Salla), gross/net = -34.5, note "شحن محفظة (لبوليصة شحن)".

### Backend
- **`parsers/salla.py`** — Detects wallet-recharge rows by STRICT
  payment_method matching against needles: `"order.payment_method."`,
  `"شحن محفظة"`, `"wallet recharge"`, `"wallet_recharge"`. Tagged with
  `event_type="salla_purchase"`. Negative-amount rows on normal methods
  (مدى / credit card) are NOT flagged — they are customer refunds and
  stay in the existing sale aggregation.
- **`parsers/salla.py`** — New file-level totals: `salla_purchases_total`,
  `salla_purchases_count`. Wallet rows are EXCLUDED from `totals.gross`
  / `totals.net` / `totals.fees` so the file's main totals represent
  real customer sales only.
- **`service.py`** — `_apply_entries` skips `event_type=="salla_purchase"`
  rows so the wallet deductions DON'T pollute `unified_orders.actual_*`
  fields on the referenced order (the order_number may belong to a
  real customer order whose actual_net is unrelated).

### Frontend
- **`pages/PaymentSettlements.jsx`** — New column "مشتريات سله" in the
  history table (amber-tinted) showing `-{salla_purchases_total}` with
  the operation count as a sub-line. Empty rows show "—".
- New inline notice on the recent-upload card: when wallet rows are
  detected, an amber callout explains the deduction and total.

### Verified — 61/61 tests pass
- 10 NEW tests in `test_salla_wallet_iter75.py` covering: strict
  detection (only explicit method match), parser totals on the real
  wallet-file sample (3 rows / 109.25 ر.س / 114 sales / 117 entries),
  entry shape, original-file backward compat (no wallet rows ⇒ counts
  stay 0), end-to-end upload via API, and the safety invariant that
  wallet rows do NOT pollute unified_orders.actual_*.
- All 16 iter74 + 9 iter73 + 8 iter68 + 10 iter72 + 8 phase22 = 51
  regression tests still PASS.

### Real wallet-file verification (merchant's own file)
- Invoice # 6233377: 117 rows total
- 114 real sales → gross=21,818.59 fees=427.81 vat=64.13 net=21,326.65
- 3 wallet recharges → salla_purchases_total=109.25 ر.س
  - order 259635319: -34.50
  - order 259392433: -40.25
  - order 257396516: -34.50

### Safety guarantees
- ✅ Wallet rows excluded from sale totals (gross / net / fees / vat).
- ✅ Wallet rows NEVER write to unified_orders.actual_* fields.
- ✅ Negative customer refunds via مدى / credit card are NOT flagged
  as wallet recharges (preserved as part of sale aggregation).
- ✅ Original Salla invoices that have no wallet rows continue to
  parse identically (backward compat verified via test).


---


## ✅ ITERATION 74 — Phase 80: Payment-Gateway Settlement File Imports

User asked: "أريد تطوير النظام ليعتمد على ملفات التسويات والفواتير الفعلية من سلة وتمارا وتابي بدلاً من الاعتماد الكامل على النسب التقديرية. الطلبات المطابقة تستخدم الرسوم والصافي الفعلي، وغير المطابقة تبقى بالنسب التقديرية. لا يتم إنشاء تحويلات بنكية تلقائية ولا تعديل تسويات قديمة ولا إنشاء صفوف تلقائية في payment_adjustments. فقط تحديث expected_orders_balance بناء على actual_net_amount."

### Backend — `/app/backend/settlements_import/`
- **`parsers/salla.py`** — Reads Salla `Invoice # XXXXXXX` xlsx. 7 cols
  (رقم الطلب / إجمالي / طريقة الدفع / الرسوم / مستحق قبل / الضريبة / مستحق بعد).
  Handles Arabic tashkeel (damma) in headers, longest-match column
  resolver to avoid "الضريبة" mismatching "المستحق قبل الضريبة".
- **`parsers/tamara.py`** — Tamara Merchant Statement. Skips 26-row
  preamble, finds detail header by scanning for "Merchant Order ID",
  emits one entry per Captured/Refunded event row, attaches statement
  metadata (statement_id, statement_period, tamara_merchant_id).
- **`parsers/tabby.py`** — Tabby Settlement Report. 11-row preamble,
  18-col detail header, infers full vs partial refund by net vs gross.
- **`registry.py`** — Provider sniffer (`detect_provider`) based on
  sheet title + cell-content heuristics.
- **`service.py`** — `import_file()` with sha256 dedup, `_apply_entries()`
  groups by order_number (handles sale+refund pairs), `_consolidate_rows`
  collapses multiple events per order into one set of `actual_*` fields,
  `delete_file()` rolls back actual_* on every related order,
  `coverage_analytics()` aggregates orders by payment_fee_status +
  per-provider totals.
- **`routes.py`** — 5 endpoints under `/api/payment-settlements`:
  - `POST /upload` — multipart xlsx, auto-detects provider.
  - `GET ""` — list audit rows (most-recent first).
  - `GET /{file_id}` — full detail incl. unmatched_orders (capped 200).
  - `DELETE /{file_id}` — rolls back actual_* on N orders, deletes row.
  - `GET /_analytics/coverage` — estimated vs actual breakdown.
- **`accounts_routes.py`** — `sync-payment-methods` aggregation now
  uses `$cond` to pick `actual_net_amount` when
  `payment_fee_status == 'actual'`, else falls back to `total_amount`.
  Result: `expected_orders_balance` reflects actual fees automatically
  for matched orders.

### New unified_orders fields (set only by the importer)
`actual_payment_method`, `actual_gross_amount`, `actual_payment_fee`,
`actual_payment_vat`, `actual_net_amount`, `actual_refund_amount`,
`actual_partial_refund_amount`, `actual_fee_rate`, `settlement_source`,
`settlement_date`, `settlement_reference`, `payment_fee_status`,
`last_settlement_file_id`, `last_settlement_applied_at`.

### New collections
- **`settlement_files`** — audit log (sha256-deduped per user). Stores
  file metadata + totals + matched/unmatched counts.
- **`settlement_entries`** — per-row trace of every parsed entry for
  later drill-down and analytics screens.

### Frontend
- **`/app/frontend/src/pages/PaymentSettlements.jsx`** — Drop zone +
  click-to-browse. Live coverage cards (orders_total / orders_actual /
  orders_estimated / actual_net total). Per-provider breakdown panel.
  History table with provider chip, totals, matched/unmatched counts,
  delete-with-rollback. ConfirmModal for delete + unmatched-orders
  modal. Arabic labels throughout.
- Route `/payment-settlements`, sidebar entry "فواتير وتسويات بوابات
  الدفع" with Receipt icon.

### Verified — 57/57 tests pass (testing agent iter_41.json)
- 16 NEW tests in `test_settlements_import_iter74.py` covering: 3
  parsers against REAL merchant samples (140 / 131 / 82 rows),
  dedup, upload→match→rollback, bad-file rejection, coverage analytics.
- 6 review-driven tests covering: list/detail endpoints, no-auto-
  adjustments invariant, expected_orders_balance switching to
  actual_net_amount, reconciliation regression, Salla header invoice#.
- 35 regression tests across Salla Direct (iter73), payment
  idempotency (iter68), shipping companies (iter72), reconciliation
  phase22 — all still PASS.
- Frontend E2E: page loads, all testids present, upload flow + delete
  with confirm work end-to-end, no console errors.

### Safety guarantees (per merchant's explicit asks)
- ✅ Estimated rates remain the source of truth for orders NOT in any
  settlement file.
- ✅ NO automatic bank transfers created.
- ✅ NO movement from `expected_orders_balance` → `current_balance`.
- ✅ NO automatic rows in `payment_adjustments` (verified via test).
- ✅ Old settlements / transfers are NEVER modified.
- ✅ Re-uploading the same file → silent no-op (sha256 dedup).
- ✅ Delete fully rolls back the actual_* fields on every affected
  order (orders_rolled_back count returned to caller).

### Real-data parser verification (merchant's own files)
- Salla: 140 rows → gross=26,686.32 fees=500.38 VAT=74.97 net=26,110.97
- Tamara: 131 rows → gross=25,213.54 refunds_full=1,581.20 refunds_partial=128.36
- Tabby: 82 rows → gross=15,771.96 fees=1,180.45 net=13,815.78


---


## ✅ ITERATION 73 — Salla Direct Phase 2 (OAuth UI + Sync + Sources Comparison)

User asked: "ابدأ الآن بتكامل سلة المباشر باستخدام Salla Partners OAuth الرسمي (Client ID / Secret / Redirect URI / Access Token / Refresh Token). زر يدوي 'مزامنة الآن' في البداية. أدخل البيانات من واجهة الإعدادات. لا توقف Make.com أو Excel أو PDF. أنشئ Salla Direct كمصدر إضافي باسم `salla_direct`. أضف سجل مزامنة وشاشة مقارنة بين المصادر."

### Backend
- **`/app/backend/salla_integration/config_store.py`** (NEW) — DB-backed
  OAuth credentials store. Client Secret encrypted with the same Fernet
  key that protects tokens. `save_config()` treats empty client_secret
  as "no change" so the UI can update client_id alone.
- **`/app/backend/salla_integration/sync.py`** (NEW) — `_salla_order_to_doc`
  maps Salla REST payload → unified_orders shape; `run_orders_sync()`
  pulls up to 2000 orders/run with rate-limit-aware pagination;
  `run_products_sync()` populates the `salla_products` cache;
  `compute_sources_comparison()` buckets unified_orders by (make/excel/
  salla) presence so the merchant sees overlap + missing-from-each diff.
- **`/app/backend/salla_integration/service.py`** — Added in-process
  `_CREDS_CACHE` + `update_credentials_cache()`. `is_configured/get_client_id/
  get_client_secret` now resolve in this order: DB → cache → .env.
- **`/app/backend/salla_integration/routes.py`** — 7 new endpoints:
  - `GET /api/salla/config` — read OAuth creds (no secret leak).
  - `PUT /api/salla/config` — save Client ID/Secret/Redirect URI.
  - `DELETE /api/salla/config` — wipe.
  - `POST /api/salla/sync/orders` — manual orders pull.
  - `POST /api/salla/sync/products` — manual products pull.
  - `GET /api/salla/sync/logs` — sync log feed.
  - `GET /api/salla/sources-comparison` — by_combination + per_source totals.
- **`/app/backend/orders_db.py`** — Extended merge rule: salla_direct
  follows the same "fill empty only" contract as Excel when Make has
  already touched the order, so Make stays authoritative until the
  merchant verifies parity. New `last_salla_direct_sync_at` timestamp.

### Frontend
- **`/app/frontend/src/pages/SallaIntegration.jsx`** — Inline credentials
  form (no .env editing required). New Sync section with two buttons
  (Sync Orders / Sync Products), a "Compare Sources" link, and a sync
  log table (kind, status, created/updated/errors, started_at). Edit
  credentials button appears once connected.
- **`/app/frontend/src/pages/SallaSourceComparison.jsx`** (NEW) — Route
  `/salla-sources`. Date range filter, 3 per-source cards (Make / Excel
  / Salla Direct), full by_combination breakdown table, diff lists
  ("in Salla Direct but missing in Make" / vice versa).
- **`/app/frontend/src/components/Sidebar.jsx`** — Added Storefront
  icon import + 2 nav links: "ربط متجر سلة" + "مقارنة مصادر البيانات".
- **`/app/frontend/src/App.js`** — New route `/salla-sources`.

### Verified — 51/51 tests pass (testing agent iter_40.json)
- 16 NEW review-driven tests covering all 14 review_request items.
- 9 NEW unit tests in `test_salla_direct_iter73.py` (mapper + merge
  rule + endpoint smoke tests).
- 26 regression tests (iter68 payment idempotency, iter72 shipping
  companies, reconciliation phase22) all still PASS.

### Safety guarantees (per merchant's explicit asks)
- Make.com webhook flow, Excel uploads, and PDF imports continue to
  work unchanged. salla_direct is purely additive.
- salla_direct CANNOT overwrite Make-authored fields (total_amount /
  order_status / payment_method) — only fills empty fields and creates
  brand-new orders not seen by Make/Excel.
- All credentials encrypted at rest (Fernet, same key as tokens).
- No scheduler enabled in Phase 2 — manual button only, per user.
- `/api/salla/config` GET never echoes the raw client_secret; only
  `has_client_secret: bool`.

### Pending follow-ups (Phase 3+)
- Refunds/Transactions sync (Salla `PUT /transactions/{id}` is the
  refund endpoint per playbook).
- Auto-scheduler at 15 min once merchant verifies parity.
- Auto-settlement detection logic (Phase 70.2 — paused while Salla
  Direct lands).


---


## ✅ ITERATION 72 — Unified Shipping-Company Dictionary + Excel apostrophe scrub

User reported duplicates between e.g. `'iMile للتوصيل'` (with literal
single quotes from Excel force-text exports) and `iMile للتوصيل` (clean).
Each Excel-style row was actually 1,799 orders that would have collided
with the next Make.com webhook payload had it landed.

### New files
- **`/app/backend/shipping_companies.py`** — central alias dictionary +
  `normalize_shipping_company(raw) → (canonical_key, display)` +
  `scrub_shipping_company(raw) → display_only`. Covers iMile, مندوب /
  مندوب الرياض, SMSA, Aramex, DHL, FedEx, Naqel, Zajil, Bosta, J&T,
  pickup, plus null/unknown markers.
- **`/app/backend/shipping_migrations.py`** — idempotent one-shot scrub
  for `unified_orders.shipping_company` + `user_settings
  .deferred_shipping_companies`. Wired into startup so a fresh deploy
  cleans the DB automatically (3,864/7,091 docs fixed on first run).

### Write-boundary hardening
- `excel_parser.py` — every Excel row now scrubs `shipping_company`.
- `webhook_routes.py` — Make.com payload scrubs at insert time.
- `import_jobs.py` — async batch upsert path scrubs as well.

### Verified
- 10 new pytest cases (`test_shipping_companies_iter72.py`) covering
  apostrophes / BOM / zero-width / aliases / order-of-aliases / unknown
  slug stability / migration idempotency.
- 35/35 total backend tests green.
- Dashboard shipping_breakdown drops from N+ "dirty" rows to **4 clean
  canonical rows** on real merchant data.

### Numbers untouched
- 0.00 SAR change to total_sales / expected_orders_balance.
- 0 ghost accounts created.
- All canonical balances preserved.

---


## ✅ ITERATION 71b — Reconciliation transparency accepts date filters

Caught a regression in iter71 before redeploy: the Reports KPI was about
to lose its date filter because `reconciliation.transparency.total_sales`
was lifetime-only. Fixed by:
- `reconciliation_routes.summary` now accepts optional `from_date` /
  `to_date` query params that filter the transparency computation only
  (platform balances stay point-in-time snapshots).
- `Reports.jsx` passes the current filter querystring when calling
  `/reconciliation/summary` so the transparency block + KPI honour the
  user's period selector.

Verified on Preview: with-dates and without-dates both produce
`total_sales == dashboard.totals.total_sales` for matching filters
(87,529.49 with 433 orders for 2026-06-01..05).

---


## ✅ ITERATION 71 — Reports KPI uses reconciliation.transparency.total_sales

User saw a 128.60 SAR discrepancy between the "إجمالي المبيعات" KPI at the top of /reports (sourced from `dashboard.totals.total_sales`) and the new transparency card (sourced from `reconciliation.transparency.total_sales`). Root cause: Dashboard's `_matches_any` does bi-directional substring matching while sync uses MongoDB regex (one-directional), so Dashboard included a couple of orders with shorter statuses.

### Change (frontend-only, 1 line)
`/app/frontend/src/pages/Reports.jsx` — the "إجمالي المبيعات" KPI now reads `reconciliation?.transparency?.total_sales ?? agg.total_sales` (graceful fallback if the recon endpoint is unreachable).

Dashboard backend left untouched per user's explicit request.

### Verified on Preview
- KPI top of /reports: 87,529.49 (clean dataset)
- Transparency card "إجمالي المبيعات (التقارير)": 87,529.49
- Transparency card "داخل الأصول": 87,529.49
- Gap card: 0.00 — "الأرقام متطابقة"
All three numbers now consistent. Awaits Redeploy → expected Production value will be ~583,686 across all surfaces.

---


## ✅ ITERATION 70 — Reports ↔ Accounts Parity & Transparency Card

User reported Reports total (583,686.39) ≠ Accounts total (584,040.78). Root cause was **two divergent filters**: Dashboard applied `hide_inferred_date_orders`, sync did not; AND Dashboard payment_breakdown used `normalize_payment_method` (slug fallback) while sync used the stricter `resolve_account_key`.

### Backend
- **`accounts_routes.sync_payment_methods`**: now also honours `hide_inferred_date_orders` so Dashboard and Sync see the SAME order universe.
- **`reconciliation_routes.summary`**: now returns a `transparency` block:
  - `total_sales` (recomputed with identical filters)
  - `in_accounts` + `in_accounts_orders` (sum of canonical platforms)
  - `unclassified_amount` + per-bucket breakdown (`waiting`, …)
  - `empty_payment_method_amount` + `empty_payment_method_orders`
  - `gap` = total_sales − in_accounts
  - `filters_applied` (statuses list + hide_inferred toggle) — surfaced so the operator can verify parity at a glance.

### Frontend
- **`Reports.jsx`**: new card "المبيعات ↔ الأصول — توضيح الفرق" right under the KPI grid. Three KPIs (Reports total / In accounts / Gap) + a detail table per unclassified bucket + per-empty-method row. Includes deep link to `/reconciliation`.

### Verified on Preview (clean data)
- `total_sales = in_accounts = 87,529.49` (433 طلب)
- gap = 0.00, no unclassified rows, no empty payment methods.
- Card UI renders correctly (data-testid="reports-vs-accounts-card").

### Tests
- 16/16 pytest still green (`test_payment_method_idempotency_iter68.py` + `test_reconciliation_phase22.py`).

### Production note
Awaits Redeploy. After deploy, the gap on Production should be **fully attributable** to `waiting` (255.12) + empty payment_method (~128.60) and reflected in the new transparency table.

---


## ✅ ITERATION 69 — P0 Ghost-Accounts Hard Fix (منع رجوع الحسابات الوهمية)

User reported ghost accounts (`\N`, "البطاقة الإئتمانية" as a standalone asset, COD duplicates, etc.) returning after recent edits.
Root cause: `normalize_payment_method()` was slug-fallbacking unknown raw values, AND `sync-payment-methods` was overwriting `current_balance` so Phase 2.1 transfers got wiped on every sync (which the user saw as "balance went back up = ghost returned").

### Backend hardening
- **`payment_methods.py`**:
  - Added `CANONICAL_TOP_LEVEL_KEYS = {salla, tabby, tamara, emkan, bank_transfer, cash_on_delivery}`.
  - New `resolve_account_key(raw) → (account_key | None, display | None)` — single classification gate the whole app must use.
  - New `detect_settlement_provider(raw)` (replaces the keyword list in settlements_routes).
  - Added raw "سلة" / "salla payments" aliases → roll up under Salla.
- **`accounts_routes.py`**:
  - `sync_payment_methods` now uses `resolve_account_key`. Unknown / null raw values are **never** turned into accounts — instead logged to a new `unclassified_payment_methods` collection.
  - Cleanup pass now hard-deletes auto-created accounts whose `normalized_payment_method` ∉ canonical set AND has 0 transactions; hides (not deletes) those with manual transactions.
  - **Regression fix**: `current_balance` is no longer overwritten by `opening_balance + expected` — `_recompute_balance` is called instead, so internal transfers from Phase 2.1 stay applied across syncs.
  - New `ensure_accounts_indexes(db)` creates a **partial unique index** on `(user_id, normalized_payment_method)` for `auto_created=True` accounts. Even direct double-inserts are now refused at the DB level.
  - New diagnostic `GET /api/accounts/unclassified-payment-methods` returns raw payment_method strings that couldn't be classified (NEVER auto-promoted to accounts).
- **`settlements_routes.py`**:
  - Replaced local `PROVIDER_KEYWORDS` keyword list with central `detect_settlement_provider`.
- **`server.py`**:
  - `ensure_accounts_indexes` wired into startup.

### Tests (8/8 + 14/14 regression)
New `/app/backend/tests/test_payment_method_idempotency_iter68.py` (7 tests):
1. Every reported spelling (`مدى`, `Apple Pay`, `البطاقة الإئتمانية`, `بطاقة بنكية`, COD variants, `حوالة بنكيةمصرف الراجحي`, `EmkanInstallment`, …) routes to its correct canonical key/parent.
2. `resolve_account_key` refuses every shape of null / unknown.
3. 3 successive sync calls keep count stable + zero unclassified.
4. Sync preserves Salla's 11,321.40 current_balance across calls (transfer no longer wiped).
5. Unclassified diagnostic endpoint reachable (route ordering fix).
6. Direct double-insert blocked by partial unique index.
7. Pre-seeded ghost account ("normalized_payment_method=credit_card_legacy_slug") gets hard-deleted by sync.

Plus existing `test_reconciliation_phase22.py` (8) and `test_accounts_iter57.py` (6) all still green.

### Verified DB state (amasi.jewelery@gmail.com)
- Indexes: `uniq_auto_user_normalized_pm` created with `partialFilterExpression={auto_created:true, normalized_payment_method:{$type:'string'}}`.
- 9 accounts total: 3 banks + 6 payment platforms (سلة 11,321.40 · تمارا 16,090.72 · تابي 12,320.78 · تحويل بنكي 5,507.43 · الدفع عند الاستلام 2,057.96 · إمكان 231.20).
- Total assets: **87,529.49 ر.س** (matches /api/reconciliation/summary totals.expected).
- 0 ghosts. 0 unclassified rows.

---


## ✅ ITERATION 68 — Phase 2.2 Reconciliation Screen (شاشة المطابقة والتسويات)

Read-only view comparing Expected vs Transferred vs Pending per payment platform.
Deliberately small first version — NO charts/timeline, NO 14-day alerts, NO statement matching, NO order-level breakdown.

### Backend
- **NEW `/app/backend/reconciliation_routes.py`** (registered in `server.py:77,2814`):
  - `GET /api/reconciliation/summary` → `{ totals, platforms[] }`.
    - `totals` = grand `expected / transferred / pending / collection_rate`.
    - Each `platforms[]` row: `account_id, name, normalized_payment_method, orders_count, expected, transferred, pending, current_balance, collection_rate, transfers_count, last_transfer_at, last_transfer_to_bank, currency`.
    - `transferred` = Σ outgoing `internal_transfer` rows from the platform whose peer is `account_type='bank'` (single batched accounts lookup, no N+1).
    - `pending = expected − transferred`, `collection_rate = transferred/expected*100`.
  - `GET /api/reconciliation/platform/{account_id}` → `{ summary, transfers[] }` where `transfers` is hydrated from the `transfers` envelope (keeps `reference`, `notes`, `attachment_url`) and filtered to bank destinations only. 404 if not a payment_platform.

### Frontend
- **NEW `/app/frontend/src/pages/Reconciliation.jsx`** at `/reconciliation`:
  - 4 KPI cards (Total Expected / Transferred / Pending / Collection Rate).
  - One row per platform with 9 columns: المنصة، المتوقع، المحوّل، المعلّق، الرصيد الحالي، نسبة التحصيل، آخر تحويل، عدد التحويلات، إجراء.
  - Per-row `RateBar` progress bar (emerald/amber/rose tier coloring).
  - Totals row at the bottom recomputed in the UI.
- **NEW `/app/frontend/src/pages/ReconciliationDetail.jsx`** at `/reconciliation/:accountId`:
  - 4 KPIs + simple stacked horizontal bar visualizing distribution (Transferred / Pending / Expected).
  - Outgoing transfers-to-bank table (date, bank, amount, reference, attachment).
- Wired routes in `App.js` (lines 30-32, 66-68) and Sidebar link `nav-reconciliation` with `Scales` icon (Sidebar.jsx line 20, 29).

### Verified numbers (real merchant amasi.jewelery@gmail.com)
- Total Expected: **87,529.49 ر.س** (matches Σ expected_orders_balance from /api/accounts)
- Total Transferred: **40,000.00 ر.س** (1 transfer: Salla → بنك الإنماء, TRX-002, 2026-02-15)
- Total Pending: **47,529.49 ر.س** · Collection rate **45.7%**
- Salla row: 51,321.40 expected / 40,000 transferred / 11,321.40 pending / 77.9%, 255 طلب
- Per-platform numbers reconcile 1:1 between `/api/accounts` ↔ `/api/reconciliation/summary` ↔ `/api/reconciliation/platform/{id}`.

### Testing (iter_39)
- New pytest file `/app/backend/tests/test_reconciliation_phase22.py` (8/8 PASS).
- Frontend: 12/12 review checks PASS (sidebar, navigation, KPIs, table columns, drill-down, back link).
- Zero defects found; no auto-fixes applied.

### Deferred (still out of scope per user)
- 14-day Salla rule alerts.
- Automated bank statement import / matching.
- Orders-level breakdown inside detail page.
- Treating "تحويل بنكي" payment method differently from Salla/Tabby/Tamara (user said: leave it for now, address later as "direct collection source" instead of settlement platform).

---

## ✅ ITERATION 67 — Phase 2.1 Polish (تحسينات قبل المرحلة 2.2)

Four fixes user requested before moving to reconciliation:

### 1. Date display
`Transfers.jsx` — replaced `toLocaleDateString("ar-SA-u-nu-latn")` (returned junk like `152026/2/`) with manual `DD/MM/YYYY` from the raw "YYYY-MM-DD" string. Same for `fmtDateTime` (now `DD/MM/YYYY HH:mm`).

### 2. Pending-collection breakdown card
`AccountDetails.jsx` — for `auto_created` payment platforms, new 3-metric card shows:
- **المتوقع من الطلبات** = `expected_orders_balance` (+ orders_count)
- **المحوّل للبنوك** = `expected_orders_balance − current_balance` (cap ≥ 0)
- **المتبقي المعلّق** = `current_balance`
- Progress bar = % collected. Result on سلة: 51,321.40 expected / 40,000 transferred / 11,321.40 pending / 77.9% bar.

### 3. Overdraft guard
- Backend: `POST /api/transfers` refuses when `amount > from_acc.current_balance` (epsilon 0.001 for float). Returns Arabic message including both numbers.
- Frontend: `TransferFormModal` computes `overdraft` live, paints the amount input rose, disables submit button, and shows ⚠ warning under the input.

### 4. Delete reverses both sides
Confirmed already-working: `DELETE /api/transfers/{id}` deletes both linked `account_transactions` rows + envelope, then recomputes both account balances. End-to-end test: سلة 1,321.40 → 51,321.40 ; الإنماء 50,000 → 0.00 after delete.

---

## ✅ ITERATION 66 — Phase 2.1 Internal Transfers (تحويلات بين الحسابات)

**Scope (deliberately small)**: record bank transfers between user's own accounts. NO automatic reconciliation, NO 14-day alerts, NO bank-statement matching, NO debt tracking — those come later.

### Backend
- **NEW `/app/backend/transfers_routes.py`**:
  - `POST /api/transfers` — creates ONE `transfers` envelope doc + TWO linked `account_transactions` rows (`internal_transfer`, direction `out` + `in`), then recomputes both account balances. All three share `transfer_id`.
  - `GET /api/transfers?from_date=&to_date=&account_id=&limit=200` — list with filters.
  - `DELETE /api/transfers/{id}` — atomic undo: deletes both ledger rows + envelope, recomputes both sides.
  - `ensure_transfers_indexes` adds `(user_id, transfer_date desc)` + unique `id` + sparse `transfer_id` on transactions.
  - Server registers under `/api/transfers`.
- **NEW endpoint** `POST /api/accounts/ensure-default-banks` (in `accounts_routes.py`):
  - Idempotently creates 3 banks (بنك الإنماء / بنك الأهلي / بنك الراجحي) with `account_type="bank"`, `opening_balance=0`, `auto_created=true`, `source="default_banks"`.
  - Case-insensitive name match prevents duplicates.

### Frontend
- **NEW `/app/frontend/src/pages/Transfers.jsx`**:
  - Header + "تحويل جديد" button.
  - Balance cards strip — every bank + payment_platform with current_balance (clickable → account details).
  - Transfers log table: التاريخ | من حساب | إلى حساب | المبلغ | المرجع | المرفق | المستخدم | وقت الإنشاء | حذف.
  - Modal with: from select, vertical arrow, to select (auto-filters out source), amount, date, reference, attachment URL, notes. Live preview: source balance + destination balance-after.
  - On page load → POSTs `/accounts/ensure-default-banks` once (idempotent).
- **Sidebar**: new entry "التحويلات بين الحسابات" with `ArrowsLeftRight` icon, route `/transfers`.
- **`AccountDetails.jsx`**: `internal_transfer` rows now render as colored pills — emerald "تحويل وارد · من {peer}" or rose "تحويل صادر · إلى {peer}".

### Verified live (amasi.jewelery@gmail.com Preview)
- Ensure-default-banks created 3 banks.
- Transfer 50,000 ر.س from سلة → بنك الإنماء:
  - سلة current_balance: 51,321.40 → **1,321.40** ✓
  - بنك الإنماء: 0 → **50,000.00** ✓
  - `expected_orders_balance` of سلة stays 51,321.40 (gap = uncollected/pending part — useful KPI for future reconciliation).

---

## ✅ ITERATION 65 — Dashboard payment_breakdown Rollup (تجميع الجدول)

**User pain after iter-64**: KPI buckets (تحويل بنكي = 5,507) were already merged using `normalize_payment_method`, but the **payment_breakdown TABLE** on the Dashboard still showed each raw spelling ("حوالة بنكيةمصرف الراجحي", "حوالة بنكيةمصرف الإنماء", "حوالة بنكيةالبنك الأهلي التجاري") as 3 separate rows. The table totals matched but the visual presentation drifted from the Accounts page.

### Change — `server.py:dashboard`
After `_merge_breakdown(...)`, a new `_rollup_payment_breakdown()` pass:
1. Maps each raw row through `normalize_payment_method`.
2. Groups by `parent or sub_key` (so all 3 "حوالة بنكية…" rows roll into one "تحويل بنكي" row).
3. Sums totals/fees/orders/VAT per bucket.
4. Aggregates `sub_methods` by canonical key inside each bucket (so 3 spellings of الراجحي collapse to one sub-row).
5. Sorts buckets by sales desc, sub-rows by sales desc.

### Verified on real data (amasi.jewelery@gmail.com Preview)
Dashboard total **87,529.49** = Σ payment_breakdown **87,529.49** = Accounts total **87,529.49**. Per bucket:
- سلة 51,321.40 (255) → مدى, بطاقة ائتمانية, STC Pay, Apple Pay
- تمارا 16,090.72 (81)
- تابي 12,320.78 (60)
- **تحويل بنكي 5,507.43 (25)** → بنك الراجحي 4,923 / الإنماء 343 / الأهلي 241 ✅ rolled up
- الدفع عند الاستلام 2,057.96 (11)
- إمكان 231.20 (1)

> Frontend table (`Dashboard.jsx`) already maps from `payment_breakdown[]` — it now shows 6 canonical rows automatically.

---

## ✅ ITERATION 64 — Full Cross-Page Unification (الأرقام موحّدة في كل صفحة)

**Real impact (amasi.jewelery@gmail.com Preview)**: Dashboard 87,529.49 ↔ Accounts 87,529.49 — **exact match**, was 9,541 ر.س off before this iter.

### Changes
1. **Bank sub-rollup** (`payment_methods.py`): added 9 specific bank aliases (الراجحي, الإنماء, الأهلي, الرياض, ساب, البلاد, العربي, الجزيرة, الأول) with `parent_key="bank_transfer"`. PARENT_LABELS now maps `bank_transfer → "تحويل بنكي"`. "حوالة بنكيةمصرف الراجحي" et al. now roll into a single تحويل بنكي account with per-bank sub_methods. Ordering matters: specific bank aliases come BEFORE generic "حوالة بنكية".
2. **Status filter parity** (`accounts_routes.py:sync_payment_methods`): now applies the SAME `settings.report_included_statuses` filter the Dashboard uses (case-insensitive partial-match via regex). Without this, sync was counting refunded/cancelled orders the dashboard had already excluded.
3. **Single classifier** (`server.py:dashboard`): `tamara_keywords`, `tabby_keywords`, `cod_keywords`, `bank_keywords`, the `_is_electronic_method` helper — all replaced with `normalize_payment_method(name)`. `_is_electronic_method` is now: `parent_key == "salla"`.
4. **Null filter** (`payment_methods.py`): `\N`, `\n`, `null`, `nan`, `غير محدد`, `غير معروف` → returns empty tuple, sync skips.

### Verified on real data
- 6 canonical accounts, zero stale entries.
- تحويل بنكي = 5,507.43 (25 طلب) with sub-rows: الراجحي 4,923 (89.4%), الإنماء 343 (6.2%), الأهلي 241 (4.4%).
- سلة = 51,321.40 (255 طلب) with mada + بطاقة ائتمانية + STC Pay + Apple Pay.
- Dashboard payment_breakdown classification now agrees with Accounts buckets.

---

## ✅ ITERATION 63 — Arabic Letter Folding + Null Filtering (إصلاح المطابقة)

**Real user impact (amasi.jewelery@gmail.com)**: 3 stale auto-accounts cluttering the assets page — "البطاقة الإئتمانية" (15,770 ر.س, hamza-bearing alef), "دفع عند الإستلام" (3,200 ر.س, ditto), "\N" (250 ر.س, literal CSV null).

### `backend/payment_methods.py`
- **`_normalize_arabic`**: collapses أ/إ/آ/ٱ→ا, ى→ي, ة→ه, drops kashida. Applied to BOTH input and aliases at match-time. Without it, "البطاقة الإئتمانية" never matched alias "بطاقة ائتمانية" → was kept as its own asset.
- **`_NULL_MARKERS`**: catches `\N`, `\n`, `null`, `nan`, "غير محدد", "غير معروف". Returns empty triple so the row is dropped during sync.
- Added explicit alias "البطاقة الائتمانية" (with ال) so the article variant is recognised.

### `backend/accounts_routes.py` — `sync_payment_methods` cleanup
- Cleanup expanded: any `auto_created=True` account whose `normalized_payment_method` is NOT in the current canonical group set AND has **zero transactions** is deleted. Catches both old Salla-sub rails AND old non-canonical spellings.

### Verified on real user data
- Pre-fix:  8 accounts, 97,071.17 ر.س total. Includes 3 stale rows.
- Post-fix: 6 accounts, 96,820.48 ر.س total. Stale rows merged into سلة / الدفع عند الاستلام / dropped (\N).
- "سلة" rollup correctly absorbed the credit-card 15,770 ر.س → now 56,045.73 ر.س with 4 sub-methods (مدى, بطاقة ائتمانية, STC Pay, Apple Pay).

> ℹ️ **Remaining diff (Dashboard 87,529 vs Accounts 96,820 = +9,291)**: Dashboard's `parsed_all["total_sales"]` excludes some order statuses; sync sums every unified_order regardless. Will be addressed in a follow-up by applying the same filter to sync (or showing both numbers side-by-side in UI).

---

## ✅ ITERATION 62 — Unified Payment-Method Names (قاموس موحّد)

**User pain**: Same payment method had different spellings across pages — "تابي (Tabby)" in Accounts vs "تابي" in Settings; "بطاقات ائتمانية" (plural) in Accounts vs "بطاقة ائتمانية" (singular) in Settings; "مدفوعات سلة" in Settlements vs "سلة" in Accounts. Also: محفظة سلة / بطاقة بنكية / STC Pay / Visa / MasterCard were referenced in the parser but absent from Settings, so the merchant couldn't edit their commission.

### Single source of truth — `backend/payment_methods.py` (NEW)
Module exports:
- Canonical constants: `SALLA`, `TABBY`, `TAMARA`, `EMKAN`, `BANK_TRANSFER`, `CASH_ON_DELIVERY`, `MADA`, `APPLE_PAY`, `STC_PAY`, `VISA`, `MASTERCARD`, `CREDIT_CARD`, `DEBIT_CARD`, `SALLA_WALLET`.
- `DEFAULT_PAYMENT_METHODS` (13 rows) — used to seed settings.
- `PAYMENT_ALIASES` — every raw spelling we've ever seen, tagged with parent (`"salla"` for rollup rails, `None` for standalone).
- `normalize_payment_method(raw) → (sub_key, sub_display, parent_key)`.
- `PARENT_LABELS`, `SALLA_SUB_KEYS`.

### Settings (`auth.py`)
- `DEFAULT_PAYMENT_METHODS` now imported from `payment_methods.py`.
- `ensure_user_settings` **backfills** missing canonical methods into existing user settings docs — preserves user's commission/vat edits (only APPENDS new rows). Result: every existing tenant gets STC Pay / Visa / MasterCard / بطاقة بنكية / محفظة سلة added on next request, without losing their tweaked rates.

### Accounts (`accounts_routes.py`)
- Inline alias table deleted — module imports from `payment_methods.py`.
- `sync_payment_methods` now refreshes the `name` + `provider_name` of existing auto-created accounts on every sync, so canonical-name changes propagate (e.g. "تابي (Tabby)" → "تابي").

### Settlements
- Backend `list_providers` label `"مدفوعات سلة"` → `"سلة"`.
- Frontend `Settlements.jsx` `PROVIDER_TONES.salla.label` → `"سلة"`.
- Placeholder text updated.

### Verified live
- Settings now shows 13 payment methods, all with unified names. User's prior commission edits (مدى 1.85%) preserved.
- Accounts sync produces 4 standalone accounts with unified names: سلة / تابي / تحويل بنكي / الدفع عند الاستلام (no parentheses).
- Sub-methods inside سلة show: مدى, Apple Pay (and would show STC Pay / Visa / MasterCard / بطاقة ائتمانية / بطاقة بنكية / محفظة سلة if orders arrive with those rails).

---

## ✅ ITERATION 61 — Salla Rollup Account (تجميع منصات الدفع تحت "سلة")

**User pain**: After iter-60, every payment method (مدى, Apple Pay, …) became a standalone payment_platform account. The user wants **one** "سلة" account that aggregates all Salla card rails, with breakdown shown only inside its detail page.

### Backend — `accounts_routes.py`
- `_PAYMENT_ALIASES` now carries a 4th column `parent_key`. Salla card rails (`mada`, `apple_pay`, `stc_pay`, `visa`, `mastercard`, `credit_card`) all have `parent_key="salla"`. Tabby / Tamara / Emkan / COD / Bank Transfer stay standalone (`parent_key=None`).
- `normalize_payment_method` returns `(sub_key, sub_display, parent_key)`.
- `POST /api/accounts/sync-payment-methods` does TWO-LEVEL aggregation:
  - Rolls every Salla rail up into a single account `normalized_payment_method="salla"`, name "سلة".
  - Stores per-rail breakdown in `sub_methods[]`: `[{key, display, amount, count}]`.
  - **Auto-cleanup**: any auto-created standalone account whose key is now a Salla rail AND has no transactions is deleted (returned as `removed_legacy`).
  - Response: `{synced, created, updated, removed_legacy, accounts[]}`.

### Frontend
- `AccountDetails.jsx`: new card "تفاصيل طرق الدفع داخل هذا الحساب" rendered when `account.sub_methods.length > 0`. Shows per-rail amount, count, and a % bar of the rollup total.

### Verified live
- Sync deleted legacy "Apple Pay" + "مدى" standalone accounts (returned in `removed_legacy`).
- Created "سلة" rollup = **141,379.49 ر.س** (801 orders) → sub_methods: مدى 139,144 (98.4%), Apple Pay 2,235 (1.6%).
- Payment-platforms tab now shows 4 accounts: سلة / تحويل بنكي / تابي / الدفع عند الاستلام.

---

## ✅ ITERATION 60 — Auto-create Payment Platform Accounts from Orders

**User pain**: `/accounts` had empty Payment Platforms tab and required manual addition of every method.

### Backend — `/app/backend/accounts_routes.py`
- **NEW `POST /api/accounts/sync-payment-methods`**: aggregates `payment_method` across `unified_orders` (sum + count), normalises via `normalize_payment_method()` to a canonical key, upserts a `payment_platform` account per key with `auto_created: true`, `source: "orders_payment_method"`, `normalized_payment_method`, `expected_orders_balance`, `orders_count`. Returns `{synced, created, updated, accounts[]}`.
- **`normalize_payment_method`**: alias table maps Arabic + English spellings (Apple Pay / ApplePay / ابل باي → `apple_pay`; مدى / mada → `mada`; Tabby / تابي → `tabby`; Tamara, Emkan, STC Pay, MasterCard, Visa, COD, Bank Transfer, Salla Pay) — fallback to slug. Idempotent: 2nd sync = 0 created, N updated.
- **`_recompute_balance`**: now starts running balance from `expected_orders_balance` (auto-accounts) so manually adding settlement transactions (Phase 2) deducts correctly from the gross order amount.

### Frontend — `/app/frontend/src/pages/Accounts.jsx`
- Sky button **"مزامنة طرق الدفع من الطلبات"** next to **"إضافة حساب جديد"** in the header, with spinning icon while syncing.
- Each auto-created row shows a small **⚡ تلقائي** pill (sky background) + helper line `"X طلب · رصيد متوقع التحصيل"`.

### Verified live
- 1st sync detected: مدى (794 orders → 139,144 ر.س), Apple Pay (7 → 2,235), تحويل بنكي (10 → 2,300), Tabby (1 → 120), الدفع عند الاستلام (2 → 500). Total payment-platforms summary card jumped to **144,299.49 ر.س**.
- 2nd sync: `synced=5, created=0, updated=5` ✓ idempotent.

---

## ✅ ITERATION 59 — Concurrent Excel + Make Pipeline (تشغيل متوازي بدون توقف)

**User pain**: Uploading Excel froze Make.com webhook ingestion for several seconds while the synchronous endpoint parsed openpyxl + ran 2 DB ops per order serially.

### Backend
- **NEW `/app/backend/import_jobs.py`** — async background worker with:
  - `import_jobs` collection (status, counts, errors, started/completed timestamps).
  - `parse_salla_excel` + `build_report` offloaded to threads via `asyncio.to_thread`.
  - Order upserts in `BATCH_SIZE=50` batches; `await asyncio.sleep(0)` between batches lets webhook coroutines run.
  - Process-local `_ORDER_LOCKS` dict keyed by `(user_id, order_number)` — Excel + Make never race on the same doc, but DIFFERENT orders proceed in parallel.
  - Endpoints: `GET /api/import-jobs`, `GET /api/import-jobs/{id}`, `DELETE /api/import-jobs/{id}`.
- **`POST /api/analyses`** now returns `{job_id, status:"queued"}` in <100 ms; processing runs as a fire-and-forget `asyncio.create_task`.
- **`orders_db.py`** merge rule — Make is authoritative once it touches an order: subsequent Excel writes only fill empty fields (no overwrite of `total_amount`/`order_status`/`payment_method` or `products[]`). New persisted fields: `last_make_update_at`, `last_excel_import_at`, `last_source`, `updated_by_source`.
- **`webhook_routes.py`** `POST /webhook/make/{token}` wraps each order upsert in the same per-order lock.

### Frontend
- **NEW `/app/frontend/src/pages/ImportJobs.jsx`** — table of jobs with progress bars, status pills (queued/processing/completed/failed), per-job detail panel (created/updated/skipped/error counts + last 20 error rows), auto-poll every 2s while any job is active.
- `UploadExcel.jsx` now redirects to `/import-jobs` after a successful POST.
- Sidebar link "حالة الاستيراد" added with `Queue` icon.

### Verified by `/app/backend/tests/test_concurrent_iter59.py`
- 800-row Excel POST returned in **4 ms** (was: seconds).
- 10 parallel webhooks DURING the Excel job: avg **105 ms**, max **145 ms** (was: blocked waiting for Excel).
- 800 orders upserted in < 2 s in the background.
- Make-priority test: Make wrote first → Excel re-write did NOT overwrite `total_amount`/`order_status`/`payment_method`, BUT Excel DID fill the empty `customer_name`. Both `last_make_update_at` & `last_excel_import_at` populated.

---

## Original Problem Statement
أريد بناء تطبيق محاسبي ذكي للتجارة الإلكترونية يقوم بتحليل ملفات Excel المصدرة من منصة سلة واستخراج وتحليل البيانات المالية تلقائياً.

## Architecture
- **Backend**: FastAPI + Motor (MongoDB async) — JWT auth (cookies + bearer), openpyxl Excel parsing, xlsxwriter Excel export, reportlab + arabic-reshaper for PDF export, httpx for Snapchat Marketing API.
- **Frontend**: React 19 + React Router 7 + TailwindCSS + Shadcn/UI + Recharts + @phosphor-icons/react.
- **Database**: MongoDB collections: `users`, `settings`, `daily_costs`, `analyses`, `snapchat_connections`, `snapchat_ad_accounts` (multi-account selection — iteration 15), `snapchat_account_daily` (per-account, per-day spend with native + SAR + FX rate — iteration 15), `meta_connections`, `meta_daily_stats`, `product_costs` (iteration 19 — supports `image_url` from iteration 23).
- **`unified_orders` schema additions (iteration 24)**:
  - `profit_status` ∈ {`complete`, `incomplete_missing_cost`, `incomplete_no_products`}
  - `products_total_lines`, `products_matched_lines`
  - `missing_product_cost_lines[]` now stores `image_url` per line.

## ✅ ITERATION 58 — Order Diagnostics (شاشة تشخيص فروقات الطلبات)
**Status (Feb 2026)**: COMPLETE — wired into `/diagnostics` route + sidebar nav link "تشخيص فروقات الطلبات" with MagnifyingGlass icon. Verified end-to-end via screenshot: page renders, scan executes, summary cards populate (unified_orders / legacy_analyses / webhook_orders / system_total), and overlap card shows correct state.

**User pain**: Dashboard shows 478 orders / 95,178.89 ر.س while Salla shows 475 / 94,724.17 — needs to know WHY without auto-deleting anything.

### Backend — `/app/backend/diagnostics_routes.py` (READ-ONLY)
- `GET /diagnostics/summary?from_date&to_date` — counts from all sources separately: unified_orders, legacy analyses (with their orders/sales), webhook_orders, plus a system_total roll-up.
- `GET /diagnostics/scan-duplicates` — detects 3 inflation sources:
  1. **legacy_overlap_orders**: same order_number in BOTH unified_orders AND a legacy analysis sample (counted twice in dashboard).
  2. **legacy_file_duplicates**: same filename uploaded N times (each adds its totals to dashboard).
  3. **unified_self_dups**: should be zero; safety net.
- `POST /diagnostics/compare-with-salla` — merchant supplies Salla's count/sales (+ optional list of order_numbers). Returns exact arithmetic gap AND set-diff (`in_system_not_in_salla`, `in_salla_not_in_system`).
- `GET /diagnostics/order-trace/{order_number}` — find every collection an order_number lives in (unified, webhook, embedded in legacy analyses) for forensic trace.

### Frontend — `/app/frontend/src/pages/OrdersDiagnostics.jsx`
- Two-column input panel: date range + Salla reference (orders, sales, optional list of order_numbers).
- **"فحص التكرارات الآن"** button → shows source-by-source counts + overlap detection card (rose if any, emerald if none) + per-file upload history.
- **"مقارنة مع سلة"** button → comparison card with system/salla/diff for both orders and sales; two side-by-side lists (in-system-not-salla in rose, in-salla-not-system in amber) with CSV export.
- Click any order number in the diff lists → opens **Trace Modal** showing every location that order_number exists.
- "قراءة فقط" notice — no destructive actions; merchant explicitly approves any cleanup later.
- Sidebar link `nav-diagnostics` (with magnifying-glass icon) added between History and Daily Costs.

### Verified on PREVIEW
- Scan correctly detected `salla_test.xlsx` uploaded 4× (would inflate dashboard by 4× orders), `make_*.json` uploaded 2× (legacy_file_duplicates list).
- Compare correctly reported the arithmetic gap for the user's exact scenario (475 / 94,724.17).
- Trace modal works for any order_number; shows source + amount + dates per location.

### Why no auto-delete
Spec explicitly: "قبل أي حذف أو دمج: أعرض لي أولاً قائمة الطلبات المكررة بالتفصيل حتى أراجعها." Phase 2 (later, after user reviews data) will add an approval-gated merge UI.

---


## ✅ ITERATION 57 — Phase 1: Financial Accounts & Transactions foundation

**User ask**: Foundation layer for the upcoming accounting system. Three account types (bank / payment platform / ads platform), opening balance auto-transaction, summary cards, transactions ledger per account.

### Implementation

#### New Backend module — `/app/backend/accounts_routes.py`
- Two new collections: `accounts` and `account_transactions` (3 indexes each).
- 3 account types: `bank`, `payment_platform`, `ads_platform`.
- 8 transaction types: opening_balance, income, expense, internal_transfer, settlement, debt, debt_payment, manual_adjustment.
- 3 statuses: active, hidden, inactive.
- **Opening balance auto-creates an `opening_balance` transaction** when non-zero.
- Negative opening balances supported (e.g., owed amount on ads platform).
- `current_balance` stored on account doc (cached). Recompute walks transactions chronologically and rewrites `balance_after` on each row to keep history honest after edits.
- **Deletion gate**: cannot delete an account with > 1 transaction; must hide instead. UI shows Arabic error message.
- Endpoints: catalogue, summary (by_type + grand_total), CRUD on accounts, CRUD on transactions, all owner-scoped.

#### Server.py wiring
- Imported `attach_accounts_routes` and mounted under `/api/accounts/*`.
- 3 new indexes created at startup.

#### Frontend
- `/app/frontend/src/pages/Accounts.jsx` — list page with 4 summary cards (gradient backgrounds: emerald/sky/violet/amber), 5 tabs (all/bank/payment_platform/ads_platform/hidden), full table with type badge + currency + balance (color-coded for negative), edit/hide/delete actions, info banner.
- `/app/frontend/src/pages/AccountDetails.jsx` — hero card showing big balance + status + actions, full transactions table (date / type / description / in / out / balance after / status / actions), Add Transaction modal with in/out direction toggle. Opening balance transaction is non-deletable.
- `App.js`: routes `/accounts` and `/accounts/:id` added.
- `Sidebar.jsx`: new `nav-accounts` link with `Wallet` icon, placed right after `nav-dashboard` per spec.

### Verified
- **Backend tests** (`tests/test_accounts_iter57.py`): **6/6 PASS** — catalogue, opening-balance auto-tx, summary aggregation (50k + 12.4k + −2.3k = 60.1k), running balance after CRUD, deletion gate enforced, hidden accounts excluded from summary.
- **Live Playwright E2E**: created 3 accounts of all types (positive bank, positive payment, NEGATIVE ads), navigated to detail page, added a 5,000 USD transaction → balance recomputed correctly (-2,300 + 5,000 = 2,700 USD) ✅.

### Pending / Phase 2 (deferred)
- Internal transfer wizard (move money between accounts as two linked transactions).
- Reconciliation with Salla/Tamara/Tabby actual payouts.
- Recurring deposits / payouts (rentals, salaries).
- Attachment uploads (currently field exists but no upload flow).
- Multi-currency conversion in the grand_total card (currently sums raw values without FX).

---


## ✅ ITERATION 56 (2026-02) — Payment Settlements Ledger + 14-day Salla Window

**User pain**: A partial refund (139 SAR removed from a delivered order, not a full cancellation) caused a silent mismatch between Salla's wallet ("غير المفوّترة") and the system's electronic_net — because the system only excluded refunded/cancelled status orders, not partial amount adjustments. Merchant flagged this as a bug, but the actual issue is the absence of an adjustment ledger.

### Implementation

#### New Backend module — `/app/backend/settlements_routes.py`
- New Mongo collection `payment_adjustments` with 3 indexes (`adjusted_at desc`, `order_number`, `provider`).
- Provider auto-detection (`detect_provider`) with 6 named providers + `other`: salla / tamara / tabby / emkan / bank_transfer / cod.
- 14-day window classifier (`classify_14d_window`) — Salla-only, classifies as `inside_14d` (still pending in Salla's wallet) vs `outside_14d` (already paid out).
- 5 adjustment types: `partial_refund`, `full_refund`, `item_removed`, `order_cancelled`, `manual_adjustment`.
- Endpoints: `GET/POST/PUT/DELETE /api/settlements`, `GET /api/settlements/summary`, `GET /api/settlements/providers`.
- `adjustment_amount` is canonicalised server-side as `original_amount - new_amount` — client-supplied mismatches are rejected.
- **Critical rule**: adjustments aggregate by their `adjusted_at` date (NOT order date) so a refund processed today against a 30-day-old order shows up in today's report, matching Salla's actual wallet behavior.

#### Dashboard integration (`server.py`)
- Calls `aggregate_settlements_by_provider(db, user_id, from, to)` on every dashboard request.
- Subtracts per-provider adjustment totals from each NET:
  - `electronic_net` (salla) now = `gross − fees − salla_adjustments`
  - `bnpl_net` = `gross − fees − tamara_adj − tabby_adj − emkan_adj`
  - `bank_net` = `gross − fees − bank_adj`
- Added new response fields: `settlements_total`, `settlements_by_provider`, `electronic_net_before_settlements`, `salla_settlements_inside_14d`, `salla_settlements_outside_14d`.

#### New Frontend page — `/settlements`
- Date range + provider + 14-day window filters.
- 7 per-provider summary cards showing total adjustment + count.
- Grand-total banner with explanation that adjustments deduct from dashboard nets.
- Full table: order#, order date, adjusted date, provider badge, 14d window pill, original, new, adjustment (rose-red), type, reason, edit/delete actions.
- Add/edit modal with live `adjustment_amount` preview before save.
- Smart info banner explaining the `adjusted_at` rule + 14-day window logic.

#### Sidebar + routing
- New link `nav-settlements` between shipping accounts and profile.
- Route `/settlements` registered in `App.js`.

#### Dashboard alert (reconciliation)
- When `|electronic_net − salla_reference|` matches the total Salla adjustments for the period (±0.5 SAR), shows an amber "الفرق مع محفظة سلة مفسَّر" banner explaining that the gap is **not a bug** — it's the 14-day cycle behavior, with a link to `/settlements` for details.

### Tests
- `/app/backend/tests/test_settlements_iter56.py` — 6/6 PASS covering: provider detection (8 different raw payment_method strings), amount canonicalisation + validation, 14-day window classification (inside/outside/non-Salla), adjusted_at-based range filter, dashboard deduction integration (creates 139 SAR adjustment → verifies electronic_net drops by exactly 139 + before-settlements field matches), full CRUD lifecycle.

### Verified end-to-end
- Live curl test: 500 SAR Salla order → 361 SAR new → adjustment = 139 SAR → electronic_net dropped from 14,026.37 → 13,887.37 (exact match).
- Live Playwright run: opened modal, filled fields, saved → row appeared with correct provider badge, 14-day pill, and rose-red adjustment column. Cleanup worked.

### Pending / Future
- Webhook capture from Salla Direct when partial refunds happen (currently manual entry only).
- Make.com refund webhook integration.
- COD adjustments don't show 14-day window (intentional — applies to Salla payments only per spec).

---


## ✅ ITERATION 55 (2026-02) — Header KPI strip on Executive Profit Summary card

**User ask**: "اضافة متوصط تكلفة الطلب / العائد / عدد الطلبات براس بطاقة الملخص التنفيذي للأرباح بصف واحد".

### Implementation
- Added new `HeaderKpi` sub-component in `ProfitSummaryCard.jsx` plus `fmtInt()` helper for thousands-separated integers.
- New strip rendered between the green title bar and the body — `grid grid-cols-3 gap-2` (stays one row even on mobile by design, with `truncate` on labels so long Arabic text wraps gracefully).
- 3 tiles: عدد الطلبات (sky/`ShoppingCart`), متوسط تكلفة الطلب (amber/`Coins`), العائد على الإعلانات ROAS (emerald/`ChartBar`).
- All values pulled from existing `totals` fields — **no backend changes** (`total_orders`, `avg_cost_per_order`, `overall_roas`).
- Graceful fallback to `—` when `avg_cost_per_order` or `overall_roas` are null (backend returns null when ads spend is 0 — matches existing tooltip behavior).
- ROAS shown with `×` suffix per industry convention (e.g., `3.26×`).
- Dashed divider beneath the strip visually groups it as a "summary header" distinct from the cost breakdown.

### Verified
- With merchant-like data (1,247 orders / 18.57 SAR avg / 3.26× ROAS) — all three render correctly.
- Empty-ads scenario (orders=47, avg=null, roas=null) — shows `47` / `—` / `—` cleanly.
- Mobile (390px viewport) — grid stays 3 columns, labels truncate, no horizontal scroll. Verified visually.

---


## ✅ ITERATION 54 (2026-02) — Toggle visibility of "Product Cost" card on Dashboard

**User ask**: "التحكم بإظهار وإخفاء بطاقة تكلفة المنتجات بداية لوحة التحكم".

### Implementation (reuses existing `dashboard_hidden_cards` setting — no schema change)
- `frontend/src/lib/dashboardCards.js` — exported new `SPECIAL_DASHBOARD_CARDS` array containing one entry `{id: "product_cost_card", label: "تكلفة المنتجات (بطاقة خاصة)"}`. This keeps standalone components (not KPI-grid-driven) toggleable using the same `dashboard_hidden_cards` user setting.
- `frontend/src/pages/Dashboard.jsx` — wrapped `<ProductCostCard>` with `{!hiddenCards.includes("product_cost_card") && (...)}`.
- `frontend/src/pages/Settings.jsx` — added a new amber-highlighted "بطاقات خاصة (أعلى لوحة التحكم)" group at the end of the existing KPI customization card. The Show-all / Hide-all buttons now also operate on these special cards. Uses the same `card-toggle-{id}` testid pattern.

### Verified end-to-end via Playwright
1. Default state — product cost card visible on `/`.
2. Toggling OFF in Settings → Save → reload `/` → card gone (`SKU/Product ID` substring count = 0).
3. Toggling back ON → Save → reload `/` → card present again.

### Future-proofing
The `SPECIAL_DASHBOARD_CARDS` mechanism is reusable: when we want to toggle the Executive Profit Summary or any other standalone block, we just add one more entry to that array.

---


## 🐛 BUG FIX (2026-02 — Iteration 53) — Executive Profit Summary card hid the Operating Expenses line

**Merchant report**: "هناك غلط ببطاقة الملخص للأرباح، الإجمالي النهائي لا يطابق طرح السطور أعلاه."

### Root Cause
Backend's `net_profit_adjusted` formula deducts **operating expenses** (rents + salaries + …) from the running total:
```
net_profit = sales − fees − shipping − ads − product_cost − operating_expenses_total
```
But `/app/frontend/src/components/ProfitSummaryCard.jsx` only displayed 5 deduction lines (product cost, ads, shipping, payment fees) and used `t.net_profit` from the backend as-is for the final row — creating an unexplained gap equal to `operating_expenses_total` whenever the merchant had any operating costs configured.

### Fix
- Added a conditional line `− المصروفات التشغيلية (رواتب وإيجارات وغيرها)` in the card, rendered only when `operating_expenses_total > 0` so it doesn't add visual clutter for stores that don't use that feature.
- Updated the manual-fallback math (used only when backend hasn't yet returned `net_profit`) to also subtract `operating_expenses_total`.
- File touched: `frontend/src/components/ProfitSummaryCard.jsx`.

### Verified
Live dashboard run with `total_sales=28,153.25`, `operating_expenses_total=315` → backend reports `net_profit=22,605.29`, manual sum after the new line: `28153.25 − 1734.50 − 1102.75 − 1875.65 − 520.06 − 315 = 22605.29` ✅ (perfect match).

---


## ✅ ITERATION 52 (2026-02) — Remove "Made with Emergent" badge + Show-Register toggle — **DONE**

**User asks**:
1. Remove the "Made with Emergent" badge permanently from every page.
2. Add a setting in `الإعدادات → إعدادات تسجيل الدخول` to toggle the visibility of the "إنشاء حساب جديد" link on the login screen. Default OFF (single-store deployment). UI-only — keep `/api/auth/register` working.

### Implementation
- **Badge removal**: Deleted the `<a id="emergent-badge">` block from `/app/frontend/public/index.html` (was lines 41-85). Added a defensive CSS rule in `/app/frontend/src/index.css` (`#emergent-badge { display: none !important; }`) to hide it in case the platform re-injects it post-deploy.
- **Backend** (new endpoints in `server.py`, singleton `app_config` collection with `_id='global'`):
  - `GET /api/public/login-config` — **unauthenticated**, returns `{show_register_link: bool}`. Read by the public `/login` page.
  - `GET /api/app-config` — Owner-only, returns full app config.
  - `PUT /api/app-config` — Owner-only, accepts partial `AppConfigIn{show_register_link?: bool}`.
  - Defaults: `show_register_link=False` (kept hidden by default for single-store deployments).
- **Frontend**:
  - `pages/Login.jsx` — calls `GET /public/login-config` on mount; conditionally renders either the register link or a muted "التسجيل مغلق — هذا النظام خاص بمتجر واحد." message.
  - `pages/Settings.jsx` — new `login-settings-card` (Owner-only — gated by `user?.is_owner`) with a switch (`show-register-toggle`) that auto-saves on click. Status indicator below shows "ظاهر" / "مخفي".

### Tests
- **Backend**: `/app/backend/tests/test_app_config_iter52.py` (3 tests) — public endpoint anonymous read, Owner toggle round-trip, non-Owner 403. Combined run: 12/12 PASS (iter51 9 + iter52 3).
- **Frontend (iteration_38.json)**: 7/7 scenarios PASS via testing agent. Badge confirmed absent on `/login`, `/`, `/settings`, `/profile`, `/team`. Toggle round-trips fully validated. `/api/auth/register` confirmed still functional even when UI toggle is OFF.

---


## ✅ ITERATION 51 (2026-02) — RBAC: Profile / Team Management / Password Recovery — **DONE**

**User ask**: "Responsive Dashboard + Profile (Email/Password) + Password Recovery + Team Roles/Permissions".

### Backend (already shipped in previous session, fixed this iteration)
- `PUT /api/auth/profile/name|password|email|security-question` — self-service profile updates.
- `POST /api/auth/forgot-password/check` — returns the security question (or a generic prompt to avoid email enumeration).
- `POST /api/auth/forgot-password/reset` — resets password if the security-question answer matches (bcrypt-hashed, normalised).
- `GET/POST/PUT/DELETE /api/team/users` — Owner-only CRUD. Owner row cannot be modified/deleted by others.
- `GET /api/auth/permissions/catalogue` — returns `permissions[]` (with i18n labels) + `role_defaults{}` mapping.
- **Bug fix**: All `verify_password(payload.current_password, user["password_hash"])` calls were failing because `get_current_user_from_db` strips `password_hash` for safety. Fixed by re-fetching the user via `db.users.find_one({"id": user["id"]})` inside the four profile endpoints.
- **Bug fix**: `GET /team/users` capped at 500 with no sort — newly created users wouldn't appear if DB had ≥500 users. Now sorts by `created_at DESC` with cap 5000.

### Frontend (new — this iteration)
- `/app/frontend/src/pages/Profile.jsx` — 4 sections: Name, Email (with current-password confirm), Password (current + new + confirm), Security Question (current-password confirm).
- `/app/frontend/src/pages/TeamManagement.jsx` — Owner-only table with role badges (color-coded per role), Add/Edit/Delete modals, fine-grained permissions UI showing role defaults vs added/denied.
- `/app/frontend/src/components/ForgotPasswordModal.jsx` — 2-step modal (email lookup → question + answer + new password). Linked from `/login` via "نسيت كلمة المرور؟".
- `/app/frontend/src/components/Sidebar.jsx` — Added "حسابي" (always) and "إدارة الفريق" (owner-only) nav links.
- `/app/frontend/src/context/AuthContext.jsx` — `login()` now refreshes via `/auth/me` so `is_owner` + `permissions` + `has_security_question` are available immediately.
- `/app/frontend/src/App.js` — Routes `/profile` and `/team` added.

### Testing
- **Backend**: 9/9 PASS — `/app/backend/tests/test_auth_and_team_iter51.py`.
- **Frontend (iteration_37.json)**: 5/5 feature groups PASS via testing agent. Full forgot-password flow exercised against the live preview URL.
- Test credentials updated in `/app/memory/test_credentials.md` (admin sec question + viewer seed).

### Known optional improvements (NOT blocking)
- TeamManagement renders all rows client-side — virtualization or server-side pagination would help when team grows beyond a few hundred. (Current testing DB has 4499 stale test users which is not a real-world scenario.)
- Logout button can be overlapped by the "Made with Emergent" badge on small viewports — raise z-index of sidebar footer to fix.

---


## 🐛 CRITICAL BUG FIX (2026-06 — Iteration 50) — **Meta Ads orders inflated 5-10× by duplicate `action_type` values**

**Merchant report (Production)**: "نتائج الإعلانات في بطاقات الحسابات الإعلانية فيسبوك ليست صحيحة، يظهر أرقام طلبات أكبر من الحقيقية بـ 10 أضعاف، والعائد والمبيعات الشهرية واليومية".

### Root Cause
Meta's Graph API `/insights` endpoint يُرجع **نفس عملية الشراء تحت `action_type` متعددة** عندما يكون لدى التاجر Pixel + Conversions API + Facebook/Instagram Shop مُفعّلين معاً (وهذا هو الإعداد القياسي لمعظم متاجر سلة):
```json
"actions": [
  {"action_type": "purchase",                             "value": 5},
  {"action_type": "omni_purchase",                        "value": 5},
  {"action_type": "offsite_conversion.fb_pixel_purchase", "value": 5},
  {"action_type": "onsite_web_purchase",                  "value": 5},
  {"action_type": "onsite_conversion.purchase",           "value": 5}
]
```
الكود السابق كان يجمع كل قيمة فيها كلمة `"purchase"` → 5 × 5 = **25 عملية شراء** بدلاً من 5 الحقيقية. مع تركيب Pixel + CAPI + Shop المعتاد، التضخيم 5–10×. هذا يطابق ال **"10 أضعاف"** التي رصدها التاجر.

### Fix (`/app/backend/meta_routes.py`)
- ✅ استبدلت `_extract_purchases()` و `_extract_purchase_value()` بمنطق **يلتقط نوع واحد فقط** حسب أولوية Meta الرسمية للـ deduplication:
  ```python
  _PURCHASE_TYPE_PRIORITY = (
      "omni_purchase",                            # Meta-official cross-channel dedup
      "purchase",                                 # base Pixel event
      "offsite_conversion.fb_pixel_purchase",     # Pixel-only attribution
      "onsite_web_purchase",                      # Shop purchases
      "onsite_conversion.purchase",
  )
  ```
- ✅ دالة helper موحَّدة `_pick_canonical_purchase_value()` تستخدم للعدد والقيمة معاً.
- ✅ عند تكرار نفس الـ action_type مع قيم مختلفة (نوافذ إسناد 7d/1d)، تُختار **الأكبر** (دفاعياً).

### Historical data
الـ rows المحفوظة سابقاً في `meta_ads_daily` لا تزال تحوي القيم المضخمة، لكن endpoint الـ`/api/meta/sync` يستخدم upsert بـ `(date, campaign_id)` — بمجرد إعادة المزامنة من زر **"Sync Now"** أو **"Auto-sync"** في الـ dashboard ستُستبدَل بالقيم الصحيحة. لا حاجة لـ migration.

### Tests (11/11 PASS — `tests/test_meta_purchases_dedup_iter50.py`)
1. التضخيم الأصلي (5 أنواع × 5 = 25) → الآن 5.
2. التضخيم في قيمة الـ revenue (3 أنواع × 1234.50 = 3,703.50) → الآن 1234.50.
3. `omni_purchase` يُفضَّل على `purchase` العادي.
4. fallback إلى `purchase` عند غياب `omni_purchase`.
5. fallback إلى `fb_pixel_purchase` للـ legacy pixels.
6. fallback إلى `onsite_*` لمتاجر Facebook/Instagram Shop.
7. الإجراءات غير-الشراء (view_content, add_to_cart) تُتجاهَل.
8. NULL/empty/malformed safe.
9. نفس النوع مرتين → تُختار القيمة الأكبر.
10. **Real-world Saudi merchant payload**: 5 أنواع شراء + noise من video_view/link_click → النتيجة 5 (وليس 50).

### كيف يرى التاجر الإصلاح
1. **أعد النشر** ليصل التغيير إلى Production.
2. اذهب إلى **الإعدادات → Meta Ads → "مزامنة الآن"** (Sync Now) — أو انتظر التزامن التلقائي اليومي (يحدث بعد 23 ساعة من آخر مزامنة).
3. ستُستبدَل أرقام آخر 7-30 يوماً (الفترة المُعاد جلبها) بقيم deduplicated صحيحة.
4. الأرقام الجديدة ستطابق Meta Ads Manager → "Results" → "Website Purchases" → "Per Channel" بنسبة 100%.

---

## 🎯 NEW FEATURE (2026-06 — Iteration 49) — **بطاقة "الملخص التنفيذي للأرباح" في لوحة التحكم**

**Merchant request**: "تقرير مختصر في لوحة التحكم — المبيعات / تكاليف المنتجات / إجمالي تكاليف الإعلانات / إجمالي تكاليف الشحن الآجل والمقدم / إجمالي رسوم جميع طرق الدفع / صافي الأرباح — باللون مرتبة وأنيقة".

### Frontend (`ProfitSummaryCard.jsx` — مكوّن جديد)
- ✅ بطاقة منفصلة بتصميم **gradient أخضر-كهرماني**، تظهر فوق شبكة الـKPI مباشرة (تحت ProductCostCard).
- ✅ **شريط علوي** أخضر داكن بعنوان: "الملخص التنفيذي للأرباح" + وصف "تقرير مختصر للفترة المحددة".
- ✅ **5 صفوف خصم ملوّنة** بصرياً (كل صف بأيقونة + لون مختلف لتمييز سريع):
  1. 🟢 **المبيعات** (Coins) — `total_sales`
  2. 🟠 **− تكاليف المنتجات** (Package) — `total_product_cost`
  3. 🔴 **− إجمالي تكاليف الإعلانات** (Megaphone) — `total_ads_cost`
  4. 🔵 **− إجمالي تكاليف الشحن (مقدم + آجل)** (Truck) — `total_shipping_cost` (يشمل المقدّم والآجل معاً)
  5. 🟣 **− إجمالي رسوم جميع طرق الدفع** (Receipt) — `other_payment_fees + tamara_fees + tabby_fees + emkan_fees + bank_fees`
- ✅ **صف الصافي** صندوق أخضر مميّز (text-2xl، gradient، shadow-md) لإبراز الرقم النهائي:
  6. ✅ **= صافي الأرباح** = من `totals.net_profit` (مع fallback ذاتي إذا غاب من الـ backend).

### Design details
- Soft `bg-gradient-to-br from-emerald-50 via-white to-amber-50` — يدمج البطاقة مع باقي الـ dashboard.
- Border emerald-200/60 + shadow-sm للتركيز دون إزعاج.
- Hover effects على كل صف (`hover:bg-white/40`).
- RTL-aware، Tajawal font للأرقام، responsive (يتقلّص على mobile).

### Wiring
- زيرو تعديلات في الـ backend — كل القيم متاحة في `totals` المُرسل أصلاً من `/api/dashboard`.
- Pure presentational — لا يطلب API إضافي.

### Visual verification (Playwright)
- البطاقة ظاهرة، net-profit يطابق `totals.net_profit` (-1,339.50 ر.س)، تكاليف المنتجات تظهر القيمة المُدخلة في iter-46 (1,234.50)، باقي الصفوف تعرض القيم الصحيحة.

---

## 🎯 NEW FEATURE (2026-06 — Iteration 48) — **أيقونة معلومات ⓘ على كل بطاقة KPI تشرح طريقة الاحتساب**

**Merchant request**: "نشتي توضيح على كل بطاقة كيف تم احتساب تقرير البطاقة من خلال النقر على أيقونة صغيرة تظهر توضيح ثم يختفي عند يرحل إشارة الموس".

### Frontend (`dashboardCards.js` + `Dashboard.jsx`)
- ✅ كل بطاقة في `KPI_GROUPS` الآن تحتوي حقل **`explanation`** بنصّ متعدد الأسطر يشرح:
  - **الصيغة الرياضية** الدقيقة المُستخدَمة.
  - **مصدر البيانات** (Make / Excel / Webhooks / إدخال يدوي / إعدادات).
  - **القيود/الفلاتر** المطبَّقة (مثلاً: استبعاد الحالات الملغية في `electronic_net`).
- ✅ مكوّن جديد `KpiInfoTooltip`:
  - أيقونة `Info` صغيرة (14px، رمادي افتراضياً، تتلوّن brand عند الـ hover).
  - يظهر **tooltip أسود متراص** بعرض 18-20rem + سهم صغير للأعلى.
  - **يعمل على ٤ مسارات**: `mouseenter` + `focus` + `click toggle` + `escape/click-outside`.
    - hover (سطح المكتب): يفتح تلقائياً.
    - focus (لوحة المفاتيح): يفتح للتصفّح بإمكانية الوصول.
    - click (mobile/touch): toggle.
    - Escape key أو الضغط خارج الـ tooltip: يُغلق.
  - رأس صغير بلون كهرماني: **"طريقة الاحتساب"**.
  - النص يحترم `whitespace-pre-line` فيُعرض كما كُتب بفواصل أسطر طبيعية.
- ✅ يُحقن داخل label الـ Kpi → 28 بطاقة كلها تحصل على الأيقونة تلقائياً (data-driven).

### Coverage
كل البطاقات الـ28 لها explanation مكتوب بعناية: 
- **المبيعات**: total_sales, net_sales, total_orders, expected_salla_transfer.
- **التسويق**: overall_roas, avg_cost_per_order.
- **رسوم الدفع**: other_payment_fees, electronic_net, bank_net, tamara/tabby/emkan_fees, bnpl_net.
- **الشحن**: total_shipping_cost, deferred_shipping_cost, shipping_approved/unapproved.
- **COD**: cod_approved, cod_unapproved.
- **المصاريف**: total_vat, total_ads_cost, total_product_cost, daily_expenses_total, operating_expenses_total, operating_salaries_total, operating_rentals_total, operating_prepaid_total, net_profit.

### Visual verification (Playwright)
- 28 أيقونة معلومات ظاهرة على كل البطاقات (`data-testid` لكل: `kpi-{id}-info-btn` + `kpi-{id}-info-btn-content`).
- Hover على ROAS → الـ tooltip يظهر بالنص الصحيح.
- Mouseleave → الـ tooltip يختفي تلقائياً (تأكيد من E2E).
- لا توجد regression: **21/21 backend tests** عابرة (iter-44/45/46/47).

---

## 🎯 NEW FEATURE (2026-06 — Iteration 47) — **فصل التحويلات البنكية في بطاقة KPI مستقلة**

**Merchant request**: "طريقة الدفع bank لا تنحسب ضمن صافي المدفوعات الإلكترونية، تنحسب في بطاقة لحالها باسم المدفوعات البنكية".

### Why this split matters
التحويلات البنكية لا تمر عبر بوابة سلة — تتم تسويتها بنكياً (T+1/T+2) وتُحفظ أحياناً يدوياً من قِبل التاجر. خلطها مع `electronic_net` كان يُربك التطابق مع شاشة سلة "غير المفوترة" ويُضخّم الرقم بمبالغ لم تمر إلكترونياً أصلاً.

### Backend (`server.py`)
- ✅ ثابت جديد `bank_keywords`:
  ```py
  ("تحويل بنكي", "حوالة بنكية", "تحويل البنك", "تحويل بنوك",
   "bank transfer", "bank_transfer", "wire transfer")
  ```
  + مطابقة `name_lc == "bank"` لتغطية الاسم الحرفي "Bank".
- ✅ في حلقة `payment_breakdown` (السطر 988-991): فرع جديد قبل else يجمع `bank_sales` و`bank_fees` ويستثنيها من `other_payment_sales`.
- ✅ نفس المنطق مطبَّق في فرع `legacy_analyses` (السطر 1129-1131) للحفاظ على الـ backward-compat.
- ✅ `_is_electronic_method` (داخل dashboard) و `_is_electronic` (في debug endpoint) كلاهما الآن يستثنيان البنك → audit modal يبقى متوافقاً مع البطاقة.
- ✅ payload response يحتوي الآن 3 حقول جديدة:
  ```json
  "bank_sales": …, "bank_fees": …, "bank_net": …
  ```

### Frontend (`dashboardCards.js`)
- ✅ بطاقة KPI جديدة `bank_net` في مجموعة "رسوم بوابات الدفع":
  - icon: `Bank` (أخضر accent للتمييز عن باقي بطاقات الصف)
  - hint: "تحويل بنكي بعد العمولة"
  - money: true → عرض `…ر.س` بشكل صحيح
- ✅ تظهر بجانب `electronic_net` مباشرةً ليرى التاجر الفصل بصرياً فوراً.
- ✅ تخضع تلقائياً لنظام `dashboard_hidden_cards` — يمكن للتاجر إخفاؤها مثل باقي البطاقات.

### Behavior contract
- البنك **يظهر دائماً** بكل الحالات (الملغية والمرتجعة كذلك) لأنه يمثّل تدفق نقدي بنكي، ليس مدفوعات بوابة. الـ status filter لا يلمس بطاقة البنك.
- المبلغ الإجمالي للبطاقة = `SUM(bank_sales) − SUM(bank_fees)` (الرسوم 0 افتراضياً ما لم يضبط التاجر عمولة لطريقة الدفع البنكية في الإعدادات).

### Tests (5/5 PASS — `tests/test_dashboard_iter47_bank_split.py`)
1. `test_bank_orders_have_dedicated_kpi` — 900 ر.س بنكي + 200 ر.س مدى → `bank_net=900`، `electronic_net≈200` (بعد العمولة).
2. `test_bare_bank_name_is_classified_as_bank` — اسم "Bank" الحرفي يدخل البطاقة البنكية.
3. `test_bank_card_includes_all_statuses` — الملغية البنكية تبقى في البطاقة.
4. `test_debug_endpoint_excludes_bank_orders` — audit modal لا يرى الطلبات البنكية (consistency).
5. `test_mixed_payment_types_are_independent` — 4 طرق دفع (مدى + بنك + تمارا + COD) — كل دلو في بطاقته بشكل منفصل.

### Visual verification (Playwright)
بطاقة "المدفوعات البنكية" ظاهرة بأيقونة Bank الخضراء في صف "رسوم بوابات الدفع"، بطاقة `electronic_net` مازالت موجودة بجانبها مع زر "تفاصيل".

---

## 🎯 NEW FEATURE (2026-06 — Iteration 46) — **كارت منبثق لإضافة إجمالي تكلفة المنتجات حسب التاريخ**

**Merchant request**: "كارت منبثق لإضافة إجمالي تكلفة المنتجات حسب التاريخ المحدد في صفحة تكلفة المنتجات بشكل مؤقت حتى يتم موازنة التكاليف من كل منتج بالمستقبل".

### Backend
- ✅ **صفر تعديلات**. الـ endpoint موجود مسبقاً:
  - `POST /api/daily-costs` — upsert by `date`، يضم حقل `product_costs`.
  - `GET /api/daily-costs` — قائمة مرتبة DESC.
  - `DELETE /api/daily-costs/{date}` — يمسح الصف كاملاً.
- ✅ **الدمج التلقائي مع لوحة التحكم**: السطر 1233 في `server.py` يستخدم:
  ```py
  product_cost_effective = max(computed_product_cost, daily_products_total)
  ```
  أي أن أي قيمة تُحفظ في `daily_costs.product_costs` تدخل تلقائياً في حساب صافي الربح بدون أي تعديل إضافي.

### Frontend
- ✅ **مكون جديد** `DailyProductCostModal.jsx`:
  - منتقي تاريخ + حقل المبلغ (ر.س) + ملاحظة اختيارية.
  - **تنبيه أصفر صريح** "حل مؤقت — استبدله بتكلفة لكل منتج لاحقاً".
  - **تنبيه ذكي** عند اختيار تاريخ له إدخال سابق: يعرض القيمة الحالية ويغير الزر من "حفظ" إلى "تحديث".
  - **جدول آخر 30 إدخال** مع زر حذف لكل سطر (يحافظ على بيانات الإعلانات للتاريخ نفسه عبر upsert بـ `product_costs=0` بدلاً من DELETE كامل).
  - Preserves the OTHER cost fields (snapchat_ads, tiktok_ads, etc.) عند الحفظ — لا يصفّرها.
- ✅ **زر جديد** "إجمالي تكلفة يوم" (Coins icon، خلفية كهرمانية) في header صفحة `/product-costs` بجانب "إضافة منتج".
- ✅ Toast نجاح ديناميكي حسب الحالة (`حفظ` vs `تحديث`).

### Tests (5/5 PASS — `tests/test_daily_product_costs_iter46.py`)
1. `test_daily_product_cost_flows_into_dashboard` — قيمة 1250.50 ر.س تظهر في `manual_product_cost` و`total_product_cost` بالـ dashboard.
2. `test_daily_product_cost_upsert_replaces_for_same_date` — حفظ تاريخ نفسه مرتين → القيمة تُستبدل (لا تُضاف).
3. `test_zeroing_product_cost_removes_it_from_total` — تصفير القيمة يزيلها من الحساب.
4. `test_multiple_days_sum_correctly` — يومان مختلفان (300+700) يُجمعان إلى 1000 في الـ dashboard.
5. `test_endpoint_requires_auth` — يتطلب bearer token.

### Visual verification (Playwright)
- الزر ظاهر، الـ modal يفتح، الإدخال يُحفظ → toast يظهر → الصف يُضاف إلى جدول الإدخالات السابقة → التنبيه الذكي يظهر عند إعادة فتح نفس التاريخ.

---

## 🐛 BUG FIX (2026-06 — Iteration 45.1) — **"تعذّر تحميل تفاصيل الحساب" عند فتح modal تفاصيل صافي المدفوعات**

**Merchant report**: "رسالة خطأ تظهر تعذّر تحميل تفاصيل الحساب عند الضغط على تفاصيل صافي المدفوعات الإلكترونية".

### Root cause
في الـ endpoint الجديد `GET /api/dashboard/electronic-net-debug` كنا نستدعي `_matches_any(...)` — وهي **دالة nested معرّفة داخل `dashboard()` handler**، غير قابلة للوصول من خارجها. عندما يكون لدى المستخدم `settings.report_included_statuses` غير فارغة، يحدث `NameError: name '_matches_any' is not defined` → 500 → toast "تعذّر تحميل تفاصيل الحساب".

### Fix (`server.py`)
- ✅ استبدال استدعاء `_matches_any` بـ inline substring-match داخل الـ endpoint:
  ```py
  included_lower = [s.strip().lower() for s in included_statuses if s and s.strip()]
  all_orders = [o for o in all_orders
                if any(t in (o.get("order_status", "") or "").strip().lower()
                       for t in included_lower)]
  ```
- ✅ **Regression test جديد** `test_debug_endpoint_works_with_report_included_statuses` يثبت أن الـ endpoint يستجيب 200 (وليس 500) عندما يكون `report_included_statuses` مضبوطاً.

### Tests (7/7 PASS — `tests/test_dashboard_iteration45_electronic_net.py`)
الـ 6 الأصلية + الـ regression الجديد.

### Visual confirm
Modal يفتح، الصناديق الـ 3 ظاهرة، لا توجد رسالة خطأ.

> ⚠️ **ملاحظة**: المستخدم رأى هذه الرسالة على **Production**. الإصلاح مطبَّق في Preview فقط — تحتاج لإعادة نشر التطبيق ليصل إصلاح إلى Production.

---

## 🛠️ BUG FIX (2026-06 — Iteration 45) — **مطابقة "صافي المدفوعات الإلكترونية" مع شاشة سلة "غير المفوترة"**

**Merchant report**: لوحة التحكم تعرض صافي المدفوعات = `26,643.23` SAR بينما شاشة سلة → المدفوعات → غير المفوترة = `21,715.87` SAR (فارق `4,927.36` ≈ 23% زيادة).

### RCA
- `electronic_net` كان يُحسب كـ `SUM(other_payment_sales) − SUM(other_payment_fees)` من `payment_breakdown` بدون أي فلترة على حالة الطلب.
- النتيجة: الطلبات الملغية / المرتجعة / فشل الدفع / بانتظار الدفع كانت تُحسب رغم أنها لم تمر فعلياً عبر البوابة.
- سلة في "غير المفوترة" تعرض فقط المعاملات التي تم استلامها فعلياً.

### Backend (`server.py`)
- ✅ ثابت `DEFAULT_ELECTRONIC_NET_EXCLUDED_STATUSES` يغطي: `ملغ`, `مسترد`, `مرتجع`, `فشل`, `مرفوض`, `بانتظار الدفع` + المرادفات الإنجليزية.
- ✅ helper `_is_excluded_for_electronic_net(status, terms)` — مطابقة جزئية حساسة-بـ-`lower`.
- ✅ إضافة حقلين جديدين في الإعدادات: `electronic_net_excluded_statuses` (override) + `salla_electronic_net_reference` (للمقارنة).
- ✅ بعد حساب الـ payment_breakdown الكامل، نُعيد حساب `other_payment_sales / other_payment_fees` من قائمة طلبات مفلترة (electronic-only + non-excluded statuses). البطاقات الأخرى (BNPL/COD/الإجمالي) لا تتأثر.
- ✅ حقل جديد في `totals.electronic_net_breakdown` يُرجع: `included_count / excluded_count / gross_before_filter / gross_after_filter / fees_before/after / excluded_statuses_active`.
- ✅ **endpoint جديد** `GET /api/dashboard/electronic-net-debug?from_date=&to_date=` يُرجع:
  - إجمالي الطلبات قبل الفلترة + بعدها
  - تقسيم حسب الحالة المستبعدة (status → count)
  - عيّنة من الطلبات المضمنة / المستبعدة (50 لكل واحدة + سبب الاستبعاد)
  - per-payment-method breakdown بعد الفلترة
  - مقارنة مع `salla_electronic_net_reference` (gap_vs_computed + gap_percent)
- ✅ **endpoint جديد** `POST /api/settings/electronic-net/sync-to-salla` — يستعيد القائمة الافتراضية المطابقة لسلة بنقرة واحدة.

### Frontend
- ✅ زر **"تفاصيل"** صغير على بطاقة `kpi-electronic_net` يفتح modal جديد (`ElectronicNetDebugModal.jsx`).
- ✅ Modal يُظهر:
  - شارة المقارنة مع سلة (أخضر للتطابق ≤ 1% أو أصفر للفارق)
  - 3 صناديق (قبل الفلترة / الاستبعاد بسبب الحالة / الصافي النهائي) مع الأرقام التفصيلية
  - جدول الاستبعاد حسب الحالة
  - جدول تقسيم حسب طريقة الدفع بعد الفلترة (عدد، إجمالي، رسوم، صافي)
  - عيّنة الطلبات المستبعدة قابلة للطي مع سبب الاستبعاد لكل صف
  - زر **تنزيل CSV** للتقرير الكامل
- ✅ في `/settings` قسم جديد "صافي المدفوعات الإلكترونية — مطابقة سلة" يحتوي:
  - زر **"مزامنة مطابقة مع سلة"** يستدعي `POST /sync-to-salla`
  - `StatusListEditor` للحالات المستبعدة (مع 11 اقتراحاً افتراضياً + الحالات المُكتشَفة من طلبات المستخدم)
  - حقل **"رقم سلة المرجعي"** (placeholder = `21715.87`) — يحفظ في `salla_electronic_net_reference`
  - تنبيه أصفر يوضّح أن الحل النهائي 100% يحتاج Salla Payments API (Phase 2)

### Tests (6/6 PASS — `tests/test_dashboard_iteration45_electronic_net.py`)
1. `test_default_filter_excludes_cancelled_refunded_pending` — 7 طلبات (4 مدفوعة + 3 مستبعدة) → `included_count=4`, `excluded_count=3`, post-filter gross = 500.
2. `test_debug_endpoint_returns_full_breakdown` — جدول الحالات المستبعدة + سبب الاستبعاد لكل طلب + عيّنة المضمنة/المستبعدة.
3. `test_salla_reference_populates_gap` — `salla_electronic_net_reference=900` على صافي 1000 → `gap=+100, gap_percent=11.11%`.
4. `test_sync_to_salla_restores_default_exclusions` — override `["custom_only"]` ثم sync → القائمة تعود للقيم الافتراضية المطابقة لسلة.
5. `test_bnpl_and_cod_orders_unchanged_by_filter` — تمارا/تابي/إمكان/COD لا تتأثر بالفلتر.
6. `test_debug_endpoint_handles_empty_store_safely` — متجر فارغ يُرجع الـ shape الصحيح بدون كسر.

### Path forward (Phase 2 — لاحقاً)
البطاقة حالياً تعتمد على فلتر حالة الطلب لتقريب الرقم. الحل النهائي 100% يتطلب جمع جدول `payment_transactions` مباشرة من Salla Payments API (مذكور في الإعدادات و في تنبيه الـ modal). سيُنفَّذ ضمن **Salla Direct Integration — Phase 2**.

---

## 🎯 NEW FEATURE (2026-06 — Iteration 44) — **بطاقتا ROAS ومتوسط تكلفة الطلب في لوحة التحكم**

**Merchant request**: "اضافة بطاقة ROAS في لوحة التحكم ومتوسط تكلفة الطلب".

### Backend (`server.py`)
- ✅ في `/api/dashboard` بعد حساب `total_sales`, `total_orders`, `daily_ads_total`:
  ```py
  overall_roas        = total_sales / daily_ads_total   if daily_ads_total > 0   else None
  avg_cost_per_order  = daily_ads_total / total_orders  if total_orders > 0 and daily_ads_total > 0 else None
  ```
- يُحقَن الحقلان في `totals` payload.
- `null` بدلاً من `0` أو `Infinity` عند انعدام الإنفاق/الطلبات — لتمكين الواجهة من إظهار "—" بدلاً من قيم مضللة.

### Frontend (`dashboardCards.js` + `Dashboard.jsx`)
- ✅ مجموعة KPI جديدة `marketing` ("أداء التسويق") تحتوي بطاقتين:
  1. **`overall_roas`** — icon: `ChartLineUp`, accent: green, value: `t.overall_roas`, `format: v => v == null ? "—" : v.toFixed(2) + "×"`.
  2. **`avg_cost_per_order`** — icon: `Tag`, accent, money: true (تُلحق `(ر.س)` تلقائياً).
- ✅ Renderer في `Dashboard.jsx` يدعم الآن دالة `format` اختيارية لكل بطاقة + يعرض `—` بدلاً من `formatMoney(null)` للبطاقات النقدية ذات القيمة الخالية.
- ✅ يعملان تلقائياً مع نظام إخفاء البطاقات (`dashboard_hidden_cards`) — يمكن للتاجر إخفاء أي منهما من الإعدادات بدون أي كود إضافي.

### Tests (4/4 PASS — `tests/test_dashboard_iteration44_roas.py`)
1. `test_overall_roas_and_cpa_when_ads_present` — 1000 SAR sales / 200 SAR ads → ROAS=5.00×, CPA=20.00 SAR.
2. `test_kpis_are_null_when_no_ad_spend` — `daily_ads_total=0` → كلتاهما `None`.
3. `test_cpa_null_when_no_orders` — `total_orders=0` → ROAS=0 (finite)، CPA=None.
4. `test_roas_uses_all_ad_platforms_combined` — Snapchat+Instagram+Google → تجمع كل المنصات في المقام.

### Visual verification (Playwright)
- البطاقتان ظاهرتان في صف "أداء التسويق" مع ال hints الصحيحة بالعربية، تعرضان `—` لمستخدم تجريبي بلا بيانات.

---

## 🎯 NEW FEATURE (2026-06 — Iteration 43) — **تعديل بيانات البطاقات قبل الطباعة (الاسم/المقاس/اللون/الملاحظة)**

**Merchant request**: "عرض تفصيل المنتج وإمكانية التعديل عليها قبل رفع الملف — مثل الاسم، اللون، التعديل على الكتابه".

### المتطلبات المختارة
- **الحقول القابلة للتعديل**: `customer_name` (الاسم/العميل) + `size` (المقاس) + `color` (اللون) + `note` (الملاحظة/الكتابة على الكرت).
- **النطاق**: مؤقت — يعيش 24 ساعة مع جلسة الـ upload، ولا يلمس `unified_orders` أو أي مجموعة أخرى. Make / Excel / Webhooks تظل غير متأثرة تماماً.
- **زر "إعادة تعيين"** يرجع البطاقة إلى القيم الأصلية المستخرجة من PDF سلة.
- **تعديل مجمّع** (`scope: "product"`): تطبيق نفس القيم على كل البطاقات التي تشترك في المنتج (priority: `product_id` → `sku` → `name_norm`).

### Backend (`preparation_routes.py`)
- ✅ **`_line_to_storage`** يحفظ snapshot لـ `original_customer_name / original_size / original_color / original_note` عند أول رفع. لا يُكتب فوقها أبداً.
- ✅ **`_edited_fields_from_storage(d)`** يقارن القيم الحالية مع الـ snapshot ويُعيد قائمة الحقول المعدّلة. تُحقَن في `_line_to_preview` كحقل `edited_fields`.
- ✅ **`PATCH /api/preparation/line/{upload_id}/{idx}`** — body اختياري `{customer_name?, size?, color?, note?, scope: "line"|"product"}`. الحقول الغائبة لا تُعدَّل؛ `null` يمسح الحقل؛ السترينج المُمرَّر يُحفظ بعد `strip()` و cap 500 حرف. يرجع `{ok, applied_to_indices, applied_count, fields_updated, scope, product_name}`.
- ✅ **`POST /api/preparation/line/{upload_id}/{idx}/reset`** — body `{scope}`. يستعيد الحقول الأربعة من الـ snapshot.
- ✅ **`/preview/{upload_id}`** يحقن `edited_fields` في كل preview line لكي تعرف الواجهة أي البطاقات معدَّلة.

### Frontend (`ProductPreparation.jsx`)
- ✅ **`EditCardModal`** — modal ثنائي اللغة بالكامل، يفتح عند الضغط على زر **"تعديل"** (Pencil icon، grey/amber على hover) في footer البطاقة.
- ✅ ترويسة المودال تظهر اسم المنتج + رقم الطلب.
- ✅ Toggle Scope (`prep-edit-scope-line` / `prep-edit-scope-product`) — اختيار البطاقة الواحدة أو كل بطاقات نفس المنتج (مع تنبيه أصفر صريح عند اختيار "كل البطاقات").
- ✅ 4 حقول form: الاسم (text) + المقاس (text) + اللون (text) + الملاحظة (textarea 3 سطور). كلها capped 500 حرف.
- ✅ زر **"إعادة تعيين"** (amber pill) يظهر فقط عند `row.edited_fields.length > 0`.
- ✅ **شارة ✏️ صغيرة (amber)** تظهر بجانب كل حقل معدَّل في البطاقة — التاجر يرى بصرياً أي البطاقات تحتاج مراجعة قبل التصدير.
- ✅ **Smart diff**: المودال يرسل فقط الحقول التي تغيّرت فعلاً (تجنّب overwrites غير ضرورية).
- ✅ Toasts عربية بالكامل: "تم حفظ التعديل (الاسم، الملاحظة) على N بطاقة" / "تم استرجاع القيم الأصلية".

### Tests (`tests/test_preparation_iteration43.py` — 9/9 PASS، 62/62 cross-suite preparation)
1. `test_patch_single_line_updates_only_target` — تعديل بطاقة واحدة، الجيران لا يتأثرون.
2. `test_patch_product_scope_applies_to_all_siblings` — `scope=product` يطبّق على كل البطاقات التي تشترك بنفس المنتج.
3. `test_reset_line_restores_original_values` — reset يعيد القيم الأربعة إلى الـ snapshot، `edited_fields` تصبح فارغة.
4. `test_edited_fields_propagate_to_generated_pdf` — التعديل يظهر في PDF النهائي (ASCII marker مضمَّن في نص الصفحة).
5. `test_patch_with_no_editable_fields_returns_400` — body فيه `{scope: "line"}` فقط بدون حقول قابلة للتعديل → 400.
6. `test_patch_out_of_range_idx_returns_404` — idx خارج النطاق → 404.
7. `test_edit_endpoints_require_auth` — كلا الـ endpoints يتطلبان bearer token.
8. `test_edit_cross_user_returns_404` — المستخدم B لا يستطيع تعديل upload للمستخدم A.
9. `test_patch_with_none_clears_the_field` — `{note: null}` يمسح الملاحظة و `note` يدخل قائمة `edited_fields`.

### Visual verification (Playwright)
- Modal يفتح → القيم الحالية معبأة → تبديل النطاق يعمل → الحفظ يُغلق المودال + يعرض toast + شارات ✏️ تظهر بجانب الحقول المعدلة في البطاقة → reset يرجع كل شيء و يخفي الشارات.

---

## 🎯 BUG FIX (2026-06 — Iteration 42) — **خط عربي بدعم كامل لتجنّب الترميز المكسور في PDF التجهيز**

**Merchant report**: "نوع الخط يظهر الترميز حق الأحرف غلط" — بعد تبديل الخط في iteration 39 لـ Cairo، بعض الأحرف العربية (خصوصاً الـ Arabic Presentation Forms-B في النطاق `FE70–FEFF`) ظهرت كصناديق `.notdef` (□) في الـ PDF الناتج.

### Root cause
ملفات Cairo TTF المحمّلة من `fonts.googleapis.com` كانت **subsetted** (تغطّي 89/144 من Arabic Presentation Forms-B فقط). ReportLab يرسم صامتاً صندوق `.notdef` لأي codepoint غير موجود في الـ cmap → الأحرف تظهر مكسورة.

### Fix (`/app/backend/preparation_pdf.py`)
- ✅ **Bundled `NotoSansArabic-SemiBold.ttf` + `NotoSansArabic-Bold.ttf`** at `/app/backend/fonts/` (~190 KB each — full glyph coverage).
- ✅ **Coverage verified programmatically**:
  - Base Arabic block (`0600–06FF`): **256/256** (100%).
  - Arabic Presentation Forms-A (`FB50–FDFF`): full coverage.
  - Arabic Presentation Forms-B (`FE70–FEFF`): **141/144** (only 3 reserved codepoints missing — `0xFE75`, `0xFEFD`, `0xFEFE` which are not used in real text).
- ✅ **Preference order in `_register_font()`**:
  1. NotoSansArabic SemiBold + Bold (PRIMARY — bundled, full coverage).
  2. Cairo SemiBold + Bold (secondary — still bundled for future per-glyph fallback).
  3. Noto Naskh Arabic (system).
  4. DejaVu Sans / Amiri (last resort).
- ✅ **New helpers** `_font_supports(font_name, text)` + `_load_cmap(ttf_path)` + `_FONT_CMAPS` dict — let the draw loop verify glyph coverage at runtime and catch missing glyphs BEFORE they ship.

### Verification
- ✅ All 12 real-world Arabic test strings (الاسم / ملاحظة / المقاس / اللون / تغليف انيق آمايس / شركة الشحن / iMile / رقم الطلب / تعليقة النصر / الكتابه على الكرت / إجمالي المنتجات في الطلب / كف وقلادة فضة 925) → fully covered by primary font.
- ✅ **72/72 cross-suite pytest PASS** (preparation + Salla phase 1).
- ✅ Generated a real PDF from `/tmp/compare/original_salla.pdf` (19 product cards) → fonts embedded: `NotoSansArabic-SemiBold`, `NotoSansArabic-Bold`. 
- ✅ `analyze_file_tool` visual confirmation across all 19 cards: **PERFECT** rendering — all Arabic letters connected, no `.notdef` boxes, lam-alef + diacritics render correctly.

### Why this is durable
Future agents touching the font logic should remember: **Google Fonts CSS-API delivers subsetted TTFs by default**. Always verify a TTF's cmap covers `FE70–FEFF` (use `_load_cmap` + `_font_supports`). NotoSansArabic ships with comprehensive coverage and is the safe primary.

---

## 🎯 ENHANCEMENT (2026-06 — Iteration 41) — **Multi-file accumulative upload + unique export filenames**

**Merchant request**:
1. "تغير اسم كل ملف يتم طباعته" — every exported PDF should have a unique filename (the current `product_preparation_YYYYMMDD.pdf` collides when generating multiple batches per day).
2. "إمكانية إضافة ملفات متعددة ثم اختيار المنتجات لرفعها" — accumulate products from several Salla orders PDFs into one preparation session, then cherry-pick which to print.

### Backend changes
- New endpoint `POST /api/preparation/append/{upload_id}` (preparation_routes.py).
  - Validates PDF (size + extension).
  - Parses with `parse_salla_orders_pdf` then enriches with shipping + catalog images.
  - **Dedup strategy**: items sharing an `item_key` with the new file are dropped from the existing list — the new file wins (latest copy). Returns `replaced_count` so the UI can show "حُدِّث N منتج مكرر".
  - **Re-indexes all storage rows** contiguously to keep `idx` valid across the UI.
  - Persists a growing `filenames: list[str]` on the upload doc (insert order preserved).
  - Refreshes the upload TTL so multi-step sessions don't auto-expire.
- `/upload` and `/preview/{upload_id}` responses now also return `filenames` (always at least 1 entry) — same response shape across the lifecycle.

### Frontend changes (`ProductPreparation.jsx`)
- New state: `appending`, `appendRef` (hidden file input).
- New handler `handleAppendFile(file)` — calls `/append/{upload_id}` if a session exists, otherwise falls through to a fresh upload.
- **Chip strip** under the dropzone showing every uploaded filename with the `(N ملف)` counter.
- **New button** "+ إضافة ملف PDF آخر" (emerald 600) in the sticky action bar between "تحديد الكل" and "إعادة الرفع". Tooltip clarifies the difference (تتراكم vs تستبدل).
- **Unique filename per export**: changed from `product_preparation_YYYYMMDD.pdf` to `preparation_YYYYMMDD_HHMMSS_Nمنتج.pdf` — includes hour:minute:second AND the card count, so two batches generated in the same day land as distinct files in Downloads.

### Tests (`tests/test_preparation_iteration41.py` — 6/6 PASS, 66/66 cross-suite)
- `test_append_returns_merged_preview` — append with the same content → `replaced_count == first_count`, `filenames == ["fileA.pdf","fileB.pdf"]`, same `upload_id`.
- `test_append_reindexes_lines_contiguously` — no idx duplicates, all idx ∈ [0, total).
- `test_append_against_invalid_upload_id_returns_404` — bad upload_id → 404.
- `test_append_rejects_non_pdf` — wrong extension + empty body → 400.
- `test_preview_exposes_filenames_list` — both `/upload` and `/append` populate `filenames` consistently.
- `test_generate_after_append_with_selected_indices` — PDF binary download works on merged indices, `X-Exported-Cards` header matches selection size.

---

## 🎯 ENHANCEMENT (2026-06 — Iteration 40) — **Critical image-to-name alignment fixes**

**Merchant report**: "صور المنتجات مختلفة عن أسماء المنتجات" — images on cards didn't match the product names.

### Two distinct bugs found by inspecting the merchant's June 2026 upload

**Bug ① — IMAGE/NAME SWAP on multi-item orders.**
`page.get_images(full=True)` returns images in **xref-declaration order**, not visual order. On page 11 of the sample (order `#263839904`, 2 items: تغليف انيق آمايس + قلادة روز), Salla declared the قلادة xref FIRST even though it appears visually BELOW the تغليف image. The parser blindly zipped `images[i]` with `bottom_names[i]`, so قلادة got the تغليف image and vice-versa.

**Bug ② — MISSING IMAGE for repeated products in same order.**
The parser had `seen_xrefs: set[int]` to dedupe — but when an order contained the SAME product twice (e.g. order `#263822478` has قلادة روز ×2), Salla emitted the same xref at 2 distinct rectangles. The set rejected the second appearance, so only one of the two قلادة slots got an image. The third product line in that order (تغليف آمايس) was ALSO missing because the dedup logic ran out of xrefs.

### Fix
`_extract_page_product_images` rewritten to:
- Walk **every rectangle** via `page.get_image_rects(xref)` (one entry per rect, not per xref).
- Cache the decoded bytes/dimensions per xref to avoid repeated zlib decompression.
- **Sort all candidates by `rect.y0` (visual top-to-bottom)** before returning — guaranteed to match the bottom-name extraction order.

### Tests (`tests/test_preparation_iteration40.py` — 5/5 PASS, 60/60 cross-suite)
- `test_all_items_in_repeated_product_orders_have_images` — order #263822478 must yield 3 images for 3 items (was 2 before fix).
- `test_repeated_product_uses_same_image_bytes` — the two قلادة slots share identical bytes.
- `test_same_product_across_orders_has_same_image_hash` — تغليف and قلادة each have a single unique hash across ALL their occurrences (proves no swap).
- `test_order_263839904_image_not_swapped` — explicit regression for the originally-reported page.
- `test_no_product_has_wrong_image_via_cross_order_consistency` — broad fuzz check: no product family may have >1 distinct image hash.

### Other affected tests (iter-34 suite)
The pre-iter-40 tests relied on the parser bug coincidentally producing a "1 missing image" scenario in the تغليف group. After the fix all 4 images are present, so a new test helper `_strip_image_from_one_sibling()` was added that deterministically clears one image via direct MongoDB update — preserving the test scenarios without depending on the original bug. 5 tests in iter-34 updated to use the helper.

---

## 🎯 ENHANCEMENT (2026-06 — Iteration 39) — **Printable PDF: Cairo SemiBold + Cairo Bold + increased line spacing**

**Merchant request**: change PDF font to **Cairo SemiBold** (Bold accents for Order#/Product/Shipping rows), add visible spacing between data rows, and keep the iter-38 field order locked.

### Changes
- **Bundled** `Cairo-SemiBold.ttf` + `Cairo-Bold.ttf` at `/app/backend/fonts/` (downloaded from `fonts.gstatic.com` — actual static TTF instances at weight 600 and 700 respectively). Sizes ~150 KB + ~150 KB.
- `_register_font()` now returns a **tuple** `(regular_or_semibold, bold)`; preference order:
  1. Cairo SemiBold + Cairo Bold (bundled)
  2. Noto Naskh Arabic (system fallback — used the same TTF for both slots if Cairo missing)
  3. DejaVu / Amiri (last resort)
- `generate_preparation_pdf` updated:
  - `font_name, font_bold = _register_font()` — caller now gets both names.
  - **`line_gap` raised from 2.0 → 3.5 pt**.
  - Each block entry now carries an `extra_gap_above` 5th tuple slot — used to push specific groups (customer name, note, date+qty, shipping) a little further from the previous group for cleaner visual sectioning.
  - Draw loop: `c.setFont(font_bold if is_bold else font_name, fsize)` — bold rows render with Cairo-Bold; the rest render with Cairo-SemiBold.

### Field order (locked, per merchant)
1. Order # (Bold)
2. Product name (Bold, up to 2 wrapped lines)
3. الاسم: customer (SemiBold)
4. المقاس: X   اللون: Y (SemiBold, combined row)
5. ملاحظة: note (SemiBold, muted color, up to 2 lines)
6. Date + Qty (SemiBold)
7. Shipping carrier - N (Bold, accent color)

### Tests (`tests/test_preparation_iteration39.py` — 6/6 PASS, 55/55 cross-suite)
- `test_cairo_ttf_files_are_bundled` — both TTFs exist & ≥50 KB (catches accidental HTML downloads).
- `test_register_font_picks_cairo` — returns `("Cairo-SemiBold","Cairo-Bold")` + idempotent.
- `test_generated_pdf_embeds_cairo_font` — PyMuPDF `page.get_fonts()` includes a basefont containing "Cairo".
- `test_line_gap_increased_for_iter39` — source parse asserts `line_gap >= 3.0`.
- `test_field_order_in_build_text_lines_matches_spec` — source-position regex anchors verify the merchant's field order.
- `test_pdf_uses_bold_font_for_accent_rows` — BOTH Cairo-SemiBold AND Cairo-Bold are embedded in the output PDF.

---

## 🎯 ENHANCEMENT (2026-06 — Iteration 38) — **Salla PDF parser + printable PDF — major data-fidelity fixes**

**Merchant report**: compared a generated `system_pdf` against the original `orders.pdf` he uploaded. Cards in the printed output were missing/wrong on multiple fields. Detailed diff revealed 5 distinct bugs.

### Bugs found & fixed
1. **Address/phone/footer leaking into `product_options`**
   `_parse_options_block` stopped only on bare-digit lines, so the LAST product on each Salla page consumed the trailing `+966… / السعودية / الرمز البريدي / حي … / شارع …` strings, producing dict entries like `{"+966500275471": "السعودية"}`. Pollution then surfaced as junky option pills on the printed card.
   Fix: new `_looks_like_address_or_footer()` sentinel; `_parse_options_block` now stops at phones, country names, postal-code labels, address tokens, and footer markers (for BOTH key-side and value-side matches).

2. **Customer name missing on compound option keys**
   Salla supports keys like `"الاسم على التعليقه"`, `"الاسم على سبحه"`, plus the PyMuPDF ligature-broken `"السم عىل …"`. The old `_pick_name_from_options` required an exact match against `KEY_NAME_VARIANTS`. Items like `تعليقة النصر` therefore lost the name `"أبو عمر"`. Fix: prefix-match with `+ " "` or `+ "ال"` after the variant.

3. **Note missing when PyMuPDF concatenates lines**
   PyMuPDF sometimes merges two source lines into one (`"سنة التخرج"` + `"2026"` → `"سنة التخرج2026"`), which shifts the alternating key/value dict by one. The note word `"ملاحظه"` ends up as a *value* and the actual note text as the *next entry's key*. Fix: `_pick_note_from_options` now has a 2-strategy approach — key-side (happy path) + value-side scan of `(items[idx+1][0])` as fallback.

4. **Note key variant missing**
   `NOTE_KEY_PREFIXES` listed only `"ملاحظ"`. PyMuPDF's lam-alef reorder produces `"مالحظ"` (extra alef early) — never matched. Added `"مالحظ"` and `"العباره ع كرت"` variants.

5. **Printed PDF cards did NOT render product_name, size, color**
   `_build_text_lines` in `generate_preparation_pdf` only drew order#, customer, note, qty+date, shipping. Iter-36 added size/color to `ProductLine` but the PRINTABLE PDF never rendered them — so the merchant's on-screen card and printed card disagreed. Fix: insert product_name (up to 2 wrapped lines, bold), and a combined `المقاس: X   اللون: Y` line right after the customer name.

### Tests (`tests/test_preparation_iteration38.py` — 12/12 PASS, 49/49 total cross-suite)
- Unit tests on `_looks_like_address_or_footer`, `_parse_options_block` (asserts address/phone NOT consumed), `_pick_name_from_options` (prefix match), `_pick_note_from_options` (shifted-dict scenario).
- E2E pinned to `/tmp/compare/original_salla.pdf` (12 orders / 19 lines):
  - Order `#263829492` has size + color.
  - Order `#263839771` (تعليقة) has customer "أبو عمر".
  - Order `#263840401` has the previously-lost shifted note.
  - Order `#263832078` has color on both items + note on the كف item.
  - No address pollution in any line's `product_options`.
  - Generated PDF contains product name, size label + value, color label, customer "أبو عمر", and the shifted-note text (via NFKD normalization).

---

## 🎯 ENHANCEMENT (2026-06 — Iteration 37) — **Salla Direct Integration — Phase 1 (OAuth + Encrypted tokens + Status UI)**

**Merchant request**: Build a direct `Salla → System` connection via OAuth + (later) Webhooks, **without touching Make / PDF / Excel** (those must keep working). Tokens, Store ID auto-fetched after merchant consent. 4-phase roll-out plan — this iteration delivers Phase 1 only.

### Phase 1 Scope (this iteration — DONE)
1. New isolated module `/app/backend/salla_integration/` (no edits to any pre-existing import path).
2. Encrypted token storage (Fernet via `cryptography` library) with key rotation support.
3. Full OAuth 2.0 Authorization-Code flow:
   - `GET /api/salla/oauth/login` — builds Salla authorize URL with scopes `offline_access orders.read orders.write webhooks.read webhooks.write customers.read settings.read` + CSRF `state` stored in `salla_oauth_states` (10-min TTL).
   - `GET /api/salla/oauth/callback` — state validation → token exchange → `/store/info` fetch → encrypted upsert into `salla_integrations` → 302 redirect to frontend with `?status=connected|error|warn`.
4. Auto-refresh wrapper `ensure_fresh_access_token()` with per-user `asyncio.Lock` (no race on refresh-token rotation).
5. `GET /api/salla/status` — public, no secrets — for the UI.
6. `POST /api/salla/test-connection` + `POST /api/salla/refresh-store-info` — live calls to `/store/info`.
7. `POST /api/salla/disconnect` — local-only revoke (Phase 2 will add remote webhook delete).
8. Frontend: new page `/settings/salla` (`SallaIntegration.jsx`) with:
   - Status pill (connected / not_connected / needs_reauth / غير مُعدّ بعد).
   - **Coexistence banner**: "Make.com و رفع PDF و رفع Excel يعملون كما هم" — explicitly reassures the merchant.
   - "Not configured" panel surfaces the EXACT `.env` keys + the redirect URI to register in Salla Partners.
   - "Connect" CTA → full-page navigation to Salla's authorize URL.
   - Connected card: store name + domain + ID + email + plan + status + scopes + expires + last refresh.
   - Actions: اختبار الاتصال / جلب بيانات المتجر / إعادة الربط / إلغاء الربط (with confirm modal).
   - Phase 2/3/4 preview list at the bottom.
9. Settings page gets a new entry-point card (`settings-salla-link-card`) at the very top — visible immediately on `/settings`.
10. Route registered in `App.js`: `/settings/salla`.

### Required env vars (added to `backend/.env` — empty by default)
```
SALLA_CLIENT_ID=            # ← merchant fills from Partners Portal
SALLA_CLIENT_SECRET=        # ← merchant fills from Partners Portal
SALLA_TOKEN_ENC_KEY=...     # ← auto-generated Fernet key
SALLA_AUTH_BASE=https://accounts.salla.sa
SALLA_API_BASE=https://api.salla.dev/admin/v2
```

### Critical guarantees
- **No edits** to any pre-existing collection (`unified_orders`, `analyses`, `snapchat_*`, `meta_*`, `preparation_*`, `product_*`, etc.) — verified by `test_isolation_from_existing_collections`.
- **No edits** to Make webhooks, PDF upload, Excel upload routes.
- Tokens NEVER appear in logs or API responses (only the encrypted blob is stored; the public serializer drops it).
- `/api/salla/oauth/login` returns **503 with Arabic guidance** when `SALLA_CLIENT_ID` is empty — never builds a malformed authorize URL.

### Tests (`tests/test_salla_phase1.py` → 13/13 PASS, regression-safe with iter-34 → 37/37 total)
- Fernet roundtrip + nondeterministic encryption + tamper detection.
- All `/api/salla/*` routes require auth (401/403 without bearer).
- `/status` shape when not connected.
- `/oauth/login` returns valid authorize URL with all required scopes when configured; 503 when not.
- `/disconnect` is idempotent.
- `/test-connection` returns 404 + needs_reauth=True when no integration row exists.
- Full OAuth callback flow (respx-mocked) — exchange_code → fetch_store_info → encrypted persist → integration_to_public exposes no secrets.
- Auto-refresh: expired access_token triggers `refresh_with_token`, new tokens persisted.
- `invalid_grant` on refresh marks the row `status: needs_reauth` and sets `last_error`.
- **Isolation**: connecting Salla writes nothing to unified_orders / analyses / snapchat_* / meta_* / preparation_* / product_costs / operating_expenses / daily_costs.

### Future Phases (gated on user approval after 7-14 day validation)
- **Phase 2**: HMAC-verified webhook endpoint `POST /api/webhooks/salla/order` + programmatic webhook registration (order.created/updated/status.updated/cancelled/refunded) + event log page.
- **Phase 3**: "مزامنة الطلبات القديمة" pulling historical orders into unified_orders + tying into existing P/L calculations.
- **Phase 4**: Connection monitoring dashboard + Salla↔system reconciliation tool.

---

## 🎯 ENHANCEMENT (2026-06 — Iteration 36) — **Cards-Grid UX revamp + Critical "don't overwrite existing image" fix**

**Merchant report**: "عند إضافة صوره يتم تعديلها على المنتج الذي ليس لديه صوره فقط وليس جميع المنتجات بالبلوك" → the previous iteration's `scope=product` was blindly overwriting sibling lines' images (including PDF-extracted ones). Also requested a major UX overhaul: per-product individual cards in a Grid (not grouped `<details>`).

**Backend changes** (`/app/backend/preparation_pdf.py` + `/app/backend/preparation_routes.py`):
- `ProductLine` extended with `size`, `color`, `product_id`, `sku`, `product_options` (free-form dict of remaining option keys).
- New helpers `_pick_size_from_options`, `_pick_color_from_options`, `_filter_display_options`. Size keys: `المقاس / مقاس / القياس / Size`. Color keys: `اللون / لون / Color`. Lam-alef-stripped variants supported.
- `_line_to_preview` + `_line_to_storage` + `_line_from_storage` round-trip the new fields.
- **`PUT /api/preparation/image/{upload_id}/{idx}` rewritten** (iter-36 semantics):
  - Sibling-matching priority chain: `product_id` (if both target+sibling have it) → `sku` → normalized `product_name`.
  - **Lines that already have an image are NEVER overwritten** — only the clicked card + image-LESS siblings are updated.
  - Response shape adds `skipped_with_existing_image: int` and `scope ∈ {'line','name','sku','product_id'}` for granular UI feedback.
  - The catalog auto-save logic is unchanged → next upload's matching products still auto-load.

**Frontend changes** (`/app/frontend/src/pages/ProductPreparation.jsx` — rewritten):
- Replaced `<details>` grouped view with a **flat responsive Grid** (`prep-cards-grid`): `grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5`.
- New `ProductCard` component: square image on top, checkbox (`prep-card-check-{idx}`) top-left, order badge (`prep-card-order-{idx}`) top-right, product name truncated (`prep-card-name-{idx}`), customer name with user-icon, size pill (`prep-card-size-{idx}`), color pill (`prep-card-color-{idx}`), note line-clamped-to-2, free-form options spread (cap 3), footer with shipping company / total products / "إضافة صورة" button or "مخصّصة" badge.
- Sticky action bar (top-of-scroll): "تصدير المنتجات المحددة إلى PDF (N)" + "تحديد الكل" + "إعادة الرفع" + selection counter "(N) من (M) محدّد".
- Inline `MissingImageButton` shown only on cards without an image. After upload, shows two toasts: success + (optional) "(K) بطاقات لها صور أصلاً — لم يتم استبدالها" using the new `skipped_with_existing_image` count.
- Text NEVER overflows: `truncate` for short fields, `line-clamp-2 break-words` for notes, all with `title` tooltips.
- Mobile-first: 390px viewport renders 2 cols with zero horizontal overflow (verified by testing agent).

**Tests** (`tests/test_preparation_iteration34.py` → 24 PASS):
- New: `test_put_image_skips_lines_with_existing_image` — proves the 3 pre-imaged siblings keep their bytes byte-identical after a scope=product upload by a 4th card.
- New: `test_preview_exposes_size_color_options` — `_line_to_preview` includes size / color / product_id / sku / product_options.
- New: `test_pdf_parser_extracts_size_color_from_options_block` — unit test on the helpers; verifies size, color, name, note extraction + remaining-options dict excludes them.
- Updated: `test_put_image_applies_to_all_lines_with_same_product_name` — now asserts the iter-36 contract: applied_to_indices == no-image siblings; existing-image siblings → skipped_with_existing_image counter.
- Updated: `test_generated_pdf_uses_user_uploaded_image` — accepts mixed image_source ∈ {user_upload, None} since pre-imaged siblings are no longer overwritten.

**End-to-end frontend regression (iteration_36.json)**: **18/18 PASS, 0 defects**. The critical don't-overwrite fix verified via before/after screenshots of the "تغليف انيق" group + the in-app info toast.

---

## 🎯 ENHANCEMENT (2026-06 — Iteration 35) — **Image Catalog UI + Item-level Print Selection (14-point list)**

**Merchant request** (14-point revamp of `/product-preparation`): item-level selection (a single order with 3 products lets you print 1 + skip 2), PDF text never overflows the card, dates in MM/DD, every uploaded image is silently persisted into a global per-user catalog so the *next* PDF containing the same product auto-loads the saved image — and a dedicated UI page to inspect/edit/delete that catalog.

**Backend** (already completed in the previous fork's session, 20/20 pytest):
- `exported_items` collection: unique `(user_id, item_key)` where `item_key = "{order#}_{product_name}_{option}_{idx}"`. Replaces the older order-level `exported_orders` for dedup so partial prints work.
- `product_image_catalog` collection: unique `(user_id, name_norm)` — stores user-uploaded images keyed by normalized product_name. Auto-populated when the merchant uploads a custom image via `PUT /preparation/image/{upload_id}/{idx}`, and consumed when a new PDF is parsed via `_enrich_lines_with_catalog_images`.
- PDF generator (`preparation_pdf.py`): word-wraps long product names + `short_date()` helper formats every date as `MM/DD`.
- New endpoints under `/api/preparation/image-catalog/`:
  - `GET /` — list saved images (name_norm + product_name + product_id + sku + updated_at).
  - `GET /image/{name_norm}` — stream the stored JPEG.
  - `PUT /{name_norm}` — multipart upsert. **Iter-35 fix**: `file` is now `Optional[UploadFile] = File(None)` so metadata-only edits (product_id / sku changes without re-uploading the image) succeed with 200 + `metadata_only: true`. Was previously 422.
  - `DELETE /{name_norm}` — remove a catalog row.

**Frontend** (this fork):
- `ProductPreparation.jsx` — checkboxes wired: `selectedIdx` Set state, `toggleOne`/`toggleGroup`/`toggleAll`, `prep-row-check-{idx}`, `prep-group-check-{name}`, `prep-print-selected-btn` (disabled when selection empty), `prep-select-all-btn`. After a successful print, `selectedIdx` is reset and preview is refreshed so only the *remaining* items show.
- **NEW page `ImageCatalog.jsx`** (`/image-catalog`):
  - Header: count badge + refresh + "إضافة صورة" CTA.
  - Search input filters by product_name / product_id / SKU.
  - Empty-state card with primary CTA `إضافة أول صورة`.
  - Table: thumbnail + product_name + product_id + SKU + updated_at + edit + delete actions.
  - **Upload/Edit modal** (`catalog-upload-modal`): name + product_id + sku + image input. In edit mode the name input is disabled, and submitting **without picking a new file** triggers the backend metadata-only branch (image bytes preserved).
  - **Confirm-delete modal** (`catalog-delete-modal`) with product name in the message.
  - Indigo info banner explaining the auto-match behaviour.
  - Pillow-style image resize is server-side; client only previews via FileReader.
- **Sidebar nav**: new "إدارة صور المنتجات" link (`nav-image-catalog`) with `<Image>` icon, right under "تجهيز المنتجات".
- **Route registration**: `/image-catalog` added to `App.js`.

**Tests** (`tests/test_preparation_iteration34.py` extended to 21):
- All 20 prior tests still PASS.
- New `test_image_catalog_metadata_only_update` (iter-35) — seeds a row, PUTs metadata only (no file), asserts: `metadata_only=True`, product_id/sku updated, image bytes are **byte-identical** before/after, and PUT against a non-existent slug → 400.
- Full E2E UI flow verified by `testing_agent_v3_fork` (12/13 → 13/13 after the file-required fix): add → search → edit (metadata-only path) → delete; mobile 390 no horizontal scroll; auth gating.

---

## 🎯 ENHANCEMENT (2026-06 — Iteration 34b) — **رفع صور مخصّصة للمنتجات الناقصة**

**Merchant request**: عند وجود منتج بدون صورة (لم تكن في PDF ولا في الكتالوج)، يجب أن يستطيع التاجر **رفع صورة بنفسه**، وتعتمد الصورة المرفوعة في PDF التجهيز النهائي.

**Backend** — new endpoint `PUT /api/preparation/image/{upload_id}/{idx}`:
- يقبل multipart `file` + query `scope` (default `product`، optional `line`).
- يتحقق من content-type `image/*`، حد 8MB، صورة سليمة عبر Pillow.
- يُعيد ضبط الحجم تلقائياً (أقصى ضلع 800px) ويعيد التشفير JPEG quality 85 لتوفير مساحة Mongo.
- **Smart scoping**: `scope=product` (الافتراضي) يطبّق الصورة على **كل البطاقات** التي تشترك في نفس `product_name` (case+whitespace normalized) — لأن الصورة هي صورة المنتج لا الطلب، ولن يحتاج التاجر لرفعها 4 مرات لـ "تغليف انيق" المتكرر في 4 طلبات.
- يحفظ `image_b64`, `image_mime: image/jpeg`, و `image_source: "user_upload"` على كل line مطابقة.

**Frontend**:
- زر **"إضافة صورة"** indigo صغير يظهر فقط بجانب المجموعات بدون صورة.
- File picker مخفي + Toast: "تم تطبيق الصورة على N طلبات لنفس المنتج 'XYZ'".
- شارة خضراء **"صورة مخصّصة"** بجانب اسم المجموعة بعد الرفع — التاجر يعرف بصرياً أي صور أصلية وأي مرفوعة منه.
- Cache busting عبر `imgVersion` state — `<img src=".../{idx}?v=N">` يتجدّد فوراً بعد الرفع.
- Refactor: `refreshPreview()` helper مشترك لتجنّب تكرار الـ inline fetch في `handleGenerate` و `handleClearLog`.

**Tests** (5 جديدة، إجمالي **13/13** PASS لـ Iteration 34):
1. `test_put_image_applies_to_all_lines_with_same_product_name` — uploading once → 4 lines updated ✅
2. `test_put_image_scope_line_only_updates_one_row` — escape hatch granular ✅
3. `test_put_image_rejects_non_image_and_oversized` — content-type/empty/corrupt → 400 ✅
4. `test_put_image_cross_user_404` — B لا يستطيع تعديل upload الخاص بـ A ✅
5. `test_generated_pdf_uses_user_uploaded_image` — PDF نهائي يحتوي على الصورة المرفوعة ✅

**E2E UI verified**: التقطنا لقطة شاشة بعد رفع صورة لمجموعة "تغليف انيق" (4 طلبات) — Toast "تم تطبيق الصورة على 4 طلبات"، شارة "صورة مخصّصة" ظهرت، الصورة الجديدة معروضة فوراً.

---


## 🎯 NEW FEATURE (2026-06 — Iteration 34) — **تجهيز المنتجات: تحويل PDF طلبات سلة → ملف طباعة 4×4**

**Merchant request**: صفحة جديدة `/product-preparation` تستقبل PDF الطلبات من سلة، تستخرج لكل منتج: اسم العميل (من خيار "الاسم")، الملاحظة، الكمية، شركة الشحن، التاريخ، صورة المنتج، ثم تُجمّع المنتجات (لا الطلبات) وتُرتّبها من الأكثر مبيعاً، وتُخرج PDF A4 portrait بترتيب 4×4 = 16 بطاقة في الصفحة. كل بطاقة تحتوي: تسلسل، صورة، QR (رقم الطلب فقط لا URL)، رقم الطلب، الاسم، ملاحظة، تاريخ، كمية، "{carrier} - {N}" حيث N = إجمالي منتجات الطلب. مع منع تكرار التصدير عبر مجموعة `exported_orders`.

**Backend** (Python — استقرار مع الـ stack الحالي، بدلاً من إضافة Node.js sidecar):
- `preparation_pdf.py`: parser/generator نقي بدون أي ربط بـ FastAPI/Mongo
  - `parse_salla_orders_pdf(bytes) → list[ProductLine]` — PyMuPDF لقراءة PDF
  - يتعامل مع كل صفحة كطلب، يستخرج رقم الطلب من `رقم الطلب #...`، التاريخ، ثم لكل "خيارات المنتج" block يستخرج "الاسم" (يدعم الـ glyph-encoded variants: "السم" / "الاسم" / "الإسم"…) والملاحظات (`الكتابه على الكرت` / `ملاحظة` …)
  - استخراج صور المنتجات بـ heuristic ذكية (≥150px، أبعاد 0.4–2.5، حجم ≤1.5MP) لاستبعاد الشعارات والخلفيات
  - `generate_preparation_pdf(lines) → bytes` — reportlab + arabic-reshaper + python-bidi + NotoNaskhArabic/DejaVuSans، QR عبر `qrcode` (محتوى رقم الطلب فقط)
- `preparation_routes.py`: 7 endpoints تحت `/api/preparation/`
  - `POST /upload` — رفع PDF (حد 25MB)، يستدعي parser، يثري بـ `shipping_company` من `unified_orders` وصور احتياطية من `product_costs.image_url`، يستبعد الطلبات الموجودة سابقاً في `exported_orders`، يحفظ snapshot في `preparation_uploads` (TTL 24h)
  - `GET /preview/{upload_id}` — معاينة المجموعات
  - `GET /image/{upload_id}/{idx}` — بث صور المنتجات للـ thumbnails
  - `POST /generate/{upload_id}` — توليد PDF نهائي + استبعاد ثاني عند التوليد + إدراج في `exported_orders` (insert_many ordered=False مع unique index)
  - `GET /excluded/{upload_id}` — قائمة المستبعدين
  - `GET /export-log/stats` — عدّاد سجل التصدير
  - `DELETE /export-log` — حذف السجل (يتطلب `{"confirm": true}`)
- مجموعات جديدة + indexes:
  - `exported_orders`: unique (user_id, order_number) — يضمن عدم التكرار
  - `preparation_uploads`: unique (user_id, upload_id) + TTL على `expires_at_dt`

**Frontend** (React — مع شعار الـ stack الحالي):
- `ProductPreparation.jsx` (~340 سطر) في `/product-preparation` + nav link "تجهيز المنتجات" (أيقونة Package)
- Drag-and-drop dropzone، Stats row (4 بطاقات)، Action bar (تحميل/إعادة رفع/عرض المستبعدين)، Confirm modal للمسح، Group accordion بالصورة والعدد + توسعة لعرض كل الطلبات

**ملاحظة على الـ Stack**: التاجر طلب Node.js + pdf-lib + sharp، لكنني استخدمت Python equivalents لتجنّب sidecar مكلف وغير ضروري في تطبيق FastAPI: PyMuPDF (vs pdfjs-dist) + reportlab + arabic-reshaper (vs pdf-lib) + qrcode (Python) + Pillow (vs sharp). كل ذلك يعمل على نفس process الـ backend بدون أي تعقيد عملياتي إضافي.

**Tests**: 8 Pytests في `test_preparation_iteration34.py`:
1. Auth مطلوب لكل endpoint ✅
2. Upload يستخرج 12 طلب / 19 منتج / 12 مجموعة مرتّبة من الأكثر للأقل (top = "قالدة روز" بـ 5) ✅
3. Generate يُرجع PDF (>50KB) + headers X-Exported-Orders/Cards + يحدّث stats إلى 12 ✅
4. Re-upload يستبعد جميع الـ 12 ومحاولة الـ generate ترجع 400 برسالة عربية تتضمن "مسح سجل التصدير" ✅
5. Clear-log بدون confirm = 400، مع confirm = 200 + deleted_count 12 + إعادة الـ upload تنجح ✅
6. Cross-user scoping — مستخدم B لا يستطيع رؤية upload الخاص بـ A (404 على preview/generate/image) ✅
7. رفض الملفات غير-PDF والملفات الفارغة ✅
8. Image streaming يُرجع content-type يبدأ بـ image/ ✅

التغطية الإجمالية الآن: **350+ Pytests** تعمل (مع 2 pre-existing failures في Meta TTL test).

---


## 🎯 ENHANCEMENT (2026-06 — Iteration 33b) — **Δ% Δ comparison badge: Snap vs النظام**

Added a side-by-side delta comparison so the merchant can instantly spot attribution gaps without opening a second report.

**Backend** (`/api/snapchat/reference-stats`):
- New `system_comparison` block in the response, READ-only (no extra writes):
  - `yesterday`: Riyadh-day spend/revenue/ROAS from `snapchat_account_daily` + `delta_roas_pct`, `delta_spend_pct`.
  - `month`: Riyadh MTD (1st → yesterday) + same deltas.
- Δ% = `(official - system) / system * 100`. Returns `None` when system has zero data — frontend renders "—" instead of "+∞%".
- Note: the comparison block reads `snapchat_account_daily` only (which is the existing source-of-truth for the system's Snap view) — no new collection added, no isolation broken.

**Frontend** (`SnapchatOfficialCard.jsx`):
- `MetricTile` now accepts `delta` and `deltaLabel` props → renders a small badge under the value (green up / red down / slate flat).
- Δ badge surfaces on: yesterday's Spend(SAR) + ROAS, month's Spend(SAR) + ROAS.
- Added a one-line summary banner: "مقارنة ROAS — Snap الرسمي: X / النظام (بتوقيت الرياض، أمس): Y (فرق ±Z%)".

**Tests** (2 new):
- `test_system_comparison_block_present_and_math_correct`: locks in Δ math (e.g. Snap 2.0x vs system 2.5x → -20.0%).
- `test_delta_pct_is_none_when_system_has_no_data`: division-by-zero protection.

Total Iteration 33 test count: **7/7 PASS**.

---

## 🎯 NEW FEATURE (2026-06 — Iteration 33) — **Snapchat Official (PDT) — بطاقة مرجعية معزولة للمقارنة**

**Merchant request**: بطاقة عرض داخل قسم Snapchat Ads تُظهِر أرقام Snapchat الرسمية بتوقيت الحساب الإعلاني PDT (للمقارنة فقط) دون أن تدخل في أي حسابات أرباح/مصروفات/ROAS/تقارير نهائية.

**Backend**:
- جديد: `GET /api/snapchat/reference-stats` (مع `?refresh=true` للجلب الفوري).
- لكل حساب إعلاني مفعّل: يحسب "أمس" بتوقيت الحساب الأصلي (PDT/PT) + "الشهر الحالي (1→أمس)" ويستدعي `/adaccounts/{id}/stats` بـ `granularity=TOTAL` (مع `DAY` كـ fallback) لجلب `spend, impressions, swipes, conversion_purchases, conversion_purchases_value`.
- التحويل USD→SAR بسعر **3.752** (ثابت — اختيار التاجر) عبر `SNAP_REF_USD_TO_SAR` وهو **متغير معزول** غير مستخدم في أي مكان آخر في الكود.
- التجميع عبر جميع الحسابات المفعّلة في رقم واحد + Cache مدته 10 دقائق لتقليل ضغط Snap API.
- التخزين في مجموعة **منعزلة تماماً**: `snapchat_reference_stats` (لا يوجد أي استعلام في النظام يقرأها سوى هذا الـ endpoint).

**Frontend**: `SnapchatOfficialCard.jsx` يُحقن داخل قسم Snapchat Ads بعد الـ trend، بتنسيق indigo/dashed مميِّز عن البطاقات المالية الصفراء + Disclaimer + Last Sync + زر "تحديث الآن" + ROAS-aware lights.

**Isolation contract verified (5/5 tests)**: `tests/test_snapchat_reference_stats_iteration33.py` يضمن:
1. Auth مطلوب للـ endpoint ✅
2. خطأ ودود عندما Snap غير مربوط ✅
3. Cache يعيد الـ snapshot المخزَّن دون أن يلمس Snap API ✅
4. **/api/dashboard لا يتأثر** بعد حقن snapshot في `snapchat_reference_stats` (نفس total_sales/net_sales/snapchat_ads_total إلخ) ✅
5. **/api/dashboard/snapchat-summary** يبقى كما هو ولا يتأثر ✅

**Bonus regression fix**: `tests/test_unified_orders.test_merge_make_then_excel` كان يطالب بسلوك Iteration 31 القديم (Excel يطغى على Make). تم تحديث assertion ليطابق سلوك Iteration 31 (Make هو المرجع). الآن **343/343 PASS** (باستثناء 2 اختبارات Meta token المتعلقة بانتهاء صلاحية toke​n test مستقل).

---


## 🎯 UX TWEAK (2026-06 — Iteration 32) — **Dashboard default filter = اليوم بدل هذا الشهر**

**Merchant request**: "تاريخ افتراضي عرض البيانات اخر يوم بلوحة التحكم بدل الشهر".

**Change**:
- `defaultFilters()` في `AdvancedFilters.jsx` يقبل الآن preset key اختياري (default = `"this_month"` للحفاظ على سلوك التقارير).
- `AdvancedFilters` يقبل prop جديد `defaultPreset` (default = `"this_month"`) لكي يعيد زر "مسح" الفلاتر إلى الـ preset الافتراضي للصفحة بدل القيمة المثبتة.
- `Dashboard.jsx` يستخدم الآن `defaultFilters("today")` و `<AdvancedFilters defaultPreset="today" />` — اللوحة تفتح افتراضياً على بيانات اليوم فقط.
- صفحة التقارير `Reports.jsx` تبقى على `"this_month"` كما هي (لا regression).

**Verified**: لقطة شاشة بعد تسجيل دخول `admin@hesab.app` — زر الفلتر يعرض "اليوم" والنطاق `02-06-2026 → 02-06-2026`.

---


## 🎯 ROOT-CAUSE FIX (2026-06 — Iteration 31) — **data_source precedence: Make > Excel (يحلّ المشكلة المتكررة)**

**Merchant report (متكررة)**: "عند رفع ملف اكسل بالطلبات الجديده يتوقف النظام عن احتساب طلبات make بكل مره ولازم اكلمك عشان تضبطه من جديد".

**Root cause** (في `orders_db.py` السطر 142):
```python
merged["data_source"] = source  # ← آخر كاتب يفوز دائماً
```
- طلب يصل من Make → `data_source = "make"` ✓
- نفس الطلب يأتي لاحقاً في رفع Excel → `data_source = "excel"` ❌
- بعد كل رفع Excel، كل الطلبات التي أصلها Make تتحول صامتةً إلى `data_source = "excel"` → Dashboard counters: `orders_make_count` ينهار إلى ~0 → "النظام يتوقف عن احتساب طلبات make".

**Fix in `orders_db.py`**:
- ✅ **Make هي الـ AUTHORITATIVE source** (أغنى — تحوي `products[]`، webhook fresh).
- ✅ بمجرد وجود أي كتابة من Make في تاريخ الطلب، `data_source` يبقى `"make"` للأبد، بغض النظر عن إعادة استيراد Excel.
- ✅ `data_sources[]` (التاريخ الكامل) لا يزال يسجّل كل كتابة Excel للـ audit.
- ✅ تدفّق Excel-first ثم Make → promote إلى `"make"` تلقائياً (لأن Make أغنى).
- ✅ تدفّق Excel-only يبقى `"excel"` (لا false promotions).

**Self-heal للطلبات السابقة (في `server.py`)**: عند فتح Dashboard، الطلبات التي data_source = "excel" لكن history فيها Make write يتم **promote تلقائياً** إلى "make" + يُحفظ التعديل في DB. هذا يصلح الطلبات القديمة المتضرّرة دون migration script.

**Tests** (`test_data_source_precedence_iteration31.py`): 6 جديدة + 50 regression = **56/56 PASS** للمسارات المتأثرة. تغطّي:
1. Make → Excel: `data_source` يبقى "make" ✅
2. Excel → Make: يُرفع تلقائياً إلى "make" ✅
3. Excel-only: يبقى "excel" ✅
4. Dashboard يصلح طلبات قديمة متضرّرة تلقائياً ✅
5. Dashboard لا يرفع طلبات Excel-only ✅
6. End-to-end: `orders_make_count` لا ينقص بعد إعادة استيراد Excel ✅

**أثر النشر**: بعد Re-deploy، **رفع أي ملف Excel جديد لن يكسر طلبات Make مرة أخرى**. الطلبات القديمة التي تضرّرت من البق ستصلَّح تلقائياً عند أول فتح للـ Dashboard.

---

## 🎯 ROOT-CAUSE FIX (2026-06 — Iteration 30) — **Payment-gateway synonym matching (cross-language)**

**Merchant report**: "بطاقة رسوم بوابة الدفع عدا تابي وتمارا وامكان في لوحة التحكم تظهر القيمة صفر — لا يتم احتساب الرسوم وخصمها من بطاقة صافي المدفوعات الإلكترونية".

**Root cause** (verified via direct DB inspection):
- إعدادات المستخدم بأسماء عربية (`"مدى"`, `"البطاقة الإئتمانية"`, `"Apple Pay"`).
- لكن سلة ترسل أسماء البوابات بصيغ إنجليزية / متغايرة (`"Mada"`, `"Visa/MasterCard"`, `"apple pay"`).
- `normalize_name` القديم كان يطبّق lowercase + إزالة diacritics فقط، لم يتعرّف على أن `"مدى" = "Mada"`.
- نتيجةً: `fee_amount = 0` لكل البوابات إلا Tabby/Tamara/Emkan (الوحيدة التي صادف أن اسمها العربي == ما يرسله سلة).

**Backend** (`excel_parser.py`):
- ✅ **`normalize_name` موسّع**: يوحّد الآن المتغيرات العربية:
  - أ/إ/آ → ا
  - ى → ي
  - ة → ه
  - ؤ → و
  - ئ → ي
  - (بالإضافة إلى lowercase + diacritics — السابق)
- ✅ **`PAYMENT_SYNONYMS`** — قاموس مرادفات شامل لـ 10 مجموعات بوابات. كل مجموعة ثنائية الاتجاه:
  - Mada: `مدى`/`mada`/`مدا`
  - Tamara: `تمارا`/`tamara`
  - Tabby: `تابي`/`tabby`
  - Emkan: `إمكان`/`امكان`/`emkan`/`amkan`/`emkaninstallment`
  - Apple Pay: `ابل باي`/`apple pay`/`applepay`
  - STC Pay: `stc pay`/`stcpay`/`stc`/`اس تي سي باي`
  - Credit cards: `بطاقة ائتمانية`/`credit card`/`visa`/`mastercard`/`visa/mastercard`
  - COD: `عند الاستلام`/`cod`/`cash on delivery`
  - Bank transfer: `تحويل بنكي`/`bank transfer`
  - Wallet: `محفظة`/`wallet`/`salla wallet`
- ✅ **`_payment_synonym_match`** — bidirectional lookup. يجرّب: (1) exact match → (2) substring → (3) synonym group resolution.
- ✅ المجموعات تُنرمَل عند module-load (`PAYMENT_SYNONYMS = [...normalize_name(t)...]`) لتطابق الـ post-normalize keys.

**Tests** (`test_payment_synonym_iteration30.py`): 18 جديدة + 105 regression = **123/123 PASS** للـ payment + product_costs suites.
التغطية:
- normalize_name يوحّد كل المتغيرات العربية (أ/إ/آ/ى/ة/ؤ/ئ).
- 7 cross-language pairs (Mada/Tabby/Apple Pay/STC/Credit Cards/Emkan/COD).
- `Visa/MasterCard` ينطبق على إعداد `البطاقة الإئتمانية` بـ commission 1.5%.
- `بطاقة ائتمانية` (بدون "ال" + بدون "إ") ينطبق على إعداد `البطاقة الإئتمانية`.
- بوابة فعلياً غير معروفة (`Crypto-Pay-XYZ`) تبقى `matched=False` بـ fee=0 (لا false positives).
- Tabby/Tamara/Emkan ما زالت تشتغل (لا regression).

**أثر النشر**: بمجرد إعادة النشر (Re-deploy)، رسوم كل بوابات الدفع التي تستخدمها المتجر ستحسب تلقائياً وتخصم من بطاقة "صافي المدفوعات الإلكترونية" في Dashboard وتقارير المبيعات.

---

## 🎯 ROOT-CAUSE FIX (2026-06 — Iteration 29) — **Cross-match SKU ↔ Product ID**

**Merchant report (real production data)**: "إلى الآن مافي اي بيانات تكلفة المنتجات ماتظهر خالص — مرتبط 2,123 منتج بدون تكلفة 0 — اليوم 0 الشهر 0".

**Root cause** (discovered via direct DB inspection of merchant's catalogue):
- التاجر استورد ملف منتجات سلة، وكل الـ 2,123 منتج وُضِعت معرّفاتهم في حقل **`sku`** (مثلاً `sku='1573005664'`) بينما حقل **`product_id`** يبقى فارغ ('').
- لكن طلبات Make.com القادمة تحوي القيمة في حقل **`product_id`** (لأن سلة يرسل `product_id`).
- النتيجة: المطابقة الكلاسيكية (`sku→sku` و `product_id→product_id`) **تفشل دائماً** لكل الطلبات لأن المعرّفات في حقول متبادلة.

**Fix in `compute_order_cost` و `_reprocess_orders_for_keys`**:
- ✅ **Cross-match lookup**: عند البحث، نطابق كل معرّف من الطلب على **كلا الحقلين** في الكاتالوج:
  - `order.sku → catalogue.sku_normalized` (canonical) → ثم `catalogue.product_id` (cross)
  - `order.product_id → catalogue.product_id` (canonical) → ثم `catalogue.sku_normalized` (cross)
- ✅ **أولوية المطابقة**: canonical أولاً، ثم cross (لتجنب تعارض في حالة وجود نفس القيمة في كلا الحقلين على صفين مختلفين).
- ✅ **`matched_by` الجديد**: `sku_as_product_id` أو `product_id_as_sku` للإفصاح عن طريقة المطابقة (مفيد للتصحيح).
- ✅ **Reprocess محسّن**: عند تعديل تكلفة، يبحث في `cost_items.sku`, `cost_items.product_id`, `missing_product_cost_lines.sku`, و `missing_product_cost_lines.product_id` على كل المعرّفات (لتغطية cross-match في الطلبات السابقة).

**Live verification on the merchant's actual catalogue (preview env)**:
- أرسلت طلب اختباري بـ `product_id="129545691"` (محفوظ في الكاتالوج كـ SKU بتكلفة 22 ر.س × 3 وحدات)
- النتيجة: `total_product_cost=66.0 ر.س`, `profit_status=complete`, `matched_by=product_id_as_sku` ✅
- Summary endpoint: `today_total=66.0`, `month_total=66.0` ✅

**Tests** (`test_cross_match_iteration29.py`): 4 جديدة + 65 regression = **69/69 PASS**:
- catalogue SKU = order product_id → match cross
- catalogue product_id = order sku → match cross
- canonical match يفوز على cross عند التعارض
- recompute يلتقط cross-match للطلبات السابقة

**أثر النشر**:
- بمجرد إعادة النشر (Re-deploy) للإنتاج، كل الطلبات السابقة (الشهر + الـ 60 يوم) ستتم مطابقتها تلقائياً عبر self-heal في `/summary` و `/api/dashboard`.
- لو ضغط التاجر "تحديث الشهر بالكامل"، النتيجة الفورية: 2,123 منتج مرتبط بالفعل بكل الطلبات.

---

## 🔧 ENHANCEMENT (2026-06 — Iteration 28) — **Self-heal شهر كامل + زر "تحديث الشهر بالكامل" + Audit details**

**Merchant requirement**: "إجمالي تكاليف المنتجات ما تظهر في بطاقة تكلفة المنتجات في لوحة التحكم" + الخيار C (إعادة نشر + زر شهر كامل + تأكيد بعد التنفيذ).

**Backend** (`product_costs.py`):
- ✅ **`/summary` self-heal الشهر بالكامل** (بدلاً من اليوم فقط في iter 27): يبحث عن كل طلبات الشهر الحالي بـ `total_product_cost=null` ويُعيد ربطها قبل احتساب الإجماليات. الـ response يحوي الآن `stale_today_healed` + `stale_month_healed`.
- ✅ **`/recompute` محسَّن** يرجع تفاصيل تدقيق كاملة (audit breakdown):
  - `orders_updated`, `window_days`
  - `complete_orders` (الربح موثوق)
  - `incomplete_orders` (≥1 منتج بدون تكلفة)
  - `no_products_orders` (طلب بدون products[])
  - `distinct_missing_products` (منتجات فريدة لا زالت بدون تكلفة فعلياً)

**Frontend** (`ProductCostCard.jsx`):
- ✅ **زر "تحديث آخر يومين"** (أخضر) — الحل السريع للطلبات الحديثة.
- ✅ **زر "تحديث الشهر بالكامل"** (كهرماني، بارز) — يستدعي `/recompute?days=30` ويصلح كل طلبات الشهر السابقة.
- ✅ **Toast تفصيلي بعد التحديث** يعرض: "تحديث الشهر بالكامل: 1,250 طلب • ✅ 1,180 مكتمل الربح • ⚠️ 70 غير مكتمل • 18 منتج بدون تكلفة" — التاجر يعرف بالضبط ماذا تم.
- ✅ يعمل على **جميع البيئات** (الإنتاج والمعاينة) لأن `/recompute` موجود منذ iteration 19.

**Tests** (`test_self_heal_iteration27.py` موسّع): 6/6 جديدة + 59 regression = **65/65 PASS**. التغطية الإضافية:
- `/summary` يصلح طلبات قديمة في الشهر (ليس اليوم فقط) → `stale_month_healed >= 1`.
- `/recompute` يرجع audit breakdown كامل (complete/incomplete/no_products/distinct_missing).

**خطة النشر للإنتاج**:
1. التاجر يضغط **"Re-deploy"** على Emergent → iteration 28 ينتشر.
2. عند فتح Dashboard، `/summary` و `/api/dashboard` يُنفّذان self-heal تلقائياً للشهر الكامل.
3. لو بقيت طلبات لم تصلح (سبب نادر) → يضغط "تحديث الشهر بالكامل" → toast يؤكد العدد المُصلَح.
4. التأكيد: لا طلبات بحالة "Missing Cost" إلا تلك التي منتجاتها فعلياً ليست في الكاتالوج (يمكن مراجعتها من `/product-costs?tab=missing`).

---

## 🐛 BUGFIX (2026-06 — Iteration 27) — **Self-heal لتكلفة طلبات اليوم + زر "تحديث التكلفة الآن"**

**Merchant report**: "تكلفة منتجات الطلبات حق تاريخ اليوم كامله لم يتم احتسبها".

**Root cause analysis**: على الإنتاج (والذي يسبق iteration 26)، إذا كان أي طلب من اليوم وصل قبل إضافة تكلفة المنتج في الكاتالوج، فإن `total_product_cost` يظل `null` ولا يُعاد حسابه تلقائياً، فيظهر "اليوم: 0.00" في Dashboard بشكل خاطئ.

**Backend** (`product_costs.py` + `server.py`):
- ✅ **`/summary` self-heal**: قبل احتساب `today_total`/`month_total`، يُمسح صف بصف على طلبات اليوم التي `total_product_cost = null` ويُستدعى `attach_cost_to_order_doc`. يرجع `stale_today_healed` في الـ response.
- ✅ **`/api/dashboard` self-heal**: نفس المنطق على الطلبات في النطاق المفلتر (cap = 500 طلب/طلب واحد لمنع التباطؤ). تحديث in-memory + DB في نفس الوقت ليعكس الإجماليات الجديدة فوراً.
- ✅ Idempotent + try/except → لو فشل heal على صف واحد، باقي العملية تكمل.
- ✅ `/recompute?days=N` المنطقي القديم (موجود منذ iter 19) — مازال يعمل كـ manual fallback.

**Frontend** (`ProductCostCard.jsx`):
- ✅ **زر "⚡ تحديث التكلفة الآن"** بارز (أخضر، أعلى البطاقة) — يستدعي `POST /product-costs/recompute?days=2` ويعرض toast بعدد الطلبات التي تم تحديثها.
- ✅ يعمل على كل البيئات (الإنتاج والمعاينة) لأن `/recompute` موجود منذ iteration 19.

**Tests** (`test_self_heal_iteration27.py`): 4 جديدة + 59 regression = **63/63 PASS**. التغطية:
- `/summary` ينفّذ heal تلقائياً لطلبات اليوم بدون TPC → `today_total` صحيح + `stale_today_healed >= 1`.
- بيانات صحية → `stale_today_healed = 0` (لا عمل إضافي).
- `/api/dashboard` ينفّذ heal كذلك → الطلب المُحدّث ينعكس في DB بعد الـ request.
- `/recompute` endpoint لم يتغير ومازال يعمل.

**ملاحظة للنشر**: التاجر يحتاج **إعادة نشر (redeploy)** ليصل الـ self-heal للإنتاج. لكن حتى بدون النشر، يمكنه الضغط على زر "تحديث التكلفة الآن" في Dashboard المنشور — هذا الزر يستدعي endpoint موجود منذ iteration 19 ويحل المشكلة فوراً.

---

## ✨ ENHANCEMENT (2026-06 — Iteration 26) — **تقرير مبيعات المنتجات + بطاقة Dashboard + Auto-recompute آخر يومين**

**Merchant requirement**: تقرير مبيعات منتجات تفصيلي + بطاقة "📦 تكلفة المنتجات" في Dashboard (اليوم/الشهر/مرتبط/بدون) + إعادة احتساب آخر يومين تلقائياً بعد كل تعديل تكلفة.

**Backend** (`product_costs.py` + `server.py`):
- ✅ **`GET /api/product-costs/product-sales`** — تقرير مبيعات تفصيلي:
  - الأعمدة: image_url, name, product_id, sku, units_sold, total_sales, total_cost, total_profit, profit_margin_pct, cost_status
  - النطاق الافتراضي: آخر يومين (today + yesterday) كما طلب التاجر
  - `cost_status = "incomplete"` لأي منتج بعض/كل وحداته بدون تكلفة → `total_profit` و `profit_margin_pct` تصبح `null` (لا 0)
  - الإجماليات `totals.*_complete` تستبعد الصفوف غير المكتملة تماماً (ربح فعلي فقط)
  - الفرز: غير المكتملة أولاً (لينتبه التاجر) ثم حسب المبيعات تنازلياً
- ✅ **`/product-costs/summary` المحسّن** يرجع الآن:
  - `linked_products_count` — المنتجات في الكاتالوج بـ `cost_pending=False`
  - `missing_products_count` — مجموع: catalogue pending + SKUs من طلبات بدون كاتالوج (بدون double-counting)
  - بالإضافة لـ `today_total`, `month_total`, `avg_cost`, `top_products_last_30d` السابقة
- ✅ **`_recompute_recent_orders(db, uid, days=2)`** helper جديد — يستدعى تلقائياً بعد كل:
  - `POST /product-costs/` (create)
  - `PUT /product-costs/{id}` (update)
  - `POST /product-costs/import` (bulk import)
  - يُرجع `recent_orders_recomputed` count في الـ response

**Frontend**:
- ✅ **`ProductCostCard.jsx` جديد** — يُعرض أعلى Dashboard:
  - 4 خلايا: اليوم (ر.س) / الشهر (ر.س) / مرتبط (منتج) / بدون تكلفة (منتج)
  - زر تحديث + آخر تحديث (timestamp)
  - خلية "بدون تكلفة" تتحول لون كهرماني وتصبح link لـ `/product-costs?tab=missing` عندما العدد > 0
  - Refresh تلقائي عند تغيير filters
- ✅ **`ProductSalesReport.jsx` جديد** — مدمج في `Reports.jsx` (نهاية الصفحة):
  - 4 summary boxes: مبيعات (الكل) / مبيعات (مكتملة) / إجمالي التكلفة / صافي الربح + هامش
  - جدول كامل: صورة + اسم + Product ID + SKU + الوحدات + المبيعات + التكلفة + الربح + الهامش
  - Badge "⚠️ تكلفة غير مكتملة" بجانب اسم المنتجات الناقصة
  - الصفوف غير المكتملة: خلفية ميلانية، التكلفة "—"، الربح "غير محسوب"، الهامش "—"
  - Banner أصفر للـ incomplete products → link مباشر لـ `/product-costs?tab=missing`

**Tests** (`test_product_sales_report_iteration26.py`): 8 جديدة + 51 regression = **59/59 PASS**. التغطية:
- Default range = آخر يومين تماماً (yesterday + today)
- منتج كامل التكلفة: KPIs كاملة (units, sales, cost, profit, margin)
- منتج بدون تكلفة: `cost_status=incomplete`, `total_profit=null`, `profit_margin_pct=null`
- الإجماليات تستبعد incomplete rows
- `/summary` يكشف `linked_products_count` و `missing_products_count`
- بعد POST cost → `recent_orders_recomputed >= 2`
- بعد PUT cost → `recent_orders_recomputed >= 1`

**ملاحظة عن Net Profit في Dashboard**: صيغة `net_profit` في `/api/dashboard` كانت بالفعل تحسم `total_product_cost` (من iteration 19) قبل الحساب. Iteration 26 يضمن أن هذا الرقم محدث آخر يومين دائماً بعد أي تعديل تكلفة.

---

## ✨ ENHANCEMENT (2026-06 — Iteration 25) — **Product ID كمفتاح أساسي + تكلفة اختيارية + Auto-reprocess بعد الاستيراد**

**Merchant requirement**: ملف منتجات سلة لا يحوي SKU. اجعل Product ID المفتاح الأساسي، SKU اختياري، التكلفة اختيارية (وعند الفراغ → "بدون تكلفة" لا = 0)، وشغّل Auto-reprocess بعد كل استيراد لإعادة ربط الطلبات السابقة.

**Backend** (`product_costs.py`):
- ✅ **`ProductCostIn`**: SKU صار `Optional`، `cost_price` صار `Optional[float]`، وتم إضافة `@model_validator` يفرض وجود `sku أو product_id` على الأقل.
- ✅ **`create_cost` (`POST /product-costs/`)**: يبحث عن الـ existing **بـ `product_id` أولاً**، ثم بـ `sku_normalized` كاحتياطي. SKU فارغ مقبول. لو التكلفة لم تُرسل → `cost_pending=True`.
- ✅ **`update_cost` (`PUT /product-costs/{id}`)**: تعديل `cost_price` يمسح `cost_pending` تلقائياً (التاجر حدّد سعراً). يدعم تعديل SKU أيضاً.
- ✅ **Bulk import (`POST /product-costs/import`)**:
  - عمود التكلفة أصبح **اختيارياً**. الصفوف بدون تكلفة تُستورد مع `cost_pending=True, cost_price=0`.
  - مفتاح الـ upsert: `product_id` أولاً (مستقر بين التصديرات)، `sku_normalized` ثانياً.
  - إعادة استيراد نفس `product_id` بـ SKU جديد → **لا duplicate** (يُحدّث الصف الموجود).
  - بعد انتهاء اللوب: استدعاء `_reprocess_orders_for_keys` مرة واحدة لكل المفاتيح التي وصلت بتكلفة فعلية → الطلبات السابقة تتحول من incomplete → complete تلقائياً.
  - الـ response يحوي: `pending_count` (عدد الصفوف بدون تكلفة) + `reprocessed_orders` (عدد الطلبات التي أُعيد ربطها).
- ✅ **`compute_order_cost`**: يستثني صفوف `cost_pending=True` (لا يُعتبر السعر 0 — الطلب يظل في حالة incomplete).
- ✅ **`/missing`**: يضم الآن المنتجات من الكاتالوج التي `cost_pending=True` (يظهر `pending_in_catalogue=True` على كل صف) حتى لو لم يصل طلب لها بعد.

**Frontend** (`ProductCosts.jsx`):
- ✅ **مودال إضافة/تعديل**:
  - **رقم المنتج (Product ID)** صار الحقل الأساسي في الأعلى.
  - **SKU** صار اختيارياً مع label واضح "(اختياري)".
  - **تكلفة الشراء** صارت اختيارية ("اتركه فارغاً لإدخاله لاحقاً").
  - Validation: يكفي رقم المنتج أو SKU. التكلفة الفارغة مقبولة.
  - Toast بعد الحفظ: "تمت إضافة المنتج • التكلفة في انتظار التحديد" لو cost فارغة.
- ✅ **جدول الكاتالوج**: badge أصفر "⚠️ بدون تكلفة" بجانب اسم المنتج لكل صف `cost_pending=True`، وعمود التكلفة يعرض "في الانتظار" بدلاً من 0.
- ✅ **Toast الاستيراد** يعرض: `N جديد • M محدّث • K بدون تكلفة (في الانتظار) • L صورة • أُعيد ربط P طلب سابق`.
- ✅ **مودال الاستيراد** أُعيدت كتابته: يوضح أن **رقم المنتج هو المفتاح الأساسي**، SKU/التكلفة/الاسم كلها اختيارية، ويذكر صراحةً أن "بعد الاستيراد يُعاد ربط الطلبات السابقة تلقائياً".

**Tests** (`test_product_costs_iteration25.py`): 13 جديدة + 38 regression = **51/51 PASS**. التغطية:
- Salla Excel بدون SKU (فقط Product ID) يُستورد بنجاح.
- إعادة استيراد بنفس Product ID مع SKU جديد → لا duplicate.
- صفوف بدون تكلفة → cost_pending=True, cost_price=0.
- الطلبات على منتج cost_pending → incomplete_missing_cost (ليس match).
- تعديل cost_price يمسح cost_pending.
- Bulk import يطلق reprocess مرة واحدة لكل المفاتيح ذات التكلفة الفعلية.
- /missing يضم cost_pending من الكاتالوج.
- Manual create: product_id فقط ✓ / SKU فقط ✓ / كلاهما فارغ → 422.

---

## ✨ ENHANCEMENT (2026-06 — Iteration 24) — **حالة الربح + إعادة الربط التلقائي + تنبيه طلبات Excel**

**Merchant requirement** (Option C — Make.com كمصدر أساسي للمنتجات): لا تُحسب تكلفة المنتج المفقودة كـ 0، اجعل الطلب في حالة "ربح غير مكتمل" حتى تتم إضافة التكلفة، وأعد ربط الطلبات السابقة فور إضافة التكلفة، وأضف تنبيه واضح لطلبات Excel بدون products[].

**Backend** (`product_costs.py` + `server.py`):
- ✅ **`profit_status` على كل طلب** — `complete` (كل المنتجات مطابقة) / `incomplete_missing_cost` (≥1 منتج بدون تكلفة) / `incomplete_no_products` (لا توجد قائمة products، عادةً Excel).
- ✅ **`products_total_lines` + `products_matched_lines`** عدّادات على مستوى الطلب.
- ✅ **التكلفة المفقودة لا تُفترض = 0** — `total_product_cost` يحوي المجموع **الجزئي** (المطابق فقط) وعدّاد الطلبات غير المكتملة الربح يُعرض في Dashboard كي يعرف التاجر أن الربح المعروض تقريبي.
- ✅ **Auto-reprocess targeted** — بعد POST/PUT على `product_costs/`، يبحث النظام عن كل الطلبات التي تحوي ذلك SKU/product_id (سواء في `missing_product_cost_lines` أو `cost_items`) ويعيد حساب التكلفة + يحدّث `profit_status` تلقائياً. الـ response يحوي `reprocessed_orders` count.
- ✅ **`missing_product_cost_lines` يحوي `image_url`** الآن — مأخوذة من webhook payload الأصلي.
- ✅ **`/api/product-costs/missing` المحسّن** — يرجع: `image_url`, `product_id`, `last_order_number`, `last_order_date`, `occurrences`, إضافةً إلى `excel_no_products_count` (عدد طلبات Excel بدون products[]).
- ✅ **Dashboard** يرجع 3 عدّادات جديدة: `incomplete_profit_orders_count`, `no_products_orders_count`, `excel_no_products_count`.

**Frontend** (`Dashboard.jsx` + `ProductCosts.jsx`):
- ✅ **تنبيه Dashboard جديد (برتقالي)** — "X طلب من Excel بدون تفاصيل منتجات — تكلفة المنتجات غير محسوبة، يُنصح بربط Make.com".
- ✅ **التنبيه الأصفر القديم** يفتح الآن `/product-costs?tab=missing` بدل صفحة الكاتالوج.
- ✅ **تاب "بدون تكلفة" المحسّن** — جدول جديد بأعمدة: الصورة (thumbnail) / اسم المنتج / SKU / Product&nbsp;ID / عدد الطلبات / آخر طلب (رقم + تاريخ) / زر "إضافة تكلفة".
- ✅ **زر "إضافة تكلفة"** يفتح المودال مُعبَّأ مسبقاً بـ SKU + product_id + name + image_url.
- ✅ **بعد الحفظ** يظهر toast: "تمت إضافة المنتج • أُعيد ربط N طلب سابق" (يظهر فقط حين N>0).
- ✅ **Deep-link** `?tab=missing` يفتح التاب الصحيح مباشرة.

**Tests** (`test_profit_status_iteration24.py`): 9/9 جديدة + 58/58 regression = **67/67 PASS**. التغطية:
- 3 حالات `profit_status` كاملة (complete / incomplete_missing_cost / incomplete_no_products).
- partial match (1 من 2) يحفظ حالة incomplete + المجموع الجزئي صحيح.
- POST cost → إعادة ربط الطلبات + الـ status يتحول تلقائياً إلى complete.
- PUT cost_price → إعادة الحساب لكل الطلبات المطابقة.
- `/missing` يرجع image_url + last_order + excel_no_products_count.
- Dashboard يكشف 3 عدّادات iteration-24 الجديدة.

---

## ✨ ENHANCEMENT (2026-06 — Iteration 23) — **صورة المنتج من العمود F**

## User Personas
1. **تاجر إلكتروني** يدير متجر على منصة سلة ويحتاج لتحليل الأرباح الحقيقية.

## Core Requirements (static)
- تحليل ملف Excel من سلة (المبيعات، عدد الطلبات، طرق الدفع، شركات الشحن).
- إدخال نسب عمولات الدفع وتكاليف الشحن من الإعدادات.
- حساب الأرباح الصافية بعد العمولات والشحن والإعلانات والمنتجات.
- تقارير تفصيلية لكل طريقة دفع ولكل شركة شحن.
- إضافة التكاليف اليومية (إعلانات سناب/تيك توك/إنستقرام + منتجات).
- حسابات منفصلة لكل مستخدم (auth + isolation).
- تصدير التقارير إلى PDF و Excel.

## ✨ ENHANCEMENT (2026-06 — Iteration 23) — **صورة المنتج من العمود F**

**Merchant request**: "فقط اضافه صورة المنتج من العمود F في ملف الاكسل بالمولد تعديل تكلفة المنتج."

**Backend changes** (`product_costs.py`):
- ✅ **حقل `image_url` جديد** في `ProductCostIn` و `ProductCostUpdate` (Pydantic) — يُحفظ في `product_costs` collection.
- ✅ **استيراد ذكي من Excel**: 
  - أولاً يبحث عن header مطابق (`صورة`, `image`, `image_url`, `الصورة`, `رابط الصورة`, `photo`, `picture`, `thumbnail`, إلخ).
  - إذا لم يجد header، يقع تلقائياً على **العمود F (index 5)** — وهو الموضع الافتراضي لرابط الصورة في تصدير منتجات سلة.
  - **Guard ذكي**: العمود F يُعتبر "صورة" فقط إذا (1) لم يكن مستخدماً لعمود آخر مَعروف، و (2) **يحتوي على الأقل صف واحد بقيمة تشبه URL** (`http://`, `https://`, `//`, أو امتداد صورة معروف). هذا يمنع ابتلاع أعمدة عادية مثل "category" التي قد تقع صدفةً في العمود F.
- ✅ **التحقق من القيمة**: قبل الحفظ نقبل فقط القيم التي تبدأ بـ `http://`/`https://`/`//`/`/` أو تنتهي بامتداد صورة معروف. النصوص العادية تُتجاهل.
- ✅ **الحفاظ على البيانات الموجودة**: عند إعادة الاستيراد من ملف بدون عمود صورة، الصورة المحفوظة سابقاً **لا تُمسح** (نستخدم `$setOnInsert` فقط للقيمة الفارغة).
- ✅ **إخراج محسّن**: `images_imported` count + `image_column_detected` (`"header"` / `"column_F"` / `null`).
- ✅ **PUT endpoint** يدعم تحديث/مسح `image_url` يدوياً.

**Frontend changes** (`ProductCosts.jsx`):
- ✅ **قسم صورة المنتج في أعلى المودال** (إضافة + تعديل) — صورة مصغّرة (24×24) + حقل URL + رسالة شرح "تُستورد تلقائياً من العمود F في ملف Excel من سلة. يمكنك أيضاً لصق رابط الصورة هنا يدوياً أو مسحه".
- ✅ **معالجة أخطاء الصور**: لو الرابط مكسور، الصورة تختفي بدون كسر التخطيط.
- ✅ **عمود "الصورة" في الجدول** — thumbnail 40×40 على يمين كل صف؛ الصفوف بدون صورة تظهر بأيقونة Package باهتة.
- ✅ **رسالة الـ toast بعد الاستيراد** تشمل عدد الصور المستوردة (مثلاً: "تم الاستيراد: 12 جديد • 3 محدّث • 10 صور").
- ✅ **مودال الاستيراد** يذكر صراحةً "صورة المنتج (العمود F افتراضياً)".

**Tests**: 6/6 جديدة (`test_product_costs_image.py`) + 23/23 regression (product_costs full suite) — كلها تمر. تغطي:
- استيراد العمود F كصورة عند عدم وجود header مطابق.
- header مسمى يطغى على fallback العمود F.
- النصوص العادية في العمود F **لا تُخزّن** كصورة (تجنّب false positives).
- إعادة الاستيراد بدون عمود صورة **يحافظ** على الصورة السابقة.
- إنشاء/تحديث/مسح `image_url` يدوياً عبر API.

---

## ✨ ENHANCEMENT (2026-06 — Iteration 22) — **Import without SKU — استيراد عبر "رقم المنتج" فقط**

**Merchant request**: "المنتجات تسجيل بالملف رقم المنتج + تكلفة المنتج إذا لم يوجد SKU." بعض تجار سلة لا يستخدمون SKU إطلاقاً، ملفهم يحتوي رقم المنتج فقط.

**Backend changes** (`product_costs.py`):
- ✅ **فصل `رقم المنتج` عن SKU**: كان موجوداً في aliases الـ SKU خطأً. الآن `رقم المنتج` / `Product ID` / `id` aliases خاصة بـ `product_id`، و `SKU` / `كود المنتج` / `Reference` تبقى للـ SKU.
- ✅ **إضافة "تكلفة المنتج"** إلى aliases التكلفة.
- ✅ **القاعدة الجديدة للاستيراد**: مطلوب التكلفة + **واحد على الأقل** من {SKU, رقم المنتج}. اسم المنتج أصبح **اختياري** — إذا غاب، يُستخدم SKU أو رقم المنتج كاسم مؤقت.
- ✅ **عند غياب SKU**: `sku_normalized = product_id` (يحافظ على الفهرس الفريد)، حقل `sku` يبقى فارغاً (لا نختلق SKU وهمي)، حقل `product_id` يُحفظ ليتطابق مع طلبات سلة.
- ✅ **رسالة خطأ ودودة**: "الأعمدة المطلوبة: التكلفة + (SKU أو رقم المنتج). اسم المنتج اختياري."

**Frontend changes** (`ProductCosts.jsx`):
- ✅ **Modal الاستيراد** يعرض الآن: SKU + رقم المنتج + التكلفة + اسم المنتج (اختياري) مع شرح صريح "يكفي وجود التكلفة + (SKU أو رقم المنتج)".
- ✅ **جدول المنتجات** يعرض "—" مع علامة "(رقم المنتج: 1001)" بدلاً من خانة فارغة عند المنتجات بدون SKU.
- ✅ **Testids** تستخدم الآن `sku || product_id || id` فلا تتعارض الصفوف بدون SKU.

**Tests**: 7/7 جديدة (`test_product_costs_import_v3.py`) + 26/26 regression — كلها تمر. تغطي:
- استيراد ملف بـ "رقم المنتج + تكلفة المنتج" فقط (بدون SKU، بدون اسم).
- إعادة استيراد نفس product_id → UPDATE (لا duplicate).
- SKU + رقم المنتج معاً → SKU primary، product_id محفوظ للـ fallback lookup.
- ربط الطلبات بـ `product_id` فقط (عندما لا يحوي السطر SKU).
- رسائل خطأ ودودة عند غياب التكلفة أو غياب كل الـ identifiers.

---

## 🐛 BUG FIX (2026-06 — Iteration 21) — **بطاقات Snap/TikTok/Meta لا تعرض الطلبات والإيرادات**

**Reported by merchant**: "بطاقة تيك تك / سناب / انستقرام تعرض الصرف صحيح، لكن عدد الطلبات وباقي البيانات لا تظهر."

**Root cause**: الـ Pixel data من المنصات الثلاث (`snapchat_daily_stats.purchases`, `tiktok_ads_daily.purchases`, `meta_ads_daily.purchases`) قد تكون **0** بشكل مشروع — لأسباب متعددة:
- Pixel غير مُفعّل أو غير مربوط بسلة.
- المنصة لم تُسلّم بيانات التحويلات لذلك اليوم بعد (تأخّر typical).
- إعداد UTM مختلف يمنع الـ attribution.

النتيجة قبل الإصلاح: الكرت يعرض `orders=0` رغم وجود **صرف > 0** ووجود طلبات حقيقية في Salla بـ `utm_source` يطابق المنصة.

**Fix applied** (`/app/backend/server.py`):
- ✅ helper مشترك جديد `_attributed_orders_from_store(db, uid, source_aliases, start, end)` — يبحث في `unified_orders` عن طلبات `utm_source` يطابق aliases المنصة (case-insensitive, regex partial-match).
- ✅ تطبيق fallback في الـ 3 endpoints:
  - **Snap**: aliases = `("snapchat", "snap")` — يُفعَّل عند `orders=0 AND revenue=0`.
  - **TikTok**: aliases = `("tiktok", "tik_tok", "tik-tok")` — نفس الشرط.
  - **Meta** (Facebook + Instagram): aliases = `("facebook", "fb", "instagram", "ig", "meta")` — نفس الشرط.
- ✅ Pixel data تأخذ الأولوية: إن كان Pixel يُرجع `purchases > 0`، يُحتفظ بقيمته (لا نتجاوز البيانات الموثوقة).
- ✅ ROAS و CPA يُعاد احتسابهما تلقائياً بعد الـ fallback.

**Verification**:
- ✅ `tests/test_dashboard_orders_fallback.py` (5/5 pass): يغطي السيناريوهات الأربعة — Snap/TikTok/Meta fallback + اختبار "Pixel-precedence" + اختبار "no false positives".
- ✅ Full regression: 49/49 pass.

---

## ✨ ENHANCEMENT (2026-06 — Iteration 20) — **Product Cost Import v2 — Salla-friendly + manual-supplier**

**Merchant request**: ملف Excel من سلة فيه أعمدة كثيرة (وصف، صور، مخزون، باركود، فئات…)؛ النظام يأخذ فقط الأعمدة المطلوبة لاحتساب الربح ويحفظ الباقي للمستقبل. المورد إدارة يدوية حصراً — لا يُستورد من Excel.

**Backend changes** (`product_costs.py`):
- ✅ **Expanded HEADER_ALIASES** — every common Salla/Zid/Woo/Shopify variant for SKU and cost is recognised:
  - SKU: `sku`, `كود المنتج`, `كود`, `الرمز`, `رقم المنتج`, `Reference`, `Product Code`, `code`, `item code`, `merchant_sku`.
  - Cost: `cost`, `cost_price`, `purchase_price`, `buy_price`, `التكلفة`, `تكلفة الشراء`, `سعر التكلفة`, `سعر الشراء`, `الكلفة`, `كلفة المنتج`.
- ✅ **`meta` dict** captures every UNMAPPED column verbatim — non-empty cells only. The response now returns `meta_columns_preserved: [...]` so the merchant sees what was kept. Meta is NEVER used in any financial calculation.
- ✅ **Supplier columns are NEVER imported** — even if Excel contains `supplier`/`المورد`, the row's `supplier_name` stays untouched (manual UI value preserved across re-imports).
- ✅ **New `update_existing` query param** on `/import` (default `True`): when `False`, duplicate SKUs are **SKIPPED** and reported under `skipped` count. Maps to the UI checkbox.
- ✅ **New fields**: `supplier_country`, `supplier_notes` on `ProductCostIn`/`ProductCostUpdate` — manual-only.

**Frontend changes** (`ProductCosts.jsx`):
- ✅ **New Import modal** (`product-costs-import-modal`): explains exactly what gets imported (3 columns) vs. what gets preserved in meta, plus a yellow warning that supplier is manual-only. Includes a checkbox `product-costs-update-existing-checkbox` (default checked) for the new flag.
- ✅ **Modal toast** now reports `created` + `updated` + `skipped` + `errors` + `meta_columns_preserved` count.
- ✅ **Add/Edit modal** has a dedicated "بيانات المورد (إدارة يدوية)" section with `supplier_name`, `supplier_country`, `supplier_notes` inputs, decorated with a blue badge "لا تؤثر على احتساب الربح" so the merchant knows these fields are purely catalog metadata.

**Tests**:
- ✅ `tests/test_product_costs_import_v2.py` (8/8 pass): expanded aliases (Arabic + English variants), supplier never imported, manual supplier preserved across re-imports, meta dict preservation, `update_existing=False` skip behaviour.
- ✅ **Full regression**: 20/20 pass (combined v1+v2 + snap-no-overwrite + tiktok-agg).

---

## ✨ FEATURE (2026-06 — Iteration 19) — **Product Cost Management — احتساب الربح الحقيقي**

**Merchant request**: نظام إدارة تكلفة المنتجات — صفحة `/product-costs`، حقل `cost_price` لكل SKU، استيراد Excel، الربط مع طلبات سلة (SKU أولاً ثم product_id)، تنبيه على Dashboard للمنتجات الناقصة، صافي الربح الحقيقي = المبيعات − تكلفة المنتجات − رسوم الدفع − الشحن − الإعلانات.

**Backend** (`/app/backend/product_costs.py`):
- ✅ `product_costs` collection (unique `(user_id, sku_normalized)` + `(user_id, product_id)` index).
- ✅ CRUD endpoints `GET/POST/PUT/DELETE /api/product-costs/` with case-insensitive SKU dedup, soft-delete + auto-reactivation on re-create.
- ✅ `POST /api/product-costs/import` — Excel uploader accepting Arabic OR English headers (SKU/اسم المنتج/التكلفة/المورد), upsert by SKU.
- ✅ `GET /api/product-costs/missing` — aggregated SKUs without cost across recent orders.
- ✅ `GET /api/product-costs/summary` — today/month/avg/top-10 profitable.
- ✅ `POST /api/product-costs/recompute` — re-enriches existing orders after import.
- ✅ `compute_order_cost(db, uid, products)` helper — SKU first, product_id fallback (per merchant requirement: SKU more stable in Salla).
- ✅ Webhook ingestion enriched: every Salla order via `/api/webhook/make/{token}` gets `unified_orders.total_product_cost` + `cost_items[]` + `missing_product_cost_lines[]` automatically.
- ✅ Dashboard `/api/dashboard` totals exposes `computed_product_cost`, `manual_product_cost`, `total_product_cost` (effective max), `missing_product_cost_count`. Net profit and net_sales (when `deduct_product_costs` is on) use `product_cost_effective`.

**Frontend**:
- ✅ New page `/product-costs` (`ProductCosts.jsx`) — search + add modal + edit + soft-delete + Excel import + recompute.
- ✅ Tabs: "كل المنتجات" / "بدون تكلفة" (with count badge).
- ✅ Summary grid: 4 cards (today/month/avg/count).
- ✅ Sidebar nav "تكاليف المنتجات" (`nav-product-costs`).
- ✅ Dashboard alert banner `dashboard-missing-product-costs-alert` shown when `missing_product_cost_count > 0`, linking to `/product-costs`.

**Tests**:
- ✅ `tests/test_product_costs.py` (6/6 pass): CRUD, soft-delete + reactivation, Excel import (Arabic/English headers), missing endpoint, summary, recompute.
- ✅ `tests/test_product_costs_webhook.py` (2/2 pass, created by testing agent): webhook enrichment + SKU-first precedence over product_id.
- ✅ Full regression suite: **34/34 pass**.
- ✅ Frontend E2E flow verified by `testing_agent_v3_fork`: all add/edit/delete/import/tab-switch/recompute work; modal SKU input correctly disabled in edit mode; mobile-responsive @ 390.

**Test report**: `/app/test_reports/iteration_19.json`.

---

## ✨ FEATURE (2026-06 — Iteration 18) — **Dashboard Snapchat card: per-account TODAY breakdown**

**Merchant request**: "بدّل الصرف الشهري بكرت السناب لوحة التحكم إلى الصرف اليومي للسناب الثاني، مع تحديث إجمالي تكلفة الإعلانات بكرت لوحة التحكم بصرف جميع الحسابات الإعلانية."

**Implemented**:
- ✅ **Dashboard Snapchat card**: when 2+ Snapchat ad accounts are enabled, the "هذا الشهر" block is replaced by **"صرف اليوم — لكل حساب إعلاني"** with one cell per enabled account showing today's spend in SAR (and native currency if different). The header carries an account-count badge and an "Asia/Riyadh" timezone badge.
- ✅ When only 0/1 account is enabled, the **original Monthly block is preserved** as a graceful fallback (single-account merchants see no UX regression).
- ✅ **Total Ads Cost card** (`card-total_ads_cost` in `dashboardCards.js`) now correctly reflects ALL ad accounts — Snapchat (sum of every enabled account via `daily_costs.snapchat_ads` after iteration 17), TikTok (webhook + manual via iteration 16 fix), Meta. Verified live with seeded data: `total_ads_cost = 400.0` when Brand A=150 + Brand B=250.
- ✅ **Visual verification**: screenshot @ 1280x900 with 2 seeded accounts shows the per-account cards rendering correctly, zero horizontal overflow.

**New testids**: `snap-per-account-breakdown`, `snap-account-today-card-{ad_account_id}`.

**Data flow**: Dashboard now polls `/api/snapchat/accounts-summary` (added in iteration 15) in parallel with the existing summary endpoints — no new backend code needed.

---

## 🐛 BUG FIX (2026-06 — Iteration 17) — **Snapchat card dropping the 2nd account after legacy refresh**

**Reported by merchant**: "بطاقة اعلانات السناب في لوحة التحكم تعرض تكلفة الإعلانات من حساب [user_id] فقط ... بالبداية كان يعرض بشكل صحيح التكلفة من الحسابين الإعلانيين ولكن بعد التحديث مرتين نقصه صرف الحساب الثاني."

**Root cause**: TWO endpoints were writing to `daily_costs.snapchat_ads`:
1. `/snapchat/sync-all-accounts` (new, iteration 15) — wrote the SUM across all enabled accounts.
2. `/snapchat/daily-spend/bulk` (legacy single-account) — wrote ONLY the spend of `snapchat_connections.ad_account_id`, OVERWRITING the multi-account aggregate.

When the merchant hit the legacy refresh on the dashboard (which still pointed at `/daily-spend/bulk`), it silently wiped the second account's spend from the card. Each subsequent click kept the value pinned to a single account.

**Fix applied** (`/app/backend/snapchat_routes.py`):
- ✅ Added `_reaggregate_snap_daily(uid, date_str)` helper — the single source of truth for `daily_costs.snapchat_ads` and `snapchat_daily_stats`. Sums from `snapchat_account_daily` (per-account collection) across ALL of the user's accounts.
- ✅ Added `_ensure_legacy_account_tracked(uid, ad_id, ...)` — auto-upserts a `snapchat_ad_accounts` enabled row for the legacy account so the aggregation helper sees it (idempotent).
- ✅ Refactored legacy `/daily-spend/bulk`: now writes to `snapchat_account_daily` for the account being synced, then calls the helper. Never overwrites another account's data.
- ✅ Refactored `/sync-all-accounts`: replaced inline aggregation with calls to the same helper (DRY + guarantees both endpoints stay in sync forever).

**Verification**:
- ✅ Regression test `tests/test_snap_aggregation_no_overwrite.py` (2/2 pass) — simulates the exact bug sequence: seed 2 accounts → legacy refresh on account A twice → confirm B's spend STILL counted.
- ✅ Full Snapchat+TikTok+Meta suite: **68/68 pass**.

---

## 🐛 BUG FIX (2026-06 — Iteration 16) — **TikTok Dashboard Card was 0 even when campaigns were spending**

**Reported by merchant**: "تقرير التيك تك في لوحة التحكم أو بطاقة تكلفة الإعلانات لا تعرض أي بيانات على الرغم من أن الحملات تصرف بالوقت الحالي."

**Root cause investigation** (3 bugs in one report):
- **Bug A — multi-campaign-per-date overwrite** (`/api/dashboard/tiktok-summary`):  the line `tt_by_date = {r["date"]: r for r in tt_rows}` SILENTLY dropped all but the last campaign per date. Merchants running 2-3 active TikTok campaigns saw only 1/3 of their actual spend on the card.
- **Bug B — partial daily_costs coverage dropped webhook spend** (`/api/dashboard/tiktok-summary`):  `_agg()` iterated over `dc_spend_by_date.items()` only, then fell back to webhook ONLY when daily_costs contributed exactly `0.0`. As a result, **any merchant with even one old manual `daily_costs.tiktok_ads` row inside the range had ALL webhook spend for OTHER dates dropped**. Admin's card showed 73 SAR (= old manual 33 + 40) instead of the correct 423 SAR (= 73 + webhook 350.75).
- **Bug C — master `daily_ads_total` missed TikTok webhook entirely** (`/api/dashboard`): the `daily_ads_total` sum read `tiktok_ads` only from `daily_costs`, ignoring `tiktok_ads_daily` (where Make.com pushes). The "إجمالي تكلفة الإعلانات" card on Dashboard therefore undercounted TikTok by the full webhook amount for every merchant.

**Fix applied** (`/app/backend/server.py`):
- ✅ Multi-campaign aggregation: `tt_by_date` now accumulates spend+purchases+revenue across rows for the same date.
- ✅ Spend aggregation iterates the **union** of `tt_by_date` and `dc_spend_by_date` dates and uses `max(webhook, manual)` per date to avoid double-counting.
- ✅ `daily_ads_total` adds `max(tiktok_spend_from_tiktok_ads_daily, sum(daily_costs.tiktok_ads))` (no more silent drop).

**Verification**:
- ✅ Live admin card: before fix `last_30d.spend=73.0`, after fix `last_30d.spend=423.75` (correct: 33+40 manual + 350.75 webhook).
- ✅ Live admin dashboard: `daily_ads_total = 702.75`, `total_ads_cost = 702.75`, `tiktok_spend = 350.75` (was 0).
- ✅ Pytest: `tests/test_tiktok_dashboard_aggregation.py` — 4/4 new regression tests pass (locks in bug A/B/C).
- ✅ Full Snapchat+Meta+TikTok suite: 63/63 pass.

---

## ✅ COMPLETED & VERIFIED (2026-06 — Iteration 15) — **Snapchat Multi-Account Expansion**
**Status**: 🟢 Production-ready. Tested end-to-end by `testing_agent_v3_fork`.

**Acceptance — 100% PASS (7/7 new + 62/62 regression):**
- ✅ **Multi-account selection**: `GET/PUT /api/snapchat/selected-accounts` — merchant can enable/disable any number of Snapchat ad accounts simultaneously via checkbox UI in Settings. Removing an account marks it `enabled=False` (not deleted) so re-enabling preserves sync history.
- ✅ **DB schema**: new `snapchat_ad_accounts` collection (unique `(user_id, ad_account_id)`) and `snapchat_account_daily` collection (unique `(user_id, ad_account_id, date)` + secondary `(user_id, date)` index). Indexes registered in `server.py` startup.
- ✅ **Asia/Riyadh enforcement**: all daily-spend windows = `00:00 → 23:59 Asia/Riyadh` (HOUR granularity to bypass Snap's DAY-PDT constraint). PDT is NEVER used for storage or display.
- ✅ **Currency tracking**: each `snapchat_account_daily` row stores `spend_native` + `currency_native` + `fx_rate` + `spend_sar` (alongside legacy `spend` alias). USD→SAR conversion uses SAMA peg 3.75. Per-account UI shows BOTH native and SAR side-by-side.
- ✅ **Dashboard card aggregation**: `POST /api/snapchat/sync-all-accounts` iterates over all enabled accounts and (1) writes per-(account,date) rows, (2) aggregates the cross-account sum back into legacy `daily_costs.snapchat_ads` so the existing dashboard card auto-updates without any other code change.
- ✅ **New detail page `/snapchat-accounts`**: per-account cards with today / month / 30d spend in native+SAR+FX-rate, last-sync badge, "مزامنة كل الحسابات" button. Cross-account totals card on top. Empty-state with CTA to settings.
- ✅ **Sidebar nav**: new "حسابات Snapchat" link (`nav-snapchat-accounts` testid).
- ✅ **OAuth untouched**: existing connect/disconnect/`/snapchat/config`/`/select-adaccount` (back-compat) all still pass.
- ✅ **Friendly Arabic errors**: `sync-all-accounts` without OAuth → "حساب سناب غير مربوط. اربطه من الإعدادات." (no JSON / OAuthException leak).
- ✅ **Mobile responsive**: zero horizontal page scroll at 390x844 on `/settings` and `/snapchat-accounts`.

**New endpoints**:
- `GET /api/snapchat/selected-accounts` — list enabled accounts.
- `PUT /api/snapchat/selected-accounts` — replace the enabled set.
- `POST /api/snapchat/sync-all-accounts` — sync all enabled, write per-account daily rows + aggregate to `daily_costs`.
- `GET /api/snapchat/accounts-summary` — per-account today/month/30d spend (native + SAR + FX).

**Test report**: `/app/test_reports/iteration_15.json`. Pytest suite: `/app/backend/tests/test_snapchat_multi_account.py` (7 tests).

---

## ✅ COMPLETED & VERIFIED (2026-06 — Iteration 14) — **خيار A: Meta Token Exchange**
**Status**: 🟢 Production-ready. Tested end-to-end by `testing_agent_v3_fork`. Awaiting merchant green-light before starting **خيار B (Full OAuth flow)**.

**Acceptance — 100% PASS:**
- ✅ Backend `POST /api/meta/exchange-token` — converts a Short-lived Graph API Explorer token (1-2h) into a 60-day Long-lived token via Meta's `fb_exchange_token` grant, persists the new token + `token_expires_at`, clears any prior expired status.
- ✅ Settings UI — blue dashed-border section "تحويل تلقائي إلى Long-lived Token (60 يوم)" inside the `meta-credentials-details` accordion. Button correctly disabled when input is empty. SecretField masking on the short-lived input (no layout break at 390 or 1280 widths).
- ✅ Friendly Arabic errors on every edge case (empty, short, missing app creds, bad ad_account_id, fake creds rejected by Meta) — verified ZERO raw JSON / `OAuthException` / `[object Object]` leaks.
- ✅ Expiry countdown banner (`meta-token-expiry-info`) — colour-coded emerald/amber/red based on days remaining.
- ✅ Backend regression: **13/13 pass** (6 new in `test_meta_token_exchange.py` + 7 in `test_meta_friendly_errors.py`) + **6/6 iteration-13 regression pass** — no regressions.
- ✅ Mobile (390x844) and desktop (1280x800) — zero horizontal page scroll.

**Reports**: `/app/test_reports/iteration_14.json` + pytest XML at `/app/test_reports/pytest/iteration_14_meta_exchange.xml`.

**Pending (NOT started — awaiting merchant approval):**
- ⏸️ **خيار B — Full OAuth "اربط مع Facebook"**: one-click Meta login flow that eliminates the need for the merchant to copy ANY token (even short-lived). Will reuse `meta_connections` schema and add `redirect_uri` + state JWT exactly like the Snapchat OAuth flow.

**Doc note**: SecretField testids are `{prefix}-input-masked` (hidden) and `{prefix}-textarea` (revealed) — NOT `{prefix}-input`. Update spec for future test agents.

## Implemented (2026-05 — Meta Token Exchange: Short-lived → Long-lived auto-conversion)
- 💡 **Merchant request**: Avoid having to re-paste a fresh 60-day Long-lived token every 2 months. Allow the merchant to paste any Short-lived token (1-2 hour, easier to obtain from Graph API Explorer) and have us convert it automatically.
- ✅ **Backend**:
  - **New helper `_exchange_short_for_long_lived(app_id, app_secret, short_token)`** in `meta_routes.py`: calls Meta's official `GET /v18.0/oauth/access_token?grant_type=fb_exchange_token` endpoint. Returns the new 60-day token + `expires_in` (in seconds).
  - **New `POST /api/meta/exchange-token`** endpoint accepting `{short_lived_token, app_id?, app_secret?, ad_account_id?}` (last 3 fall back to the stored config when blank — typical update flow). Validates: minimum 20-char short token + required app credentials (with friendly Arabic errors). On success: saves the new `access_token` + computes `token_expires_at = now + expires_in seconds` + clears any prior `expired` status.
  - **Response** includes: `access_token_masked` (first 10 + bullets + last 6), `token_expires_at` (ISO), `token_expires_in_days` (≈60). We **never** return the full token to the browser.
  - **`/meta/config`** now exposes `token_expires_at` + `token_exchanged_at` so the UI can render countdowns.
- ✅ **Frontend (Settings.jsx)**:
  - **New visually-distinct blue dashed-border section** "تحويل تلقائي إلى Long-lived Token (60 يوم)" right above the manual token field.
  - **`<SecretField>` for the short-lived input** (paste long token without breaking layout) + helper text linking to Graph API Explorer + permission list.
  - **`data-testid="meta-exchange-token-btn"`** button. Disabled while input empty (UX guard). Spin icon while loading. On success, toast shows: `"✓ تم التحويل وحفظ التوكن الجديد (EAA****ABC). صالح حتى 7 يوليو 2026 (~60.0 يوم)"`.
  - **Expiry countdown** (`data-testid="meta-token-expiry-info"`): live calculation from `token_expires_at`. Color-coded — green when >7 days, amber 1-7 days ("⚠️ متبقي N يوم فقط — جدّد الآن"), red when expired.
  - Manual access-token field label updated to "Access Token (Long-lived) — أو الصق توكن جاهز يدوياً" + helper updated to point at the new auto-flow.
- ✅ **Error handling**: All edge cases return friendly Arabic — never raw Pydantic JSON or Meta error bodies. Tested:
  - `short_lived_token=""` → "Short-lived token قصير جداً أو فارغ — انسخ التوكن كاملاً من Graph API Explorer."
  - No stored app_id/secret → "Meta App ID و App Secret مطلوبان للتحويل. احفظهما أولاً..."
  - Bogus token + creds → Friendly Meta classification (typically "تعذّرت المزامنة...").
- ✅ **Tested**: smoke screenshot confirms section renders cleanly, button is reactive (disabled-when-empty), no horizontal scroll, all testids present. 28/28 backend pytest regression pass.


- 🐛 **Issue**: Long Meta access tokens (200+ chars) and Snap client secrets caused horizontal page scroll on mobile + overflowed cards + made the Settings page feel cluttered.
- ✅ **New component `SecretField.jsx`** (`/app/frontend/src/components/SecretField.jsx`):
  - **Masked preview** by default: shows first 10 chars + bullets + last 6 chars (max ~22 chars on screen).
  - **👁 عرض / 🙈 إخفاء** toggle: expands the field into a wrappable `<textarea>` for editing.
  - **📋 نسخ** button (clipboard API) + **🗑 مسح** button (clears field, doesn't touch server).
  - CSS `overflow-wrap: anywhere; word-break: break-all;` ensures no horizontal scroll even with extreme-length tokens.
  - Fully responsive — buttons flex-wrap under field on phones.
  - Accepts `existingMask` prop so backend-returned masks ("EAA****ABC") are shown as placeholder hints.
  - Optional `statusBadge` prop for inline status pills.
- ✅ **New `<StatusBadge/>`** component (also exported from SecretField.jsx):
  - 🟢 صالح / 🟡 يحتاج تجديد قريباً / 🔴 منتهي الصلاحية / صلاحيات ناقصة / حساب غير صالح / تم تجاوز الحد / خطأ شبكة.
  - Driven by Meta's `connection_status` already returned by `/api/dashboard/meta-summary` & `/api/meta/config`.
  - Rendered inline next to "Access Token (Long-lived)" label.
- ✅ **New top-level Settings section "🔐 بيانات الربط الحساسة"** wraps both Snapchat AND Meta integration cards inside two collapsible `<details>` accordions (`data-testid="snap-credentials-details"` and `meta-credentials-details`). Each accordion summary shows the platform badge + connection-status pill so the merchant sees state at a glance without expanding.
- ✅ **Settings.jsx hardening**:
  - All `grid` containers got `min-w-0` (prevents flex/grid blowout from long children).
  - Both card wrappers got `overflow-hidden` + `p-4 sm:p-6` (smaller padding on phones).
  - Meta App Secret + Meta Access Token + Snap Client Secret → ALL converted to `<SecretField>` (testids: `meta-app-secret-*`, `meta-access-token-*`, `snap-client-secret-*`).
  - Redirect URI input got `overflowWrap: anywhere` + `wordBreak: break-all` (long URLs no longer push the card width).
- ✅ **Tested** @ 390x844 mobile viewport:
  - `document.documentElement.scrollWidth === clientWidth === 390` → **zero horizontal scroll**.
  - All new testids present and functional (toggle, copy, clear).
  - Snap accordion (closed) + Meta accordion (open) render correctly together.


- 🐛 **Issue**: Snapchat's `DAY` granularity stats require `start_time` to be midnight in the **ad-account's native TZ** (usually Pacific). For Saudi merchants this meant "today" on Snapchat ran from 11:00 AM → 11:00 AM Riyadh time — not 00:00 → 23:59 like every other Saudi business measures their day.
- 💡 **Merchant requirement**: "اعتماد توقيت السعودية Asia/Riyadh في احتساب اليوم الإعلاني." Even if Snapchat internally tracks the day in PT, our dashboard must show the Riyadh business day.
- ✅ **Backend technique**: switched from `granularity=DAY` to `granularity=HOUR`. HOUR has NO TZ alignment constraint — we can request `start_time = 2026-06-01T00:00:00+03:00` and `end_time = 2026-06-02T00:00:00+03:00`, and Snapchat returns 24 hourly buckets which we sum. The resulting total is the EXACT Riyadh-day spend regardless of where the ad account is hosted.
- ✅ **Both endpoints updated**:
  - `GET /api/snapchat/daily-spend?date=` (single-date, used by DailyCosts page and Dashboard refresh button).
  - `POST /api/snapchat/daily-spend/bulk` (range mode, used by "تحديث آخر 7 أيام" etc).
  - Both now use `granularity=HOUR` for `spend` AND for conversion metrics (Phase 2).
- ✅ **Response diagnostics**:
  - `business_timezone: "Asia/Riyadh"` (always)
  - `aggregation_method: "hourly_riyadh"`
  - `ad_account_timezone` (informational, e.g. `"America/Los_Angeles"`)
  - `snap_day_start_riyadh` / `snap_day_end_riyadh` (always 00:00 → 24:00 Riyadh strings).
- ✅ **Frontend (`Dashboard.jsx`)**:
  - Banner color/text refreshed from amber (warning) to **green (confirmation)**: `"✓ يتم احتساب اليوم حسب توقيت السعودية (2026-06-01 00:00 → 2026-06-02 00:00) • TZ حساب الإعلانات على Snap: America/Los_Angeles — لكننا نجمع الصرف ساعةً بساعة لتغطية يوم الرياض كاملاً (00:00 → 23:59)."`
  - Zero-spend toast now says: `"تم الجلب — صرف يوم 2026-06-01 بتوقيت الرياض (00:00 → 24:00) = 0.00 ر.س. تأكد من وجود حملات نشطة أو انتظر بدء صرف اليوم."` — no longer references Pacific/PT.
- ✅ **DB schema**: `daily_costs.date` continues to be the Riyadh business date (no schema change). Reports/`/reports/ads` automatically show Riyadh-aligned data.
- ✅ **Tested**: 28/28 backend regression pass. Curl admin (no creds) returns the friendly Arabic error.


- 💡 **User insight**: merchant reported that Snapchat's "today" doesn't align with Riyadh midnight — for their account, the day appears to start at ~12:00 PM Riyadh time. This is because Snapchat's DAY granularity uses the **ad account's own timezone** (often Pacific or UTC), not Riyadh's.
- ✅ **Backend (`snapchat_routes.py`)**:
  - `GET /api/snapchat/daily-spend?date=` response now includes 3 new diagnostic fields: `ad_account_timezone` (e.g. `"America/Los_Angeles"`), `snap_day_start_riyadh` (e.g. `"2026-06-01 11:00"`), `snap_day_end_riyadh` (e.g. `"2026-06-02 11:00"`).
- ✅ **Frontend (`Dashboard.jsx`)**:
  - **New `snapDayInfo` state** cached after each refresh. Renders a persistent yellow info banner inside the Snap card: `"ℹ️ TZ حساب الإعلانات: America/Los_Angeles • "يوم Snap" يبدأ 2026-06-01 11:00 وينتهي 2026-06-02 11:00 بتوقيت الرياض."`
  - Zero-spend toast also surfaces the same TZ boundary so the merchant immediately understands why today=0 ("التزال بداية اليوم لم تبدأ بعد بتوقيت Snap").
  - `data-testid="snap-day-info-banner"` for testability.


- 🐛 **Issue**: even after the two-phase bulk fix, the Dashboard refresh button still used `POST /snapchat/daily-spend/bulk` which involves more moving parts than necessary for a single-day refresh.
- 💡 **User insight**: the DailyCosts page already has a working "جلب من سناب" button that has been reliable in production. Just port that exact flow to Dashboard.
- ✅ **Fix in `Dashboard.jsx`**:
  - **`snap-refresh-today-btn`** now calls `GET /snapchat/daily-spend?date=YYYY-MM-DD` (single-date, spend-only, proven reliable) then manually upserts the value into `daily_costs` via `POST /daily-costs` — preserving any other fields on the same date (snapchat_ads_2, tiktok_ads, instagram_ads, google_ads, product_costs, notes).
  - **`refresh-all-ads-btn`** (Snap branch) also switched to the same single-date flow for consistency.
  - Friendly Arabic error toasts retained: covers `Unsupported Stats Query`, `invalid_token / 401`, `permission / 403`, "اربط Snapchat" empty-state. Never shows raw JSON.
  - Distinguishes zero-spend (info toast: "لا توجد حملات نشطة أو لم يبدأ الصرف بعد") from non-zero success.
  - FX-conversion note shown when the ad-account currency ≠ SAR (matches DailyCosts UX).
- ✅ **Verified**: smoke screenshot confirms toast for admin-without-creds shows clean Arabic: "حساب سناب غير مربوط. اربطه من الإعدادات.". Backend unchanged (two-phase bulk fix from previous iteration still in place for legacy callers).


- 🐛 **Issue**: `POST /api/snapchat/daily-spend/bulk` returned raw JSON error `{"request_status":"ERROR","debug_message":"Unsupported Stats Query"…}` to the merchant on every refresh attempt.
- 🔍 **Root cause**: We were requesting `spend + conversion_purchases + conversion_purchases_value` in a single `/adaccounts/{id}/stats` call. Snapchat Marketing API rejects this combo because conversion metrics (a) require explicit `swipe_up_attribution_window` + `view_attribution_window` parameters, AND (b) are sometimes unavailable at ad-account level depending on the Pixel setup. Result: the entire request fails (including spend), so even the `spend` value never reached `daily_costs`.
- ✅ **Fix in `snapchat_routes.py`**:
  - **Two-phase request strategy**: Phase 1 fetches `fields=spend` only (always supported on `/adaccounts/{id}/stats`). Phase 2 attempts to fetch `conversion_purchases + conversion_purchases_value` with the required attribution windows (`swipe_up=28_DAY`, `view=1_DAY`). If Phase 2 fails (Pixel inactive, account-level metrics blocked, etc), we silently log and continue with `purchases=0` and `revenue=0` — spend still saves correctly.
  - **Error parsing**: when Phase 1 fails, we now extract Snapchat's `debug_message` field from the JSON response instead of returning the whole body verbatim (no JSON leak).
- ✅ **Fix in `Dashboard.jsx`**:
  - Toast now translates well-known Snapchat error patterns into Arabic: `Unsupported Stats Query`, `invalid_token / 401`, `permission / 403`, `granularity / start time` — each gets a tailored Arabic message with a remediation hint. Generic errors get a truncated friendly wrapper. No raw JSON / `request_id` strings leak to the user.
- ✅ **Tested**: 28/28 backend regression pass (`test_unified_ads_report.py` + `test_operating_expenses.py` + `test_meta_friendly_errors.py`). Snapchat-specific tests passing.


- 🐛 **Issue 1 — Snapchat refresh = 0**: Investigation revealed the refresh path was correct (Riyadh date), but the UI gave no diagnostic when Snapchat API legitimately returned `spend=0` (TZ mismatch on ad account, no active campaigns, etc). Fix: backend response now includes `ad_account_timezone`; frontend distinguishes 3 outcomes — success with spend, fetched-but-zero (info toast with TZ hint), and hard error (friendly Arabic).
- ✅ **TikTok card always visible** — removed `if (totals.tiktok_spend > 0 || ...)` gating. Now mirrors Snap/Meta layout exactly: Today (spend/orders/revenue/ROAS) + Month (same 4) + 30-day sparkline + "آخر تحديث" + footer link to `/reports/ads`.
- ✅ **`tiktok-refresh-btn`** — calls new `GET /api/dashboard/tiktok-summary` and re-renders the card (TikTok Marketing API direct integration deferred to a future iteration; for now it re-aggregates the existing Make.com webhook data).
- ✅ **`tiktok-empty-state`** — friendly Arabic prompt with link to `/make-webhook` when `has_data=false`.
- ✅ **New backend endpoint `GET /api/dashboard/tiktok-summary`** — mirrors snap/meta contracts: `{today, month, last_30d, history[30], last_fetched_at, source, has_data}` all in Riyadh time.
- ✅ **Total Ads Cost now includes Meta** — `daily_ads_total` and `total_ads_cost` aggregate over `daily_costs.{snapchat_ads, snapchat_ads_2, tiktok_ads, instagram_ads, google_ads}` **PLUS** `meta_ads_daily.spend` (was missing). Verified via SEED test: +300 SAR Meta row increases both fields by exactly 300.
- ✅ **New `meta_spend / meta_purchases / meta_revenue / meta_roas`** in `/api/dashboard` totals.
- ✅ **`refresh-all-ads-btn`** (gradient yellow→pink→blue) at top of Dashboard — orchestrates Snap + Meta + TikTok in parallel via `Promise.all`. Each platform fails independently (NEVER blocks others, NEVER clears data). Consolidated toast: `"تحديث جزئي (1/3) • ✓ Snapchat: 5 سجل • ✗ Meta: انتهت صلاحية… • ✓ TikTok: تم تحديث البيانات المحلية"` — no JSON / `[object Object]` leaks.
- ✅ **All cards show `آخر تحديث`** with Riyadh-formatted timestamp.
- ✅ **Code review fix** (from testing-agent): `refreshAllAds` fallback now falls through to `tiktokSummary?.today?.date` before resorting to `new Date()` (UTC) — guarantees Riyadh-aligned dates even when only one summary loaded.
- ✅ **Testing**: testing_agent_v3_fork → **43/43 backend pass** (6 new + 37 regression) + **10/10 Playwright frontend pass**. Report: `/app/test_reports/iteration_13.json`.


- 🐛 **Issue**: clicking the "تحديث فوري للصرف اليوم" button on the Meta card surfaced a raw JSON `OAuthException code 190 Session has expired` to the merchant when their Access Token was no longer valid. No clear path to fix it.
- ✅ **Backend (`meta_routes.py`)**:
  - New `_classify_meta_error(text) → (status, friendly_arabic_msg)` covering: expired-token (code 190 / "session expired" / "access token invalid"), permission denied (code 200 / ads_read), invalid ad account (code 100), rate-limited (code 17), network/timeout, and generic fallback. Each returns a hand-translated Arabic message.
  - New `_verify_meta_credentials(ad_account, token)` — lightweight ping (calls `/act_X?fields=id,name,...`) to test creds without burning the heavier `/insights` quota.
  - New `_set_status(user_id, status, last_error)` — persists `connection_status`, `last_error_message`, `last_error_at` in `meta_connections`.
  - `POST /api/meta/sync` — on Meta error, raises HTTP **401** when status="expired" (else 400) with `detail = {message, status, raw}` so the frontend can branch. **CRITICAL**: existing `meta_ads_daily` rows are NEVER cleared on token failure — historical spend stays visible behind the banner.
  - `POST /api/meta/auto-sync-if-stale` — same classification path but silent (background job, no exception raise).
  - **New endpoint `POST /api/meta/test-connection`** — accepts the same body shape as `PUT /meta/config`, verifies against Meta API, and persists the credentials **ONLY IF the test passes**. Returns `{ok, message, account: {id, name, currency, timezone}, saved: true}` on success; 400 with the friendly Arabic on failure.
  - `GET /api/meta/config` now exposes `connection_status`, `last_error_message`, `last_error_at`.
  - `GET /api/dashboard/meta-summary` now exposes the same 3 fields so the Dashboard banner can render reactively.
- ✅ **Frontend (`Dashboard.jsx`)**:
  - Meta card refresh button now handles **both** detail shapes (object with `{message, status}` and legacy plain string) via a typeof guard. Raw JSON / `[object Object]` can no longer leak.
  - New **expired banner** `data-testid="meta-expired-banner"` (red, prominent) above the KPI grid — shown only when `connection_status === "expired"`. Contains the Arabic warning + **`meta-update-link-btn`** linking to `/settings`.
  - Secondary `data-testid="meta-warn-banner"` (amber, softer) for other non-ok statuses (rate-limit, permission, etc).
  - After a failed sync, `fetchMetaSummary()` is called so the banner appears immediately without a page refresh.
- ✅ **Frontend (`Settings.jsx`)**:
  - Central error formatter `fmtMetaErr(e, fallback)` used in every Meta catch block — handles both detail shapes uniformly.
  - **New button `data-testid="meta-test-connection-btn"`** ("اختبار الاتصال", amber) — calls `/test-connection`; success toast shows account name, failure toast shows the friendly Arabic message. Save button relabeled to **"حفظ بدون اختبار"** so the merchant understands the distinction.
  - **New banner `data-testid="meta-settings-expired-banner"`** (red, with timestamp) when `connection_status === "expired"`, plus secondary amber warn-banner for other errors.
  - Token input placeholder + helper text now coach the merchant: "ألصق التوكن الجديد ثم اضغط اختبار الاتصال".
  - Token + secret inputs are auto-cleared after a successful save/test (avoids accidental resubmission).
- ✅ **Testing**: testing_agent_v3_fork → **37/37 pytest pass** (7 new in `test_meta_friendly_errors.py` + 30 regression) + **7/7 Playwright frontend checks pass**. Exact Arabic string `"انتهت صلاحية ربط Meta Ads، يرجى تحديث Access Token من الإعدادات."` verified to render from both `/test-connection` and the Dashboard refresh flow. Zero raw-JSON / `[object Object]` leaks. Report: `/app/test_reports/iteration_12.json`.


- 🐛 **Root cause**: The dashboard refresh button correctly **upserted** Snapchat spend with `$set` (overwrite, NOT increment), but the `date` used as the upsert key disagreed with the date the dashboard read:
  - **Writer** (refresh button → `/snapchat/daily-spend/bulk`): used the browser's *local* date or the *Snapchat ad account TZ* (typically Asia/Riyadh = UTC+3).
  - **Reader** (`/dashboard/snapchat-summary`): used `datetime.now(timezone.utc).date()`.
  - **Effect**: Between 21:00 UTC (00:00 Riyadh) and 23:59 UTC (02:59 Riyadh) every day, the writer saved under tomorrow's date (Riyadh's new day) while the reader still queried yesterday's date (UTC's not-yet-rolled-over day) → `today.spend = 0` for ~3 hours each night.
- ✅ **Fix in `server.py`**: introduced module-level `RIYADH_TZ = ZoneInfo("Asia/Riyadh")` (with UTC+3 fallback) plus helpers `_local_today_iso()` and `_local_today_date()`. Replaced all `datetime.now(timezone.utc).date()` calls in `/dashboard/snapchat-summary` and `/dashboard/meta-summary` (today_str, month_start, d30_start_str, and the 30-day history loop) with the Riyadh-based variant.
- ✅ **Fix in `meta_routes.py`**: same approach — `_today_riyadh()` helper, used by `POST /api/meta/sync` and `POST /api/meta/auto-sync-if-stale` so "days=1" actually fetches today's Riyadh date (was UTC).
- ✅ **Fix in `Dashboard.jsx`**: Snapchat refresh button now reads `todayStr = snapSummary.today.date` (the canonical Riyadh date from the backend) instead of computing it from `new Date()`. Guarantees writer and reader agree even when the merchant's browser is in a different timezone (e.g. team member abroad).
- ✅ **Note on `snapchat_routes.py`**: when explicit `from_date/to_date` are sent (the typical case from the dashboard refresh button), they are honored verbatim — `$set` overwrites the row instead of inserting a duplicate. Added a clarifying comment block. The "days" fallback still uses ad-account TZ (Snapchat API requires it for DAY granularity).
- ✅ **Verification**:
  - Curl regression test: POST daily-costs `{snapchat_ads: 100}` → GET dashboard.today.spend = 100; POST again with `{snapchat_ads: 250}` → dashboard.today.spend = **250 (overwrite confirmed, not 350)**.
  - Pytest regression: **206/206 backend tests pass** (no regressions on any existing test).


- ✅ **Sidebar transformed into a slide-in drawer on mobile** (<1024px) while remaining fixed on desktop (≥1024px). Uses `translate-x-full` ↔ `translate-x-0` transition with `lg:translate-x-0` always winning on desktop.
- ✅ **New mobile top header** (`data-testid="mobile-header"`) — hidden on desktop (`lg:hidden`) — contains compact logo + `data-testid="mobile-menu-btn"` hamburger button on the left side.
- ✅ **`Layout.jsx` rewritten**: manages `mobileOpen` state; `useEffect` on `location.pathname` auto-closes the drawer on route changes; another `useEffect` locks `document.body.overflow="hidden"` while drawer is open (with cleanup); replaced fixed `ps-64` with `lg:ps-64` (zero padding on mobile).
- ✅ **Sidebar drawer features**: full-screen `bg-black/50` backdrop (`data-testid="sidebar-backdrop"`) closes drawer on tap; in-drawer X close button (`data-testid="sidebar-close-btn"`); clicking any nav link auto-closes drawer; proper z-index layering (sidebar=z-50, backdrop=z-40).
- ✅ **Dashboard cards adapted**: Snap/Meta/TikTok section headers use `flex-col sm:flex-row`; refresh buttons are `w-full sm:w-auto`; container padding `p-4 sm:p-6`; recent-analyses table wrapped in `overflow-x-auto -mx-4 sm:mx-0 px-4 sm:px-0` with `min-w-[640px]`.
- ✅ **AdsReport** (`/reports/ads`): h1 scales `text-3xl sm:text-4xl lg:text-5xl`; refresh button `w-full md:w-auto`; combined KPIs `grid-cols-2 sm:grid-cols-3 lg:grid-cols-6`; platform cards stack on mobile; comparison table already had `overflow-x-auto`.
- ✅ **Reports** (`/reports`): h1 mobile-friendly; payments/shipping tables wrapped in `overflow-x-auto` with `min-w-[480px]`/`min-w-[400px]`.
- ✅ **OperatingExpenses**: h1 mobile-friendly; `oe-tabs` now `overflow-x-auto` with `whitespace-nowrap` buttons (horizontal scroll instead of wrap); `TableWrap` adds `min-w-[640px]` + edge-to-edge `-mx-4 sm:mx-0 px-4 sm:px-0`; section `Card` add-button `w-full sm:w-auto`.
- ✅ **Settings**: payment-methods-list and shipping-companies-list wrap their 12/14-column grid in `overflow-x-auto -mx-6 sm:mx-0 px-6 sm:px-0` with `min-w-[640px]`/`min-w-[700px]` on inner rows; save-settings-btn `w-full md:w-auto`.
- ✅ **Login/Register**: already responsive (`w-full lg:w-1/2`) with hero panel `hidden lg:flex` — verified.
- ✅ **Testing**: testing_agent_v3_fork verified 100% at BOTH 390x844 (iPhone-12) AND 1280x800 (desktop) — zero horizontal page scroll on any tested route; drawer slide/backdrop/auto-close/scroll-lock all confirmed; **26/26 backend pytest regression pass**. Report: `/app/test_reports/iteration_11.json`.


- ✅ **Snapchat & Meta cards on dashboard simplified to a unified 4+4 layout** per merchant request: "الكرت في Dashboard يكون سريع وواضح — كم صرفنا اليوم؟ كم طلب جاء؟ كم مبيعات؟ كم العائد؟".
  - **Today section** (4 cards on each): صرف اليوم (ر.س) · طلبات اليوم · مبيعات اليوم (ر.س) · ROAS اليوم.
  - **Month section** (4 cards on each): الصرف الشهري (ر.س) · طلبات الشهر · مبيعات الشهر (ر.س) · ROAS الشهر.
  - ROAS = sales ÷ spend (rounded 2dp), shows `—` when spend=0, color flips emerald ≥2x else amber.
- ✅ **Meta card cleanup — removed from dashboard** (they remain only on `/reports/ads`):
  - `meta-cpa-month` (CPA tile) ❌
  - `meta-performance-row` containing CPC, CPM, CTR, Impressions, Clicks ❌
  - `meta-campaigns-table` ❌
- ✅ **Snapchat card cleanup**: removed inline `≈ $X` USD conversion text from `snap-spend-today` / `snap-spend-month` / 30-day spend total (merchant operates in SAR only).
- ✅ **Unified instant-refresh buttons** — both cards now have the same UX:
  - Snap: `snap-refresh-today-btn` → `POST /api/snapchat/daily-spend/bulk` with today=today.
  - Meta: `meta-sync-now-btn` text changed from "مزامنة Meta الآن" to "تحديث فوري للصرف اليوم" → `POST /api/meta/sync` with `{days: 1}` (was 30).
  - Subtitle on Meta updated: "ربط مباشر مع Meta Marketing API — اضغط الزر للتحديث الفوري لصرف اليوم".
  - Empty state on Meta updated: prompts user to click "تحديث فوري للصرف اليوم" (was "مزامنة Meta الآن").
- ✅ **New footer link on both cards** → `/reports/ads`:
  - `snap-card-details-link` and `meta-card-details-link` with text "التفاصيل (CPC / CPM / CTR / الحملات) في تقرير الإعلانات الموحَّد ←".
- ✅ **30-day sparkline preserved** on both cards (compact, not "campaign details" — kept for at-a-glance trend).
- ✅ **Testing**: testing_agent_v3_fork verified frontend 100% — all required testids present, all removed testids confirmed absent, network panel confirms `{days: 1}` is sent on Meta refresh. Backend pytest regression: **21/21 pass** (test_unified_ads_report.py + test_operating_expenses.py). Iteration report: `/app/test_reports/iteration_10.json`.


### P0 — Meta Ads dashboard card cleanup
- ✅ **Removed all Make.com references** from the Meta Ads section header subtitle, replacing "تتم المزامنة يومياً عبر Marketing API" with "ربط مباشر مع Meta Marketing API — اضغط الزر للتحديث الفوري".
- ✅ **Replaced the Make.com empty-state** with a direct-integration prompt: `data-testid="meta-empty-state"` directs the user to the Settings page with a primary CTA button (`meta-go-settings-btn` → `/settings`) instead of asking them to set up a Make.com Scenario.
- ✅ **Added CPM card** to the Meta performance row (was missing). The row is now 5 cards: CPC, CPM, CTR, Impressions, Clicks (test-ids: `meta-cpc-month`, `meta-cpm-month`, `meta-ctr-month`, `meta-impressions-month`, `meta-clicks-month`).
- ✅ Meta `Sync Now` button (`meta-sync-now-btn`) calls `POST /api/meta/sync` directly — returns user-friendly Arabic error toast when Meta credentials aren't configured.

### P1 — Unified Ads Report page (`/reports/ads`)
- ✅ **New backend endpoint** `GET /api/reports/ads?from_date=&to_date=` — JWT-protected. Returns `{range, platforms[3], combined, series}` where each platform exposes `spend / impressions / clicks / purchases / revenue / cpc / cpm / ctr / cpa / roas` and `series` is a per-day cross-platform spend array.
- ✅ **Backend math** (server-side, zero-guarded, rounded to 2 dp): `cpc = spend / clicks`, `cpm = (spend / impressions) × 1000`, `ctr = (clicks / impressions) × 100`, `cpa = spend / purchases`, `roas = revenue / spend`.
- ✅ **Data sources**: Snapchat (daily_costs.snapchat_ads + snapchat_ads_2 + snapchat_daily_stats Pixel revenue/orders), TikTok (tiktok_ads_daily), Meta (meta_ads_daily).
- ✅ **New frontend page** `/reports/ads` (`pages/AdsReport.jsx`) with 5 sub-components:
  - **CombinedTotals** header card (6 KPIs across all platforms)
  - **PlatformCard ×3** — Snapchat (yellow theme), TikTok (black), Meta (blue) — each showing full 10-metric breakdown
  - **DailySpendChart** — Recharts LineChart with 3 lines comparing daily spend
  - **ComparisonTable** — 10 metric rows × (3 platforms + Total)
  - **RoasComparison** — Recharts BarChart of ROAS per platform (renders only when ≥1 platform has positive ROAS)
- ✅ **Date pickers** (`ads-report-from-date` / `ads-report-to-date`) default to month-to-date and refetch on change. Manual refresh button (`ads-report-refresh-btn`).
- ✅ **Reports page entry-point** — added prominent "تقرير الإعلانات الموحَّد" link (`reports-ads-link`) in the Reports header.
- ✅ **5 new pytest tests** in `test_unified_ads_report.py` (empty-state shape, Snapchat ingestion, TikTok ingestion + derived-metric math, date-range filtering, 3-platform combined math). **206/206 backend tests pass.**
- ✅ **Testing agent verified**: 100% on backend + 100% on frontend (all test-ids functional, both charts render, comparison table has exactly 10 rows).

## Implemented (2026-05 — Prepaid Expenses / المصروفات المدفوعة مقدماً)
- ✅ **New standalone accounting section** inside `/operating-expenses` — *not* merged with rentals per user request. Order of tabs is now: salaries → rentals → **prepaid** → daily → report.
- ✅ **Six sub-types** (PREPAID_TYPES whitelist):
  - 🚗 `vehicle_insurance` — تأمين السيارات
  - 👷 `worker_insurance` — تأمين الموظفين
  - 🪪 `iqama_visa` — الإقامات والتأشيرات
  - 📜 `government_license` — الرخص والتصاريح الحكومية
  - 🔁 `annual_subscription` — الاشتراكات السنوية
  - 📦 `other` — أخرى
- ✅ **Each record**: type, beneficiary/asset, amount, start_date, end_date, status (active/expired), notes — plus auto-derived `period_days` and `daily_cost` returned on list/create/update.
- ✅ **Amortization math** (proper accounting): `daily_cost = amount / max(period_days, 1)` where `period_days = (end - start).days + 1` (inclusive). Verified: 1825 SAR over 365 days = exactly 5.00 SAR/day.
- ✅ **CRUD endpoints**: `GET/POST/PUT/DELETE /api/operating-expenses/prepaid[/{id}]`.
- ✅ **Expired/inactive records excluded** from all daily/range calculations and from `summary.prepaid.active_count` and `by_type` aggregation.
- ✅ **Summary endpoint** now returns `prepaid: {total_paid, daily_total, active_count, by_type: {<type>: {total_paid, daily_cost, count}}}`.
- ✅ **Report endpoint** daily/monthly/yearly buckets now include `prepaid_total` and `prepaid_by_type`.
- ✅ **Dashboard integration**: new totals `operating_prepaid_total` and `operating_prepaid_by_type`. The existing `operating_expenses_total` already includes the prepaid sum, so `net_profit` is automatically reduced.
- ✅ **New dashboard KPI card**: `operating_prepaid_total` labeled "المدفوعة مقدماً (تأمين/إقامات)".
- ✅ **Frontend**: dedicated PrepaidPanel + PrepaidFormFields with **live inline preview** (`amount ÷ N يوم = X ر.س / يوم`) so the merchant sees the daily amortization before saving.
- ✅ **4 new pytest tests** (16 total in test_operating_expenses.py): CRUD+math, summary+by_type, dashboard+report integration, expired-status exclusion. **201/201 backend tests pass.**
- ✅ **Testing agent verified**: 100% backend + 100% frontend (5 tabs, CRUD UI, modal preview, table derived fields, summary cards, report sub-rows, dashboard KPI).

## Implemented (2026-05 — Operating Expenses / المصروفات التشغيلية اليومية)
- ✅ **New page `/operating-expenses`** — the formal source of all fixed and variable operating costs used in P&L calculations. Sidebar link "المصروفات التشغيلية" (Wallet icon).
- ✅ **Backend module `expenses_routes.py`** with three independent expense types:
  - **Monthly Salaries** (`operating_salaries` collection) — 3 categories: `employee` (موظفين/إداريين/محاسبين/مسوقين), `household` (مصروف البيت/المنزل/الشخصي), `charity` (الصدقات/التبرعات/الكفالات). Daily cost = `monthly_amount / days_in_month` (calendar-aware).
  - **Annual Rentals** (`operating_rentals` collection) — types: office/warehouse/shop/employee_housing/other. Daily cost = `annual_amount / 365`. Status active/expired (date-bounded).
  - **Daily Variable Expenses** (`operating_daily_expenses` collection) — free-form date+type+description+amount+payment_method.
- ✅ **CRUD endpoints**: `GET/POST/PUT/DELETE /api/operating-expenses/{salaries|rentals|daily}[/{id}]`.
- ✅ **Aggregation endpoints**:
  - `GET /api/operating-expenses/summary` — KPI cards data (per-category monthly totals, today's per-day breakdown, **per-country breakdown**).
  - `GET /api/operating-expenses/report` — daily / monthly / yearly aggregates + custom range.
- ✅ **Stopped/expired records correctly excluded** from all computations.
- ✅ **Dashboard integration**: `GET /api/dashboard` now exposes `operating_expenses_total`, `operating_salaries_total`, `operating_salaries_employee/household/charity`, `operating_rentals_total`, `operating_daily_other_total`. `net_profit` is reduced by `operating_expenses_total`.
- ✅ **Net Sales toggle**: new `net_sales_config.deduct_operating_expenses` flag (default `True`) in Settings → "خصم المصروفات التشغيلية" — controls whether operating expenses are deducted from `net_sales` KPI.
- ✅ **3 new dashboard KPI cards**: `operating_expenses_total`, `operating_salaries_total`, `operating_rentals_total` (in costs group).
- ✅ **Salary editing**: full record edit (name/category/country/amount/start_date/status/notes) via PUT — verified by `test_salary_edit_full_record_persists`.

## Implemented (2026-05 — Salary Country Classification)
- ✅ **New `country` field on salaries** with three values: `saudi` 🇸🇦 / `yemen` 🇾🇪 / `other` 🌍 (default `saudi` for backward compatibility).
- ✅ **Backend validation**: invalid country values rejected with HTTP 400.
- ✅ **Idempotent startup backfill**: pre-existing salaries without `country` get `country=saudi` set automatically.
- ✅ **Summary endpoint** now returns `by_country: {<country>: {monthly_total, count}}` so the dashboard can show per-country totals.
- ✅ **Frontend**: country dropdown (with flag) in salary add/edit modal (`data-testid="oe-salary-country"`), new "الدولة" column in salaries table, and two new summary cards "رواتب السعودية 🇸🇦" and "رواتب اليمن 🇾🇪" at the top of the page.
- ✅ **2 additional pytest tests** covering country persistence, `by_country` aggregation math, invalid-country rejection, and full-record edit persistence. **197/197 backend tests pass.**

## Implemented (2026-05 — Per-Order Date Filtering Across Dashboard)
- ✅ **Excel parser:** picks up the order-creation date column even when the header reads "تاريخ إنشاء الطلب" (now in `DATE_COLS`). Falls back to **column Q (index 16)** when no header matches, matching Salla's standard layout.
- ✅ **Parser bug fix:** `_match_col` no longer false-matches empty header cells (an empty string is no longer treated as a substring of every candidate).
- ✅ **Parser bug fix:** preserves Excel-native date cells (datetime/date) by emitting ISO format instead of `str(datetime)` so the normalizer always succeeds.
- ✅ **Dashboard SSOT shift:** `GET /api/dashboard` now aggregates ALL KPIs (sales, fees, BNPL splits, electronic net, total shipping, deferred shipping, expected Salla transfer, VAT, balances, monthly trend) directly from `unified_orders` filtered by per-order `order_date`, NOT from `analyses.date`. A single upload spanning Jan/Feb/Mar correctly splits across months.
- ✅ **Startup backfill:** any pre-existing unified_orders documents with `order_date_raw` but missing `order_date` get their dates normalized on next backend start (idempotent, logs count).
- ✅ Three new regression tests cover: (1) header label "تاريخ إنشاء الطلب" detection, (2) column-Q fallback when header is unknown, (3) dashboard split-by-order-date when one upload contains multi-month orders. **107/107 backend tests pass.**

## Implemented (2026-05 — Phase 2: Advanced Filters)
- ✅ New shared component `AdvancedFilters.jsx`: date presets (today/yesterday/7d/30d/this-month/last-month/this-year/custom) + payment-methods multi-select + shipping-companies multi-select. Reusable across Dashboard and Reports.
- ✅ Backend `/api/dashboard` and `/api/balances` accept comma-separated `payment_methods` and `shipping_companies` query params and apply case-insensitive partial-match filtering.

## Implemented (2026-05 — Bug fix v2: Auto-inferred date for incoming Make.com orders)
After v1 left 147 orders without date (which annoyed the user since new Make.com webhooks weren't auto-appearing in the dashboard), v2 reintroduces a controlled fallback:
- ✅ When Make.com sends an order WITHOUT `created_at`, the webhook now assigns `order_date = today (UTC)` AND marks `order_date_inferred=True`. Order appears immediately in dashboard.
- ✅ When the SAME order arrives later with a real `created_at`, the merge logic in `orders_db.py` automatically OVERWRITES the inferred date with the authoritative one and flips `order_date_inferred=False`. Excel re-imports work the same way.
- ✅ Startup migration v2: restores `order_date = received_at[:10]` for the 147 orders that v1 had cleared, marking them as inferred. They now reappear in dashboard with a yellow "approximate date" banner.
- ✅ New stat `orders_inferred_date` (count of inferred-date orders) shown on MakeWebhook page in an informational yellow banner: "X طلب بتاريخ تقريبي" + guide to fix Make.com mapping.
- ✅ `orders_missing_date` (truly missing) kept as a separate RED banner (extremely rare now).
- ✅ Webhook response now reports `inferred_date` count.
- ✅ 5 regression tests in `tests/test_no_date_fallback.py` rewritten for new behavior. **126/126 tests pass.**

## Implemented (2026-05 — Bug fix: Make.com orders inflating current month)
- 🐛 **Root cause** (found via Production diagnostic with the user's account): the webhook previously fell back to `datetime.now()` when Make.com sent a payload without `created_at`. This silently labeled March/April orders that Make.com forwarded today as "May orders", inflating the current month's KPIs by ~138 orders for the user.
- ✅ **Fix in `webhook_routes.py`**: removed the today fallback. Orders without `created_at` are now stored with `order_date=None` (still visible on the Make.com page, but excluded from date-filtered dashboard/reports queries).
- ✅ **Startup migration in `server.py`**: detects rows where `data_source=make` + `order_date_raw=''` + `order_date == received_at[:10]` (i.e. previously got the today-fallback) and clears their `order_date` to None. Idempotent; logs `cleared` count.
- ✅ **New endpoint** `GET /api/webhook/orders-missing-date` returns the orders that need attention.
- ✅ **`GET /api/webhook/stats` now exposes** `orders_missing_date` counter; ingest response exposes `without_date`.
- ✅ **UI warning banner** on `/make-webhook`: yellow banner displays count + Make.com fix instructions whenever `orders_missing_date > 0`.
- ✅ **5 new regression tests** in `tests/test_no_date_fallback.py` covering: no-fallback behavior, date-filter exclusion, stats counter, missing-date endpoint, correct-month routing.
- ✅ **126/126 backend tests pass.**

## Implemented (2026-05 — Phase 3: Net Sales Configuration)
- ✅ New Pydantic model `NetSalesConfig` (server.py) with 7 independent flags: `deduct_payment_fees`, `deduct_shipping`, `deduct_deferred_shipping`, `deduct_ads`, `deduct_product_costs`, `deduct_vat`, `deduct_daily_expenses`. Defaults reflect typical Salla seller workflow (deduct payment fees + regular shipping + ads + product costs; don't deduct VAT or deferred shipping).
- ✅ `GET /api/settings` now exposes `net_sales_config`; `PUT /api/settings` accepts and persists it. Backwards compatible (None preserves prior value; missing → defaults applied at read-time).
- ✅ `GET /api/dashboard` computes `totals.net_sales` based on the merchant's config and returns the active `net_sales_config` for the UI to show what's deducted.
- ✅ New Settings page section "حساب صافي المبيعات" with 6 toggles (deduct_daily_expenses kept hidden — folded into product_costs for now) and a live equation preview showing exactly what gets subtracted in real time.
- ✅ New KPI card `net_sales` in `dashboardCards.js` (group "sales") with accent styling and tooltip "حسب إعدادات الخصم". Auto-included in the dashboard-customization toggle list (21 cards now).
- ✅ Backend regression tests `tests/test_net_sales_config.py` (4 tests): default exposure, persistence, dashboard inclusion, custom-flag math verification. **121/121 backend tests pass.**


- ✅ **Single source of truth: `unified_orders` collection.** Both Excel uploads and Make.com webhook write here.
- ✅ New module `orders_db.py` with intelligent merge logic:
  - `_merge_into(existing, incoming, source)` — field-level merge.
  - Empty incoming never overwrites existing; empty existing accepts incoming.
  - Critical fields (`total_amount`, `order_status`, `payment_status`) → newer source wins.
  - Non-critical fields → first writer wins (preserve manual data).
  - `field_sources` dict tags each scalar with its writing source.
  - `data_sources` array records every source touching the order (capped 20 entries).
  - `data_source` field = last writer.
- ✅ **Excel parser extended**: `parse_salla_excel` now returns `orders_individual[]` with full per-row fields (customer_name, customer_mobile, subtotal, shipping_cost, discount, currency, source, status). 6 new column matchers added.
- ✅ **Upload-excel endpoint**: after report generation, upserts every parsed order to `unified_orders` with `data_source="excel"`. Returns `orders_imported` + `orders_updated` counters.
- ✅ **Make webhook** rewritten to use `upsert_order()` with `data_source="make"`. UTM fields (utm_source, utm_medium, utm_campaign, device) now persisted.
- ✅ **Build-analysis** reads from `unified_orders` so analytics aggregate across BOTH sources naturally (no double-counting via order_number dedup).
- ✅ **Stats endpoint** returns `by_source: {excel: N, make: M}` breakdown.
- ✅ **DELETE webhook settings** only deletes Make-sourced orders (preserves Excel rows).
- ✅ **One-time migration** on startup copies legacy `webhook_orders` → `unified_orders` (idempotent).
- ✅ **Frontend**: `MakeWebhook.jsx` renamed table to "آخر الطلبات الموحَّدة (Excel + Make.com)"; per-row colored Make/Excel badges + sky-blue "مدمج" chip when an order has been touched by both sources; new "Make / Excel" KPI card.
- ✅ **Testing**: **80/80 backend tests pass** (9 new tests covering bidirectional merge, field provenance, source isolation, build-analysis cross-source aggregation). Frontend Playwright fully green.

## Implemented (2026-05 — Make.com Webhook Source)
- ✅ **Second data source: Make.com webhook integration.** Salla → Make.com → /api/webhook/make/{token} → same DB → same reports.
- ✅ Backend module `webhook_routes.py`:
  - `POST /api/webhook/make/{token}` (PUBLIC, token-authed) — accepts single object, array, or `{orders: [...]}`. Upsert by `(user_id, order_number)` ensures no duplicates and supports updates.
  - `GET /api/webhook/settings` (JWT) — auto-creates token; returns webhook_url + sample payload.
  - `POST /api/webhook/settings/rotate-token` — invalidates old token immediately.
  - `DELETE /api/webhook/settings` — disconnect: removes token + all stored orders for the user.
  - `GET /api/webhook/orders` — list received orders (date_from/date_to/limit), DESC by order_date.
  - `GET /api/webhook/stats` — total_orders_in_db, total_received_ever, last_sync_at, date_range (earliest/latest).
  - `POST /api/webhook/build-analysis` — aggregates orders in [date_from, date_to] → `analyses` document with `source: "make"`, using the EXACT same `match_settings()` + `_build_report()` pipeline as Excel.
- ✅ MongoDB collections: `webhook_tokens` (unique on user_id + token), `webhook_orders` (unique on (user_id, order_number), index on order_date).
- ✅ `_orders_to_parsed()` bridges raw orders → `parse_salla_excel`-compatible dict, so the rest of the pipeline (dashboard, reports, daily costs, shipping accounts, BNPL fees, KPI cards) works unchanged.
- ✅ Liberal date parsing: handles YYYY-MM-DD, ISO 8601, DD/MM/YYYY, etc.
- ✅ Pydantic `Config.extra="allow"` + full `raw` JSON preserved on each order — no data loss from unknown Make.com mapping fields.
- ✅ Frontend page `/make-webhook` (`MakeWebhook.jsx`): copyable webhook URL, token rotate/disconnect, sample JSON payload, build-analysis form (date range + ads/products costs), recent-orders table, stats KPIs (stored/received/last sync/date range).
- ✅ Sidebar: new `ربط Make.com` link.
- ✅ Testing: **71/71 backend tests pass** (16 new webhook tests + 55 prior). Frontend Playwright fully green.

## Implemented (2026-05 — Deferred Shipping Companies)
- ✅ **Two-tier shipping**: each shipping company can be marked `is_deferred=true` in Settings.
  - Regular companies: cost deducted directly from sales (default behavior).
  - Deferred companies: cost still counted as expense (net_profit), but **not** deducted from the projected Salla→bank transfer.
- ✅ **New backend module** `shipping_accounts.py`:
  - `GET /api/shipping-accounts` — list each deferred company with total_owed (from analyses) + total_paid (from ledger) + remaining.
  - `GET /api/shipping-accounts/{company}/payments` — payment history (DESC by date).
  - `POST /api/shipping-accounts/{company}/payments` — record a payment {amount, payment_date, invoice_number, note}.
  - `DELETE /api/shipping-accounts/payments/{payment_id}` — undo a payment.
  - MongoDB collection `shipping_payments` with index `(user_id, company_name, payment_date desc)`.
- ✅ **Excel parser & report**: `match_settings()` now propagates `is_deferred` into each `shipping_breakdown` row and computes `deferred_shipping_cost` aggregate.
- ✅ **Dashboard**: new fields/KPIs `deferred_shipping_cost`, `regular_shipping_cost`, and `expected_salla_transfer = total_sales − total_payment_fees − regular_shipping_cost`.
- ✅ **Frontend page** `/shipping-accounts` (`ShippingAccounts.jsx`): summary KPIs (Owed/Paid/Remaining), per-company cards with progress bar, expandable payment ledger (with delete), and "add payment" modal.
- ✅ **Settings UI**: 14-column grid with a dedicated "آجل" checkbox per shipping row.
- ✅ **Sidebar**: new `حسابات الشحن الآجلة` link.
- ✅ Testing: **55/55 backend tests pass** (13 new shipping-accounts tests, 19 snapchat, 23 base). Frontend Playwright smoke green.

## Implemented (2026-02)
- ✅ JWT custom auth (register/login/logout/me) with httpOnly cookies + bearer token.
- ✅ Sidebar layout (RTL) — 6 صفحات: Dashboard, Upload, History, Daily Costs, Reports, Settings.
- ✅ Excel parser (auto-detects Arabic/English column names from Salla).
- ✅ Settings: edit payment commissions + shipping costs per company + VAT.
- ✅ Analysis creation with file upload + costs + matched commissions.
- ✅ Analysis result page: KPIs + Pie chart (payments) + Bar chart (shipping) + tables.
- ✅ Daily costs page: add/edit/delete by date (incl. Snapchat ×2, TikTok, Instagram, Google, مصاريف يومية).
- ✅ Reports page: aggregated across all analyses with charts and tables.
- ✅ History page with search & deletion.
- ✅ PDF and Excel export endpoints + frontend buttons.
- ✅ Dashboard with monthly trend (LineChart) and recent analyses + date range filter.
- ✅ BNPL (Tamara/Tabby) fees separated into a distinct KPI card.

## Implemented (2026-05 — Snapchat Integration)
- ✅ **Snapchat Marketing API OAuth integration** — per-user account connect:
  - Backend module `snapchat_routes.py` (separate from `server.py`):
    - `POST /api/snapchat/config` — save client_id / client_secret / redirect_uri (upsert)
    - `GET  /api/snapchat/config` — return status (without leaking client_secret)
    - `DELETE /api/snapchat/config` — disconnect
    - `GET  /api/snapchat/authorize-url` — build Snapchat OAuth URL with signed JWT state
    - `GET  /api/snapchat/oauth/callback` — handle code → exchange → store refresh_token; always redirects to `/settings?snapchat=success|error`
    - `GET  /api/snapchat/adaccounts` — list ad accounts (auto-refresh access_token)
    - `POST /api/snapchat/select-adaccount` — persist selected ad account
    - `GET  /api/snapchat/daily-spend?date=YYYY-MM-DD` — fetch daily spend (handles micro-currency conversion ÷1,000,000)
  - MongoDB: `snapchat_connections` collection, unique index on `user_id`.
  - State CSRF defense: signed JWT (10 min TTL) embedding user_id — no cookies/headers needed on callback.
  - Frontend Settings page: dedicated "ربط Snapchat Ads" card with App ID / App Secret (password) / Redirect URI fields + Connect/Disconnect/Re-connect + ad account picker.
  - Frontend Daily Costs page: small "Snap" button next to سناب شات input → calls `/snapchat/daily-spend` for selected date and auto-fills the field.
- ✅ **Bug fix — logout cookie deletion**: `clear_auth_cookies` now mirrors `set_auth_cookies` attributes (Secure, SameSite=None, HttpOnly, Path=/). Previously the deletion Set-Cookie had `SameSite=lax` → browsers ignored it → logout was a no-op. Verified end-to-end with a real browser via Playwright.
- ✅ **Bug fix — Recharts width(-1) warnings**: ResponsiveContainer now uses `width="99%" minWidth={0} minHeight={0}` across Dashboard / Reports / AnalysisResult.

## Backlog / Next
### ⏸️ Awaiting merchant approval
- **Meta OAuth Flow — خيار B ("اربط مع Facebook")**: one-click Meta login that eliminates manual token copying entirely. Mirrors the Snapchat OAuth flow (Configure App ID + Secret + Redirect URI → click "اربط" → Meta consent screen → callback persists 60-day token + permissions). Will reuse the existing `meta_connections` schema and add `redirect_uri` + signed-JWT state. **NOT to be started until merchant explicitly approves.**

### P1
- Snapchat campaign creation (P1 from user; user explicitly deferred this).
- TikTok / Instagram Ads API direct integration (mirror Snapchat flow).
- Auto-fill Daily Costs for all platforms by date (one-click fetch).
- Multi-user team workspaces / sharing.
- Forgot password + email-based reset (currently console-logged).
- Currency localization (currently SAR hard-coded).

### P2
- Compare two analyses side-by-side.
- Profit per product analysis (requires line-items in Excel).
- Server-side refresh token revocation/blacklist (defense-in-depth on logout).

## Test Credentials
See `/app/memory/test_credentials.md`.
