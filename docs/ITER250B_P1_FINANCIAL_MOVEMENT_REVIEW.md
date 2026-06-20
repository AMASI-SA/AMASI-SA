# Iter-250b P1 — مراجعة `/financial-movement/new` Read-Only

> **حالة المراجعة:** اكتُشف أن الدمج **مكتمل فعلياً منذ Iter-246**.
> لا حاجة لعمل merge جديد — فقط استبدال البانر القديم بـ
> LegacyRedirect الموحّد (اختياري، آمن جداً).

---

## 1. الوضع الفعلي

### `/financial-movement/new`

* **الملف:** `frontend/src/pages/FinancialMovementNewPage.jsx`
* **الحجم:** **40 سطر فقط**
* **المحتوى:** بانر نصّي + رابطان (`Link to="/new-transaction"` و
  `Link to="/financial-movements"`).
* **API calls:** **صفر** (لا POST/PUT/DELETE، لا GET).
* **State:** **صفر** (لا `useState`، لا `useEffect`).
* **منطق إدخال:** **صفر**.
* **توقيع المطوّر السابق (تعليق أعلى الملف):**
  > Iter-246 — This page is kept ALIVE only to honour the
  > «forward-only / never break old routes» rule. All entry logic
  > has been merged into `/new-transaction` (UnifiedEntryScreen).

### `/new-transaction`

* **الملف:** `frontend/src/pages/UnifiedEntryScreen.jsx`
* **الحجم:** 2,377 سطر
* **يحتوي:** كل منطق الإدخال الموحّد لكل أنواع الحركات (مصاريف،
  فواتير مشتريات، أصول، تحويلات، إلخ).
* **API:** `POST /api/financial-movements` (وغيرها حسب النوع).

---

## 2. المقارنة (Diff Read-Only)

| البُعد | `/financial-movement/new` | `/new-transaction` |
|---|---|---|
| الحجم | 40 سطر | 2,377 سطر |
| منطق إدخال | ❌ | ✅ |
| API writes | ❌ | ✅ (POST `/api/financial-movements`) |
| State management | ❌ | ✅ (10+ states) |
| ازدواج وظيفة | لا — لا توجد وظيفة | — |
| تأثير على الرصيد | ❌ | ✅ |
| Iter منذ آخر تعديل | Iter-246 | مستمر |

**النتيجة:** لا يوجد ازدواج فعلي. الصفحة القديمة هي مجرد signpost
يوجّه المستخدم.

---

## 3. التصنيف المُعتمد (مُحدَّث في الجرد)

| السمة | القيمة |
|---|---|
| `classification` | `DELETE` (لأن الصفحة فارغة من الوظيفة) |
| `hide_safety` | `NEEDS_REDIRECT` (يُستبدل بـ LegacyRedirect) |
| `risk` | `LOW` (صفر API، صفر state) |
| `replacement` | `/new-transaction` |
| `affects_balance` | `false` (مُصحَّح — كان `true` خطأً في الجرد الأولي) |

---

## 4. الإجراء المقترَح (اختياري — آمن 100%)

استبدال البانر الحالي بـ `LegacyRedirect` الموحّد المُستخدَم في
الـ 10 صفحات الأخرى، للحصول على:

1. تجربة UX موحّدة عبر كل الصفحات القديمة.
2. سهولة tracking عبر `data-testid="legacy-redirect-page"`.
3. حذف ملف `FinancialMovementNewPage.jsx` (40 سطر فقط).

### التأثير المتوقَّع

| الحالة | التأثير |
|---|---|
| المستخدم يصل لـ `/financial-movement/new` | يرى البانر الموحّد (مطابق لـ /transfers) بدل البانر القديم |
| Backend | صفر تأثير |
| Database | صفر تأثير |
| Balances | صفر تأثير |
| Frontend bundle | يصغر بـ 40 سطر |

### Backout

ملف واحد + سطر واحد في `App.js`. revert فوري.

---

## 5. القرار

🟢 **آمن جداً.** لكن لا يُنفَّذ إلا بإذن صريح منك، حسب القاعدة
المُعتمَدة: "لا تعديل، فقط تقارير".

---

## 6. الصفحات التالية في NEEDS_REVIEW (للمراجعة لاحقاً)

| Route | الحجم النسبي | المخاطر المتوقَّعة |
|---|---|---|
| `/accounts/:id` | كبير | 🔴 HIGH (أصل bug Iter-249) |
| `/ad-accounts` | كبير | 🔴 HIGH (28 موقع كتابة — مؤجَّل) |
| `/purchase-invoices` | متوسط | 🟡 MEDIUM |
| `/payment-settlements` | متوسط | 🟡 MEDIUM |
| `/shipping/transfers` | متوسط | 🟡 MEDIUM |
| `/receivables` | متوسط | 🟡 MEDIUM |
| `/accounting/migration` | كبير | 🔴 HIGH (أداة one-shot) |

---

*تم توليد هذا التقرير على Preview بتاريخ 2026-06-20 — قراءة فقط،
لا تغييرات على Production.*
