/**
 * iter-45 — Electronic Net audit modal.
 *
 * Opened from the "صافي المدفوعات الإلكترونية" KPI card. Shows the merchant
 * exactly which orders went into the figure, which were dropped (and why),
 * the per-payment-method gross/fees/net breakdown, and a comparison panel
 * vs the value they see in Salla → المدفوعات → غير المفوترة.
 *
 * The modal is read-only — to change the active filter the merchant goes
 * to Settings → "صافي المدفوعات الإلكترونية" (link from the modal header).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    X, Warning, CheckCircle, Equals, MagnifyingGlass, GearSix,
    DownloadSimple,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api from "../lib/api";

const formatSar = (v) => {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

export default function ElectronicNetDebugModal({ open, onClose, filters }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const qs = new URLSearchParams();
            if (filters?.from) qs.set("from_date", filters.from);
            if (filters?.to) qs.set("to_date", filters.to);
            const { data: r } = await api.get(
                `/dashboard/electronic-net-debug${qs.toString() ? `?${qs}` : ""}`,
            );
            setData(r);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "تعذّر تحميل تفاصيل الحساب");
        } finally {
            setLoading(false);
        }
    }, [filters?.from, filters?.to]);

    useEffect(() => {
        if (open) load();
    }, [open, load]);

    const downloadCsv = useCallback(() => {
        if (!data) return;
        const rows = [
            ["نوع البطاقة", "صافي المدفوعات الإلكترونية — تفاصيل الحساب"],
            ["الفترة", `من ${data.range.from_date || "—"} إلى ${data.range.to_date || "—"}`],
            [""],
            ["إجمالي الطلبات الإلكترونية", data.totals.electronic_orders_total],
            ["الطلبات المضمنة", data.totals.electronic_orders_included],
            ["الطلبات المستبعدة", data.totals.electronic_orders_excluded],
            [""],
            ["الإجمالي قبل الفلترة (ر.س)", data.totals.pre_filter_gross],
            ["الرسوم قبل الفلترة (ر.س)", data.totals.pre_filter_fees],
            ["الصافي قبل الفلترة (ر.س)", data.totals.pre_filter_net],
            [""],
            ["الإجمالي بعد الفلترة (ر.س)", data.totals.post_filter_gross],
            ["الرسوم بعد الفلترة (ر.س)", data.totals.post_filter_fees],
            ["الصافي النهائي (ر.س)", data.totals.post_filter_net],
            [""],
            ["مرجع سلة المحفوظ (ر.س)", data.salla_reference?.value ?? "—"],
            ["الفرق مقابل سلة (ر.س)", data.salla_reference?.gap_vs_computed ?? "—"],
            ["النسبة المئوية للفرق", data.salla_reference?.gap_percent != null ? `${data.salla_reference.gap_percent}%` : "—"],
            [""],
            ["الحالات المستبعدة المفعّلة", data.excluded_statuses_active.join(" | ")],
            [""],
            ["استبعاد حسب الحالة:"],
            ["الحالة", "عدد الطلبات"],
            ...data.excluded_by_status.map(s => [s.status, s.count]),
            [""],
            ["تقسيم حسب طريقة الدفع (بعد الفلترة):"],
            ["طريقة الدفع", "عدد", "إجمالي (ر.س)", "رسوم (ر.س)", "صافي (ر.س)"],
            ...data.payment_breakdown_after_filter.map(p => [
                p.name, p.orders_count, p.total_sales, p.fee_amount, p.net_amount,
            ]),
            [""],
            ["عيّنة الطلبات المستبعدة:"],
            ["رقم الطلب", "التاريخ", "طريقة الدفع", "الحالة", "المبلغ", "سبب الاستبعاد"],
            ...data.excluded_orders_sample.map(o => [
                o.order_number, o.order_date, o.payment_method,
                o.order_status, o.total_amount, o.exclusion_reason || "",
            ]),
        ];
        const csv = rows.map(r => r.map(c => {
            const s = String(c ?? "");
            return s.includes(",") || s.includes("\"") || s.includes("\n")
                ? `"${s.replace(/"/g, '""')}"`
                : s;
        }).join(",")).join("\n");
        const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `electronic-net-debug-${data.range.from_date || ""}-${data.range.to_date || ""}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }, [data]);

    const gapBadge = useMemo(() => {
        if (!data?.salla_reference?.value) return null;
        const gap = Number(data.salla_reference.gap_vs_computed || 0);
        const pct = Number(data.salla_reference.gap_percent || 0);
        if (Math.abs(pct) < 1) {
            return { color: "emerald", icon: CheckCircle,
                text: `مطابق لسلة (الفارق ${formatSar(Math.abs(gap))} ر.س فقط)` };
        }
        return { color: "amber", icon: Warning,
            text: `فارق ${formatSar(gap)} ر.س (${pct > 0 ? "+" : ""}${pct}%) مقابل سلة` };
    }, [data]);

    if (!open) return null;

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-6 overflow-y-auto"
            data-testid="electronic-net-debug-modal"
            onClick={onClose}
        >
            <div
                className="bg-white rounded-2xl shadow-2xl max-w-4xl w-full p-5 sm:p-6 max-h-[90vh] overflow-y-auto"
                style={{ fontFamily: "Tajawal" }}
                onClick={e => e.stopPropagation()}
            >
                {/* Header */}
                <div className="flex items-start justify-between gap-3 mb-4 pb-3 border-b border-slate-200">
                    <div className="flex items-start gap-2 min-w-0">
                        <div className="w-10 h-10 rounded-lg bg-indigo-100 text-indigo-700 flex items-center justify-center flex-shrink-0">
                            <MagnifyingGlass size={22} weight="duotone" />
                        </div>
                        <div className="min-w-0">
                            <h3 className="font-extrabold text-base sm:text-lg text-slate-900">
                                تفاصيل صافي المدفوعات الإلكترونية
                            </h3>
                            <p className="text-[11px] text-slate-500 mt-0.5">
                                مطابقة مع شاشة سلة → المدفوعات → غير المفوترة
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="text-slate-400 hover:text-slate-700 p-1 flex-shrink-0"
                        data-testid="electronic-net-debug-close-btn"
                        aria-label="إغلاق"
                    >
                        <X size={20} weight="bold" />
                    </button>
                </div>

                {loading && (
                    <div className="text-center py-12 text-slate-500">جارٍ تحميل البيانات…</div>
                )}

                {!loading && data && (
                    <div className="space-y-5">
                        {/* Comparison panel (top of fold) */}
                        {gapBadge && (
                            <div className={[
                                "rounded-xl border-2 p-4 flex items-start gap-3",
                                gapBadge.color === "emerald"
                                    ? "border-emerald-300 bg-emerald-50"
                                    : "border-amber-300 bg-amber-50",
                            ].join(" ")} data-testid="electronic-net-gap-badge">
                                <gapBadge.icon size={22} weight="bold"
                                    className={gapBadge.color === "emerald" ? "text-emerald-700" : "text-amber-700"} />
                                <div className="min-w-0 flex-1">
                                    <div className={`font-bold text-sm ${
                                        gapBadge.color === "emerald" ? "text-emerald-900" : "text-amber-900"
                                    }`}>
                                        {gapBadge.text}
                                    </div>
                                    <div className="text-[11px] mt-1 text-slate-600 flex flex-wrap gap-3">
                                        <span>صافي النظام: <b>{formatSar(data.totals.post_filter_net)} ر.س</b></span>
                                        <span>•</span>
                                        <span>صافي سلة: <b>{formatSar(data.salla_reference.value)} ر.س</b></span>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* 3-column before / impact / after */}
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                            <div className="rounded-lg border border-slate-200 p-3 bg-slate-50">
                                <div className="text-[11px] font-bold text-slate-600 mb-1">قبل الفلترة</div>
                                <div className="text-[10px] text-slate-500">عدد الطلبات: {data.totals.electronic_orders_total}</div>
                                <div className="num text-base font-extrabold text-slate-700 mt-1">
                                    {formatSar(data.totals.pre_filter_net)} ر.س
                                </div>
                                <div className="text-[10px] text-slate-500 mt-1">
                                    إجمالي {formatSar(data.totals.pre_filter_gross)} − رسوم {formatSar(data.totals.pre_filter_fees)}
                                </div>
                            </div>
                            <div className="rounded-lg border border-amber-200 p-3 bg-amber-50">
                                <div className="text-[11px] font-bold text-amber-700 mb-1 flex items-center gap-1">
                                    <Warning size={12} weight="bold" />
                                    استبعاد بسبب الحالة
                                </div>
                                <div className="text-[10px] text-amber-700">عدد المستبعدة: {data.totals.electronic_orders_excluded}</div>
                                <div className="num text-base font-extrabold text-amber-900 mt-1">
                                    − {formatSar(data.totals.pre_filter_gross - data.totals.post_filter_gross)} ر.س
                                </div>
                                <div className="text-[10px] text-amber-700 mt-1">
                                    حالات: {data.excluded_statuses_active.slice(0, 4).join(" / ")}{data.excluded_statuses_active.length > 4 ? "…" : ""}
                                </div>
                            </div>
                            <div className="rounded-lg border-2 border-emerald-300 p-3 bg-emerald-50">
                                <div className="text-[11px] font-bold text-emerald-800 mb-1 flex items-center gap-1">
                                    <Equals size={12} weight="bold" />
                                    الصافي النهائي
                                </div>
                                <div className="text-[10px] text-emerald-800">عدد الطلبات: {data.totals.electronic_orders_included}</div>
                                <div className="num text-lg font-extrabold text-emerald-900 mt-1">
                                    {formatSar(data.totals.post_filter_net)} ر.س
                                </div>
                                <div className="text-[10px] text-emerald-700 mt-1">
                                    إجمالي {formatSar(data.totals.post_filter_gross)} − رسوم {formatSar(data.totals.post_filter_fees)}
                                </div>
                            </div>
                        </div>

                        {/* Excluded by status */}
                        {data.excluded_by_status.length > 0 && (
                            <div>
                                <div className="text-xs font-bold text-slate-700 mb-2">
                                    تفصيل الطلبات المستبعدة حسب الحالة
                                </div>
                                <div className="rounded-lg border border-slate-200 overflow-x-auto">
                                    <table className="mezan-table compact w-full text-xs">
                                        <thead className="bg-slate-50">
                                            <tr>
                                                <th className="text-start px-3 py-2 font-bold text-slate-700">الحالة</th>
                                                <th className="text-end px-3 py-2 font-bold text-slate-700">عدد الطلبات</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {data.excluded_by_status.map(s => (
                                                <tr key={s.status} className="border-t border-slate-100">
                                                    <td className="px-3 py-1.5">{s.status}</td>
                                                    <td className="px-3 py-1.5 text-end font-bold num">{s.count}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}

                        {/* Payment-method breakdown */}
                        {data.payment_breakdown_after_filter.length > 0 && (
                            <div>
                                <div className="text-xs font-bold text-slate-700 mb-2">
                                    تقسيم حسب طريقة الدفع (بعد الفلترة)
                                </div>
                                <div className="rounded-lg border border-slate-200 overflow-x-auto">
                                    <table className="mezan-table compact w-full text-xs">
                                        <thead className="bg-slate-50">
                                            <tr>
                                                <th className="text-start px-3 py-2 font-bold text-slate-700">طريقة الدفع</th>
                                                <th className="text-end px-3 py-2 font-bold text-slate-700">عدد</th>
                                                <th className="text-end px-3 py-2 font-bold text-slate-700">إجمالي</th>
                                                <th className="text-end px-3 py-2 font-bold text-slate-700">رسوم</th>
                                                <th className="text-end px-3 py-2 font-bold text-slate-700">صافي</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {data.payment_breakdown_after_filter.map((p, i) => (
                                                <tr key={`${p.name}-${i}`} className="border-t border-slate-100">
                                                    <td className="px-3 py-1.5">{p.name}</td>
                                                    <td className="px-3 py-1.5 text-end num">{p.orders_count}</td>
                                                    <td className="px-3 py-1.5 text-end num">{formatSar(p.total_sales)}</td>
                                                    <td className="px-3 py-1.5 text-end num text-amber-700">{formatSar(p.fee_amount)}</td>
                                                    <td className="px-3 py-1.5 text-end num font-bold text-emerald-700">{formatSar(p.net_amount)}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}

                        {/* Excluded sample */}
                        {data.excluded_orders_sample.length > 0 && (
                            <details className="rounded-lg border border-amber-200 bg-amber-50/50">
                                <summary className="cursor-pointer px-3 py-2 text-xs font-bold text-amber-900 list-none flex items-center justify-between hover:bg-amber-100/50">
                                    <span>عرض عيّنة الطلبات المستبعدة ({data.excluded_orders_sample.length})</span>
                                    <span className="text-[10px] text-amber-700">انقر للتوسيع</span>
                                </summary>
                                <div className="px-3 py-2 max-h-72 overflow-y-auto">
                                    <table className="mezan-table compact w-full text-[11px]">
                                        <thead className="text-amber-900">
                                            <tr>
                                                <th className="text-start px-2 py-1">رقم الطلب</th>
                                                <th className="text-start px-2 py-1">طريقة الدفع</th>
                                                <th className="text-start px-2 py-1">الحالة</th>
                                                <th className="text-end px-2 py-1">المبلغ</th>
                                                <th className="text-start px-2 py-1">السبب</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {data.excluded_orders_sample.map((o, i) => (
                                                <tr key={i} className="border-t border-amber-100">
                                                    <td className="px-2 py-1 font-bold num">#{o.order_number}</td>
                                                    <td className="px-2 py-1">{o.payment_method}</td>
                                                    <td className="px-2 py-1">{o.order_status}</td>
                                                    <td className="px-2 py-1 text-end num">{formatSar(o.total_amount)}</td>
                                                    <td className="px-2 py-1 text-amber-800">{o.exclusion_reason}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </details>
                        )}

                        {/* Footer actions */}
                        <div className="flex flex-wrap gap-2 pt-4 border-t border-slate-200">
                            <button
                                type="button"
                                onClick={downloadCsv}
                                className="inline-flex items-center gap-1 px-3 py-2 rounded-lg bg-slate-50 hover:bg-slate-100 text-slate-700 border border-slate-200 text-xs font-bold"
                                data-testid="electronic-net-download-csv"
                            >
                                <DownloadSimple size={14} weight="bold" />
                                تنزيل CSV
                            </button>
                            <Link
                                to="/settings"
                                className="inline-flex items-center gap-1 px-3 py-2 rounded-lg bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 text-xs font-bold mr-auto"
                                data-testid="electronic-net-settings-link"
                            >
                                <GearSix size={14} weight="bold" />
                                ضبط الفلتر من الإعدادات
                            </Link>
                            <button
                                type="button"
                                onClick={onClose}
                                className="px-4 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-xs"
                                data-testid="electronic-net-close-bottom-btn"
                            >
                                إغلاق
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
