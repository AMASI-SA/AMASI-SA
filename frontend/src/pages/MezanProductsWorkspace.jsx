import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowRight, ArrowsClockwise, Cube, FloppyDisk, MagnifyingGlass,
    Package, SlidersHorizontal, SortAscending, SpinnerGap, WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import ProductControlCenterPanel from "../components/products/ProductControlCenterPanel";
import ProductMediaDraftEditor from "../components/products/ProductMediaDraftEditor";
import ProductOptionCostEditor from "../components/products/ProductOptionCostEditor";
import {
    applyMissingSkus, getProductV2Costs, getProductsV2Summary,
    listWorkspaceProducts, previewMissingSkus, refreshProductV2Details,
    saveProductV2Costs, syncProductsV2,
} from "../services/mezanProductsV2";

const STATUS_FILTERS = [["", "كل المنتجات"], ["active", "منتجات للبيع"], ["inactive", "منتجات مخفية"], ["out_of_stock", "منتجات نفدت"]];
const SELECTED_PRODUCT_KEY = "mezan.products-v2.selected-product";

function initialSelectedProduct() {
    if (typeof window === "undefined") return "";
    const fromUrl = new URLSearchParams(window.location.search).get("product");
    return fromUrl || window.localStorage.getItem(SELECTED_PRODUCT_KEY) || "";
}

function rememberSelectedProduct(id) {
    if (typeof window === "undefined" || !id) return;
    window.localStorage.setItem(SELECTED_PRODUCT_KEY, id);
    const url = new URL(window.location.href);
    url.searchParams.set("product", id);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function money(value) {
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function statusLabel(value) {
    return ({ active: "نشط", out_of_stock: "نفد", inactive: "مخفي" })[value] || value || "غير معروف";
}

function ProductThumb({ product, size = "md" }) {
    const classes = size === "lg" ? "h-20 w-20 sm:h-28 sm:w-28" : "h-12 w-12 sm:h-14 sm:w-14";
    return product?.main_image
        ? <img src={product.main_image} alt={product.name || ""} className={`${classes} shrink-0 rounded-xl border border-slate-200 object-cover`} />
        : <div className={`${classes} flex shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-300`}><Package size={size === "lg" ? 38 : 24} /></div>;
}

function DescriptionPreview({ html }) {
    if (!html) return <p className="text-sm text-slate-400">لا يوجد وصف.</p>;
    return <iframe title="معاينة وصف المنتج" sandbox="" srcDoc={`<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;line-height:1.8;padding:12px;color:#172033}img{max-width:100%;height:auto}table{max-width:100%;border-collapse:collapse}td,th{border:1px solid #ddd;padding:5px}</style></head><body>${html}</body></html>`} className="h-[320px] w-full rounded-xl border border-slate-200 bg-white sm:h-[360px]" />;
}

function SkuModal({ preview, busy, onClose, onApply }) {
    if (!preview) return null;
    return <div className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/45 p-3 sm:p-4" dir="rtl">
        <div className="max-h-[90vh] w-full max-w-3xl overflow-hidden rounded-2xl bg-white shadow-2xl sm:rounded-3xl">
            <div className="flex items-center justify-between border-b px-4 py-3 sm:px-6 sm:py-4"><div><h3 className="font-black sm:text-lg">توليد SKU تلقائي</h3><p className="text-xs text-slate-500">ناقص SKU: {preview.missing_total}</p></div><button onClick={onClose}>✕</button></div>
            <div className="max-h-[60vh] space-y-2 overflow-auto p-3 sm:p-5">{(preview.items || []).map((row) => <div key={row.salla_product_id} className="flex items-center justify-between gap-3 rounded-xl border p-3"><span className="min-w-0 truncate font-bold">{row.name}</span><span className="num shrink-0 font-black text-violet-800">{row.proposed_sku}</span></div>)}</div>
            <div className="flex justify-end border-t p-3 sm:p-4"><button disabled={busy || !(preview.items || []).length} onClick={onApply} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">{busy ? "جارٍ التحديث…" : "اعتماد"}</button></div>
        </div>
    </div>;
}

export default function MezanProductsWorkspace() {
    const [items, setItems] = useState([]);
    const [summary, setSummary] = useState(null);
    const [pagination, setPagination] = useState({ page: 1, total: 0, total_pages: 1 });
    const [selectedId, setSelectedId] = useState(initialSelectedProduct);
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
    const [mobileView, setMobileView] = useState(() => initialSelectedProduct() ? "detail" : "list");
    const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);

    const load = useCallback(async ({ page = 1, search = appliedQuery } = {}) => {
        setLoading(true); setError("");
        try {
            const [listResult, summaryResult] = await Promise.all([
                listWorkspaceProducts({ page, perPage: 30, query: search, status, sort, missingSku }),
                getProductsV2Summary(),
            ]);
            const nextItems = listResult.items || [];
            setItems(nextItems);
            setPagination(listResult.pagination || { page: 1, total: 0, total_pages: 1 });
            setSummary(summaryResult || null);
            if (!selectedId && nextItems[0]) {
                const firstId = nextItems[0].mezan_product_id || nextItems[0].id;
                setSelectedId(firstId);
                rememberSelectedProduct(firstId);
            }
        } catch (err) {
            const detail = err?.response?.data?.detail;
            setError((typeof detail === "string" ? detail : detail?.message) || err?.message || "تعذّر تحميل المنتجات.");
        } finally { setLoading(false); }
    }, [appliedQuery, missingSku, selectedId, sort, status]);

    const loadSelected = useCallback(async () => {
        if (!selectedId) return;
        setDetailLoading(true);
        try {
            const [detailResult, costResult] = await Promise.all([refreshProductV2Details(selectedId), getProductV2Costs(selectedId)]);
            setSelected(detailResult.product || null);
            setCosts({ base_cost: costResult?.base_cost ?? "", variant_costs: costResult?.variant_costs || {}, notes: costResult?.notes || "" });
        } catch (err) { toast.error(err?.response?.data?.detail?.message || "تعذر جلب تفاصيل المنتج"); }
        finally { setDetailLoading(false); }
    }, [selectedId]);

    useEffect(() => { load({ page: 1 }); }, [status, sort, missingSku]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => { loadSelected(); }, [loadSelected]);
    useEffect(() => { if (selectedId) rememberSelectedProduct(selectedId); }, [selectedId]);

    const media = selected?.images || [];
    const options = selected?.options || [];
    const customFields = selected?.custom_fields || [];
    const variants = selected?.variants || [];
    const selectedStatusLabel = useMemo(() => STATUS_FILTERS.find(([value]) => value === status)?.[1] || "كل المنتجات", [status]);

    async function syncNow() {
        setSyncing(true);
        try { const result = await syncProductsV2(); toast.success(`تمت المزامنة: ${result.seen_products || 0} منتج`); await load({ page: pagination.page || 1 }); }
        catch { toast.error("تعذرت المزامنة"); }
        finally { setSyncing(false); }
    }

    async function saveCosts() {
        if (!selectedId) return;
        setCostSaving(true);
        try { await saveProductV2Costs(selectedId, costs); toast.success("تم حفظ تكاليف ميزان المستقلة"); }
        catch { toast.error("تعذر حفظ التكاليف"); }
        finally { setCostSaving(false); }
    }

    async function applySkuBatch() {
        setSkuBusy(true);
        try { const result = await applyMissingSkus({ prefix: "AMS", width: 5, limit: 50, confirmation: "تحديث SKU في سلة" }); toast.success(`تم تحديث ${result.succeeded || 0} منتج`); setSkuPreview(null); await load({ page: pagination.page || 1 }); }
        finally { setSkuBusy(false); }
    }

    function selectProduct(id) {
        setSelectedId(id);
        rememberSelectedProduct(id);
        setMobileView("detail");
        window.setTimeout(() => window.scrollTo({ top: 0, behavior: "smooth" }), 0);
    }

    return <div className="min-w-0 space-y-3 overflow-x-hidden sm:space-y-4" dir="rtl">
        <SkuModal preview={skuPreview} busy={skuBusy} onClose={() => setSkuPreview(null)} onApply={applySkuBatch} />

        <section className="rounded-2xl border bg-white p-3 shadow-sm sm:rounded-3xl sm:p-4">
            <form onSubmit={(event) => { event.preventDefault(); setAppliedQuery(query.trim()); load({ page: 1, search: query.trim() }); }} className="relative">
                <MagnifyingGlass className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث بالاسم أو SKU أو الباركود…" className="w-full rounded-2xl border py-3 pr-11 pl-4" />
            </form>
            <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4 xl:flex">
                <select value={sort} onChange={(event) => setSort(event.target.value)} className="col-span-2 min-w-0 rounded-xl border px-3 py-2 md:col-span-1 xl:w-52"><option value="newest">المضافة حديثًا</option><option value="oldest">الأقدم</option><option value="name">الاسم</option><option value="price_high">السعر الأعلى</option><option value="price_low">السعر الأقل</option></select>
                <button type="button" onClick={() => setMobileFiltersOpen((value) => !value)} className="rounded-xl border px-3 py-2 xl:hidden"><SlidersHorizontal className="ml-1 inline" /> {selectedStatusLabel}</button>
                <button type="button" onClick={() => setMissingSku((value) => !value)} className={`rounded-xl border px-3 py-2 ${missingSku ? "border-violet-500 bg-violet-50 text-violet-800" : ""}`}>بدون SKU</button>
                <button type="button" onClick={async () => setSkuPreview(await previewMissingSkus({ limit: 50 }))} className="rounded-xl border border-violet-300 px-3 py-2 text-violet-800">توليد SKU</button>
                <button type="button" onClick={syncNow} disabled={syncing} className="col-span-2 rounded-xl bg-violet-700 px-3 py-2 text-white md:col-span-1 xl:mr-auto xl:px-4">{syncing ? <SpinnerGap className="inline animate-spin" /> : <ArrowsClockwise className="inline" />} مزامنة سلة</button>
            </div>
            {mobileFiltersOpen && <div className="mt-3 grid grid-cols-2 gap-2 rounded-2xl bg-slate-50 p-2 xl:hidden">{STATUS_FILTERS.map(([value, label]) => <button key={value} type="button" onClick={() => { setStatus(value); setMobileFiltersOpen(false); }} className={`rounded-xl px-3 py-2 text-sm ${status === value ? "bg-violet-700 text-white" : "bg-white"}`}>{label}</button>)}</div>}
        </section>

        {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-rose-800"><WarningCircle className="inline" /> {error}</div>}

        <section className="grid min-w-0 gap-3 lg:grid-cols-[340px_minmax(0,1fr)] xl:min-h-[820px] xl:grid-cols-[220px_420px_minmax(0,1fr)] xl:gap-4">
            <aside className="hidden rounded-3xl border bg-white p-4 xl:block">
                <h2 className="mb-4 font-black"><SlidersHorizontal className="inline" /> تصفية المنتجات</h2>
                {STATUS_FILTERS.map(([value, label]) => <button key={value} onClick={() => setStatus(value)} className={`mb-1 w-full rounded-xl px-3 py-3 text-right ${status === value ? "bg-violet-700 text-white" : "hover:bg-slate-50"}`}>{label}</button>)}
                <div className="mt-4 text-xs text-slate-500">إجمالي V2: {summary?.total || 0}</div>
            </aside>

            <div className={`${mobileView === "detail" ? "hidden lg:block" : "block"} min-w-0 overflow-hidden rounded-2xl border bg-white sm:rounded-3xl`}>
                <div className="flex justify-between border-b p-3 sm:p-4"><div><h2 className="font-black">المنتجات</h2><p className="text-xs text-slate-500">{pagination.total} منتج</p></div><SortAscending /></div>
                <div className="max-h-[calc(100vh-260px)] overflow-auto lg:max-h-[760px]">
                    {loading ? <div className="flex h-72 items-center justify-center"><SpinnerGap className="animate-spin" /></div> : items.map((product) => {
                        const id = product.mezan_product_id || product.id;
                        return <button key={id} onClick={() => selectProduct(id)} className={`flex w-full min-w-0 items-center gap-3 border-b p-3 text-right ${selectedId === id ? "bg-emerald-50" : ""}`}><ProductThumb product={product} /><div className="min-w-0 flex-1"><h3 className="truncate font-black">{product.name}</h3><p className="truncate text-xs text-slate-500">{product.sku || "بدون SKU"} · {money(product.sale_price ?? product.price)}</p><span className="text-xs">{statusLabel(product.status)}</span></div></button>;
                    })}
                </div>
                <div className="flex justify-between border-t p-3 text-sm"><button disabled={pagination.page <= 1} onClick={() => load({ page: pagination.page - 1 })}>السابق</button><span>{pagination.page} / {pagination.total_pages}</span><button disabled={pagination.page >= pagination.total_pages} onClick={() => load({ page: pagination.page + 1 })}>التالي</button></div>
            </div>

            <main className={`${mobileView === "list" ? "hidden lg:block" : "block"} min-w-0 overflow-hidden rounded-2xl border bg-white p-3 sm:rounded-3xl sm:p-5`}>
                <button type="button" onClick={() => setMobileView("list")} className="mb-3 flex items-center gap-2 rounded-xl border px-3 py-2 text-sm font-bold lg:hidden"><ArrowRight /> العودة إلى المنتجات</button>
                {detailLoading ? <div className="flex h-72 items-center justify-center"><SpinnerGap className="animate-spin" /></div> : !selected ? <div className="flex h-72 flex-col items-center justify-center text-slate-400"><Cube size={54} /><p>اختر منتجًا</p></div> : <div className="min-w-0 space-y-4 sm:space-y-5">
                    <div className="flex min-w-0 items-center gap-3 border-b pb-4 sm:gap-4 sm:pb-5"><ProductThumb product={selected} size="lg" /><div className="min-w-0"><h1 className="break-words text-lg font-black sm:text-xl">{selected.name}</h1><p className="break-all text-xs text-slate-500">Salla #{selected.salla_product_id} · {selected.sku || "بدون SKU"}</p></div></div>
                    <ProductControlCenterPanel productId={selectedId} product={selected} onPublished={loadSelected} />
                    <ProductMediaDraftEditor productId={selectedId} images={media} onPublished={loadSelected} />
                    <section className="rounded-2xl border p-3 sm:p-4"><h2 className="mb-3 font-black">معاينة وصف المنتج الحالي</h2><DescriptionPreview html={selected.description_html || selected.description} /></section>
                    <section className="rounded-2xl border border-emerald-200 p-3 sm:p-4"><div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="font-black">التكلفة الأساسية — Mezan</h2><p className="text-xs text-slate-500">مستقلة عن نشر بيانات المنتج إلى سلة.</p></div><button onClick={saveCosts} disabled={costSaving} className="rounded-xl bg-emerald-700 px-4 py-2 text-white"><FloppyDisk className="inline" /> حفظ</button></div><div className="grid gap-3 sm:grid-cols-2 sm:gap-4"><label className="text-sm">تكلفة سلة<input value={selected.cost_price_from_salla ?? ""} readOnly className="mt-1 w-full rounded-xl border bg-slate-50 p-3" /></label><label className="text-sm">تكلفة المنتج في ميزان<input type="number" min="0" step="0.01" value={costs.base_cost} onChange={(event) => setCosts((current) => ({ ...current, base_cost: event.target.value }))} className="mt-1 w-full rounded-xl border border-emerald-300 p-3" /></label></div></section>
                    <ProductOptionCostEditor productId={selectedId} options={options} customFields={customFields} />
                    <section className="rounded-2xl border p-3 sm:p-4"><h2 className="mb-3 font-black">المتغيرات ({variants.length})</h2>{!variants.length ? <p className="text-slate-400">لا توجد متغيرات مستقلة.</p> : <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-right text-xs"><thead><tr><th className="p-3">المتغير</th><th>SKU</th><th>السعر</th><th>تكلفة ميزان</th></tr></thead><tbody>{variants.map((variant) => <tr key={variant.id} className="border-t"><td className="p-3 font-bold">{variant.display_name || variant.name || `متغير #${variant.id}`}</td><td>{variant.sku || "—"}</td><td>{money(variant.price)}</td><td><input type="number" min="0" step="0.01" value={costs.variant_costs?.[variant.id] ?? ""} onChange={(event) => setCosts((current) => ({ ...current, variant_costs: { ...current.variant_costs, [variant.id]: event.target.value } }))} className="w-28 rounded-lg border p-2" /></td></tr>)}</tbody></table></div>}</section>
                </div>}
            </main>
        </section>
    </div>;
}
