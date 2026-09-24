import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useReactToPrint } from "react-to-print";
import api from "../lib/api";
import { addDaysISO, todaySA } from "../lib/dates";
import { formatMoney, formatInt } from "../lib/format";
import DateInput, { isValidISODate } from "../components/DateInput";

const PAGE_SIZE = 20;
const STATUS_OPTIONS = [
    { value: "pending_review", label: "انتظار المراجعة" },
    { value: "reviewed", label: "تم المراجعة" },
    { value: "in_progress", label: "قيد التنفيذ" },
];

function CostSummary({ rows, final = false }) {
    const sold = rows.reduce((sum, row) => sum + row.units_sold, 0);
    const before = rows.reduce((sum, row) => sum + row.units_cancelled_before, 0);
    const after = rows.reduce((sum, row) => sum + row.units_returned_after, 0);
    const unclassified = rows.reduce((sum, row) => sum + row.units_unclassified, 0);
    const units = sold + after + unclassified;
    const knownCost = rows.reduce((sum, row) => sum + (row.total_cost ?? 0), 0);
    const uncertainCost = rows.reduce((sum, row) => sum + (row.uncertain_cost ?? 0), 0);
    const missing = rows.some(row => row.total_cost == null);
    const uncertainMissing = rows.some(row => row.uncertain_cost == null);
    return (
        <div className="flex flex-wrap gap-x-8 gap-y-2 bg-violet-50 p-4 text-sm font-bold" data-testid={final ? "sold-products-final-totals" : "sold-products-page-totals"}>
            <span>المباعة: {formatInt(sold)}</span>
            <span>الملغاة قبل التنفيذ، مستبعدة: {formatInt(before)}</span>
            <span>الملغاة أو المسترجعة بعد التنفيذ: {formatInt(after)}</span>
            <span>غير محسومة المرحلة، محسوبة مؤقتًا: {formatInt(unclassified)}</span>
            <span>القطع المحتسبة في الصافي: {formatInt(units)}</span>
            {final && <span>متوسط تكلفة القطعة المحتسبة: {missing ? "غير مكتمل" : units ? `${formatMoney(knownCost / units)} ر.س` : "—"}</span>}
            <span>{final ? "صافي تكلفة جميع القطع" : "صافي تكلفة قطع الصفحة"}: {missing ? "غير مكتمل" : `${formatMoney(knownCost)} ر.س`}</span>
            {unclassified > 0 && <span className="text-amber-800">منها تكلفة قطع غير محسومة المرحلة: {uncertainMissing ? "غير مكتملة" : `${formatMoney(uncertainCost)} ر.س`}</span>}
            {missing && <span className="text-amber-800">التكلفة المسجلة فقط: {formatMoney(knownCost)} ر.س · توجد قطع بلا تكلفة</span>}
        </div>
    );
}

function SoldTable({ rows, printable = false }) {
    return (
        <table className="w-full text-sm text-right border-collapse min-w-[1050px]">
            <thead className="bg-slate-100 text-slate-700">
                <tr>{["صورة المنتج", "اسم المنتج", "الرقم المخزني SKU", "عدد القطع المباعة خلال الفترة", "ملغاة قبل التنفيذ", "ملغاة أو مسترجعة بعد التنفيذ", "تعذر تحديد وقت الإلغاء", "تكلفة المنتج المسجلة", "صافي تكلفة القطع عدا الملغاة قبل التنفيذ"].map(label =>
                    <th key={label} className="p-3 border-b border-slate-200">{label}</th>)}</tr>
            </thead>
            <tbody>
                {rows.map((row, i) => (
                    <tr key={`${row.product_id}-${row.sku}-${i}`} className="border-b border-slate-100 break-inside-avoid">
                        <td className="p-2 w-20">{row.image_url ? <img src={row.image_url} alt={row.name} className="h-12 w-12 object-cover rounded" /> : "—"}</td>
                        <td className="p-3 font-semibold">{!printable
                            ? <Link target="_blank" rel="noopener noreferrer" className="text-violet-800 underline hover:text-violet-950" to={row.mezan_product_id ? `/products-v2?product=${encodeURIComponent(row.mezan_product_id)}&focus=cost` : `/products-v2?view=list&lookup_sku=${encodeURIComponent(row.sku || row.name || "")}`}>{row.name || "بدون اسم"}{row.cost_status !== "complete" && <span className="block text-xs text-amber-700">{row.mezan_product_id ? "أضف تكلفة المنتج ↗" : "ابحث عن المنتج لإضافة التكلفة ↗"}</span>}</Link>
                            : row.name || "بدون اسم"}</td>
                        <td className="p-3" dir="ltr">{row.sku || "—"}</td>
                        <td className="p-3 tabular-nums">{formatInt(row.units_sold)}</td>
                        <td className="p-3 tabular-nums">{formatInt(row.units_cancelled_before)}</td>
                        <td className="p-3 tabular-nums">{formatInt(row.units_returned_after)}</td>
                        <td className="p-3 tabular-nums">{formatInt(row.units_unclassified)}</td>
                        <td className="p-3 tabular-nums">{row.unit_cost == null ? "غير مسجلة" : <>{formatMoney(row.unit_cost)} ر.س <span className="block text-xs text-slate-500">{row.cost_source === "salla" ? "من سلة" : "من ميزان"}</span></>}</td>
                        <td className="p-3 tabular-nums">{row.total_cost == null ? "تكلفة غير مكتملة" : `${formatMoney(row.total_cost)} ر.س`}{row.units_unclassified > 0 && <span className="block text-xs text-amber-700">يشمل {row.uncertain_cost == null ? "تكلفة غير محسومة" : `${formatMoney(row.uncertain_cost)} ر.س`} لقطع غير محسومة المرحلة</span>}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
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
    const printRef = useRef(null);
    const print = useReactToPrint({ contentRef: printRef, documentTitle: `المنتجات-المباعة-${range.from}-${range.to}` });

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError("");
        api.get("/dashboard-v2/sold-products", { params: { from_date: range.from, to_date: range.to, statuses: range.statuses } })
            .then(({ data: result }) => { if (active) setData(result); })
            .catch(() => { if (active) { setData(null); setError("تعذر تحميل المبيعات. حاول مرة أخرى."); } })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [range]);

    const count = data?.count || 0;
    const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
    const current = Math.min(page, pages);
    const applyRange = (event) => {
        event.preventDefault();
        if (!isValidISODate(from) || !isValidISODate(to) || from > to) {
            setError("اختر تاريخ بداية ونهاية صحيحين، وتأكد أن البداية قبل النهاية.");
            return;
        }
        if (filterByStatus && !selectedStatuses.length) {
            setError("اختر حالة طلب واحدة على الأقل أو أوقف تصفية الحالات.");
            return;
        }
        setPage(1);
        setRange({ from, to, statuses: filterByStatus ? selectedStatuses.join(",") : "default" });
    };
    const selectedStatusLabel = range.statuses === "default" ? "حالات التقرير الافتراضية" : STATUS_OPTIONS.filter(option => range.statuses.split(",").includes(option.value)).map(option => option.label).join("، ");
    const pageRows = data?.items?.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE) || [];
    const printPages = Array.from({ length: pages }, (_, index) => data?.items?.slice(index * PAGE_SIZE, (index + 1) * PAGE_SIZE) || []);

    return (
        <main dir="rtl" className="space-y-5 p-4 sm:p-6" data-testid="sold-products-page">
            <header className="flex flex-wrap items-center justify-between gap-4">
                <div><h1 className="text-2xl font-extrabold">المنتجات الأكثر مبيعًا</h1><p className="text-sm text-slate-500">صافي تكلفة القطع = تكلفة المباعة والملغاة أو المسترجعة بعد التنفيذ وغير محسومة المرحلة؛ تُستبعد الملغاة قبل التنفيذ. تكلفة ميزان أولًا ثم سلة.</p></div>
                <button type="button" onClick={print} disabled={loading || !count} className="rounded-lg bg-violet-700 px-4 py-2 font-bold text-white disabled:opacity-50">طباعة جميع المنتجات / حفظ PDF</button>
            </header>
            <form onSubmit={applyRange} className="flex flex-wrap items-end gap-3 rounded-xl bg-white p-4 border border-slate-200">
                <label className="min-w-44 flex-1 text-sm font-semibold">من تاريخ<DateInput value={from} onChange={e => setFrom(e.target.value)} /></label>
                <label className="min-w-44 flex-1 text-sm font-semibold">إلى تاريخ<DateInput value={to} onChange={e => setTo(e.target.value)} /></label>
                <fieldset className="min-w-44 flex-1 text-sm font-semibold">
                    <legend>حالات الطلب · بدون تفعيل تُستخدم حالات التقرير الافتراضية</legend>
                    <label className="flex items-center gap-2 py-2"><input type="checkbox" checked={filterByStatus} onChange={e => setFilterByStatus(e.target.checked)} /> تفعيل البحث حسب حالة الطلب</label>
                    {filterByStatus && <div className="flex flex-wrap gap-3">
                        {STATUS_OPTIONS.map(option => <label key={option.value} className="flex items-center gap-1">
                            <input type="checkbox" checked={selectedStatuses.includes(option.value)} onChange={e => setSelectedStatuses(current => e.target.checked ? [...current, option.value] : current.filter(value => value !== option.value))} /> {option.label}
                        </label>)}
                    </div>}
                </fieldset>
                <button className="rounded-lg bg-slate-900 px-5 py-3 text-white font-bold" type="submit">عرض المبيعات</button>
            </form>
            {error && <p role="alert" className="text-red-700">{error}</p>}
            {loading ? <p>جارٍ تحميل المنتجات…</p> : data && <section className="rounded-xl border border-slate-200 bg-white overflow-hidden">
                <div className="p-4 text-sm text-slate-600">من {range.from} إلى {range.to} · {selectedStatusLabel} · {count} منتج · بدون تكلفة أولًا، ثم حسب عدد القطع{data.incomplete_count > 0 && ` · ${data.incomplete_count} منتج بتكلفة غير مكتملة`}</div>
                <p className="px-4 pb-3 text-xs text-amber-800">أعداد الإلغاء والاسترجاع تشمل الطلبات الملغاة حتى إن لم تكن حالتها ضمن تصفية المبيعات. تُستبعد من الصافي القطع المثبت إلغاؤها قبل التنفيذ فقط. تدخل القطع غير محسومة المرحلة في الصافي مؤقتًا وتظهر تكلفتها منفصلة؛ السجلات القديمة قد تكون ناقصة.</p>
                {count ? <div className="overflow-x-auto"><SoldTable rows={pageRows} /></div> : <p className="p-8 text-center">لا توجد منتجات في هذه الفترة والحالة.</p>}
                {count > 0 && <CostSummary rows={pageRows} />}
                {pages > 1 && <nav aria-label="صفحات المنتجات" className="flex flex-wrap justify-center items-center gap-2 p-4">
                    <button disabled={current === 1} onClick={() => setPage(current - 1)} className="px-3 py-2 border rounded disabled:opacity-40">السابق</button>
                    {Array.from({ length: pages }, (_, i) => <button key={i} aria-current={current === i + 1 ? "page" : undefined} onClick={() => setPage(i + 1)} className={`px-3 py-2 border rounded ${current === i + 1 ? "bg-violet-700 text-white" : ""}`}>{i + 1}</button>)}
                    <button disabled={current === pages} onClick={() => setPage(current + 1)} className="px-3 py-2 border rounded disabled:opacity-40">التالي</button>
                </nav>}
                <CostSummary rows={data.items} final />
            </section>}
            <div className="absolute left-[-99999px] top-0" aria-hidden="true">
                <div ref={printRef} dir="rtl" className="p-6 bg-white text-black" style={{ fontFamily: "Tahoma, Arial, sans-serif" }}>
                    <style>{`@page { size: A4 landscape; margin: 10mm; } @media print { table { width: 100%; min-width: 0 !important; font-size: 8px !important; border-collapse: collapse; } th, td { padding: 2px !important; } thead { display: table-header-group; } tr { break-inside: avoid; height: 24px; } img { width: 20px !important; height: 20px !important; object-fit: cover; } section { break-inside: avoid; } }`}</style>
                    <h1 className="text-xl font-bold mb-2">المنتجات الأكثر مبيعًا</h1>
                    <p className="mb-4">من {range.from} إلى {range.to} · {selectedStatusLabel} · {count} منتج · بدون تكلفة أولًا، ثم حسب عدد القطع</p>
                    <p className="mb-2 text-xs">صافي التكلفة يشمل جميع القطع باستثناء الملغاة المثبتة قبل التنفيذ. تكلفة غير محسومة المرحلة محسوبة مؤقتًا وتظهر منفصلة؛ قد تنقص بعض الحالات التاريخية.</p>
                    {printPages.map((rows, index) => <section key={index} style={{ breakAfter: index < printPages.length - 1 ? "page" : "auto" }}>
                        {pages > 1 && <p>صفحة {index + 1} من {pages}</p>}
                        {rows.length > 0 && <SoldTable rows={rows} printable />}
                        {rows.length > 0 && <CostSummary rows={rows} />}
                    </section>)}
                    <CostSummary rows={data?.items || []} final />
                </div>
            </div>
        </main>
    );
}
