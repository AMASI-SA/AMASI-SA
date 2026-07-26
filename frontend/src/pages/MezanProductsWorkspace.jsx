import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowsClockwise,
    Barcode,
    CheckCircle,
    Cube,
    ImageSquare,
    MagnifyingGlass,
    Package,
    PencilSimple,
    SlidersHorizontal,
    SortAscending,
    SpinnerGap,
    Storefront,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    applyMissingSkus,
    getProductV2,
    getProductsV2Summary,
    listWorkspaceProducts,
    previewMissingSkus,
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

export default function MezanProductsWorkspace() {
    const [items, setItems] = useState([]);
    const [summary, setSummary] = useState(null);
    const [pagination, setPagination] = useState({ page: 1, per_page: 30, total: 0, total_pages: 1 });
    const [selectedId, setSelectedId] = useState("");
    const [selected, setSelected] = useState(null);
    const [query, setQuery] = useState("");
    const [appliedQuery, setAppliedQuery] = useState("");
    const [status, setStatus] = useState("");
    const [sort, setSort] = useState("newest");
    const [missingSku, setMissingSku] = useState(false);
    const [loading, setLoading] = useState(true);
    const [detailLoading, setDetailLoading] = useState(false);
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
        getProductV2(selectedId)
            .then((data) => { if (live) setSelected(data); })
            .catch(() => { if (live) setSelected(null); })
            .finally(() => { if (live) setDetailLoading(false); });
        return () => { live = false; };
    }, [selectedId]);

    const categories = useMemo(() => (selected?.categories || []).map((row) => row.name).filter(Boolean).join("، "), [selected]);
    const media = useMemo(() => {
        const raw = selected?.raw_salla || {};
        const values = raw.images || raw.media || [];
        const list = Array.isArray(values) ? values : [];
        const urls = list.map((row) => typeof row === "string" ? row : row?.url || row?.original || row?.thumbnail).filter(Boolean);
        if (selected?.main_image && !urls.includes(selected.main_image)) urls.unshift(selected.main_image);
        return urls.slice(0, 8);
    }, [selected]);

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

    async function openSkuPreview() {
        try {
            setSkuPreview(await previewMissingSkus({ limit: 50 }));
        } catch (err) {
            toast.error(err?.response?.data?.detail?.message || "تعذر تجهيز معاينة SKU");
        }
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
                        <option value="newest">المضافة حديثًا</option>
                        <option value="oldest">الأقدم</option>
                        <option value="name">الاسم</option>
                        <option value="price_high">السعر: الأعلى</option>
                        <option value="price_low">السعر: الأقل</option>
                    </select>
                    <button onClick={() => setMissingSku((value) => !value)} className={`rounded-xl border px-4 py-3 text-sm font-bold ${missingSku ? "border-amber-300 bg-amber-50 text-amber-900" : "border-slate-200"}`}>
                        <SlidersHorizontal size={18} className="ml-2 inline" /> بدون SKU
                    </button>
                    <button onClick={openSkuPreview} className="rounded-xl border border-violet-300 px-4 py-3 text-sm font-black text-violet-800">توليد SKU</button>
                    <button onClick={syncNow} disabled={syncing} className="rounded-xl bg-violet-700 px-4 py-3 text-sm font-black text-white disabled:opacity-50">
                        {syncing ? <SpinnerGap className="ml-2 inline animate-spin" /> : <ArrowsClockwise className="ml-2 inline" />} مزامنة سلة
                    </button>
                </div>
            </section>

            {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>}

            <section className="grid min-h-[720px] gap-4 xl:grid-cols-[220px_420px_minmax(0,1fr)]">
                <aside className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
                    <h2 className="mb-4 flex items-center gap-2 font-black"><SlidersHorizontal size={20} /> تصفية المنتجات</h2>
                    <div className="space-y-1">
                        {STATUS_FILTERS.map(([value, label]) => (
                            <button key={value} onClick={() => setStatus(value)} className={`w-full rounded-xl px-3 py-3 text-right text-sm font-bold ${status === value ? "bg-violet-700 text-white" : "text-slate-700 hover:bg-slate-50"}`}>{label}</button>
                        ))}
                    </div>
                    <div className="mt-6 border-t border-slate-100 pt-4 text-xs text-slate-500">
                        <div className="flex justify-between py-1"><span>إجمالي V2</span><strong className="num">{summary?.total || 0}</strong></div>
                        <div className="flex justify-between py-1"><span>نشط</span><strong className="num">{summary?.active || 0}</strong></div>
                        <div className="flex justify-between py-1"><span>مؤرشف</span><strong className="num">{summary?.archived || 0}</strong></div>
                    </div>
                </aside>

                <div className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
                    <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
                        <div>
                            <h2 className="font-black">المنتجات</h2>
                            <p className="text-[11px] text-slate-500">{pagination.total} منتج · الأحدث أولًا</p>
                        </div>
                        <SortAscending size={22} className="text-violet-700" />
                    </div>
                    <div className="max-h-[650px] overflow-auto">
                        {loading ? (
                            <div className="flex h-72 items-center justify-center"><SpinnerGap size={28} className="animate-spin text-violet-700" /></div>
                        ) : items.map((product) => {
                            const id = product.mezan_product_id || product.id;
                            const active = selectedId === id;
                            return (
                                <button key={id} onClick={() => setSelectedId(id)} className={`flex w-full items-center gap-3 border-b border-slate-100 p-3 text-right transition ${active ? "bg-emerald-50 ring-1 ring-inset ring-emerald-300" : "hover:bg-slate-50"}`}>
                                    <ProductThumb product={product} />
                                    <div className="min-w-0 flex-1">
                                        <h3 className="truncate text-sm font-black">{product.name}</h3>
                                        <p className="num mt-1 text-[11px] text-slate-500">{product.sku || "بدون SKU"} · {money(product.sale_price ?? product.price)}</p>
                                        <span className="mt-1 inline-block rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold">{statusLabel(product.status)}</span>
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                    <div className="flex items-center justify-between border-t border-slate-100 p-3">
                        <button disabled={pagination.page <= 1 || loading} onClick={() => load({ page: pagination.page - 1 })} className="rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-30">السابق</button>
                        <span className="num text-xs">{pagination.page} / {pagination.total_pages}</span>
                        <button disabled={pagination.page >= pagination.total_pages || loading} onClick={() => load({ page: pagination.page + 1 })} className="rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-30">التالي</button>
                    </div>
                </div>

                <main className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                    {detailLoading ? (
                        <div className="flex h-full items-center justify-center"><SpinnerGap size={30} className="animate-spin text-violet-700" /></div>
                    ) : !selected ? (
                        <div className="flex h-full flex-col items-center justify-center text-slate-400"><Cube size={54} /><p className="mt-3 font-bold">اختر منتجًا لفتحه</p></div>
                    ) : (
                        <div className="space-y-5">
                            <div className="flex flex-col gap-4 border-b border-slate-100 pb-5 sm:flex-row sm:items-center sm:justify-between">
                                <div className="flex min-w-0 items-center gap-4">
                                    <ProductThumb product={selected} size="lg" />
                                    <div className="min-w-0">
                                        <h1 className="truncate text-xl font-black">{selected.name}</h1>
                                        <p className="num mt-1 text-xs text-slate-500">Salla #{selected.salla_product_id} · {selected.sku || "بدون SKU"}</p>
                                    </div>
                                </div>
                                <span className="rounded-xl bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900">معاينة قبل الكتابة</span>
                            </div>

                            <section className="rounded-2xl border border-slate-200 p-4">
                                <h2 className="mb-3 flex items-center gap-2 font-black"><ImageSquare size={20} /> الصور</h2>
                                <div className="grid grid-cols-3 gap-3 sm:grid-cols-5">
                                    {(media.length ? media : [null]).map((url, index) => url ? <img key={url} src={url} alt="" className="aspect-square w-full rounded-xl border object-cover" /> : <div key={index} className="flex aspect-square items-center justify-center rounded-xl border bg-slate-50 text-slate-300"><ImageSquare size={28} /></div>)}
                                </div>
                            </section>

                            <section className="rounded-2xl border border-slate-200 p-4">
                                <h2 className="mb-4 flex items-center gap-2 font-black"><PencilSimple size={20} /> المعلومات الأساسية</h2>
                                <div className="grid gap-4 md:grid-cols-2">
                                    <label className="text-xs font-bold text-slate-500">اسم المنتج<input value={selected.name || ""} readOnly className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm text-slate-900" /></label>
                                    <label className="text-xs font-bold text-slate-500">SKU<input value={selected.sku || ""} readOnly className="num mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm text-slate-900" /></label>
                                    <label className="text-xs font-bold text-slate-500">السعر<input value={selected.price ?? ""} readOnly className="num mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm text-slate-900" /></label>
                                    <label className="text-xs font-bold text-slate-500">التصنيفات<input value={categories} readOnly className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm text-slate-900" /></label>
                                </div>
                                <label className="mt-4 block text-xs font-bold text-slate-500">الوصف<textarea value={selected.description || ""} readOnly rows={7} className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-3 text-sm leading-7 text-slate-900" /></label>
                            </section>

                            <section className="rounded-2xl border border-violet-200 bg-violet-50/50 p-4">
                                <h2 className="font-black text-violet-950">مساعد تحسين المنتج بالذكاء الاصطناعي</h2>
                                <p className="mt-2 text-sm leading-6 text-violet-800">سيستخدم الزيارات، الإضافة للسلة، بدء الدفع، الإكمال، المرتجعات، والبحث الداخلي لتحديد سبب التسرب، ثم يقترح عنوانًا ووصفًا وصورًا بديلة. لا تُكتب أي توصية إلى سلة قبل المعاينة والاعتماد.</p>
                                <div className="mt-3 grid gap-2 sm:grid-cols-3">
                                    <button disabled className="rounded-xl border border-violet-200 bg-white px-3 py-3 text-xs font-bold text-violet-700 opacity-60">اقتراح عنوان</button>
                                    <button disabled className="rounded-xl border border-violet-200 bg-white px-3 py-3 text-xs font-bold text-violet-700 opacity-60">تحسين الوصف</button>
                                    <button disabled className="rounded-xl border border-violet-200 bg-white px-3 py-3 text-xs font-bold text-violet-700 opacity-60">تحليل الصور</button>
                                </div>
                            </section>
                        </div>
                    )}
                </main>
            </section>
        </div>
    );
}
