import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const ROOT = "/integrations-v2/tiktok_ads/spend-guard";
const BUTTON = "rounded-xl border bg-white px-4 py-3 text-sm font-bold disabled:opacity-40";
const STATUS = { paused_verified: "أُوقفت الحملة وتحققت النتيجة", already_paused: "الحملة موقوفة بالفعل", below_trigger: "الصرف دون الحد المختار", blocked: "لم تكتمل شروط الفحص؛ لم يُعتمد إيقاف", uncertain: "نتيجة غير مؤكدة؛ راجع سجل عمليات الحملة", checking: "بانتظار نتيجة الفحص", submitted: "أُرسل الإيقاف؛ راجع نتيجة العملية" };

export default function TikTokSpendGuard({ selection, onClose }) {
    const [policy, setPolicy] = useState(null), [enabled, setEnabled] = useState(false), [threshold, setThreshold] = useState(""), [ack, setAck] = useState(false);
    const [busy, setBusy] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState(""), [history, setHistory] = useState(null);
    const mounted = useRef(true), inflight = useRef(false), controller = useRef(null);
    function receive(value) {
        const rule = (value.rules || []).find((row) => row.campaign_id === selection.campaignId);
        setPolicy(value); setEnabled(value.enabled === true && !!rule); setThreshold(rule ? String(rule.pause_at_daily_spend_native) : ""); setAck(false);
    }
    async function request(work, done) {
        if (inflight.current) return;
        inflight.current = true; setBusy(true); setError(""); setMessage(""); controller.current = new AbortController();
        try { const response = await work({ signal: controller.current.signal, timeout: 60000 }); if (mounted.current) done(response.data); }
        catch (failure) { if (mounted.current) setError(failure?.response?.data?.detail?.message || "تعذر تأكيد حفظ السياسة؛ حدّثها قبل المحاولة التالية."); }
        finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    function refresh() { return request((options) => api.get(`${ROOT}/policy`, { ...options, params: { account_id: selection.accountId } }), receive); }
    useEffect(() => { mounted.current = true; refresh(); return () => { mounted.current = false; controller.current?.abort(); }; }, []);
    const otherRules = (policy?.rules || []).filter((rule) => rule.campaign_id !== selection.campaignId);
    const savedEnabled = policy?.enabled === true && (policy.rules || []).some((rule) => rule.campaign_id === selection.campaignId);
    function save(event) {
        event.preventDefault(); if (!policy || busy) return;
        const rules = [...otherRules, ...(enabled ? [{ campaign_id: selection.campaignId, pause_at_daily_spend_native: Number(threshold) }] : [])];
        if (rules.length && !ack) return;
        request((options) => api.put(`${ROOT}/policy`, { account_id: selection.accountId, expected_revision: policy.revision, enabled: rules.length > 0, rules, polling_and_report_delay_acknowledged: ack }, options), (data) => { receive(data); setMessage("حُفظت سياسة المالك. يظهر سجل الفحص عند مرور دورة المتابعة التالية."); });
    }
    function disableAccount() {
        if (!policy) return;
        request((options) => api.put(`${ROOT}/policy`, { account_id: selection.accountId, expected_revision: policy.revision, enabled: false, rules: [], polling_and_report_delay_acknowledged: false }, options), (data) => { receive(data); setMessage("عُطلت سياسات الحماية لهذا الحساب. الطلب الذي سبق إرساله قد يكتمل؛ راجع السجل."); });
    }
    return <section data-testid="tiktok-spend-guard" className="space-y-4 rounded-2xl border border-amber-200 bg-white p-5" dir="rtl">
        <div className="flex justify-between gap-3"><h3 className="font-black">حماية صرف حملة TikTok</h3><button className={BUTTON} type="button" disabled={busy} onClick={onClose}>إغلاق حماية الصرف</button></div>
        <p className="text-sm">{selection.accountName || selection.accountId} · {selection.name}</p>
        <p className="rounded-xl bg-amber-50 p-3 text-sm leading-6">تراجع متابعة ميزان الحملات المحددة دوريًا، وتطلب إيقاف الحملة إذا بلغ صرف يومها الحد الذي تختاره. قد تتأخر تقارير تيك توك وتُراجع القواعد بالتناوب؛ هذه الحماية ليست حد صرف فوريًا. لا تشغّل الحملات ولا ترفع ميزانيتها.</p>
        {error && <p role="alert" className="text-sm text-rose-800">{error}</p>}{message && <p role="status" className="text-sm text-emerald-800">{message}</p>}
        {policy && <form onSubmit={save}><fieldset disabled={busy} className="space-y-3">
            <p className="text-sm">{savedEnabled ? "قاعدة الحملة المحفوظة مفعّلة" : "قاعدة الحملة المحفوظة غير مفعّلة"} · عملة الصرف {policy.currency || selection.currency || "تُثبت من الحساب عند الحفظ"}{policy.timezone ? ` · يوم الصرف بتوقيت ${policy.timezone}` : ""}</p>
            <label className="flex gap-2 text-sm font-bold"><input type="checkbox" aria-label="تفعيل حماية صرف الحملة" checked={enabled} onChange={(event) => { setEnabled(event.target.checked); setAck(false); }} />إيقاف هذه الحملة آليًا عند بلوغ حد الصرف اليومي المختار</label>
            {enabled && <label className="block text-sm font-bold">حد الصرف اليومي للحملة · عملة الحساب<input aria-label="حد صرف حماية TikTok" className="mt-1 w-full rounded-xl border p-3" type="number" min="0.01" max="10000000" step="0.01" required value={threshold} onChange={(event) => { setThreshold(event.target.value); setAck(false); }} /></label>}
            <label className="flex gap-2 text-sm"><input type="checkbox" aria-label="اعتماد تأخر فحص الحماية" checked={ack} onChange={(event) => setAck(event.target.checked)} />أفهم أن المتابعة دورية كل خمس ساعات وبالتناوب، وأن تأخر التقرير والفحص قد يسمح باستمرار الصرف بعد الحد. أعتمد إيقاف الحملة المحددة وفق هذه السياسة.</label>
            <button type="submit" className={BUTTON} disabled={busy || !ack && (enabled || otherRules.length > 0)}>حفظ سياسة حماية الحملة</button>
            {policy.enabled && <button type="button" className={BUTTON} disabled={busy} onClick={disableAccount}>تعطيل حماية الحساب بالكامل</button>}
        </fieldset></form>}
        <button type="button" className={BUTTON} disabled={busy} onClick={refresh}>تحديث سياسة الحماية</button>
        <button type="button" className={BUTTON} disabled={busy} onClick={() => request((options) => api.get(`${ROOT}/actions`, { ...options, params: { account_id: selection.accountId, campaign_id: selection.campaignId } }), (data) => setHistory(data.items || []))}>سجل حماية صرف الحملة</button>
        {history && <div className="space-y-2 text-sm">{history.length === 0 && <p>لم تُسجل مراجعة لهذه الحملة بعد.</p>}{history.map((row) => <div key={row._id} className="rounded-xl border p-3"><p>{STATUS[row.status] || "راجع حالة الفحص"}</p><p>{new Date(row.created_at).toLocaleString("ar-SA", { timeZone: "Asia/Riyadh" })} بتوقيت الرياض</p>{row.reported_spend_native != null && <p>الصرف المبلّغ {row.reported_spend_native} {row.currency} · حد الإيقاف {row.pause_at_daily_spend_native} {row.currency} · يوم الحساب {row.native_day}</p>}</div>)}</div>}
    </section>;
}
