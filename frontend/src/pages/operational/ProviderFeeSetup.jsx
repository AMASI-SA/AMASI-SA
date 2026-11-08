import React, { useEffect, useRef, useState } from "react";
import { useOptionalAuth } from "../../context/AuthContext";
import {
    createOnboardingFeePolicy, listOnboardingFeeCandidates, listOnboardingFeePolicies,
    listOnboardingProviderPlanScopes, createOnboardingProviderPlanScope,
    previewOnboardingFeePolicy, saveOnboardingFeeSourceMode, updateOnboardingFeePolicy,
} from "../../services/accountingOnboarding";
import { formatRiyadhDateTime } from "../../lib/dates";
import PaginatedReportTable from "./PaginatedReportTable";
import { requestId } from "./api";
import "./financialPosition.css";

const providers = { salla: "سلة", tamara: "تمارا", tabby: "تابي", emkan: "إمكان" };
const rails = { mada: "مدى", visa: "فيزا", mastercard: "ماستركارد", apple_pay: "Apple Pay", google_pay: "Google Pay", stc_pay: "STC Pay", credit_card: "بطاقة ائتمانية", debit_card: "بطاقة بنكية", salla_wallet: "محفظة سلة" };
const taxes = { exclusive: "تضاف الضريبة إلى العمولة", inclusive: "العمولة تشمل الضريبة", exempt: "معفاة", not_applicable: "لا تنطبق" };
const treatments = { retain: "تحتفظ المنصة بالرسم", recalculate: "يعاد احتساب الرسم" };
const taxRounding = { half_up: "أقرب هللة، وعند التعادل للأعلى", half_even: "أقرب هللة، وعند التعادل إلى الرقم الزوجي" };
const taxBases = { rounded_fee: "الرسم بعد التقريب", unrounded_fee: "الرسم قبل التقريب" };
const events = { capture: "عمولة الدفع", payout: "رسوم التحويل", refund: "رسوم الاسترجاع", cancellation: "رسوم الإلغاء" };
const eventGuidance = {
    payout: "رسوم التحويل الفعلية تؤخذ من كشف التسوية. تستخدم التعرفة المحفوظة هنا للمعاينة والمطابقة.",
    refund: "تُحتسب رسوم معالجة الاسترجاع عند ثبوت الاسترجاع وفق التعرفة السارية، وتضاف إلى معالجة عمولة الدفع الأصلية.",
    cancellation: "تستخدم تعرفة الإلغاء للمعاينة والمطابقة مع شروط المنصة وبيانات الإلغاء الموثقة.",
};
const initial = { provider: "", event_type: "capture", payment_method: "", plan_id: "", currency: "SAR", percentage: "", fixed_amount: "", vat_treatment: "", vat_rate: "", effective_from: "", effective_to: "", evidence: "", refund_fee_treatment: "", cancellation_fee_treatment: "" };

function Input({ label, ...props }) {
    return <label className="block">{label}<input aria-label={label} className="block w-full border rounded p-2" {...props} /></label>;
}
function Select({ label, options, optional = false, ...props }) {
    return <label className="block">{label}<select aria-label={label} className="block w-full border rounded p-2" required={!optional} {...props}><option value="">{optional ? "غير موثق" : "اختر"}</option>{Object.entries(options).map(([k, name]) => <option key={k} value={k}>{name}</option>)}</select></label>;
}

function FeeTable({ policies, onEdit, onPreview, busy }) {
    const rows = policies.filter(policy => policy.status !== "inactive");
    return <article className="ofp-table-card">
        <div className="ofp-table-title"><h2>جدول رسوم طرق الدفع</h2></div>
        <PaginatedReportTable rows={rows} label="رسوم طرق الدفع" pageSize={13}>{pageRows => <div className="ofp-table-scroll">
            <table>
                <thead><tr><th>المنصة</th><th>طريقة الدفع / الخطة</th><th>نوع الرسم</th><th>العمولة %</th><th>الرسم الثابت</th><th>الضريبة %</th><th>العملة</th><th>فترة السريان</th><th>الإجراءات</th></tr></thead>
                <tbody>{pageRows.map(policy => <tr key={policy.id}>
                    <td>{providers[policy.provider]}</td>
                    <td>{rails[policy.payment_method] || "كل الطرق"}<br />{policy.plan_id || "كل الخطط"}</td>
                    <td>{events[policy.event_type || "capture"]}</td>
                    <td><bdi>{policy.percentage}</bdi></td>
                    <td><bdi>{policy.fixed_amount}</bdi></td>
                    <td>{policy.components?.length ? "حسب الأجزاء" : policy.vat_rate ?? "لا تنطبق"}</td>
                    <td>{policy.currency}</td>
                    <td><bdi>{policy.effective_from}</bdi><br />{policy.effective_to ? <bdi>{policy.effective_to}</bdi> : "مستمرة"}</td>
                    <td>
                        <button type="button" className="ofp-button" disabled={busy} onClick={() => onEdit(policy)}>تعديل</button>
                        <button type="button" className="ofp-button" disabled={busy} onClick={() => onPreview(policy)}>معاينة الرسم</button>
                        <details><summary>التفاصيل وسجل التعديلات</summary>
                            <p>{policy.evidence}</p>
                            <p>الإصدار {policy.version || 1} · {formatRiyadhDateTime(policy.confirmed_at)}</p>
                            {policy.minimum != null && <p>الحد الأدنى للرسم {policy.minimum} {policy.currency}</p>}
                            {policy.maximum != null && <p>الحد الأعلى للرسم {policy.maximum} {policy.currency}</p>}
                            {(policy.components || []).map(part => <p key={part.id}>
                                {part.percentage}% + {part.fixed_amount} {policy.currency} · {taxes[part.vat_treatment]}
                                {["inclusive", "exclusive"].includes(part.vat_treatment) && <> {part.vat_rate}%</>}
                                {" · "}{taxBases[part.vat_basis || "rounded_fee"]} · {taxRounding[part.vat_rounding || "half_up"]}
                            </p>)}
                            {(policy.revisions || []).map(previous => <p key={previous.version}>
                                الإصدار {previous.version} · {previous.percentage}% + {previous.fixed_amount} {previous.currency} · {formatRiyadhDateTime(previous.confirmed_at)}
                            </p>)}
                        </details>
                    </td>
                </tr>)}</tbody>
            </table>
        </div>}</PaginatedReportTable>
        {!rows.length && <p className="p-3">لا توجد تعرفة معتمدة.</p>}
    </article>;
}

function FeePreview({ policy, onClose }) {
    const [amount, setAmount] = useState("");
    const [date, setDate] = useState(policy.effective_from);
    const [result, setResult] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    async function preview(event) {
        event.preventDefault();
        setBusy(true); setError(""); setResult(null);
        try {
            setResult(await previewOnboardingFeePolicy(policy.id, { amount, transaction_date: date }));
        } catch {
            setError("تعذرت المعاينة. راجع المبلغ وتاريخ سريان التعرفة.");
        } finally { setBusy(false); }
    }
    return <section className="border rounded p-4 space-y-3">
        <h2 className="font-bold">معاينة {events[policy.event_type || "capture"]} · {providers[policy.provider]}</h2>
        <form onSubmit={preview} className="grid gap-3 sm:grid-cols-3">
            <Input label="المبلغ لمعاينة الرسم" type="number" required disabled={busy} min="0.01" step="0.01" value={amount} onChange={event => { setAmount(event.target.value); setResult(null); }} />
            <Input label="تاريخ العملية للمعاينة" type="date" required disabled={busy} value={date} onChange={event => { setDate(event.target.value); setResult(null); }} />
            <button type="submit" disabled={busy} className="ofp-button">احسب المعاينة</button>
        </form>
        {result && <p role="status">الرسم قبل الضريبة {result.fee_net ?? "غير متاح"}، الضريبة {result.fee_vat ?? "غير متاح"}، إجمالي الرسم {result.fee_total} {result.currency}.
            {["capture", "payout"].includes(policy.event_type || "capture") && <> صافي المبلغ {result.net_amount} {result.currency}.</>}
        </p>}
        {error && <p role="alert">{error}</p>}
        <button type="button" onClick={onClose}>إغلاق المعاينة</button>
    </section>;
}

function TabbyPlanScopes({ policies, disabled }) {
    const [currency, setCurrency] = useState("SAR");
    const [saved, setSaved] = useState(null), [selected, setSelected] = useState([]);
    const [from, setFrom] = useState(""), [to, setTo] = useState(""), [evidence, setEvidence] = useState("");
    const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
    const [reload, setReload] = useState(0);
    const generation = useRef(0), inFlight = useRef(false);
    const eligible = policies.filter(p => p.provider === "tabby" && p.currency === currency && p.status === "active" &&
        p.fee_model_version === 2 && p.plan_id && (p.event_type || "capture") === "capture" && p.confirmed_by && p.confirmed_at && p.evidence);
    useEffect(() => {
        const token = ++generation.current;
        setSaved(null); setSelected([]); setMessage(""); setBusy(true);
        listOnboardingProviderPlanScopes(currency).then(value => {
            if (token === generation.current) setSaved(value);
        }).catch(() => { if (token === generation.current) setMessage("تعذرت قراءة نطاقات تابي أو لا توجد صلاحية. أعد التحميل للتحقق."); })
            .finally(() => { if (token === generation.current) setBusy(false); });
        return () => { generation.current += 1; };
    }, [currency, policies, reload]);
    async function saveScope(event) {
        event.preventDefault();
        if (inFlight.current || disabled || busy || !saved?.version || !selected.length || !from || (to && to < from) || evidence.trim().length < 3) return;
        if (selected.some(id => !eligible.some(p => p.id === id))) return;
        inFlight.current = true; setBusy(true); setMessage("");
        const token = generation.current;
        try {
            await createOnboardingProviderPlanScope({ provider: "tabby", currency, policy_ids: selected,
                expected_version: saved.version, effective_from: from, effective_to: to || null, evidence: evidence.trim() });
            if (token !== generation.current) return;
            setSelected([]); setFrom(""); setTo(""); setEvidence("");
            setSaved(null);
            const latest = await listOnboardingProviderPlanScopes(currency);
            if (token === generation.current) { setSaved(latest); setMessage("حُفظ نطاق التعرفات المفعلة لتابي. لم تتغير التعرفات أو خطط الطلبات."); }
        } catch (error) {
            if (token !== generation.current) return;
            setSaved(null);
            const code = error?.response?.data?.detail?.code;
            setMessage(code === "provider_plan_scope_terms_differ" ? "شروط التعرفات المحددة غير متطابقة؛ راجع الرسم الثابت والضريبة والتقريب وفترة السريان." :
                code === "provider_plan_scope_overlap" ? "يوجد نطاق معتمد متداخل مع هذه الفترة. أعد التحميل لمراجعته." :
                "تعذر تأكيد حفظ النطاق أو تغير إصدار الإعداد. أعد تحميل النطاقات قبل أي محاولة أخرى.");
        } finally { inFlight.current = false; if (token === generation.current) setBusy(false); }
    }
    return <section className="ofp-table-card p-4 space-y-3" aria-label="نطاق تعرفات تابي المفعلة">
        <h2>نطاق تعرفات تابي المفعلة</h2>
        <p>حدد عقود الخطط المفعلة للمتجر وفترة سريانها. يسمح النطاق باحتساب التعرفة المتطابقة عند غياب خطة الطلب، دون تعيين خطة للطلب أو تعديل أي عمولة.</p>
        <Input label="عملة نطاق تابي" value={currency} maxLength={3} pattern="[A-Z]{3}" disabled={busy || disabled} onChange={e => setCurrency(e.target.value.toUpperCase())} />
        <button type="button" className="ofp-button" disabled={busy || disabled} onClick={() => setReload(n => n + 1)}>إعادة تحميل نطاقات تابي</button>
        {saved && <div><p>إصدار إعداد تابي: {saved.version ?? "غير متاح"}</p>
            {(saved.items || []).map(scope => <article key={scope.id} className="border rounded p-2">
                <p>النطاق {scope.id} · {scope.status === "active" ? "معتمد" : "غير فعال"} · {scope.currency}</p>
                <p>من {scope.effective_from} إلى {scope.effective_to || "مستمر"}</p><p>{scope.evidence}</p>
                <p>التعرفات المرتبطة: {(scope.contracts || []).map(ref => ref.policy_id).join("، ")}</p>
            </article>)}
            {!saved.items?.length && <p>لا يوجد نطاق محفوظ لهذه العملة.</p>}
        </div>}
        <form onSubmit={saveScope} aria-label="اعتماد نطاق تابي"><fieldset disabled={busy || disabled || !Number.isInteger(saved?.version)} className="space-y-3">
            <legend>اختيار التعرفات المعتمدة المفعلة للمتجر</legend>
            {eligible.map(p => <label key={p.id} className="block"><input type="checkbox" aria-label={`تعرفة تابي ${p.id}`} checked={selected.includes(p.id)} onChange={e => setSelected(ids => e.target.checked ? [...ids, p.id] : ids.filter(id => id !== p.id))} />
                {p.plan_id} · {p.id} · {p.effective_from} إلى {p.effective_to || "مستمر"} · الإصدار {p.version || 1}
            </label>)}
            {!eligible.length && <p>لا توجد تعرفات تابي تفصيلية معتمدة مؤهلة بهذه العملة.</p>}
            <Input label="بداية نطاق تابي" type="date" required value={from} onChange={e => setFrom(e.target.value)} />
            <Input label="نهاية نطاق تابي الاختيارية" type="date" min={from || undefined} value={to} onChange={e => setTo(e.target.value)} />
            <Input label="دليل تفعيل خطط تابي وفترتها" required minLength={3} maxLength={1000} value={evidence} onChange={e => setEvidence(e.target.value)} />
            <button type="submit" className="ofp-button" disabled={!selected.length}>اعتماد نطاق تعرفات تابي</button>
        </fieldset></form>
        {message && <p role="status">{message}</p>}
    </section>;
}

function FeeForm() {
    const [policies, setPolicies] = useState([]), [suggestions, setSuggestions] = useState([]);
    const [conflicts, setConflicts] = useState([]);
    const [form, setForm] = useState(initial), [parts, setParts] = useState([]), [busy, setBusy] = useState(true), [message, setMessage] = useState("");
    const [editing, setEditing] = useState(null);
    const [sourceMode, setSourceMode] = useState(null);
    const [previewPolicy, setPreviewPolicy] = useState(null);
    const formRef = useRef(null);
    useEffect(() => {
        let current = true;
        Promise.all([
            listOnboardingFeePolicies(),
            listOnboardingFeeCandidates().catch(() => ({ items: [], conflicts: [] })),
        ]).then(([saved, stored]) => {
            if (current) { setPolicies(saved.items); setSourceMode(saved.source_mode || "legacy_settings"); setSuggestions(stored.items); setConflicts(stored.conflicts || []); }
        }).catch(() => { if (current) setMessage("تعذرت قراءة التعرفات. أعد تحميل الصفحة."); })
            .finally(() => { if (current) setBusy(false); });
        return () => { current = false; };
    }, []);
    const set = key => ({ value: form[key], onChange: e => setForm({ ...form, [key]: e.target.value }) });
    const number = { type: "number", min: 0, step: "any", required: true };
    const changePart = (i, key) => ({ value: parts[i][key], onChange: e => setParts(parts.map((part, n) => n === i ? { ...part, [key]: e.target.value } : part)) });
    async function activate() {
        setBusy(true); setMessage("");
        try {
            const result = await saveOnboardingFeeSourceMode("mz2_policies");
            setSourceMode(result.source_mode);
            setMessage("تم ربط الأرصدة التشغيلية بجدول رسوم ميزان 2.");
        } catch { setMessage("تعذر ربط الجدول. تحقق من حفظ التعرفات ثم أعد المحاولة."); }
        finally { setBusy(false); }
    }
    function edit(policy) {
        setForm(Object.fromEntries(Object.keys(initial).map(key => [key, policy[key] ?? initial[key]])));
        if (policy.provider === "salla" && !policy.payment_method) setForm(current => ({ ...current, payment_method: "all" }));
        setParts((policy.components || []).map(part => ({
            id: part.id, percentage: part.percentage, fixed_amount: part.fixed_amount,
            vat_treatment: part.vat_treatment, vat_rate: part.vat_rate ?? "",
            vat_rounding: part.vat_rounding || "half_up", vat_basis: part.vat_basis || "rounded_fee",
            refund_fee_treatment: part.refund_fee_treatment || "", cancellation_fee_treatment: part.cancellation_fee_treatment || "",
        })));
        setEditing({ id: policy.id, version: policy.version || 1, minimum: policy.minimum ?? null, maximum: policy.maximum ?? null });
        setPreviewPolicy(null);
        setMessage("");
        formRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    }
    async function save(e) {
        e.preventDefault();
        if (busy) return;
        setBusy(true); setMessage("");
        try {
            const vatRate = ["exclusive", "inclusive"].includes(form.vat_treatment) ? form.vat_rate : null;
            const payload = { ...form, payment_method: form.provider === "salla" && form.payment_method !== "all" ? form.payment_method : null,
                plan_id: form.plan_id || null, effective_to: form.effective_to || null,
                vat_rate: vatRate, cancellation_fee_treatment: form.cancellation_fee_treatment || null, refund_fee_treatment: form.refund_fee_treatment || null };
            if (parts.length) payload.components = parts.map(p => ({ ...p,
                vat_rate: p.vat_rate === "" ? null : p.vat_rate,
                refund_fee_treatment: p.refund_fee_treatment || null,
                cancellation_fee_treatment: p.cancellation_fee_treatment || null }));
            const row = editing
                ? await updateOnboardingFeePolicy(editing.id, { ...payload, minimum: editing.minimum, maximum: editing.maximum,
                    expected_version: editing.version })
                : await createOnboardingFeePolicy(payload);
            setPolicies(prev => editing ? prev.map(policy => policy.id === row.id ? row : policy) : [...prev, row]);
            setForm(initial); setParts([]); setEditing(null);
            setMessage("حُفظت التعرفة. تُطبّق عند إعادة احتساب الأرصدة على العمليات المشمولة بفترة السريان.");
        } catch (err) {
            const detail = err.response?.data?.detail;
            const errors = {
                provider_fee_policy_overlap: "توجد تعرفة لهذه الطريقة والخطة خلال الفترة المختارة. راجع التواريخ.",
                provider_fee_policy_version_conflict: "تغيّرت التعرفة أثناء التعديل. أعد تحميلها قبل الحفظ.",
            };
            setMessage(errors[detail?.code] || "تعذر حفظ التعرفة. تحقق من المستند والتواريخ ومجموع الأجزاء والنسب.");
        } finally { setBusy(false); }
    }
    return <main dir="rtl" className="p-5 space-y-4 max-w-7xl mx-auto">
        <a href="/operational-balances/financial-position">العودة إلى المركز المالي التشغيلي</a>
        <h1 className="text-2xl font-bold">رسوم طرق الدفع في ميزان 2</h1>
        <p>يعرض الجدول تعرفات الرسوم وفترات سريانها، ويُحفظ كل تعديل مع إصدار التعرفة السابق. تعتمد الرسوم الفعلية عند توثيق كشف التسوية.</p>
        {sourceMode === "mz2_policies" ? <p role="status">مصدر احتساب الرسوم المعتمد هو جدول ميزان 2.</p> : sourceMode === "legacy_settings" && <div className="space-y-2">
            <p>المصدر الحالي هو إعدادات طرق الدفع القديمة. احفظ التعرفات ثم اربط الأرصدة بجدول ميزان 2.</p>
            <button type="button" className="ofp-button" disabled={busy || !policies.length} onClick={activate}>ربط الأرصدة التشغيلية بهذا الجدول</button>
        </div>}
        <FeeTable policies={policies} onEdit={edit} onPreview={setPreviewPolicy} busy={busy} />
        {previewPolicy && <FeePreview key={previewPolicy.id + ":" + previewPolicy.version} policy={previewPolicy} onClose={() => setPreviewPolicy(null)} />}
        <form ref={formRef} onSubmit={save}><fieldset disabled={busy} className="space-y-3">
            <legend className="font-bold">{editing ? "تعديل التعرفة المحفوظة" : "إضافة تعرفة إلى ميزان 2"}</legend>
            <p>يمكن تعبئة النسبة والرسم من إعدادات طرق الدفع المحفوظة. طابقها مع المستند وحدد فترة سريانها قبل الاعتماد.</p>
            {conflicts.length > 0 && <div className="border rounded p-3"><strong>طرق دفع تحتاج مراجعة عقد المنصة</strong>
                <ul>{conflicts.map(r => <li key={r.provider + (r.payment_method || "")}>
                    {r.labels.join("، ")} — {r.reason === "duplicate_settings" ? "توجد عدة تعريفات محفوظة لطريقة الدفع نفسها." : "بيانات الرسم أو الضريبة غير مكتملة."}
                </li>)}</ul>
                <p>أدخل التعرفة المثبتة في العقد ومرجعه وتاريخ سريانها.</p>
            </div>}
            {!editing && <div className="flex flex-wrap gap-2">{suggestions.map(r => <button className="border rounded px-3" type="button" key={r.provider + (r.payment_method || "")} onClick={() => { setForm({ ...initial, provider: r.provider, payment_method: r.payment_method || "", percentage: r.percentage, fixed_amount: r.fixed_amount, vat_rate: r.vat_rate }); setParts([]); }}>{r.label} · {r.percentage}% + {r.fixed_amount}</button>)}</div>}
            <Select label="المنصة" options={providers} disabled={Boolean(editing)} value={form.provider} onChange={e => { setForm({ ...initial, provider: e.target.value }); setParts([]); }} />
            <Select label="نوع الرسم" options={events} disabled={Boolean(editing)} {...set("event_type")} />
            {form.provider === "salla" && <Select label="طريقة الدفع لدى سلة" options={{ ...rails, all: "تعرفة عامة لكل الطرق بحسب المستند" }} disabled={Boolean(editing)} {...set("payment_method")} />}
            <Input label="معرف خطة التقسيط إن خصّ العقد خطة بعينها" maxLength={256} disabled={Boolean(editing)} {...set("plan_id")} />
            {eventGuidance[form.event_type] && <p>{eventGuidance[form.event_type]}</p>}
            <div className="grid gap-3 sm:grid-cols-3">
                <Input label="نسبة العمولة الإجمالية %" {...number} max={100} {...set("percentage")} />
                <Input label="الرسم الثابت الإجمالي" {...number} step=".01" {...set("fixed_amount")} />
                <Input label="عملة التعرفة" required pattern="[A-Z]{3}" disabled={Boolean(editing)} {...set("currency")} />
                <Input label="تسري من" required type="date" {...set("effective_from")} />
                <Input label="تسري حتى" type="date" min={form.effective_from} {...set("effective_to")} />
                <Select label="معالجة ضريبة العمولة" options={taxes} {...set("vat_treatment")} />
                {["inclusive", "exclusive"].includes(form.vat_treatment) && <Input label="نسبة ضريبة العمولة %" {...number} max={100} {...set("vat_rate")} />}
            </div>
            {!parts.length && <><Select label="معالجة رسوم الاسترداد" options={treatments} optional {...set("refund_fee_treatment")} /><Select label="معالجة رسوم الإلغاء" options={treatments} optional {...set("cancellation_fee_treatment")} /></>}
            <p>إذا فصّل المستند عمولة قابلة للرد وأخرى محتفظًا بها أو رسومًا ثابتة بمعالجة مستقلة، أدخل كل جزء على حدة. تُحسب ضريبة كل جزء بحسب معالجته ونسبته، وتُقرّب قيمته وضريبته إلى هللة وفق أسلوب التقريب المحدد.</p>
            {parts.map((part, i) => <fieldset key={part.id} className="border p-2 grid gap-2 sm:grid-cols-2"><legend>جزء العمولة {i + 1}</legend>
                <Input label={`النسبة للجزء ${i + 1}`} {...number} max={100} {...changePart(i, "percentage")} />
                <Input label={`المبلغ الثابت للجزء ${i + 1}`} {...number} step=".01" {...changePart(i, "fixed_amount")} />
                <Select label={`معالجة ضريبة الجزء ${i + 1}`} options={taxes} {...changePart(i, "vat_treatment")} />
                {["inclusive", "exclusive"].includes(part.vat_treatment) && <Input label={`نسبة ضريبة الجزء ${i + 1} %`} {...number} max={100} {...changePart(i, "vat_rate")} />}
                <Select label={`تقريب ضريبة الجزء ${i + 1}`} options={taxRounding} {...changePart(i, "vat_rounding")} />
                <Select label={`أساس ضريبة الجزء ${i + 1}`} options={taxBases} {...changePart(i, "vat_basis")} />
                <Select label={`معالجة استرداد الجزء ${i + 1}`} options={{ ...treatments, full_only: "يرد عند الاسترداد الكامل فقط" }} optional {...changePart(i, "refund_fee_treatment")} />
                <Select label={`معالجة إلغاء الجزء ${i + 1}`} options={treatments} optional {...changePart(i, "cancellation_fee_treatment")} />
                <button type="button" onClick={() => setParts(parts.filter((_, n) => n !== i))}>حذف الجزء</button>
            </fieldset>)}
            <button type="button" disabled={parts.length >= 20} onClick={() => setParts([...parts, {
                id: requestId(), percentage: "", fixed_amount: "", vat_treatment: form.vat_treatment,
                vat_rate: ["exclusive", "inclusive"].includes(form.vat_treatment) ? form.vat_rate : "",
                vat_rounding: "half_up", vat_basis: "rounded_fee", refund_fee_treatment: "", cancellation_fee_treatment: "",
            }])}>إضافة جزء مفصل للعمولة</button>
            <Input label="مرجع العقد أو كشف المنصة المثبت للتعرفة وفترتها" required minLength={3} maxLength={1000} {...set("evidence")} />
            <button type="submit" className="ofp-button">{editing ? "حفظ تعديل التعرفة" : "اعتماد التعرفة لفترة السريان"}</button>
            {editing && <button type="button" onClick={() => { setEditing(null); setForm(initial); setParts([]); }}>إلغاء التعديل</button>}
        </fieldset></form>
        {message && <p role="status">{message}</p>}
        <TabbyPlanScopes policies={policies} disabled={busy} />
    </main>;
}
export default function ProviderFeeSetup() {
    const { user } = useOptionalAuth() || {};
    return user?.role === "owner" ? <FeeForm key={user.id} /> : <p dir="rtl">إدارة تعرفات العمولات متاحة لمالك المتجر.</p>;
}
