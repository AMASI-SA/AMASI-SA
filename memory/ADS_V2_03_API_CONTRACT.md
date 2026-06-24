# 🔌 Ads V2 — API Contract (Endpoint × Endpoint)

> **الحالة:** مسودة لاعتماد التاجر  
> **التاريخ:** 2026-06-24  
> **Prefix:** كل الـ endpoints تحت `/api/ads-v2/`  
> **Auth:** كل الـ endpoints تتطلب JWT صالح (`Depends(current_user)`).  
> **Role-based:** أدوار محددة في كل endpoint أدناه.  
> **Feature flag:** كل عمليات الكتابة تتطلب `ads_v2_feature_flags.v2_enabled=True` للمستخدم.

---

## 0. ثوابت عامة

### 0.1 Response envelope (موحَّد)

```jsonc
{
  "ok": true,
  "data": { ... },             // payload الفعلي
  "meta": {
    "request_id": "uuid",
    "took_ms": 42,
    "source_layer": "ads_v2_data_layer"   // كل قراءة تقرير
  }
}
```

عند الخطأ:
```jsonc
{
  "ok": false,
  "error": {
    "code": "ADS_V2_VALIDATION_001",
    "message": "Bank fee method requires rate_pct > 0",
    "field": "bank_fee_rate_pct"
  }
}
```

### 0.2 Pagination (موحَّد)

```jsonc
GET /ads-v2/sync/runs?page=1&page_size=50

{
  "data": [...],
  "pagination": {
    "page": 1, "page_size": 50, "total": 234, "total_pages": 5
  }
}
```

### 0.3 الأدوار (Role Matrix)

| الدور | Read | Write Settings | Approve Reviews | Reverse Postings | Toggle Flags |
|---|---|---|---|---|---|
| `owner` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `admin` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `accountant` | ✅ | ❌ | ✅ | ✅ | ❌ |
| `operations` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `viewer` | ✅ | ❌ | ❌ | ❌ | ❌ |

---

## 1. Feature Flags

### `GET /ads-v2/flags` — Read user's flags
**Role:** any  
**Response:**
```jsonc
{
  "v2_enabled": false,
  "v2_reports_replace_v1": false,
  "v1_writes_disabled": false,
  "enabled_at": null,
  "phase": "phase_a"               // computed: a | b | c
}
```

### `POST /ads-v2/flags/toggle` — Toggle a flag
**Role:** owner  
**Body:** `{ field: "v2_enabled", value: true, note: "Enabling Phase 0" }`  
**Side effect:** يُضاف entry لـ `history` array + log في `ads_v2_review_history` (action=feature_flag_toggle).

---

## 2. OAuth Credentials

### `GET /ads-v2/connections`
**Role:** any  
**Query:** `provider=snapchat` (optional)  
**Response:** قائمة كل credentials (بدون `access_token_enc`):
```jsonc
[
  {
    "id": "...",
    "provider": "snapchat",
    "organization_id": "abda4a7b-...",
    "organization_name": "متجر أماسي",
    "status": "active",
    "token_expires_at": "2026-08-01T00:00:00Z",
    "scopes_granted": [...],
    "connected_at": "...",
    "accounts_linked_count": 1
  }
]
```

### `POST /ads-v2/connections/{provider}/start`
**Role:** owner | admin  
**Body:** `{ "return_to": "/ads-v2/connections" }`  
**Response:**
```jsonc
{
  "oauth_url": "https://accounts.snapchat.com/login/oauth2/authorize?...",
  "state": "encrypted-token",
  "ttl_seconds": 300
}
```

### `GET /ads-v2/connections/{provider}/callback`
**Role:** owner | admin  
**Query:** `code=...&state=...`  
**Side effect:** 
1. يبادل الـ code بـ access_token
2. يستخرج `organization_id` و `organization_name` من الـ token
3. يُنشئ `ads_v2_oauth_credentials` (أو يحدِّث إذا موجود)
4. لا يُنشئ حسابات تلقائياً — يُحوِّل التاجر لصفحة "اختر الحسابات لتفعيلها"

### `DELETE /ads-v2/connections/{id}`
**Role:** owner  
**Side effect:** 
- ينقل status إلى `revoked`
- يُعطِّل كل `ads_v2_accounts` المربوطة بـ `sync_status='unauthorized'`
- لا يحذف الحسابات (للحفاظ على history)

### `POST /ads-v2/connections/{id}/refresh`
**Role:** owner | admin  
**Response:** `{ ok: true, new_expires_at: "..." }`

### `POST /ads-v2/connections/{id}/test`
**Role:** owner | admin  
**Side effect:** يستدعي endpoint بسيط للمزود (مثل `/me`) لاختبار صحة الـ token.

---

## 3. Accounts CRUD

### `GET /ads-v2/accounts`
**Role:** any  
**Query:** `provider=`, `sync_status=`, `include_disabled=false`  
**Response:**
```jsonc
[
  {
    "id": "...",
    "provider": "snapchat",
    "external_account_id": "cf8ea7c9-...",
    "display_name": "متجر أماسي سعودي",
    "organization_name": "Establishment AMASI AL-KHALIJ",
    "currency_native": "SAR",
    "timezone": "Asia/Riyadh",
    "bank_fee_enabled": false,
    "bank_fee_method": "none",
    "sync_status": "active",
    "last_sync_finished_at": "...",
    "last_synced_date": "2026-06-23",
    "oauth_credential": {
      "id": "...",
      "organization_name": "Establishment AMASI AL-KHALIJ",
      "status": "active"
    }
  }
]
```

### `GET /ads-v2/accounts/{id}`
**Role:** any → يُرجع كل التفاصيل + إحصائيات (آخر 30 يوم spend total).

### `POST /ads-v2/accounts/discover`
**Role:** owner | admin  
**Body:** `{ oauth_credential_id: "..." }`  
**Description:** يستدعي API المزود لجلب قائمة الحسابات تحت هذا الـ credential. يُرجع الحسابات بدون تفعيل.  
**Response:**
```jsonc
{
  "discovered": [
    {
      "external_account_id": "cf8ea7c9-...",
      "name": "متجر أماسي سعودي",
      "currency_native": "SAR",
      "already_linked": false
    }
  ]
}
```

### `POST /ads-v2/accounts`
**Role:** owner | admin  
**Body:**
```jsonc
{
  "oauth_credential_id": "...",
  "provider": "snapchat",
  "external_account_id": "cf8ea7c9-...",
  "display_name": "متجر أماسي سعودي",
  "currency_native": "SAR",
  "timezone": "Asia/Riyadh"
}
```
**Validation:** فرض uniqueness على `(user_id, provider, external_account_id)`.

### `PATCH /ads-v2/accounts/{id}`
**Role:** owner | admin  
**Body:** أي subset من الحقول (display_name, bank_fee_*, sync_status, timezone).  
**Validation:** قواعد bank_fee_method من Schema doc.  
**Side effect:** تعديل bank_fee → recompute لـ `ads_v2_spend_daily` لكل الأيام **غير المُعتمَدة** فقط.

### `DELETE /ads-v2/accounts/{id}`
**Role:** owner  
**Behavior:** soft-delete (`soft_deleted=true`, `sync_status='paused'`).  
**Validation:** يمنع إذا له `ads_v2_ledger_postings` (يفرض على المستخدم reverse أولاً).

---

## 4. Currency Settings

### `GET /ads-v2/currency`
**Role:** any  
**Query:** `from=USD&to=SAR&active_only=true`  
**Response:** قائمة أسعار صرف.

### `GET /ads-v2/currency/lookup`
**Role:** any  
**Query:** `from=USD&to=SAR&date=2026-06-23`  
**Response:**
```jsonc
{
  "rate": 3.752,
  "source": "manual",
  "effective_from": "2026-06-01",
  "setting_id": "..."
}
```
أو 404 إذا غاب السعر السارّي → يُرجع `{ error: { code: "FX_NOT_FOUND" } }`.

### `POST /ads-v2/currency`
**Role:** owner | admin  
**Body:**
```jsonc
{
  "from_currency": "USD",
  "to_currency": "SAR",
  "rate": 3.752,
  "effective_from": "2026-06-01",
  "effective_to": null,
  "source": "manual",
  "notes": "SAMA reference"
}
```
**Validation:** لا تداخل في النطاقات (effective_from..to) لنفس الزوج.

### `PATCH /ads-v2/currency/{id}`
**Role:** owner | admin  
**Behavior:** تعديل rate أو effective_to.  
**Validation:** لا تعديل إذا السعر استُخدم في `ads_v2_spend_daily` لأيام معتمدة (الـ snapshot في review محفوظ، لكن المنع لتجنب الالتباس).

### `DELETE /ads-v2/currency/{id}` → soft delete (`is_active=false`)

---

## 5. Sync (المزامنة)

### `POST /ads-v2/sync/account/{id}`
**Role:** owner | admin  
**Body:**
```jsonc
{
  "date_from": "2026-06-23",
  "date_to": "2026-06-23",
  "force_refresh": false
}
```
**Response:** `{ run_id: "...", status: "running" }`  
**Side effect:** يُنشئ `ads_v2_sync_runs` ويبدأ async task.

### `POST /ads-v2/sync/all`
**Role:** owner | admin  
**Body:** `{ date_from, date_to, providers: ["meta","snapchat"] }`  
**Response:** `{ run_ids: [...], total_accounts: 4 }`

### `GET /ads-v2/sync/runs`
**Role:** any  
**Query:** `account_id=`, `status=`, `from=`, `to=`, paginated  
**Response:** قائمة runs.

### `GET /ads-v2/sync/runs/{id}`
**Role:** any → run معين + per-account errors.

---

## 6. Spend Data (Read-Only)

### `GET /ads-v2/spend/raw`
**Role:** any  
**Query:** `account_id=&date=&run_id=`  
**Description:** سطور `ads_v2_spend_raw` للتدقيق (10k max per request).

### `GET /ads-v2/spend/daily`
**Role:** any  
**Query:** `account_id=&from=&to=`  
**Response:** سطور `ads_v2_spend_daily` (مع كل الـ fx + bank_fee breakdown).

---

## 7. Reconciliation Layer

### `POST /ads-v2/reconciliation/recheck`
**Role:** owner | admin  
**Body:** `{ account_id, date_from, date_to, force=false }`  
**Description:** يستدعي API المزود الآن ويُحدِّث `platform_reported_*` ويُعيد حساب الـ anomaly_flags.  
**Response:** `{ checked: 5, anomalies_found: 2, run_id: "..." }`

### `GET /ads-v2/reconciliation`
**Role:** any  
**Query:** `account_id=&date_from=&date_to=&status=&has_anomalies=`  
**Response:** سطور reconciliation.

### `GET /ads-v2/reconciliation/{id}`
**Role:** any → سطر معين مع كامل `checks_history`.

### `POST /ads-v2/reconciliation/{id}/override`
**Role:** owner  
**Body:** `{ override_reason: "Confirmed with Ads Manager", override_status: "passed" }`  
**Side effect:** لو الحالة `failed/blocked` → تسمح بالتحويل لـ review رغماً عنها (يُسجَّل في history).

### `GET /ads-v2/reconciliation/promote-preview`
**Role:** any  
**Query:** `account_id=&date_from=&date_to=`  
**Description:** يُرجع قائمة dailies جاهزة للتحويل لـ review (passed/passed_with_warnings) بدون فعل التحويل.

### `POST /ads-v2/reconciliation/promote`
**Role:** owner | admin  
**Body:** `{ account_id?, date_from, date_to }`  
**Side effect:** يُنشئ `ads_v2_spend_review` rows.  
**Response:** `{ promoted: 5, skipped: 2, errors: [...] }`

---

## 8. Review Queue (المراجعة)

### `GET /ads-v2/review`
**Role:** any  
**Query:** 
- `status=pending` (or `held_*`, `approved`, `rejected`, `reopened`)
- `account_id=`, `date_from=`, `date_to=`, `provider=`
- `has_flags=` (true/false)
- `page=1&page_size=50`

### `GET /ads-v2/review/{id}`
**Role:** any → سطر مراجعة كامل + linked reconciliation + history.

### `POST /ads-v2/review/{id}/approve`
**Role:** owner | admin | accountant  
**Body:** `{ note: "Verified" }`  
**Side effect:** 
1. يتحقق من `review_status in ('pending','reopened','held_*')`
2. لو `held_*` → يحتاج override flag في الـ body (`force=true`)
3. يستدعي posting workflow → ينشئ `ads_v2_ledger_postings` + GL legs
4. يُحدِّث review إلى `approved`
5. يُسجِّل في `ads_v2_review_history`

### `POST /ads-v2/review/{id}/reject`
**Role:** owner | admin | accountant  
**Body:** `{ note: "Suspicious data" }`  
**Side effect:** `review_status='rejected'` + history.

### `POST /ads-v2/review/{id}/reopen`
**Role:** owner | admin  
**Body:** `{ note: "Re-investigating" }`  
**Side effect:** 
- من `rejected` → `reopened` (يسمح بـ approve أو reject مرة أخرى)
- من `approved` (بدون reverse) → ممنوع، يجب reverse أولاً
- يزيد `reopen_count`

### `POST /ads-v2/review/{id}/edit-fx`
**Role:** owner  
**Body:** `{ new_fx_rate: 3.755, reason: "Corrected from SAMA" }`  
**Description:** نادر — يسمح بتعديل fx snapshot قبل approve.  
**Side effect:** يُحدِّث snapshot في review (history يحفظ القديم).

### `POST /ads-v2/review/bulk-approve`
**Role:** owner | admin | accountant  
**Body:**
```jsonc
{
  "filter": {
    "account_id": "...",          // optional
    "date_from": "2026-06-15",
    "date_to": "2026-06-21",
    "provider": "meta"             // optional
  },
  "options": {
    "skip_held": true,             // تخطي الـ held_*
    "force_held": false,           // override held_*
    "dry_run": false
  },
  "note": "Approving June Week 3"
}
```
**Response:** `{ matched: 14, approved: 12, skipped: 2, errors: [] }`  
**Side effect:** كل سطر يمر بنفس logic الـ single approve. transaction-safe.

### `POST /ads-v2/review/bulk-reject` (مماثل)

---

## 9. Ledger Postings

### `GET /ads-v2/postings`
**Role:** any  
**Query:** `account_id=&from=&to=&reversed=`  
**Response:** قائمة postings (مع `legs_summary`).

### `GET /ads-v2/postings/{id}`
**Role:** any → posting + GL legs الفعلية + linked review.

### `POST /ads-v2/postings/{id}/reverse`
**Role:** owner | admin | accountant  
**Body:**
```jsonc
{
  "reason": "Refund issued by Meta",
  "follow_up_action": "reopen_review"     // reopen_review | mark_correction | none
}
```
**Side effect:**
1. يُنشئ mirror legs في GL بـ `txn_group_id` جديد
2. يُنشئ سطر في `ads_v2_reversals`
3. يحدّث `ads_v2_ledger_postings.reversed=true, current_reversal_id`
4. لو `follow_up_action='reopen_review'`: ينقل review لـ `reopened`

---

## 10. Reports (المصدر الموحَّد) ⭐

> **قاعدة صارمة:** كل هذه الـ endpoints **تُنفَّذ عبر `ads_v2_data_layer.py`** ولا تستعلم Mongo مباشرة.

### `GET /ads-v2/reports/by-day`
**Query:** `from=&to=&provider=&account_id=`  
**Response:**
```jsonc
{
  "data": [
    {
      "date": "2026-06-23",
      "spend_sar": 5234.50,
      "bank_fee_sar": 145.20,
      "gross_sar": 5379.70,
      "providers": {
        "meta": 510.27,
        "snapchat": 4724.23
      }
    }
  ],
  "totals": {
    "spend_sar": 152340.00,
    "bank_fee_sar": 4310.50,
    "gross_sar": 156650.50
  },
  "meta": {
    "source_layer": "ads_v2_data_layer.get_spend_by_day",
    "ssot": "ads_v2_spend_daily"
  }
}
```

### `GET /ads-v2/reports/by-account`
**Query:** `from=&to=&provider=`  
**Response:** تجميع حسب account.

### `GET /ads-v2/reports/by-provider`
**Query:** `from=&to=`  
**Response:** تجميع حسب provider.

### `GET /ads-v2/reports/debt-by-account`
**Query:** `as_of_date=2026-06-23&provider=`  
**Description:** القراءة الوحيدة من GL — تحسب debt كـ SUM(credit) - SUM(debit) لكل ad_account.  
**Response:**
```jsonc
[
  {
    "account_id": "...",
    "display_name": "متجر أماسي سعودي",
    "debt_sar": 6518.06,
    "as_of_date": "2026-06-23",
    "last_payment_date": "2026-06-20",
    "currency_native": "SAR"
  }
]
```

### `GET /ads-v2/reports/reconciliation/{account_id}`
**Query:** `from=&to=`  
**Description:** يقارن لكل يوم:
- `spend_daily_sum_sar` (من spend_daily)
- `ledger_sum_sar` (من general_ledger entry_type starts_with 'ads_v2_')
- `platform_sum_sar` (من reconciliation table)
- `drift_internal_sar` = spend_daily - ledger
- `drift_platform_sar` = spend_daily - platform

**Response:**
```jsonc
{
  "account_id": "...",
  "data": [
    {
      "date": "2026-06-23",
      "spend_daily_sar": 2526.78,
      "ledger_sar": 2526.78,
      "platform_sar": 2814.00,
      "drift_internal_sar": 0.0,
      "drift_platform_sar": 287.22,
      "status": "ledger_match_platform_drift"
    }
  ],
  "drift_summary": {
    "internal_drift_count": 0,
    "platform_drift_count": 12,
    "max_platform_drift_pct": 14.55
  }
}
```

> **القاعدة الأهم:** `drift_internal_sar > 0` = **BUG في النظام** (spend_daily و GL مختلفان رغم أن GL يأتي من daily). أي drift_internal > 0 يُعتبر alert فوري.

---

## 11. Diagnostics

### `GET /ads-v2/diagnostics/sync-health`
**Role:** any  
**Response:**
```jsonc
{
  "scheduler_status": "running",
  "last_heartbeat_at": "...",
  "next_run_eta_seconds": 240,
  "accounts_overview": [
    { "id": "...", "name": "...", "sync_status": "active",
      "last_synced_date": "2026-06-23", "lag_days": 1 }
  ]
}
```

### `GET /ads-v2/diagnostics/review-queue-aging`
**Role:** any → كم review pending وكم يوم بقي.

### `GET /ads-v2/diagnostics/fx-coverage`
**Role:** any → تواريخ بدون fx_rate سارٍ.

### `GET /ads-v2/diagnostics/v1-v2-comparison`
**Role:** any  
**Query:** `from=&to=`  
**Description:** يقارن إجماليات V1 (`meta_ads_daily + snapchat_account_daily`) مع V2 (`ads_v2_spend_daily`) لنفس الفترة. **حيوي خلال Phase A.**

---

## 12. Webhooks (للمستقبل، Phase 5)

سيُضاف لاحقاً endpoint لاستقبال webhooks من Meta/Snap عند `ad_account.spend_threshold_reached` لتحديث `ads_v2_spend_raw` فورياً بدون انتظار cron.

---

## 13. Versioning & Stability

- كل الـ endpoints تحت `/api/ads-v2/` تُعتبر **v2.0** stable.
- أي breaking change مستقبلاً → `/api/ads-v3/`.
- إضافة حقول جديدة في response = non-breaking ✓
- حذف/تغيير حقل = breaking ✗

---

## 14. الاختبار

كل endpoint له على الأقل:
1. **Happy path test** (200 OK)
2. **Auth required test** (401)
3. **Role denied test** (403)
4. **Validation error test** (400)
5. **Idempotency test** (re-execute لا يُسبب double-effect)

---

**✍️ التعديلات المطلوبة:** هل أحتاج إضافة/تعديل/حذف أي endpoint؟ يفضَّل قبل البناء.
