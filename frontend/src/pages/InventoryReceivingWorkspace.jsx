import { useEffect, useMemo, useRef, useState } from "react";
import {
    Barcode,
    Camera,
    CheckCircle,
    ClipboardText,
    Cube,
    MagnifyingGlass,
    MapPin,
    Package,
    Plus,
    SpinnerGap,
    Trash,
    WarningCircle,
    Warehouse,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import StockPreparationOrders from "../components/inventory/StockPreparationOrders";
import SallaInventorySync from "../components/inventory/SallaInventorySync";
import {
    loadInventoryReceivingCatalog,
    loadPurchaseReceivingLocationSuggestions,
    newInventoryReceiptIdempotencyKey,
    postPurchaseInventoryReceipt,
} from "../services/mezanInventoryReceiving";

const EMPTY_CATALOG = {
    actor_workplace: {},
    purchase_invoices: [],
    products: [],
    warehouses: [],
    locations: [],
    recent_receipts: [],
    inventory_health: [],
    inventory_alerts: [],
};

const ERROR_MESSAGES = {
    inventory_receipt_idempotency_conflict: "تكررت العملية ببيانات مختلفة. حدّث الصفحة ثم أعد المحاولة.",
    purchase_invoice_not_found: "فاتورة الشراء غير موجودة.",
    purchase_invoice_line_not_found: "بند فاتورة الشراء غير موجود.",
    mezan_product_not_found: "المنتج غير موجود في منتجات ميزان.",
    purchase_line_product_sku_mismatch: "SKU المنتج لا يطابق SKU بند فاتورة الشراء.",
    inventory_location_not_found: "خانة المخزن غير موجودة.",
    inventory_warehouse_not_assigned: "هذا المخزن غير مسند إلى الموظف الحالي.",
    inventory_location_disabled: "خانة المخزن متوقفة.",
    inventory_location_not_permanent: "هذه الخانة ليست مخصصة للتخزين الدائم.",
    inventory_location_product_mismatch: "الخانة تحتوي منتجًا أو تجهيزًا مختلفًا. استخدم الخانة المقترحة أو خانة فارغة.",
    inventory_location_barcode_mismatch: "الباركود المدخل لا يطابق الخانة المختارة.",
    purchase_invoice_quantity_exceeded: "الكمية تتجاوز المتبقي في بند فاتورة الشراء.",
    inventory_location_capacity_exceeded: "الخانة لا تتسع لهذه الكمية.",
    inventory_variant_required: "اختر خيار المنتج الذي ستسجل كميته.",
    inventory_variant_not_found: "خيار المنتج غير موجود أو لم تُحمّل تفاصيله.",
    inventory_variants_not_loaded: "حمّل تفاصيل المنتج وخياراته أولًا قبل تسجيل مخزونه.",
    fulfillment_permission_required: "لا توجد صلاحية لاستلام مخزون المشتريات.",
};

function errorMessage(error, fallback = "تعذر إتمام العملية.") {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    return ERROR_MESSAGES[detail?.code] || detail?.message || fallback;
}

function Field({ label, hint, children }) {
    return (
        <label className="block">
            <span className="mb-1.5 block text-sm font-extrabold text-slate-800">{label}</span>
            {children}
            {hint && <span className="mt-1.5 block text-xs leading-5 text-slate-500">{hint}</span>}
        </label>
    );
}

const controlClass = "w-full rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-900 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100 disabled:bg-slate-100 disabled:text-slate-400";

function receiptStateLabel(value) {
    return value === "ready_complete" ? "جاهز كامل" : "يحتاج تجهيز";
}

function inventoryHealthLabel(value) {
    const labels = {
        out_of_stock: "نفد المخزون",
        preorder: "حجز مسبق",
        low_stock: "قرب النفاد",
        healthy: "متوفر",
    };
    return labels[value] || value;
}

function CameraBarcodeScanner({ onDetected, onClose }) {
    const videoRef = useRef(null);
    const [cameraError, setCameraError] = useState("");

    useEffect(() => {
        let stopped = false;
        let stream;
        let animationFrame;

        async function start() {
            if (!navigator.mediaDevices?.getUserMedia || !globalThis.BarcodeDetector) {
                setCameraError("هذا المتصفح لا يدعم قراءة الباركود بالكاميرا. استخدم قارئ الباركود.");
                return;
            }
            try {
                const supported = await globalThis.BarcodeDetector.getSupportedFormats();
                const formats = ["code_39", "code_128", "qr_code"].filter((value) => supported.includes(value));
                const detector = new globalThis.BarcodeDetector(formats.length ? { formats } : undefined);
                stream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: { ideal: "environment" } },
                    audio: false,
                });
                if (stopped || !videoRef.current) return;
                videoRef.current.srcObject = stream;
                await videoRef.current.play();

                const detect = async () => {
                    if (stopped || !videoRef.current) return;
                    try {
                        const rows = await detector.detect(videoRef.current);
                        const value = String(rows?.[0]?.rawValue || "").trim();
                        if (value) {
                            onDetected(value);
                            return;
                        }
                    } catch {
                        // Keep scanning; transient frames may not contain a barcode.
                    }
                    animationFrame = requestAnimationFrame(detect);
                };
                animationFrame = requestAnimationFrame(detect);
            } catch (error) {
                setCameraError(
                    error?.name === "NotAllowedError"
                        ? "يجب السماح للكاميرا حتى يتم مسح باركود الخانة."
                        : "تعذر تشغيل الكاميرا. استخدم قارئ الباركود.",
                );
            }
        }

        start();
        return () => {
            stopped = true;
            if (animationFrame) cancelAnimationFrame(animationFrame);
            for (const track of stream?.getTracks?.() || []) track.stop();
        };
    }, [onDetected]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4" dir="rtl">
            <div className="w-full max-w-lg rounded-3xl bg-white p-4 shadow-2xl">
                <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                        <h3 className="font-black text-slate-950">تصوير باركود الخانة</h3>
                        <p className="mt-1 text-xs text-slate-500">وجّه الكاميرا إلى الباركود المثبت على الخانة المقترحة.</p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl border px-3 py-2 text-sm font-bold">إغلاق</button>
                </div>
                {cameraError ? (
                    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold text-amber-900">{cameraError}</div>
                ) : (
                    <video ref={videoRef} muted playsInline className="aspect-[4/3] w-full rounded-2xl bg-black object-cover" />
                )}
            </div>
        </div>
    );
}

export default function InventoryReceivingWorkspace() {
    const [activeSection, setActiveSection] = useState("purchase_receiving");
    const [catalog, setCatalog] = useState(EMPTY_CATALOG);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [invoiceId, setInvoiceId] = useState("");
    const [lineId, setLineId] = useState("");
    const [productId, setProductId] = useState("");
    const [variantId, setVariantId] = useState("");
    const [productSearch, setProductSearch] = useState("");
    const [warehouseId, setWarehouseId] = useState("");
    const [locationId, setLocationId] = useState("");
    const [locationSuggestions, setLocationSuggestions] = useState([]);
    const [loadingSuggestions, setLoadingSuggestions] = useState(false);
    const [scannedBarcode, setScannedBarcode] = useState("");
    const [cameraOpen, setCameraOpen] = useState(false);
    const [quantity, setQuantity] = useState(1);
    const [preparationState, setPreparationState] = useState("requires_preparation");
    const [specifications, setSpecifications] = useState([{ name: "", value: "" }]);

    const refresh = async ({ quiet = false } = {}) => {
        if (!quiet) setLoading(true);
        try {
            const data = await loadInventoryReceivingCatalog();
            setCatalog({ ...EMPTY_CATALOG, ...(data || {}) });
        } catch (error) {
            toast.error(errorMessage(error, "تعذر تحميل شاشة استلام المشتريات."));
        } finally {
            if (!quiet) setLoading(false);
        }
    };

    useEffect(() => {
        refresh();
    }, []);

    const invoice = useMemo(
        () => catalog.purchase_invoices.find((row) => row.id === invoiceId),
        [catalog.purchase_invoices, invoiceId],
    );
    const line = useMemo(
        () => (invoice?.lines || []).find((row) => row.id === lineId),
        [invoice, lineId],
    );
    const product = useMemo(
        () => catalog.products.find((row) => row.mezan_product_id === productId),
        [catalog.products, productId],
    );
    const selectedVariant = useMemo(
        () => (product?.variants || []).find((row) => String(row.id) === variantId),
        [product, variantId],
    );
    const filteredProducts = useMemo(() => {
        const query = productSearch.trim().toLocaleLowerCase("ar");
        if (!query) return catalog.products;
        return catalog.products.filter((row) => (
            String(row.name || "").toLocaleLowerCase("ar").includes(query)
            || String(row.sku || "").toLocaleLowerCase("ar").includes(query)
            || String(row.barcode || "").toLocaleLowerCase("ar").includes(query)
        ));
    }, [catalog.products, productSearch]);
    const selectedLocation = useMemo(
        () => locationSuggestions.find((row) => row.id === locationId)
            || catalog.locations.find((row) => row.id === locationId),
        [locationSuggestions, catalog.locations, locationId],
    );
    const receiptSpecifications = useMemo(
        () => specifications
            .map((row) => ({ name: row.name.trim(), value: row.value.trim() }))
            .filter((row) => row.name && row.value),
        [specifications],
    );

    useEffect(() => {
        if (!line) return;
        setQuantity(1);
        const invoiceSku = String(line.sku || "").trim().toUpperCase();
        if (!invoiceSku) return;
        const matchingProduct = catalog.products.find((row) => (
            String(row.sku || "").trim().toUpperCase() === invoiceSku
            || (row.variants || []).some(
                (variant) => String(variant.sku || "").trim().toUpperCase() === invoiceSku,
            )
        ));
        if (matchingProduct) {
            setProductId(matchingProduct.mezan_product_id);
            setProductSearch(matchingProduct.name || matchingProduct.sku || "");
        }
    }, [line, catalog.products]);

    useEffect(() => {
        setVariantId("");
        const invoiceSku = String(line?.sku || "").trim().toUpperCase();
        if (!invoiceSku) return;
        const matchingVariant = (product?.variants || []).find(
            (row) => String(row.sku || "").trim().toUpperCase() === invoiceSku,
        );
        if (matchingVariant) setVariantId(String(matchingVariant.id));
    }, [product, line]);

    useEffect(() => {
        const defaultWarehouseId = catalog.actor_workplace?.warehouse_id;
        if (
            defaultWarehouseId
            && !warehouseId
            && catalog.warehouses.some((row) => row.id === defaultWarehouseId)
        ) {
            setWarehouseId(defaultWarehouseId);
        }
    }, [catalog.actor_workplace, catalog.warehouses, warehouseId]);

    useEffect(() => {
        setLocationId("");
        setLocationSuggestions([]);
        setScannedBarcode("");
    }, [warehouseId]);

    useEffect(() => {
        if (
            !invoice?.id
            || !line?.id
            || !product?.mezan_product_id
            || !warehouseId
            || Number(quantity) < 1
            || ((product.variants || []).length > 0 && !selectedVariant)
        ) {
            setLocationSuggestions([]);
            setLocationId("");
            return undefined;
        }
        let cancelled = false;
        const timer = setTimeout(async () => {
            setLoadingSuggestions(true);
            try {
                const data = await loadPurchaseReceivingLocationSuggestions({
                    purchase_invoice_id: invoice.id,
                    purchase_invoice_line_id: line.id,
                    product_id: product.mezan_product_id,
                    variant_id: selectedVariant?.id ? String(selectedVariant.id) : null,
                    warehouse_id: warehouseId,
                    quantity: Number(quantity),
                    preparation_state: preparationState,
                    specifications: receiptSpecifications,
                });
                if (cancelled) return;
                const suggestions = data?.suggestions || [];
                setLocationSuggestions(suggestions);
                setLocationId((current) => (
                    suggestions.some((row) => row.id === current)
                        ? current
                        : data?.recommended_location_id || ""
                ));
                setScannedBarcode("");
            } catch (error) {
                if (cancelled) return;
                setLocationSuggestions([]);
                setLocationId("");
                toast.error(errorMessage(error, "تعذر اقتراح خانة التخزين."));
            } finally {
                if (!cancelled) setLoadingSuggestions(false);
            }
        }, 350);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [
        invoice?.id,
        line?.id,
        product?.mezan_product_id,
        product?.variants,
        selectedVariant,
        warehouseId,
        quantity,
        preparationState,
        receiptSpecifications,
    ]);

    const setSpecification = (index, key, value) => {
        setSpecifications((current) => current.map(
            (row, rowIndex) => rowIndex === index ? { ...row, [key]: value } : row,
        ));
    };

    const submit = async (event) => {
        event.preventDefault();
        if (!invoice || !line || !product || !selectedLocation) {
            toast.error("اختر الفاتورة والبند والمنتج والمخزن والخانة.");
            return;
        }
        if ((product.variants || []).length > 0 && !selectedVariant) {
            toast.error("اختر خيار المنتج الذي ستُسجل كميته.");
            return;
        }
        if (Number(product.variants_count || 0) > 0 && !(product.variants || []).length) {
            toast.error("حمّل تفاصيل المنتج وخياراته أولًا قبل تسجيل مخزونه.");
            return;
        }
        if (!scannedBarcode.trim()) {
            toast.error("امسح أو أدخل باركود الخانة.");
            return;
        }
        if (
            String(selectedLocation.barcode_value || selectedLocation.code || "").trim().toUpperCase()
            !== scannedBarcode.trim().toUpperCase()
        ) {
            toast.error("الباركود المصوّر لا يطابق الخانة المقترحة.");
            return;
        }
        if (Number(quantity) > Number(line.remaining_quantity || 0)) {
            toast.error("الكمية أكبر من المتبقي في بند الفاتورة.");
            return;
        }
        setSaving(true);
        try {
            const result = await postPurchaseInventoryReceipt({
                idempotency_key: newInventoryReceiptIdempotencyKey(),
                purchase_invoice_id: invoice.id,
                purchase_invoice_line_id: line.id,
                product_id: product.mezan_product_id,
                variant_id: selectedVariant?.id ? String(selectedVariant.id) : null,
                location_id: selectedLocation.id,
                scanned_barcode: scannedBarcode.trim(),
                quantity: Number(quantity),
                preparation_state: preparationState,
                specifications: receiptSpecifications,
            });
            toast.success(result?.duplicate ? "هذه العملية مسجلة مسبقًا." : "تم استلام الكمية وربطها بخانة المخزن.");
            setScannedBarcode("");
            setSpecifications([{ name: "", value: "" }]);
            await refresh({ quiet: true });
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return (
            <div className="flex min-h-[360px] items-center justify-center" dir="rtl">
                <SpinnerGap size={34} className="animate-spin text-violet-700" />
            </div>
        );
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="inventory-receiving-v2-page">
            <header className="overflow-hidden rounded-3xl border border-violet-200 bg-white shadow-sm">
                <div className="bg-gradient-to-l from-violet-800 via-violet-700 to-indigo-700 px-6 py-7 text-white">
                    <div className="flex items-start gap-4">
                        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-white/15">
                            <ClipboardText size={28} weight="duotone" />
                        </span>
                        <div>
                            <div className="text-sm font-bold text-violet-100">Mezan OS V2</div>
                            <h1 className="mt-1 text-2xl font-black sm:text-3xl">استلام المشتريات والمخزون</h1>
                            <p className="mt-2 max-w-3xl text-sm leading-6 text-violet-100">
                                المنتج يبقى عامًا للمتجر، أما الكمية فتُسجل على مخزن الفرع وخانته. حدّد هل الدفعة جاهزة كاملة أم تحتاج تجهيزًا.
                            </p>
                        </div>
                    </div>
                </div>
            </header>

            <nav className="grid gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm sm:grid-cols-3" aria-label="أقسام استلام المخزون">
                <button
                    type="button"
                    onClick={() => setActiveSection("purchase_receiving")}
                    className={`flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-black transition ${activeSection === "purchase_receiving" ? "bg-violet-700 text-white" : "text-slate-600 hover:bg-slate-50"}`}
                >
                    <ClipboardText size={20} weight="duotone" /> استلام فاتورة شراء
                </button>
                <button
                    type="button"
                    onClick={() => setActiveSection("stock_preparation")}
                    className={`flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-black transition ${activeSection === "stock_preparation" ? "bg-emerald-700 text-white" : "text-slate-600 hover:bg-slate-50"}`}
                >
                    <Cube size={20} weight="duotone" /> أمر تجهيز مخزون
                </button>
                <button
                    type="button"
                    onClick={() => setActiveSection("salla_sync")}
                    className={`flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-black transition ${activeSection === "salla_sync" ? "bg-sky-700 text-white" : "text-slate-600 hover:bg-slate-50"}`}
                >
                    <Warehouse size={20} weight="duotone" /> مزامنة سلة
                </button>
            </nav>

            {activeSection === "salla_sync" ? (
                <SallaInventorySync />
            ) : activeSection === "stock_preparation" ? (
                <StockPreparationOrders
                    inventoryCatalog={catalog}
                    onInventoryChanged={() => refresh({ quiet: true })}
                />
            ) : (
                <>
            {catalog.inventory_alerts.length > 0 && (
                <section className="rounded-3xl border border-amber-200 bg-amber-50 p-5 shadow-sm" data-testid="inventory-alerts">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="flex items-start gap-3">
                            <WarningCircle size={27} className="mt-0.5 shrink-0 text-amber-700" weight="duotone" />
                            <div>
                                <h2 className="text-lg font-black text-amber-950">تنبيهات المخزون ({catalog.inventory_alerts.length})</h2>
                                <p className="mt-1 text-xs leading-5 text-amber-800">الرصيد المتاح محسوب بعد خصم حجوزات الطلبات. لا يُنفّذ أي إغلاق أو تعديل خارجي في سلة من هذه الشاشة.</p>
                            </div>
                        </div>
                    </div>
                    <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                        {catalog.inventory_alerts.slice(0, 12).map((row) => (
                            <article key={row.mezan_product_id || row.salla_product_id} className="rounded-2xl border border-amber-200 bg-white p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <div className="truncate font-black text-slate-950">{row.name}</div>
                                        <div className="mt-1 font-mono text-xs text-slate-500">{row.sku || "بدون SKU"}</div>
                                    </div>
                                    <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-extrabold ${row.health_status === "out_of_stock" ? "bg-rose-100 text-rose-800" : row.health_status === "preorder" ? "bg-violet-100 text-violet-800" : "bg-amber-100 text-amber-800"}`}>
                                        {inventoryHealthLabel(row.health_status)}
                                    </span>
                                </div>
                                <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                                    <div className="rounded-xl bg-slate-50 p-2"><div className="text-slate-500">في المخزن</div><div className="mt-1 font-black">{row.on_hand_quantity}</div></div>
                                    <div className="rounded-xl bg-slate-50 p-2"><div className="text-slate-500">محجوز</div><div className="mt-1 font-black">{row.reserved_quantity}</div></div>
                                    <div className="rounded-xl bg-slate-50 p-2"><div className="text-slate-500">متاح</div><div className="mt-1 font-black">{row.available_quantity}</div></div>
                                </div>
                                <p className="mt-3 text-xs font-bold leading-5 text-slate-600">
                                    {row.health_status === "preorder"
                                        ? "طلبات هذا المنتج تُصنف حجزًا مسبقًا وتنتظر وصول مخزون."
                                        : row.health_status === "out_of_stock"
                                            ? "سياسة المنتج تطلب إيقاف البيع حتى تسجيل كمية جديدة."
                                            : `وصل إلى حد التنبيه (${row.low_stock_threshold})؛ ابدأ التوريد.`}
                                </p>
                            </article>
                        ))}
                    </div>
                </section>
            )}

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.55fr)]">
                <form onSubmit={submit} className="space-y-6 rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
                    <section>
                        <div className="mb-4 flex items-center gap-2">
                            <ClipboardText size={22} className="text-violet-700" weight="duotone" />
                            <h2 className="text-lg font-black text-slate-950">1. مرجع الشراء والمنتج</h2>
                        </div>
                        <div className="grid gap-4 md:grid-cols-2">
                            <Field label="فاتورة الشراء">
                                <select
                                    value={invoiceId}
                                    onChange={(event) => {
                                        setInvoiceId(event.target.value);
                                        setLineId("");
                                        setProductId("");
                                        setVariantId("");
                                    }}
                                    className={controlClass}
                                >
                                    <option value="">اختر الفاتورة</option>
                                    {catalog.purchase_invoices.filter((row) => row.has_remaining).map((row) => (
                                        <option key={row.id} value={row.id}>
                                            {row.invoice_number || "بدون رقم"} — {row.supplier_name} — {row.invoice_date}
                                        </option>
                                    ))}
                                </select>
                            </Field>
                            {invoice && (
                                <div className="md:col-span-2">
                                    <div className="mb-2 text-sm font-extrabold text-slate-800">منتجات الفاتورة</div>
                                    <div className="grid gap-2 sm:grid-cols-2">
                                        {(invoice.lines || []).filter((row) => Number(row.remaining_quantity) > 0).map((row) => (
                                            <button
                                                key={row.id}
                                                type="button"
                                                onClick={() => {
                                                    setLineId(row.id);
                                                    setProductId("");
                                                    setVariantId("");
                                                    setProductSearch(row.product_name || row.sku || "");
                                                }}
                                                className={`rounded-2xl border p-3 text-right transition ${lineId === row.id ? "border-violet-500 bg-violet-50 ring-2 ring-violet-100" : "border-slate-200 hover:bg-slate-50"}`}
                                            >
                                                <span className="block font-black text-slate-950">{row.product_name}</span>
                                                <span className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                                                    <span className="font-mono">{row.sku || "بدون SKU"}</span>
                                                    <span>كمية الفاتورة {row.quantity}</span>
                                                    <span className="font-bold text-violet-700">متبقي {row.remaining_quantity}</span>
                                                </span>
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}
                            {line && product ? (
                                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 md:col-span-2">
                                    <div className="flex items-center gap-2 text-sm font-black text-emerald-950">
                                        <CheckCircle size={20} /> تم ربط بند الفاتورة بمنتج ميزان
                                    </div>
                                    <div className="mt-2 font-black text-slate-950">{product.name}</div>
                                    <div className="mt-1 font-mono text-xs text-slate-600">{selectedVariant?.sku || product.sku || line.sku || "بدون SKU"}</div>
                                </div>
                            ) : line ? (
                                <>
                                    <Field label="البحث عن منتج ميزان" hint="لم نجد تطابقًا تلقائيًا؛ اختر المنتج الصحيح مرة واحدة.">
                                        <div className="relative">
                                            <MagnifyingGlass size={19} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                            <input
                                                value={productSearch}
                                                onChange={(event) => setProductSearch(event.target.value)}
                                                placeholder="اسم المنتج أو SKU أو الباركود"
                                                className={`${controlClass} pr-10`}
                                            />
                                        </div>
                                    </Field>
                                    <Field label="مطابقة المنتج">
                                        <select
                                            value={productId}
                                            onChange={(event) => {
                                                setProductId(event.target.value);
                                                setVariantId("");
                                            }}
                                            className={controlClass}
                                        >
                                            <option value="">اختر المنتج</option>
                                            {filteredProducts.slice(0, 250).map((row) => (
                                                <option key={row.mezan_product_id} value={row.mezan_product_id}>
                                                    {row.name} {row.sku ? `— ${row.sku}` : ""}
                                                </option>
                                            ))}
                                        </select>
                                    </Field>
                                </>
                            ) : null}
                            {(product?.variants || []).length > 0 && (
                                <Field label="خيار المنتج" hint="الكمية ستُحفظ وتُزامن لهذا الخيار فقط.">
                                    <select value={variantId} onChange={(event) => setVariantId(event.target.value)} className={controlClass}>
                                        <option value="">اختر اللون / المقاس</option>
                                        {(product.variants || []).map((row) => (
                                            <option key={row.id} value={row.id}>
                                                {row.display_name || row.name || row.sku || `خيار ${row.id}`}
                                                {row.sku ? ` — ${row.sku}` : ""}
                                            </option>
                                        ))}
                                    </select>
                                </Field>
                            )}
                        </div>
                        {line && product && line.sku && (selectedVariant?.sku || product.sku) && String(line.sku).trim().toUpperCase() !== String(selectedVariant?.sku || product.sku).trim().toUpperCase() && (
                            <div className="mt-4 flex gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-bold text-amber-900">
                                <WarningCircle size={20} className="shrink-0" />
                                المنتج المختار لا يطابق SKU بند الفاتورة، ولن يسمح النظام بحفظ الاستلام.
                            </div>
                        )}
                    </section>

                    <section className="border-t border-slate-100 pt-6">
                        <div className="mb-4 flex items-center gap-2">
                            <Warehouse size={22} className="text-violet-700" weight="duotone" />
                            <h2 className="text-lg font-black text-slate-950">2. مخزن الفرع والخانة</h2>
                        </div>
                        <div className="grid gap-4 md:grid-cols-2">
                            <Field
                                label="مخزن / فرع الموظف"
                                hint={catalog.actor_workplace?.warehouse_id === warehouseId ? "تم اختياره تلقائيًا من مقر عمل الموظف." : "لا يمكن للموظف الاستلام إلا في الفروع المسندة إليه."}
                            >
                                <select
                                    value={warehouseId}
                                    onChange={(event) => setWarehouseId(event.target.value)}
                                    className={controlClass}
                                    disabled={catalog.warehouses.length === 1}
                                >
                                    <option value="">اختر المخزن</option>
                                    {catalog.warehouses.map((row) => (
                                        <option key={row.id} value={row.id}>{row.name} — {row.code} — {row.city}</option>
                                    ))}
                                </select>
                            </Field>
                            <Field label="الكمية" hint={line ? `المتبقي في بند الفاتورة: ${line.remaining_quantity}` : ""}>
                                <input
                                    type="number"
                                    min="1"
                                    max={line?.remaining_quantity || undefined}
                                    step="1"
                                    value={quantity}
                                    onChange={(event) => setQuantity(event.target.value)}
                                    className={controlClass}
                                />
                            </Field>
                            <div className="rounded-2xl border border-sky-200 bg-sky-50 p-4 md:col-span-2">
                                <div className="text-xs font-bold text-sky-700">نوع الاستخدام</div>
                                <div className="mt-1 flex items-center gap-2 font-black text-sky-950">
                                    <Warehouse size={20} /> تخزين دائم
                                </div>
                                <p className="mt-1 text-xs leading-5 text-sky-800">فواتير الشراء لا تُستلم في خانات التجميع المؤقت أو المرتجعات أو التالف.</p>
                            </div>
                            <div className="md:col-span-2">
                                <div className="mb-2 flex items-center justify-between gap-3">
                                    <div>
                                        <div className="text-sm font-extrabold text-slate-800">الموقع المقترح من النظام</div>
                                        <p className="mt-1 text-xs text-slate-500">الأولوية لخانة فيها نفس المنتج والتجهيز، ثم خانة تخزين دائم فارغة.</p>
                                    </div>
                                    {loadingSuggestions && <SpinnerGap size={22} className="animate-spin text-violet-700" />}
                                </div>
                                {!loadingSuggestions && product && warehouseId && locationSuggestions.length === 0 && (
                                    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-900">
                                        لا توجد خانة تخزين دائم مناسبة لهذه الكمية. أنشئ خانة جديدة أو فرّغ مساحة داخل مخزن الفرع.
                                    </div>
                                )}
                                <div className="grid gap-2 sm:grid-cols-2">
                                    {locationSuggestions.slice(0, 6).map((row, index) => (
                                        <button
                                            key={row.id}
                                            type="button"
                                            onClick={() => {
                                                setLocationId(row.id);
                                                setScannedBarcode("");
                                            }}
                                            className={`rounded-2xl border p-4 text-right transition ${locationId === row.id ? "border-violet-500 bg-violet-50 ring-2 ring-violet-100" : "border-slate-200 hover:bg-slate-50"}`}
                                        >
                                            <span className="flex items-start justify-between gap-3">
                                                <span>
                                                    <span className="block font-black text-slate-950">
                                                        {row.section_name || row.section_code || "القسم العام"}
                                                        {" · "}
                                                        {row.cabinet_name || `دولاب ${row.cabinet_code || "—"}`}
                                                    </span>
                                                    <span className="mt-1 block font-mono text-xs text-slate-500">الخانة {row.code}</span>
                                                </span>
                                                {index === 0 && <span className="rounded-full bg-violet-100 px-2 py-1 text-[10px] font-black text-violet-800">الأفضل</span>}
                                            </span>
                                            <span className={`mt-3 inline-flex rounded-full px-2.5 py-1 text-[11px] font-black ${row.recommendation === "same_configuration" ? "bg-emerald-100 text-emerald-800" : "bg-sky-100 text-sky-800"}`}>
                                                {row.recommendation_label}
                                            </span>
                                            <span className="mt-2 block text-xs text-slate-600">
                                                حاليًا {row.current_quantity}
                                                {row.remaining_capacity === null ? " · السعة غير محددة" : ` · متاح ${row.remaining_capacity}`}
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <div className="md:col-span-2">
                                <Field label="تأكيد وضع المنتج بتصوير باركود الخانة" hint="لن يُسجل المخزون إلا إذا طابق الباركود الخانة المقترحة.">
                                    <div className="flex flex-col gap-2 sm:flex-row">
                                        <div className="relative flex-1">
                                            <Barcode size={20} className="absolute right-3 top-1/2 -translate-y-1/2 text-violet-600" />
                                            <input
                                                value={scannedBarcode}
                                                onChange={(event) => setScannedBarcode(event.target.value)}
                                                placeholder="امسح باركود الخانة هنا"
                                                autoComplete="off"
                                                disabled={!selectedLocation}
                                                className={`${controlClass} pr-10 font-mono`}
                                            />
                                        </div>
                                        <button
                                            type="button"
                                            onClick={() => setCameraOpen(true)}
                                            disabled={!selectedLocation}
                                            className="inline-flex items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm font-black text-violet-800 disabled:opacity-40"
                                        >
                                            <Camera size={20} /> فتح الكاميرا
                                        </button>
                                    </div>
                                </Field>
                                {selectedLocation && (
                                    <div className="mt-2 flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-700">
                                        <MapPin size={18} className="shrink-0 text-violet-700" />
                                        توجّه إلى {selectedLocation.section_name || "القسم العام"}، {selectedLocation.cabinet_name || `الدولاب ${selectedLocation.cabinet_code || "—"}`}، الخانة <b className="font-mono">{selectedLocation.code}</b> ثم امسح باركودها.
                                    </div>
                                )}
                            </div>
                        </div>
                    </section>

                    <section className="border-t border-slate-100 pt-6">
                        <div className="mb-4 flex items-center gap-2">
                            <Package size={22} className="text-violet-700" weight="duotone" />
                            <h2 className="text-lg font-black text-slate-950">3. حالة الدفعة ومواصفاتها</h2>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <button
                                type="button"
                                onClick={() => setPreparationState("requires_preparation")}
                                className={`rounded-2xl border p-4 text-right transition ${preparationState === "requires_preparation" ? "border-amber-400 bg-amber-50 ring-2 ring-amber-100" : "border-slate-200 hover:bg-slate-50"}`}
                            >
                                <span className="flex items-center gap-2 font-black text-slate-950"><Cube size={22} className="text-amber-600" /> يحتاج تجهيز</span>
                                <span className="mt-2 block text-xs leading-5 text-slate-600">مخزون أساسي متوفر، لكن خدمة الاسم أو القص أو التطريز لم تُنفذ بعد.</span>
                            </button>
                            <button
                                type="button"
                                onClick={() => setPreparationState("ready_complete")}
                                className={`rounded-2xl border p-4 text-right transition ${preparationState === "ready_complete" ? "border-emerald-400 bg-emerald-50 ring-2 ring-emerald-100" : "border-slate-200 hover:bg-slate-50"}`}
                            >
                                <span className="flex items-center gap-2 font-black text-slate-950"><CheckCircle size={22} className="text-emerald-600" /> جاهز كامل</span>
                                <span className="mt-2 block text-xs leading-5 text-slate-600">كل خدماته مكتملة. إذا طابقت المواصفات الطلب، يتجاوز التجهيز ويتجه للشحن.</span>
                            </button>
                        </div>

                        <div className="mt-5 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                            <div className="mb-3 flex items-center justify-between gap-3">
                                <div>
                                    <div className="font-black text-slate-950">المواصفات الفعلية لهذه الدفعة</div>
                                    <p className="mt-1 text-xs leading-5 text-slate-500">مثال: الاسم = عبير، اللون = ذهبي. اترك الاسم فارغًا للمخزون الأساسي غير المنفذ.</p>
                                </div>
                                <button
                                    type="button"
                                    onClick={() => setSpecifications((current) => [...current, { name: "", value: "" }])}
                                    className="inline-flex shrink-0 items-center gap-1 rounded-xl border border-violet-200 bg-white px-3 py-2 text-xs font-extrabold text-violet-700 hover:bg-violet-50"
                                >
                                    <Plus size={16} weight="bold" /> إضافة
                                </button>
                            </div>
                            <div className="space-y-2">
                                {specifications.map((row, index) => (
                                    <div key={index} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] gap-2">
                                        <input value={row.name} onChange={(event) => setSpecification(index, "name", event.target.value)} placeholder="المواصفة، مثل الاسم" className={controlClass} />
                                        <input value={row.value} onChange={(event) => setSpecification(index, "value", event.target.value)} placeholder="القيمة، مثل عبير" className={controlClass} />
                                        <button
                                            type="button"
                                            onClick={() => setSpecifications((current) => current.length === 1 ? [{ name: "", value: "" }] : current.filter((_, rowIndex) => rowIndex !== index))}
                                            className="rounded-xl border border-rose-200 bg-white px-3 text-rose-600 hover:bg-rose-50"
                                            aria-label="حذف المواصفة"
                                        >
                                            <Trash size={18} />
                                        </button>
                                    </div>
                                ))}
                            </div>
                            {preparationState === "ready_complete" && (
                                <div className="mt-3 flex gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs font-bold leading-5 text-emerald-900">
                                    <CheckCircle size={18} className="mt-0.5 shrink-0" />
                                    سجّل كل المواصفات التي تميّز القطعة الجاهزة. لا يتجاوز الطلب التجهيز إلا عند التطابق الكامل معها.
                                </div>
                            )}
                        </div>
                    </section>

                    <button
                        type="submit"
                        disabled={saving || loadingSuggestions || !selectedLocation}
                        className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-violet-700 px-5 py-4 text-base font-black text-white shadow-sm transition hover:bg-violet-800 disabled:cursor-wait disabled:opacity-60"
                    >
                        {saving ? <SpinnerGap size={22} className="animate-spin" /> : <CheckCircle size={22} weight="duotone" />}
                        {saving ? "جارٍ تسجيل الاستلام…" : "تسجيل المخزون في الخانة"}
                    </button>
                </form>

                <aside className="space-y-5">
                    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                        <h2 className="flex items-center gap-2 text-lg font-black text-slate-950">
                            <Package size={22} className="text-violet-700" /> ملخص الاستلام
                        </h2>
                        <dl className="mt-4 space-y-3 text-sm">
                            <div className="flex justify-between gap-3 border-b border-slate-100 pb-3"><dt className="text-slate-500">المنتج</dt><dd className="text-left font-extrabold text-slate-900">{product?.name || "—"}</dd></div>
                            <div className="flex justify-between gap-3 border-b border-slate-100 pb-3"><dt className="text-slate-500">الخيار</dt><dd className="text-left font-bold text-slate-900">{selectedVariant?.display_name || selectedVariant?.name || "منتج بسيط"}</dd></div>
                            <div className="flex justify-between gap-3 border-b border-slate-100 pb-3"><dt className="text-slate-500">SKU</dt><dd className="font-mono font-bold text-slate-900">{selectedVariant?.sku || product?.sku || line?.sku || "—"}</dd></div>
                            <div className="flex justify-between gap-3 border-b border-slate-100 pb-3"><dt className="text-slate-500">الخانة</dt><dd className="font-bold text-slate-900">{selectedLocation?.code || "—"}</dd></div>
                            <div className="flex justify-between gap-3"><dt className="text-slate-500">حالة الدفعة</dt><dd className={`font-extrabold ${preparationState === "ready_complete" ? "text-emerald-700" : "text-amber-700"}`}>{receiptStateLabel(preparationState)}</dd></div>
                        </dl>
                    </section>

                    <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                        <h2 className="text-lg font-black text-slate-950">آخر عمليات الاستلام</h2>
                        <div className="mt-4 space-y-3">
                            {catalog.recent_receipts.length === 0 && <p className="rounded-xl bg-slate-50 p-4 text-sm text-slate-500">لا توجد عمليات استلام مسجلة بعد.</p>}
                            {catalog.recent_receipts.slice(0, 10).map((receipt) => (
                                <article key={receipt.id} className="rounded-2xl border border-slate-200 p-3">
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <div className="font-extrabold text-slate-950">{receipt.product_name}</div>
                                            <div className="mt-1 text-xs text-slate-500">{receipt.location_code} · كمية {receipt.quantity}</div>
                                        </div>
                                        <span className={`rounded-full px-2.5 py-1 text-[11px] font-extrabold ${receipt.preparation_state === "ready_complete" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>
                                            {receiptStateLabel(receipt.preparation_state)}
                                        </span>
                                    </div>
                                    {Object.keys(receipt.specifications || {}).length > 0 && (
                                        <div className="mt-2 flex flex-wrap gap-1">
                                            {Object.entries(receipt.specifications).map(([name, value]) => (
                                                <span key={name} className="rounded-lg bg-violet-50 px-2 py-1 text-[11px] font-bold text-violet-800">{name}: {value}</span>
                                            ))}
                                        </div>
                                    )}
                                </article>
                            ))}
                        </div>
                    </section>
                </aside>
            </div>
                </>
            )}
            {cameraOpen && (
                <CameraBarcodeScanner
                    onDetected={(value) => {
                        setScannedBarcode(value);
                        setCameraOpen(false);
                        const expected = String(selectedLocation?.barcode_value || selectedLocation?.code || "").trim().toUpperCase();
                        if (value.trim().toUpperCase() === expected) {
                            toast.success("تم تأكيد باركود الخانة.");
                        } else {
                            toast.error("الباركود المصوّر لا يطابق الخانة المقترحة.");
                        }
                    }}
                    onClose={() => setCameraOpen(false)}
                />
            )}
        </div>
    );
}
