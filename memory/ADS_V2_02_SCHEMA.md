# 🗄️ Ads V2 — Collections Schema (تفصيلي)

> **الحالة:** مسودة لاعتماد التاجر  
> **التاريخ:** 2026-06-24  
> **المعيار:** كل collection يستخدم `BaseDocument` (PyObjectId) — قابل للـ JSON serialization من أول لحظة.

---

## 0. تنسيق عام

- كل المجموعات تبدأ بـ `ads_v2_`
- التواريخ الكاملة: `datetime.now(timezone.utc)` تُخزَّن بـ ISO 8601 string
- التواريخ النهارية: `YYYY-MM-DD` string
- المعرفات: UUID4 string (`uuid.uuid4().hex` بحجم 32)
- العملات: ISO 4217 string (SAR, USD, AED, EUR, QAR)
- المبالغ المالية: `float` (سيُعاد النظر لاحقاً لـ `Decimal128` إذا تطلب الأمر)

---

## 1. `ads_v2_feature_flags`

تفعيل/تعطيل V2 لكل مستخدم بشكل مستقل.

```python
{
    "_id": ObjectId(),
    "id": "uuid-hex",
    "user_id": "uuid-hex",                     # FK → users.id
    "v2_enabled": False,                       # default False — V2 لا يعمل لأحد بدون تفعيل صريح
    "v2_reports_replace_v1": False,            # Phase B: التقارير في Dashboard تقرأ V2
    "v1_writes_disabled": False,               # Phase C: V1 cron يتوقف
    "enabled_at": None,                        # ISO datetime
    "enabled_by": None,                        # user_id (admin يفعّل لتاجر)
    "history": [                               # كل تغيير على flag يُسجَّل هنا
        {
            "field": "v2_enabled",
            "from": False, "to": True,
            "at": "2026-06-25T10:00:00Z",
            "by": "admin-uuid",
            "note": "Phase 0 enabled"
        }
    ],
    "created_at": "ISO",
    "updated_at": "ISO"
}
```

**Indexes:** `{user_id: 1}` unique.

---

## 2. `ads_v2_oauth_credentials`

Token مفصول لكل organization.

```python
{
    "id": "uuid-hex",
    "user_id": "uuid-hex",
    "provider": "snapchat",                    # meta | snapchat | tiktok | google_ads
    "organization_id": "36e8955e-...",         # Snap: org_id | Meta: business_id | TikTok: bc_id | Google: customer_id
    "organization_name": "Establishment AMASI AL-KHALIJ",
    "access_token_enc": "base64-aes256-...",   # encrypted with APP_SECRET_KEY
    "refresh_token_enc": "base64-aes256-...",
    "token_expires_at": "2026-08-01T00:00:00Z",
    "scopes_granted": ["snapchat-marketing-api"],
    "status": "active",                        # active | expired | revoked | reauth_required
    "last_refreshed_at": "ISO",
    "connected_at": "ISO",
    "disconnected_at": None,
    "created_at": "ISO",
    "updated_at": "ISO"
}
```

**Indexes:**
- `{user_id: 1, provider: 1, organization_id: 1}` unique
- `{token_expires_at: 1}` (للـ refresh job)

**ملاحظات أمنية:**
- التشفير: AES-256-GCM بمفتاح من env `ADS_V2_ENCRYPTION_KEY`
- المفتاح يدور تلقائياً كل 90 يوم
- لا access_token plain text في logs ولا API responses

---

## 3. `ads_v2_accounts`

كتالوج الحسابات الإعلانية.

```python
{
    "id": "uuid-hex",
    "user_id": "uuid-hex",
    "oauth_credential_id": "uuid-hex",          # FK | null إذا manual
    "provider": "snapchat",                     # meta | snapchat | tiktok | google_ads
    "external_account_id": "cf8ea7c9-36e2-...", # ID لدى المزود
    "display_name": "متجر أماسي سعودي",
    "currency_native": "SAR",                   # USD | SAR | AED | EUR | QAR ...
    "timezone": "Asia/Riyadh",                  # IANA tz
    "organization_id": "36e8955e-...",          # redundant copy للسرعة
    "organization_name": "Establishment AMASI AL-KHALIJ",
    
    # === Bank Fee Configuration ===
    "bank_fee_enabled": False,
    "bank_fee_rate_pct": 0.0,                   # نسبة من spend (مثال 0.0285 = 2.85%)
    "bank_fee_flat_amount": 0.0,                # مبلغ ثابت يومي بـ SAR
    "bank_fee_method": "none",                  # none | pct_only | flat_only | pct_plus_flat
    "bank_fee_notes": "",                       # مثال: "Visa cross-border markup"
    
    # === Sync State ===
    "sync_status": "active",                    # active | paused | error | unauthorized | manual
    "sync_error_message": None,
    "sync_error_count": 0,
    "last_sync_started_at": None,
    "last_sync_finished_at": None,
    "last_synced_date": None,                   # آخر تاريخ موجود في spend_daily
    
    # === Metadata ===
    "created_at": "ISO",
    "updated_at": "ISO",
    "disabled_at": None,
    "soft_deleted": False
}
```

**Indexes:**
- `{user_id: 1, provider: 1, external_account_id: 1}` unique (partial: `soft_deleted=False`)
- `{user_id: 1, sync_status: 1}`
- `{oauth_credential_id: 1}`

**Validation:**
- `bank_fee_method='pct_only'` → `bank_fee_rate_pct > 0` و `bank_fee_flat_amount == 0`
- `bank_fee_method='flat_only'` → `bank_fee_flat_amount > 0` و `bank_fee_rate_pct == 0`
- `bank_fee_method='pct_plus_flat'` → كلاهما > 0
- `bank_fee_method='none'` → كلاهما = 0 و `bank_fee_enabled = False`

---

## 4. `ads_v2_currency_settings`

أسعار الصرف الرسمية (لا fallback في الكود).

```python
{
    "id": "uuid-hex",
    "user_id": "uuid-hex",
    "from_currency": "USD",                     # ISO 4217
    "to_currency": "SAR",                       # دائماً SAR (لأنه عملة الحسابات)
    "rate": 3.752,                              # 1 USD = 3.752 SAR
    "effective_from": "2026-06-01",             # السعر يسري من هذا اليوم
    "effective_to": None,                       # None = سارٍ حتى أمر آخر
    "source": "manual",                         # manual | sama_api | xe_api | provider_reported
    "source_reference": "SAMA reference rate June 2026",
    "is_active": True,                          # soft-disable بدون حذف
    "created_at": "ISO",
    "created_by": "user-uuid",
    "updated_at": "ISO",
    "notes": ""
}
```

**Indexes:**
- `{user_id: 1, from_currency: 1, to_currency: 1, effective_from: -1}`
- `{user_id: 1, is_active: 1}`

**قاعدة Lookup:**
```
rate = first WHERE (from, to, user_id) AND effective_from <= date 
                  AND (effective_to IS NULL OR effective_to >= date)
                  AND is_active = True
       ORDER BY effective_from DESC
```

**Multi-FX:**
- لا قيد على `from_currency` → أي عملة مدعومة (USD, AED, QAR, EUR, …)
- `to_currency` دائماً SAR في v2.0 (لاحقاً قد يُضاف USD destination)

---

## 5. `ads_v2_sync_runs`

سجل كل تنفيذ مزامنة.

```python
{
    "id": "uuid-hex",
    "user_id": "uuid-hex",
    "account_id": "uuid-hex",                   # null إذا run شامل لكل الحسابات
    "provider": "meta",
    "trigger": "cron",                          # cron | manual | webhook | backfill
    "scope": {
        "date_from": "2026-06-23",
        "date_to": "2026-06-23",
        "force_refresh": False
    },
    "started_at": "ISO",
    "finished_at": None,
    "status": "running",                        # running | success | partial | failed
    "stats": {
        "api_calls_made": 0,
        "rows_inserted_raw": 0,
        "rows_updated_daily": 0,
        "accounts_succeeded": 0,
        "accounts_failed": 0
    },
    "error_summary": None,
    "error_per_account": {                      # {account_id: error_dict}
        # "uuid-1": {"code": "rate_limit", "message": "..."}
    },
    "created_at": "ISO"
}
```

**Indexes:**
- `{user_id: 1, started_at: -1}`
- `{account_id: 1, started_at: -1}`
- `{status: 1, started_at: -1}`

---

## 6. `ads_v2_spend_raw`

Append-only: كل قراءة من API تُحفظ كاملة.

```python
{
    "id": "uuid-hex",
    "user_id": "uuid-hex",
    "account_id": "uuid-hex",
    "source_run_id": "uuid-hex",                # FK → sync_runs.id
    "provider": "meta",
    "date": "2026-06-23",                       # في timezone الحساب
    "granularity": "campaign_day",              # day | campaign_day | hour
    "dimension_keys": {                         # null حسب granularity
        "campaign_id": "120240133140550420",
        "campaign_name": "كيس حق اماسي",
        "adset_id": None,
        "ad_id": None
    },
    "spend_native": 54.81,
    "currency_native": "SAR",
    "impressions": 2834,
    "clicks": 57,
    "purchases": 0,
    "purchase_value_native": 0.0,
    "provider_payload_hash": "sha256-hex",      # SHA-256 من JSON الـ raw
    "provider_payload_excerpt": {               # أول 50 حقل للتدقيق
        "ctr": 2.0113, "cpm": 19.3402, "cpc": 0.9616
    },
    "fetched_at": "ISO"
}
```

**Indexes:**
- `{user_id: 1, account_id: 1, date: 1, source_run_id: 1, "dimension_keys.campaign_id": 1}` unique
- `{user_id: 1, account_id: 1, date: 1, fetched_at: -1}` (latest read)
- `{source_run_id: 1}`

**سياسة Retention:** الحفاظ على كل runs لأقل تقدير سنة. لاحقاً سياسة archival.

---

## 7. `ads_v2_spend_daily` ⭐ (SSOT للأرقام)

التجميع اليومي — كل التقارير تقرأ من هنا.

```python
{
    "id": "uuid-hex",
    "user_id": "uuid-hex",
    "account_id": "uuid-hex",
    "provider": "snapchat",
    "date": "2026-06-23",
    
    # === Native amounts (مجموع من spend_raw آخر run) ===
    "spend_native": 654.78,
    "currency_native": "USD",
    "impressions": 250000,
    "clicks": 4500,
    "purchases": 12,
    "purchase_value_native": 1240.50,
    
    # === FX Conversion (مرتبط بـ ads_v2_currency_settings) ===
    "fx_rate_to_sar": 3.752,                    # null لو غاب السعر
    "fx_rate_source": "manual",
    "fx_rate_as_of": "2026-06-23",
    "fx_setting_id": "uuid-hex",                # FK → currency_settings.id
    
    # === Computed SAR amounts ===
    "spend_sar": 2456.74,                       # spend_native * fx_rate (null لو فُقد FX)
    "bank_fee_sar": 70.04,                      # محسوب من account.bank_fee_*
    "bank_fee_breakdown": {                     # شفافية الحساب
        "method": "pct_plus_flat",
        "rate_pct": 0.0285,
        "rate_pct_amount": 70.04,
        "flat_amount": 0.0,
        "total": 70.04
    },
    "gross_sar": 2526.78,                       # spend_sar + bank_fee_sar
    
    # === Quality flags ===
    "confidence": "final",                      # provisional (< 3 days) | final
    "data_health": "ok",                        # ok | missing_fx | partial_api | stale
    "sources_count": 1,                         # كم run ساهم في القراءة الأخيرة
    
    # === Audit ===
    "last_source_run_id": "uuid-hex",
    "last_recomputed_at": "ISO",
    "first_seen_at": "ISO",
    "updated_at": "ISO"
}
```

**Indexes:**
- `{user_id: 1, account_id: 1, date: 1}` unique
- `{user_id: 1, date: 1, confidence: 1}`
- `{user_id: 1, provider: 1, date: -1}`

**قواعد:**
- `confidence='final'` إذا `date < today - 3 days`
- `data_health='missing_fx'` إذا `currency_native != 'SAR'` و `fx_rate_to_sar IS NULL`
- لو الـ recompute يتغير: `last_recomputed_at` يُحدَّث

---

## 8. `ads_v2_reconciliation` ⭐ (الطبقة الجديدة)

مقارنة spend_daily بمصادر تحقق إضافية قبل المراجعة.

```python
{
    "id": "uuid-hex",
    "user_id": "uuid-hex",
    "account_id": "uuid-hex",
    "date": "2026-06-23",
    "provider": "snapchat",
    
    # === Internal SSOT ===
    "spend_daily_total_native": 654.78,
    "spend_daily_total_sar": 2456.74,
    
    # === External Cross-Checks ===
    "platform_reported_native": 750.00,         # إعادة استدعاء API الآن
    "platform_reported_sar": 2814.00,
    "platform_fetched_at": "ISO",
    
    # === Drift Analysis ===
    "drift_native": 95.22,                      # platform - daily
    "drift_pct": 14.55,                         # %
    "drift_direction": "platform_higher",       # platform_higher | platform_lower | match
    
    # === Anomaly Detection ===
    "anomaly_flags": [                          # كل العلامات المكتشفة
        "late_reporting",
        "drift_above_5pct"
    ],
    "late_reporting_detected": True,
    "spend_changed_after_close": False,         # هل تغير الرقم بعد إغلاق اليوم؟
    "post_close_delta_pct": 0.0,
    
    # === Day-over-day comparison ===
    "yesterday_spend_sar": 2100.30,
    "wow_change_pct": 17.0,                     # week-over-week
    "wow_anomaly": False,                       # > 100% غير طبيعي
    
    # === Recon Decision ===
    "recon_status": "passed_with_warnings",     # passed | passed_with_warnings | needs_review | failed
    "recon_blocked_review": False,              # True إذا فشل وأوقف التحويل لـ review
    "blocking_reasons": [],
    
    # === History (Append-only) ===
    "checks_history": [                         # كل recompute يضيف entry هنا
        {
            "at": "2026-06-24T02:00:00Z",
            "spend_daily_sar": 2456.74,
            "platform_sar": 2814.00,
            "drift_pct": 14.55,
            "trigger": "initial_recon"
        },
        {
            "at": "2026-06-25T02:00:00Z",
            "spend_daily_sar": 2780.50,         # تغير الرقم بعد re-sync
            "platform_sar": 2814.00,
            "drift_pct": 1.20,
            "trigger": "post_close_recon",
            "post_close_delta": 323.76
        }
    ],
    
    "created_at": "ISO",
    "updated_at": "ISO"
}
```

**Indexes:**
- `{user_id: 1, account_id: 1, date: 1}` unique
- `{user_id: 1, recon_status: 1}`
- `{user_id: 1, "anomaly_flags": 1, date: -1}`

**أنواع الـ anomaly_flags:**
- `late_reporting` — منصة تُبلغ بأرقام أعلى بعد > 24 ساعة من إغلاق اليوم
- `drift_above_5pct` — اختلاف > 5% بين internal و platform
- `drift_above_15pct` — اختلاف > 15% (يحجب التحويل لـ review)
- `wow_spike_above_100pct` — قفزة غير طبيعية أسبوع-بأسبوع
- `wow_drop_above_80pct` — هبوط غير طبيعي
- `post_close_change` — الرقم تغير بعد إغلاق اليوم في spend_daily
- `missing_fx_no_recon` — لا يمكن المقارنة بـ SAR لغياب FX

---

## 9. `ads_v2_spend_review`

طبقة الموافقة الإلزامية.

```python
{
    "id": "uuid-hex",
    "user_id": "uuid-hex",
    "account_id": "uuid-hex",
    "date": "2026-06-23",
    "provider": "snapchat",
    "reconciliation_id": "uuid-hex",            # FK
    
    # === Snapshot من spend_daily (مُجمَّد لحظة الدخول للمراجعة) ===
    "spend_native_snapshot": 654.78,
    "currency_native": "USD",
    "fx_rate_snapshot": 3.752,
    "spend_sar_snapshot": 2456.74,
    "bank_fee_sar_snapshot": 70.04,
    "gross_sar_snapshot": 2526.78,
    
    # === Review state machine ===
    "review_status": "pending",                 
    # pending | approved | rejected | reopened | held_needs_fx
    # held_anomaly | held_unauthorized | held_drift
    
    "review_flags": [                           # من reconciliation + من المراجعة نفسها
        "drift_above_5pct"
    ],
    
    # === Decision ===
    "decided_at": None,                         # ISO عند approve/reject
    "decided_by": None,                         # user_id
    "decision_note": None,
    
    # === Reopen tracking ===
    "previously_rejected_at": None,             # عند reopen
    "reopen_count": 0,
    
    # === Posting linkage ===
    "posted_txn_group_id": None,                # FK → general_ledger
    "posted_at": None,
    "posting_id": None,                         # FK → ads_v2_ledger_postings
    
    # === Idempotency ===
    "idempotency_key": "ads_v2:USER:ACCT:2026-06-23",
    
    "created_at": "ISO",
    "updated_at": "ISO"
}
```

**Indexes:**
- `{user_id: 1, account_id: 1, date: 1}` unique
- `{user_id: 1, review_status: 1, created_at: -1}`
- `{idempotency_key: 1}` unique

---

## 10. `ads_v2_review_history`

كل تغير حالة على review يُسجَّل (append-only).

```python
{
    "id": "uuid-hex",
    "review_id": "uuid-hex",
    "user_id": "uuid-hex",
    "account_id": "uuid-hex",
    "date": "2026-06-23",
    "action": "approve",                        # create | approve | reject | reopen | edit_fx | edit_bank_fee
    "from_status": "pending",
    "to_status": "approved",
    "actor_user_id": "uuid-hex",
    "actor_email": "owner@example.com",
    "note": "Verified against Ads Manager",
    "context": {                                # data snapshot قبل التغيير (للـ edit actions)
        "old_fx_rate": 3.75,
        "new_fx_rate": 3.752
    },
    "ip_address": "1.2.3.4",
    "at": "ISO"
}
```

**Indexes:**
- `{review_id: 1, at: -1}`
- `{user_id: 1, at: -1}`
- `{action: 1, at: -1}`

---

## 11. `ads_v2_ledger_postings`

Audit log كل ترحيل لـ GL.

```python
{
    "id": "uuid-hex",
    "review_id": "uuid-hex",                    # FK unique
    "txn_group_id": "uuid-hex",                 # FK → general_ledger.txn_group_id
    "user_id": "uuid-hex",
    "account_id": "uuid-hex",
    "date": "2026-06-23",
    "provider": "snapchat",
    
    "amounts": {
        "spend_sar": 2456.74,
        "bank_fee_sar": 70.04,
        "gross_sar": 2526.78
    },
    
    "legs_summary": [                           # snapshot من الـ legs المُرحَّلة
        {"entry_type": "ads_v2_expense", "side": "debit",
         "amount": 2456.74, "path": "expense:advertising:snapchat"},
        {"entry_type": "ads_v2_balance_credit", "side": "credit",
         "amount": 2456.74, "path": "counterparty:ad_account:uuid"},
        {"entry_type": "ads_v2_bank_fee", "side": "debit",
         "amount": 70.04, "path": "expense:bank_fee:advertising"},
        {"entry_type": "ads_v2_balance_credit", "side": "credit",
         "amount": 70.04, "path": "counterparty:ad_account:uuid"}
    ],
    
    "posted_at": "ISO",
    "posted_by": "user-uuid",
    "posted_via": "manual_approve",             # manual_approve | bulk_approve | re_approve_after_reopen
    
    "reversed": False,                          # شامل (للسرعة بدون JOIN)
    "current_reversal_id": None,                # FK → ads_v2_reversals.id
    
    "created_at": "ISO"
}
```

**Indexes:**
- `{review_id: 1}` unique
- `{txn_group_id: 1}` unique
- `{user_id: 1, posted_at: -1}`
- `{user_id: 1, account_id: 1, date: 1}`

---

## 12. `ads_v2_reversals`

سجل كل عكس لقيد (append-only، يدعم متعدد).

```python
{
    "id": "uuid-hex",
    "original_posting_id": "uuid-hex",          # FK → ads_v2_ledger_postings
    "user_id": "uuid-hex",
    "reversal_txn_group_id": "uuid-hex",        # FK → general_ledger
    "reason": "Refund processed by provider",
    "reversed_at": "ISO",
    "reversed_by": "user-uuid",
    "reversal_type": "full",                    # full | partial (للمستقبل)
    "amount_reversed_sar": 2526.78,
    "legs_summary": [...],                      # mirror legs
    "follow_up_action": "reopen_review",        # reopen_review | mark_as_correction | none
    "follow_up_completed": False,
    "created_at": "ISO"
}
```

**Indexes:**
- `{original_posting_id: 1, reversed_at: -1}`
- `{user_id: 1, reversed_at: -1}`

---

## 13. كيف يبدو سطر `general_ledger` من V2

> **V1 و V2 يكتبان في نفس `general_ledger`** لكن بـ `entry_type` مختلفة لتمييز المصدر.

```python
# مثال: قيد Meta 510.27 SAR موافَق عليه
[
    {
        "txn_group_id": "xxx",
        "entry_type": "ads_v2_expense",        # ✓ مميز V2
        "user_id": "...",
        "entity_type": "expense",
        "entity_id": "advertising:meta",
        "side": "debit",
        "amount": 510.27,
        "posted_at": "...",
        "status": "posted",
        "metadata": {
            "source": "ads_v2",                # ✓ علامة المصدر
            "ads_v2_review_id": "...",
            "ads_v2_posting_id": "...",
            "account_id": "...",
            "spend_date": "2026-06-23",
            "provider": "meta",
            "currency_native": "SAR",
            "spend_native": 510.27,
            "fx_rate": 1.0,
            "idempotency_key": "ads_v2:USER:ACCT:2026-06-23:spend"
        }
    },
    {
        "txn_group_id": "xxx",
        "entry_type": "ads_v2_balance_credit",
        "entity_type": "counterparty",
        "entity_id": "ad_account:...",
        "side": "credit",
        "amount": 510.27,
        ...
    }
    # + 2 legs إضافيتين لو bank_fee_enabled
]
```

---

## 14. ملخص الجداول

| # | Collection | الدور | الحجم المتوقع/سنة |
|---|---|---|---|
| 1 | `ads_v2_feature_flags` | تفعيل تدريجي | 1 صف/مستخدم |
| 2 | `ads_v2_oauth_credentials` | tokens مفصولة | 1-10 صفوف/مستخدم |
| 3 | `ads_v2_accounts` | كتالوج الحسابات | 2-20 صف/مستخدم |
| 4 | `ads_v2_currency_settings` | FX رسمي | 10-100 صف/مستخدم |
| 5 | `ads_v2_sync_runs` | logs runs | 24×365 = ~9k/مستخدم |
| 6 | `ads_v2_spend_raw` | append-only API | 5k-50k/مستخدم |
| 7 | `ads_v2_spend_daily` | **SSOT** للأرقام | 365 × عدد الحسابات |
| 8 | `ads_v2_reconciliation` | فحص التحقق | 365 × عدد الحسابات |
| 9 | `ads_v2_spend_review` | المراجعة | 365 × عدد الحسابات |
| 10 | `ads_v2_review_history` | audit | 3-10× عدد reviews |
| 11 | `ads_v2_ledger_postings` | audit ترحيل | 1× عدد reviews approved |
| 12 | `ads_v2_reversals` | audit عكس | 0-5% من postings |

---

**✍️ التعديلات المطلوبة:** أضف أي حقل/index/قيد تريده. أي تعديل قبل البرمجة أسهل بمراحل من بعدها.
