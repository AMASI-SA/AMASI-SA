# قيود: فحوص موزعة ومتابعة مستقلة — نتيجة التنفيذ المحلي

الحالة الحالية: WIP في PR #1776، فرع codex/qoyod-bounded-scans-current-20261105؛ أُذن بالرفع في 6 نوفمبر. الحفظ الأول 47d9ff5b2ce9dddac913ec31d5cad70408618aa3. لا دمج أو نشر أو تغيير إنتاج. السجل المحلي أدناه تاريخي؛ آخر نتائج Mongo في القسم المضاف بالأسفل.

## المصدر وحماية العمل القائم

- المستودع AMASI-SA/AMASI-SA، فرع الإنتاج hotfix/prod-snap-meta-final.
- خط الأساس الأول a973ee2d7556cc8a3afaa10bc51bee4df2959d7d (#1772)، متضمنًا إصلاح الشريط #1770.
- أثناء العمل تقدم الإنتاج إلى 4b336e2a8bb8288dae50505e8eca33147744f441 (#1773). لم تتداخل ملفاته مع التعديل؛ نُقلت التعديلات المحلية إلى فرع مبني عليه بواسطة git switch، دون merge أو commit.
- الفرع المحلي codex/qoyod-bounded-scans-current-20261105.
- المجلد D:/codex-worktrees/qoyod-bounded-scans-20261105. تعديلات النسخة الأصلية للمستخدم محفوظة ولم تُنقل أو تُمسح.
- لم يُستخدم release guard prepare/prepublish أو زر نشر. البناء المحلي ليس حزمة نشر معتمدة ولا إثبات نشر.

## السلوك المنفذ

1. خمس نوافذ يومية بتوقيت الرياض: 00:00، 04:48، 09:36، 14:24، 19:12. عند الانقطاع تُجمع النوافذ الفائتة في فحص واحد؛ لا طابور تعويض عاصف. النجاح الذي يعبر حد نافذة يغطي النافذة الحالية أيضًا.
2. زر يدوي في الشريط والمطابقة. الفحص اليدوي والمجدول وتحديث شاشة الفواتير يستخدمون القفل نفسه عبر النسخ. لا فحص شامل عند GET أو فتح صفحة. مهلة تهدئة يدوية 60 ثانية، دون تكديس ضغطات الزر.
3. lease للفحص 120 ثانية، يجدد كل 20 ثانية، وtoken يسيّج نشر النتيجة. التنفيذ محدود بـ20 دقيقة. لا تُستبدل النتيجة الناجحة عند الفشل أو الإلغاء أو استبدال القفل.
4. تعرض الصفحات الجيل المنشور، ويقرأ الشريط وثيقة إعدادات صغيرة واحدة مع آخر نجاح ووقته وحالة القِدم. فشل القراءة يحتفظ بالعرض السابق. صلاحية إثبات الغياب المالية الأصلية 15 دقيقة لم تُمدّد؛ عرض العدد القديم لا يمنح إذن إرسال.
5. تبقى متابعة الطلبات مستقلة: hint دائم بعد حفظ الطلب، واكتشاف أقصاه100 طلب للجولة، وحصة للأحداث الجديدة وحصة للاستدراك. استدراك بصفحات keyset من100 مرجع كل دقيقة مع checkpoint دائم وإعادة مرور، لاستعادة الأحداث المفقودة والكتابات التي لا تستخدم hook.
6. إرسال متتابع بحد5 طلبات. عند وجود المسارين تُحجز3 خانات للجديد و2 للاستدراك، وتستخدم الخانات الفارغة للمسار الآخر. الترتيب داخل المسار أقدم أولًا؛ لم يعد عداد نافذة العامل يُعرض باعتباره إجمالي التراكم الكامل.
7. تحقق حي للحالة والدفع، والأقفال والعلامات المحلية باقية. جرى تجديد قفل العامل وفحص ملكيته قبل العمل والإرسال. أُلغي تجاوز البحث عن مرجع الفاتورة للمسار التلقائي؛ غياب قديم لا يكفي، وفشل المزود يوقف الإنشاء بأمان. إثبات الوجود قد يبقى مخزنًا لأنه يمنع الكتابة؛ الغياب يعاد التحقق منه لكل إرسال محروس.
8. فحوص Mongo على دفعات100 مع إتاحة حلقة التنفيذ، وكتابة الفواتير100 بدل500، وقراءة قيود50 فاتورة بالصفحة وبالتتابع. الفحص الشامل يمكنه تجاوز حد التدقيق التفاعلي10,000 دون رفعه للعامل؛ يراقب ضغط الذاكرة كل1000 سجل، ويحتفظ بحد زمن الاستعلام8000ms وبميزانية الفحص. الضغط أو عدم الاكتمال يحفظان العرض السابق.
9. يعاد استخدام تدقيق المرشحين نفسه للمطابقة والاستثناءات والعدادات؛ أزيلت قراءة الفواتير المكررة. تُحفظ صفوف العرض في قطع100 سجل، ويُنشر مؤشر الجيل بعد حفظها. تنظيف الأجيال الأقدم يحتفظ بالجيل الحالي والسابق.
10. حد عرض المطابقة1000 صف، والاستثناءات5000 صف، مع توضيح الحد/عدم اكتمال عدادات العرض عند بلوغه؛ لا يُقدم الجزء المحدود على أنه كل النتائج. دقة فلاتر الاستثناءات تعتمد على الصفوف المحفوظة، ويظهر counts_complete=false إذا كانت اللقطة محدودة.
11. تعريفات فهارس جديدة لقائمة المتابعة وقطع العرض فقط؛ لم تُنشأ في الإنتاج. لم تتغير مفاتيح تفعيل الإرسال أو صلاحيات المالك. المجدول يحتاج enabled ومالك الطلبات وsecurity_owner_id المعرّف أصلًا؛ لم تُتجاوز مشكلة الصلاحية الحية المسجلة سابقًا.

## القياس قبل وبعد

الملفات before.json وafter.json تحفظ ثلاث تجارب لكل نسخة:1000 طلب اصطناعي مؤهل و10 جولات، دون أي طلب مزود أو إرسال أو بيانات عملاء. before قيس قبل تعديل كود الإنتاج، وbaseline-recheck يعيد المصدر الأصلي المعزول لاحقًا للتحقق من اتجاه النتيجة. Windows وmongomock؛ المحاكاة ليست محرك Mongo الحقيقي ولا تستخدم فهارسه.

| القياس لكل تجربة | قبل، وسيط ثلاث تجارب | بعد، وسيط ثلاث تجارب |
|---|---:|---:|
| سجلات أعيدت إلى التطبيق، جميع المجموعات المحسوبة | 10000 | 1650 |
| استدعاءات find/find_one على مستوى التطبيق | 30 | 61 |
| استدعاءات الكتابة، بما فيها bulk وعمليات لا تغيّر سجلات | 10 | 13 |
| CPU للعملية في هذا الاختبار | 4734.38ms | 1000ms |
| الزمن الكلي للاختبار | 4967.33ms | 1147.83ms |
| ذروة تخصيص Python المقاسة بـtracemalloc | 2458664 بايت | 548381 بايت |
| وسيط أكبر تأخر heartbeat اصطناعي | 565.22ms | 99.06ms |

انخفض حجم السجلات المسترجعة83.5%، لكن استدعاءات القراءة الصغيرة زادت103.3% لإدارة المتابعة والإنصاف والاستدراك. هذه ليست أعداد أوامر Mongo الشبكية؛ لم يُقَس getMore أو documentsExamined أو كلفة الفهارس أو عدد السجلات المتغيرة فعليًا داخل bulk. التهيئة تضيف100 hint في bulk ثم تحديثات الصفوف المختارة؛ عدد استدعاءات الكتابة لا يساوي عدد الوثائق المكتوبة.

إعادة خط الأساس القريبة أعطت وسيط CPU3078.12ms وزمن3499.12ms، مع بقاء30 قراءة و10000 سجل. هذا يبيّن تذبذب المضيف ويمنع اعتماد نسبة CPU ثابتة. النتيجة المحلية تبين اتجاه تقليل العمل في اكتشاف المرشحين فقط، ولا تثبت نسبة توفير على موارد الخدمة ككل.

لم يُقَس زمن صفحات الموظفين أو عمليات التجهيز بعد التعديل، ولم تُقَس ذاكرة الحاوية/RSS أو CPU الإنتاج. مقارنة إنتاج قبل/بعد غير ممكنة قبل نشر النسخة؛ لذلك لا يُستنتج أي تسريع للصفحات من هذه الأرقام أو من انخفاض الفحوص.

## الأمان والحدود المتبقية

- قد يتطلب التحقق الضروري قبل الإرسال قراءة بديلة كاملة من قيود إذا رفض البحث بالمرجع أو تجاهل الفلتر. حدها200 صفحة ×50، ولا تُحوَّل الصفحات المكررة/المجهولة أو404 عام إلى إثبات غياب. هذه الكلفة مستقلة عن جدول المطابقة، ولم تدخل قياس الاكتشاف أعلاه.
- تتطلب مراجعة ما قبل الدمج تجربة lease وفهارس/queryPlanner على Mongo حقيقي مع نسختين. اختبارات التزامن الحالية تستخدم mongomock، ولا تثبت سلوك انقسام الشبكة أو ساعات النسخ المختلفة.
- يحافظ سقف قراءة المزود والمهل والضغط على الفشل المغلق؛ لا نضمن نجاح فحص كبير في الإنتاج قبل معاينة بيانات الحجم وخطط الاستعلام.
- اكتشاف الطلبات المستدركة محدود100 مرجع/دقيقة، وموعد إعادة فحص طلب غير مؤهل يصل4 ساعات إذا لم يصل hint حديث. الزمن الكامل لدورة الاستدراك يعتمد على عدد الطلبات؛ لا يُفترض SLA غير مقاس.
- لا تعديل الآن لـOBS_CONTROL_FILE أو profiling أو إعدادات القياس. القياسات الموجودة للذاكرة وانتظار Mongo ومسارات الموظفين تحتاج الوصول التشغيلي الآمن؛ رمز التشخيص غير متاح في الجلسة.

## التحقق وخطة المقارنة التشغيلية اللاحقة

اختبارات الجدولة: خمس نوافذ، قفل مشترك، فشل يحتفظ بالنجاح، token قديم لا ينشر، استدراك بعد إعادة التشغيل، عدم فقد حدث يصل أثناء القراءة، حدود الدفعات، حصة الجديد والاستدراك، استمرار المتابعة أثناء تعليق الفحص، وعدم بدء فحص من GET ورفض الموظف المموه كمالك. اختبارات الإرسال تشمل الدفع والحالة والتكرار والمبالغ وCOD والأقفال ورفض الإنشاء بعد خطأ تحقق المزود. اختبارات الواجهة تحفظ العدد والوقت عند الفشل وتمنع ضغطات يدوية متكررة وتقرأ حالة الفحص فقط.

التحقق المحلي المسجل: 263 اختبار backend ناجح قبل تحديث الأساس إلى1773؛ أعيدت المجموعة مع اختبارات الإصدار1773 على الأساس النهائي، والنتيجة النهائية تسجل في VALIDATION.txt. الواجهة28 اختبارًا في6 مجموعات. بناء Vite محلي ناجح، مع تحذيرات CSS/chunk المعروفة؛ ليس release build أو rehearsal معتمدًا.

بعد إذن مستقل للنشر، تُجمع نافذتان من نشاط طبيعي متقارب، ويفضل دورة يوم كاملة لتغطية الخمس نوافذ: عدد طلبات الموظفين وأنواعها وعدد الطلبات الجديدة وحجم البيانات وإعادات تشغيل النسخ. تجمع لكل نسخة الهوية والتوقيت وCPU وثوانيه وRSS/ذاكرة الحاوية وانتظار Mongo وأعداد الأوامر/getMore، وسجلات جولات المتابعة والفحص، وأزمنة مسارات التجهيز p50/p95 ومعدل المهلة. يربط القياس بالنوافذ الثقيلة ويقارن حسب نوع العملية وحجم النشاط. لا اختبار ضغط أو إرسال تجريبي؛ لا نسبة سرعة معتمدة قبل ذلك.

## Real Mongo checkpoint — 2026-11-06

Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1776. User explicitly authorized branch/PR publication, not merge or deployment. Production changed: no. Issue #1006 rejected a checkpoint comment because it reached the 2500-comment limit; this PR is the continuation ledger.

An isolated loopback MongoDB8.0.12 single-member replica set was started with a 0.25GiB WiredTiger cache. No existing database or application worker was reused or stopped. Synthetic worker processes alone were terminated for crash tests.

Initial real-Mongo evidence found: (1) a crashed scan could not be reclaimed in its current slot; (2) two workers could both reclaim an existing failed send lock, producing two local synthetic provider-sink entries; (3) writing a followup collection inside nested operational transactions poisoned the outer transaction even when the exception was caught. The revised implementation permits reclaim only after lease expiry, uses CAS for send-lock recovery and handles duplicate insert claims as a refusal, and stores a dirty revision in the canonical order's existing transaction. The worker durably enqueues then acknowledges that exact revision. Restricted transaction permissions are unchanged.

Fresh results: real-mongo-worker.txt/XML:8 passed in39.60s, including two OS processes and the unmodified20-second lease-renewal interval. The crash test alone shortens lease duration to3s in its synthetic process. real-mongo-integration.txt/XML:39 passed,20 subtests, no skips in73.66s. All36 formerly skipped integration cases ran and passed; none is classified inapplicable. No stock/transaction/service mocks were added to those cases. Provider transport remains synthetic, never a Qoyod financial request.

Diagnostic limitation: this Windows Mongo build emitted malformed UTF-8 only in explain.serverInfo.host. Only diagnostic explain decoding uses replacement, verifies that no plan/value field was replaced, and omits host from published evidence. Application data decoding remains strict.

Pending: real-Mongo timing/count/profile comparison, representative invoice lookup plans, final regressions, published report and final PR review checkpoint. Next safe action: run bounded synthetic benchmarking on this isolated server, then publish measured results to this draft.

## Indexed lookup milestone

The bounded worker lookup now uses only the three invoice-reference forms already accepted by official_qoyod_reference, backed by three compound indexes. Interactive/full diagnostic behavior is unchanged. Real-Mongo parity checks retained plan_b, attested official and raw references and rejected unproven external aliases. With1004 invoice rows, the winning plan used all3 new indexes and examined6 documents (including residual-filter/duplicate fetch accounting) to return3 proven matches. No production indexes were created.

A combined invocation covered312 backend cases:311 passed; one diagnostic assertion failed only because BSON Timestamp metadata could not be JSON-encoded. The diagnostic assertion was corrected without weakening application codecs; its focused rerun passed. Primary timing will use no profiling. Earlier exploratory profiler files remain explicitly labeled and are not the primary timing evidence.

Current remote checkpoint before this milestone: afaf78ba60f8df120420b6cd8281d2f98c372b91. Next: serial paired real-Mongo baseline/candidate timing at1000 and5000 orders, then final full regression and remote report. No merge or production change.
