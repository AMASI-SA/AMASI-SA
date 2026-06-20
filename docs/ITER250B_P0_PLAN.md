# Iter-250b P0 — خطة Forward Fix للحسابات الإعلانية (Dry-Run)

> **حالة الخطة:** مسوّدة للمراجعة. لا تطبيق فعلي حتى اعتماد كل المراحل.
> **النطاق:** توحيد كتابة /ad-accounts على `general_ledger` فقط
> (نمط Iter-203).
> **المبدأ:** كل سطر تجميد/تعطيل في الكود معكوس بـ feature flag —
> لا حذف لـ collections ولا modifications على بيانات تاريخية.

---

## 1. الوضع الحالي المثبت من Production

| المقياس | القيمة |
|---|---|
| إجمالي مواقع الكتابة | **28** |
| Endpoints متميزة | **14** |
| HIGH risk | **14** ⚠️ |
| كتابات DUPLICATE | **2** |
| كتابات LEGACY | **7** |
| كتابات CACHE | **2** |
| كتابات SSOT (آمنة، GL) | **5** |
| كتابات RECOVERY | **5** (idempotent) |
| كتابات ONE_SHOT (migration) | **3** (already executed) |
| كتابات MASTER (counterparty doc) | **4** (config/CRUD) |

🔴 **رصيد ad_accounts في GL** = `-158,584.54` ريال (من post-deploy-check)

---

## 2. خريطة الـ Forward Fix — ماذا نُجمّد، وماذا نُبقي

### 🟢 يُبقى كما هو (SSOT — لا تغيير)

| الموقع | السبب |
|---|---|
| `general_ledger insert_many` في `/topup` (سطر 1078) | كتابة Iter-203 الصحيحة |
| `general_ledger reverse+repost` في PUT `/topup/{id}` (سطر 1164) | Iter-218 |
| `general_ledger insert` في `/spend` (سطر 2587) | الكتابة الوحيدة الصحيحة |
| `general_ledger insert_many` في `/adjustments` (سطر 783) | تسوية يدوية |
| `general_ledger + ad_account_ledger` في snapchat sync | يحتاج تفكيك (ad_account_ledger ⇒ disable) |

### 🔴 يُجمَّد خلف Feature Flag (Forward Fix Phase 1)

| الموقع | الملف:السطر | الإجراء |
|---|---|---|
| `account_transactions insert_one` في POST `/topup` | `ad_account_routes.py:526` | لف بـ `if not FLAG_FREEZE_AD_LEGACY:` |
| `liabilities update_one` في POST `/topup` | `:1044` | نفس الـ flag |
| `counterparties update_one` (cache) في POST `/topup` | `:1057` | **يبقى مفعّل** (cache فقط) |
| `ad_account_ledger insert_one` في POST `/topup` | `:397` | نفس الـ flag |
| `account_transactions delete+insert` في PUT `/topup/{id}` | `:1228` | نفس الـ flag |
| `ad_account_ledger update_one` في PUT `/topup/{id}` | `:1283` | نفس الـ flag |
| `counterparties update_one (cache)` في POST `/spend` | `:1376` | **يبقى مفعّل** (cache) |
| `liabilities update_one` في POST `/spend` | `:1391` | نفس الـ flag |
| `liabilities insert_one` في POST `/spend` | `:1422` | نفس الـ flag |
| `liabilities delete+insert` في PUT `/opening` | `:2506` | نفس الـ flag |
| `ad_account_ledger insert_one` في PUT `/opening` | `:2553` | نفس الـ flag |
| `ad_account_ledger insert_many` في snapchat_routes.py | `snapchat_routes.py:1091` | نفس الـ flag |

🛡️ **الـ counterparties cache (current/debt_balance) يبقى مكتوباً** — لأنه cache لا حقيقة، ويُغذّى من GL trigger في `Iter-160`. تجميد `liabilities` و `ad_account_ledger` لا يُؤثّر عليه.

### 🟡 يُترك معطّلاً افتراضياً (Migration / Cleanup)

| الموقع | الإجراء |
|---|---|
| POST `/ad-accounts/migration/apply` | إضافة `enabled=False` يدوي |
| POST `/ad-accounts/migration/cleanup-duplicates` | نفس |
| POST `/ad-accounts/diagnostics/duplicate-topups/cleanup` | نفس |

---

## 3. آلية الـ Feature Flag

```python
# backend/ad_account_flags.py
import os
FREEZE_AD_LEGACY_WRITES = os.environ.get(
    "FREEZE_AD_LEGACY_WRITES", "0") == "1"
```

في كل موقع legacy:
```python
if not FREEZE_AD_LEGACY_WRITES:
    await db.liabilities.update_one(...)   # legacy
# دائماً نكتب الـ GL:
await db.general_ledger.insert_many([...])  # SSOT
```

**التفعيل:** ضع `FREEZE_AD_LEGACY_WRITES=1` في `/app/backend/.env`
ثم `sudo supervisorctl restart backend`. لا تعديل على الكود
وقت التفعيل.

**التعطيل (Backout):** أعد القيمة إلى `0` (أو احذفها)، أعد تشغيل
الـ backend. كل المواقع تعود تكتب كما كانت.

---

## 4. مراحل التطبيق (لا قفزات)

### Phase 0 — Dry-Run (✅ مُنفَّذ في هذه التكرار)
- `GET /api/audit/ad-account-dryrun-diff` يُرجع لكل حساب:
  - الـ deltas بين counterparty cache و GL
  - عدد سطور كل legacy collection
  - `freeze_safety` لكل حساب:
    - `SAFE_TO_FREEZE_LEGACY` — لا فرق، آمن للتجميد
    - `FREEZE_OK_BUT_LIABILITIES_STALE` — آمن، لكن liabilities سيحتاج تنظيفاً لاحقاً
    - `NEEDS_RECONCILIATION_FIRST` — يجب تشغيل recompute أولاً
- **خَرَج فقط — لا كتابة.**

### Phase 1 — Forward Fix (ينتظر اعتمادك)
1. أضف `ad_account_flags.py` (سطرين).
2. لف كل سطر legacy في `if not FREEZE_AD_LEGACY_WRITES:` (12 موقع).
3. ابقَ على القيمة `0` افتراضياً — **لا يتغيّر سلوك Production**.
4. انشر. هذه خطوة آمنة 100% (الكود لا يفعل شيئاً جديداً، فقط يصبح
   مُهيّأ للتجميد).

### Phase 2 — Apply (بعد Phase 1 بأسبوع كاختبار استقرار)
1. شغّل dry-run مرة أخرى. تأكد كل الحسابات `SAFE_TO_FREEZE_LEGACY`.
2. أضف `FREEZE_AD_LEGACY_WRITES=1` في `.env` على Production.
3. أعد تشغيل backend.
4. راقب لمدة 24-48 ساعة:
   - `iter250a-post-deploy-check.B_areas_snapshot.ad_accounts.rows_created_last_24h` يبقى موجباً (GL يستقبل كتابات جديدة)
   - dry-run بدون تغيّر في الـ deltas (مما يثبت أن legacy لم يعد يستقبل شيئاً)

### Phase 3 — Cleanup (شهر بعد Phase 2 — اختياري)
- حذف الـ `if not FREEZE...` blocks من الكود (تنظيف).
- حذف `ad_account_ledger` collection (بعد backup).
- حذف liabilities المتعلقة بـ ad_accounts (بعد backup).
- **لا تنفّذ هذه المرحلة إلا بعد قرار صريح.**

---

## 5. Apply-Token Gating (لمرحلة Phase 2)

نُضيف Endpoint وسيط: `POST /api/audit/ad-account-freeze-legacy/apply`
يستلم:
```json
{
  "apply_token": "<sha256 of: account_count|total_delta|date>",
  "explicit_confirm": true
}
```
- الـ `apply_token` يُحسب من نتائج dry-run الحالية (يفسد إذا تغيّر
  أي شيء بين Dry-Run والـ Apply).
- لا يُنفّذ كتابة DB، فقط يضع علامة في `system_flags` collection
  تُقرأ في startup → تفعّل الـ flag.

(تطبيق هذه الـ endpoint مؤجل لمرحلة Phase 2 بعد اعتمادك.)

---

## 6. Backout Plan

في أي مرحلة (Phase 1 أو 2)، إذا ظهر فرق غير مفهوم:

| السيناريو | Backout الفوري |
|---|---|
| Phase 1: كتابات GL تتأخر | لا تأثير على البيانات — الكود يكتب كل المسارات |
| Phase 2: ظهور فروقات | احذف `FREEZE_AD_LEGACY_WRITES` من `.env` → restart → جميع المسارات تستأنف الكتابة فوراً |
| Phase 2: فقد بيانات | غير ممكن — لا يحدث حذف. legacy ما زالت تحتوي كل ما قبل التجميد |
| Phase 3 (إن نُفّذت): فقد بيانات | تتطلب استرجاع من backup قبل Phase 3 |

---

## 7. Acceptance Criteria (شروط الاعتماد قبل Phase 1)

1. ✅ Dry-Run يُرجع `overall_recommendation = "safe_to_apply_forward_fix"`
   لكل الحسابات النشطة.
2. ✅ `iter250a-post-deploy-check` يبقى `set_match=true` و
   `C_legacy_recent_writes.count_last_24h = 0`.
3. ✅ تقرير forensic لكل حساب يُظهر `ssot_health=HEALTHY` أو
   تصحيح يدوي معتمد للحسابات التي لا تظهر HEALTHY.
4. ✅ موافقة صريحة من المالك على Phase 1.

---

## 8. ما لن يتم تنفيذه في هذه التكرار

- ❌ تعديل أي سطر في `ad_account_routes.py`.
- ❌ إضافة feature flag بعد (يأتي في Phase 1 بعد اعتمادك).
- ❌ كتابة على DB.
- ❌ حذف أو نقل أي collection.
- ❌ تنفيذ Apply-Token endpoint.

---

*المرجع: تقرير forensic Iter-250b P0 + post-deploy-check
Iter-250a على Production بتاريخ 2026-06-20.*
