import { useEffect, useMemo, useState } from "react";
import {
    MagnifyingGlass,
    SpinnerGap,
    WarningCircle,
    X,
} from "@phosphor-icons/react";

import { getSnapchatOrderSourceAudit } from "../../services/snapchatOrderSourceAudit";

const FILTERS = Object.freeze([
    { id: "all", label: "الكل" },
    { id: "matched", label: "طلبات الحملات" },
    { id: "non_campaign", label: "مباشر/واتساب/يدوي" },
    { id: "ambiguous", label: "ملتبس" },
]);

const CLASSIFICATION_LABELS = Object.freeze({
    matched: "مطابق لحملة",
    non_campaign: "غير منسوب لحملة",
    ambiguous: "مطابقة ملتبسة",
});

const MATCH_LABELS = Object.freeze({
    campaign_id: "معرف الحملة",
    campaign_name: "اسم الحملة الفريد",
    ambiguous_id: "معرف ملتبس",
    ambiguous_name: "اسم ملتبس",
    unmatched: "غير مطابق",
});

function numeric(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "—";
}

function money(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed)
        ? `${parsed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`
        : "0.00 ر.س";
}

function dateTime(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString("ar-SA", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function SummaryCard({ title, value, note, tone = "slate" }) {
    const toneClass = {
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
        amber: "border-amber-200 bg-amber-50 text-amber-900",
        blue: "border-blue-200 bg-blue-50 text-blue-900",
        violet: "border-violet-200 bg-violet-50 text-violet-900",
        slate: "border-slate-200 bg-slate-50 text-slate-900",
    }[tone] || "border-slate-200 bg-slate-50 text-slate-900";
    return (
        <article className={`rounded-2xl border p-4 ${toneClass}`}>
            <div className="text-xs font-black opacity-70">{title}</div>
            <div className="mt-2 font-mono text-2xl font-black" dir="ltr">{numeric(value)}</div>
            <div className="mt-1 text-[11px] font-bold leading-5 opacity-70">{note}</div>
        </article>
    );
}

export default function SnapchatOrderSourceAudit({
    accountId,
    dateFrom,
    dateTo,
}) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [open, setOpen] = useState(false);
    const [filter, setFilter] = useState("all");
    const [query, setQuery] = useState("");

    useEffect(() => {
        if (!accountId || !dateFrom || !dateTo) {
            setData(null);
            return undefined;
        }
        let active = true;
        setLoading(true);
        setError("");
        getSnapchatOrderSourceAudit({ accountId, dateFrom, dateTo })
            .then((result) => {
                if (active) setData(result);
            })
            .catch((loadError) => {
                if (!active) return;
                const detail = loadError?.response?.data?.detail;
                setError(
                    (typeof detail === "string" ? detail : detail?.message)
                    || "تعذر تحميل تدقيق مصادر طلبات سلة.",
                );
            })
            .finally(() => {
                if (active) setLoading(false);
            });
        return () => {
            active = false;
        };
    }, [accountId, dateFrom, dateTo]);

    const rows = useMemo(() => {
        const needle = query.trim().casefold?.() || query.trim().toLocaleLowerCase();
        return (data?.orders || []).filter((row) => {
            if (filter !== "all" && row.classification !== filter) return false;
            if (!needle) return true;
            return [
                row.order_number,
                row.campaign_name,
                row.campaign_id,
                row.source_label,
                row.order_type,
                row.status,
            ].some((value) => String(value || "").toLocaleLowerCase().includes(needle));
        });
    }, [data, filter, query]);

    if (!accountId) return null;
    const summary = data?.summary || {};

    return (
        <>
            <section
                className="border-x border-t border-slate-200 bg-white p-4"
                data-testid="snapchat-order-source-summary"
            >
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-base font-black text-slate-900">مصادر الطلبات للفترة</h3>
                        <p className="mt-1 text-xs font-bold leading-5 text-slate-500">
                            سلة هي مصدر الطلب الحقيقي. الطلبات غير المنسوبة لا يوزعها ميزان على الحملات بالتخمين.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={() => setOpen(true)}
                        disabled={!data || loading}
                        className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 text-sm font-black text-slate-800 disabled:opacity-50"
                        data-testid="open-snapchat-order-audit"
                    >
                        <MagnifyingGlass size={18} weight="bold" />
                        مراجعة الطلبات
                    </button>
                </div>

                {loading ? (
                    <div className="mt-4 flex items-center gap-2 rounded-xl bg-slate-50 p-4 text-sm font-bold text-slate-500">
                        <SpinnerGap size={20} className="animate-spin" />
                        جاري مطابقة طلبات سلة مع الحملات…
                    </div>
                ) : error ? (
                    <div className="mt-4 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold text-amber-900">
                        <WarningCircle size={20} weight="fill" />
                        {error}
                    </div>
                ) : data ? (
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                        <SummaryCard
                            title="طلبات مرتبطة بحملة"
                            value={summary.campaign_matched_orders}
                            note="يشمل الحملات النشطة والمتوقفة؛ قد يختلف عن جدول النشطة فقط"
                            tone="emerald"
                        />
                        <SummaryCard
                            title="طلبات سناب بلا ربط تفصيلي"
                            value={summary.snapchat_attribution_gap_orders}
                            note="منسوبة إلى Snapchat في سلة دون حملة محددة"
                            tone="amber"
                        />
                        <SummaryCard
                            title="إجمالي طلبات سلة"
                            value={summary.total_salla_created_orders}
                            note={`المبيعات المالية: ${money(summary.total_financial_sales_sar)}`}
                            tone="blue"
                        />
                        <SummaryCard
                            title="مشتريات Snapchat — كل الحساب"
                            value={summary.platform_attributed_purchases}
                            note={`لا يتأثر بفلتر الحملات؛ مجموع صفوف الحملات ${summary.platform_campaign_purchases ?? "—"}`}
                            tone="violet"
                        />
                    </div>
                ) : null}
            </section>

            {open && (
                <div
                    className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/60 p-3 sm:p-6"
                    role="dialog"
                    aria-modal="true"
                    aria-label="تدقيق طلبات سلة المطابقة"
                    data-testid="snapchat-order-audit-dialog"
                >
                    <section className="flex max-h-[92vh] w-full max-w-7xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl">
                        <header className="flex items-start justify-between gap-3 border-b border-slate-200 p-5">
                            <div>
                                <h2 className="text-xl font-black text-slate-950">تدقيق طلبات سلة</h2>
                                <p className="mt-1 text-xs font-bold text-slate-500">
                                    {dateFrom} — {dateTo} · {data?.account?.account_name || accountId} · {summary.date_timezone || "توقيت الحساب"}
                                </p>
                            </div>
                            <button
                                type="button"
                                onClick={() => setOpen(false)}
                                className="rounded-xl border border-slate-200 p-2 text-slate-600 hover:bg-slate-50"
                                aria-label="إغلاق"
                            >
                                <X size={22} weight="bold" />
                            </button>
                        </header>

                        <div className="border-b border-slate-200 p-4">
                            <div className="flex flex-wrap gap-2">
                                {FILTERS.map((item) => (
                                    <button
                                        key={item.id}
                                        type="button"
                                        onClick={() => setFilter(item.id)}
                                        className={`rounded-xl px-3 py-2 text-xs font-black ${filter === item.id ? "bg-slate-950 text-white" : "border border-slate-200 bg-white text-slate-700"}`}
                                    >
                                        {item.label}
                                    </button>
                                ))}
                                <label className="relative mr-auto block min-w-[240px] flex-1 sm:max-w-sm">
                                    <MagnifyingGlass size={17} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                    <input
                                        value={query}
                                        onChange={(event) => setQuery(event.target.value)}
                                        placeholder="رقم الطلب أو الحملة أو المصدر"
                                        className="h-10 w-full rounded-xl border border-slate-200 bg-slate-50 pr-9 pl-3 text-sm outline-none focus:border-emerald-400"
                                    />
                                </label>
                            </div>
                        </div>

                        <div className="overflow-auto">
                            <table className="min-w-[1180px] w-full text-right text-xs">
                                <thead className="sticky top-0 z-10 bg-slate-100 text-slate-600">
                                    <tr>
                                        <th className="px-3 py-3 font-black">رقم الطلب</th>
                                        <th className="px-3 py-3 font-black">وقت الطلب</th>
                                        <th className="px-3 py-3 font-black">المبلغ</th>
                                        <th className="px-3 py-3 font-black">التصنيف</th>
                                        <th className="px-3 py-3 font-black">الحملة</th>
                                        <th className="px-3 py-3 font-black">طريقة المطابقة</th>
                                        <th className="px-3 py-3 font-black">المصدر/النوع</th>
                                        <th className="px-3 py-3 font-black">الحالة المالية</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100">
                                    {rows.map((row, index) => (
                                        <tr key={`${row.order_number || "order"}-${index}`} className="hover:bg-slate-50">
                                            <td className="px-3 py-3 font-mono font-black text-slate-900">{row.order_number || "—"}</td>
                                            <td className="px-3 py-3 whitespace-nowrap text-slate-600">{dateTime(row.local_created_at)}</td>
                                            <td className="px-3 py-3 whitespace-nowrap font-mono font-black">{money(row.amount_sar)}</td>
                                            <td className="px-3 py-3">
                                                <span className={`rounded-full px-2 py-1 font-black ${row.classification === "matched" ? "bg-emerald-100 text-emerald-800" : row.classification === "ambiguous" ? "bg-rose-100 text-rose-800" : "bg-amber-100 text-amber-800"}`}>
                                                    {CLASSIFICATION_LABELS[row.classification] || row.classification}
                                                </span>
                                            </td>
                                            <td className="px-3 py-3">
                                                <div className="font-black text-slate-800">{row.campaign_name || "—"}</div>
                                                {row.campaign_id && <div className="mt-1 font-mono text-[10px] text-slate-400">{row.campaign_id}</div>}
                                            </td>
                                            <td className="px-3 py-3 text-slate-600">{MATCH_LABELS[row.match_method] || row.match_method || "—"}</td>
                                            <td className="px-3 py-3">
                                                <div className="font-bold text-slate-700">{row.source_label || "غير محدد"}</div>
                                                <div className="mt-1 text-[10px] text-slate-400">
                                                    {row.is_gift ? "هدية" : row.order_type || row.origin_category || "—"}
                                                </div>
                                            </td>
                                            <td className="px-3 py-3">
                                                <div className={`font-black ${row.financially_included ? "text-emerald-700" : "text-slate-500"}`}>
                                                    {row.financially_included ? "مشمول ماليًا" : "غير مشمول ماليًا"}
                                                </div>
                                                <div className="mt-1 text-[10px] text-slate-400">{row.status || "—"}</div>
                                            </td>
                                        </tr>
                                    ))}
                                    {!rows.length && (
                                        <tr>
                                            <td colSpan={8} className="p-10 text-center font-bold text-slate-400">لا توجد طلبات ضمن هذا الفلتر.</td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>

                        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3 text-xs font-bold text-slate-500">
                            <span>المعروض: {numeric(rows.length)} من {numeric(data?.orders_total)}</span>
                            <span>لا تعديل على سلة أو Snapchat · لا توزيع للطلبات غير المنسوبة</span>
                        </footer>
                    </section>
                </div>
            )}
        </>
    );
}
