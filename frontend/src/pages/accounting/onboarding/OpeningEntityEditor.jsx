import React, { useState } from "react";
import { OpeningField } from "./OpeningCourierEditor";

const inputClass = "mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3";
export const DOMAIN_FIELDS = {
    banks: [["balance", "الرصيد الافتتاحي"]],
    providers: [["balance", "الرصيد المستحق لنا"]],
    employees: [["salary_payable", "راتب مستحق"], ["advance", "سلفة الموظف"], ["custody", "عهدة الموظف"]],
    suppliers: [["payable", "مستحق للمورد"]],
    external_persons: [["receivable", "مستحق لنا على الطرف"]],
    drivers: [["cod_receivable", "COD في عهدة الموصل"], ["fee_payable", "أجرة مستحقة للموصل"]],
    advertising: [["prepaid_wallet", "محفظة مدفوعة مقدمًا"], ["payable", "مستحق للمنصة"]],
};
export function validateEntityRows(rows, fields, entities, banks = [], requireBank = false) {
    const errors = [];
    const seen = new Set();
    for (const row of rows) {
        if (!entities.some(entity => entity.id === row.entity_id) || seen.has(row.entity_id)) errors.push("اختر هوية حقيقية دون تكرار.");
        seen.add(row.entity_id);
        for (const [field] of fields) {
            if (!/^\d+(\.\d{1,2})?$/.test(row[field] ?? "") || !Number.isFinite(Number(row[field]))) errors.push("أدخل كل مبلغ صراحة، بما فيه الصفر؛ لا تقبل القيم السالبة.");
        }
        if (!row.evidence_ref?.trim()) errors.push("دليل ناقص.");
        if (requireBank && !banks.some(bank => bank.id === row.settlement_bank_id)) errors.push("اختر بنك التسوية.");
    }
    return [...new Set(errors)];
}

export default function OpeningEntityEditor({ domain, value = [], onChange, entities = [], banks = [], createExternalPerson, onEntityCreated }) {
    const fields = DOMAIN_FIELDS[domain] || [];
    const [person, setPerson] = useState({ name: "", phone: "", notes: "" });
    const [adding, setAdding] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const patch = (index, changes) => onChange(value.map((row, i) => i === index ? { ...row, ...changes } : row));
    async function create() {
        if (!person.name.trim() || !person.phone.trim()) { setError("الاسم والهاتف مطلوبان."); return; }
        setBusy(true); setError("");
        try {
            const created = await createExternalPerson(person);
            if (!created?.id) throw new Error("لم يُعد الخادم هوية الطرف.");
            onEntityCreated?.(created);
            onChange([...value, { entity_id: created.id, evidence_ref: "", receivable: "" }]);
            setAdding(false); setPerson({ name: "", phone: "", notes: "" });
        } catch (err) { setError(err?.response?.data?.detail?.code || err.message || "تعذر إنشاء الطرف."); }
        finally { setBusy(false); }
    }
    return <section dir="rtl" className="space-y-4">
        <p className="text-sm text-slate-600">اختر الجهة المكتشفة، ثم أدخل المبالغ والأدلة. وجود الجهة لا يعني وجود رصيد.</p>
        {domain === "banks" && <p className="rounded-lg bg-amber-50 p-3">الحساب البنكي السالب يُصنف التزام سحب على المكشوف مستقلًا. الصندوق لا يقبل السالب.</p>}
        {domain === "providers" && <p>مدى وApple Pay ضمن جهة التسوية الفعلية؛ لا رصيد منصة مكرر ولا تعلّم آلي لرسوم مستقبلية.</p>}
        {domain === "suppliers" && <p>دفعة المورد المقدمة أصل مستقل، ولا تُخصم من مستحقه. تمكينها ينتظر تصنيف النواة.</p>}
        {value.map((row, index) => <fieldset key={index} className="grid gap-4 rounded-xl border bg-white p-4 md:grid-cols-2" disabled={busy}>
            <legend className="px-2 font-bold">جهة {index + 1}</legend>
            <label>الجهة<select aria-label={`الجهة ${index + 1}`} className={inputClass} value={row.entity_id} onChange={e => patch(index, { entity_id: e.target.value, evidence_ref: "", settlement_bank_id: "", funding_account_id: "", funding_reference: "", original_currency: "", fx_rate_to_sar: "", fx_at: "", fx_source: "", ...Object.fromEntries(fields.map(([field]) => [field, ""])) })}><option value="">اختر جهة موجودة</option>{entities.map(entity => <option key={entity.id} value={entity.id}>{entity.name}{entity.currency ? ` · ${entity.currency}` : ""}</option>)}</select></label>
            {fields.map(([field, label]) => <div key={field}><OpeningField label={`${label} ${index + 1}`} type="number" min="0" step="0.01" value={row[field]} onChange={v => patch(index, { [field]: v })} /><button type="button" className="mt-1 text-sm text-emerald-800 underline" onClick={() => patch(index, { [field]: "0" })}>إثبات صفر — {label}</button></div>)}
            {domain === "providers" && <label>بنك التسوية<select aria-label={`بنك التسوية ${index + 1}`} className={inputClass} value={row.settlement_bank_id || ""} onChange={e => patch(index, { settlement_bank_id: e.target.value })}><option value="">اختر البنك صراحة</option>{banks.map(bank => <option key={bank.id} value={bank.id}>{bank.name}</option>)}</select></label>}
            {domain === "advertising" && <>
                <label>مرجع حساب التمويل (عند انطباقه)<select aria-label={`حساب التمويل ${index + 1}`} className={inputClass} value={row.funding_account_id || ""} onChange={e => patch(index, { funding_account_id: e.target.value })}><option value="">لم يُحدد / لا ينطبق</option>{banks.map(bank => <option key={bank.id} value={bank.id}>{bank.name}</option>)}</select></label>
                <OpeningField label={`مرجع التمويل ${index + 1}`} value={row.funding_reference} onChange={v => patch(index, { funding_reference: v })} />
                <OpeningField label={`العملة ${index + 1}`} value={row.original_currency} onChange={v => patch(index, { original_currency: v.toUpperCase() })} />
                {row.original_currency && row.original_currency !== "SAR" && <><OpeningField label={`سعر التحويل للريال ${index + 1}`} type="number" min="0" step="0.000001" value={row.fx_rate_to_sar} onChange={v => patch(index, { fx_rate_to_sar: v })} /><OpeningField label={`توقيت التحويل — الرياض ${index + 1}`} type="datetime-local" value={row.fx_at} onChange={v => patch(index, { fx_at: v })} /><OpeningField label={`مصدر سعر التحويل ${index + 1}`} value={row.fx_source} onChange={v => patch(index, { fx_source: v })} /></>}
            </>}
            <OpeningField label={`الدليل المطلوب ${index + 1}`} value={row.evidence_ref} onChange={v => patch(index, { evidence_ref: v })} />
            <button type="button" className="text-rose-800" onClick={() => onChange(value.filter((_, i) => i !== index))}>حذف الجهة من المسودة</button>
        </fieldset>)}
        <button type="button" className="rounded-lg border px-4 py-2 font-bold" disabled={busy} onClick={() => onChange([...value, { entity_id: "", evidence_ref: "", ...Object.fromEntries(fields.map(([field]) => [field, ""])) }])}>اختيار جهة موجودة</button>
        {domain === "external_persons" && <button type="button" className="ms-2 rounded-lg border px-4 py-2" disabled={busy || !createExternalPerson} onClick={() => setAdding(!adding)}>إضافة طرف جديد</button>}
        {adding && <fieldset disabled={busy} className="space-y-3 rounded-xl border p-4"><legend>طرف خارجي جديد</legend><OpeningField label="اسم الطرف" value={person.name} onChange={v => setPerson({ ...person, name: v })} /><OpeningField label="هاتف الطرف" type="tel" value={person.phone} onChange={v => setPerson({ ...person, phone: v })} /><OpeningField label="ملاحظات الطرف" value={person.notes} onChange={v => setPerson({ ...person, notes: v })} /><button type="button" className="rounded-lg bg-emerald-800 px-4 py-2 text-white" onClick={create}>حفظ الطرف واختياره</button></fieldset>}
        {error && <p role="alert" className="text-rose-800">{error}</p>}
    </section>;
}
