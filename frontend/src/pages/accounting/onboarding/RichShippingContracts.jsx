import React, { useEffect, useRef, useState } from "react";
import OpeningCourierEditor from "./OpeningCourierEditor";

const purposes = { contract: "العقد", shipping_tax: "ضريبة الشحن", commission_tax: "ضريبة العمولة" };
const copy = value => JSON.parse(JSON.stringify(value));
const requestId = () => globalThis.crypto?.randomUUID?.() || `rich-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const errorText = err => {
    const detail = err?.response?.data?.detail;
    return typeof detail === "string" ? detail : detail?.code || err?.message || "تعذر الاتصال";
};
const sourceNames = { contract: "عقد", invoice: "فاتورة", statement: "كشف", owner_confirmation: "إقرار المالك الموثق", legacy_copy: "نسخة تاريخية" };
const inclusion = value => value === true ? "شامل الضريبة" : value === false ? "غير شامل الضريبة" : "غير محدد";
function percentOfFraction(value) {
    const match = /^(\d+)(?:\.(\d+))?$/.exec(String(value));
    if (!match) return "غير محدد";
    const digits = match[2] || "";
    const whole = `${match[1]}${digits.padEnd(2, "0").slice(0, 2)}`.replace(/^0+(?=\d)/, "");
    const fraction = digits.slice(2).replace(/0+$/, "");
    return `${whole}${fraction ? `.${fraction}` : ""}`;
}
function riyadhDate(value) {
    if (!value) return "بلا نهاية محددة";
    const aware = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) ? `${value}:00+03:00` : value;
    const date = new Date(aware);
    return Number.isNaN(date.getTime()) ? "تاريخ غير صالح" : new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}
function ContractTerms({ terms, courierName, label }) {
    return <section aria-label={label} className="space-y-3 rounded-lg border p-3">
        <h4 className="font-bold">شروط {courierName || terms.courier_id}</h4>
        <dl className="grid gap-2 md:grid-cols-2">
            <div><dt>طريقة سداد الشركة</dt><dd>{terms.payment_mode === "prepaid" ? "مسبق الدفع" : terms.payment_mode === "postpaid" ? "آجل" : "غير محددة"}</dd></div>
            <div><dt>تكلفة الشحن</dt><dd>{terms.shipping_cost} ريال سعودي</dd></div>
            <div><dt>ضريبة الشحن</dt><dd>{terms.shipping_vat_percent}% · {inclusion(terms.shipping_cost_vat_inclusive)}</dd></div>
            <div><dt>ضريبة العمولة</dt><dd>{terms.commission_vat_percent}% · {inclusion(terms.commission_vat_inclusive)}</dd></div>
            <div><dt>بداية السريان بتوقيت الرياض</dt><dd>{riyadhDate(terms.effective_from)}</dd></div>
            <div><dt>نهاية السريان بتوقيت الرياض — غير مشمولة</dt><dd>{riyadhDate(terms.effective_to)}</dd></div>
            <div><dt>نوع المصدر</dt><dd>{sourceNames[terms.source_kind] || "غير محدد"}</dd></div>
            <div><dt>مرجع دليل العقد</dt><dd>{terms.evidence_ref}</dd></div>
        </dl>
        <p>شرائح عمولة الدفع عند الاستلام. النسبة المحفوظة ككسر: 0.01 = 1%.</p>
        <div className="overflow-x-auto"><table className="w-full text-right"><thead><tr><th>من مبلغ بالريال</th><th>إلى مبلغ بالريال</th><th>نسبة العمولة</th><th>العمولة الثابتة</th></tr></thead><tbody>{terms.cod_fee_tiers.map((tier, i) => <tr key={i} className="border-t"><td>{tier.min_amount} · {tier.min_inclusive ? "يشمل الحد" : "لا يشمل الحد"}</td><td>{tier.max_amount === null || tier.max_amount === "" ? "بلا حد أعلى" : `${tier.max_amount} · ${tier.max_inclusive ? "يشمل الحد" : "لا يشمل الحد"}`}</td><td>{percentOfFraction(tier.commission_percent)}% (الكسر المحفوظ: {tier.commission_percent})</td><td>{tier.fixed_fee} ريال سعودي</td></tr>)}</tbody></table></div>
    </section>;
}
function ContractAudit({ record, evidence = [] }) {
    return <details className="rounded-lg border p-3"><summary>تفاصيل التتبع والمراجعة</summary><dl>
        <dt>هوية شركة الشحن</dt><dd>{record.terms?.courier_id || record.party_id}</dd>
        <dt>معرف المسودة</dt><dd>{record.draft_id || record.id}</dd>
        <dt>بصمة المسودة</dt><dd className="break-all">{record.draft_hash || record.hash}</dd>
        {record.draft_id && <><dt>معرف العقد</dt><dd>{record.id}</dd></>}
        <dt>أنشأ المسودة / اعتمد العقد</dt><dd>{record.confirmed_by || record.created_by || "غير متاح"}</dd>
        <dt>وقت الإجراء بتوقيت الرياض</dt><dd>{record.confirmed_at || record.created_at ? riyadhDate(record.confirmed_at || record.created_at) : "غير متاح"}</dd>
    </dl>{evidence.map(row => <dl key={row.evidence_id} className="mt-2 border-t"><dt>دليل {purposes[row.purpose]}</dt><dd>{row.evidence_id}</dd><dt>معرف الأصل المحفوظ</dt><dd>{row.file_id}</dd><dt>راجع الدليل</dt><dd>{row.approved_by || "غير متاح"}</dd><dt>وقت مراجعة الدليل بتوقيت الرياض</dt><dd>{row.approved_at ? riyadhDate(row.approved_at) : "غير متاح"}</dd><dt>بصمة الأصل</dt><dd className="break-all">{row.source_sha256 || "غير متاحة"}</dd></dl>)}</details>;
}
export function shippingTerms(courierId, draft) {
    const date = value => {
        if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value || "")) throw new Error("shipping_contract_timezone_required");
        return `${value}:00+03:00`;
    };
    return {
        courier_id: courierId, payment_mode: draft.payment_mode,
        shipping_cost: draft.shipping_cost, shipping_cost_vat_inclusive: draft.shipping_cost_vat_inclusive,
        shipping_vat_percent: draft.shipping_vat_percent,
        commission_vat_inclusive: draft.commission_vat_inclusive, commission_vat_percent: draft.commission_vat_percent,
        effective_from: date(draft.effective_from), effective_to: draft.effective_to ? date(draft.effective_to) : null,
        evidence_ref: draft.evidence_ref, source_kind: draft.source_kind,
        cod_fee_tiers: draft.cod_fee_tiers.map(t => ({ min_amount: t.min_amount, max_amount: t.max_amount === "" ? null : t.max_amount,
            min_inclusive: t.min_inclusive, max_inclusive: t.max_inclusive, commission_percent: t.commission_percent,
            fixed_fee: t.fixed_fee, vat_percent: draft.commission_vat_percent, vat_included: draft.commission_vat_inclusive })),
    };
}

export default function RichShippingContracts({ transport, permissions = [], financialBase, value, onChange, banks = [], onBusyChange = () => {} }) {
    const [data, setData] = useState(null), [error, setError] = useState(""), [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false), [pending, setPending] = useState(null);
    const [reason, setReason] = useState(""), [confirmed, setConfirmed] = useState(false);
    const [courier, setCourier] = useState(""), [fileId, setFileId] = useState(""), [purpose, setPurpose] = useState("");
    const [selectedDraft, setSelectedDraft] = useState(""), [evidence, setEvidence] = useState({});
    const flight = useRef(false);
    const [viewedFile, setViewedFile] = useState("");
    const canView = permissions.includes("accounting.shipping.view");
    const canManage = permissions.includes("accounting.rules.manage");
    const canReview = permissions.includes("accounting.shipping.contracts.review");
    const ready = data && reason.trim().length >= 3 && confirmed && !busy && !pending;
    const couriers = (data?.couriers || []).filter(c => c.status === "active").map(c => ({ id: c.courier_key, name: c.name }));
    const draft = data?.drafts.find(d => d.id === selectedDraft && d.status === "draft");
    async function load() { const result = await transport.getRichShippingContracts(); setData(result); return result; }
    useEffect(() => { if (canView) load().catch(err => setError(errorText(err))); }, [transport, canView]);
    useEffect(() => {
        const warn = event => { if (pending) { event.preventDefault(); event.returnValue = ""; } };
        window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
    }, [pending]);
    async function send(request) {
        if (flight.current) return;
        flight.current = true; setBusy(true); onBusyChange(true); setError(""); setMessage("");
        let unresolved = false;
        try {
            await transport[request.method](copy(request.payload));
            setPending(null); setConfirmed(false); setMessage("حُفظت بيانات العقد على الخادم؛ لا قيد مالي أو تفعيل.");
            await load();
        } catch (err) {
            const code = errorText(err); setError(code);
            if (code === "shipping_setup_version_conflict") {
                setPending(null); setConfirmed(false);
                try { await load(); } catch (readError) { setError(`${code} · ${errorText(readError)}`); setData(null); }
                setMessage("تغيرت نسخة الإعداد. راجع البيانات المحدثة وأكد الطلب من جديد.");
            } else if (!err?.response || err.response.status >= 500) {
                unresolved = true; setPending(request);
            } else { setPending(null); }
        } finally { flight.current = false; setBusy(false); onBusyChange(unresolved); }
    }
    async function mutate(method, fields) {
        if (!ready) return;
        await send({ method, payload: copy({ request_id: requestId(), version: data.version, confirmed: true, reason: reason.trim(), ...fields }) });
    }
    async function viewOriginal(id) {
        setError("");
        try { await transport.downloadShippingEvidence(id); setViewedFile(id); }
        catch (err) { setViewedFile(""); setError(errorText(err)); }
    }
    async function upload(file) {
        if (!file || flight.current || pending) return;
        flight.current = true; setBusy(true); onBusyChange(true); setError("");
        try {
            const artifact = await transport.uploadOnboardingEvidence({ file, financialBase, purpose: "opening_balance", sectionId: "couriers_cod" });
            setFileId(artifact.source_file_id); setViewedFile(""); setMessage("حُفظ أصل الملف فقط؛ لم يُراجع ولم يُرحل رصيد افتتاحي.");
        } catch (err) { setError(errorText(err)); }
        finally { flight.current = false; setBusy(false); onBusyChange(false); }
    }
    if (!canView) return <p role="alert">لا تتوفر لك صلاحية عرض عقود الشحن.</p>;
    return <section aria-label="عقود الشحن الأصلية" className="min-w-0 max-w-full space-y-4 break-words [&_select]:max-w-full [&_input]:max-w-full [&_dd]:break-all [&_button]:max-w-full [&_button]:break-all" dir="rtl">
        <p>شروط توصيل الشحن بالريال السعودي. اعتماد الشروط لا يرحّل قيدًا ماليًا ولا يفعّل التشغيل. الأرصدة الافتتاحية وبنك التسوية في المرحلة 8 مستقلان.</p>
        {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
        {pending && <div role="alert">نتيجة الطلب غير مؤكدة. أعد نفس الطلب قبل المغادرة.<button type="button" disabled={busy} onClick={() => send(pending)}>إعادة الطلب نفسه</button></div>}
        <button type="button" disabled={busy || Boolean(pending)} onClick={() => load().catch(err => setError(errorText(err)))}>تحديث العقود</button>
        {data && <>
            <p>نسخة الإعداد: {data.version}</p>
            <fieldset disabled={busy || Boolean(pending)}><label>سبب إجراء العقد<input aria-label="سبب إجراء العقد" value={reason} onChange={e => setReason(e.target.value)} /></label><label><input type="checkbox" aria-label="تأكيد إجراء العقد" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> أؤكد البيانات وسبب هذا الإجراء</label></fieldset>
            <OpeningCourierEditor termsOnly value={value} onChange={onChange} couriers={couriers} banks={banks} busy={busy || Boolean(pending) || !canManage} onSave={ready && canManage ? (id, input) => mutate("saveRichShippingDraft", { context: "delivery", terms: shippingTerms(id, input) }) : undefined} />
            {!canManage && <p>لا تتوفر لك صلاحية إدارة شروط العقود وحفظ المسودة.</p>}
            <fieldset disabled={busy || Boolean(pending)} className="space-y-3"><legend>أصل الدليل ومراجعته المحاسبية</legend>
                <p>رفع الملف يحفظ أصله فقط. نزّل الأصل وراجعه ثم اختر الشركة وغرض الدليل؛ الرفع وحده لا يعتمد الدليل ولا يسجل رصيدًا.</p>
                <label>رفع أصل العقد<input aria-label="رفع أصل العقد" type="file" onChange={e => upload(e.target.files?.[0])} /></label>
                <label>معرف الملف المحفوظ<input aria-label="معرف الملف المحفوظ" value={fileId} onChange={e => { setFileId(e.target.value); setViewedFile(""); }} /></label>
                <button type="button" disabled={!canReview || !fileId} onClick={() => viewOriginal(fileId)}>تنزيل الأصل المحفوظ للمراجعة</button>
                <label>شركة دليل العقد<select aria-label="شركة دليل العقد" value={courier} onChange={e => setCourier(e.target.value)}><option value="">اختر الهوية الأصلية</option>{couriers.map(c => <option key={c.id} value={c.id}>{c.name} · {c.id}</option>)}</select></label>
                <label>غرض دليل العقد<select aria-label="غرض دليل العقد" value={purpose} onChange={e => setPurpose(e.target.value)}><option value="">حدد الغرض</option>{Object.entries(purposes).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
                <p>الملف: {fileId || "غير مختار"} · الغرض: {purposes[purpose] || "غير مختار"} · الشركة: {courier || "غير مختارة"}</p>
                <button type="button" disabled={!ready || !canReview || !courier || !fileId || !purpose || viewedFile !== fileId} onClick={() => mutate("reviewShippingEvidence", { courier_id: courier, file_id: fileId, purpose, confirmation: "APPROVE_MZ2_SHIPPING_EVIDENCE" })}>راجعت الأصل وأعتمد هذا الدليل</button>
                {!canReview && <p>اعتماد الدليل والعقد يتطلب صلاحية مراجعة محاسبية صريحة، حتى للمالك.</p>}
            </fieldset>
            <section aria-label="الأدلة المراجعة">{data.contract_evidence.map(row => <div key={row.evidence_id} className="min-w-0 max-w-full break-all border p-2"><p>{row.evidence_id} · {row.courier_id} · {purposes[row.purpose]} · {row.file_id} · {row.state} · مراجعة {row.revision}</p><button type="button" disabled={busy || !canReview} onClick={() => viewOriginal(row.file_id)}>تنزيل الأصل {row.file_id}</button><button type="button" disabled={!ready || !canReview || row.state !== "approved"} onClick={() => mutate("revokeShippingEvidence", { evidence_id: row.evidence_id, revision: row.revision, confirmation: "REVOKE_MZ2_SHIPPING_EVIDENCE" })}>سحب اعتماد الدليل {row.evidence_id}</button></div>)}</section>
            <fieldset disabled={busy || Boolean(pending)}><legend>مراجعة واعتماد شروط العقد</legend>
                <label>مسودة العقد<select aria-label="مسودة العقد" value={selectedDraft} onChange={e => { setSelectedDraft(e.target.value); setEvidence({}); setConfirmed(false); }}><option value="">اختر المسودة</option>{data.drafts.filter(d => d.status === "draft").map(d => <option key={d.id} value={d.id}>{d.terms.courier_id} · {d.id}</option>)}</select></label>
                {draft && <><ContractTerms terms={draft.terms} courierName={couriers.find(c => c.id === draft.terms.courier_id)?.name} label="شروط المسودة المحفوظة" /><ContractAudit record={draft} evidence={data.contract_evidence.filter(row => Object.values(evidence).includes(row.evidence_id))} />{Object.entries(purposes).map(([id, label]) => <label key={id}>دليل {label}<select aria-label={`دليل ${label}`} value={evidence[id] || ""} onChange={e => setEvidence({ ...evidence, [id]: e.target.value })}><option value="">اختر الدليل {id === "contract" ? "المطلوب" : "عند وجود ضريبة"}</option>{data.contract_evidence.filter(r => r.courier_id === draft.terms.courier_id && r.purpose === id && r.state === "approved").map(r => <option key={r.evidence_id} value={r.evidence_id}>{r.file_id} · {r.evidence_id}</option>)}</select></label>)}<button type="button" disabled={!ready || !canReview || !evidence.contract} onClick={() => mutate("approveRichShippingContract", { draft_id: draft.id, draft_hash: draft.hash, contract_evidence_id: evidence.contract, shipping_tax_evidence_id: evidence.shipping_tax || null, commission_tax_evidence_id: evidence.commission_tax || null, confirmation: "APPROVE_MZ2_SHIPPING_CONTRACT" })}>اعتماد شروط العقد المختار</button></>}
            </fieldset>
            <section aria-label="العقود المعتمدة">{data.contracts.map(c => <details key={c.id}><summary>عقد {couriers.find(row => row.id === c.party_id)?.name || c.party_id} · معتمد</summary><ContractTerms terms={c.contract_version} courierName={couriers.find(row => row.id === c.party_id)?.name} label="شروط العقد المعتمد" /><ContractAudit record={c} evidence={c.evidence_snapshot?.items || []} /></details>)}</section>
        </>}
    </section>;
}
