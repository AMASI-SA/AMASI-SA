/**
 * iter-49 — Executive profit summary card.
 *
 * One-stop visual breakdown that walks the merchant from gross sales
 * down to net profit. Color-coded "waterfall"-style with:
 *   • Green hero  for top-line sales
 *   • Amber rows  for cost deductions
 *   • Emerald row for the final net-profit number
 *
 * Pure presentational — pulls everything from `totals` returned by
 * /api/dashboard (no extra API calls).
 */
import {
    Coins, Package, Megaphone, Truck, Receipt, TrendUp, Equals, Briefcase,
    ShoppingCart, ChartBar,
} from "@phosphor-icons/react";

const fmtSar = (v) => {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
};

/**
 * Format a cost as a percentage of total sales. Returns null when the
 * computation isn't meaningful (no sales, or the cost is zero) so the
 * caller can decide whether to render the badge at all.
 */
const sharePct = (cost, sales) => {
    const s = Number(sales || 0);
    const c = Number(cost || 0);
    if (s <= 0 || c === 0) return null;
    return (c / s) * 100;
};

const fmtPct = (p) => `${p.toFixed(2)}%`;

/** Compact integer formatter for "عدد الطلبات" (with thousands separator). */
const fmtInt = (v) => {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("en-US");
};

/** Hero KPI tile used in the new header strip (3 tiles in one row). */
function HeaderKpi({ icon: Icon, label, value, hint, tone = "emerald", testid, badge = null }) {
    const tones = {
        sky:     { border: "border-sky-100",     iconBg: "bg-sky-100 text-sky-700",         num: "text-sky-700" },
        amber:   { border: "border-amber-100",   iconBg: "bg-amber-100 text-amber-700",     num: "text-amber-700" },
        emerald: { border: "border-emerald-100", iconBg: "bg-emerald-100 text-emerald-700", num: "text-emerald-700" },
    };
    const t = tones[tone] || tones.emerald;
    return (
        <div
            className={`rounded-xl bg-white/80 border ${t.border} px-3 py-2.5 text-center relative`}
            data-testid={testid}
        >
            <div className="flex items-center justify-center gap-1.5 text-[11px] font-bold text-slate-500 mb-1">
                <span className={`w-4 h-4 inline-flex items-center justify-center rounded ${t.iconBg}`}>
                    <Icon size={11} weight="bold" />
                </span>
                <span className="truncate">{label}</span>
            </div>
            <div
                className={`num text-xl sm:text-2xl font-extrabold ${t.num} leading-tight`}
                style={{ fontFamily: "Tajawal" }}
            >
                {value}
            </div>
            {hint && (
                <div className="text-[10px] text-slate-400 mt-0.5 truncate" title={hint}>
                    {hint}
                </div>
            )}
            {badge}
        </div>
    );
}

function Line({ icon: Icon, label, value, share = null, color = "amber", isFirst = false, isLast = false }) {
    // Color palette for each row — tuned for clarity on a soft gradient bg.
    const palettes = {
        green:   { tile: "bg-emerald-600",      icon: "text-white",      amount: "text-emerald-700",   bar: "bg-emerald-200/60" },
        amber:   { tile: "bg-amber-100",        icon: "text-amber-700",  amount: "text-amber-700",     bar: "bg-amber-200/40" },
        rose:    { tile: "bg-rose-100",         icon: "text-rose-700",   amount: "text-rose-700",      bar: "bg-rose-200/40" },
        violet:  { tile: "bg-violet-100",       icon: "text-violet-700", amount: "text-violet-700",    bar: "bg-violet-200/40" },
        sky:     { tile: "bg-sky-100",          icon: "text-sky-700",    amount: "text-sky-700",       bar: "bg-sky-200/40" },
        emerald: { tile: "bg-emerald-600",      icon: "text-white",      amount: "text-emerald-700",   bar: "bg-emerald-300/40" },
    };
    const p = palettes[color] || palettes.amber;
    return (
        <div
            className={[
                "flex items-center justify-between gap-3 px-3 py-2.5 transition-colors hover:bg-white/40",
                isLast ? "rounded-b-xl" : "",
                isFirst ? "rounded-t-xl" : "border-t border-white/60",
            ].join(" ")}
        >
            <div className="flex items-center gap-2.5 min-w-0">
                <div className={`w-8 h-8 rounded-lg ${p.tile} ${p.icon} flex items-center justify-center flex-shrink-0`}>
                    <Icon size={16} weight="bold" />
                </div>
                <span className="text-sm font-bold text-slate-700 truncate">{label}</span>
            </div>
            <div className={`flex items-center gap-2 num text-base font-extrabold ${p.amount}`} style={{ fontFamily: "Tajawal" }}>
                {share != null && (
                    <span
                        className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${p.bar} ${p.amount}`}
                        title="نسبة هذه التكلفة من إجمالي المبيعات"
                        data-testid="profit-line-share"
                    >
                        {fmtPct(share)}
                    </span>
                )}
                <span>{value}</span>
            </div>
        </div>
    );
}

export default function ProfitSummaryCard({ totals }) {
    const t = totals || {};
    // Compose the deductions explicitly so each line is auditable and
    // matches the description on the KPI tooltips above.
    const sales            = Number(t.total_sales        || 0);
    const productCost      = Number(t.total_product_cost || 0);
    const adsCost          = Number(t.total_ads_cost     || 0);
    // Total shipping = both upfront (cash) AND deferred (post-paid) — the
    // merchant asked for "المقدم والآجل" together. `total_shipping_cost`
    // from the backend already includes both because it sums across all
    // shipping company configurations regardless of `deferred` flag.
    const shippingTotal    = Number(t.total_shipping_cost || 0);
    // All payment-gateway fees aggregated.
    const allPaymentFees   = (Number(t.other_payment_fees || 0)
                            + Number(t.tamara_fees        || 0)
                            + Number(t.tabby_fees         || 0)
                            + Number(t.emkan_fees         || 0)
                            + Number(t.bank_fees          || 0));
    // Operating expenses (المصروفات التشغيلية اليومية) — backend's
    // `net_profit` formula deducts this, so we MUST display it as a line
    // here too. Otherwise sales − productCost − ads − shipping − fees
    // doesn't equal the "صافي الأرباح" number and merchants reasonably
    // flag it as a bug (iter-53 fix).
    const operatingExpenses = Number(t.operating_expenses_total || 0);

    // ── iter-55 header KPIs (3 tiles in one row above the breakdown) ──
    const totalOrders     = Number(t.total_orders || 0);
    // Iter-207c — Salla-vs-Accounting transparency badge data.
    const excludedCount = Number(t.excluded_orders_count || 0);
    const excludedGross = Number(t.excluded_gross || 0);
    const sallaRefCount = Number(t.salla_reference_orders_count || 0);
    const sallaRefGross = Number(t.salla_reference_gross || 0);
    const ordersBadge = excludedCount > 0 ? (
        <div
            className="mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-amber-50 border border-amber-200 text-[9px] font-bold text-amber-800 cursor-help"
            data-testid="profit-kpi-orders-excluded-badge"
            title={
                `منصة سلة تعرض جميع الطلبات المنشأة (${fmtInt(sallaRefCount)} طلب بقيمة ${fmtSar(sallaRefGross)} ر.س).\n\n` +
                `النظام المحاسبي يعتمد فقط الطلبات الداخلة في التقارير المالية (${fmtInt(totalOrders)} طلب بقيمة ${fmtSar(sales)} ر.س).\n\n` +
                `الفرق:\n${fmtInt(excludedCount)} طلب معلَّق أو ملغى بقيمة ${fmtSar(excludedGross)} ر.س.`
            }
        >
            +{fmtInt(excludedCount)} معلَّق/ملغى ({fmtSar(excludedGross)} ر.س)
        </div>
    ) : null;
    // `avg_cost_per_order` is null when ads=0 or orders=0 (backend convention).
    const avgCostPerOrder = t.avg_cost_per_order != null
        ? Number(t.avg_cost_per_order)
        : null;
    // `overall_roas` is null when ads=0 (backend convention).
    const roas            = t.overall_roas != null
        ? Number(t.overall_roas)
        : null;

    // Use the authoritative net_profit from backend; fall back to manual
    // calc only when the backend hasn't surfaced it yet (e.g., empty store).
    const netProfit = t.net_profit != null
        ? Number(t.net_profit)
        : sales - productCost - adsCost - shippingTotal - allPaymentFees - operatingExpenses;

    return (
        <div
            className="rounded-2xl bg-gradient-to-br from-emerald-50 via-white to-amber-50 border-2 border-emerald-200/60 shadow-sm overflow-hidden"
            data-testid="profit-summary-card"
        >
            {/* Title strip */}
            <div className="px-4 py-3 bg-gradient-to-l from-emerald-600 to-emerald-700 text-white flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <TrendUp size={20} weight="bold" />
                    <h3 className="font-extrabold text-base sm:text-lg" style={{ fontFamily: "Tajawal" }}>
                        الملخص التنفيذي للأرباح
                    </h3>
                </div>
                <div className="text-[11px] text-emerald-100/90 hidden sm:block">
                    تقرير مختصر للفترة المحددة
                </div>
            </div>

            {/* iter-55: Header KPI strip — 3 tiles in one row.
                Sits between the title bar and the breakdown so merchants get
                an at-a-glance read of efficiency (orders / cost-per-order /
                ROAS) before the waterfall begins. */}
            <div className="grid grid-cols-3 gap-2 px-3 pt-3" data-testid="profit-header-kpis">
                <HeaderKpi
                    icon={ShoppingCart}
                    label="عدد الطلبات"
                    value={fmtInt(totalOrders)}
                    hint="إجمالي طلبات الفترة"
                    tone="sky"
                    testid="profit-kpi-orders"
                    badge={ordersBadge}
                />
                <HeaderKpi
                    icon={Coins}
                    label="متوسط تكلفة الطلب"
                    value={avgCostPerOrder != null ? fmtSar(avgCostPerOrder) : "—"}
                    hint="ر.س / طلب (إعلانات)"
                    tone="amber"
                    testid="profit-kpi-avg-cost"
                />
                <HeaderKpi
                    icon={ChartBar}
                    label="العائد على الإعلانات"
                    value={roas != null ? `${roas.toFixed(2)}×` : "—"}
                    hint="ROAS = المبيعات ÷ الإعلانات"
                    tone="emerald"
                    testid="profit-kpi-roas"
                />
            </div>

            {/* Subtle dashed divider so the KPI strip feels grouped */}
            <div className="mx-3 mt-3 mb-1 border-t border-dashed border-emerald-200/70"></div>

            {/* Body */}
            <div className="p-3 space-y-0">
                <Line icon={Coins}      label="المبيعات"                      value={fmtSar(sales)}          color="green"  isFirst />
                <Line icon={Package}    label="− تكاليف المنتجات"             value={fmtSar(productCost)}    share={sharePct(productCost, sales)}     color="amber"  />
                <Line icon={Megaphone}  label="− إجمالي تكاليف الإعلانات"      value={fmtSar(adsCost)}        share={sharePct(adsCost, sales)}         color="rose"   />
                <Line icon={Truck}      label="− إجمالي تكاليف الشحن (مقدم + آجل)" value={fmtSar(shippingTotal)}  share={sharePct(shippingTotal, sales)}   color="sky"    />
                <Line icon={Receipt}    label="− إجمالي رسوم جميع طرق الدفع"    value={fmtSar(allPaymentFees)} share={sharePct(allPaymentFees, sales)}  color="violet" />
                {operatingExpenses > 0 && (
                    <Line icon={Briefcase} label="− المصروفات التشغيلية (رواتب وإيجارات وغيرها)" value={fmtSar(operatingExpenses)} share={sharePct(operatingExpenses, sales)} color="amber" />
                )}

                {/* Net profit row — visually distinguished */}
                <div className="mt-1 mx-2 mb-2 rounded-xl bg-emerald-600 text-white shadow-md">
                    <div className="flex items-center justify-between gap-3 px-3 py-3">
                        <div className="flex items-center gap-2">
                            <div className="w-9 h-9 rounded-lg bg-white/20 flex items-center justify-center">
                                <Equals size={18} weight="bold" />
                            </div>
                            <span className="text-sm sm:text-base font-extrabold">صافي الأرباح</span>
                        </div>
                        <div
                            className="flex items-center gap-2 num text-xl sm:text-2xl font-extrabold"
                            style={{ fontFamily: "Tajawal" }}
                        >
                            {sales > 0 && (
                                <span
                                    className="text-xs font-bold px-2 py-0.5 rounded bg-white/20 text-white"
                                    title="هامش الربح الصافي = صافي الأرباح ÷ المبيعات"
                                    data-testid="profit-summary-margin"
                                >
                                    {fmtPct((netProfit / sales) * 100)}
                                </span>
                            )}
                            <span data-testid="profit-summary-net">
                                {fmtSar(netProfit)} ر.س
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
