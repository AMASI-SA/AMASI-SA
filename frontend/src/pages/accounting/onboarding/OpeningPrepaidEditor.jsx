import React from "react";
import { OpeningField } from "./OpeningCourierEditor";
import { calculatePrepaid, validatePrepaidRow } from "./prepaidCalculation";

export default function OpeningPrepaidEditor({ value = [], onChange, obligations = [], cutoverDate = "" }) {
    const patch = (index, changes) => onChange(value.map((row, i) => i === index ? { ...row, ...changes } : row));
    const select = (index, id) => {
        const source = obligations.find(item => item.id === id);
        patch(index, { source_mode: "obligation", obligation_id: id, title: source?.title || "", entity: source?.entity || "",
            coverage_start: source?.coverage_start || "", coverage_end: source?.coverage_end || "",
            payment_status: source?.payment_status || "", payment_date: source?.payment_date || "", currency: source?.currency || "",
            amount_paid: "", evidence_ref: "" });
    };
    return <section className="space-y-4" aria-label="المصروفات المدفوعة مقدمًا">
        <p>فقط النقد المدفوع قبل القطع مقابل تغطية لاحقة يدخل الأصل الافتتاحي. نهاية التغطية غير مشمولة؛ الحساب بأيام التقويم الفعلية.</p>
        {!obligations.length && <p role="status">لا توجد التزامات دورية متاحة من ميزان 2. استخدم الاستثناء الصريح عند وجود دفع موثق فقط.</p>}
        {value.map((row, index) => {
            const source = obligations.find(item => item.id === row.obligation_id);
            let result;
            try { result = calculatePrepaid(row, cutoverDate); } catch { /* Incomplete setup is displayed below. */ }
            const errors = validatePrepaidRow(row, cutoverDate, obligations);
            return <fieldset key={index} className="grid gap-4 rounded-xl border p-4 md:grid-cols-2"><legend>بند مدفوع مقدمًا {index + 1}</legend>
                <label>مصدر البند<select aria-label={`مصدر البند ${index + 1}`} value={row.source_mode || "obligation"} onChange={e => onChange(value.map((r, i) => i === index ? { source_mode: e.target.value } : r))}><option value="obligation">اختر التزامًا أو اشتراكًا موجودًا</option><option value="manual_exception">بند مدفوع مقدمًا غير موجود في الالتزامات</option></select></label>
                {row.source_mode !== "manual_exception" ? <><label>الالتزام<select aria-label={`الالتزام ${index + 1}`} value={row.obligation_id || ""} onChange={e => select(index, e.target.value)}><option value="">اختر التزامًا أو اشتراكًا موجودًا</option>{obligations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
                    {source && <dl><dt>اسم الالتزام</dt><dd>{source.title}</dd><dt>نوع المصروف</dt><dd>{source.expense_type}</dd><dt>الجهة</dt><dd>{source.entity}</dd><dt>الدورة</dt><dd>{source.cycle}</dd><dt>مبلغ الفترة — ليس إثبات دفع</dt><dd>{source.period_amount}</dd><dt>حالة الالتزام</dt><dd>{source.status}</dd><dt>حالة الدفع</dt><dd>{source.payment_status || "غير متاحة؛ يلزم دليل دفع"}</dd></dl>}</> : <><OpeningField label={`اسم البند ${index + 1}`} value={row.title} onChange={title => patch(index, { title })} /><OpeningField label={`الجهة ${index + 1}`} value={row.entity} onChange={entity => patch(index, { entity })} /><p>استثناء إعداد فقط؛ لن يتم إنشاء التزام دوري.</p></>}
                {[["coverage_start", "بداية التغطية"], ["coverage_end", "نهاية التغطية غير المشمولة"], ["payment_date", "تاريخ الدفع"]].map(([field, label]) => <OpeningField key={field} type="date" label={`${label} ${index + 1}`} value={row[field]} onChange={v => patch(index, { [field]: v })} />)}
                <OpeningField label={`المبلغ المدفوع ${index + 1}`} value={row.amount_paid} onChange={amount_paid => patch(index, { amount_paid })} />
                <label>عملة الدفع<select aria-label={`عملة الدفع ${index + 1}`} value={row.currency || ""} onChange={e => patch(index, { currency: e.target.value })}><option value="">أكد العملة من الدليل</option><option value="SAR">SAR</option></select></label>
                <OpeningField label={`دليل الدفع والتغطية ${index + 1}`} value={row.evidence_ref} onChange={evidence_ref => patch(index, { evidence_ref })} />
                {result && <div role="status">أيام التغطية: {result.coverage_days} · المستهلكة: {result.consumed_days} · المتبقية: {result.remaining_days}<p>المستهلك قبل القطع: <output>{result.consumed_before_cutover}</output> SAR</p><p>الأصل المقدم المتبقي: <output>{result.prepaid_remaining_at_cutover}</output> SAR</p></div>}
                {errors.length > 0 && <ul aria-label={`نواقص البند ${index + 1}`}>{errors.map(error => <li key={error}>{error}</li>)}</ul>}
                <button type="button" onClick={() => onChange(value.filter((_, i) => i !== index))}>حذف البند</button>
            </fieldset>;
        })}
        <button type="button" onClick={() => onChange([...value, { source_mode: "obligation" }])}>إضافة بند مدفوع مقدمًا</button>
    </section>;
}
