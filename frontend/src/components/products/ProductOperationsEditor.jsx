import { useEffect, useMemo, useState } from "react";
import {
    Cube, LinkSimple, MagnifyingGlass, Package, SpinnerGap, Trash,
    WarningCircle, Wrench,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getProductOperations,
    linkProductResource,
    saveProductOperationProfile,
    unlinkProductResource,
} from "../../services/mezanProductsV2";
import { PRODUCT_OPERATION_CHOICES_ENABLED } from "../../lib/productOperationFreeze";

function errorCode(error, fallback) {
    const detail = error?.response?.data?.detail;
    const code = detail?.code;
    const labels = {
        resource_already_linked_to_option: "هذه الخدمة أو المكوّن مرتبط بأحد خيارات المنتج. أزل ربط الخيار أولًا.",
        invalid_fulfillment_type: "اختر نوع تنفيذ المنتج.",
        invalid_inventory_policy: "اختر هل المنتج يتتبع مخزون الفروع.",
        invalid_stockout_policy: "اختر ماذا يحدث عند نفاد المخزون.",
        invalid_low_stock_threshold: "حد قرب النفاد يجب أن يكون صفرًا أو عددًا صحيحًا موجبًا.",
    };
    return labels[code] || detail?.message || code || fallback;
}

function ResourcePicker({ title, Icon, rows, query, setQuery, onLink, busy }) {
    return (
        <section className="rounded-2xl border border-slate-200 p-3">
            <h3 className="font-black"><Icon className="ml-1 inline" />{title}</h3>
            <label className="relative mt-3 block">
                <MagnifyingGlass className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`ابحث في ${title}…`} className="w-full rounded-xl border py-2.5 pr-10 pl-3 text-sm" />
            </label>
            <div className="mt-3 max-h-64 space-y-2 overflow-auto">
                {!rows.length && <p className="rounded-xl bg-slate-50 p-3 text-xs text-slate-400">لا توجد نتائج.</p>}
                {rows.map((resource) => {
                    const conflict = (resource.option_links || [])[0];
                    return (
                        <div key={resource.id} className={`rounded-xl border p-3 ${conflict ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`}>
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="truncate font-bold">{resource.name}</div>
                                    <div className="mt-1 text-[11px] text-slate-500">{resource.code}{resource.requires_preparation && <span className="mr-2 rounded-full bg-violet-100 px-2 py-0.5 font-bold text-violet-800">يتطلب تجهيز</span>}</div>
                                </div>
                                <button type="button" disabled={busy || !!conflict} onClick={() => onLink(resource)} className="shrink-0 rounded-lg border border-violet-300 px-3 py-2 text-xs font-black text-violet-800 disabled:border-slate-200 disabled:text-slate-400"><LinkSimple className="ml-1 inline" /> ربط</button>
                            </div>
                            {conflict && <p className="mt-2 text-[11px] font-bold leading-5 text-amber-800">مرتبط بالخيار «{conflict.option_name}: {conflict.value_name}»؛ لا يمكن ربطه بالمنتج نفسه.</p>}
                        </div>
                    );
                })}
            </div>
        </section>
    );
}

export default function ProductOperationsEditor({ productId }) {
    const [data, setData] = useState({ profile: {}, product_links: [], resources: [] });
    const [fulfillmentType, setFulfillmentType] = useState("");
    const [inventoryPolicy, setInventoryPolicy] = useState("");
    const [stockoutPolicy, setStockoutPolicy] = useState("close_when_out_of_stock");
    const [lowStockThreshold, setLowStockThreshold] = useState(3);
    const [serviceQuery, setServiceQuery] = useState("");
    const [componentQuery, setComponentQuery] = useState("");
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);

    async function load() {
        if (!productId) return;
        setLoading(true);
        try {
            const result = await getProductOperations(productId);
            setData(result);
            setFulfillmentType(result?.profile?.fulfillment_type || "");
            setInventoryPolicy(result?.profile?.inventory_policy || "");
            setStockoutPolicy(result?.profile?.stockout_policy || "close_when_out_of_stock");
            setLowStockThreshold(result?.profile?.low_stock_threshold ?? 3);
        } catch (error) {
            toast.error(errorCode(error, "تعذر تحميل إعدادات تشغيل المنتج"));
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { load(); }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

    const linked = useMemo(
        () => (data.resources || []).filter((row) => row.linked_to_product),
        [data.resources],
    );
    const available = useMemo(
        () => (data.resources || []).filter((row) => !row.linked_to_product),
        [data.resources],
    );
    const filterRows = (kind, query) => {
        const needle = query.trim().toLowerCase();
        return available.filter((row) => {
            const matchesKind = kind === "service" ? row.kind === "service" || !row.track_inventory : row.kind === "stock_component" || row.track_inventory;
            return matchesKind && (!needle || `${row.name || ""} ${row.code || ""}`.toLowerCase().includes(needle));
        });
    };
    const services = filterRows("service", serviceQuery);
    const components = filterRows("component", componentQuery);

    async function saveProfile() {
        if (!fulfillmentType || !inventoryPolicy) {
            toast.error("اختر مسار التنفيذ وسياسة المخزون.");
            return;
        }
        if (
            inventoryPolicy === "branch_stock_required"
            && (
                !Number.isInteger(Number(lowStockThreshold))
                || Number(lowStockThreshold) < 0
            )
        ) {
            toast.error("حد قرب النفاد يجب أن يكون صفرًا أو عددًا صحيحًا موجبًا.");
            return;
        }
        setBusy(true);
        try {
            const result = await saveProductOperationProfile(productId, {
                fulfillment_type: fulfillmentType,
                inventory_policy: inventoryPolicy,
                stockout_policy: stockoutPolicy,
                low_stock_threshold: Number(lowStockThreshold),
            });
            setData(result);
            toast.success("تم حفظ نوع تنفيذ المنتج");
        } catch (error) {
            toast.error(errorCode(error, "تعذر حفظ إعداد التشغيل"));
        } finally {
            setBusy(false);
        }
    }

    async function link(resource) {
        setBusy(true);
        try {
            const result = await linkProductResource(productId, resource.id, 1);
            setData(result);
            toast.success(`تم ربط ${resource.name} بالمنتج`);
            window.dispatchEvent(new CustomEvent("mezan:product-resource-links-changed", { detail: { productId } }));
        } catch (error) {
            toast.error(errorCode(error, "تعذر ربط الخدمة أو المكوّن"));
        } finally {
            setBusy(false);
        }
    }

    async function unlink(resource) {
        setBusy(true);
        try {
            const result = await unlinkProductResource(productId, resource.id);
            setData(result);
            toast.success(`تم إلغاء ربط ${resource.name}`);
            window.dispatchEvent(new CustomEvent("mezan:product-resource-links-changed", { detail: { productId } }));
        } catch (error) {
            toast.error(errorCode(error, "تعذر إلغاء الربط"));
        } finally {
            setBusy(false);
        }
    }

    if (loading) return <section className="flex min-h-40 items-center justify-center rounded-2xl border"><SpinnerGap className="animate-spin text-violet-700" /></section>;

    return (
        <section className="space-y-4 rounded-2xl border border-violet-200 bg-violet-50/30 p-3 sm:p-4" data-testid="product-operations-editor">
            <div>
                <h2 className="font-black">خدمات ومكوّنات المنتج</h2>
                <p className="mt-1 text-xs leading-6 text-slate-500">الربط هنا يطبق على المنتج دائمًا. أي خدمة أو مكوّن مرتبط بخيار لا يمكن تكراره هنا.</p>
            </div>

            <div className="rounded-xl border border-violet-200 bg-violet-100/70 p-3 text-xs font-bold leading-6 text-violet-950">
                خيارات التشغيل والمخزون مجمّدة مؤقتًا. جميع المنتجات تدخل مسار التجهيز، ويستمر ربط الخدمات والمكوّنات أدناه كالمعتاد.
            </div>

            {PRODUCT_OPERATION_CHOICES_ENABLED && <>
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
                <div className="grid gap-4 xl:grid-cols-2">
                    <div>
                        <div className="mb-2 text-xs font-bold text-slate-600">المسار الافتراضي للتنفيذ</div>
                        <div className="grid grid-cols-2 gap-2">
                            <button type="button" onClick={() => setFulfillmentType("instant")} className={`rounded-xl border p-3 text-sm font-black ${fulfillmentType === "instant" ? "border-emerald-500 bg-emerald-50 text-emerald-900" : "bg-white"}`}><Package className="ml-1 inline" /> مباشر للشحن</button>
                            <button type="button" onClick={() => setFulfillmentType("requires_preparation")} className={`rounded-xl border p-3 text-sm font-black ${fulfillmentType === "requires_preparation" ? "border-violet-500 bg-violet-100 text-violet-950" : "bg-white"}`}><Wrench className="ml-1 inline" /> يحتاج تجهيز</button>
                        </div>
                        <p className="mt-2 text-[11px] leading-5 text-slate-500">أي خدمة تجهيز مرتبطة بالمنتج تطبق على جميع طلباته، والخدمة المرتبطة بخيار تطبق عند اختيار ذلك الخيار فقط.</p>
                    </div>
                    <div>
                        <div className="mb-2 text-xs font-bold text-slate-600">سياسة مخزون المنتج النهائي</div>
                        <div className="grid grid-cols-2 gap-2">
                            <button type="button" onClick={() => setInventoryPolicy("branch_stock_required")} className={`rounded-xl border p-3 text-sm font-black ${inventoryPolicy === "branch_stock_required" ? "border-sky-500 bg-sky-50 text-sky-950" : "bg-white"}`}><Package className="ml-1 inline" /> يتتبع المخزون</button>
                            <button type="button" onClick={() => setInventoryPolicy("finished_goods_inventory_not_tracked")} className={`rounded-xl border p-3 text-sm font-black ${inventoryPolicy === "finished_goods_inventory_not_tracked" ? "border-amber-500 bg-amber-50 text-amber-950" : "bg-white"}`}><Wrench className="ml-1 inline" /> لا يتتبع النهائي</button>
                        </div>
                        <p className="mt-2 text-[11px] leading-5 text-slate-500">تتبع المخزون مستقل عن التجهيز. يمكن للسلسال أن يُحجز من مخزون الفرع ثم يدخل خدمة كتابة الاسم.</p>
                    </div>
                    <p className="text-[11px] leading-5 text-slate-500 xl:col-span-2">المنتج يتبع المتجر كله ولا يرتبط بفرع. الكميات وحدها تُسجل داخل الفروع، وارتباط الموظف يحدد العرض والصلاحيات فقط.</p>
                </div>
                <button type="button" disabled={busy || !fulfillmentType || !inventoryPolicy} onClick={saveProfile} className="rounded-xl bg-violet-700 px-5 py-3 text-sm font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : "حفظ التشغيل"}</button>
                </div>

                {inventoryPolicy === "branch_stock_required" && (
                <section className="rounded-2xl border border-amber-200 bg-white p-4">
                    <div className="flex items-center gap-2 font-black text-slate-950">
                        <WarningCircle size={21} className="text-amber-600" />
                        التنبيه والتصرف عند نفاد المخزون
                    </div>
                    <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
                        <div>
                            <div className="mb-2 text-xs font-bold text-slate-600">عند وصول الرصيد المتاح إلى صفر</div>
                            <div className="grid gap-2 sm:grid-cols-2">
                                <button type="button" onClick={() => setStockoutPolicy("close_when_out_of_stock")} className={`rounded-xl border p-3 text-right text-sm font-black ${stockoutPolicy === "close_when_out_of_stock" ? "border-rose-400 bg-rose-50 text-rose-950" : "bg-white"}`}>
                                    إيقاف البيع
                                    <span className="mt-1 block text-[11px] font-normal leading-5 text-slate-500">يظهر تنبيه لإغلاق المنتج ولا يُسمح للطلب بتجاوز نقص المخزون.</span>
                                </button>
                                <button type="button" onClick={() => setStockoutPolicy("allow_preorder")} className={`rounded-xl border p-3 text-right text-sm font-black ${stockoutPolicy === "allow_preorder" ? "border-violet-400 bg-violet-50 text-violet-950" : "bg-white"}`}>
                                    حجز مسبق
                                    <span className="mt-1 block text-[11px] font-normal leading-5 text-slate-500">يُقبل الطلب داخل ميزان كحجز مسبق وينتظر وصول المخزون، ولا ينتقل للشحن.</span>
                                </button>
                            </div>
                        </div>
                        <label className="block">
                            <span className="mb-2 block text-xs font-bold text-slate-600">حذّرني عندما يصل الرصيد إلى</span>
                            <input type="number" min="0" max="100000" step="1" value={lowStockThreshold} onChange={(event) => setLowStockThreshold(event.target.value)} className="w-full rounded-xl border border-slate-200 px-3 py-3 text-sm font-black outline-none focus:border-amber-400" />
                            <span className="mt-2 block text-[11px] leading-5 text-slate-500">يُحسب من الكمية المتاحة بعد خصم حجوزات الطلبات.</span>
                        </label>
                    </div>
                </section>
                )}

                {inventoryPolicy === "branch_stock_required" && linked.some((row) => row.requires_preparation) && (
                <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs font-bold leading-6 text-sky-950">
                    سيحجز ميزان كمية المنتج من الفرع أولًا، ثم يرسل الطلب إلى خدمات التجهيز المرتبطة به.
                </div>
                )}
                {fulfillmentType === "instant" && linked.some((row) => row.requires_preparation) && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold leading-6 text-amber-900">الخدمة التي تتطلب تجهيزًا تتغلب على المسار المباشر لهذا الطلب، ولا تلغي تتبع مخزون المنتج.</div>}
            </>}

            <section className="rounded-2xl border border-slate-200 bg-white p-3">
                <h3 className="font-black">المرتبط بالمنتج مباشرة ({linked.length})</h3>
                {!linked.length ? <p className="mt-2 text-xs text-slate-400">لا توجد خدمات أو مكوّنات مرتبطة بالمنتج مباشرة.</p> : <div className="mt-3 grid gap-2 sm:grid-cols-2">{linked.map((resource) => <div key={resource.id} className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 p-3"><div className="min-w-0"><div className="truncate font-bold">{resource.name}</div><div className="text-[11px] text-slate-500">{resource.track_inventory ? "مكوّن" : "خدمة"} · الكمية {resource.product_quantity || 1}</div></div><button type="button" disabled={busy} onClick={() => unlink(resource)} className="rounded-lg border border-rose-200 p-2 text-rose-700"><Trash /></button></div>)}</div>}
            </section>

            <div className="grid gap-3 xl:grid-cols-2">
                <ResourcePicker title="الخدمات" Icon={Wrench} rows={services} query={serviceQuery} setQuery={setServiceQuery} onLink={link} busy={busy} />
                <ResourcePicker title="المكوّنات" Icon={Cube} rows={components} query={componentQuery} setQuery={setComponentQuery} onLink={link} busy={busy} />
            </div>
        </section>
    );
}
