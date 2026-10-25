import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const ROOT = "/integrations-v2/tiktok_ads/child-management/proposals";
const ACTIONS = [["pause", "إيقاف"], ["enable", "تشغيل"], ["rename", "تعديل الاسم"], ["set_budget", "تعديل الميزانية"], ["set_conversion_bid", "تعديل عرض التحويل"]];
const STATUS = { previewed: "راجع المعاينة قبل الاعتماد", submitted: "أُرسل الطلب؛ تحقق من النتيجة", uncertain: "نتيجة غير مؤكدة؛ لا تكرر الإرسال", completed: "اكتمل التعديل وتحققت المطابقة" };
const FIELDS = { operation_status: "الحالة", adgroup_name: "اسم المجموعة", ad_name: "اسم الإعلان", budget: "الميزانية", conversion_bid_price: "عرض التحويل" };
const BUTTON = "rounded-xl border bg-white px-4 py-3 text-sm font-bold disabled:opacity-40";
const nonce = () => crypto.randomUUID().replaceAll("-", "");
const display = (value) => value === "ENABLE" ? "مفعّل" : value === "DISABLE" ? "موقوف" : String(value ?? "—");

export default function TikTokChildControls({ selection, onClose }) {
    const [action, setAction] = useState("pause"), [name, setName] = useState(selection.name || ""), [amount, setAmount] = useState(""), [ceiling, setCeiling] = useState(""), [reason, setReason] = useState("");
    const [proposal, setProposal] = useState(null), [approved, setApproved] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(""), [history, setHistory] = useState([]);
    const mounted = useRef(true), inflight = useRef(false), controller = useRef(null), key = useRef(nonce()), submitted = useRef(new Set());
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
    const financial = ["enable", "set_budget", "set_conversion_bid"].includes(action);
    const pending = proposal && (["submitted", "uncertain"].includes(proposal.status) || submitted.current.has(proposal.proposal_id));
    const displayedKind = proposal?.kind || selection.kind;
    const displayedName = proposal ? proposal.before?.ad_name || proposal.before?.adgroup_name || proposal.entity_id : selection.name;
    function changed(setter, value) { setter(value); setApproved(false); key.current = nonce(); }
    async function request(work, receive) {
        if (inflight.current) return;
        inflight.current = true; setBusy(true); setError(""); controller.current = new AbortController();
        try { const response = await work({ signal: controller.current.signal, timeout: 60000 }); if (mounted.current) receive(response.data); }
        catch (failure) { if (mounted.current) setError(failure?.response?.data?.detail?.message || "لم تثبت نتيجة العملية؛ اقرأ حالتها قبل المتابعة."); }
        finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    function receive(data) { setProposal((old) => ({ ...old, ...data })); setApproved(false); }
    function prepare(event) {
        event.preventDefault();
        const value = { kind: selection.kind, action, account_id: selection.accountId, campaign_id: selection.campaignId, adgroup_id: selection.adgroupId, reason: reason.trim(), idempotency_key: key.current };
        if (selection.kind === "ad") value.ad_id = selection.adId;
        if (action === "rename") value.name = name.trim();
        if (["set_budget", "set_conversion_bid"].includes(action)) value.amount_native = Number(amount);
        if (financial) value.spend_ceiling_native = Number(ceiling);
        request((options) => api.post(ROOT, value, options), (data) => { setProposal(data); setApproved(false); });
    }
    function execute() {
        if (!proposal || !approved || pending || inflight.current) return;
        submitted.current.add(proposal.proposal_id); setApproved(false);
        request((options) => api.post(`${ROOT}/${proposal.proposal_id}/approve-and-execute`, { confirmation_digest: proposal.confirmation_digest }, options), receive);
    }
    return <section data-testid="tiktok-child-controls" className="space-y-4 rounded-2xl border border-emerald-200 bg-white p-5" dir="rtl">
        <div className="flex justify-between gap-3"><h3 className="font-black">إدارة {displayedKind === "adgroup" ? "مجموعة" : "إعلان"} Smart+</h3><button type="button" className={BUTTON} disabled={busy} onClick={onClose}>إغلاق إدارة العنصر</button></div>
        <p className="text-sm">{proposal?.account_id || selection.accountName || selection.accountId} · {displayedName} · {proposal?.entity_id || selection.adId || selection.adgroupId}</p>
        {error && <p role="alert" className="text-sm text-rose-800">{error}</p>}
        {!proposal && <form onSubmit={prepare}><fieldset disabled={busy} className="grid gap-3 md:grid-cols-2">
            <label className="text-sm font-bold">العملية<select aria-label="عملية إدارة عنصر TikTok" value={action} onChange={(event) => changed(setAction, event.target.value)} className="w-full rounded-xl border p-3">{ACTIONS.filter(([id]) => selection.kind === "adgroup" || !["set_budget", "set_conversion_bid"].includes(id)).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            {action === "rename" && <label className="text-sm font-bold">الاسم<input aria-label="اسم عنصر TikTok" required maxLength={100} value={name} onChange={(event) => changed(setName, event.target.value)} className="w-full rounded-xl border p-3" /></label>}
            {["set_budget", "set_conversion_bid"].includes(action) && <label className="text-sm font-bold">{action === "set_budget" ? "الميزانية" : "عرض التحويل"} · عملة الحساب<input aria-label="مبلغ عنصر TikTok" required type="number" min="0.01" max="10000000" step="0.01" value={amount} onChange={(event) => changed(setAmount, event.target.value)} className="w-full rounded-xl border p-3" /></label>}
            {financial && <label className="text-sm font-bold">سقف الإنفاق الموافق عليه · عملة الحساب<input aria-label="سقف إنفاق عنصر TikTok" required type="number" min="0.01" max="10000000" step="0.01" value={ceiling} onChange={(event) => changed(setCeiling, event.target.value)} className="w-full rounded-xl border p-3" /></label>}
            <label className="text-sm font-bold">سبب العملية<input aria-label="سبب إدارة عنصر TikTok" required minLength={5} maxLength={400} value={reason} onChange={(event) => changed(setReason, event.target.value)} className="w-full rounded-xl border p-3" /></label>
            <button type="submit" className={BUTTON}>معاينة تعديل العنصر</button>
        </fieldset></form>}
        {proposal && <div className="space-y-3"><p role="status" className="font-bold">{STATUS[proposal.status] || proposal.status}</p><p className="text-sm">الحساب {proposal.account_id} · العنصر {proposal.entity_id} · عملة التنفيذ {proposal.currency}</p>
            <table className="w-full text-right text-sm"><thead><tr><th>الإعداد</th><th>قبل</th><th>المطلوب</th></tr></thead><tbody>{Object.entries(proposal.changes || {}).map(([field, value]) => <tr key={field}><td>{FIELDS[field] || field}</td><td>{display(proposal.before?.[field])}</td><td>{display(value)}</td></tr>)}</tbody></table>
            <p className="text-sm">{proposal.reason}</p>{proposal.financial_bound && <p className="rounded-xl bg-amber-50 p-3 text-sm">الحد المثبت {proposal.financial_bound.maximum_native} {proposal.currency} {proposal.financial_bound.period === "lifetime" ? "لعمر الحملة" : "في اليوم"} · السقف الموافق عليه {proposal.financial_bound.approved_ceiling_native} {proposal.currency}.</p>}
            {proposal.status === "previewed" && !pending && <><label className="flex gap-2 text-sm"><input type="checkbox" checked={approved} disabled={busy} onChange={(event) => setApproved(event.target.checked)} />أوافق على تعديل العنصر والإعدادات المعروضة لهذه العملية فقط</label><button type="button" className={BUTTON} disabled={!approved || busy} onClick={execute}>اعتماد تعديل العنصر</button></>}
            {proposal.status === "completed" && proposal.verified && <p className="text-sm text-emerald-800">أكد تيك توك التعديل. حدّث مزامنة التقارير لإظهار أحدث بيانات العنصر.</p>}
            <button type="button" className={BUTTON} disabled={busy} onClick={() => request((options) => api.get(`${ROOT}/${proposal.proposal_id}`, options), receive)}>تحديث حالة تعديل العنصر</button>
            {["submitted", "uncertain"].includes(proposal.status) && <button type="button" className={BUTTON} disabled={busy} onClick={() => request((options) => api.post(`${ROOT}/${proposal.proposal_id}/reconcile`, {}, options), receive)}>التحقق من نتيجة تعديل العنصر</button>}
        </div>}
        <button type="button" className={BUTTON} disabled={busy} onClick={() => request((options) => api.get(ROOT, options), (data) => setHistory(data.items || []))}>سجل تعديل المجموعات والإعلانات</button>
        {history.map((row) => <button type="button" key={row.proposal_id} className={`${BUTTON} block w-full text-right`} disabled={busy} onClick={() => request((options) => api.get(`${ROOT}/${row.proposal_id}`, options), (data) => { setProposal(data); setApproved(false); })}>{row.entity_id} · {STATUS[row.status] || row.status}</button>)}
    </section>;
}
