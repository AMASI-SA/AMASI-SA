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
| `X-Idempotency-Key` | `salla:order:{{1.data.reference_id}}:{{1.event}}:{{1.data.status.slug}}` |
| `X-Mezan-Source` | `make.com` |

> 💡 الـ `{{1.data.reference_id}}` و `{{1.event}}` و `{{1.data.status.slug}}` يجب أن تستبدلها بـ **mappings** من الـ trigger module في Make (الرقم `1` قد يختلف عندك حسب ترتيب الـ Modules).
>
> 🔑 **مهم — تحديث 2026-02-27:** الـ `:{{1.data.status.slug}}` في نهاية الـIdempotency Key يضمن أن تغيير حالة نفس الطلب (مثلاً `under_review` → `completed`) ينتج Key مختلف. هذا يسمح للطلب بدخول النظام مرتين كرسالتين منفصلتين (الأولى SKIPPED، الثانية تُعالَج). بدون هذه الإضافة، Salla webhook الثاني يُعتبر duplicate ويُسقَط بصمت — وهي مشكلة الطلب `268452656` التي ظهرت في الإنتاج. حماية تكرار الفاتورة محفوظة عبر `trigger_once_only` في طبقة قيود.

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

> 📌 **ملاحظة Salla Trigger في Make**: الـ module يفكّ الـ envelope `{event, data: {...}}` تلقائياً ويعطيك الحقول تحت `data.*` (وليس `order.*`).

استخدم هذا الـ template **حرفياً** ثم استبدل `{{1.x}}` بـ mappings فعلية من panel الـ Salla trigger module (الرقم `1` قد يختلف عندك حسب ترتيب الـ Modules):

```json
{
  "event_type":        "order_completed",
  "order_id":          "{{1.data.id}}",
  "order_number":      "{{1.data.reference_id}}",
  "created_at":        "{{1.data.date.date}}",
  "completed_at":      "{{formatDate(now; 'YYYY-MM-DD HH:mm:ss')}}",
  "order_status":      "{{1.data.status.name}}",
  "order_status_slug": "{{1.data.status.slug}}",
  "currency":          "{{1.data.currency}}",
  "payment_method":    "{{1.data.payment_method}}",
  "subtotal":          {{1.data.amounts.sub_total.amount}},
  "tax":               {{1.data.amounts.tax.amount}},
  "shipping_cost":     {{1.data.amounts.shipping_cost.amount}},
  "total_amount":      {{1.data.amounts.total.amount}},
  "customer_name":     "{{1.data.customer.first_name}} {{1.data.customer.last_name}}",
  "customer_mobile":   "{{1.data.customer.mobile}}",
  "customer_email":    "{{1.data.customer.email}}",
  "items": [
    {{map(1.data.items; "{""sku"":""" + sku + """,""name"":""" + name + """,""quantity"":" + quantity + ",""price"":{""amount"":" + amounts.price_without_tax.amount + ",""currency"":""" + amounts.price_without_tax.currency + """}}"; ",")}}
  ],
  "shipping_company":  "{{1.data.shipments.0.courier_name}}",
  "received_from":     "make"
}
```

> 📝 إذا Salla يرجع `packages[]` بدلاً من `items[]` لمتجرك، استبدل `data.items` بـ `data.packages` وكرّر الـ structure داخل `packages[].items[]` — المعالج يقبل الشكلين (انظر §3.2 من العقد).

### 1.3.1 — جدول Mapping الكامل (الحقل في الـ Body → الحقل في Salla Trigger)

| الحقل في الـ Body | المصدر من Salla Trigger | إلزامي؟ | ملاحظات |
|---|---|---|---|
| `event_type` | ثابت `"order_completed"` | ✅ | نص ثابت — لا mapping |
| `order_id` | `1.data.id` | ✅ | الرقم الداخلي الطويل |
| `order_number` | `1.data.reference_id` | ✅ | الرقم القصير الظاهر للعميل |
| `created_at` | `1.data.date.date` | ✅ | بصيغة `YYYY-MM-DD HH:mm:ss` |
| `completed_at` | `formatDate(now; 'YYYY-MM-DD HH:mm:ss')` | ✅ | الوقت الحالي للسيناريو |
| `order_status` | `1.data.status.name` | ✅ | الاسم العربي الظاهر |
| `order_status_slug` | `1.data.status.slug` | ✅ | **slug** هو ما يطابقه ميزان |
| `currency` | `1.data.currency` | ✅ | `SAR` فقط |
| `payment_method` | `1.data.payment_method` | ✅ | يجب أن يكون مربوطاً في Payment Method Mapping |
| `subtotal` | `1.data.amounts.sub_total.amount` | ✅ | **رقم بدون اقتباس** |
| `tax` | `1.data.amounts.tax.amount` | ✅ | **رقم بدون اقتباس** |
| `shipping_cost` | `1.data.amounts.shipping_cost.amount` | اختياري | `0` إذا غير موجود |
| `total_amount` | `1.data.amounts.total.amount` | ✅ | **> 0** وإلا يُرفض الطلب |
| `customer_name` | `1.data.customer.first_name` + ` ` + `1.data.customer.last_name` | موصى به | بدونه يصبح "Guest" |
| `customer_mobile` | `1.data.customer.mobile` | موصى به | بصيغة `+9665XXXXXXXX` |
| `customer_email` | `1.data.customer.email` | موصى به | حروف صغيرة |
| `items[].sku` | `1.data.items[].sku` | ✅ | **يجب أن يكون غير فارغ لكل عنصر** |
| `items[].name` | `1.data.items[].name` | ✅ | اسم المنتج |
| `items[].quantity` | `1.data.items[].quantity` | ✅ | رقم |
| `items[].price.amount` | `1.data.items[].amounts.price_without_tax.amount` | ✅ | السعر بدون ضريبة |
| `items[].price.currency` | `1.data.items[].amounts.price_without_tax.currency` | ✅ | عادة `SAR` |
| `shipping_company` | `1.data.shipments.0.courier_name` | اختياري | فقط للأرشفة |
| `received_from` | ثابت `"make"` | ✅ | نص ثابت |

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
  -H 'X-Idempotency-Key: salla:order:TEST-001:order.status.updated:completed' \
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

---

## Iter-273 — Totals Guard (P0)

Production order `268670571` slipped through to `PRODUCT_RESOLVED`
with a truncated `items[]` (one row, unit_price=5) while the order's
own `subtotal=105` and `total=131.60`. Make.com's `map()` step had
silently dropped most of the line items.

### What changed in Mezan
A **Totals Guard** now runs immediately after the `NORMALIZED` stage
and BEFORE any Qoyod-bound work (customer/product/invoice). It
checks:

1. `sum(items[].unit_price × items[].quantity) ≈ subtotal`
   (within ±0.05 SAR — accepts both tax-inclusive and tax-exclusive
   conventions).
2. `subtotal + tax_amount + shipping_amount − discount_amount ≈ total_amount`.

If either check fails, the row goes to **`DEAD_LETTER`** with one
of these error codes (no auto-retry):

| Error code                  | Meaning                                      |
|-----------------------------|----------------------------------------------|
| `line_items_incomplete`     | items_sum ≪ subtotal — Make dropped rows.   |
| `line_items_total_mismatch` | items_sum ≠ subtotal in either direction.   |
| `order_total_mismatch`      | header math doesn't reconcile to declared total. |

### Required Make.com fix
The mapper that builds `data.items[]` MUST emit **every** line of
the Salla order, not a single mapped row.

**Wrong** (causes Iter-273 refusal):
```text
[items] = {{1.data.items[1]}}            ← single object, indexed!
```

**Right**:
```text
[items] = {{1.data.items}}               ← whole array, native
```

Or, if Make's `map()` is required for shape massage, wrap with an
**Array Aggregator** that joins ALL mapped objects:
```
Modules
 ├─ Salla webhook (HTTP webhook)
 ├─ Iterator    on `1.data.items[]`
 ├─ Set Variable / Transform per item (whatever shape changes you need)
 └─ Array Aggregator (target source: iterator step, target structure: items[])
 └─ HTTP POST → /api/integrations/qoyod/webhook
```

### Where to verify on a live order
After receiving a fresh webhook on Mezan:

1. Open `🩺 مراقب أول مزامنة` → click the failing row.
2. The `totals_guard` block shows:
   - `items_count`, `items_sum_excl`, `items_sum_incl`
   - `subtotal`, `tax_amount`, `shipping_amount`, `discount_amount`
   - `shortfall` (= subtotal − items_sum_excl)
   - `parsed_items[]` — what we actually received per SKU
3. Compare against the Salla order details page to identify which
   SKUs Make dropped.

### Backfill caveat
Iter-273 ships the guard with `default_tolerance = 0.05 SAR`. Any
historical inbox row whose totals were already mismatched will hit
the guard on the next reprocess. This is intentional: never POST a
partial invoice to Qoyod, even on a retry.
