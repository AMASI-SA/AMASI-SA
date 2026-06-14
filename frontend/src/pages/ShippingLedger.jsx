// Iter-192-ext — Shipping Ledger (per-order, delivered-only)
//
// Read-only analytic page. Consumes /api/shipping-ledger which already
// applies all the business rules:
//   • delivered/completed orders only
//   • payment_mode badge per row (prepaid / deferred)
//   • COD vs shipping vs fees columns ready for the future settlement
//     drill-down (Iter-193)

import React, { useEffect, useMemo, useState } from "react";
import api from "../lib/api";
import { toast } from "sonner";
import { PaymentModeBadge } from "../components/PaymentModeBadge";

const fmt = (v) => Number(v || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
});
const intf = (v) => Number(v || 0).toLocaleString("en-US");

export default function ShippingLedger() {
    const [loading, setLoading] = useState(true);
    const [rows, setRows] = useState([]);
    const [totals, setTotals] = useState({});
    const [filters, setFilters] = useState({
        date_from: "", date_to: "", courier: "",
        payment_mode: "", payment_method: "",
        settlement_status: "", has_cod: "",
    });

    const load = async () => {
        setLoading(true);
        try {
            const params = {};
            for (const [k, v] of Object.entries(filters)) {
                if (v !== "" && v !== null) params[k] = v;
            }
            const r = await api.get("/shipping-ledger", { params });
            setRows(r.data?.rows || []);
            setTotals(r.data?.totals || {});
        } catch (e) {
            toast.error("فشل تحميل دفتر الشحن");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

    const couriers = useMemo(() => {
        const set = new Set();
        for (const r of rows) if (r.shipping_company) set.add(r.shipping_company);
        return [...set].sort();
    }, [rows]);

    const exportCSV = () => {
        const headers = [
            "Order", "Date", "Courier", "Payment Mode", "Payment Method",
            "Status", "Shipping Cost", "Prepaid?", "COD Amount", "COD Fee",
            "Net Due", "Settlement",
        ];
        const lines = [headers.join(",")];
        for (const r of rows) {
            lines.push([
                r.order_id, r.order_date, r.shipping_company,
                r.payment_mode, `"${(r.payment_method || "").replace(/"/g, '""')}"`,
                `"${r.order_status}"`,
                r.shipping_cost, r.prepaid_shipping ? "yes" : "no",
                r.cod_amount, r.cod_fee, r.net_due, r.settlement_status,
            ].join(","));
        }
        const blob = new Blob(["\ufeff" + lines.join("\n")],
                              { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `shipping-ledger-${new Date().toISOString().slice(0,10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const SummaryCard = ({ label, value, suffix = "ر.س", color = "slate" }) => (
        <div className={`rounded-xl border-2 p-3 bg-${color}-50 border-${color}-200`}>
            <div className="text-[11px] text-slate-600 font-bold leading-tight">{label}</div>
            <div className={`text-lg font-extrabold num text-${color}-800 mt-0.5`}>
                {typeof value === "number" && suffix === "ر.س" ? fmt(value) : intf(value)}
                {suffix ? <span className="text-[10px] mr-1">{suffix}</span> : null}
            </div>
        </div>
    );

    return (
        <div className="p-6 max-w-[1500px] mx-auto" data-testid="shipping-ledger-page">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                    <div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            🚚 دفتر الشحن التفصيلي
                        </h1>
                        <p className="text-sm text-slate-500 mt-1">
                            الطلبات الموصَّلة فقط — أساس حساب أرصدة شركات الشحن وتسويات COD.
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <button type="button" onClick={load}
                            className="px-3 py-1.5 text-xs font-bold text-slate-700 border border-slate-300 hover:border-emerald-400 rounded-lg"
                            data-testid="ledger-refresh">🔄 تحديث</button>
                        <button type="button" onClick={exportCSV}
                            className="px-3 py-1.5 text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg"
                            data-testid="ledger-export-csv">⬇️ تصدير CSV</button>
                    </div>
                </div>

                {/* Summary cards */}
                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 mb-4">
                    <SummaryCard label="عدد الطلبات الموصلة" value={totals.delivered_count || 0} suffix="" color="emerald" />
                    <SummaryCard label="إجمالي تكلفة الشحن" value={totals.total_shipping_cost} color="slate" />
                    <SummaryCard label="إجمالي COD" value={totals.total_cod} color="amber" />
                    <SummaryCard label="إجمالي رسوم COD" value={totals.total_cod_fees} color="rose" />
                    <SummaryCard label="إجمالي المسوى" value={totals.total_settled} color="emerald" />
                    <SummaryCard label="إجمالي المتبقي" value={totals.total_unsettled} color="amber" />
                    <SummaryCard label="شحن Prepaid (مدفوع مسبقاً)" value={totals.total_prepaid_shipping} color="emerald" />
                    <SummaryCard label="شحن Deferred (آجل)" value={totals.total_deferred_shipping} color="amber" />
                </div>

                {/* Filters */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4 p-3 bg-slate-50 border border-slate-200 rounded-xl">
                    <input type="date" value={filters.date_from}
                        onChange={(e) => setFilters({ ...filters, date_from: e.target.value })}
                        className="px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid="filter-date-from" placeholder="من تاريخ" />
                    <input type="date" value={filters.date_to}
                        onChange={(e) => setFilters({ ...filters, date_to: e.target.value })}
                        className="px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid="filter-date-to" placeholder="إلى تاريخ" />
                    <select value={filters.courier}
                        onChange={(e) => setFilters({ ...filters, courier: e.target.value })}
                        className="px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid="filter-courier">
                        <option value="">كل شركات الشحن</option>
                        {couriers.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <select value={filters.payment_mode}
                        onChange={(e) => setFilters({ ...filters, payment_mode: e.target.value })}
                        className="px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid="filter-payment-mode">
                        <option value="">كل طرق السداد</option>
                        <option value="prepaid">🟢 دفع مقدم</option>
                        <option value="deferred">🟠 دفع آجل</option>
                    </select>
                    <input type="text" value={filters.payment_method}
                        onChange={(e) => setFilters({ ...filters, payment_method: e.target.value })}
                        className="px-2 py-1.5 border border-slate-300 rounded text-xs"
                        placeholder="طريقة الدفع (بحث)"
                        data-testid="filter-payment-method" />
                    <select value={filters.has_cod}
                        onChange={(e) => setFilters({ ...filters, has_cod: e.target.value })}
                        className="px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid="filter-has-cod">
                        <option value="">COD: الكل</option>
                        <option value="true">يوجد COD</option>
                        <option value="false">بدون COD</option>
                    </select>
                    <select value={filters.settlement_status}
                        onChange={(e) => setFilters({ ...filters, settlement_status: e.target.value })}
                        className="px-2 py-1.5 border border-slate-300 rounded text-xs"
                        data-testid="filter-settlement-status">
                        <option value="">حالة التسوية: الكل</option>
                        <option value="unsettled">غير مسوى</option>
                        <option value="partial">مسوى جزئياً</option>
                        <option value="settled">مسوى بالكامل</option>
                    </select>
                    <button type="button" onClick={load}
                        className="px-3 py-1.5 text-xs font-bold text-white bg-slate-700 hover:bg-slate-800 rounded"
                        data-testid="ledger-apply-filters">تطبيق الفلاتر</button>
                </div>

                {/* Table */}
                <div className="overflow-x-auto border border-slate-200 rounded-xl">
                    <table className="w-full text-[11px]" data-testid="ledger-table">
                        <thead className="bg-slate-100 text-slate-700">
                            <tr>
                                <th className="text-right p-2 font-extrabold">رقم الطلب</th>
                                <th className="text-right p-2 font-extrabold">التاريخ</th>
                                <th className="text-right p-2 font-extrabold">شركة الشحن</th>
                                <th className="text-center p-2 font-extrabold">طريقة السداد</th>
                                <th className="text-right p-2 font-extrabold">طريقة الدفع</th>
                                <th className="text-right p-2 font-extrabold">حالة الطلب</th>
                                <th className="text-left p-2 font-extrabold">تكلفة الشحن</th>
                                <th className="text-left p-2 font-extrabold">COD</th>
                                <th className="text-left p-2 font-extrabold">رسوم COD</th>
                                <th className="text-left p-2 font-extrabold">صافي مستحق</th>
                                <th className="text-center p-2 font-extrabold">التسوية</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && (
                                <tr><td colSpan={11} className="text-center p-6 text-slate-500">جاري التحميل…</td></tr>
                            )}
                            {!loading && rows.length === 0 && (
                                <tr><td colSpan={11} className="text-center p-6 text-slate-500"
                                       data-testid="ledger-empty">لا توجد طلبات موصلة مطابقة للفلاتر.</td></tr>
                            )}
                            {!loading && rows.map((r) => (
                                <tr key={r.id || r.order_id} className="border-t border-slate-100 hover:bg-slate-50"
                                    data-testid={`ledger-row-${r.order_id}`}>
                                    <td className="p-2 font-bold">{r.order_id}</td>
                                    <td className="p-2 text-slate-600">{(r.order_date || "").slice(0, 10)}</td>
                                    <td className="p-2">{r.shipping_company}</td>
                                    <td className="p-2 text-center">
                                        <PaymentModeBadge payment_mode={r.payment_mode} size="xs" />
                                    </td>
                                    <td className="p-2 text-slate-600">{r.payment_method}</td>
                                    <td className="p-2 text-slate-600">{r.order_status}</td>
                                    <td className="p-2 text-left num">
                                        {fmt(r.shipping_cost)}
                                        {r.prepaid_shipping && (
                                            <div className="text-[9px] text-emerald-700 font-bold">مدفوع مسبقاً</div>
                                        )}
                                    </td>
                                    <td className="p-2 text-left num">{fmt(r.cod_amount)}</td>
                                    <td className="p-2 text-left num text-rose-700">{fmt(r.cod_fee)}</td>
                                    <td className="p-2 text-left num font-extrabold">{fmt(r.net_due)}</td>
                                    <td className="p-2 text-center">
                                        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                                            r.settlement_status === "settled"
                                                ? "bg-emerald-100 text-emerald-800"
                                                : r.settlement_status === "partial"
                                                    ? "bg-amber-100 text-amber-800"
                                                    : "bg-slate-100 text-slate-700"
                                        }`}>
                                            {r.settlement_status === "settled" ? "مسوى"
                                                : r.settlement_status === "partial" ? "جزئي"
                                                    : "غير مسوى"}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                <div className="mt-3 text-[10px] text-slate-500 leading-relaxed">
                    💡 الدفتر يعرض الطلبات الموصلة فقط — الطلبات المعلقة/الملغاة/المرتجعة
                    تظهر في شاشة التشخيص. شركات الشحن «الدفع المقدم» يُعتبر شحنها مخصوماً
                    مسبقاً من مستحقات سلة ولا يُدخل ضمن أرصدة شركات الشحن.
                </div>
            </div>
        </div>
    );
}
