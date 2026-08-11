# WhatsApp Ingress V1 — استقبال فقط إلى ميزان

## الحالة

- القناة الأولى: WhatsApp Cloud API.
- الاتجاه المسموح: **Inbound فقط**.
- مصدر الحقيقة: ميزان.
- الإرسال إلى العميل: **مغلق**.
- استدعاء GPT: **غير موجود في Webhook**.
- إنشاء طلب أو خصم أو رابط دفع أو تعديل منتج: **غير موجود**.

## المسار

```text
Meta signed webhook
→ WhatsAppInboundAdapter
→ ChannelGateway
→ customer identity vault
→ customers / conversations / conversation_messages
```

المحول يفحص التوقيع ويحوّل Payload الخاص بـWhatsApp إلى عقد القناة الموحد.
البوابة هي التي تربط العميل وتشفّر المحتوى وتحفظه. لا يعرف WhatsApp شيئًا عن
GPT، ولا يعرف GPT لاحقًا شيئًا عن تفاصيل Webhook الخاصة بـMeta.

## Endpoint

```text
GET  /api/customer-intelligence/v1/channels/whatsapp/webhook
POST /api/customer-intelligence/v1/channels/whatsapp/webhook
```

- `GET` مخصص لتحدي Meta ويتحقق من `hub.verify_token` قبل إعادة
  `hub.challenge`.
- `POST` يتحقق من `X-Hub-Signature-256` باستخدام App Secret وعلى bytes جسم
  الطلب الأصلي قبل تحليل JSON.
- Payload غير الموقع لا يدخل البوابة ولا يكتب أي سجل.
- إشعارات الحالة التي لا تحتوي رسالة عميل تقبل بلا إنشاء رسالة وهمية.

## صندوق الوارد الحقيقي — قراءة فقط

```text
GET /api/customer-intelligence/v1/inbox?limit=20&messages_limit=30&offset=0
```

- المسار محمي بجلسة ميزان ومتاح للمالك فقط.
- نطاق المتجر والقناة يُستخرج من جلسة المالك وسجل القناة؛ لا يقبل `user_id`
  أو `merchant_id` من الطلب.
- يفك الخادم تشفير اسم العميل ومحتوى الرسالة في الذاكرة فقط، ويرجع الحقول
  اللازمة للعرض دون رقم الجوال أو ciphertext أو معرفات Meta أو مفاتيح HMAC.
- الاستجابة ترسل `Cache-Control: no-store, private`، وتثبت صراحةً أن الإرسال
  والرد الآلي والتعديلات التجارية كلها مغلقة.
- تدعم القائمة تصفحًا محدودًا عبر `offset` و`next_offset`، وتعيد أعداد
  المحادثات والرسائل الإجمالية بدل عدّ السجلات المعروضة فقط.
- مفتاح `MEZAN_CUSTOMER_INTELLIGENCE_LIVE_INBOX_ENABLED` مستقل عن معاينة
  التحليلات، ويوقف API القراءة الحي وحده عند الحاجة.
- تعرض الوسائط كنوع وتعليق/اسم ملف آمن فقط؛ تنزيل ملف Meta غير مفعّل في V1.
- الواجهة تعرض المحادثات الحقيقية في تبويب «المحادثات»، بينما تبقى تحليلات
  الذكاء في التبويبات الأخرى بيانات معاينة حتى بناء مرحلة التحليل التالية.

مرجع التنفيذ هو توثيق Meta الرسمي لـ[إنشاء WhatsApp Webhook](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/)
و[عقد messages webhook](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages).

## إعداد بيئة النشر

جميع القيم التالية Backend-only ولا توضع في React أو Git:

```text
MEZAN_WHATSAPP_INGRESS_ENABLED=true
MEZAN_WHATSAPP_WEBHOOK_VERIFY_TOKEN=<random callback verification token>
MEZAN_WHATSAPP_APP_SECRET=<Meta app secret>
MEZAN_CHANNEL_BINDING_HMAC_KEY=<dedicated HMAC secret>
MEZAN_CUSTOMER_PII_ENC_KEY=<Fernet encryption key>
MEZAN_CUSTOMER_INTELLIGENCE_LIVE_INBOX_ENABLED=true
```

الافتراضي هو أن `MEZAN_WHATSAPP_INGRESS_ENABLED` مغلق. إذا فُعّل المفتاح من
دون App Secret أو Verify Token يرجع Endpoint حالة `503` ولا يقبل الرسائل.

## ربط رقم WhatsApp بمتجر ميزان

يُنشأ سجل `mezan_customer_channels_v1` بعد اعتماد المالك، ويحتوي:

```text
user_id
merchant_id
channel_id
provider=whatsapp
external_account_key=HMAC(phone_number_id)
status=connected
ingress_enabled=true
egress_mode=disabled
send_allowed=false
ai_auto_reply_allowed=false
```

لا يحفظ `phone_number_id` أو رقم الهاتف أو Access Token في هذا السجل. HMAC
العالمي يستخدم فقط لتحويل Webhook موقع إلى سجل القناة الوحيد، ثم تصبح جميع
القراءات والكتابات التالية مقيدة بـ`user_id + merchant_id + channel_id`.

## ما يحفظ

- معرفات المحادثة والرسالة الخارجية تتحول إلى HMAC tenant-scoped.
- رقم المرسل واسم جهة الاتصال ومحتوى الرسالة تحفظ داخل payload مشفر فقط.
- النص والصوت والصورة والمستند والتفاعل تدخل العقد الموحد.
- ملفات الصورة والصوت لا تنزل في هذه المرحلة؛ يحفظ مرجع Media المشفر فقط.
- تكرار Meta لنفس `message.id` لا ينشئ رسالة ثانية.

## بوابات الاختبار الحقيقي

قبل أول رسالة من رقم أماسي يجب التأكد من:

1. إضافة أسرار Backend في بيئة النشر وعدم ظهورها في logs أو frontend.
2. إنشاء سجل القناة بقيم الإرسال والرد الآلي مغلقة.
3. نجاح تحدي Meta على Endpoint المنشور.
4. نجاح رسالة اختبار موقعة وظهور سجل واحد مشفر في ميزان.
5. إظهار الرسالة للمالك من API القراءة الحي، من دون أي زر إرسال.
6. تشغيل GPT لاحقًا كمقترح مستقل يحمل `execution_allowed=false`.

لا يرفع نجاح الاستقبال صلاحية الإرسال تلقائيًا.
