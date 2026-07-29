# Google Integrations V2 — Production Setup

## الهدف

تفعيل زر **ربط حساب Google** داخل مركز التطبيقات والتكاملات في Mezan OS V2 لربط حساب Google واحد واكتشاف الخدمات المتاحة منه:

- Google Analytics 4
- Google Search Console
- Google Merchant Center
- Google Ads

الربط في هذه المرحلة للقراءة والاكتشاف فقط. لا توجد عمليات إنشاء أو تعديل أو حذف للحملات أو المنتجات أو إعدادات Google.

## إعداد Google Cloud

1. أنشئ أو اختر Google Cloud Project مخصصًا لميزان.
2. فعّل APIs التالية:
   - Google Analytics Admin API
   - Search Console API
   - Merchant API
   - Google Ads API
3. جهّز OAuth consent screen.
4. أنشئ OAuth 2.0 Client من نوع Web application.
5. أضف رابط Callback الإنتاجي حرفيًا إلى Authorized redirect URIs:

```text
https://<BACKEND-HOST>/api/integrations-v2/google/callback
```

يجب أن يطابق هذا الرابط قيمة `GOOGLE_OAUTH_REDIRECT_URI` دون أي اختلاف.

## متغيرات Backend المطلوبة

```text
GOOGLE_OAUTH_CLIENT_ID=<google-oauth-client-id>
GOOGLE_OAUTH_CLIENT_SECRET=<google-oauth-client-secret>
GOOGLE_OAUTH_REDIRECT_URI=https://<BACKEND-HOST>/api/integrations-v2/google/callback
GOOGLE_TOKEN_ENC_KEY=<fernet-key>
JWT_SECRET=<existing-strong-jwt-secret>
FRONTEND_URL=https://<MEZAN-FRONTEND-HOST>
```

لـ Google Ads فقط:

```text
GOOGLE_ADS_DEVELOPER_TOKEN=<developer-token>
GOOGLE_ADS_API_VERSION=v25
```

إن لم يوجد Developer Token، يظل Google OAuth صالحًا للخدمات الأخرى وتظهر ملاحظة منفصلة على بطاقة Google Ads.

## إنشاء مفتاح تشفير Token

يجب إنشاء مفتاح Fernet مرة واحدة وحفظه كسر إنتاجي، وعدم تغييره بعد بدء الربط إلا عبر دورة تدوير مفاتيح مدروسة:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

يدعم النظام مفتاحًا قديمًا اختياريًا أثناء التدوير:

```text
GOOGLE_TOKEN_ENC_KEY_OLD=<previous-fernet-key>
```

## حدود الأمان

- الربط متاح للمالك فقط.
- OAuth state موقّع وقصير العمر ويُستخدم مرة واحدة.
- Access Token وRefresh Token يحفظان مشفرين في مجموعة خاصة منفصلة.
- لا تعاد الأسرار إلى Frontend أو مجموعات العرض العامة.
- لا يتم اعتبار أي صلاحية ممنوحة ما لم تعدها Google ضمن الصلاحيات الفعلية.
- عمليات Google Ads وMerchant Center الكتابية محجوبة بسياسة Mezan V2 حتى لو كان OAuth scope أوسع من القراءة.
- إلغاء الربط والحذف غير مفعلين في هذه المرحلة.

## اختبار القبول بعد النشر

1. افتح `/integrations-v2` كمالك.
2. اضغط **ربط حساب Google** على أي بطاقة Google.
3. وافق على الخدمات المطلوبة.
4. يجب أن تعود إلى `/integrations-v2?google=connected`.
5. تحقق من ظهور الحسابات المكتشفة في البطاقات المناسبة.
6. شغّل **فحص محلي** لكل خدمة مرتبطة.
7. تحقق أن سلة وقيود والطلبات وإدارة التجهيز لم تتغير.
