import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";

function currentRange() {
    const today = new Intl.DateTimeFormat("sv-SE", {timeZone: "Asia/Riyadh"}).format(new Date());
    return { date_from: today.slice(0, 8) + "01", date_to: today, split_on: "" };
}

export default function EmployeeAdditionalPage({ page }) {
    const snap = page.id === "extra.snapchat_period";
    const hasDates = snap || page.id === "extra.ads_report";
    const [draft, setDraft] = useState(currentRange);
    const [submitted, setSubmitted] = useState(() => snap ? null : {});
    const [number, setNumber] = useState(1);
    const [retry, setRetry] = useState(0);
    const [result, setResult] = useState(null);
    const [error, setError] = useState("");
    const valid = draft.date_from && draft.date_to && draft.date_from <= draft.date_to
        && (!snap || (draft.date_from < draft.split_on && draft.split_on <= draft.date_to));

    useEffect(() => {
        if (!submitted) return;
        const controller = new AbortController();
        setResult(null);
        setError("");
        api.get(`/web-page-access-v1/extra-read/${page.id}`, { params: page.id.startsWith("extra.inventory_") ? { page: number } : submitted, signal: controller.signal })
            .then(({ data }) => { if (!controller.signal.aborted) setResult(data); })
            .catch((err) => {
                if (controller.signal.aborted) return;
                const status = err.response?.status;
                const msg = status === 403 ? "صلاحيات حسابك لا تسمح بقراءة هذه البيانات."
                    : status === 422 ? "تعذّر إكمال التقرير لهذه الفترة. اختر فترة أقصر."
                    : status === 409 && snap ? "اختر حساب سناب من إعدادات المتجر أولًا."
                    : "تعذّر تحميل البيانات.";
                setError(msg);
            });
        return () => controller.abort();
    }, [page.id, number, retry, snap, submitted]);

    return <section dir="rtl" className="space-y-4 rounded-2xl border bg-white p-5">
        <h1 className="text-2xl font-bold">{page.label}</h1>
        {hasDates && <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
            event.preventDefault();
            if (valid) setSubmitted({ date_from: draft.date_from, date_to: draft.date_to, ...(snap && { split_on: draft.split_on }) });
        }}>
            {[["date_from", "من"], ["date_to", "إلى"], ...(snap ? [["split_on", "بداية الجزء الثاني"]] : [])].map(([key, label]) =>
                <label key={key} className="grid gap-1 text-sm">{label}<input aria-label={label} type="date" required value={draft[key]}
                    onChange={event => setDraft({...draft, [key]: event.target.value})} className="rounded-lg border px-3 py-2" /></label>)}
            <button className="rounded-lg border px-4 py-2 disabled:opacity-40" disabled={!valid}>{snap ? "فحص اتساق الفترة" : "عرض الفترة"}</button>
        </form>}
        {error && <p role="alert">{error} <button className="underline" onClick={() => setRetry(retry + 1)}>إعادة المحاولة</button></p>}
        {submitted && !error && !result && <p role="status">جارٍ تحميل البيانات…</p>}
        {result?.description && <p>{result.description}</p>}
        {result?.rows?.length > 0 && <div className="overflow-x-auto"><table className="w-full text-right text-sm">
            <thead><tr>{result.columns.map(col => <th className="border-b p-3" key={col.key} scope="col">{col.label}</th>)}</tr></thead>
            <tbody>{result.rows.map((row, i) => <tr key={i}>{result.columns.map(col => <td className="border-b p-3" key={col.key}>{row[col.key] ?? "—"}</td>)}</tr>)}</tbody>
        </table></div>}
        {result && !result.rows.length && !result.links && <p role="status">لا توجد سجلات متاحة ضمن هذه الصلاحية.</p>}
        {result && (number > 1 || result.has_more) && <nav className="flex items-center gap-3" aria-label="صفحات الجدول">
            <button disabled={number === 1} onClick={() => setNumber(n => n - 1)}>السابق</button>
            <span>صفحة {number}</span><button disabled={!result.has_more} onClick={() => setNumber(n => n + 1)}>التالي</button>
        </nav>}
        {result?.links?.map(link => <Link key={link.to} to={link.to} className="inline-block rounded-lg border px-4 py-2">{link.label}</Link>)}
    </section>;
}
