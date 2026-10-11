import { useEffect, useRef, useState } from "react";
import { ArrowClockwise, CheckCircle, Link, ShieldCheck, WarningCircle } from "@phosphor-icons/react";
import api from "../../lib/api";

const ROOT = "/integrations-v2/tiktok/content";
const STATUS = {
    previewed: "معاينة بانتظار اعتمادك", validating: "جارٍ التحقق قبل الإرسال", submitted: "أُرسل الطلب؛ لم تثبت النتيجة بعد",
    accepted: "استلم TikTok مهمة النشر", processing: "TikTok يعالج الوسائط", uncertain: "نتيجة الإرسال غير مؤكدة؛ يُمنع تكراره",
    blocked: "توقف الطلب قبل النشر؛ أنشئ معاينة جديدة", failed: "فشل النشر لدى TikTok", draft_delivered: "وصلت المسودة إلى صندوق TikTok",
    complete_pending_ids: "اكتملت المهمة؛ ننتظر معرّف المنشور العام", published_public: "نُشر للعامة وتأكد معرّف المنشور", published_private: "اكتمل النشر بخصوصية الحساب",
    local_uncertain: "انقطع الاتصال؛ راجع سجل العملية قبل أي محاولة جديدة",
};
const PRIVACY = { PUBLIC_TO_EVERYONE: "الجميع", MUTUAL_FOLLOW_FRIENDS: "الأصدقاء المتابعون بالتبادل", FOLLOWER_OF_CREATOR: "متابعو الحساب الخاص", SELF_ONLY: "أنا فقط" };
const START = { kind: "video", delivery: "draft", video_url: "", photos: "", duration: "", title: "", caption: "", privacy_level: "", disclosure: "", is_ai_generated: false, disable_comment: true, disable_duet: true, disable_stitch: true, media_requirements_confirmed: false };
const fieldClass = "w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-900";
const buttonClass = "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-extrabold text-slate-800 disabled:opacity-50";

function errorMessage(error) {
    const detail = error?.response?.data?.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail) && typeof detail.message === "string") return detail.message;
    if (error?.response?.status === 422) return "راجع الروابط والمدة والنص والإفصاح، ثم أعد المعاينة.";
    return "تعذر إكمال الطلب. راجع الربط أو سجل العملية قبل إعادة الإرسال.";
}

function Field({ label, children }) {
    return <label className="block space-y-1.5 text-sm font-bold text-slate-700"><span>{label}</span>{children}</label>;
}

function Tick({ label, value, onChange, disabled = false, testId }) {
    return <label className="flex items-start gap-2 text-sm font-semibold leading-6 text-slate-700"><input type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} disabled={disabled} data-testid={testId} className="mt-1 h-4 w-4 shrink-0" />{label}</label>;
}

function key() {
    return window.crypto.randomUUID().replaceAll("-", "");
}

export default function TikTokContentWorkspace() {
    const [accounts, setAccounts] = useState([]);
    const [creator, setCreator] = useState("");
    const [label, setLabel] = useState("");
    const [authorization, setAuthorization] = useState(null);
    const [proof, setProof] = useState(null);
    const [form, setForm] = useState({ ...START });
    const [proposal, setProposal] = useState(null);
    const [approved, setApproved] = useState(false);
    const [history, setHistory] = useState([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const running = useRef(false);
    const mounted = useRef(false);
    const sequence = useRef(0);
    const abort = useRef(null);
    const hasUnresolvedWrite = useRef(false);

    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; sequence.current += 1; abort.current?.abort(); };
    }, []);

    async function request(action, apply, publishing = false) {
        if (running.current) return;
        running.current = true;
        const attempt = ++sequence.current;
        const controller = new AbortController();
        abort.current = controller;
        setBusy(true); setError("");
        try {
            const result = await action({ signal: controller.signal, timeout: 60000 });
            if (mounted.current && sequence.current === attempt) apply(result.data);
        } catch (failure) {
            if (mounted.current && sequence.current === attempt) {
                setError(errorMessage(failure));
                if (publishing) {
                    hasUnresolvedWrite.current = true;
                    setProposal((current) => ({ ...current, status: "local_uncertain" }));
                }
            }
        } finally {
            if (mounted.current && sequence.current === attempt) setBusy(false);
            running.current = false;
        }
    }

    function change(field, value) {
        if (running.current) return;
        setForm((current) => ({ ...current, [field]: value }));
        if (!hasUnresolvedWrite.current) setProposal(null);
        setApproved(false); setError("");
    }

    function selectCreator(value) {
        if (running.current) return;
        setCreator(value); setProof(null); setProposal(null); setApproved(false); setHistory([]); setError("");
        hasUnresolvedWrite.current = false;
        setForm({ ...START });
    }

    function prepare(event) {
        event.preventDefault();
        if (hasUnresolvedWrite.current) { setError("راجع حالة العملية الحالية من السجل قبل إنشاء طلب آخر."); return; }
        if (!proof || proof.creator_ref !== creator || !form.disclosure || !form.privacy_level) { setError("تحقق من حساب النشر واختر الخصوصية والإفصاح أولًا."); return; }
        const value = {
            creator_ref: creator, idempotency_key: key(), kind: form.kind, delivery: form.delivery,
            video_url: form.kind === "video" ? form.video_url.trim() : null,
            photo_urls: form.kind === "photo" ? form.photos.split("\n").map((url) => url.trim()).filter(Boolean) : [],
            video_duration_seconds: form.kind === "video" ? Number(form.duration) : null,
            title: form.kind === "photo" ? form.title : "", caption: form.caption, privacy_level: form.privacy_level,
            is_brand_organic: form.disclosure === "own", is_branded_content: form.disclosure === "paid",
            is_ai_generated: form.kind === "video" && form.is_ai_generated,
            disable_comment: form.disable_comment, disable_duet: form.disable_duet, disable_stitch: form.disable_stitch,
            media_requirements_confirmed: form.media_requirements_confirmed,
        };
        if (value.photo_urls.length > 10) { setError("يسمح ميزان بحد أقصى 10 صور في المنشور."); return; }
        setApproved(false);
        request((options) => api.post(`${ROOT}/proposals`, value, options), setProposal);
    }

    function publish() {
        if (!approved || proposal?.status !== "previewed" || hasUnresolvedWrite.current) return;
        hasUnresolvedWrite.current = true;
        setApproved(false);
        request((options) => api.post(`${ROOT}/proposals/${proposal.proposal_id}/approve-and-publish`, { confirmation_digest: proposal.confirmation_digest }, options), (result) => {
            setProposal((current) => ({ ...current, ...result }));
            if (["blocked", "failed", "draft_delivered", "published_public", "published_private", "complete_pending_ids"].includes(result.status)) hasUnresolvedWrite.current = false;
        }, true);
    }

    function checkStatus() {
        if (!proposal) return;
        request((options) => api.get(`${ROOT}/proposals/${proposal.proposal_id}/status`, options), (result) => {
            setProposal((current) => ({ ...current, ...result }));
            hasUnresolvedWrite.current = ["validating", "submitted", "accepted", "processing", "uncertain"].includes(result.status);
        });
    }

    const selected = accounts.find((item) => item.creator_ref === creator);
    const input = proposal?.input;
    const canPreview = proof && (form.delivery === "draft" ? proof.capabilities?.draft_upload : proof.capabilities?.publish);
    const canCheck = proposal && proposal.status !== "previewed";

    return <section className="space-y-4" dir="rtl" data-testid="tiktok-content-workspace">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center gap-3"><ShieldCheck size={26} className="text-emerald-700" /><div><h2 className="text-xl font-black text-slate-900">منشورات TikTok</h2><p className="mt-1 text-sm font-semibold leading-6 text-slate-500">اربط حساب النشر، عاين المحتوى، ثم اعتمد إرساله. يسحب TikTok الوسائط مباشرة من روابطك الموثقة.</p></div></div>
            <p className="mt-3 rounded-xl bg-slate-50 p-3 text-sm leading-6 text-slate-700">المعاينة صالحة 10 دقائق. حد ميزان: 10 صور للمنشور، و15 عملية إرسال للحساب خلال 24 ساعة. التحقق من الحالة يدوي، ولا تُحمّل ملفات الفيديو إلى خادم ميزان.</p>
            <div className="mt-4 flex flex-wrap gap-2"><button type="button" className={buttonClass} disabled={busy} onClick={() => request((options) => api.get(`${ROOT}/creators`, options), (data) => setAccounts(data.items || []))} data-testid="tiktok-content-load-accounts"><ArrowClockwise size={18} />تحديث حسابات النشر</button><button type="button" className={buttonClass} disabled={busy} onClick={() => request((options) => api.get(`${ROOT}/proposals`, { ...options, params: { limit: 12, ...(creator ? { creator_ref: creator } : {}) } }), (data) => setHistory(data.items || []))} data-testid="tiktok-content-history">سجل النشر</button></div>
            <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto]"><Field label="اسم الحساب في ميزان"><input value={label} onChange={(event) => setLabel(event.target.value)} maxLength={80} disabled={busy} className={fieldClass} placeholder="مثال: حساب متجر أماسي" /></Field><button type="button" disabled={busy || !label.trim()} className={`${buttonClass} sm:self-end`} onClick={() => request((options) => api.post(`${ROOT}/creators/connect/start`, { label: label.trim() }, options), (data) => {
                try { const url = new URL(data.authorization_url); if (url.protocol !== "https:" || url.hostname !== "www.tiktok.com" || url.pathname !== "/v2/auth/authorize" || url.username || url.password || url.port || url.hash) throw new Error(); setAuthorization(data); }
                catch { setError("تعذر التحقق من رابط تفويض TikTok."); }
            })} data-testid="tiktok-content-connect"><Link size={18} />ربط حساب النشر</button></div>
            {authorization && <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm leading-6 text-violet-900"><p>ستراجع في TikTok صلاحيات هوية الحساب ونشر الفيديو والصور والمسودات وقراءة التعليقات وإدارتها. الحساب الإعلاني له تفويض مستقل.</p><a href={authorization.authorization_url} rel="noreferrer noopener" className="mt-2 inline-flex font-black underline">متابعة تفويض TikTok</a></div>}
            <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto]"><Field label="حساب النشر"><select value={creator} onChange={(event) => selectCreator(event.target.value)} disabled={busy} className={fieldClass} data-testid="tiktok-content-creator"><option value="">اختر حسابًا بعد تحديث القائمة</option>{accounts.map((item) => <option key={item.creator_ref} value={item.creator_ref}>{item.label} · {item.status === "connected" ? "متصل" : "يحتاج إعادة تفويض"}</option>)}</select></Field><button type="button" disabled={busy || !creator || selected?.status !== "connected"} className={`${buttonClass} sm:self-end`} onClick={() => request((options) => api.post(`${ROOT}/creators/${creator}/verify`, null, options), (data) => { setProof(data); setProposal(null); setApproved(false); setForm((current) => ({ ...current, privacy_level: "" })); })} data-testid="tiktok-content-verify">التحقق من حساب النشر</button></div>
            {!accounts.length && <p className="mt-3 text-sm font-semibold text-slate-500">اضغط تحديث لعرض الحسابات المرتبطة. ربط الإعلانات وحده لا يفعّل نشر المنشورات.</p>}
            {proof && <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm leading-6 text-emerald-900" data-testid="tiktok-content-readiness"><p className="font-black"><CheckCircle size={18} className="ml-2 inline" />تم التحقق من هوية الحساب وصلاحياته الفعلية</p><p>النشر: {proof.capabilities.publish ? "متاح" : "يحتاج صلاحية"} · المسودات: {proof.capabilities.draft_upload ? "متاحة" : "تحتاج صلاحية"} · التعليقات: {proof.capabilities.comments_read ? "القراءة متاحة" : "تحتاج صلاحية"}</p><p>الرسائل: {proof.capabilities.messaging_read ? "صلاحية قراءة متاحة" : "لم يمنح الحساب صلاحية الرسائل"}. الرد التلقائي لم يُفعّل بعد.</p><p className="mt-2 font-bold">نطاقات وبوادئ الوسائط الموثقة:</p>{proof.verified_properties.length ? <ul className="list-inside list-disc break-all" dir="ltr">{proof.verified_properties.map((item, index) => <li key={index}>{item.host}{item.type === 2 ? item.path : " (النطاق وفروعه)"}</li>)}</ul> : <p>وثّق ملكية رابط الوسائط في تطبيق TikTok لإتاحة النشر.</p>}</div>}
        </div>

        <form onSubmit={prepare} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="tiktok-content-form">
            <fieldset disabled={busy || hasUnresolvedWrite.current} className="space-y-4">
                <h3 className="text-lg font-black text-slate-900">تجهيز المنشور</h3>
                <div className="grid gap-3 sm:grid-cols-2"><Field label="نوع المحتوى"><select className={fieldClass} value={form.kind} onChange={(event) => { change("kind", event.target.value); }} data-testid="tiktok-content-kind"><option value="video">فيديو</option><option value="photo">صور</option></select></Field><Field label="طريقة الإرسال"><select className={fieldClass} value={form.delivery} onChange={(event) => change("delivery", event.target.value)}><option value="draft">مسودة لاستكمالها في TikTok</option><option value="publish">نشر مباشر بعد المعاينة والاعتماد</option></select></Field></div>
                {form.kind === "video" ? <div className="grid gap-3 sm:grid-cols-[1fr_140px]"><Field label="رابط الفيديو الموثق"><input type="url" required value={form.video_url} onChange={(event) => change("video_url", event.target.value)} maxLength={2048} className={fieldClass} dir="ltr" placeholder="https://your-domain.com/product.mp4" /></Field><Field label="مدة الفيديو بالثواني"><input type="number" required min={3} max={proof?.settings.max_video_post_duration_sec || 600} step={1} value={form.duration} onChange={(event) => change("duration", event.target.value)} className={fieldClass} /></Field></div> : <><Field label="روابط الصور الموثقة — رابط في كل سطر، حتى 10 صور"><textarea required value={form.photos} onChange={(event) => change("photos", event.target.value)} maxLength={20500} rows={4} className={fieldClass} dir="ltr" placeholder="https://your-domain.com/product.jpg" /></Field><Field label="عنوان منشور الصور"><input value={form.title} onChange={(event) => change("title", event.target.value)} maxLength={90} className={fieldClass} /></Field></>}
                <Field label="نص المنشور"><textarea value={form.caption} onChange={(event) => change("caption", event.target.value)} maxLength={form.kind === "video" ? 2200 : 4000} rows={3} className={fieldClass} /></Field>
                <div className="grid gap-3 sm:grid-cols-2"><Field label="خصوصية المنشور"><select required value={form.privacy_level} onChange={(event) => change("privacy_level", event.target.value)} className={fieldClass}><option value="">اختر بعد التحقق من الحساب</option>{(proof?.settings.privacy_level_options || []).filter((value) => form.kind !== "video" || form.delivery !== "publish" || value === "PUBLIC_TO_EVERYONE").map((value) => <option key={value} value={value}>{PRIVACY[value]}</option>)}</select></Field><Field label="الإفصاح عن المحتوى التجاري"><select required value={form.disclosure} onChange={(event) => change("disclosure", event.target.value)} className={fieldClass}><option value="">اختر الإفصاح المناسب</option><option value="none">لا يروج لمنتج أو علامة تجارية</option><option value="own">ترويج منتجات متجري — محتوى ترويجي</option><option value="paid">شراكة مدفوعة مع علامة تجارية</option></select></Field></div>
                {form.delivery === "draft" ? <p className="rounded-xl bg-amber-50 p-3 text-sm font-bold leading-6 text-amber-900">{form.kind === "video" ? "مسودة الفيديو تصل إلى صندوق TikTok؛ تُضاف الكتابة والخصوصية والإفصاح وإعدادات التفاعل داخل TikTok." : "مسودة الصور تحتفظ بالعنوان والنص فقط؛ تُستكمل الخصوصية والإفصاح وإعدادات التفاعل داخل TikTok."}</p> : <div className="grid gap-2 sm:grid-cols-2"><Tick label="إيقاف التعليقات" value={form.disable_comment || proof?.settings.comment_disabled === true} disabled={proof?.settings.comment_disabled === true} onChange={(value) => change("disable_comment", value)} />{form.kind === "video" && <><Tick label="إيقاف الدويتو" value={form.disable_duet || proof?.settings.duet_disabled === true} disabled={proof?.settings.duet_disabled === true} onChange={(value) => change("disable_duet", value)} /><Tick label="إيقاف الدمج" value={form.disable_stitch || proof?.settings.stitch_disabled === true} disabled={proof?.settings.stitch_disabled === true} onChange={(value) => change("disable_stitch", value)} /><Tick label="الفيديو مولّد أو معدّل بشكل ملحوظ بالذكاء الاصطناعي" value={form.is_ai_generated} onChange={(value) => change("is_ai_generated", value)} /></>}</div>}
                <p className="text-xs font-semibold leading-6 text-slate-500">{form.kind === "video" ? "الفيديو MP4 أو MOV أو WebM، حتى 1GB، بدقة لا تقل عن 360×360 ومعدل 23–60 إطارًا/ثانية. مدة الحساب تُتحقق قبل الإرسال." : "الصور JPG أو JPEG أو WebP، حتى 20MB للصورة، بدقة حتى 1080×1920 أو 1920×1080."} يجب أن يبقى الرابط متاحًا لـTikTok لمدة 30 دقيقة على الأقل. يتحقق ميزان من ملكية الرابط وبيانات الطلب؛ يتحقق TikTok من الملف عند سحبه.</p>
                <Tick label="تحققت من مواصفات الملفات ومن إتاحة الروابط وحقوق استخدام المحتوى" value={form.media_requirements_confirmed} onChange={(value) => change("media_requirements_confirmed", value)} testId="tiktok-content-media-confirmation" />
                <button type="submit" disabled={!canPreview || !form.media_requirements_confirmed || !form.disclosure || !form.privacy_level} className={`${buttonClass} bg-slate-950 text-white`} data-testid="tiktok-content-preview">معاينة المنشور قبل الإرسال</button>
            </fieldset>
        </form>

        {error && <div role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-900"><WarningCircle size={20} className="ml-2 inline" />{error}</div>}
        {proposal && <div className="space-y-3 rounded-2xl border border-violet-200 bg-white p-5" data-testid="tiktok-content-proposal"><h3 className="text-lg font-black">{STATUS[proposal.status] || "راجع حالة العملية"}</h3><p className="text-sm font-bold text-slate-600">الحساب: {proposal.account_label} · {proposal.kind === "video" ? "فيديو" : "صور"} · {proposal.delivery === "draft" ? "مسودة" : "نشر مباشر"}</p>{input && <><p className="whitespace-pre-wrap break-words text-sm leading-6">{input.title && <strong>{input.title}<br /></strong>}{input.caption || "دون نص"}</p><ul dir="ltr" className="space-y-1 break-all rounded-xl bg-slate-50 p-3 text-xs">{(input.kind === "video" ? [input.video_url] : input.photo_urls).map((url) => <li key={url}>{url}</li>)}</ul><p className="text-sm font-bold">الخصوصية: {PRIVACY[input.privacy_level]} · الإفصاح: {input.is_branded_content ? "شراكة مدفوعة" : input.is_brand_organic ? "محتوى ترويجي" : "دون ترويج تجاري"}</p></>}{proposal.delivery === "publish" && proposal.effective_post_info && <p className="text-sm leading-6">التعليقات: {proposal.effective_post_info.disable_comment ? "موقوفة" : "مسموحة"}{proposal.kind === "video" && <> · الدويتو: {proposal.effective_post_info.disable_duet ? "موقوف" : "مسموح"} · الدمج: {proposal.effective_post_info.disable_stitch ? "موقوف" : "مسموح"} · وسم الذكاء الاصطناعي: {proposal.effective_post_info.is_ai_generated ? "مفعّل" : "غير مفعّل"}</>}</p>}{proposal.draft_notice && <p className="rounded-xl bg-amber-50 p-3 text-sm font-bold leading-6 text-amber-900">{proposal.draft_notice}</p>}{proposal.publish_task_id && <p className="break-all text-xs font-semibold">معرّف مهمة النشر: <span dir="ltr">{proposal.publish_task_id}</span></p>}{proposal.post_ids?.length > 0 && <p className="text-sm font-bold text-emerald-700">معرّفات المنشور المؤكدة: <span dir="ltr">{proposal.post_ids.join("، ")}</span></p>}{proposal.status === "previewed" && <><Tick label="أعتمد إرسال المحتوى والروابط والإعدادات المعروضة إلى TikTok مرة واحدة" value={approved} onChange={setApproved} disabled={busy} testId="tiktok-content-approval" /><button type="button" disabled={busy || !approved} onClick={publish} className={`${buttonClass} bg-violet-700 text-white`} data-testid="tiktok-content-publish">{proposal.delivery === "draft" ? "اعتماد إرسال المسودة" : "اعتماد ونشر المنشور"}</button></>}{canCheck && <button type="button" disabled={busy} onClick={checkStatus} className={buttonClass} data-testid="tiktok-content-check-status"><ArrowClockwise size={18} />التحقق من حالة العملية</button>}</div>}
        {history.length > 0 && <div className="space-y-2 rounded-2xl border border-slate-200 bg-white p-5" data-testid="tiktok-content-history-list"><h3 className="font-black">آخر 12 عملية نشر</h3>{history.map((item) => <button type="button" key={item.proposal_id} disabled={busy} className="flex w-full items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 text-right text-sm font-bold" onClick={() => request((options) => api.get(`${ROOT}/proposals/${item.proposal_id}`, options), (result) => { setProposal(result); setApproved(false); hasUnresolvedWrite.current = ["validating", "submitted", "accepted", "processing", "uncertain"].includes(result.status); })}><span>{item.account_label} · {item.kind === "video" ? "فيديو" : "صور"}</span><span className="text-xs text-slate-500">{STATUS[item.status] || item.status}</span></button>)}</div>}
    </section>;
}
