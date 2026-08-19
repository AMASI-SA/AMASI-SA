import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle,
    Cube,
    MagnifyingGlass,
    Package,
    SpinnerGap,
    Trash,
    Wrench,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getProductOperations,
    linkProductGroups,
    linkProductResource,
    unlinkProductGroup,
    unlinkProductResource,
} from "../../services/mezanProductsV2";
import {
    saveProductCostCategory,
    saveProductCostCompletion,
} from "../../services/productCostSetup";

function errorCode(error, fallback) {
    const detail = error?.response?.data?.detail;
    const labels = {
        resource_already_linked_to_option: "هذه الخدمة أو المكوّن مرتبط بخيار في المنتج. أزل ربط الخيار أولًا.",
        group_resource_already_linked_to_option: "تحتوي المجموعة على عنصر مرتبط بخيار في المنتج.",
        resource_link_managed_by_group: "هذا العنصر مرتبط من خلال مجموعة. أزل المجموعة بدل العنصر.",
        component_group_not_found: "إحدى المجموعات لم تعد موجودة.",
        product_group_link_not_found: "ربط المجموعة غير موجود.",
        product_cost_category_not_found: "تصنيف المنتج غير موجود أو غير نشط.",
    };
    return labels[detail?.code] || detail?.message || detail?.code || fallback;
}

function isService(resource) {
    return resource?.kind === "service" || !resource?.track_inventory;
}

export default function ProductOperationsEditor({ productId }) {
    const [data, setData] = useState({
        product: {},
        cost_setup: {},
        product_links: [],
        resources: [],
        categories: [],
        groups: [],
        product_group_links: [],
    });
    const [query, setQuery] = useState("");
    const [busy, setBusy] = useState(false);
    const [loading, setLoading] = useState(true);

    async function load() {
        if (!productId) return;
        setLoading(true);
        try {
            setData(await getProductOperations(productId));
        } catch (error) {
            toast.error(errorCode(error, "تعذر تحميل إعدادات تكلفة المنتج"));
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { load(); }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

    const categories = useMemo(() => data.categories || [], [data.categories]);
    const categoryId = data?.cost_setup?.cost_category_id || data?.product?.cost_category_id || "";
    const categoryName = data?.cost_setup?.cost_category_name || data?.product?.cost_category_name || "";
    const linkedResources = useMemo(
        () => (data.resources || []).filter((row) => row.linked_to_product),
        [data.resources],
    );
    const linkedGroups = useMemo(
        () => (data.groups || []).filter((row) => row.linked_to_product),
        [data.groups],
    );
    const availableResources = useMemo(() => {
        const needle = query.trim().toLowerCase();
        if (!categoryId) return [];
        return (data.resources || []).filter((row) => {
            if (row.linked_to_product) return false;
            if (!(row.category_ids || []).map(String).includes(String(categoryId))) return false;
            if ((row.option_links || []).length) return false;
            return !needle || `${row.name || ""} ${row.code || ""}`.toLowerCase().includes(needle);
        });
    }, [data.resources, categoryId, query]);
    const availableGroups = useMemo(() => {
        const needle = query.trim().toLowerCase();
        if (!categoryId) return [];
        return (data.groups || []).filter((row) =>
            !row.linked_to_product
            && String(row.category_id || "") === String(categoryId)
            && (!needle || `${row.name || ""} ${(row.resources || []).map((item) => item.name).join(" ")}`.toLowerCase().includes(needle)),
        );
    }, [data.groups, categoryId, query]);

    async function chooseCategory(nextCategoryId) {
        if (!nextCategoryId || busy) return;
        setBusy(true);
        try {
            const result = await saveProductCostCategory(productId, nextCategoryId);
            setData(result);
            toast.success("تم حفظ تصنيف المنتج؛ ستظهر عناصر هذا التصنيف تلقائيًا.");
        } catch (error) {
            toast.error(errorCode(error, "تعذر حفظ تصنيف المنتج"));
        } finally {
            setBusy(false);
        }
    }

    async function addResource(resource) {
        setBusy(true);
        try {
            const result = await linkProductResource(productId, resource.id, 1);
            setData({ ...result, cost_setup: data.cost_setup, product: { ...result.product, cost_category_id: categoryId, cost_category_name: categoryName } });
            toast.success(`تمت إضافة ${resource.name} وحفظها تلقائيًا`);
        } catch (error) {
            toast.error(errorCode(error, "تعذر إضافة العنصر"));
        } finally { setBusy(false); }
    }

    async function removeResource(resource) {
        setBusy(true);
        try {
            const result = await unlinkProductResource(productId, resource.id);
            setData({ ...result, cost_setup: data.cost_setup, product: { ...result.product, cost_category_id: categoryId, cost_category_name: categoryName } });
            toast.success(`تمت إزالة ${resource.name}`);
        } catch (error) {
            toast.error(errorCode(error, "تعذر إزالة العنصر"));
        } finally { setBusy(false); }
    }

    async function addGroup(group) {
        setBusy(true);
        try {
            const result = await linkProductGroups(productId, [group.id]);
            setData({ ...result, cost_setup: data.cost_setup, product: { ...result.product, cost_category_id: categoryId, cost_category_name: categoryName } });
            toast.success(`تمت إضافة مجموعة ${group.name} وحفظها تلقائيًا`);
        } catch (error) {
            toast.error(errorCode(error, "تعذر إضافة المجموعة"));
        } finally { setBusy(false); }
    }

    async function removeGroup(group) {
        setBusy(true);
        try {
            const result = await unlinkProductGroup(productId, group.id);
            setData({ ...result, cost_setup: data.cost_setup, product: { ...result.product, cost_category_id: categoryId, cost_category_name: categoryName } });
            toast.success(`تمت إزالة مجموعة ${group.name}`);
        } catch (error) {
            toast.error(errorCode(error, "تعذر إزالة المجموعة"));
        } finally { setBusy(false); }
    }

    async function completeCostSetup() {
        setBusy(true);
        try {
            const result = await saveProductCostCompletion(productId, true);
            setData(result);
            toast.success("تم حفظ المنتج كتكلفة مكتملة؛ سيختفي من قائمة المنتجات المباعة بدون تكلفة.");
            window.dispatchEvent(new CustomEvent("mezan:product-cost-completed", { detail: { productId } }));
        } catch (error) {
            toast.error(errorCode(error, "تعذر اعتماد اكتمال التكلفة"));
        } finally { setBusy(false); }
    }

    if (loading) {
        return <section className="flex min-h-40 items-center justify-center rounded-2xl border"><SpinnerGap className="animate-spin text-violet-700" /></section>;
    }

    return (
        <section className="space-y-4 rounded-2xl border border-violet-200 bg-violet-50/30 p-3 sm:p-4" data-testid="product-operations-editor" dir="rtl">
            <div>
                <h2 className="font-black">تصنيف المنتج والمكونات والخدمات</h2>
                <p className="mt-1 text-xs leading-6 text-slate-500">اختر تصنيف المنتج مرة واحدة. بعدها يعرض ميزان تلقائيًا المكونات والخدمات التابعة له فقط، بدون إعادة اختيار التصنيف عند كل إضافة.</p>
            </div>

            <div className="rounded-2xl border border-violet-200 bg-white p-4">
                <label className="block text-xs font-black text-slate-600">تصنيف المنتج</label>
                <select
                    value={categoryId}
                    onChange={(event) => chooseCategory(event.target.value)}
                    disabled={busy}
                    className="mt-2 min-h-12 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-black"
                    data-testid="product-cost-category-select"
                >
                    <option value="">اختر التصنيف — مثال: ملابس أو مطليات</option>
                    {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
                </select>
                <p className="mt-2 text-[11px] leading-5 text-slate-500">تكلفة المنتج الأساسية والمكونات والخدمات اختيارية. التصنيف وظيفته تنظيم العناصر التي تظهر لهذا المنتج.</p>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="flex items-center justify-between gap-3">
                    <div>
                        <h3 className="font-black">المرتبط حاليًا</h3>
                        <p className="mt-1 text-xs text-slate-500">أي إضافة أو إزالة تُحفظ مباشرة.</p>
                    </div>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black">{linkedResources.length} عنصر</span>
                </div>
                <div className="mt-3 space-y-2">
                    {linkedGroups.map((group) => (
                        <div key={group.id} className="flex items-center justify-between gap-3 rounded-xl border border-violet-200 bg-violet-50 p-3">
                            <div><div className="font-bold">{group.name}</div><div className="mt-1 text-[11px] text-slate-500">{group.group_kind === "service" ? "مجموعة خدمات" : "مجموعة مكونات"} · {(group.resources || []).length} عناصر</div></div>
                            <button type="button" disabled={busy} onClick={() => removeGroup(group)} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-black text-red-700"><Trash className="ml-1 inline" />إزالة</button>
                        </div>
                    ))}
                    {linkedResources.filter((row) => row.manual_link).map((resource) => (
                        <div key={resource.id} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3">
                            <div><div className="font-bold">{resource.name}</div><div className="mt-1 text-[11px] text-slate-500">{isService(resource) ? "خدمة" : "مكوّن"} · {resource.code || "بدون رمز"}</div></div>
                            <button type="button" disabled={busy} onClick={() => removeResource(resource)} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-black text-red-700"><Trash className="ml-1 inline" />إزالة</button>
                        </div>
                    ))}
                    {!linkedResources.length && <p className="rounded-xl bg-slate-50 p-3 text-xs text-slate-400">لا توجد مكونات أو خدمات، وهذا مسموح.</p>}
                </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <h3 className="font-black">إضافة مكوّن أو خدمة</h3>
                <p className="mt-1 text-xs text-slate-500">المعروض الآن من تصنيف: <strong>{categoryName || "لم يتم تحديد تصنيف"}</strong></p>
                {!categoryId ? (
                    <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-900">حدد تصنيف المنتج أولًا لعرض العناصر المناسبة.</div>
                ) : (
                    <>
                        <label className="relative mt-3 block">
                            <MagnifyingGlass className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث داخل مكونات وخدمات التصنيف…" className="w-full rounded-xl border py-2.5 pr-10 pl-3 text-sm" />
                        </label>
                        <div className="mt-3 grid gap-3 lg:grid-cols-2">
                            {availableGroups.map((group) => (
                                <button key={group.id} type="button" disabled={busy} onClick={() => addGroup(group)} className="rounded-xl border border-violet-200 bg-violet-50 p-3 text-right hover:border-violet-400">
                                    <Cube className="ml-1 inline text-violet-700" /><span className="font-black">{group.name}</span>
                                    <span className="mt-1 block text-[11px] text-slate-500">{group.group_kind === "service" ? "مجموعة خدمات" : "مجموعة مكونات"} · {(group.resources || []).length} عناصر</span>
                                </button>
                            ))}
                            {availableResources.map((resource) => (
                                <button key={resource.id} type="button" disabled={busy} onClick={() => addResource(resource)} className="rounded-xl border border-slate-200 bg-white p-3 text-right hover:border-violet-400">
                                    {isService(resource) ? <Wrench className="ml-1 inline text-violet-700" /> : <Package className="ml-1 inline text-sky-700" />}
                                    <span className="font-black">{resource.name}</span>
                                    <span className="mt-1 block text-[11px] text-slate-500">{isService(resource) ? "خدمة" : "مكوّن"} · {resource.code || "بدون رمز"}</span>
                                </button>
                            ))}
                        </div>
                        {!availableGroups.length && !availableResources.length && <p className="mt-3 rounded-xl bg-slate-50 p-3 text-xs text-slate-400">لا توجد عناصر أخرى متاحة ضمن هذا التصنيف.</p>}
                    </>
                )}
            </div>

            <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                <h3 className="font-black text-emerald-950">اعتماد اكتمال التكلفة</h3>
                <p className="mt-1 text-xs leading-6 text-emerald-900">التكلفة والمكونات والخدمات ليست إجبارية. هذا الزر فقط يعني أن مراجعة المنتج انتهت، وهو وحده الذي يخفي المنتج من قائمة «المنتجات المباعة بدون تكلفة».</p>
                <button
                    type="button"
                    disabled={busy || data?.cost_setup?.complete === true}
                    onClick={completeCostSetup}
                    className="mt-3 flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 font-black text-white disabled:opacity-50"
                    data-testid="product-cost-complete-button"
                >
                    <CheckCircle size={20} />
                    {data?.cost_setup?.complete === true ? "المنتج مكتمل التكلفة" : "حفظ المنتج كتكلفة مكتملة"}
                </button>
            </div>
        </section>
    );
}
