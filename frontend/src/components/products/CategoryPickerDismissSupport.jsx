import { useEffect, useMemo, useRef, useState } from "react";
import { MagnifyingGlass, SpinnerGap } from "@phosphor-icons/react";

export default function CategoryPickerDismissSupport({ value, items, loading, onChange }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");
    const rootRef = useRef(null);
    const selected = String(value || "").split(",").map((row) => row.trim()).filter(Boolean);
    const byId = useMemo(() => Object.fromEntries(items.map((row) => [String(row.id), row])), [items]);
    const visible = items.filter((row) => `${row.name} ${row.path}`.toLowerCase().includes(query.toLowerCase())).slice(0, 100);

    useEffect(() => {
        if (!open) return undefined;
        const closeOutside = (event) => {
            if (!rootRef.current?.contains(event.target)) setOpen(false);
        };
        const closeEscape = (event) => {
            if (event.key === "Escape") setOpen(false);
        };
        document.addEventListener("pointerdown", closeOutside, true);
        document.addEventListener("keydown", closeEscape);
        return () => {
            document.removeEventListener("pointerdown", closeOutside, true);
            document.removeEventListener("keydown", closeEscape);
        };
    }, [open]);

    function toggle(id) {
        const key = String(id);
        const next = selected.includes(key) ? selected.filter((row) => row !== key) : [...selected, key];
        onChange(next.join(","));
    }

    return <div ref={rootRef} className="relative" onMouseLeave={() => open && setOpen(false)}>
        <div className="text-xs font-black text-slate-600">تصنيفات سلة</div>
        <button type="button" aria-expanded={open} onClick={() => setOpen((row) => !row)} className="mt-1 min-h-12 w-full rounded-xl border bg-white p-3 text-right text-sm">
            {!selected.length ? "اختر التصنيفات…" : selected.map((id) => byId[id]?.path || byId[id]?.name || `تصنيف ${id}`).join("، ")}
        </button>
        {open && <div className="absolute z-50 mt-2 w-full overflow-hidden rounded-2xl border bg-white shadow-2xl">
            <label className="flex items-center gap-2 border-b p-3"><MagnifyingGlass className="text-slate-400" /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث باسم التصنيف…" className="min-w-0 flex-1 outline-none" /></label>
            <div className="max-h-72 overflow-auto p-2">
                {loading ? <div className="p-6 text-center"><SpinnerGap className="inline animate-spin" /></div> : visible.map((row) => <label key={row.id} className="flex cursor-pointer items-center gap-3 rounded-xl p-3 hover:bg-slate-50"><input type="checkbox" checked={selected.includes(String(row.id))} onChange={() => toggle(row.id)} /><span><b>{row.name}</b><small className="block text-slate-400">{row.path}</small></span></label>)}
                {!loading && !visible.length && <div className="p-5 text-center text-sm text-slate-400">لا توجد نتيجة.</div>}
            </div>
        </div>}
    </div>;
}
