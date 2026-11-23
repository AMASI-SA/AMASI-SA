import { useEffect, useRef, useState } from "react";
import { previewBundleAdoption, applyBundleAdoption, newBundleAdoptionRequestId, bundleAdoptionError } from "../../services/bundleSupplierAdoption";

const money = (value) => value !== null && value !== "" && Number.isFinite(Number(value))
    ? `${Number(value).toLocaleString("ar-SA-u-nu-latn", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`
    : "غير متاح";
const specifications = (value) => Object.values(value || {}).filter((part) => typeof part === "string" || typeof part === "number").join(" · ");

function validPreview(value, orderNumber, lineId) {
    return value?.schema === 1 && value.state === "preview"
        && String(value.order_number) === String(orderNumber) && String(value.order_line_id) === lineId
        && /^[a-f0-9]{64}$/.test(value.preview_hash || "")
        && Number.isInteger(value.commercial_quantity) && value.commercial_quantity > 0
        && Number.isInteger(value.physical_quantity) && value.physical_quantity > 0
        && Array.isArray(value.requirements) && value.requirements.length === value.physical_quantity
        && new Set(value.requirements.map((item) => item.piece_id)).size === value.physical_quantity
        && value.requirements.every((item) => item.piece_id && item.leaf_id && item.unit_cost != null && Number.isFinite(Number(item.unit_cost)))
        && value.expected_total_cost != null && Number.isFinite(Number(value.expected_total_cost));
}

export default function BundleSupplierAdoptionPanel({ orderNumber, items = [], isOwner = false, actorKey, sourceRevision, onApplied }) {
    const choices = items.filter((item) => item.order_item_id != null && String(item.order_item_id).trim());
    const [open, setOpen] = useState(false);
    const [lineId, setLineId] = useState("");
    const [preview, setPreview] = useState(null);
    const [result, setResult] = useState(null);
    const [reason, setReason] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const version = useRef(0);
    const busyRef = useRef(false);
    const requestId = useRef(null);
    const submission = useRef(null);
    const chosenId = lineId || String(choices[0]?.order_item_id || "");

    useEffect(() => {
        version.current += 1;
        busyRef.current = false; requestId.current = null; submission.current = null;
        setPreview(null); setResult(null); setReason(""); setError(""); setBusy(false); setLineId("");
        return () => { version.current += 1; };
    }, [orderNumber, actorKey, sourceRevision, isOwner]);

    function changeLine(value) {
        version.current += 1;
        requestId.current = null; submission.current = null;
        setLineId(value); setPreview(null); setResult(null); setReason(""); setError("");
    }

    async function inspect() {
        if (isOwner !== true || !orderNumber || !chosenId || busyRef.current) return;
        const current = ++version.current;
        busyRef.current = true; setBusy(true); setError(""); setPreview(null); setResult(null);
        requestId.current = null; submission.current = null;
        try {
            const next = await previewBundleAdoption(orderNumber, chosenId);
            if (current !== version.current) return;
            if (!validPreview(next, orderNumber, chosenId)) throw new Error("تعذّر التحقق من معاينة القطع. أعد تحميل الطلب ثم حاول مجددًا.");
            requestId.current = newBundleAdoptionRequestId();
            setPreview(next); setReason("");
        } catch (err) {
            if (current === version.current) setError(bundleAdoptionError(err));
        } finally {
            if (current === version.current) { busyRef.current = false; setBusy(false); }
        }
    }

    async function apply() {
        if (isOwner !== true || busyRef.current || !preview || !requestId.current || reason.trim().length < 3) return;
        const current = version.current;
        const payload = submission.current || {
            order_line_id: preview.order_line_id, preview_hash: preview.preview_hash,
            client_request_id: requestId.current, reason: reason.trim(),
        };
        submission.current = payload;
        busyRef.current = true; setBusy(true); setError("");
        let next;
        let accepted = false;
        try {
            next = await applyBundleAdoption(orderNumber, payload);
            if (current !== version.current) return;
            if (next?.state !== "applied" || !next.adoption_id || !Array.isArray(next.piece_ids)
                || next.piece_ids.length !== preview.physical_quantity
                || new Set(next.piece_ids).size !== preview.physical_quantity
                || next.preview?.preview_hash !== preview.preview_hash) {
                throw new Error("لم يصل تأكيد واضح للتحويل. أعد المحاولة بنفس العملية للتحقق من نتيجتها.");
            }
            accepted = true;
            setResult(next);
        } catch (err) {
            if (current === version.current) {
                setError(bundleAdoptionError(err));
                // A definitive rejection needs a fresh preview. An unknown outcome
                // retains the exact idempotent payload for a deliberate retry.
                const status = err?.response?.status;
                if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
                    setPreview(null); submission.current = null; requestId.current = null;
                }
            }
        } finally {
            if (current === version.current) { busyRef.current = false; setBusy(false); }
        }
        if (accepted && current === version.current) onApplied?.(next);
    }

    if (isOwner !== true || !orderNumber || !choices.length) return null;
    return <section dir="rtl" className="rounded-2xl border border-violet-200 bg-white p-5" data-testid="bundle-adoption-panel">
        <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="font-extrabold text-slate-950">تجهيز المنتج كطقم</h2><p className="mt-1 text-sm text-slate-600">راجع عدد القطع ومواصفات كل قطعة قبل تحويل تجهيز منتج أُرسل للمورد.</p></div>
            {!open && <button type="button" className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-2 text-sm font-bold text-violet-800" data-testid="bundle-adoption-open" onClick={() => setOpen(true)}>مراجعة قطع الطقم</button>}
        </div>
        {open && <div className="mt-4 space-y-4">
            {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
            {result ? <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-900"><p className="font-bold">تم تحويل التجهيز إلى {preview.physical_quantity} قطعة.</p><p className="mt-1 text-sm">تظهر القطع في استلام المورد بمواصفاتها وتكلفتها المتوقعة. يُثبت الاستلام الفعلي عند حفظ فاتورة المورد.</p><a className="mt-3 inline-block font-bold underline" href="/fulfillment-v2?stage=preparation">فتح استلام المورد</a></div> : <>
                <div className="flex flex-wrap items-end gap-3">
                    <label className="min-w-56 flex-1 text-sm font-bold">منتج الطلب<select className="mt-2 block w-full rounded-xl border p-2.5 font-normal" value={chosenId} onChange={(event) => changeLine(event.target.value)} disabled={busy || Boolean(submission.current)}>{choices.map((item) => <option key={item.order_item_id} value={String(item.order_item_id)}>{item.name || item.product_name || "منتج الطلب"}</option>)}</select></label>
                    <button type="button" disabled={busy || Boolean(submission.current)} onClick={inspect} className="rounded-xl border px-4 py-2.5 text-sm font-bold disabled:opacity-50" data-testid="bundle-adoption-preview">{busy && !preview ? "جارٍ فحص القطع…" : "معاينة التحويل"}</button>
                </div>
                {preview && <div className="space-y-4 rounded-xl border bg-slate-50 p-4">
                    <div className="grid gap-3 text-sm sm:grid-cols-3"><p>كمية البيع: <b className="num" data-testid="bundle-adoption-commercial-quantity">{preview.commercial_quantity}</b></p><p>قطع المورد: <b className="num" data-testid="bundle-adoption-physical-quantity">{preview.physical_quantity}</b></p><p>التكلفة المتوقعة: <b className="num" data-testid="bundle-adoption-total">{money(preview.expected_total_cost)}</b></p></div>
                    <p className="text-sm">المورد: <b>{preview.supplier?.name || "المورد المسند"}</b></p>
                    <div className="overflow-x-auto"><table className="w-full text-right text-sm"><thead><tr><th className="p-2">القطعة</th><th className="p-2">المواصفات</th><th className="p-2">تكلفة الوحدة</th></tr></thead><tbody>{preview.requirements.map((item, index) => <tr key={item.piece_id} className="border-t"><td className="p-2">{index + 1}. {item.product_name}</td><td className="p-2">{specifications(item.specifications) || "حسب مواصفات المنتج"}</td><td className="num p-2">{money(item.unit_cost)}</td></tr>)}</tbody></table></div>
                    <label className="block text-sm font-bold">سبب تصحيح التجهيز<textarea className="mt-2 block w-full rounded-xl border bg-white p-3 font-normal" rows={2} maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} disabled={busy || Boolean(submission.current)} placeholder="مثال: العميل اختار الطقم المكوّن من قطعتين" /></label>
                    <button type="button" disabled={busy || reason.trim().length < 3} onClick={apply} className="rounded-xl bg-violet-700 px-5 py-2.5 text-sm font-bold text-white disabled:opacity-50" data-testid="bundle-adoption-apply">{busy ? "جارٍ حفظ التحويل…" : submission.current ? "إعادة التحقق من التحويل" : "تأكيد تحويل التجهيز"}</button>
                </div>}
            </>}
        </div>}
    </section>;
}
