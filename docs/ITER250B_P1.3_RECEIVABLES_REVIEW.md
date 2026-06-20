# Iter-250b P1.3 — تقرير Forensic لـ `/receivables`

> **Read-Only.** لا تعديل على ملفات أو endpoints أو DB.

---

## 1. الـ Surface

- **Frontend:** `pages/Receivables.jsx` (375 سطر — يحتوي state + forms + API)
- **Backend module:** `liabilities_routes.py` (1,966+ سطر، مشترك مع موظفين/موردين/ad accounts)
- **Domain key:** `kind = "receivable"` داخل collection `liabilities`

---

## 2. Routes/Endpoints المستخدمة من الصفحة

| Method | Endpoint | الوظيفة |
|---|---|---|
| GET | `/api/liabilities?kind=receivable&limit=500` | قائمة الذمم |
| GET | `/api/accounts?type=bank&limit=200` | حسابات استلام التحصيل |
| POST | `/api/liabilities` (kind=receivable) | إنشاء ذمّة جديدة |
| POST | `/api/liabilities/{id}/collect` | تسجيل تحصيل |
| DELETE | `/api/liabilities/{id}` | حذف ذمّة |

---

## 3. مواقع الكتابة (8 مواقع، 3 collections)

| الملف:السطر | Collection | Op | الغرض | Risk |
|---|---|---|---|---|
| `liabilities_routes.py:276` | `account_transactions` | `insert_one` | كتابة سطر دفع/تحصيل | 🔴 HIGH |
| `liabilities_routes.py:239` | `account_transactions` | `update_one` | تحديث الـ AT row | 🟡 MEDIUM |
| `liabilities_routes.py:1966` | `account_transactions` | `delete_one` | حذف عند rollback | 🟡 MEDIUM |
| `liabilities_routes.py:1218` | `liabilities` | `insert_one` | إنشاء ذمّة (kind=receivable) | 🟢 LOW (canonical) |
| `liabilities_routes.py:1856` | `liabilities` | `update_one` | تحديث رصيد الذمّة | 🔴 HIGH |
| `liabilities_routes.py:1925` | `liabilities` | `update_one` | تحديث بعد التحصيل | 🔴 HIGH |
| `liabilities_routes.py:279` | `general_ledger` | mirror (Iter-240) | mirror SSOT للقيد | 🟢 LOW (SSOT) |
| `liabilities_routes.py:1015` | `liabilities` | `update_one` (داخل bulk ops) | عمليات جماعية | 🟡 MEDIUM |

**ملاحظة:** السطر 279 (mirror إلى GL) يعمل **مع كل** insert/update لـ
`account_transactions` المتعلق بذمّة → كل تحصيل ينعكس على GL تلقائياً.

---

## 4. مواقع القراءة + علاقة بـ externals

| الموقع | المصدر |
|---|---|
| Page `/receivables` (الجدول) | `liabilities.find({kind: "receivable"})` |
| Page `/externals-ledger` | `general_ledger.find(entity_type="external_person")` ✅ SSOT |
| Page `/financial-position-ledger` | `general_ledger` |

⚠️ **التضارب المحتمل:**
- `/receivables` يعرض من `liabilities` (legacy)
- `/externals-ledger` يعرض من `general_ledger` (SSOT)
- Iter-240 mirror يحافظ على التطابق، **لكن** أي ذمّة قديمة (ما قبل Iter-240) قد لا تظهر في `general_ledger` رغم وجودها في `liabilities`.

---

## 5. كشف الازدواج

| Collection | يُكتب من /receivables؟ | يُكتب من /externals-ledger؟ |
|---|---|---|
| `liabilities` | ✅ نعم | ❌ لا |
| `account_transactions` | ✅ نعم | ❌ لا |
| `general_ledger` | ✅ نعم (mirror) | ❌ لا (read-only) |
| `counterparties` | ❌ لا (الذمم لا تستخدم counterparties) | ❌ لا |

✅ **لا ازدواج كتابة فعلي** — `/externals-ledger` هي read-only من GL.

⚠️ **ازدواج عرض:** نفس البيانات قد تظهر في صفحتين بمنطق مختلف:
- `/receivables`: ذمم مفتوحة فقط (`status != "paid"`)
- `/externals-ledger`: كل القيود التاريخية بـ entity_type=external_person

---

## 6. SSOT الفعلي للذمم

**3 طبقات منفصلة:**

```
1. الذمّة كـ entity                →  liabilities (kind=receivable)
2. حركة التحصيل (debit/credit)    →  account_transactions
3. الانعكاس المحاسبي (SSOT)        →  general_ledger (entity_type=external_person)
                                       via Iter-240 mirror
```

**SSOT للرصيد المحاسبي:** `general_ledger` (Iter-240+)
**SSOT لحالة الذمّة:** `liabilities.status` (open / partial / paid)
**عرض المستخدم النهائي:** `/externals-ledger` (يبني من GL، SSOT-grade)

---

## 7. مصفوفة المخاطر

| المخاطرة | المستوى | الوصف |
|---|---|---|
| ازدواج كتابة | 🟡 MEDIUM | 3 collections للعملية الواحدة، لكن mirror Iter-240 يضمن التطابق |
| تأثير على الرصيد | 🔴 HIGH | كل تحصيل يكتب في AT + يحدّث liabilities + GL mirror |
| فقدان مزامنة AT↔GL | 🟡 MEDIUM | لو فشل mirror → liabilities.balance ≠ GL.net |
| ذمم ما قبل Iter-240 | 🟡 MEDIUM | قد توجد في liabilities بدون GL counterpart |
| تأثير على `/externals-ledger` | 🔴 HIGH | إن تعطّل mirror، الصفحة تعرض أرقاماً ناقصة |
| Apply مكرر للتحصيل | 🟡 MEDIUM | يمكن تحصيل أكثر من المتبقي (يحتاج فحص idempotency) |

---

## 8. التوصية النهائية

🟠 **`/receivables` → `MERGE` (إبقاء التصنيف الحالي)**

**الأسباب:**
1. **يكتب في 3 مصادر متوازية** — نمط مطابق لمشكلة `/ad-accounts`.
2. **يعتمد على `liabilities` كـ master** بينما SSOT الفعلي للرصيد
   هو `general_ledger`. هذا يفتح الباب لتضارب لو فشل الـ mirror.
3. **`/externals-ledger` متاحة كبديل ledger-based جاهز** — نفس
   البيانات بدون legacy.
4. **`/new-transaction` تدعم `type=receivable_collect`** (محتمل بعد
   فحص) كنقطة إدخال موحّدة بدلاً من POST /liabilities المباشر.

**لكن مثل `/ad-accounts`:**
- لا يُنفَّذ أي merge قبل تشخيص مماثل للحسابات الإعلانية:
  - Forensic لكل ذمّة (هل GL يطابق liabilities؟)
  - Dry-Run للـ recompute
  - Decision matrix قبل أي تنفيذ

---

## 9. مقارنة مع `/ad-accounts` (للسياق)

| البُعد | `/ad-accounts` | `/receivables` |
|---|---|---|
| Collections | 5 (counterparties + liabilities + ad_account_ledger + account_transactions + GL) | 3 (liabilities + AT + GL mirror) |
| مواقع الكتابة | 28 | 8 |
| HIGH risk | 14 | 4 |
| SSOT الفعلي | general_ledger (Iter-203) | general_ledger (Iter-240 mirror) |
| التوصية | MERGE (مجمَّدة على decision matrix) | MERGE (نفس النمط، أبسط) |
| الموقف الآن | تصفير مؤجَّل | لم يبدأ |

→ **`/receivables` أبسط بكثير من `/ad-accounts`** ويمكن معالجته
بأمان أكبر متى ما قرر المالك.

---

## 10. توصيات تشغيلية (لا تنفيذ)

| التوصية | المستوى |
|---|---|
| `KEEP` التصنيف الحالي = MERGE في الجرد | ✅ صحيح |
| لا تخفي الصفحة (لا تطبّق SAFE_TO_HIDE) — الأعمال اليومية تعتمد عليها | ✅ |
| بناء forensic لكل ذمّة (مثل recompute-dryrun للحسابات الإعلانية) | اختياري لاحقاً |
| فحص idempotency للتحصيل (هل يمنع التحصيل المزدوج؟) | اختياري |
| فحص ذمم ما قبل Iter-240 (هل لها GL counterpart؟) | اختياري |

---

## 11. الصفحة التالية المقترحة (NEEDS_REVIEW)

`/accounts/:id` — كان أصل bug Iter-249 (sub_account=balance vs main).
المراجعة هنا حساسة جداً وتحتاج خطة منفصلة.

---

*تم توليد هذا التقرير على Preview بتاريخ 2026-06-20 — قراءة فقط.
لا تعديل على Production أو DB.*
