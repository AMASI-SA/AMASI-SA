# Eligible Orders / طلبات مؤهلة لم تُرحّل إلى قيود — Read-Only Design (Iter-001)

**Author:** E1 (Emergent Agent)  
**Scope:** تصميم صفحة Read-Only لعرض كل طلبات سلة المؤهلة للفوترة والتي لم تصل إلى قيود بعد.  
**Mode:** Read-only design فقط. لا كتابة كود، لا endpoint جديد يُنشأ، لا Deploy.  
**Date:** 2026-07-01  
**Report file:** `/app/AUDIT/ELIGIBLE_ORDERS_READONLY_DESIGN_ITER_001.md`

---

## 0. TL;DR

- ⚠️ **الفرق الحاسم عن `QoyodPendingOrders`**: صفحة Pending Orders تقرأ من `integration_inbox` — أي **الطلبات التي دخلت خط المعالجة**. صفحة "Eligible Orders" ستقرأ من `unified_orders` — أي **كل الطلبات في سلة**، وتُقارنها بـ `integration_inbox` + `qoyod_invoices` لاكتشاف:
  - طلبات دخلت الـ pipeline لكن توقفت (مرئية في Pending Orders).
  - **طلبات لم تدخل الـ pipeline إطلاقاً** (مفقودة تماماً — الأخطر).
  - طلبات دخلت وأُرسلت (مغلقة).
- 🟢 **لا endpoint جديد كتابياً** — Read-only فقط. يُقترح: `GET /api/integrations/qoyod/admin/eligible-orders`.
- 🟢 **لا كتابة** على أي collection. لا bypass، لا approve.
- 🟢 يُحتَرم القفل: `production_writes_locked=true`, `selective_live_send_enabled=false` — العرض يعرض السبب فقط.

---

## 1. مصادر البيانات (Collections)

| Collection | القراءة | الغرض |
|---|---|---|
| `unified_orders` | ✅ Read-only | مصدر الحقيقة لكل طلبات سلة — النقطة المرجعية. |
| `integration_inbox` | ✅ Read-only | كشف "هل الطلب دخل خط المعالجة؟" + آخر `pipeline_stage`. |
| `qoyod_invoices` | ✅ Read-only | كشف "هل الفاتورة أُرسلت لقيود؟" + `qoyod_invoice_id` حقيقي (بدون DRY:/PREVIEW:). |
| `qoyod_customers_mapping` | ✅ Read-only | كشف حالة ربط العميل (adopted / dry_run_only). |
| `qoyod_products_mapping` | ✅ Read-only | كشف حالة ربط المنتج (adopted / dry_run_only). |
| `qoyod_settings` | ✅ Read-only | قراءة `invoice_trigger_statuses`, `selective_live_send_enabled`. |

**لا كتابة على أي collection**. **لا استدعاء لـ Qoyod API**.

---

## 2. منطق التأهيل — كيف نُحدد الطلبات المؤهلة؟

### 2.1 مرشّح الاستعلام الأولي (SQL-like)

```
SELECT * FROM unified_orders
WHERE user_id = <tenant>
  AND status IN {completed, delivered, shipped, shipping,
                  processing, in_progress,
                  تم التنفيذ, تم التوصيل, تم الشحن, جاري التوصيل}
  AND created_at >= <since_date>   -- default: last 90 days
```

**المصدر الموحد للحالات**: `ELIGIBLE_ORDER_STATUSES` من `integrations.qoyod.eligible_statuses` — نفس المصدر الذي يستخدمه `preflight` و `business_rules` و `live_send_gate`. **لا drift**.

### 2.2 التصنيف — Left-Join مع الجداول الأخرى

لكل طلب مرشّح:

```
inbox_row  = integration_inbox.find_one(salla_order_id=order.id)
invoice    = qoyod_invoices.find_one(salla_order_id=order.id,
                                     qoyod_invoice_id ∉ DRY:/PREVIEW:)
customer_ok = check qoyod_customers_mapping for phone/email
                 → NOT dry_run_only AND has real qoyod_customer_id
products_ok = for each SKU in order.items:
                 qoyod_products_mapping.find_one(sku=sku)
                 → NOT dry_run_only AND product_id is numeric
totals_ok  = order.total ≈ sum(items.total) + shipping + tax
                  (tolerance = 0.01 SAR)
```

### 2.3 التوصية المستنبطة — 8 حالات (كما طلبت)

**قواعد التصنيف بالترتيب** (أول قاعدة تصدق → تُوقف السلسلة):

| # | القاعدة | التوصية | لون |
|---|---|---|---|
| 1 | `invoice != None AND qoyod_invoice_id` نُميري صالح | `already_sent` | 🟢 |
| 2 | `NOT totals_ok` (diff > 0.01) | `totals_mismatch` | 🟠 |
| 3 | `payment_method == bank_transfer` AND `receiving_bank == None` | `blocked_bank_transfer_routing` | 🟠 |
| 4 | `payment_method ∉ (COD ∪ Prepaid ∪ BNPL ∪ bank_transfer)` | `blocked_status` (unsupported) | 🔴 |
| 5 | `NOT customer_ok` | `blocked_customer` | 🔴 |
| 6 | `NOT products_ok` (any SKU with dry_run_only or missing) | `blocked_product` | 🔴 |
| 7 | `inbox_row is None` (never entered pipeline) | `ready_for_manual_approval` | 🟡 |
| 8 | كل الفحوصات نجحت + selective_live_send_enabled=false | `ready_for_preview` | 🟢 |

**ملاحظة**: التصنيف #7 هو الأخطر — يعني الطلب مرّ في سلة لكن Mezan لم يستقبله أو لم يعالجه. سيتم تمييزه بلون خاص.

---

## 3. تدفق البيانات (Data Flow)

```
Frontend                            Backend
─────────                            ────────

/qoyod/eligible-orders  ─── GET ───► /api/integrations/qoyod/admin/eligible-orders
                                            │
                                            ▼
                                     read qoyod_settings
                                     (get ELIGIBLE_ORDER_STATUSES)
                                            │
                                            ▼
                                     unified_orders.find({
                                        status: {$in: eligible},
                                        created_at: {$gte: since}
                                     })  ── batch of N orders
                                            │
                                            ▼
                                     For each order:
                                       + integration_inbox.find_one(order_id)
                                       + qoyod_invoices.find_one(order_id)
                                       + [customer/products/totals checks]
                                            │
                                            ▼
                                     Classify → 1 of 8 recommendations
                                            │
                                            ▼
                                     Return JSON:
                                     {
                                       counts: {ready_for_preview: N,
                                                already_sent: N, ...},
                                       items: [{order_number, trace_id,
                                                status, payment_method,
                                                total, recommendation,
                                                blockers: [...], ...}]
                                     }
```

**لا استدعاء لـ Qoyod API** في هذا المسار. كل شيء من DB محلي.

---

## 4. Response Schema (النموذج)

```json
{
    "generated_at": "2026-07-01T12:00:00+03:00",
    "since_date":  "2026-04-02",
    "counts": {
        "ready_for_preview":          0,
        "ready_for_manual_approval":  0,
        "already_sent":               0,
        "blocked_customer":           0,
        "blocked_product":            0,
        "blocked_bank_transfer_routing": 0,
        "blocked_status":             0,
        "totals_mismatch":            0
    },
    "gates": {
        "production_writes_locked": true,
        "selective_live_send_enabled": false,
        "settlements_write_gate": "OPEN (pending disable)"
    },
    "items": [
        {
            "order_number":     "268307955",
            "trace_id":         "c7b3f31e77864b06bd1130e1308b48c9",
            "order_date":       "2026-06-28T09:15:00+03:00",
            "order_status":     "delivered",
            "payment_method":   "tabby_installment",
            "total_amount":     116.85,
            "currency":         "SAR",
            "pipeline_stage":   "UNRESOLVED_QOYOD_DEPENDENCY",
            "has_prior_invoice": false,
            "prior_invoice_id": null,
            "customer_resolved": true,
            "customer_qoyod_id": 223,
            "products_resolved": true,
            "products_summary": {"resolved": 3, "dry_run_only": 0, "missing": 0},
            "totals_valid":     true,
            "totals_diff":      0.0,
            "posting_mode":     "paid_receipt",
            "sendable":         true,
            "block_reason":     null,
            "recommendation":   "ready_for_preview",
            "in_inbox":         true,
            "detail_links": {
                "preview_url":  "/api/integrations/qoyod/admin/preview-reprocess",
                "inbox_url":    "/integrations/qoyod/pending-orders"
            }
        }
    ]
}
```

**كل الحقول للقراءة فقط** — الـ `detail_links` تشير إلى endpoints موجودة أصلاً.

---

## 5. حالات خاصة — كيف نتعامل معها؟

### 5.1 الطلبات `already_sent`

- الفحص: `qoyod_invoices.find_one(salla_order_id, qoyod_invoice_id ∉ DRY:/PREVIEW:)`.
- إذا موجود: التوصية `already_sent` + عرض `qoyod_invoice_id` كرابط لـ QoyodInvoices.
- **لا يظهر في القائمة الرئيسية** — يُخفى افتراضياً، يُظهَر بـ filter checkbox "أظهر المُرسَلة".

### 5.2 COD

- Payment method = `cod / cash_on_delivery`.
- إذا `products_ok AND customer_ok AND totals_ok`: التوصية `ready_for_preview` (COD يمر عادة).
- في تفاصيل الطلب: `posting_mode = "credit_invoice_only"` (COD → فاتورة بدون سند قبض).

### 5.3 Bank Transfer

- Payment method = `bank_transfer`.
- بحاجة إلى `receiving_bank` (الحساب البنكي المستلم) من settings.
- إذا `receiving_bank == None`: التوصية `blocked_bank_transfer_routing` + عرض الرسالة "يحتاج تحديد البنك المستلم من إعدادات قيود".
- إذا موجود: التوصية `ready_for_preview` + `posting_mode = "credit_invoice_only"` (Iter-294 سيوجّه السند لاحقاً).

### 5.4 الطلبات المفقودة من الـ Inbox

- الفحص: `integration_inbox.find_one(salla_order_id)` → `None`.
- التوصية: `ready_for_manual_approval` + Alert "الطلب موجود في سلة لكنه لم يدخل خط المعالجة".
- في UI: يُلوَّن بأصفر لأنه يشير إلى webhook مفقود أو stall في الـ pipeline.

### 5.5 BNPL (Tabby / Tamara / Emkan)

- منذ Iter-293.5-rev3 → BNPL على قائمة السماح (allowlist).
- نفس منطق Prepaid: إذا mappings نظيفة → `ready_for_preview`.

### 5.6 الحالات المتقادمة

- إذا `unified_orders.status ∈ {cancelled, refunded, deleted}` → **لا تُدرج أصلاً في القائمة** (unified_orders يمكن أن يحتوي تغيرات حالة).
- إذا `unified_orders.status ∈ eligible` **لكن** الحالة الحالية في `integration_inbox` هي `STALE_TRACE_NOT_CURRENT_ORDER_STATE` → التوصية `blocked_status` + رسالة "التتبّع القديم لا يعكس الحالة الحالية".

---

## 6. آليات الحماية (Guardrails)

| الحماية | كيف |
|---|---|
| **لا كتابة على DB** | endpoint واحد فقط `GET` — لا `POST/PUT/DELETE`. |
| **لا استدعاء Qoyod API** | كل البيانات من MongoDB محلياً. لا `QoyodAPIClient` calls. |
| **احترام القفل** | حقل `gates` في الاستجابة يظهر `production_writes_locked` و `selective_live_send_enabled`. UI يعرض بانر "🔒 قفل الإنتاج مفعّل — عرض فقط". |
| **لا Preview auto-run** | التوصية تُعرض فقط. أي "Preview" لطلب معيَّن يفتح الـ preview endpoint الموجود مسبقاً (`/admin/preview-reprocess`). |
| **لا approve-and-send** | الصفحة **لا تحتوي** أي زر أو link يستدعي endpoint إرسال. |
| **Rate limit** | `limit=500` كحد أقصى لكل استدعاء. Pagination بـ `page` و `page_size`. |
| **Read-Only banner UI** | بانر ثابت أعلى الصفحة: "🕰️ صفحة تشخيصية Read-Only — لا يوجد إجراء إرسال". |

---

## 7. Frontend Design (تخطيط الصفحة)

```
┌─────────────────────────────────────────────────────────────┐
│ 🕰️ Read-Only Diagnostic — لا يوجد إجراء إرسال              │
├─────────────────────────────────────────────────────────────┤
│ Gates:  🔒 Production Writes Locked  ⛔ Live Send Disabled  │
│                                                              │
│ Filters: [Since date ▼] [Status ▼] [Payment ▼] [Recommendation ▼] │
│          [☐ أظهر المُرسَلة already_sent]                   │
├─────────────────────────────────────────────────────────────┤
│ Counts Row:                                                  │
│  ✅ Ready for Preview: 12      🟡 Ready Manual Approval: 3  │
│  🔴 Blocked Customer: 5        🔴 Blocked Product: 8         │
│  🟠 Bank Transfer Routing: 2   🟠 Totals Mismatch: 1        │
│  🔴 Blocked Status: 4          🟢 Already Sent: 145         │
├─────────────────────────────────────────────────────────────┤
│ Table                                                         │
│ ┌──────────┬──────────┬────────────┬──────────┬──────────┐  │
│ │ Order #  │ Date     │ Payment    │ Total    │ Recomm.  │  │
│ ├──────────┼──────────┼────────────┼──────────┼──────────┤  │
│ │ 268307955│ 28/06/26 │ tabby_inst │ 116.85   │ 🟢 Prev  │  │
│ │ 268307956│ 27/06/26 │ cod        │ 240.00   │ 🔴 Cust  │  │
│ │ ...                                                       │  │
│ └──────────┴──────────┴────────────┴──────────┴──────────┘  │
│                                                              │
│ Row click → Drawer: order details + blockers + Preview link │
└─────────────────────────────────────────────────────────────┘
```

**Route**: `/integrations/qoyod/eligible-orders` (يُضاف كـ link في Sidebar تحت قسم قيود).

**لا زر إرسال**. **لا زر Approve**. **فقط عرض + Preview link (يفتح Preview الموجود مسبقاً)**.

---

## 8. الفرق مع `QoyodPendingOrders.jsx` الموجودة

| الميزة | Pending Orders (موجودة) | Eligible Orders (مقترحة) |
|---|---|---|
| مصدر البيانات | `integration_inbox` (طلبات في الـ pipeline) | `unified_orders` (كل الطلبات) |
| نطاق الطلبات | ما دخل الـ pipeline فقط | **يشمل الطلبات المفقودة من الـ pipeline** |
| التصنيف | 7 categories (ready_to_send, needs_mapping, ...) | 8 recommendations (أدق، تعالج already_sent + totals_mismatch + missing_from_inbox) |
| الغرض | Triage تشغيلي يومي | **Audit استراتيجي بعد فترات الإيقاف** |
| Show already_sent | ❌ لا يظهرون | ✅ يظهرون كـ filter option |
| Show missing_from_inbox | ❌ لا يعرفها | ✅ **الميزة الأهم** — يكشفها |

**الاستنتاج**: الصفحتان **مكمّلتان لا متكرّرتان**. Pending Orders للتشغيل اليومي، Eligible Orders للـ Audit والاستعادة بعد فترات الإيقاف.

---

## 9. خطة التنفيذ (لا تُنفَّذ إلا بموافقتك)

**Phase A — Backend endpoint (Read-Only)**
1. `GET /api/integrations/qoyod/admin/eligible-orders` مع query params: `since_date`, `status`, `payment_method`, `recommendation`, `show_already_sent`, `page`, `page_size`.
2. Handler يجمع البيانات من الـ 5 collections ويصنّف.
3. اختبارات pytest: 8 حالات تصنيف + edge cases (empty inbox, missing mappings).

**Phase B — Frontend Page (Read-Only)**
1. `EligibleOrders.jsx` — 400-500 سطر تقريباً.
2. Route `/integrations/qoyod/eligible-orders` في `App.js`.
3. Sidebar entry تحت قسم قيود.
4. Drawer للتفاصيل — يعرض blockers.
5. Preview link يستخدم `/admin/preview-reprocess` الموجود.

**Phase C — Testing**
1. Backend tests: pytest يغطي كل التصنيفات الـ 8.
2. Frontend: `testing_agent_v3_fork` للـ smoke test.
3. لا screenshot سيرة عمل (لا يوجد workflow).

**Phase D — بعد التحقق من التقرير**
1. الاعتماد التدريجي حسب رأيك.

---

## 10. المسائل المفتوحة (تحتاج قرارك قبل التنفيذ)

| # | السؤال | افتراضياً |
|---|---|---|
| 1 | **النطاق الزمني الافتراضي؟** آخر 90 يوم، أم 180، أم بلا حد؟ | 90 يوم |
| 2 | **هل نعرض الطلبات المُرسَلة `already_sent` افتراضياً؟** | لا — filter منفصل |
| 3 | **هل الصفحة تحتاج Auto-refresh** (كل X دقيقة)؟ | لا — refresh يدوي فقط |
| 4 | **Sidebar link**: `طلبات مؤهلة للفوترة` أم `Eligible Orders (Audit)`؟ | "طلبات مؤهلة للفوترة" |
| 5 | **حد أقصى في response؟** 200 أم 500 أم pagination كامل؟ | 200 مع pagination |
| 6 | **هل نُدخل الطلبات القديمة جداً (ما قبل تفعيل الـ integration)؟** — قد يتسرب طوفان بيانات لا معنى له | لا — `since_date` إلزامي، افتراضياً 90 يوم |
| 7 | **هل نحتاج ملف CSV export؟** لتحليل خارجي في Excel | نعم كـ Phase B optional |
| 8 | **هل الصفحة تحتاج access control خاص** (فقط admin)؟ | نفس صلاحيات `pending-orders` |

---

## 11. Read-Only Confirmations

- ✅ هذا **تصميم فقط** — لا كود، لا endpoint فعلي جديد.
- ✅ لم يُلمس أي ملف.
- ✅ لم تُنشأ collection.
- ✅ لم يُنفَّذ migration.
- ✅ لم يُنفَّذ Deploy.
- ✅ لم يُستدعَ Qoyod API.
- ✅ `production_writes_locked=true` باقٍ.
- ✅ `selective_live_send_enabled=false` باقٍ.
- ✅ P0 Gate على `/settlements` writes باقٍ (سيُنفَّذ قبل فتح النظام).

---

## 12. المطلوب منك الآن

**اختر:**

| الخيار | الوصف |
|---|---|
| **A** | **قبول التصميم كما هو** — ابدأ Phase A (Backend endpoint) بموافقتك على الافتراضيات في §10. |
| **B** | **قبول التصميم مع تعديل الافتراضيات** — أخبرني بأجوبتك على §10 وسأبدأ فوراً. |
| **C** | **تعديل التصميم** — أرسل ملاحظاتك على المنطق أو الحقول أو التصنيفات. |
| **D** | **RCA إضافي أولاً** — تحتاج توضيحاً أعمق لعلاقة الجداول أو التصنيفات قبل الاعتماد. |
| **E** | **تأجيل** — لا تنفيذ الآن، ابقَ التصميم مسجَّلاً كـ backlog. |

**التذكيرات:**
- ✅ P0.1/P0.2/P0.3 مغلقة كـ RCA.
- 🔴 P0 Gate `/settlements` write disable — مؤجَّل حتى فتح النظام.
- 🔴 مهمة 268307955 — بانتظار Redeploy + adoption يدوي.
- ⛔ لا approve-and-send، لا one-shot، لا selective live send.
- ⛔ لا Qoyod writes، لا Deploy.

لا تنفيذ حتى إشارتك.
