import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowsClockwise,
    Cube,
    FloppyDisk,
    ImageSquare,
    MagnifyingGlass,
    Package,
    PencilSimple,
    SlidersHorizontal,
    SortAscending,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    applyMissingSkus,
    getProductV2Costs,
    getProductsV2Summary,
    listWorkspaceProducts,
    previewMissingSkus,
    refreshProductV2Details,
    saveProductV2Costs,
    syncProductsV2,
} from "../services/mezanProductsV2";

const STATUS_FILTERS = [
    ["", "كل المنتجات"],
    ["active", "منتجات للبيع"],
    ["inactive", "منتجات مخفية"],
    ["out_of_stock", "منتجات نفدت"],
];

function money(value) {
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function statusLabel(value) {
    if (value === "active") return "نشط";
    if (value === "out_of_stock") return "نفد";
    if (value === "inactive") return "مخفي";
    return value || "غير معروف";
}

function ProductThumb({ product, size = "md" }) {
    const classes = size === "lg" ? "h-28 w-28" : "h-14 w-14";
    return product?.main_image ? (
        <img src={product.main_image} alt={product.name || ""} className={`${classes} rounded-xl border border-slate-200 object-cover`} />
    ) : (
        <div className={`${classes} flex items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-300`}>
            <Package size={size === "lg" ? 38 : 24} weight="duotone" />
        </div>
    );
}

function SkuModal({ preview, busy, onClose, onApply }) {
    if (!preview) return null;
    return (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/45 p-4" dir="rtl">
            <div className="max-h-[85vh] w-full max-w-3xl overflow-hidden rounded-3xl bg-white shadow-2xl">
                <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
                    <div>
                        <h3 className="text-lg font-black">توليد SKU تلقائي</h3>
                        <p className="mt-1 text-xs text-slate-500">أعلى رقم حالي: {preview.prefix}{String(preview.current_max).padStart(preview.width, "0")} · ناقص SKU: {preview.missing_total}</p>
                    </div>
                    <button onClick={onClose} className="rounded-lg px-3 py-2 text-slate-500 hover:bg-slate-100">✕</button>
                </div>
                <div className="max-h-[55vh] overflow-auto p-5">
                    <div className="space-y-2">
                        {(preview.items || []).map((row) => (
                            <div key={row.salla_product_id} className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 p-3">
                                <div className="flex min-w-0 items-center gap-3">
                                    <ProductThumb product={row} />
                                    <div className="min-w-0">
                                        <div className="truncate font-bold">{row.name}</div>
                                        <div className="num text-[11px] text-slate-500">Salla: {row.salla_product_id}</div>
                                    </div>
                                </div>
                                <span className="num rounded-lg bg-violet-50 px-3 py-2 font-black text-violet-800">{row.proposed_sku}</span>
                            </div>
                        ))}
                    </div>
                </div>
                <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-6 py-4">
                    <p className="text-xs text-slate-500">لن يتم تعديل أي SKU موجود. الأرقام تُحجز لمنع التكرار.</p>
                    <button disabled={busy || !(preview.items || []).length} onClick={onApply} className="rounded-xl bg-violet-700 px-5 py-3 text-sm font-black text-white disabled:opacity-50">
                        {busy ? "جارٍ التحديث في سلة…" : `تحديث ${(preview.items || []).length} منتج`}
                    </button>
                </div>
            </div>
        </div>
    );
}

function DescriptionPreview({ html }) {
    if (!html) return <p className="text-sm text-slate-400">لا يوجد وصف.</p>;
    return (
        <iframe
            title="معاينة وصف المنتج"
            sandbox=""
            srcDoc={`<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;line-height:1.8;padding:12px;color:#172033}img{max-width:100%;height:auto}table{max-width:100%;border-collapse:collapse}td,th{border:1px solid #ddd;padding:5px}</style></head><body>${html}</body></html>`}
            className="h-[360px] w-full rounded-xl border border-slate-200 bg-white"
        />
    );
}

export default function MezanProductsWorkspace() {
    const [items, setItems] = useState([]);
    const [summary, setSummary] = useState(null);
    const [pagination, setPagination] = useState({ page: 1, per_page: 30, total: 0, total_pages: 1 });
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
        setLoading(true);
        setError("");
        try {
            const [listResult, summaryResult] = await Promise.all([
                listWorkspaceProducts({ page, perPage: 30, query: search, status, sort, missingSku }),
                getProductsV2Summary(),
            ]);
            const nextItems = listResult.items || [];
            setItems(nextItems);
            setPagination(listResult.pagination || { page: 1, per_page: 30, total: 0, total_pages: 1 });
            setSummary(summaryResult || null);
            if (!selectedId && nextItems[0]) setSelectedId(nextItems[0].mezan_product_id || nextItems[0].id);
        } catch (err) {
            const detail = err?.response?.data?.detail;
            setError((typeof detail === "string" ? detail : detail?.message) || err?.message || "تعذّر تحميل المنتجات.");
        } finally {
            setLoading(false);
        }
    }, [appliedQuery, missingSku, selectedId, sort, status]);

    useEffect(() => { load({ page: 1 }); }, [status, sort, missingSku]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        if (!selectedId) return;
        let live = true;
        setDetailLoading(true);
        Promise.all([
            refreshProductV2Details(selectedId),
            getProductV2Costs(selectedId),
        ])
            .then(([detailResult, costResult]) => {
                if (!live) return;
                setSelected(detailResult.product || null);
                setCosts({
                    base_cost: costResult?.base_cost ?? "",
                    variant_costs: costResult?.variant_costs || {},
                    notes: costResult?.notes || "",
                });
            })
            .catch((err) => {
                if (!live) return;
                setSelected(null);
                const detail = err?.response?.data?.detail;
                toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذر جلب تفاصيل المنتج الكاملة من سلة");
            })
            .finally(() => { if (live) setDetailLoading(false); });
        return () => { live = false; };
    }, [selectedId]);

    const categories = useMemo(() => (selected?.categories || []).map((row) => row.name).filter(Boolean).join("، "), [selected]);
    const media = selected?.images || [];
    const options = selected?.options || [];
    const variants = selected?.variants || [];

    async function syncNow() {
        setSyncing(true);
        try {
            const result = await syncProductsV2();
            toast.success(`تمت المزامنة: ${result.seen_products || 0} منتج`);
            await load({ page: 1 });
        } catch (err) {
            const detail = err?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذرت المزامنة");
        } finally { setSyncing(false); }
    }

    async function saveCosts() {
        if (!selectedId) return;
        setCostSaving(true);
        try {
            await saveProductV2Costs(selectedId, costs);
            toast.success("تم حفظ تكاليف المنتج والمتغيرات في ميزان");
        } catch (err) {
            const detail = err?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذر حفظ التكاليف");
        } finally { setCostSaving(false); }
    }

    async function openSkuPreview() {
        try { setSkuPreview(await previewMissingSkus({ limit: 50 })); }
        catch (err) { toast.error(err?.response?.data?.detail?.message || "تعذر تجهيز معاينة SKU"); }
    }

    async function applySkuBatch() {
        setSkuBusy(true);
        try {
            const result = await applyMissingSkus({ prefix: "AMS", width: 5, limit: 50, confirmation: "تحديث SKU في سلة" });
            toast.success(`تم تحديث ${result.succeeded || 0} منتج في سلة`);
            if (result.failed) toast.warning(`فشل ${result.failed} منتج — لم تتكرر الأرقام`);
            setSkuPreview(null);
            await load({ page: 1 });
        } catch (err) {
            toast.error(err?.response?.data?.detail?.message || "تعذر تحديث SKU في سلة");
        } finally { setSkuBusy(false); }
    }

    function submitSearch(event) {
        event.preventDefault();
        const value = query.trim();
        setAppliedQuery(value);
        load({ page: 1, search: value });
    }

    return (
        <div className="space-y-4" dir="rtl" data-testid="mezan-products-v2-workspace">
            <SkuModal preview={skuPreview} busy={skuBusy} onClose={() => setSkuPreview(null)} onApply={applySkuBatch} />

            <section className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
                    <form onSubmit={submitSearch} className="relative min-w-0 flex-1">
                        <MagnifyingGlass size={20} className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث في المنتجات بالاسم أو SKU أو الباركود…" className="w-full rounded-2xl border border-slate-200 py-3 pr-11 pl-4 text-sm outline-none focus:border-violet-400" />
                    </form>
                    <select value={sort} onChange={(event) => setSort(event.target.value)} className="rounded-xl border border-slate-200 px-3 py-3 text-sm font-bold">
                        <option value="newest">المضافة حديثًا</option><option value="oldest">الأقدم</option><option value="name">الاسم</option><option value="price_high">السعر: الأعلى</option><option value="price_low">السعر: الأقل</option>
                    </select>
                    <button onClick={() => setMissingSku((value) => !value)} className={`rounded-xl border px-4 py-3 text-sm font-bold ${missingSku ? "border-amber-300 bg-amber-50 text-amber-900" : "border-slate-200"}`}><SlidersHorizontal size={18} className="ml-2 inline" /> بدون SKU</button>
                    <button onClick={openSkuPreview} className="rounded-xl border border-violet-300 px-4 py-3 text-sm font-black text-violet-800">توليد SKU</button>
                    <button onClick={syncNow} disabled={syncing} className="rounded-xl bg-violet-700 px-4 py-3 text-sm font-black text-white disabled:opacity-50">{syncing ? <SpinnerGap className="ml-2 inline animate-spin" /> : <ArrowsClockwise className="ml-2 inline" />} مزامنة سلة</button>
                </div>
            </section>

            {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>}

            <section className="grid min-h-[820px] gap-4 xl:grid-cols-[220px_420px_minmax(0,1fr)]">
                <aside className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
                    <h2 className="mb-4 flex items-center gap-2 font-black"><SlidersHorizontal size={20} /> تصفية المنتجات</h2>
                    <div className="space-y-1">{STATUS_FILTERS.map(([value, label]) => <button key={value} onClick={() => setStatus(value)} className={`w-full rounded-xl px-3 py-3 text-right text-sm font-bold ${status === value ? "bg-violet-700 text-white" : "text-slate-700 hover:bg-slate-50"}`}>{label}</button>)}</div>
                    <div className="mt-6 border-t border-slate-100 pt-4 text-xs text-slate-500">
                        <div className="flex justify-between py-1"><span>إجمالي V2</span><strong className="num">{summary?.total || 0}</strong></div>
                        <div className="flex justify-between py-1"><span>نشط</span><strong className="num">{summary?.active || 0}</strong></div>
                        <div className="flex justify-between py-1"><span>مؤرشف</span><strong className="num">{summary?.archived || 0}</strong></div>
                    </div>
                </aside>

                <div className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
                    <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><div><h2 className="font-black">المنتجات</h2><p className="text-[11px] text-slate-500">{pagination.total} منتج · الأحدث أولًا</p></div><SortAscending size={22} className="text-violet-700" /></div>
                    <div className="max-h-[760px] overflow-auto">
                        {loading ? <div className="flex h-72 items-center justify-center"><SpinnerGap size={28} className="animate-spin text-violet-700" /></div> : items.map((product) => {
                            const id = product.mezan_product_id || product.id;
                            const active = selectedId === id;
                            return <button key={id} onClick={() => setSelectedId(id)} className={`flex w-full items-center gap-3 border-b border-slate-100 p-3 text-right transition ${active ? "bg-emerald-50 ring-1 ring-inset ring-emerald-300" : "hover:bg-slate-50"}`}><ProductThumb product={product} /><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-black">{product.name}</h3><p className="num mt-1 text-[11px] text-slate-500">{product.sku || "بدون SKU"} · {money(product.sale_price ?? product.price)}</p><span className="mt-1 inline-block rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold">{statusLabel(product.status)}</span></div></button>;
                        })}
                    </div>
                    <div className="flex items-center justify-between border-t border-slate-100 p-3"><button disabled={pagination.page <= 1 || loading} onClick={() => load({ page: pagination.page - 1 })} className="rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-30">السابق</button><span className="num text-xs">{pagination.page} / {pagination.total_pages}</span><button disabled={pagination.page >= pagination.total_pages || loading} onClick={() => load({ page: pagination.page + 1 })} className="rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-30">التالي</button></div>
                </div>

                <main className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                    {detailLoading ? <div className="flex h-full items-center justify-center"><SpinnerGap size={30} className="animate-spin text-violet-700" /></div> : !selected ? <div className="flex h-full flex-col items-center justify-center text-slate-400"><Cube size={54} /><p className="mt-3 font-bold">اختر منتجًا لفتحه</p></div> : (
                        <div className="space-y-5">
                            <div className="flex flex-col gap-4 border-b border-slate-100 pb-5 sm:flex-row sm:items-center sm:justify-between"><div className="flex min-w-0 items-center gap-4"><ProductThumb product={selected} size="lg" /><div className="min-w-0"><h1 className="truncate text-xl font-black">{selected.name}</h1><p className="num mt-1 text-xs text-slate-500">Salla #{selected.salla_product_id} · {selected.sku || "بدون SKU"}</p></div></div><span className="rounded-xl bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-900">تفاصيل مباشرة من سلة</span></div>

                            <section className="rounded-2xl border border-slate-200 p-4"><h2 className="mb-3 flex items-center gap-2 font-black"><ImageSquare size={20} /> الصور ({media.length})</h2><div className="grid grid-cols-3 gap-3 sm:grid-cols-5">{(media.length ? media : [null]).map((row, index) => row ? <img key={row.id || row.url} src={row.url} alt={row.alt || ""} className="aspect-square w-full rounded-xl border object-cover" /> : <div key={index} className="flex aspect-square items-center justify-center rounded-xl border bg-slate-50 text-slate-300"><ImageSquare size={28} /></div>)}</div></section>

                            <section className="rounded-2xl border border-slate-200 p-4"><h2 className="mb-4 flex items-center gap-2 font-black"><PencilSimple size={20} /> المعلومات الأساسية</h2><div className="grid gap-4 md:grid-cols-2"><label className="text-xs font-bold text-slate-500">اسم المنتج<input value={selected.name || ""} readOnly className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm text-slate-900" /></label><label className="text-xs font-bold text-slate-500">SKU<input value={selected.sku || ""} readOnly className="num mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm text-slate-900" /></label><label className="text-xs font-bold text-slate-500">السعر<input value={selected.price ?? ""} readOnly className="num mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm text-slate-900" /></label><label className="text-xs font-bold text-slate-500">التصنيفات<input value={categories} readOnly className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm text-slate-900" /></label></div></section>

                            <section className="rounded-2xl border border-slate-200 p-4"><h2 className="mb-3 font-black">وصف المنتج — معاينة HTML صحيحة</h2><DescriptionPreview html={selected.description_html || selected.description} /></section>

                            <section className="rounded-2xl border border-slate-200 p-4"><div className="mb-4 flex items-center justify-between"><div><h2 className="font-black">التكاليف</h2><p className="mt-1 text-xs text-slate-500">تكلفة سلة مرجعية، وتكلفة ميزان مستقلة للمحاسبة والربحية.</p></div><button onClick={saveCosts} disabled={costSaving} className="rounded-xl bg-emerald-700 px-4 py-2.5 text-xs font-black text-white disabled:opacity-50">{costSaving ? <SpinnerGap className="ml-1 inline animate-spin" /> : <FloppyDisk className="ml-1 inline" />} حفظ التكاليف</button></div><div className="grid gap-4 md:grid-cols-2"><label className="text-xs font-bold text-slate-500">سعر التكلفة في سلة<input value={selected.cost_price_from_salla ?? ""} readOnly className="num mt-1 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-3" /></label><label className="text-xs font-bold text-slate-500">تكلفة المنتج في ميزان<input type="number" min="0" step="0.01" value={costs.base_cost} onChange={(event) => setCosts((current) => ({ ...current, base_cost: event.target.value }))} className="num mt-1 w-full rounded-xl border border-emerald-300 px-3 py-3" /></label></div></section>

                            <section className="rounded-2xl border border-slate-200 p-4"><h2 className="mb-3 font-black">خيارات المنتج ({options.length})</h2>{!options.length ? <p className="text-sm text-slate-400">لا توجد خيارات في المنتج.</p> : <div className="space-y-3">{options.map((option) => <div key={option.id} className="rounded-xl border border-slate-200 p-3"><div className="font-bold">{option.name}</div><div className="mt-2 flex flex-wrap gap-2">{(option.values || []).map((value) => <span key={value.id} className="rounded-lg bg-slate-100 px-3 py-1.5 text-xs">{value.name}{value.price ? ` +${money(value.price)}` : ""}</span>)}</div></div>)}</div>}</section>

                            <section className="rounded-2xl border border-slate-200 p-4"><h2 className="mb-3 font-black">المتغيرات وتكاليفها ({variants.length})</h2>{!variants.length ? <p className="text-sm text-slate-400">لا توجد متغيرات مستقلة.</p> : <div className="overflow-auto"><table className="w-full min-w-[720px] text-right text-xs"><thead><tr className="border-b bg-slate-50"><th className="p-3">المتغير</th><th className="p-3">SKU</th><th className="p-3">السعر</th><th className="p-3">الكمية</th><th className="p-3">تكلفة سلة</th><th className="p-3">تكلفة ميزان</th></tr></thead><tbody>{variants.map((variant) => <tr key={variant.id} className="border-b"><td className="p-3 font-bold">{variant.name || (variant.selections || []).map((row) => row.value || row.name).filter(Boolean).join(" / ") || `متغير ${variant.id}`}</td><td className="num p-3">{variant.sku || "—"}</td><td className="p-3">{money(variant.sale_price ?? variant.price)}</td><td className="num p-3">{variant.unlimited_quantity ? "غير محدود" : variant.quantity ?? "—"}</td><td className="p-3">{money(variant.cost_price_from_salla)}</td><td className="p-3"><input type="number" min="0" step="0.01" value={costs.variant_costs?.[variant.id] ?? ""} onChange={(event) => setCosts((current) => ({ ...current, variant_costs: { ...(current.variant_costs || {}), [variant.id]: event.target.value } }))} className="num w-28 rounded-lg border border-emerald-300 px-2 py-2" /></td></tr>)}</tbody></table></div>}</section>

                            <section className="rounded-2xl border border-violet-200 bg-violet-50/50 p-4"><h2 className="font-black text-violet-950">مساعد تحسين المنتج بالذكاء الاصطناعي</h2><p className="mt-2 text-sm leading-6 text-violet-800">أصبحت الصور والوصف والخيارات والمتغيرات والتكلفة متاحة داخل Product V2. الخطوة التالية تربطها ببيانات الزيارات والإضافة للسلة وبدء الدفع لإنتاج اقتراحات قابلة للمعاينة والاعتماد.</p></section>
                        </div>
                    )}
                </main>
            </section>
        </div>
    );
}
