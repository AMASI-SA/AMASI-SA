import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const ROOT = "/integrations-v2/tiktok_ads/management/proposals";
const ACTIONS = [["rename", "تعديل الاسم"], ["pause", "إيقاف الحملة"], ["set_budget", "تعديل الميزانية"], ["enable", "تشغيل الحملة"]];
const STATUS = { previewed: "معاينة محفوظة", executing: "قيد التنفيذ", submitted: "أُرسل إلى TikTok", verifying: "قيد التحقق", uncertain: "نتيجة غير مؤكدة", completed: "مكتمل ومثبت" };
function key() {
    return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `tiktok_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}
function failureText(error) {
    return error?.response?.data?.detail?.message || "تعذر تأكيد الاستجابة. حدّث حالة الاقتراح قبل متابعة التنفيذ.";
}

export default function TikTokCampaignControls({ selection, onClose, onCompleted }) {
    const create = selection.mode === "create";
    const [action, setAction] = useState(create ? "create" : "rename");
    const [name, setName] = useState(create ? "" : selection.name || "");
    const [budget, setBudget] = useState("");
    const [ceiling, setCeiling] = useState("");
    const [budgetMode, setBudgetMode] = useState("BUDGET_MODE_DAY");
    const [objective, setObjective] = useState("WEB_CONVERSIONS");
    const [reason, setReason] = useState("");
    const [proposal, setProposal] = useState(null);
    const [approved, setApproved] = useState(false);
    const [executionStarted, setExecutionStarted] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [history, setHistory] = useState(null);
    const mounted = useRef(true), inflight = useRef(false), nonce = useRef(key()), controller = useRef(null);
    const callback = useRef(onCompleted);
    useEffect(() => { callback.current = onCompleted; }, [onCompleted]);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);

    function changed(setter, value) {
        setter(value); setProposal(null); setApproved(false); setExecutionStarted(false); setError(""); nonce.current = key();
    }
    async function request(work, receive) {
        if (inflight.current) return;
        inflight.current = true; setBusy(true); setError("");
        controller.current = new AbortController();
        try {
            const data = await work(controller.current.signal);
            if (mounted.current) receive(data);
        } catch (error) {
            if (mounted.current) setError(failureText(error));
        } finally {
            inflight.current = false;
            if (mounted.current) setBusy(false);
        }
    }
    function prepare(event) {
        event.preventDefault();
        const payload = { action, account_id: selection.accountId, reason: reason.trim(), idempotency_key: nonce.current };
        if (!create) payload.campaign_id = selection.campaignId;
        if (action === "create" || action === "rename") payload.campaign_name = name.trim();
        if (action === "create" || action === "set_budget") payload.budget_native = Number(budget);
        if (action === "create") { payload.objective_type = objective; payload.budget_mode = budgetMode; }
        if (action === "enable" || action === "set_budget") payload.spend_ceiling_native = Number(ceiling);
        request(async (signal) => (await api.post(ROOT, payload, { signal })).data, (data) => { setProposal(data); setApproved(false); setExecutionStarted(false); });
    }
    function receive(data) {
        setProposal(data); setApproved(false);
        if (data.status === "completed" && data.verified === true) callback.current?.(data);
    }
    function execute() {
        if (!proposal || !approved || executionStarted || inflight.current) return;
        setExecutionStarted(true);
        request(async (signal) => (await api.post(`${ROOT}/${encodeURIComponent(proposal.proposal_id)}/approve-and-execute`, { confirmation_digest: proposal.confirmation_digest }, { signal, timeout: 60000 })).data, receive);
    }
    function refresh() {
        request(async (signal) => (await api.get(`${ROOT}/${encodeURIComponent(proposal.proposal_id)}`, { signal })).data, receive);
    }
    function reconcile() {
        request(async (signal) => (await api.post(`${ROOT}/${encodeURIComponent(proposal.proposal_id)}/reconcile`, {}, { signal })).data, receive);
    }
    function showHistory() {
        request(async (signal) => (await api.get(ROOT, { signal, params: { limit: 12 } })).data, (data) => setHistory(data.items || []));
    }
    function openHistory(row) {
        request(async (signal) => (await api.get(`${ROOT}/${encodeURIComponent(row.proposal_id)}`, { signal })).data, (data) => {
            setProposal(data); setApproved(false); setExecutionStarted(data.status !== "previewed");
        });
    }
    const input = "w-full rounded-xl border border-slate-300 bg-white p-3";
    const financial = action === "enable" || action === "set_budget";
    const pending = proposal && ["executing", "submitted", "verifying", "uncertain"].includes(proposal.status);
    return <section id="tiktok-native-management-panel" data-testid="tiktok-native-management-panel" className="space-y-4 rounded-2xl border border-emerald-200 bg-white p-5" dir="rtl">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-lg font-black">{create ? "إنشاء حملة TikTok موقوفة" : "إدارة حملة TikTok"}</h3><p className="text-sm text-slate-600">{selection.accountName || selection.accountId}{!create && ` · ${selection.name || selection.campaignId}`}</p></div><button type="button" disabled={busy} onClick={onClose} className="rounded-lg border px-3 py-2 disabled:opacity-50">إغلاق إدارة الحملة</button></div>
        <p className="rounded-xl bg-emerald-50 p-3 text-sm">تُنشأ الحملة بحالة موقوفة. راجع الإعدادات وعملة الحساب ثم وافق على العملية المحددة. إدارة الإنفاق بالذكاء الاصطناعي لم تُفعّل.</p>
        {error && <div role="alert" className="rounded-xl bg-rose-50 p-3 text-sm text-rose-800">{error}</div>}
        {!proposal && <form onSubmit={prepare} className="grid gap-4 md:grid-cols-2">
            {!create && <label className="space-y-1 text-sm font-bold">العملية<select aria-label="عملية إدارة الحملة" value={action} disabled={busy} onChange={(event) => changed(setAction, event.target.value)} className={input}>{ACTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}
            {(create || action === "rename") && <label className="space-y-1 text-sm font-bold">اسم الحملة<input aria-label="اسم حملة TikTok" maxLength={240} required value={name} disabled={busy} onChange={(event) => changed(setName, event.target.value)} className={input} /></label>}
            {create && <label className="space-y-1 text-sm font-bold">الهدف<select aria-label="هدف حملة TikTok" value={objective} disabled={busy} onChange={(event) => changed(setObjective, event.target.value)} className={input}><option value="WEB_CONVERSIONS">مبيعات الموقع</option><option value="LEAD_GENERATION">العملاء المحتملون</option></select></label>}
            {(create || action === "set_budget") && <label className="space-y-1 text-sm font-bold">الميزانية · {selection.currency || "عملة الحساب"}<input aria-label="ميزانية حملة TikTok" type="number" min="0.01" max="10000000" step="0.01" required value={budget} disabled={busy} onChange={(event) => changed(setBudget, event.target.value)} className={input} /></label>}
            {create && <label className="space-y-1 text-sm font-bold">حد الميزانية<select aria-label="نوع ميزانية حملة TikTok" value={budgetMode} disabled={busy} onChange={(event) => changed(setBudgetMode, event.target.value)} className={input}><option value="BUDGET_MODE_DAY">حد يومي لجميع المجموعات</option><option value="BUDGET_MODE_TOTAL">حد إجمالي لعمر الحملة</option></select></label>}
            {financial && <><label className="space-y-1 text-sm font-bold">سقف الإنفاق الموافق عليه · {selection.currency || "عملة الحساب"}<input aria-label="سقف إنفاق حملة TikTok" type="number" min="0.01" max="10000000" step="0.01" required value={ceiling} disabled={busy} onChange={(event) => changed(setCeiling, event.target.value)} className={input} /></label><p className="text-sm text-amber-800">يلزم إثبات ميزانية Smart+ محدودة ودون زيادة تلقائية. الميزانية اليومية الديناميكية قد تصرف حتى 125% من متوسطها اليومي؛ يعرض الاقتراح الحد قبل موافقتك.</p></>}
            <label className="space-y-1 text-sm font-bold md:col-span-2">سبب العملية<textarea aria-label="سبب إدارة حملة TikTok" minLength={5} maxLength={400} required value={reason} disabled={busy} onChange={(event) => changed(setReason, event.target.value)} className={input} /></label>
            <button type="submit" disabled={busy} className="rounded-xl bg-emerald-700 px-4 py-3 font-bold text-white disabled:opacity-50">{busy ? "جارٍ تجهيز المعاينة…" : "معاينة الاقتراح"}</button>
        </form>}
        {proposal && <div className="space-y-4" data-testid="tiktok-management-proposal">
            <div className="rounded-xl bg-slate-50 p-4"><p className="font-black">{STATUS[proposal.status] || proposal.status}</p><p className="text-sm">{proposal.campaign_name} · {proposal.account_name || proposal.account_id} · عملة التنفيذ: {proposal.currency}</p><p className="font-mono text-xs text-slate-500">{proposal.created_campaign_id || proposal.campaign_id || proposal.proposal_id}</p><p className="text-sm">{proposal.reason}</p></div>
            <table className="w-full text-right text-sm"><thead><tr><th className="p-2">الإعداد</th><th className="p-2">قبل</th><th className="p-2">المطلوب</th></tr></thead><tbody>{Object.entries(proposal.planned || {}).map(([field, value]) => <tr key={field} className="border-t"><td className="p-2">{{ campaign_name: "الاسم", operation_status: "الحالة", objective_type: "الهدف", budget: `الميزانية · ${proposal.currency}`, budget_mode: "نوع الميزانية", budget_optimize_on: "توزيع ميزانية الحملة تلقائيًا", sales_destination: "وجهة المبيعات" }[field] || field}</td><td className="p-2">{String(proposal.before?.[field] ?? "—")}</td><td className="p-2">{{ ENABLE: "مفعّل", DISABLE: "موقوف", WEBSITE: "الموقع", WEB_CONVERSIONS: "مبيعات الموقع", LEAD_GENERATION: "العملاء المحتملون", BUDGET_MODE_DAY: "حد يومي", BUDGET_MODE_TOTAL: "حد إجمالي" }[value] || (typeof value === "boolean" ? value ? "نعم" : "لا" : String(value))}</td></tr>)}</tbody></table>
            {proposal.financial_bound && <p className="rounded-xl bg-amber-50 p-3 text-sm">الحد المثبت: {proposal.financial_bound.maximum_native} {proposal.currency} {proposal.financial_bound.period === "lifetime" ? "لعمر الحملة" : "في اليوم"} · السقف الموافق عليه: {proposal.financial_bound.approved_ceiling_native} {proposal.currency}.</p>}
            {proposal.status === "previewed" && !executionStarted && <><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={approved} disabled={busy} onChange={(event) => setApproved(event.target.checked)} aria-label="الموافقة على اقتراح TikTok المحدد" />أوافق على الإعدادات المعروضة وعملة التنفيذ {proposal.currency} لهذه العملية فقط.</label><button type="button" disabled={!approved || busy} onClick={execute} className="rounded-xl bg-emerald-700 px-4 py-3 font-bold text-white disabled:opacity-50">الموافقة والتنفيذ في TikTok</button></>}
            {proposal.status === "completed" && proposal.verified === true && <p role="status" className="rounded-xl bg-emerald-50 p-3 text-sm font-bold">أكد TikTok الإعدادات المطلوبة. {proposal.action === "create" && "الحملة موقوفة؛ أكمل إعداد المجموعات والإعلانات قبل تشغيلها."}</p>}
            {(pending || executionStarted && proposal.status !== "completed") && <p className="text-sm text-amber-800">لا تعِد إرسال العملية. اقرأ حالتها أو تحقق من النتيجة. {proposal.reconcile_after && `يتاح التحقق بعد ${new Date(proposal.reconcile_after).toLocaleTimeString("ar-SA")}.`}</p>}
            <div className="flex flex-wrap gap-2"><button type="button" disabled={busy} onClick={refresh} className="rounded-lg border px-3 py-2">تحديث حالة الاقتراح</button>{pending && <button type="button" disabled={busy || proposal.reconcile_after && Date.parse(proposal.reconcile_after) > Date.now()} onClick={reconcile} className="rounded-lg border px-3 py-2">التحقق من TikTok</button>}{proposal.status === "previewed" && !executionStarted && <button type="button" disabled={busy} onClick={() => { setProposal(null); setApproved(false); nonce.current = key(); }} className="rounded-lg border px-3 py-2">تعديل الاقتراح</button>}</div>
        </div>}
        <button type="button" disabled={busy} onClick={showHistory} className="text-sm font-bold text-emerald-800">سجل عمليات TikTok</button>
        {history && <div className="space-y-2" aria-label="سجل عمليات TikTok">{history.length === 0 && <p className="text-sm text-slate-500">لا توجد عمليات محفوظة.</p>}{history.map((row) => <button type="button" key={row.proposal_id} disabled={busy} onClick={() => openHistory(row)} className="block w-full rounded-xl border p-3 text-right text-sm">{row.campaign_name} · {row.account_name || row.account_id} · {STATUS[row.status] || row.status}</button>)}</div>}
    </section>;
}
