# تسليم المشرف الحالي — MZ2-FIN-CUTOVER-001

> أحدث مرجع للحالة هو قسم «نقطة التحقق SUP-20260922-02» في نهاية هذا الملف. الأقسام السابقة محفوظة كسجل الاستئناف التاريخي؛ لا تنقل حالتها القديمة إلى رؤوس أحدث.

آخر تحديث إشرافي: 2026-09-22  
المالك: عرفات — متجر أماسي  
الغرض: مرجع استئناف دائم للمشرف البديل عند انقطاع المحادثة. هذا سجل إشرافي، وليس تفويضًا بالتطبيق أو الدمج أو النشر.

## قاعدة التوثيق الملزمة

بعد كل مراجعة أو اختبار أو قرار مادي، يُحدَّث هذا الملف بنفس الهوية ويضاف:

1. التاريخ والمسار أو PR والـBase والـHead.
2. ما تحقق منه المشرف مباشرة، ومصدر الدليل.
3. ما ورد كتقرير من المنفذ ولم يُتحقق منه مستقلًا.
4. الاختبارات المشغلة فعلًا ونتائجها، وما لم يُشغّل.
5. أي كتابة أو تعديل أو Commit أو Push أو Merge أو Deploy أو تغيير بيانات.
6. الموانع الحالية والخطوة الآمنة التالية.

لا يُنقل نجاح قديم إلى Head جديد، ولا يُعتبر فحص النص أو نجاح CI بديلًا عن HTTP وReal Mongo والمتصفح عندما تكون مطلوبة.

## الحواجز العامة المعتمدة

- `P01=IN_PROGRESS`.
- `P02=LOCKED`.
- `P03=LOCKED`.
- لا Merge أو Deploy أو تغيير Preview/Production أو كتابة مالية حقيقية دون تفويض مستقل وصريح.
- لا فتح P02 أو P03 بسبب اكتمال توثيق أو اختبارات معزولة.
- لا إنشاء دفتر أستاذ أو كاتب قيود أو خدمة افتتاحيات منافسة.
- ميزان 2 نظام جديد: لا ترحيل قيود ميزان القديم؛ القيم القديمة `legacy_copy / unverified` حتى التحقق، ولا تنشئ قيودًا تلقائيًا.
- تاريخ القطع الفعلي ومرجع الأدلة الموقعة والأرصدة الافتتاحية الحقيقية لم تُعتمد في هذا السجل.
- القيد المرحّل لا يعدّل أو يحذف؛ التصحيح يكون بعكس مصرح ثم قيد بديل مستقل.

## ما قام به المشرف في هذه المحادثة

- مراجعات قراءة فقط؛ لم يُعدّل كود التطبيق ولم تُشغّل اختبارات التطبيق ولم يحدث Commit أو Push أو Merge أو Deploy.
- تحقق مباشر من GitHub لحالات PRs وSHAs المذكورة أدناه.
- تحقق من بصمات ومستندات محلية مرفقة ومقارنتها بالنسخ البعيدة حيث أمكن.
- أنشئ هذا الملف وحده كوثيقة تسليم دائمة للمستخدم.

## المسار A — P02 الشحن وCOD — PR #1130

- الرابط: https://github.com/AMASI-SA/AMASI-SA/pull/1130
- الحالة المتحققة: مفتوح، Draft، غير مدموج.
- Base: `5292a87a476a140ae8c3c78e88dfba7d8c83f035`.
- Head: `20400fffb03594af8a38d6b4750c170233bd5f37`.
- Patch V3 المعزول المراجع SHA-256:
  `06d760f8edfcdc3beb64e66f1d976d47066de89c6bba602f6a4837347d0763c6`.
- تحقق المشرف من بصمة Patch #1130 المضمّن في مشغّل الفحص، وقائمة ملفاته وإحصاءاته، وسلامة صياغة ملفات Python الجديدة.
- لم ينجح `git apply --check` على worktree الحقيقي لأن worktree لم يكن متاحًا في بيئة المراجعة.
- Patch #1126 منفصل عن #1130؛ لم تصل نسخة Patch #1126 الفعلية للمشرف في تلك المراجعة، بل الإحصاء فقط.

### حكم المشرف على V3

`CHANGES_REQUIRED_NOT_READY_TO_APPLY`

المطلوب في V4 قبل أي تطبيق:

1. جعل `require_native_contract_runtime()` أول سطر تنفيذي في `prepare_contract_courier` و`post_contract_courier`، قبل actor أو DB أو أي قراءة/تحقق.
2. اختبارات تثبت HTTP 423 وصفر استدعاءات actor/DB/order/courier/evidence وصفر كتابة عندما تكون البوابة مقفلة.
3. إزالة تحويل `Decimal` إلى `float` في فحص `_leg`، واستخدام Decimal/quantize فقط مع اختبارات الدقة والحدود.
4. إبقاء المسارات الحالية `prepare_courier_fee` و`post_courier_fee` و`PUT /rates` دون تعديل، وعدم تسجيل Endpoints جديدة.
5. خدمة الأدلة ما زالت `NOT_INTEGRATED`.
6. أول اعتراف ببيع COD خارجي ما زال محجوبًا بـ`cod_base_receivable_required`.
7. الأستاذ والتقارير للمسار الجديد ما زالا غير موصولين.

لا تطبيق، لا اختبارات، لا Commit، لا Push حتى تصل V4 ويُنفذ `git apply --check` قراءة فقط في طرفية Emergent/worktree الصحيح ثم يراجع المشرف نتيجته.

## المسار B — الحسابات المالية والأرصدة الافتتاحية — PR #1131

- الرابط: https://github.com/AMASI-SA/AMASI-SA/pull/1131
- الحالة المتحققة: مفتوح، Draft، غير مدموج، وملف توثيق واحد فقط.
- Base: `6365a042dfcb125e81e5e198ea1ff1537373ce51`.
- Head: `406e4cf45c8856dbb1edcb7226b9227c3f0b08f7`.
- SHA-256 للوثيقة:
  `7c5eed7dae78c43228c3cecd4dc35876be91ae86f5c4766ca51b6640c7f48b57`.
- Git blob المحلي والبعيد المتطابق:
  `caf8a438012beea39f434c2576445407cccdd19f`.
- لا توجد تشغيلات CI لهذا الرأس، وBackend/Frontend/Tests ما زالت `NOT_STARTED/NOT_RUN`.

### ما اعتمد توثيقيًا

- صفحتا الحسابات المالية والأرصدة الافتتاحية.
- أنواع الحسابات المالية الخمسة: بنك، صندوق، محفظة إعلانية مسبقة، ذمة منصة آجلة، سحب على المكشوف.
- إنشاء الحساب تعريف فقط بلا قيد أو رصيد.
- العملة الأصلية محفوظة؛ القيد والتقارير النهائية SAR؛ SAR بسعر 1؛ غير SAR يحفظ السعر ووقته ومصدره وSnapshot والمقابل بالريال.
- السالب لا يغير النوع تلقائيًا؛ الصندوق السالب مرفوض؛ البنك/المحفظة السالبة تحتاج هوية التزام مستقلة؛ الصفر إثبات صريح بلا قيد صفري.
- المسودة والمعاينة والاعتماد منفصلة عن الترحيل النهائي.
- الحساب المستخدم لا يحذف ويجوز تعطيله؛ الدليل وSnapshot ثابتان؛ التصحيح بعد الترحيل بعكس ثم افتتاحية بديلة.
- التوصية المعمارية الحالية: `accounting_ledger_v2` في #1023 نواة التخزين المرجعية، مع تكييف خدمة #1116 والمحافظة على حماية ومعاملات وتقارير #1124؛ ليست توصية بدمج أي PR كما هو.

### بوابات لازمة قبل بدء الكود

1. حسم هل «الحسابات المالية» صفحة تاسعة أم تبويب داخل صفحة قائمة، وتثبيت `page_id` والمسار وصلاحية العرض دون تغيير عقد الصفحات الثماني ضمنيًا.
2. التأكيد أن الأنواع الخمسة تخص تعريف الحسابات المالية فقط، وأن صفحة الافتتاحيات الموحدة تبقى شاملة للبنوك/الصناديق، مزودي الدفع، الشحن وCOD، الموردين، الموظفين والرواتب، المخزون بالتكلفة، ورأس المال.
3. تثبيت مفاتيح صلاحيات منفصلة للعرض، المسودة/الإدارة، المراجعة، الترحيل، والعكس. المفتاح الحالي `accounting.opening_balances.approve` لا يكفي لأنه يمر عبر مسار يرحّل حاليًا.
4. تحديد خدمة الأدلة الفعلية وحالاتها وبصمتها وحجمها وSnapshot وحارس الحذف؛ النص أو `evidence_ref` وحده لا يكفي.
5. تثبيت خطة انتقال تمنع عمل الكاتب القديم والجديد معًا، وتربط التقارير والأرصدة والمعاملات والإقفال بنواة V2 دون GL ثالث أو fallback قديم.

الحكم: `DOCUMENTATION_CHECKPOINT_PASS / IMPLEMENTATION_GATE_BLOCKED`.

## المسار C — الحسابات الإعلانية والمديونيات — PR #1132

- الرابط: https://github.com/AMASI-SA/AMASI-SA/pull/1132
- الحالة المتحققة: مفتوح، Draft، غير مدموج.
- الفرع: `chatgpt/mz2-advertising-accounts-debts-20260922`.
- Head السابق الذي روجعت عليه النتائج المعزولة: `6d84ebd106661e36fb0fea6f53a1b2de9a35a346`.
- Head البعيد الأحدث الذي ظهر أثناء إنشاء هذا التسليم:
  `405883c34f81c5d17625083b11e3c608662023af`.
- رسالة آخر Commit: `test(accounting): verify advertising contracts against any PR base and exact review parents`.
- لم يراجع المشرف بعد فرق الرأس الجديد كاملًا؛ لا تنقل قبول الرأس السابق إليه.
- على الرأس الأحدث ظهرت تشغيلتان فقط عند التحقق: `MZ2 Advertising Isolated Contracts` و`Qoyod Payment Freshness`، وكلتاهما ناجحة. هذا لا يساوي قبول التكامل أو جميع البوابات.
- الـPR ما زال يستهدف فرع #1131، لكن بياناته المعلنة مبنية على نقطة أقدم؛ يلزم إعادة قراءة Base/Head والتاريخ قبل أي حكم.

### الحكم السابق الملزم حتى مراجعة الرأس الجديد

`CHANGES_REQUIRED_ISOLATED_CONTRACT`

- 93 اختبارًا المعزولة السابقة تخص Head `6d84ebd…`، ولا تثبت Real Mongo أو HTTP أو التزامن أو التكامل مع النواة.
- لا Merge أو Deploy أو ربط تشغيلي.
- المطلوب يشمل عقد شحن المحفظة من البنك/الصندوق، replay اقتصادي، مطابقة الأيام والفواتير وتخصيص الدفعات بالحساب، Snapshots العملات والعمولة، والصلاحيات/Workflow؛ ثم تشغيل CI من جديد على Head/Base الفعليين.
- لا إنشاء نواة قيود أو محافظ أو افتتاحيات بديلة لتجاوز #1131.

## الحالة المحاسبية العامة التي لا يجوز تجاوزها

- لا يوجد تاريخ قطع فعلي معتمد في هذا السجل.
- لا يوجد مرجع ورقة أدلة موقعة معتمد في هذا السجل.
- لا توجد أرصدة افتتاحية حقيقية معتمدة أو إثبات صفر مكتمل لكل حساب.
- لا يوجد تفويض بترحيل افتتاحية أو تفعيل P02/P03.
- لا تعتمد ملفات أو قيم ميزان القديم كمصدر مالي موثوق.
- أي قبول للواجهة أو API يجب أن يفرق بين مسودة/معاينة/اعتماد/ترحيل، ويوثق هل حدثت كتابة أم لا.

## أول خطوات آمنة للمشرف البديل

1. اقرأ هذا الملف كاملًا، ثم اقرأ أحدث وصف وHead لكل PR من GitHub؛ لا تعتمد الرسائل القديمة إذا تغير الرأس.
2. لا تبدأ من الصفر ولا تعِد الاختبارات المقبولة على نفس الرأس بلا سبب؛ لكن لا تنقلها إلى رأس جديد.
3. في #1130: انتظر V4، راجع الفرق، ثم نفذ check-only في worktree الصحيح قبل السماح بالتطبيق.
4. في #1131: اسمح بتحديث الوثيقة ووصف PR فقط حتى إغلاق بوابات الصفحة/النطاق/الصلاحيات/الأدلة/الانتقال؛ لا Backend أو Frontend.
5. في #1132: راجع الفرق بين `6d84ebd…` و`405883c…` والـBase الحالي وفحوص الرأس الجديد قبل إصدار حكم.
6. بعد كل خطوة مادية، حدّث هذا الملف بنفس هويته ولا تنشئ نسخة مبعثرة جديدة.

## رسالة الاستئناف المختصرة

«أكمل كمشرف مستقل على `MZ2-FIN-CUTOVER-001`. المرجع الدائم هو ملف `MZ2-SUPERVISOR-HANDOFF-CURRENT.md` المحفوظ عند عرفات. اقرأه كاملًا ثم تحقق من أحدث Heads في GitHub. لا تبدأ من الصفر، ولا تطبق أو تدمج أو تنشر أو تغيّر Preview/Production. الحالة: P01 قيد التنفيذ، P02/P03 مقفلة. #1130 ينتظر V4 بعد رفض V3 للتطبيق، #1131 توثيق مقبول لكن التنفيذ محجوب بخمس بوابات، و#1132 انتقل إلى Head جديد غير مراجع بالكامل. وثق كل تحقق أو قرار بتحديث الملف نفسه.»

---

## نقطة التحقق SUP-20260922-02 — استئناف المشرف المستقل

التاريخ: 2026-09-22. هذه النقطة تحدث الحالة التاريخية أعلاه ولا تمحوها. هوية الملف الوحيدة هي `docs/operations/MZ2-FIN-CUTOVER-001/SUPERVISOR-HANDOFF-CURRENT.md`، على فرع `chatgpt/mz2-supervisor-handoff-20260922` ضمن Draft PR #1133.

### أساس الاستئناف وحدود المراجعة

- قُرئ سجل التسليم السابق كاملًا عند `9824ef45fb6689de56b5f3ed237a90a3725e25c6` قبل اتخاذ القرار، وقُرئ تعليق #1006 رقم `5781919809`. استعلام التعليقات منذ تلك النقطة أعاد تعليق الاستئناف نفسه دون نقطة إشراف لاحقة وقت القراءة.
- قُرئ `AGENTS.md` من المصدر المحفوظ في أدلة CI؛ لا تشغيل لأوامر النشر أو المصدر الواردة فيه.
- تحقق مباشر من بيانات PRs #1130/#1131/#1132/#1133: جميعها مفتوحة وDraft وغير مدموجة عند القراءة.
- لا توجد طرفية Emergent أو worktree التطبيق مفحوصان في هذه المراجعة. لا ادعاء بشأن `git status` أو تغييرات المنفذين المحلية؛ فحص GitHub لا يثبت حالتها.
- الحالة الملزمة لم تتغير: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا قطع أو افتتاحيات فعلية معتمدة ولا تفويض مالي أو تشغيلي جديد.

### الهويات المتحققة والتغير عن التسليم

| المسار | Base / المرجع | Head المتحقق | الملاحظة |
| --- | --- | --- | --- |
| #1130 | `5292a87a476a140ae8c3c78e88dfba7d8c83f035` | `20400fffb03594af8a38d6b4750c170233bd5f37` | لم يتغير الرأس البعيد؛ V4 لم تُراجع في هذه النقطة |
| #1131 | `6365a042dfcb125e81e5e198ea1ff1537373ce51` | `29e4cd940b195df0164fe7bc74b07c77a0a02bfc` | رأس جديد بعد checkpoint المقبول `406e4cf...` |
| #1132 | بيانات PR وأدلة CI: `ab3ef10c5aedfe5e2190bc57bb8ed26afc1347f2` | `405883c34f81c5d17625083b11e3c608662023af` | رأس فرع الأساس الحي أصبح `29e4cd9...`؛ لا تساوٍ بين الهوية المختبرة والفرع الحالي |
| #1133 قبل هذا التحديث | `6365a042dfcb125e81e5e198ea1ff1537373ce51` | `9824ef45fb6689de56b5f3ed237a90a3725e25c6` | ملف واحد؛ blob المقروء قبل الاستبدال `f7ca3ce2f0fff1bb5429dd7101ee53688539888d` |

مصادر الهوية: بيانات PRs في GitHub، ومرجع فرع #1131 المباشر `refs/heads/chatgpt/mz2-ad-financial-accounts-opening-balances-20260922`، ومرجع `refs/pull/1132/merge`.

### #1130 — استمرار الحجب دون تنفيذ

حكم `CHANGES_REQUIRED_NOT_READY_TO_APPLY` على V3 مستمر. لم يُفحص أو يُطبق Patch V4 ولم يُشغّل `git apply --check` أو اختبار أو script لـP02. لم يُعدّل فرع #1130 أو ملفاته. لا يُستنتج من ثبات الرأس غياب عمل محلي عند المنفذ.

الخطوة الآمنة التالية: استلام هوية وبصمة V4 ومراجعة ترتيب البوابة وDecimal وعدم تغيير المسارات القائمة؛ ثم check-only في worktree الصحيح، لا التطبيق أو الاختبارات أو Commit/Push للمسار قبل اجتياز الحاجز المحدد.

### #1131 — الإضافات الخمس موجودة، لكن لا قبول تنفيذ

تحقق المشرف من Commit `29e4cd940b195df0164fe7bc74b07c77a0a02bfc`: الأب هو `406e4cf45c8856dbb1edcb7226b9227c3f0b08f7`، والفرق ملف توثيق واحد `+118/-11`. قُرئت أقسام المراجعة الخمسة الجديدة وسياق حدود التسليم مباشرة من الوثيقة المثبتة؛ blob `5adbef91884bde086966f7473722db5bcaf43820`.

الإضافات المتحققة نصيًا:

1. صفحة تاسعة مستهدفة `financial-accounts`، رابطها `/integrations-v2?workspace=financial&page=financial-accounts`، وإذنا عرض وإدارة تعريف منفصلان؛ لا ادعاء بأن المسار يعمل حاليًا.
2. فصل الأنواع الخمسة لتعريف الحسابات عن نطاق الافتتاحيات الموحد والأقسام السبعة، مع الحفاظ على فئات #1116 وعدم فتح P02/P03.
3. مفاتيح مستقلة للمسودة والمراجعة والترحيل، وإعادة استخدام إذن العكس المركزي؛ لا تحويل تلقائي لـ`accounting.opening_balances.approve` إلى إذن ترحيل جديد.
4. تحديد `accounting_source_files.py::preserve_original` كمرجع لحفظ الأصل، مع نسخة اعتماد وغرض وبصمة وSnapshot ومنع حذف مطلوبين؛ الوثيقة تصرح بأن توسيع حماية أدلة الافتتاحيات غير منفذ.
5. خطة انتقال مستهدفة إلى V2 بحاجز مالك واحد، منع ازدواج الكاتب القديم والجديد، وتكييف القراءة والتقارير والمعاملات؛ عقد الجذر والعكس والبديل وإثبات الصفر موصوف كعمل غير منفذ.

حد الدليل: هذه مراجعة إضافات الوثيقة، لا إعادة تدقيق كامل لجميع مصادر #1023/#1116/#1124 أو إثبات توافقها أو تنفيذها. SHA-256 المعلن `7ca53e378a5f129e1a87b1901ea1d81625ec724721b873e928d9a4b7091f01b6` وفحوص التنسيق المحلية و462 سطرًا/80,639 بايت بقيت تقرير منفذ؛ لم تُعد حسابها هذه المراجعة. قراءة Actions أعادت قائمة تشغيلات PR فارغة لهذا الرأس؛ لا تُسجل PASS.

الحالة الحالية: `DOCUMENTATION_DELTA_REVIEWED / IMPLEMENTATION_GATE_BLOCKED`. يظل `DOCUMENTATION_CHECKPOINT_PASS` قبولًا تاريخيًا للرأس `406e4cf...` فقط؛ لا يُنقل إلى الرأس الجديد تلقائيًا. يلزم استكمال المطابقة المستقلة لعقد النواة والأدلة والصلاحيات وخطة الانتقال قبل حكم قبول التحديث، ثم تفويض مستقل قبل بدء Backend/Frontend.

### #1132 — تحقق مستقل من الفرق والأدلة، مع كشف تقادم Base

مقارنة `6d84ebd106661e36fb0fea6f53a1b2de9a35a346...405883c34f81c5d17625083b11e3c608662023af` أعادت commit واحدًا، ahead=1/behind=0، وملفًا واحدًا فقط: `.github/workflows/mz2-advertising-contract.yml`، `+91/-21`. قُرئ ملف workflow كاملًا مباشرة؛ blob `08bb96ca3ca2bd88b8c3ee3654030c911ec45413`. هذا تصحيح R6 للـworkflow، وليس نشر تصحيحات R1–R5.

تحقق نصي مباشر من وجود سياقي `head` و`base-merge`، وربط SHA والآباء بالحدث، وصلاحيات مستودع للقراءة فقط، وتعطيل حفظ credentials في checkout، ومصادر وأدلة allowlisted، والاختبارات المعزولة. لا توجد في هذا الفرق تغييرات على Backend أو Frontend أو العقود الاقتصادية.

تحقق مباشر من التشغيل [35765107753](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35765107753): وظيفتا head/base-merge وخطوات التحقق واختبارات Backend/Node/React مكتملة وناجحة. نُزّل الأثران وقرئت السجلات وملفات الهوية، ولم تُنفذ شيفرتهما.

| دليل CI | Artifact ID | SHA-256 المتحقق للـZIP |
| --- | --- | --- |
| head | `10711646972` | `71f76542898f05feb74f352ba0da296d40d8dc0d3616ffe0e7e5dae11bfc90ca` |
| base-merge | `10711941357` | `1afaf43413dd119cceff661b00af8987327e95f7a078fe4362e85aa4d9b6fd0a` |

فحص Python محلي مستقل للبايتات فقط: exit 0. طابقت بصمتا ZIP قيم GitHub، وطابقت SHA-256 وGit blob لجميع ملفات source-manifest وعددها 111 في كل أثر. قرئت ملخصات السجلات الخام: **58 Backend +17 Node +18 React DOM، نجاح دون skips، في كل سياق**. هذه 93 حالة في سياقين، لا 186 حالة مختلفة. لم يُعد المشرف تشغيل اختبارات التطبيق.

الهويات الفعلية داخل `context.json`:

- head checkout: `405883c34f81c5d17625083b11e3c608662023af`؛ tree `75e4cfe4d2eb08523b9350724e3b9ae5b728cbb6`.
- base-merge checkout: `0b9c45c672668f61970cec75a287ab266f4d49c5`؛ tree `257b22b065af3bddfef36123eeaab3c34437d968`.
- review_base في الأثرين: `ab3ef10c5aedfe5e2190bc57bb8ed26afc1347f2`.
- الآباء المرتبون للدمج الاصطناعي: `[ab3ef10c5aedfe5e2190bc57bb8ed26afc1347f2, 405883c34f81c5d17625083b11e3c608662023af]`.
- مرجع `refs/pull/1132/merge` أعاد كذلك `0b9c45c...` وقت القراءة. هذا مرجع مراجعة اصطناعي، وليس Merge للـPR.

**المانع الجديد المتحقق:** فرع الأساس المباشر الآن عند `29e4cd940b195df0164fe7bc74b07c77a0a02bfc`، بينما بيانات PR وأدلة CI ومرجع الدمج الاصطناعي مرتبطة بالأساس الأقدم `ab3ef10...`. النجاح صالح لهويته المختبرة فقط ولا يثبت سياق Base الحالي. Workflow يثبت هوية الحدث؛ لم تثبت هذه المراجعة آلية مستقلة لتجديد القبول عند تقدم SHA في فرع الأساس نفسه. يلزم دليل جديد بهوية Head/Base الفعلية قبل أي قبول لاحق، دون إعادة توجيه PR أو تغيير الفروع في هذه الجلسة.

قراءة تشغيلات الرأس أظهرت أيضًا Qoyod `35765107743=success`، وتشغيل Advertising لاحقًا `35768263583=skipped`؛ skipped ليس PASS ولا دليلًا على السياق الجديد. لا تُنقل فحوص MZ2/Release/Security/CodeQL للرأس القديم إلى هذا الرأس.

تقارير المنفذ غير المتحققة هنا: patch محلي `PR1132-R1-R5-from-405883c.patch` ببصمة معلنة `e803ef4ddbbddea1bb1ece4971642078574fa07629ea2b08e0d1bd853fc0beec`، و9 ملفات `+1003/-55`، و119 Backend+22 Node PASS محليًا، و25 React DOM مكتوبة وغير مشغلة. لم يُجلب أو يُراجع هذا patch في هذه النقطة، ولم تُعتمد أرقامه أو بصمته. وصف PR يصرح بتوقف رفع المصدر بسبب مراجعة أمان الأداة؛ لم تُجرّب هذه الجلسة أي مسار بديل لتجاوز ذلك الحجب.

الحكم الحالي: `CHANGES_REQUIRED_ISOLATED_CONTRACT`، مع `CURRENT_BASE_CONTEXT_NOT_VERIFIED`. اكتملت قراءة فرق workflow والتحقق من مصدر نتائج CI المحددة، لا قبول R1–R5 ولا التكامل أو HTTP أو Real Mongo أو التزامن المستديم أو UAT.

### حالة #1133 والكتابة والاختبارات في هذه النقطة

- فحوص الرأس السابق `9824ef45fb6689de56b5f3ed237a90a3725e25c6` أصبحت مكتملة وناجحة: Release Readiness `35768294111`، Security `35768294162`، Qoyod `35768293985`، Snapchat `35768293998`، CodeQL `35768294183`. هذه قراءة حالات GitHub، لا إعادة تشغيل ولا نقل نجاح إلى commit التوثيق الجديد.
- التغيير المحفوظ بهذا commit هو هذا الملف فقط، على فرع التسليم القائم وداخل Draft PR #1133، باستخدام blob السابق للمقارنة قبل الاستبدال. لا إنشاء ملف تسليم بديل ولا تعديل ملفات #1130/#1131/#1132.
- يوثق معرف commit الناتج وبصمته وقراءة ما بعد الحفظ وحالة CI الخاصة به في ملخص PR #1133 ونقطة #1006 بعد نجاح الكتابة؛ لا يوضع SHA ذاتي داخل الملف. لا تُعد عمليات المتابعة الخارجية مكتملة حتى تؤكدها استجابات GitHub.
- الكتابات المحلية اقتصرت على تنزيل أثري CI إلى `/mnt/data/mz2-pr1132-review-head-evidence.zip` و`/mnt/data/mz2-pr1132-review-base-evidence.zip`. لا استخراج أو تطبيق patch أو تحميل وحدات المشروع للتنفيذ.
- الاختبار المنفذ بواسطة المشرف: فحص بصمات وmanifest وقراءة هويات وسجلات فقط، exit 0. اختبارات التطبيق/HTTP/Real Mongo/Browser/UAT و`git apply --check`: **NOT_RUN في هذه الجلسة**. لم يُطلب تشغيل أو إعادة تشغيل CI يدويًا؛ أي CI تلقائي بعد commit التوثيق يراجع على SHA الخاص به.
- لا Merge، لا Deploy، لا تعديل Preview/Production، لا release lease أو intent، لا تعديل صلاحية أو فهرس أو إعداد تشغيلي، ولا كتابة مالية حقيقية أو تجريبية إلى قاعدة التطبيق. لا تُساوي هذه العبارة تحققًا حيًا من حالة البيئتين؛ لم تُستخدما أصلًا في هذه المراجعة.

### الخطوة الآمنة التالية ورسالة الاستئناف الحالية

الأولوية: استكمال مراجعة مصادر عقد #1131 المثبتة وربط بنوده الخمسة بالنواة الفعلية، مع بقاء التنفيذ محجوبًا؛ ومراجعة R1–R5 فقط بعد جلب أثره الحقيقي والتحقق منه دون تجاوز حجب الأداة. عند مراجعة #1132 لاحقًا يجب تحديث دليل سياق Base الحالي صراحة، لا الاكتفاء بالنجاح القديم أو العلامة الخضراء. #1130 يبقى عند حاجز V4 ثم check-only في worktree الصحيح.

«استأنف من Draft PR #1133 والفرع `chatgpt/mz2-supervisor-handoff-20260922`. اقرأ `docs/operations/MZ2-FIN-CUTOVER-001/SUPERVISOR-HANDOFF-CURRENT.md` كاملًا، خصوصًا SUP-20260922-02، ثم أعد قراءة Heads. #1130 عند 20400ff... ينتظر V4؛ #1131 تحرك إلى 29e4cd9... والإضافات التوثيقية مقروءة لكن لم يُقبل التنفيذ؛ #1132 عند 405883c... والفرق workflow فقط، وCI مثبتة على Base قديم ab3ef10... لا الفرع الحالي. R1–R5 المحلية غير مراجعة وغير منشورة وفق تقرير المنفذ. لا تنقل قبولًا، ولا تطبق أو تدمج أو تنشر أو تغير البيئات أو تكتب ماليًا. حدّث نفس الملف بعد كل نقطة إشراف مادية.»


---

## نقطة التحقق SUP-20260922-03 — مراجعة V4 الثابتة لـ PR #1130

التاريخ: 2026-09-22. هذه النقطة تكمّل SUP-20260922-02 ولا تمحوها. لا يوجد تفويض تطبيق أو اختبار أو دمج أو نشر.

### هويات GitHub قبل الحكم

- #1130: مفتوح، Draft، غير مدموج. Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035` وHead `20400fffb03594af8a38d6b4750c170233bd5f37`؛ لم يتغير الرأس البعيد.
- #1131: ما زال عند Head `29e4cd940b195df0164fe7bc74b07c77a0a02bfc`.
- #1132: ما زال عند Head `405883c34f81c5d17625083b11e3c608662023af` وبيانات PR ما زالت تعرض Base SHA `ab3ef10c5aedfe5e2190bc57bb8ed26afc1347f2`.
- #1133 قبل كتابة هذه النقطة: Head `4aef0e1b2d8498db675af872a68a1745453a8515`، والملف المرجعي blob `332ee1462c341547200031aad62c84747cd88298`.

### ما تحقق منه المشرف مباشرة من ملفات V4

المصادر المتاحة فعليًا في جلسة المراجعة كانت تقرير التحقق JSON، تقرير V4 Markdown، ومشغّل `p02-1130-v4-check-only.txt`. لم تكن نسخة Patch #1130 المنفصلة ولا Patch #1126 المنفصلة ضمن الملفات المركبة المتاحة للمشرف في هذه النقطة.

- SHA-256 المحسوب مباشرة لملف المشغّل: `c6fa5d8eed7d4ec4b061e0f2b63b24237dce1e2096671c2dffbf16fe1a21d8cb`.
- فُك payload الـBase64 المضمّن داخل المشغّل في الذاكرة فقط؛ SHA-256 المحسوب مباشرة للـPatch المضمّن: `14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a`.
- الـPatch المضمّن يحتوي بالضبط ثمانية مسارات #1130 المعلنة، ولا يحتوي hunk #1126. العد النصي المستقل: 1641 إضافة و4 حذف.
- أُعيد بناء الملفات الستة Python الجديدة الكاملة من unified diff في الذاكرة فقط، ونجح `ast.parse` عليها جميعًا. لم تُستورد الوحدات ولم يُنفذ كود التطبيق أو الاختبارات.
- تحليل AST للدالتين `prepare_contract_courier` و`post_contract_courier` يثبت أن أول تعليمة تنفيذية في كل منهما هي `require_native_contract_runtime()`.
- تحليل AST للدالة `_leg` لم يجد أي استدعاء لـ `float` أو `round`; المسار المقترح يستعمل Decimal/Context/quantize للتحقق من الهللة.
- عدّ اختبارات `test_*` في الملفين الجديدين أعاد 32 + 13 = 45 اختبارًا مكتوبًا. لم يُشغّل أي منها.

بهذا تُعد مشكلتا V3 الثابتتان المحددتان سابقًا — ترتيب البوابة وتحويل Decimal إلى float داخل `_leg` — مصححتين في **نص V4 المضمّن الذي روجع**. هذا لا يثبت قابلية تطبيقه على worktree ولا نجاحه وقت التشغيل.

### مراجعة مشغّل check-only

تحقق المشرف نصيًا من أن المشغّل:

- يثبت `/app` و`/tmp/mz2-p02-stage1.ZMgPW8/worktree`، Base/Head والفرع المحلي وبصمة registry وبصمات ملفات CONTRACT_SLICE الثلاثة وحالتها المتوقعة.
- لا يسمح إلا بقائمة Git قراءة ثابتة وثلاثة أوامر check على الـworktree: `git apply --check -` و`git apply --stat -` و`git apply --numstat -`.
- يرفض تشغيل أوامر apply الثلاثة على أي root غير الـworktree المحدد أو دون payload الذاكرة.
- يلتقط HEAD/branch/status/index/content-manifest قبل وبعد، ويقارن الحالة ولا ينظف أو يتراجع عند تغيرها.
- لا يحتوي تطبيقًا فعليًا للـPatch، ولا `--index` أو `--cached`، ولا تشغيل اختبارات أو قاعدة بيانات أو التطبيق، ولا ينشئ worktree بديلًا.

### ما بقي تقريرًا أو غير متحقق

- لم تُشغّل طرفية Emergent الأصلية في هذه المراجعة؛ لذلك `HEAD_BASE_BRANCH_CHECK` و`PRIOR_FILES_TARGET_HASH_CHECK` و`git apply --check/stat/numstat` وحالات `git status` الحقيقية قبل/بعد كلها **NOT_RUN/NOT_OBSERVED**.
- لا يجوز وصف أي فحص في container آخر أو نسخة مستودع أخرى بأنه target-worktree PASS؛ لم يُنفذ بديل كهذا.
- لأن ملف Patch #1130 المنفصل لم يكن مركبًا هنا، لم يُجرَ byte-for-byte compare مستقل بينه وبين payload المشغّل؛ المتحقق مباشرة هو أن payload نفسه يحمل SHA المعلن للـPatch. قيمة `embedded_patch_equals_patch_file=true` في JSON تبقى تقرير إعداد للحزمة في هذه النقطة.
- ملف Patch #1126 المنفصل لم يكن مركبًا هنا؛ بصمته `56283072cc9b38e8f323060241c89b1273b2397228d2d018cd46526ed9d3a050` ونطاقه المعلن بقيا تقريرًا غير معاد التحقق من بايتاته.
- اختبارات V4 كلها `NOT_RUN`. لا HTTP أو Real Mongo أو Frontend أو CI أو UAT شُغّل هنا.
- خدمة الأدلة الإنتاجية تبقى `NOT_INTEGRATED`، وربط الأستاذ والتقارير `NOT_CONNECTED`، وأول بيع COD خارجي يبقى محجوبًا بعقد `cod_base_receivable_required`.

### الحكم والبوابة التالية

الحكم التفصيلي:

`V4_STATIC_CORRECTIONS_VERIFIED / TARGET_CHECK_REQUIRED`

والحالة التشغيلية الملزمة تبقى:

`CHANGES_REQUIRED_NOT_READY_TO_APPLY`

نجاح المراجعة الثابتة لا يفوض التطبيق ولا الاختبارات ولا فتح P02. الخطوة الآمنة التالية فقط هي تشغيل **نفس مشغّل V4 المراجع** في طرفية Emergent الأصلية وعلى الـworktree المثبت، ثم حفظ وإرسال أقسام `git status --short` قبل/بعد وكتلة `P02_1130_V4_CHECK_ONLY_RESULT`. إذا كانت النتيجة `PATCH1130_V4_APPLY_CHECK_PASS_ONLY` فهذا يثبت قابلية التطبيق فقط؛ لا تطبيق ولا اختبار ولا Commit/Push/PR edit لـ#1130 إلا بتفويض مستقل لاحق.

### الكتابات والتغييرات في هذه النقطة

- الكتابة الوحيدة المصرح بها من المشرف هي إضافة هذه النقطة إلى ملف التسليم المرجعي نفسه على فرع #1133.
- لم يُعدل كود #1130 أو #1131 أو #1132، ولم يُطبق Patch، ولم تُشغّل اختبارات.
- لا Merge، لا Deploy، لا Preview/Production mutation، ولا كتابة مالية.
- `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-04 — نتيجة تشغيل V4 check-only في Emergent

التاريخ: 2026-09-22. هذه النقطة توثق تشغيل المستخدم لمشغّل V4 المراجع داخل طرفية Emergent الأصلية، دون تطبيق أو اختبار.

### هوية GitHub عند التوثيق

- #1130 ما زال مفتوحًا وDraft وغير مدموج: Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035`، Head `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1133 قبل كتابة هذه النقطة عند Head `1a7a3a40f89ad7b73dd9f7a4b6a707a32dd6c7e1`.

### نتيجة التشغيل الفعلية المرسلة من طرفية Emergent

المشغّل وصل إلى `/app` ونجح في أخذ snapshot له، لكنه لم يجد المسار المثبت للـworktree:

`/tmp/mz2-p02-stage1.ZMgPW8/worktree`

والنتيجة الفعلية:

`BLOCKED_EMERGENT_WORKTREE_ACCESS`

المشاهدات المهمة:

- `/app` قبل وبعد:
  - HEAD `6365a042dfcb125e81e5e198ea1ff1537373ce51`
  - branch `refs/heads/hotfix/prod-snap-meta-final`
  - `git status --short`: `?? .worktrees_p02_runtime.py`
  - index SHA-256 `42e67fc54cff66a9641f67bbdc0b3c0c8d07cb0860cf2fb5fb2eb850ec5bc5c0`
  - content manifest SHA-256 `e7d7bb4bad9b91296b3f0e6d2043ff0b262d823868c5c8898471542605330a19`
  - file count 2757
- snapshot قبل وبعد لـ`/app` متطابق في البيانات المرسلة.
- worktree snapshot قبل وبعد: unavailable/null.
- `git apply --check` و`--stat` و`--numstat`: لم تُشغّل.
- `commands` فارغة، `applied=false`, `tests=NOT_RUN`, `commit=NONE`, `push=NONE`, `pr_edited=false`.
- Preview وProduction معلنان unchanged by this check؛ لم يُستخدم أي مسار تطبيق أو كتابة مالية.

### الحكم

`TARGET_WORKTREE_MISSING / CHECK_NOT_RUN`

ويظل الحكم التشغيلي:

`CHANGES_REQUIRED_NOT_READY_TO_APPLY`

لا يوجد PASS لقابلية التطبيق بعد. ثبات `/app` لا يعوض غياب الـworktree الهدف.

### الخطوة الآمنة التالية

تشخيص قراءة فقط لمواقع worktrees المسجلة في Git، دون إنشاء أو حذف أو prune أو reset أو checkout:

- `git -C /app worktree list --porcelain`
- فحص وجود المسار القديم ودليل الأب فقط.
- لا يُنشأ worktree جديد ولا يُعاد استخدام مسار مختلف حتى يراجع المشرف هوية HEAD/branch/common-dir ويقرر الخطوة التالية.

الحواجز لم تتغير: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Merge/Deploy/Preview/Production mutation ولا كتابة مالية.


---

## نقطة التحقق SUP-20260922-05 — اكتشاف worktree #1130 المفقود ماديًا

التاريخ: 2026-09-22. هذه النقطة توثق مخرجات فحص القراءة فقط التي أرسلها المستخدم من طرفية Emergent الأصلية بعد SUP-20260922-04.

### النتيجة المتحققة من المخرجات

`git -C /app worktree list --porcelain` ما زال يسجل worktree خاص #1130:

- path: `/tmp/mz2-p02-stage1.ZMgPW8/worktree`
- HEAD: `20400fffb03594af8a38d6b4750c170233bd5f37`
- branch: `refs/heads/local/p02-stage1-ZMgPW8`
- lock reason: `P02 PR1130 isolated stage1; preserve`

لكن الفحص المباشر للمسار أعاد `No such file or directory` لكل من دليل الأب والـworktree نفسه. أي أن سجل Git الإداري ما زال موجودًا ومقفولًا، بينما شجرة العمل المادية في `/tmp` اختفت.

`/app` بقي عند:

- HEAD `6365a042dfcb125e81e5e198ea1ff1537373ce51`
- branch `refs/heads/hotfix/prod-snap-meta-final`
- status الوحيد الظاهر: `?? .worktrees_p02_runtime.py`

### الحكم

`LOCKED_WORKTREE_REGISTRATION_PRESENT / WORKTREE_DIRECTORY_MISSING`

لا يجوز اعتبار المسار prunable أو حذفه تلقائيًا لأن Git يعرضه مقفولًا صراحةً بسبب `preserve`. لا `git worktree prune` ولا `unlock` ولا `remove` ولا إنشاء worktree بديل قبل فحص metadata الإدارية والمرجع المحلي قراءة فقط.

يبقى #1130 عند `CHANGES_REQUIRED_NOT_READY_TO_APPLY`; `git apply --check` لم يُشغّل بعد.

### الخطوة الآمنة التالية

فحص قراءة فقط لهوية metadata الخاصة بالـworktree المقفول والفرع المحلي، بما يشمل:

- common git dir وworktrees admin dir.
- مرجع `refs/heads/local/p02-stage1-ZMgPW8` وقيمته.
- ملفات metadata المطابقة التي تشير إلى `/tmp/mz2-p02-stage1.ZMgPW8/worktree`.
- محتوى `gitdir`, `HEAD`, `commondir`, `locked` وبصمة/حجم `index` إن وجدت.

لا تعديل أو حذف للـmetadata في هذه الخطوة.

الحواجز لم تتغير: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا تطبيق أو اختبارات أو Merge/Deploy أو Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-06 — metadata worktree #1130 محفوظة والـindex متاح

التاريخ: 2026-09-22. هذه النقطة توثق مخرجات فحص metadata القراءة فقط من طرفية Emergent الأصلية.

### الهوية المتحققة

- #1130 ما زال مفتوحًا وDraft وغير مدموج عند Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035` وHead `20400fffb03594af8a38d6b4750c170233bd5f37`.
- سجل worktree الإداري المطابق موجود في `.git/worktrees/worktree`.
- `gitdir`: `/tmp/mz2-p02-stage1.ZMgPW8/worktree/.git`.
- `HEAD` داخل metadata: `ref: refs/heads/local/p02-stage1-ZMgPW8`.
- المرجع المحلي `refs/heads/local/p02-stage1-ZMgPW8` ما زال يشير إلى `20400fffb03594af8a38d6b4750c170233bd5f37`.
- `commondir`: `../..`.
- lock reason: `P02 PR1130 isolated stage1; preserve`.
- ملف index الإداري موجود: size `325797`, mode `600`, SHA-256 `e99e4d2a19781ee5bffe7c04956e9b72b795034458da5dadee05c59f754cd1de`.

### /app

- HEAD `6365a042dfcb125e81e5e198ea1ff1537373ce51`.
- branch `refs/heads/hotfix/prod-snap-meta-final`.
- status الوحيد المرسل: `?? .worktrees_p02_runtime.py`.

### الحكم

`WORKTREE_ADMIN_METADATA_INTACT / PHYSICAL_TREE_MISSING / INDEX_PRESERVED`

لا يوجد بعد دليل أن محتوى الـindex مطابق تمامًا لـHEAD أو يحتوي staged changes. كذلك الـindex لا يثبت أو يستعيد تلقائيًا أي ملفات untracked أو تعديلات working-tree غير staged كانت موجودة قبل اختفاء مجلد `/tmp`.

لذلك لا يُنشأ worktree جديد ولا يُعمل prune/unlock/remove/repair قبل فحص الفرق بين الـindex المحفوظ وHEAD قراءة فقط.

### الخطوة الآمنة التالية

تشغيل تشخيص قراءة فقط على gitdir الإداري `.git/worktrees/worktree`:

- `git diff --cached --name-status HEAD`
- `git diff --cached --stat HEAD`
- `git status` غير مطلوب لأن شجرة العمل المادية مفقودة.
- فحص ملفات حالة Git الإدارية مثل `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REBASE_HEAD` ووجود أدلة rebase/merge دون تعديل.

لا كتابة أو تنظيف أو استرجاع في هذه الخطوة.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Patch apply ولا Tests ولا Merge/Deploy ولا Preview/Production mutation ولا كتابة مالية.


---

## نقطة التحقق SUP-20260922-07 — index worktree #1130 مطابق لـHEAD ولا توجد عملية Git عالقة

التاريخ: 2026-09-22. هذه النقطة توثق مخرجات فحص القراءة فقط للـindex الإداري الخاص بالـworktree المفقود.

### النتيجة المتحققة

- admin gitdir: `/app/.git/worktrees/worktree` موجود.
- HEAD الإداري والفرع المحلي كلاهما يشيران إلى `20400fffb03594af8a38d6b4750c170233bd5f37` / `refs/heads/local/p02-stage1-ZMgPW8`.
- `git diff --cached --name-status HEAD`: فارغ.
- `git diff --cached --stat HEAD`: فارغ.
- لا توجد `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `REBASE_HEAD`, `BISECT_START`, ولا أدلة `rebase-merge`, `rebase-apply`, `sequencer`.
- index SHA-256 أعيد التحقق منه وبقي `e99e4d2a19781ee5bffe7c04956e9b72b795034458da5dadee05c59f754cd1de`.
- ضمن المسارات الثلاثة للـCONTRACT_SLICE، الـindex يحتوي فقط الملف المتتبع `backend/tests/test_courier_cod_fee_tiers_v2.py` عند blob `b0044e15a48ee15b3669e42f94e7b57f336187c4`. الملفان الآخران كانا untracked في الحالة التاريخية، ولذلك غيابهما من الـindex متوقع ولا يثبت فقد بايتاتهما نهائيًا.
- `/app` بقي عند `6365a042dfcb125e81e5e198ea1ff1537373ce51` على `refs/heads/hotfix/prod-snap-meta-final` مع `?? .worktrees_p02_runtime.py`.

### الحكم

`INDEX_MATCHES_HEAD / NO_GIT_OPERATION_IN_PROGRESS / WORKING_TREE_ONLY_STATE_UNRESOLVED`

الـindex لا يحمل staged changes تحتاج إنقاذًا، لكن الحالة التاريخية للـworktree كانت تحتوي ملفًا متتبعًا معدلًا وملفين untracked للـCONTRACT_SLICE. هذه البايتات ليست ممثلة في الـindex، لذلك لا يُعاد إنشاء أو إصلاح worktree قبل محاولة العثور على نسخها المحفوظة قراءة فقط.

### الخطوة الآمنة التالية

بحث قراءة فقط عن نسخ CONTRACT_SLICE أو بصماتها في `/app` وملف المساعدة `.worktrees_p02_runtime.py`، وفحص نسخة HEAD للملف المتتبع لمقارنة SHA-256 مع البصمة التاريخية. لا `worktree add/repair/prune/unlock/remove` ولا checkout/reset/apply.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا تطبيق أو اختبارات أو Merge/Deploy أو Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-08 — CONTRACT_SLICE قابل للاسترجاع بايتًا-ببايت من الأثر المعتمد

التاريخ: 2026-09-22. هذه النقطة تكمّل فحص الاسترجاع دون أي كتابة إلى Emergent.

### نتيجة البحث في /app و/tmp المرسلة من المستخدم

- الفرع المحلي بقي عند `20400fffb03594af8a38d6b4750c170233bd5f37`.
- نسخة HEAD والـindex من `backend/tests/test_courier_cod_fee_tiers_v2.py` لها SHA-256 `5d497abe2697fa23187e9d91dbae4fd628b79fcdce316815efceb5b9c64984c4`، وليست البصمة التاريخية المعدلة.
- البحث في `/app` و`/tmp` لم يجد أي ملف يطابق البصمات التاريخية الثلاث؛ النسخ الموجودة من الملف المتتبع كلها عند SHA-256 `5d497a...`.
- `/app/.worktrees_p02_runtime.py` موجود ببصمة `322b6018f17f2de0229c303264e95961fb3cc251568e3caca1671dc2a76c9d4e` لكنه لم يظهر في grep كمصدر للملفات الثلاثة.

### الاسترجاع المستقل من آثار ChatGPT المحفوظة

عُثر في المكتبة على `p02-contract-slice-verified-runner.txt`، وهو المشغّل المقيد سابقًا ببصمة الملف المعتمد `p02-stage1-contracts(1).txt`. تم materialize للقراءة البرمجية فقط خارج Emergent؛ لم يُشغّل.

المشغّل يحتوي:
- المصدر الكامل `MODULE` للملف `backend/accounting_shipping_contracts.py`.
- المصدر الكامل `TESTS` للملف `backend/tests/test_mz2_shipping_contracts.py`.
- خوارزمية حتمية تولد النسخة المعدلة من `backend/tests/test_courier_cod_fee_tiers_v2.py` من Blob HEAD المثبت `b0044e15a48ee15b3669e42f94e7b57f336187c4`.

تحقق المشرف حسابيًا من البايتات دون تنفيذ شيفرة التطبيق:

1. `MODULE.encode()`:
   SHA-256 `9f25d896d9835dc36a29e91f2dab90de2cf8f1a4c2cec18f01d9fb097863b7c8`.
2. `TESTS.encode()`:
   SHA-256 `61168e55eb400da907f8d0fb795f7c28155e968d22ff4f5b45eae14b8b83f0b2`.
3. جُلب ملف HEAD الحقيقي من GitHub عند #1130، Blob `b0044e15a48ee15b3669e42f94e7b57f336187c4`; SHA-256 بايتاته يطابق نتيجة الطرفية المرسلة `5d497abe2697fa23187e9d91dbae4fd628b79fcdce316815efceb5b9c64984c4`. بعد تطبيق تحويلات النص الحتمية نفسها الواردة في المشغّل، أعادت النسخة الناتجة:
   SHA-256 `ff35204aaaecfb897869341c82b9c86b82d7580ce70fb397c617edfd76933f9c`.

إذن البصمات الثلاث التاريخية ليست مجرد تقارير؛ بايتاتها قابلة لإعادة البناء بصورة حتمية من أثر معتمد محفوظ + Blob GitHub مثبت.

### الحكم

`CONTRACT_SLICE_BYTES_RECOVERABLE_EXACTLY / WORKTREE_RECONSTRUCTION_CAN_BE_PLANNED`

لا يعني هذا أن worktree أُعيد بناؤه؛ لم تحدث أي كتابة في Emergent بعد. لا يزال `git apply --check` لـV4 غير منفذ.

### قيد الاسترجاع

أي استرجاع لاحق يجب:
- يعيد نفس المسار الإداري والفرع المحلي وHEAD فقط.
- لا يغير الـindex المحفوظ ولا refs.
- يعيد tracked files من الـindex/HEAD إلى شجرة عمل جديدة فارغة.
- يعيد الملفات الثلاثة فقط إلى البصمات التاريخية المثبتة.
- يتحقق بعد الكتابة من status المتوقع بالضبط:
  ` M backend/tests/test_courier_cod_fee_tiers_v2.py`
  `?? backend/accounting_shipping_contracts.py`
  `?? backend/tests/test_mz2_shipping_contracts.py`
- لا يشغّل اختبارات ولا يطبق V4 في خطوة الاسترجاع نفسها.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Merge/Deploy/Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-09 — إعداد مشغّل استرجاع worktree #1130 دون تنفيذ

التاريخ: 2026-09-22. بعد إثبات أن بايتات CONTRACT_SLICE الثلاثة قابلة للاسترجاع حتميًا، أُعد خارج Emergent مشغّل استرجاع محلي باسم:

`p02-1130-recover-original-worktree-only.sh`

SHA-256:
`e41bb9d797d11765c6342f3455e82b7f9285af74411ac40bae58e1409894b2f9`

حجمه 41610 بايت، ونجح `bash -n` عليه. المشغّل **لم يُنفذ**.

### حدود المشغّل

قبل أي كتابة يتحقق من:
- /app HEAD `6365a042dfcb125e81e5e198ea1ff1537373ce51` والفرع `hotfix/prod-snap-meta-final`.
- status المتوقع لـ/app: `?? .worktrees_p02_runtime.py`.
- admin gitdir `/app/.git/worktrees/worktree`.
- local branch `refs/heads/local/p02-stage1-ZMgPW8` عند `20400fffb03594af8a38d6b4750c170233bd5f37`.
- admin HEAD/commondir/locked، وindex SHA-256 `e99e4d2a19781ee5bffe7c04956e9b72b795034458da5dadee05c59f754cd1de`.
- أن المسار `/tmp/mz2-p02-stage1.ZMgPW8` ما زال غير موجود.

إذا اجتازت الحواجز، ينشئ فقط الشجرة المادية المفقودة ويربط `.git` بنفس admin gitdir، ثم يستخدم `checkout-index -a -f` لإخراج الملفات المتتبعة من الـindex المحفوظ إلى شجرة عمل فارغة؛ لا يغير index أو ref أو branch.

بعد ذلك يعيد فقط بايتات CONTRACT_SLICE الثلاثة المثبتة:
- `backend/accounting_shipping_contracts.py` -> `9f25d896d9835dc36a29e91f2dab90de2cf8f1a4c2cec18f01d9fb097863b7c8`.
- `backend/tests/test_mz2_shipping_contracts.py` -> `61168e55eb400da907f8d0fb795f7c28155e968d22ff4f5b45eae14b8b83f0b2`.
- `backend/tests/test_courier_cod_fee_tiers_v2.py` -> `ff35204aaaecfb897869341c82b9c86b82d7580ce70fb397c617edfd76933f9c`.

ويتحقق من أن status النهائي يتكون فقط من نفس الحالات التاريخية الثلاث بغض النظر عن ترتيب العرض:
- modified غير staged للملف المتتبع.
- untracked للملفين الجديدين.

كما يعيد التحقق من index SHA و/app بعد الاسترجاع.

### ما لا يفعله

لا `git add`, لا reset/clean/prune/unlock/remove/repair، لا checkout/switch للفرع، لا Patch V4، لا اختبارات، لا Commit/Push/PR edit، لا Preview/Production ولا كتابة مالية.

الحكم:
`RECOVERY_RUNNER_PREPARED_NOT_EXECUTED`

الخطوة الآمنة التالية هي تشغيل هذا المشغّل وحده في طرفية Emergent الأصلية، ومراجعة `P02_WORKTREE_RECOVERY_RESULT` قبل العودة إلى V4 check-only.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-10 — محاولة تشغيل مشغّل الاسترجاع انتهت بـ exit 1 دون سبب ظاهر

التاريخ: 2026-09-22. أرسل المستخدم لقطة من طرفية Emergent تُظهر فقط:

`The terminal process "/bin/bash" terminated with exit code: 1.`

لا تحتوي اللقطة على سطر `RESULT=` أو كتلة `P02_WORKTREE_RECOVERY_RESULT`، لذلك لا يمكن تحديد هل التوقف حدث قبل أي كتابة أم بعد إنشاء جزء من الشجرة المادية. لا يُعاد تشغيل المشغّل قبل فحص الحالة الحالية قراءة فقط.

تحقق GitHub عند هذه النقطة:
- #1130 ما زال مفتوحًا وDraft وغير مدموج عند Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035` وHead `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1133 قبل هذا التحديث عند Head `cb22d557e04f74d8ff70831b3a844c73ee522ff4`.

الحكم:
`RECOVERY_ATTEMPT_EXIT_1 / CURRENT_RECOVERY_STATE_UNKNOWN`

الخطوة الآمنة التالية: فحص قراءة فقط لوجود `/tmp/mz2-p02-stage1.ZMgPW8/worktree`، HEAD/branch/status إن وجد، بصمات الملفات الثلاثة إن وجدت، وبقاء index/admin metadata و/app دون تغيير. لا إعادة تشغيل ولا تنظيف ولا repair/prune/unlock/remove/apply قبل هذه المشاهدة.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-11 — الاسترجاع أعاد الشجرة والـCONTRACT_SLICE بدقة، مع تغير بصمة index البايتية

التاريخ: 2026-09-22. هذه النقطة توثق فحص ما بعد محاولة الاسترجاع التي انتهت سابقًا بـ exit 1.

### ما تحقق مباشرة من مخرجات طرفية Emergent

المسار المادي عاد موجودًا:

`/tmp/mz2-p02-stage1.ZMgPW8/worktree`

وهو يقرأ:
- HEAD `20400fffb03594af8a38d6b4750c170233bd5f37`
- branch `refs/heads/local/p02-stage1-ZMgPW8`

وحالة worktree الحالية بالضبط:
- ` M backend/tests/test_courier_cod_fee_tiers_v2.py`
- `?? backend/accounting_shipping_contracts.py`
- `?? backend/tests/test_mz2_shipping_contracts.py`

البصمات الفعلية بعد الاسترجاع:
- `backend/accounting_shipping_contracts.py` -> `9f25d896d9835dc36a29e91f2dab90de2cf8f1a4c2cec18f01d9fb097863b7c8`
- `backend/tests/test_mz2_shipping_contracts.py` -> `61168e55eb400da907f8d0fb795f7c28155e968d22ff4f5b45eae14b8b83f0b2`
- `backend/tests/test_courier_cod_fee_tiers_v2.py` -> `ff35204aaaecfb897869341c82b9c86b82d7580ce70fb397c617edfd76933f9c`

وهي مطابقة تمامًا للبصمات التاريخية المعتمدة.

admin metadata بقي:
- HEAD -> `refs/heads/local/p02-stage1-ZMgPW8`
- gitdir -> المسار المستعاد
- commondir -> `../..`
- lock -> `P02 PR1130 isolated stage1; preserve`

أما SHA-256 لملف index فقد أصبح:
`faed3c238a093b90ff7a29e741a55b89abd344693820ed098ee02c98a808d0f5`

بدل البصمة السابقة:
`e99e4d2a19781ee5bffe7c04956e9b72b795034458da5dadee05c59f754cd1de`

### تفسير exit 1

مراجعة مشغّل الاسترجاع تثبت أن آخر حاجز قبل طباعة `CONTRACT_SLICE_WORKTREE_RECOVERED_EXACTLY` يقارن SHA-256 البايتية لملف index بالبصمة القديمة. بما أن الشجرة والملفات الثلاثة والحالة وصلت إلى القيم المتوقعة، ثم ظهرت بصمة index الجديدة، فالخروج 1 يتفق مع توقف المشغّل عند حاجز `BLOCKED_INDEX_CHANGED_AFTER_RECOVERY`.

لا يُعتبر تغير البصمة البايتية وحده staged change أو تغيرًا منطقيًا في index؛ يلزم تحقق قراءة فقط من `git diff --cached HEAD` وentries الحالية قبل متابعة V4.

### /app

بقي:
- HEAD `6365a042dfcb125e81e5e198ea1ff1537373ce51`
- branch `refs/heads/hotfix/prod-snap-meta-final`
- status `?? .worktrees_p02_runtime.py`

### الحكم

`WORKTREE_AND_CONTRACT_SLICE_RECOVERED_EXACTLY / INDEX_BYTE_HASH_CHANGED / SEMANTIC_INDEX_CHECK_REQUIRED`

#1130 ما زال `CHANGES_REQUIRED_NOT_READY_TO_APPLY`، وV4 check-only لم يُعد تشغيله بعد.

### الخطوة الآمنة التالية

فحص قراءة فقط للـindex بعد الاسترجاع:
- `git diff --cached --name-status HEAD`
- `git diff --cached --stat HEAD`
- `git ls-files --stage` لمسارات CONTRACT_SLICE.
- لا reset/add/checkout/prune/unlock/remove/repair/apply.

إذا ظل cached diff فارغًا ولم توجد staged entries مختلفة، تُعامل بصمة index الجديدة كاختلاف تمثيل/metadata لا اختلاف محتوى، ثم يمكن إعادة تشغيل V4 check-only نفسه على worktree المستعاد.

الحواجز لم تتغير: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Merge/Deploy/Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-12 — تحقق دلالي من index بعد استرجاع worktree #1130

التاريخ: 2026-09-22. هذه النقطة توثق فحص القراءة فقط بعد استرجاع الشجرة المادية.

### النتيجة المتحققة من طرفية Emergent

- `git diff --cached --name-status HEAD`: فارغ.
- `git diff --cached --stat HEAD`: فارغ.
- مدخل CONTRACT_SLICE المتتبع في index ما زال:
  `100644 b0044e15a48ee15b3669e42f94e7b57f336187c4 0 backend/tests/test_courier_cod_fee_tiers_v2.py`.
- SHA-256 البايتية الحالية لملف index:
  `faed3c238a093b90ff7a29e741a55b89abd344693820ed098ee02c98a808d0f5`.
- حالة worktree ما زالت بالضبط:
  - ` M backend/tests/test_courier_cod_fee_tiers_v2.py`
  - `?? backend/accounting_shipping_contracts.py`
  - `?? backend/tests/test_mz2_shipping_contracts.py`
- `/app` بقي عند HEAD `6365a042dfcb125e81e5e198ea1ff1537373ce51` / `refs/heads/hotfix/prod-snap-meta-final` مع `?? .worktrees_p02_runtime.py`.

### الحكم

`INDEX_SEMANTICALLY_UNCHANGED / RECOVERED_WORKTREE_READY_FOR_V4_CHECK_ONLY`

اختلاف SHA لملف index عن البصمة السابقة لا يمثل staged content change؛ الـcached diff فارغ ومدخل الملف المتتبع ما زال Blob HEAD نفسه. لا حاجة لإعادة تشغيل recovery ولا لتعديل index.

### هوية GitHub عند القرار

- #1130: مفتوح، Draft، غير مدموج، Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035`، Head `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1131: Head `29e4cd940b195df0164fe7bc74b07c77a0a02bfc`.
- #1132: Head `405883c34f81c5d17625083b11e3c608662023af`، Base SHA المعلن في PR ما زال `ab3ef10c5aedfe5e2190bc57bb8ed26afc1347f2`.
- #1133 قبل كتابة هذه النقطة: Head `c42d4a99b0f9acd317c0c40000acc95d9ddf1a5d`.

### الخطوة الآمنة التالية

تشغيل **نفس** مشغّل V4 check-only المراجع سابقًا، SHA-256:
`c6fa5d8eed7d4ec4b061e0f2b63b24237dce1e2096671c2dffbf16fe1a21d8cb`

على worktree المستعاد. لا تشغيل recovery مرة أخرى، ولا تطبيق Patch، ولا اختبارات. المطلوب فقط نتيجة:
- `git status --short` قبل/بعد لـ/app والـworktree.
- `P02_1130_V4_CHECK_ONLY_RESULT`.
- `git apply --stat` و`git apply --numstat`.

إذا كانت النتيجة `PATCH1130_V4_APPLY_CHECK_PASS_ONLY` فهي تثبت قابلية التطبيق فقط ولا تفوض التطبيق أو الاختبارات أو Commit/Push.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-13 — V4 check-only لـ PR #1130 اجتاز الهدف الأصلي

التاريخ: 2026-09-22. هذه النقطة توثق المخرجات الفعلية من طرفية Emergent الأصلية بعد استرجاع worktree والتحقق الدلالي من index.

### هوية GitHub عند القرار

- #1130: مفتوح، Draft، غير مدموج. Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035`، Head `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1131: Head `29e4cd940b195df0164fe7bc74b07c77a0a02bfc`.
- #1132: Head `405883c34f81c5d17625083b11e3c608662023af`، Base SHA في PR `ab3ef10c5aedfe5e2190bc57bb8ed26afc1347f2`.
- #1133 قبل كتابة هذه النقطة: Head `db1d3dfdf479057f2059c159fb8f5492d0d63142`.

### نتيجة V4 check-only الفعلية

النتيجة:
`PATCH1130_V4_APPLY_CHECK_PASS_ONLY`

الهوية داخل التقرير:
- worktree `/tmp/mz2-p02-stage1.ZMgPW8/worktree`
- Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035`
- Head `20400fffb03594af8a38d6b4750c170233bd5f37`
- branch `refs/heads/local/p02-stage1-ZMgPW8`
- patch SHA-256 `14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a`
- merge-base = Base نفسه
- origin = `AMASI-SA/AMASI-SA`

أوامر الفحص الثلاثة:
- `git apply --check -` -> exit 0
- `git apply --stat -` -> exit 0
- `git apply --numstat -` -> exit 0

النطاق الفعلي:
- 8 ملفات
- 1641 إضافة
- 4 حذف
- numstat مطابق للمسارات والأعداد المعلنة.

### ثبات الحالة

`app_and_worktree_unchanged=true`.

حالة /app قبل وبعد بقيت:
`?? .worktrees_p02_runtime.py`

حالة worktree قبل وبعد بقيت بالضبط:
- ` M backend/tests/test_courier_cod_fee_tiers_v2.py`
- `?? backend/accounting_shipping_contracts.py`
- `?? backend/tests/test_mz2_shipping_contracts.py`

بصمات CONTRACT_SLICE الثلاثة قبل وبعد متطابقة:
- `9f25d896d9835dc36a29e91f2dab90de2cf8f1a4c2cec18f01d9fb097863b7c8`
- `61168e55eb400da907f8d0fb795f7c28155e968d22ff4f5b45eae14b8b83f0b2`
- `ff35204aaaecfb897869341c82b9c86b82d7580ce70fb397c617edfd76933f9c`

لم يطبق شيء: `applied=false`; الاختبارات `NOT_RUN`; لا Commit/Push/PR edit.

### الحكم

`V4_TARGET_APPLY_CHECK_PASS / READY_FOR_CONTROLLED_ISOLATED_APPLY`

هذا يلغي فقط مانع قابلية التطبيق السابق `CHANGES_REQUIRED_NOT_READY_TO_APPLY` الخاص بـV4. لا يفتح P02 ولا يثبت نجاح الاختبارات أو التكامل.

يُسمح بالخطوة التالية فقط:
- تطبيق **Patch #1130 V4 نفسه وبصمته نفسها** على worktree المعزول نفسه.
- لا تطبيق Patch #1126 في هذه الخطوة.
- لا تشغيل اختبارات في خطوة التطبيق.
- بعد التطبيق: تحقق من Head/branch/status، بصمات CONTRACT_SLICE، وأن التغيير الإضافي يقتصر على مسارات V4 الثمانية فوق الحالة التاريخية الثلاثية.
- لا Commit/Push/PR edit قبل مراجعة نتيجة التطبيق.

الحواجز العامة مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Merge/Deploy/Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-14 — إعداد مشغّل تطبيق V4 المعزول فقط

التاريخ: 2026-09-22. بعد قبول `PATCH1130_V4_APPLY_CHECK_PASS_ONLY`، أُعد خارج Emergent مشغّل تطبيق واحد مقيد باسم:

`p02-1130-v4-apply-only.sh`

SHA-256:
`dcd64ff12b32b6d53f4f6d8d18c426e02e8d3819b8f03630b6c77fe3bf80d86a`

الحجم: 129394 بايت. `bash -n`: PASS. المشغّل لم يُنفذ بعد.

### حدود المشغّل

قبل التطبيق يعيد التحقق من:
- /app HEAD/branch/status.
- worktree `/tmp/mz2-p02-stage1.ZMgPW8/worktree`.
- Head `20400fffb03594af8a38d6b4750c170233bd5f37` والفرع `refs/heads/local/p02-stage1-ZMgPW8`.
- Base/merge-base وorigin.
- عدم وجود staged changes.
- الحالة التاريخية الثلاثية فقط.
- بصمات CONTRACT_SLICE الثلاثة.
- Blob وworking bytes لـ`backend/accounting_module_contract.py`.
- عدم وجود مسارات V4 الجديدة مسبقًا.
- SHA-256 للـPatch المضمّن `14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a`.
- `git apply --check -` مرة أخيرة قبل التطبيق.

بعد ذلك ينفذ فقط:
`git apply -`
على الـworktree المعزول نفسه.

بعد التطبيق يتحقق من:
- Head/branch لم يتغيرا.
- index لا يحمل staged changes وبصمته البايتية لم تتغير خلال التطبيق.
- /app لم يتغير.
- CONTRACT_SLICE الثلاثة لم تتغير.
- status النهائي يتكون فقط من الحالة التاريخية الثلاثية + تعديل registry + سبعة مسارات V4 الجديدة.
- يطبع SHA-256 لكل مسار V4 بعد التطبيق.

### ما لا يفعله

لا Patch #1126، لا اختبارات، لا `git add`، لا Commit، لا Push، لا PR edit، لا Merge/Deploy، لا Preview/Production أو كتابة مالية. لا ينظف أو يعمل rollback إذا وجد اختلافًا بعد التطبيق؛ يتوقف ويحفظ الحالة للمراجعة.

الحكم:
`V4_APPLY_RUNNER_PREPARED_NOT_EXECUTED`

الخطوة الآمنة التالية: تشغيل هذا المشغّل وحده في طرفية Emergent الأصلية، ثم مراجعة `P02_1130_V4_APPLY_ONLY_RESULT` قبل أي اختبار.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-15 — إلغاء لصق مشغّل التطبيق دون أثر

التاريخ: 2026-09-22. بعد توقف لصق مشغّل التطبيق الضخم داخل طرفية Emergent وظهور موجه `>`، أوقف المستخدم الإدخال ثم نفذ فحصًا قراءة فقط.

### النتيجة المتحققة

- worktree HEAD ما زال `20400fffb03594af8a38d6b4750c170233bd5f37`.
- status ما زال بالضبط CONTRACT_SLICE التاريخي:
  - ` M backend/tests/test_courier_cod_fee_tiers_v2.py`
  - `?? backend/accounting_shipping_contracts.py`
  - `?? backend/tests/test_mz2_shipping_contracts.py`
- كل مسارات V4 الجديدة السبعة التي فُحصت غير موجودة.
- لم يظهر أي أثر لتطبيق جزئي لـV4.

تحقق GitHub:
- #1130 ما زال مفتوحًا وDraft وغير مدموج عند Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035` وHead `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1133 قبل كتابة هذه النقطة عند Head `51c9c73a60683a60059c129d040325512b75c20a`.

### الحكم

`ABORTED_PASTE_NO_APPLY_EFFECT / READY_FOR_FILE_BASED_ISOLATED_APPLY`

لا يعاد لصق المشغّل الضخم. الخطوة التالية الآمنة هي استخدام ملف Patch V4 نفسه بعد رفعه إلى بيئة Emergent، والتحقق من SHA-256 ثم `git apply --check` ثم `git apply` بأوامر قصيرة، مع فحص status والبصمات بعدها.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا اختبارات أو Commit/Push/PR edit أو Merge/Deploy أو Preview/Production mutation أو كتابة مالية قبل مراجعة نتيجة التطبيق.


---

## نقطة التحقق SUP-20260922-16 — محاولة file-based apply لم تبدأ لأن ملف Patch غير موجود

التاريخ: 2026-09-22. نفّذ المستخدم أوامر التطبيق القصيرة في طرفية Emergent، لكن المسار المتوقع للـPatch:

`/tmp/p02-1130-isolated-review-v4.patch`

لم يكن موجودًا.

### النتيجة المتحققة

- `sha256sum` أعاد: `No such file or directory`.
- `git apply --check` لم يعمل لأن ملف Patch غير موجود.
- `git apply` لم يعمل للسبب نفسه.
- worktree HEAD بقي `20400fffb03594af8a38d6b4750c170233bd5f37`.
- الحالة بقيت بالضبط:
  - ` M backend/tests/test_courier_cod_fee_tiers_v2.py`
  - `?? backend/accounting_shipping_contracts.py`
  - `?? backend/tests/test_mz2_shipping_contracts.py`
- بصمات CONTRACT_SLICE الثلاثة بقيت مطابقة للقيم المعتمدة.
- `git diff --stat` بعد المحاولة أظهر فقط تعديل الملف المتتبع التاريخي: 7 إضافات / 6 حذوفات، ولا توجد مسارات V4 مطبقة.

تحقق GitHub عند التوثيق:
- #1130 ما زال مفتوحًا وDraft وغير مدموج عند Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035` وHead `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1133 قبل كتابة هذه النقطة عند Head `c39ecc0df9746d54047da14d9e0714913c024185`.

### الحكم

`PATCH_TRANSFER_MISSING / NO_APPLY_EFFECT`

لا توجد حاجة لأي rollback أو استرجاع جديد. V4 ما زال جاهزًا للتطبيق المعزول بمجرد نقل بايتات Patch الصحيحة إلى Emergent.

الخطوة الآمنة التالية: نقل Patch V4 إلى `/tmp` بطريقة قصيرة ومتحققة من SHA-256، ثم تنفيذ apply-check والتطبيق. لا اختبارات أو Commit/Push قبل مراجعة نتيجة التطبيق.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-17 — تجهيز نقل Patch V4 عبر GitHub Issue دون رفع يدوي

التاريخ: 2026-09-22. بعد فشل المسار اليدوي لعدم وجود ملف Patch في `/tmp`، جُهز مسار نقل موثق عبر Issue #1006 دون تعديل فرع #1130.

أُعيدت قراءة Patch V4 المحفوظ في Library كاملًا (1702 سطرًا)، وأعيد تجميع النص مع newline النهائي. الحجم الناتج UTF-8 = **89921 بايت**، مطابق تمامًا لحجم ملف Patch الأصلي المحفوظ. ثم رُمز Base64 وقُسم إلى ثلاثة أجزاء في تعليقات Issue #1006:

- comment `5782817671` — part 1/3
- comment `5782818225` — part 2/3
- comment `5782818706` — part 3/3

كل تعليق يحمل هوية Patch المطلوبة:
`14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a`

الهدف من هذه التعليقات نقل البايتات فقط. الطرفية يجب أن:
1. تجلب الأجزاء الثلاثة من GitHub API العام.
2. تفك Base64 في الذاكرة.
3. تتحقق من الحجم 89921 ومن SHA-256 أعلاه.
4. تكتب الملف إلى `/tmp/p02-1130-isolated-review-v4.patch` فقط بعد نجاح التحقق.
5. تعيد `git apply --check` ثم التطبيق المعزول فقط إذا ظلت هوية worktree والحالة المطلوبة صحيحة.

لا اختبارات أو Commit/Push/PR edit أو Merge/Deploy أو Preview/Production أو كتابة مالية ضمن مسار النقل.

الحكم:
`PATCH_TRANSFER_ARTIFACT_READY / CONTROLLED_APPLY_PENDING`

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-18 — تطبيق V4 المعزول نجح فعليًا على worktree #1130

التاريخ: 2026-09-22. هذه النقطة توثق تنفيذ التطبيق المعزول الفعلي من طرفية Emergent الأصلية بعد نقل Patch V4 والتحقق من بصمته.

### نتيجة النقل والتطبيق

نجح نقل Patch V4 إلى:
`/tmp/p02-1130-isolated-review-v4.patch`

والتحقق المباشر أعاد:
- الحجم: `89921` بايت.
- SHA-256: `14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a`.

قبل التطبيق:
- HEAD `20400fffb03594af8a38d6b4750c170233bd5f37`.
- الحالة التاريخية الثلاثية فقط:
  - ` M backend/tests/test_courier_cod_fee_tiers_v2.py`
  - `?? backend/accounting_shipping_contracts.py`
  - `?? backend/tests/test_mz2_shipping_contracts.py`
- بصمات CONTRACT_SLICE الثلاثة مطابقة للقيم المعتمدة.

`git apply --check` نجح، ثم نُفذ `git apply` للـPatch نفسه، وخرج المشغّل:
`RESULT=V4_APPLIED_ISOLATED_PENDING_REVIEW`
مع `P02_APPLY_RC=0`.

### الحالة بعد التطبيق

HEAD بقي `20400fffb03594af8a38d6b4750c170233bd5f37`; لا Commit.

status بعد التطبيق يتكون من الحالة التاريخية + نطاق V4 المتوقع:
- tracked modified: `backend/accounting_module_contract.py`
- tracked modified التاريخي: `backend/tests/test_courier_cod_fee_tiers_v2.py`
- V4 untracked الجديدة:
  - `backend/accounting_shipping_contract_gate.py`
  - `backend/accounting_shipping_contract_service.py`
  - `backend/accounting_shipping_evidence.py`
  - `backend/accounting_shipping_payment_evidence.py`
  - `backend/tests/test_mz2_shipping_contract_isolation.py`
  - `backend/tests/test_mz2_shipping_payment_evidence.py`
  - `docs/operations/MZ2-FIN-CUTOVER-001/P02-1130-V4-ISOLATION-REVIEW.md`
- CONTRACT_SLICE untracked التاريخيان بقيا موجودين.

بصمات CONTRACT_SLICE الثلاثة بعد التطبيق بقيت مطابقة تمامًا للبصمات قبل التطبيق.

`/app` بقي عند `6365a042dfcb125e81e5e198ea1ff1537373ce51` / `refs/heads/hotfix/prod-snap-meta-final` مع `?? .worktrees_p02_runtime.py`.

لا اختبارات، لا staging، لا Commit/Push، لا PR edit.

### تحقق مستقل من البايتات المتوقعة لـV4

أعاد المشرف تطبيق Patch V4 ذي البصمة نفسها خارج Emergent على نسخة Base الموثقة من `backend/accounting_module_contract.py`، وحسب SHA-256 المتوقعة لمسارات V4 الثمانية:

- `backend/accounting_module_contract.py` -> `d802ea261a9bc64e624cc309a48c02c9c9fe76ea954fb99f4917a393bd559c8f`
- `backend/accounting_shipping_contract_gate.py` -> `f78310e35cffc0ba5589d36c4d1fb8d0bd7b0fd6f39e5a2eae274ca40872b79e`
- `backend/accounting_shipping_contract_service.py` -> `e9d1b88fee748a43271a7e5070c55215c5e9aea2d1634cf8bbb9045134674528`
- `backend/accounting_shipping_evidence.py` -> `805cb4eaa8d592d67e1daec9cc9a41a3cbd7789266047a68f97c56e6677a598f`
- `backend/accounting_shipping_payment_evidence.py` -> `904d98f2711913dc763ac3663e9f3e00a4b2640d26c4403dc0ac67b9a1381052`
- `backend/tests/test_mz2_shipping_contract_isolation.py` -> `0153e5996cb64388a8085df8fe2664bbce0ff6b2f01a8b23bc9f1dd745ba5012`
- `backend/tests/test_mz2_shipping_payment_evidence.py` -> `abc030ae1544799bdf3721364f5a7d8f7a9bd40091c23b98fb93e2768d2dd224`
- `docs/operations/MZ2-FIN-CUTOVER-001/P02-1130-V4-ISOLATION-REVIEW.md` -> `cd1495b5f708904d516635fbb3cd7b4e8f0e8b868ba58d6f6f342f76b33ca356`

هذه بصمات متوقعة مشتقة من Patch المعتمد وBase GitHub، وليست بعد قراءة مباشرة لملفات target بعد التطبيق.

### الحكم

`V4_APPLIED_ISOLATED / POST_APPLY_BYTE_VERIFICATION_REQUIRED`

الخطوة التالية الآمنة هي قراءة فقط:
- SHA-256 لمسارات V4 الثمانية ومقارنتها بالقيم أعلاه.
- `git diff --check`.
- `git diff --cached --name-status HEAD` للتأكد أن لا staged changes.
- إعادة status وHEAD.

لا اختبارات قبل اجتياز هذا التحقق القصير. لا Commit/Push/PR edit.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Merge/Deploy/Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-19 — تحقق ما بعد التطبيق واجتياز بوابة البايتات

التاريخ: 2026-09-22. هذه النقطة توثق الفحص الفعلي بعد تطبيق V4 المعزول.

### النتائج الفعلية من طرفية Emergent

- HEAD: `20400fffb03594af8a38d6b4750c170233bd5f37`.
- branch: `refs/heads/local/p02-stage1-ZMgPW8`.
- `git diff --cached --name-status HEAD`: فارغ.
- `git diff --check`: بلا مخرجات/أخطاء.
- تحققت SHA-256 لمسارات V4 الثمانية مباشرة على target، وكلها `OK` ومطابقة للبصمات المشتقة من Patch/Base في SUP-20260922-18:
  - accounting_module_contract.py
  - accounting_shipping_contract_gate.py
  - accounting_shipping_contract_service.py
  - accounting_shipping_evidence.py
  - accounting_shipping_payment_evidence.py
  - test_mz2_shipping_contract_isolation.py
  - test_mz2_shipping_payment_evidence.py
  - P02-1130-V4-ISOLATION-REVIEW.md
- status بعد التحقق يتكون من V4 المتوقع + CONTRACT_SLICE التاريخي فقط.
- `/app` بقي عند `6365a042dfcb125e81e5e198ea1ff1537373ce51` / `refs/heads/hotfix/prod-snap-meta-final` مع `?? .worktrees_p02_runtime.py`.

### الحكم

`V4_APPLIED_AND_BYTES_VERIFIED / V4_ISOLATED_TEST_GATE_AUTHORIZED`

أصبحت خطوة الاختبار المعزول مسموحة. نطاق الاختبار التالي هو **اختبارات V4 الجديدة فقط**:
- `backend/tests/test_mz2_shipping_contract_isolation.py` — 32 حالة مكتوبة.
- `backend/tests/test_mz2_shipping_payment_evidence.py` — 13 حالة مكتوبة.
- الإجمالي المتوقع: 45 حالة.

لا يُطبق Patch #1126، ولا تُعاد اختبارات CONTRACT_SLICE التاريخية في هذه الدفعة؛ بايتاتها لم تتغير وقد سبق تثبيت هويتها. لا Mongo حقيقي ولا HTTP/Frontend/UAT في هذه البوابة.

بسبب فقد مجلد `/tmp` السابق، لا تُفترض بيئة Python القديمة. الاختبار يجب أن يستخدم بيئة مؤقتة معزولة خارج worktree، مع حزم الاختبار المحددة في workflow التاريخي لـ#1130، و`PYTHONDONTWRITEBYTECODE=1` وHOME/TMP معزولين، دون كتابة إلى /app أو قاعدة بيانات.

لا Commit/Push/PR edit قبل مراجعة نتائج الاختبار.

تحقق GitHub قبل القرار: #1130 ما زال Draft وغير مدموج عند Head `20400fffb03594af8a38d6b4750c170233bd5f37`; لا نقل قبول إلى Head آخر.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-20 — بوابة اختبارات V4 توقفت قبل التنفيذ لغياب Python 3.13

التاريخ: 2026-09-22. هذه النقطة توثق محاولة تشغيل بوابة الاختبارات المعزولة بعد اجتياز تطبيق V4 والتحقق البايتـي.

### النتيجة الفعلية من طرفية Emergent

المشغّل وصل فقط إلى فحوص الهوية الأولية:

- HEAD = `20400fffb03594af8a38d6b4750c170233bd5f37`.
- branch = `refs/heads/local/p02-stage1-ZMgPW8`.
- staged check اجتاز؛ لا staged changes ظاهرة.
- عند فحص Python أعاد:
  - `BLOCKED_PYTHON_313_MISSING`
  - `Python 3.11.16`
  - `P02_V4_TEST_RC=1`

لم يُنشأ venv، ولم تُثبت حزم، ولم يبدأ أي اختبار من الاختبارين الجديدين، ولا توجد نتيجة تشغيل لـ32 أو 13 حالة.

### هوية GitHub عند التوثيق

- #1130 ما زال مفتوحًا وDraft وغير مدموج عند Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035` وHead `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1133 قبل كتابة هذه النقطة عند Head `f19a5b2052fc0c6f5277fffe9fba8273521eb552`.

### الحكم

`V4_TESTS_NOT_RUN / PYTHON_313_ENVIRONMENT_BLOCKED`

هذا ليس فشلًا في كود V4 ولا نجاحًا له. لا تُستخدم Python 3.11 بدلًا من 3.13 كدليل قبول صامت، لأن workflow التاريخي لـP02 يثبت Python 3.13 كبيئة أدوات الاختبار.

### الخطوة الآمنة التالية

تشخيص قراءة فقط للبيئة لمعرفة هل Python 3.13 أو مدير Python/حاويات متاح أصلًا دون تثبيت نظامي:
- مسارات `python3.13` المحتملة.
- `uv`, `pyenv`, `mise`, `docker`, `podman`.
- أي بيئة Python 3.13 محفوظة تحت `/opt`, `/usr/local`, `/root/.cache` أو أدلة الأدوات المؤقتة.
- لا apt/install/system mutation في خطوة التشخيص.

إذا وُجد runtime 3.13 موثوق، تُعاد نفس بوابة الاختبارات فقط. إذا لم يوجد، يُقرر لاحقًا بين runtime مؤقت معزول مثبت ببصمة أو تشغيل CI بعد تفويض مستقل؛ لا downgrade غير معلن إلى 3.11 كقبول.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Commit/Push/PR edit أو Merge/Deploy أو Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-21 — مسار Python 3.13.15 المؤقت عبر uv

التاريخ: 2026-09-22. بعد حاجز غياب Python 3.13، نفذ المستخدم تشخيصًا قراءة فقط.

### البيئة الفعلية في Emergent

- `python3.13`: غير موجود.
- `python3.12`: غير موجود.
- `python3.11`: `/usr/local/bin/python3.11`.
- `python3 --version`: `Python 3.11.16`.
- `uv`: موجود في `/opt/bin/uv`.
- `pyenv`, `mise`, `asdf`: غير موجودة.
- `docker`, `podman`: غير موجودة.
- البحث المحدود لم يجد runtime Python 3.13 جاهزًا.
- worktree بقي عند Head `20400fffb03594af8a38d6b4750c170233bd5f37` وبنطاق V4 + CONTRACT_SLICE المتوقع.

### تحقق مستقل من GitHub Actions التاريخي

راجع المشرف run `35653351758` / job `106510834586` الخاص بـ`P02 disposable offline test tools`.

سجل `actions/setup-python@v5` يثبت أن الطلب `python-version: 3.13` حُل فعليًا إلى:

`CPython 3.13.15`

ومساره على runner كان:
`/opt/hostedtoolcache/Python/3.13.15/x64`.

هذا يحدد runtime الاختبار المرجعي بدقة لهذه البوابة.

### الحكم

`PYTHON_31315_REFERENCE_VERIFIED / UV_TEMP_RUNTIME_AUTHORIZED`

الخطوة التالية المسموحة:
- استخدام `/opt/bin/uv` لجلب CPython **3.13.15 بالضبط** إلى دليل مؤقت تحت `/tmp`.
- تعيين `UV_PYTHON_INSTALL_DIR` و`UV_CACHE_DIR` إلى أدلة مؤقتة خاصة بالاختبار.
- إنشاء venv مؤقت واستخدام `uv pip` لتثبيت حزم الاختبار المحددة سابقًا.
- التحقق من `Python 3.13.15` قبل تشغيل أي test.
- ثم تشغيل ملفي V4 الجديدين فقط (32 + 13).

لا apt/system install، لا تعديل PATH دائم، لا كتابة داخل /app، لا #1126، لا Commit/Push/PR edit.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-22 — قفل نسخة runtime وحزم الاختبار من سجل P02 التاريخي

التاريخ: 2026-09-22. استُكملت مراجعة job `106510834586` من run `35653351758`.

### runtime المرجعي

`actions/setup-python@v5` جهز:
`CPython 3.13.15`.

### الحزم المباشرة كما استُخدمت في workflow

- mongomock-motor 0.0.36
- pymongo 4.18.1
- fastapi 0.141.1
- pydantic 2.13.5
- httpx 0.28.1
- bcrypt 4.1.3
- PyJWT 2.13.0
- openpyxl 3.1.5
- python-multipart 0.0.20
- python-dotenv 1.2.3
- cryptography 50.0.0
- pytest 9.0.3
- pytest-asyncio حُل إلى 1.4.0
- email-validator حُل إلى 2.3.0

كما يثبت log تنزيل wheels المتوافقة مع CPython 3.13، ومنها `pymongo-4.18.1-cp313...` و`pydantic_core-2.46.5-cp313...` و`cffi-2.1.1-cp313...`.

### القرار

بوابة الاختبار القادمة تستخدم `uv` الموجود في `/opt/bin/uv` لإنشاء runtime/venv مؤقتين تحت `/tmp` فقط، مع CPython 3.13.15 بالضبط والحزم المباشرة بالإصدارات الفعلية أعلاه. أي فشل تنزيل/إنشاء يُعامل كحاجز بيئة لا كفشل V4.

لا تثبيت نظامي، لا تعديل دائم لـPATH/HOME، لا كتابة إلى /app، لا #1126، ولا Commit/Push.

الحواجز: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-23 — V4: نجاح 45/45 اختبارًا معزولًا على CPython 3.13.15

التاريخ: 2026-09-22. هذه النقطة توثق نتيجة التشغيل الفعلية التي أرسلها المستخدم من طرفية Emergent.

### بيئة الاختبار

- uv: `0.12.17 (aarch64-unknown-linux-gnu)`.
- runtime: `CPython 3.13.15`.
- venv: `/tmp/mz2-p02-v4-uv.yu9C05/venv`.
- target HEAD: `20400fffb03594af8a38d6b4750c170233bd5f37`.
- target branch: `refs/heads/local/p02-stage1-ZMgPW8`.

### النتائج

اختبار V4 الأول:
`backend/tests/test_mz2_shipping_contract_isolation.py`
- `Ran 32 tests`
- `OK`
- exit 0.

اختبار V4 الثاني:
`backend/tests/test_mz2_shipping_payment_evidence.py`
- `Ran 13 tests`
- `OK`
- exit 0.

ملخص المشغّل:
- `PYTHON=3.13.15`
- `ISOLATION_EXIT=0`
- `ISOLATION_32=PASS`
- `PAYMENT_EXIT=0`
- `PAYMENT_13=PASS`
- `STATUS_UNCHANGED=PASS`
- `FILES_UNCHANGED=PASS`
- `DIFF_CHECK_EXIT=0`
- `COMMIT=NONE`
- `PUSH=NONE`
- `P02=LOCKED`
- `RESULT=V4_45_ISOLATED_TESTS_PASS_ONLY`
- `P02_V4_TEST_RC=0`.

### تفسير النتيجة

هذه بوابة PASS فعلية لـ45 اختبار V4 الجديد على الـHead المستهدف وحالة worktree الحالية. لا توجد كتابة مالية أو قاعدة بيانات حقيقية أو HTTP/Frontend/UAT في هذه البوابة.

V4 نفسها تشير إلى 43 اختبارًا سابقًا لعقد الشحن/الحاسبة/legacy (13 + 25 + 5)، ليصبح إجمالي المجموعات المكتوبة 88 عند جمعها مع 45 الجديدة. نتيجة الـ45 لا تُنقل تلقائيًا إلى الـ43 القديمة ولا تحل محل إعادة regression على الشجرة المجمعة قبل Commit.

### الحكم

`V4_45_ISOLATED_TESTS_PASS / COMBINED_43_REGRESSION_GATE_NEXT`

الخطوة الآمنة التالية قبل أي Commit/Push:
- إعادة تشغيل اختبارات CONTRACT_SLICE التاريخية الثلاثة على **الشجرة الحالية بعد V4**:
  1. calculator: 13 tests
  2. contracts: 25 tests
  3. legacy COD tiers: 5 pytest cases
- استخدام نفس CPython 3.13.15/venv الحالية إن بقيت متاحة.
- حفظ status/bytes قبل وبعد والتأكد أن V4 وCONTRACT_SLICE لم يتغيرا.
- لا تطبيق #1126 في هذه الدفعة.

إذا اجتازت 43/43 مع ثبات الحالة، يصبح الدليل الحالي 88/88 isolated/regression على نفس worktree، وعندها يمكن تقييم بوابة Commit/Push مستقلة.

الحواجز العامة مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Merge/Deploy/Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-24 — محاولة 43 regression لم تبدأ بسبب اختفاء worktree المؤقت مجددًا

التاريخ: 2026-09-22. بعد نجاح 45/45 اختبار V4، حاول المستخدم بدء بوابة regression التاريخية 43 حالة.

### النتيجة الفعلية

أول فحص للهدف أعاد:

`fatal: cannot change to '/tmp/mz2-p02-stage1.ZMgPW8/worktree': No such file or directory`

ثم:
`P02_REGRESSION43_RC=1`.

لم يصل المشغّل إلى فحص Python، ولم يبدأ:
- calculator 13
- contracts 25
- legacy 5

ولا توجد نتيجة PASS/FAIL لأي من الـ43.

### تفسير الحالة

هذا حاجز فقدان لمسار `/tmp`، وليس فشلًا في كود V4 أو regression. نتيجة V4 السابقة 45/45 تبقى موثقة عند نفس Head والبايتات التي اختُبرت قبل اختفاء المسار، ولا تُحوّل هذه المحاولة إلى نتيجة جديدة.

تحقق GitHub عند التوثيق:
- #1130 ما زال مفتوحًا وDraft وغير مدموج عند Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035` وHead `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1133 قبل هذه النقطة عند Head `d8cff29c2a9283c5626b78a03038108af390a8e3`.

### الحكم

`COMBINED_43_REGRESSION_NOT_RUN / TARGET_WORKTREE_MISSING_AGAIN`

الخطوة الآمنة التالية هي فحص قراءة فقط:
- `git worktree list --porcelain`
- admin metadata تحت `/app/.git/worktrees/worktree`
- local branch ref
- index SHA وcached diff من admin إن أمكن
- وجود/غياب parent وworktree وvenv المؤقت
- /app HEAD/status

لا prune/unlock/remove/repair/reset/clean/add/apply قبل هذا الفحص.

إذا بقي admin/index والفرع محفوظين كما في الاسترجاع السابق، يمكن إعادة بناء نفس الحالة المجمعة بدقة من HEAD + CONTRACT_SLICE الموثق + Patch V4 الموثق، ثم تحقق البايتات قبل إعادة بوابة 43.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`. لا Commit/Push/PR edit أو Merge/Deploy أو Preview/Production mutation أو كتابة مالية.


---

## نقطة التحقق SUP-20260922-25 — admin/index/branch محفوظة بعد فقدان /tmp للمرة الثانية

التاريخ: 2026-09-22. هذه النقطة توثق الفحص القراءة فقط بعد اختفاء worktree قبل تشغيل بوابة regression 43.

### المشاهدات المباشرة

- Git ما زال يسجل worktree:
  - path: `/tmp/mz2-p02-stage1.ZMgPW8/worktree`
  - HEAD: `20400fffb03594af8a38d6b4750c170233bd5f37`
  - branch: `refs/heads/local/p02-stage1-ZMgPW8`
  - lock: `P02 PR1130 isolated stage1; preserve`
- المسار المادي `/tmp/mz2-p02-stage1.ZMgPW8` غير موجود.
- admin directory `/app/.git/worktrees/worktree` موجود وسليم بنيويًا.
- admin HEAD ما زال يشير إلى `refs/heads/local/p02-stage1-ZMgPW8`.
- admin gitdir ما زال يشير إلى worktree المفقود.
- admin index موجود، الحجم `325797` وبصمة SHA-256:
  `faed3c238a093b90ff7a29e741a55b89abd344693820ed098ee02c98a808d0f5`
  وهي نفس بصمة index التي ثُبتت بعد الاسترجاع السابق.
- local branch ref = `20400fffb03594af8a38d6b4750c170233bd5f37`.
- لا MERGE_HEAD/CHERRY_PICK_HEAD/REVERT_HEAD/REBASE_HEAD/BISECT_START ولا rebase/sequencer markers ظهرت.
- محاولة cached diff من admin فشلت فقط لأن parent `/tmp/mz2-p02-stage1.ZMgPW8` غير موجود.
- بيئة uv/venv السابقة وملف Patch المؤقت اختفت أيضًا.
- `/app` ما زال HEAD `6365a042dfcb125e81e5e198ea1ff1537373ce51` والفرع `refs/heads/hotfix/prod-snap-meta-final` والحالة `?? .worktrees_p02_runtime.py`.

### هوية GitHub عند القرار

- #1130: مفتوح، Draft، غير مدموج، Base `5292a87a476a140ae8c3c78e88dfba7d8c83f035`، Head `20400fffb03594af8a38d6b4750c170233bd5f37`.
- #1133 قبل هذه النقطة: Head `1859116608ad7408db5b215189d555988aaf8dd7`.

### الحكم

`WORKTREE_STORAGE_LOST / GIT_ADMIN_INDEX_BRANCH_PRESERVED_EXACTLY`

الحالة قابلة لإعادة البناء دون بدء من الصفر. الاستراتيجية التالية يجب أن تتجنب الاعتماد على بقاء /tmp بين الرسائل:
1. إعادة بناء tracked tree من HEAD دون staging/commit.
2. استعادة CONTRACT_SLICE الثلاثة من بايتات النقل الموثقة.
3. إعادة تطبيق Patch V4 ذي SHA-256 المعتمد.
4. تحقق SHA-256 لكل ملفات CONTRACT_SLICE + V4.
5. إنشاء CPython 3.13.15 مؤقت عبر uv.
6. تشغيل **جميع 88 اختبارًا** (45 V4 + 43 regression) في نفس عملية الاسترجاع/الاختبار، ثم التوقف للمراجعة.

لا Commit/Push/PR edit في هذه العملية. لا #1126، لا Merge/Deploy/Preview/Production mutation أو كتابة مالية.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-26 — تجهيز Runner واحد يعيد البناء ويشغّل 88 اختبارًا قبل فقدان /tmp

التاريخ: 2026-09-22. بعد إثبات أن admin/index/branch محفوظة وأن المسار المادي فقط هو المفقود، جُهزت وسيلة استرجاع/اختبار واحدة لتجنب الاعتماد على بقاء `/tmp` بين الرسائل.

### Artifact نقل CONTRACT_SLICE

أُنشئ تعليق نقل في Issue #1006:
- comment id: `5783487472`

يحمل Base64 للبصمات التاريخية الثلاثة فقط:
- `backend/accounting_shipping_contracts.py`
  SHA-256 `9f25d896d9835dc36a29e91f2dab90de2cf8f1a4c2cec18f01d9fb097863b7c8`
  bytes `10380`.
- `backend/tests/test_mz2_shipping_contracts.py`
  SHA-256 `61168e55eb400da907f8d0fb795f7c28155e968d22ff4f5b45eae14b8b83f0b2`
  bytes `12961`.
- `backend/tests/test_courier_cod_fee_tiers_v2.py`
  SHA-256 `ff35204aaaecfb897869341c82b9c86b82d7580ce70fb397c617edfd76933f9c`
  bytes `3211`.

Patch V4 يبقى من تعليقات النقل الموثقة:
`5782817671`, `5782818225`, `5782818706`
وبصمة Patch:
`14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a`.

### Runner الجديد

المسار على فرع التوثيق فقط:
`docs/operations/MZ2-FIN-CUTOVER-001/P02-1130-RECONSTRUCT-AND-88.sh`

تم إنشاؤه في commit:
`0c0d846b5334f90e67b206f43c37258c5428fd1b`

Git blob SHA:
`3cc23b4a8c7ddcd591a2f8ee2a8a8992440fb31a`

الحجم النصي: 13503 حرفًا / 324 سطرًا. لم يُنفذ بعد.

### سلوك Runner

1. يتحقق من admin/index/branch/lock و/app قبل أي كتابة.
2. يشترط غياب parent القديم حتى لا يكتب فوق حالة موجودة.
3. يعيد tracked tree من Head نفسه عبر `git archive`، دون staging/commit.
4. يستعيد CONTRACT_SLICE من تعليق النقل ويتحقق من الحجم وSHA-256 لكل ملف.
5. يستعيد Patch V4 من تعليقات النقل ويتحقق من 89921 بايت وبصمة Patch المعتمدة.
6. يشغّل `git apply --check` ثم `git apply` على worktree المعزول فقط.
7. يتحقق من status النهائي وبصمات جميع 11 ملفًا في الحالة المجمعة.
8. ينشئ CPython 3.13.15 مؤقتًا عبر uv تحت `/tmp`.
9. يشغّل في نفس العملية جميع الاختبارات:
   - V4 isolation: 32
   - V4 payment evidence: 13
   - calculator regression: 13
   - contract regression: 25
   - legacy COD tiers: 5
   - الإجمالي: 88
10. يقارن status/files/index و/app قبل/بعد الاختبارات.
11. لا يعمل git add/commit/push/PR edit، ولا #1126، ولا Merge/Deploy/Preview/Production أو كتابة مالية.

نتيجة النجاح المستهدفة:
`RESULT=RECONSTRUCTED_EXACT_88_OF_88_PASS_ONLY`

### الحكم

`EXACT_RECONSTRUCT_AND_88_RUNNER_PREPARED_NOT_EXECUTED`

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.


---

## نقطة التحقق SUP-20260922-27 — إعادة البناء الدقيقة نجحت؛ توقف كاذب عند بصمة index البايتية قبل الاختبارات

التاريخ: 2026-09-22. هذه النقطة توثق تنفيذ المشغّل السابق بعد فقدان /tmp.

### ما تحقق فعليًا

المشغّل نجح في:
- جلب نفسه من GitHub عند blob `3cc23b4a8c7ddcd591a2f8ee2a8a8992440fb31a`.
- إعادة tracked tree على Head `20400fffb03594af8a38d6b4750c170233bd5f37`.
- استعادة CONTRACT_SLICE الثلاثة بالحجوم والبصمات الموثقة:
  - module: 10380 bytes / `9f25d896...`
  - tests: 12961 bytes / `61168e55...`
  - legacy: 3211 bytes / `ff35204a...`
- استعادة Patch V4 بالضبط:
  - 89921 bytes
  - SHA-256 `14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a`
- التحقق من بصمات الحالة المجمعة لجميع المسارات الـ11؛ كلها `OK`.
- اجتياز `git diff --cached --name-status HEAD` كفارغ قبل حاجز index.
- اجتياز `git diff --check` قبل حاجز index.

ثم توقف عند:
`RESULT=BLOCKED_INDEX_CHANGED`

ولم يصل إلى إنشاء Python 3.13.15 أو أي اختبار من الـ88. النتيجة:
`P02_RECONSTRUCT_88_RC=1`.

### تفسير الحاجز

الحارس الذي قارن SHA-256 الخام لملف `.git/worktrees/worktree/index` بالقيمة التاريخية أصبح أضيق من المطلوب. Git قد يعيد كتابة stat-cache/metadata داخل ملف index عند فحص working tree المعاد إنشاؤها، حتى مع بقاء **المحتوى الدلالي للـindex دون staged changes**. في هذه المحاولة ثبت مباشرة قبل الحاجز أن cached diff فارغ وأن كل بايتات العمل المستهدفة مطابقة.

الحكم:
`EXACT_COMBINED_TREE_RECONSTRUCTED / RAW_INDEX_BYTE_GUARD_FALSE_BLOCKER / 88_TESTS_NOT_RUN`

هذا لا يمنح PASS للـ88 ولا يلغي PASS الـ45 السابق. الحالة الحالية بعد المحاولة هي أفضل نقطة للاستئناف: worktree المجمعة موجودة ببصماتها الصحيحة، ولا حاجة لإعادة reconstruction ما دامت لم تختف.

### إجراء تصحيحي على فرع التوثيق فقط

المشغّل القديم:
`docs/operations/MZ2-FIN-CUTOVER-001/P02-1130-RECONSTRUCT-AND-88.sh`

تم **تعطيله صراحة** حتى لا يعاد تشغيل حارس raw-index الخاطئ:
- deprecation commit: `ffa1b53016c4715fbac354e25e7639ed3872bf2a`
- current blob: `d2e80494f0c607a1ea875d17dfcdf8259c23cee2`

أي نسخة سابقة منه لا تُستخدم.

### الخطوة الآمنة التالية

على worktree الحالية فقط:
1. تحقق Head/branch/status.
2. تحقق exact 11 SHA-256.
3. تحقق `git diff --cached --name-status HEAD` فارغ و`git ls-files --unmerged` فارغ.
4. سجّل semantic index manifest من `git ls-files --stage` قبل الاختبارات.
5. أنشئ CPython 3.13.15 مؤقتًا عبر uv.
6. شغّل 32 + 13 + 13 + 25 + 5 = 88 في نفس العملية.
7. بعد الاختبارات: status/files/app unchanged، cached diff/unmerged ما زالا فارغين، وsemantic index manifest لم يتغير.
8. لا تعتمد raw index file SHA كحارس قبول.

لا Commit/Push/PR edit أو #1126 أو Merge/Deploy/Preview/Production mutation أو كتابة مالية قبل مراجعة نتيجة الـ88.

الحواجز مستمرة: `P01=IN_PROGRESS`, `P02=LOCKED`, `P03=LOCKED`.
