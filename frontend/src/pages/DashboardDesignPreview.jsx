import { useState } from "react";
import {
    AlertTriangle,
    BarChart3,
    BriefcaseBusiness,
    CalendarDays,
    ChevronDown,
    CircleDollarSign,
    CreditCard,
    MapPin,
    Megaphone,
    PackageOpen,
    RefreshCw,
    ShoppingBag,
    ShoppingCart,
    TrendingUp,
    Truck,
    Trophy,
    UsersRound,
} from "lucide-react";
import {
    CartesianGrid,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

export const PROFIT_SUMMARY_LABELS = [
    "المبيعات",
    "تكاليف المنتجات",
    "إجمالي تكاليف الإعلانات",
    "إجمالي تكاليف الشحن (مقدم + أجل)",
    "إجمالي رسوم جميع طرق الدفع",
    "المصروفات التشغيلية (رواتب وإيجارات وغيرها)",
    "صافي الأرباح",
];

export const ORDER_TIMES = [
    "منذ 1 ثانية",
    "منذ 2 دقائق",
    "منذ 1 ساعة",
    "منذ ساعتين",
    "منذ 20 ساعة",
    "منذ 1 يوم",
    "منذ 1 شهر",
    "منذ 1 سنة",
];

const PREVIEW_TOTALS = {
    sales: 169155,
    productCost: 48920,
    adsCost: 22740,
    shippingCost: 12480,
    paymentFees: 5936,
    operatingExpenses: 10240,
};

const KPI_CARDS = [
    { label: "تكلفة الطلب", value: "25.18 ر.س", Icon: ShoppingBag, tone: "blue" },
    { label: "عدد الطلبات", value: "903", Icon: ShoppingCart, tone: "emerald" },
    { label: "العائد", value: "7.44×", Icon: TrendingUp, tone: "violet" },
    { label: "متوسط قيمة سلة المشتريات", value: "187.33 ر.س", Icon: ShoppingBag, tone: "rose" },
];

const DAILY_AD_DATA = [
    { label: "1 أغسطس", snapchat: 390, tiktok: 360, meta: 420, google: 180 },
    { label: "2 أغسطس", snapchat: 440, tiktok: 330, meta: 480, google: 190 },
    { label: "3 أغسطس", snapchat: 360, tiktok: 310, meta: 400, google: 170 },
    { label: "4 أغسطس", snapchat: 500, tiktok: 390, meta: 520, google: 210 },
    { label: "5 أغسطس", snapchat: 630, tiktok: 440, meta: 590, google: 250 },
    { label: "6 أغسطس", snapchat: 590, tiktok: 470, meta: 610, google: 270 },
    { label: "7 أغسطس", snapchat: 430, tiktok: 350, meta: 450, google: 190 },
    { label: "8 أغسطس", snapchat: 510, tiktok: 410, meta: 530, google: 230 },
    { label: "9 أغسطس", snapchat: 610, tiktok: 470, meta: 620, google: 280 },
    { label: "10 أغسطس", snapchat: 470, tiktok: 390, meta: 500, google: 220 },
    { label: "11 أغسطس", snapchat: 560, tiktok: 430, meta: 570, google: 260 },
    { label: "12 أغسطس", snapchat: 490, tiktok: 400, meta: 520, google: 240 },
    { label: "13 أغسطس", snapchat: 560, tiktok: 450, meta: 600, google: 300 },
];

const MONTHLY_AD_DATA = [
    { label: "مارس", snapchat: 12400, tiktok: 9800, meta: 11200, google: 5100 },
    { label: "أبريل", snapchat: 13800, tiktok: 10500, meta: 12300, google: 5700 },
    { label: "مايو", snapchat: 14900, tiktok: 11800, meta: 13700, google: 6200 },
    { label: "يونيو", snapchat: 13200, tiktok: 12100, meta: 14200, google: 6500 },
    { label: "يوليو", snapchat: 15800, tiktok: 13400, meta: 15100, google: 7100 },
    { label: "أغسطس", snapchat: 6340, tiktok: 6120, meta: 6890, google: 3390 },
];

const AD_PLATFORMS = [
    { key: "snapchat", label: "سناب شات", color: "#f59e0b", value: 6340 },
    { key: "tiktok", label: "تيك توك", color: "#111827", value: 6120 },
    { key: "meta", label: "Meta", color: "#2563eb", value: 6890 },
    { key: "google", label: "Google Ads", color: "#059669", value: 3390 },
];

const ABANDONED_CARTS = [
    { product: "مريول مدرسي وردي طويل بالدانتيل للبنات", customer: "سارة القحطاني", amount: 324, age: "منذ 18 دقيقة", emoji: "👗", tone: "rose" },
    { product: "تيشيرت تشجيع أصفر براق بالاسم والرقم", customer: "أحمد الشهري", amount: 160.92, age: "منذ 43 دقيقة", emoji: "👕", tone: "amber" },
    { product: "تيشيرت تشجيع أصفر بتفاصيل براقة", customer: "نوف العتيبي", amount: 160.92, age: "منذ ساعة", emoji: "👕", tone: "yellow" },
    { product: "طقم رياضي أبيض وأخضر للصغار", customer: "محمد الدوسري", amount: 150.12, age: "منذ ساعتين", emoji: "🥋", tone: "emerald" },
    { product: "طقم رياضي أبيض وأخضر بشعار الفريق", customer: "عبدالعزيز العنزي", amount: 210, age: "منذ 3 ساعات", emoji: "👚", tone: "cyan" },
];

const TOP_PRODUCTS = [
    { product: "تيشيرت تشجيع أصفر بالاسم والرقم", units: 1284, sales: 205722.88, emoji: "👕", tone: "amber" },
    { product: "تيشيرت تشجيع أبيض بتفاصيل خضراء", units: 1021, sales: 162914.32, emoji: "👚", tone: "emerald" },
    { product: "مريول مدرسي وردي طويل بالدانتيل", units: 876, sales: 104976, emoji: "👗", tone: "rose" },
    { product: "طقم رياضي أبيض وأخضر بشعار الفريق", units: 712, sales: 93312, emoji: "🥋", tone: "cyan" },
    { product: "هودي تشجيعي أخضر بحجيب", units: 598, sales: 71681.2, emoji: "🧥", tone: "green" },
];

const ORDER_ROWS = [
    { name: "نوال البدري", number: "277947819", city: "الرياض", status: "تم المراجعة", amount: 161.11, time: ORDER_TIMES[0], fresh: true, avatar: "👩🏻" },
    { name: "يزيد صالح", number: "277947445", city: "جدة", status: "تم المراجعة", amount: 113.4, time: ORDER_TIMES[1], fresh: false, avatar: "👨🏻" },
    { name: "Shatha Almorshid", number: "277944366", city: "الرياض", status: "بانتظار المراجعة", amount: 175.12, time: ORDER_TIMES[2], fresh: true, avatar: "👩🏻" },
    { name: "أمينة الغامدي", number: "277936572", city: "الدمام", status: "تم المراجعة", amount: 134, time: ORDER_TIMES[3], fresh: false, avatar: "👨🏻" },
    { name: "وفاء الريحان", number: "277934656", city: "الرياض", status: "تم المراجعة", amount: 204.12, time: ORDER_TIMES[4], fresh: false, avatar: "👩🏻" },
    { name: "Zina Obaid", number: "277927065", city: "جدة", status: "بانتظار المراجعة", amount: 295.22, time: ORDER_TIMES[5], fresh: true, avatar: "👨🏻" },
    { name: "عبدالله عود", number: "277922836", city: "صبيا", status: "تم المراجعة", amount: 233.36, time: ORDER_TIMES[6], fresh: false, avatar: "👩🏻" },
    { name: "علي ناجي سرحان", number: "277921542", city: "الرياض", status: "تم المراجعة", amount: 170.83, time: ORDER_TIMES[7], fresh: false, avatar: "👨🏻" },
];

const TOP_PAGES = [
    { title: "مسبحة رجالية فخمة بالاسم | متجر أماسي", views: 58 },
    { title: "قلادة زهرة بيضاء بالاسم حسب الطلب", views: 28 },
    { title: "كفر آيفون مرآة مخصص بالاسم وزهور", views: 23 },
    { title: "متجر أماسي — هدايا تلامس القلوب", views: 18 },
    { title: "الشحن والتوصيل — متجر أماسي", views: 17 },
    { title: "كوب خيل عربي مخصص بالعبارات", views: 17 },
];

const ACTIVE_USERS = [6, 12, 13, 13, 11, 8, 7, 21, 11, 16, 17, 12, 15, 11, 8, 11, 15, 9, 9, 7, 11, 12, 11, 10, 9, 11, 15, 7, 14, 11, 3];

function money(value) {
    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(value);
}

function percentage(value, total = PREVIEW_TOTALS.sales) {
    return `${((value / total) * 100).toFixed(2)}%`;
}

function toneClasses(tone) {
    const tones = {
        blue: "bg-blue-50 text-blue-700 ring-blue-100",
        emerald: "bg-emerald-50 text-emerald-700 ring-emerald-100",
        violet: "bg-violet-50 text-violet-700 ring-violet-100",
        rose: "bg-rose-50 text-rose-600 ring-rose-100",
        amber: "bg-amber-50 text-amber-700 ring-amber-100",
        yellow: "bg-yellow-50 text-yellow-700 ring-yellow-100",
        cyan: "bg-cyan-50 text-cyan-700 ring-cyan-100",
        green: "bg-green-50 text-green-700 ring-green-100",
    };
    return tones[tone] || tones.blue;
}

function Panel({ children, className = "", testid }) {
    return (
        <section
            data-testid={testid}
            className={`overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.03)] ${className}`}
        >
            {children}
        </section>
    );
}

function ProductThumb({ emoji, tone }) {
    return (
        <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl text-2xl ring-1 ${toneClasses(tone)}`} aria-hidden="true">
            {emoji}
        </div>
    );
}

function ScrollRail() {
    return (
        <div className="absolute bottom-5 left-2 top-16 hidden w-3 flex-col items-center justify-between xl:flex" aria-hidden="true">
            <span className="h-0 w-0 border-x-[6px] border-b-[8px] border-x-transparent border-b-slate-400" />
            <span className="absolute top-5 h-24 w-2 rounded-full bg-slate-400" />
            <span className="h-0 w-0 border-x-[6px] border-t-[8px] border-x-transparent border-t-slate-400" />
        </div>
    );
}

function AbandonedCartsCard() {
    return (
        <Panel className="relative" testid="preview-abandoned-carts">
            <div className="flex h-14 items-center justify-between border-b border-slate-100 px-4">
                <div className="flex items-center gap-2">
                    <ShoppingCart className="h-5 w-5 text-rose-500" />
                    <h2 className="text-base font-extrabold text-slate-700">آخر 5 سلات متروكة</h2>
                </div>
                <span className="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-bold text-rose-600">5 سلات</span>
            </div>
            <ScrollRail />
            <div className="pl-5">
                {ABANDONED_CARTS.map((cart) => (
                    <div key={`${cart.customer}-${cart.product}`} className="flex min-h-[91px] items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-b-0">
                        <ProductThumb emoji={cart.emoji} tone={cart.tone} />
                        <div className="min-w-0 flex-1">
                            <p className="line-clamp-2 text-[12px] font-bold leading-5 text-slate-800">{cart.product}</p>
                            <p className="mt-0.5 text-[11px] text-slate-500">{cart.customer}</p>
                        </div>
                        <div className="shrink-0 text-left">
                            <p className="num text-[12px] font-bold text-rose-500">SAR {money(cart.amount)}</p>
                            <p className="mt-1 text-[10px] text-slate-400">{cart.age}</p>
                        </div>
                    </div>
                ))}
            </div>
        </Panel>
    );
}

function TopProductsCard() {
    return (
        <Panel className="relative" testid="preview-top-products">
            <div className="flex h-14 items-center justify-between border-b border-slate-100 px-4">
                <div className="flex items-center gap-2">
                    <Trophy className="h-5 w-5 text-slate-500" />
                    <h2 className="text-base font-extrabold text-slate-700">المنتجات الأكثر مبيعًا</h2>
                </div>
                <span className="text-[10px] text-slate-400">حسب الفترة المحددة</span>
            </div>
            <ScrollRail />
            <div className="pl-5">
                <div className="grid grid-cols-[minmax(0,1fr)_70px_108px] gap-2 border-b border-slate-100 px-4 py-2 text-[10px] font-bold text-slate-400">
                    <span>المنتج</span><span>الوحدات</span><span>إجمالي المبيعات</span>
                </div>
                {TOP_PRODUCTS.map((item) => (
                    <div key={item.product} className="grid min-h-[71px] grid-cols-[minmax(0,1fr)_70px_108px] items-center gap-2 border-b border-slate-100 px-4 py-2 last:border-b-0">
                        <div className="flex min-w-0 items-center gap-2">
                            <ProductThumb emoji={item.emoji} tone={item.tone} />
                            <p className="line-clamp-2 text-[10px] font-bold leading-4 text-slate-700">{item.product}</p>
                        </div>
                        <span className="num text-[12px] font-bold text-slate-700">{item.units.toLocaleString("en-US")}</span>
                        <span className="num text-[11px] font-bold text-blue-600">SAR {money(item.sales)}</span>
                    </div>
                ))}
            </div>
        </Panel>
    );
}

function FiltersRow() {
    return (
        <div className="flex min-h-12 flex-wrap items-center gap-3" data-testid="preview-filters">
            <button type="button" className="order-3 inline-flex h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 shadow-sm">
                <CalendarDays className="h-4 w-4" />
                الفترة: 1 أغسطس 2026 — 13 أغسطس 2026
            </button>
            <button type="button" className="order-2 inline-flex h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 shadow-sm">
                طرق الدفع <ChevronDown className="h-4 w-4" />
            </button>
            <button type="button" className="order-1 inline-flex h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 shadow-sm">
                شركات الشحن <ChevronDown className="h-4 w-4" />
            </button>
            <button type="button" className="mr-auto inline-flex h-11 items-center gap-2 rounded-xl border border-blue-200 bg-white px-4 text-sm font-extrabold text-blue-700 shadow-sm">
                تحديث الجميع <RefreshCw className="h-4 w-4" />
            </button>
        </div>
    );
}

function MetricCard({ label, value, Icon, tone }) {
    return (
        <div dir="rtl" className="flex min-w-0 items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
            <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-[11px] font-bold leading-4 text-slate-600">{label}</p>
                <p className="num mt-1 whitespace-nowrap text-lg font-black text-slate-900">{value}</p>
            </div>
            <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 ${toneClasses(tone)}`}>
                <Icon className="h-[18px] w-[18px]" />
            </span>
        </div>
    );
}

function PreviewSummaryStrip() {
    return (
        <div dir="ltr" className="grid gap-3 min-[1180px]:grid-cols-[minmax(0,1.7fr)_minmax(280px,0.72fr)]" data-testid="preview-summary-strip">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="preview-kpi-cards">
                {KPI_CARDS.map((card) => <MetricCard key={card.label} {...card} />)}
            </div>
            <div dir="rtl" className="flex min-h-[82px] items-center justify-center gap-3 rounded-xl border border-amber-300 bg-amber-50/80 px-4 text-center text-amber-900 shadow-sm" data-testid="preview-missing-cost-alert">
                <AlertTriangle className="h-5 w-5 shrink-0 text-amber-500" />
                <p className="text-[13px] font-extrabold leading-5">
                    19 منتجًا مبيعًا بدون تكلفة ميزان
                    <span className="block text-amber-700">أضف التكلفة لاعتماد الأرباح</span>
                </p>
            </div>
        </div>
    );
}

function AdsTooltip({ active, payload, label }) {
    if (!active || !payload?.length) return null;
    return (
        <div dir="rtl" className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
            <p className="mb-1 font-extrabold text-slate-800">{label}</p>
            {payload.map((item) => (
                <p key={item.dataKey} style={{ color: item.color }}>
                    {item.name}: <span className="num font-bold">{money(item.value)} ر.س</span>
                </p>
            ))}
        </div>
    );
}

function AdsSpendCard() {
    const [granularity, setGranularity] = useState("daily");
    const data = granularity === "daily" ? DAILY_AD_DATA : MONTHLY_AD_DATA;
    const interval = granularity === "daily" ? 2 : 0;

    return (
        <Panel className="h-full border-amber-200" testid="preview-ads-chart">
            <div className="flex h-14 items-center justify-between border-b border-amber-100 bg-amber-50/60 px-4">
                <div className="flex items-center gap-2">
                    <CircleDollarSign className="h-5 w-5 text-amber-600" />
                    <h2 className="font-extrabold text-amber-800">مصروفات منصات الإعلانات</h2>
                </div>
                <div className="flex rounded-lg border border-amber-200 bg-white p-1 text-[10px] font-bold">
                    <button type="button" onClick={() => setGranularity("daily")} className={`rounded-md px-2.5 py-1 ${granularity === "daily" ? "bg-amber-500 text-white" : "text-slate-500"}`}>يومي</button>
                    <button type="button" onClick={() => setGranularity("monthly")} className={`rounded-md px-2.5 py-1 ${granularity === "monthly" ? "bg-amber-500 text-white" : "text-slate-500"}`}>شهري</button>
                </div>
            </div>
            <div className="px-3 pt-3">
                <div className="mb-1 flex items-center justify-between px-2 text-[10px] text-slate-500">
                    <span>{granularity === "daily" ? "التجميع اليومي للفترة المحددة" : "التجميع الشهري للفترة المحددة"}</span>
                    <span className="rounded-full bg-amber-50 px-2 py-1 font-bold text-amber-700">بيانات معاينة</span>
                </div>
                <div className="h-[185px]" dir="ltr">
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={data} margin={{ top: 10, right: 8, left: -18, bottom: 0 }}>
                            <CartesianGrid vertical={false} stroke="#e2e8f0" strokeDasharray="4 4" />
                            <XAxis dataKey="label" interval={interval} tick={{ fontSize: 9, fill: "#64748b" }} axisLine={false} tickLine={false} />
                            <YAxis tick={{ fontSize: 9, fill: "#64748b" }} axisLine={false} tickLine={false} tickFormatter={(value) => `${Math.round(value / 1000)}K`} />
                            <Tooltip content={<AdsTooltip />} />
                            {AD_PLATFORMS.map((platform) => (
                                <Line key={platform.key} type="monotone" dataKey={platform.key} name={platform.label} stroke={platform.color} strokeWidth={2} dot={{ r: 2, fill: platform.color }} activeDot={{ r: 4 }} />
                            ))}
                        </LineChart>
                    </ResponsiveContainer>
                </div>
                <div className="grid grid-cols-4 gap-1.5">
                    {AD_PLATFORMS.map((platform) => (
                        <div key={platform.key} className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-center">
                            <div className="flex items-center justify-center gap-1 text-[9px] font-bold text-slate-600">
                                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: platform.color }} />
                                {platform.label}
                            </div>
                            <p className="num mt-1 text-[11px] font-black text-slate-800">{money(platform.value)} ر.س</p>
                        </div>
                    ))}
                </div>
            </div>
            <div className="mt-3 flex h-11 items-center justify-between border-t border-amber-100 bg-amber-50/70 px-4">
                <span className="text-sm font-extrabold text-slate-700">إجمالي المصروفات</span>
                <span className="num text-lg font-black text-slate-900">22,740.00 ر.س</span>
            </div>
        </Panel>
    );
}

const PROFIT_ROWS = [
    { label: PROFIT_SUMMARY_LABELS[0], value: PREVIEW_TOTALS.sales, Icon: CircleDollarSign, color: "emerald", isSales: true },
    { label: PROFIT_SUMMARY_LABELS[1], value: PREVIEW_TOTALS.productCost, Icon: PackageOpen, color: "amber" },
    { label: PROFIT_SUMMARY_LABELS[2], value: PREVIEW_TOTALS.adsCost, Icon: Megaphone, color: "rose", expandable: true },
    { label: PROFIT_SUMMARY_LABELS[3], value: PREVIEW_TOTALS.shippingCost, Icon: Truck, color: "sky", expandable: true },
    { label: PROFIT_SUMMARY_LABELS[4], value: PREVIEW_TOTALS.paymentFees, Icon: CreditCard, color: "violet", expandable: true },
    { label: PROFIT_SUMMARY_LABELS[5], value: PREVIEW_TOTALS.operatingExpenses, Icon: BriefcaseBusiness, color: "orange", expandable: true },
];

const PROFIT_TONES = {
    emerald: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    rose: "bg-rose-50 text-rose-600",
    sky: "bg-sky-50 text-sky-700",
    violet: "bg-violet-50 text-violet-700",
    orange: "bg-orange-50 text-orange-700",
};

function ProfitSummaryCardPreview() {
    const netProfit = PREVIEW_TOTALS.sales
        - PREVIEW_TOTALS.productCost
        - PREVIEW_TOTALS.adsCost
        - PREVIEW_TOTALS.shippingCost
        - PREVIEW_TOTALS.paymentFees
        - PREVIEW_TOTALS.operatingExpenses;

    return (
        <Panel className="h-full border-emerald-200" testid="preview-profit-summary">
            <div className="flex h-14 items-center justify-between border-b border-emerald-100 bg-emerald-50/60 px-4">
                <div className="flex items-center gap-2">
                    <TrendingUp className="h-5 w-5 text-emerald-600" />
                    <h2 className="font-extrabold text-emerald-800">الملخص التنفيذي للأرباح</h2>
                </div>
                <span className="rounded-full border border-emerald-200 bg-white px-2 py-1 text-[9px] font-bold text-emerald-700">كل المعلومات محفوظة</span>
            </div>
            <div className="px-5 py-3">
                {PROFIT_ROWS.map((row) => (
                    <div dir="ltr" key={row.label} className="grid min-h-[54px] grid-cols-[minmax(180px,0.72fr)_minmax(0,1.28fr)_40px] items-center gap-4 border-b border-slate-100 py-2 last:border-b-0">
                        <div className="flex min-w-0 items-center gap-2 text-left">
                            {row.expandable && <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />}
                            <div className="min-w-0">
                                <span className={`num block whitespace-nowrap text-lg font-black ${row.isSales ? "text-emerald-700" : "text-slate-900"}`}>{money(row.value)} ر.س</span>
                                {!row.isSales && <span className={`mt-1 inline-flex rounded-md px-2 py-0.5 text-[10px] font-black ${PROFIT_TONES[row.color]}`}>{percentage(row.value)}</span>}
                            </div>
                        </div>
                        <p dir="rtl" className="text-right text-[13px] font-extrabold leading-5 text-slate-700">{row.label}</p>
                        <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${PROFIT_TONES[row.color]}`}><row.Icon className="h-5 w-5" /></span>
                    </div>
                ))}
            </div>
            <div dir="ltr" className="mx-5 mb-4 flex min-h-[70px] items-center justify-between rounded-xl bg-emerald-600 px-5 text-white shadow-md shadow-emerald-100">
                <div className="text-left">
                    <p className="num whitespace-nowrap text-2xl font-black">{money(netProfit)} ر.س</p>
                    <span className="rounded-md bg-white/15 px-2 py-0.5 text-[10px] font-bold">{percentage(netProfit)}</span>
                </div>
                <div className="flex items-center gap-2">
                    <div dir="rtl" className="text-right">
                        <p className="text-lg font-black">صافي الأرباح</p>
                        <p className="text-[10px] text-emerald-100">بعد جميع التكاليف والمصروفات</p>
                    </div>
                    <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-white/15"><TrendingUp className="h-5 w-5" /></span>
                </div>
            </div>
        </Panel>
    );
}

function LatestOrdersCard() {
    return (
        <Panel testid="preview-latest-orders">
            <div className="flex h-14 items-center justify-between border-b border-slate-100 px-4">
                <div className="flex items-center gap-2">
                    <ShoppingBag className="h-5 w-5 text-slate-500" />
                    <h2 className="text-base font-extrabold text-slate-700">أحدث الطلبات</h2>
                </div>
                <span className="text-[10px] text-slate-400">آخر الطلبات حسب وقت الإنشاء</span>
            </div>
            <div>
                {ORDER_ROWS.map((order) => (
                    <div key={order.number} dir="ltr" className="grid min-h-[58px] grid-cols-[94px_122px_minmax(0,1fr)] items-center gap-4 border-b border-slate-100 px-4 py-2 last:border-b-0">
                        <span className="text-[11px] text-slate-400">{order.time}</span>
                        <span className="num text-sm font-extrabold text-teal-700">SAR {money(order.amount)}</span>
                        <div dir="rtl" className="flex min-w-0 items-center gap-3">
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xl ring-1 ring-slate-200">{order.avatar}</div>
                            <div className="min-w-0 flex-1">
                                <div className="flex min-w-0 items-center gap-2">
                                    <p className="truncate text-[12px] font-extrabold text-slate-800">{order.name}</p>
                                    {order.fresh && <span className="shrink-0 rounded-full border border-rose-300 bg-rose-50 px-2 py-0.5 text-[9px] font-bold text-rose-500">جديد</span>}
                                </div>
                                <div className="mt-1 flex min-w-0 items-center gap-1.5 text-[10px] text-slate-400">
                                    <span className="num">#{order.number}</span>
                                    <MapPin className="h-3 w-3" />
                                    <span>{order.city}</span>
                                    <span className="h-1.5 w-1.5 rounded-full bg-slate-600" />
                                    <span className="truncate">{order.status}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </Panel>
    );
}

function GoogleAnalyticsCard() {
    const maxViews = Math.max(...TOP_PAGES.map((page) => page.views));
    return (
        <Panel className="border-blue-200" testid="preview-ga4-live">
            <div className="flex h-14 items-center justify-between border-b border-blue-100 px-4">
                <div className="flex items-center gap-2">
                    <BarChart3 className="h-5 w-5 text-blue-600" />
                    <h2 className="text-sm font-black text-slate-800">Google Analytics 4 — مباشر</h2>
                </div>
                <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-1 text-[8px] font-bold text-blue-600">Property 358605193</span>
            </div>
            <div className="p-3">
                <div className="mb-3 flex items-center gap-2">
                    <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><BarChart3 className="h-5 w-5" /></span>
                    <div>
                        <h3 className="text-sm font-extrabold text-slate-800">الصفحات الأكثر مشاهدة</h3>
                        <p className="text-[10px] text-slate-400">حسب عنوان الصفحة — آخر 30 دقيقة</p>
                    </div>
                </div>
                <div className="space-y-3">
                    {TOP_PAGES.map((page) => (
                        <div key={page.title}>
                            <div className="flex items-center justify-between gap-2 text-[10px]">
                                <span className="truncate text-slate-600">{page.title}</span>
                                <span className="num font-black text-slate-800">{page.views}</span>
                            </div>
                            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
                                <div className="h-full rounded-full bg-blue-500" style={{ width: `${(page.views / maxViews) * 100}%` }} />
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </Panel>
    );
}

function ActiveUsersCard() {
    const max = Math.max(...ACTIVE_USERS);
    return (
        <Panel className="border-blue-200" testid="preview-active-users">
            <div className="flex items-center gap-2 px-4 pt-4">
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-50 text-violet-600"><UsersRound className="h-5 w-5" /></span>
                <div>
                    <h2 className="text-base font-black text-slate-800">المستخدمون النشطون الآن</h2>
                    <p className="text-[10px] text-slate-400">قياس لحظي من Google Analytics</p>
                </div>
            </div>
            <div className="grid grid-cols-2 gap-3 p-4">
                <div className="rounded-xl border border-violet-100 bg-violet-50/70 p-3 text-center">
                    <p className="text-[10px] font-bold text-violet-600">آخر 5 دقائق</p>
                    <p className="num mt-1 text-3xl font-black text-violet-950">39</p>
                </div>
                <div className="rounded-xl border border-blue-100 bg-blue-50/70 p-3 text-center">
                    <p className="text-[10px] font-bold text-blue-600">آخر 30 دقيقة</p>
                    <p className="num mt-1 text-3xl font-black text-blue-950">228</p>
                </div>
            </div>
            <div className="px-4 pb-4">
                <div dir="ltr" className="flex h-40 items-end gap-[3px] border-b border-slate-200 px-1 pb-1">
                    {ACTIVE_USERS.map((value, index) => (
                        <div key={`${index}-${value}`} className="min-w-0 flex-1 rounded-t bg-gradient-to-t from-violet-700 to-blue-500" style={{ height: `${Math.max(8, (value / max) * 100)}%` }} title={`${value} مستخدم`} />
                    ))}
                </div>
                <div dir="ltr" className="mt-1 flex justify-between text-[9px] text-slate-400"><span>-30</span><span>-20</span><span>-10</span><span>الآن</span></div>
            </div>
        </Panel>
    );
}

export default function DashboardDesignPreview() {
    return (
        <div dir="rtl" className="min-h-screen bg-[#fbfcfd] px-4 py-5 text-slate-900" data-testid="dashboard-design-preview">
            <div className="mx-auto max-w-[1560px]">
                <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <p className="text-xs font-bold text-slate-400">مرحبًا، عرفات</p>
                        <h1 className="text-3xl font-black tracking-tight text-slate-900">لوحة التحكم</h1>
                    </div>
                    <div className="flex items-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-2 text-xs font-extrabold text-violet-700" data-testid="mock-data-banner">
                        <span className="h-2 w-2 rounded-full bg-violet-500" />
                        Preview ببيانات وهمية — لا توجد أي كتابة على Production
                    </div>
                </header>

                <div dir="ltr" className="grid gap-4 min-[1280px]:grid-cols-[clamp(300px,25vw,380px)_minmax(0,1fr)]">
                    <aside dir="rtl" className="space-y-4">
                        <AbandonedCartsCard />
                        <AdsSpendCard />
                        <TopProductsCard />
                    </aside>

                    <main dir="rtl" className="min-w-0 space-y-4">
                        <FiltersRow />
                        <PreviewSummaryStrip />

                        <div dir="ltr" className="grid min-w-0 gap-4 min-[1120px]:grid-cols-[minmax(0,2fr)_minmax(300px,0.92fr)]">
                            <div dir="rtl" className="min-w-0 space-y-4">
                                <ProfitSummaryCardPreview />
                                <LatestOrdersCard />
                            </div>
                            <div dir="rtl" className="min-w-0 space-y-4">
                                <GoogleAnalyticsCard />
                                <ActiveUsersCard />
                            </div>
                        </div>
                    </main>
                </div>
            </div>
        </div>
    );
}
