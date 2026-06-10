/**
 * PendingOrdersCard (Iter-83)
 * ---------------------------
 * Separate callout that surfaces orders the merchant marked as
 * "pending" via Settings → حالات الطلبات. These are NOT counted in
 * the confirmed net (assets), but they're tracked so the merchant
 * knows what's in-flight.
 *
 * Reads from /api/payment-gateway-metrics (single source of truth)
 * and shows the per-gateway pending breakdown.
 */
import { useEffect, useState } from "react";
import { Hourglass } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";

export default function PendingOrdersCard({ qs = "", testid = "pending-orders-card" }) {
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
            <div className="rounded-xl border border-amber-200 bg-amber-50/40 p-5 animate-pulse" data-testid={`${testid}-loading`}>
                <div className="h-5 w-48 bg-amber-100 rounded mb-3" />
                <div className="h-16 bg-amber-100/50 rounded" />
            </div>
        );
    }
    if (!data) return null;

    const totalsPending = Number(data.totals?.pending_gross || 0);
    const totalsCount = Number(data.totals?.pending_orders_count || 0);

    if (totalsPending === 0 && totalsCount === 0) {
        return (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50/40 p-5 flex items-center gap-3" data-testid={`${testid}-empty`}>
                <Hourglass size={22} weight="duotone" className="text-emerald-700" />
                <div className="text-sm">
                    <div className="font-bold text-emerald-800">لا توجد طلبات معلَّقة</div>
                    <div className="text-xs text-emerald-700/80">كل الطلبات مُؤكَّدة أو مُسترجعة أو مُلغاة.</div>
                </div>
            </div>
        );
    }

    const pendingRows = (data.rows || [])
        .filter((r) => Number(r.pending_gross || 0) > 0 || Number(r.pending_orders_count || 0) > 0)
        .sort((a, b) => b.pending_gross - a.pending_gross);

    return (
        <div className="rounded-xl border-2 border-amber-200 bg-amber-50/40 p-5" data-testid={testid}>
            <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
                <div className="flex items-start gap-3 min-w-0">
                    <div className="w-10 h-10 rounded-lg bg-amber-100 text-amber-700 flex items-center justify-center shrink-0">
                        <Hourglass size={22} weight="duotone" />
                    </div>
                    <div className="min-w-0">
                        <h3 className="text-lg font-bold text-amber-900" style={{ fontFamily: "Tajawal" }}>
                            طلبات معلَّقة / قيد التحصيل
                        </h3>
                        <p className="text-xs text-amber-800/80 mt-0.5">
                            هذه الطلبات لم تُعتمد بعد ضمن الأصول.{" "}
                            <Link to="/settings" className="underline font-bold" data-testid={`${testid}-settings-link`}>
                                إدارة فئات الحالات
                            </Link>
                        </p>
                    </div>
                </div>
                <div className="text-end shrink-0">
                    <div className="text-[11px] text-amber-700">الإجمالي المعلَّق (ر.س)</div>
                    <div className="num text-2xl sm:text-3xl font-extrabold text-amber-800" data-testid={`${testid}-total`}>
                        {formatMoney(totalsPending)}
                    </div>
                    <div className="text-[11px] text-amber-700/80 num">{formatInt(totalsCount)} طلب</div>
                </div>
            </div>

            <div className="overflow-x-auto">
                <table className="mezan-table w-full text-sm" data-testid={`${testid}-table`}>
                    <thead className="text-xs text-amber-800/80 bg-amber-100/50">
                        <tr>
                            <th className="text-right px-3 py-1.5 font-semibold">البوابة</th>
                            <th className="text-right px-3 py-1.5 font-semibold">عدد الطلبات</th>
                            <th className="text-right px-3 py-1.5 font-semibold">القيمة المعلَّقة</th>
                            <th className="text-right px-3 py-1.5 font-semibold">% من إجمالي البوابة</th>
                        </tr>
                    </thead>
                    <tbody>
                        {pendingRows.map((r) => {
                            const ratio = (r.gross + r.pending_gross + r.refund_total) > 0
                                ? (r.pending_gross / (r.gross + r.pending_gross + r.refund_total) * 100)
                                : 0;
                            return (
                                <tr key={r.key} className="border-t border-amber-200" data-testid={`${testid}-row-${r.key}`}>
                                    <td className="px-3 py-1.5 font-bold">{r.name_ar}</td>
                                    <td className="px-3 py-1.5 num">{formatInt(r.pending_orders_count)}</td>
                                    <td className="px-3 py-1.5 num font-bold text-amber-800">{formatMoney(r.pending_gross)}</td>
                                    <td className="px-3 py-1.5 num text-amber-700/80">{ratio.toFixed(1)}%</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
