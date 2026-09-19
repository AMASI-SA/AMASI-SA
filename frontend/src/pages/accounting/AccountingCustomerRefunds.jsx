import { useEffect, useState } from "react";
import api from "../../lib/api";

const base = "/financial-provider-apps/accounting-module/customer-refunds";
const providers = { salla: "سلة Pay", tamara: "تمارا", tabby: "تابي", emkan: "إمكان" };
const states = { awaiting_entitlement_approval: "بانتظار اعتماد المستحق", due: "مستحق غير مدفوع", partially_paid: "مدفوع جزئيًا", paid: "تم السداد", conflict: "تعارض: احتمال دفع الاسترداد مرتين — يلزم مراجعة", awaiting_entitlement_and_approval: "تحويل مسجل ينتظر ربط المستحق والاعتماد", posted: "تحويل منفذ مثبت محاسبيًا" };
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
    const [bankReference, setBankReference] = useState("");
    const [paid, setPaid] = useState("");
    const [proof, setProof] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const can = p => accountingPermissions.includes("accounting.refunds." + p);
    async function load() { setData((await api.get(base, { params: { order_number: order } })).data); }
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
    const instant = date + "T00:00:00+03:00";
    async function download(row) {
        const response = await api.get(base + "/bank-payments/" + row.id + "/proof", { responseType: "blob" });
        const url = URL.createObjectURL(response.data); const link = document.createElement("a");
        link.href = url; link.download = row.proof_name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    return <section className="space-y-3 rounded-xl border p-4" dir="rtl">
        <h3 className="text-lg font-bold">استردادات العملاء والتحويلات البنكية</h3>
        <p>تعديل الطلب لا يثبت الدفع. إثبات المستحق يعكس صافي المرتجع وضريبته الأصلية مرة واحدة. تسجيل التحويل يسدد مستحق العميل فقط، ولا يخفض ذمة مزود الدفع.</p>
        <label>رقم الطلب<input aria-label="طلب استرداد العميل" value={order} onChange={e => setOrder(e.target.value)} /></label>
        <button disabled={busy} onClick={() => run(async () => {})}>بحث وتحديث الاستردادات</button>
        {error && <p role="alert">{error}</p>}
        {can("create") && <div className="grid gap-3 sm:grid-cols-2">
            <label>العملية الأصلية<select aria-label="أصل استرداد العميل" value={original} onChange={e => setOriginal(e.target.value)}><option value="">اختر العملية المثبتة</option>{data.originals.map(x => <option key={x.event_key} value={x.event_key}>{x.proposal.event.order_number} — {providers[x.proposal.event.provider]} — {x.proposal.tax.gross}</option>)}</select></label>
            <label>هوية عملية الاسترداد<input aria-label="هوية استرداد العميل" value={reference} onChange={e => setReference(e.target.value)} /></label>
            <p className="sm:col-span-2">هوية داخلية واضحة لكل عملية استرداد مستقلة؛ لا تستخدم رقم الطلب وحده ولا تقدّمها كمعرّف من المزود.</p>
            <label>تاريخ المستحق أو التحويل<input aria-label="تاريخ استرداد العميل" type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
            <label>المبلغ المستحق<input aria-label="مستحق استرداد العميل" type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} /></label>
            <label>سبب المستحق ودليله<input aria-label="سبب استرداد العميل" value={reason} onChange={e => setReason(e.target.value)} /></label>
            <button disabled={busy || !original || !reference || !amount || !reason} onClick={() => run(() => api.post(base, { original_key: original, case_reference: reference, amount, recognized_at: instant, reason }))}>حفظ مستحق للمراجعة دون قيد</button>
            <label>الحساب الذي نفذ التحويل<select aria-label="بنك تحويل الاسترداد" value={bank} onChange={e => setBank(e.target.value)}><option value="">اختر البنك</option>{data.banks.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
            <label>مبلغ الدفعة المنفذة<input aria-label="دفعة استرداد العميل" type="number" min="0.01" step="0.01" value={paid} onChange={e => setPaid(e.target.value)} /></label>
            <label>مرجع التحويل<input aria-label="مرجع تحويل الاسترداد" value={bankReference} onChange={e => setBankReference(e.target.value)} /></label>
            <label>إثبات التحويل<input aria-label="إثبات تحويل الاسترداد" type="file" onChange={e => readProof(e.target.files[0])} /></label>
            <button disabled={busy || !original || !reference || !bank || !paid || !bankReference || !proof} onClick={() => run(() => api.post(base + "/bank-payments", { original_key: original, case_reference: reference, bank_account_id: bank, amount: paid, paid_at: instant, bank_reference: bankReference, ...proof }))}>تسجيل تحويل منفذ كمسودة دون قيد</button>
        </div>}
        {data.cases.map(row => <article key={row.id} className="rounded border p-3">
            <strong>{row.order_number} — {row.case_reference}</strong><p>الدفع الأصلي: {providers[row.original_provider]} — {states[row.state]}</p>
            <p>المستحق {row.amount} — المدفوع {row.paid} — المتبقي {row.remaining}</p>
            <p>صافي المرتجع {(row.tax || row.tax_preview)?.net} — الضريبة {(row.tax || row.tax_preview)?.tax}</p>
            {row.conflict_reason && <p role="alert">استرداد مؤكد من المزود بعد التحويل البنكي. لا تُنفذ دفعة إضافية؛ راجع جهة التنفيذ وهوية الاسترداد.</p>}
            {row.due_txn_group_id && <p dir="ltr">{row.due_txn_group_id}</p>}
            {!row.recognized && can("post") && <button disabled={busy} onClick={() => run(() => api.post(base + "/" + row.id + "/approve"))}>اعتماد وإثبات مستحق العميل</button>}
        </article>)}
        {data.payments.map(row => <article key={row.id} className="rounded border p-3">
            <strong>{row.case_reference} — تنفيذ من البنك {row.bank_account_name}</strong><p>{row.amount} — {row.bank_reference} — {states[row.status]}</p>
            <button onClick={() => run(() => download(row))}>تنزيل إثبات التحويل {row.bank_reference}</button>
            {row.txn_group_id && <p dir="ltr">{row.txn_group_id}</p>}
            {row.status !== "posted" && can("pay") && <button disabled={busy} onClick={() => run(() => api.post(base + "/bank-payments/" + row.id + "/approve"))}>اعتماد تسجيل التحويل المنفذ</button>}
        </article>)}
    </section>;
}
