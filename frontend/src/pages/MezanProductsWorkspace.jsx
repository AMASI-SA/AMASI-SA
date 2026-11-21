import { useCallback, useEffect, useState } from "react";
import {
    ArrowRight, ArrowsClockwise, Cube, FloppyDisk, MagnifyingGlass,
    Package, SlidersHorizontal, SpinnerGap, WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import InventorySuiteFrame,{InventoryPageHeader,InventorySheet} from "../components/inventory/suite/InventorySuiteFrame";
import "./inventory-catalog-resources.css";

import ProductControlCenterPanel from "../components/products/ProductControlCenterPanel";
import EmployeeProductDraft from "../components/products/EmployeeProductDraft";
import BundleEditor from "../components/products/BundleEditor";
import BundleReportPanel from "../components/products/BundleReportPanel";
import ProductMediaDraftEditor from "../components/products/ProductMediaDraftEditor";
import ProductOptionCostEditor from "../components/products/ProductOptionCostEditor";
import ProductOperationsEditor from "../components/products/ProductOperationsEditor";
import {
    applyMissingSkus, getProductV2, getProductV2Costs, getProductsV2Summary,
    listProductsV2, listWorkspaceProducts, previewMissingSkus, refreshProductV2Details,
    saveProductV2Costs, syncProductsV2,
} from "../services/mezanProductsV2";
import {
    isValidSoldMissingCostResult,
    resolveInitialProductsView,
    resolveInitialSelectedProduct,
} from "../lib/mezanV2CostLinks";

const STATUS_FILTERS = [["", "كل المنتجات"], ["active", "منتجات للبيع"], ["inactive", "منتجات مخفية"], ["out_of_stock", "منتجات نفدت"]];
const SELECTED_PRODUCT_KEY = "mezan.products-v2.selected-product";

function initialSelectedProduct() {
    if (typeof window === "undefined") return "";
    return resolveInitialSelectedProduct(
        window.location.search,
        window.localStorage.getItem(SELECTED_PRODUCT_KEY) || "",
    );
}

function initialMissingCostFilter() {
    if (typeof window === "undefined") return false;
    const params = new URLSearchParams(window.location.search);
    return params.get("missing_mezan_cost") === "1"
        && params.get("sold_only") === "1";
}

function initialMissingCostRange() {
    if (typeof window === "undefined") {
        return { from: "", to: "", paymentMethods: "", shippingCompanies: "", productIds: "" };
    }
    const params = new URLSearchParams(window.location.search);
    return {
        from: params.get("from") || "",
        to: params.get("to") || "",
        paymentMethods: params.get("payment_methods") || "",
        shippingCompanies: params.get("shipping_companies") || "",
        productIds: params.get("product_ids") || "",
    };
}

function initialMobileView() {
    if (typeof window === "undefined") return "list";
    return resolveInitialProductsView(
        window.location.search,
        window.localStorage.getItem(SELECTED_PRODUCT_KEY) || "",
    );
}

function initialCostFocus() {
    if (typeof window === "undefined") return false;
    return new URLSearchParams(window.location.search).get("focus") === "cost";
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
        ? <img src={product.main_image} alt={product.name || ""} className={`${classes} shrink-0 rounded-xl border border-slate-200 object-contain`} />
        : <div className={`${classes} flex shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-300`}><Package size={size === "lg" ? 38 : 24} /></div>;
}

function DescriptionPreview({ html }) {
    if (!html) return <p className="text-sm text-slate-400">لا يوجد وصف.</p>;
    return <iframe title="معاينة وصف المنتج" sandbox="" srcDoc={`<!doctype html><html dir="rtl"><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;line-height:1.8;padding:12px;color:#172033}img{max-width:100%;height:auto}table{max-width:100%;border-collapse:collapse}td,th{border:1px solid #ddd;padding:5px}</style></head><body>${html}</body></html>`} className="h-[320px] w-full rounded-xl border border-slate-200 bg-white sm:h-[360px]" />;
}

function SkuModal({ preview, busy, onClose, onApply }) {
    return <InventorySheet open={Boolean(preview)} onClose={onClose} busy={busy} title="توليد الرقم المخزني" description={"راجع الأرقام المقترحة قبل اعتمادها في سلة. عدد الأصناف بدون رقم: " + (preview?.missing_total || 0)} footer={<><button type="button" className="mz-catalog-secondary" disabled={busy} onClick={onClose}>إلغاء</button><button type="button" disabled={busy || !(preview?.items || []).length} onClick={onApply} className="mz-catalog-primary">{busy ? "جارٍ التحديث…" : "اعتماد"}</button></>}>
        <div className="mz-catalog-variants">{(preview?.items || []).map((row) => <article key={row.salla_product_id}><strong>{row.name}</strong><bdi>{row.proposed_sku}</bdi></article>)}</div>
    </InventorySheet>;
}

export default function MezanProductsWorkspace({ catalogSearch = null, canEditDraft = false }) {
    const catalogOnly = catalogSearch !== null;
    const bundlesView = !catalogOnly && new URLSearchParams(window.location.search).get("inventory_view") === "bundles";
    const [detailTab,setDetailTab] = useState(() => !catalogOnly && initialCostFocus() ? "costs" : bundlesView ? "bundle" : "overview");
    const [items, setItems] = useState([]);
    const [summary, setSummary] = useState(null);
    const [pagination, setPagination] = useState({ page: 1, total: 0, total_pages: 1 });
    const [selectedId, setSelectedId] = useState(() => catalogOnly
        ? resolveInitialSelectedProduct(catalogSearch)
        : initialSelectedProduct());
    const [selected, setSelected] = useState(null);
    const [costs, setCosts] = useState({ base_cost: "", variant_costs: {}, notes: "" });
    const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get("lookup_sku") || "");
    const [appliedQuery, setAppliedQuery] = useState(() => new URLSearchParams(window.location.search).get("lookup_sku") || "");
    const [status, setStatus] = useState("");
    const [sort, setSort] = useState("newest");
    const [missingSku, setMissingSku] = useState(false);
    const [missingMezanCost, setMissingMezanCost] = useState(() => !catalogOnly && initialMissingCostFilter());
    const [missingCostRange] = useState(initialMissingCostRange);
    const [focusCostEditor] = useState(() => !catalogOnly && initialCostFocus());
    const [loading, setLoading] = useState(true);
    const [detailLoading, setDetailLoading] = useState(false);
    const [detailError, setDetailError] = useState("");
    const [costSaving, setCostSaving] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [skuPreview, setSkuPreview] = useState(null);
    const [skuBusy, setSkuBusy] = useState(false);
    const [error, setError] = useState("");
    const [mobileView, setMobileView] = useState(() => catalogOnly ? resolveInitialProductsView(catalogSearch) : initialMobileView());

    const load = useCallback(async ({ page = 1, search = appliedQuery } = {}) => {
        setLoading(true); setError("");
        try {
            const [listResult, summaryResult] = catalogOnly
                ? [await listProductsV2({ page, perPage: 30, query: search, status }), null]
                : await Promise.all([
                listWorkspaceProducts({
                    page,
                    perPage: 30,
                    query: search,
                    status,
                    sort,
                    missingSku,
                    missingMezanCost,
                    soldOnly: missingMezanCost,
                    fromDate: missingCostRange.from,
                    toDate: missingCostRange.to,
                    paymentMethods: missingCostRange.paymentMethods,
                    shippingCompanies: missingCostRange.shippingCompanies,
                    productIds: missingMezanCost ? missingCostRange.productIds : "",
                }),
                getProductsV2Summary(),
            ]);
            if (
                missingMezanCost
                && !isValidSoldMissingCostResult(listResult, missingCostRange.productIds)
            ) {
                setItems([]);
                setPagination({ page: 1, total: 0, total_pages: 1 });
                throw new Error("رفض ميزان عرض قائمة غير مطابقة: لم يطبّق الخادم فلتر المنتجات المباعة بدون تكلفة.");
            }
            const nextItems = listResult.items || [];
            setItems(nextItems);
            setPagination(listResult.pagination || { page: 1, total: 0, total_pages: 1 });
            setSummary(summaryResult || null);
            if (!selectedId && nextItems[0]) {
                const firstId = nextItems[0].mezan_product_id || nextItems[0].id;
                setSelectedId(firstId);
                if (!catalogOnly) rememberSelectedProduct(firstId);
            }
        } catch (err) {
            const detail = err?.response?.data?.detail;
            setError((typeof detail === "string" ? detail : detail?.message) || err?.message || "تعذّر تحميل المنتجات.");
        } finally { setLoading(false); }
    }, [appliedQuery, catalogOnly, missingCostRange.from, missingCostRange.paymentMethods, missingCostRange.productIds, missingCostRange.shippingCompanies, missingCostRange.to, missingMezanCost, missingSku, selectedId, sort, status]);

    const loadSelected = useCallback(async () => {
        if (!selectedId) return;
        setDetailLoading(true);
        setDetailError("");
        try {
            if (catalogOnly) {
                setSelected(await getProductV2(selectedId));
                return;
            }
            const [detailResult, costResult] = await Promise.all([refreshProductV2Details(selectedId), getProductV2Costs(selectedId)]);
            setSelected(detailResult.product || null);
            setCosts({ base_cost: costResult?.base_cost ?? "", variant_costs: costResult?.variant_costs || {}, notes: costResult?.notes || "" });
        } catch (err) {
            setSelected(null);
            setDetailError(err?.response?.data?.detail?.message || "تعذر جلب تفاصيل المنتج");
        }
        finally { setDetailLoading(false); }
    }, [catalogOnly, selectedId]);

    useEffect(() => { load({ page: 1 }); }, [status, sort, missingSku, missingMezanCost]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => { loadSelected(); }, [loadSelected]);
    useEffect(() => { if (selectedId && !catalogOnly) rememberSelectedProduct(selectedId); }, [catalogOnly, selectedId]);
    useEffect(() => {
        if (!focusCostEditor || !selected || detailLoading) return undefined;
        const timer = window.setTimeout(() => {
            document.getElementById("mezan-product-cost-editor")?.scrollIntoView({
                behavior: "smooth",
                block: "center",
            });
        }, 50);
        return () => window.clearTimeout(timer);
    }, [detailLoading, focusCostEditor, selected]);

    const media = selected?.images || [];
    const options = selected?.options || [];
    const customFields = selected?.custom_fields || [];
    const variants = selected?.variants || [];

    async function syncNow() {
        setSyncing(true);
        try { const result = await syncProductsV2(); toast.success(`تمت المزامنة: ${result.seen_products || 0} منتج`); await load({ page: pagination.page || 1 }); }
        catch { toast.error("تعذرت المزامنة"); }
        finally { setSyncing(false); }
    }

    async function saveCosts() {
        if (!selectedId || costSaving) return;
        setCostSaving(true);
        try {
            await saveProductV2Costs(selectedId, costs);
            toast.success("تم حفظ تكاليف ميزان المستقلة");
            await loadSelected();
            await load({ page: 1 });
        }
        catch { toast.error("تعذر حفظ التكاليف"); }
        finally { setCostSaving(false); }
    }

    async function applySkuBatch() {
        if (skuBusy) return;
        setSkuBusy(true);
        try { const result = await applyMissingSkus({ prefix: "AMS", width: 5, limit: 50, confirmation: "تحديث SKU في سلة" }); toast.success(`تم تحديث ${result.succeeded || 0} منتج`); setSkuPreview(null); await load({ page: pagination.page || 1 }); }
        catch { toast.error("تعذر تحديث الأرقام المخزنية. يمكنك إعادة المحاولة."); }
        finally { setSkuBusy(false); }
    }

    async function previewSkus() {
        if (skuBusy) return;
        setSkuBusy(true);
        try { setSkuPreview(await previewMissingSkus({ limit: 50 })); }
        catch { toast.error("تعذر جلب الأرقام المقترحة. يمكنك إعادة المحاولة."); }
        finally { setSkuBusy(false); }
    }

    function selectProduct(id) {
        if (costSaving || skuBusy) return;
        setSelectedId(id);
        if (!catalogOnly) rememberSelectedProduct(id);
        setMobileView("detail");
        setDetailTab(bundlesView ? "bundle" : "overview");
        window.setTimeout(() => window.scrollTo({ top: 0, behavior: "smooth" }), 0);
    }

    function toggleMissingMezanCost() {
        const next = !missingMezanCost;
        setMissingMezanCost(next);
        if (typeof window === "undefined") return;
        const url = new URL(window.location.href);
        if (next) {
            url.searchParams.set("missing_mezan_cost", "1");
            url.searchParams.set("sold_only", "1");
        } else {
            url.searchParams.delete("missing_mezan_cost");
            url.searchParams.delete("sold_only");
            url.searchParams.delete("from");
            url.searchParams.delete("to");
            url.searchParams.delete("payment_methods");
            url.searchParams.delete("shipping_companies");
            url.searchParams.delete("product_ids");
            url.searchParams.delete("missing_all_cost");
        }
        window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    }

    return <InventorySuiteFrame active={bundlesView ? "bundles" : "catalog"}>
        <div className="mz-catalog-workspace" dir="rtl" data-testid="inventory-catalog-workspace">
            <InventoryPageHeader title={bundlesView ? "البكجات وتركيب المنتجات" : "الأصناف والمنتجات"} description={bundlesView ? "اختر المنتج، ثم راجع مكوناته وخياراته وتكلفته وتوريده." : "صورة الصنف ورقمه وخياراته وتكلفته، في مساحة واحدة."}>
                {!catalogOnly && <a className="mz-catalog-secondary" href="/products-v2?workspace=intake">إضافة صنف</a>}
                {!catalogOnly && <button className="mz-catalog-primary" onClick={syncNow} disabled={syncing}>{syncing ? <SpinnerGap className="animate-spin" /> : <ArrowsClockwise />}مزامنة سلة</button>}
            </InventoryPageHeader>
            {!catalogOnly && <SkuModal preview={skuPreview} busy={skuBusy} onClose={() => setSkuPreview(null)} onApply={applySkuBatch} />}
            {bundlesView && <BundleReportPanel />}
            <section className="mz-catalog-tools">
                <form onSubmit={(event) => { event.preventDefault(); setAppliedQuery(query.trim()); load({ page: 1, search: query.trim() }); }} className="mz-catalog-search">
                    <MagnifyingGlass size={21} /><input aria-label="بحث الأصناف بالاسم أو الرقم المخزني" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="الاسم، الرقم المخزني أو الباركود…" /><button type="submit">بحث</button>
                </form>
                <div className="mz-catalog-status-tabs" aria-label="حالة الأصناف">{STATUS_FILTERS.map(([value, label]) => <button key={value} type="button" aria-pressed={status === value} onClick={() => setStatus(value)}>{label}</button>)}</div>
                {!catalogOnly && <details className="mz-catalog-extra-filters"><summary><SlidersHorizontal size={17} />تصفية وإجراءات إضافية</summary><div>
                    <label>ترتيب الأصناف<select aria-label="ترتيب الأصناف" value={sort} onChange={(event) => setSort(event.target.value)}><option value="newest">المضافة حديثًا</option><option value="oldest">الأقدم</option><option value="name">الاسم</option><option value="price_high">السعر الأعلى</option><option value="price_low">السعر الأقل</option></select></label>
                    <button type="button" aria-pressed={missingSku} onClick={() => setMissingSku((value) => !value)}>بدون SKU</button>
                    <button type="button" aria-pressed={missingMezanCost} onClick={toggleMissingMezanCost}>مباعة بدون تكلفة ميزان</button>
                    <button type="button" onClick={previewSkus} disabled={skuBusy}>{skuBusy ? "جارٍ التحميل…" : "توليد SKU"}</button>
                </div></details>}
            </section>
            {missingMezanCost && <section className="mz-catalog-notice mz-catalog-warning" data-testid="missing-mezan-cost-filter-banner"><WarningCircle size={20} /><div><strong>{"يعرض الآن المنتجات المباعة بدون تكلفة صريحة في ميزان " + pagination.total + " فقط."}</strong><p>تكلفة سلة تُستخدم للحساب فقط، ولا تُخرج المنتج من هذه القائمة.</p>{(missingCostRange.from || missingCostRange.to) && <small>{missingCostRange.from || "البداية"} — {missingCostRange.to || "اليوم"}</small>}</div></section>}
            {error && <div className="mz-catalog-notice mz-catalog-error" role="alert"><WarningCircle />{error}<button type="button" onClick={() => load({ page: pagination.page || 1 })}>إعادة المحاولة</button></div>}
            <section className={"mz-catalog-layout" + (!catalogOnly && detailTab !== "overview" ? " is-editing" : "")}>
                <section className="mz-catalog-list" data-mobile-hidden={mobileView === "detail"} aria-label="قائمة الأصناف">
                    <div className="mz-catalog-section-title"><h2>{bundlesView ? "منتجات البكجات" : "دليل الأصناف"} <span>{pagination.total}</span></h2><p>الرقم المخزني يحدد الصنف؛ المواصفات تحدد النسخة.</p></div>
                    {loading ? <div className="mz-catalog-empty" role="status"><SpinnerGap className="animate-spin" size={32} />جارٍ تحميل الأصناف…</div> : !items.length ? <div className="mz-catalog-empty"><Cube size={44}/><h3>{missingMezanCost ? "لا توجد منتجات مباعة ناقصة تكلفة ميزان في هذه الفترة." : "لا توجد أصناف تطابق البحث."}</h3><p>جرّب الاسم أو الرقم المخزني أو غيّر الفلتر.</p></div> : <div className="mz-catalog-table-scroll"><table className="mz-catalog-table"><thead><tr><th>الصنف</th><th>الرقم المخزني</th><th>سعر البيع</th><th>الحالة</th></tr></thead><tbody>{items.map((product) => { const id = product.mezan_product_id || product.id; return <tr key={id} className={selectedId === id ? "is-selected" : ""} onClick={() => selectProduct(id)}><td><button type="button" className="mz-catalog-product" onClick={(event) => { event.stopPropagation(); selectProduct(id); }} aria-label={"عرض " + product.name}><ProductThumb product={product}/><span><strong>{product.name}</strong><small><bdi>{product.sku || "بدون SKU"}</bdi> · {product.product_type === "composite" ? "بكج / منتج مركب" : "منتج"}{product.mezan_cost_missing ? " · يحتاج مراجعة التكلفة" : ""}</small></span></button></td><td data-label="الرقم المخزني"><bdi>{product.sku || "بدون SKU"}</bdi></td><td data-label="سعر البيع" className="mz-catalog-price">{money(product.sale_price ?? product.price)}</td><td data-label="الحالة"><span className={"mz-catalog-state state-" + product.status}>{statusLabel(product.status)}</span>{product.mezan_cost_missing && <small className="mz-catalog-missing-cost">{product.mezan_cost_status?.uses_salla_fallback && !product.mezan_cost_status?.missing_everywhere ? "تكلفة سلة فقط" : "بدون أي تكلفة"}</small>}</td></tr>; })}</tbody></table></div>}
                    <div className="mz-catalog-pagination"><button disabled={pagination.page <= 1 || loading} onClick={() => load({ page: pagination.page - 1 })}>السابق</button><span>صفحة {pagination.page} من {pagination.total_pages} · إجمالي الأصناف {summary?.total ?? pagination.total}</span><button disabled={pagination.page >= pagination.total_pages || loading} onClick={() => load({ page: pagination.page + 1 })}>التالي</button></div>
                </section>
                <aside className="mz-catalog-inspector" data-mobile-hidden={mobileView === "list"} aria-label="تفاصيل الصنف">
                    <button type="button" className="mz-catalog-mobile-back" onClick={() => setMobileView("list")}><ArrowRight size={19}/>العودة إلى الأصناف</button>
                    {detailLoading ? <div className="mz-catalog-empty" role="status"><SpinnerGap className="animate-spin" size={32}/>جارٍ تحميل التفاصيل…</div> : detailError ? <div className="mz-catalog-empty" role="alert"><WarningCircle size={34}/><p>{detailError}</p><button type="button" className="mz-catalog-secondary" onClick={loadSelected}>إعادة المحاولة</button></div> : !selected ? <div className="mz-catalog-empty"><Cube size={44}/><h3>اختر صنفًا</h3><p>ستظهر صورته ومواصفاته وإجراءاته هنا.</p></div> : <>
                        <div className="mz-catalog-identity"><ProductThumb product={selected} size="lg"/><div><span>الصنف المحدد</span><h2>{selected.name}</h2><bdi>{selected.sku || "بدون SKU"}</bdi><span className={"mz-catalog-state state-" + selected.status}>{statusLabel(selected.status)}</span></div></div>
                        <div className="mz-catalog-quick-values"><div><span>سعر البيع</span><strong>{money(selected.sale_price ?? selected.price)}</strong></div>{!catalogOnly && <div><span>تكلفة ميزان</span><strong>{money(costs.base_cost)}</strong></div>}<div><span>النسخ</span><strong>{variants.length}</strong></div></div>
                        {!catalogOnly && <nav className="mz-catalog-detail-tabs" aria-label="تفاصيل وإجراءات الصنف">{[["overview","المواصفات"],["product","بيانات الصنف"],["costs","التكاليف"],["bundle","البكج"],["media","الصور"],["operations","التجهيز"]].map(([id,label]) => <button key={id} type="button" disabled={costSaving} aria-pressed={detailTab === id} onClick={() => setDetailTab(id)}>{label}</button>)}</nav>}
                        <div className="mz-catalog-detail-content">
                            {canEditDraft && <EmployeeProductDraft key={selectedId} productId={selectedId}/>}
                            {(catalogOnly || detailTab === "overview") && <>
                                <section><h3>خيارات المنتج</h3>{!options.length ? <p className="mz-catalog-muted">لا توجد خيارات معرفة لهذا المنتج.</p> : <dl className="mz-catalog-option-summary">{options.map((option) => <div key={option.id}><dt>{option.name}</dt><dd>{option.values?.length ? option.values.map((value) => value.name || value.label).join("، ") : "تخصيص نصي عند الطلب"}</dd></div>)}</dl>}</section>
                                <section><h3>النسخ والرقم المخزني</h3>{!variants.length ? <p className="mz-catalog-muted">لا توجد نسخ مستقلة.</p> : <div className="mz-catalog-variants">{variants.map((variant) => <article key={variant.id}><strong>{variant.display_name || variant.name || "نسخة المنتج"}</strong><bdi>{variant.sku || "بدون SKU"}</bdi><span>{money(variant.price)}</span></article>)}</div>}</section>
                                <details className="mz-catalog-description"><summary>وصف المنتج الحالي</summary><DescriptionPreview html={selected.description_html || selected.description}/></details>
                            </>}
                            {!catalogOnly && detailTab === "product" && <ProductControlCenterPanel productId={selectedId} product={selected} onPublished={loadSelected}/>}
                            {!catalogOnly && detailTab === "bundle" && <BundleEditor key={selectedId} productId={selectedId} salePrice={selected.sale_price ?? selected.price}/>}
                            {!catalogOnly && detailTab === "media" && <ProductMediaDraftEditor productId={selectedId} images={media} onPublished={loadSelected}/>}
                            {!catalogOnly && detailTab === "costs" && <fieldset disabled={costSaving} id="mezan-product-cost-editor" className="mz-catalog-cost-editor"><div className="mz-catalog-section-title"><div><h3>تكلفة المنتج في ميزان</h3><p>تكلفة ميزان مستقلة عن نشر بيانات المنتج إلى سلة.</p></div><button type="button" onClick={saveCosts} disabled={costSaving} className="mz-catalog-primary"><FloppyDisk size={17}/>{costSaving ? "جارٍ الحفظ…" : "حفظ التكاليف"}</button></div><div className="mz-catalog-form-grid"><label>تكلفة سلة<input aria-label="تكلفة سلة" value={selected.cost_price_from_salla ?? ""} readOnly/></label><label>تكلفة المنتج في ميزان<input aria-label="تكلفة المنتج في ميزان" type="number" min="0" step="0.01" value={costs.base_cost} onChange={(event) => setCosts((current) => ({ ...current, base_cost: event.target.value }))}/></label></div>{variants.length > 0 && <div className="mz-catalog-variant-costs">{variants.map((variant) => <label key={variant.id}><span>{variant.display_name || variant.name || "نسخة المنتج"}<bdi>{variant.sku || "بدون SKU"}</bdi></span><input aria-label={"تكلفة " + (variant.display_name || variant.name || variant.sku)} type="number" min="0" step="0.01" value={costs.variant_costs?.[variant.id] ?? ""} onChange={(event) => setCosts((current) => ({ ...current, variant_costs: { ...current.variant_costs, [variant.id]: event.target.value } }))}/></label>)}</div>}<ProductOptionCostEditor productId={selectedId} options={options} customFields={customFields}/></fieldset>}
                            {!catalogOnly && detailTab === "operations" && <ProductOperationsEditor productId={selectedId}/>}
                        </div>
                    </>}
                </aside>
            </section>
        </div>
    </InventorySuiteFrame>;
}
