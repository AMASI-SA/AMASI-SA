import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";
import { describeFile, uploadVideoFile } from "./tiktokFileTransfer";
import TikTokCaptionComposer from "./TikTokCaptionComposer";

const ROOT = "/integrations-v2/tiktok/content/files";
const ACCOUNTS = "/integrations-v2/tiktok/content/creators";
const BUTTON = "rounded-xl border bg-white px-4 py-3 text-sm font-bold disabled:opacity-40";
const FIELD = "w-full rounded-xl border bg-white p-3 text-sm";
const PRIVACY = { PUBLIC_TO_EVERYONE: "الجميع", MUTUAL_FOLLOW_FRIENDS: "الأصدقاء المتابعون بالتبادل", FOLLOWER_OF_CREATOR: "متابعو الحساب", SELF_ONLY: "أنا فقط" };
const STATUS = { previewed: "راجع الملف والإعدادات ثم اعتمد الرفع", validating: "جارٍ التحقق من التفويض", submitted: "أُرسل طلب التهيئة؛ راجع النتيجة", awaiting_upload: "TikTok ينتظر نقل الملف", processing: "TikTok يعالج الفيديو", uncertain: "نتيجة غير مؤكدة؛ حدّث الحالة قبل أي محاولة جديدة", blocked: "توقفت العملية قبل تهيئة الرفع", failed: "أبلغ TikTok بفشل العملية", draft_delivered: "وصل الفيديو إلى صندوق TikTok لاستكمال نشره", published_public: "نُشر الفيديو للعامة وتأكد معرّف المنشور", published_private: "اكتمل النشر بخصوصية الحساب", complete_pending_ids: "اكتملت المهمة؛ ننتظر إثبات معرّف المنشور العام" };
const START = { delivery: "direct", privacy_level: "", caption: "", disclosure: "none", is_aigc: false, disable_comment: true, disable_duet: true, disable_stitch: true, media_rights_confirmed: false };
const pending = (proposal) => proposal && !["previewed", "blocked", "failed", "draft_delivered", "published_public", "published_private"].includes(proposal.status);
const nonce = () => window.crypto.randomUUID().replaceAll("-", "");
const withoutTransfer = (data) => { const { transfer, ...safe } = data; return safe; };
const failureMessage = (failure) => failure?.response?.data?.detail?.message || (failure instanceof Error && !failure.response ? failure.message : "تعذر تأكيد العملية. راجع السجل قبل إرسال طلب جديد.");

export default function TikTokFilePublishing({ onBusyChange }) {
    const [accounts, setAccounts] = useState([]), [creator, setCreator] = useState(""), [proof, setProof] = useState(null);
    const [file, setFile] = useState(null), [descriptor, setDescriptor] = useState(null), [localURL, setLocalURL] = useState("");
    const [form, setForm] = useState({ ...START }), [proposal, setProposal] = useState(null), [approved, setApproved] = useState(false);
    const [busy, setBusy] = useState(false), [error, setError] = useState(""), [phase, setPhase] = useState(""), [progress, setProgress] = useState(0), [history, setHistory] = useState([]);
    const [copyBusy, setCopyBusy] = useState(false);
    const running = useRef(false), mounted = useRef(true), controller = useRef(null), requestKey = useRef(null);
    const selected = useRef(null), metadata = useRef(null), initialized = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
    useEffect(() => { onBusyChange?.(busy || copyBusy); }, [busy, copyBusy, onBusyChange]);
    useEffect(() => { if (!file) { setLocalURL(""); return undefined; } const url = URL.createObjectURL(file); setLocalURL(url); return () => URL.revokeObjectURL(url); }, [file]);
    async function work(action) {
        if (running.current) return;
        running.current = true; setBusy(true); setError(""); controller.current = new AbortController();
        try { await action({ signal: controller.current.signal, timeout: 60000 }); }
        catch (failure) { if (mounted.current) setError(failureMessage(failure)); }
        finally { running.current = false; if (mounted.current) setBusy(false); }
    }
    const receive = (data) => { if (mounted.current) { setProposal(withoutTransfer(data)); setApproved(false); } };
    function change(name, value) { if (busy) return; setForm((old) => ({ ...old, [name]: value })); setApproved(false); if (!pending(proposal)) { setProposal(null); requestKey.current = null; } }
    function chooseCreator(value) {
        if (busy) return;
        setCreator(value); setProof(null); setProposal(null); setApproved(false); setHistory([]); setForm({ ...START }); initialized.current = false; requestKey.current = null;
    }
    function verify() {
        setProof(null); setApproved(false);
        return work(async (options) => { const { data } = await api.post(`${ROOT}/creators/${creator}/verify`, {}, options); if (mounted.current) { setProof(data); setForm((old) => ({ ...old, privacy_level: "", disable_comment: true, disable_duet: true, disable_stitch: true })); } });
    }
    function chooseFile(candidate) {
        if (!candidate || busy) return;
        setFile(candidate); selected.current = candidate; setDescriptor(null); metadata.current = null; setApproved(false); setProgress(0);
        if (!pending(proposal)) { setProposal(null); initialized.current = false; requestKey.current = null; }
        work(async (options) => {
            setPhase("جارٍ فحص الفيديو على جهازك");
            const info = await describeFile(candidate, { signal: options.signal, onProgress: (value) => { if (mounted.current) setProgress(value); } });
            if (mounted.current) { setDescriptor(info); metadata.current = info; setPhase("الملف جاهز للمعاينة"); }
        });
    }
    function prepare(event) {
        event.preventDefault();
        if (!descriptor || !proof || pending(proposal) || initialized.current) return;
        requestKey.current ||= nonce();
        const fields = form.delivery === "draft" ? { ...form, privacy_level: null, caption: "", disclosure: "none", is_aigc: false } : form;
        work(async (options) => { const { data } = await api.post(`${ROOT}/proposals`, { creator_ref: creator, idempotency_key: requestKey.current, ...descriptor, ...fields }, options); receive(data); setPhase(""); });
    }
    async function transfer(data, options) {
        if (!selected.current || !metadata.current || metadata.current.file_manifest_sha256 !== data.input.file_manifest_sha256 || selected.current.size !== data.input.file_size) throw new Error("اختر نفس ملف المعاينة قبل نقل الفيديو.");
        receive(data); setPhase("جارٍ نقل الفيديو من جهازك إلى TikTok"); setProgress(Math.floor((data.transfer.offset || 0) * 100 / selected.current.size));
        await uploadVideoFile(selected.current, data.source_info, data.transfer, { signal: options.signal, onProgress: (value) => { if (mounted.current) setProgress(value); } });
        if (mounted.current) setPhase("اكتمل نقل الملف؛ نتحقق من نتيجة TikTok");
        const status = await api.post(`${ROOT}/proposals/${data.proposal_id}/status`, {}, options); receive(status.data);
    }
    function execute() {
        if (!approved || !proposal || initialized.current || !descriptor || descriptor.file_manifest_sha256 !== proposal.input.file_manifest_sha256) return;
        initialized.current = true; setApproved(false);
        work(async (options) => {
            setPhase("جارٍ تجهيز وجهة الرفع في TikTok");
            const { data } = await api.post(`${ROOT}/proposals/${proposal.proposal_id}/approve-and-initialize`, { confirmation_digest: proposal.confirmation_digest }, options);
            await transfer(data, options);
        });
    }
    function resume() {
        if (!proposal || !descriptor || descriptor.file_manifest_sha256 !== proposal.input.file_manifest_sha256) return;
        work(async (options) => { const { data } = await api.post(`${ROOT}/proposals/${proposal.proposal_id}/transfer`, { file_manifest_sha256: descriptor.file_manifest_sha256 }, options); await transfer(data, options); });
    }
    const frozen = proposal?.input;
    return <section dir="rtl" data-testid="tiktok-file-publishing" className="space-y-4 rounded-2xl border bg-white p-5">
        <h2 className="text-lg font-black">رفع فيديو من الجهاز إلى حساب TikTok</h2>
        <p className="text-sm leading-6">اختر الفيديو من جهازك وراجع إعداداته ثم اعتمد الرفع. ينتقل الملف إلى TikTok مباشرة؛ ميزان يحتفظ ببيانات العملية وسجلها. الفيديو MP4 أو MOV أو WebM، حتى 1GB و10 دقائق بحسب صلاحية الحساب. يتحقق TikTok من الملف أثناء المعالجة.</p>
        {error && <p role="alert" className="rounded-xl bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
        <div className="flex flex-wrap gap-3"><button type="button" className={BUTTON} disabled={busy} onClick={() => work(async (options) => { const { data } = await api.get(ACCOUNTS, options); if (mounted.current) setAccounts(data.items || []); })}>تحديث حسابات رفع الملفات</button>
            <select aria-label="حساب رفع ملف TikTok" className={FIELD} disabled={busy} value={creator} onChange={(e) => chooseCreator(e.target.value)}><option value="">اختر حساب النشر</option>{accounts.map((row) => <option key={row.creator_ref} value={row.creator_ref} disabled={row.status !== "connected"}>{row.label} · {row.status === "connected" ? "متصل" : "يحتاج إعادة ربط"}</option>)}</select>
            <button type="button" className={BUTTON} disabled={busy || !creator} onClick={verify}>التحقق من جاهزية رفع الملفات</button></div>
        {!accounts.length && <p className="text-sm">يمكن ربط حساب النشر أو تجديد تفويضه من قسم «رابط خارجي»، ثم تحديث قائمة الحسابات هنا.</p>}
        {proof && <p className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-900">تم التحقق من هوية الحساب وإعدادات نشر الملفات. الحد المتاح للفيديو {proof.settings.max_video_post_duration_sec} ثانية.</p>}
        <label className="block space-y-2 font-bold">اختيار فيديو من الجهاز<input aria-label="اختيار فيديو من الجهاز" className={FIELD} type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" disabled={busy || !proof} onChange={(e) => chooseFile(e.target.files?.[0])} /></label>
        {localURL && <video src={localURL} controls preload="metadata" aria-label="معاينة الفيديو المحلي" className="max-h-80 w-full rounded-xl bg-black" />}
        {descriptor && <p className="text-sm">{descriptor.file_name} · {(descriptor.file_size / 1048576).toFixed(1)} MB · {descriptor.duration_seconds} ثانية · {descriptor.width}×{descriptor.height}</p>}
        {!proposal && form.delivery === "direct" && <TikTokCaptionComposer disabled={busy} onBusyChange={setCopyBusy} onApply={(caption) => { if (busy || copyBusy) return; change("caption", caption); setForm((old) => ({ ...old, disclosure: "own_business" })); }} />}
        {phase && <div role="status" className="text-sm"><p>{phase}</p>{busy && <progress aria-label="تقدم فحص أو رفع الفيديو" className="w-full" max="100" value={progress} />}</div>}
        {!proposal && <form onSubmit={prepare}><fieldset disabled={busy || copyBusy || !proof || !descriptor} className="space-y-3">
            <label className="block font-bold">طريقة النشر<select className={FIELD} aria-label="طريقة نشر ملف TikTok" value={form.delivery} onChange={(e) => change("delivery", e.target.value)}><option value="direct">نشر على الحساب بعد المعاينة والاعتماد</option><option value="draft">مسودة لاستكمالها داخل TikTok</option></select></label>
            {form.delivery === "direct" ? <><label className="block font-bold">نص المنشور<textarea aria-label="نص منشور ملف TikTok" className={FIELD} maxLength={2200} value={form.caption} onChange={(e) => change("caption", e.target.value)} /></label>
                <label className="block font-bold">الخصوصية<select className={FIELD} aria-label="خصوصية ملف TikTok" required value={form.privacy_level} onChange={(e) => change("privacy_level", e.target.value)}><option value="">اختر الخصوصية</option>{proof?.settings.privacy_level_options.map((value) => <option key={value} value={value}>{PRIVACY[value]}</option>)}</select></label>
                <label className="block font-bold">الإفصاح التجاري<select className={FIELD} aria-label="إفصاح ملف TikTok" value={form.disclosure} onChange={(e) => change("disclosure", e.target.value)}><option value="none">محتوى غير تجاري</option><option value="own_business">يروج لنشاطي التجاري</option><option value="paid_partnership">شراكة مدفوعة مع طرف آخر</option></select></label>
                {[['is_aigc','الفيديو مولّد بالذكاء الاصطناعي'],['disable_comment','إيقاف التعليقات'],['disable_duet','إيقاف الدويتو'],['disable_stitch','إيقاف الدمج']].map(([key,label]) => <label className="flex gap-2 text-sm" key={key}><input aria-label={label} type="checkbox" checked={form[key]} disabled={key.startsWith('disable_') && proof?.settings[key.replace('disable_', '') + '_disabled']} onChange={(e) => change(key,e.target.checked)} />{label}</label>)}</> : <p className="text-sm">تصل المسودة إلى صندوق TikTok؛ تكمل النص والخصوصية والإفصاح والنشر داخل التطبيق.</p>}
            <label className="flex gap-2 text-sm"><input type="checkbox" aria-label="تأكيد حق استخدام الفيديو" checked={form.media_rights_confirmed} onChange={(e) => change("media_rights_confirmed", e.target.checked)} required />أؤكد امتلاكي حق استخدام الفيديو والصوت المصاحب له.</label>
            <button className={BUTTON} type="submit" disabled={!form.media_rights_confirmed}>معاينة رفع الملف</button>
        </fieldset></form>}
        {proposal && <div className="space-y-3 rounded-xl bg-slate-50 p-4"><p role="status" className="font-bold">{STATUS[proposal.status] || "حدّث حالة العملية"}</p><p>{frozen.file_name} · {frozen.delivery === "direct" ? PRIVACY[frozen.privacy_level] : "مسودة"}</p>{frozen.caption && <p className="whitespace-pre-wrap">{frozen.caption}</p>}
            <p className="text-sm">الإفصاح: {({ none: "غير تجاري", own_business: "نشاطي التجاري", paid_partnership: "شراكة مدفوعة" })[frozen.disclosure]} · فيديو بالذكاء الاصطناعي: {frozen.is_aigc ? "نعم" : "لا"}</p>
            {proposal.status === "previewed" && !initialized.current && <><label className="flex gap-2 text-sm"><input aria-label="اعتماد رفع ملف TikTok" type="checkbox" disabled={busy} checked={approved} onChange={(e) => setApproved(e.target.checked)} />أعتمد إرسال هذا الفيديو بإعداداته المعروضة إلى TikTok مرة واحدة.</label><button type="button" className={BUTTON} disabled={busy || !approved} onClick={execute}>اعتماد رفع الملف ونشره</button></>}
            <button type="button" className={BUTTON} disabled={busy} onClick={() => work(async (options) => { const { data } = await api.post(`${ROOT}/proposals/${proposal.proposal_id}/status`, {}, options); initialized.current = data.status !== "previewed"; receive(data); })}>تحديث حالة رفع الملف</button>
            {proposal.status === "awaiting_upload" && <button type="button" className={BUTTON} disabled={busy || !descriptor || descriptor.file_manifest_sha256 !== frozen.file_manifest_sha256} onClick={resume}>استكمال نفس عملية الرفع</button>}
            {(['failed','blocked','published_public','published_private','draft_delivered'].includes(proposal.status) || (proposal.status === 'previewed' && !initialized.current)) && <button type="button" className={BUTTON} disabled={busy} onClick={() => { setProposal(null); initialized.current = false; requestKey.current = null; setApproved(false); }}>تجهيز معاينة جديدة</button>}
            {proposal.uploaded_bytes != null && <p className="text-sm">استلم TikTok {proposal.uploaded_bytes} من {frozen.file_size} بايت.</p>}{proposal.public_post_ids?.length > 0 && <p className="break-all text-sm">معرّف المنشور العام: {proposal.public_post_ids.join('، ')}</p>}
        </div>}
        {busy && <button type="button" className={BUTTON} onClick={() => controller.current?.abort()}>إيقاف النقل؛ ثم حدّث حالة العملية</button>}
        <button type="button" className={BUTTON} disabled={busy} onClick={() => work(async (options) => { const { data } = await api.get(`${ROOT}/proposals`, options); if (mounted.current) setHistory(data.items || []); })}>سجل رفع الملفات</button>
        {history.map((row) => <button key={row.proposal_id} className={`${BUTTON} block w-full text-right`} type="button" disabled={busy} onClick={() => work(async (options) => { const { data } = await api.get(`${ROOT}/proposals/${row.proposal_id}`, options); initialized.current = data.status !== "previewed"; receive(data); })}>{row.input.file_name} · {STATUS[row.status] || "راجع الحالة"} · {new Date(row.created_at).toLocaleString("ar-SA", { timeZone: "Asia/Riyadh" })}</button>)}
    </section>;
}
