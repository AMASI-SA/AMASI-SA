# Iter-250b · P1.5.e — Salla Settlement Regression Investigation (READ-ONLY)

**Date:** 2026-06-20
**Status:** ✅ Investigation complete · parser bug fix applied · awaiting deploy

---

## 🎯 السؤال الجوهري

> هل كان النظام سابقاً يطلب البنك المستقبل عند رفع تسوية سلة ويُنشئ التحويل تلقائياً؟
> ولماذا لا يفعل ذلك الآن؟

---

## 1) النتيجة الحاسمة من البحث في كامل الكود + Git History

### 🔴 لم يوجد قَطّ "Salla Auto-Bridge" في الكود

بحثت في:
- جميع ملفات backend الحالية: `grep -rn "salla.*bank_account|bank_account.*salla"` → **0 نتائج**
- Git history كامل: `git log --all -S "salla.*bank_account"` → **0 نتائج**
- جميع الـ commits المحذوفة + الـ files المحذوفة → **لا يوجد ملف Salla settlement مع bank selector تم حذفه**
- `git log -G "salla_settlement"` → التطورات كلها متعلقة بـ analytics فقط

### ✅ ما وجدته فعلاً (الـ Iter-156 السياق التاريخي)

من **PRD git history** (Feb 13 2026 · Iter-156):

> **Iter-156**: 🟧 Salla Settlements — Dedicated page (mirror of Tabby/Tamara).
> User request: "تسويات سله مثل صفحة تسوية تمارا وتابي".
> Built: Excel upload + per-method analytics.
> **NOT YET DONE (Phase 2 — surfaced in roadmap)**: Expected-vs-actual commission comparison.

لم يُذكر `bank_account_id` ولا `auto-bridge` في تخطيط Iter-156 — **النية الأصلية كانت analytics فقط، ليس تحويل أوتوماتيكي**.

---

## 2) السلوك الذي ربما يخلط عليك (التشابه مع BNPL)

> 🔍 **هناك endpoint موجود بـ bank_account_id لكنه لـ Tabby/Tamara فقط**

**الـ endpoint:** `POST /api/bnpl/settlements/register`
**الملف:** `backend/bnpl/settlements_routes.py:108-212`
**الصفحة:** `/bnpl-settlements/register`
**يقبل:**
```python
class BNPLSettlementRegisterIn(BaseModel):
    provider: str               # ← يجب أن يكون "tabby" أو "tamara" فقط
    bank_account_id: str        # ← البنك المستقبل
    transferred_amount: float
    commission: float
    commission_vat: float
    settlement_fee: float
    settlement_reference: str
```

**ماذا يفعل:**
1. ✅ يُنشئ `general_ledger` txn_group متوازن (`bnpl_settlement`)
2. ✅ يُنشئ `account_transactions` row من نوع `settlement` على البنك
3. ✅ يستدعي `_recompute_balance` لتحديث رصيد البنك
4. ✅ Idempotent بـ `(provider, settlement_reference)`

**يرفض سلة بصراحة:**
```python
PROVIDERS = ("tabby", "tamara")   # ← في settlements_service.py:118

if payload.provider.lower() not in PROVIDERS:
    raise HTTPException(400, f"unknown provider {payload.provider}")
```

> 🎯 **هذا هو السلوك الذي تتذكره** — لكنه كان دائماً مقتصراً على **Tabby/Tamara**. سلة لم تشملها أبداً.

---

## 3) لماذا الذاكرة قد تخلط بين الاثنين؟

| الصفحة | URL | يطلب bank؟ | يُنشئ ledger؟ |
|---|---|---|---|
| **تسجيل BNPL** | `/bnpl-settlements/register` | ✅ نعم | ✅ نعم |
| **تسويات سلة** | `/salla-settlements` | ❌ **لا** | ❌ **لا** |
| **فواتير وتسويات بوابات الدفع** | `/payment-settlements` | ❌ لا | ❌ لا |

الصفحات الثلاث في القائمة الجانبية متجاورة (سطر 58–67 في Sidebar.jsx). من السهل اعتقاد أن جميعها تفعل ما تفعله BNPL — لكن **فقط BNPL register** فيها الـ bank selector + auto-bridge.

---

## 4) كيف يصل المال من سلة إلى البنك في النظام الحالي؟

### المسار الحالي (مُجزَّأ — مصدر ضياع المعلومات)

```
1. سلة تُولِّد ملف Excel
        ↓
2. /salla-settlements → رفع الملف
        ↓
3. settlement_files + settlement_entries + unified_orders (tagging فقط)
        ↓
4. ❌ لا account_transaction · لا ledger · لا تغيير في رصيد سلة
        ↓
5. التاجر يجب أن يفتح يدوياً /transfers
        ↓
6. يختار: من سلة → إلى البنك · يدخل المبلغ يدوياً
        ↓
7. ✅ /transfers/_create_transfer() ينشئ:
   - account_transaction OUT من سلة
   - account_transaction IN في البنك
   - general_ledger balanced txn_group
   - _recompute_balance لكلا الحسابين
```

> الخطوات 5–7 **اختيارية ويدوية** — لا شيء يُذكِّر التاجر بها بعد الرفع.

---

## 5) إجابات صريحة على أسئلتك الثلاث

### ❓ هل السلوك القديم ما زال موجوداً لكنه غير مستدعى؟

**لا.** السلوك (auto-bridge مع bank selector لسلة) **لم يُكتب أبداً** في أي iteration.

### ❓ هل تم حذفه؟

**لا.** Git history (`git log --diff-filter=D --name-only`) لا يُظهر أي ملف يخص Salla + bank تم حذفه.

### ❓ هل تم تعطيله أثناء توحيد الصفحات؟

**لا.** الـ comments + iter tags في الكود توضح:
- Iter-156 (Feb 13 2026): بناء `/salla-settlements` كـ analytics-only
- Iter-220 (Mar 2026 تقديراً): بناء `/bnpl-settlements/register` مع bank selector — **مقتصراً على Tabby/Tamara بقصد**
- Iter-249 (Mar 2026): إصلاح اختفاء BNPL في bank UI
- Iter-250b (Jun 2026): التوحيد المعماري الذي نحن فيه

لا يوجد **أي iteration** أزال feature موجود لـ Salla.

---

## 6) الخلاصة النهائية

> 🔴 **التشخيص:** ليست regression. هذه فجوة معمارية أصلية لم تُسدّ.

| الفئة | الحالة |
|---|---|
| Regression حقيقي؟ | ❌ لا |
| Feature drift؟ | ❌ لا |
| Disabled flag مخفي؟ | ❌ لا |
| Code path مهجور؟ | ❌ لا |
| **التفسير الفعلي** | **سلة لم يُبنَ لها auto-bridge قَطّ. كانت دائماً عملية يدوية في خطوتين.** |

---

## 7) إصلاح Parser Bug — ✅ تم تطبيقه (في انتظار deploy)

### الملف: `backend/settlements_import/parsers/salla.py:215-217`

**قبل:**
```python
salla_purchases_total += abs(net or gross)
salla_purchases_count += 1
continue                       # ← total_net NOT updated
```

**بعد:**
```python
salla_purchases_total += abs(net or gross)
salla_purchases_count += 1
# Iter-250b · P1.5.e — Bug fix: include wallet_recharge in
# totals.net so that Σ(actual_net_amount) across ALL rows
# equals totals.net.
total_net += net
continue
```

### الأثر

| ملف | totals.net قبل | totals.net بعد |
|---|---|---|
| فاتورة #6381217 | 16,134.15 ❌ | **15,892.65** ✓ (يطابق Excel و actual_net entries) |

### حدود الإصلاح

⚠️ **لا يُحدِّث الملفات المُستوردة قبل الـ deploy.** الـ settlement_files القديمة ستبقى `totals.net = 16,134.15` (المخزَّن وقت الاستيراد). لكن:
- ✅ الـ `settlement_entries.actual_net_amount` صحيحة دائماً (لكل صف فعلياً)
- ✅ أي recompute UI من entries → سيُعطي الرقم الصحيح
- ✅ الملفات الجديدة بعد الـ deploy → `totals.net` صحيح

---

## 8) الإجراءات المتبقية

### ✅ ما تم
- [x] Regression investigation (هذا التقرير)
- [x] إصلاح parser bug (`salla.py:217`)
- [x] جاهز للـ deploy

### ⏳ ما يحتاج تصرّفك

#### الحل الفوري (لا deploy)
- [ ] افتح `/transfers`
- [ ] سجِّل تحويلاً يدوياً:
  - **من:** سلة (account_id: `e64f21e5-1dc4-4910-918b-13119d147394`)
  - **إلى:** البنك المستقبل
  - **المبلغ:** `15,892.65 ر.س`
  - **الوصف:** `تسوية سلة فاتورة #6381217`
- [ ] رصيد سلة سيصبح: `25,627.58 − 15,892.65 = 9,734.93 ر.س` ✓ (يطابق توقعك 9,734.98)

#### بعد الـ Deploy (الفواتير القادمة)
- [x] أي ملف سلة جديد يُرفع → `totals.net` صحيح من البداية
- [ ] يبقى التحويل اليدوي في `/transfers` ضرورياً (حتى نقرر بناء Auto-Bridge)

### ❌ ما **لم** يُنفَّذ (حسب طلبك)
- ❌ Auto-Bridge جديد
- ❌ Migration
- ❌ Recompute للملفات القديمة
- ❌ Cleanup

---

## 9) التوصية القادمة (للنقاش لاحقاً)

بناءً على هذا التقرير، يمكنك بعد الـ deploy التفكير في:

| الخيار | الوصف | تقدير |
|---|---|---|
| **OPT-A** | إبقاء النظام كما هو (manual workflow) | 🟢 لا عمل · لا مخاطرة |
| **OPT-B** | بناء "Auto-Bridge" لسلة مثل BNPL | 🟡 feature متوسط · يحتاج design |
| **OPT-C** | بناء "Pending Transfers Reminder" — بعد كل رفع ملف، يظهر إشعار للتاجر يطلب تسجيل التحويل اليدوي | 🟢 خفيف · UX win |

---

**نهاية تقرير Regression · Iter-250b · P1.5.e**
