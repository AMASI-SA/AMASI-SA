import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import api from "../lib/api";

const qoyodBase = "legacy.integrations_v2.qoyod";
const qoyodTabLabels = {
    [qoyodBase]: "الحالة",
    [qoyodBase + ".exceptions"]: "الاستثناءات",
    [qoyodBase + ".invoices"]: "فواتير قيود",
    [qoyodBase + ".reconciliation"]: "المطابقة",
    [qoyodBase + ".settings"]: "الإعدادات",
};

function QoyodNavigation({ selectedId }) {
    const { user } = useAuth();
    const tabs = (user?.web_page_access?.pages || []).filter(({ id }) => Object.hasOwn(qoyodTabLabels, id));
    if (tabs.length < 2) return null;
    return <nav aria-label="صفحات قيود" className="flex flex-wrap gap-2">
        {tabs.map(({ id, to }) =>
            <Link key={id} to={to} aria-current={selectedId === id ? "page" : undefined}
                className={selectedId === id ? "rounded-lg bg-emerald-900 px-3 py-2 text-sm text-white" : "rounded-lg border px-3 py-2 text-sm"}>
                {qoyodTabLabels[id]}
            </Link>)}
    </nav>;
}

export default function EmployeeLegacyRead({ page }) {
    const [day, setDay] = useState(() => new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Riyadh" }).format(new Date()));
    const [number, setNumber] = useState(1);
    const [attempt, setAttempt] = useState(0);
    const [result, setResult] = useState(null);
    const [error, setError] = useState("");

    useEffect(() => {
        const controller = new AbortController();
        setResult(null);
        setError("");
        api.get(`/web-page-access-v1/legacy-read/${encodeURIComponent(page.id)}`, {
            params: { page: number, ...(page.date_filter ? { day } : {}) }, signal: controller.signal,
        }).then(({ data }) => {
            if (!controller.signal.aborted) setResult(data);
        }).catch((reason) => {
            if (controller.signal.aborted) return;
            setError(reason?.response?.status === 403
                ? "عرض هذه البيانات يحتاج إلى صلاحية الصفحة والحسابات المرتبطة بها."
                : "تعذّر تحميل هذه الصفحة.");
        });
        return () => controller.abort();
    }, [number, page.id, page.date_filter, day, attempt]);

    return <section dir="rtl" className="space-y-4 rounded-2xl border bg-white p-5">
        <h1 className="text-xl font-extrabold">{page.label}</h1>
        {Object.hasOwn(qoyodTabLabels, page.id) && <QoyodNavigation selectedId={page.id} />}
        {page.date_filter && <label className="inline-flex items-center gap-3">تاريخ التقرير
            <input type="date" value={day} onChange={event => {
                if (event.target.value) { setNumber(1); setDay(event.target.value); }
            }} className="rounded-lg border px-3 py-2" />
        </label>}
        {error && <p role="alert">{error} <button onClick={() => setAttempt(value => value + 1)} className="underline">إعادة المحاولة</button></p>}
        {!result && !error && <p role="status">جارٍ تحميل البيانات…</p>}
        {result && <>
            {result.description && <p className="text-sm text-slate-600">{result.description}</p>}
            {result.rows.length > 0 ? <div className="overflow-x-auto"><table className="w-full text-right text-sm">
                <thead><tr>{result.columns.map(({ key, label }) =>
                    <th key={key} className="border-b px-3 py-2">{label}</th>)}</tr></thead>
                <tbody>{result.rows.map((row, index) => <tr key={index}>
                    {result.columns.map(({ key }) => <td key={key} className="border-b px-3 py-2">{row[key] ?? "—"}</td>)}
                </tr>)}</tbody>
            </table></div> : <p role="status">لا توجد سجلات ضمن الصلاحيات الممنوحة.</p>}
            {(number > 1 || result.has_more) && <nav aria-label="صفحات الجدول" className="flex items-center gap-3">
                <button type="button" disabled={number === 1} onClick={() => setNumber(number - 1)} className="rounded-lg border px-3 py-1 disabled:opacity-50">السابق</button>
                <span>صفحة {number}</span>
                <button type="button" disabled={!result.has_more} onClick={() => setNumber(number + 1)} className="rounded-lg border px-3 py-1 disabled:opacity-50">التالي</button>
            </nav>}
        </>}
    </section>;
}
