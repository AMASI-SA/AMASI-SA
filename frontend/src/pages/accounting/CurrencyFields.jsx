import React from "react";
import { CURRENCY_CODES, isCurrencyCode, currencyChange, fxTimeInRiyadh } from "./currencyRules";
export { CURRENCY_CODES, isCurrencyCode, currencyChange } from "./currencyRules";
const fieldClass = "mt-1 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm";

export function CurrencySelect({ value = "SAR", onChange, label = "العملة", className = fieldClass }) {
    const valid = isCurrencyCode(value);
    return <select required aria-label={label} className={className} value={valid ? value : ""} onChange={event => { if (isCurrencyCode(event.target.value)) onChange(event.target.value); }}>
        {!valid && <option value="" disabled>عملة غير متاحة — اختر من القائمة</option>}
        {CURRENCY_CODES.map(code => <option key={code} value={code}>{code}</option>)}
    </select>;
}

export default function CurrencyFields({ row, onChange, label = "العملة", accountBound = false, account }) {
    const currency = accountBound ? account?.currency : row.original_currency === undefined ? "SAR" : row.original_currency;
    const valid = isCurrencyCode(currency);
    const foreign = valid && currency !== "SAR";
    return <>
        <label className="block text-sm font-semibold">{label}{accountBound
            ? <output aria-label={label} className="mt-2 block" dir="ltr">{valid ? currency : "BLOCKED_BY_BACKEND / not_ready"}</output>
            : <CurrencySelect label={label} value={currency} onChange={code => onChange(currencyChange(code))} />}</label>
        {accountBound && !valid && <p role="alert">عملة حساب MZ2 غير متاحة؛ لا يمكن اختيار عملة بديلة.</p>}
        {currency === "SAR" && <p className="text-xs text-slate-500">سعر التحويل إلى SAR: 1 تلقائيًا</p>}
        {foreign && <>
            <label className="block text-sm">سعر التحويل إلى SAR<input required type="number" min="0.000001" step="0.000001" aria-label={`سعر التحويل ${label}`} className={fieldClass} value={row.fx_rate_to_sar ?? ""} onChange={event => onChange({ fx_rate_to_sar: event.target.value })} /></label>
            <label className="block text-sm">توقيت التحويل — الرياض<input required type="datetime-local" aria-label={`توقيت التحويل ${label}`} className={fieldClass} value={fxTimeInRiyadh(row.fx_at)} onChange={event => onChange({ fx_at: event.target.value })} /></label>
            <label className="block text-sm">مصدر سعر التحويل<input required={!row.fx_evidence_file_id} aria-label={`مصدر التحويل ${label}`} className={fieldClass} value={row.fx_source || ""} onChange={event => onChange({ fx_source: event.target.value })} /></label>
        </>}
    </>;
}
