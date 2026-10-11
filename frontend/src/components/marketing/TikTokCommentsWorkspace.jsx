import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const buttonClass = "min-h-11 rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-extrabold text-slate-800 disabled:opacity-50";
const safeId = (value) => typeof value === "string" && /^[0-9]{1,30}$/.test(value);
const count = (value) => Number.isSafeInteger(value) && value >= 0 && value <= 20;
const cursor = (value) => Number.isSafeInteger(value) && value >= 0;
const readOnly = (data) => data?.receive_only === true && data.provider_write_allowed === false
    && data.ai_auto_reply_allowed === false && data.ai_analysis_requested === false;

export default function TikTokCommentsWorkspace({ creatorRef, accountConnected = false }) {
    const [confirmed, setConfirmed] = useState(false);
    const [connected, setConnected] = useState(false);
    const [posts, setPosts] = useState([]);
    const [postsCursor, setPostsCursor] = useState(null);
    const [video, setVideo] = useState("");
    const [result, setResult] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const active = useRef(null);
    const mounted = useRef(false);
    const locked = useRef(false);
    const validAccount = typeof creatorRef === "string" && /^[a-f0-9]{64}$/.test(creatorRef) && accountConnected;
    const root = `/integrations-v2/tiktok/content/creators/${creatorRef}/comments`;

    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; active.current?.abort(); };
    }, []);

    async function request(call, apply) {
        if (locked.current || !validAccount) return;
        locked.current = true;
        setBusy(true);
        setError("");
        const controller = new AbortController();
        active.current = controller;
        try {
            const response = await call({ signal: controller.signal, timeout: 60000 });
            if (!mounted.current || controller.signal.aborted) return;
            if (!readOnly(response.data)) throw new Error("unproven_read_contract");
            apply(response.data);
        } catch (failure) {
            if (!mounted.current || controller.signal.aborted) return;
            const detail = failure?.response?.data?.detail;
            setError(typeof detail?.message === "string" ? detail.message : "تعذر التحقق من استيراد التعليقات. راجع الربط ثم حاول مجددًا.");
        } finally {
            locked.current = false;
            if (mounted.current && !controller.signal.aborted) setBusy(false);
        }
    }

    function connect() {
        if (!confirmed) return;
        return request((options) => api.post(`${root}/connect`, { confirm_receive_only: true }, options), (data) => {
            if (data.status !== "connected" || data.source !== "owner_initiated_api" || data.creator_ref !== creatorRef) throw new Error("unproven_binding");
            setConnected(true);
        });
    }

    function loadPosts(next = null) {
        return request((options) => api.get(`${root}/posts`, { ...options, params: next === null ? {} : { cursor: next } }), (data) => {
            if (!Array.isArray(data.items) || data.items.length > 20 || typeof data.has_more !== "boolean"
                || (data.has_more && (!cursor(data.next_cursor) || data.next_cursor === 0
                    || (next !== null && data.next_cursor >= next)))) throw new Error("unproven_page");
            const items = data.items.map((row) => {
                if (!safeId(row?.video_id) || row.media_type !== "VIDEO" || typeof row.caption !== "string" || row.caption.length > 120) throw new Error("unproven_post");
                return { video_id: row.video_id, caption: row.caption };
            });
            setPosts(items);
            setPostsCursor(data.has_more ? data.next_cursor : null);
            setVideo("");
            setResult(null);
        });
    }

    function pull(next = 0) {
        if (!safeId(video) || !posts.some((row) => row.video_id === video)) return;
        return request((options) => api.post(`${root}/pull`, { video_id: video, cursor: next }, options), (data) => {
            if (![data.imported, data.duplicates, data.skipped, data.inspected].every(count)
                || data.imported + data.duplicates + data.skipped !== data.inspected
                || typeof data.has_more !== "boolean" || (data.has_more && (!cursor(data.next_cursor) || data.next_cursor <= next))) throw new Error("unproven_pull");
            setResult({ imported: data.imported, duplicates: data.duplicates, skipped: data.skipped,
                has_more: data.has_more, next_cursor: data.has_more ? data.next_cursor : null });
        });
    }

    return <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="tiktok-comments-workspace">
        <div><h3 className="text-lg font-black text-slate-900">تعليقات TikTok ومسودات الردود</h3><p className="mt-2 text-sm leading-6 text-slate-600">استورد التعليقات النصية العامة لفيديوهات حسابك إلى صندوق ميزان، ثم أنشئ مسودات الردود وراجعها هناك. كل طلب يقرأ حتى 20 عنصرًا، دون تحميل صور أو فيديوهات. الرسائل والرد التلقائي لم يُفعّلا بعد.</p></div>
        {!validAccount ? <p className="text-sm font-bold text-amber-800">اختر حساب نشر متصل من القائمة أعلاه.</p> : <>
            {!connected ? <><label className="flex items-start gap-2 text-sm font-semibold leading-6 text-slate-700"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={busy} className="mt-1 h-4 w-4" data-testid="tiktok-comments-confirmation" />أعتمد استيراد التعليقات العامة إلى صندوق متجري في ميزان للقراءة ومسودات الردود فقط.</label><button type="button" disabled={busy || !confirmed} onClick={connect} className={buttonClass} data-testid="tiktok-comments-connect">ربط التعليقات بصندوق ميزان</button></> : <>
                <p className="text-sm font-bold text-emerald-800" data-testid="tiktok-comments-connected">ربط التعليقات جاهز. تبدأ القراءة فقط عند اختيار أحد الأزرار أدناه.</p>
                <div className="flex flex-wrap gap-2"><button type="button" disabled={busy} onClick={() => loadPosts()} className={buttonClass} data-testid="tiktok-comments-posts">عرض دفعة الفيديوهات</button>{postsCursor !== null && <button type="button" disabled={busy} onClick={() => loadPosts(postsCursor)} className={buttonClass}>دفعة الفيديوهات التالية</button>}</div>
                {posts.length > 0 ? <label className="block space-y-2 text-sm font-bold text-slate-700">الفيديو<select value={video} onChange={(event) => { setVideo(event.target.value); setResult(null); }} disabled={busy} className="w-full rounded-xl border border-slate-200 px-3 py-2.5" data-testid="tiktok-comments-video"><option value="">اختر فيديو لقراءة تعليقاته</option>{posts.map((row) => <option key={row.video_id} value={row.video_id}>{row.caption || `فيديو ${row.video_id}`}</option>)}</select></label> : <p className="text-sm text-slate-500">تظهر هنا فيديوهات الحساب بعد قراءة دفعة المنشورات. تُستثنى منشورات الصور في هذه المرحلة.</p>}
                <div className="flex flex-wrap gap-2"><button type="button" disabled={busy || !video} onClick={() => pull()} className={buttonClass} data-testid="tiktok-comments-pull">استيراد دفعة التعليقات</button>{result?.has_more && <button type="button" disabled={busy || !video} onClick={() => pull(result.next_cursor)} className={buttonClass}>دفعة التعليقات التالية</button>}</div>
                {result && <p className="rounded-xl bg-emerald-50 p-3 text-sm font-bold leading-6 text-emerald-900" data-testid="tiktok-comments-result">تم استيراد {result.imported} تعليق · موجودة مسبقًا {result.duplicates} · مستثناة {result.skipped}. التعليقات المستثناة تشمل تعليقات صاحب الحساب والصور والتعليقات غير العامة.</p>}
                <p className="text-xs leading-5 text-slate-500">انتظر 15 ثانية بين دفعتين من النوع نفسه. لا يُرسل محتوى التعليقات إلى الذكاء عند الاستيراد؛ إنشاء مسودة بالذكاء إجراء منفصل داخل الصندوق.</p>
                <a href="/customer-intelligence?tab=conversations" target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center font-extrabold text-violet-700 underline">فتح صندوق العملاء ومسودات الردود</a>
            </>}
        </>}
        {error && <p role="alert" className="rounded-xl bg-amber-50 p-3 text-sm font-bold text-amber-900">{error}</p>}
    </section>;
}
