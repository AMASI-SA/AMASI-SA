import { useEffect, useMemo, useState } from "react";
import {
    Calculator,
    CheckCircle,
    Cube,
    Gear,
    ImageSquare,
    Package,
    Plus,
    Receipt,
    SpinnerGap,
    Storefront,
    WarningCircle,
    Wrench,
} from "@phosphor-icons/react";

import { getMezanProductWorkspace } from "../services/mezanProductCatalog";
import {
    calculateConfigurationCost,
    getOptionRuleSummary,
    setOptionFixedCostDelta,
} from "../services/mezanProductCosting";
import {
    createPersonalizedConfiguration,
    deriveInventoryBalances,
    findReadyStockForOrder,
    formatStorageLocation,
    getConfigurationBalance,
    receiveApprovedReturnPreview,
    receivePurchasePreview,
    transformStockPreview,
} from "../services/mezanProductInventory";

function amount(money) {
    const value = money?.amount;
    return value === null || value === undefined || value === "" ? null : Number(value);
}

function formatMoney(value, empty = "غير محدد") {
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return empty;
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function formatQuantity(value) {
    if (value === null || value === undefined || value === "") return "غير محدد";
    return Number(value).toLocaleString("en-US");
}

function kindCopy(kind) {
    if (kind === "stock_component") return "مكوّن مخزني";
    if (kind === "labor_service") return "تكلفة عمل";
    if (kind === "option_cost") return "فرق خيار";
    return kind || "غير محدد";
}

function optionTypeCopy(type) {
    if (type === "text") return "نص قصير";
    if (type === "image") return "رفع صورة";
    if (type === "radio") return "اختيار واحد";
    return type;
}

function DetailValue({ label, value, mono = false, tone = "slate" }) {
    const tones = {
        slate: "border-slate-200 bg-slate-50",
        teal: "border-teal-200 bg-teal-50",
        amber: "border-amber-200 bg-amber-50",
        violet: "border-violet-200 bg-violet-50",
    };
    return (
        <div className={`rounded-xl border p-3 ${tones[tone] || tones.slate}`}>
            <div className="text-[11px] font-bold text-slate-500">{label}</div>
            <div className={`mt-1 break-words text-sm font-black text-slate-950 ${mono ? "num" : ""}`}>{value ?? "—"}</div>
        </div>
    );
}

function MetricCard({ label, value, Icon, tone = "violet" }) {
    const tones = {
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        teal: "border-teal-200 bg-teal-50 text-teal-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        slate: "border-slate-200 bg-white text-slate-950",
    };
    return (
        <div className={`rounded-2xl border p-4 shadow-sm ${tones[tone] || tones.slate}`}>
            <div className="flex items-center justify-between gap-3">
                <div>
                    <div className="text-xs font-bold opacity-65">{label}</div>
                    <div className="num mt-1 text-2xl font-black">{value}</div>
                </div>
                <div className="rounded-xl bg-white/80 p-2"><Icon size={22} weight="fill" /></div>
            </div>
        </div>
    );
}

function SectionTitle({ Icon, title, subtitle }) {
    return (
        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
                <Icon size={23} weight="fill" className="text-violet-700" />
                <h2 className="text-lg font-black text-slate-950">{title}</h2>
            </div>
            {subtitle && <div className="text-xs font-bold text-slate-500">{subtitle}</div>}
        </div>
    );
}

export default function MezanProducts() {
    const [workspace, setWorkspace] = useState(null);
    const [resources, setResources] = useState([]);
    const [recipes, setRecipes] = useState([]);
    const [inventoryState, setInventoryState] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [selectedColor, setSelectedColor] = useState("silver");
    const [customerName, setCustomerName] = useState("اسم تجريبي");
    const [attachmentPresent, setAttachmentPresent] = useState(true);
    const [attachmentFingerprint, setAttachmentFingerprint] = useState("IMG-PREVIEW-001");
    const [notice, setNotice] = useState("");
    const [openOptionEditor, setOpenOptionEditor] = useState("");
    const [purchaseQuantity, setPurchaseQuantity] = useState(10);
    const [purchaseReference, setPurchaseReference] = useState("PI-NEW-PREVIEW-001");
    const [purchaseUnitCost, setPurchaseUnitCost] = useState("");
    const [purchaseLocationId, setPurchaseLocationId] = useState("location-main-c01-r01-b01");
    const [productionQuantity, setProductionQuantity] = useState(20);
    const [productionReference, setProductionReference] = useState("MO-PREVIEW-001");
    const [productionSourceBucketKey, setProductionSourceBucketKey] = useState("");
    const [productionDestinationLocationId, setProductionDestinationLocationId] = useState("location-main-c02-r01-b01");
    const [returnQuantity, setReturnQuantity] = useState(1);
    const [returnReference, setReturnReference] = useState("RET-PREVIEW-001");
    const [returnOrderReference, setReturnOrderReference] = useState("ORDER-PREVIEW-001");
    const [returnLocationId, setReturnLocationId] = useState("location-returns-c01-r01-b01");

    useEffect(() => {
        let active = true;
        async function load() {
            setLoading(true);
            setError("");
            try {
                const result = await getMezanProductWorkspace();
                if (!active) return;
                setWorkspace(result);
                setResources(result.resources || []);
                setRecipes(result.recipes || []);
                setInventoryState({
                    configurations: result.inventory_configurations || [],
                    locations: result.inventory_locations || [],
                    movements: result.inventory_movements || [],
                    reservations: result.inventory_reservations || [],
                });
            } catch (loadError) {
                if (active) setError(loadError?.message || "تعذّر تحميل مساحة عمل المنتجات.");
            } finally {
                if (active) setLoading(false);
            }
        }
        load();
        return () => { active = false; };
    }, []);

    const product = workspace?.products?.[0] || null;
    const recipe = recipes.find((entry) => entry.product_id === product?.id) || null;
    const orderExample = workspace?.order_examples?.find((entry) => entry.product_id === product?.id) || null;

    const costing = useMemo(() => calculateConfigurationCost({
        recipe,
        resources,
        selections: { color: selectedColor },
    }), [recipe, resources, selectedColor]);

    const inventoryBalances = useMemo(() => (
        inventoryState ? deriveInventoryBalances(inventoryState) : []
    ), [inventoryState]);

    const selectedBaseConfiguration = inventoryState?.configurations?.find((entry) => (
        entry.product_id === product?.id
        && entry.stage === "base_ready"
        && entry.option_values?.color === selectedColor
    )) || null;
    const sourceBuckets = inventoryBalances.filter((row) => (
        row.configuration_id === selectedBaseConfiguration?.id
        && row.condition === "sellable"
        && row.quantity_available > 0
    ));
    const selectedSourceBucket = sourceBuckets.find((row) => (
        row.bucket_key === productionSourceBucketKey
    )) || sourceBuckets[0] || null;
    const selectedSourceLocationId = selectedSourceBucket?.location_id || "";
    const selectedSourceLotId = selectedSourceBucket?.lot_id || "";

    useEffect(() => {
        if (selectedSourceBucket?.bucket_key && selectedSourceBucket.bucket_key !== productionSourceBucketKey) {
            setProductionSourceBucketKey(selectedSourceBucket.bucket_key);
        }
    }, [productionSourceBucketKey, selectedSourceBucket?.bucket_key]);

    const destinationConfiguration = product ? createPersonalizedConfiguration({
        productId: product.id,
        sku: product.sku,
        color: selectedColor,
        customerName,
        attachmentFingerprint: attachmentPresent ? attachmentFingerprint : "",
    }) : null;

    const productionInput = selectedBaseConfiguration && destinationConfiguration ? {
        idempotency_key: `production:${productionReference}`,
        production_reference: productionReference,
        source_configuration_id: selectedBaseConfiguration.id,
        source_location_id: selectedSourceLocationId,
        source_lot_id: selectedSourceLotId,
        destination_configuration: destinationConfiguration,
        destination_location_id: productionDestinationLocationId,
        quantity_units: Number(productionQuantity),
        requires_attachment: product.options?.some((option) => option.type === "image" && option.required),
        attachment_present: attachmentPresent,
        attachment_fingerprint: attachmentFingerprint,
    } : null;

    const productionPreview = useMemo(() => (
        inventoryState && productionInput
            ? transformStockPreview(inventoryState, productionInput)
            : null
    // productionInput is intentionally represented by its scalar dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    ), [
        customerName,
        attachmentFingerprint,
        attachmentPresent,
        inventoryState,
        product?.id,
        product?.sku,
        productionQuantity,
        productionReference,
        productionDestinationLocationId,
        selectedBaseConfiguration?.id,
        selectedColor,
        selectedSourceLocationId,
        selectedSourceLotId,
    ]);

    const stockMatch = useMemo(() => (
        inventoryState && product ? findReadyStockForOrder(inventoryState, {
            productId: product.id,
            sku: product.sku,
            color: selectedColor,
            customerName,
            attachmentFingerprint: attachmentPresent ? attachmentFingerprint : "",
        }) : { matched: false, quantity_available: 0, locations: [], configuration_key: "" }
    ), [attachmentFingerprint, attachmentPresent, customerName, inventoryState, product, selectedColor]);

    const selectedColorOption = product?.options
        ?.find((option) => option.key === "color")
        ?.values?.find((value) => value.key === selectedColor);

    function updateResourceCost(resourceId, rawValue) {
        setResources((current) => current.map((resource) => {
            if (resource.id !== resourceId) return resource;
            const parsed = rawValue === "" ? null : Math.max(0, Number(rawValue || 0));
            return { ...resource, unit_cost: parsed };
        }));
        setNotice("");
    }

    function toggleOptionEditor(optionKey, valueKey, mode) {
        const key = `${optionKey}:${valueKey}:${mode}`;
        setOpenOptionEditor((current) => current === key ? "" : key);
        setNotice("");
    }

    function updateOptionCost(optionKey, valueKey, rawValue) {
        setRecipes((current) => current.map((entry) => (
            entry.id === recipe?.id
                ? setOptionFixedCostDelta(entry, optionKey, valueKey, rawValue)
                : entry
        )));
        setNotice("");
    }

    function jumpToPreviewSection(sectionId, message) {
        document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
        setNotice(message);
    }

    function openInventoryForColor(color, message) {
        setSelectedColor(color);
        jumpToPreviewSection("inventory-operations", message);
    }

    function applyPurchaseMovement() {
        if (!inventoryState || !selectedBaseConfiguration) return;
        const result = receivePurchasePreview(inventoryState, {
            invoice_reference: purchaseReference,
            configuration_id: selectedBaseConfiguration.id,
            location_id: purchaseLocationId,
            quantity_units: Number(purchaseQuantity),
            unit_cost_halalas: purchaseUnitCost === ""
                ? null
                : Math.round(Number(purchaseUnitCost) * 100),
        });
        if (!result.ok) {
            setNotice(result.error.message);
            return;
        }
        setInventoryState(result.state);
        setNotice(result.duplicate
            ? "هذه الفاتورة مُرحّلة سابقًا؛ لم تُضف الكمية مرة ثانية."
            : `تم استلام ${purchaseQuantity} قطعة في المعاينة وربطها بموقع التخزين.`);
    }

    function applyProductionMovement() {
        if (!productionPreview) return;
        if (!productionPreview.ok) {
            setNotice(productionPreview.error.message);
            return;
        }
        setInventoryState(productionPreview.state);
        setNotice(productionPreview.duplicate
            ? "أمر الإنتاج مُرحّل سابقًا؛ لم يُخصم أو يُنتج مرتين."
            : `تم تحويل ${productionQuantity} قطعة ذريًا في المعاينة: خُصمت من العام وأُضيفت كمخزون مطابق للمواصفات.`);
    }

    function applyApprovedReturnMovement() {
        if (!inventoryState || !destinationConfiguration) return;
        const existing = inventoryState.configurations.find((entry) => (
            entry.configuration_key === destinationConfiguration.configuration_key
        ));
        const stagedState = existing ? inventoryState : {
            ...inventoryState,
            configurations: [...inventoryState.configurations, destinationConfiguration],
        };
        const result = receiveApprovedReturnPreview(stagedState, {
            return_reference: returnReference,
            order_reference: returnOrderReference,
            configuration_id: existing?.id || destinationConfiguration.id,
            location_id: returnLocationId,
            quantity_units: Number(returnQuantity),
            approved_for_stock: true,
            requires_attachment: product.options?.some((option) => option.type === "image" && option.required),
        });
        if (!result.ok) {
            setNotice(result.error.message);
            return;
        }
        setInventoryState(result.state);
        setNotice(result.duplicate
            ? "المرتجع مُرحّل سابقًا؛ لم يُضف مرة ثانية."
            : `تم اعتماد المرتجع وإضافة ${returnQuantity} قطعة في موقع المرتجعات المحدد.`);
    }

    function applyPreview() {
        setNotice("تم تطبيق القيم داخل المعاينة فقط. لا توجد كتابة إلى سلة أو المخزون أو المحاسبة.");
    }

    if (loading) {
        return (
            <div className="flex min-h-[55vh] items-center justify-center" dir="rtl" data-testid="mezan-products-loading">
                <div className="flex items-center gap-3 font-bold text-violet-700"><SpinnerGap size={28} className="animate-spin" /> جاري تجهيز مساحة عمل المنتج…</div>
            </div>
        );
    }

    if (!product) {
        return <div className="rounded-2xl border border-rose-200 bg-rose-50 p-5 font-bold text-rose-800" dir="rtl">{error || "لم يتم العثور على المنتج المرجعي."}</div>;
    }

    const inventoryResources = resources.filter((entry) => entry.track_inventory);
    const laborResources = resources.filter((entry) => !entry.track_inventory);
    const inventoryLocations = inventoryState?.locations || [];
    const locationById = new Map(inventoryLocations.map((entry) => [entry.id, entry]));
    const configurationById = new Map((inventoryState?.configurations || []).map((entry) => [entry.id, entry]));
    const sourceBefore = selectedBaseConfiguration
        ? getConfigurationBalance(inventoryState, selectedBaseConfiguration.id, {
            condition: "sellable",
            location_id: selectedSourceLocationId,
            lot_id: selectedSourceLotId,
        })
        : 0;
    const destinationCurrent = inventoryState?.configurations?.find((entry) => (
        entry.configuration_key === destinationConfiguration?.configuration_key
    ));
    const destinationBefore = destinationCurrent
        ? getConfigurationBalance(inventoryState, destinationCurrent.id, { condition: "sellable" })
        : 0;
    const sourceAfter = productionPreview?.ok && selectedBaseConfiguration
        ? getConfigurationBalance(productionPreview.state, selectedBaseConfiguration.id, {
            condition: "sellable",
            location_id: selectedSourceLocationId,
            lot_id: selectedSourceLotId,
        })
        : sourceBefore;
    const previewDestination = productionPreview?.state?.configurations?.find((entry) => (
        entry.configuration_key === destinationConfiguration?.configuration_key
    ));
    const destinationAfter = productionPreview?.ok && previewDestination
        ? getConfigurationBalance(productionPreview.state, previewDestination.id, { condition: "sellable" })
        : destinationBefore;

    return (
        <div className="space-y-5" dir="rtl" data-testid="mezan-products-page">
            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-violet-100 p-3 text-violet-700"><Package size={30} weight="fill" /></div>
                        <div>
                            <div className="flex flex-wrap items-center gap-2">
                                <h1 className="text-2xl font-black text-slate-950">منتجات Mezan OS</h1>
                                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-[11px] font-black text-emerald-800">مرجع سلة موثّق</span>
                            </div>
                            <p className="mt-1 text-sm text-slate-500">منتج سلة، مكوناته المشتركة، قواعد تكلفته، ومخزون التركيبات الجاهزة.</p>
                        </div>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
                        <div className="num font-black text-slate-950">SKU: {product.sku}</div>
                        <div className="mt-1 text-xs text-slate-500">المزامنة الحية تنتظر products.read</div>
                    </div>
                </div>
                <div className="border-t border-amber-200 bg-amber-50 px-4 py-4 sm:px-5" data-testid="manual-reference-banner">
                    <div className="flex items-start gap-3 text-amber-950">
                        <WarningCircle size={24} weight="fill" className="mt-0.5 shrink-0 text-amber-600" />
                        <div>
                            <div className="font-black">{workspace?.meta?.label}</div>
                            <div className="mt-1 text-sm leading-6 text-amber-800">هذا المنتج حقيقي ومطابق للصور المرسلة، لكن القيم ليست استجابة API حية بعد. صورة العميل واسمه الكامل غير محفوظين في الكود العام، ولا توجد أي كتابة خارج هذه الصفحة.</div>
                        </div>
                    </div>
                </div>
            </section>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricCard label="سعر البيع في سلة" value={formatMoney(amount(product.price))} Icon={Storefront} />
                <MetricCard label="سعر التكلفة المرجعي" value="محجوب" Icon={Receipt} tone="amber" />
                <MetricCard label="المكونات المركزية" value={inventoryResources.length} Icon={Cube} tone="teal" />
                <MetricCard label="خدمات العمل" value={laborResources.length} Icon={Wrench} tone="slate" />
            </section>

            {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">{error}</div>}

            <section className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                    <SectionTitle Icon={Storefront} title="بيانات المنتج في سلة" subtitle="موثّقة من شاشة الإدارة" />
                    <div className="flex flex-col gap-4 sm:flex-row">
                        <div className="flex h-28 w-full shrink-0 items-center justify-center rounded-2xl border border-violet-200 bg-gradient-to-br from-violet-100 via-white to-teal-100 text-xl font-black text-violet-800 sm:w-28">AMS</div>
                        <div className="min-w-0 flex-1">
                            <h2 className="text-xl font-black text-slate-950">{product.name}</h2>
                            <p className="mt-2 text-sm leading-6 text-slate-600">{product.description}</p>
                            <div className="mt-3 flex flex-wrap gap-2">
                                {product.categories.map((category) => <span key={category.id} className="rounded-full bg-violet-100 px-3 py-1 text-xs font-bold text-violet-800">{category.name}</span>)}
                                <span className="rounded-full bg-teal-100 px-3 py-1 text-xs font-bold text-teal-800">يتطلب شحن</span>
                                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-700">كمية غير محدودة في سلة</span>
                            </div>
                        </div>
                    </div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <DetailValue label="SKU" value={product.sku} mono />
                        <DetailValue label="سعر البيع" value={formatMoney(amount(product.price))} mono />
                        <DetailValue label="سعر التكلفة في سلة" value="موثّق ومحجوب في الكود العام" tone="amber" />
                        <DetailValue label="الوزن" value={`${product.weight} كجم`} mono />
                    </div>
                </div>

                <div className="rounded-2xl border border-teal-200 bg-white p-4 shadow-sm sm:p-5" data-testid="verified-order-example">
                    <SectionTitle Icon={Receipt} title="شكل المنتج داخل الطلب" subtitle="Snapshot موثّق ومقنّع" />
                    <div className="rounded-xl border border-teal-200 bg-teal-50 p-4">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <div className="font-black text-slate-950">{orderExample.product_name}</div>
                                <div className="num mt-1 text-xs font-bold text-teal-700">{orderExample.sku} · الكمية {orderExample.quantity}</div>
                            </div>
                            <div className="rounded-xl bg-white p-2 text-teal-700"><ImageSquare size={25} weight="fill" /></div>
                        </div>
                        <div className="mt-4 space-y-2">
                            {orderExample.selected_options.map((option) => (
                                <div key={option.key} className="flex items-center justify-between gap-3 rounded-lg bg-white px-3 py-2 text-sm">
                                    <span className="font-bold text-slate-500">{option.label}</span>
                                    <span className="font-black text-slate-950">
                                        {option.attachment_present ? "صورة مرفقة — غير محفوظة في Fixture" : (option.display_value || option.value)}
                                    </span>
                                </div>
                            ))}
                        </div>
                        <div className="mt-3 grid grid-cols-2 gap-2">
                            <DetailValue label="سعر الوحدة" value={formatMoney(amount(orderExample.unit_price))} mono tone="teal" />
                            <DetailValue label="الإجمالي" value={formatMoney(amount(orderExample.total))} mono tone="teal" />
                        </div>
                    </div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                <SectionTitle Icon={Gear} title="خيارات المنتج في سلة" subtitle="كل الحقول إلزامية" />
                <div className="grid gap-3 lg:grid-cols-3">
                    {product.options.map((option) => (
                        <div key={option.id} className="rounded-xl border border-slate-200 p-4">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <div className="font-black text-slate-950">{option.name}</div>
                                    <div className="mt-1 text-xs text-slate-500">{optionTypeCopy(option.type)} · {option.placeholder}</div>
                                </div>
                                <span className="rounded bg-rose-100 px-2 py-1 text-[10px] font-black text-rose-700">مطلوب</span>
                            </div>
                            {option.values?.length > 0 ? (
                                <div className="mt-4 space-y-2">
                                    {option.values.map((value) => {
                                        const summary = getOptionRuleSummary(recipe, option.key, value.key);
                                        const costEditorKey = `${option.key}:${value.key}:cost`;
                                        const baseConfiguration = inventoryState?.configurations?.find((entry) => (
                                            entry.product_id === product.id
                                            && entry.stage === "base_ready"
                                            && entry.option_values?.color === value.key
                                        ));
                                        const baseQuantity = baseConfiguration
                                            ? getConfigurationBalance(inventoryState, baseConfiguration.id, { condition: "sellable" })
                                            : 0;
                                        return (
                                            <div key={value.id} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
                                                <div className="flex items-center justify-between gap-3">
                                                    <span className="font-black">{value.name}</span>
                                                    <span className="text-xs font-bold text-slate-500">سعر سلة +0 · تكلفة داخلية +{summary.fixed_cost_delta}</span>
                                                </div>
                                                <div className="mt-2 flex flex-wrap gap-2">
                                                    <button type="button" onClick={() => toggleOptionEditor(option.key, value.key, "cost")} className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-white px-2.5 py-1 text-[11px] font-black text-violet-700"><Plus size={12} weight="bold" /> تكلفة</button>
                                                    <button type="button" onClick={() => openInventoryForColor(value.key, `إدارة مخزون اللون ${value.name} تتم بحركة شراء أو تحويل أو مرتجع، وليست بتعديل رقم مباشر.`)} className="inline-flex items-center gap-1 rounded-full border border-teal-200 bg-white px-2.5 py-1 text-[11px] font-black text-teal-700"><Package size={12} weight="fill" /> إدارة الحركات</button>
                                                </div>
                                                <div className="mt-2 text-[11px] font-bold text-slate-500">الرصيد العام الموثّق: <span className="num text-slate-950">{formatQuantity(baseQuantity)}</span> · الرصيد يتغير من المستندات المُرحّلة فقط</div>
                                                {openOptionEditor === costEditorKey && (
                                                    <label className="mt-3 block rounded-lg border border-violet-200 bg-violet-50 p-2">
                                                        <span className="text-[11px] font-black text-violet-900">فرق التكلفة الداخلي لخيار {value.name}</span>
                                                        <div className="relative mt-1">
                                                            <input type="number" min="0" step="0.01" value={summary.fixed_cost_delta} onChange={(event) => updateOptionCost(option.key, value.key, event.target.value)} className="num w-full rounded-lg border border-violet-200 bg-white py-2 pr-3 pl-12 text-left font-bold outline-none focus:border-violet-500" data-testid={`option-cost-${value.key}`} />
                                                            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[10px] font-bold text-slate-400">ر.س</span>
                                                        </div>
                                                    </label>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : (
                                <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs text-slate-500">
                                    <div>القيمة تأتي من العميل ولا تنشئ SKU جديدًا.</div>
                                    <div className="mt-2 flex flex-wrap gap-2">
                                        <button type="button" onClick={() => jumpToPreviewSection("cost-resource-catalog", "أضف تكلفة هذا الحقل من خدمة العمل المرتبطة به داخل الكتالوج.")} className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-white px-2.5 py-1 text-[11px] font-black text-violet-700"><Plus size={12} weight="bold" /> تكلفة عمل</button>
                                        <button type="button" onClick={() => jumpToPreviewSection("inventory-operations", "أدخل المواصفات والكمية في تحويل الإنتاج؛ سيخصم النظام من العام ويُنشئ مخزونًا مطابقًا مع موقع تخزين.")} className="inline-flex items-center gap-1 rounded-full border border-teal-200 bg-white px-2.5 py-1 text-[11px] font-black text-teal-700"><Wrench size={12} weight="fill" /> تحويل إنتاج</button>
                                    </div>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            </section>

            <section id="cost-resource-catalog" className="scroll-mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="cost-resource-catalog">
                <SectionTitle Icon={Cube} title="كتالوج مكونات التكلفة المركزي" subtitle="المكوّن يُنشأ مرة واحدة ثم تربطه الوصفات" />
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[760px] text-right text-sm">
                        <thead>
                            <tr className="border-b border-slate-200 text-xs text-slate-500">
                                <th className="px-3 py-3">المكوّن / الخدمة</th>
                                <th className="px-3 py-3">النوع</th>
                                <th className="px-3 py-3">تكلفة الوحدة</th>
                                <th className="px-3 py-3">المخزون المتاح</th>
                                <th className="px-3 py-3">الحالة</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                            {resources.map((resource) => (
                                <tr key={resource.id}>
                                    <td className="px-3 py-3"><div className="font-black text-slate-950">{resource.name}</div><div className="num mt-0.5 text-[11px] text-slate-400">{resource.code}</div></td>
                                    <td className="px-3 py-3"><span className={`rounded-full px-2.5 py-1 text-xs font-bold ${resource.track_inventory ? "bg-teal-100 text-teal-800" : "bg-violet-100 text-violet-800"}`}>{kindCopy(resource.kind)}</span></td>
                                    <td className="px-3 py-3">
                                        <input type="number" min="0" step="0.01" value={resource.unit_cost ?? ""} onChange={(event) => updateResourceCost(resource.id, event.target.value)} placeholder="أدخل التكلفة" className="num w-36 rounded-lg border border-slate-200 px-3 py-2 text-left outline-none focus:border-violet-400" />
                                    </td>
                                    <td className="px-3 py-3">
                                        {resource.track_inventory ? <button type="button" onClick={() => jumpToPreviewSection("inventory-operations", "الرصيد مشتق من الحركات الموثّقة ولا يُعدّل من جدول التكلفة.")} className="rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-xs font-black text-teal-800">عرض الحركات</button> : <span className="text-xs font-bold text-slate-400">لا يتتبع مخزونًا</span>}
                                    </td>
                                    <td className="px-3 py-3">
                                        {resource.unit_cost === null ? <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-800">تحتاج تكلفة</span> : <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-bold text-emerald-800"><CheckCircle size={13} weight="fill" /> جاهزة</span>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm font-bold text-blue-900">تكلفة سلة الإجمالية تبقى مرجعًا خاصًا ومحجوبًا في الكود العام؛ ولا نوزعها افتراضيًا على الكيس والعلبة والسلسال والعمل. الرصيد المخزني لا يُكتب هنا، بل يُشتق من حركات مُرحّلة لها مستند وموقع.</div>
            </section>

            <section id="inventory-operations" className="scroll-mt-4 rounded-2xl border border-teal-200 bg-white p-4 shadow-sm sm:p-5" data-testid="inventory-operations">
                <SectionTitle Icon={Package} title="إدارة المخزون بالحركات" subtitle="شراء · تحويل/تخصيص · مرتجع مع موقع تخزين" />
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-bold leading-6 text-amber-900">
                    لا يوجد حقل لتعديل الرصيد مباشرة. كل كمية ناتجة عن مستند مُرحّل له رقم فريد وموقع كامل؛ والمستند المكرر لا يضيف الكمية مرتين.
                </div>

                {stockMatch.matched && (
                    <div className="mt-3 rounded-xl border border-emerald-300 bg-emerald-50 p-4" data-testid="ready-stock-alert">
                        <div className="flex items-start gap-3">
                            <CheckCircle size={24} weight="fill" className="mt-0.5 shrink-0 text-emerald-600" />
                            <div>
                                <div className="font-black text-emerald-950">تنبيه: توجد قطعة مطابقة لمواصفات الطلب في المخزون</div>
                                <div className="mt-1 text-sm font-bold text-emerald-800">المتاح: <span className="num">{stockMatch.quantity_available}</span> قطعة</div>
                                {stockMatch.locations.map((entry) => <div key={entry.bucket_key} className="mt-1 text-sm text-emerald-900">{entry.location_label}</div>)}
                            </div>
                        </div>
                    </div>
                )}

                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {inventoryBalances.filter((row) => row.quantity_on_hand !== 0).map((row) => {
                        const configuration = configurationById.get(row.configuration_id);
                        const location = locationById.get(row.location_id);
                        return (
                            <div key={row.bucket_key} className={`rounded-xl border p-4 ${row.valid ? "border-slate-200 bg-slate-50" : "border-rose-300 bg-rose-50"}`}>
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <div className="font-black text-slate-950">{configuration?.label || configuration?.configuration_key}</div>
                                        <div className="mt-1 text-[11px] font-bold text-slate-500">{formatStorageLocation(location)}</div>
                                    </div>
                                    <div className="num text-3xl font-black text-teal-700">{row.quantity_available}</div>
                                </div>
                                <div className="num mt-2 text-[10px] text-slate-400">{row.lot_id}</div>
                            </div>
                        );
                    })}
                </div>

                <div className="mt-5 grid items-start gap-4 xl:grid-cols-3">
                    <div className="rounded-2xl border border-violet-200 bg-violet-50/50 p-4" data-testid="purchase-stock-form">
                        <div className="flex items-center gap-2"><Receipt size={22} weight="fill" className="text-violet-700" /><h3 className="font-black text-slate-950">1. استلام فاتورة شراء</h3></div>
                        <p className="mt-2 text-xs leading-5 text-slate-600">المثال المرحّل حاليًا: فضي 50 وذهبي 100، وكل لون محفوظ في خانة مستقلة. التكلفة تأتي من فاتورة الشراء.</p>
                        <div className="mt-3 grid grid-cols-2 gap-2">
                            {product.options.find((option) => option.key === "color").values.map((value) => <button key={value.key} type="button" onClick={() => setSelectedColor(value.key)} className={`rounded-lg border px-3 py-2 text-xs font-black ${selectedColor === value.key ? "border-violet-600 bg-violet-600 text-white" : "border-slate-200 bg-white text-slate-700"}`}>{value.name}</button>)}
                        </div>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">مرجع فاتورة الشراء</span><input value={purchaseReference} onChange={(event) => { setPurchaseReference(event.target.value); setNotice(""); }} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left outline-none focus:border-violet-400" dir="ltr" /></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">الكمية المستلمة</span><input type="number" min="1" step="1" value={purchaseQuantity} onChange={(event) => { setPurchaseQuantity(event.target.value); setNotice(""); }} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left outline-none focus:border-violet-400" /></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">تكلفة الوحدة من الفاتورة</span><div className="relative mt-1"><input type="number" min="0" step="0.01" value={purchaseUnitCost} onChange={(event) => { setPurchaseUnitCost(event.target.value); setNotice(""); }} placeholder="اختياري في المعاينة" className="num w-full rounded-lg border border-slate-200 bg-white py-2 pr-3 pl-12 text-left outline-none focus:border-violet-400" /><span className="absolute left-3 top-1/2 -translate-y-1/2 text-[10px] font-bold text-slate-400">ر.س</span></div></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">موقع الاستلام</span><select value={purchaseLocationId} onChange={(event) => { setPurchaseLocationId(event.target.value); setNotice(""); }} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold outline-none focus:border-violet-400">{inventoryLocations.map((location) => <option key={location.id} value={location.id}>{formatStorageLocation(location)}</option>)}</select></label>
                        <button type="button" onClick={applyPurchaseMovement} className="mt-3 w-full rounded-lg bg-violet-700 px-3 py-2.5 text-sm font-black text-white">ترحيل الاستلام في المعاينة</button>
                    </div>

                    <div className="rounded-2xl border border-teal-300 bg-teal-50/60 p-4" data-testid="production-transform-form">
                        <div className="flex items-center gap-2"><Wrench size={22} weight="fill" className="text-teal-700" /><h3 className="font-black text-slate-950">2. تحويل تصنيع/تخصيص</h3></div>
                        <p className="mt-2 text-xs leading-5 text-slate-600">يخصم من المخزون العام ويضيف نفس العدد كمخزون مطابق للاسم واللون. السطران يُرحّلان معًا أو يُرفضان معًا.</p>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">مرجع أمر الإنتاج</span><input value={productionReference} onChange={(event) => { setProductionReference(event.target.value); setNotice(""); }} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left outline-none focus:border-teal-400" dir="ltr" /></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">الاسم المراد تجهيزه</span><input value={customerName} onChange={(event) => { setCustomerName(event.target.value); setNotice(""); }} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-teal-400" /></label>
                        <label className="mt-3 flex items-center gap-2 rounded-lg border border-slate-200 bg-white p-2 text-xs font-bold"><input type="checkbox" checked={attachmentPresent} onChange={(event) => { setAttachmentPresent(event.target.checked); setNotice(""); }} /> صورة المنتج المخصص متوفرة</label>
                        <label className="mt-2 block"><span className="text-[11px] font-black text-slate-600">بصمة/مرجع الصورة للمطابقة</span><input value={attachmentFingerprint} onChange={(event) => { setAttachmentFingerprint(event.target.value); setNotice(""); }} disabled={!attachmentPresent} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left text-xs outline-none focus:border-teal-400 disabled:bg-slate-100" dir="ltr" /></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">كمية الإنتاج</span><input type="number" min="1" step="1" value={productionQuantity} onChange={(event) => { setProductionQuantity(event.target.value); setNotice(""); }} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left outline-none focus:border-teal-400" /></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">دفعة وموقع المصدر</span><select value={selectedSourceBucket?.bucket_key || ""} onChange={(event) => { setProductionSourceBucketKey(event.target.value); setNotice(""); }} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold outline-none focus:border-teal-400"><option value="" disabled>اختر دفعة متاحة</option>{sourceBuckets.map((bucket) => <option key={bucket.bucket_key} value={bucket.bucket_key}>{bucket.lot_id} · المتاح {bucket.quantity_available} · {formatStorageLocation(locationById.get(bucket.location_id))}</option>)}</select></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">موقع المنتج المخصص بعد التحويل</span><select value={productionDestinationLocationId} onChange={(event) => { setProductionDestinationLocationId(event.target.value); setNotice(""); }} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold outline-none focus:border-teal-400">{inventoryLocations.map((location) => <option key={location.id} value={location.id}>{formatStorageLocation(location)}</option>)}</select></label>
                        <div className="mt-3 grid grid-cols-2 gap-2 text-center text-xs">
                            <div className="rounded-lg border border-slate-200 bg-white p-2"><div className="font-bold text-slate-500">العام قبل ← بعد</div><div className="num mt-1 text-lg font-black text-slate-950">{sourceBefore} ← {sourceAfter}</div></div>
                            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-2"><div className="font-bold text-emerald-700">المخصص قبل ← بعد</div><div className="num mt-1 text-lg font-black text-emerald-900">{destinationBefore} ← {destinationAfter}</div></div>
                        </div>
                        <div className="mt-2 space-y-1 text-[11px] font-bold text-slate-600"><div>المصدر: {formatStorageLocation(locationById.get(selectedSourceLocationId))}</div><div>الوجهة: {formatStorageLocation(locationById.get(productionDestinationLocationId))}</div><div>تكلفة المرحلة: خدمة النحت فقط؛ القطعة العامة جاهزة قبل التخصيص.</div></div>
                        {productionPreview && !productionPreview.ok && <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 p-2 text-xs font-bold text-rose-800">{productionPreview.error.message}</div>}
                        <button type="button" onClick={applyProductionMovement} disabled={!productionPreview?.ok} className="mt-3 w-full rounded-lg bg-teal-700 px-3 py-2.5 text-sm font-black text-white disabled:cursor-not-allowed disabled:opacity-40">ترحيل التحويل في المعاينة</button>
                    </div>

                    <div className="rounded-2xl border border-amber-200 bg-amber-50/60 p-4" data-testid="approved-return-form">
                        <div className="flex items-center gap-2"><Package size={22} weight="fill" className="text-amber-700" /><h3 className="font-black text-slate-950">3. مرتجع معتمد للمخزون</h3></div>
                        <p className="mt-2 text-xs leading-5 text-slate-600">بعد مراجعة القطعة والموافقة على الاحتفاظ بها، تُسجل بنفس مواصفاتها ويُحدد موقعها؛ والطلب المطابق لاحقًا يُظهر مكانها.</p>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">مرجع المرتجع</span><input value={returnReference} onChange={(event) => { setReturnReference(event.target.value); setNotice(""); }} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left outline-none focus:border-amber-400" dir="ltr" /></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">رقم الطلب الأصلي</span><input value={returnOrderReference} onChange={(event) => { setReturnOrderReference(event.target.value); setNotice(""); }} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left outline-none focus:border-amber-400" dir="ltr" /></label>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">الكمية المعتمدة</span><input type="number" min="1" step="1" value={returnQuantity} onChange={(event) => { setReturnQuantity(event.target.value); setNotice(""); }} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left outline-none focus:border-amber-400" /></label>
                        <div className="mt-3 rounded-lg border border-amber-200 bg-white p-2 text-xs font-bold text-amber-900">المواصفات: {selectedColorOption?.name} · الاسم: {customerName || "غير مكتمل"}</div>
                        <label className="mt-3 block"><span className="text-[11px] font-black text-slate-600">موقع القطعة بعد الاعتماد</span><select value={returnLocationId} onChange={(event) => { setReturnLocationId(event.target.value); setNotice(""); }} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold outline-none focus:border-amber-400">{inventoryLocations.map((location) => <option key={location.id} value={location.id}>{formatStorageLocation(location)}</option>)}</select></label>
                        <button type="button" onClick={applyApprovedReturnMovement} className="mt-3 w-full rounded-lg bg-amber-600 px-3 py-2.5 text-sm font-black text-white">اعتماد وإضافة في المعاينة</button>
                    </div>
                </div>

                {notice && <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm font-bold text-blue-900">{notice}</div>}

                <div className="mt-5 overflow-x-auto">
                    <table className="w-full min-w-[760px] text-right text-sm" data-testid="inventory-movement-ledger">
                        <thead><tr className="border-b border-slate-200 text-xs text-slate-500"><th className="px-3 py-3">المستند</th><th className="px-3 py-3">النوع</th><th className="px-3 py-3">الحركة</th><th className="px-3 py-3">الموقع</th><th className="px-3 py-3">الحالة</th></tr></thead>
                        <tbody className="divide-y divide-slate-100">
                            {(inventoryState?.movements || []).flatMap((movement) => (movement.lines || []).map((line) => {
                                const configuration = configurationById.get(line.configuration_id) || inventoryState.configurations.find((entry) => entry.id === line.configuration_id);
                                return (
                                    <tr key={`${movement.id}:${line.role}:${line.configuration_id}`}>
                                        <td className="num px-3 py-3 text-xs font-bold">{movement.reference?.id}</td>
                                        <td className="px-3 py-3 font-bold">{movement.type === "purchase_receipt" ? "فاتورة شراء" : movement.type === "stock_transform" ? "تحويل إنتاج" : "مرتجع معتمد"}</td>
                                        <td className="px-3 py-3"><div className="font-black text-slate-950">{configuration?.label || configuration?.configuration_key}</div><div className={`num mt-1 font-black ${line.delta_units > 0 ? "text-emerald-700" : "text-rose-700"}`}>{line.delta_units > 0 ? "+" : ""}{line.delta_units}</div></td>
                                        <td className="px-3 py-3 text-xs font-bold text-slate-600">{formatStorageLocation(locationById.get(line.location_id))}</td>
                                        <td className="px-3 py-3"><span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-black text-emerald-800">مُرحّل</span></td>
                                    </tr>
                                );
                            }))}
                        </tbody>
                    </table>
                </div>
                <div className="mt-3 text-[11px] font-bold text-slate-500">هذه الصفحة ما زالت معاينة داخل الذاكرة. الحفظ الدائم لاحقًا يحتاج معاملة Backend ذرية وفهرسًا فريدًا لرقم العملية.</div>
            </section>

            <section className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="product-recipe">
                    <SectionTitle Icon={Wrench} title="وصفة المنتج BOM" subtitle={`الإصدار ${recipe.version} · مسودة`} />
                    <div className="space-y-2">
                        {costing.lines.map((line) => (
                            <div key={line.id} className="grid gap-2 rounded-xl border border-slate-200 p-3 sm:grid-cols-[minmax(0,1.4fr)_90px_130px] sm:items-center">
                                <div><div className="font-black text-slate-950">{line.name}</div><div className="mt-0.5 text-[11px] text-slate-500">{line.reason || kindCopy(line.kind)} · {line.source.startsWith("option:") ? "حسب اختيار العميل" : "أساسي"}</div></div>
                                <div className="num text-sm font-bold">× {line.quantity}</div>
                                <div className={`num text-sm font-black ${line.cost_known ? "text-emerald-700" : "text-amber-700"}`}>{line.cost_known ? formatMoney(line.total_cost) : "تكلفة ناقصة"}</div>
                            </div>
                        ))}
                    </div>
                    <div className="mt-4 rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950">
                        <div className="font-black">قاعدة اللون المحددة الآن: {selectedColorOption?.name}</div>
                        <div className="mt-1 leading-6">اختيار الفضي يربط «سلسال فضي» ويضيف فرق تكلفة داخلي `5 ر.س` مرة واحدة. هذه ليست زيادة على سعر سلة.</div>
                    </div>
                </div>

                <div id="configuration-calculator" className="scroll-mt-4 rounded-2xl border border-teal-200 bg-white p-4 shadow-sm sm:p-5" data-testid="configuration-calculator">
                    <SectionTitle Icon={Calculator} title="حاسبة التركيبة" subtitle="تكلفة + مخزون جاهز" />
                    <div className="space-y-3">
                        <label className="block"><span className="text-xs font-black text-slate-600">الاسم المطلوب</span><input value={customerName} onChange={(event) => { setCustomerName(event.target.value); setNotice(""); }} className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none focus:border-teal-400" data-testid="configuration-name" /></label>
                        <div><div className="text-xs font-black text-slate-600">اللون</div><div className="mt-1 grid grid-cols-2 gap-2">{product.options.find((option) => option.key === "color").values.map((value) => <button key={value.key} type="button" onClick={() => { setSelectedColor(value.key); setNotice(""); }} className={`rounded-xl border px-3 py-2.5 text-sm font-black ${selectedColor === value.key ? "border-teal-600 bg-teal-600 text-white" : "border-slate-200 bg-white text-slate-700"}`}>{value.name}</button>)}</div></div>
                        <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm font-bold"><input type="checkbox" checked={attachmentPresent} onChange={(event) => { setAttachmentPresent(event.target.checked); setNotice(""); }} /> صورة العميل مرفقة</label>
                        <label className="block"><span className="text-xs font-black text-slate-600">بصمة الصورة للمطابقة</span><input value={attachmentFingerprint} onChange={(event) => { setAttachmentFingerprint(event.target.value); setNotice(""); }} disabled={!attachmentPresent} className="num mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-left outline-none focus:border-teal-400 disabled:bg-slate-100" dir="ltr" /></label>
                    </div>
                    <div className="mt-4 grid gap-2 sm:grid-cols-2">
                        <DetailValue label="التكلفة المعروفة حاليًا" value={formatMoney(costing.known_total)} mono tone="violet" />
                        <DetailValue label="بنود ناقصة التكلفة" value={costing.missing_lines.length} mono tone={costing.complete ? "teal" : "amber"} />
                        <DetailValue label="مطابقة مخزون جاهز" value={stockMatch.matched ? "مطابقة موجودة" : "لا توجد مطابقة"} tone={stockMatch.matched ? "teal" : "amber"} />
                        <DetailValue label="الكمية الجاهزة المتاحة" value={formatQuantity(stockMatch.quantity_available)} mono tone={stockMatch.quantity_available > 0 ? "teal" : "amber"} />
                    </div>
                    <div className="num mt-3 break-all rounded-lg bg-slate-950 p-3 text-left text-[11px] text-slate-200" dir="ltr">{stockMatch.configuration_key}</div>
                    {!attachmentPresent && <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-800">الطلب يحتاج صورة؛ لا يمكن اعتباره مكتمل المواصفات.</div>}
                    {notice && <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-bold text-emerald-800">{notice}</div>}
                    <button type="button" onClick={applyPreview} className="mt-4 w-full rounded-xl bg-teal-600 px-4 py-3 text-sm font-black text-white hover:bg-teal-700">تطبيق في المعاينة</button>
                    <div className="mt-2 text-center text-[11px] text-slate-400">الحفظ غير دائم ولا يخصم أي مخزون.</div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="ready-configuration-stock">
                <SectionTitle Icon={Package} title="مخزون المنتجات الجاهزة حسب المواصفات" subtitle="مشتق من التحويلات والمرتجعات المعتمدة" />
                <div className="grid gap-3 md:grid-cols-2">
                    {inventoryBalances.filter((row) => (
                        configurationById.get(row.configuration_id)?.stage === "personalized_ready"
                        && row.quantity_available > 0
                    )).map((entry) => {
                        const configuration = configurationById.get(entry.configuration_id);
                        const color = configuration.option_values.color === "silver" ? "فضي" : "ذهبي";
                        const name = configuration.custom_values.customer_name.normalized;
                        const available = entry.quantity_available;
                        return (
                            <div key={entry.bucket_key} className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                                <div className="flex items-center justify-between gap-3"><div><div className="font-black text-slate-950">{product.name}</div><div className="mt-1 text-sm font-bold text-slate-600">الاسم: {name} · اللون: {color}</div></div><div className={`num text-3xl font-black ${available > 0 ? "text-emerald-700" : "text-rose-700"}`}>{available}</div></div>
                                <div className="mt-2 text-xs font-bold text-emerald-900">{formatStorageLocation(locationById.get(entry.location_id))}</div>
                                <div className="num mt-3 break-all text-[10px] text-slate-500" dir="ltr">{configuration.configuration_key}</div>
                            </div>
                        );
                    })}
                    {!inventoryBalances.some((row) => (
                        configurationById.get(row.configuration_id)?.stage === "personalized_ready"
                        && row.quantity_available > 0
                    )) && <div className="md:col-span-2 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-5 text-center text-sm font-bold text-slate-500">لا يوجد مخزون مخصص مُرحّل بعد. جرّب تحويل 20 قطعة في قسم إدارة المخزون.</div>}
                </div>
                <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-600">صورة العميل نفسها لا تُخزن في Fixture العام، لكن بصمتها تدخل في مفتاح المطابقة حتى لا يطابق النظام قطعتين بصورتين مختلفتين. كل رصيد مخصص مرتبط أيضًا بموقع تخزين، ولا يبقى محسوبًا ضمن الرصيد العام بعد التحويل.</div>
            </section>
        </div>
    );
}
