# Iter-250b P1.2 — تقرير Forensic لـ `/payment-settlements`

> **Read-Only.** لا تعديل على أي ملف أو endpoint أو DB.
> الهدف: تحديد SSOT الفعلي وكشف أي ازدواج كتابة.

---

## 1. خريطة الأنظمة (نظامان مختلفان لا واحد)

عند البحث في الكود اكتُشف أن المنصّة فيها **مساران منفصلان**
كل واحد منهم يسمّى "settlement" — لكن لكل واحد كود وملفات DB
مختلفة تماماً.

| البُعد | A — Payment Settlements Import | B — Manual Settlements |
|---|---|---|
| Frontend route | `/payment-settlements` ✅ نشط | `/settlements` 🛑 مُخفى (SAFE_TO_HIDE) |
| Frontend file | `PaymentSettlements.jsx` (530 سطر) | `Settlements.jsx` (مخفي بـ LegacyRedirect) |
| Backend module | `settlements_import/` (5 ملفات) | `settlements_routes.py` (سطر 364) |
| URL prefix | `/api/payment-settlements/*` | `/api/settlements/*` |
| الغرض | استيراد ملفات Salla/Tamara/Tabby Excel | إدخال تسويات يدوية |

---

## 2. Routes/Endpoints لكل نظام

### A — `/payment-settlements` (الباقي والنشط)

| Method | Endpoint | الوظيفة |
|---|---|---|
| POST | `/api/payment-settlements/upload` | رفع ملف Excel/CSV |
| GET | `/api/payment-settlements` | قائمة الملفات المرفوعة |
| GET | `/api/payment-settlements/{file_id}` | تفاصيل ملف |
| DELETE | `/api/payment-settlements/{file_id}` | حذف ملف + rollback |
| PATCH | `/api/payment-settlements/{file_id}/settlement-date` | تعديل التاريخ |
| GET | `/api/payment-settlements/_analytics/coverage` | تغطية الطلبات |
| GET | `/api/payment-settlements/_analytics/salla` | تحليلات Salla |
| GET | `/api/payment-settlements/_overview/unified` | عرض موحّد |
| POST | `/api/payment-settlements/_overview/export-excel` | تصدير Excel |

### B — `/settlements` (legacy، مُخفى)

| Method | Endpoint | الوظيفة |
|---|---|---|
| GET | `/api/settlements/providers` | قائمة المزوّدين |
| GET | `/api/settlements` | تسويات يدوية |
| POST | `/api/settlements` | إنشاء تسوية يدوية |
| PUT | `/api/settlements/{id}` | تعديل |
| DELETE | `/api/settlements/{id}` | حذف |
| GET | `/api/settlements/summary` | ملخّص |

---

## 3. مواقع الكتابة (تفصيلية)

### A — `settlements_import/` (المسار النشط)

| الملف:السطر | Collection | Op | Risk |
|---|---|---|---|
| `service.py:117` | `settlement_files` | insert_one | LOW (audit) |
| `service.py:132` | `settlement_entries` | insert_many | LOW (canonical) |
| `service.py:199` | `unified_orders` | update_one | MEDIUM (cross-ref) |
| `service.py:368` | `unified_orders` | update_many | MEDIUM (rollback) |
| `service.py:389` | `settlement_entries` | delete_many | MEDIUM (delete-cascade) |
| `service.py:390` | `settlement_files` | delete_one | LOW |
| `routes.py:60` | `settlement_files` | update_one | LOW (status update) |
| `routes.py:116` | `settlement_files` | update_one | LOW (date patch) |

**إجمالي:** 8 مواقع كتابة، 3 collections → `settlement_files`, `settlement_entries`, `unified_orders`.

### B — `settlements_routes.py` (المسار القديم)

| الملف:السطر | Collection | Op | Risk |
|---|---|---|---|
| `:216` | `payment_adjustments` | update_many | MEDIUM |
| `:281` | `payment_adjustments` | insert_one | MEDIUM |
| `:321` | `unified_orders` | update_one | MEDIUM |
| `:451` | `payment_adjustments` | insert_one | MEDIUM |
| `:478` | `payment_adjustments` | update_one | MEDIUM |
| `:488` | `payment_adjustments` | delete_one | MEDIUM |

**إجمالي:** 6 مواقع كتابة، 2 collections → `payment_adjustments`, `unified_orders`.

---

## 4. كشف الازدواجية

### ✅ لا يوجد ازدواج كتابة فعلي

السببان مختلفان تماماً في الـ collections:

| Collection | يُكتب من A؟ | يُكتب من B؟ |
|---|---|---|
| `settlement_files` | ✅ نعم | ❌ لا |
| `settlement_entries` | ✅ نعم | ❌ لا |
| `payment_adjustments` | ❌ لا | ✅ نعم |
| `unified_orders` | ✅ نعم (cross-ref) | ✅ نعم (cross-ref) |

**نقطة التقاطع الوحيدة:** `unified_orders`. كلا النظامين يحدّث
حقول مختلفة فيه:
- النظام A: يحدّث `settlement_match_status`, `settlement_file_id`
- النظام B: يحدّث `payment_adjustments_ref`

⚠️ **لكن:** ولا واحد منهما يكتب في `general_ledger`! وهذا يعني
أن التسويات **لا تخلق قيوداً محاسبية** في الـ ledger. كل ما تفعله
هي **mark على الطلبات** بأنها مُسوّاة.

### الحركة المحاسبية الفعلية تأتي من:
- `bnpl/settlement_bridge.py` (Tamara/Tabby) → general_ledger ✅
- `bnpl_settlement_banktx_routes.py` (Iter-248 backfill) → account_transactions ⚠️
- `settlements_import` يقرأ الملف فقط ويربطه بالطلبات، **لا يكتب قيوداً**.

---

## 5. SSOT الفعلي للتسويات

| البُعد | SSOT الفعلي |
|---|---|
| الملفات المرفوعة من المزوّدين | `settlement_files` (canonical) |
| السطور المُحلَّلة من الملفات | `settlement_entries` (canonical) |
| التسويات اليدوية (إن وُجدت) | `payment_adjustments` (legacy) |
| القيد المحاسبي للتسوية | **`general_ledger`** عبر `settlement_bridge.py` |
| ربط الطلبات بالتسوية | `unified_orders.settlement_*` fields |
| **الـ UI الفعلي للعرض** | `/settlements-overview` (SSOT الموحَّد) |

**خلاصة:** هناك **3 طبقات منفصلة** لا واحدة:
1. **استيراد البيانات الخام** → `payment-settlements`
2. **التسويات اليدوية** → `settlements` (legacy، مُخفى)
3. **القيد المحاسبي** → `general_ledger` عبر BNPL bridge

---

## 6. الموقف من المراجعة (لكل نظام)

### A — `/payment-settlements` 🟢

- **التصنيف الحالي:** MERGE → /settlements-overview
- **التصنيف الموصى به بعد المراجعة:** **KEEP** (تغيير)
- **السبب:** له وظيفة فريدة (استيراد + parsing) لا تغطّيها
  `/settlements-overview`. هي مزوّد بيانات للأخير، ليست نسخة منه.
- **المخاطر:** 🟢 LOW
- **الإجراء المقترح:** تحديث الجرد فقط، لا حذف ولا merge.

### B — `/settlements` 🟠

- **التصنيف الحالي:** DEPRECATE (مُخفى بـ LegacyRedirect)
- **يبقى كما هو:** الإخفاء صحيح، لا يحتاج تعديلاً.
- **المخاطر:** 🟡 MEDIUM (لو فُعِّل مجدداً، يكتب في `payment_adjustments` مباشرة)

---

## 7. مصفوفة المخاطر النهائية

| المخاطرة | المستوى | الوصف |
|---|---|---|
| ازدواج كتابة | 🟢 LOW | A و B لا يكتبان في نفس الـ collections (عدا unified_orders بحقول مختلفة) |
| تأثير على الرصيد | 🟢 LOW | لا واحد منهما يكتب في general_ledger |
| فقدان بيانات | 🟢 LOW | كل عمليات الحذف لها rollback (settlement_files.delete → unified_orders rollback) |
| `payment_adjustments` فارغ | 🟡 MEDIUM | يحتاج فحص live: هل التاريخ يحتوي سطوراً؟ |
| `settlement_entries` ضخم | 🟡 MEDIUM | كل ملف Excel ينشئ سطوراً كثيرة — يحتاج indexing (موجود) |
| Apply إلى UI القديم | 🔴 HIGH (مفترض) | لو رُفع الإخفاء بالخطأ، المستخدم يستطيع إنشاء payment_adjustments بدون قيد ledger |

---

## 8. التوصية النهائية

🟢 **`/payment-settlements` يبقى KEEP** — لا merge، لا حذف، لا تغيير.
- وظيفته فريدة (استيراد ملفات Excel من المزوّدين).
- صفر تأثير على الرصيد.
- بنيته القانونية سليمة (settlement_files + settlement_entries).

📝 **تحديث الجرد المطلوب:**
- `classification`: `MERGE` → **`KEEP`**
- `replacement`: `/settlements-overview` → `null`
- `hide_safety`: `NEEDS_REVIEW` → **`KEEP_VISIBLE`**
- `reason`: تحديث ليعكس أنه data importer مستقل.

🛑 **لا يُطلب أي عمل تنفيذي** — فقط تحديث الجرد عند موافقتك.

---

## 9. الصفحة التالية في NEEDS_REVIEW

`/receivables` — مرشّحة للمراجعة التالية كما طلبت.

---

*تم توليد هذا التقرير على Preview بتاريخ 2026-06-20 — Read-Only.
لا تعديل على Production. لا تعديل على DB.*
