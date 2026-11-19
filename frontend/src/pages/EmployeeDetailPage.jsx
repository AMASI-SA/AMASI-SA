import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import api from "../lib/api";
import { detailHref, detailRouteAt, validDetailId } from "../lib/webDetailAccess";

const entityLabels = { courier: "شركة شحن", external_person: "جهة خارجية" };
const button = "rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-50";

function Results({ table }) {
    return <section className="space-y-2">
        <h2 className="font-bold">{table.title}</h2>
        {table.rows.length ? <div className="overflow-x-auto"><table className="w-full text-right text-sm">
            <thead><tr>{table.columns.map(column => <th key={column.key} className="border-b px-3 py-2">{column.label}</th>)}</tr></thead>
            <tbody>{table.rows.map((row, index) => <tr key={index}>
                {table.columns.map(column => <td key={column.key} className="border-b px-3 py-2">{row[column.key] ?? "—"}</td>)}
            </tr>)}</tbody>
        </table></div> : <p>لا توجد حركات متاحة في هذه الصفحة.</p>}
    </section>;
}

function DetailScreen({ page, identity, initialKind, start, end }) {
    const navigate = useNavigate();
    const [kind, setKind] = useState(initialKind || "courier");
    const [input, setInput] = useState(identity || "");
    const [number, setNumber] = useState(1);
    const [attempt, retry] = useState(0);
    const [result, setResult] = useState(null);
    const [status, setStatus] = useState(null);
    const [invalid, setInvalid] = useState(false);
    const isEntity = page.id === "detail.entity";
    const isOrder = page.id === "detail.order";
    const lookupLabel = isOrder ? "رقم الطلب" : page.id === "detail.analysis" ? "معرّف التحليل"
        : isEntity || page.id === "detail.supplier" ? "معرّف الجهة" : "معرّف الحساب";
    const requestKey = JSON.stringify([page.id, identity, kind, start, end, number, attempt]);
    useEffect(() => {
        if (isOrder && !identity) return undefined;
        const controller = new AbortController();
        api.get("/web-page-access-v1/details/" + page.id, {
            params: { identity, kind: isEntity ? kind : undefined, start, end, page: number }, signal: controller.signal,
        }).then(({ data }) => {
            if (!controller.signal.aborted) setResult({ key: requestKey, data });
        }).catch(reason => {
            if (!controller.signal.aborted) setStatus({ key: requestKey, code: reason.response?.status });
        });
        return () => controller.abort();
    }, [page.id, isOrder, identity, isEntity, kind, start, end, number, requestKey]);
    const data = result?.key === requestKey ? result.data : null;
    const error = status?.key === requestKey ? status : null;
    const more = data?.has_more || data?.tables?.some(table => table.has_more);
    function submit(event) {
        event.preventDefault();
        const value = input.trim();
        setInvalid(!validDetailId(value));
        const href = detailHref(page.id, value, kind);
        if (href) navigate(href);
    }
    return <section dir="rtl" className="space-y-5 rounded-2xl border bg-white p-5">
        <h1 className="text-xl font-extrabold">{page.label}</h1>
        {identity ? <Link to={page.to} className="text-sm font-bold underline">بحث آخر</Link> :
            <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
                {isEntity && <label>نوع الجهة<select value={kind} onChange={event => { setKind(event.target.value); setNumber(1); }}
                    className="block rounded-lg border px-3 py-2">{Object.entries(entityLabels).map(([key, label]) =>
                    <option key={key} value={key}>{label}</option>)}</select></label>}
                <label>{lookupLabel}<input value={input} onChange={event => { setInput(event.target.value); setInvalid(false); }}
                    dir="ltr" maxLength={200} required className="block rounded-lg border px-3 py-2" /></label>
                <button className={button} type="submit">عرض التفاصيل</button>
                {invalid && <p role="alert">أدخل المعرّف كما يظهر في سجل ميزان.</p>}
            </form>}
        {error ? <p role="alert">{[403, 404].includes(error.code) ? "السجل غير متاح ضمن صلاحياتك." : error.code === 409 ? "كشف المورد ينتظر اعتماد بيانات الدفتر المحاسبي." : "تعذّر تحميل البيانات."}
            <button className="mx-2 underline" onClick={() => retry(value => value + 1)}>إعادة المحاولة</button></p> :
            !data && (!isOrder || identity) && <p role="status">جارٍ تحميل البيانات…</p>}
        {data?.choices && (data.choices.length ? <ul className="divide-y">{data.choices.map(choice =>
            <li key={choice.id}><Link className="flex justify-between gap-2 py-2 font-bold underline"
                to={detailHref(page.id, choice.id, kind)}>{choice.label}<span dir="ltr" className="text-sm">{choice.id}</span></Link></li>)}</ul> :
            <p>لا توجد سجلات متاحة ضمن صلاحياتك.</p>)}
        {data?.facts && <><h2 className="font-bold">{data.title}</h2>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">{data.facts.map(fact => <div key={fact.label}>
                <dt className="text-sm text-slate-600">{fact.label}</dt><dd className="font-bold">{fact.value}</dd></div>)}</dl>
            {data.tables.map(table => <Results key={table.title} table={table} />)}</>}
        {(number > 1 || more) && <nav aria-label="صفحات النتائج" className="flex items-center gap-3">
            <button className={button} disabled={number <= 1} onClick={() => setNumber(number - 1)}>السابق</button>
            <span>صفحة {number}</span><button className={button} disabled={!more} onClick={() => setNumber(number + 1)}>التالي</button>
        </nav>}
    </section>;
}

export default function EmployeeDetailPage({ page }) {
    const location = useLocation();
    const route = detailRouteAt(location);
    if (!route || route.pageId !== page?.id) return <p role="alert">الصفحة غير متاحة.</p>;
    return <DetailScreen key={[page.id, route.kind, route.identity, route.start, route.end].join("/")} page={page} identity={route.identity} initialKind={route.kind} start={route.start} end={route.end} />;
}
