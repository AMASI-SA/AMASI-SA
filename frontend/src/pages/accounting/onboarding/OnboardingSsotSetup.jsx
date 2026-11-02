import React, { useEffect, useState } from "react";
import { CurrencySelect, isCurrencyCode } from "../CurrencyFields";

const names = { accrued_expense: "مصروف مستحق", other_payable: "التزام آخر", other_receivable: "ذمة مدينة أخرى", sales_vat_payable: "ضريبة مبيعات مستحقة", input_vat: "ضريبة مدخلات", prepaid_expense: "مدفوع مقدمًا استثنائي" };
const sid = stage => stage === "payment_fees" ? "providers" : "equity";
export function contractSection(session, stage, contract) {
    const section = session.sections[sid(stage)];
    const data = { ...section.data, lines: [...section.data.lines] };
    if (stage === "payment_fees") data.fee_policy_ids = [...new Set([...(data.fee_policy_ids || []), contract.id])];
    else {
        const field = contract.calculation ? "prepaid_selection_ids" : "typed_fact_ids";
        data[field] = [...new Set([...(data[field] || []), contract.id])];
        const amount = contract.amount ?? contract.calculation.remaining_prepaid_after_cutover;
        const debit = ["prepaid_expense", "other_receivable", "input_vat"].includes(contract.category);
        const existing = data.lines.find(line => line.entity_id === contract.entity_id && line.category === contract.category) || {};
        data.lines = data.lines.filter(line => !(line.entity_id === contract.entity_id && line.category === contract.category));
        data.lines.push({ ...existing, ...contract.fx_fields, category: contract.category, entity_id: contract.entity_id,
            original_amount: amount, original_currency: contract.currency,
            fx_rate_to_sar: contract.currency === "SAR" ? "1" : (contract.fx_fields?.fx_rate_to_sar || existing.fx_rate_to_sar),
            meaning: Number(amount) === 0 ? "zero" : debit ? "available_to_us" : "owed_by_us", evidence_file_id: contract.evidence });
    }
    return { status: "incomplete", reason: section.reason || "", evidence_file_id: section.evidence_file_id, data };
}

export default function OnboardingSsotSetup({ stage, session, transport, disabled, onSelect, onError, onBusyChange = () => {} }) {
    const [items, setItems] = useState([]), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
    const [catalogStatus, setCatalogStatus] = useState("loading");
    const [form, setForm] = useState({ provider: "salla", vat_treatment: "exclusive", currency: "SAR", category: "accrued_expense" });
    const cutover = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(session.cutover.cutover_at));
    const section = session.sections[sid(stage)];
    const field = (key, label, type = "text") => <label className="block" key={key}>{label}<input aria-label={label} type={type} value={form[key] || ""} onChange={e => setForm(v => ({ ...v, [key]: e.target.value }))} className="block w-full rounded border p-2" /></label>;
    async function fetchItems() {
        const result = stage === "payment_fees" ? await transport.listOnboardingFeePolicies() : stage === "prepaid" ? await transport.listOnboardingPrepaids(cutover) : await transport.listOnboardingFacts();
        if (!Array.isArray(result?.items)) throw new Error("onboarding_response_invalid");
        return result.items;
    }
    useEffect(() => { let active = true; setItems([]); setMessage(""); setCatalogStatus("loading"); fetchItems().then(rows => { if (active) { setItems(rows); setCatalogStatus("ready"); } }).catch(e => { if (active) { setCatalogStatus("error"); onError(e); } }); return () => { active = false; }; }, [stage, session.id, session.version]); // explicit saves refresh persisted selections
    async function run(task) { if (busy) return; setBusy(true); onBusyChange(true); setMessage(""); try { await task(); setItems(await fetchItems()); setCatalogStatus("ready"); setMessage("حُفظ العقد واختياره في الجلسة. أكمل دليل القسم ومراجعته."); } catch (e) { onError(e); } finally { setBusy(false); onBusyChange(false); } }
    async function select(contract) {
        const fx_fields = contract.currency !== "SAR" && form.fx_rate_to_sar ? {
            fx_rate_to_sar: form.fx_rate_to_sar, fx_at: form.fx_at ? `${form.fx_at}:00+03:00` : undefined,
            fx_source: form.fx_source, fx_evidence_file_id: form.fx_evidence_file_id,
        } : undefined;
        await onSelect({ ...contract, fx_fields });
    }
    async function create() {
        if (!section.evidence_file_id) throw new Error("opening_evidence_section_file_required");
        if (!isCurrencyCode(form.currency)) throw new Error("onboarding_account_currency_mismatch");
        const evidence = section.evidence_file_id;
        const bounds = Object.fromEntries(["minimum", "maximum"].filter(key => form[key] !== undefined && form[key] !== "").map(key => [key, form[key]]));
        const contract = stage === "payment_fees" ? await transport.createOnboardingFeePolicy({ provider: form.provider, percentage: form.percentage, fixed_amount: form.fixed_amount, ...bounds, vat_treatment: form.vat_treatment, effective_from: form.effective_from, effective_to: form.effective_to || null, currency: form.currency, evidence }) : await transport.createOnboardingFact({ category: form.category, display_name: form.display_name, reference: form.reference, amount: form.amount, currency: form.currency, cutover_date: cutover, evidence, ...(form.category === "prepaid_expense" ? { manual_contract: form.manual_contract } : {}) });
        await select(contract);
    }
    return <fieldset disabled={disabled || busy} className="space-y-3" data-testid="ssot-setup">
        <p>الدليل المطلوب هو ملف القسم المحفوظ: {section.evidence_file_id || "ارفع الدليل واحفظ القسم أولًا"}. الأرصدة والضريبتان مستقلّة؛ لا مقاصة.</p>
        {stage === "payment_fees" && <div className="grid gap-4 md:grid-cols-2"><label>المزود<select className="block w-full rounded border p-2" aria-label="مزود سياسة الرسوم" value={form.provider} onChange={e => setForm(v => ({ ...v, provider: e.target.value }))}>{["salla", "tabby", "tamara", "emkan"].map(p => <option key={p}>{p}</option>)}</select></label>{field("percentage", "النسبة المئوية")}{field("fixed_amount", "المبلغ الثابت")}{field("minimum", "الحد الأدنى للرسوم — اختياري")}{field("maximum", "الحد الأعلى للرسوم — اختياري")}{field("effective_from", "سارية من", "date")}{field("effective_to", "سارية حتى", "date")}<label>معالجة الضريبة<select className="block w-full rounded border p-2" aria-label="معالجة الضريبة" value={form.vat_treatment} onChange={e => setForm(v => ({ ...v, vat_treatment: e.target.value }))}>{Object.entries({ inclusive: "شاملة الضريبة", exclusive: "غير شاملة الضريبة", exempt: "معفاة", not_applicable: "لا تنطبق وفق العقد" }).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><p className="text-sm text-slate-600 md:col-span-2">الحد الفارغ يبقى غير محدد، ولا يتحول إلى صفر. شروط النطاق والسريان والتداخل تُفحص في الخادم.</p></div>}
        {stage === "obligations" && <><label>عقد التصنيف<select className="block w-full rounded border p-2" aria-label="عقد التصنيف" value={form.category} onChange={e => setForm(v => ({ ...v, category: e.target.value }))}><optgroup label="لنا — أصول وحقوق">{["other_receivable", "input_vat", "prepaid_expense"].map(key => <option key={key} value={key}>{names[key]}</option>)}</optgroup><optgroup label="علينا — التزامات">{["accrued_expense", "other_payable", "sales_vat_payable"].map(key => <option key={key} value={key}>{names[key]}</option>)}</optgroup></select></label>{field("display_name", "اسم الجهة أو الالتزام")}{field("reference", "مرجع العقد")}{field("amount", "المبلغ الموثق")}{form.category === "prepaid_expense" && field("manual_contract", "العقد الاستثنائي للمدفوع مقدمًا")}<p>التأمينات: عقد تصنيف مستقل غير مثبت؛ لا تُنشأ تحت تصنيف آخر.</p></>}
        <label className="block">عملة العقد<CurrencySelect label="عملة العقد" value={form.currency} onChange={currency => setForm(v => ({ ...v, currency, fx_rate_to_sar: "", fx_at: "", fx_source: "", fx_evidence_file_id: "" }))} /></label>
        {stage !== "payment_fees" && <details><summary>دليل تحويل العملة عند اختيار عقد بعملة غير SAR</summary>{field("fx_rate_to_sar", "سعر التحويل إلى SAR")}{field("fx_at", "وقت سعر التحويل بالرياض", "datetime-local")}{field("fx_source", "مصدر سعر التحويل")}{field("fx_evidence_file_id", "معرف ملف دليل سعر التحويل")}</details>}
        {stage !== "prepaid" && <button type="button" onClick={() => run(create)}>إنشاء العقد وحفظ اختياره</button>}
        {stage === "prepaid" && <p>اختر فاتورة التزام حقيقي مدفوعة قبل القطع. الحساب بأيام التقويم، ويشمل تاريخ نهاية التغطية وفق عقد الفاتورة.</p>}
        {catalogStatus === "loading" && <p role="status">جارٍ قراءة العقود المحفوظة…</p>}
        {catalogStatus === "error" && <p role="alert">تعذر تحميل العقود؛ لا يمكن اعتبار القائمة فارغة أو الرصيد صفرًا. تحقق من الصلاحية والاتصال.</p>}
        {catalogStatus === "ready" && items.length === 0 && <p role="status" className="rounded-xl bg-amber-50 p-3">لا توجد عقود متاحة في هذا المصدر. أدخل المصدر الموثق أو راجع هويته؛ لا يعني ذلك صفرًا أو عدم الانطباق.</p>}
        <ul>{items.map(item => stage === "prepaid" ? <li className="rounded border p-3" key={item.invoice_id}>
            <p>{item.title} · {item.type} · {item.entity_name || item.entity_id || "جهة غير محددة"}</p><p>التغطية: {item.coverage_start} → {item.coverage_end}</p><p>الدفع: {item.payment_date || "غير مدفوع"} · {item.payment_amount ?? "ناقص"} {item.currency || "عملة تحتاج تأكيدًا"}</p><p>تجديد تلقائي: {String(item.auto_renew)} · الدليل: {item.evidence || "ناقص"}</p><p>المستهلك: {item.calculation.consumed_before_cutover ?? "غير محسوب"} · المتبقي: {item.calculation.remaining_prepaid_after_cutover ?? "غير مستحق"}</p>
            <button type="button" disabled={!item.calculation.eligible || item.source_stale} onClick={() => run(async () => { if (!section.evidence_file_id) throw new Error("opening_evidence_section_file_required"); const saved = item.selection || await transport.selectOnboardingPrepaid({ obligation_id: item.obligation_id, invoice_id: item.invoice_id, cutover_date: cutover, currency: form.currency, evidence: section.evidence_file_id }); await select(saved); })}>اختيار الالتزام وحفظ الرصيد</button>
        </li> : <li key={item.id}>{item.display_name || item.provider} · {item.amount ?? item.percentage} {item.currency} · {item.effective_from || item.cutover_date}<button type="button" onClick={() => run(() => select(item))}>اختيار العقد المحفوظ</button></li>)}</ul>
        {message && <p role="status">{message}</p>}
    </fieldset>;
}
