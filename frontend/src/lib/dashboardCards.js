/**
 * Dashboard KPI cards — single source of truth.
 *
 * Each card carries:
 *   id          : unique key (matches `dashboard_hidden_cards` entry)
 *   label       : Arabic display name
 *   icon        : Phosphor icon component
 *   accent?     : highlight tile in the brand color
 *   money?      : append "(ر.س)" + format with thousands separators
 *   isInt?      : integer formatting (no decimals, no SAR)
 *   format?     : optional override returning a pre-formatted string
 *   value       : pure function (totals) => raw value
 *   hint?       : one-line subtitle under the icon
 *   explanation : iter-48 — multi-line tooltip shown when the merchant
 *                 hovers/taps the ⓘ icon on the card. Describes HOW the
 *                 figure is calculated so they can audit it themselves.
 */
import {
    Coins, ShoppingBag, Receipt, Truck, Megaphone, Package,
    TrendUp, Percent, Wallet, Bank, ChartLineUp, Tag,
} from "@phosphor-icons/react";

export const KPI_GROUPS = [
    {
        id: "sales",
        title: "المبيعات والطلبات",
        cards: [
            {
                id: "total_sales", label: "إجمالي المبيعات", icon: Coins, accent: true, money: true,
                value: (t) => t.total_sales,
                explanation:
                    "مجموع `total_amount` لكل الطلبات الموحَّدة (unified_orders) ضمن الفترة المحددة.\n" +
                    "يتأثر بفلتر الحالات في الإعدادات إن كان مفعّلاً، وبخيار إخفاء الطلبات بتاريخ تقريبي."
            },
            {
                id: "net_sales", label: "صافي المبيعات", icon: TrendUp, accent: true, money: true,
                hint: "حسب إعدادات الخصم",
                value: (t) => t.net_sales,
                explanation:
                    "= إجمالي المبيعات − ما تختاره في إعدادات صافي المبيعات.\n" +
                    "يمكن خصم: عمولات بوابات الدفع + رسوم الشحن + المرتجعات + ضريبة القيمة المضافة."
            },
            {
                id: "total_orders", label: "إجمالي الطلبات", icon: ShoppingBag, isInt: true,
                value: (t) => t.total_orders,
                explanation:
                    "عدد الطلبات الفريدة في الفترة المحددة.\n" +
                    "يستبعد أي طلبات مكرّرة من Make.com أو Excel، ويخضع لفلاتر الحالة في الإعدادات."
            },
            {
                id: "expected_salla_transfer", label: "المتوقع من سلة", icon: Bank, accent: true, money: true,
                hint: "حوالة سلة المتوقعة",
                value: (t) => t.expected_salla_transfer,
                explanation:
                    "= إجمالي المبيعات − إجمالي رسوم بوابات الدفع − تكاليف الشحن غير الآجل.\n" +
                    "تقدير لما تتوقع سلة تحويله لك بعد خصم عمولاتها والشحن. لا يشمل الشحن الآجل."
            },
        ],
    },
    {
        id: "marketing",
        title: "أداء التسويق",
        cards: [
            {
                id: "overall_roas",
                label: "ROAS (العائد على الإنفاق الإعلاني)",
                icon: ChartLineUp,
                accent: true,
                hint: "إجمالي المبيعات ÷ إجمالي تكلفة الإعلانات",
                value: (t) => t.overall_roas,
                format: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}×`),
                explanation:
                    "= إجمالي المبيعات ÷ إجمالي تكلفة الإعلانات (سناب + تيك توك + ميتا + قوقل + إنستغرام).\n" +
                    "كلما زاد الرقم، كان عائد كل ريال إعلاني أعلى. القيمة 3× تعني أن كل 1 ر.س إعلان يُحقّق 3 ر.س مبيعات.\n" +
                    "يُعرض «—» عند عدم وجود إنفاق إعلاني في الفترة."
            },
            {
                id: "avg_cost_per_order",
                label: "متوسط تكلفة الطلب",
                icon: Tag,
                accent: true,
                money: true,
                hint: "إجمالي تكلفة الإعلانات ÷ عدد الطلبات",
                value: (t) => t.avg_cost_per_order,
                explanation:
                    "= إجمالي تكلفة الإعلانات ÷ عدد الطلبات.\n" +
                    "كم تنفق إعلانياً مقابل كل طلب. يُساعدك على معرفة هامش الربح المتبقي بعد الإعلان."
            },
        ],
    },
    {
        id: "payments",
        title: "رسوم بوابات الدفع",
        cards: [
            {
                id: "other_payment_fees", label: "رسوم بوابات الدفع", icon: Receipt, money: true,
                hint: "عدا تمارا وتابي وإمكان",
                value: (t) => t.other_payment_fees,
                explanation:
                    "مجموع عمولات البوابات الإلكترونية (مدى، فيزا، Apple Pay…) محسوبة من إعدادات\n" +
                    "طرق الدفع: (نسبة × المبلغ) + رسم ثابت + ضريبة القيمة المضافة 15%.\n" +
                    "لا يشمل تمارا/تابي/إمكان (تمت تسويتها في بطاقاتها) ولا COD ولا التحويلات البنكية."
            },
            {
                id: "electronic_net", label: "صافي المدفوعات الإلكترونية", icon: Wallet, money: true,
                hint: "المبيعات − العمولات",
                value: (t) => t.electronic_net,
                explanation:
                    "= مبيعات الطلبات الإلكترونية المُكتمَلة − عمولات البوابات.\n" +
                    "تُستبعد الطلبات الملغية/المرتجعة/الفاشلة/المعلّقة لتطابق شاشة سلة → غير المفوترة.\n" +
                    "لا يشمل: BNPL ولا COD ولا التحويلات البنكية. اضغط زر «تفاصيل» لرؤية الفلترة كاملة."
            },
            {
                id: "bank_net", label: "المدفوعات البنكية", icon: Bank, accent: true, money: true,
                hint: "تحويل بنكي بعد العمولة",
                value: (t) => t.bank_net,
                explanation:
                    "= مبيعات الطلبات بطريقة «تحويل بنكي / Bank Transfer» − أي عمولة مضبوطة لهذه الطريقة.\n" +
                    "يظهر كل المبالغ بكل الحالات (يمثّل تدفقاً نقدياً بنكياً، ليس مدفوعات بوابة).\n" +
                    "منفصل عن «صافي المدفوعات الإلكترونية» لأن التسوية البنكية لا تمر عبر سلة."
            },
            {
                id: "tamara_fees", label: "رسوم تمارا", icon: Receipt, money: true, hint: "BNPL",
                value: (t) => t.tamara_fees,
                explanation:
                    "مجموع عمولات تمارا = (نسبة × مبلغ الطلب) + رسم ثابت + ضريبة 15%، حسب إعدادات طريقة الدفع.\n" +
                    "النسبة الافتراضية في سلة لتمارا = 6% (قابلة للتعديل من الإعدادات)."
            },
            {
                id: "tabby_fees", label: "رسوم تابي", icon: Receipt, money: true, hint: "BNPL",
                value: (t) => t.tabby_fees,
                explanation:
                    "مجموع عمولات تابي = (نسبة × مبلغ الطلب) + رسم ثابت + ضريبة 15%، حسب إعدادات طريقة الدفع."
            },
            {
                id: "emkan_fees", label: "رسوم إمكان", icon: Receipt, money: true, hint: "BNPL",
                value: (t) => t.emkan_fees,
                explanation:
                    "مجموع عمولات إمكان = (نسبة × مبلغ الطلب) + رسم ثابت + ضريبة 15%، حسب إعدادات طريقة الدفع."
            },
            {
                id: "bnpl_net", label: "صافي تمارا وتابي وإمكان", icon: Wallet, money: true,
                hint: "بعد خصم العمولات",
                value: (t) => t.bnpl_net,
                explanation:
                    "= مبيعات (تمارا + تابي + إمكان) − مجموع عمولات الثلاثة.\n" +
                    "صافي ما يصل إليك من بوابات الدفع المُؤجَّل (BNPL)."
            },
        ],
    },
    {
        id: "shipping",
        title: "الشحن",
        cards: [
            {
                id: "total_shipping_cost", label: "تكاليف الشحن", icon: Truck, money: true,
                value: (t) => t.total_shipping_cost,
                explanation:
                    "مجموع تكاليف الشحن لكل الطلبات = (سعر الشحن المُضبوط لكل شركة شحن) × (عدد الطلبات).\n" +
                    "يشمل شركات الشحن النقدية والآجلة معاً."
            },
            {
                id: "deferred_shipping_cost", label: "مستحقات الشحن الآجل", icon: Truck, money: true,
                hint: "ذمم للشركات الآجلة",
                value: (t) => t.deferred_shipping_cost,
                explanation:
                    "تكلفة الشحن للشركات التي ضبطتها كـ«آجلة» في الإعدادات (مثل J&T، iMile).\n" +
                    "هذه تُسوَّى لاحقاً، لذا تُحسب كذمم مستحقة بدلاً من تخصم من حوالة سلة المتوقعة."
            },
            {
                id: "shipping_approved", label: "رصيد شحن معتمد", icon: Truck, money: true,
                hint: "طلبات (تم التوصيل)",
                value: (t) => t.shipping_approved,
                explanation:
                    "تكلفة الشحن للطلبات التي حالتها ضمن «الحالات المعتمدة للشحن» في الإعدادات (افتراضياً: تم التوصيل).\n" +
                    "تُمثل ما يجب على شركة الشحن أن تُسلِّمك إيّاه أو تخصم منك."
            },
            {
                id: "shipping_unapproved", label: "رصيد شحن غير معتمد", icon: Truck, money: true,
                hint: "طلبات قيد التنفيذ/الشحن",
                value: (t) => t.shipping_unapproved,
                explanation:
                    "تكلفة الشحن للطلبات التي لم تصل بعد لحالة «معتمدة».\n" +
                    "تكاليف محجوزة قابلة للتحوّل إلى معتمدة عند اكتمال التوصيل، أو مسترَدّة عند الإلغاء."
            },
        ],
    },
    {
        id: "cod",
        title: "الدفع عند الاستلام (COD)",
        cards: [
            {
                id: "cod_approved", label: "COD معتمد", icon: Wallet, accent: true, money: true,
                hint: "مستحق على شركة الشحن",
                value: (t) => t.cod_approved,
                explanation:
                    "مجموع مبالغ طلبات COD التي حالتها ضمن «الحالات المعتمدة للـ COD» (افتراضياً: تم التوصيل).\n" +
                    "هذا هو المبلغ الذي تطلبه من شركة الشحن لتحوّله لك بعد التحصيل."
            },
            {
                id: "cod_unapproved", label: "COD غير معتمد", icon: Wallet, money: true,
                hint: "لم يصل بعد",
                value: (t) => t.cod_unapproved,
                explanation:
                    "مجموع مبالغ طلبات COD التي لم تصل بعد لحالة «معتمدة».\n" +
                    "أرصدة مُتوقَّعة عند اكتمال التوصيل، أو تسقط عند الإلغاء/الإرجاع."
            },
        ],
    },
    {
        id: "costs",
        title: "المصاريف والضريبة",
        cards: [
            {
                id: "total_vat", label: "إجمالي الضريبة المخصومة", icon: Percent, money: true,
                hint: "ضريبة الدفع + الشحن",
                value: (t) => t.total_vat,
                explanation:
                    "مجموع ضريبة القيمة المضافة (15%) المُحتسبة على عمولات بوابات الدفع ورسوم الشحن.\n" +
                    "هذا المبلغ يُخصم منك ضمن العمولات، وقد يكون قابلاً للاسترداد ضريبياً حسب وضعك."
            },
            {
                id: "total_ads_cost", label: "تكاليف الإعلانات", icon: Megaphone, money: true,
                value: (t) => t.total_ads_cost,
                explanation:
                    "مجموع الإنفاق الإعلاني عبر كل المنصات: سناب شات + تيك توك + ميتا + إنستغرام + قوقل.\n" +
                    "يأتي من Webhooks الـTikTok والـMeta المتصلة، أو من إدخالك اليدوي في «التكاليف اليومية»."
            },
            {
                id: "total_product_cost", label: "تكاليف المنتجات", icon: Package, money: true,
                hint: "من ملفات Excel",
                value: (t) => t.total_product_cost,
                explanation:
                    "= MAX(تكاليف من ملف Excel/المنتجات لكل SKU، إجمالي تكلفة المنتجات اليومي المُدخل يدوياً).\n" +
                    "يُستخدم الأكبر بين القيمتين تجنّباً للحساب المزدوج. يدخل في صافي الربح النهائي."
            },
            {
                id: "daily_expenses_total", label: "مصاريف يومية", icon: Receipt, money: true,
                hint: "من سجل التكاليف",
                value: (t) => t.daily_expenses_total,
                explanation:
                    "مجموع تكاليف المنتجات اليومية التي أدخلتها يدوياً من زر «إجمالي تكلفة يوم» في صفحة تكاليف المنتجات.\n" +
                    "هذه قيمة مؤقتة حتى تربط كل طلب بتكاليف SKU الفعلية."
            },
            {
                id: "operating_expenses_total", label: "المصروفات التشغيلية", icon: Receipt, money: true,
                hint: "رواتب + إيجارات + مدفوعة مقدماً + يومية أخرى",
                value: (t) => t.operating_expenses_total,
                explanation:
                    "مجموع المصروفات التشغيلية في صفحة «المصروفات التشغيلية»:\n" +
                    "الرواتب (الموظفين + بيت + صدقات) + الإيجارات + المصاريف المدفوعة مقدماً + اليومية الأخرى.\n" +
                    "تُوزَّع على أيام الفترة لتعكس الجزء الذي يخص هذا التقرير فقط."
            },
            {
                id: "operating_salaries_total", label: "إجمالي الرواتب", icon: Coins, money: true,
                hint: "موظفين + بيت + صدقات",
                value: (t) => t.operating_salaries_total,
                explanation:
                    "مجموع جزء الرواتب (موظفين + بيت + صدقات) المُوزَّع على الفترة من إعدادات الرواتب الشهرية.\n" +
                    "= (الراتب الشهري × عدد أيام الفترة) ÷ 30."
            },
            {
                id: "operating_rentals_total", label: "إيجارات الفترة", icon: Bank, money: true,
                hint: "بالتوزيع اليومي",
                value: (t) => t.operating_rentals_total,
                explanation:
                    "مجموع الإيجارات المُسجَّلة في إعدادات «المصروفات التشغيلية»، موزَّعة على أيام كل عقد.\n" +
                    "= (إجمالي الإيجار ÷ مدة العقد) × عدد الأيام المنطبقة في الفترة."
            },
            {
                id: "operating_prepaid_total", label: "المدفوعة مقدماً (تأمين/إقامات)", icon: Receipt, money: true,
                hint: "بالتوزيع اليومي على فترة الانتفاع",
                value: (t) => t.operating_prepaid_total,
                explanation:
                    "المبالغ المدفوعة مقدماً (تأمين سيارة، رخص، إقامات) موزَّعة على أيام فترة الانتفاع.\n" +
                    "= (المبلغ الكلي ÷ عدد أيام السريان) × عدد الأيام المنطبقة في الفترة."
            },
            {
                id: "net_profit", label: "صافي الربح النهائي", icon: TrendUp, accent: true, money: true,
                hint: "بعد التكاليف اليومية والمصروفات التشغيلية",
                value: (t) => t.net_profit,
                explanation:
                    "= إجمالي المبيعات − رسوم بوابات الدفع − تكاليف الشحن غير الآجل − تكاليف الإعلانات\n" +
                    "  − تكاليف المنتجات − المصروفات التشغيلية − الضريبة المخصومة.\n" +
                    "الرقم الصافي الفعلي بعد كل المصاريف. إذا كانت تكاليف المنتجات ناقصة، الرقم تقريبي."
            },
        ],
    },
];

/** Flat list of all KPI cards (preserves group order). */
export const ALL_KPI_CARDS = KPI_GROUPS.flatMap((g) => g.cards.map((c) => ({ ...c, groupId: g.id, groupTitle: g.title })));

/** Look up a card by id (returns undefined if removed). */
export function findKpi(id) {
    return ALL_KPI_CARDS.find((c) => c.id === id);
}
