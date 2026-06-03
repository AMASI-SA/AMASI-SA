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
} from "@phosphor-icons/react";

const fmtSar = (v) => {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
};

function Line({ icon: Icon, label, value, color = "amber", isFirst = false, isLast = false }) {
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
            <div className={`num text-base font-extrabold ${p.amount}`} style={{ fontFamily: "Tajawal" }}>
                {value}
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

            {/* Body */}
            <div className="p-3 space-y-0">
                <Line icon={Coins}      label="المبيعات"                      value={fmtSar(sales)}          color="green"  isFirst />
                <Line icon={Package}    label="− تكاليف المنتجات"             value={fmtSar(productCost)}    color="amber"  />
                <Line icon={Megaphone}  label="− إجمالي تكاليف الإعلانات"      value={fmtSar(adsCost)}        color="rose"   />
                <Line icon={Truck}      label="− إجمالي تكاليف الشحن (مقدم + آجل)" value={fmtSar(shippingTotal)}  color="sky"    />
                <Line icon={Receipt}    label="− إجمالي رسوم جميع طرق الدفع"    value={fmtSar(allPaymentFees)} color="violet" />
                {operatingExpenses > 0 && (
                    <Line icon={Briefcase} label="− المصروفات التشغيلية (رواتب وإيجارات وغيرها)" value={fmtSar(operatingExpenses)} color="amber" />
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
                            className="num text-xl sm:text-2xl font-extrabold"
                            style={{ fontFamily: "Tajawal" }}
                            data-testid="profit-summary-net"
                        >
                            {fmtSar(netProfit)} ر.س
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
