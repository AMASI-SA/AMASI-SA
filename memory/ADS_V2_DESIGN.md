# 📐 Ads V2 — وثيقة التصميم الكاملة (Design-Only · No Code Yet)

> **الحالة:** مسودة بانتظار اعتماد التاجر  
> **التاريخ:** 2026-06-24  
> **القرار الأساسي:** بناء منظومة إعلانات جديدة (Ads V2) **بالتوازي** مع V1 الحالي بدون حذف أي شيء قائم، مع تجميد V1 تدريجياً.

---

## 0. مبادئ التصميم (الميثاق)

### 0.1 المبادئ غير القابلة للتفاوض

| # | المبدأ | كيف يُفرَض في التصميم |
|---|---|---|
| 1 | **Source of Truth واحد** | كل التقارير المالية تقرأ من `ads_v2_spend_daily` أو `general_ledger` فقط. لا قراءة مباشرة من المزود. |
| 2 | **لا قيد بدون موافقة** | جدول `ads_v2_spend_review` حاجز إجباري بين الـ raw spend و `general_ledger`. |
| 3 | **Idempotency في كل عملية** | مفتاح `(user_id, account_id, date, source_hash)` فريد في كل مرحلة. |
| 4 | **Audit Trail لا يُكسر** | كل سطر يحمل `created_at, created_by, approved_at, approved_by, posted_at, ledger_txn_group_id, source_run_id`. |
| 5 | **Isolation بين الحسابات** | حساب لا يعرف عن حساب آخر. كل سطر بـ `account_id` صريح. |
| 6 | **عملة واحدة في الترحيل** | كل قيد GL بـ SAR. سعر الصرف يُحفظ مع كل سطر مع مصدره. |
| 7 | **لا Fallback ثابت في الكود** | إذا غاب سعر الصرف → السطر يدخل `review` بحالة `needs_fx` وليس بـ 3.75. |
| 8 | **Feature Flag حقيقي** | `ads_v2_enabled` per-user. لا تشغيل عام قبل موافقة التاجر. |
| 9 | **V1 يبقى يعمل** | لا حذف، لا تعديل في collections القديمة. V2 يكتب في collections جديدة بـ prefix `ads_v2_*`. |
| 10 | **العمولة البنكية كـ leg مستقل** | في الـ review و GL والتقرير، تظهر العمولة كسطر مستقل بـ `entry_type=ad_bank_fee`. |

### 0.2 ما الذي **لن** نفعله في V2

- لن نَستورد بيانات V1 إلى V2 تلقائياً (لا migration للأرصدة).
- لن نَكتب في `general_ledger` بنفس أنماط V1 (entry_type جديدة كلياً).
- لن نَستخدم `counterparties` لتخزين حسابات الإعلانات (جدول جديد مستقل).
- لن نَدمج Snapchat Orgs المختلفة تحت Token واحد ضمنياً.

---

## 1. Collections الجديدة (Schema تفصيلي)

> **Prefix:** كل المجموعات الجديدة تبدأ بـ `ads_v2_` لمنع أي تداخل مع V1.  
> **Indexes:** سيُحدَّد كل index صراحة عند البناء.  
> **PyObjectId:** كل المجموعات تستخدم `BaseDocument` و `to_mongo/from_mongo`.

### 1.1 `ads_v2_accounts` — كتالوج الحسابات الإعلانية

| الحقل | النوع | المصدر | الوصف |
|---|---|---|---|
| `id` | str (UUID) | تلقائي | المعرف الأساسي |
| `user_id` | str | session | صاحب الحساب |
| `provider` | enum | manual | `meta` / `snapchat` / `tiktok` / `google_ads` |
| `external_account_id` | str | provider API | معرف الحساب لدى المزود |
| `display_name` | str | manual + provider | اسم الحساب كما يراه التاجر |
| `currency_native` | enum | provider API | عملة الحساب (USD/SAR/AED…) |
| `timezone` | str | provider API | timezone الحساب (Asia/Riyadh, America/Los_Angeles) |
| `organization_id` | str (nullable) | provider API | معرف المنظمة (مهم لـ Snapchat) |
| `organization_name` | str (nullable) | provider API | اسم المنظمة |
| `oauth_credential_id` | str | OAuth flow | يربط الحساب بـ token معين (مفصول لكل منظمة) |
| `bank_fee_enabled` | bool | manual | هل تُطبَّق عمولة بنكية على هذا الحساب؟ |
| `bank_fee_rate` | float | manual | النسبة المئوية (مثال 0.0285 = 2.85%) |
| `bank_fee_method` | enum | manual | `markup_on_spend` / `flat_per_charge` |
| `sync_status` | enum | tlj | `active` / `paused` / `error` / `unauthorized` |
| `sync_error` | str (nullable) | tlj | آخر رسالة خطأ من المزود |
| `last_sync_started_at` | datetime | tlj | بدء آخر مزامنة (UTC) |
| `last_sync_finished_at` | datetime | tlj | انتهاء آخر مزامنة (UTC) |
| `last_synced_date` | date | tlj | آخر يوم مُتاح في المصدر |
| `created_at` / `updated_at` | datetime | تلقائي | |

**Index:** `{user_id:1, provider:1, external_account_id:1}` unique.

### 1.2 `ads_v2_oauth_credentials` — Tokens مفصولة لكل منظمة

| الحقل | النوع | الوصف |
|---|---|---|
| `id` | str | UUID |
| `user_id` | str | |
| `provider` | enum | meta/snapchat/tiktok/google_ads |
| `organization_id` | str (nullable) | لـ Snapchat: org id; لـ Meta: business_id; لـ Google: customer_id |
| `organization_name` | str | للعرض |
| `access_token` | str (مشفَّر) | encrypted at rest |
| `refresh_token` | str (مشفَّر) | |
| `token_expires_at` | datetime | لمعرفة موعد التجديد |
| `scopes_granted` | list[str] | |
| `status` | enum | `active` / `expired` / `revoked` |
| `connected_at` | datetime | |

**Index:** `{user_id:1, provider:1, organization_id:1}` unique.

> **المعالجة الصريحة لمشكلة الرياض:** كل organization تحتاج credential منفصل. الواجهة تُجبر التاجر على ربط منظمة Snap "Establishment AMASI AL-KHALIJ" بـ OAuth flow منفصل قبل ظهور الرياض كحساب قابل للمزامنة.

### 1.3 `ads_v2_spend_raw` — Append-only من API المزود

| الحقل | النوع | الوصف |
|---|---|---|
| `id` | str | UUID |
| `user_id` | str | |
| `account_id` | str | FK → `ads_v2_accounts.id` |
| `provider` | enum | |
| `date` | str (YYYY-MM-DD) | تاريخ الصرف في timezone الحساب |
| `granularity` | enum | `day` / `campaign_day` / `hour` |
| `dimension_keys` | dict | `{campaign_id, adset_id, ad_id}` (nullable حسب granularity) |
| `spend_native` | float | بعملة الحساب |
| `currency_native` | str | redundant للتتبع |
| `impressions` | int | |
| `clicks` | int | |
| `provider_payload_hash` | str | sha256 من حمولة الـ API الأصلية لـ idempotency |
| `source_run_id` | str | FK → `ads_v2_sync_runs.id` |
| `fetched_at` | datetime | UTC |

**Index:** 
- `{user_id:1, account_id:1, date:1, dimension_keys:1, source_run_id:1}` unique (نسمح بعدة قراءات تاريخية بـ run_id مختلف)
- `{user_id:1, account_id:1, date:1, fetched_at:-1}` (للقراءة الأخيرة فقط)

### 1.4 `ads_v2_spend_daily` — التجميع اليومي (Single Source of Truth للأرقام)

| الحقل | النوع | الوصف |
|---|---|---|
| `id` | str | |
| `user_id` | str | |
| `account_id` | str | |
| `provider` | enum | |
| `date` | str (YYYY-MM-DD) | |
| `spend_native` | float | المجموع الأحدث من `spend_raw` لهذا اليوم |
| `currency_native` | str | |
| `fx_rate_to_sar` | float (nullable) | من `ads_v2_currency_settings`; null إذا غاب |
| `fx_rate_source` | str | `manual` / `sama_api` / `cached_yesterday` |
| `fx_rate_as_of` | date | تاريخ سعر الصرف |
| `spend_sar` | float (nullable) | محسوب: `spend_native * fx_rate_to_sar` |
| `bank_fee_sar` | float | 0 إذا غير مفعَّل |
| `gross_sar` | float | `spend_sar + bank_fee_sar` (المبلغ النهائي للترحيل) |
| `confidence` | enum | `final` / `provisional` (تاريخ < اليوم - 3 = final) |
| `last_recomputed_at` | datetime | |
| `last_source_run_id` | str | |

**Index:** `{user_id:1, account_id:1, date:1}` unique (يوم واحد لكل حساب).

> **القاعدة:** كل التقارير تستعلم هذا الجدول. لا تقرير يستعلم `spend_raw` مباشرة.

### 1.5 `ads_v2_spend_review` — طبقة المراجعة الإلزامية

| الحقل | النوع | الوصف |
|---|---|---|
| `id` | str | |
| `user_id` | str | |
| `account_id` | str | |
| `date` | str (YYYY-MM-DD) | |
| `provider` | enum | |
| `spend_native` | float | snapshot من `ads_v2_spend_daily` لحظة دخول المراجعة |
| `currency_native` | str | |
| `fx_rate_to_sar` | float | nullable |
| `spend_sar` | float | |
| `bank_fee_sar` | float | |
| `gross_sar` | float | |
| `review_status` | enum | `pending` / `approved` / `rejected` / `held_needs_fx` / `held_anomaly` / `held_unauthorized` |
| `review_flags` | list[str] | `["needs_fx","provider_drift_15pct","missing_token","stale_data_7d"]` |
| `created_at` | datetime | عند دفع dailies إلى review |
| `decided_at` | datetime (nullable) | |
| `decided_by` | str (nullable) | user_id للمعتمد |
| `decision_note` | str (nullable) | |
| `posted_txn_group_id` | str (nullable) | بعد الترحيل |
| `posted_at` | datetime (nullable) | |
| `idempotency_key` | str | `v2:{user_id}:{account_id}:{date}` لمنع double-post |

**Index:** `{user_id:1, account_id:1, date:1}` unique (يوم واحد في المراجعة لكل حساب).

> **Flow:** SVC `promote_dailies_to_review` يفحص dailies بحالة `final` ولم تُرفع للمراجعة بعد → يُنشئ سطر review بحالة `pending`. التاجر يرى/يوافق/يرفض. لا ترحيل بدون موافقة.

### 1.6 `ads_v2_currency_settings` — مصدر FX رسمي

| الحقل | النوع | الوصف |
|---|---|---|
| `id` | str | |
| `user_id` | str | |
| `from_currency` | str | مثال: USD |
| `to_currency` | str | دائماً SAR في الإصدار الأول |
| `rate` | float | مثال 3.752 |
| `effective_from` | date | السعر يسري من هذا التاريخ |
| `effective_to` | date (nullable) | إلى تاريخ معين أو مستمر |
| `source` | enum | `manual` / `sama_api` / `xe_api` |
| `note` | str | |
| `created_at` / `created_by` | tlj | |

**Index:** `{user_id:1, from_currency:1, to_currency:1, effective_from:-1}`.

> **قواعد:**
> - لو تاريخ صرف بدون rate سارٍ → السطر يدخل review بـ `held_needs_fx`. **لا 3.75 افتراضي.**
> - تعديل rate بأثر رجعي ممكن لأيام لم تُعتمَد بعد. الأيام المعتمدة محفوظة بسعرها الأصلي في `spend_daily`.

### 1.7 `ads_v2_sync_runs` — سجل تنفيذ المزامنة (Audit)

| الحقل | النوع | الوصف |
|---|---|---|
| `id` | str | |
| `user_id` | str | |
| `account_id` | str (nullable) | إذا run كامل بدلاً من حساب واحد |
| `provider` | enum | |
| `trigger` | enum | `cron` / `manual` / `webhook` / `backfill` |
| `started_at` / `finished_at` | datetime | |
| `status` | enum | `running` / `success` / `partial` / `failed` |
| `api_calls_made` | int | |
| `rows_inserted_raw` | int | |
| `rows_updated_daily` | int | |
| `error_summary` | str (nullable) | |
| `error_per_account` | dict | `{account_id: error_msg}` |

**Index:** `{user_id:1, started_at:-1}`, `{status:1}`.

### 1.8 `ads_v2_ledger_postings` — Audit Log للترحيلات

| الحقل | النوع | الوصف |
|---|---|---|
| `id` | str | |
| `review_id` | str | FK → `ads_v2_spend_review.id` |
| `txn_group_id` | str | FK → `general_ledger.txn_group_id` |
| `user_id` | str | |
| `account_id` | str | |
| `date` | str | |
| `legs_summary` | list | `[{entry_type, side, amount, account_path}]` للتدقيق السريع |
| `posted_at` | datetime | |
| `posted_by` | str | user_id |
| `reversed_at` | datetime (nullable) | |
| `reversed_by` | str (nullable) | |
| `reversal_txn_group_id` | str (nullable) | |

**Index:** `{review_id:1}` unique, `{txn_group_id:1}`.

### 1.9 `ads_v2_feature_flags` — تفعيل تدريجي

| الحقل | النوع | الوصف |
|---|---|---|
| `user_id` | str | |
| `v2_enabled` | bool | افتراضياً false |
| `v2_replaces_v1_reports` | bool | عندما تنتقل التقارير لـ V2 |
| `v1_writes_disabled` | bool | إيقاف كتابة V1 الجديدة عند الانتقال |
| `enabled_at` | datetime | |

**Index:** `{user_id:1}` unique.

---

## 2. الـ APIs الجديدة (Endpoints)

> **Prefix:** كل الـ endpoints تحت `/api/ads-v2/`  
> **Auth:** كل endpoint يحتاج `Depends(current_user)`  
> **Role:** الكتابة محدودة على `owner` و `admin`. القراءة على كل الأدوار.

### 2.1 Accounts CRUD

| Method | Path | الوصف |
|---|---|---|
| GET | `/ads-v2/accounts` | قائمة الحسابات (مع sync_status و آخر مزامنة) |
| GET | `/ads-v2/accounts/{id}` | تفاصيل حساب |
| POST | `/ads-v2/accounts` | إضافة حساب يدوي (للحالات بدون OAuth) |
| PATCH | `/ads-v2/accounts/{id}` | تعديل bank_fee, sync_status, display_name |
| DELETE | `/ads-v2/accounts/{id}` | منع الحذف إذا له dailies أو reviews; soft-disable فقط |
| POST | `/ads-v2/accounts/{id}/test-connection` | اختبار token + permissions |

### 2.2 OAuth Connections (per organization)

| Method | Path | الوصف |
|---|---|---|
| GET | `/ads-v2/connections` | كل tokens المرتبطة (بدون كشف القيم) |
| POST | `/ads-v2/connections/{provider}/start` | يُعيد OAuth URL |
| GET | `/ads-v2/connections/{provider}/callback` | يستقبل code → يستخرج org_id → ينشئ credential |
| DELETE | `/ads-v2/connections/{id}` | فصل + تعطيل الحسابات التابعة |
| POST | `/ads-v2/connections/{id}/refresh` | تجديد token يدوي |

### 2.3 Sync (المزامنة)

| Method | Path | الوصف |
|---|---|---|
| POST | `/ads-v2/sync/account/{id}` | مزامنة حساب واحد لتاريخ معين أو نطاق |
| POST | `/ads-v2/sync/all` | مزامنة كل حسابات المستخدم (manual trigger) |
| GET | `/ads-v2/sync/runs` | تاريخ آخر runs (paginated) |
| GET | `/ads-v2/sync/runs/{id}` | تفاصيل run معين (errors per account) |

### 2.4 Currency Settings

| Method | Path | الوصف |
|---|---|---|
| GET | `/ads-v2/currency/rates` | كل أسعار الصرف المُدخَلة |
| POST | `/ads-v2/currency/rates` | إضافة سعر صرف بـ effective_from |
| PATCH | `/ads-v2/currency/rates/{id}` | تعديل rate أو effective range |
| DELETE | `/ads-v2/currency/rates/{id}` | حذف منعاً للاستخدام (فقط إذا لم يُستخدَم في `spend_daily` بعد) |
| GET | `/ads-v2/currency/lookup?from=USD&to=SAR&date=2026-06-23` | يُرجع السعر السارّي لتاريخ معين |

### 2.5 Spend Data (للقراءة فقط)

| Method | Path | الوصف |
|---|---|---|
| GET | `/ads-v2/spend/daily?account_id=&from=&to=` | السطور من `spend_daily` |
| GET | `/ads-v2/spend/raw?account_id=&date=` | السطور من `spend_raw` (للتدقيق) |
| GET | `/ads-v2/spend/summary?from=&to=` | ملخص حسب provider/account/يوم |

### 2.6 Review Queue (طبقة المراجعة)

| Method | Path | الوصف |
|---|---|---|
| GET | `/ads-v2/review?status=pending` | قائمة المعلَّقة |
| GET | `/ads-v2/review/{id}` | تفاصيل سطر مراجعة |
| POST | `/ads-v2/review/{id}/approve` | يعتمد → يُرحَّل إلى GL |
| POST | `/ads-v2/review/{id}/reject` | يرفض مع note |
| POST | `/ads-v2/review/bulk-approve` | مع نوع فلتر |
| POST | `/ads-v2/review/promote-dailies` | يدفع dailies الـ final إلى الـ review |

### 2.7 Ledger Postings (Audit)

| Method | Path | الوصف |
|---|---|---|
| GET | `/ads-v2/ledger/postings?account_id=&from=&to=` | كل القيود المُرحَّلة من V2 |
| POST | `/ads-v2/ledger/postings/{id}/reverse` | عكس قيد مع reason |

### 2.8 Reports (المصدر الموحَّد)

| Method | Path | الوصف |
|---|---|---|
| GET | `/ads-v2/reports/ads-by-day` | تجميع spend_daily حسب يوم |
| GET | `/ads-v2/reports/ads-by-account` | تجميع حسب account |
| GET | `/ads-v2/reports/ads-by-provider` | تجميع حسب provider |
| GET | `/ads-v2/reports/debt-by-account` | المديونية الحالية = SUM(`general_ledger` debit-credit) لكل ad_account |
| GET | `/ads-v2/reports/reconciliation/{account_id}` | spend_daily SUM vs ledger SUM (يكشف drift) |

### 2.9 Diagnostics (Read-Only)

| Method | Path | الوصف |
|---|---|---|
| GET | `/ads-v2/diagnostics/sync-health` | scheduler heartbeat + accounts sync status |
| GET | `/ads-v2/diagnostics/review-queue-aging` | كم يوم بقي السطر في pending |
| GET | `/ads-v2/diagnostics/fx-coverage` | تواريخ بدون fx_rate في currency_settings |

---

## 3. الصفحات الجديدة في الواجهة (Frontend)

> **Prefix:** كل الصفحات تحت `/ads-v2/*` في الـ router  
> **Sidebar:** قسم جديد "إعلانات V2" منفصل عن "إعلانات" (V1) — حتى يرى التاجر الانتقال

### 3.1 الخريطة

| الصفحة | المسار | الوصف |
|---|---|---|
| Dashboard V2 | `/ads-v2` | بطاقات: spend اليوم/الأسبوع/الشهر، sync_status لكل حساب، عدد reviews pending |
| Accounts | `/ads-v2/accounts` | جدول الحسابات، أزرار test/disable/edit bank_fee |
| Account Detail | `/ads-v2/accounts/:id` | spend_daily history، سجل runs، سجل postings |
| Connections | `/ads-v2/connections` | كل OAuth connections حسب org، زر "اربط منظمة جديدة" |
| Sync Runs | `/ads-v2/sync` | تاريخ التشغيلات، فلتر بـ status |
| Currency Settings | `/ads-v2/currency` | جدول CRUD لأسعار الصرف بـ effective ranges |
| Review Queue | `/ads-v2/review` | قائمة pending مع filters (provider, account, date range) — أزرار approve/reject |
| Ledger Postings | `/ads-v2/postings` | كل ما تم ترحيله من V2 إلى GL — كل سطر فيه زر "افتح في GL" + "عكس" |
| Reports | `/ads-v2/reports` | تبويبات: by day, by account, by provider, debt summary |
| Reconciliation | `/ads-v2/reports/reconciliation/:account_id` | جدول: spend_daily ↔ ledger ↔ فرق |
| Diagnostics | `/ads-v2/diagnostics` | sync health, review aging, fx coverage |

### 3.2 قواعد UI

- شعار V2 ظاهر في كل صفحة (Badge أزرق "V2")
- حقول `bank_fee_enabled` و `bank_fee_rate` ظاهرة بوضوح في كل سطر review
- لون مختلف لكل `review_status` (أصفر للـ pending, أحمر للـ held, أخضر للـ approved)
- زر **View V1 Counterpart** يأخذ التاجر للصفحة المقابلة في V1 لمقارنة الأرقام في فترة الانتقال

---

## 4. تدفق البيانات الكامل (End-to-End)

### 4.1 Cron Job (كل ساعة)

```
[Scheduler: hourly tick]
  │
  ├─ for each user where ads_v2_enabled=true:
  │    │
  │    ├─ for each ads_v2_accounts where sync_status='active':
  │    │    │
  │    │    ├─ Insert ads_v2_sync_runs (status='running')
  │    │    ├─ Call provider API (Meta/Snap/TikTok/Google)
  │    │    ├─ Upsert ads_v2_spend_raw (one row per dimension_keys)
  │    │    ├─ Recompute ads_v2_spend_daily for affected dates:
  │    │    │    ├─ SUM spend_native from latest raw
  │    │    │    ├─ Lookup fx_rate from ads_v2_currency_settings
  │    │    │    │   (if missing → spend_sar=null, confidence=provisional)
  │    │    │    ├─ Compute bank_fee_sar from account.bank_fee_*
  │    │    │    ├─ gross_sar = spend_sar + bank_fee_sar
  │    │    │    └─ Set confidence='final' if date < today-3
  │    │    └─ Update ads_v2_sync_runs (status='success')
  │    │
  │    └─ ads_v2_accounts.last_sync_finished_at = now()
  │
  └─ Done.
```

### 4.2 Review Promotion (يومي 02:00 توقيت الرياض)

```
[Promote dailies → review]
  │
  ├─ for each ads_v2_spend_daily with confidence='final'
  │    AND NOT EXISTS ads_v2_spend_review(account_id, date):
  │    │
  │    ├─ flags = []
  │    ├─ if spend_sar is null  → flags.append("needs_fx") → status='held_needs_fx'
  │    ├─ if provider_drift > 15% (vs previous fetch) → flags.append("provider_drift_15pct") → 'held_anomaly'
  │    ├─ if account.oauth_credential.status='expired' → flags.append("missing_token") → 'held_unauthorized'
  │    ├─ if last_synced_date < today-7 → flags.append("stale_data_7d") → 'held_anomaly'
  │    ├─ if no flags → status='pending'
  │    └─ Insert ads_v2_spend_review
  │
  └─ Done.
```

### 4.3 Approval & Ledger Posting

```
[User clicks Approve in /ads-v2/review/:id]
  │
  ├─ Verify review_status in ('pending','held_*')  → if held: require admin override
  ├─ Build legs:
  │    Leg 1: DEBIT  expense:advertising:{provider}:{account_id}  | spend_sar
  │    Leg 2: CREDIT counterparty:ad_account:{account_id}         | spend_sar
  │    Leg 3: (if bank_fee_enabled)
  │           DEBIT expense:bank_fee:advertising                  | bank_fee_sar
  │           CREDIT counterparty:ad_account:{account_id}         | bank_fee_sar
  │    Total: gross_sar
  │
  ├─ post_txn_group(legs, idempotency_key='v2:{user_id}:{account_id}:{date}')
  ├─ Update ads_v2_spend_review:
  │    review_status='approved'
  │    decided_at=now(), decided_by=user.id
  │    posted_txn_group_id=…
  │    posted_at=now()
  │
  ├─ Insert ads_v2_ledger_postings (audit)
  │
  └─ Return: { posted: true, txn_group_id: …, legs: [...] }
```

### 4.4 Reversal

```
[User clicks Reverse posting]
  │
  ├─ Require reason (string)
  ├─ Build mirror legs (opposite side, same amounts)
  ├─ post_txn_group(mirror_legs, idempotency_key='v2_rev:{posting_id}')
  ├─ Update original posting:
  │    reversed_at=now(), reversed_by=user.id, reversal_txn_group_id=…
  ├─ Update review:
  │    review_status='pending' (يمكن إعادة المراجعة)
  │
  └─ Done.
```

---

## 5. ما الذي سيتم إيقافه من Ads V1

> **القاعدة الذهبية:** لا نحذف، لا نُعدِّل بيانات قديمة. نوقف الكتابة الجديدة فقط، ونُعطي V1 وضع "Read-Only Legacy".

### 5.1 Phase A — V2 يعمل بالتوازي (V1 لا يزال نشطاً)

- V1 و V2 يكتبان في collections منفصلة
- V1 الـ `_ad_spend_window_post_loop` لا يزال يكتب في `general_ledger` بـ entry_type قديم
- V2 يكتب بـ entry_type جديد: `ads_v2_expense`, `ads_v2_balance_credit`, `ads_v2_debt_credit`, `ads_v2_bank_fee`
- التقارير القديمة تبقى تقرأ V1، التقارير V2 تقرأ V2
- التاجر يقارن يدوياً

### 5.2 Phase B — التحول للقراءة من V2

عندما يثق التاجر في V2:

- `ads_v2_feature_flags.v2_replaces_v1_reports = true`
- صفحات V1 (`/ad-accounts`, `/ads-report`, `/advertising-expenses-report`) تُضاف لها لافتة "Legacy" وتُعرَض المصادر القديمة، لكن البطاقات الرئيسية في Dashboard تقرأ V2.

### 5.3 Phase C — إيقاف كتابة V1

عندما يثق التاجر تماماً (شهر+ من V2 مستقر):

- `ads_v2_feature_flags.v1_writes_disabled = true`
- يُعطَّل `_ad_spend_window_post_loop` (السكدجولر القديم)
- يُعطَّل `_run_sync_for_all` لمزودي Meta/Snap (V1 cron)
- V1 collections (`ad_account_ledger`, `meta_ads_daily`, `snapchat_account_daily`) تبقى للقراءة فقط
- صفحات V1 تُحوَّل إلى "View Historical" — لا أزرار، لا إعدادات

### 5.4 ما الذي **لن** يُحذف

- ❌ لن يُحذف أي صف من `general_ledger` خاص بـ V1.
- ❌ لن يُعدَّل أي رصيد قديم.
- ❌ لن تُحذف collections القديمة.
- ❌ لن تُحذف صفحات V1 لمدة 6 أشهر على الأقل بعد Phase C.

---

## 6. خطة الترحيل التدريجي (Migration Roadmap)

### المرحلة 0 — التأسيس (لا أرقام في GL بعد)

**المدة المتوقعة:** 3-4 أيام عمل

| # | المهمة | المخرج |
|---|---|---|
| 0.1 | إنشاء كل collections مع indexes | جدول 1.x مكتمل |
| 0.2 | بناء `ads_v2_accounts` CRUD + UI | صفحة Accounts فارغة قابلة للإضافة اليدوية |
| 0.3 | بناء `ads_v2_currency_settings` CRUD + UI | صفحة Currency تعمل |
| 0.4 | بناء `ads_v2_oauth_credentials` + OAuth flows لكل مزود (بدءاً بـ Snapchat لحل مشكلة الرياض) | ربط منظمة Snap منفصلة |
| 0.5 | اختبارات وحدات لكل CRUD | tests/test_ads_v2_phase0.py |

**معيار النجاح:** التاجر يستطيع إضافة حساب يدوياً، ربط منظمة Snap، إضافة سعر صرف.

### المرحلة 1 — المزامنة (Raw + Daily فقط)

**المدة:** 4-5 أيام عمل

| # | المهمة | المخرج |
|---|---|---|
| 1.1 | بناء adapters لكل مزود (Meta/Snap/TikTok/Google) — استدعاء API + تحويل إلى `spend_raw` | sync يعمل manual لكل حساب |
| 1.2 | بناء `recompute_daily` job (محرك التجميع) | spend_daily يُحدَّث بعد كل sync |
| 1.3 | بناء scheduler V2 (cron ساعي، منفصل عن V1 cron) + heartbeat جدول `ads_v2_sync_runs` | sync تلقائي يعمل |
| 1.4 | بناء صفحة Sync Runs + Account Detail | التاجر يرى ماذا يحدث |
| 1.5 | اختبارات: محاكاة API responses، تحقق idempotency, تحقق fx fallback="held" | tests/test_ads_v2_phase1.py |

**معيار النجاح:** spend_daily يحتوي بيانات Meta + Snap (Self Service + الرياض) ليوم كامل، مع fx_rate من ads_v2_currency_settings، بدون أي fallback ثابت.

### المرحلة 2 — المراجعة والترحيل

**المدة:** 3-4 أيام عمل

| # | المهمة | المخرج |
|---|---|---|
| 2.1 | بناء `promote_dailies_to_review` job + cron 02:00 الرياض | review queue تمتلئ تلقائياً |
| 2.2 | بناء UI صفحة Review + approve/reject/bulk | التاجر يعتمد |
| 2.3 | بناء `post_review_to_gl` + audit trail في `ads_v2_ledger_postings` | قيود تظهر في GL |
| 2.4 | بناء صفحة Ledger Postings + Reverse | التاجر يستطيع العكس |
| 2.5 | اختبارات end-to-end: من API → raw → daily → review → GL → reverse | tests/test_ads_v2_phase2.py |

**معيار النجاح:** يوم كامل من Meta + Snap يصل إلى `general_ledger` بعد موافقة التاجر بقيد double-entry سليم.

### المرحلة 3 — التقارير الموحدة

**المدة:** 2-3 أيام عمل

| # | المهمة | المخرج |
|---|---|---|
| 3.1 | بناء `/ads-v2/reports/*` endpoints | كل التقارير تقرأ من spend_daily/GL |
| 3.2 | بناء صفحة Reports V2 (4 تبويبات) | التاجر يرى الأرقام |
| 3.3 | بناء Reconciliation report (spend_daily ↔ GL) | drift يظهر صراحة |
| 3.4 | اختبارات: مقارنة V2 reports مع GL مباشرة لا فرق | tests/test_ads_v2_phase3.py |

**معيار النجاح:** التقارير الأربعة (by day/account/provider/debt) كلها تُعطي نفس الإجماليات لنفس الفترة. Reconciliation drift = 0.

### المرحلة 4 — الانتقال (Switchover)

**المدة:** أسبوع مراقبة + قرار التاجر

| # | المهمة | المخرج |
|---|---|---|
| 4.1 | يومياً، التاجر يقارن V1 و V2 لنفس اليوم | جدول مقارنة في صفحة Diagnostics |
| 4.2 | عند الثقة: تفعيل `v2_replaces_v1_reports=true` | Dashboard الرئيسي يقرأ V2 |
| 4.3 | شاشة "Legacy" تُضاف لصفحات V1 | التمييز واضح |
| 4.4 | بعد شهر مستقر: `v1_writes_disabled=true` | V1 cron يُعطَّل |

**معيار النجاح:** التاجر يفعِّل العلَم بثقة، ولا يطلب الرجوع لـ V1 لأي رقم.

---

## 7. خطة الاختبارات

### 7.1 Unit Tests

| الملف | التغطية |
|---|---|
| `test_ads_v2_accounts_crud.py` | إضافة/تعديل/حذف منع الحذف عند ربط بيانات |
| `test_ads_v2_oauth_isolation.py` | tokens لمنظمات مختلفة لا تتداخل |
| `test_ads_v2_currency_lookup.py` | البحث بـ effective_from يُرجع السعر السارّي |
| `test_ads_v2_currency_no_fallback.py` | غياب سعر = review held، **ليس 3.75** |
| `test_ads_v2_bank_fee_calc.py` | حساب العمولة % و flat صحيح |
| `test_ads_v2_idempotency.py` | إعادة sync لنفس اليوم لا تُضاعف raw، daily ينضبط |

### 7.2 Integration Tests (mocked provider APIs)

| الملف | التغطية |
|---|---|
| `test_ads_v2_meta_sync.py` | mock Meta API → raw → daily صحيح |
| `test_ads_v2_snap_multi_org.py` | حسابان بـ org مختلفة، كل واحد بـ token صحيح، لا تداخل |
| `test_ads_v2_snap_riyadh_blocked.py` | لو org بدون token → الحساب يُعلَّم `unauthorized` |
| `test_ads_v2_recompute_daily.py` | تعديل raw يُعيد حساب daily الصحيح |
| `test_ads_v2_promote_to_review.py` | dailies بـ confidence=final تنتقل تلقائياً |

### 7.3 End-to-End Tests (testing_agent_v3_fork)

سيتم تشغيل سيناريوهات كاملة:
- إضافة حساب → mock API → sync → daily يظهر → review pending → approve → GL يحتوي القيد → report يُظهره
- العكس: post → reverse → mirror legs → review back to pending
- multi-org Snap: ربط orgs → كل حساب يُسحب من token الصحيح

### 7.4 Regression Tests

- بعد كل مرحلة: التأكد أن V1 لم يتأثر (V1 cron يستمر، V1 reports تستمر)
- اختبار: total V1 لـ يوم معين ≈ total V2 (مع توقع فروق صغيرة بسبب فلاتر مختلفة)

### 7.5 Test Data

- بيئة `db_test_ads_v2` منفصلة في mongo
- mock fixtures لكل مزود (Meta/Snap/TikTok/Google) محفوظة كـ JSON files
- seeding script: 30 يوم بيانات mock + counterpart V1 للمقارنة

---

## 8. كيف نضمن أن كل التقارير تقرأ من نفس المصدر؟

### 8.1 آلية الحوكمة

| الأداة | الوصف |
|---|---|
| **`ads_v2_data_layer.py`** (طبقة وحيدة) | كل قراءة للأرقام تمر عبر دالة واحدة `get_spend_by(...)`. أي تقرير يستخدم `db.ads_v2_spend_daily.find(...)` مباشرة = bug. |
| **`ads_v2_ledger_reader.py`** | كل قراءة لأرصدة الإعلانات من GL تمر عبر `get_ad_debt(account_id, as_of_date)`. لا حسابات متفرقة. |
| **Linting Rule** | اختبار ثابت يفحص أن لا ملف خارج طبقة البيانات يحتوي `db.ads_v2_spend_daily` أو `entry_type.startswith('ads_v2_')`. |
| **Reconciliation Endpoint** | `/ads-v2/reports/reconciliation/{account_id}` يُرجع `spend_daily_sum` و `ledger_sum` لكل يوم. أي فرق ≠ 0 = alert. |
| **Contract Test** | اختبار يستدعي كل تقارير V2 لنفس الفترة ويتأكد أن المجاميع المتقاطعة متطابقة (sum by day == sum by account للحساب الواحد). |

### 8.2 التزامات على مستوى الكود

```
✅ كل endpoint في /ads-v2/reports/* يستدعي ads_v2_data_layer.get_*
❌ ممنوع: استخدام pymongo aggregation مباشر داخل route file
✅ كل insertion إلى spend_daily تمر عبر recompute_daily(account_id, date)
❌ ممنوع: تعديل spend_daily من خارج المحرك
✅ كل قيد GL من V2 يمر عبر post_review_to_gl()
❌ ممنوع: استدعاء post_txn_group من خارج طبقة V2
```

### 8.3 Self-Verification في Dashboard V2

كل صفحة تقرير تعرض في الأسفل:
- "آخر مزامنة: ..."
- "آخر recompute: ..."
- "Reconciliation drift: 0.00 SAR ✓" (أو drift صريح)
- "Source: ads_v2_spend_daily" badge

---

## 9. قرار التاجر المطلوب الآن

> **لا نبدأ البرمجة قبل أن توافق على هذه النقاط صراحة:**

### 9.1 موافقات معمارية

- [ ] هل تقبل التصميم الكامل المذكور أعلاه؟
- [ ] هل تقبل المراحل 0 → 4 بترتيبها وتقديرات وقتها؟
- [ ] هل تقبل أن V1 سيبقى يعمل بالكامل أثناء بناء V2 (لا يتأثر)؟
- [ ] هل تقبل أن قيود GL من V2 ستحمل entry_type مختلف عن V1 (لتمييز المصدر)؟
- [ ] هل تقبل خطة Switchover بثلاث مراحل (Phase A/B/C)؟

### 9.2 قرارات تشغيلية تحتاج موافقتك

| قرار | الخيار أ | الخيار ب |
|---|---|---|
| **هل تريد بدء العمل بـ Snapchat فقط أم كل المزودين؟** | Snap فقط (لحل الرياض سريعاً) | كل المزودين دفعة (تنفيذ أطول) |
| **هل تريد ربط Google Ads و TikTok الآن أم في إصدار لاحق؟** | الآن | لاحقاً |
| **هل تريد UI مراجعة فردية أم batch؟** | فردي per-row | bulk (يوم/أسبوع/حساب) |
| **هل تريد العمولة البنكية حسب نسبة % فقط، أم flat + نسبة معاً؟** | % فقط | الاثنان |
| **عند rejection: هل يعود السطر للـ pending أم يبقى rejected نهائياً؟** | يعود للمراجعة بعد re-sync | rejected نهائي |
| **هل تحتاج Multi-FX (مثلاً USD → SAR و EUR → SAR)؟** | فقط USD→SAR الآن | كل العملات |

### 9.3 أسرار / مفاتيح API مطلوبة (لاحقاً عند المرحلة 1)

- Meta Marketing API: App ID + App Secret + Long-Lived Token
- Snapchat Marketing API: Client ID + Secret + Refresh Token (لكل منظمة منفصل)
- TikTok Ads API: App ID + Secret (إذا قررت تضمينه)
- Google Ads API: Developer Token + OAuth Client + Customer IDs

---

## 10. ما لن يتغير

- ✋ `general_ledger` يبقى SSOT المالي العام للتطبيق كله.
- ✋ `compute_balance()` يبقى محرك الأرصدة.
- ✋ الـ counterparties الموجودة لا تُحذف؛ V2 يبني فوقها بربط `external_account_id`.
- ✋ التقارير المالية العامة (المركز المالي، الأرباح/الخسائر) ستقرأ من GL كما هي اليوم، وستلتقط قيود V2 تلقائياً.

---

# 📋 الموافقة المطلوبة قبل الانطلاق

**أنت بحاجة إلى:**

1. **الموافقة على التصميم** (نعم/لا/تعديل) — أعطني نقاطك للتعديل.
2. **اختيار الخيارات في القسم 9.2** — أنتظر إجاباتك على الستة قرارات.
3. **التأكد من توفر مفاتيح API** عند المرحلة 1 (ليس الآن).
4. **اختيار نقطة البداية:** هل نبدأ بـ Snap فقط (المرحلة 0 + 1 لـ Snap)، أم نبني الـ Foundation لكل المزودين دفعة؟

**ملاحظة:** الوثيقة محفوظة في `/app/memory/ADS_V2_DESIGN.md` لتكون مرجعاً ثابتاً طوال البناء.
