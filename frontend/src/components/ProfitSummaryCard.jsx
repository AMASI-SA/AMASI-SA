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
    ShoppingCart, ChartBar, CaretLeft, CaretRight,
} from "@phosphor-icons/react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { buildMezanProductCostHref } from "../lib/mezanV2CostLinks";
import ExcludedOrdersModal from "./ExcludedOrdersModal";
import AdsCostBreakdownModal from "./AdsCostBreakdownModal";
import AdsExecutiveBreakdownTable from "./AdsExecutiveBreakdownTable";
import DailyProductCostModal from "./DailyProductCostModal";

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

const optionalNumber = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

export function buildPaymentFeeRows(paymentBreakdown = []) {
    const output = [];
    (Array.isArray(paymentBreakdown) ? paymentBreakdown : []).forEach((group, groupIndex) => {
        const children = Array.isArray(group?.sub_methods) && group.sub_methods.length
            ? group.sub_methods
            : [group];
        children.forEach((child, childIndex) => {
            const kind = child?.kind || (group?.key === "ad_bank_commissions" ? "ad_bank_commission" : "payment_method");
            output.push({
                key: child?.key || `${group?.key || groupIndex}-${childIndex}`,
                name: child?.display || child?.name || group?.name || "طريقة دفع غير معروفة",
                parentName: child !== group ? (child?.parent_name || group?.name || null) : null,
                kind,
                ordersCount: optionalNumber(child?.orders_count ?? group?.orders_count),
                baseAmount: optionalNumber(child?.total_sales ?? group?.total_sales) || 0,
                commissionPercent: optionalNumber(child?.commission_percent ?? group?.commission_percent),
                fixedFee: optionalNumber(child?.fixed_fee ?? group?.fixed_fee),
                vatPercent: optionalNumber(child?.vat_percent ?? group?.vat_percent),
                vatAmount: optionalNumber(child?.vat_amount),
                feeAmount: optionalNumber(child?.fee_amount ?? group?.fee_amount) || 0,
                nativeCurrency: child?.native_currency || null,
                exchangeRateToSar: optionalNumber(child?.exchange_rate_to_sar),
                spendNative: optionalNumber(child?.spend_native),
                applyBankCommission: child?.apply_bank_commission !== false,
                configured: child?.configured === true,
            });
        });
    });
    return output;
}

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

function Line({ icon: Icon, label, value, share = null, color = "amber", isFirst = false, isLast = false, onClick = null, testid = null, tooltip = null, expanded = false, expandable = false }) {
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
    const interactive = typeof onClick === "function";
    const Comp = interactive ? "button" : "div";
    return (
        <div className="relative">
        <Comp
            type={interactive ? "button" : undefined}
            onClick={interactive ? onClick : undefined}
            className={[
                "w-full text-right flex items-center justify-between gap-3 px-3 py-2.5 transition-colors hover:bg-white/40",
                interactive ? "cursor-pointer focus:outline-none focus:ring-2 focus:ring-rose-300 rounded-lg" : "",
                isLast ? "rounded-b-xl" : "",
                isFirst ? "rounded-t-xl" : "border-t border-white/60",
            ].join(" ")}
            data-testid={testid || undefined}
            aria-expanded={expandable ? expanded : undefined}
            title={interactive && !expandable ? "اضغط لعرض القيود التفصيلية للفترة" : undefined}
        >
            <div className="flex items-center gap-2.5 min-w-0">
                <div className={`w-8 h-8 rounded-lg ${p.tile} ${p.icon} flex items-center justify-center flex-shrink-0`}>
                    <Icon size={16} weight="bold" />
                </div>
                <span className="text-sm font-bold text-slate-700 truncate">
                    {label}
                    {interactive && !expandable && (
                        <span className="ms-1 text-[10px] text-rose-600 font-bold">
                            🔍
                        </span>
                    )}
                    {expandable && (
                        <span
                            className={`ms-1 inline-block text-[10px] text-slate-500 font-bold transition-transform ${expanded ? "rotate-180" : ""}`}
                            aria-hidden="true"
                        >
                            ▾
                        </span>
                    )}
                </span>
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
        </Comp>
        {/* iter-256 — Inline expandable accordion section. Stays
            within the card flow so the rest of the summary remains
            visible and the user doesn't lose context. */}
        {expandable && expanded && tooltip && (
            <div
                className="mx-2 mb-2 rounded-xl bg-white/95 border border-slate-200 shadow-sm p-3"
                data-testid={`${testid || "line"}-accordion`}
            >
                {tooltip}
            </div>
        )}
        </div>
    );
}

export default function ProfitSummaryCard({
    totals,
    shippingBreakdown = [],
    paymentBreakdown = [],
    fromDate,
    toDate,
    periodLabel,
    productCostBreakdown = null,
    adsBreakdownEndpoint = "/dashboard/ads-cost-breakdown",
    adsExecutiveBreakdown = null,
}) {
    const t = totals || {};
    const [excludedOpen, setExcludedOpen] = useState(false);
    const [adsBreakdownOpen, setAdsBreakdownOpen] = useState(false);
    const [adsExpanded, setAdsExpanded] = useState(false);
    // iter-250b · Dashboard hover/click enhancements.
    const [productCostOpen, setProductCostOpen] = useState(false);
    // iter-256 · Inline accordion expanders (replaces hover tooltips so
    // the user reads details without losing the rest of the summary).
    const [shippingExpanded, setShippingExpanded] = useState(false);
    const [paymentFeesExpanded, setPaymentFeesExpanded] = useState(false);
    const [opExpanded, setOpExpanded] = useState(false);
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
    const calculatedPaymentFees = (Number(t.other_payment_fees || 0)
                            + Number(t.tamara_fees        || 0)
                            + Number(t.tabby_fees         || 0)
                            + Number(t.emkan_fees         || 0)
                            + Number(t.bank_fees          || 0)
                            + Number(t.ad_bank_commission_fees || 0));
    const allPaymentFees = t.total_payment_fees != null
        ? Number(t.total_payment_fees)
        : calculatedPaymentFees;
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
        <button
            type="button"
            onClick={() => setExcludedOpen(true)}
            className="mt-1 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-amber-50 border border-amber-200 text-[9px] font-bold text-amber-800 cursor-pointer hover:bg-amber-100 hover:border-amber-300 transition-colors"
            data-testid="profit-kpi-orders-excluded-badge"
            title={
                `منصة سلة تعرض جميع الطلبات المنشأة (${fmtInt(sallaRefCount)} طلب بقيمة ${fmtSar(sallaRefGross)} ر.س).\n\n` +
                `النظام المحاسبي يعتمد فقط الطلبات الداخلة في التقارير المالية (${fmtInt(totalOrders)} طلب بقيمة ${fmtSar(sales)} ر.س).\n\n` +
                `الفرق:\n${fmtInt(excludedCount)} طلب معلَّق أو ملغى بقيمة ${fmtSar(excludedGross)} ر.س.\n\n` +
                `🔍 اضغط لعرض تفاصيل الطلبات المستثناة`
            }
        >
            +{fmtInt(excludedCount)} معلَّق/ملغى ({fmtSar(excludedGross)} ر.س)
        </button>
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

    // ── iter-250b · Dashboard tooltip data ─────────────────────────
    // 1) Shipping companies breakdown — comes from /api/dashboard's
    //    `shipping_breakdown` array (name, orders_count, cost_per_order,
    //    total_cost, is_deferred).
    const shippingRows = Array.isArray(shippingBreakdown)
        ? shippingBreakdown.filter(r => Number(r.total_cost) > 0)
        : [];
    const shippingTooltip = (
        <div data-testid="shipping-tooltip-content" dir="rtl">
            <div className="flex items-center justify-between mb-2 pb-2 border-b border-slate-200">
                <span className="text-xs font-extrabold text-sky-900">
                    🚚 تفاصيل تكاليف الشحن (لكل شركة)
                </span>
                <span className="text-[10px] text-slate-500">
                    {shippingRows.length} شركة · المصدر: shipping_cost_ssot
                </span>
            </div>
            {shippingRows.length === 0 ? (
                <div className="text-xs text-slate-500 py-2 text-center">
                    لا توجد بيانات شحن في هذه الفترة
                </div>
            ) : (
                <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                    <thead className="bg-slate-50 text-slate-700">
                        <tr>
                            <th className="text-right p-1.5 font-extrabold">الشركة</th>
                            <th className="text-center p-1.5 font-extrabold">الشحنات</th>
                            <th className="text-left p-1.5 font-extrabold">سعر الوحدة<br/><span className="text-[9px] text-slate-400">(بدون الضريبة)</span></th>
                            <th className="text-left p-1.5 font-extrabold">ضريبة الوحدة<br/><span className="text-[9px] text-slate-400">(VAT)</span></th>
                            <th className="text-left p-1.5 font-extrabold">إجمالي الوحدة<br/><span className="text-[9px] text-slate-400">(سعر + ضريبة)</span></th>
                            <th className="text-left p-1.5 font-extrabold">الإجمالي</th>
                        </tr>
                    </thead>
                    <tbody>
                        {shippingRows.map((r, i) => {
                            const oc = Number(r.orders_count) || 0;
                            // Prefer SSOT-canonical per-unit fields when
                            // present; fall back to recomputing from
                            // legacy (cost_per_order / vat_amount).
                            const baseUnit = r.cost_per_unit != null
                                ? Number(r.cost_per_unit)
                                : Number(r.cost_per_order) || 0;
                            const taxUnit = r.tax_per_unit != null
                                ? Number(r.tax_per_unit)
                                : (oc > 0
                                    ? Number((Number(r.vat_amount) || 0) / oc)
                                    : 0);
                            const totalUnit = r.total_per_unit != null
                                ? Number(r.total_per_unit)
                                : baseUnit + taxUnit;
                            const vatPct = r.vat_rate != null
                                ? Number(r.vat_rate) * 100
                                : Number(r.vat_percent) || 0;
                            return (
                                <tr key={i} className="border-t border-slate-100">
                                    <td className="p-1.5 font-bold text-slate-700">
                                        {r.name}
                                        {r.is_deferred && (
                                            <span className="ms-1 text-[9px] px-1 py-0.5 rounded bg-amber-100 text-amber-700">
                                                آجل
                                            </span>
                                        )}
                                    </td>
                                    <td className="p-1.5 text-center font-mono font-bold text-slate-700">
                                        {fmtInt(r.orders_count)}
                                    </td>
                                    <td className="p-1.5 text-left font-mono text-slate-600">
                                        {fmtSar(baseUnit)}
                                    </td>
                                    <td className="p-1.5 text-left font-mono text-violet-700">
                                        {fmtSar(taxUnit)}
                                        {vatPct > 0 && (
                                            <span className="ms-1 text-[9px] text-slate-400">
                                                ({vatPct.toFixed(0)}%)
                                            </span>
                                        )}
                                    </td>
                                    <td className="p-1.5 text-left font-mono font-bold text-emerald-700">
                                        {fmtSar(totalUnit)}
                                    </td>
                                    <td className="p-1.5 text-left font-mono font-extrabold text-sky-700">
                                        {fmtSar(r.total_cost)}
                                    </td>
                                </tr>
                            );
                        })}
                        <tr className="border-t-2 border-sky-200 bg-sky-50/60">
                            <td colSpan={5} className="p-1.5 font-extrabold text-slate-800">
                                الإجمالي
                            </td>
                            <td className="p-1.5 text-left font-mono font-extrabold text-sky-800">
                                {fmtSar(shippingTotal)}
                            </td>
                        </tr>
                    </tbody>
                </table>
                </div>
            )}
            <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">
                القاعدة الموحدة: <strong>إجمالي تكلفة الشحنة = سعر الشحنة + ضريبة الشحنة</strong>.
                نفس مصدر دفتر الشحن التفصيلي (<code>shipping_cost_ssot.py</code>).
            </p>
        </div>
    );


    const paymentFeeRows = buildPaymentFeeRows(paymentBreakdown)
        .filter((row) => row.ordersCount > 0 || row.baseAmount > 0 || row.feeAmount > 0);
    const paymentFeesTooltip = (
        <div data-testid="payment-fees-tooltip-content" dir="rtl">
            <div className="flex items-center justify-between gap-3 mb-2 pb-2 border-b border-slate-200">
                <span className="text-xs font-extrabold text-violet-900">
                    💳 تفاصيل رسوم طرق الدفع والعمولات البنكية
                </span>
                <span className="text-[10px] text-slate-500">
                    {paymentFeeRows.length} طريقة / حساب
                </span>
            </div>
            {paymentFeeRows.length === 0 ? (
                <div className="text-xs text-slate-500 py-2 text-center">
                    لا توجد رسوم طرق دفع في هذه الفترة
                </div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[900px] text-[11px]" data-testid="payment-fees-breakdown-table">
                        <thead className="bg-slate-50 text-slate-700">
                            <tr>
                                <th className="text-right p-1.5 font-extrabold">طريقة الدفع / الحساب</th>
                                <th className="text-center p-1.5 font-extrabold">الطلبات</th>
                                <th className="text-left p-1.5 font-extrabold">المبلغ الخاضع</th>
                                <th className="text-left p-1.5 font-extrabold">نسبة العمولة</th>
                                <th className="text-left p-1.5 font-extrabold">رسوم ثابتة</th>
                                <th className="text-left p-1.5 font-extrabold">VAT</th>
                                <th className="text-left p-1.5 font-extrabold">إجمالي الرسوم</th>
                            </tr>
                        </thead>
                        <tbody>
                            {paymentFeeRows.map((row) => (
                                <tr key={row.key} className="border-t border-slate-100">
                                    <td className="p-1.5 font-bold text-slate-700">
                                        <div>{row.name}</div>
                                        {row.parentName && row.parentName !== row.name && (
                                            <div className="mt-0.5 text-[9px] text-slate-400">{row.parentName}</div>
                                        )}
                                        {row.kind === "ad_bank_commission" && (
                                            <div className="mt-1 flex flex-wrap items-center gap-1 text-[9px]">
                                                <span className="rounded bg-violet-50 px-1 py-0.5 text-violet-700">عمولة سحب إعلاني</span>
                                                {row.nativeCurrency && row.exchangeRateToSar != null && (
                                                    <span className="rounded bg-slate-100 px-1 py-0.5 text-slate-600">
                                                        {row.nativeCurrency} × {row.exchangeRateToSar.toFixed(4)}
                                                    </span>
                                                )}
                                                {!row.applyBankCommission && (
                                                    <span className="rounded bg-slate-100 px-1 py-0.5 text-slate-500">غير مفعلة</span>
                                                )}
                                            </div>
                                        )}
                                    </td>
                                    <td className="p-1.5 text-center font-mono font-bold text-slate-700">
                                        {row.kind === "ad_bank_commission" ? "—" : fmtInt(row.ordersCount)}
                                    </td>
                                    <td className="p-1.5 text-left font-mono text-slate-700">{fmtSar(row.baseAmount)}</td>
                                    <td className="p-1.5 text-left font-mono text-violet-700">
                                        {row.commissionPercent == null ? "—" : `${row.commissionPercent.toFixed(2)}%`}
                                    </td>
                                    <td className="p-1.5 text-left font-mono text-slate-600">
                                        {row.fixedFee == null ? "—" : fmtSar(row.fixedFee)}
                                    </td>
                                    <td className="p-1.5 text-left font-mono text-slate-600">
                                        {row.vatAmount != null && row.vatAmount > 0
                                            ? fmtSar(row.vatAmount)
                                            : row.vatPercent != null && row.vatPercent > 0
                                                ? `${row.vatPercent.toFixed(0)}%`
                                                : "—"}
                                    </td>
                                    <td className="p-1.5 text-left font-mono font-extrabold text-violet-800">
                                        {fmtSar(row.feeAmount)}
                                    </td>
                                </tr>
                            ))}
                            <tr className="border-t-2 border-violet-200 bg-violet-50/60">
                                <td colSpan={6} className="p-1.5 font-extrabold text-slate-800">الإجمالي</td>
                                <td className="p-1.5 text-left font-mono font-extrabold text-violet-900">
                                    {fmtSar(allPaymentFees)}
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            )}
            <p className="mt-2 text-[10px] leading-relaxed text-slate-500">
                عمولة الحساب الإعلاني تُحسب من الصرف الأصلي بعد تحويله بسعر الصرف المحفوظ للحساب،
                وتُخصم مرة واحدة ضمن إجمالي رسوم طرق الدفع.
            </p>
        </div>
    );

    const hasAdsExecutiveBreakdown = Boolean(
        adsExecutiveBreakdown?.providers && adsExecutiveBreakdown?.total
    );
    const adsExecutiveTooltip = hasAdsExecutiveBreakdown
        ? <AdsExecutiveBreakdownTable data={adsExecutiveBreakdown} />
        : null;

    // 2) Operating expenses breakdown — sourced from `totals.operating_*`.
    const opRows = [
        { name: "رواتب الموظفين",
          value: Number(t.operating_salaries_employee || 0) },
        { name: "مصاريف منزلية",
          value: Number(t.operating_salaries_household || 0) },
        { name: "صدقات / زكاة",
          value: Number(t.operating_salaries_charity || 0) },
        { name: "إيجارات",
          value: Number(t.operating_rentals_total || 0) },
        { name: "كهرباء وماء",
          value: Number(t.operating_utilities_total || 0) },
        { name: "تجديدات وتأمين والتزامات دورية",
          value: Number(t.operating_renewals_total || 0) },
        { name: "مصاريف مدفوعة مقدماً",
          value: Number(t.operating_prepaid_total || 0) },
        { name: "مصاريف يومية أخرى",
          value: Number(t.operating_daily_other_total || 0) },
    ].filter(r => r.value > 0);
    const opTooltip = (
        <div data-testid="op-tooltip-content" dir="rtl">
            <div className="flex items-center justify-between mb-2 pb-2 border-b border-slate-200">
                <span className="text-xs font-extrabold text-amber-900">
                    💼 تفاصيل المصروفات التشغيلية
                </span>
                <span className="text-[10px] text-slate-500">
                    {opRows.length} بند
                </span>
            </div>
            {opRows.length === 0 ? (
                <div className="text-xs text-slate-500 py-2 text-center">
                    لا توجد مصروفات تشغيلية في هذه الفترة
                </div>
            ) : (
                <table className="w-full text-[11px]">
                    <tbody>
                        {opRows.map((r, i) => (
                            <tr key={i} className="border-t border-slate-100">
                                <td className="py-1 font-bold text-slate-700">{r.name}</td>
                                <td className="text-left font-mono font-extrabold text-amber-700">
                                    {fmtSar(r.value)}
                                </td>
                            </tr>
                        ))}
                        <tr className="border-t-2 border-amber-200">
                            <td className="py-1 font-extrabold text-slate-800">الإجمالي</td>
                            <td className="text-left font-mono font-extrabold text-amber-800">
                                {fmtSar(operatingExpenses)}
                            </td>
                        </tr>
                    </tbody>
                </table>
            )}
        </div>
    );

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
                <Line icon={Package}    label="− تكاليف المنتجات"             value={fmtSar(productCost)}    share={sharePct(productCost, sales)}     color="amber"  onClick={() => setProductCostOpen(true)} testid="profit-line-product-cost" />
                <Line icon={Megaphone}  label="− إجمالي تكاليف الإعلانات"      value={fmtSar(adsCost)}        share={sharePct(adsCost, sales)}         color="rose"   tooltip={adsExecutiveTooltip} expandable={hasAdsExecutiveBreakdown} expanded={adsExpanded} onClick={() => hasAdsExecutiveBreakdown ? setAdsExpanded((value) => !value) : setAdsBreakdownOpen(true)} testid="profit-line-ads-cost" />
                <Line icon={Truck}      label="− إجمالي تكاليف الشحن (مقدم + آجل)" value={fmtSar(shippingTotal)}  share={sharePct(shippingTotal, sales)}   color="sky"    tooltip={shippingTooltip} testid="profit-line-shipping" expandable expanded={shippingExpanded} onClick={() => setShippingExpanded(v => !v)} />
                <Line icon={Receipt}    label="− إجمالي رسوم جميع طرق الدفع"    value={fmtSar(allPaymentFees)} share={sharePct(allPaymentFees, sales)}  color="violet" tooltip={paymentFeesTooltip} testid="profit-line-payment-fees" expandable expanded={paymentFeesExpanded} onClick={() => setPaymentFeesExpanded((value) => !value)} />
                {operatingExpenses > 0 && (
                    <Line icon={Briefcase} label="− المصروفات التشغيلية (رواتب وإيجارات وغيرها)" value={fmtSar(operatingExpenses)} share={sharePct(operatingExpenses, sales)} color="amber" tooltip={opTooltip} testid="profit-line-operating" expandable expanded={opExpanded} onClick={() => setOpExpanded(v => !v)} />
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
            <ExcludedOrdersModal
                open={excludedOpen}
                onClose={() => setExcludedOpen(false)}
                fromDate={fromDate}
                toDate={toDate}
                periodLabel={periodLabel}
            />
            <AdsCostBreakdownModal
                open={adsBreakdownOpen && !hasAdsExecutiveBreakdown}
                onClose={() => setAdsBreakdownOpen(false)}
                fromDate={fromDate}
                toDate={toDate}
                endpoint={adsBreakdownEndpoint}
            />
            {productCostBreakdown ? (
                <MezanV2ProductCostModal
                    open={productCostOpen}
                    onClose={() => setProductCostOpen(false)}
                    data={productCostBreakdown}
                    fromDate={fromDate}
                    toDate={toDate}
                />
            ) : (
                <DailyProductCostModal
                    open={productCostOpen}
                    onClose={() => setProductCostOpen(false)}
                    onSaved={() => setProductCostOpen(false)}
                />
            )}
        </div>
    );
}

export function MezanV2ProductCostModal({ open, onClose, data, fromDate, toDate }) {
    const [page, setPage] = useState(1);
    if (!open) return null;
    const rows = Array.isArray(data?.product_rows)
        ? [...data.product_rows].sort((left, right) => (
            Number(right.units_sold || 0) - Number(left.units_sold || 0)
            || Number(right.total_sales || 0) - Number(left.total_sales || 0)
            || String(left.name || "").localeCompare(String(right.name || ""), "ar")
        ))
        : [];
    const summary = data?.product_profit_summary || {};
    const filters = { from: fromDate, to: toDate };
    const pageSize = 10;
    const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    const currentPage = Math.min(page, totalPages);
    const visibleRows = rows.slice(
        (currentPage - 1) * pageSize,
        currentPage * pageSize,
    );
    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            onClick={onClose}
            data-testid="mezan-v2-product-cost-overlay"
        >
            <div
                className="flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl"
                onClick={(event) => event.stopPropagation()}
                dir="rtl"
                data-testid="mezan-v2-product-cost-modal"
            >
                <div className="flex shrink-0 items-start justify-between gap-4 border-b bg-amber-50 px-4 py-4 sm:px-6">
                    <div>
                        <h2 className="text-lg font-extrabold text-amber-900 sm:text-xl">ربحية المنتجات — ميزان 2</h2>
                        <p className="mt-1 text-xs leading-relaxed text-amber-800">
                            المبيعات والتكلفة والربح لكل منتج. التكلفة تشمل تكلفة ميزان والمكونات والخدمات؛ سلة احتياطيًا فقط.
                        </p>
                    </div>
                    <button type="button" onClick={onClose} className="shrink-0 rounded-lg border bg-white px-3 py-1.5 text-sm font-bold">إغلاق ✕</button>
                </div>
                <div className="grid shrink-0 grid-cols-2 gap-2 border-b bg-white px-4 py-3 text-xs sm:grid-cols-4 sm:px-6">
                    <ProductSummaryTile label="عدد المنتجات" value={fmtInt(summary.product_count)} />
                    <ProductSummaryTile label="الكمية المباعة" value={fmtInt(summary.total_units)} />
                    <ProductSummaryTile label="إجمالي المبيعات" value={`${fmtSar(summary.total_sales)} ر.س`} tone="emerald" />
                    <ProductSummaryTile label="إجمالي التكلفة" value={`${fmtSar(data?.total)} ر.س`} tone="amber" />
                </div>
                <div className="min-h-0 flex-1 overflow-auto">
                    {rows.length === 0 ? (
                        <div className="p-12 text-center text-sm text-slate-500" data-testid="mezan-v2-product-profit-empty">
                            لا توجد منتجات مباعة في الفترة المحددة.
                        </div>
                    ) : (
                        <table className="w-full min-w-[920px] text-sm" data-testid="mezan-v2-product-profit-table">
                            <thead className="sticky top-0 z-10 bg-slate-50 text-slate-600 shadow-sm">
                                <tr>
                                    <th className="px-4 py-3 text-right font-extrabold">المنتج</th>
                                    <th className="px-3 py-3 text-center font-extrabold">الكمية</th>
                                    <th className="px-3 py-3 text-left font-extrabold">تكلفة القطعة</th>
                                    <th className="px-3 py-3 text-left font-extrabold">إجمالي المبيعات</th>
                                    <th className="px-3 py-3 text-left font-extrabold">إجمالي التكلفة</th>
                                    <th className="px-4 py-3 text-left font-extrabold">صافي الأرباح</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visibleRows.map((row) => (
                                    <ProductProfitRow
                                        key={row.identity || row.mezan_product_id || row.salla_product_id || row.sku}
                                        row={row}
                                        href={buildMezanProductCostHref(row, filters)}
                                    />
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
                {rows.length > 0 && (
                    <div className="flex shrink-0 items-center justify-between gap-3 border-t bg-white px-4 py-2.5 text-xs sm:px-6">
                        <span className="font-bold text-slate-500">10 منتجات في الصفحة · الأعلى كمية مباعة أولًا</span>
                        <div
                            className="flex items-center gap-2"
                            data-testid="mezan-v2-product-pagination"
                            data-current-page={currentPage}
                            data-total-pages={totalPages}
                        >
                            <button
                                type="button"
                                onClick={() => setPage((value) => Math.max(1, Math.min(value, totalPages) - 1))}
                                disabled={currentPage <= 1}
                                aria-label="الصفحة السابقة"
                                className="flex h-8 w-8 items-center justify-center rounded-lg border bg-white text-slate-700 disabled:cursor-not-allowed disabled:opacity-35"
                                data-testid="mezan-v2-product-page-previous"
                            >
                                <CaretRight size={16} weight="bold" />
                            </button>
                            <span className="min-w-[88px] text-center font-extrabold text-slate-700">
                                الصفحة {currentPage} من {totalPages}
                            </span>
                            <button
                                type="button"
                                onClick={() => setPage((value) => Math.min(totalPages, Math.min(value, totalPages) + 1))}
                                disabled={currentPage >= totalPages}
                                aria-label="الصفحة التالية"
                                className="flex h-8 w-8 items-center justify-center rounded-lg border bg-white text-slate-700 disabled:cursor-not-allowed disabled:opacity-35"
                                data-testid="mezan-v2-product-page-next"
                            >
                                <CaretLeft size={16} weight="bold" />
                            </button>
                        </div>
                    </div>
                )}
                <div className="shrink-0 border-t bg-slate-50 px-4 py-3 text-[11px] text-slate-600 sm:px-6">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <span>صافي ربح المنتج = مبيعات المنتج − تكلفته، دون توزيع الإعلان أو الشحن أو رسوم الدفع على المنتجات.</span>
                        {summary.has_unpriced_products && (
                            <span className="font-bold text-amber-800">توجد منتجات بلا تكلفة؛ أرباحها مخفية حتى تُضاف التكلفة.</span>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}


function ProductSummaryTile({ label, value, tone = "slate" }) {
    const color = tone === "emerald"
        ? "text-emerald-700"
        : tone === "amber" ? "text-amber-800" : "text-slate-800";
    return (
        <div className="rounded-xl border bg-slate-50/70 p-2.5">
            <div className="font-bold text-slate-500">{label}</div>
            <div className={`num mt-1 text-base font-extrabold ${color}`}>{value}</div>
        </div>
    );
}


function ProductProfitRow({ row, href }) {
    const isMissing = row.cost_status === "missing";
    const isFallback = row.cost_status === "salla_fallback";
    const hasPartialCost = isMissing && row.total_cost != null;
    const profit = row.net_profit == null ? null : Number(row.net_profit);
    return (
        <tr
            className={`border-t align-middle ${isMissing || isFallback ? "bg-amber-50/50" : "bg-white"}`}
            data-testid={`mezan-v2-product-profit-row-${row.identity}`}
        >
            <td className="px-4 py-3">
                <Link to={href} className="group flex min-w-[300px] items-center gap-3" data-testid="mezan-v2-product-cost-link">
                    {row.image_url ? (
                        <img src={row.image_url} alt={row.name || ""} className="h-14 w-14 shrink-0 rounded-xl border bg-white object-cover" />
                    ) : (
                        <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border bg-slate-50 text-slate-300">
                            <Package size={24} />
                        </span>
                    )}
                    <span className="min-w-0">
                        <span className="block max-w-[420px] truncate font-extrabold text-slate-800 group-hover:text-emerald-700 group-hover:underline">{row.name}</span>
                        <span className="mt-1 block text-xs text-slate-500">{row.sku || "بدون SKU"}</span>
                        {(isMissing || isFallback) && (
                            <span className="mt-1 inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-extrabold text-amber-900">
                                {isMissing
                                    ? hasPartialCost
                                        ? "تكلفة جزئية — أضف التكلفة الأساسية"
                                        : "بدون تكلفة — أضف التكلفة"
                                    : "تكلفة سلة فقط — أضف تكلفة ميزان"}
                            </span>
                        )}
                    </span>
                </Link>
            </td>
            <td className="num px-3 py-3 text-center font-extrabold text-slate-700">{fmtInt(row.units_sold)}</td>
            <td className={`num px-3 py-3 text-left font-bold ${isMissing || isFallback ? "text-amber-800" : "text-slate-700"}`}>
                {row.average_unit_cost == null ? "—" : `${fmtSar(row.average_unit_cost)} ر.س`}
            </td>
            <td className="num px-3 py-3 text-left font-extrabold text-emerald-700">{fmtSar(row.total_sales)} ر.س</td>
            <td className={`num px-3 py-3 text-left font-extrabold ${isMissing || isFallback ? "text-amber-800" : "text-slate-800"}`}>
                {row.total_cost == null ? "—" : `${fmtSar(row.total_cost)} ر.س`}
            </td>
            <td className={`num px-4 py-3 text-left font-extrabold ${profit == null ? "text-amber-800" : profit >= 0 ? "text-emerald-700" : "text-rose-700"}`}>
                {profit == null ? "—" : `${fmtSar(profit)} ر.س`}
                {isFallback && profit != null && <span className="ms-1 text-[10px] font-bold">تقديري</span>}
            </td>
        </tr>
    );
}
