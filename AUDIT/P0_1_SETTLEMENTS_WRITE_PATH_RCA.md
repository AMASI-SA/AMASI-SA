# P0.1 — `/settlements` Write Path RCA (Read-Only)

**Author:** E1 (Emergent Agent)  
**Scope:** Frontend route `/settlements`, page `Settlements.jsx`, backend `POST /api/settlements`, collection `payment_adjustments`, and every reader/writer touching them.  
**Mode:** Read-only. No code changed. No routes disabled. No collections touched. No writes to Production. No Qoyod calls. No Deploys.  
**Date:** 2026-07-01  
**Report file:** `/app/AUDIT/P0_1_SETTLEMENTS_WRITE_PATH_RCA.md`  
**Trigger:** Iter-001 identified `/settlements` as hidden from Sidebar but with active `POST` — potential silent write to accounting data.

---

## 0. TL;DR — الخلاصة التنفيذية

- ✅ **Frontend مطمئن**: `/settlements` **مُعطَّلة فعلياً في الواجهة** — الـ `App.js` يلفّها في `LegacyRedirect` منذ Iter-250a. لا يمكن للمستخدم فتح `Settlements.jsx` من الواجهة. النقر على أي رابط `/settlements` يعرض بانر "صفحة قديمة معطّلة" ويعيد التوجيه إلى `/settlements-overview`.
- 🟠 **Backend غير مطمئن**: `POST /api/settlements` **لا يزال route فعّالاً وقابلاً للاستدعاء المباشر** من أي client (curl, Postman, بوابة Legacy). لم يُعطَّل عند تعطيل الواجهة.
- 🟠 **الـ collection المكتوبة**: `payment_adjustments` فقط — **لا كتابة على `general_ledger`**. هذا يعني أي write من هذا المسار **يتجاوز الـ SSOT المحاسبي (Iter-161)** ويُنتج أرقاماً غير معتمدة في الـ Ledger.
- 🟠 **الـ Dashboard يقرأ `payment_adjustments`** (`server.py:2544`) لحساب "Salla 14-day window" — أي كتابة عرضية على هذا المسار **ستؤثر على أرقام Dashboard** لكنها لن تُرى في `/financial-position-ledger`.
- 🟢 **لا writes على**: `general_ledger`, `settlement_entries`, `settlement_files`, `account_transactions`, `liabilities`, `unified_orders`.
- 🟢 **لم يُستدعَ من أي مكان في الكود إلا من الصفحة الميتة `Settlements.jsx`** — لا webhooks، لا cron، لا scheduler، لا service آخر يستدعي `POST /api/settlements` internally.
- 🟢 **`record_auto_settlement()` helper موجود لكن غير موصول** (كود ميت + اختباري). التعليق حرفياً: `NOT WIRED UP IN 70.1 — the function exists so the schema, types and index can be unit-tested`.

---

## 1. الملفات المعنية

### 1.1 Frontend

| ملف | حالة | ماذا يفعل |
|---|---|---|
| `/app/frontend/src/App.js:50` | ⚠️ import ميت | `import Settlements from "./pages/Settlements"` — الاسم مستورد لكنه غير مستخدم عملياً. |
| `/app/frontend/src/App.js` (route `/settlements`) | 🟢 مُعطَّلة عبر LegacyRedirect | `<Route path="/settlements" element={<LegacyRedirect oldLabel="تسويات المدفوعات" replacement="/settlements-overview" reason="تم توحيد التسويات في صفحة واحدة مبنية على Ledger." />} />` — الصفحة `Settlements.jsx` **لا تُعرض أبداً**. |
| `/app/frontend/src/pages/Settlements.jsx` (452 سطر) | 🕰️ ميتة (Dead Code) | تحتوي `api.post("/settlements", ...)` و `api.get("/settlements")` و `api.get("/settlements/summary")` — لكن لا يمكن الوصول إليها من الـ router. |
| `/app/frontend/src/pages/Dashboard.jsx` | ⚠️ رابط قديم فقط | يحوي `<Link to="/settlements">تسويات سلة المسجَّلة</Link>` — النقر يعرض LegacyRedirect (آمن). |
| `/app/frontend/src/components/LegacyRedirect.jsx` | 🟢 يعمل بشكل صحيح | يظهر بانر "🕰️ صفحة قديمة معطّلة" ويربط بـ `/settlements-overview`. |

**Frontend Verdict**: 🟢 لا مسار مستخدم فعلاً. النطاق الوحيد للخطر هو رابط `Dashboard.jsx` الذي يقود إلى صفحة redirect فقط.

### 1.2 Backend

| ملف | Route/Handler | ماذا يفعل |
|---|---|---|
| `/app/backend/server.py:76-82` | `import` | `from settlements_routes import attach_settlements_routes, aggregate_settlements_by_provider, ..., ensure_settlements_indexes` |
| `/app/backend/server.py:3901` | Registration | `attach_settlements_routes(api, db)` — يُلحق `router` بجذر الـ API. |
| `/app/backend/settlements_routes.py:363-513` | `attach_settlements_routes` | يُنشئ `APIRouter(prefix="/settlements", tags=["settlements"])` ويسجّل 6 endpoints. |

---

## 2. Backend Endpoints — كل الأفعال تحت `/api/settlements`

| Method | Path | Handler | يقرأ من | يكتب إلى | خطر |
|---|---|---|---|---|---|
| GET | `/api/settlements/providers` | `list_providers` | (const) | — | 🟢 |
| GET | `/api/settlements` | `list_settlements` | `payment_adjustments` | — | 🟢 |
| **POST** | **`/api/settlements`** | **`create_settlement`** | — | **`payment_adjustments`** | 🟠 |
| **PUT** | **`/api/settlements/{id}`** | **`update_settlement`** | `payment_adjustments` | **`payment_adjustments`** | 🟠 |
| **DELETE** | **`/api/settlements/{id}`** | **`delete_settlement`** | — | **`payment_adjustments`** (delete) | 🟠 |
| GET | `/api/settlements/summary` | `settlements_summary` | `payment_adjustments` (via `aggregate_settlements_by_provider`) | — | 🟢 |

**تفصيل `POST /api/settlements` (`settlements_routes.py:414`):**
```python
doc = {
    "id": uuid, "user_id": ..., "order_id": ..., "order_number": ...,
    "payment_method": ..., "provider": detect_provider(...),
    "original_amount": ..., "new_amount": ..., "adjustment_amount": ...,
    "adjustment_type": partial_refund|full_refund|item_removed|
                       order_cancelled|manual_adjustment,
    "order_created_at": ..., "adjusted_at": ..., "reason": ...,
    "source": "manual", "detection_source": "manual", "trigger": "manual",
    "created_at": now, "created_by": user_id,
}
await db.payment_adjustments.insert_one(doc)
```

**لا يُكتب في**: `general_ledger`, `settlement_entries`, `settlement_files`, `account_transactions`, `liabilities`, `unified_orders`, `accounting_audit_log`.

---

## 3. Collection `payment_adjustments` — تحليل شامل

### 3.1 كتّاب Collection (Writers)

| موقع | نوع الكتابة | حالة |
|---|---|---|
| `settlements_routes.py:451` (`POST /settlements`) | `insert_one` | 🟠 **orphan-callable** — الواجهة معطّلة لكن الـ endpoint حيّ. |
| `settlements_routes.py:478` (`PUT /settlements/{id}`) | `update_one` | 🟠 **orphan-callable** |
| `settlements_routes.py:488` (`DELETE /settlements/{id}`) | `delete_one` | 🟠 **orphan-callable** |
| `settlements_routes.py:216` (`backfill_settlement_provenance`) | `update_many` | 🟢 startup only (idempotent). Iter-70.1. Yes it modifies rows but only to add `detection_source`/`trigger` fields. Safe. |
| `settlements_routes.py:281` (`record_auto_settlement`) | `insert_one` | 🟢 **NOT WIRED UP** — التعليق نفسه يقول: `"NOT WIRED UP IN 70.1 — the function exists so the schema, types and index can be unit-tested in isolation before 70.2 actually starts calling it from upsert_unified_order()."` Grep لم يجد أي استدعاء من كود إنتاجي. |
| Tests (`test_settlements_iter70_1.py`) | `insert_one` | 🟢 test-only. |

**⚠️ Zero code path** in production actually invokes `record_auto_settlement`, `settlement_files upload`, `webhook handler` etc. writes to `payment_adjustments`. الطريق الوحيد للكتابة اليوم هو الـ HTTP endpoints.

### 3.2 قرّاء Collection (Readers)

| موقع | كيف يستخدمه | خطر |
|---|---|---|
| `server.py:2544` (Dashboard aggregation) | يجمع Salla adjustments حسب `inside_14d` / `outside_14d` — يُظهرها في KPI الـ Dashboard. | 🟠 **يؤثر على Dashboard** |
| `server.py:4670-4672` | index creation فقط. | 🟢 |
| `salla_balance_forensic_routes.py` (5 مواقع) | forensic reports فقط. | 🟢 read-only |
| `settlements_routes.py` (نفسها) | GET/summary | 🟢 |
| `tests/test_settlements_iter70_1.py` | tests | 🟢 |

**لا قرّاء من**: `general_ledger` routes, `accounts_routes.py`, `financial-position` endpoints, `SettlementDashboard`, `PaymentSettlements`, `BnplSettlements`. أي: **`payment_adjustments` منفصل عن SSOT الـ Ledger**.

### 3.3 حجم البيانات المتوقّع

- Index قائم على `(user_id, adjusted_at desc)`, `(user_id, order_number)`, `(user_id, provider)`.
- unique partial index على `(user_id, order_number, original_amount, new_amount)` عندما `detection_source=="auto"` — لكن **ما من writes auto** أصلاً، فهذا الحاجز لا يعمل ضد entries manual.
- منطقياً: إذا لم يكن أحد يستخدم `Settlements.jsx` منذ Iter-250a (تاريخ التعطيل)، فالـ collection **يجب أن يكون شبه فارغ أو مجمَّد** على البيانات القديمة.

---

## 4. مقارنة: Old Path vs Current SSOT Path

### 4.1 Iter-56/70.1 — النظام القديم (الآن Legacy)

```
User → Settlements.jsx UI → POST /api/settlements
                          → db.payment_adjustments.insert_one
                          
Dashboard → GET /api/dashboard/kpis → reads payment_adjustments (server.py:2544)
```

- **الغرض الأصلي**: تسجيل تعديلات يدوية لكل طلب (استرجاع جزئي، حذف منتج، إلخ) للحصول على "صافي إيرادات" أدق.
- **مشكلة النظام القديم**: البيانات لا تُوجَّه إلى `general_ledger` → أي رقم فيه لا يظهر في `/financial-position-ledger`.

### 4.2 Iter-161/193/221/251 — النظام الحالي (SSOT)

```
Salla webhook       → payment_refunds
                    → general_ledger  ✅
                    → unified_orders.actual_*

Settlement file     → settlements_import (upload)
                    → settlement_files + settlement_entries
                    → unified_orders.actual_*
                    → general_ledger  ✅

Bank transfer       → bank_transfer_reviews (queue)
                    → after approval → general_ledger  ✅

Settlement Engine   → SettlementDashboard (dry-run)
                    → general_ledger  ✅
```

**كل مسار حديث ينتهي في `general_ledger`**. الـ payment_adjustments لم يعد جزءاً من السلسلة.

### 4.3 هل توجد ازدواجية أو أرقام مختلفة؟

| البُعد | Old path | Current path | حكم |
|---|---|---|---|
| **الاسترجاعات (Refunds)** | `payment_adjustments` | `payment_refunds` + `general_ledger` | 🟠 **مصدران مختلفان لنفس المفهوم**. لكن `payment_adjustments` مجمَّد لأن الواجهة معطّلة. |
| **حذف منتج من طلب** | `payment_adjustments (item_removed)` | `unified_orders.diff` + `payment_refunds` | 🟠 نفس القضية — مجمَّد. |
| **تسوية يدوية عامة** | `payment_adjustments (manual_adjustment)` | ➜ لا بديل مباشر. الأقرب: `POST /api/financial-movements/new` + booking في `general_ledger`. | 🟠 القرار المحاسبي: كل تسوية يدوية اليوم يجب أن تسجَّل في GL، لا في payment_adjustments. |
| **صافي الإيرادات (Dashboard)** | يخصم `sum(payment_adjustments)` | يخصم من `general_ledger` (refunds) + `payment_adjustments` (14-day window Salla) | 🔴 **Dashboard الحالي يمزج المصدرين** — server.py:2544 لا يزال يقرأ من payment_adjustments. |

**الحكم**: 
- ✅ **لا ازدواجية كتابة اليوم** لأن الواجهة معطّلة والـ endpoint لا يستدعيه أحد.
- 🟠 **لكن Dashboard يقرأ من مصدر Legacy** (`payment_adjustments`) للنافذة الـ 14 يوماً. إن كان هذا الرقم قديماً/متجمّداً فسيكون الـ inside_14d/outside_14d غير دقيق.
- 🟠 **لو أي client خارجي (curl, Postman, script قديم) استدعى `POST /api/settlements`**، فسيُدخل رقماً في `payment_adjustments` **يظهر في Dashboard لكن لا يظهر في `/financial-position-ledger`** — كسر SSOT صامت.

---

## 5. Risk Assessment — التقييم النهائي

| Dimension | Level | Reason |
|---|---|---|
| **Frontend exposure** | 🟢 LOW | Route مُغلَّف بـ LegacyRedirect. |
| **Backend exposure** | 🟠 MEDIUM | Endpoints فعّالة بدون حاجز. |
| **Data integrity** | 🟠 MEDIUM | Writes تتجاوز GL. |
| **Discovery risk** | 🟢 LOW | لا caller خارجي معروف. |
| **Dashboard impact** | 🟠 MEDIUM | server.py:2544 يقرأ من هذا المصدر. |
| **Accounting compliance** | 🟠 MEDIUM | Writes لا تظهر في GL → مخالفة SSOT. |
| **ZATCA risk** | 🟢 LOW | لا كتابة على قيود من هذا المسار. |

---

## 6. Recommendations — التوصيات (لا تُنفَّذ إلا بموافقتك الصريحة)

| # | التوصية | نوع | خطر التنفيذ |
|---|---|---|---|
| **R1** | **Disable `POST /api/settlements`** (يعيد 410 Gone أو 405 Method Not Allowed) — منع أي كتابة عرضية على `payment_adjustments`. | Disable Write | 🟢 آمن جداً — لا caller معروف. |
| **R2** | **Disable `PUT /api/settlements/{id}` و `DELETE /api/settlements/{id}`** — نفس السبب. | Disable Write | 🟢 آمن. |
| **R3** | **Keep `GET /api/settlements`, `GET /api/settlements/summary`, `GET /api/settlements/providers`** كـ **Read-Only** — Dashboard قد يعتمد على `summary`، وأي تقرير forensic قد يستخدم `list`. | Keep Read-Only | 🟢 آمن. |
| **R4** | **RCA منفصل لـ `server.py:2544`** — هل الـ Dashboard 14-day window يعتمد على بيانات منتهية أم لا زال يحتاج تحديث حي؟ إن كانت البيانات مجمّدة، فيمكن إما (أ) تجميد Dashboard 14-day KPI، (ب) نقل حسابه إلى `payment_refunds` بدلاً من `payment_adjustments`. | يحتاج RCA جديد | — |
| **R5** | **Delete `Settlements.jsx`** من الملفات + إزالة `import` من `App.js:50` (سيبقى LegacyRedirect فقط) — تنظيف Dead Code. | Cleanup | 🟢 آمن جداً — لا caller. |
| **R6** | **إبقاء `record_auto_settlement()` + `stamp_order_amount_history()`** كـ helpers — لم يُوصَلا وبقاؤهما لا يضرّ. Iter-70.2 قد يستأنف الفكرة. | Keep | 🟢 |
| **R7** | **إضافة تعليق `@deprecated Iter-250a`** فوق `attach_settlements_routes` مع `# ⚠️ Read-Only mode after Iter-XXX` عند تنفيذ R1-R2. | Documentation | 🟢 |
| **R8** | **إضافة test regression** يتأكد أن `POST /api/settlements` يعيد 410/405 بعد تنفيذ R1. | Testing | 🟢 |

---

## 7. تحقيقات إضافية اقترحها هذا الـ RCA

هذه ملاحظات جانبية اكتشفتها أثناء الفحص — **لا تُنفَّذ الآن**، سجّلها فقط:

1. 🟡 **`server.py:2544` يقرأ من `payment_adjustments`** — يحتاج RCA منفصل ليؤكد أن Dashboard 14-day window logic لا يزال صالحاً بعد Iter-161.
2. 🟡 **`Dashboard.jsx` لا يزال يعرض `<Link to="/settlements">`** — يقود إلى LegacyRedirect (آمن) لكن رابط تنقل قديم يجب تحديثه ليقود مباشرة إلى `/settlements-overview` أو `/settlement-engine`.
3. 🟡 **`record_auto_settlement()` مقصود منه Iter-70.2** الذي لم يُنفَّذ أبداً. يجب اتخاذ قرار: إما إتمام Iter-70.2 (رفض من الـ CEO سابقاً حسب تعليق `settlements_import/service.py:4` — "NEVER auto-create payment_adjustments rows") أو حذف الـ helper.
4. 🟡 **`salla_balance_forensic_routes.py`** يقرأ من `payment_adjustments` بكثافة — إذا جمّدنا المصدر يجب أن نعرف أنه ما زال جزءاً من forensic answers.

---

## 8. Read-Only Confirmations — تأكيدات هذا الـ RCA

- ✅ لم يُلمس أي ملف كود.
- ✅ لم يُعطَّل أي endpoint.
- ✅ لم يُخفَ أي route.
- ✅ لم تُحذف أي collection.
- ✅ لم يُنفَّذ أي migration.
- ✅ لم يُنفَّذ أي write على DB.
- ✅ لم يُستدعَ Qoyod API.
- ✅ لم يُنفَّذ Deploy.
- ✅ `production_writes_locked=true` باقٍ.
- ✅ `selective_live_send_enabled=false` باقٍ.

---

## 9. المطلوب منك الآن

**اختر واحداً من:**

| الخيار | الوصف |
|---|---|
| **A** | نفّذ R1+R2+R3 معاً (disable POST/PUT/DELETE + keep GET) — أفضل قرار أمانياً. مع اختبار regression. |
| **B** | نفّذ R1 فقط (disable POST) — أقل تدخّل ممكن. |
| **C** | نفّذ R5 فقط (حذف `Settlements.jsx` + import) — cleanup فقط، الـ endpoints تبقى. |
| **D** | لا شيء الآن — دع الوضع كما هو ونتحرك إلى P0.2 (`/financial-position`). |
| **E** | نفّذ R1+R2+R3+R5+R7+R8 كلها في PR واحد — التنظيف الكامل. |
| **F** | ابدأ RCA فرعي لـ R4 أولاً (`server.py:2544` Dashboard 14-day window). |

**التذكير**:
- لا كتابة على Production.
- لا Deploy.
- لا تغيير حتى تعطي إذناً صريحاً.
- P0.2 و P0.3 لم تبدأ.
