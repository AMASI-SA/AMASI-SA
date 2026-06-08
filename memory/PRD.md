# PRD — MEZAN / ميزان (تطبيق محاسبي ذكي لمنصة سلة)

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية يدمج بيانات من Salla Excel + Make.com webhooks + Salla Direct API، يحسب المبيعات والعمولات والشحن وCOD، يدير الأصول والتسويات بشكل ذكي.

## Current Brand
- Name: **MEZAN / ميزان**
- Tagline: «منصة التحليلات والمحاسبة للتجارة الإلكترونية»
- Stack: React + FastAPI + MongoDB

---

## ✅ ITERATION 110-d — Cumulative-debt fix + Diagnose UI (Feb 2026)

### Why
بعد إصلاح المزامنة الأول وزر force-resync، اكتشف المستخدم:
- **بق التراكم**: عند الضغط على إعادة المزامنة (force=true)، الدين كان يتضاعف بدل أن يُستبدل. مثال: 300 SAR → ضغطة → 600 SAR.
- **سؤال Snapchat**: لا تظهر أي قيم رغم وجود البيانات على Production. سبب محتمل: عدم تطابق `external_account_id` بين الـ counterparty وبين البيانات الفعلية.

### Backend Fixes
1. **Cumulative-debt bug (`_run_sync_for_all` when `force=True`)**: قبل كتابة بيانات الصرف الجديدة، الكود الآن:
   - يبحث عن سجلات `ad_account_ledger` السابقة بـ `auto_cron=True` في الفترة المحددة
   - يستعيد جزء `from_balance` للرصيد
   - يخفّض/يحذف liability المرتبط بـ `source=ad_account_cron` بمقدار `uncovered`
   - يحذف الـ ledger rows القديمة
   - ثم يطبّق البيانات الجديدة من البداية → re-sync يصبح idempotent حقيقي.
2. **`GET /api/ad-accounts/diagnose`** (read-only): لكل حساب يعرض:
   - الـ `external_account_id` المُدخل
   - لكل source collection: حقل التمييز، الـ IDs المتاحة فعلياً (أول 10)، عدد الصفوف، عيّنة من آخر بيانات، هل ID المستخدم مطابق؟
   - تشخيص نصي عربي يوضّح المشكلة وتوصية إصلاح.

### Frontend
- **`/ad-accounts`**: زر "🩺 تشخيص المزامنة" (blue) في الـ header → dialog يعرض كل حساب مع:
  - شارة ✅/❌ صحي/غير صحي
  - الـ external ID الحالي + الرصيد + آخر مزامنة + نمط الدين
  - بطاقة لكل source collection مع badge "✓ مطابق" أو "✗ غير مطابق"
  - الـ IDs المتاحة (الـ ID المطابق مظلّل بأخضر، الباقي amber)
  - عيّنة من آخر بيانات الصرف
  - صندوق "🔍 المشكلة" مع التوصيات للحسابات غير الصحية

### Tests
- `test_ad_account_sync_fix_iter110.py` (6/6 ✅): إضافة `test_force_resync_is_idempotent_no_double_counting` + `test_force_resync_picks_up_increased_spend`. مزامنة 300 SAR مرتين → الدين يبقى 300 (وليس 600). نموّ الصرف من 100 إلى 300 → الدين يحدّث إلى 300 (وليس 400).
- `test_ad_account_diagnose_iter110.py` (3/3 ✅): mismatch + healthy + no-external-id paths.

### Route Fix
- إعادة ترتيب route definitions: `/diagnose` يجب أن يُسجَّل قبل `/{cp_id}` وإلا FastAPI يفسّر "diagnose" كـ counterparty ID.

---


## ✅ ITERATION 110-c — Ad-account delete button + Settings toggle (Feb 2026)

### Why
المستخدم يريد القدرة على حذف حساب إعلاني، لكن مع التحكم في إظهار/إخفاء الزر من الإعدادات لمنع الحذف العَرَضي.

### Backend
- **`SettingsIn.ad_account_allow_delete: bool`** — حقل جديد افتراضي `False`.
- **`GET /api/settings`** يُرجع الحقل، **`PUT /api/settings`** يقبله.
- **`DELETE /api/ad-accounts/{cp_id}`** — كان موجوداً سابقاً من Iter-107 ويرفض الحذف عندما `balance > 0` أو يوجد مديونية مفتوحة. لم يُعدّل.

### Frontend
- **`/settings`**: toggle جديد "إظهار زر حذف الحسابات الإعلانية" — كروت rose-tinted تطابق نمط toggle حذف ملفات التسويات. يُحفظ مع باقي الإعدادات بنفس الـ PUT.
- **`/ad-accounts`**: زر "🗑️ حذف" يظهر في كل بطاقة عندما `allowDelete=true` فقط. الزر:
  - مُعطَّل بصرياً (opacity-40) عندما `balance > 0` أو `open_debt > 0` مع tooltip يشرح السبب.
  - يطلب تأكيد مع رسالة عربية واضحة قبل استدعاء DELETE.
  - بعد النجاح يُعيد تحميل القائمة.

### Tests — `tests/test_ad_account_delete_iter110.py` (4/4 ✅)
1. **`test_settings_toggle_round_trip`**: GET → False (default) → PUT True → GET → True. لا تتأثر الـ flags الأخرى.
2. **`test_delete_zero_balance_zero_debt_account`**: حساب جديد بدون أي حركات يُحذف بنجاح ويُختفي من القائمة.
3. **`test_delete_blocked_when_balance_positive`**: تعبئة الحساب من بنك → DELETE يعيد 400 "لا يمكن الحذف. الحساب فيه رصيد متبقي".
4. **`test_delete_blocked_when_open_debt`**: تسجيل صرف يخلق مديونية → DELETE يعيد 400 "لا يمكن الحذف. الحساب عليه مديونية مفتوحة".

### UI Verification (Screenshot)
- قبل التفعيل: لا زر حذف في بطاقات `/ad-accounts`.
- بعد التفعيل من `/settings`: زر "🗑️ حذف" يظهر مع باقي الأزرار.

---


## 🐛 BUGFIX 8-Feb-2026 (follow-up) — force re-sync to recover from buggy idempotency stamps
**User report after first fix deployed**: "المزامنة تمت: 0 حساب · 7 مُتخطّى" — every account skipped. Cause: the previous buggy sync had already stamped `last_auto_sync_date = today` on all 7 counterparties, so even after the data-source fix the new code hits the idempotency guard and returns "already synced" without creating any debt.

**Fix**:
- New `force: bool` field on the `/sync-all` body (defaults to `false`).
- When `force=true`, `_run_sync_for_all` skips the `last_auto_sync_date == to_date` check.
- The daily cron at 23:55 still uses `force=false` so it can't double-charge.
- UI: when "مزامنة الكل الآن" returns ALL skipped (processed=0, skipped>0), automatically prompt with `window.confirm` offering a force-resync. Also enriched the success toast to show the total new debt amount in SAR.

**Test added** — `test_force_resync_bypasses_idempotency_after_buggy_run`: pre-stamp a counterparty's `last_auto_sync_date` to today (mimicking the bug), confirm normal call returns `skipped=true`, then call again with `force=true` and verify spend & liability were created.

---


## 🐛 BUGFIX 8-Feb-2026 — sync-all & sync-from-platform reading wrong collections
**User report**: "عند مزامنه الكل الان لا يتم جلب مديونية الحسابات الإعلانية اليومية". After clicking "🔄 مزامنة الكل الآن" the UI showed "تمت المزامنة" but no liability was created for Snapchat counterparties that had `external_account_id` set.

**Root cause**: Two endpoints had outdated collection mappings:
- `_run_sync_for_all` (used by `/sync-all` + the daily cron) hard-coded `snapchat → snapchat_ads_daily` and filtered by `ad_account_id`. But the real per-account data lives in `snapchat_account_daily`, and `meta_ads_daily` uses field name `account_id` (NOT `ad_account_id`). The filter returned 0 rows → "no spend" branch → updated `last_auto_sync_date` and reported success with debt=0.
- `sync_from_platform` (the per-account button) had the exact same bug.

**Fix**: Both endpoints now delegate to the new `_fetch_daily_spend()` helper (introduced for Iter-110 historical migration), which uses the `PROVIDER_SOURCES` map → Snapchat hits `snapchat_account_daily.ad_account_id` (falling back to `snapchat_ads_daily` when no per-account rows exist), Meta hits `meta_ads_daily.account_id`. The response now also returns `source_collection` so the UI/logs can see which collection was actually used.

**Tests added** — `tests/test_ad_account_sync_fix_iter110.py` (3/3 ✅) that would fail on the pre-fix code:
- sync-all picks up `snapchat_account_daily` rows scoped by `ad_account_id` (no cross-account leak).
- sync-all picks up Meta rows via `account_id`.
- sync-from-platform (per-account button) works the same way.

---


## ✅ ITERATION 110 — Historical migration + opening balance for ad-accounts (Feb 2026)

### Why
المستخدم لديه بيانات صرف تاريخية موجودة فعلياً في `snapchat_account_daily / snapchat_ads_daily / meta_ads_daily / tiktok_ads_daily` ويريد ترحيلها كمديونيات منفصلة لكل حساب إعلاني، مع ضمان عدم دمج حسابات سناب المتعددة في حساب واحد، وإمكانية إضافة رصيد افتتاحي يدوي للحسابات غير القابلة للترحيل.

### Backend (`ad_account_routes.py`)
- **`POST /api/ad-accounts/migration/preview`** — تقرير معاينة قراءة فقط: لكل حساب يعرض إجمالي صرف الفترة، عدد الأيام، أول/آخر يوم، الصرف اليومي (cap 60 سطر)، الرصيد/المديونية الحالية، حالة الربط بـ `external_account_id`، آخر مزامنة، تنبيهات (account بدون external_id يُحجب افتراضياً).
- **`POST /api/ad-accounts/migration/apply`** — يُنفّذ الترحيل **فقط للحسابات الواردة في `account_ids`** (تحكم صريح). يدعم وضعين:
   - `daily`: سطر ledger مستقل لكل يوم (افتراضي، أدق).
   - `lump`: سطر واحد إجمالي للفترة.
   يحترم `debt_mode` (auto ينشئ liability، manual يسجّل الصرف فقط بدون مديونية).
- **`PUT /api/ad-accounts/{cp_id}/opening`** — تعيين رصيد افتتاحي / مديونية افتتاحية / تاريخ بداية الاحتساب / طريقة احتساب. ينشئ liability منفصل بمصدر `ad_account_opening` (لا يتعارض مع liabilities الـ engine). تمرير `opening_debt=0` يحذف liability الافتتاحي.
- **`PROVIDER_SOURCES`** dict جديد يحدّد المصدر الصحيح لكل منصة:
   - Snapchat: `snapchat_account_daily` (مع ad_account_id) → fallback إلى `snapchat_ads_daily` لو لا توجد بيانات per-account.
   - Meta: `meta_ads_daily.account_id`.
   - TikTok: `tiktok_ads_daily` (لا يحتوي scope field → يحذّر المستخدم في الحالات متعددة الحسابات).

### Frontend (`AdAccounts.jsx`)
- زر جديد **"🔄 ترحيل المديونيات التاريخية"** (amber) في رأس الصفحة.
- **`MigrationDialog`** بـ 3 خطوات:
   1. اختيار الفترة (with explanation banner).
   2. جدول معاينة لكل الحسابات + checkbox + radio لـ daily/lump + popover للصرف اليومي + عداد المحدد للترحيل + إجمالي الصرف.
   3. نتائج الترحيل (rows_posted, total_spend, debt_created, balance_after).
- زر **"⚙️ افتتاحي"** في كل بطاقة حساب → **`OpeningDialog`** يحفظ الرصيد/المديونية الافتتاحية يدوياً.
- **حماية تلقائية**: الحسابات غير المربوطة بـ Ad Account ID تظهر بخلفية amber + checkbox معطّل بشكل افتراضي + تنبيه نصي. يجب على المستخدم تحديدها صراحة.

### Tests — `tests/test_ad_account_migration_iter110.py` (7/7 ✅)
1. `preview` يفصل الصرف لكل ad_account حسب `external_account_id` (no leak).
2. حساب بدون `external_account_id` يُعلَّم `blocked_by_default=true`.
3. apply يومي → سطر ledger لكل يوم.
4. apply مجمّع → سطر ledger واحد.
5. apply يحترم `debt_mode=manual` (لا liability).
6. opening ينشئ liability بمصدر `ad_account_opening` (1 صف فقط) — تعيين `opening_debt=0` يحذفه.
7. apply يلمس فقط `account_ids` الواردة في payload (no cross-account writes).

---


## ✅ ITERATION 108 — Scheduled daily ad-account sync cron (Feb 2026)

### Why
بدلاً من تشغيل المزامنة يدوياً لكل حساب إعلاني، تعمل تلقائياً كل ليلة 11:55 مساءً لكل المستخدمين وكل حسابات Snap/TikTok/Meta.

### Backend
- **`POST /api/ad-accounts/sync-all`** — يشغّل المزامنة لكل حسابات إعلانية المستخدم في فترة محددة. مثالي للمستخدم لإطلاقها يدوياً وقت ما يريد.
- **`run_daily_cron(db)`** — دالة موديول-ليفل تستدعي `_run_sync_for_all` لكل مستخدم لديه حساب إعلاني واحد على الأقل.
- **Asyncio scheduler** داخل `server.py` على `on_startup` — حلقة لا نهائية تنام حتى 23:55 ثم تشغّل الـ cron.
- **Idempotency** عبر حقل جديد `last_auto_sync_date` على counterparty — إذا حاول الـ cron (أو الـ user) المزامنة لنفس `to_date` يُرجع `skipped: true` بدون مضاعفة المديونية.
- **Run log** في collection جديد `cron_runs` — تاريخ كل تشغيل + عدد المستخدمين المعالَجين + ملخص.

### Frontend
- زر **"🔄 مزامنة الكل الآن"** بنفسجي في رأس `/ad-accounts` بجانب "إضافة حساب".
- بانر توعوي يوضّح أن الـ cron يعمل كل ليلة 11:55.

### Tests — `tests/test_ad_account_cron_iter108.py` (2/2 ✅)
1. `/sync-all` يعالج 3 حسابات (Snap/TT/Meta) بمكالمة واحدة، ينشئ المديونية لكل واحد.
2. **Idempotency**: تشغيل `/sync-all` مرتين على نفس `to_date` يُرجع `skipped: true` في المرة الثانية ولا يُضاعف المديونية (لو كان الصرف 100، يبقى دين 100 وليس 200).

---


## ✅ ITERATION 107 — Ad-account inline create + multi-provider + platform sync (Feb 2026)

### Why
1. السماح بإضافة حساب إعلاني جديد مباشرة من صفحة `/ad-accounts` بدون التنقل لـ counterparties.
2. توسيع قائمة المنصات المدعومة لتشمل أي منصة إعلانية مستقبلية.
3. ربط نظام المديونية مع بيانات الصرف اليومية الموجودة فعلياً في `*_ads_daily`.

### Backend (`ad_account_routes.py`)
- **`POST /api/ad-accounts`** — إنشاء حساب إعلاني inline مع fuzzy duplicate guard (تحذير لا دمج تلقائي).
- **`DELETE /api/ad-accounts/{cp_id}`** — حذف مرفوض لو فيه رصيد أو مديونية مفتوحة.
- **`POST /api/ad-accounts/{cp_id}/sync-from-platform`** — يجمع الصرف اليومي من collection المنصة المطابقة (`snapchat_ads_daily` / `tiktok_ads_daily` / `meta_ads_daily`) في فترة محددة ويُسجِّله كصرف واحد عبر منطق `/spend` الموجود (تطبق نفس قواعد المديونية auto/manual).
- **`AD_PROVIDERS` extended**: snapchat, tiktok, meta, **google**, **twitter**, **other** (نفس الامتداد في `counterparties_routes` و `liabilities_routes` للتوافق التام).

### Frontend (`AdAccounts.jsx`)
- **زر "+ إضافة حساب إعلاني"** في رأس الصفحة → يفتح Dialog مع:
  - قائمة المنصات الـ 6.
  - حقل اسم + ملاحظات.
  - Fuzzy warning مع زر "أنشئ منفصلاً" عند التشابه.
- **زر "🔄 مزامنة"** يظهر فقط على كروت Snap/TikTok/Meta → يفتح Dialog لاختيار فترة وجمع الصرف.
- لافتة "غير مدعومة" للمنصات بدون daily-spend collection (Google/X/Other) — يُطلب من المستخدم استخدام "تسجيل صرف" يدوياً.

### Tests — `tests/test_ad_account_create_sync_iter107.py` (7/7 ✅)
1. إنشاء inline لكل المنصات الـ 6.
2. رفض الاسم المتطابق داخل نفس المنصة (409 duplicate).
3. السماح بنفس الاسم في منصات مختلفة (مع توضيح أنه يجب تمييز الأسماء).
4. مزامنة TikTok: 3 أيام × 100/150/50 → صرف 300 + مديونية 300.
5. مزامنة بدون بيانات → 0 spend مع رسالة واضحة.
6. منصة غير مدعومة (Google) → 400.
7. الحذف مرفوض مع رصيد ومديونية، مقبول بعد التصفير.

Plus full Iter-106 regression (8/8) — 15 tests pass together.

---


## ✅ ITERATION 106 — Ad-Account Balance + Auto-Debt Engine (Feb 2026)

### Why
Track prepaid ad-platform balances (Snapchat, TikTok, Meta + any future provider) and automatically convert overspend into a recorded debt that flows into the Financial Position. Each `counterparties` row of kind=ad_account is now a self-contained ad wallet with its own balance, debt mode and movement ledger.

### Backend (`backend/ad_account_routes.py` — NEW)
- **2 new fields on `counterparties`**: `balance` (float, prepaid amount), `debt_mode` ("auto" | "manual", default "auto").
- **New collection `ad_account_ledger`** — append-only history of every movement: `{type, amount, balance_after, debt_after, breakdown, account_id, related_liability_id, ...}`.
- **Debt itself REUSES `liabilities(kind=ad_account)`** linked via `counterparty_id` — same row appears in the Financial Position's ad-debt KPI (no duplicated balance math).
- **Endpoints** (all under `/api/ad-accounts/`):
  - `GET /` — list with totals across all accounts.
  - `GET /{cp_id}` — single account summary.
  - `GET /{cp_id}/ledger` — movement history.
  - `PUT /{cp_id}/settings` — toggle debt_mode (auto/manual).
  - `POST /{cp_id}/topup` — atomic: bank ↓ amount → pay down open debt FIRST → remainder goes to balance.
  - `POST /{cp_id}/spend` — atomic: balance covers what it can; in **auto** mode the uncovered piece creates / extends an open liability; in **manual** mode no auto-debt is created.

### Business rules (verified by tests)
1. **Spend ≤ balance** → balance ↓, no debt.
2. **Spend > balance (auto)** → balance → 0, remainder becomes liability. (User's Snap example: balance 500, spend 800 → balance 0, debt 300.)
3. **Top-up with open debt** → debt cleared first, remainder → balance. (User's example: debt 300, top-up 1000 → debt 0, balance 700.)
4. **Manual mode** → uncovered spend is NEVER auto-converted to debt (user must add it manually).
5. Top-up always deducts from the chosen bank (existing `account_transactions` ledger).
6. Ledger captures `topup`, `spend`, `debt` with timestamp & breakdown.
7. Daily ad spend collections (`snapchat_ads_daily` / `tiktok_ads_daily` / `meta_ads_daily`) UNCHANGED — they remain the source of truth for daily-cost reporting. /spend is a separate, optional ledger hook.

### Frontend (`AdAccounts.jsx` — NEW page `/ad-accounts`)
- 3 top KPIs (إجمالي الأرصدة + المديونيات + الصرف التراكمي).
- Per-account card with: name + provider badge, mode toggle, 3 mini-KPIs (Balance / Debt / Spend), last topup/spend/debt timestamps, action buttons (تعبئة / تسجيل صرف / السجل).
- **Topup dialog** — pick bank, amount, date. Shows current balance + debt and informs user that debt will be paid first.
- **Spend dialog** — amount + date + description. Shows mode reminder.
- **Ledger dialog** — chronological list with type badges + amount + balance_after + debt_after.
- Sidebar entry "الحسابات الإعلانية والمديونية" under "إدارة المشتريات والعهد والتحصيلات".

### Tests — `tests/test_ad_account_engine_iter106.py` (8/8 ✅)
1. Covered spend → no debt.
2. Spend > balance (auto) → balance=0, debt=remainder. Liability row exists.
3. Top-up pays down debt first then adds balance.
4. Exact-clear top-up.
5. Manual mode never creates auto-debt.
6. Ledger has topup + spend + debt rows.
7. Top-up actually deducts the bank balance.
8. List endpoint aggregates totals correctly across multiple accounts.

---


## ✅ ITERATION 105 — Custom App Integration (Feb 2026)

### Why
Add the merchant's own app as a primary, real-time data source while keeping ALL existing sources (Excel, Make.com, settlement files, Snap/TikTok/Meta ads, manual entries) untouched.

### Backend (`backend/custom_app_routes.py` — NEW)
- **API Key auth** (`X-API-Key` header) — per-user, stored in `settings.custom_app.api_key`. Auto-seeded with `mzn_<32-byte-token-urlsafe>` on first access.
- **Endpoints** (all under `/api/integrations/custom-app/`):
  - `POST /orders` — accepts single order or `{orders: [...]}` batch. Upserts into `unified_orders` via existing merge logic with `source="custom_app"`. Line items stored in NEW `order_items` collection. Raw payload saved in `integration_events` for audit.
  - `POST /products` — upsert into NEW `custom_app_products`.
  - `POST /customers` — upsert into NEW `custom_app_customers`.
  - `POST /test-connection` — ping.
  - `GET /status` (JWT) — counters + last order + recent events + recent errors.
  - `GET /settings` (JWT) — current API key + endpoint URLs.
  - `POST /settings/api-key/regenerate` (JWT) — rotates key, old key instantly invalidated.
  - `POST /settings/toggle` (JWT) — enable/disable integration.

### Source precedence updated (`orders_db.py`)
- `custom_app` > `make` > `salla_direct` > `excel` (merchant's own app is the new authoritative source).
- Existing sources keep working with the exact same fill-empty-fields rule when an order has been touched by Make or custom_app.

### Captured fields (full spec from user)
- Identity: order_id, order_number, reference_id, created_at, updated_at, order_status, payment_status, payment_method, source, currency.
- Amounts: subtotal, discount, shipping_cost, tax, fees, total_amount, paid_amount, refunded_amount.
- Customer: id, name, mobile, email, city, country.
- Shipping: shipping_company, tracking_number, shipment_status, shipping_address.
- Marketing: utm_source/medium/campaign/content/term, device_type.
- Items: product_id, sku, barcode, name, variant, qty, unit_price, total_price, cost_price, weight, image_url, category, brand.

### Dedup logic
- Primary key: `(user_id, order_number)`. Repeats UPDATE the existing record. No new rows.
- `data_source` set to `custom_app` once any custom-app payload touches it (sticks even if Excel re-uploads same period).
- All write events appended to `data_sources[]` history (capped at 20).

### Frontend (`CustomAppIntegration.jsx` — NEW page `/integrations/custom-app`)
- **Tab 1 — الإعدادات**:
  - Status banner (enabled / disabled) + toggle.
  - API Key with mask/reveal/copy/regenerate.
  - 4 endpoint URLs ready-to-paste (`orders`/`products`/`customers`/`test`).
  - JSON example for the merchant's developer.
- **Tab 2 — المراقبة والسجل**:
  - Connection status banner (`connected` / `error` / `no_data`).
  - 4 KPIs (orders / products / customers / errors).
  - Last received order summary.
  - Auto-refresh every 30s.
  - Latest-20 events table (timestamp / type / status / summary).
  - Recent-errors panel with raw error details.
- Sidebar entry "ربط تطبيقي الخاص" under "الاستيراد والربط".

### Tests — `tests/test_custom_app_integration_iter105.py` (10/10 ✅)
1. Single order with 2 items → 1 unified_order + 2 order_items.
2. Re-sending same order_number → updates, no dup.
3. Batch of 3 orders → 3 created.
4. Missing order identifier → graceful failure in results array.
5. Products upsert: 2 created + 1 updated.
6. Customers upsert: 2 created.
7. Invalid / missing API key → 401.
8. Regenerate key invalidates old key & enables new key.
9. Existing `/api/orders` endpoint still responds (no shadowing).
10. Test-connection endpoint returns user email.

---


## ✅ ITERATION 104 — Procurement, Advances & Receivables Section (Feb 2026)

### Why
Group the daily operational workflows (purchases, employee advances, customer receivables) into one dedicated sidebar section "إدارة المشتريات والعهد والتحصيلات" — reusing existing collections, no duplicated balance math.

### Backend
- New endpoint **`POST /api/liabilities/{id}/collect`** (Iter-104) — opposite of `/pay`:
  - Only valid for `kind=receivable`.
  - Increases the chosen bank's `current_balance` via `account_transactions` (direction=in, transaction_type=`receivable_collection`).
  - Decreases the receivable's `remaining_amount`, flips `status` unpaid → partial → paid.
- Salary advances (`POST /api/liabilities` kind=salary_advance) and supplier liabilities already exist — these pages are thin UIs on top of them.

### Frontend — new pages (in NEW sidebar section)
- `/operations-dashboard` (NEW) — aggregator: 3 panels (المشتريات / العهد / الذمم) with 10 KPIs all pulled live from `/purchase-invoices` and `/liabilities?kind=...`.
- `/purchase-invoices` (from Iter-103) — moved into the new section.
- `/advances` (NEW) — list/filter/create over `liabilities(kind=salary_advance)`. Creating deducts from the chosen bank immediately and recovery happens automatically when the next salary is paid (existing back-end logic).
- `/receivables` (NEW) — list/filter/create over `liabilities(kind=receivable)`. Each open row has a **تحصيل** button → opens dialog → adds to chosen bank via the new collect endpoint.

### Sidebar reorganisation
- New top-level section **"إدارة المشتريات والعهد والتحصيلات"** between "العمليات المالية" and "الاستيراد والربط".
- Contains: لوحة العمليات / فواتير المشتريات / عهد الموظفين والمندوبين / الذمم والتحصيلات.

### Architectural rule respected
ALL 4 pages use existing collections only:
- `counterparties(kind=supplier)` — supplier registry.
- `liabilities` — supplier debt + advances + receivables (all kinds).
- `purchase_invoices` (from Iter-103) — invoice headers linked to liabilities.
- `accounts` + `account_transactions` — every cash movement.
No duplicated balances. No new collections in this iteration.

### Tests — `tests/test_receivable_collect_iter104.py` (3/3 ✅)
1. Collect 400 then 600 on a 1000 receivable → bank goes 0 → 400 → 1000, status flips unpaid → partial → paid.
2. Collect rejected on non-receivable (e.g., supplier) liabilities.
3. Over-collection (250 on 200 remaining) rejected with Arabic error.

Plus regression: all Iter-100/101/103 tests still pass (21/21).

---


## ✅ ITERATION 103 — Purchase invoices (no inventory) (Feb 2026)

### Scope (Option B chosen by user)
Track supplier purchase invoices with line items WITHOUT inventory, stock_movements, FIFO/AVG, or `product_costs` auto-update. Quantities are recorded for the paper trail only.

### Backend (`backend/purchase_invoices_routes.py` — NEW)
- New collection `purchase_invoices`:
  ```
  { id, user_id, supplier_counterparty_id, supplier_name,
    invoice_number?, invoice_date, due_date?,
    lines: [{ id, product_name, sku?, quantity, unit_price, line_total }],
    subtotal, tax_amount, total,
    liability_id  → linked supplier liability (single source of truth for payment),
    notes, created_at, updated_at }
  ```
- Endpoints (`/api/purchase-invoices`):
  - `POST /` — create invoice + auto-create `liabilities` (kind=supplier) row with `expected_amount = total`. Source supplier name from counterparties.
  - `GET /` — list with filters (`supplier_id`, `status`, `from`/`to`, `limit`).
  - `GET /{id}` — single enriched with live payment state.
  - `PUT /{id}` — edit; refused if any payment was recorded. Resyncs liability total.
  - `DELETE /{id}` — refused if any payment was recorded; otherwise removes both invoice and unpaid liability.
  - `GET /supplier/{cp_id}/statement` — aggregated totals (invoiced, paid, balance_owed) + per-invoice rows.
- Status (`unpaid/partial/paid`) and `paid_amount`/`remaining_amount` come from the linked liability — no duplicated balance math.

### Frontend (`PurchaseInvoices.jsx` — NEW)
- New page `/purchase-invoices` with:
  - Three KPI cards (after filter): إجمالي / مسدَّد / متبقي للموردين.
  - Filters: search, supplier picker, status buttons.
  - Table with status pills + actions (سداد → routes to Financial Input Hub, edit/delete disabled when paid_amount > 0).
  - Create/edit dialog with multi-line editor and live total calculation.
  - Supplier statement dialog (opens from clicking supplier name).
- Sidebar entry "فواتير المشتريات" under "العمليات المالية".

### Tests — `tests/test_purchase_invoices_iter103.py` (9/9 ✅)
1. Create → linked liability with correct supplier name & total (qty × price + tax).
2. Unknown supplier → 404.
3. Zero-total invoice rejected.
4. Paying linked liability flips invoice status (unpaid → partial → paid) and shows correct paid/remaining.
5. Edit refused after payment.
6. Edit (lines/tax) resyncs the liability expected_amount.
7. Delete lifecycle: OK unpaid (removes both rows), rejected when paid.
8. Supplier statement: 3 invoices + 2 payments → correct invoiced / paid / balance_owed.
9. List filters (by supplier).

---


## ✅ ITERATION 102 — Pro-rata salary by days worked (Feb 2026)

### Why
Merchant wants the monthly salary obligation to reflect the days actually worked (`days_in_month = 28 / 29 / 30 / 31`), so partial-month employees aren't over-paid in the books.

### Backend
- `liabilities` rows of `kind=salary` now carry three new fields:
  - `monthly_amount_base` — the contractual monthly figure (immutable).
  - `days_in_month`       — calendar days of `period_key` (28/29/30/31).
  - `days_worked`         — defaults to `days_in_month`, editable.
- New endpoint **`PUT /api/liabilities/{id}/days-worked`**:
  - Validates `kind=salary`, status ≠ paid, employee in `category=employee` (household/charity rejected).
  - `days_worked ∈ [0, days_in_month]`, must keep `expected_amount ≥ paid_amount`.
  - Recomputes `expected_amount = monthly_amount_base × days_worked / days_in_month` and refreshes status.
- Auto-generation (`/generate-salaries`) now persists the new fields for every new row.
- `_compute_status` updated: when `expected ≤ 0` → `paid` (handles `days_worked = 0` cleanly).

### Frontend (`FinancialInputHub.jsx` → "سداد التزام" tab)
- When user selects a salary liability, a violet inline editor appears:
  - "أيام العمل (من X يوم)" + "احتساب وتحديث" button.
  - Displays both the contractual amount and the post-recomputation amount.
- After applying, the parent list reloads so the dropdown shows the updated remaining.

### Tests — `tests/test_salary_days_worked_iter102.py` (6/6 ✅)
1. Generated rows carry correct `days_in_month` (Feb 28, Mar 31).
2. Recompute on edit: 25/30 days of 3000 → 2500. 0 days → 0 (status flips to paid).
3. Validation: negative / > days_in_month / non-numeric all rejected.
4. Households / charity rejected (no day proration).
5. Cannot reduce expected below already-paid.
6. Net position drops by exactly the saved amount.

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
