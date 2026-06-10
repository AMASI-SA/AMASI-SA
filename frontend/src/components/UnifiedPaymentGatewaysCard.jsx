/**
 * Unified Payment Gateways Card (Iter-81)
 * ---------------------------------------
 * Reads from the CENTRAL /api/payment-gateway-metrics endpoint so the
 * exact same per-gateway numbers (gross / fees / net / orders) appear
 * on Dashboard, Reports, Accounts, and Reconciliation.
 *
 * Priority chain (server-side): actual settlement-file → estimated rates.
 * Use the `qs` prop to pass any date filters (e.g. ?from_date=...&to_date=...).
 */
import { useEffect, useState } from "react";
import { CreditCard, Info, Hourglass } from "@phosphor-icons/react";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";

export default function UnifiedPaymentGatewaysCard({ qs = "", testid = "unified-gateways-card", periodLabel = null }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                const { data } = await api.get(`/payment-gateway-metrics${qs ? "?" + qs : ""}`);
                if (!cancelled) setData(data);
            } catch {
                if (!cancelled) setData(null);
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [qs]);

    if (loading) {
        return (
            <div className="rounded-xl border border-border bg-white p-6 animate-pulse" data-testid={`${testid}-loading`}>
                <div className="h-5 w-48 bg-slate-200 rounded mb-3" />
                <div className="h-24 bg-slate-100 rounded" />
            </div>
        );
    }

    const rows = (data?.rows || []).filter((r) => r.key !== "_other");
    if (!data) return null;
    const t = data.totals || {};

    if (rows.length === 0) {
        return (
            <div className="rounded-xl border border-dashed border-border bg-white p-6 text-center" data-testid={testid}>
                <div data-testid={`${testid}-empty`}>
                    <CreditCard size={28} weight="duotone" className="text-muted-foreground mx-auto mb-2" />
                    <div className="font-bold text-sm" style={{ fontFamily: "Tajawal" }}>بوابات الدفع — مصدر موحَّد</div>
                    <div className="text-xs text-muted-foreground mt-1">
                        لا توجد طلبات في هذه الفترة لعرض تفاصيل البوابات.
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="rounded-xl border border-border bg-white p-5 sm:p-6" data-testid={testid}>
            <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                <div className="flex items-start gap-3 min-w-0">
                    <div className="w-10 h-10 rounded-lg bg-sky-100 text-sky-700 flex items-center justify-center shrink-0">
                        <CreditCard size={22} weight="duotone" />
                    </div>
                    <div className="min-w-0">
                        <h3 className="text-lg sm:text-xl font-bold flex items-center gap-2 flex-wrap" style={{ fontFamily: "Tajawal" }}>
                            <span>بوابات الدفع — مصدر موحَّد</span>
                            {periodLabel && (
                                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-sky-50 text-sky-700 border border-sky-200" data-testid={`${testid}-period`}>
                                    {periodLabel}
                                </span>
                            )}
                        </h3>
                        <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1 flex-wrap">
                            <Info size={12} />
                            نفس الأرقام تظهر في التقارير، الحسابات، والمطابقة. الفعلي من التسويات يطغى على المُقدَّر.
                        </p>
                    </div>
                </div>
                <div className="text-end shrink-0">
                    <div className="text-[11px] text-muted-foreground">الصافي المؤكَّد (ر.س)</div>
                    <div className="num text-2xl sm:text-3xl font-extrabold text-brand" data-testid={`${testid}-net-total`}>
                        {formatMoney(t.net)}
                    </div>
                    {Number(t.pending_gross || 0) > 0 && (
                        <div className="text-[11px] text-amber-700 mt-1 inline-flex items-center gap-1 justify-end" data-testid={`${testid}-pending-hint`}>
                            <Hourglass size={11} weight="duotone" />
                            معلَّق إضافي: <span className="num font-bold">{formatMoney(t.pending_gross)}</span> ({formatInt(t.pending_orders_count)} طلب)
                        </div>
                    )}
                </div>
            </div>

            <div className="overflow-x-auto">
                <table
                    className="mezan-table w-full text-right text-sm border-collapse min-w-[980px]
                        [&_th]:px-3 [&_th]:border-s [&_th]:border-border
                        [&_td]:px-3 [&_td]:border-s [&_td]:border-border
                        [&_th:first-child]:border-s-0 [&_td:first-child]:border-s-0"
                    data-testid={`${testid}-table`}
                >
                    <thead className="text-muted-foreground bg-accent/40 border-b-2 border-border">
                        <tr>
                            <th className="py-3 font-semibold">البوابة</th>
                            <th className="py-3 font-semibold">الطلبات</th>
                            <th className="py-3 font-semibold">الإجمالي</th>
                            <th className="py-3 font-semibold">الرسوم</th>
                            <th className="py-3 font-semibold">ض. الرسوم</th>
                            <th className="py-3 font-semibold">المرتجع</th>
                            <th className="py-3 font-semibold">معلَّق</th>
                            <th className="py-3 font-semibold">الصافي</th>
                            <th className="py-3 font-semibold">المصدر</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => {
                            const isActual = (r.actual_orders_count || 0) > 0;
                            const cov = isActual ? Number(r.coverage_pct || 0) : 0;
                            const pending = Number(r.pending_gross || 0);
                            const pendingCount = Number(r.pending_orders_count || 0);
                            return (
                                <tr key={r.key} className="border-b border-border last:border-0 hover:bg-accent/30 transition-colors" data-testid={`${testid}-row-${r.key}`}>
                                    <td className="py-2.5 font-semibold">{r.name_ar}</td>
                                    <td className="py-2.5 num">{formatInt(r.orders_count)}</td>
                                    <td className="py-2.5 num font-bold">{formatMoney(r.gross)}</td>
                                    <td className="py-2.5 num text-rose-700">{formatMoney(r.fees)}</td>
                                    <td className="py-2.5 num text-rose-700/70">{formatMoney(r.fees_vat)}</td>
                                    <td className={`py-2.5 num ${r.refund_total > 0 ? "text-amber-700" : "text-muted-foreground"}`}>
                                        {formatMoney(r.refund_total)}
                                    </td>
                                    <td className={`py-2.5 num ${pending > 0 ? "text-amber-700" : "text-muted-foreground"}`} title={pending > 0 ? `${pendingCount} طلب بحالة معلَّقة` : ""}>
                                        {formatMoney(pending)}
                                    </td>
                                    <td className="py-2.5 num font-extrabold text-emerald-700">{formatMoney(r.net)}</td>
                                    <td className="py-2.5">
                                        {isActual ? (
                                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800" title={`${cov.toFixed(1)}% من الطلبات تم مطابقتها بملف تسوية`}>
                                                فعلي {cov.toFixed(0)}%
                                            </span>
                                        ) : (
                                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-700">
                                                مُقدَّر
                                            </span>
                                        )}
                                    </td>
                                </tr>
                            );
                        })}
                        <tr className="bg-accent/30 font-bold border-t-2 border-border">
                            <td className="py-2.5">الإجمالي</td>
                            <td className="py-2.5 num">{formatInt(t.orders_count)}</td>
                            <td className="py-2.5 num">{formatMoney(t.gross)}</td>
                            <td className="py-2.5 num text-rose-700">{formatMoney(t.fees)}</td>
                            <td className="py-2.5 num text-rose-700/70">{formatMoney(t.fees_vat)}</td>
                            <td className="py-2.5 num text-amber-700">{formatMoney(t.refund_total)}</td>
                            <td className="py-2.5 num text-amber-700">{formatMoney(t.pending_gross)}</td>
                            <td className="py-2.5 num text-emerald-700">{formatMoney(t.net)}</td>
                            <td className="py-2.5" />
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    );
}
