# 📚 Ads V2 — فهرس الوثائق + Single Source of Truth Layer

> **الحالة:** مسودة لاعتماد التاجر  
> **التاريخ:** 2026-06-24

---

## 1. فهرس الوثائق

| # | الوثيقة | المسار | عدد الأقسام |
|---|---|---|---|
| 00 | **هذا الملف** (Index + Data Layer Contract) | `ADS_V2_00_INDEX.md` | 4 |
| 01 | ERD Diagram (Mermaid) | `ADS_V2_01_ERD.md` | 5 |
| 02 | Collections Schema تفصيلي | `ADS_V2_02_SCHEMA.md` | 14 |
| 03 | API Contract (كل endpoint) | `ADS_V2_03_API_CONTRACT.md` | 14 |
| 04 | Review Workflow + State Machine | `ADS_V2_04_REVIEW_WORKFLOW.md` | 8 |
| 05 | Posting Workflow + Reversal | `ADS_V2_05_POSTING_WORKFLOW.md` | 9 |
| 06 | Reconciliation Workflow (الطبقة الجديدة) | `ADS_V2_06_RECONCILIATION_WORKFLOW.md` | 14 |

**الوثيقة الأصلية (Master Design):** `ADS_V2_DESIGN.md` (10 أقسام، تبقى للمرجع).

---

## 2. الـ Single Source of Truth Architecture

### 2.1 المبدأ الصارم

> **قاعدة:** كل قراءة لأرقام Ads V2 في أي مكان من التطبيق **يجب** أن تمر عبر دالة في `ads_v2_data_layer.py`. أي قراءة مباشرة من Mongo = bug يجب إصلاحه فوراً.

### 2.2 لماذا؟

تكرار مشكلة V1: ثلاث تقارير تقرأ من ثلاث مصادر (forensic vs cron vs UI) → ثلاثة أرقام مختلفة لنفس اليوم. **Ads V2 يحظر هذا تماماً.**

### 2.3 الـ Layer Structure

```
┌─────────────────────────────────────────────────────────────┐
│                     UI / Reports / Diagnostics              │
│         (No direct Mongo access for ads_v2_*)               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  ads_v2_data_layer.py                       │
│  • get_spend_by_day(user_id, date_from, date_to, ...)      │
│  • get_spend_by_account(user_id, account_id, ...)          │
│  • get_spend_by_provider(user_id, ...)                     │
│  • get_account_debt(user_id, account_id, as_of_date)       │
│  • get_reconciliation_drift(user_id, account_id, ...)      │
│  • get_review_queue(user_id, filters)                      │
│  • get_posting_history(user_id, filters)                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  MongoDB Collections                        │
│  ads_v2_spend_daily | ads_v2_spend_review |                 │
│  general_ledger | ads_v2_reconciliation | …                 │
└─────────────────────────────────────────────────────────────┘
```

### 2.4 آلية الـ Enforcement (3 طبقات حماية)

#### الطبقة 1: Folder boundary
- **مسموح فقط** للملفات داخل `/app/backend/ads_v2/data_layer/` بأن تستخدم `db.ads_v2_*` أو `db.general_ledger` (للقراءات المتعلقة بـ V2).
- كل routes / reports / endpoints تستدعي الدوال من data_layer **فقط**.

#### الطبقة 2: Lint Rule (custom)
ملف `tests/lint/test_ads_v2_data_layer_boundary.py`:

```python
"""Static check: no file outside ads_v2/data_layer/ may
reference db.ads_v2_* or write to ads_v2 entry_types."""

import re, glob

VIOLATIONS = []
ALLOWED_PREFIX = "/app/backend/ads_v2/data_layer/"

PATTERNS = [
    r"db\.ads_v2_\w+",
    r"\.ads_v2_\w+\.(find|insert|update|aggregate|delete)",
    r"entry_type.*ads_v2_",
]

for filepath in glob.glob("/app/backend/**/*.py", recursive=True):
    if filepath.startswith(ALLOWED_PREFIX):
        continue
    if "/tests/" in filepath:
        continue
    content = open(filepath).read()
    for pattern in PATTERNS:
        if re.search(pattern, content):
            VIOLATIONS.append(f"{filepath}: matches {pattern}")

def test_no_direct_v2_access():
    assert not VIOLATIONS, "\n".join(VIOLATIONS)
```

هذا الفحص يعمل في CI ويُفشل أي PR يخرق القاعدة.

#### الطبقة 3: Runtime Audit (optional)
كل دالة في data_layer تكتب metric:
- `ads_v2_data_layer_calls_total{function="get_spend_by_day"}`
- تظهر في Diagnostics dashboard

أي route يستعلم `db.ads_v2_*` مباشرة → لا metric → يكتشف بسهولة.

### 2.5 توقيع الدوال (Contract)

كل دالة في data_layer:

1. ✅ تأخذ `user_id` كأول parameter دائماً
2. ✅ ترجع `(data, meta)` tuple حيث meta = `{source_collection, computed_at, ssot_layer}`
3. ✅ تستخدم indexes المُحدَّدة في Schema doc
4. ✅ لا تكتب في DB إطلاقاً (read-only layer)
5. ✅ تُسجَّل في log عند الاستدعاء (للتدقيق)

### 2.6 مثال على دالة data_layer

```python
# /app/backend/ads_v2/data_layer/spend.py
from typing import Optional
from datetime import datetime, timezone
from utils.types import DataLayerResult

async def get_spend_by_day(
    db,
    user_id: str,
    date_from: str,
    date_to: str,
    provider: Optional[str] = None,
    account_id: Optional[str] = None,
) -> DataLayerResult:
    """
    Returns daily spend aggregation strictly from ads_v2_spend_daily.
    
    Single Source of Truth: ads_v2_spend_daily
    """
    q = {
        "user_id": user_id,
        "date": {"$gte": date_from, "$lte": date_to},
    }
    if provider:
        q["provider"] = provider
    if account_id:
        q["account_id"] = account_id

    pipeline = [
        {"$match": q},
        {"$group": {
            "_id": "$date",
            "spend_sar": {"$sum": "$spend_sar"},
            "bank_fee_sar": {"$sum": "$bank_fee_sar"},
            "gross_sar": {"$sum": "$gross_sar"},
            "accounts_count": {"$addToSet": "$account_id"},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = await db.ads_v2_spend_daily.aggregate(pipeline).to_list(None)

    data = [
        {
            "date": r["_id"],
            "spend_sar": r["spend_sar"],
            "bank_fee_sar": r["bank_fee_sar"],
            "gross_sar": r["gross_sar"],
            "accounts_count": len(r["accounts_count"]),
        }
        for r in rows
    ]
    return DataLayerResult(
        data=data,
        meta={
            "source_collection": "ads_v2_spend_daily",
            "ssot_layer": "ads_v2_data_layer.get_spend_by_day",
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "filters": q,
        }
    )
```

### 2.7 قائمة الدوال في data_layer (Full)

| الدالة | الـ Source | الاستخدام |
|---|---|---|
| `get_spend_by_day` | `ads_v2_spend_daily` | تقرير حسب اليوم |
| `get_spend_by_account` | `ads_v2_spend_daily` | تقرير حسب حساب |
| `get_spend_by_provider` | `ads_v2_spend_daily` | تقرير حسب مزود |
| `get_spend_by_campaign` | `ads_v2_spend_raw` | تقرير حسب حملة (تفصيلي) |
| `get_account_debt` | `general_ledger` (entry_type ads_v2_*) | رصيد الدين |
| `get_account_balance_history` | `general_ledger` | تطور الرصيد عبر الزمن |
| `get_reconciliation_drift` | `ads_v2_reconciliation` | فروقات vs platform |
| `get_internal_vs_ledger_drift` | `ads_v2_spend_daily` + `general_ledger` | drift بين SSOT و GL |
| `get_review_queue` | `ads_v2_spend_review` | قائمة المراجعة |
| `get_posting_history` | `ads_v2_ledger_postings` | سجل الترحيلات |
| `get_reversal_history` | `ads_v2_reversals` | سجل العكس |
| `get_sync_runs` | `ads_v2_sync_runs` | logs المزامنة |
| `get_fx_lookup` | `ads_v2_currency_settings` | سعر صرف لتاريخ |
| `get_v1_vs_v2_comparison` | V1 collections + `ads_v2_spend_daily` | المقارنة في Phase A |

### 2.8 الأرقام المضمونة

| السؤال | المصدر الوحيد |
|---|---|
| كم صرفنا اليوم؟ | `get_spend_by_day(user_id, today, today)` |
| كم نُدِين لمتجر إعلانات؟ | `get_account_debt(user_id, account_id, today)` |
| ما الفرق مع المنصة؟ | `get_reconciliation_drift(...)` |
| هل هناك drift داخلي؟ | `get_internal_vs_ledger_drift(...)` ← يجب أن يكون 0 |

**لا توجد طريقة أخرى للحصول على هذه الأرقام.** أي تقرير، أي بطاقة Dashboard، أي API → كلها تمر عبر هذه الدوال.

### 2.9 Contract Test (إلزامي)

`tests/test_ads_v2_contract.py`:

```python
@pytest.mark.asyncio
async def test_sum_by_day_equals_sum_by_account():
    """
    Sum of spend_sar grouped by day = Sum grouped by account = Sum grouped by provider.
    All three must match for the same period.
    """
    sum_day = await data_layer.get_spend_by_day(db, uid, "2026-06-01", "2026-06-30")
    sum_acc = await data_layer.get_spend_by_account(db, uid, "2026-06-01", "2026-06-30")
    sum_prov = await data_layer.get_spend_by_provider(db, uid, "2026-06-01", "2026-06-30")
    
    total_day = sum(r["gross_sar"] for r in sum_day.data)
    total_acc = sum(r["gross_sar"] for r in sum_acc.data)
    total_prov = sum(r["gross_sar"] for r in sum_prov.data)
    
    assert abs(total_day - total_acc) < 0.01
    assert abs(total_acc - total_prov) < 0.01

@pytest.mark.asyncio
async def test_internal_ledger_drift_is_zero():
    """
    For every (account, date) with an approved review,
    spend_daily.gross_sar must equal sum of GL legs.
    """
    drift = await data_layer.get_internal_vs_ledger_drift(
        db, uid, "2026-06-01", "2026-06-30"
    )
    for row in drift.data:
        assert abs(row["drift_sar"]) < 0.01, f"Drift on {row['date']}: {row}"

@pytest.mark.asyncio
async def test_data_layer_boundary_enforced():
    """Static check: no file outside data_layer/ accesses ads_v2_* collections."""
    # ... (the lint test above)
```

هذه الاختبارات تعمل في CI **قبل كل merge**.

---

## 3. التطبيق العملي (Phase 0 Action Items)

عند البدء، أول 5 ملفات تُنشأ:

1. `/app/backend/ads_v2/__init__.py`
2. `/app/backend/ads_v2/models.py` — BaseDocument extensions + enums
3. `/app/backend/ads_v2/data_layer/__init__.py`
4. `/app/backend/ads_v2/data_layer/spend.py` — أول دالة (`get_spend_by_day`)
5. `/app/backend/tests/test_ads_v2_data_layer_boundary.py` — lint enforcement

ثم يبدأ الـ CRUD للـ accounts و OAuth.

---

## 4. الفرق بين V1 و V2 (للوضوح)

| البُعد | V1 (الحالي) | V2 (الجديد) |
|---|---|---|
| Source of Truth للأرقام | متعدد (cron, forensic, ledger) | `ads_v2_spend_daily` فقط |
| Reconciliation | غير موجود | طبقة إلزامية قبل المراجعة |
| Review قبل GL | غير موجود (auto post) | إلزامي |
| OAuth per-org | غير مدعوم (token واحد) | مدعوم (حل مشكلة الرياض) |
| FX | fallback ثابت 3.75 | جدول رسمي + held إن غاب |
| Bank fee | غير محسوب في GL | leg مستقل بـ % و flat |
| Audit Trail | متفرق | شامل (5 جداول audit) |
| Reversal | unsupported / dangerous | first-class citizen |
| التقارير | كل تقرير له منطق خاص | كلها عبر data_layer واحد |
| Multi-FX | USD only | كل العملات |
| Idempotency | جزئي | كامل عبر unique keys |
| Multi-provider | Meta + Snap | Meta + Snap + TikTok (+ Google لاحقاً) |

---

## 5. خريطة الانتقال (Mapping V1 → V2)

| V1 Concept | V2 Equivalent | ملاحظة |
|---|---|---|
| `counterparties` (kind=ad_account) | `ads_v2_accounts` | V2 جدول مستقل |
| `meta_ads_daily` | `ads_v2_spend_raw` + `ads_v2_spend_daily` | V2 يفصل raw عن aggregate |
| `snapchat_account_daily` | `ads_v2_spend_raw` + `ads_v2_spend_daily` | نفس الشيء |
| `snapchat_ad_accounts` | جزء من `ads_v2_accounts` + `ads_v2_oauth_credentials` | V2 يفصل OAuth |
| `ad_account_ledger` | `general_ledger` (V2 entries) | V2 يستخدم GL مباشرة |
| Cron `_ad_spend_window_post_loop` | Cron V2 (يمر عبر review) | V2 لا يكتب مباشرة لـ GL |
| `ads_currency_settings` | `ads_v2_currency_settings` | جدول جديد بنفس الفكرة |

> **القاعدة:** V1 ممنوع تماماً من القراءة في V2. data_layer لا تستعلم V1 (إلا في endpoint مخصص `get_v1_vs_v2_comparison`).

---

**الوثيقة التالية:** كلها مكتوبة. الآن دور التاجر — هل يعتمد التصميم؟
