import { useRef, useState } from "react";
import api from "../../lib/api";

const ROOT = "/integrations-v2/tiktok/content/comment-replies";
const LABELS = {
    previewed: "معاينة جاهزة للاعتماد", sending: "بدأ الإرسال؛ لا تكرره",
    accepted: "استلم TikTok الرد؛ ظهوره للعامة لم يُثبت بعد",
    published_public: "تأكد ظهور الرد للعامة على TikTok",
    hidden: "الرد موجود لدى TikTok لكنه مخفي عن العامة",
    uncertain: "نتيجة الإرسال غير مؤكدة؛ راجع التعليق في TikTok ولا تكرر الرد",
    local_uncertain: "انقطع تأكيد العملية؛ افتح سجل الردود ولا تكرر الإرسال",
};
const button = "min-h-11 rounded-lg border border-violet-300 bg-white px-3 py-2 text-sm font-bold disabled:opacity-50";
const message = (error) => typeof error?.response?.data?.detail?.message === "string"
    ? error.response.data.detail.message : "تعذر إكمال التحقق. راجع سجل الردود قبل أي إعادة إرسال.";

export function TikTokReplyHistory({ conversationId }) {
    const [items, setItems] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const lock = useRef(false);
    const run = async (action) => {
        if (lock.current) return;
        lock.current = true; setBusy(true); setError("");
        try { await action(); } catch (failure) { setError(message(failure)); }
        finally { lock.current = false; setBusy(false); }
    };
    return <section className="mt-3 rounded-xl border border-slate-200 p-3" aria-label="سجل ردود تيك توك">
        <button type="button" className={button} disabled={busy} onClick={() => run(async () => {
            const result = await api.post(`${ROOT}/history`, { conversation_id: conversationId }, { timeout: 10000 });
            setItems(result.data.items);
        })}>سجل ردود تيك توك</button>
        {items?.length === 0 && <p>لا توجد عمليات رد محفوظة لهذه المحادثة.</p>}
        {items?.map((item) => <article key={item.proposal_id} className="mt-3 rounded border p-3">
            <p>{item.text}</p><p role="status">{LABELS[item.status] || "نتيجة غير مؤكدة"}</p>
            {["accepted", "published_public", "hidden", "sending", "uncertain"].includes(item.status) && <button type="button" className={button} disabled={busy}
                onClick={() => run(async () => {
                    const result = await api.post(`${ROOT}/${item.proposal_id}/status`, {}, { timeout: 60000 });
                    setItems((current) => current.map((row) => row.proposal_id === item.proposal_id ? result.data : row));
                })}>التحقق من ظهور الرد</button>}
        </article>)}
        {error && <p role="alert">{error}</p>}
    </section>;
}

export default function TikTokReplyDelivery({ suggestion, text, disabled = false, onConsumed }) {
    const [proposal, setProposal] = useState(null);
    const [approved, setApproved] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const lock = useRef(false);
    const consumed = useRef(false);
    const run = async (sending) => {
        if (lock.current || disabled || consumed.current) return;
        lock.current = true; setBusy(true); setError("");
        if (sending) { consumed.current = true; onConsumed?.(); }
        try {
            const result = sending
                ? await api.post(`${ROOT}/${proposal.proposal_id}/execute`, { confirmation_digest: proposal.confirmation_digest }, { timeout: 60000 })
                : await api.post(`${ROOT}/preview`, { conversation_id: suggestion.conversation_id, suggestion_id: suggestion.id,
                    version: suggestion.version, text: text.trim() }, { timeout: 60000 });
            setProposal(result.data); setApproved(false);
        } catch (failure) {
            setError(message(failure));
            if (sending) setProposal((current) => ({ ...current, status: "local_uncertain" }));
        } finally { lock.current = false; setBusy(false); }
    };
    const matches = proposal?.status === "previewed" && proposal.text === text.trim();
    const validText = text.trim().length > 0 && Array.from(text.trim()).length <= 1200;
    return <section className="mt-3 rounded-xl border border-violet-300 p-3" aria-label="إرسال رد عام إلى تيك توك">
        <p className="text-xs leading-6">معاينة الرد تتحقق من صلاحية إدارة التعليقات وملكية الفيديو والتعليق الحالي. الإرسال ينشر النص علنًا باسم حساب متجرك، ولا يشغّل الرد التلقائي.</p>
        <button type="button" className={button} disabled={disabled || busy || consumed.current || !validText}
            onClick={() => run(false)}>معاينة الرد على تيك توك</button>
        {!validText && <p>اكتب ردًا بين حرف واحد و1200 حرف.</p>}
        {proposal && <div className="mt-3 rounded border bg-white p-3">
            <p>التعليق الأصلي: {proposal.source_text}</p><p>الرد المعتمد للإرسال: {proposal.text}</p>
            <p role="status">{LABELS[proposal.status] || "نتيجة غير مؤكدة"}</p>
            {proposal.status === "previewed" && <>
                {!matches && <p>تغير النص؛ أعد المعاينة قبل الاعتماد.</p>}
                <label className="mt-2 flex gap-2"><input type="checkbox" checked={approved && matches} disabled={busy || disabled || !matches}
                    onChange={(event) => setApproved(event.target.checked)} />أعتمد نشر هذا النص كـرد عام على التعليق الموضح</label>
                <button type="button" className={button} disabled={busy || disabled || !approved || !matches || consumed.current}
                    onClick={() => run(true)}>اعتماد وإرسال الرد إلى تيك توك</button>
            </>}
        </div>}
        {error && <p role="alert">{error}</p>}
    </section>;
}
