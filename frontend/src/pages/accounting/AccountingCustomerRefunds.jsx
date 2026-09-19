import { useEffect, useState } from "react";
import api from "../../lib/api";

const base = "/financial-provider-apps/accounting-module/customer-refunds";
const providers = { salla: "سلة Pay", tamara: "تمارا", tabby: "تابي", emkan: "إمكان" };
const states = { awaiting_execution_confirmation: "بانتظار تأكيد التنفيذ", due: "مستحق غير مدفوع", partially_paid: "منفذ جزئيًا", paid: "تم التنفيذ كاملًا", conflict: "تعارض: احتمال دفع الاسترداد مرتين — يلزم مراجعة", awaiting_entitlement_and_approval: "حركة مسجلة تنتظر ربط المسودة والاعتماد", posted: "حركة استرداد معتمدة" };
const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date());

export default function AccountingCustomerRefunds({ accountingPermissions = [] }) {
    const [data, setData] = useState({ originals: [], banks: [], cases: [], payments: [] });
    const [order, setOrder] = useState("");
    const [original, setOriginal] = useState("");
    const [reference, setReference] = useState("");
    const [amount, setAmount] = useState("");
    const [reason, setReason] = useState("");
    const [date, setDate] = useState(today);
    const [bank, setBank] = useState("");
    const [channel, setChannel] = useState("bank");
    const [providerRefund, setProviderRefund] = useState("");
    const [bankReference, setBankReference] = useState("");
    const [paid, setPaid] = useState("");
    const [proof, setProof] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const can = p => accountingPermissions.includes("accounting.refunds." + p);
    async function load() { setData({ originals: [], banks: [], cases: [], payments: [], ...(await api.get(base, { params: { order_number: order } })).data }); }
    async function run(work) {
        setError(""); setBusy(true);
        try { await work(); await load(); }
        catch (e) { const d = e.response?.data?.detail; setError(typeof d === "string" ? d : "تعذر إكمال العملية؛ تحقق من نتيجتها قبل المحاولة مجددًا"); }
        finally { setBusy(false); }
    }
    useEffect(() => { run(async () => {}); }, []);
    async function readProof(file) {
        if (!file || file.size > 1024 * 1024) { setProof(null); setError("أرفق إثباتًا لا يتجاوز 1 ميجابايت"); return; }
        const bytes = await file.arrayBuffer();
        let binary = ""; for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
        setProof({ proof_name: file.name, proof_base64: btoa(binary) });
    }
    const instant = date + "T00:00:00Z";
    async function download(row) {
        const response = await api.get(base + "/bank-payments/" + row.id + "/proof", { responseType: "blob" });
        const url = URL.createObjectURL(response.data); const link = document.createElement("a");
        link.href = url; link.download = row.proof_name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    return <section className="space-y-3 rounded-xl border p-4" dir="rtl">
        <h3 className="text-lg font-bold">استردادات العملاء والتحويلات البنكية</h3>
        <p>تعديل الطلب لا يثبت الدفع. الويبهوك ينشئ مسودة فقط. اعتماد الحركة اليومية يثبت المبلغ المنفذ بصافي المرتجع وضريبته الأصلية. التحويل البنكي لا يخفض ذمة مزود الدفع.</p>
        <label>رقم الطلب<input aria-label="طلب استرداد العميل" value={order} onChange={e => { setOrder(e.target.value); setOriginal(""); setReference(""); setAmount(""); setPaid(""); setBankReference(""); setProviderRefund(""); setProof(null); }} /></label>
        <button disabled={busy} onClick={() => run(async () => {})}>بحث وتحديث الاستردادات</button>
        {error && <p role="alert">{error}</p>}
        {can("create") && <label>مسودة الاسترداد<select aria-label="اختيار مسودة الاسترداد" value={reference} onChange={e => { const row = data.cases.find(x => x.case_reference === e.target.value); if (row) { setReference(row.case_reference); setOriginal(row.original_key); setAmount(row.amount); setPaid(row.remaining); } }}><option value="">اختر المسودة أو أدخل استردادًا جديدًا</option>{data.cases.map(row => <option key={row.id} value={row.case_reference}>{row.order_number} — المطلوب {row.amount} — المتبقي {row.remaining}</option>)}</select></label>}
        {can("create") && <div className="grid gap-3 sm:grid-cols-2">
            <label>العملية الأصلية<select aria-label="أصل استرداد العميل" value={original} onChange={e => setOriginal(e.target.value)}><option value="">اختر العملية المثبتة</option>{data.originals.map(x => <option key={x.event_key} value={x.event_key}>{x.proposal.event.order_number} — {providers[x.proposal.event.provider]} — {x.proposal.tax.gross}</option>)}</select></label>
            <label>هوية عملية الاسترداد<input aria-label="هوية استرداد العميل" value={reference} onChange={e => setReference(e.target.value)} /></label>
            <p className="sm:col-span-2">هوية داخلية واضحة لكل عملية استرداد مستقلة؛ لا تستخدم رقم الطلب وحده ولا تقدّمها كمعرّف من المزود.</p>
            <label>تاريخ المستحق أو التحويل<input aria-label="تاريخ استرداد العميل" type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
            <label>المبلغ المستحق<input aria-label="مستحق استرداد العميل" type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} /></label>
            <label>سبب المستحق ودليله<input aria-label="سبب استرداد العميل" value={reason} onChange={e => setReason(e.target.value)} /></label>
            <button disabled={busy || !original || !reference || !amount || !reason} onClick={() => run(() => api.post(base, { original_key: original, case_reference: reference, amount, recognized_at: instant, reason }))}>حفظ مستحق للمراجعة دون قيد</button>
            <label>جهة التنفيذ الفعلية<select aria-label="جهة تنفيذ الاسترداد" value={channel} onChange={e => setChannel(e.target.value)}><option value="bank">حساب بنكي</option>{Object.entries(providers).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>
            {channel !== "bank" && <label>معرف استرداد المزود إن توفر<input aria-label="هوية استرداد المزود" value={providerRefund} onChange={e => setProviderRefund(e.target.value)} /></label>}
            {channel === "bank" && <label>الحساب الذي نفذ التحويل<select aria-label="بنك تحويل الاسترداد" value={bank} onChange={e => setBank(e.target.value)}><option value="">اختر البنك</option>{data.banks.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
            <label>مبلغ الدفعة المنفذة<input aria-label="دفعة استرداد العميل" type="number" min="0.01" step="0.01" value={paid} onChange={e => setPaid(e.target.value)} /></label>
            <label>مرجع التحويل<input aria-label="مرجع تحويل الاسترداد" value={bankReference} onChange={e => setBankReference(e.target.value)} /></label>
            <label>إثبات التحويل<input aria-label="إثبات تحويل الاسترداد" type="file" onChange={e => readProof(e.target.files[0])} /></label>
            <button disabled={busy || !original || !reference || (channel === "bank" && !bank) || !paid || (!bankReference && !proof)} onClick={() => run(() => api.post(base + "/bank-payments", { original_key: original, case_reference: reference, bank_account_id: channel === "bank" ? bank : "", execution_channel: channel, provider_refund_id: providerRefund || null, amount: paid, paid_at: instant, bank_reference: bankReference, ...proof }))}>حفظ حركة الاسترداد كمسودة دون قيد</button>
        </div>}
        {data.cases.map(row => <article key={row.id} className="rounded border p-3">
            <strong>{row.order_number} — {row.case_reference}</strong><p>الدفع الأصلي: {providers[row.original_provider]} — {states[row.state]}</p>
            <p>المستحق {row.amount} — المدفوع {row.paid} — المتبقي {row.remaining}</p>
            <p>صافي المرتجع {(row.tax || row.tax_preview)?.net} — الضريبة {(row.tax || row.tax_preview)?.tax}</p>
            {row.conflict_reason && <p role="alert">استرداد مؤكد من المزود بعد التحويل البنكي. لا تُنفذ دفعة إضافية؛ راجع جهة التنفيذ وهوية الاسترداد.</p>}
            {row.due_txn_group_id && <p dir="ltr">{row.due_txn_group_id}</p>}
        </article>)}
        {data.payments.map(row => <article key={row.id} className="rounded border p-3">
            <strong>{row.case_reference} — جهة التنفيذ {row.execution_channel === "bank" ? row.bank_account_name : providers[row.execution_channel]}</strong><p>{row.amount} — {row.bank_reference} — {states[row.status]}</p>
            {row.proof_name && <button onClick={() => run(() => download(row))}>تنزيل إثبات التنفيذ {row.bank_reference}</button>}
            {row.txn_group_id && <p dir="ltr">{row.txn_group_id}</p>}
            {row.status !== "posted" && can("pay") && <button disabled={busy} onClick={() => run(() => api.post(base + "/bank-payments/" + row.id + "/approve"))}>اعتماد حركة الاسترداد المنفذة</button>}
        </article>)}
    </section>;
}
