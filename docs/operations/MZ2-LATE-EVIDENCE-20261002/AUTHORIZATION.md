MZ2_NEW_C_EVIDENCE_CONTRACT — CONTRACT APPROVED FOR IMPLEMENTATION

أعتمد الآن عقد الإثبات المقترح الموثق في:

docs/operations/MZ2-REBASE-AUDIT-20261002/NEW-C-EVIDENCE-CONTRACT.md

وأفوّضك بتنفيذه، مع الالتزام الكامل بالحدود التالية.

## الهدف

إضافة مسار موثوق لإرفاق دليل التسليم المالي بعد Delivered، بحيث يحافظ على:

- إلزامية Track F financial delivery proof.
- ختم C3 التاريخي وعدم تعديله.
- التاريخ الأصلي للتسليم.
- هوية المالك.
- هوية المندوب.
- الطلب canonical.
- assignment_id.
- collection_id.
- هوية الدليل وبصمته.
- هوية الرافع.
- هوية المراجع/المعتمد.
- أوقات التسليم والرفع والإرفاق والاعتماد كلٌ بشكل مستقل.

## القاعدة الأساسية

الإرفاق اللاحق لا يعدّل C3.

إذا كان C3 يحتوي مرجعًا مختومًا:
- يبقى كما هو.
- لا يُستبدل.
- لا يُعاد ختمه.
- لا يُعاد بناؤه.

إذا لم يكن C3 يحتوي المرجع:
- يسجل ذلك صراحة.
- لا يتم اختلاق سجل تاريخي.
- لا يُدّعى أن الدليل كان موجودًا وقت التسليم.

الإرفاق اللاحق يكون Evidence Event مستقلًا ومضافًا append-only.

## صلاحيات الإرفاق والاعتماد

التنفيذ المقترح:

- المندوب يرفع دليل التسليم.
- محاسب مخوّل يراجعه ويعتمده.

لكن لا تنشئ صلاحيات جديدة بشكل حر.

أعد استخدام نظام الصلاحيات/الهويات الموجود.

إذا كان تحديد صلاحية المحاسب المعتمِد غير موجود في العقود الحالية:
- توقف عند هذه الفجوة.
- لا تخترع Role أو Permission اقتصاديًا جديدًا.

## التحقق

يجب التحقق من:

- owner/user_id
- order canonical identity
- driver identity
- assignment_id
- collection_id
- delivery identity
- evidence content hash
- uploader identity
- reviewer identity
- timestamps

والتعارض يجب أن يكون FAIL CLOSED.

التكرار المطابق يجب أن يكون idempotent ويعيد نفس النتيجة دون إنشاء سجل مكرر.

## الفصل عن المحاسبة

الإرفاق أو الاعتماد وحده:

- لا ينشئ Journal Entry.
- لا ينشئ Bank movement.
- لا يغير COD receivable.
- لا يغير Driver receivable/payable.
- لا يغير الاعتراف المحاسبي.
- لا يغير توقيت الاعتراف.
- لا ينشئ Writer ماليًا جديدًا.

استخدم الكاتب المالي الموجود عند استهلاك الدليل لاحقًا.

## C3

لا تغيّر:
- C3 sealed evidence
- C3 reference
- historical timestamps
- historical delivery state

ويجب أن يبقى:

Delivered COD → Driver COD responsibility = كامل المبلغ مرة واحدة.

## نقطة مهمة جدًا

يجب تحديد سياسة واضحة للحالة:

C3 عند Delivered بدون delivery-proof reference
ثم لاحقًا:
Delivery evidence attached + approved

يجب ألا يتم تعديل C3 التاريخي.

بل يصبح لدينا:

Historical C3 record
+
Later Evidence Attachment / Approval event

ويكون المسار المالي قادرًا على معرفة أن الدليل تمت إضافته لاحقًا، مع الحفاظ على توقيت الاعتراف الأصلي.

إذا كان العقد المالي الحالي لا يسمح باستهلاك evidence مضاف بعد الاعتراف، لا تتحايل؛ وثّقها كفجوة منفصلة بدل تغيير الاعتراف بأثر رجعي.

## A/B bug

بعد عقد الإثبات، أصلح خلل الـA/B المستقل:

رفع إيصال بديل يرفض Delivered بينما إعادة إرسال الدفع تشترط Delivered.

الإصلاح يجب أن يكون Wiring فقط.

لا تعتبر إصلاحه بديلًا عن delivery financial proof.

## Rebase

بعد اكتمال عقد الإثبات وإصلاح A/B:

لا تعمل Rebase الآن على PR #1237 نفسه.

احتفظ بـ:

48bb39d983fe7003bc972c624a1508655a16f570

كنقطة مرجعية محفوظة.

ثم أنشئ فرع Integration جديد فوق Production الحالي:

83363097d48e034dc7140a60c290efc684e1ffde

وانقل التغييرات المطلوبة بعناية.

يجب حل التعارضين:

- backend/store_delivery_driver_app_routes.py
- backend/tests/test_store_delivery_accounting.py

مع الحفاظ على:
- C3
- Track F
- PR #1238 الحالي
- جميع إصلاحات MZ2 السابقة.

لا force push.

## الاختبارات

بعد التنفيذ:

1. focused tests لعقد evidence.
2. idempotency.
3. duplicate evidence.
4. conflicting evidence → FAIL CLOSED.
5. wrong owner/driver/order → reject.
6. late attachment بعد Delivered.
7. reviewer approval.
8. C3 remains immutable.
9. no financial journal from attachment/approval.
10. Track F consumption.
11. A/B receipt resubmission.
12. Real Mongo.
13. CI.

ثم Full Regression وBuild بعد إعادة التأسيس، وليس قبلها.

## Production safety

يبقى مطلقًا:

Production financial writes = 0

ممنوع:
- Production financial mutation
- Opening Post
- Activation
- تغيير write-control
- Deploy
- Merge إلى Production

## بعد اكتمال التنفيذ

أرسل لي:

- Contract implementation summary
- الملفات المعدلة
- C3 immutability proof
- evidence lifecycle
- permissions proof
- idempotency proof
- financial no-write proof
- A/B fix
- focused tests
- Real Mongo
- CI
- Production SHA
- Integration HEAD/TREE
- divergence/rebase result
- Remaining blockers

ولا تعتبر Release Readiness PASS حتى نكمل:
- Full Regression
- Build
- Real Mongo
- SSOT
- Security
- CodeQL
- Full Business UAT
- Smoke B حسب عقد Release
- Release Intent النهائي

إذا ظهرت أثناء التنفيذ فجوة تتطلب Writer ماليًا جديدًا أو عقدًا اقتصاديًا جديدًا غير الموجود، توقف عندها فورًا ولا تخترع حلًا.

الحالة الحالية حتى اكتمال هذا التفويض:

MZ2_RELEASE_READINESS = NO
PRODUCTION_WRITES = 0
WRITE_CONTROL = UNCHANGED
MERGE = NO
DEPLOY = NO
OPENING POST = NO
ACTIVATION = NO

ابدأ بتنفيذ عقد الإثبات.

## Follow-up authorization

The user explicitly authorized reuse of accounting.shipping.contracts.review for late delivery evidence attachment review only. No new role or permission, posting grant, journal, balance/control change or C3 history mutation is authorized. Identical retries are idempotent; conflicts fail closed.
