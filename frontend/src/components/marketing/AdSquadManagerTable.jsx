import { useEffect, useMemo, useState } from "react";
import {
    ChartBar,
    Check,
    DownloadSimple,
    Eye,
    PencilSimple,
    Trash,
} from "@phosphor-icons/react";

const STATUS_LABELS = Object.freeze({
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
});

const SORT_STORAGE_KEY = "mezan-snapchat-adsquad-sort-v1";
const SORT_EVENT = "mezan:snapchat-adsquad-sort-updated";
const VALID_SORTS = new Set(["newest", "spend", "active"]);
const CHECKBOX_WIDTH = 48;
const NAME_WIDTH = 300;

export const AD_SQUAD_MANAGER_NATIVE_COLUMN_ORDER = Object.freeze([
    "name",
    "status",
    "campaign",
    "delivery",
    "orders",
    "cpa",
    "roas",
    "spend",
    "sales",
    "impressions",
    "clicks",
    "ctr",
    "budget",
    "optimization",
    "account",
]);

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

function timestamp(value) {
    const parsed = Date.parse(String(value || ""));
    return Number.isFinite(parsed) ? parsed : 0;
}

export function readAdSquadSortPreference(storage = typeof window !== "undefined" ? window.localStorage : null) {
    try {
        const value = String(storage?.getItem(SORT_STORAGE_KEY) || "newest");
        return VALID_SORTS.has(value) ? value : "newest";
    } catch {
        return "newest";
    }
}

export function sortAdSquadRows(rows = [], mode = "newest") {
    const normalized = VALID_SORTS.has(mode) ? mode : "newest";
    return [...rows].sort((left, right) => {
        const leftActive = activeStatus(left.status) ? 1 : 0;
        const rightActive = activeStatus(right.status) ? 1 : 0;
        const leftSpend = finite(left.spend_sar) || 0;
        const rightSpend = finite(right.spend_sar) || 0;
        const leftTime = timestamp(left.created_at_provider || left.start_time || left.updated_at_provider);
        const rightTime = timestamp(right.created_at_provider || right.start_time || right.updated_at_provider);
        if (normalized === "active" && leftActive !== rightActive) return rightActive - leftActive;
        if (normalized === "spend" && leftSpend !== rightSpend) return rightSpend - leftSpend;
        if (normalized === "active" && leftSpend !== rightSpend) return rightSpend - leftSpend;
        if (leftTime !== rightTime) return rightTime - leftTime;
        return String(left.ad_squad_name || "").localeCompare(
            String(right.ad_squad_name || ""),
            "ar",
            { numeric: true },
        );
    });
}

function csvEscape(value) {
    return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function csv(rows) {
    const headers = [
        "اسم المجموعة الإعلانية", "معرف المجموعة", "الحالة", "اسم الحملة", "معرف الحملة",
        "حالة التسليم", "المشتريات", "CPA", "ROAS", "الصرف", "المبيعات",
        "الظهور", "النقرات", "CTR", "الميزانية اليومية", "عملة الميزانية",
        "هدف التحسين", "إستراتيجية المزايدة", "الحساب", "تاريخ البدء",
    ];
    const body = rows.map((row) => [
        row.ad_squad_name,
        row.ad_squad_id,
        statusLabel(row.status),
        row.campaign_name,
        row.campaign_id,
        deliveryLabel(row),
        row.orders,
        row.cpa_sar,
        row.roas,
        row.spend_sar,
        row.sales_sar,
        row.impressions,
        row.swipes,
        row.ctr_pct,
        row.budget?.daily_native,
        row.budget?.currency,
        row.optimization_goal,
        row.bid_strategy,
        row.account_name,
        row.start_time,
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

function sortableValue(row, key) {
    if (key === "name") return String(row.ad_squad_name || "");
    if (key === "status") return activeStatus(row.status) ? 1 : 0;
    return finite(row[key]);
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
    const [preferredSort, setPreferredSort] = useState(() => readAdSquadSortPreference());
    const [columnSort, setColumnSort] = useState(null);

    useEffect(() => {
        const update = (event) => {
            const next = String(event?.detail?.sort_by || readAdSquadSortPreference());
            setPreferredSort(VALID_SORTS.has(next) ? next : "newest");
            setColumnSort(null);
        };
        window.addEventListener(SORT_EVENT, update);
        return () => window.removeEventListener(SORT_EVENT, update);
    }, []);

    const sorted = useMemo(() => {
        const preferred = sortAdSquadRows(rows, preferredSort);
        if (!columnSort?.key) return preferred;
        const direction = columnSort.direction === "asc" ? 1 : -1;
        return [...preferred].sort((left, right) => {
            const a = sortableValue(left, columnSort.key);
            const b = sortableValue(right, columnSort.key);
            if (a === null && b === null) return 0;
            if (a === null) return 1;
            if (b === null) return -1;
            if (typeof a === "number" && typeof b === "number") return (a - b) * direction;
            return String(a).localeCompare(String(b), "ar", { numeric: true }) * direction;
        });
    }, [rows, preferredSort, columnSort]);

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
        setColumnSort((current) => ({
            key,
            direction: current?.key === key && current.direction === "desc" ? "asc" : "desc",
        }));
    }

    const stickyStatusRight = CHECKBOX_WIDTH + NAME_WIDTH;

    return (
        <section
            className="overflow-hidden rounded-b-2xl border border-t-0 border-slate-200 bg-white shadow-sm"
            data-testid="ad-squad-manager-table"
            data-native-column-layout="true"
            dir="rtl"
            data-sort-mode={preferredSort}
        >
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
                            <th className="sticky right-0 z-40 w-12 border-b border-l border-slate-200 bg-slate-50 px-3 py-3 text-center">
                                <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="تحديد كل المجموعات الظاهرة" className="h-4 w-4 accent-emerald-600" />
                            </th>
                            <th className="sticky right-12 z-30 min-w-[300px] border-b border-l border-slate-200 bg-slate-50 px-4 py-3" data-column-id="name"><button type="button" onClick={() => sortBy("name")} className="font-black">اسم المجموعة الإعلانية</button></th>
                            <th className="sticky z-30 min-w-[120px] border-b border-l border-slate-200 bg-slate-50 px-4 py-3" style={{ right: stickyStatusRight }} data-column-id="status"><button type="button" onClick={() => sortBy("status")} className="font-black">الحالة</button></th>
                            <th className="min-w-[240px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="campaign">الحملة</th>
                            <th className="min-w-[220px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="delivery">حالة التسليم</th>
                            <th className="min-w-[125px] border-b border-slate-200 px-4 py-3" data-column-id="orders"><button type="button" onClick={() => sortBy("orders")} className="font-black">النتائج</button></th>
                            <th className="min-w-[145px] border-b border-slate-200 px-4 py-3" data-column-id="cpa"><button type="button" onClick={() => sortBy("cpa_sar")} className="font-black">تكلفة النتيجة</button></th>
                            <th className="min-w-[130px] border-b border-slate-200 px-4 py-3" data-column-id="roas"><button type="button" onClick={() => sortBy("roas")} className="font-black">ROAS</button></th>
                            <th className="min-w-[145px] border-b border-slate-200 px-4 py-3" data-column-id="spend"><button type="button" onClick={() => sortBy("spend_sar")} className="font-black">المبلغ المصروف</button></th>
                            <th className="min-w-[145px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="sales">المبيعات</th>
                            <th className="min-w-[150px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="impressions">مرات الظهور</th>
                            <th className="min-w-[115px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="clicks">النقرات</th>
                            <th className="min-w-[105px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="ctr">CTR</th>
                            <th className="min-w-[150px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="budget">الميزانية اليومية</th>
                            <th className="min-w-[170px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="optimization">هدف التحسين</th>
                            <th className="min-w-[220px] border-b border-slate-200 px-4 py-3 font-black" data-column-id="account">الحساب الإعلاني</th>
                        </tr>
                    </thead>
                    <tbody>
                        {visible.map((row) => {
                            const key = rowKey(row);
                            const checked = selected.has(key);
                            const background = checked ? "bg-emerald-50" : "bg-white group-hover:bg-slate-50";
                            return (
                                <tr key={key} className={`${checked ? "bg-emerald-50/60" : "bg-white"} group hover:bg-slate-50`}>
                                    <td className={`${background} sticky right-0 z-20 border-b border-l border-slate-100 px-3 py-4 text-center`}><input type="checkbox" checked={checked} onChange={() => toggle(row)} aria-label={`تحديد ${row.ad_squad_name}`} className="h-4 w-4 accent-emerald-600" /></td>
                                    <td className={`${background} sticky right-12 z-10 border-b border-l border-slate-100 px-4 py-4`} data-column-id="name"><div className="max-w-[270px] truncate font-extrabold text-slate-950" title={row.ad_squad_name}>{row.ad_squad_name}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{row.ad_squad_id}</div>{row.start_time && <div className="mt-1 text-[10px] font-bold text-slate-400">بدء: {new Date(row.start_time).toLocaleDateString("ar-SA")}</div>}</td>
                                    <td className={`${background} sticky z-10 border-b border-l border-slate-100 px-4 py-4`} style={{ right: stickyStatusRight }} data-column-id="status"><StatusCell row={row} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="campaign"><div className="max-w-[210px] truncate font-bold text-slate-700" title={row.campaign_name}>{row.campaign_name}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{row.campaign_id || "—"}</div></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="delivery"><DeliveryCell row={row} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="orders"><Metric value={number(row.orders)} detail="مشتريات" /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="cpa"><Metric value={money(row.cpa_sar)} detail="لكل عملية شراء" /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="roas"><Metric value={ratio(row.roas, "×")} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="spend"><Metric value={money(row.spend_sar)} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="sales"><Metric value={money(row.sales_sar)} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="impressions"><Metric value={number(row.impressions)} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="clicks"><Metric value={number(row.swipes)} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="ctr"><Metric value={ratio(row.ctr_pct, "%")} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="budget"><Metric value={finite(row.budget?.daily_native) === null ? "—" : `${number(row.budget.daily_native)} ${row.budget?.currency || ""}`} /></td>
                                    <td className="border-b border-slate-100 px-4 py-4 font-bold text-slate-700" data-column-id="optimization">{row.optimization_goal || "—"}</td>
                                    <td className="border-b border-slate-100 px-4 py-4" data-column-id="account"><div className="max-w-[200px] truncate font-bold text-slate-700">{row.account_name}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{row.account_id}</div></td>
                                </tr>
                            );
                        })}
                        {!visible.length && (
                            <tr><td colSpan={16} className="px-6 py-16 text-center"><ChartBar size={38} weight="duotone" className="mx-auto text-slate-300" /><div className="mt-3 font-black text-slate-600">{loading ? "جاري تحميل المجموعات الإعلانية…" : showSelectedOnly ? "لا توجد مجموعات محددة." : "لا توجد بيانات مجموعات إعلانية موثقة ضمن الفترة."}</div></td></tr>
                        )}
                    </tbody>
                    {!!visible.length && (
                        <tfoot className="bg-slate-50/95 font-black text-slate-800">
                            <tr>
                                <td className="sticky right-0 z-20 border-l border-t-2 border-slate-300 bg-slate-50 px-3 py-3" />
                                <td className="sticky right-12 z-10 border-l border-t-2 border-slate-300 bg-slate-50 px-4 py-3" data-column-id="name">إجمالي الفترة</td>
                                <td className="sticky z-10 border-l border-t-2 border-slate-300 bg-slate-50 px-4 py-3" style={{ right: stickyStatusRight }} data-column-id="status" />
                                <td className="border-t-2 border-slate-300 px-4 py-3" />
                                <td className="border-t-2 border-slate-300 px-4 py-3" />
                                <td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{number(totals.orders)}</td>
                                <td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{money(totals.cpa_sar)}</td>
                                <td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{ratio(totals.roas, "×")}</td>
                                <td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{money(totals.spend_sar)}</td>
                                <td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{money(totals.sales_sar)}</td>
                                <td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{number(totals.impressions)}</td>
                                <td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{number(totals.swipes)}</td>
                                <td className="border-t-2 border-slate-300 px-4 py-3 font-mono">{ratio(totals.ctr_pct, "%")}</td>
                                <td className="border-t-2 border-slate-300 px-4 py-3" />
                                <td className="border-t-2 border-slate-300 px-4 py-3" />
                                <td className="border-t-2 border-slate-300 px-4 py-3" />
                            </tr>
                        </tfoot>
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
