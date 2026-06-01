// Iteration 26: Product Sales Report.
// Fetches /api/product-costs/product-sales for the active date range and
// renders a per-product table with: image, name, product_id, sku, units,
// sales, cost, profit, margin %, and a "تكلفة غير مكتملة" badge for
// products that lack cost data. Totals row reflects ONLY complete rows
// — incomplete rows are visually flagged and EXCLUDED from real-profit
// calculations (per merchant rule: never assume missing cost = 0).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Package, Warning, ChartLineUp } from "@phosphor-icons/react";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";

export default function ProductSalesReport({ fromDate, toDate }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const params = new URLSearchParams();
            if (fromDate) params.set("from_date", fromDate);
            if (toDate) params.set("to_date", toDate);
            const qs = params.toString();
            const { data: body } = await api.get(
                `/product-costs/product-sales${qs ? "?" + qs : ""}`,
            );
            setData(body);
        } catch {
            setData(null);
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); /* eslint-disable-next-line */ }, [fromDate, toDate]);

    if (loading) return (
        <div className="rounded-xl border border-border bg-white p-6 text-center text-muted-foreground"
             data-testid="product-sales-report-loading">
            جاري تحميل تقرير مبيعات المنتجات…
        </div>
    );

    if (!data || data.count === 0) return (
        <div className="rounded-xl border-2 border-dashed border-border bg-white p-8 text-center"
             data-testid="product-sales-report-empty">
            <ChartLineUp size={48} weight="duotone" className="text-muted-foreground/50 mx-auto mb-3" />
            <h3 className="text-lg font-bold mb-1">لا توجد مبيعات في النطاق المحدد</h3>
            <p className="text-sm text-muted-foreground">
                نطاق التقرير: {data?.range?.from_date || "—"} إلى {data?.range?.to_date || "—"}.
            </p>
        </div>
    );

    const t = data.totals || {};

    return (
        <div className="rounded-xl border border-border bg-white p-4 sm:p-6"
             data-testid="product-sales-report">
            <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
                <div>
                    <h3 className="text-xl font-bold" style={{ fontFamily: "Tajawal" }}>
                        تقرير مبيعات المنتجات
                    </h3>
                    <p className="text-xs text-muted-foreground mt-1">
                        من {data.range.from_date} إلى {data.range.to_date} • {data.count} منتج
                        {data.incomplete_count > 0 && (
                            <span className="ms-2 text-amber-700 font-bold">
                                ({data.incomplete_count} تكلفة غير مكتملة)
                            </span>
                        )}
                    </p>
                </div>
            </div>

            {/* Totals summary row */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                <SummaryBox label="مبيعات (الكل)"
                            value={`${formatMoney(t.total_sales_all)} ر.س`}
                            testid="psr-totals-sales-all" />
                <SummaryBox label="مبيعات (مكتملة التكلفة)"
                            value={`${formatMoney(t.total_sales_complete)} ر.س`}
                            testid="psr-totals-sales-complete" />
                <SummaryBox label="إجمالي التكلفة"
                            value={`${formatMoney(t.total_cost_complete)} ر.س`}
                            tint="red"
                            testid="psr-totals-cost" />
                <SummaryBox label="صافي الربح"
                            value={`${formatMoney(t.total_profit_complete)} ر.س`}
                            sub={`هامش ${t.margin_complete_pct?.toFixed(1) || "0"}%`}
                            tint="green"
                            testid="psr-totals-profit" />
            </div>

            {data.incomplete_count > 0 && (
                <Link to="/product-costs?tab=missing"
                      className="block mb-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 hover:bg-amber-100 transition-colors"
                      data-testid="psr-incomplete-banner">
                    <div className="flex items-center gap-2 flex-wrap">
                        <Warning size={18} weight="fill" className="text-amber-600" />
                        <strong>{data.incomplete_count} منتج بدون تكلفة كاملة</strong>
                        <span className="opacity-80">— لم يُحسب الربح لها (مستثناة من الإجماليات). أضف التكلفة من صفحة "بدون تكلفة".</span>
                        <span className="ms-auto text-xs font-bold text-amber-800 underline">إضافة الآن ←</span>
                    </div>
                </Link>
            )}

            <div className="overflow-x-auto -mx-4 sm:mx-0 px-4 sm:px-0">
                <table className="w-full text-sm text-right border-collapse min-w-[820px]"
                       data-testid="psr-table">
                    <thead className="text-muted-foreground bg-accent/40 border-b-2 border-border text-xs">
                        <tr>
                            <th className="py-2 px-3 font-bold w-14">الصورة</th>
                            <th className="py-2 px-3 font-bold text-start">اسم المنتج</th>
                            <th className="py-2 px-3 font-bold text-start" dir="ltr">Product&nbsp;ID</th>
                            <th className="py-2 px-3 font-bold text-start" dir="ltr">SKU</th>
                            <th className="py-2 px-3 font-bold text-end">الوحدات</th>
                            <th className="py-2 px-3 font-bold text-end">المبيعات</th>
                            <th className="py-2 px-3 font-bold text-end">التكلفة</th>
                            <th className="py-2 px-3 font-bold text-end">الربح</th>
                            <th className="py-2 px-3 font-bold text-end">الهامش %</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                        {data.items.map((it, i) => {
                            const incomplete = it.cost_status !== "complete";
                            const key = it.product_id || it.sku || it.name || i;
                            return (
                                <tr key={i}
                                    className={`hover:bg-accent/30 transition-colors ${incomplete ? "bg-amber-50/40" : ""}`}
                                    data-testid={`psr-row-${key}`}>
                                    <td className="py-2 px-2">
                                        <div className="w-10 h-10 rounded-md border border-border bg-accent/30 overflow-hidden mx-auto flex items-center justify-center">
                                            {it.image_url ? (
                                                <img
                                                    src={it.image_url}
                                                    alt={it.name || "product"}
                                                    className="w-full h-full object-cover"
                                                    onError={(e) => { e.currentTarget.style.display = "none"; }}
                                                />
                                            ) : (
                                                <Package size={14} className="text-muted-foreground/60" />
                                            )}
                                        </div>
                                    </td>
                                    <td className="py-2 px-3">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <span className="font-semibold">{it.name || "(بدون اسم)"}</span>
                                            {incomplete && (
                                                <span className="text-[10px] px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded-full font-bold whitespace-nowrap"
                                                      data-testid={`psr-incomplete-badge-${key}`}>
                                                    ⚠️ تكلفة غير مكتملة
                                                </span>
                                            )}
                                        </div>
                                    </td>
                                    <td className="py-2 px-3 text-xs tabular-nums" dir="ltr">{it.product_id || "—"}</td>
                                    <td className="py-2 px-3 text-xs tabular-nums" dir="ltr">{it.sku || "—"}</td>
                                    <td className="py-2 px-3 text-end tabular-nums font-bold">{formatInt(it.units_sold)}</td>
                                    <td className="py-2 px-3 text-end tabular-nums">{formatMoney(it.total_sales)} <span className="text-[10px] text-muted-foreground">ر.س</span></td>
                                    <td className="py-2 px-3 text-end tabular-nums text-red-700">
                                        {incomplete ? <span className="italic text-amber-700">—</span> : formatMoney(it.total_cost)}
                                    </td>
                                    <td className="py-2 px-3 text-end tabular-nums font-bold">
                                        {it.total_profit === null
                                            ? <span className="italic text-amber-700">غير محسوب</span>
                                            : <span className={it.total_profit >= 0 ? "text-emerald-700" : "text-red-700"}>{formatMoney(it.total_profit)}</span>
                                        }
                                    </td>
                                    <td className="py-2 px-3 text-end tabular-nums">
                                        {it.profit_margin_pct === null
                                            ? <span className="italic text-muted-foreground">—</span>
                                            : `${it.profit_margin_pct.toFixed(1)}%`
                                        }
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function SummaryBox({ label, value, sub, tint, testid }) {
    const tintCls = tint === "green"
        ? "border-emerald-200 bg-emerald-50 text-emerald-900"
        : tint === "red"
            ? "border-red-200 bg-red-50 text-red-900"
            : "border-border bg-accent/30 text-foreground";
    return (
        <div className={`rounded-lg border p-3 ${tintCls}`} data-testid={testid}>
            <div className="text-[10px] font-bold opacity-70 mb-1">{label}</div>
            <div className="text-base sm:text-lg font-extrabold tabular-nums leading-tight">{value}</div>
            {sub && <div className="text-[10px] opacity-70 mt-0.5">{sub}</div>}
        </div>
    );
}
