import { useCallback, useEffect, useMemo, useState } from "react";
import {
    Check,
    Funnel,
    MagnifyingGlass,
    Package,
    SpinnerGap,
    Stack,
    WarningCircle,
    X,
} from "@phosphor-icons/react";

import { listReviewedProductCatalog } from "../services/orderReviewEngine";
import {
    displayReviewedQuantity,
    filterReviewedProducts,
    selectedReviewedCategoryNames,
    toggleReviewedCategory,
} from "../reviewedProductFilters";

function categoryLabel(category) {
    return String(category?.path || category?.name || category?.id || "").trim();
}

function ProductImage({ product }) {
    return product.image_url ? (
        <img
            src={product.image_url}
            alt={product.name}
            loading="lazy"
            className="h-full w-full object-cover"
        />
    ) : (
        <div className="flex h-full w-full items-center justify-center bg-slate-100 text-slate-400">
            <Package size={34} />
        </div>
    );
}

function CategoryCheckbox({ category, selected, onToggle }) {
    const depth = Math.max(0, Number(category?.depth || 0));
    return (
        <button
            type="button"
            onClick={() => onToggle(category.id)}
            className={`flex w-full items-center gap-3 rounded-xl border px-3 py-3 text-right transition ${selected
                ? "border-violet-500 bg-violet-50 text-violet-900"
                : "border-slate-200 bg-white text-slate-700 hover:border-violet-300"}`}
            style={{ paddingInlineStart: `${12 + Math.min(depth, 4) * 14}px` }}
            aria-pressed={selected}
        >
            <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border ${selected
                ? "border-violet-600 bg-violet-600 text-white"
                : "border-slate-300 bg-white"}`}
            >
                {selected && <Check size={15} weight="bold" />}
            </span>
            <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-extrabold">{category.name || category.id}</span>
                {category.path && category.path !== category.name && (
                    <span className="mt-0.5 block truncate text-[11px] text-slate-400">{category.path}</span>
                )}
            </span>
            <span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-xs font-black text-slate-600">
                {category.product_count || 0}
            </span>
        </button>
    );
}

function MobileCategoryDrawer({ open, categories, selectedIds, onToggle, onClear, onClose }) {
    if (!open) return null;
    const selected = new Set(selectedIds);
    return (
        <div className="fixed inset-0 z-[120] md:hidden" dir="rtl">
            <button
                type="button"
                className="absolute inset-0 bg-slate-950/45"
                onClick={onClose}
                aria-label="إغلاق التصنيفات"
            />
            <aside className="absolute inset-y-0 right-0 flex w-[88vw] max-w-sm flex-col bg-slate-50 shadow-2xl">
                <div className="flex items-center justify-between border-b bg-white px-4 py-4">
                    <div>
                        <h3 className="text-lg font-black text-slate-950">تصنيفات المنتجات</h3>
                        <p className="mt-1 text-xs text-slate-500">يمكن اختيار أكثر من تصنيف</p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl border border-slate-200 bg-white p-2">
                        <X size={22} />
                    </button>
                </div>
                <div className="flex-1 space-y-2 overflow-y-auto p-3">
                    <button
                        type="button"
                        onClick={onClear}
                        className={`flex w-full items-center justify-between rounded-xl border px-3 py-3 text-sm font-extrabold ${selected.size === 0
                            ? "border-violet-500 bg-violet-50 text-violet-900"
                            : "border-slate-200 bg-white text-slate-700"}`}
                    >
                        <span>جميع التصنيفات</span>
                        {selected.size === 0 && <Check size={18} weight="bold" />}
                    </button>
                    {categories.map((category) => (
                        <CategoryCheckbox
                            key={category.id}
                            category={category}
                            selected={selected.has(category.id)}
                            onToggle={onToggle}
                        />
                    ))}
                </div>
                <div className="border-t bg-white p-3">
                    <button type="button" onClick={onClose} className="w-full rounded-xl bg-violet-700 px-4 py-3 font-extrabold text-white">
                        عرض المنتجات
                    </button>
                </div>
            </aside>
        </div>
    );
}

export default function ReviewedOrders() {
    const [catalog, setCatalog] = useState({ products: [], categories: [], summary: {}, truncated: false });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [search, setSearch] = useState("");
    const [selectedCategoryIds, setSelectedCategoryIds] = useState([]);
    const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            setCatalog(await listReviewedProductCatalog({ limit: 2000 }));
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const selectedCategorySet = useMemo(
        () => new Set(selectedCategoryIds),
        [selectedCategoryIds],
    );
    const filteredProducts = useMemo(
        () => filterReviewedProducts(catalog.products, selectedCategoryIds, search),
        [catalog.products, selectedCategoryIds, search],
    );
    const selectedNames = useMemo(
        () => selectedReviewedCategoryNames(catalog.categories, selectedCategoryIds),
        [catalog.categories, selectedCategoryIds],
    );
    const categoryById = useMemo(
        () => new Map(catalog.categories.map((category) => [category.id, category])),
        [catalog.categories],
    );
    const shownQuantity = useMemo(
        () => filteredProducts.reduce((total, product) => total + Number(product.quantity || 0), 0),
        [filteredProducts],
    );

    const toggleCategory = (categoryId) => {
        setSelectedCategoryIds((current) => toggleReviewedCategory(current, categoryId));
    };

    if (loading) return <div className="flex min-h-80 items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-600" /></div>;
    if (error) return <div className="rounded-2xl border border-rose-200 bg-rose-50 p-5 text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>;

    return (
        <section className="space-y-4" dir="rtl" data-testid="reviewed-products-stage">
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <h2 className="text-xl font-black text-slate-950 sm:text-2xl">منتجات تمت مراجعتها</h2>
                        <p className="mt-1 text-sm text-slate-500">كل منتج يظهر مرة واحدة، والكمية تجمع جميع الطلبات الموجودة في هذه المرحلة.</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-center sm:flex sm:text-right">
                        <div className="rounded-xl bg-violet-50 px-3 py-2">
                            <div className="text-[11px] font-bold text-violet-500">المنتجات</div>
                            <div className="mt-0.5 text-lg font-black text-violet-900">{filteredProducts.length}</div>
                        </div>
                        <div className="rounded-xl bg-emerald-50 px-3 py-2">
                            <div className="text-[11px] font-bold text-emerald-600">إجمالي الكمية</div>
                            <div className="mt-0.5 text-lg font-black text-emerald-900">{displayReviewedQuantity(shownQuantity)}</div>
                        </div>
                    </div>
                </div>

                <div className="mt-4 flex gap-2">
                    <label className="relative block min-w-0 flex-1">
                        <MagnifyingGlass className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                            value={search}
                            onChange={(event) => setSearch(event.target.value)}
                            className="w-full rounded-xl border border-slate-200 py-3 pl-3 pr-10 text-sm outline-none focus:border-violet-500"
                            placeholder="بحث باسم المنتج أو SKU"
                        />
                    </label>
                    <button
                        type="button"
                        onClick={() => setMobileFiltersOpen(true)}
                        className="relative inline-flex h-12 shrink-0 items-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 font-extrabold text-violet-800 md:hidden"
                    >
                        <Funnel size={20} weight="fill" />
                        <span className="hidden min-[390px]:inline">التصنيفات</span>
                        {selectedCategoryIds.length > 0 && (
                            <span className="absolute -left-1 -top-1 flex h-6 min-w-6 items-center justify-center rounded-full bg-rose-600 px-1 text-xs font-black text-white">
                                {selectedCategoryIds.length}
                            </span>
                        )}
                    </button>
                </div>

                <div className="mt-4 hidden items-center gap-2 overflow-x-auto pb-2 md:flex" aria-label="تصفية المنتجات حسب التصنيف">
                    <button
                        type="button"
                        onClick={() => setSelectedCategoryIds([])}
                        className={`shrink-0 rounded-full border px-4 py-2 text-sm font-extrabold transition ${selectedCategoryIds.length === 0
                            ? "border-violet-600 bg-violet-600 text-white"
                            : "border-slate-200 bg-white text-slate-700 hover:border-violet-300"}`}
                    >
                        جميع التصنيفات
                    </button>
                    {catalog.categories.map((category) => {
                        const selected = selectedCategorySet.has(category.id);
                        return (
                            <button
                                key={category.id}
                                type="button"
                                onClick={() => toggleCategory(category.id)}
                                title={categoryLabel(category)}
                                aria-pressed={selected}
                                className={`inline-flex shrink-0 items-center gap-2 rounded-full border px-4 py-2 text-sm font-extrabold transition ${selected
                                    ? "border-violet-600 bg-violet-600 text-white"
                                    : "border-slate-200 bg-white text-slate-700 hover:border-violet-300"}`}
                            >
                                <span>{category.name || category.id}</span>
                                <span className={`rounded-full px-2 py-0.5 text-xs ${selected ? "bg-white/20" : "bg-slate-100"}`}>{category.product_count || 0}</span>
                            </button>
                        );
                    })}
                </div>

                {selectedNames.length > 0 && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                        <span className="text-xs font-bold text-slate-400">المحدد:</span>
                        {selectedNames.map((name, index) => (
                            <span key={`${name}-${index}`} className="rounded-full bg-violet-50 px-3 py-1 text-xs font-extrabold text-violet-800">{name}</span>
                        ))}
                        <button type="button" onClick={() => setSelectedCategoryIds([])} className="text-xs font-extrabold text-rose-600">مسح الكل</button>
                    </div>
                )}
            </div>

            {catalog.truncated && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-bold text-amber-900">
                    تم الوصول إلى الحد التشغيلي لعدد الطلبات. لن يتم إنشاء ملف تجهيز من بيانات ناقصة قبل معالجة هذا التنبيه.
                </div>
            )}

            {filteredProducts.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
                    {catalog.products.length === 0 ? "لا توجد منتجات في مرحلة تمت المراجعة." : "لا توجد منتجات مطابقة للتصنيفات أو البحث."}
                </div>
            ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="reviewed-products-grid">
                    {filteredProducts.map((product) => {
                        const productCategories = (product.direct_category_ids || product.category_ids || [])
                            .map((id) => categoryById.get(id))
                            .filter(Boolean)
                            .slice(0, 2);
                        return (
                            <article
                                key={product.group_key}
                                className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
                                data-testid="reviewed-product-card"
                            >
                                <div className="grid grid-cols-[104px_minmax(0,1fr)] gap-3 p-3 sm:grid-cols-[118px_minmax(0,1fr)] sm:p-4">
                                    <div className="aspect-square overflow-hidden rounded-2xl border border-slate-100 bg-slate-50">
                                        <ProductImage product={product} />
                                    </div>
                                    <div className="flex min-w-0 flex-col">
                                        <div className="min-w-0 flex-1">
                                            <h3 className="line-clamp-2 text-base font-black leading-7 text-slate-950 sm:text-lg">{product.name}</h3>
                                            {product.sku && <div className="mt-1 truncate text-xs font-bold text-slate-400" dir="ltr">SKU: {product.sku}</div>}
                                            {productCategories.length > 0 && (
                                                <div className="mt-2 flex flex-wrap gap-1">
                                                    {productCategories.map((category) => (
                                                        <span key={category.id} className="max-w-full truncate rounded-full bg-slate-100 px-2 py-1 text-[10px] font-extrabold text-slate-600">
                                                            {category.name || category.id}
                                                        </span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                        <div className="mt-3 flex items-end justify-between gap-2">
                                            <div className="text-xs font-bold text-slate-400">
                                                في <b className="text-slate-700">{product.source_order_count || 0}</b> طلب
                                            </div>
                                            <div className="min-w-20 rounded-2xl bg-emerald-600 px-3 py-2 text-center text-white shadow-sm shadow-emerald-200">
                                                <div className="text-[10px] font-bold text-emerald-100">الكمية</div>
                                                <div className="text-2xl font-black leading-none">{displayReviewedQuantity(product.quantity)}</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </article>
                        );
                    })}
                </div>
            )}

            <div className="rounded-xl border border-slate-200 bg-white p-3 text-xs font-semibold leading-6 text-slate-500">
                <Stack className="ml-1 inline text-violet-600" size={17} />
                التجميع يعتمد على هوية منتج سلة. أسماء النقش والرسائل الشخصية لا تكرر البطاقة، وتبقى تفاصيل كل طلب محفوظة للخطوة التالية.
            </div>

            <MobileCategoryDrawer
                open={mobileFiltersOpen}
                categories={catalog.categories}
                selectedIds={selectedCategoryIds}
                onToggle={toggleCategory}
                onClear={() => setSelectedCategoryIds([])}
                onClose={() => setMobileFiltersOpen(false)}
            />
        </section>
    );
}
