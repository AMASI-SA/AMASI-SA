import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle,
    Cube,
    FolderSimple,
    LinkSimple,
    MagnifyingGlass,
    Package,
    Plus,
    SpinnerGap,
    Trash,
    WarningCircle,
    Wrench,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getProductOperations,
    linkProductGroups,
    linkProductResource,
    saveProductOperationProfile,
    unlinkProductGroup,
    unlinkProductResource,
} from "../../services/mezanProductsV2";
import { PRODUCT_OPERATION_CHOICES_ENABLED } from "../../lib/productOperationFreeze";
import {
    filterProductGroups,
    groupNamesForResource,
    resourceMatchesCategory,
    toggleSelectedGroup,
} from "../../lib/productGroupPicker";

function errorCode(error, fallback) {
    const detail = error?.response?.data?.detail;
    const code = detail?.code;
    const labels = {
        resource_already_linked_to_option: "هذه الخدمة أو المكوّن مرتبط بأحد خيارات المنتج. أزل ربط الخيار أولًا.",
        group_resource_already_linked_to_option: "تحتوي المجموعة على خدمة أو مكوّن مرتبط بخيار في المنتج. أزل ربط الخيار أولًا.",
        resource_link_managed_by_group: "هذا العنصر مرتبط من خلال مجموعة. أزل المجموعة بدل حذف العنصر مباشرة.",
        component_group_not_found: "إحدى المجموعات المحددة لم تعد موجودة.",
        product_group_link_not_found: "ربط المجموعة غير موجود.",
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
                {!rows.length && <p className="rounded-xl bg-slate-50 p-3 text-xs text-slate-400">لا توجد نتائج ضمن التصنيف المحدد.</p>}
                {rows.map((resource) => {
                    const conflict = (resource.option_links || [])[0];
                    return (
                        <div key={resource.id} className={`rounded-xl border p-3 ${conflict ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`}>
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="truncate font-bold">{resource.name}</div>
                                    <div className="mt-1 text-[11px] text-slate-500">{resource.code}{resource.requires_preparation && <span className="mr-2 rounded-full bg-violet-100 px-2 py-0.5 font-bold text-violet-800">يمنع الشحن حتى يكتمل</span>}</div>
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
    const [data, setData] = useState({ profile: {}, product_links: [], resources: [], categories: [], groups: [], product_group_links: [] });
    const [fulfillmentType, setFulfillmentType] = useState("");
    const [inventoryPolicy, setInventoryPolicy] = useState("");
    const [stockoutPolicy, setStockoutPolicy] = useState("close_when_out_of_stock");
    const [lowStockThreshold, setLowStockThreshold] = useState(3);
    const [serviceQuery, setServiceQuery] = useState("");
    const [componentQuery, setComponentQuery] = useState("");
    const [categoryFilter, setCategoryFilter] = useState("");
    const [groupPickerOpen, setGroupPickerOpen] = useState(false);
    const [groupKind, setGroupKind] = useState("service");
    const [selectedGroupIds, setSelectedGroupIds] = useState([]);
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

    const categories = useMemo(() => data.categories || [], [data.categories]);
    const groups = useMemo(() => data.groups || [], [data.groups]);
    const linked = useMemo(
        () => (data.resources || []).filter((row) => row.linked_to_product),
        [data.resources],
    );
    const available = useMemo(
        () => (data.resources || []).filter((row) => !row.linked_to_product),
        [data.resources],
    );
    const linkedGroups = useMemo(
        () => groups.filter((group) => group.linked_to_product),
        [groups],
    );
    const pickerGroups = useMemo(
        () => filterProductGroups(groups, { categoryId: categoryFilter, kind: groupKind }),
        [groups, categoryFilter, groupKind],
    );

    const filterRows = (kind, query) => {
        const needle = query.trim().toLowerCase();
        return available.filter((row) => {
            const matchesKind = kind === "service"
                ? row.kind === "service" || !row.track_inventory
                : row.kind === "stock_component" || row.track_inventory;
            const matchesCategory = resourceMatchesCategory(row, categoryFilter);
            return matchesKind && matchesCategory && (!needle || `${row.name || ""} ${row.code || ""}`.toLowerCase().includes(needle));
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
            && (!Number.isInteger(Number(lowStockThreshold)) || Number(lowStockThreshold) < 0)
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
            toast.success(`تم إلغاء الربط اليدوي لـ ${resource.name}`);
            window.dispatchEvent(new CustomEvent("mezan:product-resource-links-changed", { detail: { productId } }));
        } catch (error) {
            toast.error(errorCode(error, "تعذر إلغاء الربط"));
        } finally {
            setBusy(false);
        }
    }

    function openGroupPicker() {
        if (!categoryFilter) {
            toast.error("اختر تصنيف التجهيز أولًا.");
            return;
        }
        setSelectedGroupIds([]);
        setGroupKind("service");
        setGroupPickerOpen(true);
    }

    async function addSelectedGroups() {
        if (!selectedGroupIds.length) return;
        setBusy(true);
        try {
            const result = await linkProductGroups(productId, selectedGroupIds);
            setData(result);
            setSelectedGroupIds([]);
            setGroupPickerOpen(false);
            toast.success("تمت إضافة المجموعات وربط عناصرها بالمنتج دون تكرار");
            window.dispatchEvent(new CustomEvent("mezan:product-resource-links-changed", { detail: { productId } }));
        } catch (error) {
            toast.error(errorCode(error, "تعذر ربط المجموعات"));
        } finally {
            setBusy(false);
        }
    }

    async function removeGroup(group) {
        setBusy(true);
        try {
            const result = await unlinkProductGroup(productId, group.id);
            setData(result);
            toast.success(`تمت إزالة مجموعة ${group.name}`);
            window.dispatchEvent(new CustomEvent("mezan:product-resource-links-changed", { detail: { productId } }));
        } catch (error) {
            toast.error(errorCode(error, "تعذر إزالة المجموعة"));
        } finally {
            setBusy(false);
        }
    }

    if (loading) return <section className="flex min-h-40 items-center justify-center rounded-2xl border"><SpinnerGap className="animate-spin text-violet-700" /></section>;

    return (
        <section className="space-y-4 rounded-2xl border border-violet-200 bg-violet-50/30 p-3 sm:p-4" data-testid="product-operations-editor">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <h2 className="font-black">خدمات ومكوّنات المنتج</h2>
                    <p className="mt-1 text-xs leading-6 text-slate-500">اختر تصنيف التجهيز لعرض خدماته ومكوّناته ومجموعاته فقط.</p>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                    <select value={categoryFilter} onChange={(event) => { setCategoryFilter(event.target.value); setSelectedGroupIds([]); }} className="min-h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm font-black" data-testid="product-resource-category-filter">
                        <option value="">اختر التصنيف</option>
                        {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
                    </select>
                    <button type="button" onClick={openGroupPicker} disabled={!categoryFilter} className="min-h-11 rounded-xl bg-violet-700 px-4 text-sm font-black text-white disabled:opacity-40" data-testid="open-product-group-picker"><Plus className="ml-1 inline" />إضافة مجموعة</button>
                </div>
            </div>

            <div className="rounded-xl border border-violet-200 bg-violet-100/70 p-3 text-xs font-bold leading-6 text-violet-950">
                خيارات التشغيل والمخزون مجمّدة مؤقتًا. جميع المنتجات تدخل مسار التجهيز، ويستمر ربط الخدمات والمكوّنات والمجموعات أدناه كالمعتاد.
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
                        </div>
                        <div>
                            <div className="mb-2 text-xs font-bold text-slate-600">سياسة مخزون المنتج النهائي</div>
                            <div className="grid grid-cols-2 gap-2">
                                <button type="button" onClick={() => setInventoryPolicy("branch_stock_required")} className={`rounded-xl border p-3 text-sm font-black ${inventoryPolicy === "branch_stock_required" ? "border-sky-500 bg-sky-50 text-sky-950" : "bg-white"}`}><Package className="ml-1 inline" /> يتتبع المخزون</button>
                                <button type="button" onClick={() => setInventoryPolicy("finished_goods_inventory_not_tracked")} className={`rounded-xl border p-3 text-sm font-black ${inventoryPolicy === "finished_goods_inventory_not_tracked" ? "border-amber-500 bg-amber-50 text-amber-950" : "bg-white"}`}><Wrench className="ml-1 inline" /> لا يتتبع النهائي</button>
                            </div>
                        </div>
                    </div>
                    <button type="button" disabled={busy || !fulfillmentType || !inventoryPolicy} onClick={saveProfile} className="rounded-xl bg-violet-700 px-5 py-3 text-sm font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : "حفظ التشغيل"}</button>
                </div>

                {inventoryPolicy === "branch_stock_required" && (
                    <section className="rounded-2xl border border-amber-200 bg-white p-4">
                        <div className="flex items-center gap-2 font-black text-slate-950"><WarningCircle size={21} className="text-amber-600" />التنبيه والتصرف عند نفاد المخزون</div>
                        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
                            <div className="grid gap-2 sm:grid-cols-2">
                                <button type="button" onClick={() => setStockoutPolicy("close_when_out_of_stock")} className={`rounded-xl border p-3 text-right text-sm font-black ${stockoutPolicy === "close_when_out_of_stock" ? "border-rose-400 bg-rose-50" : "bg-white"}`}>إيقاف البيع</button>
                                <button type="button" onClick={() => setStockoutPolicy("allow_preorder")} className={`rounded-xl border p-3 text-right text-sm font-black ${stockoutPolicy === "allow_preorder" ? "border-violet-400 bg-violet-50" : "bg-white"}`}>حجز مسبق</button>
                            </div>
                            <label className="block"><span className="mb-2 block text-xs font-bold text-slate-600">حذّرني عندما يصل الرصيد إلى</span><input type="number" min="0" max="100000" step="1" value={lowStockThreshold} onChange={(event) => setLowStockThreshold(event.target.value)} className="w-full rounded-xl border border-slate-200 px-3 py-3 text-sm font-black" /></label>
                        </div>
                    </section>
                )}
            </>}

            <section className="rounded-2xl border border-violet-200 bg-white p-3" data-testid="linked-product-groups">
                <div className="flex items-center justify-between gap-3"><h3 className="font-black"><FolderSimple className="ml-1 inline text-violet-700" />المجموعات المضافة ({linkedGroups.length})</h3>{!categoryFilter && <span className="text-[11px] font-bold text-slate-400">اختر التصنيف لإضافة مجموعة</span>}</div>
                {!linkedGroups.length ? <p className="mt-2 text-xs text-slate-400">لا توجد مجموعات مرتبطة بالمنتج.</p> : <div className="mt-3 grid gap-2 lg:grid-cols-2">{linkedGroups.map((group) => (
                    <article key={group.id} className="rounded-xl border border-violet-100 bg-violet-50/50 p-3">
                        <div className="flex items-start justify-between gap-3"><div><div className="font-black text-violet-950">{group.name}</div><div className="mt-1 text-[11px] font-bold text-slate-500">{group.group_kind === "service" ? "مجموعة خدمات" : "مجموعة مكوّنات"} · {group.resources?.length || 0} عناصر</div></div><button type="button" disabled={busy} onClick={() => removeGroup(group)} className="rounded-lg border border-rose-200 p-2 text-rose-700"><Trash /></button></div>
                        <div className="mt-2 flex flex-wrap gap-1">{(group.resources || []).map((resource) => <span key={resource.id} className="rounded-full bg-white px-2 py-1 text-[10px] font-bold text-slate-700">{resource.name}</span>)}</div>
                    </article>
                ))}</div>}
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-3">
                <h3 className="font-black">المرتبط بالمنتج مباشرة ({linked.length})</h3>
                {!linked.length ? <p className="mt-2 text-xs text-slate-400">لا توجد خدمات أو مكوّنات مرتبطة بالمنتج مباشرة.</p> : <div className="mt-3 grid gap-2 sm:grid-cols-2">{linked.map((resource) => {
                    const names = groupNamesForResource(groups, resource.group_ids);
                    return <div key={resource.id} className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 p-3"><div className="min-w-0"><div className="truncate font-bold">{resource.name}</div><div className="text-[11px] text-slate-500">{resource.track_inventory ? "مكوّن" : "خدمة"} · الكمية {resource.product_quantity || 1}</div>{names.length > 0 && <div className="mt-1 text-[10px] font-bold text-violet-700">من: {names.join("، ")}</div>}</div>{resource.manual_link ? <button type="button" disabled={busy} onClick={() => unlink(resource)} className="rounded-lg border border-rose-200 p-2 text-rose-700" title="إلغاء الربط اليدوي"><Trash /></button> : <span className="rounded-full bg-violet-100 px-2 py-1 text-[10px] font-black text-violet-800">من مجموعة</span>}</div>;
                })}</div>}
            </section>

            {!categoryFilter ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm font-bold text-slate-500">اختر تصنيف التجهيز لعرض خدماته ومكوّناته.</div>
            ) : (
                <div className="grid gap-3 xl:grid-cols-2">
                    <ResourcePicker title="الخدمات" Icon={Wrench} rows={services} query={serviceQuery} setQuery={setServiceQuery} onLink={link} busy={busy} />
                    <ResourcePicker title="المكوّنات" Icon={Cube} rows={components} query={componentQuery} setQuery={setComponentQuery} onLink={link} busy={busy} />
                </div>
            )}

            {groupPickerOpen && (
                <div className="fixed inset-0 z-[170] flex items-end justify-center bg-slate-950/60 sm:items-center sm:p-4" dir="rtl" data-testid="product-group-picker-modal">
                    <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-t-3xl bg-white p-5 shadow-2xl sm:rounded-3xl">
                        <div className="flex items-start justify-between gap-3"><div><h3 className="text-xl font-black">إضافة مجموعة</h3><p className="mt-1 text-xs font-bold text-slate-500">تظهر فقط مجموعات التصنيف المحدد، وتُربط عناصر المجموعات دون تكرار.</p></div><button type="button" onClick={() => setGroupPickerOpen(false)} className="rounded-xl border p-2"><X /></button></div>
                        <div className="mt-4 grid grid-cols-2 gap-2">
                            <button type="button" onClick={() => { setGroupKind("service"); setSelectedGroupIds([]); }} className={`rounded-xl border p-3 text-sm font-black ${groupKind === "service" ? "border-violet-500 bg-violet-50 text-violet-950" : "border-slate-200"}`}><Wrench className="ml-1 inline" />مجموعات الخدمات</button>
                            <button type="button" onClick={() => { setGroupKind("component"); setSelectedGroupIds([]); }} className={`rounded-xl border p-3 text-sm font-black ${groupKind === "component" ? "border-violet-500 bg-violet-50 text-violet-950" : "border-slate-200"}`}><Cube className="ml-1 inline" />مجموعات المكوّنات</button>
                        </div>
                        <div className="mt-4 space-y-2">
                            {!pickerGroups.length ? <div className="rounded-xl border border-dashed p-6 text-center text-sm font-bold text-slate-500">لا توجد مجموعات من هذا النوع داخل التصنيف المحدد.</div> : pickerGroups.map((group) => {
                                const linkedAlready = group.linked_to_product;
                                const checked = selectedGroupIds.includes(String(group.id));
                                return <label key={group.id} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 ${linkedAlready ? "cursor-not-allowed bg-slate-100 opacity-70" : checked ? "border-violet-500 bg-violet-50" : "border-slate-200"}`}><input type="checkbox" disabled={linkedAlready} checked={linkedAlready || checked} onChange={() => setSelectedGroupIds(toggleSelectedGroup(selectedGroupIds, group.id))} className="mt-1 h-4 w-4 accent-violet-700" /><span className="min-w-0 flex-1"><span className="block font-black">{group.name}</span><span className="mt-1 block text-[11px] font-bold text-slate-500">{(group.resources || []).map((resource) => resource.name).join(" · ")}</span></span>{linkedAlready && <span className="rounded-full bg-emerald-100 px-2 py-1 text-[10px] font-black text-emerald-800">مضافة</span>}</label>;
                            })}
                        </div>
                        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setGroupPickerOpen(false)} className="rounded-xl border px-4 py-3 font-black">إلغاء</button><button type="button" onClick={addSelectedGroups} disabled={busy || !selectedGroupIds.length} className="rounded-xl bg-violet-700 px-5 py-3 font-black text-white disabled:opacity-50">{busy ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" weight="fill" />}إضافة المحدد</button></div>
                    </div>
                </div>
            )}
        </section>
    );
}
