// Iter-207d — Excluded Orders Modal
//
// Tiny drill-down dialog used by both ProfitSummaryCard and
// UnifiedPaymentGatewaysCard. When the merchant clicks the
// "+X معلَّق/ملغى" badge, this modal opens and lists exactly which
// orders are excluded from the dashboard's main figures, so they can
// be reviewed manually against the Salla platform.

import { useEffect, useState } from "react";
import { X, Warning } from "@phosphor-icons/react";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";

const STATUS_TONE = (s) => {
    const lc = (s || "").toLowerCase();
    if (lc.includes("ملغ") || lc.includes("cancel")) return "rose";
    return "amber";  // pending / on-hold / etc
};

export default function ExcludedOrdersModal({ open, onClose,
    fromDate, toDate, periodLabel }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!open) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                const { data } = await api.get(
                    "/dashboard/excluded-orders",
                    { params: { from_date: fromDate, to_date: toDate } });
                if (!cancelled) setData(data);
            } catch (_) {
                if (!cancelled) setData(null);
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [open, fromDate, toDate]);

    if (!open) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            onClick={onClose}
            data-testid="excluded-orders-modal">
            <div className="bg-white rounded-2xl shadow-2xl max-w-4xl w-full max-h-[85vh] overflow-hidden flex flex-col"
                onClick={(e) => e.stopPropagation()}>
                {/* Header */}
                <div className="px-5 py-3 bg-gradient-to-l from-amber-50 to-rose-50 border-b border-amber-200 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Warning size={20} weight="duotone" className="text-amber-700" />
                        <div>
                            <h3 className="font-extrabold text-slate-900" style={{ fontFamily: "Tajawal" }}>
                                الطلبات المستثناة من التقارير المالية
                            </h3>
                            <p className="text-[11px] text-slate-500 mt-0.5">
                                هذه الطلبات تظهر في منصة سلة لكن لا يحتسبها النظام في الأرباح والتكاليف والمركز المالي.
                                {periodLabel && (
                                    <span className="ms-2 inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-sky-50 text-sky-700 border border-sky-200">
                                        {periodLabel}
                                    </span>
                                )}
                            </p>
                        </div>
                    </div>
                    <button onClick={onClose}
                        className="w-8 h-8 rounded-lg hover:bg-amber-100 flex items-center justify-center"
                        data-testid="excluded-orders-modal-close">
                        <X size={18} weight="bold" className="text-slate-600" />
                    </button>
                </div>

                {/* Summary strip */}
                {data && (
                    <div className="px-5 py-3 bg-amber-50/40 border-b border-amber-100 grid grid-cols-2 gap-3">
                        <div>
                            <div className="text-[10px] text-slate-500 font-bold">عدد الطلبات المستثناة</div>
                            <div className="num text-xl font-extrabold text-amber-700">
                                {formatInt(data.orders_count)}
                            </div>
                        </div>
                        <div>
                            <div className="text-[10px] text-slate-500 font-bold">قيمتها الإجمالية</div>
                            <div className="num text-xl font-extrabold text-amber-700">
                                {formatMoney(data.total_amount)} <span className="text-xs">ر.س</span>
                            </div>
                        </div>
                    </div>
                )}

                {/* Body */}
                <div className="flex-1 overflow-auto p-3">
                    {loading && (
                        <div className="text-center py-10 text-slate-400 text-sm">
                            جاري التحميل...
                        </div>
                    )}
                    {!loading && data && data.orders_count === 0 && (
                        <div className="text-center py-10 text-slate-400 text-sm">
                            لا توجد طلبات مستثناة في الفترة المحددة.
                        </div>
                    )}
                    {!loading && data && data.orders.length > 0 && (
                        <table className="w-full text-sm">
                            <thead className="text-slate-500 border-b-2 border-slate-200">
                                <tr>
                                    <th className="text-right py-2 px-2 font-bold">رقم الطلب</th>
                                    <th className="text-right py-2 px-2 font-bold">التاريخ</th>
                                    <th className="text-right py-2 px-2 font-bold">الحالة</th>
                                    <th className="text-right py-2 px-2 font-bold">طريقة الدفع</th>
                                    <th className="text-right py-2 px-2 font-bold">العميل</th>
                                    <th className="text-left py-2 px-2 font-bold">القيمة (ر.س)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.orders.map((o, idx) => {
                                    const tone = STATUS_TONE(o.order_status);
                                    return (
                                        <tr key={o.order_number || idx}
                                            className="border-b border-slate-100 hover:bg-slate-50"
                                            data-testid={`excluded-row-${o.order_number}`}>
                                            <td className="py-2 px-2 font-mono text-xs text-slate-700">
                                                {o.order_number || "—"}
                                            </td>
                                            <td className="py-2 px-2 text-slate-600 text-xs">
                                                {o.order_date}
                                            </td>
                                            <td className="py-2 px-2">
                                                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-${tone}-100 text-${tone}-800`}>
                                                    {o.order_status || "بدون حالة"}
                                                </span>
                                            </td>
                                            <td className="py-2 px-2 text-xs text-slate-600">{o.payment_method}</td>
                                            <td className="py-2 px-2 text-xs text-slate-600 truncate max-w-[180px]">
                                                {o.customer_name || "—"}
                                            </td>
                                            <td className="text-left py-2 px-2 num font-bold text-slate-900">
                                                {formatMoney(o.total_amount)}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    )}
                </div>

                {/* Footer */}
                <div className="px-5 py-3 bg-slate-50 border-t border-slate-200 text-[11px] text-slate-500">
                    💡 إذا رأيت طلباً بالخطأ في هذه القائمة، تحقق من إعدادات
                    {'"الحالات المعتمدة في التقارير"'} (Settings → Report-included statuses)
                    أو راجع الـ Status Policy.
                </div>
            </div>
        </div>
    );
}
