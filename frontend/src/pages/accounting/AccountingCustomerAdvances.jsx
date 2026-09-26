import { useEffect, useState } from "react";
import api from "../../lib/api";

const base = "/financial-provider-apps/accounting-module/customer-advances";
const providers = ["salla", "tamara", "tabby", "emkan"];

function AdvanceActions({ row, run, busy, banks = [] }) {
    const [at, setAt] = useState("");
    const [evidence, setEvidence] = useState("");
    const [amount, setAmount] = useState("");
    const [channel, setChannel] = useState(row.provider);
    const [bank, setBank] = useState("");
    const [refund, setRefund] = useState("");
    const [proof, setProof] = useState(null);
    const [proofError, setProofError] = useState("");
    const differentProvider = channel !== "bank" && channel !== row.provider;
    async function readProof(file) {
        setProof(null); setProofError("");
        if (!file) return;
        if (file.size > 1024 * 1024) { setProofError("الحد الأقصى للمستند 1 ميغابايت"); return; }
        try {
            const content = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
            setProof({ proof_name: file.name, proof_base64: String(content).split(",")[1] });
        } catch { setProofError("تعذر قراءة المستند"); }
    }
    return <div className="space-y-2">
        <label>تاريخ الحدث المحاسبي مع المنطقة الزمنية<input aria-label={`تاريخ التحصيل المقدم ${row.order_number}`} value={at} onChange={e => setAt(e.target.value)} placeholder="YYYY-MM-DDTHH:mm:ss+03:00" /></label>
        <label>مرجع مستند الإلغاء أو التنفيذ<input aria-label={`دليل التحصيل المقدم ${row.order_number}`} value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
        {!row.cancellation ? <button disabled={busy || !at || !evidence} onClick={() => run(() => api.post(`${base}/${row.id}/cancel`, { accounting_at: at, evidence_ref: evidence }))}>اعتماد إلغاء التحصيل المقدم وإثبات التزام الرد</button> : row.state !== "paid" && <>
            <label>مبلغ الرد المنفذ<input aria-label={`مبلغ رد المقدم ${row.order_number}`} value={amount} onChange={e => setAmount(e.target.value)} type="number" min="0.01" step="0.01" /></label>
            <label>جهة التنفيذ<select aria-label={`جهة رد المقدم ${row.order_number}`} value={channel} onChange={e => { setChannel(e.target.value); setProof(null); setProofError(""); }}>{providers.map(p => <option key={p} value={p}>{p}</option>)}<option value="bank">البنك</option></select></label>
            {channel === "bank" ? <label>البنك<select aria-label={`بنك رد المقدم ${row.order_number}`} value={bank} onChange={e => setBank(e.target.value)}><option value="">اختر البنك</option>{banks.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select></label> : <label>معرّف الاسترداد المؤكد لدى المزود<input aria-label={`استرداد المزود للمقدم ${row.order_number}`} value={refund} onChange={e => setRefund(e.target.value)} /></label>}
            {differentProvider && <div><p>قبل الاعتماد تحقّق أن مستند التنفيذ يربط العميل والطلب ومبلغ الرد وتاريخه ومعرّف الاسترداد بالمزود المنفذ المختلف.</p><label>مستند التنفيذ الموثق<input type="file" aria-label={`مستند رد المقدم ${row.order_number}`} onChange={e => readProof(e.target.files?.[0])} /></label>{proofError && <p role="alert">{proofError}</p>}</div>}
            <button disabled={busy || !at || !evidence || !amount || (channel === "bank" ? !bank : !refund) || (differentProvider && !proof)} onClick={() => run(() => api.post(`${base}/${row.id}/payments`, { amount, paid_at: at, execution_channel: channel, execution_reference: evidence, bank_account_id: channel === "bank" ? bank : "", provider_refund_id: channel === "bank" ? "" : refund, ...(differentProvider ? proof : {}) }))}>اعتماد رد التحصيل المقدم المنفذ</button>
        </>}
    </div>;
}

export default function AccountingCustomerAdvances({ accountingPermissions = [] }) {
    const [data, setData] = useState({ items: [], payments: [] });
    const [provider, setProvider] = useState("tamara");
    const [payment, setPayment] = useState("");
    const [evidence, setEvidence] = useState("");
    const [tax, setTax] = useState("");
    const [taxReview, setTaxReview] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    async function load() { setData((await api.get(base)).data); }
    async function run(work) {
        setBusy(true); setError("");
        try { await work(); await load(); }
        catch (e) { const detail = e.response?.data?.detail; setError(typeof detail === "string" ? detail : "تعذر الإجراء؛ راجع الدليل والصلاحية والفترة المحاسبية ثم حدّث الحالة."); }
        finally { setBusy(false); }
    }
    useEffect(() => { load().catch(() => setError("تعذر تحميل التحصيل المقدم")); }, []);
    const can = suffix => accountingPermissions.includes(`accounting.advances.${suffix}`);
    return <section aria-label="إلغاء التحصيل المقدم قبل الإيراد" className="rounded border p-4 space-y-3">
        <h3>التحصيل المقدم وإلغاء الطلب قبل إثبات الإيراد</h3>
        <p>يُستخدم فقط لتحصيل موثق لم يُثبت سابقًا، مع مراجعة تؤكد عدم إثبات ضريبة عليه. الحالات الضريبية معلقة لمراجعة المحاسب. إثبات بيع سابق أو تحصيل سابق يحتاج تسوية منفصلة.</p>
        <button disabled={busy} onClick={() => run(async () => {})}>تحديث التحصيل المقدم</button>
        {error && <p role="alert">{error}</p>}
        {can("recognize") && <div className="space-y-2">
            <label>مزود التحصيل<select aria-label="مزود التحصيل المقدم" value={provider} onChange={e => setProvider(e.target.value)}>{providers.map(p => <option key={p}>{p}</option>)}</select></label>
            <label>معرّف التحصيل لدى المزود<input aria-label="معرف التحصيل المقدم" value={payment} onChange={e => setPayment(e.target.value)} /></label>
            <label>مستند التحصيل<input aria-label="دليل التحصيل المقدم الأصلي" value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
            <label>الضريبة المثبتة أصلًا وفق المراجعة<input aria-label="الضريبة المثبتة للتحصيل المقدم" value={tax} onChange={e => setTax(e.target.value)} type="number" min="0" step="0.01" /></label>
            <label>مرجع مراجعة الضريبة الأصلية<input aria-label="مراجعة ضريبة التحصيل المقدم" value={taxReview} onChange={e => setTaxReview(e.target.value)} /></label>
            <button disabled={busy || !payment || !evidence || tax === "" || !taxReview} onClick={() => run(() => api.post(base, { provider, payment_id: payment, evidence_ref: evidence, original_tax_amount: tax, tax_review_ref: taxReview }))}>إثبات التحصيل المقدم الموثق</button>
        </div>}
        {data.items.map(row => <article key={row.id} className="rounded border p-3 space-y-2">
            <strong>{row.order_number} — {row.provider}</strong>
            <p>التحصيل {row.amount} — المدفوع {row.paid} — المتبقي {row.remaining} — {row.state}</p>
            <p>قيد التحصيل: <span dir="ltr">{row.capture_txn_group_id}</span></p>
            {row.due_txn_group_id && <p>قيد التزام الرد: <span dir="ltr">{row.due_txn_group_id}</span></p>}
            {can("refund") && <AdvanceActions row={row} run={run} busy={busy} banks={data.banks} />}
            {data.payments.filter(p => p.advance_id === row.id).map(p => <p key={p.id}>رد منفذ {p.amount} بتاريخ {p.paid_at} — <span dir="ltr">{p.txn_group_id}</span></p>)}
        </article>)}
    </section>;
}
