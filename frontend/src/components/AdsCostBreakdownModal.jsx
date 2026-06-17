/**
 * AdsCostBreakdownModal — drill-down for the
 * "إجمالي تكاليف الإعلانات" line on the ProfitSummaryCard.
 *
 * Read-only. Fetches `/api/dashboard/ads-cost-breakdown?from=…&to=…`
 * and lists every ad-spend ledger entry within the displayed period,
 * grouped by ad-provider with per-row details (account, amount,
 * source, covered-from-balance, created-debt).
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const fmtSar = (v) =>
    Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const fmtDate = (d) => d || "—";

const PROVIDER_LABEL = {
    meta: "Meta (Facebook/Instagram)",
    facebook: "Facebook",
    instagram: "Instagram",
    snapchat: "Snapchat",
    snap: "Snapchat",
    tiktok: "TikTok",
    google: "Google Ads",
    twitter: "Twitter / X",
};
const provLabel = (p) => PROVIDER_LABEL[p?.toLowerCase()] || p || "—";

export default function AdsCostBreakdownModal({
    open, onClose, fromDate, toDate,
}) {
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState(null);

    useEffect(() => {
        if (!open) return;
        let cancel = false;
        (async () => {
            setLoading(true);
            try {
                const params = new URLSearchParams();
                if (fromDate) params.set("from_date", fromDate);
                if (toDate) params.set("to_date", toDate);
                const { data } = await api.get(
                    `/dashboard/ads-cost-breakdown?${params.toString()}`,
                );
                if (!cancel) setData(data);
            } catch (e) {
                toast.error(
                    e?.response?.data?.detail
                    || "فشل تحميل تفاصيل تكاليف الإعلانات",
                );
            } finally {
                if (!cancel) setLoading(false);
            }
        })();
        return () => { cancel = true; };
    }, [open, fromDate, toDate]);

    if (!open) return null;

    const items = data?.items || [];
    const byProvider = data?.by_provider || {};

    return (
        <div
            className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
            onClick={onClose}
            data-testid="ads-cost-breakdown-overlay"
        >
            <div
                className="bg-white rounded-xl max-w-5xl w-full max-h-[90vh] flex flex-col"
                onClick={(e) => e.stopPropagation()}
                dir="rtl"
                data-testid="ads-cost-breakdown-modal"
            >
                {/* Header */}
                <div className="px-5 py-4 border-b bg-rose-50 rounded-t-xl flex items-center justify-between">
                    <div>
                        <h2 className="text-lg font-extrabold text-rose-800">
                            📣 تفاصيل تكاليف الإعلانات
                        </h2>
                        <p className="text-xs text-rose-700 mt-1">
                            الفترة:{" "}
                            <span className="font-mono">
                                {fmtDate(fromDate)} → {fmtDate(toDate)}
                            </span>
                        </p>
                    </div>
                    <button
                        onClick={onClose}
                        className="px-3 py-1 rounded bg-white hover:bg-rose-100 text-rose-700 font-bold border border-rose-300"
                        data-testid="ads-cost-breakdown-close-btn"
                    >
                        إغلاق ✕
                    </button>
                </div>

                {/* Body */}
                <div className="flex-1 overflow-y-auto p-5 space-y-4">
                    {loading && (
                        <p className="text-center text-gray-500 py-8">
                            جارٍ تحميل القيود...
                        </p>
                    )}

                    {!loading && data && (
                        <>
                            {/* Summary */}
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                <Stat
                                    label="إجمالي تكاليف الإعلانات"
                                    value={`${fmtSar(data.total_amount)} ر.س`}
                                    big
                                    testid="ads-total-amount"
                                />
                                <Stat
                                    label="عدد القيود"
                                    value={data.total_entries}
                                    testid="ads-total-entries"
                                />
                                <Stat
                                    label="عدد المنصات"
                                    value={Object.keys(byProvider).length}
                                    testid="ads-providers-count"
                                />
                                <Stat
                                    label="عدد الحسابات الإعلانية"
                                    value={
                                        Object.keys(data.by_account || {}).length
                                    }
                                    testid="ads-accounts-count"
                                />
                            </div>

                            {/* By provider */}
                            {Object.keys(byProvider).length > 0 && (
                                <div className="bg-gray-50 rounded-lg p-4">
                                    <h3 className="text-sm font-bold mb-2 text-gray-700">
                                        التوزيع حسب المنصة
                                    </h3>
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
                                        {Object.entries(byProvider)
                                            .sort(([, a], [, b]) => b - a)
                                            .map(([prov, amt]) => (
                                                <div
                                                    key={prov}
                                                    className="flex items-center justify-between bg-white rounded px-3 py-2 border"
                                                    data-testid={`ads-by-provider-${prov}`}
                                                >
                                                    <span className="font-semibold">
                                                        {provLabel(prov)}
                                                    </span>
                                                    <span className="text-rose-700 font-bold font-mono">
                                                        {fmtSar(amt)} ر.س
                                                    </span>
                                                </div>
                                            ))}
                                    </div>
                                </div>
                            )}

                            {/* Itemised list */}
                            <div className="bg-white rounded-lg border">
                                <div className="px-4 py-2 bg-gray-100 border-b">
                                    <h3 className="text-sm font-bold">
                                        قائمة القيود ({items.length})
                                    </h3>
                                </div>
                                <div className="overflow-x-auto">
                                    <table className="min-w-full text-xs">
                                        <thead className="bg-gray-50">
                                            <tr className="text-right">
                                                <th className="p-2">التاريخ</th>
                                                <th className="p-2">الحساب الإعلاني</th>
                                                <th className="p-2">المنصة</th>
                                                <th className="p-2">المبلغ</th>
                                                <th className="p-2">من الرصيد</th>
                                                <th className="p-2">مديونية مُنشأة</th>
                                                <th className="p-2">المصدر</th>
                                                <th className="p-2">الوصف</th>
                                            </tr>
                                        </thead>
                                        <tbody data-testid="ads-cost-items-table">
                                            {items.map((it) => (
                                                <tr
                                                    key={it.id}
                                                    className="border-t hover:bg-gray-50"
                                                    data-testid={`ads-cost-row-${it.id}`}
                                                >
                                                    <td className="p-2 font-mono">
                                                        {it.date}
                                                    </td>
                                                    <td className="p-2 font-semibold">
                                                        {it.ad_account_name}
                                                    </td>
                                                    <td className="p-2">
                                                        {provLabel(it.ad_provider)}
                                                    </td>
                                                    <td className="p-2 font-mono font-bold text-rose-700">
                                                        {fmtSar(it.amount)}
                                                    </td>
                                                    <td className="p-2 font-mono text-emerald-700">
                                                        {fmtSar(it.covered_from_balance)}
                                                    </td>
                                                    <td className="p-2 font-mono text-amber-700">
                                                        {fmtSar(it.created_debt)}
                                                    </td>
                                                    <td className="p-2 text-[11px] text-gray-600">
                                                        {it.source}
                                                    </td>
                                                    <td className="p-2 text-[11px] text-gray-500 max-w-[260px] truncate">
                                                        {it.description || "—"}
                                                    </td>
                                                </tr>
                                            ))}
                                            {items.length === 0 && (
                                                <tr>
                                                    <td colSpan={8}
                                                        className="p-6 text-center text-gray-500">
                                                        لا توجد قيود إعلانية مسجَّلة في هذه الفترة.
                                                    </td>
                                                </tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            </div>

                            <p className="text-xs text-gray-500 leading-relaxed">
                                المصدر: <code>ad_account_ledger</code> (Iter-160 SSOT) —
                                نفس المصدر الذي يحتسب منه إجمالي تكاليف الإعلانات في
                                الملخص التنفيذي. أي اختلاف بين المجموع هنا والقيمة في
                                البطاقة يعني تعارضاً يجب فحصه.
                            </p>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}

function Stat({ label, value, big, testid }) {
    return (
        <div className="bg-white border rounded-lg p-3" data-testid={testid}>
            <div className="text-[11px] text-gray-500 mb-1">{label}</div>
            <div className={`font-extrabold text-rose-700 ${big ? "text-xl" : "text-base"}`}>
                {value}
            </div>
        </div>
    );
}
