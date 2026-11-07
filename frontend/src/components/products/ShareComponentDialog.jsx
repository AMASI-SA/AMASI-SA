import { useMemo, useRef, useState } from "react";
import { resourceKind } from "./resourceKind";

export default function ShareComponentDialog({ categories, resources, categoryId, categoryName, busy, onClose, onSave }) {
    const [sourceId, setSourceId] = useState("");
    const [resourceId, setResourceId] = useState("");
    const saving = useRef(false);
    const options = useMemo(() => (resources || []).filter(row => resourceKind(row) === "component"
        && row.status !== "inactive" && row.is_active !== false && !row.linked_to_product
        && row.available_for_product_link !== false && !(row.option_links || []).length
        && (row.category_ids || []).map(String).includes(sourceId)), [resources, sourceId]);
    const save = async () => {
        if (busy || saving.current || !sourceId || !options.some(row => row.id === resourceId)) return;
        saving.current = true;
        try { await onSave(resourceId, sourceId); } finally { saving.current = false; }
    };
    return <div className="fixed inset-0 z-[160] flex items-center justify-center bg-slate-950/50 p-4" dir="rtl">
        <section role="dialog" aria-modal="true" aria-labelledby="share-component-title" className="w-full max-w-lg space-y-4 rounded-3xl bg-white p-6 shadow-2xl">
            <h3 id="share-component-title" className="text-lg font-black">إضافة مكوّن من تصنيف آخر</h3>
            <p className="text-sm leading-6 text-slate-600">عند الحفظ يُربط المكوّن بهذا المنتج ويُشارك مع تصنيف «{categoryName}»، مع الاحتفاظ بتصنيفاته الحالية.</p>
            <label className="block text-sm font-bold">التصنيف الآخر<select aria-label="التصنيف الآخر" value={sourceId} disabled={busy}
                onChange={event => { setSourceId(event.target.value); setResourceId(""); }} className="mt-2 w-full rounded-xl border p-3">
                <option value="">اختر التصنيف</option>{(categories || []).filter(row => String(row.id) !== String(categoryId) && row.status !== "inactive")
                    .map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
            </select></label>
            <label className="block text-sm font-bold">مكوّنات التصنيف<select aria-label="مكوّنات التصنيف" value={resourceId} disabled={busy || !sourceId}
                onChange={event => setResourceId(event.target.value)} className="mt-2 w-full rounded-xl border p-3">
                <option value="">اختر المكوّن</option>{options.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
            </select></label>
            {sourceId && !options.length && <p role="status" className="text-sm text-slate-500">لا توجد مكوّنات متاحة للإضافة من هذا التصنيف.</p>}
            <div className="flex justify-end gap-2"><button type="button" disabled={busy} onClick={onClose} className="rounded-xl border px-4 py-3 font-bold">إلغاء</button>
                <button type="button" disabled={busy || !resourceId || !options.some(row => row.id === resourceId)} onClick={save}
                    className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : "حفظ ومشاركة المكوّن"}</button></div>
        </section>
    </div>;
}
