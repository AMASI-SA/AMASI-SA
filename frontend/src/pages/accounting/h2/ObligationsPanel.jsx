import React, { useEffect, useRef, useState } from "react";
import { AccountingSkeleton, EmptyState, ErrorState, EvidenceBadge, MoneyDisplay, StatusBadge } from "../AccountingUI";
import { isCurrencyCode } from "../currencyRules";
import { FACT_LABELS, G_VIEWS, loadObligations, obligationsFailure } from "./obligationsAdapter";

function Amount({ value, currency }) {
    return isCurrencyCode(currency) ? <MoneyDisplay value={value} currency={currency} /> : <span>غير متاح — العملة غير موثقة</span>;
}
function Blocked({ children }) {
    return <div role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-4"><StatusBadge value="BLOCKED_BY_BACKEND" /><p className="mt-2 text-sm">{children}</p></div>;
}
function Records({ view, items, search }) {
    const rows = items.filter(row => [row.display_name, row.title, row.label, row.reference, row.provider, FACT_LABELS[row.category]].some(value => String(value || "").toLocaleLowerCase().includes(search.toLocaleLowerCase())));
    if (!rows.length) return <EmptyState title="لا توجد سجلات مطابقة" description="لا يعني غياب السجلات أن الرصيد صفر." />;
    return <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="سجلات الالتزامات والضرائب"><table className="w-full min-w-[640px] text-right text-sm"><thead><tr><th className="p-3">الجهة / البند</th><th className="p-3">القيمة من المصدر</th><th className="p-3">الحالة والدليل</th></tr></thead><tbody>{rows.map((row, index) => <tr key={row.id || row.invoice_id || index} className="border-t border-slate-200">
        <td className="p-3">{row.display_name || row.title || row.label || row.provider || "غير متاح"}<p className="text-xs text-slate-500">{FACT_LABELS[row.category] || row.reference || row.invoice_id || row.id}</p>{view === "facts" && <p>عند القطع: {row.cutover_date || "غير متاح"}</p>}{view === "fees" && <p>{row.effective_from || "—"} ← {row.effective_to || "نهاية غير محددة"}</p>}</td>
        <td className="p-3">{view === "persons" ? "هوية فقط؛ الرصيد غير متاح" : view === "facts" ? <Amount value={row.amount} currency={row.currency} /> : view === "fees" ? <><p>النسبة: {row.percentage ?? "—"}%</p><p>الرسم الثابت: <Amount value={row.fixed_amount} currency={row.currency} /></p><p>الحد الأدنى: <Amount value={row.minimum} currency={row.currency} /></p><p>الحد الأعلى: <Amount value={row.maximum} currency={row.currency} /></p><p>معالجة الضريبة: {row.vat_treatment || "—"}</p></> : <><p>المدفوع: <Amount value={row.payment_amount} currency={row.currency} /></p><p>المستهلك قبل القطع: <Amount value={row.calculation?.consumed_before_cutover} currency={row.currency} /></p><p>المقدم المتبقي: <Amount value={row.calculation?.remaining_prepaid_after_cutover} currency={row.currency} /></p><p>التغطية: {row.coverage_start || "—"} ← {row.coverage_end || "—"}</p></>}</td>
        <td className="p-3">{view === "prepaid" ? <><StatusBadge value={row.source_stale === true ? "BLOCKED_BY_BACKEND" : "readonly"} label={row.source_stale === true ? undefined : row.source_stale !== false ? "حالة المصدر غير متاحة" : row.calculation?.eligible === true ? "مرشح مقدم — ليس رصيدًا مرحّلًا" : row.calculation?.eligible === false ? "غير مؤهل للمقدم" : "أهلية المقدم غير متاحة"} /><p>{row.calculation?.reason || ""}</p><p>{row.selection ? "اختيار موثق" : "لم يُوثق الاختيار"}</p></> : <StatusBadge value="readonly" label={view === "persons" ? "هوية MZ2" : row.status || "للقراءة فقط"} />}{view !== "persons" && <EvidenceBadge reference={row.evidence} />}</td>
    </tr>)}</tbody></table></div>;
}
export default function ObligationsPanel() {
    const [view, setView] = useState("facts");
    const [cutover, setCutover] = useState("");
    const [search, setSearch] = useState("");
    const [retry, setRetry] = useState(0);
    const [result, setResult] = useState({ state: "loading" });
    const version = useRef(0);
    useEffect(() => {
        const request = ++version.current;
        setResult({ state: "loading" });
        loadObligations(view, cutover).then(data => { if (request === version.current) setResult(data); }).catch(error => { if (request === version.current) setResult(obligationsFailure(error)); });
        return () => { version.current += 1; };
    }, [view, cutover, retry]);
    return <section dir="rtl" className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4" aria-label="الالتزامات والمقدم والضرائب">
        <header><h2 className="text-lg font-bold">الالتزامات والمقدم والضرائب</h2><p className="text-sm text-slate-600">عقود الإعداد والأدلة للقراءة فقط. القيم عند القطع أو من فواتير المصدر؛ لا تمثل الرصيد الحالي أو ترحيلًا ماليًا.</p></header>
        <div className="flex flex-wrap gap-2">{Object.entries(G_VIEWS).map(([key, item]) => <button type="button" key={key} aria-pressed={view === key} className={`min-h-11 rounded-lg border px-3 py-2 ${view === key ? "bg-emerald-800 text-white" : "bg-white"}`} onClick={() => { setResult({ state: "loading" }); setView(key); setSearch(""); }}>{item.label}</button>)}</div>
        {view === "prepaid" && <label className="block text-sm">تاريخ القطع لحساب المصدر<input type="date" aria-label="تاريخ القطع للمقدم" value={cutover} onChange={event => { setResult({ state: "loading" }); setCutover(event.target.value); }} className="ms-2 min-h-11 rounded-lg border px-3" /></label>}
        <label className="block text-sm">بحث في السجلات المحملة<input type="search" aria-label="بحث الالتزامات" value={search} onChange={event => setSearch(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border px-3" /></label>
        {result.state === "loading" && <AccountingSkeleton />}
        {result.state === "error" && <ErrorState onRetry={() => setRetry(value => value + 1)} />}
        {result.state === "blocked" && <Blocked>عقد Track G الأصلي غير جاهز في البيئة الحالية. يلزم إتاحة مسارات القراءة الأصلية؛ لا يوجد مصدر بديل.</Blocked>}
        {result.state === "date_required" && <EmptyState title="اختر تاريخ القطع" description="حساب المقدم يُعاد من Backend للتاريخ المحدد؛ لا تحسبه الواجهة." />}
        {result.state === "ready" && <>{result.blockers.length > 0 && <Blocked>توجد موانع في المصدر: {result.blockers.map(item => item.code).filter(Boolean).join("، ")}</Blocked>}<Records view={view} items={result.items} search={search} /></>}
        <Blocked>السداد والتحصيل والإطفاء الدوري وأرصدة الأطراف الحالية وفترات الضرائب والودائع تحتاج عقود Backend مستقلة. ضريبة المبيعات والمدخلات تُعرض كلٌ على حدة دون مقاصة.</Blocked>
    </section>;
}
