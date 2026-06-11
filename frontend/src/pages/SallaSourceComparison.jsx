import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    ChartPieSlice, ArrowsClockwise, ArrowLeft, Info, CheckCircle,
    Warning, Database,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api from "../lib/api";
import { todaySA } from "../lib/dates";

/**
 * /salla-sources — Compare unified_orders bucketed by data source
 *
 * Used by the merchant during the Salla Direct rollout to verify that
 * the new direct sync sees the same orders Make.com and Excel see.
 * Once parity is reached, the merchant can confidently move toward
 * making salla_direct the primary source.
 */

const COMBINATION_LABELS = {
    make_only: "Make فقط",
    excel_only: "Excel فقط",
    salla_only: "Salla Direct فقط",
    make_and_salla: "Make + Salla Direct",
    excel_and_salla: "Excel + Salla Direct",
    make_excel_and_salla: "Make + Excel + Salla",
    make_and_excel: "Make + Excel",
    unknown: "غير محدد",
};

const SOURCE_COLORS = {
    make: "bg-emerald-500",
    excel: "bg-sky-500",
    salla_direct: "bg-violet-500",
};

function fmtMoney(n) {
    return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        .format(Number(n) || 0);
}

function todayISO() {
    return todaySA();
}

function daysAgoISO(days) {
    const d = new Date(Date.now() + 3 * 60 * 60 * 1000); // Riyadh offset
    d.setUTCDate(d.getUTCDate() - days);
    return d.toISOString().slice(0, 10);
}

export default function SallaSourceComparison() {
    const navigate = useNavigate();
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState(null);
    const [fromDate, setFromDate] = useState(daysAgoISO(30));
    const [toDate, setToDate] = useState(todayISO());

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const { data: res } = await api.get("/salla/sources-comparison", {
                params: { from_date: fromDate, to_date: toDate },
            });
            setData(res);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "تعذّر تحميل المقارنة");
        } finally {
            setLoading(false);
        }
    }, [fromDate, toDate]);

    useEffect(() => { load(); }, [load]);

    const grand = data?.totals || { orders: 0, amount: 0 };
    const per = data?.per_source_totals || {};

    return (
        <div className="p-4 sm:p-6 space-y-5" data-testid="salla-sources-page" style={{ fontFamily: "Tajawal" }}>
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-3">
                    <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-600 flex items-center justify-center text-white shadow-lg">
                        <ChartPieSlice size={28} weight="bold" />
                    </div>
                    <div>
                        <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900">مقارنة مصادر البيانات</h1>
                        <p className="text-sm text-slate-500 mt-1">
                            مقارنة الطلبات بين Excel و Make.com و Salla Direct للتحقق من التطابق قبل اعتماد سلة كمصدر نهائي.
                        </p>
                    </div>
                </div>
                <button
                    type="button"
                    onClick={() => navigate("/settings/salla")}
                    className="inline-flex items-center gap-1 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-bold"
                    data-testid="salla-sources-back-btn"
                >
                    <ArrowLeft size={14} weight="bold" />
                    رجوع لربط سلة
                </button>
            </div>

            {/* Filters */}
            <div className="bg-white border border-slate-200 rounded-xl p-4 flex flex-wrap items-end gap-3" data-testid="salla-sources-filters">
                <div>
                    <label className="block text-xs font-bold text-slate-600 mb-1">من تاريخ</label>
                    <input
                        type="date"
                        value={fromDate}
                        onChange={(e) => setFromDate(e.target.value)}
                        className="px-3 py-2 text-sm border border-slate-300 rounded-lg font-mono"
                        data-testid="salla-sources-from-date"
                    />
                </div>
                <div>
                    <label className="block text-xs font-bold text-slate-600 mb-1">إلى تاريخ</label>
                    <input
                        type="date"
                        value={toDate}
                        onChange={(e) => setToDate(e.target.value)}
                        className="px-3 py-2 text-sm border border-slate-300 rounded-lg font-mono"
                        data-testid="salla-sources-to-date"
                    />
                </div>
                <button
                    type="button"
                    onClick={load}
                    disabled={loading}
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm disabled:opacity-50"
                    data-testid="salla-sources-refresh-btn"
                >
                    <ArrowsClockwise size={14} weight="bold" className={loading ? "animate-spin" : ""} />
                    تحديث
                </button>
            </div>

            {/* Per-source totals (3 cards) */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3" data-testid="salla-sources-summary">
                {[
                    { key: "make", label: "Make.com", color: "from-emerald-500 to-emerald-600", bg: "bg-emerald-50", border: "border-emerald-200" },
                    { key: "excel", label: "Excel Uploads", color: "from-sky-500 to-sky-600", bg: "bg-sky-50", border: "border-sky-200" },
                    { key: "salla_direct", label: "Salla Direct", color: "from-violet-500 to-violet-600", bg: "bg-violet-50", border: "border-violet-200" },
                ].map((c) => {
                    const v = per[c.key] || { orders: 0, amount: 0 };
                    return (
                        <div
                            key={c.key}
                            className={`${c.bg} ${c.border} border rounded-xl p-4`}
                            data-testid={`salla-source-card-${c.key}`}
                        >
                            <div className={`inline-flex items-center gap-2 px-2 py-1 rounded-full bg-gradient-to-r ${c.color} text-white text-xs font-bold mb-3`}>
                                <Database size={12} weight="bold" />
                                {c.label}
                            </div>
                            <div className="text-2xl font-extrabold text-slate-900">{v.orders} طلب</div>
                            <div className="text-sm text-slate-600 mt-1 font-bold">{fmtMoney(v.amount)} ر.س</div>
                        </div>
                    );
                })}
            </div>

            {/* Combination breakdown */}
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden" data-testid="salla-sources-combinations">
                <div className="px-5 py-3 bg-slate-50 border-b border-slate-200">
                    <h3 className="font-extrabold text-slate-900">توزّع الطلبات حسب المصادر</h3>
                    <p className="text-[11px] text-slate-500 mt-1">إجمالي {grand.orders} طلب بقيمة {fmtMoney(grand.amount)} ر.س</p>
                </div>
                <table className="mezan-table w-full text-sm">
                    <thead className="bg-slate-50 text-slate-600 text-xs">
                        <tr>
                            <th className="text-right px-4 py-2">الحالة</th>
                            <th className="text-right px-4 py-2">عدد الطلبات</th>
                            <th className="text-right px-4 py-2">المبلغ (ر.س)</th>
                            <th className="text-right px-4 py-2">% من الإجمالي</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {Object.entries(data?.by_combination || {}).map(([key, v]) => {
                            const pct = grand.orders ? (v.orders * 100 / grand.orders) : 0;
                            return (
                                <tr key={key} className="hover:bg-slate-50" data-testid={`salla-combo-${key}`}>
                                    <td className="px-4 py-2 font-bold text-slate-800">{COMBINATION_LABELS[key] || key}</td>
                                    <td className="px-4 py-2 font-mono">{v.orders}</td>
                                    <td className="px-4 py-2 font-mono">{fmtMoney(v.amount)}</td>
                                    <td className="px-4 py-2">
                                        <div className="flex items-center gap-2">
                                            <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                                                <div className="h-full bg-indigo-500" style={{ width: `${pct}%` }} />
                                            </div>
                                            <span className="text-[11px] font-bold text-slate-600 w-12 text-end">{pct.toFixed(1)}%</span>
                                        </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            {/* Diff lists */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-4" data-testid="salla-missing-from-make">
                    <div className="flex items-start gap-2 mb-2">
                        <Warning size={18} weight="bold" className="text-amber-600 flex-shrink-0" />
                        <div className="flex-1">
                            <h4 className="font-extrabold text-amber-900 text-sm">طلبات في Salla Direct وليست في Make</h4>
                            <p className="text-[11px] text-amber-800 mt-1">عدد: {data?.missing_from_make_count || 0}</p>
                        </div>
                    </div>
                    {(data?.missing_from_make || []).length === 0 ? (
                        <div className="text-xs text-amber-700">لا توجد فروقات.</div>
                    ) : (
                        <div className="max-h-40 overflow-y-auto bg-white rounded-lg border border-amber-200 p-2 font-mono text-xs space-y-0.5">
                            {(data?.missing_from_make || []).map((o) => <div key={o}>{o}</div>)}
                        </div>
                    )}
                </div>
                <div className="bg-rose-50 border border-rose-200 rounded-xl p-4" data-testid="salla-missing-from-salla">
                    <div className="flex items-start gap-2 mb-2">
                        <Info size={18} weight="bold" className="text-rose-600 flex-shrink-0" />
                        <div className="flex-1">
                            <h4 className="font-extrabold text-rose-900 text-sm">طلبات في Make/Excel وليست في Salla Direct</h4>
                            <p className="text-[11px] text-rose-800 mt-1">عدد: {data?.missing_from_salla_count || 0}</p>
                        </div>
                    </div>
                    {(data?.missing_from_salla || []).length === 0 ? (
                        <div className="text-xs text-rose-700 flex items-center gap-1"><CheckCircle size={14} weight="bold" /> لا توجد فروقات.</div>
                    ) : (
                        <div className="max-h-40 overflow-y-auto bg-white rounded-lg border border-rose-200 p-2 font-mono text-xs space-y-0.5">
                            {(data?.missing_from_salla || []).map((o) => <div key={o}>{o}</div>)}
                        </div>
                    )}
                </div>
            </div>

            {loading && (
                <div className="text-center py-6 text-slate-500" data-testid="salla-sources-loading">
                    <ArrowsClockwise size={20} className="animate-spin mx-auto mb-1" /> جاري التحميل…
                </div>
            )}
        </div>
    );
}
