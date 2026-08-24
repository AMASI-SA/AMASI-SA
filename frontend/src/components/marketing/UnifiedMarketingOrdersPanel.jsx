import { useMemo, useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";

function number(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "—";
}

function money(value) {
    if (value?.amount === null || value?.amount === undefined || value?.amount === "") return "—";
    const parsed = Number(value.amount);
    return Number.isFinite(parsed)
        ? `${parsed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${value.currency || ""}`.trim()
        : "—";
}

export default function UnifiedMarketingOrdersPanel({ report, campaignId = null }) {
    const [query, setQuery] = useState("");
    const orders = report?.orders || [];
    const summary = report?.order_summary || {};
    const rows = useMemo(() => {
        const needle = query.trim().toLocaleLowerCase();
        return orders.filter((row) => {
            if (campaignId && row.campaign_id !== campaignId) return false;
            if (!needle) return true;
            return [
                row.order_number,
                row.campaign_name,
                row.campaign_id,
                row.status,
                row.source_label,
            ].some((value) => String(value || "").toLocaleLowerCase().includes(needle));
        });
    }, [campaignId, orders, query]);

    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white" data-testid="unified-marketing-orders-panel">
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 p-4">
                <div>
                    <h3 className="text-lg font-black text-slate-950">طلبات سلة المرتبطة بحملات Snapchat</h3>
                    <p className="mt-1 text-xs font-bold text-slate-500">
                        مطابقة صريحة بمعرف الحملة أو باسم Snapchat الفريد؛ لا يتم توزيع الطلبات المباشرة أو الملتبسة.
                    </p>
                </div>
                <label className="relative block min-w-[260px]">
                    <MagnifyingGlass size={17} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="رقم الطلب أو الحملة"
                        className="h-10 w-full rounded-xl border border-slate-200 bg-slate-50 pr-9 pl-3 text-sm font-bold outline-none focus:border-emerald-400"
                    />
                </label>
            </header>
            <div className="grid gap-3 border-b border-slate-200 p-4 sm:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-xl bg-emerald-50 p-3"><div className="text-xs font-black text-emerald-700">طلبات الحملات المالية</div><div className="mt-2 text-2xl font-black">{number(summary.matched_financial_orders)}</div></div>
                <div className="rounded-xl bg-blue-50 p-3"><div className="text-xs font-black text-blue-700">مبيعات الحملات من سلة</div><div className="mt-2 text-2xl font-black">{money(summary.matched_financial_revenue)}</div></div>
                <div className="rounded-xl bg-violet-50 p-3"><div className="text-xs font-black text-violet-700">مشتريات Snapchat</div><div className="mt-2 text-2xl font-black">{number(summary.platform_attributed_conversions)}</div></div>
                <div className="rounded-xl bg-amber-50 p-3"><div className="text-xs font-black text-amber-700">طلبات غير منسوبة لحملة</div><div className="mt-2 text-2xl font-black">{number(summary.unmatched_orders)}</div></div>
            </div>
            <div className="max-h-[480px] overflow-auto">
                <table className="min-w-[980px] w-full text-right text-xs">
                    <thead className="sticky top-0 bg-slate-50 text-slate-600">
                        <tr>
                            <th className="px-4 py-3 font-black">رقم الطلب</th>
                            <th className="px-4 py-3 font-black">الوقت المحلي</th>
                            <th className="px-4 py-3 font-black">الحملة</th>
                            <th className="px-4 py-3 font-black">المبلغ</th>
                            <th className="px-4 py-3 font-black">الحالة</th>
                            <th className="px-4 py-3 font-black">المطابقة</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {rows.map((row, index) => (
                            <tr key={`${row.order_number || "order"}-${index}`}>
                                <td className="px-4 py-3 font-mono font-black">{row.order_number || "—"}</td>
                                <td className="px-4 py-3 font-mono">{row.local_created_at || row.local_date || "—"}</td>
                                <td className="px-4 py-3"><div className="font-black">{row.campaign_name || "—"}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{row.campaign_id || "—"}</div></td>
                                <td className="px-4 py-3 font-mono font-black">{money(row.amount)}</td>
                                <td className="px-4 py-3">{row.status || "—"}</td>
                                <td className="px-4 py-3">{row.match_method || row.classification || "—"}</td>
                            </tr>
                        ))}
                        {!rows.length && <tr><td colSpan={6} className="p-8 text-center font-black text-slate-400">لا توجد طلبات مطابقة للفلتر الحالي.</td></tr>}
                    </tbody>
                </table>
            </div>
        </section>
    );
}
