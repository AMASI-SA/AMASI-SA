import { useEffect, useMemo, useRef, useState } from "react";
import { LinkSimple, Plus, SpinnerGap, Trash } from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    deleteProductOptionCost,
    getProductOptionCosts,
    refreshProductV2Details,
    saveProductOptionCost,
} from "../../services/mezanProductsV2";

function money(value) {
    if (value === null || value === undefined || value === "") return "—";
    return `${Number(value).toFixed(2)} ر.س`;
}

function fieldTypeLabel(type) {
    const value = String(type || "text").toLowerCase();
    return ({
        text: "حقل نصي قصير", textarea: "حقل نصي طويل", long_text: "حقل نصي طويل",
        number: "رقم", select: "قائمة اختيار", radio: "اختيار واحد",
        checkbox: "اختيارات متعددة", date: "تاريخ", time: "وقت", file: "رفع ملف",
    })[value] || value;
}

function isFillBased(type) {
    return ["text", "textarea", "long_text", "number", "date", "time", "file"].includes(String(type || "").toLowerCase());
}

function customFieldSubjects(fields) {
    return (fields || []).map((field) => ({
        id: field.cost_subject_id || `field:${field.id}`,
        name: field.name,
        type: field.type || "text",
        required: field.required,
        isCustomField: true,
        isFillField: true,
        values: [{ id: field.cost_value_id || "filled", name: "عند تعبئة الحقل" }],
    }));
}

function optionSubjects(options) {
    return (options || []).map((option) => {
        const values = option.values || [];
        if (!values.length && isFillBased(option.type)) {
            return { ...option, isFillField: true, values: [{ id: "filled", name: "عند تعبئة الحقل" }] };
        }
        return { ...option, hasCatalogValues: Boolean(values.length) };
    });
}

function costSubjects(options, fields, bindings) {
    const subjects = [...optionSubjects(options), ...customFieldSubjects(fields)];
    const byId = new Map(subjects.map((option) => [String(option.id), option]));
    for (const binding of bindings) {
        const id = String(binding.option_id);
        if (!byId.has(id)) {
            const option = { id: binding.option_id, name: binding.option_name || "خيار سابق", values: [], missingOption: true };
            subjects.push(option);
            byId.set(id, option);
        }
    }
    return subjects.map((option) => {
        const saved = bindings.filter((binding) => String(binding.option_id) === String(option.id));
        const hasLegacy = saved.some((binding) => String(binding.value_id) === "__option__");
        const conflict = hasLegacy && saved.some((binding) => String(binding.value_id) === "filled");
        const rows = [];
        const represented = new Set();
        for (const value of option.values || []) {
            const matching = saved.filter((binding) => String(binding.value_id) === String(value.id));
            matching.forEach((binding) => {
                rows.push({ value, binding });
                represented.add(binding);
            });
            // The legacy condition already has a cost. Showing a second, free
            // "filled" row hid that cost and invited creation of another charge.
            if (!matching.length && !(String(value.id) === "filled" && hasLegacy)) rows.push({ value });
        }
        for (const binding of saved) {
            if (represented.has(binding)) continue;
            const legacy = String(binding.value_id) === "__option__";
            rows.push({
                binding,
                value: { id: binding.value_id, name: legacy ? "عند ورود الخيار في الطلب (ربط سابق)" : binding.value_name || "قيمة سابقة" },
                orphan: option.missingOption || !legacy || option.hasCatalogValues || option.isCustomField,
            });
        }
        return { ...option, rows, conflict };
    });
}

export default function ProductOptionCostEditor({ productId, options = [], customFields = [] }) {
    const [data, setData] = useState({ bindings: [], resources: [] });
    const [detailFields, setDetailFields] = useState(customFields || []);
    const [detailOptions, setDetailOptions] = useState(options || []);
    const [editing, setEditing] = useState(null);
    const [busy, setBusy] = useState(false);
    const [loadState, setLoadState] = useState({ productId: null, status: "loading", error: "" });
    const request = useRef(0);
    const mutation = useRef(0);
    const currentProduct = useRef(productId);
    currentProduct.current = productId;
    const ready = loadState.productId === productId && loadState.status === "ready";

    async function load() {
        if (!productId) return;
        const token = ++request.current;
        const id = productId;
        setLoadState({ productId: id, status: "loading", error: "" });
        setEditing(null);
        try {
            const [costResult, detailResult] = await Promise.all([
                getProductOptionCosts(id),
                refreshProductV2Details(id),
            ]);
            if (token !== request.current || currentProduct.current !== id) return;
            if (!Array.isArray(costResult?.bindings)) throw new Error("Invalid option costs response");
            setData(costResult);
            setDetailFields(detailResult?.product?.custom_fields || customFields || []);
            setDetailOptions(detailResult?.product?.options || options || []);
            setLoadState({ productId: id, status: "ready", error: "" });
        } catch (error) {
            if (token !== request.current || currentProduct.current !== id) return;
            setLoadState({ productId: id, status: "error", error: "تعذر تحميل تكاليف الخيارات والحقول. أعد المحاولة لمعرفة التكلفة المسجلة." });
        }
    }

    useEffect(() => {
        setEditing(null);
        setBusy(false);
        load();
        return () => { request.current += 1; mutation.current += 1; };
    }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => {
        const refresh = (event) => {
            if (!event?.detail?.productId || event.detail.productId === productId) load();
        };
        window.addEventListener("mezan:product-resource-links-changed", refresh);
        return () => window.removeEventListener("mezan:product-resource-links-changed", refresh);
    }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

    const subjects = useMemo(
        () => costSubjects(detailOptions, detailFields, data.bindings || []),
        [detailFields, detailOptions, data.bindings],
    );

    function openEditor(option, row) {
        if (!ready || busy) return;
        const current = row.binding;
        const firstActiveResource = (data.resources || []).find((row) => (
            row.status !== "inactive" && row.available_for_option_link !== false
        ));
        setEditing({
            option, value: row.value, binding: current, orphan: row.orphan,
            mode: current?.mode || "resource",
            resource_id: current?.resource_id || firstActiveResource?.id || "",
            direct_amount: current?.direct_amount ?? "",
            quantity: current?.quantity || 1,
        });
    }

    async function save() {
        if (!editing || editing.orphan || !ready || busy) return;
        const token = ++mutation.current;
        const id = productId;
        setBusy(true);
        try {
            await saveProductOptionCost(id, editing.binding?.option_id ?? editing.option.id, editing.binding?.value_id ?? editing.value.id, {
                mode: editing.mode,
                resource_id: editing.mode === "resource" ? editing.resource_id : null,
                direct_amount: editing.mode === "direct" ? editing.direct_amount : null,
                quantity: editing.quantity,
            });
            if (token !== mutation.current || currentProduct.current !== id) return;
            toast.success("تم حفظ التكلفة الإضافية لهذا الخيار");
            setEditing(null);
            await load();
        } catch (error) {
            if (token !== mutation.current || currentProduct.current !== id) return;
            const code = error?.response?.data?.detail?.code;
            toast.error(code === "option_cost_binding_conflict"
                ? "يوجد ربط تكلفة محفوظ لهذا الحقل. أعد تحميل التكاليف وراجع الروابط قبل الإضافة."
                : code === "resource_already_linked_to_product"
                ? "هذه الخدمة أو المكوّن مرتبط بالمنتج مباشرة. ألغِ ذلك الربط أولًا."
                : code === "component_inactive"
                    ? "المكوّن أو الخدمة موقوفة. أعد تفعيلها أولًا."
                    : code || "تعذر حفظ التكلفة");
        } finally { if (token === mutation.current && currentProduct.current === id) setBusy(false); }
    }

    async function remove() {
        if (!editing?.binding || !ready || busy) return;
        const token = ++mutation.current;
        const id = productId;
        setBusy(true);
        try {
            await deleteProductOptionCost(id, editing.binding.option_id, editing.binding.value_id);
            if (token !== mutation.current || currentProduct.current !== id) return;
            toast.success("تم حذف التكلفة الإضافية");
            setEditing(null);
            await load();
        } catch (error) {
            if (token === mutation.current && currentProduct.current === id) toast.error("تعذر حذف التكلفة. أعد المحاولة.");
        } finally { if (token === mutation.current && currentProduct.current === id) setBusy(false); }
    }

    return (
        <div className="space-y-5">
            <section className="rounded-2xl border border-slate-200 p-4">
                <div className="mb-4">
                    <h2 className="font-black">تكاليف الخيارات والحقول</h2>
                    <p className="mt-1 text-xs leading-6 text-slate-500">تكلفة المنتج الأساسية تُحتسب دائمًا. تُضاف كل تكلفة بحسب الشرط الموضح في ربطها، وتظهر الروابط السابقة أيضًا للمراجعة.</p>
                </div>
                {!productId ? <p className="text-sm text-slate-400">اختر منتجًا لعرض تكاليفه.</p> : !ready ? (
                    loadState.productId === productId && loadState.status === "error"
                        ? <div role="alert" className="text-sm text-rose-700">{loadState.error}<button onClick={load} className="mr-2 underline">إعادة تحميل التكاليف</button></div>
                        : <p role="status" className="text-sm text-slate-500"><SpinnerGap className="ml-1 inline animate-spin" /> جارٍ تحميل تكاليف الخيارات والحقول…</p>
                ) : !subjects.length ? <p className="text-sm text-slate-400">لا توجد خيارات أو حقول مخصصة في المنتج.</p> : (
                    <div className="space-y-4">
                        {subjects.map((option) => (
                            <div key={`${option.id}:${option.isCustomField ? "field" : "option"}`} className="rounded-xl border border-slate-200 p-3">
                                <div className="mb-3 flex flex-wrap items-center gap-2">
                                    <div className="font-black">{option.name}</div>
                                    <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-600">{option.missingOption ? "خيار غير موجود حاليًا" : fieldTypeLabel(option.type || "select")}</span>
                                    {option.required && <span className="rounded-full bg-rose-100 px-2 py-1 text-[10px] font-bold text-rose-700">إلزامي</span>}
                                </div>
                                {option.conflict && <p role="alert" className="mb-3 rounded-lg bg-amber-50 p-3 text-xs leading-6 text-amber-900">لهذا الحقل أكثر من ربط تكلفة محفوظ. قد تُحتسب هذه الروابط معًا؛ راجع شرط ومبلغ كل ربط قبل تغييره.</p>}
                                <div className="space-y-2">
                                    {option.rows.map((row, index) => {
                                        const { value, binding, orphan } = row;
                                        return (
                                            <div key={`${binding?.id || value.id}:${index}`} role="group" aria-label={`تكلفة ${option.name}: ${value.name}`} className="flex flex-col gap-2 rounded-xl bg-slate-50 p-3 sm:flex-row sm:items-center sm:justify-between">
                                                <div><div className="font-bold">{value.name}</div><div className="mt-1 text-[11px] text-slate-500">{binding ? `${binding.mode === "resource" ? binding.resource?.name || "مكوّن مشترك" : "مبلغ مباشر"} · ${binding.cost_missing || binding.resolved_amount == null ? "التكلفة غير مسجلة" : `+${money(binding.resolved_amount)}`}` : "لا توجد تكلفة إضافية"}</div>{orphan && <p className="mt-1 text-xs text-amber-800">ربط محفوظ لخيار أو قيمة غير موجودة حاليًا.</p>}</div>
                                                <button disabled={busy} onClick={() => openEditor(option, row)} className="rounded-lg border border-violet-300 bg-white px-3 py-2 text-xs font-black text-violet-800 disabled:opacity-50">{binding ? <LinkSimple className="ml-1 inline" /> : <Plus className="ml-1 inline" />} {orphan ? "مراجعة الربط" : binding ? "تعديل التكلفة" : "إضافة تكلفة"}</button>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </section>

            {ready && editing && (
                <div role="dialog" aria-modal="true" aria-label={`${editing.option.name}: ${editing.value.name}`} className="fixed inset-0 z-[150] flex items-center justify-center bg-slate-950/50 p-4" dir="rtl">
                    <div className="w-full max-w-lg rounded-3xl bg-white p-6 shadow-2xl">
                        <h3 className="text-lg font-black">{editing.option.name}: {editing.value.name}</h3>
                        <p className="mt-1 text-xs text-slate-500">{String(editing.value.id) === "__option__" ? "تُضاف هذه التكلفة عند ورود هذا الخيار في الطلب وفق الربط السابق المحفوظ." : String(editing.value.id) === "filled" ? "تُضاف هذه التكلفة فقط عندما يرسل العميل قيمة غير فارغة في هذا الحقل." : "تُضاف هذه التكلفة فقط عند اختيار العميل لهذه القيمة."}</p>
                        {editing.orphan && <p role="alert" className="mt-3 text-sm text-amber-800">الخيار أو القيمة غير موجودة في كتالوج المنتج الحالي. يمكنك حذف هذا الربط المحفوظ، أو استعادة الخيار قبل تعديل تكلفته.</p>}
                        <fieldset disabled={busy || editing.orphan}>
                        <div className="mt-5 grid grid-cols-2 gap-2">
                            <button onClick={() => setEditing((row) => ({ ...row, mode: "resource" }))} className={`rounded-xl border p-3 text-sm font-bold ${editing.mode === "resource" ? "border-violet-500 bg-violet-50" : "border-slate-200"}`}>مكوّن مشترك</button>
                            <button onClick={() => setEditing((row) => ({ ...row, mode: "direct" }))} className={`rounded-xl border p-3 text-sm font-bold ${editing.mode === "direct" ? "border-violet-500 bg-violet-50" : "border-slate-200"}`}>مبلغ مباشر</button>
                        </div>
                        {editing.mode === "resource" ? (
                            <div className="mt-4 grid gap-3 sm:grid-cols-2">
                                <label className="text-xs font-bold text-slate-500">المكوّن أو الخدمة<select value={editing.resource_id} onChange={(e) => setEditing((row) => ({ ...row, resource_id: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm text-slate-900"><option value="">اختر المكوّن</option>{(data.resources || []).map((resource) => <option key={resource.id} value={resource.id} disabled={resource.linked_to_product || resource.status === "inactive" || resource.available_for_option_link === false}>{resource.name} — {money(resource.unit_cost)}{resource.status === "inactive" ? " — موقوف" : resource.linked_to_product ? " — مرتبط بالمنتج" : ""}</option>)}</select></label>
                                <label className="text-xs font-bold text-slate-500">الكمية<input type="number" min="0.0001" step="0.01" value={editing.quantity} onChange={(e) => setEditing((row) => ({ ...row, quantity: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm text-slate-900" /></label>
                            </div>
                        ) : <label className="mt-4 block text-xs font-bold text-slate-500">التكلفة الإضافية<input type="number" min="0" step="0.01" value={editing.direct_amount} onChange={(e) => setEditing((row) => ({ ...row, direct_amount: e.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm text-slate-900" /></label>}
                        </fieldset>
                        <div className="mt-6 flex items-center justify-between gap-3">
                            <div>{editing.binding && <button disabled={busy} onClick={remove} className="rounded-xl border border-rose-200 px-4 py-3 text-sm font-bold text-rose-700"><Trash className="ml-1 inline" /> حذف</button>}</div>
                            <div className="flex gap-2"><button disabled={busy} onClick={() => setEditing(null)} className="rounded-xl border px-4 py-3 text-sm font-bold">إلغاء</button><button disabled={busy || editing.orphan || (editing.mode === "resource" ? (!editing.resource_id || (data.resources || []).some((row) => row.id === editing.resource_id && (row.status === "inactive" || row.available_for_option_link === false))) : editing.direct_amount === "")} onClick={save} className="rounded-xl bg-violet-700 px-5 py-3 text-sm font-black text-white disabled:opacity-50">{busy && <SpinnerGap className="ml-1 inline animate-spin" />} حفظ</button></div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
