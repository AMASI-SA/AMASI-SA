import { useEffect, useMemo, useRef, useState } from "react";
import {
    Warning, ArrowsClockwise, ArrowSquareOut, FunnelSimple, X,
} from "@phosphor-icons/react";
import api from "../lib/api";


const PERIOD_CHIPS = [
    { key: "today", label: "اليوم" },
    { key: "yesterday", label: "بالأمس" },
    { key: "this_month", label: "هذا الشهر" },
    { key: "last_month", label: "الشهر الماضي" },
    { key: "last_30d", label: "آخر 30 يوم" },
    { key: "this_year", label: "السنة الحالية" },
    { key: "custom", label: "فترة مخصّصة" },
];

function fmtMoney(n) {
    return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        .format(Number(n) || 0);
}

function fmtDate(d) {
    if (!d) return "—";
    try { return new Date(d).toLocaleDateString("ar-SA"); } catch { return d; }
}


/**
 * Smart refund-monitor alert shown on the Reports page.
 *
 * Date filters: today / yesterday / this_month / last_month /
 * last_30d / this_year / custom (with from/to inputs).
 *
 * Endpoint: GET /api/reports/refunds-alert?period=...&from_date=&to_date=
 */
export default function RefundsAlert() {
    const [period, setPeriod] = useState("last_30d");
    const [customFrom, setCustomFrom] = useState("");
    const [customTo, setCustomTo] = useState("");
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [showDetails, setShowDetails] = useState(false);

    const reqIdRef = useRef(0);

    const fetchData = (p, cf, ct) => {
        if (p === "custom" && (!cf || !ct)) return;
        const myId = ++reqIdRef.current;
        const params = { period: p };
        if (p === "custom") {
            params.from_date = cf;
            params.to_date = ct;
        }
        setLoading(true);
        api.get("/reports/refunds-alert", { params })
            .then(
                (res) => { if (myId === reqIdRef.current) { setData(res.data); setLoading(false); } },
                () => { if (myId === reqIdRef.current) { setData(null); setLoading(false); } },
            );
    };

    // Re-fetch whenever filters change.
    useEffect(() => { fetchData(period, customFrom, customTo); }, [period, customFrom, customTo]);  // eslint-disable-line

    const s = data?.summary;
    const hasRefunds = (s?.refund_orders_count || 0) > 0;

    const severity = useMemo(() => {
        if (!s) return "neutral";
        const rate = s.refund_rate_pct || 0;
        if (rate >= 5) return "high";
        if (rate >= 2) return "medium";
        return "low";
    }, [s]);

    const severityStyles = {
        high: "border-rose-300 bg-rose-50",
        medium: "border-amber-300 bg-amber-50",
        low: "border-emerald-200 bg-emerald-50",
        neutral: "border-slate-200 bg-slate-50",
    }[severity];

    return (
        <div className={`border rounded-xl overflow-hidden ${severityStyles}`} data-testid="refunds-alert-card">
            {/* Header bar */}
            <div className="px-5 py-3 flex items-center justify-between border-b border-current/10 flex-wrap gap-2">
                <div className="flex items-center gap-2">
                    <Warning
                        size={20}
                        weight="bold"
                        className={
                            severity === "high" ? "text-rose-600"
                            : severity === "medium" ? "text-amber-600"
                            : severity === "low" ? "text-emerald-600"
                            : "text-slate-500"
                        }
                    />
                    <h3 className="font-extrabold text-slate-900 text-base">
                        تنبيه الاسترجاعات
                        <span className="text-[11px] text-slate-500 font-normal ms-2">— {data?.label || "—"}</span>
                    </h3>
                </div>
                {loading && (
                    <ArrowsClockwise size={14} className="animate-spin text-slate-400" />
                )}
            </div>

            {/* Period chips */}
            <div className="px-5 py-3 flex flex-wrap items-center gap-1.5 border-b border-current/10" data-testid="refunds-period-chips">
                <FunnelSimple size={14} weight="bold" className="text-slate-500 me-1" />
                {PERIOD_CHIPS.map((p) => (
                    <button
                        key={p.key}
                        type="button"
                        onClick={() => setPeriod(p.key)}
                        className={`px-3 py-1 rounded-full text-[11px] font-bold transition-colors ${
                            period === p.key
                                ? "bg-indigo-600 text-white"
                                : "bg-white text-slate-700 hover:bg-slate-100 border border-slate-200"
                        }`}
                        data-testid={`refunds-period-${p.key}`}
                    >
                        {p.label}
                    </button>
                ))}
            </div>

            {/* Custom date inputs */}
            {period === "custom" && (
                <div className="px-5 py-3 flex flex-wrap items-end gap-2 border-b border-current/10 bg-white/40" data-testid="refunds-custom-inputs">
                    <div>
                        <label className="block text-[10px] font-bold text-slate-600 mb-1">من</label>
                        <input
                            type="date"
                            value={customFrom}
                            onChange={(e) => setCustomFrom(e.target.value)}
                            className="px-2 py-1 text-xs border border-slate-300 rounded font-mono bg-white"
                            data-testid="refunds-custom-from"
                        />
                    </div>
                    <div>
                        <label className="block text-[10px] font-bold text-slate-600 mb-1">إلى</label>
                        <input
                            type="date"
                            value={customTo}
                            onChange={(e) => setCustomTo(e.target.value)}
                            className="px-2 py-1 text-xs border border-slate-300 rounded font-mono bg-white"
                            data-testid="refunds-custom-to"
                        />
                    </div>
                </div>
            )}

            {/* Summary cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 p-4 bg-white/40" data-testid="refunds-summary">
                <SummaryCell
                    label="طلبات مرتجعة"
                    value={s?.refund_orders_count || 0}
                    sub={s ? `${s.refund_rate_pct || 0}% من ${s.total_orders_in_period || 0}` : ""}
                    accent={severity === "high" ? "text-rose-700" : severity === "medium" ? "text-amber-700" : "text-slate-900"}
                />
                <SummaryCell
                    label="إجمالي مبلغ الاسترجاع"
                    value={fmtMoney(s?.total_refund_amount || 0)}
                    sub="ر.س"
                />
                <SummaryCell
                    label="استرجاع جزئي"
                    value={fmtMoney(s?.total_refund_partial || 0)}
                    sub="ر.س"
                    accent="text-amber-700"
                />
                <SummaryCell
                    label="استرجاع كامل"
                    value={fmtMoney(s?.total_refund_full || 0)}
                    sub="ر.س"
                    accent="text-rose-700"
                />
            </div>

            {/* By payment method breakdown */}
            {hasRefunds && (data?.by_payment_method?.length || 0) > 0 && (
                <div className="px-4 pb-3 bg-white/40">
                    <p className="text-[10px] font-bold text-slate-500 mb-1">حسب طريقة الدفع:</p>
                    <div className="flex flex-wrap gap-1.5">
                        {data.by_payment_method.map((row) => (
                            <span
                                key={row.payment_method}
                                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-white border border-slate-200 text-[11px] font-bold text-slate-700"
                                data-testid={`refunds-method-${row.payment_method}`}
                            >
                                {row.payment_method}
                                <span className="text-rose-600">{fmtMoney(row.amount)}</span>
                                <span className="text-slate-400">({row.orders})</span>
                            </span>
                        ))}
                    </div>
                </div>
            )}

            {/* Footer + expandable details */}
            {hasRefunds ? (
                <div className="px-4 py-2 border-t border-current/10 bg-white/60">
                    <button
                        type="button"
                        onClick={() => setShowDetails(true)}
                        className="text-xs text-indigo-700 hover:text-indigo-800 font-bold inline-flex items-center gap-1"
                        data-testid="refunds-view-details-btn"
                    >
                        <ArrowSquareOut size={12} weight="bold" />
                        عرض تفاصيل الطلبات الـ{s.refund_orders_count}
                    </button>
                </div>
            ) : (
                <div className="px-4 py-3 border-t border-current/10 text-xs text-slate-500 text-center" data-testid="refunds-empty">
                    لا توجد طلبات مرتجعة في هذه الفترة 🎉
                </div>
            )}

            {/* Details modal */}
            {showDetails && (
                <div
                    className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                    onClick={() => setShowDetails(false)}
                >
                    <div
                        className="bg-white rounded-xl shadow-2xl max-w-4xl w-full max-h-[85vh] flex flex-col"
                        onClick={(e) => e.stopPropagation()}
                        data-testid="refunds-details-modal"
                    >
                        <div className="px-5 py-3 border-b border-slate-200 flex items-center justify-between">
                            <div>
                                <h3 className="font-extrabold text-slate-900">تفاصيل الطلبات المرتجعة</h3>
                                <p className="text-[11px] text-slate-500 mt-0.5">
                                    {data?.label} · {data?.from_date} → {data?.to_date}
                                </p>
                            </div>
                            <button
                                onClick={() => setShowDetails(false)}
                                className="text-slate-400 hover:text-slate-700"
                                data-testid="refunds-details-close"
                            >
                                <X size={20} weight="bold" />
                            </button>
                        </div>
                        <div className="flex-1 overflow-y-auto">
                            <table className="w-full text-xs">
                                <thead className="bg-slate-50 text-slate-600 text-[11px] sticky top-0">
                                    <tr>
                                        <th className="text-right px-3 py-2">رقم الطلب</th>
                                        <th className="text-right px-3 py-2">العميل</th>
                                        <th className="text-right px-3 py-2">طريقة الدفع</th>
                                        <th className="text-right px-3 py-2">إجمالي الطلب</th>
                                        <th className="text-right px-3 py-2">استرجاع جزئي</th>
                                        <th className="text-right px-3 py-2">استرجاع كامل</th>
                                        <th className="text-right px-3 py-2">الصافي الفعلي</th>
                                        <th className="text-right px-3 py-2">تاريخ التسوية</th>
                                        <th className="text-right px-3 py-2">المصدر</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100">
                                    {(data?.orders || []).map((o, i) => (
                                        <tr key={o.order_number + i} className="hover:bg-slate-50" data-testid={`refund-row-${o.order_number}`}>
                                            <td className="px-3 py-2 font-mono font-bold">{o.order_number}</td>
                                            <td className="px-3 py-2 max-w-[140px] truncate">{o.customer_name || "—"}</td>
                                            <td className="px-3 py-2">
                                                <span className="px-1.5 py-0.5 rounded-full bg-slate-100 text-[10px] font-bold">
                                                    {o.actual_payment_method || o.payment_method || "—"}
                                                </span>
                                            </td>
                                            <td className="px-3 py-2 font-mono">{fmtMoney(o.total_amount || o.actual_gross_amount || 0)}</td>
                                            <td className="px-3 py-2 font-mono text-amber-700 font-bold">{fmtMoney(o.actual_partial_refund_amount || 0)}</td>
                                            <td className="px-3 py-2 font-mono text-rose-700 font-bold">{fmtMoney(o.actual_refund_amount || 0)}</td>
                                            <td className="px-3 py-2 font-mono text-emerald-700">{fmtMoney(o.actual_net_amount || 0)}</td>
                                            <td className="px-3 py-2 text-slate-500 text-[11px]">{fmtDate(o.settlement_date)}</td>
                                            <td className="px-3 py-2 text-[10px] text-slate-500">{o.settlement_source || "—"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}


function SummaryCell({ label, value, sub, accent = "text-slate-900" }) {
    return (
        <div className="bg-white rounded-lg p-2 border border-slate-200">
            <div className="text-[10px] text-slate-500 font-bold mb-0.5">{label}</div>
            <div className={`text-lg font-extrabold ${accent}`}>{value}</div>
            {sub && <div className="text-[10px] text-slate-400 mt-0.5">{sub}</div>}
        </div>
    );
}
