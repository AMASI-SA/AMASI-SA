# WhatsApp Ingress V1 — Meta مباشر أو 360dialog Coexistence

## الحالة

- النقل المتاح: **Meta Cloud API مباشر** أو **360dialog Coexistence**.
- اختيار 360dialog لا يحذف مسار Meta المباشر؛ لكل نقل Endpoint وإعداد مستقل.
- الاتجاه التنفيذي المسموح من ميزان: **استقبال فقط**؛ لا يوجد عميل إرسال في هذه النسخة.
- في Coexistence، رد الموظف من تطبيق WhatsApp Business يدخل ميزان كدليل
  `outbound/employee` عبر `smb_message_echoes`، وليس كإرسال صادر من ميزان.
- مصدر الحقيقة: ميزان.
- الإرسال إلى العميل: **مغلق**.
- استدعاء OpenAI: **غير موجود في Webhook**؛ يحدث فقط بطلب موظف صريح لإنشاء
  مسودة، وتبقى المسودة مشفرة وبانتظار المراجعة.
- إنشاء طلب أو خصم أو رابط دفع أو تعديل منتج: **غير موجود**.

## المسار

```text
Meta signed webhook أو 360dialog authenticated webhook
→ WhatsAppInboundAdapter أو D360InboundAdapter
→ ChannelGateway
→ customer identity vault
→ customers / conversations / conversation_messages
```

المحول يفحص التوقيع ويحوّل Payload الخاص بـWhatsApp إلى عقد القناة الموحد.
البوابة هي التي تربط العميل وتشفّر المحتوى وتحفظه. لا يعرف WhatsApp شيئًا عن
GPT، ولا يعرف GPT لاحقًا شيئًا عن تفاصيل Webhook الخاصة بـMeta.

## Endpoints النقل

```text
GET  /api/customer-intelligence/v1/channels/whatsapp/webhook
POST /api/customer-intelligence/v1/channels/whatsapp/webhook
POST /api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook
```

- `GET` مخصص لتحدي Meta ويتحقق من `hub.verify_token` قبل إعادة
  `hub.challenge`.
- `POST` يتحقق من `X-Hub-Signature-256` باستخدام App Secret وعلى bytes جسم
  الطلب الأصلي قبل تحليل JSON.
- Payload غير الموقع لا يدخل البوابة ولا يكتب أي سجل.
- إشعارات الحالة التي لا تحتوي رسالة عميل تقبل بلا إنشاء رسالة وهمية.
- مسار Meta المباشر و360dialog كلاهما يطبع `smb_message_echoes` كـرد موظف
  صادر، ويطبق حالات `sent/delivered/read/failed` على الرسالة المعروفة فقط؛
  لذلك لا يبقى اقتراح قديم بعد رد الموظف عند تبديل مزود النقل.
- Endpoint الخاص بـ360dialog يستخدم Basic Auth خاصًا بكل قناة، ثم يطابق
  `phone_number_id` مع الربط المخزن في الخادم قبل أي كتابة.
- بيانات اعتماد قناة لا تستطيع توجيه حدث إلى قناة أخرى، ولا يُستخدم
  `D360-API-KEY` ككلمة مرور للـWebhook.
- النقلان يفرضان حد جسم مقداره 1 MiB أثناء القراءة، ويعيدان `413` عند تجاوزه.
- لا يوجد Endpoint باسم `send` في أي من مساري النقل.

## صندوق الوارد الحقيقي — قراءة فقط

```text
GET /api/customer-intelligence/v1/inbox?limit=20&messages_limit=30&offset=0
```

- المسار محمي بجلسة ميزان. يراه المالك، أو موظف يحمل صلاحية
  `customer_intelligence.inbox.read`.
- الموظف يرى الطابور غير المسند والمحادثات المسندة إليه فقط؛ ولا يرى محادثات
  مسندة لموظف آخر. تبقى تبويبات المعاينة الإدارية للمالك فقط.
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
- الواجهة تعرض رسائل العميل وردود الموظف الحقيقية في تبويب «المحادثات».
- اقتراح الرد كيان صريح مستقل؛ لا يُستنتج من أول رسالة صادرة أو من Echo
  الموظف. يستطيع الموظف إنشاءه يدويًا وتعديله ومراجعته أو رفضه أو تصعيده،
  لكن زر «اعتماد وإرسال» يبقى مقفلًا ولا يرسل شيئًا في هذه النسخة.

مرجع التنفيذ هو توثيق Meta الرسمي لـ[إنشاء WhatsApp Webhook](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/)
و[عقد messages webhook](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages)،
وتوثيق 360dialog الرسمي لـ[Webhook](https://docs.360dialog.com/docs/messaging/webhook)
و[Coexistence](https://docs.360dialog.com/docs/resources/phone-numbers/coexistence).

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

### إعداد 360dialog الاختياري

```text
MEZAN_360DIALOG_INGRESS_ENABLED=true
MEZAN_360DIALOG_WEBHOOK_BINDINGS_JSON=[{"username":"<unique>","password":"<random>","phone_number_id":"<meta phone id>","channel_id":"<mezan channel id>"}]
```

- المتغيران Backend-only، و360dialog مغلق افتراضيًا.
- لا تُحفظ القيم الحقيقية في Git أو React أو السجلات.
- يجب أن يكون اسم المستخدم فريدًا لكل رقم، وكلمة المرور عشوائية مستقلة، وأن
  يطابق `channel_id` سجل القناة الموثوق في ميزان.
- يستخدم إنشاء اقتراح الرد اتصال OpenAI الموجود أصلًا في بيئة ميزان عبر
  `OPENAI_API_KEY`. يمكن اختيار نموذج مستقل عبر
  `MEZAN_OPENAI_CUSTOMER_REPLY_MODEL`، وإلا يستخدم إعداد نموذج ميزان الحالي.

### الرجوع إلى Meta المباشر

1. أوقف `MEZAN_360DIALOG_INGRESS_ENABLED`.
2. افصل Business Platform/مزود 360dialog من إعداد حساب WhatsApp Business عند
   إنهاء الخدمة حسب إجراءات Meta و360dialog.
3. أعد إعداد Meta App والـWebhook المباشر، ثم فعّل
   `MEZAN_WHATSAPP_INGRESS_ENABLED` مع Verify Token وApp Secret الصحيحين.
4. أبقِ سجل القناة نفسه وسياسات `send_allowed=false` و
   `ai_auto_reply_allowed=false`؛ النقل قابل للاستبدال ونواة المحادثات لا
   تعتمد على 360dialog.

لا تُفعّل النقلين لنفس الرقم في التشغيل الطبيعي. حماية التكرار موجودة، لكنها
ليست بديلًا عن اختيار Webhook فعّال واحد للرقم.

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

### إنشاء الربط المباشر بعد تسجيل الرقم في Meta

لا يُنشأ سجل القناة يدويًا في MongoDB. بعد أن تعرض Meta قيمة
`Phone Number ID` الحقيقية، تُمرر داخل جلسة الطرفية فقط عبر متغير مؤقت، ثم
يُشغّل أمر التهيئة أولًا دون كتابة:

```bash
read -rsp 'Meta Phone Number ID: ' MEZAN_WHATSAPP_PHONE_NUMBER_ID
export MEZAN_WHATSAPP_PHONE_NUMBER_ID
echo
python scripts/provision_whatsapp_channel.py
```

الوضع الافتراضي `dry_run`، ويشترط وجود مالك واحد ومتجر سلة واحد متصل ومفتاح
`MEZAN_CHANNEL_BINDING_HMAC_KEY` مستقل. لا يطبع الأمر قيمة `Phone Number ID`
ولا يخزنها؛ يعرض بصمة غير قابلة للعكس فقط. عند ظهور خطة صحيحة، يطبقها المشغل
بشكل صريح:

```bash
python scripts/provision_whatsapp_channel.py --apply
```

لا يستبدل الأمر أي قناة موجودة ولا يغير هويتها. إذا كانت القناة التجريبية
موجودة، يخطط لإضافة ربط Meta الحقيقي كسجل مستقل بجانبها فقط بعد بوابة صريحة:

```bash
python scripts/provision_whatsapp_channel.py --allow-additional-channel
python scripts/provision_whatsapp_channel.py --allow-additional-channel --apply
```

وجود أكثر من مالك أو أكثر من متجر متصل يتطلب تحديد النطاق صراحةً عبر
`--owner-id` و`--merchant-id`. أي تعارض عالمي أو سجل غير آمن أو تغير في
نطاق القنوات بين التخطيط والتنفيذ يوقف الأمر دون كتابة. أثناء التطبيق تُحجز
مهلة ذرية قصيرة لنطاق المالك والمتجر والمزود، لذلك لا تستطيع عمليتا تهيئة
متزامنتان تجاوز بوابة القناة الإضافية. إعادة تشغيل الأمر
لنفس ربط Meta الصحيح تكون `noop` ولا تنفذ كتابة. بعد نجاح أول رسالة حقيقية
فقط يمكن إيقاف السجل التجريبي بإجراء منفصل ومدقق؛ لا تحذفه أداة التهيئة.
بعد الانتهاء تُزال قيمة
`MEZAN_WHATSAPP_PHONE_NUMBER_ID` من جلسة الطرفية؛ لا تضاف إلى أسرار التشغيل
الدائمة لأن Webhook يحتاج الربط غير القابل للعكس فقط.

```bash
unset MEZAN_WHATSAPP_PHONE_NUMBER_ID
```

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
6. إنشاء اقتراح يدوي في ميزان والتأكد أنه `pending_approval` وأن فتح الشاشة
   أو تعديل النص أو ضغط Enter لا يستدعي أي إرسال.
7. في Coexistence، أرسل ردًا من تطبيق WhatsApp Business وتأكد أنه يظهر كـ
   «رد الموظف من واتساب» ويلغي أي اقتراح قديم دون تشغيل الذكاء.
8. اختبر إيقاف علم 360dialog وتأكد أن Endpoint يعيد `404` وأن مسار Meta
   المباشر ما زال موجودًا ومستقلًا، ثم أرسل Echo موظف وحالة تسليم عبر Meta
   وتأكد أنهما يمران عبر البوابة المشتركة ويلغيان الاقتراح القديم.

لا يرفع نجاح الاستقبال صلاحية الإرسال تلقائيًا.
