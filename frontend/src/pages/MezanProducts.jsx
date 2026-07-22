import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle,
    Gear,
    MagnifyingGlass,
    NotePencil,
    Package,
    Receipt,
    SpinnerGap,
    Storefront,
    WarningCircle,
    XCircle,
} from "@phosphor-icons/react";

import { listMezanProducts } from "../services/mezanProductCatalog";

const COST_FIELDS = [
    { key: "supplier", label: "تكلفة المورد", help: "سعر الشراء قبل المصاريف الداخلية" },
    { key: "labor", label: "التجهيز والعمل", help: "تركيب، نقش أو تجهيز المنتج" },
    { key: "packaging", label: "التغليف", help: "علبة، كيس، بطاقة ومواد التغليف" },
    { key: "extra", label: "تكاليف إضافية", help: "أي تكلفة مباشرة أخرى" },
];

function formatMoney(value, empty = "—") {
    if (value === "" || value === null || value === undefined || Number.isNaN(Number(value))) return empty;
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function amount(money) {
    const value = money?.amount;
    return value === null || value === undefined ? null : Number(value);
}

function productCostState(product) {
    const variants = Array.isArray(product?.variants) ? product.variants : [];
    if (!variants.length) return "missing";
    if (variants.every((variant) => variant.cost_status === "ready")) return "ready";
    if (variants.some((variant) => variant.cost_status === "ready")) return "partial";
    return "missing";
}

function costTotal(cost) {
    return COST_FIELDS.reduce((sum, field) => sum + Number(cost?.[field.key] || 0), 0);
}

function optionValueMap(product) {
    const map = new Map();
    for (const option of product?.options || []) {
        for (const value of option.values || []) {
            map.set(String(value.id), { option: option.name, value: value.name });
        }
    }
    return map;
}

function variantOptionLabel(product, variant) {
    const values = optionValueMap(product);
    const labels = (variant?.related_option_values || [])
        .map((id) => values.get(String(id)))
        .filter(Boolean)
        .map((entry) => `${entry.option}: ${entry.value}`);
    return labels.length ? labels.join(" · ") : "النسخة الأساسية";
}

function statusCopy(status) {
    if (status === "awaiting_scope") return "بانتظار صلاحية سلة";
    if (status === "sale") return "معروض للبيع";
    if (status === "out") return "غير متاح";
    return status || "غير محدد";
}

function CostBadge({ state }) {
    if (state === "ready") {
        return <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-bold text-emerald-800"><CheckCircle size={14} weight="fill" /> مكتملة</span>;
    }
    if (state === "partial") {
        return <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-800"><WarningCircle size={14} weight="fill" /> جزئية</span>;
    }
    return <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-1 text-xs font-bold text-rose-800"><XCircle size={14} weight="fill" /> تحتاج تكلفة</span>;
}

function ProductGlyph({ product, large = false }) {
    const label = String(product?.sku || "P").replace(/[^A-Za-z0-9]/g, "").slice(0, 3) || "P";
    return (
        <div className={`${large ? "h-24 w-24 text-xl" : "h-14 w-14 text-sm"} flex shrink-0 items-center justify-center rounded-2xl border border-violet-200 bg-gradient-to-br from-violet-100 via-white to-teal-100 font-black text-violet-800 shadow-inner`}>
            {label}
        </div>
    );
}

function MetricCard({ label, value, tone = "slate", Icon = Package }) {
    const tones = {
        slate: "border-slate-200 bg-white text-slate-950",
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        rose: "border-rose-200 bg-rose-50 text-rose-950",
    };
    return (
        <div className={`rounded-2xl border p-4 shadow-sm ${tones[tone] || tones.slate}`}>
            <div className="flex items-center justify-between gap-3">
                <div>
                    <div className="text-xs font-bold opacity-65">{label}</div>
                    <div className="num mt-1 text-2xl font-black">{Number(value || 0).toLocaleString("en-US")}</div>
                </div>
                <div className="rounded-xl bg-white/80 p-2"><Icon size={22} weight="fill" /></div>
            </div>
        </div>
    );
}

function DetailValue({ label, value, mono = false }) {
    return (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="text-[11px] font-bold text-slate-500">{label}</div>
            <div className={`mt-1 break-words text-sm font-extrabold text-slate-900 ${mono ? "num" : ""}`}>{value ?? "—"}</div>
        </div>
    );
}

export default function MezanProducts() {
    const [products, setProducts] = useState([]);
    const [meta, setMeta] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [query, setQuery] = useState("");
    const [filter, setFilter] = useState("all");
    const [selectedProductId, setSelectedProductId] = useState("");
    const [selectedVariantId, setSelectedVariantId] = useState("");
    const [draftCost, setDraftCost] = useState({ supplier: "", labor: "", packaging: "", extra: "" });
    const [notice, setNotice] = useState("");

    async function loadProducts() {
        setLoading(true);
        setError("");
        try {
            const response = await listMezanProducts();
            const items = Array.isArray(response?.items) ? response.items : [];
            setProducts(items);
            setMeta(response?.meta || null);
            setSelectedProductId((current) => current || items[0]?.id || "");
        } catch (loadError) {
            setError(loadError?.message || "تعذّر تحميل نموذج المنتجات.");
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        loadProducts();
    }, []);

    const counts = useMemo(() => {
        const variants = products.flatMap((product) => product.variants || []);
        return {
            products: products.length,
            variants: variants.length,
            ready: variants.filter((variant) => variant.cost_status === "ready").length,
            missing: variants.filter((variant) => variant.cost_status !== "ready").length,
        };
    }, [products]);

    const visibleProducts = useMemo(() => {
        const normalizedQuery = query.trim().toLowerCase();
        return products.filter((product) => {
            const state = productCostState(product);
            if (filter === "ready" && state !== "ready") return false;
            if (filter === "missing" && state === "ready") return false;
            if (!normalizedQuery) return true;
            const haystack = [
                product.name,
                product.sku,
                product.salla_id,
                ...(product.variants || []).map((variant) => variant.sku),
            ].filter(Boolean).join(" ").toLowerCase();
            return haystack.includes(normalizedQuery);
        });
    }, [filter, products, query]);

    const selectedProduct = visibleProducts.find((product) => product.id === selectedProductId) || visibleProducts[0] || null;
    const selectedVariant = selectedProduct?.variants?.find((variant) => variant.id === selectedVariantId) || selectedProduct?.variants?.[0] || null;

    useEffect(() => {
        const firstVariant = selectedProduct?.variants?.[0];
        setSelectedVariantId((current) => selectedProduct?.variants?.some((variant) => variant.id === current) ? current : (firstVariant?.id || ""));
    }, [selectedProduct]);

    useEffect(() => {
        setDraftCost({
            supplier: selectedVariant?.cost?.supplier ?? "",
            labor: selectedVariant?.cost?.labor ?? "",
            packaging: selectedVariant?.cost?.packaging ?? "",
            extra: selectedVariant?.cost?.extra ?? "",
        });
    }, [
        selectedVariant?.id,
        selectedVariant?.cost?.supplier,
        selectedVariant?.cost?.labor,
        selectedVariant?.cost?.packaging,
        selectedVariant?.cost?.extra,
    ]);

    const draftTotal = costTotal(draftCost);
    const sellingPrice = amount(selectedVariant?.sale_price) || amount(selectedVariant?.price) || 0;
    const margin = sellingPrice - draftTotal;
    const marginPercent = sellingPrice > 0 ? (margin / sellingPrice) * 100 : 0;

    function chooseProduct(product) {
        setSelectedProductId(product.id);
        setSelectedVariantId(product.variants?.[0]?.id || "");
        setNotice("");
    }

    function savePreviewCost() {
        if (!selectedProduct || !selectedVariant) return;
        const normalized = Object.fromEntries(COST_FIELDS.map((field) => [field.key, Number(draftCost[field.key] || 0)]));
        if (costTotal(normalized) <= 0) {
            setNotice("أدخل تكلفة واحدة على الأقل قبل الحفظ التجريبي.");
            return;
        }
        setProducts((current) => current.map((product) => {
            if (product.id !== selectedProduct.id) return product;
            return {
                ...product,
                variants: (product.variants || []).map((variant) => (
                    variant.id === selectedVariant.id
                        ? { ...variant, cost: normalized, cost_status: "ready" }
                        : variant
                )),
            };
        }));
        setNotice("تم حفظ التكلفة داخل هذه المعاينة فقط. لن تُرسل إلى سلة ولن تدخل في المحاسبة.");
    }

    if (loading) {
        return (
            <div className="flex min-h-[55vh] items-center justify-center" dir="rtl" data-testid="mezan-products-loading">
                <div className="flex items-center gap-3 font-bold text-violet-700"><SpinnerGap size={28} className="animate-spin" /> جاري تجهيز منتجات Mezan OS…</div>
            </div>
        );
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="mezan-products-page">
            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-violet-100 p-3 text-violet-700"><Package size={28} weight="fill" /></div>
                        <div>
                            <div className="mb-1 flex flex-wrap items-center gap-2">
                                <h1 className="text-2xl font-black text-slate-950">منتجات Mezan OS</h1>
                                <span className="rounded-full bg-violet-100 px-2.5 py-1 text-[11px] font-black text-violet-800">نظام جديد مستقل</span>
                            </div>
                            <p className="text-sm text-slate-500">كتالوج سلة، خيارات المنتجات ومتغيراتها، ثم تكلفة مستقلة لكل SKU.</p>
                        </div>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
                        <div className="font-extrabold text-slate-900">المصدر الحالي: نموذج افتراضي</div>
                        <div className="mt-1 text-xs text-slate-500">المصدر القادم: Salla products.read</div>
                    </div>
                </div>
                <div className="border-t border-amber-200 bg-amber-50 px-4 py-4 sm:px-5" data-testid="demo-data-banner">
                    <div className="flex items-start gap-3 text-amber-950">
                        <WarningCircle size={24} weight="fill" className="mt-0.5 shrink-0 text-amber-600" />
                        <div>
                            <div className="font-black">{meta?.label || "بيانات افتراضية"}</div>
                            <div className="mt-1 text-sm leading-6 text-amber-800">المنتج المرجعي <span className="num font-black">{meta?.reference_sku || "AMS10026"}</span> يستخدم الآن هيكل سلة فقط؛ لن نفترض اسمه أو خياراته الحقيقية قبل وصول صلاحية القراءة. لا توجد أي كتابة إلى سلة أو قواعد المحاسبة أو المخزون.</div>
                        </div>
                    </div>
                </div>
            </section>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricCard label="المنتجات الافتراضية" value={counts.products} tone="violet" Icon={Package} />
                <MetricCard label="متغيرات SKU" value={counts.variants} Icon={Storefront} />
                <MetricCard label="تكلفة مكتملة" value={counts.ready} tone="emerald" Icon={CheckCircle} />
                <MetricCard label="تحتاج تكلفة" value={counts.missing} tone="rose" Icon={WarningCircle} />
            </section>

            {error && (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800"><WarningCircle size={22} className="ml-2 inline" />{error}</div>
            )}

            <section className="grid items-start gap-5 xl:grid-cols-[minmax(280px,0.9fr)_minmax(0,2.1fr)]">
                <aside className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm xl:sticky xl:top-4">
                    <div className="border-b border-slate-100 p-4">
                        <h2 className="font-black text-slate-950">كتالوج المنتجات</h2>
                        <div className="relative mt-3">
                            <MagnifyingGlass size={19} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث بالاسم أو SKU…" className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2.5 pr-10 pl-3 text-sm outline-none focus:border-violet-400" data-testid="mezan-products-search" />
                        </div>
                        <div className="mt-3 grid grid-cols-3 gap-2 text-xs font-bold">
                            {[
                                ["all", "الكل"],
                                ["ready", "مكتملة"],
                                ["missing", "ناقصة"],
                            ].map(([key, label]) => (
                                <button key={key} type="button" onClick={() => setFilter(key)} className={`rounded-lg border px-2 py-2 ${filter === key ? "border-violet-600 bg-violet-600 text-white" : "border-slate-200 bg-white text-slate-600"}`}>{label}</button>
                            ))}
                        </div>
                    </div>
                    <div className="max-h-[680px] divide-y divide-slate-100 overflow-y-auto">
                        {visibleProducts.map((product) => {
                            const active = selectedProduct?.id === product.id;
                            return (
                                <button key={product.id} type="button" onClick={() => chooseProduct(product)} className={`flex w-full gap-3 p-4 text-right transition ${active ? "bg-violet-50 ring-1 ring-inset ring-violet-200" : "hover:bg-slate-50"}`} data-testid={`mezan-product-card-${product.sku}`}>
                                    <ProductGlyph product={product} />
                                    <div className="min-w-0 flex-1">
                                        <div className="truncate text-sm font-black text-slate-950">{product.name}</div>
                                        <div className="num mt-1 text-xs text-slate-500">{product.sku}</div>
                                        <div className="mt-2 flex flex-wrap items-center gap-2">
                                            <CostBadge state={productCostState(product)} />
                                            <span className="text-[11px] text-slate-400">{product.variants?.length || 0} SKU</span>
                                        </div>
                                    </div>
                                </button>
                            );
                        })}
                        {!visibleProducts.length && <div className="p-8 text-center text-sm text-slate-500">لا توجد منتجات مطابقة.</div>}
                    </div>
                </aside>

                {selectedProduct ? (
                    <main className="space-y-5 min-w-0">
                        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                                <div className="flex min-w-0 items-start gap-4">
                                    <ProductGlyph product={selectedProduct} large />
                                    <div className="min-w-0">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h2 className="text-xl font-black text-slate-950">{selectedProduct.name}</h2>
                                            {!selectedProduct.salla_snapshot_complete && <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-black text-amber-800">مرجع غير متزامن</span>}
                                        </div>
                                        <div className="num mt-1 text-sm font-bold text-violet-700">SKU: {selectedProduct.sku}</div>
                                        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{selectedProduct.description}</p>
                                    </div>
                                </div>
                                <CostBadge state={productCostState(selectedProduct)} />
                            </div>
                            {!selectedProduct.salla_snapshot_complete && (
                                <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-bold text-amber-900">{selectedProduct.source_note}</div>
                            )}
                        </section>

                        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                            <div className="mb-4 flex items-center gap-2"><Storefront size={22} weight="fill" className="text-violet-700" /><h3 className="font-black text-slate-950">تفاصيل المنتج القادمة من سلة</h3></div>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                                <DetailValue label="معرّف سلة" value={selectedProduct.salla_id || "يظهر بعد الصلاحية"} mono />
                                <DetailValue label="SKU الأساسي" value={selectedProduct.sku} mono />
                                <DetailValue label="نوع المنتج" value={selectedProduct.type === "product" ? "منتج فعلي" : selectedProduct.type} />
                                <DetailValue label="الحالة" value={statusCopy(selectedProduct.status)} />
                                <DetailValue label="السعر قبل الضريبة" value={formatMoney(amount(selectedProduct.pre_tax_price))} mono />
                                <DetailValue label="السعر شامل الضريبة" value={formatMoney(amount(selectedProduct.taxed_price))} mono />
                                <DetailValue label="الكمية" value={selectedProduct.unlimited_quantity ? "غير محدودة" : Number(selectedProduct.quantity || 0).toLocaleString("en-US")} mono />
                                <DetailValue label="الوزن والشحن" value={selectedProduct.require_shipping ? `${selectedProduct.weight || 0} ${selectedProduct.weight_type || "kg"} · يحتاج شحن` : "لا يحتاج شحن"} />
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                                {(selectedProduct.categories || []).map((category) => <span key={category.id} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-bold text-slate-600">{category.name}</span>)}
                            </div>
                        </section>

                        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                                <div className="flex items-center gap-2"><Gear size={22} weight="fill" className="text-violet-700" /><h3 className="font-black text-slate-950">خيارات سلة وقيمها</h3></div>
                                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-600">{selectedProduct.options?.length || 0} خيارات</span>
                            </div>
                            {selectedProduct.options?.length ? (
                                <div className="grid gap-3 lg:grid-cols-2">
                                    {selectedProduct.options.map((option) => (
                                        <div key={option.id} className="rounded-xl border border-slate-200 p-4">
                                            <div className="flex flex-wrap items-center justify-between gap-2">
                                                <div className="font-black text-slate-900">{option.name}</div>
                                                <div className="flex gap-1.5 text-[10px] font-bold"><span className="rounded bg-slate-100 px-2 py-1">{option.type}</span>{option.required && <span className="rounded bg-rose-100 px-2 py-1 text-rose-700">إلزامي</span>}</div>
                                            </div>
                                            {option.values?.length ? (
                                                <div className="mt-3 flex flex-wrap gap-2">
                                                    {option.values.map((value) => <span key={value.id} className="rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-bold text-violet-900">{value.name}{amount(value.price) > 0 ? ` (+${formatMoney(amount(value.price))})` : ""}</span>)}
                                                </div>
                                            ) : <div className="mt-3 rounded-lg bg-slate-50 p-3 text-xs text-slate-500">حقل إدخال من العميل، لا ينشئ SKU مستقلًا.</div>}
                                        </div>
                                    ))}
                                </div>
                            ) : <div className="rounded-xl bg-slate-50 p-5 text-center text-sm text-slate-500">هذا منتج بسيط بدون خيارات.</div>}
                        </section>

                        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-4 sm:p-5">
                                <div className="flex items-center gap-2"><Package size={22} weight="fill" className="text-violet-700" /><h3 className="font-black text-slate-950">المتغيرات الناتجة عن الخيارات</h3></div>
                                <span className="text-xs text-slate-500">اختر SKU لإدخال تكلفته</span>
                            </div>
                            <div className="divide-y divide-slate-100">
                                {(selectedProduct.variants || []).map((variant) => {
                                    const active = selectedVariant?.id === variant.id;
                                    return (
                                        <button key={variant.id} type="button" onClick={() => { setSelectedVariantId(variant.id); setNotice(""); }} className={`grid w-full gap-2 p-4 text-right sm:grid-cols-[minmax(0,1.7fr)_minmax(110px,0.7fr)_minmax(100px,0.6fr)_auto] sm:items-center sm:px-5 ${active ? "bg-teal-50 ring-1 ring-inset ring-teal-200" : "hover:bg-slate-50"}`}>
                                            <div className="min-w-0"><div className="num truncate text-sm font-black text-slate-950">{variant.sku}</div><div className="mt-1 truncate text-xs text-slate-500">{variantOptionLabel(selectedProduct, variant)}</div></div>
                                            <div><div className="text-[10px] font-bold text-slate-400">سعر البيع</div><div className="num mt-1 text-sm font-black">{formatMoney(amount(variant.sale_price) || amount(variant.price))}</div></div>
                                            <div><div className="text-[10px] font-bold text-slate-400">المخزون</div><div className="num mt-1 text-sm font-black">{Number(variant.stock_quantity || 0).toLocaleString("en-US")}</div></div>
                                            <CostBadge state={variant.cost_status === "ready" ? "ready" : "missing"} />
                                        </button>
                                    );
                                })}
                            </div>
                        </section>

                        {selectedVariant && (
                            <section className="rounded-2xl border border-teal-200 bg-white p-4 shadow-sm sm:p-5" data-testid="cost-editor">
                                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="flex items-center gap-2"><Receipt size={24} weight="fill" className="text-teal-600" /><div><h3 className="font-black text-slate-950">تفاصيل تكلفة المتغير</h3><div className="num mt-1 text-xs font-bold text-teal-700">{selectedVariant.sku} · {variantOptionLabel(selectedProduct, selectedVariant)}</div></div></div>
                                    <span className="rounded-full bg-teal-100 px-3 py-1.5 text-xs font-black text-teal-800">مسودة افتراضية</span>
                                </div>

                                <div className="mt-5 grid gap-3 sm:grid-cols-2">
                                    {COST_FIELDS.map((field) => (
                                        <label key={field.key} className="block rounded-xl border border-slate-200 bg-slate-50 p-3">
                                            <span className="text-sm font-black text-slate-900">{field.label}</span>
                                            <span className="mt-0.5 block text-[11px] text-slate-500">{field.help}</span>
                                            <div className="relative mt-3">
                                                <input type="number" min="0" step="0.01" value={draftCost[field.key]} onChange={(event) => setDraftCost((current) => ({ ...current, [field.key]: event.target.value }))} className="num w-full rounded-lg border border-slate-200 bg-white py-2.5 pr-3 pl-12 text-left font-bold outline-none focus:border-teal-400" data-testid={`cost-input-${field.key}`} />
                                                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-xs font-bold text-slate-400">ر.س</span>
                                            </div>
                                        </label>
                                    ))}
                                </div>

                                <div className="mt-4 grid gap-3 sm:grid-cols-3">
                                    <DetailValue label="إجمالي التكلفة" value={formatMoney(draftTotal)} mono />
                                    <DetailValue label="الهامش قبل المصاريف العامة" value={formatMoney(margin)} mono />
                                    <DetailValue label="نسبة الهامش" value={`${marginPercent.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`} mono />
                                </div>

                                {notice && <div className={`mt-4 rounded-xl border p-3 text-sm font-bold ${notice.startsWith("تم") ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}`}>{notice}</div>}

                                <div className="mt-5 flex flex-col-reverse gap-3 border-t border-slate-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="text-xs leading-5 text-slate-500">الحفظ هنا داخل ذاكرة الصفحة فقط ويختفي عند إعادة التحميل.</div>
                                    <button type="button" onClick={savePreviewCost} className="inline-flex items-center justify-center gap-2 rounded-xl bg-teal-600 px-5 py-3 text-sm font-black text-white shadow-sm hover:bg-teal-700" data-testid="save-preview-cost"><NotePencil size={18} weight="fill" /> حفظ تجريبي</button>
                                </div>
                            </section>
                        )}
                    </main>
                ) : (
                    <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-slate-500">اختر منتجًا لعرض تفاصيله.</div>
                )}
            </section>
        </div>
    );
}
