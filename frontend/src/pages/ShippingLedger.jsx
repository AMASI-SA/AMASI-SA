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
    const [perCompany, setPerCompany] = useState([]);
    const [warnings, setWarnings] = useState([]);
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
            setPerCompany(r.data?.per_company || []);
            setWarnings(r.data?.warnings || []);
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
            "Status", "Shipping Cost", "Prepaid?", "COD Amount",
            "COD Fee Net", "COD Fee VAT", "COD Fee Total",
            "Net Due", "Settlement",
        ];
        const lines = [headers.join(",")];
        for (const r of rows) {
            lines.push([
                r.order_id, r.order_date, r.shipping_company,
                r.payment_mode, `"${(r.payment_method || "").replace(/"/g, '""')}"`,
                `"${r.order_status}"`,
                r.shipping_cost, r.prepaid_shipping ? "yes" : "no",
                r.cod_amount, r.cod_fee_net, r.cod_fee_vat, r.cod_fee,
                r.net_due, r.settlement_status,
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
        <div className={`rounded-xl border-2 px-2.5 py-2 sm:p-3 bg-${color}-50 border-${color}-200 min-w-0`}>
            <div className="text-[10px] sm:text-[11px] text-slate-600 font-bold leading-tight truncate">{label}</div>
            <div className={`flex items-baseline gap-1 mt-0.5 text-${color}-800`}>
                <span className="text-sm sm:text-base lg:text-lg font-extrabold num truncate min-w-0 leading-tight"
                      title={typeof value === "number" && suffix === "ر.س" ? fmt(value) : String(value)}>
                    {typeof value === "number" && suffix === "ر.س" ? fmt(value) : intf(value)}
                </span>
                {suffix ? <span className="text-[9px] sm:text-[10px] font-bold shrink-0">{suffix}</span> : null}
            </div>
        </div>
    );

    return (
        <div className="p-2 sm:p-4 lg:p-6 max-w-[1500px] mx-auto" data-testid="shipping-ledger-page">
            <div className="bg-white rounded-2xl shadow-lg p-3 sm:p-4 lg:p-6">
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
                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-1.5 sm:gap-2 mb-4">
                    <SummaryCard label="عدد الطلبات الموصلة" value={totals.delivered_count || 0} suffix="" color="emerald" />
                    <SummaryCard label="إجمالي سعر الشحن (بدون الضريبة)" value={totals.total_shipping_base} color="slate" />
                    <SummaryCard label="إجمالي ضريبة الشحن" value={totals.total_shipping_tax} color="violet" />
                    <SummaryCard label="إجمالي تكلفة الشحن (شامل الضريبة)" value={totals.total_shipping_cost} color="emerald" />
                    <SummaryCard label="إجمالي COD" value={totals.total_cod} color="amber" />
                    <SummaryCard label="إجمالي رسوم COD (شامل الضريبة)" value={totals.total_cod_fees} color="rose" />
                    <SummaryCard label="إجمالي المسوى" value={totals.total_settled} color="emerald" />
                    <SummaryCard label="إجمالي المتبقي" value={totals.total_unsettled} color="amber" />
                </div>

                {Number(totals.cod_fee_rules_needing_review || 0) > 0 && (
                    <div className="mb-4 rounded-lg border-2 border-rose-300 bg-rose-50 p-3 text-xs font-bold text-rose-800" data-testid="cod-fee-tier-warning">
                        توجد {totals.cod_fee_rules_needing_review} شحنات COD لا تغطي مبالغها أي شريحة عمولة. راجع حدود الشرائح قبل اعتماد التسوية.
                    </div>
                )}

                {/* Warning banner — companies using Salla fallback price */}
                {warnings && warnings.length > 0 && (
                    <div className="mb-4 border-2 border-amber-400 bg-amber-50 rounded-lg p-3"
                         data-testid="shipping-cost-warning">
                        <div className="flex items-start gap-2">
                            <span className="text-2xl leading-none">⚠️</span>
                            <div className="flex-1">
                                <p className="font-extrabold text-amber-900 text-sm mb-1">
                                    تنبيه: شركات شحن تعتمد حالياً على سعر سلة
                                </p>
                                <ul className="text-xs text-amber-900 space-y-1 list-disc list-inside font-medium leading-relaxed">
                                    {warnings.map((w, i) => (
                                        <li key={i} data-testid={`warning-${w.shipping_company}`}>
                                            {w.message}
                                            <span className="ms-1 text-amber-700">
                                                ({intf(w.orders_affected)} طلب متأثر)
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                                <a
                                    href="/shipping/settings"
                                    className="inline-block mt-2 text-xs font-extrabold text-amber-900 underline"
                                    data-testid="warning-goto-settings"
                                >
                                    الانتقال إلى إعدادات شركات الشحن ↗
                                </a>
                            </div>
                        </div>
                    </div>
                )}

                {/* Per-company shipping cost breakdown — base / tax / total */}
                {perCompany.length > 0 && (
                    <div className="mb-4 bg-white border border-slate-200 rounded-xl p-3"
                         data-testid="per-company-cost-summary">
                        <h3 className="text-sm font-extrabold text-slate-800 mb-2">
                            تفاصيل تكاليف الشحن لكل شركة (للطلبات المعروضة)
                        </h3>
                        <div className="overflow-x-auto">
                            <table className="w-full text-xs">
                                <thead className="bg-slate-100 text-slate-700">
                                    <tr>
                                        <th className="text-right p-2 font-extrabold">الشركة</th>
                                        <th className="text-center p-2 font-extrabold">عدد الشحنات</th>
                                        <th className="text-left p-2 font-extrabold">سعر الوحدة<br/><span className="text-[10px] text-slate-500">(بدون الضريبة)</span></th>
                                        <th className="text-left p-2 font-extrabold">ضريبة الوحدة<br/><span className="text-[10px] text-slate-500">(VAT)</span></th>
                                        <th className="text-left p-2 font-extrabold">إجمالي الوحدة<br/><span className="text-[10px] text-slate-500">(سعر + ضريبة)</span></th>
                                        <th className="text-left p-2 font-extrabold">الإجمالي</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {perCompany.map((c) => (
                                        <tr key={c.shipping_company}
                                            className={`border-t border-slate-100 ${c.uses_salla_fallback ? "bg-amber-50/50" : ""}`}
                                            data-testid={`pc-${c.shipping_company}`}>
                                            <td className="p-2 font-bold">
                                                {c.shipping_company}
                                                {c.uses_salla_fallback && (
                                                    <span className="ms-2 text-[9px] px-1.5 py-0.5 rounded bg-amber-200 text-amber-900 font-extrabold">
                                                        من سلة (مؤقت)
                                                    </span>
                                                )}
                                            </td>
                                            <td className="p-2 text-center font-bold">{intf(c.orders_count)}</td>
                                            <td className="p-2 text-left num">{fmt(c.cost_per_unit)}</td>
                                            <td className="p-2 text-left num text-violet-700">
                                                {fmt(c.tax_per_unit)}
                                                {c.vat_rate > 0 && (
                                                    <span className="ms-1 text-[10px] text-slate-400">
                                                        ({(c.vat_rate*100).toFixed(0)}%)
                                                    </span>
                                                )}
                                            </td>
                                            <td className="p-2 text-left num font-extrabold text-emerald-700">
                                                {fmt(c.total_per_unit)}
                                            </td>
                                            <td className="p-2 text-left num font-extrabold">{fmt(c.total_shipping_cost)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                                {/* Footer totals */}
                                <tfoot>
                                    <tr className="bg-slate-50 border-t-2 border-slate-300 font-extrabold">
                                        <td className="p-2 text-right">المجموع</td>
                                        <td className="p-2 text-center">{intf(totals.delivered_count)}</td>
                                        <td className="p-2 text-left num">—</td>
                                        <td className="p-2 text-left num text-violet-800">{fmt(totals.total_shipping_tax)}</td>
                                        <td className="p-2 text-left num text-emerald-800">—</td>
                                        <td className="p-2 text-left num">{fmt(totals.total_shipping_cost)}</td>
                                    </tr>
                                </tfoot>
                            </table>
                        </div>
                        <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">
                            القاعدة الموحدة: <strong>إجمالي تكلفة الشحنة = سعر الشحنة + ضريبة الشحنة</strong>.
                            الضريبة محسوبة بناءً على نسبة VAT المضبوطة لكل شركة في صفحة الإعدادات.
                        </p>
                    </div>
                )}

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
                                <th className="text-left p-2 font-extrabold">سعر الشحن<br/><span className="text-[9px] text-slate-500">(بدون ضريبة)</span></th>
                                <th className="text-left p-2 font-extrabold">ضريبة الشحن</th>
                                <th className="text-left p-2 font-extrabold">إجمالي الشحن<br/><span className="text-[9px] text-slate-500">(سعر + ضريبة)</span></th>
                                <th className="text-left p-2 font-extrabold text-slate-500">الفرق مع سلة<br/><span className="text-[9px] text-slate-400 font-normal">(للمراجعة فقط)</span></th>
                                <th className="text-center p-2 font-extrabold">مصدر التكلفة</th>
                                <th className="text-left p-2 font-extrabold">COD</th>
                                <th className="text-left p-2 font-extrabold">رسوم COD</th>
                                <th className="text-left p-2 font-extrabold">صافي مستحق</th>
                                <th className="text-center p-2 font-extrabold">التسوية</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && (
                                <tr><td colSpan={15} className="text-center p-6 text-slate-500">جاري التحميل…</td></tr>
                            )}
                            {!loading && rows.length === 0 && (
                                <tr><td colSpan={15} className="text-center p-6 text-slate-500"
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
                                        {fmt(r.shipping_base)}
                                        {r.prepaid_shipping && (
                                            <div className="text-[9px] text-emerald-700 font-bold">مدفوع مسبقاً</div>
                                        )}
                                    </td>
                                    <td className="p-2 text-left num text-violet-700">
                                        {fmt(r.shipping_tax)}
                                    </td>
                                    <td className="p-2 text-left num font-extrabold text-emerald-700">
                                        {fmt(r.shipping_cost)}
                                    </td>
                                    <td className="p-2 text-left num">
                                        {r.diff_vs_salla === null || r.diff_vs_salla === undefined ? (
                                            <span className="text-slate-400">—</span>
                                        ) : Math.abs(r.diff_vs_salla) < 0.01 ? (
                                            <span className="text-emerald-600 font-bold">0.00</span>
                                        ) : r.diff_vs_salla > 0 ? (
                                            <span className="text-sky-600 font-bold" title="إعدادات النظام أعلى من سلة">
                                                +{fmt(r.diff_vs_salla)}
                                            </span>
                                        ) : (
                                            <span className="text-rose-600 font-bold" title="إعدادات النظام أقل من سلة">
                                                {fmt(r.diff_vs_salla)}
                                            </span>
                                        )}
                                        {r.salla_shipping_native > 0 && (
                                            <div className="text-[9px] text-slate-400 font-mono">
                                                سلة: {fmt(r.salla_shipping_native)}
                                            </div>
                                        )}
                                    </td>
                                    <td className="p-2 text-center"
                                        data-testid={`ledger-cost-source-${r.order_id}`}>
                                        {r.shipping_cost_source === "salla" && (
                                            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-sky-100 text-sky-800">
                                                سلة
                                            </span>
                                        )}
                                        {r.shipping_cost_source === "company_settings" && (
                                            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-violet-100 text-violet-800">
                                                إعدادات الشركة
                                            </span>
                                        )}
                                        {(!r.shipping_cost_source || r.shipping_cost_source === "none") && (
                                            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-rose-100 text-rose-800">
                                                غير متوفر
                                            </span>
                                        )}
                                    </td>
                                    <td className="p-2 text-left num">{fmt(r.cod_amount)}</td>
                                    <td className="p-2 text-left num text-rose-700">
                                        <div className="font-extrabold">{fmt(r.cod_fee)}</div>
                                        <div className="text-[9px] text-slate-500">عمولة {fmt(r.cod_fee_net)} + ضريبة {fmt(r.cod_fee_vat)}</div>
                                        {r.cod_fee_rule_needs_review && <div className="text-[9px] font-bold text-rose-700">الشريحة غير مغطاة</div>}
                                    </td>
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
