import { useEffect, useState } from "react";
import { getProductV2, listProductsV2 } from "../../services/mezanProductsV2";
import { getMezanComponentWorkspace } from "../../services/mezanComponentCatalog";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";
import { bundleError, bundleLeafCost, bundleMoney, bundleOptionVisible, getProductBundle, saveProductBundle, previewProductBundle } from "../../services/productBundles";
import CurrentProductSlots, { resizeCurrentSlots } from "./CurrentProductSlots";

const control = "w-full rounded-lg border p-2 text-sm";
const idOf = (item) => String(item.mezan_product_id || item.id);
function frozenProductNames(expansion) {
    const names = {};
    Object.entries(expansion?.frozen_definitions || {}).forEach(([key, definition]) => {
        const product = definition.catalog_product || {};
        [key, product.id, product.mezan_product_id, product.salla_product_id].filter(Boolean).forEach((id) => { if (product.name) names[String(id)] = product.name; });
    });
    return names;
}

export default function BundleEditor({ productId, salePrice }) {
    const [definition, setDefinition] = useState(null);
    const [expansion, setExpansion] = useState(null);
    const [limits, setLimits] = useState({ max_items: 100, max_depth: 5, max_leaves: 500 });
    const [resources, setResources] = useState([]);
    const [suppliers, setSuppliers] = useState([]);
    const [products, setProducts] = useState([]);
    const [productNames, setProductNames] = useState({});
    const [query, setQuery] = useState("");
    const [kind, setKind] = useState("product");
    const [selected, setSelected] = useState("");
    const [variants, setVariants] = useState([]);
    const [variant, setVariant] = useState("");
    const [variantLoading, setVariantLoading] = useState(false);
    const [rootVariants, setRootVariants] = useState([]);
    const [rootVariant, setRootVariant] = useState("");
    const [customerOptions, setCustomerOptions] = useState([]);
    const [choices, setChoices] = useState([]);
    const [childOptions, setChildOptions] = useState({});
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [busy, setBusy] = useState(false);
    const [dirty, setDirty] = useState(false);
    const [reload, setReload] = useState(0);
    const [textAnswers, setTextAnswers] = useState({});
    const [saleQuantity, setSaleQuantity] = useState(1);
    const [scopeResources, setScopeResources] = useState([]);

    useEffect(() => {
        let live = true;
        setDefinition(null); setExpansion(null); setError(""); setDirty(false); setNotice("");
        setChoices([]); setChildOptions({}); setRootVariant(""); setTextAnswers({}); setSaleQuantity(1);
        Promise.all([getProductBundle(productId), getMezanComponentWorkspace(), loadMezanSuppliersWorkspace(), getProductV2(productId)])
            .then(([bundle, components, vendors, product]) => {
                if (!live) return;
                setDefinition(bundle.definition); setExpansion(bundle.expansion); setLimits(bundle.limits);
                setProductNames(frozenProductNames(bundle.expansion));
                setResources(components.components || []); setSuppliers(vendors.suppliers || []);
                setCustomerOptions(product.options || product.product?.options || []);
                setRootVariants(product.variants || product.product?.variants || []);
                setScopeResources(bundle.scope_resources || []);
            }).catch((err) => { if (live) setError(bundleError(err)); });
        return () => { live = false; };
    }, [productId, reload]);

    useEffect(() => {
        let live = true;
        const timer = setTimeout(() => {
            listProductsV2({ query, perPage: 50 }).then((result) => {
                if (live) {
                    const found = result.items || [];
                    setProducts(found.filter((item) => idOf(item) !== String(productId)));
                    setProductNames((current) => ({ ...current, ...Object.fromEntries(found.flatMap((item) => [item.id, item.mezan_product_id, item.salla_product_id].filter(Boolean).map((id) => [String(id), item.name]))) }));
                }
            }).catch((err) => { if (live) setError(bundleError(err)); });
        }, 250);
        return () => { live = false; clearTimeout(timer); };
    }, [query, productId]);

    useEffect(() => {
        let live = true;
        setVariants([]); setVariant("");
        setVariantLoading(Boolean(selected && kind === "product"));
        if (selected && kind === "product") getProductV2(selected).then((result) => {
            if (live) setVariants(result.variants || result.product?.variants || []);
        }).catch((err) => { if (live) setError(bundleError(err)); }).finally(() => { if (live) setVariantLoading(false); });
        return () => { live = false; };
    }, [selected, kind]);

    function edit(next) { setDefinition(next); setDirty(true); setExpansion(null); setNotice(""); }
    function update(index, patch) { edit({ ...definition, items: definition.items.map((item, i) => i === index ? { ...item, ...patch } : item) }); }
    function move(index, offset) {
        const items = [...definition.items];
        [items[index], items[index + offset]] = [items[index + offset], items[index]];
        edit({ ...definition, items });
    }
    const options = kind === "product" ? products : resources.filter((item) => `${item.name} ${item.code || ""}`.toLowerCase().includes(query.toLowerCase()));
    function add() {
        if (kind === "current_product") {
            if (definition.items.length < limits.max_items) edit({ ...definition, items: [...definition.items, { kind, quantity: "1", unit: "unit", sourcing_mode: "stock", required: true }] });
            return;
        }
        const item = options.find((row) => idOf(row) === selected);
        if (!item || definition.items.length >= limits.max_items) return;
        edit({ ...definition, items: [...definition.items, { kind, ...(kind === "product" ? { product_id: selected } : { resource_id: selected }), variant_id: variant || null, name: item.name, quantity: "1", unit: item.unit || "unit", sourcing_mode: "stock", supplier_id: null, location_id: null, required: true }] });
        setSelected("");
    }
    async function save() {
        setBusy(true); setError("");
        try {
            const result = await saveProductBundle(productId, {
                expected_revision: definition.revision, product_type: definition.product_type,
                items: definition.product_type === "normal" ? [] : definition.items.map(({ name, ...item }) => item),
                ...(definition.component_scopes ? { component_scopes: definition.component_scopes } : {}),
            });
            setDefinition(result.definition); setExpansion(result.expansion); setDirty(false); setScopeResources(result.scope_resources || scopeResources);
            setProductNames((current) => ({ ...current, ...frozenProductNames(result.expansion) }));
            setNotice("حُفظ تركيب البكج. الطلبات السابقة تحتفظ بتركيبها وقت البيع.");
        } catch (err) { setError(bundleError(err)); } finally { setBusy(false); }
    }
    async function preview() {
        setBusy(true); setError(""); setExpansion(null);
        try {
            const visibleText = customerOptions.filter((option) => !option.values?.length && bundleOptionVisible(option, choices));
            const filled = visibleText.filter((option) => textAnswers[option.id]?.trim()).map((option) => String(option.id));
            const customizationIds = new Set(definition.items.flatMap((item) => (item.slots || []).flatMap((slot) => slot.customization_option_ids || [])));
            const answers = Object.fromEntries(filled.filter((id) => customizationIds.has(id)).map((id) => [id, textAnswers[id]]));
            const context = visibleText.length ? { present_option_ids: visibleText.map((option) => String(option.id)), filled_option_ids: filled, ...(Object.keys(answers).length ? { option_answers: answers } : {}) } : null;
            const result = context ? await previewProductBundle(productId, choices, rootVariant || undefined, context) : rootVariant ? await previewProductBundle(productId, choices, rootVariant) : await previewProductBundle(productId, choices);
            setExpansion(result.expansion || result);
        } catch (err) { setError(bundleError(err)); } finally { setBusy(false); }
    }
    function choose(option, valueId, checked) {
        const multiple = ["checkbox", "multiple", "multi_select"].includes(option.type);
        setChoices((current) => {
            let next = [...current.filter((entry) => entry.option_id !== String(option.id) || (multiple && entry.value_id !== String(valueId))), ...(checked ? [{ option_id: String(option.id), value_id: String(valueId) }] : [])];
            for (let i = 0; i < customerOptions.length; i += 1) next = next.filter((entry) => bundleOptionVisible(customerOptions.find((item) => String(item.id) === entry.option_id) || {}, next));
            return next;
        });
        setExpansion(null);
    }
    async function loadChildOptions(id) {
        try {
            const result = await getProductV2(id);
            setChildOptions((current) => ({ ...current, [id]: result.options || result.product?.options || [] }));
        } catch (err) { setError(bundleError(err)); }
    }
    const missingScope = definition?.items.some((item) => item.kind === "current_product") && scopeResources.some((resource) => !definition.component_scopes?.[resource.resource_id]);
    const invalid = definition?.product_type === "composite" && (!definition.items.length || definition.items.some((row) => !Number.isFinite(Number(row.quantity)) || Number(row.quantity) <= 0 || !row.unit.trim() || (row.sourcing_mode === "supplier" && !row.supplier_id) || (row.kind === "current_product" && (!Number.isInteger(Number(row.quantity)) || Number(row.quantity) > 100 || (row.slots || []).some((slot) => (slot.variant_rules || []).some((rule) => !rule.variant_id || rule.conditions.some((condition) => !condition.option_id || !condition.value_id)))))));
    return <section dir="rtl" className="space-y-4 rounded-2xl border border-violet-200 bg-white p-4" data-testid="bundle-editor">
        <h2 className="font-black">تركيب المنتج والبكجات الذكية</h2>
        <p className="text-xs text-slate-600">حتى {limits.max_items} مكوّنًا مباشرًا، و{limits.max_depth} مستويات تداخل، و{limits.max_leaves} مكوّنًا نهائيًا. يُفكّ البكج الداخلي تلقائيًا؛ يمنع الخادم الربط الدائري.</p>
        {error && <div role="alert" className="text-sm text-red-700">{error} <button type="button" onClick={() => setReload((value) => value + 1)}>إعادة تحميل التركيب</button></div>}
        {!definition ? <p>جارٍ تحميل التركيب…</p> : <fieldset disabled={busy} className="space-y-4">
            <label className="block">نوع التركيب<select className={control} value={definition.product_type} onChange={(e) => edit({ ...definition, product_type: e.target.value })}><option value="normal">منتج عادي</option><option value="composite">منتج مركّب / بكج</option></select></label>
            {definition.product_type === "composite" && <>
                <div className="grid gap-2 sm:grid-cols-2">
                    <label>مصدر المكوّن<select className={control} value={kind} onChange={(e) => { setKind(e.target.value); setSelected(""); }}><option value="product">منتجات ميزان 2</option><option value="resource">مكونات ميزان 2</option><option value="current_product">نفس المنتج الحالي</option></select></label>
                    <label>بحث بالاسم أو الرمز<input className={control} value={query} onChange={(e) => { setQuery(e.target.value); setSelected(""); }} /></label>
                    {kind !== "current_product" && <label>المكوّن<select className={control} value={selected} onChange={(e) => setSelected(e.target.value)}><option value="">اختر من النتائج</option>{options.map((item) => <option key={idOf(item)} value={idOf(item)}>{item.name} {item.sku || item.code || ""}</option>)}</select></label>}
                    {kind === "product" && <label>المتغير<select className={control} value={variant} onChange={(e) => setVariant(e.target.value)}><option value="">المنتج الأساسي</option>{variants.map((item) => <option key={item.id} value={item.id}>{item.display_name || item.name || item.sku || item.id}</option>)}</select></label>}
                </div>
                <button type="button" className="rounded-lg border px-4 py-2" disabled={(kind !== "current_product" && (!selected || variantLoading || (variants.length > 0 && !variant))) || definition.items.length >= limits.max_items} onClick={add}>إضافة مكوّن ({definition.items.length}/{limits.max_items})</button>
                {definition.items.map((row, index) => <fieldset key={index} className="space-y-2 rounded-xl border p-3">
                    <legend className="px-2 font-bold">{index + 1}. {row.kind === "current_product" ? "نفس المنتج الحالي" : row.name || productNames[row.product_id] || resources.find((item) => idOf(item) === String(row.resource_id))?.name || row.product_id || row.resource_id}{row.variant_id ? ` · ${row.variant_id}` : ""} · {row.condition ? "مكوّن مشروط" : "مكوّن عام"}</legend>
                    <div className="grid gap-2 sm:grid-cols-3">
                        <label>الكمية<input aria-label={`كمية المكوّن ${index + 1}`} type="number" min={row.kind === "current_product" ? "1" : "0.000001"} max={row.kind === "current_product" ? "100" : undefined} step={row.kind === "current_product" ? "1" : "any"} className={control} value={row.quantity} onChange={(e) => update(index, { quantity: e.target.value, ...(row.slots?.length ? { slots: resizeCurrentSlots(row.slots, e.target.value) } : {}) })} /></label>
                        <label>الوحدة<input className={control} value={row.unit} onChange={(e) => update(index, { unit: e.target.value })} /></label>
                        <label>التوريد<select className={control} value={row.sourcing_mode} onChange={(e) => update(index, { sourcing_mode: e.target.value })}><option value="stock">من المخزون</option><option value="supplier">من المورد</option></select></label>
                        <label>المورد<select className={control} value={row.supplier_id || ""} onChange={(e) => update(index, { supplier_id: e.target.value || null })}><option value="">اختر المورد</option>{suppliers.map((item) => <option key={item.id} value={item.id}>{item.company_name || item.name}</option>)}</select></label>
                        <label>معرّف موقع المخزون (اختياري)<input className={control} value={row.location_id || ""} onChange={(e) => update(index, { location_id: e.target.value || null })} /></label>
                        <label className="flex items-center gap-2"><input type="checkbox" checked={row.required !== false} onChange={(e) => update(index, { required: e.target.checked })} />مكوّن مطلوب</label>
                        <label>إضافة المكوّن<select className={control} value={row.condition ? JSON.stringify(row.condition) : ""} onChange={(e) => update(index, { condition: e.target.value ? JSON.parse(e.target.value) : null })}><option value="">عام — مع كل بكج</option>{customerOptions.flatMap((option) => (option.values || []).map((value) => <option key={`${option.id}:${value.id}`} value={JSON.stringify({ option_id: String(option.id), value_id: String(value.id) })}>{option.name}: {value.name}</option>))}</select></label>
                        <label>عند تكرار المكوّن<select className={control} value={row.repeat_policy || "once"} onChange={(e) => update(index, { repeat_policy: e.target.value })}><option value="once">مرة واحدة</option><option value="sum">جمع الكميات صراحةً</option></select></label>
                        {row.kind === "resource" && <label>تطبيق كمية المكوّن<select className={control} value={row.component_scope || "per_bundle"} onChange={(e) => update(index, { component_scope: e.target.value })}><option value="per_bundle">لكل بكج — مثل التغليف</option><option value="per_unit">لكل وحدة من نفس المنتج الحالي</option></select></label>}
                    </div>
                    {row.kind === "current_product" && <CurrentProductSlots row={row} variants={rootVariants} options={customerOptions} onChange={(patch) => update(index, patch)} />}
                    {row.kind === "product" && <div className="space-y-2"><button type="button" className="text-sm underline" onClick={() => loadChildOptions(row.product_id)}>خيارات المنتج الداخلي</button>{childOptions[row.product_id]?.length === 0 && <p className="text-xs">لا توجد خيارات لهذا المنتج.</p>}{(childOptions[row.product_id] || []).filter((option) => bundleOptionVisible(option, row.selected_options || [])).map((option) => <fieldset key={option.id}><legend>{option.name}</legend>{(option.values || []).map((value) => <label key={value.id} className="ml-3 inline-flex gap-2"><input type={["checkbox", "multiple", "multi_select"].includes(option.type) ? "checkbox" : "radio"} name={`child-${index}-${option.id}`} checked={(row.selected_options || []).some((choice) => choice.option_id === String(option.id) && choice.value_id === String(value.id))} onChange={(e) => {
                        const multiple = ["checkbox", "multiple", "multi_select"].includes(option.type);
                        let next = [...(row.selected_options || []).filter((entry) => entry.option_id !== String(option.id) || (multiple && entry.value_id !== String(value.id))), ...(e.target.checked ? [{ option_id: String(option.id), value_id: String(value.id) }] : [])];
                        for (let i = 0; i < childOptions[row.product_id].length; i += 1) next = next.filter((entry) => bundleOptionVisible(childOptions[row.product_id].find((item) => String(item.id) === entry.option_id) || {}, next));
                        update(index, { selected_options: next });
                    }} />{value.name}</label>)}</fieldset>)}</div>}
                    <div className="flex gap-4 text-sm"><button type="button" disabled={index === 0} onClick={() => move(index, -1)}>للأعلى</button><button type="button" disabled={index === definition.items.length - 1} onClick={() => move(index, 1)}>للأسفل</button><button type="button" className="text-red-700" onClick={() => edit({ ...definition, items: definition.items.filter((_, i) => i !== index) })}>حذف المكوّن</button></div>
                </fieldset>)}
                {definition.items.some((item) => item.kind === "current_product") && <section className="space-y-2 rounded-lg border p-3"><h3 className="font-bold">نطاق مكونات Components V2 الأصلية</h3><p className="text-xs">حدد صراحةً هل ترتبط المكونات الأصلية بكل وحدة فعلية أو بالبكج كاملًا لتجنب تكرار التغليف.</p>{scopeResources.map((resource) => <label className="block" key={resource.resource_id}>{resource.name || resource.resource_id}<select className={control} value={definition.component_scopes?.[resource.resource_id] || ""} onChange={(e) => edit({ ...definition, component_scopes: { ...(definition.component_scopes || {}), [resource.resource_id]: e.target.value } })}><option value="">حدد النطاق</option><option value="per_bundle">لكل بكج</option><option value="per_unit">لكل وحدة فعلية</option></select></label>)}</section>}
            </>}
            <button type="button" className="rounded-xl bg-violet-700 px-4 py-2 text-white disabled:opacity-50" disabled={busy || !dirty || invalid || missingScope} onClick={save}>{busy ? "جارٍ الحفظ…" : "حفظ التركيب وحساب التكلفة"}</button>
        </fieldset>}
        {notice && <p role="status" className="text-sm text-emerald-800">{notice}</p>}
        {dirty && <p className="text-xs text-amber-800">احفظ التغييرات للتحقق من جميع المستويات وحساب التكلفة من الكتالوج.</p>}
        {definition?.product_type === "composite" && <fieldset disabled={busy} className="space-y-3 rounded-xl border p-3"><legend>معاينة اختيارات العميل</legend><label>كمية سلة للمعاينة<input aria-label="كمية سلة للمعاينة" className={control} type="number" min="1" max="1000" step="1" value={saleQuantity} onChange={(e) => setSaleQuantity(e.target.value)} /></label>{rootVariants.length > 0 && <label>متغير البكج<select className={control} value={rootVariant} onChange={(e) => { setRootVariant(e.target.value); setExpansion(null); }}><option value="">اختر المتغير</option>{rootVariants.map((item) => <option key={item.id} value={item.id}>{item.display_name || item.name || item.sku || item.id}</option>)}</select></label>}<p className="text-xs">المكونات العامة تُضاف دائمًا، والمشروطة عند اختيار قيمتها. النصوص لا تنشئ مخزونًا؛ المقاسات المربوطة بالقطع تُعرض كتخصيص لدى المورد.</p>{customerOptions.filter((option) => bundleOptionVisible(option, choices)).map((option) => <fieldset key={option.id}><legend>{option.name}{option.required ? " *" : ""}</legend>{option.values?.length ? option.values.map((value) => <label key={value.id} className="ml-4 inline-flex gap-2"><input type={["checkbox", "multiple", "multi_select"].includes(option.type) ? "checkbox" : "radio"} name={`bundle-option-${productId}-${option.id}`} checked={choices.some((entry) => entry.option_id === String(option.id) && entry.value_id === String(value.id))} onChange={(e) => choose(option, value.id, e.target.checked)} />{value.name}</label>) : <input aria-label={option.name} className={control} placeholder="تخصيص تجريبي للمعاينة" value={textAnswers[option.id] || ""} onChange={(e) => { setTextAnswers((current) => ({ ...current, [option.id]: e.target.value })); setExpansion(null); }} />}</fieldset>)}<button type="button" disabled={dirty || busy} className="rounded-lg border p-2 disabled:opacity-50" onClick={preview}>معاينة المكونات والتكلفة للاختيارات</button></fieldset>}
        {expansion?.needs_selection ? <p role="status">اختر قيم الخيارات والمقاسات المطلوبة لمعاينة التركيب النهائي.</p> : expansion && <div className="space-y-2 rounded-xl bg-slate-50 p-3">
            <h3 className="font-bold">تكلفة المكونات النهائية — الإصدار {definition.revision}</h3>
            {Number.isInteger(Number(saleQuantity)) && Number(saleQuantity) > 0 && <>
                {expansion.units_per_selection != null && <p className="font-bold">كمية سلة {saleQuantity} × وحدات الاختيار {expansion.units_per_selection} = {Number(saleQuantity) * Number(expansion.units_per_selection)} وحدة فعلية للتجهيز</p>}
                {expansion.current_product_units_per_selection != null && <p>وحدات نفس المنتج الحالي: {Number(saleQuantity) * Number(expansion.current_product_units_per_selection)}</p>}
                {(expansion.leaves || []).map((leaf, index) => <div key={index} className="rounded-lg border bg-white p-2 text-sm"><p>{leaf.name || leaf.product_id || leaf.resource_id}{leaf.slot_id ? ` · القطعة ${leaf.slot_id}` : ""}{leaf.variant_id ? ` · المتغير ${leaf.variant_id}` : ""} × {Number(leaf.quantity) * Number(saleQuantity)}: {bundleMoney(bundleLeafCost(leaf) == null ? null : Number(bundleLeafCost(leaf)) * Number(saleQuantity))}</p><p>المتاح: {leaf.available_quantity ?? "غير متاح"} · المورد: {leaf.supplier_name || suppliers.find((supplier) => supplier.id === leaf.supplier_id)?.company_name || leaf.supplier_id || "من المخزون"}</p>{Object.keys(leaf.customization || {}).length > 0 && <p>تخصيص القطعة: {Object.entries(leaf.customization).map(([key, value]) => `${customerOptions.find((option) => String(option.id) === key)?.name || key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join("، ")}</p>}</div>)}
                <p>إجمالي التكلفة التقديرية: {bundleMoney(expansion.estimated_cost == null ? null : Number(expansion.estimated_cost) * Number(saleQuantity))}</p>
                <p>الربح التقديري قبل المصاريف الأخرى: {bundleMoney(salePrice !== null && salePrice !== undefined && Number.isFinite(Number(salePrice)) && expansion.estimated_cost != null ? (Number(salePrice) - Number(expansion.estimated_cost)) * Number(saleQuantity) : null)}</p>
            </>}
        </div>}
    </section>;
}
