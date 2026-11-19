import { lazy, Suspense, useEffect, useState } from "react";
import api from "../lib/api";

const Overview = lazy(() => import("./AdsManager"));
const Cohorts = lazy(() => import("./CustomerCohortReport"));
const Reports = lazy(() => import("./MarketingReports"));
const providers = new Set(["snapchat", "tiktok", "meta", "google"]);
const statusLabels = {
    connected: "متصل", disconnected: "غير متصل", not_connected: "غير متصل", planned: "مخطط",
    unknown: "غير معروف", healthy: "سليم", expired: "منتهي", needs_reauth: "يتطلب تجديد الربط",
    active: "نشط", pending: "بانتظار المراجعة", completed: "مكتمل", failed: "تعذّر التنفيذ",
    acknowledged: "تم الاطلاع", stopped: "متوقف", instagram: "إنستغرام", tiktok: "تيك توك", whatsapp: "واتساب",
};
function formatValue(value) {
    if (value == null || value === "" || typeof value === "object") return "—";
    if (typeof value === "string" && /^\d{4}-\d\d-\d\dT/.test(value)) {
        const date = new Date(value);
        if (!Number.isNaN(date.getTime())) return date.toLocaleString("ar-SA", { timeZone: "Asia/Riyadh" });
    }
    return Object.hasOwn(statusLabels, value) ? statusLabels[value] : String(value);
}

function SavedRecords({ page }) {
    const [filter, setFilter] = useState("");
    const [order, setOrder] = useState("");
    const [offset, setOffset] = useState(0);
    const [version, setVersion] = useState(0);
    const [result, setResult] = useState(null);
    const [error, setError] = useState(false);
    const searchOrders = ["intelligence.delivery", "intelligence.tracking"].includes(page.id);
    useEffect(() => {
        const controller = new AbortController();
        setResult(null);
        setError(false);
        api.get("/web-page-access-v1/marketing-read/" + encodeURIComponent(page.id), {
            signal: controller.signal, params: { q: order || undefined, offset },
        }).then(({ data }) => { if (!controller.signal.aborted) setResult(data); })
          .catch(() => { if (!controller.signal.aborted) setError(true); });
        return () => controller.abort();
    }, [page.id, order, offset, version]);
    return <section className="space-y-4 rounded-2xl border bg-white p-5" dir="rtl">
        <h1 className="text-2xl font-bold">{page.label}</h1>
        {searchOrders && <form className="flex gap-2" onSubmit={event => {
            event.preventDefault(); setOffset(0); setOrder(filter.trim());
        }}>
            <input className="rounded-lg border px-3 py-2" aria-label="رقم الطلب" placeholder="رقم الطلب"
                value={filter} onChange={event => setFilter(event.target.value)} />
            <button className="rounded-lg border px-4" type="submit">بحث</button>
        </form>}
        {error ? <p role="alert">تعذّر تحميل الصفحة. <button className="underline" onClick={() => setVersion(n => n + 1)}>إعادة المحاولة</button></p>
            : !result ? <p role="status">جارٍ التحميل…</p>
            : !result.rows.length ? <p>لا توجد سجلات متاحة.</p> : <div className="overflow-x-auto"><table className="w-full text-right text-sm">
                <thead><tr>{result.columns.map(label => <th key={label} className="border-b p-3">{label}</th>)}</tr></thead>
                <tbody>{result.rows.map((cells, i) => <tr key={offset + i}>{cells.map((value, j) =>
                    <td key={result.columns[j]} className="border-b p-3">{formatValue(value)}</td>)}</tr>)}</tbody>
            </table></div>}
        {(offset > 0 || result?.has_more) && <div className="flex items-center gap-3">
            <button disabled={!offset || !result} onClick={() => setOffset(Math.max(0, offset - 20))}>السابق</button>
            <span>الصفحة {offset / 20 + 1}</span>
            <button disabled={!result?.has_more || offset >= 10000} onClick={() => setOffset(offset + 20)}>التالي</button>
        </div>}
    </section>;
}

export default function EmployeeMarketingRead({ page }) {
    const provider = page.id.split(".")[1];
    let content;
    if (page.id === "marketing.overview") content = <Overview />;
    else if (page.id === "marketing.cohorts") content = <Cohorts />;
    else if (page.id === "marketing.reports") content = <Reports readOnly />;
    else if (page.section === "marketing" && providers.has(provider)) content = <Reports readOnly fixedProvider={provider} />;
    else content = <SavedRecords key={page.id} page={page} />;
    return <Suspense fallback={<p role="status">جارٍ التحميل…</p>}>{content}</Suspense>;
}
