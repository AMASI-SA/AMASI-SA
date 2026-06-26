# دليل تشغيل Make.com — Qoyod Webhook

**الحالة**: 🟢 جاهز للتشغيل · 2026-06-27
**المرحلة**: First Production Dry Run
**المرجع المتعمق**: [`qoyod-webhook-contract-v1.md`](./qoyod-webhook-contract-v1.md) — لا تتجاوز ما في هذا الدليل لأي عملية متقدمة

---

## 📋 قبل أن تبدأ — قائمة التحقق المسبقة

تأكّد أن هذه الخمسة مكتملة قبل فتح Make.com:

| # | البند | كيف تتحقق |
|---|---|---|
| 1 | Dry Run مُفعَّل | `/integrations/qoyod/settings` → التبديل "🧪 وضع التشغيل الجاف" أخضر |
| 2 | Tax ID محفوظ (= `1`) | نفس الصفحة → حقل Tax ID = `1` |
| 3 | Payment Method Mapping يحتوي على طريقة الدفع التي ستستخدمها في الـ Test (مثلاً `mada`) | جدول "ربط طرق الدفع" — يجب أن تظهر `mada` بصف "✓ مربوط" مع Account ID قيود |
| 4 | Webhook Token مُولَّد ومحفوظ خارجياً | قسم "Webhook Token (Make.com → ميزان)" → الـ Fingerprint ظاهر. **انسخ القيمة الكاملة من Mezan قبل الانتقال إلى Make** |
| 5 | حساب قيود نظيف (Audit أعطى 0 لكل شيء) | `/integrations/qoyod/fresh-start` → بانر أخضر "حساب قيود نظيف" |

> ⚠️ إذا فشلت أي خانة، أصلحها أولاً. لا تكمل الإعداد.

---

## المرحلة 1️⃣ — إعداد HTTP Module في Make.com

### الخطوة 1.1 — أضف Module جديد

داخل سيناريو Salla الحالي، أضف **Module جديد** من نوع:

```
HTTP → Make a request
```

> ❗ لا تستبدل HTTP Module القديم الذي يُرسل لـ Qoyod المباشر. اتركه (سنُلغي تفعيله لاحقاً عند Go Live). أضف Module جديد بجانبه.

### الخطوة 1.2 — اضبط الحقول بهذا الترتيب

#### `URL`
```
https://mezansalla.com/api/integrations/qoyod/webhook
```

#### `Method`
```
POST
```

#### `Headers` — أضف 4 headers بالضبط:

| Name | Value |
|---|---|
| `Content-Type` | `application/json; charset=utf-8` |
| `X-Webhook-Token` | الـ Token الذي نسخته من Mezan في الخطوة المسبقة #4 |
| `X-Idempotency-Key` | `salla:order:{{1.order.reference_id}}:{{1.event}}` |
| `X-Mezan-Source` | `make.com` |

> 💡 الـ `{{1.order.reference_id}}` و `{{1.event}}` يجب أن تستبدلها بـ **mappings** من الـ trigger module في Make (الرقم `1` قد يختلف عندك حسب ترتيب الـ Modules).

#### `Body type`
```
Raw
```

#### `Content type`
```
JSON (application/json)
```

#### `Parse response`
```
Yes  ← مهم لرؤية النتيجة في Make
```

### الخطوة 1.3 — Body (نسخ-لصق + استبدل الـ mappings)

استخدم هذا الـ template **حرفياً** ثم استبدل القيم بين `{{ }}` بالـ mappings الفعلية من الـ Salla trigger module:

```json
{
  "event_type":         "order_completed",
  "order_id":           "{{1.order.id}}",
  "order_number":       "{{1.order.reference_id}}",
  "created_at":         "{{1.order.date.date}}",
  "completed_at":       "{{formatDate(now; 'YYYY-MM-DD HH:mm:ss')}}",
  "order_status":       "{{1.order.status.name}}",
  "order_status_slug":  "{{1.order.status.slug}}",
  "currency":           "{{1.order.currency}}",
  "payment_method":     "{{1.order.payment_method}}",
  "subtotal":           {{1.order.amounts.sub_total.amount}},
  "tax":                {{1.order.amounts.tax.amount}},
  "shipping_cost":      {{1.order.amounts.shipping_cost.amount}},
  "total_amount":       {{1.order.amounts.total.amount}},
  "customer_name":      "{{1.order.customer.first_name}} {{1.order.customer.last_name}}",
  "customer_mobile":    "{{1.order.customer.mobile}}",
  "customer_email":     "{{1.order.customer.email}}",
  "items": [
    {{#each 1.order.items as |item|}}
    {
      "sku":      "{{item.sku}}",
      "name":     "{{item.name}}",
      "quantity": {{item.quantity}},
      "price":    { "amount": {{item.amounts.price_without_tax.amount}}, "currency": "{{1.order.currency}}" }
    }{{#unless @last}},{{/unless}}
    {{/each}}
  ],
  "shipping_company":   "{{1.order.shipments.0.courier_name}}",
  "received_from":      "make"
}
```

> 📝 إذا Salla يرجع `packages[]` بدلاً من `items[]` لمتجرك، استبدل `items` بـ `packages` وكرّر الـ structure داخل `packages[].items[]` — المعالج يقبل الشكلين (انظر §3.2 من العقد).

### الخطوة 1.4 — احفظ الـ Module

اضغط **OK** ثم **Save** على السيناريو كاملاً. **لا تُشغّله بعد**.

---

## المرحلة 2️⃣ — إرسال Test Payload واحد

### خياران لإرسال الـ Test:

#### الخيار أ — Run once في Make (موصى به ⭐)

1. في Make → افتح السيناريو → اضغط **Run once**.
2. اذهب إلى Salla → غيّر حالة طلب واحد فقط إلى الحالة المُفعِّلة (التي اخترتها في إعدادات Mezan).
3. Make سيلتقط الـ webhook ويُرسل HTTP request واحد إلى Mezan.
4. ارجع إلى Make → افحص الـ **execution log**:
   - HTTP Module يجب أن يعرض `Status code: 200`.
   - في الـ Output → `ok: true`، `inbox_id: "..."`, `pipeline_stage: "..."`.
   - إذا الـ status code `4xx` → افتح الخطوة 3 لتشخيص السبب.

#### الخيار ب — اختبار يدوي عبر `curl` (للمتقدمين)

من جهازك:

```bash
curl -X POST 'https://mezansalla.com/api/integrations/qoyod/webhook' \
  -H 'Content-Type: application/json; charset=utf-8' \
  -H 'X-Webhook-Token: <الـ token الذي نسخته>' \
  -H 'X-Idempotency-Key: salla:order:TEST-001:order.status.updated' \
  -H 'X-Mezan-Source: manual-test' \
  -d '{
    "event_type":        "order_completed",
    "order_id":          "TEST-001",
    "order_number":      "TEST-001",
    "created_at":        "2026-06-27 10:00:00",
    "completed_at":      "2026-06-27 10:05:00",
    "order_status":      "تم التنفيذ",
    "order_status_slug": "completed",
    "currency":          "SAR",
    "payment_method":    "mada",
    "subtotal":          100.00,
    "tax":               15.00,
    "shipping_cost":     0,
    "total_amount":      115.00,
    "customer_name":     "عميل اختبار",
    "customer_mobile":   "+966500000000",
    "customer_email":    "test@example.com",
    "items": [
      {
        "sku":      "TEST-SKU-001",
        "name":     "منتج اختبار",
        "quantity": 1,
        "price":    { "amount": 100.00, "currency": "SAR" }
      }
    ],
    "received_from": "manual-test"
  }'
```

**النتيجة المتوقعة** (Dry Run مُفعَّل):

```json
{
  "ok": true,
  "inbox_id": "...",
  "pipeline_stage": "RECEIVED",
  "duplicate": false
}
```

> 📝 الـ Status code `200` يعني الـ Webhook وصل وحُفظ. هذا لا يعني نجاح المسار كاملاً — للتحقق من العميل/المنتج/الفاتورة/السند انتقل للمرحلة 3.

---

## المرحلة 3️⃣ — التحقق التفصيلي عبر First Sync Monitor

### الخطوة 3.1 — افتح المراقب

```
https://mezansalla.com/integrations/qoyod/first-sync-monitor
```

فعّل "تحديث تلقائي كل 5 ثوانٍ" من شريط الأدوات. سترى الطلب الجديد يظهر في الأعلى تلقائياً.

### الخطوة 3.2 — افحص هذه الـ 6 نقاط بالترتيب

اضغط على الطلب الجديد لتوسيعه. تحقق من كل خطوة:

| # | ما الذي تتحقق منه؟ | أين تنظر؟ | علامة النجاح | علامة الفشل |
|---|---|---|---|---|
| 1 | **وصول الطلب** | الـ Toolbar — يجب أن يظهر صف جديد | trace_id ظاهر · timestamp = الآن | لا يظهر شيء بعد 10 ثوانٍ → راجع الـ HTTP Module log في Make |
| 2 | **نجاح Legacy Adapter** | Stage History timeline → ابحث عن `RECEIVED → VALIDATED → NORMALIZED` | 3 تحولات متتالية بدون أخطاء | يتوقف عند `FAILED_VALIDATION` → افحص `error` JSON تحت الـ entry |
| 3 | **إنشاء Customer** | بطاقة الخطوة 1 "إنشاء/مطابقة العميل" | Badge أخضر "✓ نجح" + `qoyod_id` ظاهر في الرد | Badge أحمر "✗ فشل" → افتح "تفاصيل" → اقرأ الرد JSON |
| 4 | **إنشاء Product** | بطاقة الخطوة 2 "إنشاء/مطابقة المنتجات" | "✓ نجح" + كل SKU له `qoyod_id` | "✗ فشل" → غالباً `items_missing_sku` أو خطأ من قيود |
| 5 | **إنشاء Invoice** | بطاقة الخطوة 3 "إنشاء الفاتورة في قيود" | "✓ نجح" + قسم 📥 الرد يحتوي على `qoyod_id` + `qoyod_number` (مثلاً `INV-001`) | "✗ فشل" → افحص الـ payload المُرسَل (📤) للتأكد من `tax_id`, `branch_id`, إلخ |
| 6 | **إنشاء Receipt** | بطاقة الخطوة 4 "إنشاء سند القبض في قيود" | "✓ نجح" + `qoyod_id` للسند | "✗ فشل" → غالباً `payment_method_mapping_missing` |

### الخطوة 3.3 — Pipeline Stage النهائي

في رأس الطلب يجب أن ترى:

```
pipeline_stage: COMPLETED   ← النجاح الكامل (مع DRY-RUN badge أصفر)
```

أي قيمة أخرى = هناك مشكلة. القائمة الشائعة:

| pipeline_stage | المعنى | الإجراء |
|---|---|---|
| `COMPLETED` | كل شيء نجح | ✅ جاهز للانتقال |
| `SKIPPED` | حالة الطلب لا تطابق الـ trigger | راجع `business_rules_decision` |
| `DEAD_LETTER` | فشل في إحدى المراحل بعد المحاولات | افحص آخر `stage_history` entry |
| `PARTIAL_FAILURE` | الفاتورة نجحت لكن السند فشل | راجع بطاقة الـ Receipt |
| `NEEDS_ENRICHMENT` | بيانات ناقصة — لا يحدث في Dry Run الصحيح | راجع الـ payload في Make |

---

## 📊 ملخص النتيجة المتوقعة في Dry Run الناجح

```
✅ الطلب وصل لـ Mezan        — pipeline_stage بدأ من RECEIVED
✅ Legacy Adapter        — VALIDATED → NORMALIZED بدون أخطاء
✅ Customer              — DRY:contact:xxxxxxxx (id وهمي للتجربة)
✅ Product               — DRY:product:xxxxxxxx لكل SKU
✅ Invoice               — DRY:invoice:xxxxxxxx
✅ Receipt               — DRY:receipt:xxxxxxxx
✅ pipeline_stage final  — COMPLETED مع DRY-RUN badge
```

> 📝 الـ `DRY:` prefix يعني لم يتم استدعاء Qoyod API فعلياً — فقط بُنيت الـ payloads. هذا هو السلوك المتوقع في Dry Run.

---

## 🚫 ما لن نفعله الآن

- ❌ **لا نوقف Dry Run** — حتى لو نجح الـ Test الأول
- ❌ **لا نُرسل طلباً ثانياً** — مراجعة النتيجة الكاملة أولاً
- ❌ **لا نُلغي ربط Salla المباشر بقيود** — حتى تنجح الـ Dry Run + Go Live
- ❌ **لا ننتقل لأي ميزة جديدة** — هذه مرحلة تشغيل بحتة

---

## 🆘 إذا فشل أي شيء — Troubleshooting

### الـ HTTP Module في Make يعطي 401

| رمز الخطأ | السبب | الإصلاح |
|---|---|---|
| `missing_webhook_token` | header `X-Webhook-Token` فاضي | راجع الـ header في Make — هل القيمة موجودة فعلاً؟ |
| `invalid_webhook_token` | الـ token لا يطابق المخزّن في Mezan | أعد توليده من Mezan → انسخ القيمة الجديدة → ألصقها في Make |

### الـ HTTP Module يعطي 400 + `Invalid JSON`

- افتح `/integrations/qoyod/settings` → قسم Webhook Token → سترى آخر `parse_failure` مع جزء من الـ payload المعطوب
- المشكلة عادة: علامة `"` غير مغلقة في الـ Body، أو `{{mapping}}` لم يُستبدل (تركته كنص حرفي)

### الطلب يصل لـ `RECEIVED` ولا يتقدم

- افتح First Sync Monitor → افحص `business_rules_decision`
- الأكثر شيوعاً: `order_status_slug` لا يطابق أي قيمة في `invoice_trigger_statuses` المُختارة في الإعدادات

### Customer/Product يفشل في Dry Run

- في Dry Run **لا يفشل أي شيء** عادة لأن الـ DryRunQoyodClient يرجع IDs وهمية دائماً
- إذا فشل → غالباً مشكلة في `payment_method_mapping` أو `default_tax_id` غير محفوظ

---

## ✅ بعد نجاح أول طلب

1. **افحص النتيجة لمدة 10-30 دقيقة** — تأكد لا توجد طلبات DEAD_LETTER في خلال نفس الوقت
2. **راجع `qoyod_payloads.invoice` و `qoyod_payloads.receipt`** في First Sync Monitor — تأكد أن جميع الأرقام تطابق ما تتوقعه
3. **اتفق معنا (الفريق التقني) على موعد إيقاف Dry Run** — لن يتم إيقافه تلقائياً
4. **عند الانتقال إلى Go Live**:
   - أوقف Dry Run في الإعدادات
   - أوقف ربط Salla المباشر مع قيود (في Salla → التطبيقات)
   - أرسل طلباً واحداً حقيقياً
   - عُد لـ First Sync Monitor — تأكد أن الـ IDs الآن **حقيقية** (ليست `DRY:`)
5. **تشغيل تدريجي**: اسمح للسيناريو بالعمل مع 5-10 طلبات → راجع → ثم ارفع السقف تدريجياً

---

## 📞 جهة الاتصال

- **الفريق التقني**: Mezan Integrations Platform
- **عند الطوارئ**: أرسل لقطة شاشة من First Sync Monitor (بطاقة الطلب موسَّعة) + Make execution log

---

*هذا الدليل مُحدَّث بتاريخ 2026-06-27. لأي تغيير في عقد الـ Webhook راجع `qoyod-webhook-contract-v1.md`.*
