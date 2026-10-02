import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const BASE = "/accounting-module/shipping-v2/late-delivery-evidence";
const requestId = () => globalThis.crypto?.randomUUID?.() || `review-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const label = state => ({ pending: "بانتظار المراجعة", approved: "المرفق مقبول", rejected: "المرفق مرفوض" }[state] || "الحالة غير متاحة");

export default function LateDeliveryEvidenceReview({ accountingPermissions = [] }) {
    const canView = accountingPermissions.includes("accounting.shipping.view");
    const canReview = accountingPermissions.includes("accounting.shipping.contracts.review");
    const [items, setItems] = useState([]), [error, setError] = useState(""), [notice, setNotice] = useState("");
    const [busy, setBusy] = useState(""), [notes, setNotes] = useState({}), [originals, setOriginals] = useState({});
    const [revision, setRevision] = useState(0);
    const attempts = useRef({}), urls = useRef([]), inFlight = useRef(false);
    useEffect(() => () => { urls.current.forEach(url => URL.revokeObjectURL(url)); }, []);
    useEffect(() => {
        let active = true; setItems([]); setError("");
        if (canView) api.get(BASE).then(({ data }) => { if (active) setItems(data?.items || []); })
            .catch(() => { if (active) setError("تعذر تحميل أدلة التوصيل اللاحقة."); });
        return () => { active = false; };
    }, [canView, revision]);
    if (!canView) return null;
    async function original(id) {
        if (!canReview || inFlight.current) return;
        inFlight.current = true; setBusy(id); setError("");
        try {
            const { data } = await api.get(`${BASE}/${encodeURIComponent(id)}/original`, { responseType: "blob" });
            if (!(data instanceof Blob) || !data.size) throw new Error("empty_original");
            const url = URL.createObjectURL(data); urls.current.push(url);
            setOriginals(current => ({ ...current, [id]: { url, type: data.type } }));
        } catch (err) { setError(err?.response?.data?.detail?.code || "تعذر عرض الملف الأصلي؛ المراجعة غير متاحة حتى عرضه."); }
        finally { inFlight.current = false; setBusy(""); }
    }
    async function review(row, decision) {
        const id = row.attachment.id;
        if (!canReview || !originals[id] || row.state !== "pending" || inFlight.current) return;
        const note = (notes[id] || "").trim();
        if (note.length < 3) return;
        const key = JSON.stringify([id, decision, note]);
        if (!attempts.current[key]) attempts.current[key] = requestId();
        inFlight.current = true; setBusy(id); setError(""); setNotice("");
        try {
            const { data } = await api.post(`${BASE}/${encodeURIComponent(id)}/review`, { request_id: attempts.current[key], decision, note });
            if (data?.attachment?.id !== id || data.state !== decision) throw new Error("invalid_review_response");
            setItems(current => current.map(item => item.attachment.id === id ? data : item));
            setNotice(decision === "approved" ? "تم قبول المرفق فقط؛ لم تُسجّل تسوية أو حركة مالية." : "تم رفض المرفق فقط؛ لم يتغير سجل التوصيل الأصلي.");
        } catch (err) { setError(err?.response?.data?.detail?.code || "تعذر حفظ المراجعة؛ يمكنك إعادة المحاولة دون تكرار القرار."); }
        finally { inFlight.current = false; setBusy(""); }
    }
    return <section className="space-y-3 rounded-2xl border bg-white p-5" aria-label="مراجعة مرفقات التوصيل اللاحقة">
        <h2 className="text-lg font-black">مراجعة مرفقات التوصيل اللاحقة</h2>
        <p>الإضافة اللاحقة لا تغيّر دليل التوصيل الأصلي أو توقيته. قبولها أو رفضها لا يرحّل قيدًا ولا يسوّي التحصيل.</p>
        <button disabled={Boolean(busy)} onClick={() => setRevision(value => value + 1)} className="rounded-xl border px-3 py-2">تحديث المرفقات</button>
        {!canReview && <p role="status">صلاحية مراجعة عقود الشحن مطلوبة لقبول المرفق أو رفضه.</p>}
        {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
        {items.map(row => { const attachment = row.attachment, file = originals[attachment.id]; return <article key={attachment.id} className="space-y-2 rounded-xl border p-4">
            <h3 className="font-bold">طلب #{attachment.order_number || attachment.order_id} — {label(row.state)}</h3>
            <p>الموصل: <bdi>{attachment.driver_id}</bdi></p>
            <p>وقت التوصيل الأصلي: <bdi>{attachment.source?.delivered_at || "غير متاح"}</bdi></p>
            <p>وقت إرفاق الدليل اللاحق: <bdi>{attachment.attached_at || attachment.uploaded_at || "غير متاح"}</bdi></p>
            {canReview && <button disabled={Boolean(busy)} onClick={() => original(attachment.id)} className="rounded-xl border px-3 py-2">عرض الملف الأصلي</button>}
            {file && <div><a href={file.url} target="_blank" rel="noreferrer">فتح المرفق الأصلي</a>{file.type.startsWith("image/") && <img src={file.url} alt="دليل التوصيل المرفق لاحقًا" className="max-h-96 max-w-full" />}</div>}
            {row.decision && <p>قرار المراجع: {row.decision.note || label(row.state)} — <bdi>{row.decision.reviewed_at}</bdi></p>}
            {canReview && row.state === "pending" && <div className="space-y-2"><label htmlFor={`late-evidence-note-${attachment.id}`} className="block">ملاحظة المراجعة</label><textarea id={`late-evidence-note-${attachment.id}`} disabled={Boolean(busy)} value={notes[attachment.id] || ""} onChange={event => setNotes(current => ({ ...current, [attachment.id]: event.target.value }))} className="block w-full rounded-xl border p-2" />
                {!file && <p>اعرض الملف الأصلي قبل تسجيل القرار.</p>}
                <button disabled={Boolean(busy) || !file || (notes[attachment.id] || "").trim().length < 3} onClick={() => review(row, "approved")} className="ml-2 rounded-xl bg-emerald-700 px-4 py-2 text-white disabled:opacity-40">قبول المرفق</button>
                <button disabled={Boolean(busy) || !file || (notes[attachment.id] || "").trim().length < 3} onClick={() => review(row, "rejected")} className="rounded-xl border border-rose-300 px-4 py-2 disabled:opacity-40">رفض المرفق</button>
            </div>}
        </article>; })}
        {!items.length && !error && <p>لا توجد مرفقات معروضة للمراجعة.</p>}
    </section>;
}
