import { useMemo, useState } from "react";
import {
    ChartBar,
    Check,
    DownloadSimple,
    Eye,
    PencilSimple,
    Trash,
} from "@phosphor-icons/react";

const STATUS_LABELS = {
    ACTIVE: "نشطة",
    active: "نشطة",
    ENABLED: "نشطة",
    enabled: "نشطة",
    PAUSED: "متوقفة",
    paused: "متوقفة",
    ARCHIVED: "مؤرشفة",
    archived: "مؤرشفة",
    DELETED: "محذوفة",
    deleted: "محذوفة",
    unknown: "غير محسومة",
};

function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function money(value) {
    const parsed = finite(value);
    if (parsed === null) return "—";
    return `${parsed.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function number(value) {
    const parsed = finite(value);
    if (parsed === null) return "—";
    return parsed.toLocaleString("en-US", {
        maximumFractionDigits: Number.isInteger(parsed) ? 0 : 2,
    });
}

function ratio(value, suffix = "") {
    const parsed = finite(value);
    if (parsed === null) return "—";
    return `${parsed.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}${suffix}`;
}

function activeStatus(value) {
    return ["active", "enabled", "delivering"].includes(
        String(value || "unknown").trim().toLowerCase(),
    );
}

function statusLabel(value) {
    const normalized = String(value || "unknown").trim() || "unknown";
    return STATUS_LABELS[normalized] || STATUS_LABELS[normalized.toLowerCase()] || normalized;
}

function deliveryLabel(row) {
    const explicit = String(row.delivery_status || "").trim();
    if (explicit) return explicit;
    return activeStatus(row.status) ? "يتم التسليم" : "غير نشط";
}

function rowKey(row) {
    return `${row.account_id || "unknown"}:${row.ad_squad_id || "unknown"}`;
}

function csvEscape(value) {
    return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function csv(rows) {
    const headers = [
        "اسم المجموعة الإعلانية", "معرف المجموعة", "اسم الحملة", "معرف الحملة",
        "الحالة", "حالة التسليم", "الصرف", "المشتريات", "المبيعات", "ROAS",
        "CPA", "الظهور", "النقرات", "CTR", "eCPC", "eCPM", "الميزانية اليومية",
        "عملة الميزانية", "هدف التحسين", "إستراتيجية المزايدة", "الحساب",
    ];
    const body = rows.map((row) => [
        row.ad_squad_name,
        row.ad_squad_id,
        row.campaign_name,
        row.campaign_id,
        statusLabel(row.status),
        deliveryLabel(row),
        row.spend_sar,
        row.orders,
        row.sales_sar,
        row.roas,
        row.cpa_sar,
        row.impressions,
        row.swipes,
        row.ctr_pct,
        row.cpc_sar,
        row.cpm_sar,
        row.budget?.daily_native,
        row.budget?.currency,
        row.optimization_goal,
        row.bid_strategy,
        row.account_name,
    ]);
    return [headers, ...body].map((line) => line.map(csvEscape).join(",")).join("\n");
}

function download(rows) {
    if (!rows.length || typeof document === "undefined") return;
    const blob = new Blob(["\ufeff", csv(rows)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "mezan-snapchat-ad-squads.csv";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
}

function Metric({ value, detail = "" }) {
    return (
        <div className="font-mono">
            <div className="font-extrabold text-slate-800">{value}</div>
            {detail && <div className="mt-0.5 text-[10px] font-semibold text-slate-400">{detail}</div>}
        </div>
    );
}

function StatusCell({ row }) {
    const active = activeStatus(row.status);
    return (
        <div className="flex items-center gap-2">
            <span className={`relative inline-flex h-5 w-9 shrink-0 rounded-full ${active ? "bg-emerald-500" : "bg-slate-300"}`}>
                <span className={`absolute top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-white shadow-sm ${active ? "right-[18px]" : "right-0.5"}`}>
                    {active && <Check size={10} weight="bold" className="text-emerald-600" />}
                </span>
            </span>
            <span className="text-xs font-bold text-slate-600">{statusLabel(row.status)}</span>
        </div>
    );
}

function DeliveryCell({ row }) {
    const active = activeStatus(row.status);
    const label = deliveryLabel(row);
    const blocked = active && !label.includes("يتم التسليم");
    return (
        <div className="flex items-start gap-2">
            <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${active ? (blocked ? "bg-amber-500 ring-2 ring-amber-100" : "bg-emerald-500 ring-2 ring-emerald-100") : "bg-slate-300"}`} />
            <div>
                <div className="font-bold text-slate-700">{label}</div>
                {active && (
                    <div className="mt-0.5 text-[10px] font-semibold text-slate-400">
                        {blocked ? "المجموعة مفعلة لكنها لا تسلّم حاليًا" : "قد تكون في مرحلة التعلم"}
                    </div>
                )}
            </div>
        </div>
    );
}

export default function AdSquadManagerTable({
    rows = [],
    totals = {},
    pagination = {},
    page = 1,
    onPageChange,
    loading = false,
    error = "",
}) {
    const [selected, setSelected] = useState(() => new Set());
    const [showSelectedOnly, setShowSelectedOnly] = useState(false);
    const [sort, setSort] = useState({ key: "spend_sar", direction: "desc" });

    const sorted = useMemo(() => {
        const direction = sort.direction === "asc" ? 1 : -1;
        return [...rows].sort((left, right) => {
            const a = sort.key === "name" ? left.ad_squad_name : finite(left[sort.key]);
            const b = sort.key === "name" ? right.ad_squad_name : finite(right[sort.key]);
            if (a === null && b === null) return 0;
            if (a === null) return 1;
            if (b === null) return -1;
            if (typeof a === "number" && typeof b === "number") return (a - b) * direction;
            return String(a).localeCompare(String(b), "ar", { numeric: true }) * direction;
        });
    }, [rows, sort]);
    const visible = showSelectedOnly
        ? sorted.filter((row) => selected.has(rowKey(row)))
        : sorted;
    const allSelected = visible.length > 0 && visible.every((row) => selected.has(rowKey(row)));
    const totalPages = Number(pagination.pages || 0);
    const currentPage = Number(pagination.page || page || 1);

    function toggle(row) {
        const key = rowKey(row);
        setSelected((current) => {
            const next = new Set(current);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    }

    function toggleAll() {
        setSelected((current) => {
            const next = new Set(current);
            if (allSelected) visible.forEach((row) => next.delete(rowKey(row)));
            else visible.forEach((row) => next.add(rowKey(row)));
            return next;
        });
    }

    function sortBy(key) {
        setSort((current) => ({
            key,
            direction: current.key === key && current.direction === "desc" ? "asc" : "desc",
        }));
    }

    return (
        <section className="overflow-hidden rounded-b-2xl border border-t-0 border-slate-200 bg-white shadow-sm" data-testid="ad-squad-manager-table" dir="rtl">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
                <div className="flex items-center gap-1">
                    <button
                        type="button"
                        disabled={!showSelectedOnly && selected.size === 0}
                        onClick={() => setShowSelectedOnly((value) => !value)}
                        className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-bold text-slate-600 hover:bg-slate-100 disabled:opacity-35"
                    >
                        <Eye size={16} weight="duotone" />
                        {showSelectedOnly ? "عرض الكل" : `عرض المحدد${selected.size ? ` (${selected.size})` : ""}`}
                    </button>
                    <button type="button" disabled className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-bold text-slate-600 opacity-35"><PencilSimple size={16} />تعديل</button>
                    <button type="button" disabled className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-bold text-slate-600 opacity-35"><Trash size={16} />حذف</button>
                </div>
                <div className="flex items-center gap-2">
                    <span className="rounded-full bg-blue-50 px-3 py-1 text-[10px] font-black text-blue-700">نتائج المنصة على مستوى المجموعة</span>
                    <button
                        type="button"
                        disabled={!visible.length}
                        onClick={() => download(visible)}
                        className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-bold text-slate-600 hover:bg-slate-100 disabled:opacity-35"
                    >
                        <DownloadSimple size={16} weight="duotone" />
                        تنزيل
                    </button>
                </div>
            </div>

            {error && <div className="border-b border-rose-200 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-800">{error}</div>}
            <div className="overflow-x-auto">
                <table className="w-max min-w-full border-separate border-spacing-0 text-right text-xs">
                    <thead className="bg-slate-50 text-slate-600">
                        <tr>
                            <th className="sticky right-0 z-20 w-12 border-b border-l border-slate-200 bg-slate-50 px-3 py-3 text-center">
                                <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="تحديد كل المجموعات الظاهرة" className="h-4 w-4 accent-emerald-600" />
                            </th>
                            <th className="sticky right-12 z-20 min-w-[300px] border-b border-l border-slate-200 bg-slate-50 px-4 py-3"><button type="button" onClick={() => sortBy("name")} className="font-black">اسم المجموعة الإعلانية</button></th>
                            <th className="min-w-[240px] border-b border-slate-200 px-4 py-3 font-black">الحملة</th>
                            <th className="min-w-[120px] border-b border-slate-200 px-4 py-3 font-black">الحالة</th>
                            <th className="min-w-[220px] border-b border-slate-200 px-4 py-3 font-black">حالة التسليم</th>
                            <th className="min-w-[125px] border-b border-slate-200 px-4 py-3"><button type="button" onClick={() => sortBy("orders")} className="font-black">النتائج</button></th>
                            <th className="min-w-[145px] border-b border-slate-200 px-4 py-3"><button type="button" onClick={() => sortBy("cpa_sar")} className="font-black">تكلفة النتيجة</button></th>
                            <th className="min-w-[145px] border-b border-slate-200 px-4 py-3"><button type="button" onClick={() => sortBy("spend_sar")} className="font-black">المبلغ المصروف</button></th>
                            <th className="min-w-[150px] border-b border-slate-200 px-4 py-3 font-black">مرات الظهور</th>
                            <th className="min-w-[115px] border-b border-slate-200 px-4 py-3 font-black">النقرات</th>
                            <th className="min-w-[105px] border-b border-slate-200 px-4 py-3 font-black">CTR</th>
                            <th className="min-w-[130px] border-b border-slate-200 px-4 py-3 font-black">ROAS</th>
                            <th className="min-w-[145px] border-b border-slate-200 px-4 py-3 font-black">المبيعات</th>
                            <th className="min-w-[150px] border-b border-slate-200 px-4 py-3 font-black">الميزانية اليومية</th>
                            <th className="min-w-[170px] border-b border-slate-200 px-4 py-3 font-black">هدف التحسين</th>
                            <th className="min-w-[220px] border-b border-slate-200 px-4 py-3 font-black">الحساب الإعلاني</th>
                        </tr>
                    </thead>
                    <tbody>
                        {visible.map((row) => {
                            const key = rowKey(row);
                            const checked = selected.has(key);
                            return (
                                <tr key={key} className={`${checked ? "bg-emerald-50/60" : "bg-white"} group hover:bg-slate-50`}>
                                    <td className={`${checked ? "bg-emerald-50" : "bg-white group-hover:bg-slate-50"} sticky right-0 z-10 border-b border-l border-slate-100 px-3 py-4 text-center`}><input type="checkbox" checked={checked} onChange={() => toggle(row)} aria-label={`تحديد ${row.ad_squad_name}`} className="h-4 w-4 accent-emerald-600" /></td>
                                    <td className={`${checked ? "bg-emerald-50" : "bg-white group-hover:bg-slate-50"} sticky right-12 z-10 border-b border-l border-slate-100 px-4 py-4`}><div className="max-w-[270px] truncate font-extrabold text-slate-950" title={row.ad_squad_name}>{row.ad_squad_name}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{row.ad_squad_id}</div></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><div className="max-w-[210px] truncate font-bold text-slate-700" title={row.campaign_name}>{row.campaign_name}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{row.campaign_id || "—"}</div></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><StatusCell row={row} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><DeliveryCell row={row} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={number(row.orders)} detail="مشتريات" /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={money(row.cpa_sar)} detail="لكل عملية شراء" /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={money(row.spend_sar)} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={number(row.impressions)} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={number(row.swipes)} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={ratio(row.ctr_pct, "%")} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={ratio(row.roas, "×")} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={money(row.sales_sar)} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4"><Metric value={finite(row.budget?.daily_native) === null ? "—" : `${number(row.budget.daily_native)} ${row.budget?.currency || ""}`} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4 font-bold text-slate-700">{row.optimization_goal || "—"}</td>
                                    <td className="border-b border-slate-100 px-4 py-4"><div className="max-w-[200px] truncate font-bold text-slate-700">{row.account_name}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{row.account_id}</div></td>
                                </tr>
                            );
                        })}
                        {!visible.length && (
                            <tr><td colSpan={16} className="px-6 py-16 text-center"><ChartBar size={38} weight="duotone" className="mx-auto text-slate-300" /><div className="mt-3 font-black text-slate-600">{loading ? "جاري تحميل المجموعات الإعلانية…" : showSelectedOnly ? "لا توجد مجموعات محددة." : "لا توجد بيانات مجموعات إعلانية موثقة ضمن الفترة."}</div></td></tr>
                        )}
                    </tbody>
                    {!!visible.length && (
                        <tfoot className="bg-slate-50/95 font-black text-slate-800"><tr><td className="sticky right-0 z-10 border-l border-t-2 border-slate-300 bg-slate-50 px-3 py-3" /><td className="sticky right-12 z-10 border-l border-t-2 border-slate-300 bg-slate-50 px-4 py-3">إجمالي الفترة</td><td className="border-t-2 border-slate-300 px-4 py-3" /><td className="border-t-2 border-slate-300 px-4 py-3" /><td className="border-t-2 border-slate-300 px-4 py-3" /><td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{number(totals.orders)}</td><td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{money(totals.cpa_sar)}</td><td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{money(totals.spend_sar)}</td><td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{number(totals.impressions)}</td><td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{number(totals.swipes)}</td><td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{ratio(totals.ctr_pct, "%")}</td><td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{ratio(totals.roas, "×")}</td><td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{money(totals.sales_sar)}</td><td className="border-t-2 border-slate-300 px-4 py-3" /><td className="border-t-2 border-slate-300 px-4 py-3" /><td className="border-t-2 border-slate-300 px-4 py-3" /></tr></tfoot>
                    )}
                </table>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-4 py-3">
                <span className="text-xs font-bold text-slate-500">نتائج المجموعات من Snapchat فقط؛ مطابقة سلة تبقى على مستوى الحملة حتى تتوفر هوية أدق.</span>
                <div className="flex items-center gap-3"><span className="text-xs font-bold text-slate-500">{number(pagination.total || rows.length)} مجموعة · الصفحة {currentPage}{totalPages > 0 ? ` من ${totalPages}` : ""}</span>{totalPages > 1 && <><button type="button" disabled={currentPage <= 1} onClick={() => onPageChange?.(currentPage - 1)} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-black disabled:opacity-35">السابق</button><button type="button" disabled={currentPage >= totalPages} onClick={() => onPageChange?.(currentPage + 1)} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-black disabled:opacity-35">التالي</button></>}</div>
            </div>
        </section>
    );
}
