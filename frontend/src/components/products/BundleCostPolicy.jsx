import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const control = "w-full rounded-lg border bg-white p-2 text-sm";
const messages = {
    bundle_reporting_policy_evidence_required: "أدخل مرجعًا يثبت إدراج تكلفة المنتج الأساسية ضمن البكج.",
    bundle_reporting_policy_revision_conflict: "تغير إعداد التكلفة في جلسة أخرى. أعد تحميله قبل الحفظ.",
    bundle_reporting_policy_bundle_revision_conflict: "تغير تركيب البكج. أعد تحميل التركيب قبل حفظ إعداد التكلفة.",
    bundle_reporting_policy_composite_required: "هذا الإعداد متاح للبكج المحفوظ فقط.",
};
const errorMessage = (error) => messages[error?.response?.data?.detail?.code] || "تعذر تحميل إعداد تكلفة البكج أو حفظه. أعد المحاولة.";

export default function BundleCostPolicy({ productId, bundleRevision, disabled = false, onSaved }) {
    const [data, setData] = useState(null);
    const [original, setOriginal] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const request = useRef(0);
    const current = useRef({ productId, bundleRevision, disabled });
    current.current = { productId, bundleRevision, disabled };

    async function load() {
        const token = ++request.current;
        const id = productId;
        const revision = bundleRevision;
        setBusy(true); setError(""); setNotice("");
        try {
            const response = await api.get(`/products-v2/${encodeURIComponent(id)}/bundle-reporting-cost-policy`);
            if (token !== request.current || current.current.productId !== id || current.current.bundleRevision !== revision) return;
            setData(response.data); setOriginal(response.data);
        } catch (err) {
            if (token === request.current) setError(errorMessage(err));
        } finally {
            if (token === request.current) setBusy(false);
        }
    }

    useEffect(() => {
        setData(null); setOriginal(null);
        load();
        return () => { request.current += 1; };
        // Each persisted bundle revision needs a fresh optimistic policy read.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [productId, bundleRevision]);

    const staleBundle = data && data.bundle_revision !== bundleRevision;
    const changed = data && original && (data.include_parent_base !== original.include_parent_base || data.evidence_ref !== original.evidence_ref);
    const canSave = data && !busy && !disabled && !staleBundle && changed && (!data.include_parent_base || data.evidence_ref?.trim());

    async function save() {
        if (!canSave || current.current.disabled) return;
        const token = ++request.current;
        const id = productId;
        const revision = bundleRevision;
        setBusy(true); setError(""); setNotice("");
        let saved = false;
        try {
            const response = await api.put(`/products-v2/${encodeURIComponent(id)}/bundle-reporting-cost-policy`, {
                expected_revision: data.revision,
                expected_bundle_revision: data.bundle_revision,
                include_parent_base: data.include_parent_base,
                evidence_ref: data.evidence_ref.trim(),
            });
            if (token !== request.current || current.current.productId !== id || current.current.bundleRevision !== revision) return;
            saved = true;
            setData(response.data); setOriginal(response.data);
            setNotice("حُفظ إعداد تكلفة البكج للطلبات الجديدة. تكاليف الطلبات السابقة محفوظة.");
            await onSaved?.(response.data);
        } catch (err) {
            if (token === request.current) setError(saved ? "حُفظ إعداد التكلفة، وتعذر تحديث المعاينة. أعد تحميل البكج." : errorMessage(err));
        } finally {
            if (token === request.current) setBusy(false);
        }
    }

    return <section className="space-y-3 rounded-xl border p-3" aria-label="سياسة تكلفة البكج">
        <h3 className="font-bold">التكلفة المالية للبكج</h3>
        <p className="text-sm">إعداد مستقل يضيف تكلفة المنتج الأساسية إلى تكلفة البكج للطلبات الجديدة، مع حفظ تكاليف الطلبات السابقة.</p>
        {disabled && <p role="status" className="text-sm text-amber-800">يتوقف حفظ إعداد التكلفة أثناء وجود تغييرات غير محفوظة في تركيب البكج.</p>}
        {busy && !data && <p role="status">جارٍ تحميل إعداد تكلفة البكج…</p>}
        {error && <p role="alert">{error}<button type="button" disabled={busy || disabled} className="mr-2 underline" onClick={load}>إعادة تحميل إعداد التكلفة</button></p>}
        {notice && <p role="status">{notice}</p>}
        {staleBundle && <p role="alert">تغير تركيب البكج. أعد تحميل التركيب قبل حفظ إعداد التكلفة.</p>}
        {data && <fieldset disabled={busy || disabled || staleBundle} className="space-y-3">
            <label className="flex gap-2"><input type="checkbox" checked={data.include_parent_base} onChange={(event) => setData((value) => ({ ...value, include_parent_base: event.target.checked }))} />احتساب تكلفة المنتج الأساسية ضمن البكج</label>
            <label className="block">مرجع إدراج تكلفة المنتج الأساسية<input className={control} maxLength={1000} value={data.evidence_ref} onChange={(event) => setData((value) => ({ ...value, evidence_ref: event.target.value }))} placeholder="مرجع التكلفة المعتمدة أو توضيح صاحب المتجر" /></label>
            <button type="button" className="rounded-lg bg-violet-700 p-2 text-white disabled:opacity-50" disabled={!canSave} onClick={save}>حفظ إعداد تكلفة البكج</button>
        </fieldset>}
    </section>;
}
