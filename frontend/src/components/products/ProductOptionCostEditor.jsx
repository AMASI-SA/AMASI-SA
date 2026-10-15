import { useEffect, useMemo, useState } from "react";
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
        return option;
    });
}

export default function ProductOptionCostEditor({ productId, options = [], customFields = [] }) {
    const [data, setData] = useState({ bindings: [], resources: [] });
    const [detailFields, setDetailFields] = useState(customFields || []);
    const [detailOptions, setDetailOptions] = useState(options || []);
    const [editing, setEditing] = useState(null);
    const [busy, setBusy] = useState(false);

    async function load() {
        if (!productId) return;
        try {
            const [costResult, detailResult] = await Promise.all([
                getProductOptionCosts(productId),
                refreshProductV2Details(productId),
            ]);
            setData(costResult);
            setDetailFields(detailResult?.product?.custom_fields || customFields || []);
            setDetailOptions(detailResult?.product?.options || options || []);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر تحميل تكاليف الخيارات والحقول");
        }
    }

    useEffect(() => { load(); }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => {
        const refresh = (event) => {
            if (!event?.detail?.productId || event.detail.productId === productId) load();
        };
        window.addEventListener("mezan:product-resource-links-changed", refresh);
        return () => window.removeEventListener("mezan:product-resource-links-changed", refresh);
    }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

    const bindings = useMemo(() => {
        const map = new Map();
        for (const row of data.bindings || []) map.set(`${row.option_id}:${row.value_id}`, row);
        return map;
    }, [data.bindings]);

    const subjects = useMemo(
        () => [...optionSubjects(detailOptions), ...customFieldSubjects(detailFields)],
        [detailFields, detailOptions],
    );

    function openEditor(option, value) {
        const current = bindings.get(`${option.id}:${value.id}`);
        const firstActiveResource = (data.resources || []).find((row) => (
            row.status !== "inactive" && row.available_for_option_link !== false
        ));
        setEditing({
            option, value,
            mode: current?.mode || "resource",
            resource_id: current?.resource_id || firstActiveResource?.id || "",
            direct_amount: current?.direct_amount ?? "",
            quantity: current?.quantity || 1,
        });
    }

    async function save() {
        if (!editing) return;
        setBusy(true);
        try {
            await saveProductOptionCost(productId, editing.option.id, editing.value.id, {
                mode: editing.mode,
                resource_id: editing.mode === "resource" ? editing.resource_id : null,
                direct_amount: editing.mode === "direct" ? editing.direct_amount : null,
                quantity: editing.quantity,
            });
            toast.success(editing.option.isFillField ? "تم حفظ تكلفة تعبئة الحقل" : "تم حفظ التكلفة الإضافية لهذا الخيار");
            setEditing(null);
            await load();
        } catch (error) {
            const code = error?.response?.data?.detail?.code;
            toast.error(code === "resource_already_linked_to_product"
                ? "هذه الخدمة أو المكوّن مرتبط بالمنتج مباشرة. ألغِ ذلك الربط أولًا."
                : code === "component_inactive"
                    ? "المكوّن أو الخدمة موقوفة. أعد تفعيلها أولًا."
                    : code || "تعذر حفظ التكلفة");
        } finally { setBusy(false); }
    }

    async function remove(option, value) {
        setBusy(true);
        try {
            await deleteProductOptionCost(productId, option.id, value.id);
            toast.success("تم حذف التكلفة الإضافية");
            setEditing(null);
            await load();
        } finally { setBusy(false); }
    }

    return (
        <div className="space-y-5">
            <section className="rounded-2xl border border-slate-200 p-4">
                <div className="mb-4">
                    <h2 className="font-black">تكاليف الخيارات والحقول</h2>
                    <p className="mt-1 text-xs leading-6 text-slate-500">تكلفة المنتج الأساسية تُحتسب دائمًا. تكلفة قيمة الخيار تُضاف عند اختيارها، وتكلفة الحقل النصي تُضاف فقط عندما يعبئه العميل.</p>
                </div>
                {!subjects.length ? <p className="text-sm text-slate-400">لا توجد خيارات أو حقول مخصصة في المنتج.</p> : (
                    <div className="space-y-4">
                        {subjects.map((option) => (
                            <div key={`${option.id}:${option.isCustomField ? "field" : "option"}`} className="rounded-xl border border-slate-200 p-3">
                                <div className="mb-3 flex flex-wrap items-center gap-2">
                                    <div className="font-black">{option.name}</div>
                                    <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-600">{fieldTypeLabel(option.type || "select")}</span>
                                    {option.required && <span className="rounded-full bg-rose-100 px-2 py-1 text-[10px] font-bold text-rose-700">إلزامي</span>}
                                </div>
                                <div className="space-y-2">
                                    {(option.values || []).map((value) => {
                                        const binding = bindings.get(`${option.id}:${value.id}`);
                                        return (
                                            <div key={value.id} className="flex flex-col gap-2 rounded-xl bg-slate-50 p-3 sm:flex-row sm:items-center sm:justify-between">
                                                <div><div className="font-bold">{value.name}</div><div className="mt-1 text-[11px] text-slate-500">{binding ? `${binding.mode === "resource" ? binding.resource?.name || "مكوّن مشترك" : "مبلغ مباشر"} · +${money(binding.resolved_amount)}` : "لا توجد تكلفة إضافية"}</div></div>
                                                <button onClick={() => openEditor(option, value)} className="rounded-lg border border-violet-300 bg-white px-3 py-2 text-xs font-black text-violet-800">{binding ? <LinkSimple className="ml-1 inline" /> : <Plus className="ml-1 inline" />} {binding ? "تعديل التكلفة" : "إضافة تكلفة"}</button>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </section>

            {editing && (
                <div className="fixed inset-0 z-[150] flex items-center justify-center bg-slate-950/50 p-4" dir="rtl">
                    <div className="w-full max-w-lg rounded-3xl bg-white p-6 shadow-2xl">
                        <h3 className="text-lg font-black">{editing.option.name}: {editing.value.name}</h3>
                        <p className="mt-1 text-xs text-slate-500">{editing.option.isFillField ? "تُضاف هذه التكلفة فقط عندما يرسل العميل قيمة غير فارغة في هذا الحقل." : "تُضاف هذه التكلفة فقط عند اختيار العميل لهذه القيمة."}</p>
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
                        <div className="mt-6 flex items-center justify-between gap-3">
                            <div>{bindings.has(`${editing.option.id}:${editing.value.id}`) && <button disabled={busy} onClick={() => remove(editing.option, editing.value)} className="rounded-xl border border-rose-200 px-4 py-3 text-sm font-bold text-rose-700"><Trash className="ml-1 inline" /> حذف</button>}</div>
                            <div className="flex gap-2"><button onClick={() => setEditing(null)} className="rounded-xl border px-4 py-3 text-sm font-bold">إلغاء</button><button disabled={busy || (editing.mode === "resource" ? (!editing.resource_id || (data.resources || []).some((row) => row.id === editing.resource_id && (row.status === "inactive" || row.available_for_option_link === false))) : editing.direct_amount === "")} onClick={save} className="rounded-xl bg-violet-700 px-5 py-3 text-sm font-black text-white disabled:opacity-50">{busy && <SpinnerGap className="ml-1 inline animate-spin" />} حفظ</button></div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
