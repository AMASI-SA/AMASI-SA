import React, { useEffect, useRef, useState } from "react";
import { useOptionalAuth } from "../../context/AuthContext";
import { createOnboardingFeePolicy, listOnboardingFeeCandidates, listOnboardingFeePolicies, updateOnboardingFeePolicy } from "../../services/accountingOnboarding";
import { formatRiyadhDateTime } from "../../lib/dates";
import PaginatedReportTable from "./PaginatedReportTable";
import "./financialPosition.css";

const providers = { salla: "سلة", tamara: "تمارا", tabby: "تابي", emkan: "إمكان" };
const rails = { mada: "مدى", visa: "فيزا", mastercard: "ماستركارد", apple_pay: "Apple Pay", google_pay: "Google Pay", stc_pay: "STC Pay", credit_card: "بطاقة ائتمانية", debit_card: "بطاقة بنكية", salla_wallet: "محفظة سلة" };
const taxes = { exclusive: "تضاف الضريبة إلى العمولة", inclusive: "العمولة تشمل الضريبة", exempt: "معفاة", not_applicable: "لا تنطبق" };
const treatments = { retain: "تحتفظ المنصة بالرسم", recalculate: "يعاد احتساب الرسم" };
const taxRounding = { half_up: "أقرب هللة، وعند التعادل للأعلى", half_even: "أقرب هللة، وعند التعادل إلى الرقم الزوجي" };
const taxBases = { rounded_fee: "الرسم بعد التقريب", unrounded_fee: "الرسم قبل التقريب" };
const events = { capture: "عمولة الدفع", payout: "رسوم التحويل", refund: "رسوم الاسترجاع", cancellation: "رسوم الإلغاء" };
const initial = { provider: "", event_type: "capture", payment_method: "", plan_id: "", currency: "SAR", percentage: "", fixed_amount: "", vat_treatment: "", vat_rate: "", effective_from: "", effective_to: "", evidence: "", refund_fee_treatment: "", cancellation_fee_treatment: "" };

function Input({ label, ...props }) {
    return <label className="block">{label}<input aria-label={label} className="block w-full border rounded p-2" {...props} /></label>;
}
function Select({ label, options, optional = false, ...props }) {
    return <label className="block">{label}<select aria-label={label} className="block w-full border rounded p-2" required={!optional} {...props}><option value="">{optional ? "غير موثق" : "اختر"}</option>{Object.entries(options).map(([k, name]) => <option key={k} value={k}>{name}</option>)}</select></label>;
}

function FeeTable({ policies, onEdit, busy }) {
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
                    <td>{policy.vat_rate ?? "لا تنطبق"}</td>
                    <td>{policy.currency}</td>
                    <td><bdi>{policy.effective_from}</bdi><br />{policy.effective_to ? <bdi>{policy.effective_to}</bdi> : "مستمرة"}</td>
                    <td>
                        <button type="button" className="ofp-button" disabled={busy} onClick={() => onEdit(policy)}>تعديل</button>
                        <details><summary>التفاصيل وسجل التعديلات</summary>
                            <p>{policy.evidence}</p>
                            <p>الإصدار {policy.version || 1} · {formatRiyadhDateTime(policy.confirmed_at)}</p>
                            {(policy.components || []).map(part => <p key={part.id}>
                                {part.percentage}% + {part.fixed_amount} {policy.currency} · {taxBases[part.vat_basis || "rounded_fee"]} · {taxRounding[part.vat_rounding || "half_up"]}
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

function FeeForm() {
    const [policies, setPolicies] = useState([]), [suggestions, setSuggestions] = useState([]);
    const [conflicts, setConflicts] = useState([]);
    const [form, setForm] = useState(initial), [parts, setParts] = useState([]), [busy, setBusy] = useState(true), [message, setMessage] = useState("");
    const [editing, setEditing] = useState(null);
    const formRef = useRef(null);
    useEffect(() => {
        let current = true;
        Promise.all([listOnboardingFeePolicies(), listOnboardingFeeCandidates()]).then(([saved, stored]) => {
            if (current) { setPolicies(saved.items); setSuggestions(stored.items); setConflicts(stored.conflicts || []); }
        }).catch(() => { if (current) setMessage("تعذرت قراءة التعرفات. أعد تحميل الصفحة."); })
            .finally(() => { if (current) setBusy(false); });
        return () => { current = false; };
    }, []);
    const set = key => ({ value: form[key], onChange: e => setForm({ ...form, [key]: e.target.value }) });
    const number = { type: "number", min: 0, step: "any", required: true };
    const changePart = (i, key) => ({ value: parts[i][key], onChange: e => setParts(parts.map((part, n) => n === i ? { ...part, [key]: e.target.value } : part)) });
    function edit(policy) {
        setForm(Object.fromEntries(Object.keys(initial).map(key => [key, policy[key] ?? initial[key]])));
        if (policy.provider === "salla" && !policy.payment_method) setForm(current => ({ ...current, payment_method: "all" }));
        setParts((policy.components || []).map(part => ({
            percentage: part.percentage, fixed_amount: part.fixed_amount,
            vat_rounding: part.vat_rounding || "half_up", vat_basis: part.vat_basis || "rounded_fee",
            refund_fee_treatment: part.refund_fee_treatment || "", cancellation_fee_treatment: part.cancellation_fee_treatment || "",
        })));
        setEditing({ id: policy.id, version: policy.version || 1 });
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
            if (parts.length) payload.components = parts.map((p, i) => ({ ...p, id: String(i + 1),
                vat_treatment: form.vat_treatment, vat_rate: vatRate,
                refund_fee_treatment: p.refund_fee_treatment || null,
                cancellation_fee_treatment: p.cancellation_fee_treatment || null }));
            const row = editing
                ? await updateOnboardingFeePolicy(editing.id, { ...payload, expected_version: editing.version })
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
        <p>تُحسب رسوم الأرصدة التشغيلية من التعرفات السارية هنا. تعتمد الرسوم الفعلية عند توثيق كشف التسوية، ويُحفظ كل تعديل مع إصدار التعرفة السابق.</p>
        <FeeTable policies={policies} onEdit={edit} busy={busy} />
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
            {form.event_type !== "capture" && <p>يُحتسب هذا الرسم مرة لكل عملية موثقة تنطبق عليها شروط المنصة. رسوم معالجة الاسترجاع تضاف إلى معالجة عمولة الدفع الأصلية.</p>}
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
            <p>إذا فصّل المستند عمولة قابلة للرد وأخرى محتفظًا بها أو رسومًا ثابتة بمعالجة مستقلة، أدخل كل جزء على حدة. تُقرّب قيمة كل جزء وضريبته إلى هللة وفق أسلوب التقريب المحدد.</p>
            {parts.map((part, i) => <fieldset key={i} className="border p-2 grid gap-2 sm:grid-cols-2"><legend>جزء العمولة {i + 1}</legend>
                <Input label={`النسبة للجزء ${i + 1}`} {...number} max={100} {...changePart(i, "percentage")} />
                <Input label={`المبلغ الثابت للجزء ${i + 1}`} {...number} step=".01" {...changePart(i, "fixed_amount")} />
                <Select label={`تقريب ضريبة الجزء ${i + 1}`} options={taxRounding} {...changePart(i, "vat_rounding")} />
                <Select label={`أساس ضريبة الجزء ${i + 1}`} options={taxBases} {...changePart(i, "vat_basis")} />
                <Select label={`معالجة استرداد الجزء ${i + 1}`} options={{ ...treatments, full_only: "يرد عند الاسترداد الكامل فقط" }} optional {...changePart(i, "refund_fee_treatment")} />
                <Select label={`معالجة إلغاء الجزء ${i + 1}`} options={treatments} optional {...changePart(i, "cancellation_fee_treatment")} />
                <button type="button" onClick={() => setParts(parts.filter((_, n) => n !== i))}>حذف الجزء</button>
            </fieldset>)}
            <button type="button" disabled={parts.length >= 20} onClick={() => setParts([...parts, { percentage: "", fixed_amount: "", vat_rounding: "half_up", vat_basis: "rounded_fee", refund_fee_treatment: "", cancellation_fee_treatment: "" }])}>إضافة جزء مفصل للعمولة</button>
            <Input label="مرجع العقد أو كشف المنصة المثبت للتعرفة وفترتها" required minLength={3} maxLength={1000} {...set("evidence")} />
            <button type="submit" className="ofp-button">{editing ? "حفظ تعديل التعرفة" : "اعتماد التعرفة لفترة السريان"}</button>
            {editing && <button type="button" onClick={() => { setEditing(null); setForm(initial); setParts([]); }}>إلغاء التعديل</button>}
        </fieldset></form>
        {message && <p role="status">{message}</p>}
    </main>;
}
export default function ProviderFeeSetup() {
    const { user } = useOptionalAuth() || {};
    return user?.role === "owner" ? <FeeForm key={user.id} /> : <p dir="rtl">إدارة تعرفات العمولات متاحة لمالك المتجر.</p>;
}
