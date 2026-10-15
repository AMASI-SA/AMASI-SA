import React, { useState } from "react";

const inputClass = "mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm";
export const newCourierDraft = () => ({ shipping_cost: "", shipping_vat_percent: "", shipping_cost_vat_inclusive: "", commission_vat_percent: "", commission_vat_inclusive: "", payment_mode: "", effective_from: "", effective_to: "", settlement_bank_id: "", opening_cod_receivable: "", opening_payable: "", evidence_ref: "", cod_fee_tiers: [] });
const numeric = value => typeof value === "string" && /^\d+(\.\d+)?$/.test(value) && Number.isFinite(Number(value));

export function validateCourierDraft(draft, banks = [], termsOnly = false) {
    const errors = [];
    for (const key of ["shipping_cost", "shipping_vat_percent", "commission_vat_percent", ...(termsOnly ? [] : ["opening_cod_receivable", "opening_payable"])]) {
        if (!numeric(draft[key])) errors.push("أدخل القيم المطلوبة صراحة؛ الصفر لا يُستنتج من حقل فارغ.");
    }
    if (["shipping_vat_percent", "commission_vat_percent"].some(key => Number(draft[key]) > 100)) errors.push("الضريبة بين 0 و100 بالمئة.");
    if ([draft.shipping_cost_vat_inclusive, draft.commission_vat_inclusive].some(v => typeof v !== "boolean")) errors.push("حدد شمول ضريبة الشحن والعمولة.");
    if (!["prepaid", "postpaid"].includes(draft.payment_mode)) errors.push("حدد طريقة سداد الشركة.");
    if (!draft.effective_from || (draft.effective_to && draft.effective_to <= draft.effective_from)) errors.push("حدد بداية السريان ونهاية لاحقة إن وجدت — بتوقيت الرياض.");
    if (!termsOnly && !banks.some(bank => bank.id === draft.settlement_bank_id)) errors.push("اختر بنك التسوية الموثوق صراحة.");
    if (!draft.evidence_ref?.trim()) errors.push("دليل الشركة ناقص.");
    if (!draft.cod_fee_tiers?.length) errors.push("أدخل شريحة COD صريحة، حتى إذا كانت العمولة صفرًا.");
    for (const key of ["shipping_cost", ...(termsOnly ? [] : ["opening_cod_receivable", "opening_payable"])]) {
        if (!/^\d+(\.\d{1,2})?$/.test(draft[key] || "")) errors.push("المبالغ النقدية لا تتجاوز منزلتين عشريتين.");
    }
    for (const tier of draft.cod_fee_tiers || []) {
        if (![tier.min_amount, tier.commission_percent, tier.fixed_fee].every(numeric) || Number(tier.commission_percent) > 1 || (tier.max_amount !== "" && (!numeric(tier.max_amount) || Number(tier.max_amount) < Number(tier.min_amount)))) errors.push("راجع حدود شريحة COD وعمولتها.");
        if (![tier.min_amount, tier.fixed_fee, ...(tier.max_amount === "" ? [] : [tier.max_amount])].every(v => /^\d+(\.\d{1,2})?$/.test(v))) errors.push("حدود COD والرسوم الثابتة يجب أن تكون بالهللات.");
    }
    const tiers = [...(draft.cod_fee_tiers || [])].sort((a, b) => Number(a.min_amount) - Number(b.min_amount));
    for (let i = 1; i < tiers.length; i++) {
        const previous = tiers[i - 1], current = tiers[i];
        if (previous.max_amount === "" || Number(current.min_amount) < Number(previous.max_amount)
            || (Number(current.min_amount) === Number(previous.max_amount) && current.min_inclusive && previous.max_inclusive)) errors.push("شرائح COD متداخلة.");
        const previousLast = Math.round(Number(previous.max_amount) * 100) - (previous.max_inclusive ? 0 : 1);
        const currentFirst = Math.round(Number(current.min_amount) * 100) + (current.min_inclusive ? 0 : 1);
        if (currentFirst > previousLast + 1) errors.push("توجد فجوة بين حدود شرائح COD بالهللات.");
    }
    return [...new Set(errors)];
}

export function OpeningField({ label, value, onChange, type = "text", ...props }) {
    return <label className="block text-sm font-semibold text-slate-700">{label}<input {...props} aria-label={label} className={inputClass} type={type} value={value ?? ""} onChange={e => onChange(e.target.value)} /></label>;
}
function Inclusion({ label, value, onChange }) {
    return <label className="block text-sm font-semibold">{label}<select aria-label={label} className={inputClass} value={value === "" || value === undefined ? "" : String(value)} onChange={e => onChange(e.target.value === "" ? "" : e.target.value === "true")}><option value="">حدد المعالجة</option><option value="true">شامل الضريبة</option><option value="false">غير شامل الضريبة</option></select></label>;
}

// Values are controlled by the durable wizard session, keyed by the actual courier ID.
// Switching selection never initializes another courier from the current courier.
export default function OpeningCourierEditor({ value = {}, onChange, couriers = [], banks = [], onSave, busy = false, termsOnly = false }) {
    const [selected, setSelected] = useState("");
    const [errors, setErrors] = useState([]);
    const draft = value[selected] || newCourierDraft();
    const patch = changes => { setErrors([]); onChange({ ...value, [selected]: { ...draft, ...changes } }); };
    const save = async () => {
        const next = validateCourierDraft(draft, banks, termsOnly);
        if (termsOnly && !["contract", "invoice", "statement", "owner_confirmation"].includes(draft.source_kind)) next.push("حدد نوع المصدر صراحة."); setErrors(next);
        if (!next.length && onSave) {
            try { await onSave(selected, draft); }
            catch (error) {
                const detail = error?.response?.data?.detail;
                setErrors([typeof detail === "string" ? detail : detail?.code || error?.message || "تعذر حفظ مسودة الشركة؛ راجع البيانات وأعد المحاولة."]);
            }
        }
    };
    return <section dir="rtl" className="space-y-4" data-testid="opening-couriers">
        <p className="rounded-xl bg-amber-50 p-3 font-bold" role="status">{termsOnly ? "حفظ شروط العقد لا يرحّل قيدًا ولا يفعّل التشغيل." : "P02 — LOCKED · بيانات تحضيرية فقط"}</p>
        <p className="text-sm text-slate-600">{termsOnly ? "اختر شركة الشحن المسجلة وأدخل الشروط وفق أصل العقد." : "الجهة المكتشفة اقتراح هوية فقط. أدخل الرصيد والرسوم والبنك من أدلة مستقلة."}</p>
        <label className="block font-bold">شركة الشحن<select aria-label="شركة الشحن" className={inputClass} value={selected} disabled={busy} onChange={e => { setSelected(e.target.value); setErrors([]); }}><option value="">اختر الشركة</option>{couriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        {selected && <fieldset disabled={busy} className="space-y-4"><legend className="font-bold">مسودة {couriers.find(c => c.id === selected)?.name}</legend>
            <div className="grid gap-4 md:grid-cols-2">
                <OpeningField label="تكلفة الشحن" type="number" min="0" step="0.01" value={draft.shipping_cost} onChange={v => patch({ shipping_cost: v })} />
                <OpeningField label="ضريبة الشحن %" type="number" min="0" max="100" value={draft.shipping_vat_percent} onChange={v => patch({ shipping_vat_percent: v })} />
                <Inclusion label="شمول ضريبة الشحن" value={draft.shipping_cost_vat_inclusive} onChange={v => patch({ shipping_cost_vat_inclusive: v })} />
                <label>سداد الشركة<select aria-label="سداد الشركة" className={inputClass} value={draft.payment_mode} onChange={e => patch({ payment_mode: e.target.value })}><option value="">اختر</option><option value="prepaid">مسبق الدفع</option><option value="postpaid">آجل</option></select></label>
                <OpeningField label="ضريبة العمولة %" type="number" min="0" max="100" value={draft.commission_vat_percent} onChange={v => patch({ commission_vat_percent: v })} />
                <Inclusion label="شمول ضريبة العمولة" value={draft.commission_vat_inclusive} onChange={v => patch({ commission_vat_inclusive: v })} />
                <OpeningField label="بداية السريان — الرياض" type="datetime-local" value={draft.effective_from} onChange={v => patch({ effective_from: v })} />
                <OpeningField label="نهاية السريان — الرياض (اختياري)" type="datetime-local" value={draft.effective_to} onChange={v => patch({ effective_to: v })} />
                <p className="md:col-span-2">الأرصدة والبنك أدناه تخص المرحلة 8 ولا تُرسل ضمن شروط العقد.</p>
                <label>بنك التسوية<select aria-label="بنك التسوية" className={inputClass} value={draft.settlement_bank_id} onChange={e => patch({ settlement_bank_id: e.target.value })}><option value="">اختر البنك</option>{banks.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
                <OpeningField label="COD افتتاحي لنا" type="number" min="0" step="0.01" value={draft.opening_cod_receivable} onChange={v => patch({ opening_cod_receivable: v })} />
                <OpeningField label="مستحق افتتاحي للشركة" type="number" min="0" step="0.01" value={draft.opening_payable} onChange={v => patch({ opening_payable: v })} />
                <OpeningField label="مرجع دليل الشركة — مطلوب" value={draft.evidence_ref} onChange={v => patch({ evidence_ref: v })} />
            </div>
            {termsOnly && <label>نوع مصدر العقد<select aria-label="نوع مصدر العقد" className={inputClass} value={draft.source_kind || ""} onChange={e => patch({ source_kind: e.target.value })}><option value="">اختر المصدر</option><option value="contract">عقد</option><option value="invoice">فاتورة</option><option value="statement">كشف</option><option value="owner_confirmation">إقرار المالك الموثق</option></select></label>}
            <h4 className="font-bold">شرائح COD</h4>
            {(draft.cod_fee_tiers || []).map((tier, i) => {
                const edit = changes => patch({ cod_fee_tiers: draft.cod_fee_tiers.map((t, index) => index === i ? { ...t, ...changes } : t) });
                return <div key={i} className="grid gap-3 rounded-xl border p-3 md:grid-cols-2">
                    <OpeningField label={`من مبلغ ${i + 1}`} type="number" value={tier.min_amount} onChange={v => edit({ min_amount: v })} />
                    <OpeningField label={`إلى مبلغ ${i + 1} (فارغ بلا حد)`} type="number" value={tier.max_amount} onChange={v => edit({ max_amount: v })} />
                    <label><input type="checkbox" checked={tier.min_inclusive} onChange={e => edit({ min_inclusive: e.target.checked })} /> يشمل الحد الأدنى</label>
                    <label><input type="checkbox" checked={tier.max_inclusive} onChange={e => edit({ max_inclusive: e.target.checked })} /> يشمل الحد الأعلى</label>
                    <OpeningField label={`نسبة العمولة ${i + 1} (0.01 = 1%)`} type="number" min="0" max="1" step="0.0001" value={tier.commission_percent} onChange={v => edit({ commission_percent: v })} />
                    <OpeningField label={`العمولة الثابتة ${i + 1}`} type="number" min="0" step="0.01" value={tier.fixed_fee} onChange={v => edit({ fixed_fee: v })} />
                    <button type="button" onClick={() => patch({ cod_fee_tiers: draft.cod_fee_tiers.filter((_, index) => index !== i) })}>حذف الشريحة</button>
                </div>;
            })}
            <button type="button" className="rounded-lg border px-4 py-2" onClick={() => patch({ cod_fee_tiers: [...draft.cod_fee_tiers, { min_amount: "", max_amount: "", min_inclusive: true, max_inclusive: false, commission_percent: "", fixed_fee: "" }] })}>إضافة شريحة</button>
            {errors.length > 0 && <ul role="alert" className="text-rose-800">{errors.map(error => <li key={error}>{error}</li>)}</ul>}
            <button type="button" className="block rounded-lg bg-emerald-800 px-5 py-3 font-bold text-white disabled:opacity-40" disabled={!onSave} onClick={save}>حفظ مسودة الشركة</button>
        </fieldset>}
    </section>;
}
