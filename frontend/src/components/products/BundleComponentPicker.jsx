import { useId, useState } from "react";

export default function BundleComponentPicker({ query, onQuery, items, selected, onSelect, loading, error }) {
    const id = useId();
    const [open, setOpen] = useState(false);
    const [active, setActive] = useState(-1);
    const selectedItem = items.find((item) => String(item.mezan_product_id || item.id) === selected);
    const choose = (item) => { onSelect(String(item.mezan_product_id || item.id)); setOpen(false); setActive(-1); };
    const available = !loading && !error ? items : [];
    return <div className="relative min-w-0" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
        <label htmlFor={id}>بحث واختيار المكوّن</label>
        <input id={id} role="combobox" autoComplete="off" aria-expanded={open} aria-controls={`${id}-results`} aria-autocomplete="list" aria-activedescendant={open && active >= 0 && available[active] ? `${id}-${active}` : undefined}
            className="w-full rounded-lg border p-2 text-sm" placeholder="ابحث باسم المنتج أو رمزه" value={selectedItem ? selectedItem.name : query}
            onFocus={() => setOpen(true)} onChange={(event) => { onQuery(event.target.value); setOpen(true); setActive(-1); }}
            onKeyDown={(event) => {
                if (event.key === "Escape") { setOpen(false); return; }
                if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setOpen(true); setActive((current) => Math.max(0, Math.min(available.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)))); }
                if (event.key === "Enter" && open) { event.preventDefault(); if (available[active]) choose(available[active]); }
            }} />
        {open && <div className="absolute inset-x-0 top-full z-30 mt-1 max-h-72 overflow-y-auto rounded-xl border bg-white shadow-lg">
            {loading ? <p role="status" className="p-3 text-sm">جارٍ البحث…</p> : error ? <p role="alert" className="p-3 text-sm text-red-700">{error}</p> : !items.length ? <p role="status" className="p-3 text-sm">لا توجد نتائج مطابقة. جرّب اسمًا أو رمزًا آخر.</p> : null}
            <ul id={`${id}-results`} role="listbox" aria-label="نتائج المكونات">
                {available.map((item, index) => <li key={item.mezan_product_id || item.id} id={`${id}-${index}`} role="option" aria-selected={selected === String(item.mezan_product_id || item.id)}
                    className={`flex cursor-pointer items-center gap-3 p-3 text-sm hover:bg-violet-50 ${active === index ? "bg-violet-50" : ""}`}
                    onMouseDown={(event) => event.preventDefault()} onClick={() => choose(item)}>
                    <span className="relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border bg-slate-50 text-xs text-slate-500">بدون صورة{item.main_image && <img src={item.main_image} alt="" className="absolute inset-0 h-full w-full bg-white object-cover" onError={(event) => { event.currentTarget.style.display = "none"; }} />}</span>
                    <span className="min-w-0"><span className="block break-words font-medium">{item.name}</span><span className="block text-xs text-slate-500" dir="ltr">{item.sku || item.code || ""}</span></span>
                </li>)}
            </ul>
        </div>}
    </div>;
}
