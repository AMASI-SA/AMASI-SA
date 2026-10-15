import CurrencyFields from "../CurrencyFields";
import React from "react";
import { OpeningField } from "./OpeningCourierEditor";

const selectClass = "min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3";

export function validateTermsRows(domain, rows, classifications = [], providers = []) {
    const errors = [];
    for (const row of rows) {
        if (!row.evidence_ref?.trim()) errors.push("دليل البند مطلوب.");
        if (domain === "payment_fees") {
            if (!providers.some(p => p.id === row.provider)) errors.push("اختر مزودًا حقيقيًا.");
            if (!["mdr_percent", "fixed_fee_per_order", "vat_on_fees_percent"].every(field => /^\d+(\.\d{1,2})?$/.test(row[field] || ""))) errors.push("أدخل الرسوم والضريبة صراحة بقيم غير سالبة.");
            if (Number(row.mdr_percent) > 100 || Number(row.vat_on_fees_percent) > 100) errors.push("النسبة لا تتجاوز 100%.");
        } else {
            if (!classifications.some(c => c.id === row.classification)) errors.push("تصنيف النواة مطلوب.");
            if (!row.name?.trim() || !row.entity_id?.trim()) errors.push("اسم البند ومرجع الجهة مطلوبان.");
            if (!/^\d+(\.\d{1,2})?$/.test(row.amount || "")) errors.push("الرصيد مطلوب بقيمة غير سالبة؛ أدخل الصفر صراحة.");
        }
    }
    return [...new Set(errors)];
}

// Capability/catalogue comes from the integration layer; no category or fee support is inferred.
export default function OpeningTermsEditor({ domain, value = [], onChange, classifications = [], providers = [], feeConfigurationSupported = false }) {
    const patch = (index, changes) => onChange(value.map((row, i) => i === index ? { ...row, ...changes } : row));
    const fees = domain === "payment_fees";
    const errors = validateTermsRows(domain, value, classifications, providers);
    if (fees && !feeConfigurationSupported) return <p role="status" className="rounded-xl bg-amber-50 p-4">إعداد رسوم المزود غير متاح في عقد التكامل الحالي. الرسوم الفعلية من كشف التسوية، ولا يوجد تعلّم آلي لرسوم مستقبلية.</p>;
    return <section dir="rtl" className="space-y-4">
        {!fees && <p className="rounded-xl bg-amber-50 p-4">هذه أرصدة عند القطع وفق تصنيف النواة. لا ينفذ المعالج إطفاءً آليًا أو مصروفًا دوريًا. المصروف السابق للقطع غير المدفوع التزام افتتاحي.</p>}
        {!fees && !classifications.length && <p role="status">تصنيفات النواة غير متاحة بعد؛ لا يمكن إكمال التعيين المحاسبي.</p>}
        {value.map((row, index) => <fieldset key={index} className="grid gap-4 rounded-xl border p-4 md:grid-cols-2"><legend>بند {index + 1}</legend>
            {fees ? <>
                <label>المزود<select aria-label={`مزود الرسوم ${index + 1}`} className={selectClass} value={row.provider || ""} onChange={e => patch(index, { provider: e.target.value, mdr_percent: "", fixed_fee_per_order: "", vat_on_fees_percent: "", evidence_ref: "" })}><option value="">اختر</option>{providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
                {[["mdr_percent", "العمولة %"], ["fixed_fee_per_order", "رسم ثابت للطلب"], ["vat_on_fees_percent", "ضريبة الرسوم %"]].map(([field, label]) => <OpeningField key={field} label={`${label} ${index + 1}`} type="number" min="0" step="0.01" value={row[field]} onChange={v => patch(index, { [field]: v })} />)}
            </> : <>
                <label>تصنيف النواة<select aria-label={`تصنيف النواة ${index + 1}`} className={selectClass} value={row.classification || ""} disabled={!classifications.length} onChange={e => patch(index, { classification: e.target.value })}><option value="">اختر التصنيف المعتمد</option>{classifications.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}</select></label>
                <OpeningField label={`اسم البند ${index + 1}`} value={row.name} onChange={v => patch(index, { name: v })} />
                <OpeningField label={`مرجع الجهة ${index + 1}`} value={row.entity_id} onChange={v => patch(index, { entity_id: v })} />
                <OpeningField label={`الرصيد عند القطع ${index + 1}`} type="number" min="0" step="0.01" value={row.amount} onChange={v => patch(index, { amount: v })} />
                <CurrencyFields row={row} label={`عملة البند ${index + 1}`} onChange={changes => patch(index, changes)} />
                <OpeningField label={`وصف التغطية أو الالتزام ${index + 1}`} value={row.notes} onChange={v => patch(index, { notes: v })} />
            </>}
            <OpeningField label={`دليل البند ${index + 1}`} value={row.evidence_ref} onChange={v => patch(index, { evidence_ref: v })} />
            <button type="button" className="text-rose-800" onClick={() => onChange(value.filter((_, i) => i !== index))}>حذف البند</button>
        </fieldset>)}
        {errors.length > 0 && <ul aria-label="نواقص البنود" className="text-rose-800">{errors.map(e => <li key={e}>{e}</li>)}</ul>}
        <button type="button" className="rounded-lg border px-4 py-2" onClick={() => onChange([...value, fees ? {} : { original_currency: "SAR", fx_rate_to_sar: "1" }])}>إضافة بند</button>
    </section>;
}
