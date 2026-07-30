import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowCounterClockwise,
    Barcode,
    CheckCircle,
    Cube,
    Gear,
    MagnifyingGlass,
    Package,
    Plus,
    SpinnerGap,
    Trash,
    UserCircle,
    Warehouse,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    createStockPreparationOrder,
    loadStockPreparationCatalog,
    newInventoryReceiptIdempotencyKey,
    receiveStockPreparationOrder,
    transitionStockPreparationOrder,
} from "../../services/mezanInventoryReceiving";

const EMPTY_DATA = {
    suppliers: [],
    operators: [],
    orders: [],
    permissions: {},
    retention_mode: {},
};

const STATUS_LABELS = {
    reviewed: "تمت المراجعة والإسناد",
    in_progress: "قيد التجهيز",
    ready_for_receipt: "جاهز للاستلام",
    received: "تم الاستلام والاحتفاظ",
    cancelled: "ملغي",
};

const STATUS_CLASSES = {
    reviewed: "border-violet-200 bg-violet-50 text-violet-800",
    in_progress: "border-sky-200 bg-sky-50 text-sky-800",
    ready_for_receipt: "border-amber-200 bg-amber-50 text-amber-900",
    received: "border-emerald-200 bg-emerald-50 text-emerald-800",
    cancelled: "border-slate-200 bg-slate-100 text-slate-600",
};

const ERROR_MESSAGES = {
    stock_preparation_employee_not_eligible: "الموظف غير مخوّل لتجهيز المخزون. فعّل له مسؤولية تجهيز المخزون.",
    stock_preparation_employee_warehouse_mismatch: "الموظف غير مرتبط بالمخزن المختار.",
    stock_preparation_transition_invalid: "لا يمكن نقل الأمر إلى هذه المرحلة.",
    stock_preparation_revision_conflict: "عدّل موظف آخر هذا الأمر؛ تم تحديث القائمة.",
    stock_preparation_not_ready_for_receipt: "يجب أن يعلن الموظف اكتمال التجهيز أولًا.",
    stock_preparation_quantity_exceeded: "الكمية أكبر من المتبقي في أمر التجهيز.",
    stock_preparation_not_assigned_to_actor: "هذا الأمر مسند إلى موظف آخر.",
    inventory_location_barcode_mismatch: "الباركود لا يطابق خانة المخزن المختارة.",
    inventory_location_capacity_exceeded: "الخانة لا تتسع لهذه الكمية.",
    inventory_variant_required: "اختر خيار المنتج الذي سيُجهّز.",
    inventory_variant_not_found: "خيار المنتج غير موجود أو لم تُحمّل تفاصيله.",
    inventory_variants_not_loaded: "حمّل تفاصيل المنتج وخياراته أولًا قبل إنشاء أمر التجهيز.",
};

function errorMessage(error, fallback = "تعذر إتمام العملية.") {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    return ERROR_MESSAGES[detail?.code] || detail?.message || fallback;
}

const controlClass = "h-12 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-100 disabled:bg-slate-100 disabled:text-slate-400";

function Field({ label, hint, children }) {
    return (
        <label className="block">
            <span className="mb-1.5 block text-sm font-extrabold text-slate-800">{label}</span>
            {children}
            {hint && <span className="mt-1.5 block text-xs leading-5 text-slate-500">{hint}</span>}
        </label>
    );
}

function ReceiptForm({ order, item, inventoryCatalog, busy, onReceive }) {
    const key = `${order.id}:${item.id}`;
    const locations = (inventoryCatalog.locations || []).filter(
        (row) => row.warehouse_id === order.destination_warehouse_id,
    );
    const [locationId, setLocationId] = useState("");
    const [barcode, setBarcode] = useState("");
    const [quantity, setQuantity] = useState(item.remaining_quantity || 1);
    const location = locations.find((row) => row.id === locationId);

    useEffect(() => {
        setQuantity(item.remaining_quantity || 1);
    }, [item.remaining_quantity]);

    return (
        <form
            className="mt-3 grid gap-2 rounded-2xl border border-amber-200 bg-amber-50 p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_110px_auto]"
            onSubmit={(event) => {
                event.preventDefault();
                if (!location || !barcode.trim()) {
                    toast.error("اختر الخانة وامسح باركودها.");
                    return;
                }
                onReceive({
                    order,
                    item,
                    payload: {
                        idempotency_key: newInventoryReceiptIdempotencyKey("stock-preparation-receipt"),
                        item_id: item.id,
                        location_id: location.id,
                        scanned_barcode: barcode.trim(),
                        quantity: Number(quantity),
                    },
                });
            }}
        >
            <select value={locationId} onChange={(event) => { setLocationId(event.target.value); setBarcode(""); }} className={controlClass}>
                <option value="">خانة الاحتفاظ بالمخزون</option>
                {locations.map((row) => <option key={row.id} value={row.id}>{row.code} — حاليًا {row.current_quantity}</option>)}
            </select>
            <div className="relative">
                <Barcode size={19} className="absolute right-3 top-1/2 -translate-y-1/2 text-amber-700" />
                <input value={barcode} onChange={(event) => setBarcode(event.target.value)} placeholder={location?.barcode_value || "امسح باركود الخانة"} className={`${controlClass} pr-10 font-mono`} />
            </div>
            <input type="number" min="1" max={item.remaining_quantity} value={quantity} onChange={(event) => setQuantity(event.target.value)} className={controlClass} />
            <button type="submit" disabled={busy === `receive:${key}`} className="rounded-xl bg-emerald-700 px-4 font-black text-white disabled:opacity-60">
                {busy === `receive:${key}` ? "جارٍ…" : "احتفاظ"}
            </button>
        </form>
    );
}

export default function StockPreparationOrders({ inventoryCatalog, onInventoryChanged }) {
    const [data, setData] = useState(EMPTY_DATA);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [productSearch, setProductSearch] = useState("");
    const [productId, setProductId] = useState("");
    const [variantId, setVariantId] = useState("");
    const [supplierId, setSupplierId] = useState("");
    const [warehouseId, setWarehouseId] = useState("");
    const [employeeId, setEmployeeId] = useState("");
    const [quantity, setQuantity] = useState(1);
    const [note, setNote] = useState("");
    const [specifications, setSpecifications] = useState([
        { name: "الاسم", value: "" },
        { name: "اللون", value: "" },
    ]);

    const load = useCallback(async ({ quiet = false } = {}) => {
        if (!quiet) setLoading(true);
        try {
            setData({ ...EMPTY_DATA, ...(await loadStockPreparationCatalog()) });
        } catch (error) {
            toast.error(errorMessage(error, "تعذر تحميل أوامر تجهيز المخزون."));
        } finally {
            if (!quiet) setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const products = useMemo(() => {
        const query = productSearch.trim().toLocaleLowerCase("ar");
        if (!query) return inventoryCatalog.products || [];
        return (inventoryCatalog.products || []).filter((row) => (
            String(row.name || "").toLocaleLowerCase("ar").includes(query)
            || String(row.sku || "").toLocaleLowerCase("ar").includes(query)
            || String(row.barcode || "").toLocaleLowerCase("ar").includes(query)
        ));
    }, [inventoryCatalog.products, productSearch]);

    const eligibleOperators = useMemo(
        () => (data.operators || []).filter((row) => (
            row.eligible_for_stock_preparation
            && (
                row.is_owner
                || !warehouseId
                || (row.warehouse_ids || []).includes(warehouseId)
            )
        )),
        [data.operators, warehouseId],
    );
    const selectedProduct = useMemo(
        () => (inventoryCatalog.products || []).find(
            (row) => row.mezan_product_id === productId,
        ),
        [inventoryCatalog.products, productId],
    );
    const selectedVariant = useMemo(
        () => (selectedProduct?.variants || []).find(
            (row) => String(row.id) === variantId,
        ),
        [selectedProduct, variantId],
    );

    useEffect(() => {
        if (!eligibleOperators.some((row) => row.id === employeeId)) setEmployeeId("");
    }, [eligibleOperators, employeeId]);

    const setSpecification = (index, key, value) => {
        setSpecifications((current) => current.map(
            (row, rowIndex) => rowIndex === index ? { ...row, [key]: value } : row,
        ));
    };

    async function create(event) {
        event.preventDefault();
        if (!productId || !supplierId || !warehouseId || !employeeId) {
            toast.error("اختر المنتج والمورد والمخزن والموظف.");
            return;
        }
        if ((selectedProduct?.variants || []).length > 0 && !selectedVariant) {
            toast.error("اختر خيار المنتج الذي سيُجهّز ويُحفظ في المخزون.");
            return;
        }
        if (Number(selectedProduct?.variants_count || 0) > 0 && !(selectedProduct?.variants || []).length) {
            toast.error("حمّل تفاصيل المنتج وخياراته أولًا قبل إنشاء أمر التجهيز.");
            return;
        }
        const cleanSpecifications = specifications
            .map((row) => ({ name: row.name.trim(), value: row.value.trim() }))
            .filter((row) => row.name && row.value);
        setBusy("create");
        try {
            const result = await createStockPreparationOrder({
                idempotency_key: newInventoryReceiptIdempotencyKey("stock-preparation-order"),
                supplier_id: supplierId,
                assigned_employee_id: employeeId,
                destination_warehouse_id: warehouseId,
                items: [{
                    product_id: productId,
                    variant_id: selectedVariant?.id ? String(selectedVariant.id) : null,
                    quantity: Number(quantity),
                    specifications: cleanSpecifications,
                }],
                note: note.trim() || null,
            });
            toast.success(result?.duplicate ? "أمر التجهيز مسجل مسبقًا." : `تم إنشاء وإسناد الأمر ${result?.order?.reference || ""}`);
            setProductId("");
            setVariantId("");
            setProductSearch("");
            setQuantity(1);
            setNote("");
            setSpecifications([{ name: "الاسم", value: "" }, { name: "اللون", value: "" }]);
            await load({ quiet: true });
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setBusy("");
        }
    }

    async function action(order, actionName) {
        setBusy(`action:${order.id}`);
        try {
            await transitionStockPreparationOrder(order.id, {
                action: actionName,
                expected_revision: order.revision,
                note: null,
            });
            toast.success(actionName === "start_preparation" ? "بدأ الموظف تجهيز المخزون." : actionName === "mark_ready_for_receipt" ? "أصبح الأمر جاهزًا للاستلام." : "تم تحديث مرحلة الأمر.");
            await load({ quiet: true });
        } catch (error) {
            toast.error(errorMessage(error));
            await load({ quiet: true });
        } finally {
            setBusy("");
        }
    }

    async function receive({ order, item, payload }) {
        const key = `${order.id}:${item.id}`;
        setBusy(`receive:${key}`);
        try {
            const result = await receiveStockPreparationOrder(order.id, payload);
            toast.success(result?.order?.retention_complete ? "اكتمل الاستلام وأصبحت القطع مخزونًا جاهزًا." : "تم الاحتفاظ بالكمية المستلمة، وبقي جزء من الأمر.");
            await Promise.all([load({ quiet: true }), onInventoryChanged?.()]);
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setBusy("");
        }
    }

    if (loading) {
        return <div className="flex min-h-72 items-center justify-center rounded-3xl border bg-white"><SpinnerGap size={32} className="animate-spin text-violet-700" /></div>;
    }

    return (
        <div className="space-y-5" data-testid="stock-preparation-orders">
            <section className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5 shadow-sm">
                <div className="flex items-start gap-3">
                    <Cube size={28} className="mt-0.5 shrink-0 text-emerald-700" weight="duotone" />
                    <div>
                        <h2 className="text-xl font-black text-emerald-950">أمر تجهيز مخزون جاهز</h2>
                        <p className="mt-1 text-sm leading-6 text-emerald-900">ينشأ الأمر للمخزون، يُسند لمورد وموظف، ثم يمر بالتجهيز والاستلام. النهاية ثابتة: <b>الاحتفاظ بالمخزون</b>، ولا يتحول إلى شحنة عميل.</p>
                        <p className="mt-1 text-xs font-bold text-emerald-800">الربط بالمورد تشغيلي فقط؛ لا ينشئ التزامًا أو فاتورة مالية تلقائيًا.</p>
                    </div>
                </div>
            </section>

            {data.permissions?.can_create && (
                <form onSubmit={create} className="space-y-5 rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">إنشاء وإسناد أمر جديد</h2>
                        <p className="mt-1 text-xs leading-5 text-slate-500">مثال: سلسال الاسم، الاسم عبير، اللون ذهبي، الكمية 30.</p>
                    </div>
                    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                        <Field label="البحث عن المنتج">
                            <div className="relative">
                                <MagnifyingGlass size={19} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input value={productSearch} onChange={(event) => setProductSearch(event.target.value)} placeholder="الاسم أو SKU" className={`${controlClass} pr-10`} />
                            </div>
                        </Field>
                        <Field label="منتج ميزان">
                            <select value={productId} onChange={(event) => { setProductId(event.target.value); setVariantId(""); }} className={controlClass}>
                                <option value="">اختر المنتج</option>
                                {products.slice(0, 250).map((row) => <option key={row.mezan_product_id} value={row.mezan_product_id}>{row.name} {row.sku ? `— ${row.sku}` : ""}</option>)}
                            </select>
                        </Field>
                        {(selectedProduct?.variants || []).length > 0 && (
                            <Field label="خيار المنتج" hint="اللون أو المقاس الذي سيُضاف مخزونه.">
                                <select value={variantId} onChange={(event) => setVariantId(event.target.value)} className={controlClass}>
                                    <option value="">اختر الخيار</option>
                                    {(selectedProduct.variants || []).map((row) => (
                                        <option key={row.id} value={row.id}>
                                            {row.display_name || row.name || row.sku || `خيار ${row.id}`}
                                            {row.sku ? ` — ${row.sku}` : ""}
                                        </option>
                                    ))}
                                </select>
                            </Field>
                        )}
                        <Field label="الكمية">
                            <input type="number" min="1" max="100000" value={quantity} onChange={(event) => setQuantity(event.target.value)} className={controlClass} />
                        </Field>
                        <Field label="المورد">
                            <select value={supplierId} onChange={(event) => setSupplierId(event.target.value)} className={controlClass}>
                                <option value="">اختر المورد</option>
                                {(data.suppliers || []).map((row) => <option key={row.id} value={row.id}>{row.company_name}</option>)}
                            </select>
                        </Field>
                        <Field label="مخزن الاحتفاظ النهائي">
                            <select value={warehouseId} onChange={(event) => setWarehouseId(event.target.value)} className={controlClass}>
                                <option value="">اختر المخزن</option>
                                {(inventoryCatalog.warehouses || []).map((row) => <option key={row.id} value={row.id}>{row.name} — {row.code}</option>)}
                            </select>
                        </Field>
                        <Field label="الموظف المسؤول" hint="تظهر فقط الأسماء المخولة بتجهيز المخزون والمرتبطة بالمخزن.">
                            <select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)} disabled={!warehouseId} className={controlClass}>
                                <option value="">اختر الموظف</option>
                                {eligibleOperators.map((row) => <option key={row.id} value={row.id}>{row.name || row.email}{row.is_owner ? " — المالك" : ""}</option>)}
                            </select>
                        </Field>
                    </div>

                    <section className="rounded-2xl border bg-slate-50 p-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                            <div><div className="font-black">المواصفات المطلوب تجهيزها</div><div className="mt-1 text-xs text-slate-500">كل تركيبة مختلفة تُنشأ في أمر مستقل حتى يبقى المخزون دقيقًا.</div></div>
                            <button type="button" onClick={() => setSpecifications((current) => [...current, { name: "", value: "" }])} className="inline-flex items-center gap-1 rounded-xl border border-violet-200 bg-white px-3 py-2 text-xs font-black text-violet-700"><Plus /> إضافة</button>
                        </div>
                        <div className="space-y-2">
                            {specifications.map((row, index) => (
                                <div key={index} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] gap-2">
                                    <input value={row.name} onChange={(event) => setSpecification(index, "name", event.target.value)} placeholder="الاسم / اللون / المقاس" className={controlClass} />
                                    <input value={row.value} onChange={(event) => setSpecification(index, "value", event.target.value)} placeholder="عبير / ذهبي" className={controlClass} />
                                    <button type="button" onClick={() => setSpecifications((current) => current.length === 1 ? [{ name: "", value: "" }] : current.filter((_, rowIndex) => rowIndex !== index))} className="rounded-xl border border-rose-200 bg-white px-3 text-rose-600"><Trash /></button>
                                </div>
                            ))}
                        </div>
                    </section>
                    <Field label="تعليمات الموظف">
                        <textarea value={note} onChange={(event) => setNote(event.target.value)} rows={3} placeholder="تعليمات المورد أو التجهيز…" className="w-full rounded-xl border border-slate-200 bg-white p-3 text-sm outline-none focus:border-violet-500" />
                    </Field>
                    <button type="submit" disabled={busy === "create"} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-violet-700 px-5 py-4 font-black text-white disabled:opacity-60">
                        {busy === "create" ? <SpinnerGap className="animate-spin" /> : <Package weight="duotone" />} إنشاء الأمر ورفعه للموظف
                    </button>
                </form>
            )}

            <section className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                    <div><h2 className="text-xl font-black text-slate-950">أوامر تجهيز المخزون</h2><p className="mt-1 text-xs text-slate-500">المورد والموظف والمخزن محفوظون مع كل أمر.</p></div>
                    <button type="button" onClick={() => load()} className="inline-flex items-center gap-2 rounded-xl border bg-white px-3 py-2 text-sm font-black text-slate-700"><ArrowCounterClockwise /> تحديث</button>
                </div>
                {(data.orders || []).length === 0 && <div className="rounded-3xl border border-dashed bg-white p-10 text-center text-slate-500">لا توجد أوامر تجهيز مخزون بعد.</div>}
                {(data.orders || []).map((order) => (
                    <article key={order.id} className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                            <div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <h3 className="text-lg font-black text-slate-950">{order.reference}</h3>
                                    <span className={`rounded-full border px-3 py-1 text-xs font-black ${STATUS_CLASSES[order.status] || STATUS_CLASSES.cancelled}`}>{STATUS_LABELS[order.status] || order.status}</span>
                                    <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-800">الاحتفاظ بالمخزون</span>
                                </div>
                                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs font-bold text-slate-500">
                                    <span><Gear className="ml-1 inline" /> المورد: {order.supplier_name}</span>
                                    <span><UserCircle className="ml-1 inline" /> الموظف: {order.assigned_employee_name}</span>
                                    <span><Warehouse className="ml-1 inline" /> {order.destination_warehouse_name}</span>
                                </div>
                            </div>
                            <div className="flex flex-wrap gap-2">
                                {data.permissions?.can_work && order.status === "reviewed" && <button type="button" disabled={busy === `action:${order.id}`} onClick={() => action(order, "start_preparation")} className="rounded-xl bg-sky-700 px-4 py-2 text-sm font-black text-white">بدء التجهيز</button>}
                                {data.permissions?.can_work && order.status === "in_progress" && <button type="button" disabled={busy === `action:${order.id}`} onClick={() => action(order, "mark_ready_for_receipt")} className="rounded-xl bg-amber-600 px-4 py-2 text-sm font-black text-white">جاهز للاستلام</button>}
                                {data.permissions?.can_work && order.status === "ready_for_receipt" && !order.received_quantity && <button type="button" disabled={busy === `action:${order.id}`} onClick={() => action(order, "return_to_preparation")} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-black text-slate-700">إرجاع للتجهيز</button>}
                            </div>
                        </div>

                        <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
                            <div className="rounded-xl bg-slate-50 p-3"><div className="text-slate-500">المطلوب</div><div className="mt-1 text-xl font-black">{order.requested_quantity}</div></div>
                            <div className="rounded-xl bg-emerald-50 p-3"><div className="text-emerald-700">تم الاحتفاظ</div><div className="mt-1 text-xl font-black text-emerald-900">{order.received_quantity}</div></div>
                            <div className="rounded-xl bg-amber-50 p-3"><div className="text-amber-700">المتبقي</div><div className="mt-1 text-xl font-black text-amber-900">{order.remaining_quantity}</div></div>
                        </div>

                        <div className="mt-4 space-y-3">
                            {(order.items || []).map((item) => (
                                <section key={item.id} className="rounded-2xl border border-slate-200 p-4">
                                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                                        <div className="flex min-w-0 items-start gap-3">
                                            {item.main_image ? <img src={item.main_image} alt="" className="h-14 w-14 rounded-xl border object-cover" /> : <span className="flex h-14 w-14 items-center justify-center rounded-xl bg-slate-100"><Cube /></span>}
                                            <div><div className="font-black text-slate-950">{item.product_name}</div>{item.variant_name && <div className="mt-1 text-xs font-bold text-violet-700">{item.variant_name}</div>}<div className="mt-1 font-mono text-xs text-slate-500">{item.sku || "بدون SKU"}</div></div>
                                        </div>
                                        <div className="text-sm font-black text-slate-700">مطلوب {item.quantity} · مستلم {item.received_quantity} · متبقي {item.remaining_quantity}</div>
                                    </div>
                                    {Object.keys(item.specifications || {}).length > 0 && <div className="mt-3 flex flex-wrap gap-1">{Object.entries(item.specifications).map(([name, value]) => <span key={name} className="rounded-lg bg-violet-50 px-2 py-1 text-xs font-bold text-violet-800">{name}: {value}</span>)}</div>}
                                    {order.status === "ready_for_receipt" && item.remaining_quantity > 0 && data.permissions?.can_receive && <ReceiptForm order={order} item={item} inventoryCatalog={inventoryCatalog} busy={busy} onReceive={receive} />}
                                </section>
                            ))}
                        </div>
                        {order.note && <div className="mt-3 rounded-xl bg-slate-50 p-3 text-sm text-slate-600"><WarningCircle className="ml-1 inline text-amber-600" /> {order.note}</div>}
                        {order.status === "received" && <div className="mt-3 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-black text-emerald-900"><CheckCircle /> جميع القطع أصبحت مخزونًا جاهزًا مطابقًا للمواصفات، ولم تُرسل للشحن.</div>}
                    </article>
                ))}
            </section>
        </div>
    );
}
