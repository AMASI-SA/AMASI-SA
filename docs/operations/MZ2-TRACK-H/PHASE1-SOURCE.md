Source: https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5912052425

TRACK_D — PHASE 1 SOURCE AUDIT; IMPLEMENTATION BLOCKED BY #1205 PRODUCTION BASE

125 mapping rows: SUPPORTED 28; SUPPORTED_WITH_UI_ADAPTER 43; READ_ONLY 25; LOCKED 4; GAP 25. Static source audit only. No product changes, no tests/screenshots claimed, no merge/deploy or live financial writes. Fresh GitHub check: #1205 still open/unmerged; Production still 18432683a549bf50a2c994fcb45113115f231cfb. Local report uncommitted; task branch not pushed. Full mapping follows.

# تدقيق واجهات المحاسبة في ميزان 2

تم تدقيق الصور الست مقابل كود فرع Production في 30 سبتمبر 2026. هذه نتيجة Phase 1 على المصدر الحالي، وليست موافقة على تفعيل المحاسبة أو ادعاء اكتمال الواجهات. التنفيذ النهائي واختبارات الواجهة مؤجلان لأن PR #1205 ما زال مفتوحًا وغير مدمج عند التحقق من GitHub.

## نقطة الأساس والحدود

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/track-d-accounting-ui`.
- HEAD / Production base: `18432683a549bf50a2c994fcb45113115f231cfb`.
- HEAD tree: `ea01d96456f58bb36d19fcfdaf373be30e76c952`؛ هذا Tree المصدر قبل إضافة هذا التقرير غير الملتزم.
- Worktree: `C:/Users/amasi/track-d-accounting-ui`، معزول عن تعديلات Qoyod الموجودة في نسخة المستخدم.
- Dependency: [PR #1205](https://github.com/AMASI-SA/AMASI-SA/pull/1205)، حالته `open`, `merged=false`, `merged_at=null`. لا يمثل `merge_commit_sha` التجريبي في GitHub دمجًا فعليًا.
- [آخر checkpoint للمشرف الذي قرأه التدقيق](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5911752472) يذكر حظر الدمج لحين معالجة dependency security مستقلة. هذا سياق مسجل، وليس نتيجة تشغيل CI جديد في هذه المهمة.
- لا يوجد إثبات Production يتضمن #1205؛ لا تُستخدم أدلة نشر #1202 بدلًا منه. يلزم تحديث الأساس وإعادة تدقيق العقود بعد دمج ونشر #1205 بواسطة المسار المخوّل.
- لا Merge ولا Deploy ولا تفعيل P01/P02/G47 ولا أي كتابة مالية حية. لم تُعدّل ملفات frontend/backend أو Release Intent.
- المرجع البصري: الملفات الست المؤرخة 30 سبتمبر 2026، المنتهية بـ `م-1.png` إلى `م-6.png`، كما أرسلها المستخدم. أرقامها، أسماؤها، نسب النمو وتواريخها بيانات تصميم فقط.

## معنى التصنيف

| التصنيف | المعنى |
| --- | --- |
| SUPPORTED | عقد backend ووصلة واجهة موجودان؛ يلزم احترام الصلاحيات وحالة الكتابة وقت التنفيذ. لا يعني أن الكتابة مسموحة الآن. |
| SUPPORTED_WITH_UI_ADAPTER | يمكن إعادة العرض أو الفلترة أو التجميع من عقود موجودة دون منطق محاسبي جديد. |
| READ_ONLY | عرض مصدر حقيقي مع حدود نطاقه، دون تحويله إلى صلاحية كتابة أو مصدر رصيد آخر. |
| LOCKED | قدرة موجودة لكن استعمالها المالي مقفول ضمن المهمة؛ المعاينة منفصلة عن الاعتماد. |
| GAP | لا يوجد عقد مناسب مثبت لهذا العنصر في نطاق MZ2؛ لا endpoint جديد ولا بديل legacy ضمن التنفيذ. |

في الجداول، `A` تعني `/api/financial-provider-apps/accounting-module`، و`D=A/daily-movements`، و`S=A/settlements`، و`P=A/payroll`، و`H=A/shipping-p02`، و`R=A/reports`. طلبات `api` في frontend تحذف بادئة `/api` فقط. أسماء الملفات أدناه مفاتيح للمراجع المثبتة في نهاية التقرير.

## الحركات المالية اليومية

| عنصر التصميم | القدرة والمسار الفعلي | التصنيف | قرار الواجهة والحدود |
| --- | --- | --- | --- |
| اختيار نوع العملية وتصنيفها إلى مجموعات | خدمات `accountingModule.js`؛ daily، payroll، settlements، shipping | SUPPORTED_WITH_UI_ADAPTER | محدد نوع يفتح نموذج المحرك المناسب. لا writer عام ولا إرسال حقول debit/credit من نموذج الحدث. |
| إيداع بنكي وارد | `POST D/manual-incoming`، `createAccountingManualIncomingMovement` | SUPPORTED | دليل حركة بنكية وليس قيد إيداع حرًا؛ `request_id` ثابت لإعادة المحاولة لنفس البيانات. يقبل حساب بنك فعليًا، فلا تعميم على الصناديق. |
| سحب بنكي خارج | `POST D/manual-outgoing`، `createAccountingManualOutgoingMovement` | SUPPORTED | حفظ دليل ثم تصنيف صريح مستقل؛ لا وصف الحفظ بأنه ترحيل مكتمل. |
| البنك، المبلغ، الطرف، التاريخ، المرجع، الملاحظات | `GET D/context` + عقود manual incoming/outgoing | SUPPORTED | إظهار حقول العقد حسب الاتجاه. لا تخمين بيانات الطرف. |
| رفع كشف البنك | `POST D/upload`، `uploadAccountingDailyMovements` | SUPPORTED | XLSX فقط؛ 8 MiB و5000 صف في العقد الحالي. لا وعد PDF أو صور أو MT940. |
| مصروف عام | `POST D/{id}/classify-outgoing`, `action=expense` | SUPPORTED | التصنيف يرحّل فعليًا؛ يحتاج `accounting.journals.manual_create`، ولا يُستدعى بمجرد تغيير القائمة. |
| إيجار، اشتراك، بنزين، فئات أخرى | `GET D/context.expense_categories` | SUPPORTED_WITH_UI_ADAPTER | اختيار الأكواد التي يعيدها المصدر؛ insurance ليس فئة مضمونة، يُظهر فقط إن أعاده المصدر. الفئات المحجوزة لا تمر كمصروف عام. |
| سداد مورد | نفس مسار classify-outgoing، `action=supplier_payment`, `supplier_id` | SUPPORTED | يحتاج `accounting.settlements.post` وذمة المورد الحقيقية؛ ليس فاتورة شراء أو قيدًا يدويًا. |
| إضافة مورد | `POST /api/suppliers-v2`، صفحة `/suppliers-v2` | SUPPORTED_WITH_UI_ADAPTER | انتقال إلى مسار المورد الحالي وصلاحياته وهوياته؛ لا دمج أسماء ومعرفات مصادر الموردين بالاسم. |
| فاتورة مورد | `/purchase-invoices`، `POST /api/purchase-invoices` | SUPPORTED_WITH_UI_ADAPTER | فتح المسار المتخصص؛ لا إنشاء فاتورة عبر daily أو اعتماد استلام تلقائي. |
| راتب، استحقاق، سلفة، استرداد سلفة، تسليم/إرجاع عهدة | `POST P/accrue` و`POST P/movements/{id}/classify` | SUPPORTED_WITH_UI_ADAPTER | نقل المستخدم إلى سياق الموظف والحركة البنكية المناسبة؛ التفاصيل في جدول الرواتب. |
| نقل عهدة بين موظفين | يوجد legacy `POST /api/accounting/employees/custody/transfer`، لا action مقابل في MZ2 payroll | GAP | لا استدعاء legacy من النموذج الجديد ولا اختراع قيد. |
| تحويل بين الحسابات | legacy transfers يكتب `account_transactions`؛ لا عقد مقابل مثبت في MZ2 daily | GAP | لا ربط التحويل القديم على أنه محرك MZ2. |
| تسوية تابي، تمارا، إمكان، سلة | خدمات `S` | SUPPORTED_WITH_UI_ADAPTER | انتقال إلى التسوية؛ لا إنشاء تسوية بعملية daily موحدة. |
| تسوية COD، تحصيل شركة شحن | خدمات `H` | LOCKED | معاينة/قراءة وفق العقد فقط، مع بقاء P02 مقفولًا. |
| تأكيد مزود الدفع للحركة | `POST D/{id}/confirm-provider` | SUPPORTED | المزود والسبب وربط البنك المتحقق مطلوبون؛ ينشئ receipt draft وليس قيدًا بمجرد التأكيد. |
| آخر الحركات، الحالة، المبلغ، الطرف، المرجع | `GET D?status=&limit=` | SUPPORTED_WITH_UI_ADAPTER | حتى 500 حركة؛ label واضح للنتائج المحمّلة. عدم مساواة `unclassified` بـ«مكتملة». |
| معاينة القيد قبل التصنيف اليومي/الموظف | لا preview contract مستقل مثبت للمصروف/سداد المورد أو تصنيف الموظف | GAP | لا تركيب مدين/دائن في frontend. بعد الترحيل تُقرأ المجموعة من reports إن متاحة؛ التسويات والشحن لهما preview مستقل. |
| المرفقات العامة لكل حركة | عقود manual لا تملك رفع مرفق عام؛ رفع كشف البنك قدرة مختلفة | GAP | حذف منطقة PDF/JPG/PNG العامة من هذا النموذج؛ لا رفع إلى endpoint افتتاحي أو تسوية. |
| حفظ، حفظ وإضافة جديدة، إلغاء | callbacks على عقد الحفظ المختار | SUPPORTED_WITH_UI_ADAPTER | الإلغاء محلي قبل الإرسال؛ إعادة التهيئة بعد النجاح فقط. الحفظ لا يضم Post خفيًا، ولا يعيد إرسال حركة نجحت إن فشل إجراء تالٍ. |
| قواعد الاستخدام والتاريخ | نصوص مطابقة للعقود ووقت الرياض | SUPPORTED_WITH_UI_ADAPTER | تصحيح جملة التصميم «ينشأ القيد تلقائيًا بعد الحفظ»؛ حفظ الدليل يختلف عن الاعتماد المالي. |

## التسويات

| عنصر التصميم | القدرة والمسار الفعلي | التصنيف | قرار الواجهة والحدود |
| --- | --- | --- | --- |
| مزودو سلة/تابي/تمارا/إمكان والبنوك | `GET S/context`، `getAccountingSettlementContext` | SUPPORTED | المصدر يعيد providers، banks، bindings، permissions. |
| ربط المزود بالبنك | `PUT S/bindings/{provider}` | SUPPORTED | صلاحية `accounting.rules.manage` وعقد الربط الحالي؛ ليس تغيير guard. |
| رفع ملف تسوية | `POST S/drafts/upload`، `POST S/drafts/from-file` | SUPPORTED | XLSX مثبت في uploader الحالي. drag/drop مجرد UI adapter لنفس الملف والعقد. |
| PDF وMT940 والصيغ الأخرى المرسومة | uploader الحالي يستعمل `read_safe_xlsx_upload` | GAP | لا إعلان دعمها، ولا نسخ حد 10 MB من الصورة. |
| جدول الملفات: مزود، مرجع، فترة، مبالغ، رسوم، صافي، حالة | `GET S/drafts` أو `GET S/register` وتفاصيل draft.amounts/calculation | SUPPORTED_WITH_UI_ADAPTER | الرسوم تؤخذ من تفاصيل حقيقية، لا إجمالي تقديري. قائمة register لا تعيد جميع مبالغ الرسوم. |
| من/إلى، بنك، مزود، حالة، بحث | `GET S/register?q=&provider=&status=&bank_account_id=&period_from=&period_to=` | SUPPORTED | register يفحص آخر 2000 مستند ويعيد حتى 500؛ `total_filtered` محدود بهذا النطاق. |
| pagination وإعادة تعيين | تقسيم محلي للنتائج المحملة + query reset | SUPPORTED_WITH_UI_ADAPTER | لا اختراع offset/cursor ولا وعد سجل تاريخي كامل. |
| بطاقات عدد الحالات | `GET S/context.status_counts` | SUPPORTED_WITH_UI_ADAPTER | هذه أعداد tenant حسب الحالة، ليست تلقائيًا خاضعة لفلاتر الجدول. يجب وصف النطاق. |
| بطاقات مبالغ غير المسوّى/التطابق/المراجعة/الفروقات عالميًا | لا مجموع مالي شامل بهذه التعريفات في context؛ قوائم bounded | GAP | يمكن بدلًا منها ملخص «النتائج المعروضة» عند اكتمال حقولها، دون تسميته إجمالي المتجر أو مساواة الحالات بالمطابقة التلقائية. |
| تفاصيل المطابقة والصفوف غير المتطابقة | `GET S/drafts/{id}`، `source_snapshot`، `review_reasons` | SUPPORTED | المصدر يفرّق matched/unmatched وأسباب المراجعة. |
| مطابقة صف بأمر | `POST S/drafts/{id}/match-entry` | SUPPORTED | فعل صريح؛ ليس تعديلًا محليًا لحالة الصف. |
| مرشحو البنك وربط الحركة | `GET S/drafts/{id}/bank-candidates`، `PUT .../bank-match` | SUPPORTED | عرض المرشح لا يعني قبوله. الفارق وهوية الحركة من المصدر. |
| مطابقة تلقائية عامة بالذكاء أو سماحية ±1 ريال/3 أيام | لا عقد إعداد/تنفيذ بهذه الوعود مثبت في هذا المسار | GAP | importer وmatcher الموجودان فقط؛ لا نسخ قواعد الصورة ولا ابتكار match-all. |
| المسودة والتعديل ومرجع الكشف | `PATCH S/drafts/{id}`، `PATCH .../identity` | SUPPORTED | الإبقاء على reason/revision والتطبيع الحالي؛ `matched` قد يُعرض `ready_for_review` دون تغيير workflow_state. |
| إرسال للمراجعة | `POST S/drafts/{id}/submit` | SUPPORTED | يحتاج `accounting.drafts.create`، والخادم يعيد حساب الموانع. |
| مراجعة ورفض | `POST .../review`، `POST .../reject` | SUPPORTED | review يحتاج `accounting.settlements.post` في العقد الحالي؛ لا صلاحية مخترعة باسم review. |
| اعتماد التسوية/ترحيل | `POST .../post` | SUPPORTED | لا يعمل إلا بتفاعل صريح بعد reviewed؛ صلاحية post لا تتجاوز paused أو readiness. |
| معاينة القيد | `draft.journal_preview.entries` | READ_ONLY | لا توليد القيد في frontend؛ unavailable إذا لم يُرجع الخادم معاينة. |
| سجل التسويات، الدليل، حركة البنك، القيد | `GET S/register/{id}` | READ_ONLY | detail يعيد evidence وbank_movement وledger.status/reason/entries؛ لا تفسير ledger غير الجاهز كصفر. |
| إلغاء في أسفل الصفحة | إغلاق/إعادة تعيين محلي | SUPPORTED_WITH_UI_ADAPTER | ليس حذف تسوية محفوظة ولا عكس قيد مرحّل. |

## الشحن والتحصيل

| عنصر التصميم | القدرة والمسار الفعلي | التصنيف | قرار الواجهة والحدود |
| --- | --- | --- | --- |
| شركات الشحن وموصلو المتجر | `GET H/workspace.counterparties` | READ_ONLY | استعمال الهويات الحقيقية؛ الشعارات أو الأسماء لا تنشئ هوية جديدة. |
| شحنات مرشحة لأجرة الشحن | `workspace.courier_candidates` | READ_ONLY | حد 200 مرشح؛ ليست جميع شحنات المتجر أو كل المتأخرات. |
| مرشحو COD لموصلي المتجر | `workspace.driver_candidates` | READ_ONLY | حد 200، مع حقول collection/evidence الحالية فقط. |
| الحركات البنكية القابلة للتسوية | `workspace.bank_movements` | READ_ONLY | حد 200؛ لا إدخال تحصيل وهمي بديلًا عن دليل البنك. |
| أسعار الشحن المعتمدة | `GET H/rates`، `workspace.latest_rates/rate_policy` | READ_ONLY | عرض السعر وتاريخ سريانه ومرجع اعتماده. |
| إعداد سعر معتمد | `PUT H/rates`، `saveAccountingShippingRate` | SUPPORTED | يتطلب `accounting.rules.manage`، reason/evidence/revision. يبقى خاضعًا للكتابة الموقوفة؛ لا تفعيل P02 عند حفظ إعداد. |
| معاينة أجرة شركة الشحن | `GET H/courier-fee/{evidence_id}/preview` | READ_ONLY | تعرض رفض/أهلية الخادم، ولا تحول أهلية المعاينة إلى إذن Post. |
| معاينة COD الموصل | `GET H/store-driver-cod/{assignment_id}/preview` | READ_ONLY | لا اختراع دخل/أجرة من بيانات الشحنة الناقصة. |
| معاينة تسوية أو تحصيل أو سداد أجرة | `POST H/settlements/preview` | READ_ONLY | هذا POST للمعاينة وفق العقد، مع احترام rejection/paused؛ ليس إذنًا للترحيل. |
| إثبات المصروف أو بيع COD | `POST H/courier-fee`، `POST H/store-driver-cod` | LOCKED | P02 يبقى LOCKED، أزرار الاعتماد المالي مقفلة. |
| إضافة تسوية COD أو تحصيل شركة أو تسوية صافية | `POST H/settlements/post` | LOCKED | لا إرسال هذا الطلب في المهمة. الأنواع الحالية cod_remittance/fee_payment/net_settlement. |
| أحدث الأنشطة/التسويات | `workspace.recent_events` | READ_ONLY | آخر 50 حدثًا، لا تحويله إلى سجل شامل ذي total مختلق. |
| فلترة شركة/حالة/تاريخ، بحث، صفحات | حقول الأحداث والمرشحين المحمّلة | SUPPORTED_WITH_UI_ADAPTER | فلاتر محلية على مجموعة معلنة؛ لا اختراع search endpoint. |
| ذمم COD ومبالغ قيد التحصيل وتحصيل اليوم ونسب النمو | workspace لا يعيد هذه المؤشرات الشاملة؛ تقارير MZ2 مصدر مستقل | GAP | لا جمع عينات المرشحين والأحداث لإظهار أرقام التصميم. يمكن عرض رصيد تقرير MZ2 المتاح باسمه ونطاقه فقط. |
| عدد الشحنات المعلقة وآخر تسوية وافتتاحي الشركة | الأحداث/المرشحون لا يثبتون كل الأرقام؛ الافتتاحي يحتاج دليل معتمد | GAP | لا عرض رقم افتراضي أو استنتاج الرصيد الافتتاحي من السعر. |
| شرائح عمولة COD حسب قيمة الطلب | `rates` الحالي total_fee مؤرخ، وليس نفس جدول الشرائح المصور | GAP | لا تعديل الأسعار لتقليد جدول الصورة دون عقد مثبت. |
| فروقات تحتاج مراجعة | rejection reasons من previews | SUPPORTED_WITH_UI_ADAPTER | عرض الموانع الفعلية للعنصر؛ لا invent عدد «3 تسويات». |
| عرض القيد كاملًا | `GET R/journals` وتصفية txn_group_id إن عاد في الحدث | READ_ONLY | شرط report available ووجود المرجع؛ لا صياغة قيد من مثال التصميم. |

## المخزون والمشتريات

| عنصر التصميم | القدرة والمسار الفعلي | التصنيف | قرار الواجهة والحدود |
| --- | --- | --- | --- |
| لوحة مشتركة للموردين والفواتير والاستلام | القراءات أدناه مع `PartialWorkflowPage` الحالي | SUPPORTED_WITH_UI_ADAPTER | تجميع عرض فقط، مع فصل مصادر المخزون التشغيلي والمحاسبي وصلاحيات كل منها. |
| الموردون وهوياتهم | `GET /api/suppliers-v2/workspace`، `GET /api/purchase-invoices/catalog` | READ_ONLY | لا ضم مفاتيح supplier/counterparty بالاسم؛ استعمال الروابط المعتمدة. |
| جدول فواتير الموردين والتفاصيل | `GET /api/purchase-invoices`، `GET .../{id}` | SUPPORTED_WITH_UI_ADAPTER | يعيد state/status/payment_status/remaining_amount؛ الفلاتر from/to/supplier_id/status، وحد حتى 2000. total عدد المرجع المعاد وليس بالضرورة كل التاريخ. |
| مفتوحة، مدفوعة، متأخرة | نفس القائمة؛ الحقول الفعلية | SUPPORTED_WITH_UI_ADAPTER | يمكن ربط payment_status/state؛ لا اختراع متأخرة دون due_date موثوق. القيم الناقصة لا تصبح صفرًا أو مدفوعة. |
| بطاقة الفواتير المفتوحة | remaining_amount والفواتير المؤهلة في القائمة | SUPPORTED_WITH_UI_ADAPTER | ملخص المجموعة المحملة فقط؛ لا ضم drafts إلى دين مرحّل. |
| إضافة/تعديل فاتورة شراء | `POST /api/purchase-invoices`، `PUT .../{id}` | SUPPORTED | عبر workflow الحالي بصلاحية `accounting.purchases.post` وحالة الكتابة؛ لا اعتماد تلقائي. |
| اعتماد واستلام الفاتورة | `POST /api/purchase-invoices/{id}/approve-receive` | LOCKED | قدرة قائمة ذات أثر مخزني ومالي؛ لا تنفيذها أو تفعيل G47 ضمن هذه المهمة. |
| شاشة استلام المخزون | `GET /api/inventory-v2/purchase-receiving/catalog`، خدمة `mezanInventoryReceiving.js` | READ_ONLY | تعرض products، warehouses، locations، recent_receipts، inventory_health وفق صلاحيات المستودع. هذا مخزون تشغيلي. |
| أسماء الأصناف وأكوادها وتصنيفها ووحداتها | `GET /api/components-v2/workspace`، `GET /api/products-v2` | READ_ONLY | المنتجات مرقمة الصفحات، المكونات محدودة. لا استعمال fixtures في mezanProductCatalog كبيانات حقيقية. |
| فلترة النوع والموقع والبحث | الكتالوجات وبيانات الاستلام أعلاه | SUPPORTED_WITH_UI_ADAPTER | فلترة الحقول الموجودة فعليًا؛ لا افتراض وحدة أو تحويل صنف/مكوّن. |
| حالة تكلفة المنتج/المكون | مصادر components وproducts الحالية؛ `GET /api/products-v2/cost-review` يعني مراجعة تكلفة مكتملة | READ_ONLY | لا مساواة cost-review بـ«بلا تكلفة». تُظهر الحالة فقط إذا حقول المصدر تثبتها. |
| إجمالي عدد المنتجات بلا تكلفة | يتطلب نطاق كتالوج كامل وتعريف missing_cost المثبت | GAP | لا عدّ صفحة واحدة وتسميتها عددًا شاملًا؛ لا استنتاج من مبلغ مفقود أو صفر وحده. |
| قيمة المخزون المالي لكل صنف/موقع والإجمالي | لا endpoint موحد مثبت يربط valuation المحاسبي بجدول الكميات المصور | GAP | ممنوع حساب quantity × تكلفة تشغيلية وتسميته financial valuation. |
| مقارنة قيمة المخزون بالشهر الماضي | لا تاريخ valuation مثبت لهذا العنصر | GAP | حذف نسبة النمو. |
| أوامر شراء الشهر وعددها وقيمتها/إنشاء أمر شراء | purchase invoices وstock preparation orders قدرات مختلفة | GAP | لا إعادة تسمية الفاتورة أو أمر التجهيز كأمر شراء. |
| لوحة تفاصيل الصنف وصورته ومعلوماته | نفس الكتالوجات، حقول الهوية والوحدة والتصنيف الفعلية | SUPPORTED_WITH_UI_ADAPTER | أية صورة/مورد افتراضي/تبويب لا يملك بيانات يُحذف. لا invent تاريخ حركات محاسبي للصنف. |
| تحديث التكلفة وحفظ تفاصيل الصنف | توجد editors للمنتجات والمكونات؛ ليست valuation writer موحدًا | READ_ONLY | في هذه الصفحة عرض وروابط للقدرات القائمة فقط؛ لا زر عام يغير التكلفة المحاسبية. |
| مراجعة القيمة الافتتاحية | صفحة opening-balances الحالية؛ `GET /api/opening-inventory/imports/{id}` لمعرّف معروف | READ_ONLY | لا فتح approval أو تغيير opening semantics؛ لا endpoint list مختلق للاستيرادات. |
| استيراد الأصناف من ملف | opening-inventory imports مسار ذو معنى افتتاحي، وليس importer عام للصورة | GAP | لا إعادة استعماله كاستيراد أصناف عام. |
| طباعة التقرير | طباعة بيانات العرض الفعلية من frontend | SUPPORTED_WITH_UI_ADAPTER | عنوان واضح «بيانات تشغيلية/فواتير معروضة»؛ لا تقرير valuation غير موجود. |
| ملاحظة الفرق بين المخزون الفعلي والقيمة المالية | نص توضيحي | SUPPORTED_WITH_UI_ADAPTER | إبقاء الفصل صريحًا؛ زر مراجعة الجرد لا يظهر ما لم يربط workflow فعليًا. |
| مراجعة الجرد كإجراء جديد | لا عقد جرد/تسوية شامل مثبت لهذه الصفحة | GAP | لا correction أو posting جديد. |

## الرواتب والالتزامات

| عنصر التصميم | القدرة والمسار الفعلي | التصنيف | قرار الواجهة والحدود |
| --- | --- | --- | --- |
| الموظفون والراتب الأساسي | `GET P/context.employees` | READ_ONLY | id/name/monthly_amount من operating_salaries، حتى 500 موظف. ليس employee directory كاملًا بالضرورة. |
| بطاقات الرواتب المستحقة والسلف والعهد | salary_payable/advance/custody + ledger_status/ledger_reason | SUPPORTED_WITH_UI_ADAPTER | لا عرض أي صفر/مجموع مالي إلا عندما ledger_status=available؛ الخادم قد يعيد صفرًا حسابيًا عند عدم جاهزية ledger. وصف المجموعة المحدودة. |
| جدول الموظفين، البحث والاختيار والتفاصيل | نفس السياق | SUPPORTED_WITH_UI_ADAPTER | الاسم والرصيد الحقيقيان، pagination محلية، لا department مختلق. |
| إثبات استحقاق راتب | `POST P/accrue`، `accrueAccountingPayroll` | SUPPORTED | period/accrued_at/reason؛ per-employee يدعمه PayrollAccrualIn، والbulk لا يُشغّل تلقائيًا. يحتاج accounting.payroll.post. |
| صرف راتب | `POST P/movements/{id}/classify`, action=salary_payment | SUPPORTED | دليل حركة خارجة موجود، موظف، سبب، apply_open_advances؛ لا مبلغ حر ينتج حركة بنك جديدة ضمن classifier. |
| منح سلفة | نفس classifier، action=advance_grant | SUPPORTED | حركة خارجة؛ أثر مالي فوري عند نجاح التصنيف. |
| استرداد سلفة | نفس classifier، action=advance_repayment | SUPPORTED | حركة واردة، مع فحص الرصيد في المحرك. |
| تسليم عهدة | نفس classifier، action=custody_grant | SUPPORTED | حركة خارجة؛ لا عبور من مصروف عام. |
| إرجاع عهدة | نفس classifier، action=custody_return | SUPPORTED | حركة واردة؛ لا تجاوز الرصيد. |
| الحركات البنكية المعلقة للموظفين | `GET P/context.pending_movements` | READ_ONLY | حتى 300 unclassified بدون provider؛ ليست كل حركة معلقة صالحة لكل action/اتجاه. |
| تبويب حركات الموظف | `GET R/journals` مع تصفية هوية الموظف عند توافر الصلاحية | SUPPORTED_WITH_UI_ADAPTER | يحتفظ بمصدر القيد وتاريخه؛ لا طلب report بصلاحية payroll وحدها. |
| المبلغ المصروف وحالة «تم الصرف» للشهر | context الحالي يعيد رصيدًا حاليًا لا payroll run شهريًا | GAP | لا اعتبار salary_payable=0 دليل صرف الشهر أو طرح الرصيد من monthly_amount. |
| القسم، البدلات، الاستقطاعات، صافي راتب شهري مفصل | غير موجودة في payroll context الحالي | GAP | لا عرض الأصفار في التصميم كحقائق ولا محرك حساب رواتب جديد. |
| معاينة قيد مقترح قبل الدفع | classifier لا يعيد preview مستقلًا | GAP | لا اختراع mapping محاسبي في frontend؛ تعرض المراجعة الواقعية للحركة ثم فعل اعتماد صريح. |
| الالتزامات القادمة خلال 30 يومًا | `GET /api/recurring-obligations`، summary.due_next_30_days، items.next_due_date | READ_ONLY | مصدر حقيقي موجود، خاص بالمالك `_require_owner`، غير مدمج تلقائيًا برصيد MZ2 المرحّل. تصنيفه ليس GAP. |
| عرض الكل للالتزامات | `/recurring-obligations` مع `loadRecurringObligationsWorkspace` | SUPPORTED_WITH_UI_ADAPTER | رابط للمالك فقط؛ لا ترقية صلاحيات موظف له payroll.view. |
| تقويم شهري مستقبلي/forecast غير مستند لحقول due | العقد يعيد next_due_date لا تقويمًا عامًا لكل الأشهر | GAP | عرض المستحق التالي الموجود فقط، دون توليد توقعات مالية جديدة. |
| تصدير جدول الموظفين | frontend على حقول السياق المتاحة | SUPPORTED_WITH_UI_ADAPTER | لا تصدير بيانات غير جاهزة/غير مصرح بها أو استنتاج payslip. |

## القيود والتقارير

| عنصر التصميم | القدرة والمسار الفعلي | التصنيف | قرار الواجهة والحدود |
| --- | --- | --- | --- |
| المركز المالي | `GET R/financial-position?as_of=` | READ_ONLY | assets/liabilities/totals من ledger الصحيح فقط. status غير available ليس صفرًا. |
| ميزان المراجعة | `GET R/trial-balance?as_of=` | READ_ONLY | debits/credits/net لكل entity/sub_account. لا تغيير طريقة حساب الأرصدة. |
| دفتر اليومية | `GET R/journals?as_of=` | READ_ONLY | entries مع txn_group_id وside وamount وmetadata. القيود ليست صفًا واحدًا لكل entry leg في عرض المستخدم. |
| جدول القيود مع مدين/دائن | تجميع entries حسب txn_group_id | SUPPORTED_WITH_UI_ADAPTER | تجميع عرض فقط باستخدام decimal-safe amounts؛ لا تغيير ledger أو إنشاء قيد. |
| مرجع، مصدر، تاريخ، وصف | حقول journal الفعلية، entry_type/metadata وeffective_at | SUPPORTED_WITH_UI_ADAPTER | القيم المفقودة «غير متوفر»؛ لا إنشاء JV-000784 أو اسم منشئ من الصورة. التاريخ المحاسبي يأخذ effective_at ثم الحقول الموثقة. |
| تفاصيل القيد في لوحة جانبية وإغلاقها | نفس entries للمجموعة المحددة | SUPPORTED_WITH_UI_ADAPTER | لا endpoint detail جديد؛ focus يعود للصف، mobile drawer داخل حدود الشاشة. |
| فلتر حتى تاريخ | `as_of` | SUPPORTED | الرياض وفق report_cutoff؛ حدود القطع والجهاز ليست مصدر التاريخ. |
| من تاريخ/المصدر/الحساب/المرجع/بحث | فلترة frontend للدفتر الذي أعاده الخادم | SUPPORTED_WITH_UI_ADAPTER | الخادم يرفض أي query غير as_of بـ422؛ لا إرسال from/source/account. لا تحويل فلتر الفترة إلى إعادة تعريف financial-position. |
| الحالة وإعادة التعيين والصفحات | حالات entries المتاحة وتقسيم محلي | SUPPORTED_WITH_UI_ADAPTER | لا افتراض drafts ضمن تقرير posted؛ report maximum 10000 legs وحدود failure مغلقة. |
| قيود اليوم وإجمالي المدين والدائن | تجميع مجموعات report available مع نطاق تاريخ معلن | SUPPORTED_WITH_UI_ADAPTER | count للمجموعات لا للأسطر. القيم من المصدر؛ لا تثبيت قيم التصميم. |
| قيود غير معتمدة ومراجعة واعتماد القيود العامة | journals لا يمثل register عام لمسودات المحركات | GAP | لا جمع settlement drafts كأنها كل القيود ولا زر approve-all. |
| نسب النمو منذ الشهر/أمس | لا مؤشر مطابق جاهز؛ يتطلب تعريف نطاقين متكافئين | GAP | حذفها في هذه المرحلة؛ لا نسبة من بيانات ناقصة. |
| قيد جديد عام | ليس هدف الصفحة ولا يوجد عقد MZ2 عام معتمد ضمن المهمة | GAP | لا ربط legacy GL writer؛ يمكن توجيه المستخدم للحركة الواقعية المدعومة. |
| الأستاذ العام وكشف الحساب | فلترة entries لمعرف حساب صريح مع تجميع عرض | SUPPORTED_WITH_UI_ADAPTER | فقط ضمن ledger available ونطاقه الكامل؛ لا تشغيل legacy report كبديل عند not_ready. |
| ملخص المصروفات حسب التصنيف | expense_record والـmetadata الموثقة من journals | SUPPORTED_WITH_UI_ADAPTER | يقتصر على قيود المصروف المؤهلة ذات التصنيف الصريح في البيانات، مع إعلان النطاق؛ ليس P&L جديدًا ولا دمج كل المصروفات التشغيلية. |
| طباعة وPDF | print stylesheet ونافذة المتصفح لحفظ PDF | SUPPORTED_WITH_UI_ADAPTER | عملية frontend فقط؛ تسمية «طباعة / حفظ PDF» وليست وعد downloadable PDF backend. |
| Excel | `exceljs` موجود في frontend/package.json | SUPPORTED_WITH_UI_ADAPTER | تصدير التقرير المتاح والفلاتر والنطاق نفسه؛ لا معادلات/روابط من نصوص غير موثوقة ولا كتابة مالية. |
| مرفقات أي قيد وتنزيلها | دليل الملف متاح لبعض المحركات، لا attachment contract عام لكل journal | GAP | إظهار الدليل فقط عند وجود مسار محرك مثبت؛ لا ملفات وهمية أو روابط مركبة. |
| مركز المساعدة/جميع التقارير | نص إرشادي وروابط للتقارير الثلاثة الموجودة | SUPPORTED_WITH_UI_ADAPTER | لا أعداد إشعارات/مقالات وهمية أو تبويبات بلا وجهة. |

## العناصر المشتركة والصلاحيات

| العنصر | العقد أو التنفيذ | التصنيف | القرار |
| --- | --- | --- | --- |
| التنقل الرئيسي والفرعي | AccountingWorkspace، accountingPages، MezanV2NavigationShell | SUPPORTED_WITH_UI_ADAPTER | المحافظة على first-run وowner setup بعد #1205؛ لا إعادة تطبيقه على أساس قديم. جميع الوجهات لها capability. |
| البحث العام، الجرس وعدد 6 في الصورة | خارج contract صفحات المحاسبة الجديدة | GAP | إبقاء قدرات shell الحالية فقط؛ لا بحث مالي عالمي أو notification counter مصطنع. |
| الصلاحيات | `GET A/access`، permissions من backend | SUPPORTED | صلاحية الصفحة لا تمنح action؛ الحفاظ على explicit grants، ورفض API=403 دون retry mutation. |
| paused writers | `GET A/write-control` | READ_ONLY | تعطيل mutations عند paused/unknown. لا PUT/replay من الواجهة الجديدة. عدم الجاهزية المالية لا يُخفى. |
| RTL، 1440 و390 | presentation فقط | SUPPORTED_WITH_UI_ADAPTER | أعمدة minmax(0,1fr)، wrap للنصوص، mobile cards/drawer، focus/labels، صفر overflow للصفحة. الاختبار مؤجل. |
| loading/error/empty/missing | كل read contract | SUPPORTED_WITH_UI_ADAPTER | فصل الحالات، إسقاط البيانات القديمة عند تغير النطاق/الصلاحيات، عدم تحويل null/error إلى 0. |
| bulk select وellipsis | إجراءات المصدر المثبتة فقط | SUPPORTED_WITH_UI_ADAPTER | لا bulk Post، ولا قوائم إجراءات فارغة؛ إن لم توجد عملية supported يُحذف عنصر التحديد. |

## أهم مخاطر الربط

1. `classify-outgoing` وتصنيف الموظف وإثبات الاستحقاق كتابات مالية فعلية. يجب فصل «حفظ الدليل» عن «اعتماد وترحيل» وعدم استدعاء أي منها على mount أو filter أو اختيار بطاقة.
2. payroll_context يعيد حقول رصيد صفرية عندما ledger غير متاح؛ يجب قراءة ledger_status أولًا، وإظهار «غير متاح» بدل رصيد صفري.
3. reports لا يقبل سوى as_of. تاريخ effective_at في V2 لا يُستبدل بتاريخ الإنشاء. لا fallback لدفتر legacy عند فشل الجاهزية.
4. register يعيد أعدادًا من مسح محدود؛ shipping يعيد عينات؛ daily وpayroll لهما حدود أيضًا. الملخصات يجب أن تسمي نطاقها، ولا تُعرض المقارنات الشاملة المصورة.
5. عقد recurring-obligations موجود لكنه owner-only ومصدر تشغيل اقتصادي مختلف؛ عرضه لا يُثبت قيدًا في MZ2.
6. توجد ملفات legacy للـtransfers وcustody transfer، وfixtures للمخزون. وجودها لا يجعلها adapter صالحًا للمحاسبة الجديدة.
7. `financial_provider_apps.py` يثبت lifecycle routes قبل compatibility settlement routes. يجب الحفاظ على المحرك الفعلي وحالاته، لا الاعتماد على دالة متأخرة متشابهة الاسم.
8. تصميم التقارير يحتوي drafts وnew journal وattachments عامة لا يثبتها reports contract. وتصميم المخزون يجمع quantity وvaluation دون مصدر موحد مثبت. تُترك هذه العناصر GAP.

## خطة التنفيذ والتحقق بعد إغلاق شرط الأساس

بعد تثبيت Production الذي يتضمن #1205، يُجلب origin من جديد، ويُحدّث worktree مع الحفاظ على التقرير، ويُعاد فحص mapping والصلاحيات قبل Phase 2. التنفيذ يبدأ بالمكونات المشتركة والقراءات، ثم يربط كل فعل بالمحرك الحالي، ثم يراجع جميع حالات الأخطاء والصلاحيات. لا backend core change مخطط له.

| نوع الإثبات | الحالات المطلوبة | الحالة في هذه المرحلة |
| --- | --- | --- |
| Frontend unit | اختيار نوع يفتح engine الصحيح؛ لا API مالي على تغيير الاختيار؛ idempotency؛ missing amount؛ نطاق الملخص؛ metadata date؛ ledger not_ready | لم يُشغّل؛ لا تغيير منتج بعد |
| Integration | adapter إلى المسارات الحالية فقط؛ 403/409/423؛ cancel لا يكتب؛ read-only لا يرسل Post مالي؛ retry لا يكرر الجزء الناجح | لم يُشغّل |
| Permissions | owner/employee/no grant؛ post explicit؛ recurring owner-only؛ unknown permissions fail closed | لم يُشغّل |
| Paused/P02 | منع محاولات Post في UI عند paused/unknown؛ P02 locked؛ لا تغيير write-control أو gates | لم يُشغّل |
| Browser وRTL | الصفحات الست عند 1440 و390؛ قياس scrollWidth مقابل viewport؛ لوحة التفاصيل/keyboard/filters/error/empty | لم يُشغّل |
| Screenshots | 12 لقطة فعلية للواجهة المحلية، Desktop + Mobile لكل صفحة، مع وسم fixtures إن استُخدمت | لم تُنتج؛ الصور المرجعية ليست دليل تنفيذ |
| Regression | اختبارات الصفحات والخدمات المعنية وfirst-run بعد #1205، ثم build حسب scripts الموجودة | لم يُشغّل |
| Live | لا كتابة مالية ولا Smoke B ولا تفعيل أو نشر في TRACK_D | لم يُنفّذ ولا يُطلب ضمن المهمة |

لم تُكتسب حالة `MZ2_ACCOUNTING_UI_READY_FOR_REVIEW`. الحالة الحالية: `TRACK_D_PHASE1_SOURCE_AUDIT_COMPLETE_IMPLEMENTATION_BLOCKED_BY_1205_PRODUCTION_BASE`. اكتمال التدقيق يعني مراجعة عناصر الصور والعقود على SHA المذكور، لا إثبات عملها في runtime أو اجتياز الاختبارات.

## مراجع المصدر

جميع الروابط مثبتة على Production SHA الذي دُقّق، وليست روابط إلى فرع متحرك.

- [service adapters](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/frontend/src/services/accountingModule.js)، [routing](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/frontend/src/pages/accounting/accountingPages.js)، [partial workflows](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/frontend/src/pages/accounting/AccountingWorkflowPages.jsx).
- [daily contracts](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_daily_movements.py)، [employee finance](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_employee_finance.py)، [reports](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_mz2_reports.py).
- [settlement contracts](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_settlement_routes.py)، [lifecycle](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_settlement_lifecycle_routes.py)، [register](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_settlement_register_routes.py)، [bank matching](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_settlement_bank_match_routes.py).
- [shipping P02](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_shipping_p02.py)، [shipping settlements](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_shipping_settlements.py).
- [purchase invoices](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/purchase_invoices_routes.py)، [receiving catalog](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/product_inventory_receipt_routes.py)، [suppliers](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/mezan_supplier_management_routes.py)، [cost review](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/product_cost_setup_routes.py)، [components](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/component_workspace_cost_compat_routes.py)، [opening inventory](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/opening_inventory_routes.py).
- [recurring obligations](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/recurring_obligations_routes.py)، [write-control](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/accounting_write_control.py)، [route installation order](https://github.com/AMASI-SA/AMASI-SA/blob/18432683a549bf50a2c994fcb45113115f231cfb/backend/financial_provider_apps.py).
