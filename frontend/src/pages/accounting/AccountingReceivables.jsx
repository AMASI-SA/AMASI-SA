import { useEffect, useState } from "react";
import api from "../../lib/api";

const BASE = "/financial-provider-apps/accounting-module";
const reasons = {
    refund_identity_required: "تحتاج مراجعة: معرف الاسترداد المالي الأصلي مفقود؛ رقم الطلب وحده لا يكفي",
    sales_tax_not_configured_for_date: "لم تُدخل نسبة ضريبة سارية في تاريخ الاعتراف",
    recognition_cutoff_not_configured: "لم يُحدد تاريخ قطع لهذا المسار",
    payment_evidence_missing: "دليل الدفع غير موجود",
    unique_source_order_required: "يلزم طلب مصدر واحد مطابق",
    canonical_provider_id_required: "معرف حركة المزود الأصلي مفقود",
    source_provenance_required: "مصدر الحركة غير موثق",
    existing_journal_requires_review: "يوجد إثبات سابق يحتاج مراجعة؛ لن يتكرر",
    previous_recognition_source_conflict: "بيانات المصدر تتعارض مع الإثبات السابق",
    original_recognition_required: "يلزم إثبات العملية الأصلية قبل الاسترداد",
    order_principal_conflict: "إجمالي الطلب لا يطابق مبلغ الدفع",
    fulfilment_not_proven: "تنفيذ الطلب غير مثبت",
    capture_not_confirmed: "تحصيل المبلغ غير مؤكد",
    invalid_event_date: "تاريخ الاعتراف أو دليل التحصيل والتنفيذ ناقص",
    preview_changed_review_again: "تغيرت البيانات؛ أعد المعاينة",
    previous_post_result_requires_recovery: "نتيجة تسجيل سابقة غير محسومة؛ يلزم فحصها",
    pre_cutover_recognition: "العملية تسبق تاريخ القطع",
};
const explain = value => reasons[value] || value;
const errorText = error => explain(error?.response?.data?.detail?.code
    || error?.response?.data?.detail?.message || "تعذر تنفيذ الطلب");

export default function AccountingReceivables({ accountingPermissions = [] }) {
    const [policy, setPolicy] = useState(null);
    const [rate, setRate] = useState("");
    const [effective, setEffective] = useState("");
    const [reason, setReason] = useState("");
    const [provider, setProvider] = useState("tamara");
    const [reference, setReference] = useState("");
    const [rows, setRows] = useState([]);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const manage = accountingPermissions.includes("accounting.rules.manage");
    const post = accountingPermissions.includes("accounting.receivables.post");
    useEffect(() => {
        let active = true;
        api.get(BASE + "/sales-tax").then(({ data }) => { if (active) setPolicy(data); })
            .catch(error => { if (active) setMessage(errorText(error)); });
        return () => { active = false; };
    }, []);
    async function save(event) {
        event.preventDefault(); setBusy(true); setMessage("");
        try {
            const { data } = await api.put(BASE + "/sales-tax", {
                rate, effective_at: new Date(effective).toISOString(),
                revision: policy?.revision ?? 0, reason,
            });
            setPolicy(data); setRows([]); setMessage("حُفظت النسبة وتاريخ سريانها وسجل التعديل. القيود المرحلة ثابتة.");
        } catch (error) { setMessage(errorText(error)); }
        finally { setBusy(false); }
    }
    async function preview() {
        setBusy(true); setMessage(""); setRows([]);
        try {
            const { data } = await api.get(BASE + "/receivables/sources", {
                params: { provider, order_number: reference },
            });
            const requests = data.payments.flatMap(payment => [
                { provider, payment_id: payment.provider_id, label: "بيع", reference: payment.order_reference_id },
                ...(payment.refunds || []).map(refund => ({
                    provider, payment_id: payment.provider_id, refund_id: refund.provider_refund_id,
                    label: "استرداد", reference: payment.order_reference_id,
                })),
            ]);
            const result = [];
            for (const item of requests) {
                if (item.label === "استرداد" && !item.refund_id) {
                    result.push({ ...item, result: { state: "rejected", reasons: ["refund_identity_required"] } });
                    continue;
                }
                if (!item.payment_id) {
                    result.push({ ...item, result: { state: "rejected", reasons: ["canonical_provider_id_required"] } });
                    continue;
                }
                const payload = { provider: item.provider, payment_id: item.payment_id, refund_id: item.refund_id || null };
                const { data: proposal } = await api.post(BASE + "/receivables/preview", payload);
                result.push({ ...item, payload, result: proposal });
            }
            setRows(result);
            if (!result.length) setMessage("لا توجد حركات مصدر مطابقة. لا ينشئ البحث معرفات أو حركات دفع.");
        } catch (error) { setMessage(errorText(error)); }
        finally { setBusy(false); }
    }
    async function execute(row, index) {
        setBusy(true); setMessage("");
        try {
            const { data } = await api.post(BASE + "/receivables/execute", {
                ...row.payload, preview_hash: row.result.preview_hash,
            });
            setRows(current => current.map((item, i) => i === index ? { ...item, result: data } : item));
            setMessage("حُفظ الإثبات. أعد المعاينة قبل الإجراء التالي للتحقق من الأرصدة والاستردادات.");
        } catch (error) { setMessage(errorText(error)); }
        finally { setBusy(false); }
    }
    return <details className="rounded-2xl border border-slate-200 bg-white p-4" dir="rtl">
        <summary className="cursor-pointer font-bold">ضريبة المبيعات وإثبات ذمم الطلبات</summary>
        <p className="my-3 text-sm text-slate-600">
            إجمالي الطلب شامل الضريبة. تُستخدم نسبة ميزان اليدوية السارية عند الاعتراف؛ ضريبة سلة للمراجعة فقط.
            ضريبة عمولات المزود ورسوم التسوية مستقلة.
        </p>
        {message && <p role="status" className="my-3 rounded bg-amber-50 p-3">{message}</p>}
        {policy && !policy.versions.length && <p>لم تُضبط نسبة ضريبة المبيعات؛ لا تُفترض نسبة تلقائية.</p>}
        {manage && <form onSubmit={save} className="grid gap-3 md:grid-cols-4">
            <label>النسبة اليدوية %<input required type="number" min="0" max="100" step="0.0001"
                aria-label="النسبة اليدوية" value={rate} onChange={e => setRate(e.target.value)}
                className="block w-full rounded border p-2" dir="ltr" /></label>
            <label>تاريخ ووقت السريان المحلي<input required type="datetime-local"
                aria-label="تاريخ السريان" value={effective} onChange={e => setEffective(e.target.value)}
                className="block w-full rounded border p-2" dir="ltr" /></label>
            <label>سبب التعديل<input required maxLength={500} aria-label="سبب التعديل"
                value={reason} onChange={e => setReason(e.target.value)} className="block w-full rounded border p-2" /></label>
            <button disabled={busy || !policy} className="rounded bg-slate-900 p-2 text-white">حفظ إعداد الضريبة</button>
        </form>}
        {!!policy?.versions.length && <details className="my-3">
            <summary>سجل نسب الضريبة وتعديلاتها ({policy.versions.length})</summary>
            <ul>{policy.audit.map(item => <li key={item.id}>
                <span dir="ltr">{item.version.rate}% — {item.version.effective_at}</span>
                {" · "}{item.reason}{" · "}{item.actor_id}
            </li>)}</ul>
        </details>}
        <div className="my-4 flex flex-wrap gap-3">
            <label>المزود<select aria-label="مزود إثبات الذمم" value={provider}
                onChange={e => { setProvider(e.target.value); setRows([]); }} className="mx-2 rounded border p-2">
                <option value="salla">سلة Pay</option><option value="tamara">تمارا</option><option value="tabby">تابي</option><option value="emkan">إمكان</option>
            </select></label>
            <label>مرجع الطلب<input aria-label="مرجع طلب الإثبات" value={reference}
                onChange={e => { setReference(e.target.value); setRows([]); }} className="mx-2 rounded border p-2" /></label>
            <button disabled={busy} onClick={preview} className="rounded border px-4 py-2">معاينة المؤهل والمرفوض</button>
        </div>
        <div className="overflow-auto"><table className="w-full text-sm">
            <thead><tr>{["الطلب / الحركة", "الإجمالي", "الصافي", "الضريبة / النسبة", "الحالة والدليل", "الإجراء"].map(h => <th key={h} className="p-2 text-right">{h}</th>)}</tr></thead>
            <tbody>{rows.map((row, index) => <tr key={[row.payment_id, row.refund_id, index].join(":")} className="border-t">
                <td className="p-2">{row.reference} — {row.label}<div dir="ltr">{row.refund_id || row.payment_id || "—"}</div></td>
                <td dir="ltr">{row.result.tax?.gross || "—"}</td><td dir="ltr">{row.result.tax?.net || "—"}</td>
                <td dir="ltr">{row.result.tax ? row.result.tax.tax + " / " + row.result.tax.rate + "%" : "—"}</td>
                <td className="p-2">{row.result.state === "eligible" ? "مؤهل للإثبات" :
                    ["posted", "already_posted"].includes(row.result.state) ? "مثبت — لن يتكرر" :
                    (row.result.reasons || []).map(explain).join(" · ")}
                    {row.result.event && <details><summary>تفصيل الحساب ومصدره</summary>
                        <dl className="space-y-1 py-2">
                            <dt>تاريخ الاعتراف</dt><dd dir="ltr">{row.result.event.recognized_at}</dd>
                            <dt>تاريخ القطع المطبق</dt><dd dir="ltr">{row.result.event.cutover_at}</dd>
                            <dt>مصدر الدفع</dt><dd>{row.result.event.source.payment_source}</dd>
                            <dt>مرجع مستند الدفع</dt><dd dir="ltr">{row.result.event.source.payment_document_id}</dd>
                            <dt>نسخة إعداد الضريبة المحفوظة</dt><dd dir="ltr">{row.result.tax.revision}</dd>
                            <dt>ضريبة المصدر للمراجعة فقط</dt><dd dir="ltr">
                                {Object.entries(row.result.tax.source_tax_for_review || {}).map(([key, value]) => key + ": " + value).join(" · ") || "غير متوفرة"}
                            </dd>
                        </dl>
                    </details>}
                    {row.result.txn_group_id && <div dir="ltr">{row.result.txn_group_id}</div>}
                </td>
                <td>{post && row.result.state === "eligible" && <button disabled={busy} onClick={() => execute(row, index)}
                    className="rounded bg-emerald-700 px-3 py-2 text-white">إثبات الحركة المعاينة</button>}</td>
            </tr>)}</tbody>
        </table></div>
    </details>;
}
