export const PLAN_LAST_REVIEWED_AT = "2026-07-31";

export const STATUS_META = {
    completed: {
        label: "مكتمل ومتحقق",
        shortLabel: "مكتمل",
        tone: "emerald",
    },
    in_progress: {
        label: "قيد التنفيذ",
        shortLabel: "قيد التنفيذ",
        tone: "amber",
    },
    pending: {
        label: "متبقٍ",
        shortLabel: "متبقٍ",
        tone: "slate",
    },
    waiting: {
        label: "بانتظار تحقق أو اعتماد",
        shortLabel: "بانتظار",
        tone: "rose",
    },
    deferred: {
        label: "مرحلة لاحقة",
        shortLabel: "لاحقًا",
        tone: "violet",
    },
};

export const COMPLETION_RULES = [
    "لا تصبح المهمة خضراء بمجرد ظهور الواجهة؛ يجب اكتمال الوظيفة واختبارها.",
    "المهام التي تحتاج بيانات إنتاج تبقى بانتظار التحقق حتى تنجح المطابقة الفعلية.",
    "لا ننقل صفحة قديمة كما هي؛ ننقل القدرة المطلوبة إلى مساحة V2 واحدة ثم نضع تحويلًا للمسار القديم.",
    "إنشاء الحملات والرد التلقائي والكتابة الحساسة لا تدخل في اكتمال النواة، وتبقى خلف اعتماد وصلاحيات وسجل تدقيق.",
];

export const NEXT_RECOMMENDED_STEP = {
    id: "fulfillment-end-to-end",
    title: "إكمال دورة إدارة التجهيز من «قيد التنفيذ» حتى «تم التوصيل»",
    reason: "الطلبات والمنتجات والمكونات والمخازن أصبحت لها أسس V2. أكبر فجوة تمنع تشغيل ميزان 2 يوميًا هي أن دورة الطلب لا تزال تتوقف بعد المراجعة.",
    firstDelivery: "تفعيل مرحلة «قيد التنفيذ» بعقد حالة واضح، إسناد الموظف، تحديد مصدر القطعة، ومنع الانتقال قبل اكتمال المرحلة السابقة.",
    doNotMix: "لا نخلط معها إنشاء الحملات أو الرد التلقائي أو تغيير مسار قيود.",
};

export const MEZAN_V2_WORKSTREAMS = [
    {
        id: "foundation",
        title: "أساس ميزان 2 والبنية الموحدة",
        description: "مساحات V2 الأساسية وعقود البيانات التي تمنع ازدواج المصادر والصفحات.",
        core: true,
        tasks: [
            {
                id: "v2-shell",
                title: "هيكل Mezan OS V2 والتنقل المحمي",
                status: "completed",
                evidence: "مسارات V2 مستقلة داخل Layout وOwnerOnlyRoute",
            },
            {
                id: "orders-v2-contract",
                title: "عقد الطلبات الموحد للقائمة والتفاصيل والتحديث من سلة",
                status: "completed",
                evidence: "Orders V2 + PR #112",
            },
            {
                id: "products-components-foundation",
                title: "أساس المنتجات والمكونات والتكاليف الشرطية",
                status: "completed",
                evidence: "PRs #39, #51, #55, #57, #61, #62",
            },
            {
                id: "warehouse-foundation",
                title: "الفروع والمخازن والأقسام والخزائن",
                status: "completed",
                evidence: "PRs #48–#50",
            },
            {
                id: "integrations-foundation",
                title: "مركز التطبيقات والتكاملات الآمن",
                status: "completed",
                evidence: "PR #87",
            },
            {
                id: "production-baseline",
                title: "تثبيت خط إنتاج مستقر بعد التراجع الأخير وفحص الدخول والتنقل",
                status: "in_progress",
                evidence: "يحتاج Smoke Test على نسخة الإنتاج المعتمدة قبل أي نشر جديد",
            },
        ],
    },
    {
        id: "orders-fulfillment",
        title: "الطلبات وإدارة التجهيز",
        description: "دورة الطلب التشغيلية كاملة من وصول الطلب حتى التسليم، مع سجل مسؤولية واضح.",
        core: true,
        tasks: [
            {
                id: "pending-review",
                title: "مرحلة بانتظار المراجعة وتفاصيل العميل والدفع والشحن والمنتجات",
                status: "completed",
                evidence: "PR #96 وما تلاه من تحسينات المراجعة",
            },
            {
                id: "reviewed-stage",
                title: "مرحلة تم المراجعة والدفعات والطباعة ورابط طلب سلة",
                status: "completed",
                evidence: "PR #118",
            },
            {
                id: "review-intelligence",
                title: "سجل العميل وصور التجهيز والخيارات والملاحظات",
                status: "completed",
                evidence: "PRs #132, #140, #147",
            },
            {
                id: "in-progress-stage",
                title: "مرحلة قيد التنفيذ وإسناد الطلب والقطع للموظف",
                status: "pending",
                next: true,
            },
            {
                id: "preparation-stage",
                title: "التجهيز من المستودع أو المورد أو التصنيع ومعالجة النقص",
                status: "pending",
            },
            {
                id: "assembly-stage",
                title: "الاستلام والتجميع بالباركود ومنع التكرار",
                status: "pending",
            },
            {
                id: "ready-to-ship-stage",
                title: "جاهز للشحن والبوليصة ورقم الشحنة والتحقق قبل الطباعة",
                status: "pending",
            },
            {
                id: "delivery-stages",
                title: "تم التنفيذ ثم جاري التوصيل ثم تم التوصيل ومزامنة الحالات",
                status: "pending",
            },
            {
                id: "returns-replacements",
                title: "المرتجعات والاستبدال الجزئي والكامل وأثر الشحن والمخزون",
                status: "pending",
            },
            {
                id: "orders-v2-parity",
                title: "نقل البحث المتقدم والتصدير والملخصات والمزامنة اليدوية من صفحة الطلبات القديمة",
                status: "pending",
            },
        ],
    },
    {
        id: "products-inventory",
        title: "المنتجات والمكونات والمخزون",
        description: "بيانات المنتج وتكلفته وصوره وموقعه وحركته من الشراء حتى الحجز والصرف.",
        core: true,
        tasks: [
            {
                id: "product-control",
                title: "مركز المنتجات والتفاصيل والخيارات والمتغيرات وSKU",
                status: "completed",
                evidence: "PRs #55, #57, #68",
            },
            {
                id: "bom-costs",
                title: "المكونات والوصفات وتكاليف الخيارات ولقطة تكلفة الطلب",
                status: "completed",
                evidence: "PRs #39, #61, #62, #64",
            },
            {
                id: "product-intake-access",
                title: "استقبال المنتجات وجاهزيتها والفريق والصلاحيات التشغيلية",
                status: "completed",
                evidence: "PRs #85, #88, #89",
            },
            {
                id: "product-media",
                title: "مسودات صور المنتج والرفع من الجهاز والنشر المحكوم إلى سلة",
                status: "completed",
                evidence: "PRs #92, #94, #97",
            },
            {
                id: "inventory-policy",
                title: "سياسات الشحن الفوري والتجهيز والمخزون حسب الفرع",
                status: "completed",
                evidence: "PRs #130, #145, #146",
            },
            {
                id: "inventory-movements",
                title: "إكمال الحجز والصرف والتحويل والاستلام الفعلي وربطها بدورة التجهيز",
                status: "in_progress",
            },
            {
                id: "purchasing-batches",
                title: "دفعات الشراء والمورد والفاتورة وتحديث التكلفة والموقع",
                status: "pending",
            },
            {
                id: "products-legacy-parity",
                title: "نقل بحث فواتير المورد والاستيراد والتصدير وسجل تغير التكلفة",
                status: "pending",
            },
        ],
    },
    {
        id: "finance-accounting",
        title: "المالية والمحاسبة وقيود",
        description: "توحيد الصفحات المالية الحالية دون تغيير كاتب قيود أو ترحيل إنتاجي غير معتمد.",
        core: true,
        tasks: [
            {
                id: "ledger-canonical",
                title: "الـLedger والشاشة المالية الموحدة والمطابقة كقدرات أساسية",
                status: "completed",
                evidence: "المساحات المالية الحالية والـLegacy Redirects المثبتة",
            },
            {
                id: "finance-v2-design",
                title: "تصميم مساحة مالية V2 تجمع الحسابات والحركات والمركز المالي والتقارير",
                status: "pending",
            },
            {
                id: "settlements-v2",
                title: "توحيد تسويات سلة وتمارا وتابي والتحويلات البنكية وCOD",
                status: "in_progress",
            },
            {
                id: "shipping-finance",
                title: "ربط دفتر الشحن والتحصيل والتحويلات مع الطلب ودورة التسليم",
                status: "pending",
            },
            {
                id: "qoyod-production-proof",
                title: "اختبار طلب حقيقي كامل: فاتورة وسداد وبنك وفروق هلل ومطابقة",
                status: "waiting",
                evidence: "يحافظ على وضع قيود الحالي؛ لا تغيير أو حذف أو تفعيل تلقائي من هذه الخطة",
            },
            {
                id: "finance-legacy-consolidation",
                title: "دمج صفحات الموردين والمصاريف والرواتب والعهد والتشخيص داخل مساحات مالكة",
                status: "pending",
            },
        ],
    },
    {
        id: "integrations-ads",
        title: "التطبيقات والبيانات الإعلانية",
        description: "ربط أصلي وقراءة موثوقة قبل السماح للذكاء بأي قرار أو تنفيذ.",
        core: true,
        tasks: [
            {
                id: "integrations-center",
                title: "بطاقات الربط والصلاحيات والصحة وسجل المزامنة والأخطاء",
                status: "completed",
                evidence: "PR #87",
            },
            {
                id: "snapchat-native",
                title: "سناب شات: OAuth واختيار الحسابات والمزامنة والتشخيص",
                status: "completed",
                evidence: "PRs #133, #137, #141, #148, #156",
            },
            {
                id: "meta-native",
                title: "Meta: OAuth واختيار الحسابات والمزامنة الأصلية للقراءة",
                status: "completed",
                evidence: "PRs #135, #161",
            },
            {
                id: "tiktok-native",
                title: "TikTok: OAuth والمزامنة الأصلية للقراءة",
                status: "waiting",
                evidence: "الكود مدمج عبر PRs #127 و#160؛ التشغيل ينتظر اعتماد التطبيق والربط الفعلي",
            },
            {
                id: "google-native",
                title: "Google Analytics وSearch Console وMerchant Center وGoogle Ads",
                status: "in_progress",
                evidence: "أساس OAuth مدمج عبر PR #116؛ جلب البيانات والتحقق الإنتاجي متبقٍ",
            },
            {
                id: "ads-manager-readonly",
                title: "مدير إعلانات موحد للقراءة وتحليل التغطية",
                status: "completed",
                evidence: "PRs #95 و#104",
            },
            {
                id: "ads-reconciliation",
                title: "مطابقة 7 أيام لكل حساب × يوم × عملة × منطقة زمنية مع المنصات",
                status: "pending",
            },
            {
                id: "campaign-mutations",
                title: "إنشاء وإيقاف وتعديل الحملات والميزانيات والإعلانات",
                status: "deferred",
                evidence: "بعد اكتمال النواة والمطابقة، وبالمسار: اقتراح ← معاينة ← اعتماد ← تنفيذ ← تحقق ← رجوع",
            },
        ],
    },
    {
        id: "security-release",
        title: "الأمان والجودة والإطلاق",
        description: "لا يُعلن اكتمال ميزان 2 قبل ثبات الإنتاج وإثبات الصلاحيات والرجوع والمراقبة.",
        core: true,
        tasks: [
            {
                id: "secret-safety",
                title: "عدم إظهار المفاتيح والتوكنات وفصل القراءة عن الكتابة",
                status: "completed",
                evidence: "حراس Integrations V2 وAI وAds",
            },
            {
                id: "legal-pages",
                title: "سياسة الخصوصية وحذف البيانات وشروط الاستخدام",
                status: "completed",
                evidence: "PR #165",
            },
            {
                id: "role-parity",
                title: "تكافؤ صلاحيات الموظفين في مساحات V2 قبل إغلاق المسارات القديمة",
                status: "pending",
            },
            {
                id: "end-to-end-suite",
                title: "اختبار متكامل: طلب ← مراجعة ← مخزون ← تجهيز ← شحن ← مالية",
                status: "pending",
            },
            {
                id: "performance-monitoring",
                title: "اختبارات الأداء والمراقبة والتنبيهات وسجل فشل قابل للتتبع",
                status: "pending",
            },
            {
                id: "rollback-release",
                title: "نسخة احتياطية وخطة رجوع واعتماد نشر إنتاجي بلا تعارض",
                status: "in_progress",
            },
        ],
    },
    {
        id: "customer-intelligence",
        title: "خدمة العملاء والذكاء التجاري",
        description: "الخطوة التالية بعد اكتمال النواة: تحويل المحادثة إلى فهم ومتابعة وبيع وقرارات منتجات.",
        core: false,
        tasks: [
            {
                id: "customer-preview",
                title: "مركز معاينة محكوم لذكاء العملاء والمبيعات",
                status: "completed",
                evidence: "PR #106 — بيانات مصطنعة وقراءة فقط",
            },
            {
                id: "whatsapp-ingestion",
                title: "ربط WhatsApp Business وحفظ الرسائل الخام والوسائط والموافقات",
                status: "deferred",
            },
            {
                id: "customer-identity",
                title: "ربط المحادثة بالعميل والطلب والمنتج والحملة مع سجل موحد",
                status: "deferred",
            },
            {
                id: "suggested-replies",
                title: "فهم اللهجة والأخطاء واقتراح رد ومتابعة وعرض مناسب",
                status: "deferred",
            },
            {
                id: "approved-replies",
                title: "ردود ومتابعات تلقائية محدودة بعد الاعتماد والقياس",
                status: "deferred",
            },
            {
                id: "customer-learning",
                title: "قياس إكمال الطلب وأسباب الانسحاب وفرص المنتجات والحملات",
                status: "deferred",
            },
        ],
    },
    {
        id: "ai-executive",
        title: "الذكاء التشغيلي والتنفيذ التدريجي",
        description: "يراقب ويفهم ويقترح ثم ينفذ ضمن صلاحيات صغيرة قابلة للرجوع.",
        core: false,
        tasks: [
            {
                id: "ai-readonly",
                title: "محلل عمليات للقراءة مع سياق Orders V2",
                status: "completed",
                evidence: "PRs #45 و#158",
            },
            {
                id: "product-intelligence-rules",
                title: "قواعد جاهزية وفرص المنتجات بدون تنفيذ تلقائي",
                status: "completed",
                evidence: "PR #114",
            },
            {
                id: "decision-queue",
                title: "طابور قرار موحد: دليل وسبب وثقة ومخاطر واعتماد",
                status: "deferred",
            },
            {
                id: "safe-execution",
                title: "تنفيذ منخفض المخاطر مع تحقق وسجل وRollback",
                status: "deferred",
            },
            {
                id: "ai-campaign-builder",
                title: "بناء وتحسين الحملات والمواد الإعلانية بعد اكتمال جودة البيانات",
                status: "deferred",
            },
            {
                id: "ai-executive-cycle",
                title: "الدورة الكاملة: يراقب ← يفهم ← يقترح ← ينفذ ← يقيس ← يتعلم",
                status: "deferred",
            },
        ],
    },
];

export const LEGACY_MIGRATION_GROUPS = [
    {
        id: "orders",
        source: "/orders",
        sourceLabel: "الطلبات القديمة",
        destination: "/orders-v2",
        decision: "merge_remaining",
        move: "البحث المتقدم، التصدير، الملخصات، والمزامنة اليدوية.",
        retireWhen: "تكتمل القدرات ويثبت عدم اعتماد الموظفين على الصفحة القديمة.",
    },
    {
        id: "order-review",
        source: "/order-review",
        sourceLabel: "بانتظار المراجعة",
        destination: "/fulfillment-v2?stage=pending_review",
        decision: "redirected",
        move: "تم دمج الوظيفة ووضع تحويل للمسار القديم.",
        retireWhen: "يتحقق تكافؤ صلاحيات الموظفين في V2.",
    },
    {
        id: "products",
        source: "/products + /product-costs",
        sourceLabel: "المنتجات وتكاليف المنتجات",
        destination: "/products-v2 + /components-v2",
        decision: "merge_remaining",
        move: "بحث فواتير المورد، الاستيراد والتصدير، وسجل تغير التكلفة.",
        retireWhen: "تنجح مطابقة بيانات المنتج والتكلفة والخيارات على الإنتاج.",
    },
    {
        id: "preparation-images",
        source: "/product-preparation + /image-catalog",
        sourceLabel: "تجهيز المنتجات وإدارة الصور",
        destination: "/fulfillment-v2 + /products-v2",
        decision: "merge_remaining",
        move: "PDF الدفعات، الإسناد، الاستلام، صور التجهيز، وإعادة الطباعة.",
        retireWhen: "تعمل مراحل التجهيز والاستلام كاملة داخل V2.",
    },
    {
        id: "snapchat",
        source: "/snapchat-accounts",
        sourceLabel: "حسابات سناب القديمة",
        destination: "/integrations-v2 + /ads-manager",
        decision: "redirected",
        move: "تم نقل الربط والمزامنة والتحليل ووضع تحويل للمسار القديم.",
        retireWhen: "يمر نشر إنتاجي متحقق ثم تزال المكونات والـAPI غير المستخدمة.",
    },
    {
        id: "ads",
        source: "/ads-v2/settings + /ads-v2/report + /ad-accounts",
        sourceLabel: "إعدادات وتقارير وحسابات الإعلانات",
        destination: "/integrations-v2 + /ads-manager + المالية",
        decision: "merge_remaining",
        move: "نفصل الربط عن التحليل عن الدين المحاسبي، وننقل المطابقة والقيم اليدوية إلى مالك واحد.",
        retireWhen: "تكتمل المطابقة لكل منصة وتبقى كتابة محاسبية واحدة فقط.",
    },
    {
        id: "finance",
        source: "الحسابات والحركات والمركز المالي والتسويات والموردون والمصاريف والرواتب والعهد",
        sourceLabel: "الصفحات المالية الحالية",
        destination: "مساحة المالية والمحاسبة V2",
        decision: "keep_now",
        move: "تُجمع كمساحات وتبويبات حسب الوظيفة؛ لا ننسخ عشرات الصفحات كما هي.",
        retireWhen: "يعتمد تصميم Finance V2 ويحافظ على Ledger كمصدر وحيد.",
    },
    {
        id: "salla-qoyod",
        source: "صفحات سلة وقيود وWebhooks والمراقبة والمطابقة",
        sourceLabel: "التكاملات الحساسة الحالية",
        destination: "/integrations-v2 + مساحة مالية مخصصة",
        decision: "keep_now",
        move: "تبقى في مكانها الآن. ننقلها مزودًا مزودًا بعد تكافؤ الوظيفة، ولا نغير كاتب سلة أو قيود.",
        retireWhen: "اعتماد صريح واختبار إنتاجي وخطة رجوع لكل مزود.",
    },
    {
        id: "diagnostics",
        source: "صفحات diagnostics وaudit المؤقتة",
        sourceLabel: "التشخيص والتدقيق",
        destination: "تبويب صحة وسجل أخطاء داخل كل مساحة مالكة",
        decision: "embed_later",
        move: "نحتفظ بالتشخيص المفيد ونزيل الصفحات المؤقتة والمكررة بعد نقله.",
        retireWhen: "يظهر نفس الدليل والإجراء داخل الصفحة المالكة وتصل تقارير الاستخدام إلى صفر.",
    },
];

export const PARALLEL_WORKSTREAMS = [
    {
        id: "parallel-fulfillment",
        rank: 1,
        title: "إدارة التجهيز — المرحلة التالية",
        scope: "قيد التنفيذ ثم التجهيز والاستلام والتجميع",
        branch: "agent/fulfillment-v2-next-stages",
        canStart: true,
        dependencies: "يعتمد على Orders V2 وInventory V2 الموجودين؛ المراحل نفسها تنفذ بالتتابع.",
        protected: "لا يلمس ملفات التطبيقات أو الإعلانات أو قيود.",
    },
    {
        id: "parallel-finance",
        rank: 2,
        title: "تدقيق المالية وقيود قبل النقل",
        scope: "خريطة المصادر والكتاب، اختبار المطابقة، وتصميم Finance V2 دون تفعيل كتابة جديدة",
        branch: "agent/finance-v2-capability-audit",
        canStart: true,
        dependencies: "يبدأ Read-only؛ أي تغيير إنتاجي في قيود ينتظر اعتمادًا منفصلًا.",
        protected: "لا حذف من قيود، لا تغيير auto-send، ولا ترحيل محاسبي.",
    },
    {
        id: "parallel-legacy",
        rank: 3,
        title: "تدقيق نقل الصفحات القديمة",
        scope: "اختبار تكافؤ القدرات والاستخدام والتحويلات، دون نسخ واجهات قديمة",
        branch: "agent/mezan-v2-legacy-parity",
        canStart: true,
        dependencies: "يمكنه توثيق الفجوات الآن؛ الحذف ينتظر اكتمال البديل.",
        protected: "لا يحذف صفحة لها مستخدمون أو مسار كتابة قائم.",
    },
    {
        id: "parallel-ads-quality",
        rank: 4,
        title: "جودة بيانات الإعلانات",
        scope: "مطابقة 7 أيام لكل حساب ومنصة، وتحضير Google للقراءة",
        branch: "agent/ads-v2-production-reconciliation",
        canStart: true,
        dependencies: "يحتاج الحسابات والتوكنات المعتمدة فقط وقت اختبار الإنتاج.",
        protected: "قراءة فقط؛ لا إنشاء حملة أو تعديل ميزانية.",
    },
    {
        id: "parallel-release",
        rank: 5,
        title: "ثبات الإنتاج والاختبارات الشاملة",
        scope: "Smoke tests، صلاحيات، أداء، مراقبة، وخطة رجوع",
        branch: "agent/mezan-v2-release-readiness",
        canStart: true,
        dependencies: "يبني الاختبارات الآن ويعيد تشغيلها بعد دمج كل مسار.",
        protected: "لا يغير منطق الأعمال لإجبار الاختبارات على النجاح.",
    },
    {
        id: "parallel-customer-service",
        rank: 6,
        title: "خدمة العملاء الذكية",
        scope: "ربط الرسائل الحقيقية والهوية والاقتراحات والمتابعة",
        branch: "agent/customer-intelligence-live-foundation",
        canStart: false,
        dependencies: "تبدأ بعد تثبيت دورة الطلب والمنتج والمخزون أو كتأسيس عقود فقط دون تشغيل حي.",
        protected: "لا رد تلقائي ولا عرض ولا إنشاء طلب قبل الموافقة والقياس.",
    },
];

export function getCompletionSummary({ coreOnly = true } = {}) {
    const tasks = MEZAN_V2_WORKSTREAMS
        .filter((workstream) => !coreOnly || workstream.core)
        .flatMap((workstream) => workstream.tasks)
        .filter((task) => task.status !== "deferred");
    const counts = tasks.reduce((accumulator, task) => {
        accumulator[task.status] = (accumulator[task.status] || 0) + 1;
        return accumulator;
    }, {});
    const completed = counts.completed || 0;
    return {
        total: tasks.length,
        completed,
        inProgress: counts.in_progress || 0,
        pending: counts.pending || 0,
        waiting: counts.waiting || 0,
        percent: tasks.length ? Math.round((completed / tasks.length) * 100) : 0,
    };
}

export function getMigrationSummary() {
    return LEGACY_MIGRATION_GROUPS.reduce((summary, item) => {
        summary[item.decision] = (summary[item.decision] || 0) + 1;
        return summary;
    }, {});
}
