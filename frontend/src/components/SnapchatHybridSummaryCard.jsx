import {
    ChartLineUp,
    CheckCircle,
    Coins,
    CurrencyDollar,
    Ghost,
    Info,
    ShoppingBag,
    Warning,
} from "@phosphor-icons/react";


function money(value) {
    return Number(value || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}


function integer(value) {
    return Number(value || 0).toLocaleString("en-US", {
        maximumFractionDigits: 0,
    });
}


function ratio(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return `${Number(value).toFixed(2)}×`;
}


function Metric({ label, value, hint, tone = "slate", testid, icon: Icon }) {
    const tones = {
        violet: "border-violet-200 bg-violet-50/70 text-violet-950",
        sky: "border-sky-200 bg-sky-50/70 text-sky-950",
        emerald: "border-emerald-200 bg-emerald-50/70 text-emerald-950",
        amber: "border-amber-200 bg-amber-50/70 text-amber-950",
        slate: "border-slate-200 bg-white text-slate-950",
    };
    return (
        <div className={`rounded-xl border p-3 sm:p-4 ${tones[tone] || tones.slate}`} data-testid={testid}>
            <div className="flex items-center gap-1.5 text-xs font-extrabold opacity-70">
                {Icon && <Icon size={14} weight="bold" />}
                {label}
            </div>
            <div className="num mt-2 text-xl font-extrabold sm:text-2xl">{value}</div>
            {hint && <div className="mt-1 text-[10px] font-semibold opacity-65">{hint}</div>}
        </div>
    );
}


function AttributionPanel({ period, periodKey }) {
    const attributedOrders = Number(period?.attributed_orders || 0);
    const attributedRevenue = Number(period?.attributed_revenue || 0);
    const gap = Number(period?.attribution_gap_orders || 0);
    const comparisonPct = period?.attribution_coverage_pct;
    return (
        <div className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-3 sm:p-4" data-testid={`snap-hybrid-${periodKey}-attribution`}>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <div className="text-xs font-extrabold text-indigo-900">مقارنة إسناد Snapchat</div>
                    <div className="mt-0.5 text-[10px] font-semibold text-indigo-700">
                        أرقام المنصة للمقارنة فقط، وليست الطلبات التشغيلية الفعلية.
                    </div>
                </div>
                {comparisonPct != null && (
                    <span className="rounded-full border border-indigo-200 bg-white px-2.5 py-1 text-[10px] font-extrabold text-indigo-800">
                        تحويلات سناب مقابل طلبات سلة: {Number(comparisonPct).toFixed(2)}%
                    </span>
                )}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-5">
                <div className="rounded-lg bg-white p-2.5">
                    <div className="text-[10px] font-bold text-slate-500">تحويلات سناب المنسوبة</div>
                    <div className="num mt-1 text-lg font-extrabold text-indigo-950" data-testid={`snap-hybrid-${periodKey}-attributed-orders`}>
                        {integer(attributedOrders)}
                    </div>
                </div>
                <div className="rounded-lg bg-white p-2.5">
                    <div className="text-[10px] font-bold text-slate-500">مبيعات سناب المنسوبة</div>
                    <div className="num mt-1 text-lg font-extrabold text-indigo-950" data-testid={`snap-hybrid-${periodKey}-attributed-revenue`}>
                        {money(attributedRevenue)} ر.س
                    </div>
                </div>
                <div className="rounded-lg bg-white p-2.5">
                    <div className="text-[10px] font-bold text-slate-500">ROAS المنسوب</div>
                    <div className="num mt-1 text-lg font-extrabold text-indigo-950">
                        {ratio(period?.attributed_roas)}
                    </div>
                </div>
                <div className="rounded-lg bg-white p-2.5">
                    <div className="text-[10px] font-bold text-slate-500">CPA المنسوب</div>
                    <div className="num mt-1 text-lg font-extrabold text-indigo-950">
                        {period?.attributed_cpa == null ? "—" : `${money(period.attributed_cpa)} ر.س`}
                    </div>
                </div>
                <div className="rounded-lg bg-white p-2.5">
                    <div className="text-[10px] font-bold text-slate-500">فجوة الإسناد</div>
                    <div className={`num mt-1 text-lg font-extrabold ${gap >= 0 ? "text-amber-800" : "text-rose-700"}`} data-testid={`snap-hybrid-${periodKey}-gap`}>
                        {gap > 0 ? "+" : ""}{integer(gap)} طلب
                    </div>
                </div>
            </div>
        </div>
    );
}


function PeriodSection({ title, period, periodKey }) {
    if (!period) return null;
    const actualOrders = Number(period.actual_orders ?? period.orders ?? 0);
    const actualRevenue = Number(period.actual_revenue ?? period.revenue ?? 0);
    const active = Number(period.active_orders || 0);
    const cancelled = Number(period.cancelled_orders || 0);
    const refunded = Number(period.refunded_orders || 0);
    return (
        <section className="space-y-3" data-testid={`snap-hybrid-${periodKey}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-extrabold text-slate-800 sm:text-base">{title}</h3>
                <div className="flex flex-wrap gap-1.5 text-[10px] font-extrabold">
                    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-emerald-800">
                        <CheckCircle size={12} weight="fill" /> فعالة: {integer(active)}
                    </span>
                    <span className="rounded-full border border-rose-200 bg-rose-50 px-2.5 py-1 text-rose-800">ملغية: {integer(cancelled)}</span>
                    <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-amber-800">مسترجعة: {integer(refunded)}</span>
                </div>
            </div>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-5 lg:gap-3">
                <Metric
                    label="الصرف"
                    value={`${money(period.spend)} ر.س`}
                    hint="من Snapchat Ads API"
                    tone="violet"
                    icon={Coins}
                    testid={`snap-hybrid-${periodKey}-spend`}
                />
                <Metric
                    label="الطلبات الفعلية"
                    value={integer(actualOrders)}
                    hint="مصدر الطلب المسجل في سلة: Snapchat"
                    tone="sky"
                    icon={ShoppingBag}
                    testid={`snap-hybrid-${periodKey}-orders`}
                />
                <Metric
                    label="المبيعات الفعلية"
                    value={`${money(actualRevenue)} ر.س`}
                    hint="إجمالي قيمة طلبات سلة من سناب"
                    tone="emerald"
                    icon={CurrencyDollar}
                    testid={`snap-hybrid-${periodKey}-revenue`}
                />
                <Metric
                    label="ROAS الفعلي"
                    value={ratio(period.roas)}
                    hint="مبيعات سلة ÷ صرف سناب"
                    tone="amber"
                    icon={ChartLineUp}
                    testid={`snap-hybrid-${periodKey}-roas`}
                />
                <Metric
                    label="CPA الفعلي"
                    value={period.cost_per_order == null ? "—" : `${money(period.cost_per_order)} ر.س`}
                    hint="صرف سناب ÷ طلبات سلة"
                    tone="amber"
                    testid={`snap-hybrid-${periodKey}-cpa`}
                />
            </div>
            <AttributionPanel period={period} periodKey={periodKey} />
        </section>
    );
}


function processedTimeLabel(value) {
    if (!value) return null;
    try {
        return new Date(value).toLocaleString("ar-SA", {
            timeZone: "Asia/Riyadh",
            dateStyle: "short",
            timeStyle: "short",
        });
    } catch {
        return value;
    }
}


export default function SnapchatHybridSummaryCard({ data }) {
    if (!data?.today) return null;
    const freshness = data.conversion_freshness || {};
    const processedAt = processedTimeLabel(freshness.conversion_data_processed_end_time);
    const provisional = freshness.provisional === true || data.today.conversion_data_provisional === true;
    return (
        <article
            className="rounded-2xl border-2 border-yellow-300 bg-gradient-to-br from-yellow-50 via-white to-amber-50/40 p-4 shadow-sm sm:p-6"
            data-testid="snapchat-hybrid-summary-card"
        >
            <header className="flex flex-col gap-3 border-b border-yellow-200 pb-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex items-center gap-3">
                    <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-yellow-400 text-black">
                        <Ghost size={27} weight="fill" />
                    </span>
                    <div>
                        <h2 className="text-xl font-extrabold text-slate-950 sm:text-2xl">Snapchat — الأداء الفعلي</h2>
                        <p className="mt-1 text-xs font-semibold text-slate-600">
                            الطلبات والمبيعات من سلة · الصرف من Snapchat Ads · بتوقيت الرياض
                        </p>
                    </div>
                </div>
                <div className="flex flex-wrap gap-2 text-[10px] font-extrabold">
                    <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-emerald-800">المؤشرات التشغيلية: سلة</span>
                    <span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-violet-800">الصرف: Snapchat API</span>
                </div>
            </header>

            {provisional && (
                <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs font-semibold text-amber-900" data-testid="snapchat-hybrid-provisional-warning">
                    <Warning size={17} weight="fill" className="mt-0.5 shrink-0" />
                    <span>
                        تحويلات Snapchat المنسوبة ما زالت مؤقتة
                        {processedAt ? `؛ آخر وقت مكتمل للمعالجة: ${processedAt} بتوقيت الرياض.` : "."}
                        {" "}الطلبات الفعلية من سلة لا تتأثر بهذا التأخير.
                    </span>
                </div>
            )}

            <div className="mt-5 space-y-6">
                <PeriodSection
                    title={`اليوم (${data.today.date || "—"})`}
                    period={data.today}
                    periodKey="today"
                />
                <div className="border-t border-yellow-200 pt-5">
                    <PeriodSection
                        title={`هذا الشهر (منذ ${data.month?.start || "—"})`}
                        period={data.month}
                        periodKey="month"
                    />
                </div>
            </div>

            <footer className="mt-5 flex items-start gap-2 border-t border-yellow-200 pt-3 text-[10px] font-semibold text-slate-600">
                <Info size={13} weight="bold" className="mt-0.5 shrink-0 text-slate-500" />
                <span>
                    لا يتم توزيع طلبات سلة على الحسابات الإعلانية المنفصلة دون دليل Campaign/Click ID. لذلك تعرض هذه البطاقة الإجمالي الفعلي، وتبقى بطاقات الحسابات أدناه لبيانات كل حساب من Snapchat فقط.
                </span>
            </footer>
        </article>
    );
}
