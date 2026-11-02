import React, { useEffect, useRef, useState } from "react";
import { getOnboardingDomainContext } from "../../../services/onboardingDomainContext";

const fallback = { getOnboardingDomainContext };
const blank = value => value === undefined || value === null || value === "" ? "غير مدخل" : String(value);
const money = (amount, currency) => `${blank(amount)}${currency ? ` ${currency}` : ""}`;
const method = value => ({ bank_transfer: "تحويل بنكي", card_terminal: "شبكة POS", cash: "نقد" }[value] || blank(value));
const state = value => ({ active: "نشط", inactive: "غير نشط", approved: "معتمد", rejected: "مرفوض — بلا أثر مالي", missing: "ربط ناقص", verified: "موثق", pending: "بانتظار المراجعة" }[value] || blank(value));
const failure = source => source.invalid ? "استجابة المصدر لا تطابق العقد؛ لا يمكن اعتمادها."
    : source.httpStatus === 403 ? "صلاحية قراءة هذا المصدر غير متاحة."
        : source.httpStatus === 401 ? "يلزم تسجيل الدخول لقراءة هذا المصدر."
            : source.httpStatus === 409 ? "الهوية أو الدليل غير مكتمل؛ يلزم مراجعة الربط."
                : source.httpStatus === 423 ? "المصدر محجوب بحاجز الكتابات؛ لم يتم تجاوزه."
                    : "تعذرت قراءة هذا المصدر؛ لا يعني ذلك أن الرصيد صفر أو أن القسم لا ينطبق.";
const button = "rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold disabled:opacity-40";

function Table({ columns, rows, empty = "لا توجد سجلات في هذا المصدر. هذا لا يثبت رصيدًا صفريًا." }) {
    if (!rows.length) return <p className="rounded-lg bg-slate-50 p-3 text-sm" role="status">{empty}</p>;
    return <div className="overflow-x-auto"><table className="w-full text-right text-sm"><thead><tr>{columns.map(([label]) => <th className="border-b px-3 py-2" key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr className="border-b" key={row.id || row.courier_key || row.provider || index}>{columns.map(([label, cell]) => <td className="px-3 py-3 align-top" key={label}>{cell(row)}</td>)}</tr>)}</tbody></table></div>;
}

function Body({ source, stage, search, native }) {
    const d = source.data;
    const filter = rows => rows.filter(row => !search || [row.name, row.display_name, row.title, row.provider, row.courier_key, row.id, row.city].some(v => String(v || "").toLowerCase().includes(search.toLowerCase())));
    if (source.key === "couriers") return <><p>دليل الإعدادات التشغيلية، وليس مصدر رصيد افتتاحي. الربط بهوية MZ2 يظهر فقط عند تطابق المعرّف الصريح؛ لا مطابقة بالأسماء.</p><Table rows={filter(d.items)} columns={[
        ["الشركة", r => r.display_name], ["نوع التشغيل", r => r.payment_mode === "deferred" ? "آجل" : "مسبق الدفع"],
        ["هوية MZ2", r => native === undefined ? "لم يُثبت مصدر الهويات" : native.some(c => c.courier_key === r.courier_key) ? "هوية موجودة — مراجعة العقد مستقلة" : "غير مرتبطة بهوية موثقة"],
        ["البنك", r => r.bank_account_name || "ربط البنك ناقص"], ["توثيق البنك", r => state(r.verification_status)],
    ]} /></>;
    if (source.key === "native") return <Table rows={filter(d.couriers)} empty="لا توجد هوية شركة شحن في MZ2. القائمة التشغيلية لا تنشئ الهوية المالية تلقائيًا." columns={[
        ["الشركة", r => r.name], ["الحالة", r => state(r.status)], ["العقد المعتمد", r => d.contracts.some(c => c.party_id === r.courier_key && c.status === "approved") ? "يوجد عقد معتمد؛ يلزم فحص سريانه" : "عقد معتمد غير موجود"],
        ["المرجع", r => <bdi>{r.courier_key}</bdi>],
    ]} />;
    if (source.key === "drivers") return <><p>الأجرة هنا إعداد تشغيلي لكل توصيلة، وليست مبلغًا مستحقًا أو رصيد COD.</p><Table rows={filter(d.items)} columns={[
        ["المندوب", r => r.name], ["المدينة", r => blank(r.city)], ["الحالة", r => state(r.status)],
        ["أجرة التوصيلة التشغيلية", r => blank(r.delivery_fee)], ["طلبات تم توصيلها", r => blank(r.delivery_counts?.delivered_count)],
    ]} /></>;
    if (source.key === "shipping") return <><p>التوافق البرمجي لا يعني اعتماد دفعة أو تفعيل P02.</p><p>هويات المندوبين المتاحة: {d.store_drivers.length}. موانع المرحلة: {d.stages?.["9"]?.reasons?.length ?? "غير معلوم"}.</p><p>الشبكة تنشئ ذمة POS عند المراجعة فقط. وصول البنك تسوية لاحقة مستقلة.</p></>;
    if (source.key === "statement") return <><p>كشف MZ2 الحالي؛ لا يُنسخ إلى الأرصدة الافتتاحية تلقائيًا. الذمة لنا والأجرة علينا منفصلتان دون مقاصة.</p><Table rows={[d]} columns={[
        ["COD لنا", r => money(r.cod_receivable, "SAR")], ["أجرة مستحقة علينا", r => money(r.payable, "SAR")],
        ["تحصيل COD مسجل", r => money(r.collections, "SAR")], ["أجرة مدفوعة", r => money(r.payments, "SAR")],
    ]} /></>;
    if (source.key === "cash") return <><p>أدلة نقد مؤكد للطلبات الملتقطة فقط؛ ليست رصيدًا افتتاحيًا ولا دليل وصول البنك. لا يُستنتج النقد التاريخي.</p><Table rows={[d.totals || {}]} columns={[
        ["نقد مؤكد", r => blank(r.confirmed_cash)], ["نقد مؤهل للمطابقة", r => blank(r.eligible_confirmed_cash)], ["فرق النقد", r => blank(r.variance)],
        ["توريد مطابق", r => blank(r.matched_handover)], ["نقد مؤكد متبقٍ", r => blank(r.confirmed_cash_remaining)],
    ]} /><p>تغطية المطابقة: {d.coverage?.complete === true ? "مكتملة ضمن الأدلة الملتقطة" : "غير مكتملة"}. عدد الأدلة: {d.items.length}.</p></>;
    if (source.key === "history") return <><p>آخر 50 مراجعة أصلية كحد أقصى؛ ليست رصيد POS الحالي أو قائمة الدفعات المنتظرة.</p><Table rows={d.items} columns={[
        ["الطريقة", r => method(r.payment_method)], ["الحالة", r => state(r.status)], ["المبلغ", r => money(r.amount, r.destination?.currency)],
        ["الوجهة", r => r.destination?.display_name || (r.status === "rejected" ? "لا قيد" : "هوية غير مكتملة")], ["وقت المراجعة", r => blank(r.reviewed_at)],
    ]} />{d.has_more && <p>توجد مراجعات أقدم غير معروضة هنا.</p>}</>;
    if (source.key === "fees") return <Table rows={filter(d.items)} columns={[
        ["المزود", r => r.provider], ["النسبة %", r => blank(r.percentage)], ["المبلغ الثابت", r => money(r.fixed_amount, r.currency)],
        ["الحد الأدنى", r => r.minimum === null || r.minimum === undefined ? "غير محدد بالعقد" : money(r.minimum, r.currency)],
        ["الحد الأعلى", r => r.maximum === null || r.maximum === undefined ? "غير محدد بالعقد" : money(r.maximum, r.currency)],
        ["الضريبة", r => blank(r.vat_treatment)], ["الفترة", r => `${blank(r.effective_from)} ← ${r.effective_to || "بلا نهاية محددة"}`],
    ]} />;
    if (source.key === "bindings") return <Table rows={filter(d.bindings)} columns={[
        ["المزود", r => r.provider_label || r.provider], ["البنك", r => r.bank_account_name || "غير مرتبط"],
        ["حالة الدليل", r => state(r.verification_status)],
    ]} />;
    if (source.key === "recurring") return <><p>هذه بيانات الالتزامات التشغيلية القائمة. لا نستخدم ملخص الاستحقاق التشغيلي كرصد افتتاحي؛ اختيار الرصيد الموثق ومراجعته مستقلان.</p><Table rows={filter(d.items)} columns={[
        ["الالتزام", r => r.title], ["النوع", r => blank(r.expense_type)], ["الحالة", r => state(r.status)],
        ["بداية التغطية", r => blank(r.start_date)], ["موعد الاستحقاق التالي", r => blank(r.next_due_date)],
    ]} /></>;
    if (source.key === "facts") {
        const rows = filter(d.items).filter(r => stage !== "prepaid" || r.category === "prepaid_expense");
        return <><p>مصادر موثقة مستقلة. لا جمع بين عملات مختلفة ولا مقاصة بين لنا وعلينا.</p><Table rows={rows} columns={[
            ["الجهة", r => r.display_name], ["الاتجاه", r => ["prepaid_expense", "other_receivable", "input_vat"].includes(r.category) ? "لنا" : ["accrued_expense", "other_payable", "sales_vat_payable"].includes(r.category) ? "علينا" : "تصنيف يحتاج مراجعة"],
            ["المبلغ", r => money(r.amount, r.currency)], ["القطع", r => blank(r.cutover_date)], ["الدليل", r => blank(r.evidence)],
        ]} /></>;
    }
    return null;
}

export default function OpeningDomainContext({ stage, transport = fallback }) {
    const [sources, setSources] = useState([]), [details, setDetails] = useState([]), [loading, setLoading] = useState(true), [detailBusy, setDetailBusy] = useState(false);
    const [selection, setSelection] = useState(""), [search, setSearch] = useState(""), [revision, setRevision] = useState(0);
    const sequence = useRef(0);
    useEffect(() => {
        let active = true; sequence.current += 1; setSources([]); setDetails([]); setSelection(""); setSearch(""); setLoading(true); setDetailBusy(false);
        Promise.resolve().then(() => transport.getOnboardingDomainContext(stage)).then(result => { if (active) setSources(result.sources); })
            .catch(() => { if (active) setSources([{ key: "load", label: "مصادر هذا القسم", status: "error" }]); }).finally(() => { if (active) setLoading(false); });
        return () => { active = false; sequence.current += 1; };
    }, [stage, transport, revision]);
    const native = sources.find(s => s.key === "native" && s.status === "ready")?.data.couriers;
    const driverSource = sources.find(s => s.key === "drivers" && s.status === "ready")?.data.items
        || sources.find(s => s.key === "shipping" && s.status === "ready")?.data.store_drivers;
    const identityKey = stage === "drivers" ? "id" : "courier_key";
    const choices = (stage === "drivers" ? driverSource || [] : native || []).filter(row => typeof row[identityKey] === "string" && row[identityKey].trim());
    const identitySourceAvailable = stage === "drivers" ? driverSource !== undefined : native !== undefined;
    async function loadSelected() {
        if (!selection || detailBusy) return;
        const version = ++sequence.current; setDetailBusy(true); setDetails([]);
        try { const result = await transport.getOnboardingDomainContext(stage, stage === "drivers" ? { driverId: selection } : { courierId: selection }); if (version === sequence.current) setDetails(result.sources); }
        catch (_) { if (version === sequence.current) setDetails([{ key: "detail", label: "قراءة الجهة المختارة", status: "error" }]); }
        finally { if (version === sequence.current) setDetailBusy(false); }
    }
    return <section dir="rtl" className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5" aria-label="السياق القائم للمرحلة">
        <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-bold">المصادر الحالية للمراجعة</h3><button type="button" className={button} disabled={loading || detailBusy} onClick={() => setRevision(v => v + 1)}>تحديث القراءة</button></div>
        <p className="text-sm text-slate-600">عرض فقط. لا يستورد أرصدة، ولا ينشئ هوية أو اعتمادًا أو قيدًا.</p>
        <label className="block text-sm">البحث في المصادر<input aria-label="البحث في المصادر" className="mt-1 w-full rounded-lg border p-2" value={search} onChange={e => setSearch(e.target.value)} /></label>
        {loading && <p role="status">جارٍ قراءة المصادر…</p>}
        {[...sources, ...details].map(source => <section className="space-y-3 rounded-xl border p-4 text-sm" key={source.key} aria-label={source.label}><h4 className="font-bold">{source.label}</h4>{source.status === "error" ? <p role="alert" className="text-amber-900">{failure(source)}</p> : <Body source={source} stage={stage} search={search} native={native} />}</section>)}
        {["drivers", "courier_contracts", "courier_balances"].includes(stage) && <div className="space-y-3"><label className="block">اختر جهة لقراءة كشفها<select aria-label="جهة كشف MZ2" className="block w-full rounded-lg border p-2" value={selection} disabled={loading || !choices.length} onChange={e => { sequence.current += 1; setSelection(e.target.value); setDetails([]); setDetailBusy(false); }}><option value="">اختر صراحة</option>{choices.map(r => <option key={r[identityKey]} value={r[identityKey]}>{r.name || r[identityKey]}</option>)}</select></label>{!loading && !choices.length && <p role="status">{identitySourceAvailable ? "لا توجد جهة ذات هوية صالحة لقراءة الكشف في المصدر المحمّل. راجع إعداد الهوية؛ لا يُستنتج منها رصيد صفري." : "تعذر تحميل مصدر هويات الكشف. راجع خطأ المصدر أعلاه ثم حدّث القراءة؛ لا تُستخدم الأسماء بدل الهوية."}</p>}<button type="button" className={button} disabled={!selection || loading || detailBusy} onClick={loadSelected}>قراءة كشف الجهة المختارة</button>{detailBusy && <p role="status">جارٍ قراءة الكشف…</p>}</div>}
        {stage === "drivers" && <p className="rounded-xl bg-amber-50 p-3 text-sm">ذمة COD ≠ النقد الفعلي ≠ ذمة POS ≠ البنك. رفض إيصال الشبكة لا يخفض ذمة المندوب، واعتماده لا يعني وصول المبلغ للبنك.</p>}
        {["obligations", "prepaid"].includes(stage) && <p className="rounded-xl bg-amber-50 p-3 text-sm">التأمينات/الودائع: لا يوجد هنا عقد تصنيف مستقل مثبت؛ لا تُحوّل إلى ذمة أو مدفوع مقدمًا تلقائيًا.</p>}
    </section>;
}
