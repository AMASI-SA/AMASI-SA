import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useReactToPrint } from "react-to-print";
import api from "../lib/api";
import ProductTrafficSources from "../components/ProductTrafficSources";
import { addDaysISO, todaySA } from "../lib/dates";
import { formatMoney, formatInt } from "../lib/format";
import DateInput, { isValidISODate } from "../components/DateInput";

const PAGE_SIZE = 20;
const STATUS_OPTIONS = [
    { value: "pending_review", label: "انتظار المراجعة" },
    { value: "reviewed", label: "تم المراجعة" },
    { value: "in_progress", label: "قيد التنفيذ" },
];
const money = value => `${formatMoney(value)} ر.س`;
const complete = row => row.cost_status === "complete" && row.total_cost != null;
const rowKey = row => row.report_key || `${row.product_id}:${row.sku || ""}`;

export function costReasonLabel(reason) {
    const value = String(reason || "");
    if (value.includes("cost_snapshot_line_changed")) return "بيانات الطلب لا تطابق سجل التكلفة";
    if (value.includes("selection") || value.includes("option_identity") || value.includes("unresolved")) return "اختيار غير متحقق";
    if (value.includes("component") || value.includes("resource")) return "تكلفة مكوّن أو خدمة ناقصة";
    if (value.includes("base")) return "تكلفة المنتج ناقصة";
    if (value.includes("quantity")) return "كمية غير متحققة";
    if (value.includes("bundle") || value.includes("snapshot")) return "تفاصيل تكلفة البكج غير مكتملة";
    return "تكلفة ناقصة";
}

export function CostSummary({ rows, final = false }) {
    const units = rows.reduce((sum, row) => sum + row.units_sold, 0);
    const knownCost = rows.reduce((sum, row) => sum + (row.known_cost_total ?? row.total_cost ?? 0), 0);
    const missing = rows.some(row => !complete(row));
    return <div className="flex flex-wrap gap-x-8 gap-y-2 bg-violet-50 p-4 text-sm font-bold" data-testid={final ? "sold-products-final-totals" : "sold-products-page-totals"}>
        <span>{final ? "إجمالي القطع" : "قطع هذه الصفحة"}: {formatInt(units)}</span>
        {final && <span>متوسط تكلفة القطعة: {missing ? "غير مكتمل" : units ? money(knownCost / units) : "—"}</span>}
        <span>{final ? "إجمالي تكلفة جميع القطع" : "إجمالي تكلفة قطع الصفحة"}: {missing ? "غير مكتمل" : money(knownCost)}</span>
        {missing && <span className="text-amber-800">المعروف {money(knownCost)} — الإجمالي غير مكتمل</span>}
    </div>;
}

function CostCell({ cell, kind, quantity = 1 }) {
    if (!cell) return <span className="text-slate-500">تفصيل غير متاح</span>;
    if (cell.status === "unresolved") return <span className="text-amber-800">اختيار غير متحقق</span>;
    if (cell.status === "included_in_base") return <span className="text-slate-500">ضمن تكلفة المنتج</span>;
    if (cell.status === "historical_unavailable") return <span className="text-slate-500">تفصيل غير متاح</span>;
    if (cell.status === "not_required") return <span className="text-slate-500">{kind === "service" ? "غير محدد" : "غير مطلوب"}</span>;
    if (cell.status === "missing_cost" || cell.unit_cost == null) return <span className="text-amber-800">تكلفة ناقصة</span>;
    return formatMoney(cell.unit_cost * quantity);
}

function ColumnTotal({ column, groups }) {
    let total = 0;
    let missing = false;
    for (const group of groups) {
        const cell = group.cells?.find(value => value.key === column.key);
        if (cell?.status === "costed" && cell.unit_cost != null) total += cell.unit_cost * group.quantity;
        else if (!["not_required", "included_in_base"].includes(cell?.status)) missing = true;
    }
    return missing ? <><span className="text-amber-800">غير مكتمل</span><small className="block">المعروف {formatMoney(total)}</small></> : formatMoney(total);
}

function columnTitle(column) {
    if (/^(المنتج|خدمة|مكوّن|مكون):/.test(column.label || "")) return column.label;
    const prefix = column.kind === "service" ? "خدمة" : column.kind === "product" || column.kind === "base" ? "المنتج" : "مكوّن";
    return `${prefix}: ${column.label || "بند التكلفة"}`;
}

export function CostDetails({ row }) {
    const columns = row.cost_columns || [];
    const groups = row.cost_groups || [];
    return <div className="p-4 bg-slate-50" data-testid="sold-product-cost-details">
        <p className="mb-3 text-xs text-slate-600">جميع المبالغ بالريال. أعمدة المنتج والخدمات والمكونات تعرض إجمالي التكلفة لكمية الصف. تتجمع القطع ذات الخدمات والمكونات وتكلفة كل بند المتطابقة، مهما اختلف الاسم أو النص المطلوب. الخدمة غير المختارة تظهر «غير محدد».</p>
        {!groups.length ? <p className="text-amber-800">تفاصيل التكلفة غير متاحة — حدّث التكاليف للتحقق.</p> : <div className="overflow-x-auto sold-details-scroll">
            <table className="w-full text-right text-xs border-collapse" aria-label={`تفاصيل تكلفة ${row.name}`}>
                <thead className="bg-violet-100"><tr>
                    {columns.map(column => <th key={column.key} className="border p-3">إجمالي {columnTitle(column)}</th>)}
                    <th className="border p-3">تكلفة القطعة</th><th className="border p-3">الكمية</th><th className="border p-3">الإجمالي</th>
                </tr></thead>
                <tbody>{groups.map((group, index) => <tr key={group.key || index}>
                    {columns.map(column => <td key={column.key} className="border p-3"><CostCell cell={group.cells?.find(cell => cell.key === column.key)} kind={column.kind} quantity={group.quantity} /></td>)}
                    <td className="border p-3">{group.complete && group.unit_cost != null ? formatMoney(group.unit_cost) : <><span className="text-amber-800">غير مكتملة</span>{group.known_unit_cost != null && <small className="block">المعروف {formatMoney(group.known_unit_cost)}</small>}</>}</td>
                    <td className="border p-3">{formatInt(group.quantity)}</td>
                    <td className="border p-3">{group.complete && group.total_cost != null ? formatMoney(group.total_cost) : <><span className="text-amber-800">غير مكتمل</span>{group.known_total_cost != null && <small className="block">المعروف {formatMoney(group.known_total_cost)}</small>}</>}</td>
                </tr>)}</tbody>
                <tfoot className="font-bold bg-violet-50"><tr>{columns.map(column => <td key={column.key} className="border p-3"><ColumnTotal column={column} groups={groups} /></td>)}<td className="border p-3">الإجمالي</td><td className="border p-3">{formatInt(row.units_sold)}</td><td className="border p-3">{complete(row) ? formatMoney(row.total_cost) : "غير مكتمل"}</td></tr></tfoot>
            </table>
        </div>}
        {!complete(row) && <p className="mt-3 text-amber-800">{[...new Set((row.missing_cost_reasons || []).map(costReasonLabel))].join("، ") || "تكلفة ناقصة"}. تُستكمل الخيارات المفقودة القابلة للاسترجاع تلقائيًا؛ أما التكلفة غير المسجلة فتحتاج إضافتها في المنتج.</p>}
    </div>;
}

export function SoldTable({ rows, printable = false, allDetails = false, expanded = {}, onToggle }) {
    return <table className="w-full text-sm text-right border-collapse min-w-[760px]">
        <thead className="bg-slate-100 text-slate-700"><tr>{[...(!printable ? ["التفاصيل"] : []), "المنتج", "القطع المباعة", ...(!printable ? ["القطع حسب المنصة والحساب"] : []), "تكلفة القطعة", "إجمالي التكلفة", "الحالة"].map(label => <th key={label} className={`p-3 border-b border-slate-200 ${label === "القطع حسب المنصة والحساب" ? "product-traffic-sources-heading print:hidden" : ""}`}>{label}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => {
            const key = rowKey(row);
            const open = allDetails || (!printable && expanded[key]);
            const detailId = `sold-cost-${printable ? "print" : "screen"}-${index}`;
            return <Fragment key={key}>
                <tr className="border-b border-slate-100">
                    {!printable && <td className="p-2 w-12"><button type="button" aria-expanded={!!open} aria-controls={detailId} aria-label={`تفاصيل تكلفة ${row.name}`} onClick={() => onToggle(key)} className="rounded border border-violet-200 p-2 text-violet-800 hover:bg-violet-50">{open ? "▼" : "◀"}</button></td>}
                    <td className="p-3"><div className="flex items-center gap-3">{row.image_url && <img src={row.image_url} alt="" className="h-12 w-12 object-cover rounded" />}<div className="font-semibold">{printable ? row.name : <Link target="_blank" rel="noopener noreferrer" className="text-violet-800 underline" to={row.mezan_product_id ? `/products-v2?product=${encodeURIComponent(row.mezan_product_id)}&focus=cost` : `/products-v2?view=list&lookup_sku=${encodeURIComponent(row.sku || row.name || "")}`}>{row.name || "بدون اسم"}</Link>}<span className="block text-xs font-normal text-slate-500" dir="ltr">{row.sku || "—"}</span></div></div></td>
                    <td className="p-3 tabular-nums">{formatInt(row.units_sold)}</td>
                    {!printable && <td className="p-3 product-traffic-sources print:hidden"><ProductTrafficSources sources={row.traffic_sources} /></td>}
                    <td className="p-3 tabular-nums">{!complete(row) ? "غير مكتملة" : row.unit_cost_varies ? "تختلف حسب الخيارات" : row.unit_cost == null ? "غير مكتملة" : money(row.unit_cost)}</td>
                    <td className="p-3 tabular-nums">{complete(row) ? money(row.total_cost) : <><span className="text-amber-800">الإجمالي غير مكتمل</span><small className="block">المعروف {money(row.known_cost_total ?? 0)}</small></>}</td>
                    <td className="p-3"><span className={`rounded-full px-2 py-1 text-xs ${complete(row) ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-800"}`}>{complete(row) ? "مكتمل" : [...new Set((row.missing_cost_reasons || []).map(costReasonLabel))].join("، ") || "تكلفة ناقصة"}</span></td>
                </tr>
                {open && <tr id={detailId} className="sold-details-row"><td colSpan={printable ? 5 : 7}><CostDetails row={row} /></td></tr>}
            </Fragment>;
        })}</tbody>
    </table>;
}

export default function SoldProducts() {
    const [searchParams] = useSearchParams();
    const [from, setFrom] = useState(searchParams.get("from") || addDaysISO(todaySA(), -29));
    const [to, setTo] = useState(searchParams.get("to") || todaySA());
    const [filterByStatus, setFilterByStatus] = useState(false);
    const [selectedStatuses, setSelectedStatuses] = useState([]);
    const [range, setRange] = useState({ from: searchParams.get("from") || addDaysISO(todaySA(), -29), to: searchParams.get("to") || todaySA(), statuses: "default" });
    const [page, setPage] = useState(1);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [expanded, setExpanded] = useState({});
    const [revision, setRevision] = useState(0);
    const [updatedAt, setUpdatedAt] = useState(null);
    const [refreshResult, setRefreshResult] = useState("");
    const previousIncomplete = useRef(null);
    const manualRefresh = useRef(false);
    const summaryPrintRef = useRef(null);
    const detailsPrintRef = useRef(null);
    const printSummary = useReactToPrint({ contentRef: summaryPrintRef, documentTitle: `ملخص-المنتجات-${range.from}-${range.to}` });
    const printDetails = useReactToPrint({ contentRef: detailsPrintRef, documentTitle: `تفاصيل-تكاليف-المنتجات-${range.from}-${range.to}` });

    useEffect(() => {
        let active = true;
        const isManual = manualRefresh.current;
        manualRefresh.current = false;
        setLoading(true);
        setError("");
        api.get("/dashboard-v2/sold-products", { params: { from_date: range.from, to_date: range.to, statuses: range.statuses, ...(revision ? { refresh_costs: true } : {}) } })
            .then(({ data: result }) => {
                if (!active) return;
                const incomplete = result.items.filter(row => !complete(row));
                const newlyComplete = result.items.filter(row => complete(row) && previousIncomplete.current?.has(rowKey(row))).length;
                previousIncomplete.current = new Set(incomplete.map(rowKey));
                setData(result);
                const timestamp = new Date(result.updated_at || Date.now());
                setUpdatedAt(Number.isNaN(timestamp.getTime()) ? new Date() : timestamp);
                if (isManual) {
                    const reasons = [...new Set(incomplete.flatMap(row => (row.missing_cost_reasons || []).map(costReasonLabel)))];
                    setRefreshResult(`تم تحديث جميع منتجات الفترة (${formatInt(result.items.length)}). اكتملت تكلفة ${formatInt(newlyComplete)} منتج؛ المتبقي ${formatInt(incomplete.length)}${incomplete.length ? ` — ${reasons.join("، ") || "تكلفة ناقصة"}` : ""}.`);
                }
            })
            .catch(() => { if (active) { setData(null); setError("تعذر تحديث التكاليف. حاول مرة أخرى."); } })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [range, revision]);

    const count = data?.items?.length || 0;
    const incompleteCount = data?.items?.filter(row => !complete(row)).length || 0;
    useEffect(() => {
        if (!incompleteCount || loading) return undefined;
        const timer = setInterval(() => {
            if (document.visibilityState !== "hidden") setRevision(value => value + 1);
        }, 60000);
        return () => clearInterval(timer);
    }, [incompleteCount, loading, revision]);
    const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
    const current = Math.min(page, pages);
    const applyRange = event => {
        event.preventDefault();
        if (!isValidISODate(from) || !isValidISODate(to) || from > to) { setError("اختر تاريخ بداية ونهاية صحيحين، وتأكد أن البداية قبل النهاية."); return; }
        if (filterByStatus && !selectedStatuses.length) { setError("اختر حالة طلب واحدة على الأقل أو أوقف تصفية الحالات."); return; }
        setPage(1); setExpanded({}); setRefreshResult(""); previousIncomplete.current = null;
        setRange({ from, to, statuses: filterByStatus ? selectedStatuses.join(",") : "default" });
    };
    const selectedStatusLabel = range.statuses === "default" ? "حالات التقرير الافتراضية" : STATUS_OPTIONS.filter(option => range.statuses.split(",").includes(option.value)).map(option => option.label).join("، ");
    const pageRows = data?.items?.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE) || [];

    return <main dir="rtl" className="space-y-5 p-4 sm:p-6" data-testid="sold-products-page">
        <header className="flex flex-wrap items-center justify-between gap-4">
            <div><h1 className="text-2xl font-extrabold">المنتجات الأكثر مبيعًا</h1><p className="text-sm text-slate-500">تفصيل تكلفة المنتج وخدماته ومكوناته حسب خيارات العميل</p></div>
            <div className="flex gap-2">
                <button type="button" disabled={loading} onClick={() => { manualRefresh.current = true; setRefreshResult(""); setRevision(value => value + 1); }} className="rounded-lg bg-violet-700 px-4 py-2 font-bold text-white disabled:opacity-50">{loading ? "جارٍ تحديث التكاليف…" : "↻ تحديث جميع التكاليف"}</button>
                <details className="relative"><summary className="rounded-lg border px-4 py-2 cursor-pointer">طباعة ▾</summary><div className="absolute left-0 z-10 min-w-56 rounded-lg border bg-white p-2 shadow-lg"><button type="button" onClick={() => printSummary()} disabled={loading || !count} className="block w-full p-2 text-right disabled:opacity-50">طباعة الملخص فقط</button><button type="button" onClick={() => printDetails()} disabled={loading || !count} className="block w-full p-2 text-right disabled:opacity-50">طباعة مع تفاصيل التكاليف</button></div></details>
            </div>
        </header>
        <p className="text-sm text-slate-600">آخر تحديث: {updatedAt ? updatedAt.toLocaleString("ar-SA", { timeZone: "Asia/Riyadh" }) : "—"} · مكتمل: {formatInt(count - incompleteCount)} منتجًا · غير مكتمل: {formatInt(incompleteCount)} منتجات</p>
        {refreshResult && <p role="status" className="rounded-lg bg-violet-50 p-3 text-violet-900">{refreshResult}</p>}
        <form onSubmit={applyRange} className="flex flex-wrap items-end gap-3 rounded-xl bg-white p-4 border border-slate-200">
            <label className="min-w-44 flex-1 text-sm font-semibold">من تاريخ<DateInput value={from} onChange={e => setFrom(e.target.value)} /></label>
            <label className="min-w-44 flex-1 text-sm font-semibold">إلى تاريخ<DateInput value={to} onChange={e => setTo(e.target.value)} /></label>
            <fieldset className="min-w-44 flex-1 text-sm font-semibold"><legend>حالات الطلب · بدون تفعيل تُستخدم حالات التقرير الافتراضية</legend>
                <label className="flex items-center gap-2 py-2"><input type="checkbox" checked={filterByStatus} onChange={e => setFilterByStatus(e.target.checked)} /> تفعيل البحث حسب حالة الطلب</label>
                {filterByStatus && <div className="flex flex-wrap gap-3">{STATUS_OPTIONS.map(option => <label key={option.value} className="flex items-center gap-1"><input type="checkbox" checked={selectedStatuses.includes(option.value)} onChange={e => setSelectedStatuses(values => e.target.checked ? [...values, option.value] : values.filter(value => value !== option.value))} /> {option.label}</label>)}</div>}
            </fieldset>
            <button className="rounded-lg bg-slate-900 px-5 py-3 text-white font-bold" type="submit">عرض المبيعات</button>
        </form>
        {error && <p role="alert" className="text-red-700">{error}</p>}
        {loading ? <p role="status">جارٍ تحميل المنتجات…</p> : data && <section className="rounded-xl border border-slate-200 bg-white overflow-hidden">
            <div className="p-4 text-sm text-slate-600">من {range.from} إلى {range.to} · {selectedStatusLabel} · {count} منتج · غير مكتمل التكلفة أولًا، ثم حسب عدد القطع</div>
            {count ? <div className="overflow-x-auto"><SoldTable rows={pageRows} expanded={expanded} onToggle={key => setExpanded(values => ({ ...values, [key]: !values[key] }))} /></div> : <p className="p-8 text-center">لا توجد منتجات في هذه الفترة والحالة.</p>}
            {count > 0 && <CostSummary rows={pageRows} />}
            {pages > 1 && <nav aria-label="صفحات المنتجات" className="flex flex-wrap justify-center items-center gap-2 p-4"><button disabled={current === 1} onClick={() => setPage(current - 1)} className="px-3 py-2 border rounded disabled:opacity-40">السابق</button>{Array.from({ length: pages }, (_, i) => <button key={i} aria-current={current === i + 1 ? "page" : undefined} onClick={() => setPage(i + 1)} className={`px-3 py-2 border rounded ${current === i + 1 ? "bg-violet-700 text-white" : ""}`}>{i + 1}</button>)}<button disabled={current === pages} onClick={() => setPage(current + 1)} className="px-3 py-2 border rounded disabled:opacity-40">التالي</button></nav>}
            <CostSummary rows={data.items} final />
        </section>}
        <div className="absolute left-[-99999px] top-0" aria-hidden="true">{[false, true].map(withDetails => <div key={String(withDetails)} ref={withDetails ? detailsPrintRef : summaryPrintRef} dir="rtl" className="p-6 bg-white text-black" style={{ fontFamily: "Tahoma, Arial, sans-serif" }}>
            <style>{`@page { size: A4 landscape; margin: 10mm; } @media print { table { width: 100%; min-width: 0 !important; font-size: 8px !important; border-collapse: collapse; } th, td { padding: 3px !important; } thead { display: table-header-group; } tbody tr { break-inside: avoid; } .sold-details-row { break-inside: auto !important; } .sold-details-scroll { overflow: visible !important; } img { width: 20px !important; height: 20px !important; object-fit: cover; } }`}</style>
            <h1 className="text-xl font-bold mb-2">المنتجات الأكثر مبيعًا — {withDetails ? "تفاصيل التكاليف" : "الملخص"}</h1><p className="mb-4">من {range.from} إلى {range.to} · {selectedStatusLabel} · {count} منتج</p>
            <SoldTable rows={data?.items || []} printable allDetails={withDetails} />
            <CostSummary rows={data?.items || []} final />
        </div>)}</div>
    </main>;
}
