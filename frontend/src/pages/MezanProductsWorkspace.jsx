import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowsClockwise, Cube, FloppyDisk, ImageSquare, MagnifyingGlass,
    Package, PencilSimple, SlidersHorizontal, SortAscending, SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import ProductOptionCostEditor from "../components/products/ProductOptionCostEditor";
import {
    applyMissingSkus, getProductV2Costs, getProductsV2Summary,
    listWorkspaceProducts, previewMissingSkus, refreshProductV2Details,
    saveProductV2Costs, syncProductsV2,
} from "../services/mezanProductsV2";

const STATUS_FILTERS = [["", "كل المنتجات"], ["active", "منتجات للبيع"], ["inactive", "منتجات مخفية"], ["out_of_stock", "منتجات نفدت"]];

function money(value) {
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function statusLabel(value) {
    return ({ active: "نشط", out_of_stock: "نفد", inactive: "مخفي" })[value] || value || "غير معروف";
}

function ProductThumb({ product, size = "md" }) {
    const classes = size === "lg" ? "h-28 w-28" : "h-14 w-14";
    return product?.main_image
        ? <img src={product.main_image} alt={product.name || ""} className={`${classes} rounded-xl border border-slate-200 object-cover`} />
        : <div className={`${classes} flex items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-300`}><Package size={size === "lg" ? 38 : 24} /></div>;
}

function DescriptionPreview({ html }) {
    if (!html) return <p className="text-sm text-slate-400">لا يوجد وصف.</p>;
    return <iframe title="معاينة وصف المنتج" sandbox="" srcDoc={`<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;line-height:1.8;padding:12px;color:#172033}img{max-width:100%;height:auto}table{max-width:100%;border-collapse:collapse}td,th{border:1px solid #ddd;padding:5px}</style></head><body>${html}</body></html>`} className="h-[360px] w-full rounded-xl border border-slate-200 bg-white" />;
}

function SkuModal({ preview, busy, onClose, onApply }) {
    if (!preview) return null;
    return <div className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/45 p-4" dir="rtl"><div className="max-h-[85vh] w-full max-w-3xl overflow-hidden rounded-3xl bg-white shadow-2xl"><div className="flex items-center justify-between border-b px-6 py-4"><div><h3 className="text-lg font-black">توليد SKU تلقائي</h3><p className="text-xs text-slate-500">ناقص SKU: {preview.missing_total}</p></div><button onClick={onClose}>✕</button></div><div className="max-h-[55vh] overflow-auto p-5 space-y-2">{(preview.items || []).map((row) => <div key={row.salla_product_id} className="flex items-center justify-between rounded-xl border p-3"><span className="font-bold">{row.name}</span><span className="num font-black text-violet-800">{row.proposed_sku}</span></div>)}</div><div className="flex justify-end border-t p-4"><button disabled={busy || !(preview.items || []).length} onClick={onApply} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">{busy ? "جارٍ التحديث…" : "اعتماد"}</button></div></div></div>;
}

export default function MezanProductsWorkspace() {
    const [items, setItems] = useState([]);
    const [summary, setSummary] = useState(null);
    const [pagination, setPagination] = useState({ page: 1, total: 0, total_pages: 1 });
    const [selectedId, setSelectedId] = useState("");
    const [selected, setSelected] = useState(null);
    const [costs, setCosts] = useState({ base_cost: "", variant_costs: {}, notes: "" });
    const [query, setQuery] = useState("");
    const [appliedQuery, setAppliedQuery] = useState("");
    const [status, setStatus] = useState("");
    const [sort, setSort] = useState("newest");
    const [missingSku, setMissingSku] = useState(false);
    const [loading, setLoading] = useState(true);
    const [detailLoading, setDetailLoading] = useState(false);
    const [costSaving, setCostSaving] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [skuPreview, setSkuPreview] = useState(null);
    const [skuBusy, setSkuBusy] = useState(false);
    const [error, setError] = useState("");

    const load = useCallback(async ({ page = 1, search = appliedQuery } = {}) => {
        setLoading(true); setError("");
        try {
            const [listResult, summaryResult] = await Promise.all([
                listWorkspaceProducts({ page, perPage: 30, query: search, status, sort, missingSku }),
                getProductsV2Summary(),
            ]);
            const nextItems = listResult.items || [];
            setItems(nextItems); setPagination(listResult.pagination || { page: 1, total: 0, total_pages: 1 }); setSummary(summaryResult || null);
            if (!selectedId && nextItems[0]) setSelectedId(nextItems[0].mezan_product_id || nextItems[0].id);
        } catch (err) {
            const detail = err?.response?.data?.detail;
            setError((typeof detail === "string" ? detail : detail?.message) || err?.message || "تعذّر تحميل المنتجات.");
        } finally { setLoading(false); }
    }, [appliedQuery, missingSku, selectedId, sort, status]);

    useEffect(() => { load({ page: 1 }); }, [status, sort, missingSku]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => {
        if (!selectedId) return;
        let live = true; setDetailLoading(true);
        Promise.all([refreshProductV2Details(selectedId), getProductV2Costs(selectedId)])
            .then(([detailResult, costResult]) => {
                if (!live) return;
                setSelected(detailResult.product || null);
                setCosts({ base_cost: costResult?.base_cost ?? "", variant_costs: costResult?.variant_costs || {}, notes: costResult?.notes || "" });
            })
            .catch((err) => { if (live) toast.error(err?.response?.data?.detail?.message || "تعذر جلب تفاصيل المنتج"); })
            .finally(() => { if (live) setDetailLoading(false); });
        return () => { live = false; };
    }, [selectedId]);

    const categories = useMemo(() => (selected?.categories || []).map((row) => row.name).filter(Boolean).join("، "), [selected]);
    const media = selected?.images || [];
    const options = selected?.options || [];
    const variants = selected?.variants || [];

    async function syncNow() { setSyncing(true); try { const r = await syncProductsV2(); toast.success(`تمت المزامنة: ${r.seen_products || 0} منتج`); await load({ page: 1 }); } catch { toast.error("تعذرت المزامنة"); } finally { setSyncing(false); } }
    async function saveCosts() { if (!selectedId) return; setCostSaving(true); try { await saveProductV2Costs(selectedId, costs); toast.success("تم حفظ التكلفة الأساسية"); } catch { toast.error("تعذر حفظ التكاليف"); } finally { setCostSaving(false); } }
    async function applySkuBatch() { setSkuBusy(true); try { const r = await applyMissingSkus({ prefix: "AMS", width: 5, limit: 50, confirmation: "تحديث SKU في سلة" }); toast.success(`تم تحديث ${r.succeeded || 0} منتج`); setSkuPreview(null); await load({ page: 1 }); } finally { setSkuBusy(false); } }

    return <div className="space-y-4" dir="rtl">
        <SkuModal preview={skuPreview} busy={skuBusy} onClose={() => setSkuPreview(null)} onApply={applySkuBatch} />
        <section className="rounded-3xl border bg-white p-4 shadow-sm"><div className="flex flex-col gap-3 xl:flex-row"><form onSubmit={(e) => { e.preventDefault(); setAppliedQuery(query.trim()); load({ page: 1, search: query.trim() }); }} className="relative flex-1"><MagnifyingGlass className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="ابحث بالاسم أو SKU أو الباركود…" className="w-full rounded-2xl border py-3 pr-11 pl-4" /></form><select value={sort} onChange={(e) => setSort(e.target.value)} className="rounded-xl border px-3"><option value="newest">المضافة حديثًا</option><option value="oldest">الأقدم</option><option value="name">الاسم</option><option value="price_high">السعر الأعلى</option><option value="price_low">السعر الأقل</option></select><button onClick={() => setMissingSku((v) => !v)} className="rounded-xl border px-4">بدون SKU</button><button onClick={async () => setSkuPreview(await previewMissingSkus({ limit: 50 }))} className="rounded-xl border border-violet-300 px-4 text-violet-800">توليد SKU</button><button onClick={syncNow} disabled={syncing} className="rounded-xl bg-violet-700 px-4 text-white">{syncing ? <SpinnerGap className="animate-spin" /> : <ArrowsClockwise />} مزامنة سلة</button></div></section>
        {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-rose-800"><WarningCircle className="inline" /> {error}</div>}
        <section className="grid min-h-[820px] gap-4 xl:grid-cols-[220px_420px_minmax(0,1fr)]">
            <aside className="rounded-3xl border bg-white p-4"><h2 className="mb-4 font-black"><SlidersHorizontal className="inline" /> تصفية المنتجات</h2>{STATUS_FILTERS.map(([v, l]) => <button key={v} onClick={() => setStatus(v)} className={`mb-1 w-full rounded-xl px-3 py-3 text-right ${status === v ? "bg-violet-700 text-white" : "hover:bg-slate-50"}`}>{l}</button>)}<div className="mt-4 text-xs text-slate-500">إجمالي V2: {summary?.total || 0}</div></aside>
            <div className="overflow-hidden rounded-3xl border bg-white"><div className="flex justify-between border-b p-4"><div><h2 className="font-black">المنتجات</h2><p className="text-xs text-slate-500">{pagination.total} منتج</p></div><SortAscending /></div><div className="max-h-[760px] overflow-auto">{loading ? <div className="flex h-72 items-center justify-center"><SpinnerGap className="animate-spin" /></div> : items.map((product) => { const id = product.mezan_product_id || product.id; return <button key={id} onClick={() => setSelectedId(id)} className={`flex w-full items-center gap-3 border-b p-3 text-right ${selectedId === id ? "bg-emerald-50" : ""}`}><ProductThumb product={product} /><div className="min-w-0 flex-1"><h3 className="truncate font-black">{product.name}</h3><p className="text-xs text-slate-500">{product.sku || "بدون SKU"} · {money(product.sale_price ?? product.price)}</p><span className="text-xs">{statusLabel(product.status)}</span></div></button>; })}</div><div className="flex justify-between border-t p-3"><button disabled={pagination.page <= 1} onClick={() => load({ page: pagination.page - 1 })}>السابق</button><span>{pagination.page} / {pagination.total_pages}</span><button disabled={pagination.page >= pagination.total_pages} onClick={() => load({ page: pagination.page + 1 })}>التالي</button></div></div>
            <main className="rounded-3xl border bg-white p-5">{detailLoading ? <div className="flex h-full items-center justify-center"><SpinnerGap className="animate-spin" /></div> : !selected ? <div className="flex h-full flex-col items-center justify-center text-slate-400"><Cube size={54} /><p>اختر منتجًا</p></div> : <div className="space-y-5"><div className="flex items-center gap-4 border-b pb-5"><ProductThumb product={selected} size="lg" /><div><h1 className="text-xl font-black">{selected.name}</h1><p className="text-xs text-slate-500">Salla #{selected.salla_product_id} · {selected.sku || "بدون SKU"}</p></div></div><section className="rounded-2xl border p-4"><h2 className="mb-3 font-black"><ImageSquare className="inline" /> الصور ({media.length})</h2><div className="grid grid-cols-3 gap-3 sm:grid-cols-5">{media.map((row) => <img key={row.id || row.url} src={row.url} alt={row.alt || ""} className="aspect-square w-full rounded-xl border object-cover" />)}</div></section><section className="rounded-2xl border p-4"><h2 className="mb-4 font-black"><PencilSimple className="inline" /> المعلومات الأساسية</h2><div className="grid gap-4 md:grid-cols-2"><input value={selected.name || ""} readOnly className="rounded-xl border p-3" /><input value={selected.sku || ""} readOnly className="rounded-xl border p-3" /><input value={selected.price ?? ""} readOnly className="rounded-xl border p-3" /><input value={categories} readOnly className="rounded-xl border p-3" /></div></section><section className="rounded-2xl border p-4"><h2 className="mb-3 font-black">وصف المنتج</h2><DescriptionPreview html={selected.description_html || selected.description} /></section><section className="rounded-2xl border p-4"><div className="mb-4 flex justify-between"><div><h2 className="font-black">التكلفة الأساسية</h2><p className="text-xs text-slate-500">تُحتسب دائمًا لكل قطعة.</p></div><button onClick={saveCosts} disabled={costSaving} className="rounded-xl bg-emerald-700 px-4 py-2 text-white"><FloppyDisk className="inline" /> حفظ</button></div><div className="grid gap-4 md:grid-cols-2"><label>تكلفة سلة<input value={selected.cost_price_from_salla ?? ""} readOnly className="mt-1 w-full rounded-xl border bg-slate-50 p-3" /></label><label>تكلفة المنتج في ميزان<input type="number" min="0" step="0.01" value={costs.base_cost} onChange={(e) => setCosts((c) => ({ ...c, base_cost: e.target.value }))} className="mt-1 w-full rounded-xl border border-emerald-300 p-3" /></label></div></section><ProductOptionCostEditor productId={selectedId} options={options} /><section className="rounded-2xl border p-4"><h2 className="mb-3 font-black">المتغيرات ({variants.length})</h2>{!variants.length ? <p className="text-slate-400">لا توجد متغيرات مستقلة.</p> : <div className="overflow-auto"><table className="w-full min-w-[700px] text-right text-xs"><thead><tr><th>المتغير</th><th>SKU</th><th>السعر</th><th>تكلفة ميزان</th></tr></thead><tbody>{variants.map((v) => <tr key={v.id} className="border-t"><td className="p-3">{v.name || v.id}</td><td>{v.sku || "—"}</td><td>{money(v.price)}</td><td><input type="number" min="0" step="0.01" value={costs.variant_costs?.[v.id] ?? ""} onChange={(e) => setCosts((c) => ({ ...c, variant_costs: { ...c.variant_costs, [v.id]: e.target.value } }))} className="w-28 rounded-lg border p-2" /></td></tr>)}</tbody></table></div>}</section></div>}</main>
        </section>
    </div>;
}
