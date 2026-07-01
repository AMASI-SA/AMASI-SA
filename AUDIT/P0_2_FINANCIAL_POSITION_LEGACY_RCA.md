# P0.2 — `/financial-position` Legacy vs `/financial-position-ledger` SSOT — RCA (Read-Only)

**Author:** E1 (Emergent Agent)  
**Scope:** الفرق بين صفحة `/financial-position` (Iter-93) و `/financial-position-ledger` (Iter-161 Phase 4)، وخطر عرض أرقام مختلفة عن SSOT.  
**Mode:** Read-only. لا كود تُعدَّل، لا route تُعطَّل، لا redirect جديد، لا Deploy.  
**Date:** 2026-07-01  
**Report file:** `/app/AUDIT/P0_2_FINANCIAL_POSITION_LEGACY_RCA.md`  
**Trigger:** Iter-001 صنّفت `/financial-position` كخطر P0 (احتمال أرقام مختلفة عن SSOT).

---

## 0. TL;DR — الخلاصة التنفيذية

**تصحيح مهم لتقرير Iter-001:** الخطر السابق كان **مبالغاً فيه**. الحقيقة:

- ✅ **Frontend routes**: `/financial-position` **مُعطَّلة فعلياً في الواجهة** — ملفوفة بـ `LegacyRedirect` منذ Iter-250a (نفس آلية `/settlements`). `FinancialPosition.jsx` **لا يُعرض أبداً** للمستخدم — النقر يُظهر بانر "🕰️ صفحة قديمة معطّلة" ويحوّل إلى `/financial-position-ledger`.
- ✅ **Backend data source**: كلتا الصفحتَين — القديمة `FinancialPosition.jsx` والحديثة `FinancialPositionLedger.jsx` — **تقرآن من نفس الـ SSOT endpoint** (`GET /api/accounting/financial-position` الذي يستدعي `compute_financial_position()` من `financial_position_ssot.py`). هذا التوحيد تم في **Iter-217**.
- 🟢 **لا خطر أرقام مختلفة** حتى لو استخدم أحدهم الرابط القديم مباشرة — لأن Legacy Redirect يمنع الوصول، ولو تجاوز أحدهم Legacy Redirect بطريقة ما فسيرى نفس أرقام SSOT.
- 🟡 **مخاطر ثانوية باقية**:
  - `FinancialPosition.jsx` (الميتة) لا تزال تحتفظ بـ 4 استدعاءات إضافية غير ضرورية (`/reconciliation/summary` + `/liabilities?status=unpaid` + `/liabilities?status=partial` + خطأ قديم يذكر `/shipping-accounts/ledger` لم أجده في الاستدعاء الفعلي).
  - `Dashboard.jsx` لم أفحص إن كان يشير إلى الرابط القديم (سنتحقق).

---

## 1. الملفات المعنية

### 1.1 Frontend Routes

```javascript
// /app/frontend/src/App.js:164
<Route path="/financial-position" element={
    <ProtectedRoute><Layout>
        <LegacyRedirect
            oldLabel="المركز المالي"
            replacement="/financial-position-ledger"
            replacementLabel="المركز المالي (Ledger)"
            reason="النسخة الجديدة مبنية على Ledger مباشرة."
        />
    </Layout></ProtectedRoute>
} />

// /app/frontend/src/App.js:227
<Route path="/financial-position-ledger" element={
    <ProtectedRoute><Layout>
        <FinancialPositionLedger />
    </Layout></ProtectedRoute>
} />
```

**النتيجة**: `/financial-position` route **حيّ ولكن لا يُشغّل `FinancialPosition.jsx`** — يُشغّل `LegacyRedirect` بانر.

### 1.2 Sidebar

```javascript
// /app/frontend/src/components/Sidebar.jsx:57
{ to: "/financial-position-ledger",
  label: "💰 المركز المالي (Ledger)",
  icon: PaperPlaneRight,
  testid: "nav-financial-position-ledger" },
```

**فقط النسخة الحديثة `/financial-position-ledger` تظهر في الـ Sidebar**. القديم مخفي.

### 1.3 Frontend Pages

| ملف | حالة عرض | استدعاءات API | حجم |
|---|---|---|---|
| `pages/FinancialPosition.jsx` | 🕰️ **ميتة** (لا route فعّال يعرضها) | 4 endpoints (`/accounting/financial-position` + `/reconciliation/summary` + `/liabilities?status=unpaid` + `/liabilities?status=partial`) | 544 سطر |
| `pages/FinancialPositionLedger.jsx` | 🟢 **حية** (canonical) | 1 endpoint (`/accounting/financial-position`) | 136 سطر |
| `components/LegacyRedirect.jsx` | 🟢 يعمل | (بانر — لا API) | 86 سطر |

### 1.4 Backend

```python
# /app/backend/universal_accounting_routes.py:2985
@router.get("/financial-position")
async def financial_position(user: dict = Depends(current_user)):
    """Iter-217 — SSOT financial position computed strictly from
    general_ledger... Returns a backward-compatible superset of the
    Phase-4 shape so both /financial-position (legacy page) and
    /financial-position-ledger (new page) can consume it."""
    from financial_position_ssot import compute_financial_position
    return await compute_financial_position(db, user["id"])
```

- **Endpoint واحد فقط** (`GET /api/accounting/financial-position`) للصفحتَين.
- **Reads exclusively from `general_ledger`** — لا `liabilities`، لا `account_transactions`، لا `payment_adjustments`، لا `accounts.current_balance` (إلا في حالة fallback موثّقة).
- **الـ SSOT function**: `financial_position_ssot.compute_financial_position()` — 422 سطر، Iter-217.

---

## 2. مصدر البيانات — تفصيل مقارَن

### 2.1 endpoint SSOT الموحّد

```
GET /api/accounting/financial-position
    ↓
compute_financial_position(db, user_id)   [financial_position_ssot.py:303]
    ↓
    1. _group_by_subaccount(db, user_id)
       → aggregates general_ledger by (entity_type, sub_account)
       → filters: status="posted", entry_type != "reversal",
                  metadata.legacy_orphan != true
    2. Bank/Platform balances via account_balance_ssot()
       → per-account walk (ledger + fallback for zero-activity)
    3. salary_breakdown_ssot() from ledger
    4. by_ad_provider_ssot() from ledger

Response shape:
{
    "assets": { banks, employee_advance, employee_custody,
                external_receivable, courier_cod_receivable,
                ad_account_prepaid, payment_platforms_remaining },
    "liabilities": { salaries_unpaid, supplier_payable,
                     courier_payable, external_payable,
                     ad_accounts_unpaid },
    "totals": { total_assets, total_liabilities, net_position },
    "salary_breakdown": {...},
    "by_ad_provider": {...},
    "banks_ledger_only": ...,
    "source": "general_ledger_v2",
    "iter": "iter217",
}
```

### 2.2 استدعاءات `FinancialPosition.jsx` (الصفحة الميتة)

| # | Endpoint | مصدر البيانات الفعلي | Legacy؟ |
|---|---|---|---|
| 1 | `GET /api/accounting/financial-position` | `general_ledger` (SSOT) | 🟢 حديث — منذ Iter-217 |
| 2 | `GET /api/reconciliation/summary` | ? (يحتاج فحص P0.3 مستقل) | ⚠️ قد يكون قديماً |
| 3 | `GET /api/liabilities?status=unpaid&limit=1` | `liabilities` collection — لعدّ الفواتير المفتوحة فقط (badge) | ⚠️ يقرأ من legacy collection |
| 4 | `GET /api/liabilities?status=partial&limit=1` | نفسه — بادج فقط | ⚠️ يقرأ من legacy collection |

**ملاحظة مهمة**: الصفحة الميتة (لا تُعرض) قدَّمت **Iter-217 upgrade** لاستدعاء الـ SSOT مباشرة، ثم **map** الاستجابة إلى الـ shape القديم للـ UI. أي: **حتى لو أُعيدت للحياة، ستعرض أرقام SSOT** — ليس أرقاماً مختلفة.

### 2.3 استدعاءات `FinancialPositionLedger.jsx` (الصفحة الحية)

| # | Endpoint | مصدر البيانات الفعلي | Legacy؟ |
|---|---|---|---|
| 1 | `GET /api/accounting/financial-position` | `general_ledger` (SSOT) | 🟢 SSOT فقط |

**كل الأرقام من مصدر واحد**. لا استدعاءات جانبية.

---

## 3. مقارنة الحقول المعروضة

**بما أن كلا الصفحتَين تستخدم نفس الاستجابة**، لن يوجد رقم مختلف على الإطلاق للحقول الأساسية:
- إجمالي الأصول
- إجمالي الالتزامات
- صافي المركز المالي
- BNPL / Ad Accounts / Suppliers / Couriers / Employees breakdown

الاختلافات فقط في **العرض** (UI):

| الحقل | `FinancialPosition.jsx` (الميتة) | `FinancialPositionLedger.jsx` (الحية) |
|---|---|---|
| Net Position Banner | ✅ ملوّن مع Icon | ✅ عرض بسيط |
| Breakdown Table per Ad Provider | ✅ | ✅ |
| Salary Breakdown drill-down | ✅ toggle مفصّل | ✅ ملخّص |
| Reconciliation totals | ✅ (من `/reconciliation/summary`) | ❌ ليس ضمن الصفحة |
| Open liabilities count badge | ✅ (من `/liabilities?status=...`) | ❌ ليس ضمن الصفحة |
| Number of employees active/suspended | ✅ | ✅ |

**النتيجة**: الصفحة القديمة عرضت **معلومات إضافية** (recon + open counts) لم تُنقل بعد إلى الحديثة. ليس هذا خطر أرقام مختلفة — بل ميّزة UI مفقودة.

---

## 4. Risk Assessment — التقييم النهائي (تصحيحاً لـ Iter-001)

| Dimension | مستوى | تصحيح |
|---|---|---|
| **Frontend user exposure** | 🟢 LOW | Route محمي بـ LegacyRedirect. |
| **Data drift risk (أرقام مختلفة)** | 🟢 **LOW** — ~~كان MEDIUM في Iter-001~~ | **الصفحتان تشربان من نفس SSOT منذ Iter-217**. |
| **Direct URL exposure** | 🟢 LOW | Legacy banner يمنع أي استخدام حتى مع رابط مشترك قديم. |
| **Dead code weight** | 🟡 MEDIUM | 544 سطر ميت + استدعاءات لـ 3 endpoints غير ضرورية. |
| **Legacy collection reads** | 🟡 MEDIUM | `liabilities?status=...` counts — لكن فقط badge counts، ليس أرقاماً محاسبية. |
| **Accounting compliance** | 🟢 LOW | لا خرق SSOT. |
| **ZATCA impact** | 🟢 LOW | لا تلامس مع قيود. |

**التصحيح الرئيسي لـ Iter-001**: الاعتقاد أن "أرقام /financial-position قد تختلف عن /financial-position-ledger" **خاطئ** — كلتاهما تقرأ نفس الاستجابة. الخطر الحقيقي أصغر بكثير من التصنيف الأولي.

---

## 5. Recommendations — التوصيات (لا تُنفَّذ إلا بموافقتك)

| # | التوصية | نوع | خطر التنفيذ |
|---|---|---|---|
| **R1** | **إبقاء الوضع كما هو** — النظام محمي، البيانات موحّدة، لا خطر فوري. | Keep-as-is | 🟢 |
| **R2** | **حذف `FinancialPosition.jsx`** (544 سطر Dead Code) + إزالة الـ import من `App.js:32` (تقريباً). | Cleanup | 🟢 آمن — لا caller. |
| **R3** | **حذف route `/financial-position` من `App.js`** بعد التأكد ألا bookmark قديم يستخدمه. أو **إبقاؤه** كحماية دائمة (LegacyRedirect بلا صيانة). | Optional | 🟢 |
| **R4** | **نقل الحقول الإضافية من القديمة إلى الحديثة**: reconciliation summary + open liabilities count badge — قبل حذف الملف. | Feature parity | 🟡 يتطلب اختبار UI. |
| **R5** | **RCA فرعي لـ `/reconciliation/summary`** — تحقق أن مصدره SSOT وليس Legacy. جزء من P1 الحالي. | يحتاج RCA لاحق | — |
| **R6** | **حذف `Dashboard.jsx` link إلى `/settlements`** (اكتُشف في P0.1) — كذلك تحقق من روابط Dashboard إلى `/financial-position` القديم. | Dashboard cleanup | 🟢 آمن. |

---

## 6. مقارنة سريعة: هل السبب الجذري نفس /settlements؟

نعم:
- كلا الـ Legacy pages (`/settlements`, `/financial-position`) **معطّلة بواسطة LegacyRedirect** منذ Iter-250a.
- الـ imports لا تزال في `App.js`.
- الـ pages نفسها لا تزال في الملفات لكن غير مستخدمة.
- الـ backend لكل منها يعمل بنفس شكل مختلف:
  - `/settlements` → endpoints تكتب على collection Legacy (`payment_adjustments`).
  - `/financial-position` → endpoint يقرأ من SSOT (`general_ledger`).

**الفرق الحاسم**: `/financial-position` **نظّف نفسه في Iter-217** ونقل مصدر البيانات إلى SSOT قبل تعطيل الواجهة. `/settlements` **لم يفعل** — الـ endpoints لا تزال تكتب على Legacy collection.

**استنتاج ضمني**: نمط Iter-217 هو المثال الصحيح — لو أراد الفريق مستقبلاً إعادة إحياء أي صفحة Legacy، الطريق: (1) نقل الـ endpoint إلى SSOT أولاً، (2) map الاستجابة إلى الـ UI القديم كطبقة تكيّف. `FinancialPosition.jsx:92-140` نموذج جيّد لهذا النمط.

---

## 7. Read-Only Confirmations — تأكيدات هذا الـ RCA

- ✅ لم يُلمس أي ملف كود.
- ✅ لم يُعطَّل أي endpoint.
- ✅ لم يُخفَ أي route.
- ✅ لم يُضَف أي redirect جديد.
- ✅ لم تُحذف أي collection.
- ✅ لم يُنفَّذ أي migration.
- ✅ لم يُنفَّذ أي write على DB.
- ✅ لم يُستدعَ Qoyod API.
- ✅ لم يُنفَّذ Deploy.
- ✅ `production_writes_locked=true` باقٍ.
- ✅ `selective_live_send_enabled=false` باقٍ.

---

## 8. المطلوب منك الآن

**اختر واحداً من:**

| الخيار | الوصف |
|---|---|
| **A** | **قبول الوضع كما هو** — لا تعديل، ننتقل إلى P0.3 (`liabilities` + `account_transactions` reads). |
| **B** | **حذف `FinancialPosition.jsx` + الـ import** كتنظيف dead code (R2). لا تأثير محاسبي. |
| **C** | **نقل الحقول الإضافية إلى `FinancialPositionLedger.jsx`** ثم حذف القديم (R4+R2). |
| **D** | **RCA فرعي لـ `/reconciliation/summary`** أولاً قبل P0.3. |
| **E** | **ننتقل مباشرة إلى P0.3** — ترك التنظيف لاحقاً. |

**التذكير**:
- P0.1 مسجَّل كـ **P0 Gate قبل فتح النظام لأي مستخدم فعلي**: تعطيل POST/PUT/DELETE على `/api/settlements`.
- P0.2 نتيجته: **لا مخاطر عالية**. النظام سليم من هذه الجهة.
- P0.3 لم يبدأ.
- لا Deploy، لا تعديل حتى تعطي إذناً صريحاً.
