import { useEffect, useMemo, useState } from "react";
import { CaretLeft, MagnifyingGlass, PencilSimple } from "@phosphor-icons/react";

const LEVEL_LABELS = {
    campaign: "الحملات",
    ad_group: "المجموعات الإعلانية",
    ad: "الإعلانات",
};

function number(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "—";
}

function money(value) {
    if (value?.amount === null || value?.amount === undefined || value?.amount === "") return "—";
    const parsed = Number(value?.amount);
    if (!Number.isFinite(parsed)) return "—";
    return `${parsed.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ${value?.currency || ""}`.trim();
}

function ratio(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? `${parsed.toFixed(2)}×` : "—";
}

function commerce(row) {
    return row?.commerce_outcomes || {};
}

export default function UnifiedMarketingEntityTable({
    report,
    loading = false,
    onOpenChildren,
    onManageEntity,
}) {
    const allRows = report?.rows || [];
    const level = report?.entity_level || "campaign";
    const totals = report?.totals || null;
    const canOpenChildren = ["campaign", "ad_group"].includes(level);
    const childLabel = level === "campaign" ? "Ad Squads" : "Ads";
    const [query, setQuery] = useState("");
    const [activeOnly, setActiveOnly] = useState(false);
    const [page, setPage] = useState(1);
    const pageSize = 25;
    const filteredRows = useMemo(() => {
        const needle = query.trim().toLocaleLowerCase();
        return allRows.filter((row) => {
            if (activeOnly && row.entity.active !== true) return false;
            if (!needle) return true;
            return [
                row.entity.name,
                row.entity.id,
                row.entity.status,
                row.entity.campaign_id,
                row.entity.ad_group_id,
            ].some((value) => String(value || "").toLocaleLowerCase().includes(needle));
        });
    }, [activeOnly, allRows, query]);
    const pages = Math.ceil(filteredRows.length / pageSize);
    const rows = filteredRows.slice((page - 1) * pageSize, page * pageSize);

    useEffect(() => {
        setPage(1);
    }, [activeOnly, query, report]);

    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white" data-testid="unified-marketing-entity-table">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4">
                <div>
                    <h3 className="text-lg font-black text-slate-950">{LEVEL_LABELS[level] || level}</h3>
                    <p className="mt-1 text-xs font-bold text-slate-500">
                        Unified Marketing Data Contract · {report?.contract_version || "—"}
                    </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <label className="relative block min-w-[230px]">
                        <MagnifyingGlass size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="بحث بالاسم أو المعرف" className="h-9 w-full rounded-xl border border-slate-200 bg-slate-50 pr-9 pl-3 text-xs font-bold outline-none focus:border-emerald-400" />
                    </label>
                    <button type="button" onClick={() => setActiveOnly((value) => !value)} aria-pressed={activeOnly} className={`h-9 rounded-xl border px-3 text-xs font-black ${activeOnly ? "border-emerald-300 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-white text-slate-600"}`}>النشط فقط</button>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-700">{number(filteredRows.length)} من {number(allRows.length)}</span>
                    <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-black text-amber-800">
                        القرارات: غير مفعلة
                    </span>
                </div>
            </header>
            <div className="overflow-x-auto">
                <table className="min-w-[1500px] w-full text-right text-xs">
                    <thead className="bg-slate-50 text-slate-600">
                        <tr>
                            <th className="px-4 py-3 font-black">الكيان</th>
                            <th className="px-4 py-3 font-black">الحالة</th>
                            <th className="px-4 py-3 font-black">الصرف</th>
                            <th className="px-4 py-3 font-black">الظهور</th>
                            <th className="px-4 py-3 font-black">المشاهدات</th>
                            <th className="px-4 py-3 font-black">النقرات/السحب</th>
                            <th className="px-4 py-3 font-black">مشتريات Snapchat</th>
                            <th className="px-4 py-3 font-black">قيمة Snapchat</th>
                            <th className="px-4 py-3 font-black">ROAS Snapchat</th>
                            <th className="px-4 py-3 font-black">طلبات سلة</th>
                            <th className="px-4 py-3 font-black">مبيعات سلة</th>
                            <th className="px-4 py-3 font-black">ROAS سلة</th>
                            <th className="px-4 py-3 font-black">جودة البيانات</th>
                            <th className="px-4 py-3 font-black">الإدارة</th>
                            {canOpenChildren && <th className="px-4 py-3 font-black">التفاصيل</th>}
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {rows.map((row) => (
                            <tr key={`${row.entity.level}:${row.entity.id}`} className="hover:bg-slate-50">
                                <td className="px-4 py-4">
                                    <div className="max-w-[260px] truncate text-sm font-black text-slate-950" title={row.entity.name}>{row.entity.name}</div>
                                    <div className="mt-1 font-mono text-[10px] text-slate-400">{row.entity.id}</div>
                                </td>
                                <td className="px-4 py-4">
                                    <span className={`rounded-full px-2 py-1 font-black ${row.entity.active === true ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
                                        {row.entity.status || (row.entity.active ? "ACTIVE" : "—")}
                                    </span>
                                </td>
                                <td className="px-4 py-4 font-mono font-black" dir="ltr">{money(row.delivery.spend)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.delivery.impressions)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.delivery.views)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.delivery.clicks)}</td>
                                <td className="px-4 py-4 font-mono">{number(row.platform_outcomes.conversions)}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(row.platform_outcomes.revenue)}</td>
                                <td className="px-4 py-4 font-mono">{ratio(row.platform_outcomes.roas)}</td>
                                <td className="px-4 py-4 font-mono">{commerce(row).status === "complete" ? number(commerce(row).orders) : "—"}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{commerce(row).status === "complete" ? money(commerce(row).revenue) : "—"}</td>
                                <td className="px-4 py-4 font-mono">{commerce(row).status === "complete" ? ratio(commerce(row).roas) : "—"}</td>
                                <td className="px-4 py-4">
                                    <span className={`rounded-full px-2 py-1 font-black ${row.quality.coverage_status === "complete" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>
                                        {row.quality.sync_status}
                                    </span>
                                </td>
                                <td className="px-4 py-4">
                                    <button
                                        type="button"
                                        onClick={() => onManageEntity?.(row)}
                                        className="inline-flex items-center gap-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 font-black text-amber-800 hover:bg-amber-100"
                                    >
                                        <PencilSimple size={14} weight="bold" /> تعديل / حالة
                                    </button>
                                </td>
                                {canOpenChildren && (
                                    <td className="px-4 py-4">
                                        <button
                                            type="button"
                                            onClick={() => onOpenChildren?.(row)}
                                            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-2 font-black text-emerald-700 hover:bg-emerald-50"
                                        >
                                            {childLabel} <CaretLeft size={14} weight="bold" />
                                        </button>
                                    </td>
                                )}
                            </tr>
                        ))}
                        {!loading && rows.length === 0 && (
                            <tr>
                                <td colSpan={canOpenChildren ? 15 : 14} className="px-4 py-10 text-center font-black text-slate-400">
                                    لا توجد facts مؤكدة لهذا المستوى في الفترة المختارة.
                                </td>
                            </tr>
                        )}
                    </tbody>
                    {totals && (
                        <tfoot className="border-t-2 border-slate-300 bg-slate-50 font-black">
                            <tr>
                                <td className="px-4 py-4">إجمالي الفترة</td>
                                <td className="px-4 py-4" />
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(totals.delivery.spend)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.delivery.impressions)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.delivery.views)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.delivery.clicks)}</td>
                                <td className="px-4 py-4 font-mono">{number(totals.platform_outcomes.conversions)}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{money(totals.platform_outcomes.revenue)}</td>
                                <td className="px-4 py-4 font-mono">{ratio(totals.platform_outcomes.roas)}</td>
                                <td className="px-4 py-4 font-mono">{commerce(totals).status === "complete" ? number(commerce(totals).orders) : "—"}</td>
                                <td className="px-4 py-4 font-mono" dir="ltr">{commerce(totals).status === "complete" ? money(commerce(totals).revenue) : "—"}</td>
                                <td className="px-4 py-4 font-mono">{commerce(totals).status === "complete" ? ratio(commerce(totals).roas) : "—"}</td>
                                <td className="px-4 py-4">{totals.quality.sync_status}</td>
                                <td className="px-4 py-4" />
                                {canOpenChildren && <td className="px-4 py-4" />}
                            </tr>
                        </tfoot>
                    )}
                </table>
            </div>
            {filteredRows.length > pageSize && (
                <footer className="flex items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 text-xs font-black text-slate-600">
                    <span>الصفحة {page} من {pages}</span>
                    <div className="flex gap-2">
                        <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="rounded-lg border border-slate-200 px-3 py-2 disabled:opacity-35">السابق</button>
                        <button type="button" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))} className="rounded-lg border border-slate-200 px-3 py-2 disabled:opacity-35">التالي</button>
                    </div>
                </footer>
            )}
        </section>
    );
}
