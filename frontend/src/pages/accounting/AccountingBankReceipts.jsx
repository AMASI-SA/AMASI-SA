import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const base = "/financial-provider-apps/accounting-module";
const labels = { salla: "سلة", tamara: "تمارا", tabby: "تابي", emkan: "إمكان" };
const states = { waiting_statement: "بانتظار الكشف", linked: "مرتبط بكشف — غير مرحّل", posted: "مرحّل ضمن قيد التسوية" };
const errorText = error => {
    const detail = error?.response?.data?.detail;
    return typeof detail === "string" ? detail : detail?.message || "تعذر حفظ العملية؛ أعد المحاولة بنفس البيانات للتحقق من النتيجة";
};

export default function AccountingBankReceipts({ accountingPermissions = [] }) {
    const [data, setData] = useState({ items: [], bindings: [] });
    const [provider, setProvider] = useState("tabby");
    const [amount, setAmount] = useState("");
    const [message, setMessage] = useState("");
    const [date, setDate] = useState(() => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date()));
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [saved, setSaved] = useState(null);
    const request = useRef(null);
    const canCreate = accountingPermissions.includes("accounting.receipts.create");
    const binding = data.bindings.find(item => item.provider === provider);
    async function refresh() {
        try { setData((await api.get(base + "/bank-receipts")).data); }
        catch (err) { setError(errorText(err)); }
    }
    useEffect(() => { refresh(); }, []);
    async function save(event) {
        event.preventDefault(); setError(""); setBusy(true);
        const facts = { provider, amount, bank_message: message, received_on: date };
        const fingerprint = JSON.stringify(facts);
        if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() };
        try {
            const result = (await api.post(base + "/bank-receipts", { ...facts, request_id: request.current.id })).data;
            setSaved(result); await refresh();
        } catch (err) { setError(errorText(err)); }
        finally { setBusy(false); }
    }
    return <section dir="rtl" className="space-y-4 rounded-2xl border bg-white p-5">
        <h2 className="text-xl font-bold">الحركات المالية اليومية</h2>
        <p>سجل الداخل والخارج من البنوك والصندوق. تسوية المزود تستخدم المسار أدناه؛ بقية أنواع الحركات لها مساراتها المحاسبية المستقلة.</p>
        <h3 className="font-bold">مبلغ واصل من منصة</h3>
        <p>يُحفظ المبلغ كمسودة تنتظر كشف المنصة. لا يتغير الرصيد إلا بعد الربط والمطابقة والاعتماد والترحيل.</p>
        {canCreate && <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
            <label>المنصة<select aria-label="منصة المبلغ الواصل" value={provider} onChange={e => setProvider(e.target.value)} className="block w-full rounded border p-2">
                {Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select></label>
            <div>البنك المرتبط تلقائيًا<p>{binding?.bank_account_name || "لم يُعتمد بنك للمنصة"}</p></div>
            <label>المبلغ الواصل (ريال)<input aria-label="المبلغ الواصل" required type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} className="block w-full rounded border p-2" /></label>
            <label>تاريخ الوصول<input aria-label="تاريخ الوصول" required type="date" value={date} onChange={e => setDate(e.target.value)} className="block w-full rounded border p-2" /></label>
            <label className="sm:col-span-2">رسالة البنك<textarea aria-label="رسالة البنك" required maxLength={4000} value={message} onChange={e => setMessage(e.target.value)} className="block w-full rounded border p-2" /></label>
            <button disabled={busy || !binding?.bank_account_id} className="rounded bg-emerald-800 p-3 text-white">{busy ? "جاري الحفظ…" : "حفظ مبلغ واصل كمسودة"}</button>
        </form>}
        {error && <p role="alert" className="text-rose-700">{error}</p>}
        {saved && <p role="status">حُفظ السجل <span dir="ltr">{saved.id}</span> — مرجع البنك: {saved.bank_reference || "لا يوجد مرجع صريح في الرسالة"}</p>}
        <button onClick={refresh} className="rounded border p-2">تحديث المبالغ الواصلة</button>
        <table className="w-full text-sm"><thead><tr><th>المنصة والبنك</th><th>التاريخ والمبلغ</th><th>مرجع البنك</th><th>الحالة</th></tr></thead>
            <tbody>{data.items.map(row => <tr key={row.id} className="border-t">
                <td>{labels[row.provider]} — {row.bank_account_name}</td><td>{row.received_on} — {row.amount} SAR</td>
                <td>{row.bank_reference || "غير متوفر"}<details><summary>رسالة البنك</summary>{row.bank_message}</details></td>
                <td>{states[row.status]}<div dir="ltr">{row.id}</div>{row.ledger_txn_group_id && <div dir="ltr">{row.ledger_txn_group_id}</div>}</td>
            </tr>)}</tbody></table>
    </section>;
}

export function SettlementReceiptLink({ draft, canLink, onLinked }) {
    const [items, setItems] = useState([]);
    const [selection, setSelection] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    async function refresh() {
        try { setItems((await api.get(base + "/bank-receipts")).data.items); }
        catch (err) { setError(errorText(err)); }
    }
    useEffect(() => { setSelection(""); refresh(); }, [draft.id]);
    const choices = items.filter(row => row.provider === draft.provider && row.bank_account_id === draft.bank_account_id
        && (!row.settlement_id || row.settlement_id === draft.id));
    const linked = items.find(row => row.id === draft.bank_receipt_id);
    const editable = ["draft", "needs_review", "rejected"].includes(draft.status);
    async function link() {
        setBusy(true); setError("");
        try {
            await api.put(base + "/settlements/drafts/" + encodeURIComponent(draft.id) + "/receipt", { receipt_id: selection });
            await refresh(); await onLinked();
        } catch (err) { setError(errorText(err)); }
        finally { setBusy(false); }
    }
    return <section className="space-y-2 rounded-xl border p-4">
        <h4 className="font-bold">ربط المبلغ الواصل بكشف المنصة</h4>
        {linked ? <p>مرجع البنك: {linked.bank_reference || "غير متوفر"} — المبلغ: {linked.amount} — مرجع الكشف: {draft.statement_reference}</p>
            : <p>بانتظار اختيار المبلغ المسجل في صفحة الحركات المالية اليومية. لا يُختار أي تطابق تلقائيًا.</p>}
        {!linked && editable && canLink && <>
            {choices.length > 1 && <p>يوجد أكثر من احتمال؛ راجع الرسالة والتاريخ واختر السجل المقصود صراحةً.</p>}
            <select aria-label="المبلغ الواصل المراد ربطه" value={selection} onChange={e => setSelection(e.target.value)} className="w-full rounded border p-2">
                <option value="">اختر المبلغ الواصل</option>
                {choices.map(row => <option key={row.id} value={row.id}>{row.received_on} — {row.amount} SAR — {row.bank_reference || row.bank_message} — فرق {(Number(row.amount) - Number(draft.amounts?.reported_net || 0)).toFixed(2)}</option>)}
            </select>
            <button disabled={!selection || busy} onClick={link} className="rounded border p-2">تأكيد ربط الطرفين دون ترحيل</button>
        </>}
        <button onClick={refresh} className="rounded border p-2">تحديث المبالغ المتاحة للربط</button>
        {error && <p role="alert" className="text-rose-700">{error}</p>}
    </section>;
}
