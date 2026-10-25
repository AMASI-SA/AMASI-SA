import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const ROOT = "/integrations-v2/tiktok_ads/ad-builder/proposals";
const TITLES = { video_asset: "استيراد فيديو إلى مكتبة تيك توك", adgroup: "إنشاء مجموعة إعلانية موقوفة", ad: "إنشاء إعلان فيديو موقوف" };
const FIELDS = { video_asset: [["video_url", "رابط الفيديو", "url"]], adgroup: [["pixel_id", "معرّف بكسل الشراء", "text"], ["locations", "معرّفات المواقع المستهدفة مفصولة بفاصلة", "text"], ["budget", "ميزانية المجموعة بعملة الحساب", "number"], ["ceiling", "سقف الميزانية الموافق عليه", "number"], ["start", "البداية حسب توقيت جهازك", "datetime-local"], ["end", "النهاية حسب توقيت جهازك", "datetime-local"]], ad: [["video_id", "معرّف الفيديو في مكتبة تيك توك", "text"], ["identity_id", "معرّف هوية الإعلان", "text"], ["landing_page_url", "رابط المنتج", "url"], ["ad_text", "نص الإعلان", "text"]] };
const STATUS = { previewed: "راجع المعاينة ثم اعتمدها", submitted: "أُرسل الطلب؛ تحقق من النتيجة", verifying: "جارٍ التحقق من الإنشاء", uncertain: "نتيجة غير مؤكدة؛ لا تكرر الإنشاء", completed: "اكتمل الإنشاء وتحققت المطابقة" };
const BUTTON = "rounded-xl border bg-white px-4 py-3 text-sm font-bold disabled:opacity-40";

export default function TikTokAdBuilder({ selection, onClose }) {
    const [form, setForm] = useState({}), [proposal, setProposal] = useState(null), [approved, setApproved] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(""), [history, setHistory] = useState([]);
    const mounted = useRef(true), inflight = useRef(false), sent = useRef(false), nonce = useRef(crypto.randomUUID().replaceAll("-", "")), controller = useRef(null);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
    const unresolved = sent.current || (proposal && ["submitted", "verifying", "uncertain"].includes(proposal.status));
    async function request(work, receive) {
        if (inflight.current) return;
        inflight.current = true; setBusy(true); setError(""); controller.current = new AbortController();
        try { const response = await work({ signal: controller.current.signal, timeout: 60000 }); if (mounted.current) receive(response.data); }
        catch (failure) { if (mounted.current) setError(failure?.response?.data?.detail?.message || "لم تثبت النتيجة؛ راجع السجل قبل إنشاء طلب آخر."); }
        finally { inflight.current = false; if (mounted.current) setBusy(false); }
    }
    function prepare(event) {
        event.preventDefault(); if (unresolved) return;
        const value = { kind: selection.kind, account_id: selection.accountId, name: form.name.trim(), reason: form.reason.trim(), idempotency_key: nonce.current };
        if (selection.kind === "video_asset") value.video_url = form.video_url.trim();
        if (selection.kind === "adgroup") Object.assign(value, { campaign_id: selection.campaignId, pixel_id: form.pixel_id.trim(), location_ids: form.locations.split(",").map((s) => s.trim()).filter(Boolean), budget_native: Number(form.budget), spend_ceiling_native: Number(form.ceiling), schedule_start: new Date(form.start).toISOString(), schedule_end: new Date(form.end).toISOString() });
        if (selection.kind === "ad") {
            Object.assign(value, { campaign_id: selection.campaignId, adgroup_id: selection.adgroupId, identity_id: form.identity_id.trim(), identity_type: form.identity_type || "TT_USER", landing_page_url: form.landing_page_url.trim() });
            if (form.creative_source === "spark") value.tiktok_item_id = form.tiktok_item_id.trim();
            else Object.assign(value, { video_id: form.video_id.trim(), ad_text: form.ad_text.trim() });
        }
        request((options) => api.post(ROOT, value, options), (data) => { setProposal(data); setApproved(false); });
    }
    function receive(data) { setProposal((old) => ({ ...old, ...data })); setApproved(false); sent.current = ["submitted", "verifying", "uncertain"].includes(data.status); }
    function execute() {
        if (!proposal || !approved || unresolved || busy) return;
        sent.current = true; setApproved(false);
        request((options) => api.post(`${ROOT}/${proposal.proposal_id}/approve-and-execute`, { confirmation_digest: proposal.confirmation_digest }, options), receive);
    }
    const displayKind = proposal?.kind || selection.kind;
    const input = proposal?.plan?.input;
    const spark = proposal ? !!input?.tiktok_item_id : form.creative_source === "spark";
    const adFields = spark ? [["tiktok_item_id", "معرّف منشور TikTok العام", "text"], ["identity_id", "معرّف هوية الإعلان", "text"], ["landing_page_url", "رابط المنتج", "url"]] : FIELDS.ad;
    const fields = [["name", "الاسم", "text"], ["reason", "سبب الإنشاء", "text"], ...(displayKind === "ad" ? adFields : FIELDS[displayKind])];
    return <section dir="rtl" className="space-y-4 rounded-2xl border border-violet-200 bg-white p-5" data-testid="tiktok-ad-builder">
        <div className="flex justify-between gap-3"><h3 className="font-black">{TITLES[displayKind]}</h3><button type="button" className={BUTTON} disabled={busy} onClick={onClose}>إغلاق تجهيز الإعلان</button></div>
        <p className="text-sm">{proposal?.account_id || selection.accountName || selection.accountId} · {proposal?.plan?.input?.campaign_id || selection.campaignName || selection.campaignId || "مكتبة الحساب"}</p>
        <p className="rounded-xl bg-violet-50 p-3 text-sm leading-6">يسحب تيك توك الفيديو من رابطه مباشرة. تُنشأ المجموعة والإعلان موقوفين داخل حملة موقوفة؛ لا يبدأ الإنفاق بمجرد الإنشاء.</p>
        {error && <p role="alert">{error}</p>}
        {!proposal && <form onSubmit={prepare}><fieldset disabled={busy || unresolved} className="grid gap-3 md:grid-cols-2">
            {displayKind === "ad" && <><label className="space-y-1 text-sm font-bold">مصدر الإعلان<select aria-label="مصدر إعلان TikTok" value={form.creative_source || "library"} onChange={(event) => { setForm((old) => ({ ...old, creative_source: event.target.value })); nonce.current = crypto.randomUUID().replaceAll("-", ""); }} className="w-full rounded-xl border p-3"><option value="library">فيديو من مكتبة الحساب</option><option value="spark">منشور TikTok عام قائم · Spark</option></select></label>{spark && <label className="space-y-1 text-sm font-bold">نوع هوية المنشور<select aria-label="نوع هوية منشور Spark" value={form.identity_type || "TT_USER"} onChange={(event) => { setForm((old) => ({ ...old, identity_type: event.target.value })); nonce.current = crypto.randomUUID().replaceAll("-", ""); }} className="w-full rounded-xl border p-3"><option value="TT_USER">حساب TikTok المرتبط بالإعلانات</option><option value="AUTH_CODE">منشور مفوّض برمز إعلان</option></select></label>}</>}
            {fields.map(([key, label, type]) => <label key={key} className="space-y-1 text-sm font-bold"><span>{label}</span><input aria-label={label} className="w-full rounded-xl border p-3" type={type} value={form[key] || ""} onChange={(event) => { setForm((old) => ({ ...old, [key]: event.target.value })); setApproved(false); nonce.current = crypto.randomUUID().replaceAll("-", ""); }} required min={type === "number" ? .01 : undefined} step={type === "number" ? .01 : undefined} minLength={key === "reason" ? 5 : undefined} maxLength={type === "url" ? 2048 : ["name", "ad_text"].includes(key) ? 100 : 400} /></label>)}<button className={BUTTON} type="submit">معاينة الإعدادات</button></fieldset></form>}
        {spark && proposal?.plan?.proof?.spark_post && <div className="rounded-xl bg-emerald-50 p-3 text-sm"><p>منشور عام مثبت ضمن هوية الإعلان · {proposal.plan.proof.spark_post.item_id}</p><p className="whitespace-pre-wrap">{proposal.plan.proof.spark_post.caption}</p><p>يستخدم الإعلان المنشور ونصه الأصليين. لا يُعاد رفع الفيديو.</p></div>}
        {proposal && <div className="space-y-3"><p role="status" className="font-bold">{STATUS[proposal.status] || proposal.status}</p>{input && <dl className="space-y-2 rounded-xl bg-slate-50 p-3 text-sm">{fields.map(([key, label]) => { const mapped = { locations: "location_ids", budget: "budget_native", ceiling: "spend_ceiling_native", start: "schedule_start", end: "schedule_end" }[key] || key; return <div key={key}><dt className="font-bold">{label}</dt><dd className="break-all">{Array.isArray(input[mapped]) ? input[mapped].join("، ") : String(input[mapped] ?? "")}</dd></div>; })}<div><dt>عملة الحساب</dt><dd>{proposal.plan.proof.currency}</dd></div>{displayKind === "ad" && <><div><dt>المجموعة / هوية الإعلان</dt><dd>{input.adgroup_id} / {input.identity_type}</dd></div><div><dt>زر الإعلان</dt><dd>{input.call_to_action === "SHOP_NOW" ? "تسوق الآن" : input.call_to_action}</dd></div></>}{displayKind === "adgroup" && <><div><dt>حدث التحسين</dt><dd>إتمام طلب الموقع · {input.optimization_event}</dd></div><div><dt>الجدول بتوقيت الحساب</dt><dd>{proposal.plan.body.schedule_start_time} / {proposal.plan.body.schedule_end_time} · {proposal.plan.proof.timezone}</dd></div></>}</dl>}{proposal.created_entity_id && <p>معرّف الإنشاء: <span dir="ltr">{proposal.created_entity_id}</span></p>}
            {proposal.status === "previewed" && !unresolved && <><label className="flex gap-2 text-sm font-bold"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} />أعتمد إرسال الإعدادات والروابط المعروضة إلى تيك توك مرة واحدة</label><button type="button" className={BUTTON} disabled={busy || !approved} onClick={execute}>اعتماد الإنشاء</button></>}
            <button type="button" className={BUTTON} disabled={busy} onClick={() => request((options) => api.get(`${ROOT}/${proposal.proposal_id}`, options), receive)}>تحديث حالة العملية</button>{["submitted", "verifying", "uncertain"].includes(proposal.status) && <button type="button" className={BUTTON} disabled={busy || !proposal.created_entity_id} onClick={() => request((options) => api.post(`${ROOT}/${proposal.proposal_id}/reconcile`, {}, options), receive)}>التحقق من نتيجة تيك توك</button>}
        </div>}
        <button type="button" className={BUTTON} disabled={busy} onClick={() => request((options) => api.get(ROOT, options), (data) => setHistory(data.items || []))}>سجل إنشاء الإعلانات والفيديوهات</button>{history.map((row) => <button type="button" className={`${BUTTON} block w-full text-right`} key={row.proposal_id} disabled={busy} onClick={() => request((options) => api.get(`${ROOT}/${row.proposal_id}`, options), (data) => { setProposal(data); setApproved(false); sent.current = ["submitted", "verifying", "uncertain"].includes(data.status); })}>{row.name} · {STATUS[row.status] || row.status}</button>)}
    </section>;
}
