import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    Check,
    DownloadSimple,
    FilePdf,
    Funnel,
    MagnifyingGlass,
    Minus,
    Package,
    Plus,
    SpinnerGap,
    Stack,
    WarningCircle,
    X,
} from "@phosphor-icons/react";

import {
    createReviewedPreparationBatch,
    downloadReviewedPreparationBatchPdf,
    listReviewedProductCatalog,
    previewAms11353IncidentRecovery,
    applyAms11353IncidentRecovery,
} from "../services/orderReviewEngine";
import {
    displayReviewedQuantity,
    filterReviewedProducts,
    selectedReviewedCategoryNames,
    toggleReviewedCategory,
} from "../reviewedProductFilters";
import {
    createPreparationClientRequestId,
    reconcileReviewedPreparationSelection,
    reviewedPreparationSelectionSummary,
    reviewedRemainingQuantity,
    setReviewedPreparationQuantity,
    toggleReviewedPreparationProduct,
} from "../reviewedPreparationSelection";
import CustomerServiceInstructionBanner from "../components/fulfillment/CustomerServiceInstructionBanner";

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

function Ams11353IncidentRecovery({ onRecovered }) {
    const [preview, setPreview] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const load = useCallback(async () => {
        setBusy(true); setError("");
        try { setPreview(await previewAms11353IncidentRecovery()); }
        catch (e) { setError(e?.message || "تعذرت معاينة الاستعادة."); }
        finally { setBusy(false); }
    }, []);
    useEffect(() => { load(); }, [load]);
    const apply = async () => {
        if (!preview?.ok || preview?.resolved_quantity !== 11) return;
        if (!window.confirm("تأكيد إعادة 11 قطعة من AMS11353 إلى تمت المراجعة داخل ميزان فقط؟")) return;
        setBusy(true); setError("");
        try { await applyAms11353IncidentRecovery(); await load(); await onRecovered?.(); }
        catch (e) { setError(e?.response?.data?.detail?.code || e?.message || "تعذرت الاستعادة."); setBusy(false); }
    };
    return (
        <div className="rounded-2xl border-2 border-amber-300 bg-amber-50 p-4" data-testid="ams11353-incident-recovery">
            <h3 className="text-lg font-black text-amber-950">استعادة ملف AMS11353 المفقود</h3>
            <p className="mt-1 text-sm font-bold text-amber-900">معاينة مقيدة بـ8 طلبات و11 قطعة؛ لا تغيّر سلة ولا ملف PF-20260825-0042.</p>
            {preview && <div className="mt-3 grid grid-cols-3 gap-2 text-center text-sm font-black"><div className="rounded-xl bg-white p-2">الطلبات<br />{preview.expected_order_count}</div><div className="rounded-xl bg-white p-2">القطع<br />{preview.resolved_quantity}</div><div className="rounded-xl bg-white p-2">الحجوزات<br />{preview.target_allocations?.length || 0}</div></div>}
            {preview?.already_recovered && <div className="mt-3 rounded-xl bg-emerald-100 p-3 text-sm font-black text-emerald-900">اكتملت الاستعادة سابقًا.</div>}
            {preview?.problems?.length > 0 && <div className="mt-3 rounded-xl bg-rose-100 p-3 text-xs font-bold text-rose-900">{preview.problems.join(" · ")}</div>}
            {error && <div className="mt-3 text-sm font-bold text-rose-700">{error}</div>}
            <div className="mt-3 flex gap-2"><button type="button" onClick={load} disabled={busy} className="rounded-xl border bg-white px-4 py-2 font-black">تحديث المعاينة</button><button type="button" onClick={apply} disabled={busy || !preview?.ok || preview?.already_recovered || preview?.resolved_quantity !== 11} className="rounded-xl bg-amber-700 px-4 py-2 font-black text-white disabled:opacity-40">{busy ? "جاري الفحص…" : "استعادة 11 قطعة"}</button></div>
        </div>
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

function QuantitySelector({ product, value, onChange, onRemove }) {
    const remaining = reviewedRemainingQuantity(product);
    return (
        <div className="mt-3 rounded-2xl border border-violet-200 bg-violet-50 p-3" data-testid="reviewed-preparation-quantity">
            <div className="flex items-center justify-between gap-2">
                <div>
                    <div className="text-xs font-extrabold text-violet-900">كمية هذا الملف</div>
                    <div className="mt-0.5 text-[11px] text-violet-600">المتاح حاليًا: {remaining}</div>
                </div>
                <button
                    type="button"
                    onClick={onRemove}
                    className="rounded-lg p-2 text-rose-600 hover:bg-rose-50"
                    aria-label={`إلغاء تحديد ${product.name}`}
                >
                    <X size={18} weight="bold" />
                </button>
            </div>
            <div className="mt-3 grid grid-cols-[42px_minmax(64px,1fr)_42px_auto] items-center gap-2" dir="ltr">
                <button
                    type="button"
                    onClick={() => onChange(Number(value || 1) - 1)}
                    className="flex h-11 items-center justify-center rounded-xl border border-violet-200 bg-white text-violet-800"
                    aria-label="إنقاص الكمية"
                >
                    <Minus size={18} weight="bold" />
                </button>
                <input
                    type="number"
                    inputMode="numeric"
                    min="1"
                    max={remaining}
                    step="1"
                    value={value}
                    onChange={(event) => onChange(event.target.value)}
                    className="h-11 min-w-0 rounded-xl border border-violet-300 bg-white px-2 text-center text-lg font-black text-violet-950 outline-none focus:border-violet-600"
                    aria-label={`كمية ${product.name} في الملف`}
                />
                <button
                    type="button"
                    onClick={() => onChange(Number(value || 1) + 1)}
                    className="flex h-11 items-center justify-center rounded-xl border border-violet-200 bg-white text-violet-800"
                    aria-label="زيادة الكمية"
                >
                    <Plus size={18} weight="bold" />
                </button>
                <button
                    type="button"
                    onClick={() => onChange(remaining)}
                    className="h-11 rounded-xl bg-violet-700 px-3 text-sm font-extrabold text-white"
                >
                    كامل
                </button>
            </div>
        </div>
    );
}

function BatchSuccess({ batch, onDownload, downloading }) {
    if (!batch) return null;
    return (
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-950" data-testid="reviewed-preparation-batch-success">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-3">
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-emerald-600 text-white">
                        <FilePdf size={24} weight="duotone" />
                    </span>
                    <div>
                        <div className="font-black">تم إنشاء ملف التجهيز</div>
                        <div className="mt-1 break-all text-sm font-semibold text-emerald-800">{batch.file_name || `ملف ${batch.batch_id}`}</div>
                        <div className="mt-1 text-xs text-emerald-700">
                            {batch.allocated_quantity || 0} قطعة • {batch.order_count || 0} طلب
                            {(batch.transitioned_order_numbers || []).length > 0 && (
                                <> • انتقل {(batch.transitioned_order_numbers || []).length} طلب إلى قيد التنفيذ</>
                            )}
                        </div>
                    </div>
                </div>
                <button
                    type="button"
                    onClick={onDownload}
                    disabled={downloading}
                    className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 font-extrabold text-white disabled:opacity-60"
                >
                    {downloading ? <SpinnerGap className="animate-spin" /> : <DownloadSimple size={20} weight="bold" />}
                    تحميل الملف مجددًا
                </button>
            </div>
        </div>
    );
}

export default function ReviewedOrders() {
    const reviewedDate = new URLSearchParams(window.location.search).get("reviewed_date") || "";
    const historical = Boolean(reviewedDate);
    const [catalog, setCatalog] = useState({ products: [], categories: [], summary: {}, truncated: false });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [search, setSearch] = useState("");
    const [selectedCategoryIds, setSelectedCategoryIds] = useState([]);
    const [selectedQuantities, setSelectedQuantities] = useState({});
    const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
    const [creatingBatch, setCreatingBatch] = useState(false);
    const [downloadingBatch, setDownloadingBatch] = useState(false);
    const [batchError, setBatchError] = useState("");
    const [lastBatch, setLastBatch] = useState(null);
    const [stageInstructions, setStageInstructions] = useState([]);
    const requestIdRef = useRef("");

    const load = useCallback(async ({ silent = false } = {}) => {
        if (!silent) setLoading(true);
        setError("");
        try {
            const nextCatalog = await listReviewedProductCatalog({ limit: 2000, reviewedDate });
            setCatalog(nextCatalog);
            setSelectedQuantities((current) => reconcileReviewedPreparationSelection(
                current,
                nextCatalog.products,
            ));
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            if (!silent) setLoading(false);
        }
    }, [reviewedDate]);

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
    const selectionSummary = useMemo(
        () => reviewedPreparationSelectionSummary(selectedQuantities),
        [selectedQuantities],
    );

    const toggleCategory = (categoryId) => {
        setSelectedCategoryIds((current) => toggleReviewedCategory(current, categoryId));
    };

    const downloadBatch = useCallback(async (batch) => {
        if (!batch?.batch_id) return;
        setDownloadingBatch(true);
        setBatchError("");
        try {
            await downloadReviewedPreparationBatchPdf(batch.batch_id, batch.file_name);
        } catch (downloadError) {
            setBatchError(downloadError.message);
        } finally {
            setDownloadingBatch(false);
        }
    }, []);

    const createBatch = async () => {
        if (selectionSummary.productCount === 0 || creatingBatch || catalog.truncated) return;
        const confirmed = window.confirm(
            `إنشاء ملف تجهيز يحتوي ${selectionSummary.totalQuantity} قطعة من ${selectionSummary.productCount} منتج؟`,
        );
        if (!confirmed) return;

        setCreatingBatch(true);
        setBatchError("");
        if (!requestIdRef.current) {
            requestIdRef.current = createPreparationClientRequestId();
        }
        try {
            const productsByKey = new Map(
                (catalog.products || []).map((product) => [product.group_key, product]),
            );
            const batch = await createReviewedPreparationBatch({
                clientRequestId: requestIdRef.current,
                selections: selectionSummary.selections.map((selection) => ({
                    ...selection,
                    revision: productsByKey.get(selection.group_key)?.revision || undefined,
                })),
            });
            setLastBatch(batch);
            setSelectedQuantities({});
            requestIdRef.current = "";
            await load({ silent: true });
            try {
                await downloadReviewedPreparationBatchPdf(batch.batch_id, batch.file_name);
            } catch (downloadError) {
                setBatchError(downloadError.message);
            }
        } catch (createError) {
            setBatchError(createError.message);
            if (createError?.code === "reviewed_selection_stale") {
                // The refreshed catalog belongs to a new idempotent attempt;
                // do not retain the failed request until a browser refresh.
                requestIdRef.current = "";
                setSelectedQuantities({});
            }
            if (createError?.code === "customer_service_instruction_action_required") {
                setStageInstructions(createError?.detail?.instructions || []);
            }
            await load({ silent: true });
        } finally {
            setCreatingBatch(false);
        }
    };

    const incidentRecovery = new URLSearchParams(window.location.search).get("incident") === "ams11353-20260825"
        ? <Ams11353IncidentRecovery onRecovered={() => load({ silent: true })} />
        : null;

    // The incident preview must not be hidden behind the reviewed-products
    // catalog request.  That catalog can be slow or temporarily unavailable,
    // while the recovery endpoint is deliberately narrow and independent.
    if (loading) return <section className="space-y-4" dir="rtl">{incidentRecovery}<div className="flex min-h-80 items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-600" /></div></section>;
    if (error) return <section className="space-y-4" dir="rtl">{incidentRecovery}<div className="rounded-2xl border border-rose-200 bg-rose-50 p-5 text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div></section>;

    return (
        <section className={`space-y-4 ${selectionSummary.productCount > 0 ? "pb-28 sm:pb-24" : ""}`} dir="rtl" data-testid="reviewed-orders-stage" data-view="reviewed-products">
            {incidentRecovery}
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <h2 className="text-xl font-black text-slate-950 sm:text-2xl">{historical ? `المنتجات التي مرّت بالمراجعة بتاريخ ${reviewedDate}` : "منتجات تمت مراجعتها"}</h2>
                        <p className="mt-1 text-sm text-slate-500">{historical ? "عرض تاريخي للكمية الأصلية وأرقام الطلبات، حتى لو انتقلت المنتجات لاحقًا إلى مرحلة أخرى." : "حدد المنتجات والكمية التي تريد إضافتها إلى ملف التجهيز الحالي."}</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-center sm:flex sm:text-right">
                        <div className="rounded-xl bg-violet-50 px-3 py-2">
                            <div className="text-[11px] font-bold text-violet-500">المنتجات المتاحة</div>
                            <div className="mt-0.5 text-lg font-black text-violet-900">{filteredProducts.length}</div>
                        </div>
                        <div className="rounded-xl bg-emerald-50 px-3 py-2">
                            <div className="text-[11px] font-bold text-emerald-600">{historical ? "إجمالي ما مرّ بالمراجعة" : "إجمالي المتبقي"}</div>
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

            <BatchSuccess
                batch={lastBatch}
                onDownload={() => downloadBatch(lastBatch)}
                downloading={downloadingBatch}
            />

            {batchError && (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800" role="alert">
                    <WarningCircle className="ml-2 inline" size={19} />
                    {batchError}
                </div>
            )}

            <CustomerServiceInstructionBanner
                instructions={stageInstructions}
                stage="reviewed"
                onUpdated={(response) => {
                    if (!response?.waiting_customer_service_approval) setStageInstructions([]);
                }}
            />

            {catalog.truncated && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-bold text-amber-900">
                    تم الوصول إلى الحد التشغيلي لعدد الطلبات. لن يتم إنشاء ملف تجهيز من بيانات ناقصة قبل معالجة هذا التنبيه.
                </div>
            )}

            {filteredProducts.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">
                    {catalog.products.length === 0 ? "لا توجد قطع متبقية في مرحلة تمت المراجعة." : "لا توجد منتجات مطابقة للتصنيفات أو البحث."}
                </div>
            ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="reviewed-products-grid">
                    {filteredProducts.map((product) => {
                        const productCategories = (product.direct_category_ids || product.category_ids || [])
                            .map((id) => categoryById.get(id))
                            .filter(Boolean)
                            .slice(0, 2);
                        const selected = Object.prototype.hasOwnProperty.call(selectedQuantities, product.group_key);
                        const selectedQuantity = selectedQuantities[product.group_key];
                        const remaining = reviewedRemainingQuantity(product);
                        const allocated = Math.max(0, Number(product.allocated_quantity || 0));
                        return (
                            <article
                                key={product.group_key}
                                className={`overflow-hidden rounded-2xl border bg-white shadow-sm transition ${selected ? "border-violet-500 ring-2 ring-violet-100" : "border-slate-200"}`}
                                data-testid="reviewed-product-card"
                                data-selected={selected ? "true" : "false"}
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
                                            {allocated > 0 && (
                                                <div className="mt-2 text-[11px] font-bold text-amber-700">رُفع سابقًا: {displayReviewedQuantity(allocated)}</div>
                                            )}
                                        </div>
                                        <div className="mt-3 flex items-end justify-between gap-2">
                                            <div className="text-xs font-bold text-slate-400">
                                                {historical ? "ظهر في" : "متبقي في"} <b className="text-slate-700">{product.source_order_count || 0}</b> طلب
                                            </div>
                                            <div className="min-w-20 rounded-2xl bg-emerald-600 px-3 py-2 text-center text-white shadow-sm shadow-emerald-200">
                                                <div className="text-[10px] font-bold text-emerald-100">{historical ? "الكمية الأصلية" : "المتاح"}</div>
                                                <div className="text-2xl font-black leading-none">{displayReviewedQuantity(remaining)}</div>
                                            </div>
                                        </div>
                                        {historical && (product.source_order_numbers || []).length > 0 && (
                                            <div className="mt-2 break-words text-[11px] font-bold text-violet-700" dir="ltr">
                                                {(product.source_order_numbers || []).join(" • ")}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                {!historical && <div className="border-t border-slate-100 px-3 pb-3 sm:px-4 sm:pb-4">
                                    {selected ? (
                                        <QuantitySelector
                                            product={product}
                                            value={selectedQuantity}
                                            onChange={(value) => setSelectedQuantities((current) => setReviewedPreparationQuantity(current, product, value))}
                                            onRemove={() => setSelectedQuantities((current) => toggleReviewedPreparationProduct(current, product))}
                                        />
                                    ) : (
                                        <button
                                            type="button"
                                            onClick={() => setSelectedQuantities((current) => toggleReviewedPreparationProduct(current, product))}
                                            className="mt-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 text-sm font-extrabold text-violet-800 hover:bg-violet-100"
                                        >
                                            <Check size={19} weight="bold" />
                                            تحديد للملف
                                        </button>
                                    )}
                                </div>}
                            </article>
                        );
                    })}
                </div>
            )}

            <div className="rounded-xl border border-slate-200 bg-white p-3 text-xs font-semibold leading-6 text-slate-500">
                <Stack className="ml-1 inline text-violet-600" size={17} />
                تُخصم القطع من المتاح بعد نجاح إنشاء الملف فقط. وينتقل الطلب إلى قيد التنفيذ عندما تصبح جميع قطعه المخصصة لملفات التجهيز مضافة إلى دفعات.
            </div>

            <MobileCategoryDrawer
                open={mobileFiltersOpen}
                categories={catalog.categories}
                selectedIds={selectedCategoryIds}
                onToggle={toggleCategory}
                onClear={() => setSelectedCategoryIds([])}
                onClose={() => setMobileFiltersOpen(false)}
            />

            {!historical && selectionSummary.productCount > 0 && (
                <div className="sticky bottom-3 z-50 mx-auto max-w-3xl rounded-2xl border border-violet-200 bg-white/95 p-3 shadow-2xl shadow-violet-200/60 backdrop-blur" data-testid="reviewed-preparation-selection-bar">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="grid grid-cols-2 gap-2 text-center sm:flex sm:text-right">
                            <div className="rounded-xl bg-violet-50 px-3 py-2">
                                <div className="text-[10px] font-bold text-violet-500">المنتجات المحددة</div>
                                <div className="text-lg font-black text-violet-950">{selectionSummary.productCount}</div>
                            </div>
                            <div className="rounded-xl bg-emerald-50 px-3 py-2">
                                <div className="text-[10px] font-bold text-emerald-600">قطع هذا الملف</div>
                                <div className="text-lg font-black text-emerald-950">{selectionSummary.totalQuantity}</div>
                            </div>
                        </div>
                        <div className="flex gap-2">
                            <button
                                type="button"
                                onClick={() => setSelectedQuantities({})}
                                disabled={creatingBatch}
                                className="min-h-12 rounded-xl border border-slate-200 bg-white px-4 text-sm font-extrabold text-slate-600 disabled:opacity-50"
                            >
                                إلغاء
                            </button>
                            <button
                                type="button"
                                onClick={createBatch}
                                disabled={creatingBatch || catalog.truncated}
                                className="inline-flex min-h-12 flex-1 items-center justify-center gap-2 rounded-xl bg-violet-700 px-5 text-sm font-extrabold text-white shadow-lg shadow-violet-200 disabled:cursor-not-allowed disabled:opacity-50 sm:flex-none"
                            >
                                {creatingBatch ? <SpinnerGap className="animate-spin" size={21} /> : <FilePdf size={21} weight="duotone" />}
                                {creatingBatch ? "جارٍ إنشاء الملف…" : "إنشاء وتحميل الملف"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </section>
    );
}
