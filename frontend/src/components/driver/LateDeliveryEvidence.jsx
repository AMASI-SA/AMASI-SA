import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const BASE = "/store-delivery/evidence/late-delivery";
const requestId = () => globalThis.crypto?.randomUUID?.() || `late-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const failure = error => error?.response?.data?.detail?.code || "تعذر حفظ المرفق؛ يمكنك إعادة المحاولة بنفس الملف والسبب.";
const stateLabel = state => ({ pending: "بانتظار المراجعة", approved: "تم قبول المرفق", rejected: "تم رفض المرفق" }[state] || "حالة المراجعة غير متاحة");

export default function LateDeliveryEvidence({ assignment }) {
    const [items, setItems] = useState([]), [reason, setReason] = useState(""), [file, setFile] = useState(null);
    const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
    const attempt = useRef(null), inFlight = useRef(false), fileInput = useRef(null);
    const eligible = assignment?.status === "delivered" && Boolean(assignment?.id);
    useEffect(() => {
        let active = true;
        setItems([]); setError(""); setNotice(""); setReason(""); setFile(null); attempt.current = null;
        if (eligible) api.get(BASE, { params: { assignment_id: assignment.id } }).then(({ data }) => {
            if (active) setItems(data?.items || []);
        }).catch(() => { if (active) setError("تعذر تحميل المرفقات السابقة؛ أعد فتح الشحنة للمحاولة."); });
        return () => { active = false; };
    }, [assignment?.id, eligible]);
    if (!eligible) return null;
    async function upload(event) {
        event.preventDefault();
        if (inFlight.current || !file || reason.trim().length < 3) return;
        if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 8 * 1024 * 1024) {
            setError("اختر صورة JPG أو PNG أو WebP بحجم لا يتجاوز 8 ميجابايت."); return;
        }
        const value = reason.trim();
        if (!attempt.current || attempt.current.file !== file || attempt.current.reason !== value) {
            attempt.current = { file, reason: value, id: requestId() };
        }
        const body = new FormData();
        body.append("assignment_id", assignment.id); body.append("request_id", attempt.current.id);
        body.append("reason", value); body.append("file", file);
        inFlight.current = true; setBusy(true); setError(""); setNotice("");
        try {
            const { data } = await api.post(BASE, body, { headers: { "Content-Type": "multipart/form-data" } });
            if (!data?.attachment?.id) throw new Error("invalid_attachment_response");
            setItems(current => [data, ...current.filter(row => row.attachment.id !== data.attachment.id)]);
            setNotice("تم إرفاق الدليل للمراجعة فقط. لم تتغير حالة التسوية أو أي أرصدة.");
            setFile(null); setReason(""); attempt.current = null;
            if (fileInput.current) fileInput.current.value = "";
        } catch (err) { setError(failure(err)); }
        finally { inFlight.current = false; setBusy(false); }
    }
    return <section className="mt-4 space-y-3 rounded-2xl border border-sky-200 bg-sky-50 p-4" aria-label="مرفقات لاحقة للتوصيل">
        <h3 className="font-black">إرفاق دليل بعد التوصيل</h3>
        <p className="text-sm">يُحفظ المرفق بتاريخ إضافته دون تغيير دليل التوصيل الأصلي أو وقته. قبول المرفق لا يعني تسوية التحصيل أو ترحيل مبلغ.</p>
        <form onSubmit={upload} className="space-y-3">
            <label className="block">سبب الإضافة<textarea disabled={busy} value={reason} onChange={event => setReason(event.target.value)} className="mt-1 w-full rounded-xl border p-2" /></label>
            <label className="block">ملف الدليل اللاحق<input ref={fileInput} disabled={busy} type="file" accept="image/jpeg,image/png,image/webp" onChange={event => setFile(event.target.files?.[0] || null)} className="mt-1 block w-full" /></label>
            <button disabled={busy || !file || reason.trim().length < 3} className="rounded-xl bg-sky-800 px-4 py-2 font-bold text-white disabled:opacity-40">{busy ? "جاري الإرفاق…" : "إرفاق للمراجعة"}</button>
        </form>
        {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
        {items.map(row => <article key={row.attachment.id} className="rounded-xl border bg-white p-3 text-sm">
            <strong>{stateLabel(row.state)}</strong><p>وقت التوصيل الأصلي: <bdi>{row.attachment.source?.delivered_at || "غير متاح"}</bdi></p>
            <p>وقت إرفاق الدليل اللاحق: <bdi>{row.attachment.attached_at || row.attachment.uploaded_at || "غير متاح"}</bdi></p>
            {row.decision?.note && <p>ملاحظة المراجع: {row.decision.note}</p>}
        </article>)}
    </section>;
}
