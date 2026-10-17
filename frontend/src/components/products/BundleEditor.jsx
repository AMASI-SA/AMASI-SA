import { useEffect, useRef, useState } from "react";
import { getProductV2, listProductsV2 } from "../../services/mezanProductsV2";
import { getMezanComponentWorkspace } from "../../services/mezanComponentCatalog";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";
import { bundleError, bundleLeafCost, bundleMoney, bundleOptionVisible, getProductBundleComponents, getProductBundle, saveProductBundle, previewProductBundle } from "../../services/productBundles";
import BundleProductPolicy from "./BundleProductPolicy";
import BundleComponentPicker from "./BundleComponentPicker";
import CurrentProductSlots, { resizeCurrentSlots } from "./CurrentProductSlots";

const control = "w-full rounded-lg border p-2 text-sm";
const textOption = (option) => !option.values?.length && ["text", "textarea", "long_text"].includes(option.type || "text");
const unitLabel = (unit) => unit === "unit" ? "قطعة" : unit;
let ruleSequence = 0;
const newRuleId = () => globalThis.crypto?.randomUUID?.() || `bundle-rule-${Date.now()}-${++ruleSequence}`;
const idOf = (item) => String(item.mezan_product_id || item.id);
function frozenProductNames(expansion) {
    const names = {};
    Object.entries(expansion?.frozen_definitions || {}).forEach(([key, definition]) => {
        const product = definition.catalog_product || {};
        [key, product.id, product.mezan_product_id, product.salla_product_id].filter(Boolean).forEach((id) => { if (product.name) names[String(id)] = product.name; });
    });
    return names;
}

function LinkedProductMappings({ row, parentOptions, childOptions, variants, onChange }) {
    const fields = (options, text = false) => options.filter((option) => text ? textOption(option) : option.values?.length);
    const field = (label, options, value, change) => <label>{label}<select className={control} value={value || ""} onChange={(event) => change(event.target.value)}><option value="">اختر الحقل</option>{options.map((option) => <option key={option.id} value={String(option.id)}>{option.name}</option>)}</select></label>;
    const value = (label, options, id, selected, change) => field(label, (options.find((option) => String(option.id) === String(id))?.values || []), selected, change);
    const patch = (key, index, change) => onChange({ [key]: (row[key] || []).map((entry, i) => i === index ? { ...entry, ...change } : entry) });
    const remove = (key, index) => onChange({ [key]: row[key].filter((_, i) => i !== index) });
    return <details className="space-y-2 rounded-lg border p-3"><summary>ربط اختيارات البكج بالمنتج الداخلي</summary>
        <p className="text-xs">تُطبّق هذه الروابط وقت الطلب باستخدام معرّفات الخيارات؛ النصوص تخصيص فقط ولا تنشئ مخزونًا.</p>
        <button type="button" onClick={() => onChange({ variant_rules: [...(row.variant_rules || []), { conditions: [{ option_id: "", value_id: "" }], variant_id: "" }] })}>إضافة ربط نسخة المنتج</button>
        {(row.variant_rules || []).map((rule, index) => <fieldset className="space-y-2 rounded border p-2" key={index}><legend>قاعدة نسخة {index + 1}</legend>
            {rule.conditions.map((condition, cindex) => <div key={cindex} className="grid gap-2 sm:grid-cols-2">{field("حقل البكج للنسخة", fields(parentOptions), condition.option_id, (id) => patch("variant_rules", index, { conditions: rule.conditions.map((item, i) => i === cindex ? { option_id: id, value_id: "" } : item) }))}{value("قيمة البكج للنسخة", parentOptions, condition.option_id, condition.value_id, (id) => patch("variant_rules", index, { conditions: rule.conditions.map((item, i) => i === cindex ? { ...item, value_id: id } : item) }))}</div>)}
            <button type="button" onClick={() => patch("variant_rules", index, { conditions: [...rule.conditions, { option_id: "", value_id: "" }] })}>إضافة شرط للنسخة</button>
            {field("النسخة المطلوبة", variants.map((variant) => ({ ...variant, name: variant.display_name || variant.name || variant.sku || variant.id })), rule.variant_id, (id) => patch("variant_rules", index, { variant_id: id }))}
            <button type="button" onClick={() => remove("variant_rules", index)}>حذف ربط النسخة</button>
        </fieldset>)}
        <button type="button" onClick={() => onChange({ option_mappings: [...(row.option_mappings || []), { option_id: "", value_id: "", target_option_id: "", target_value_id: "" }] })}>إضافة ربط خيار داخلي</button>
        {(row.option_mappings || []).map((mapping, index) => <div className="grid gap-2 rounded border p-2 sm:grid-cols-2" key={index}>
            {field("حقل البكج المصدر", fields(parentOptions), mapping.option_id, (id) => patch("option_mappings", index, { option_id: id, value_id: "" }))}
            {value("قيمة البكج المصدر", parentOptions, mapping.option_id, mapping.value_id, (id) => patch("option_mappings", index, { value_id: id }))}
            {field("حقل المنتج الهدف", fields(childOptions), mapping.target_option_id, (id) => patch("option_mappings", index, { target_option_id: id, target_value_id: "" }))}
            {value("قيمة المنتج الهدف", childOptions, mapping.target_option_id, mapping.target_value_id, (id) => patch("option_mappings", index, { target_value_id: id }))}
            <button type="button" onClick={() => remove("option_mappings", index)}>حذف ربط الخيار</button>
        </div>)}
        <button type="button" onClick={() => onChange({ customization_mappings: [...(row.customization_mappings || []), { option_id: "", target_option_id: "" }] })}>إضافة ربط نص تخصيص</button>
        {(row.customization_mappings || []).map((mapping, index) => <div className="grid gap-2 rounded border p-2 sm:grid-cols-2" key={index}>
            {field("نص البكج المصدر", fields(parentOptions, true), mapping.option_id, (id) => patch("customization_mappings", index, { option_id: id }))}
            {field("نص المنتج الهدف", fields(childOptions, true), mapping.target_option_id, (id) => patch("customization_mappings", index, { target_option_id: id }))}
            <button type="button" onClick={() => remove("customization_mappings", index)}>حذف ربط النص</button>
        </div>)}
    </details>;
}

export default function BundleEditor({ productId, salePrice }) {
    const [definition, setDefinition] = useState(null);
    const [expansion, setExpansion] = useState(null);
    const [limits, setLimits] = useState({ max_items: 100, max_depth: 5, max_leaves: 500 });
    const [resources, setResources] = useState([]);
    const [suppliers, setSuppliers] = useState([]);
    const [products, setProducts] = useState([]);
    const [searchLoading, setSearchLoading] = useState(false);
    const [searchError, setSearchError] = useState("");
    const [productNames, setProductNames] = useState({});
    const [query, setQuery] = useState("");
    const [kind, setKind] = useState("product");
    const [catalog, setCatalog] = useState({});
    const [catalogState, setCatalogState] = useState({});
    const loadedProducts = useRef(new Set());
    const [rootVariants, setRootVariants] = useState([]);
    const [rootVariant, setRootVariant] = useState("");
    const [customerOptions, setCustomerOptions] = useState([]);
    const [choices, setChoices] = useState([]);
    const [inherited, setInherited] = useState({});
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
                setDefinition({ ...bundle.definition, items: bundle.definition.items.map((row) => ({ ...row, rule_id: row.rule_id || newRuleId(), repeat_policy: row.repeat_policy || "once" })) }); setExpansion(bundle.expansion); setLimits(bundle.limits);
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
        setSearchLoading(true); setSearchError("");
        const timer = setTimeout(() => {
            listProductsV2({ query, perPage: 50 }).then((result) => {
                if (live) {
                    const found = result.items || [];
                    setProducts(found.filter((item) => idOf(item) !== String(productId)));
                    setProductNames((current) => ({ ...current, ...Object.fromEntries(found.flatMap((item) => [item.id, item.mezan_product_id, item.salla_product_id].filter(Boolean).map((id) => [String(id), item.name]))) }));
                }
            }).catch((err) => { if (live) { setProducts([]); setSearchError(bundleError(err)); } }).finally(() => { if (live) setSearchLoading(false); });
        }, 250);
        return () => { live = false; clearTimeout(timer); };
    }, [query, productId]);

    const productIds = (definition?.items || []).filter((row) => row.kind === "product").map((row) => String(row.product_id)).sort().join("|");
    useEffect(() => {
        let live = true;
        const ids = productIds.split("|").filter(Boolean);
        ids.forEach((id) => {
            if (loadedProducts.current.has(id)) return;
            loadedProducts.current.add(id);
            setCatalogState((current) => ({ ...current, [id]: "loading" }));
            getProductV2(id).then((result) => {
                if (live) setCatalogState((current) => ({ ...current, [id]: "ready" }));
                if (live) setCatalog((current) => ({ ...current, [id]: { ...current[id], ...(result.product || result), options: result.options || result.product?.options || [], variants: result.variants || result.product?.variants || [] } }));
            }).catch((err) => { loadedProducts.current.delete(id); if (live) { setCatalogState((current) => ({ ...current, [id]: "error" })); setError(bundleError(err)); } });
        });
        return () => { live = false; ids.forEach((id) => loadedProducts.current.delete(id)); };
    }, [productIds]);

    function edit(next) { setDefinition(next); setDirty(true); setExpansion(null); setNotice(""); }
    function update(index, patch) { edit({ ...definition, items: definition.items.map((item, i) => i === index ? { ...item, ...patch } : item) }); }
    function move(index, offset) {
        const items = [...definition.items];
        [items[index], items[index + offset]] = [items[index + offset], items[index]];
        edit({ ...definition, items });
    }
    const options = kind === "product" ? products : resources.filter((item) => `${item.name} ${item.code || ""}`.toLowerCase().includes(query.toLowerCase()));
    function add(selectedId) {
        const item = kind === "current_product" ? null : options.find((row) => idOf(row) === selectedId);
        if (kind !== "current_product" && !item) return;
        if (item && kind === "product") setCatalog((current) => ({ ...current, [selectedId]: { ...current[selectedId], ...item } }));
        setDefinition((current) => {
            if (current.items.length >= limits.max_items) return current;
            if (item && current.items.some((row) => row.kind === kind && String(row.product_id || row.resource_id) === selectedId && !row.condition)) return current;
            const row = { kind, rule_id: newRuleId(), repeat_policy: "sum", ...(item ? kind === "product" ? { product_id: selectedId } : { resource_id: selectedId } : {}), quantity: "1", unit: item?.unit || "unit", sourcing_mode: kind !== "resource" ? "auto" : "stock", supplier_id: null, location_id: null, required: true };
            return { ...current, items: [...current.items, row] };
        });
        setQuery(""); setDirty(true); setExpansion(null); setNotice("");
    }
    async function save() {
        setBusy(true); setError("");
        try {
            const result = await saveProductBundle(productId, {
                schema_version: 2, expected_revision: definition.revision, product_type: definition.product_type,
                items: definition.product_type === "normal" ? [] : definition.items.map(({ name, ...item }) => ({ ...item, rule_id: item.rule_id || newRuleId(), unit: catalog[item.product_id]?.unit || resources.find((resource) => idOf(resource) === String(item.resource_id))?.unit || item.unit })),
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
            const visibleText = customerOptions.filter((option) => textOption(option) && bundleOptionVisible(option, choices));
            const filled = visibleText.filter((option) => textAnswers[option.id]?.trim()).map((option) => String(option.id));
            const customizationIds = new Set(definition.items.flatMap((item) => [...(item.slots || []).flatMap((slot) => slot.customization_option_ids || []), ...(item.customization_mappings || []).map((mapping) => mapping.option_id)]));
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
    function clearPreview(optionId) {
        if (optionId == null) {
            setChoices([]); setTextAnswers({}); setRootVariant("");
        } else {
            let next = choices.filter((entry) => entry.option_id !== String(optionId));
            for (let i = 0; i < customerOptions.length; i += 1) next = next.filter((entry) => bundleOptionVisible(customerOptions.find((option) => String(option.id) === entry.option_id) || {}, next));
            setChoices(next);
            setTextAnswers((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== String(optionId) && bundleOptionVisible(customerOptions.find((option) => String(option.id) === id) || {}, next))));
        }
        setExpansion(null);
    }
    async function loadChildOptions(id) {
        try {
            const result = await getProductV2(id);
            setChildOptions((current) => ({ ...current, [id]: result.options || result.product?.options || [] }));
        } catch (err) { setError(bundleError(err)); }
    }
    async function loadInherited(id) {
        setInherited((current) => ({ ...current, [id]: { loading: true } }));
        try {
            const result = await getProductBundleComponents(id);
            setInherited((current) => ({ ...current, [id]: { components: result.components || [] } }));
        } catch (err) { setInherited((current) => ({ ...current, [id]: { error: bundleError(err) } })); }
    }
    function toggleInherited(index, component, use) {
        const row = definition.items[index];
        const overrides = (row.component_overrides || []).filter((entry) => !(entry.binding_kind === component.binding_kind && entry.binding_id === component.binding_id));
        update(index, { component_overrides: [...overrides, { binding_kind: component.binding_kind, binding_id: component.binding_id, use }] });
    }
    function rulePathLabel(path) {
        return (path || []).map((step) => {
            const match = String(step).match(/^(.*?)@[^:]+:(.*)$/);
            if (!match) return String(step).startsWith("slot:") ? "قطعة داخل المنتج" : "مكوّن داخلي";
            const document = expansion?.frozen_definitions?.[match[1]] || definition;
            const index = (document.items || []).findIndex((item) => item.rule_id === match[2]);
            const item = document.items?.[index];
            const name = item?.name || catalog[item?.product_id]?.name || productNames[item?.product_id];
            return index >= 0 ? `القاعدة ${index + 1}${name ? `: ${name}` : ""}` : "تركيب المنتج الداخلي";
        }).join(" ← ");
    }
    const supplierName = (id) => suppliers.find((supplier) => String(supplier.id) === String(id))?.company_name || suppliers.find((supplier) => String(supplier.id) === String(id))?.name || "اسم المورد غير متاح";
    const unresolvedCatalog = definition?.items.some((row) => row.kind === "product" && catalogState[row.product_id] !== "ready");
    const incompleteMappings = definition?.items.some((row) => (row.variant_rules || []).some((rule) => !rule.variant_id || !rule.conditions?.length || rule.conditions.some((condition) => !condition.option_id || !condition.value_id)) || (row.option_mappings || []).some((mapping) => !mapping.option_id || !mapping.value_id || !mapping.target_option_id || !mapping.target_value_id) || (row.customization_mappings || []).some((mapping) => !mapping.option_id || !mapping.target_option_id));
    const missingScope = definition?.items.some((item) => item.kind === "current_product") && scopeResources.some((resource) => !definition.component_scopes?.[resource.resource_id]);
    const invalid = definition?.product_type === "composite" && (!definition.items.length || definition.items.some((row) => !Number.isFinite(Number(row.quantity)) || Number(row.quantity) <= 0 || !(row.unit || "").trim() || (row.condition && (!row.condition.option_id || !row.condition.value_id)) || (row.sourcing_mode === "supplier" && !row.supplier_id) || (row.kind === "current_product" && (!Number.isInteger(Number(row.quantity)) || Number(row.quantity) > 100 || (row.slots || []).some((slot) => (slot.variant_rules || []).some((rule) => !rule.variant_id || rule.conditions.some((condition) => !condition.option_id || !condition.value_id)))))));
    return <section dir="rtl" className="space-y-4 rounded-2xl border border-violet-200 bg-white p-4" data-testid="bundle-editor">
        <h2 className="font-black">تركيب المنتج والبكجات الذكية</h2>
        <p className="text-xs text-slate-600">حتى {limits.max_items} مكوّنًا مباشرًا، و{limits.max_depth} مستويات تداخل، و{limits.max_leaves} مكوّنًا نهائيًا. يُفكّ البكج الداخلي تلقائيًا؛ يمنع الخادم الربط الدائري.</p>
        {error && <div role="alert" className="text-sm text-red-700">{error} <button type="button" onClick={() => setReload((value) => value + 1)}>إعادة تحميل التركيب</button></div>}
        {!definition ? <p>جارٍ تحميل التركيب…</p> : <fieldset disabled={busy} className="space-y-4">
            <label className="block">نوع التركيب<select className={control} value={definition.product_type} onChange={(e) => edit({ ...definition, product_type: e.target.value })}><option value="normal">منتج عادي</option><option value="composite">منتج مركّب / بكج</option></select></label>
            {definition.product_type === "composite" && <>
                <div className="grid gap-2 sm:grid-cols-2">
                    <label>مصدر المكوّن<select className={control} value={kind} onChange={(e) => { setKind(e.target.value); setQuery(""); }}><option value="product">منتجات ميزان 2</option><option value="resource">مكونات ميزان 2</option><option value="current_product">نفس المنتج الحالي</option></select></label>
                    {kind !== "current_product" && <BundleComponentPicker query={query} onQuery={setQuery} items={options} selected="" onSelect={add} loading={kind === "product" && searchLoading} error={kind === "product" ? searchError : ""} />}
                </div>
                <p className="text-sm">العناصر المضافة ({definition.items.length}/{limits.max_items}) — اختر نتيجة لإضافتها مباشرةً، وعدّل الكمية لتكرارها.</p>
                {kind === "current_product" && <button type="button" className="rounded-lg border px-4 py-2" disabled={definition.items.length >= limits.max_items} onClick={() => add()}>إضافة مكوّن ({definition.items.length}/{limits.max_items})</button>}
                {definition.items.map((row, index) => <fieldset key={row.rule_id || index} className="space-y-2 rounded-xl border p-3">
                    <legend className="px-2 font-bold">{index + 1}. {row.kind === "current_product" ? "نفس المنتج الحالي" : catalog[row.product_id]?.name || row.name || productNames[row.product_id] || resources.find((item) => idOf(item) === String(row.resource_id))?.name || row.product_id || row.resource_id}{row.variant_id ? ` · ${row.variant_id}` : ""} · {row.condition ? "مكوّن مشروط" : "مكوّن عام"}</legend>
                    {row.kind !== "current_product" && <div className="flex items-center gap-3"><span className="relative flex h-12 w-12 items-center justify-center overflow-hidden rounded-lg border text-xs">بدون صورة{(catalog[row.product_id]?.main_image || resources.find((item) => idOf(item) === String(row.resource_id))?.main_image) && <img className="absolute inset-0 h-full w-full bg-white object-cover" alt="" src={catalog[row.product_id]?.main_image || resources.find((item) => idOf(item) === String(row.resource_id))?.main_image} onError={(event) => { event.currentTarget.style.display = "none"; }} />}</span><span>{row.kind === "product" ? "منتج مرتبط" : "مورد / مكوّن"} · {catalog[row.product_id]?.sku || row.product_id || row.resource_id}</span></div>}
                    <div className="grid gap-2 sm:grid-cols-3">
                        <label>الكمية<input aria-label={`كمية المكوّن ${index + 1}`} type="number" min={row.kind === "current_product" ? "1" : "0.000001"} max={row.kind === "current_product" ? "100" : undefined} step={row.kind === "current_product" ? "1" : "any"} className={control} value={row.quantity} onChange={(e) => update(index, { quantity: e.target.value, ...(row.slots?.length ? { slots: resizeCurrentSlots(row.slots, e.target.value) } : {}) })} /></label>
                        {row.kind !== "resource" || resources.find((item) => idOf(item) === String(row.resource_id))?.unit ? <div>الوحدة<p>{row.kind === "product" && catalogState[row.product_id] !== "ready" ? "بانتظار التحقق من كتالوج المنتج" : unitLabel(catalog[row.product_id]?.unit || resources.find((item) => idOf(item) === String(row.resource_id))?.unit || row.unit)}</p></div> : <label>الوحدة<input className={control} value={row.unit} onChange={(e) => update(index, { unit: e.target.value })} /></label>}
                        {row.kind === "product" && <label>نسخة المنتج (المقاس / اللون)<select className={control} value={row.variant_id || ""} onChange={(e) => update(index, { variant_id: e.target.value || null })}><option value="">المنتج الأساسي</option>{row.variant_id && !(catalog[row.product_id]?.variants || []).some((variant) => String(variant.id) === String(row.variant_id)) && <option value={row.variant_id}>{row.variant_id}</option>}{(catalog[row.product_id]?.variants || []).map((variant) => <option key={variant.id} value={variant.id}>{variant.display_name || variant.name || variant.sku || variant.id}</option>)}</select></label>}
                        {row.kind === "product" || row.sourcing_mode === "auto" ? <div>التوريد<p>{row.sourcing_mode === "auto" ? "تلقائي — حسب إعدادات المنتج ومكوناته" : `إعداد سابق: ${row.sourcing_mode === "supplier" ? "من المورد" : "من المخزون"}`}</p></div> : <label>التوريد<select className={control} value={row.sourcing_mode} onChange={(e) => update(index, { sourcing_mode: e.target.value })}><option value="stock">من المخزون</option><option value="supplier">من المورد</option></select></label>}
                        {row.sourcing_mode === "supplier" && (row.kind === "product" ? <div>المورد<p>{row.supplier_id ? supplierName(row.supplier_id) : "غير محدد"}</p></div> : <label>المورد<select className={control} value={row.supplier_id || ""} onChange={(e) => update(index, { supplier_id: e.target.value || null })}><option value="">اختر المورد</option>{suppliers.map((item) => <option key={item.id} value={item.id}>{item.company_name || item.name}</option>)}</select></label>)}
                        {row.kind !== "product" && row.sourcing_mode !== "auto" && <label>معرّف موقع المخزون (اختياري)<input className={control} value={row.location_id || ""} onChange={(e) => update(index, { location_id: e.target.value || null })} /></label>}
                        <label className="flex items-center gap-2"><input type="checkbox" checked={row.required !== false} onChange={(e) => update(index, { required: e.target.checked })} />مكوّن مطلوب</label>
                        <label>إضافة المكوّن<select className={control} value={row.condition ? "conditional" : "general"} onChange={(e) => update(index, { condition: e.target.value === "conditional" ? { option_id: "", value_id: "" } : null })}><option value="general">عام — مع كل بكج</option><option value="conditional">مشروط بخيار العميل</option></select></label>
                        {row.condition && <><label>حقل خيار العميل<select className={control} value={row.condition.option_id} onChange={(e) => update(index, { condition: { option_id: e.target.value, value_id: "" } })}><option value="">اختر الحقل</option>{customerOptions.filter((option) => option.values?.length).map((option) => <option key={option.id} value={String(option.id)}>{option.name}</option>)}</select></label><label>قيمة خيار العميل<select className={control} value={row.condition.value_id} onChange={(e) => update(index, { condition: { ...row.condition, value_id: e.target.value } })}><option value="">اختر القيمة</option>{(customerOptions.find((option) => String(option.id) === row.condition.option_id)?.values || []).map((value) => <option key={value.id} value={String(value.id)}>{value.name}</option>)}</select></label></>}
                        {row.repeat_policy === "once" && <label>إعداد التكرار السابق<select className={control} value={row.repeat_policy || "once"} onChange={(e) => update(index, { repeat_policy: e.target.value })}><option value="once">مرة واحدة</option><option value="sum">جمع الكميات صراحةً</option></select></label>}
                        {row.kind === "resource" && <label>تطبيق كمية المكوّن<select className={control} value={row.component_scope || "per_bundle"} onChange={(e) => update(index, { component_scope: e.target.value })}><option value="per_bundle">لكل بكج — مثل التغليف</option><option value="per_unit">لكل وحدة من نفس المنتج الحالي</option></select></label>}
                    </div>
                    {["product", "current_product"].includes(row.kind) && <details className="rounded-lg border p-3" onToggle={(event) => { const id = row.product_id || productId; if (event.currentTarget.open && !inherited[id]) loadInherited(id); }}>
                        <summary className="cursor-pointer">مكونات المنتج الداخلية</summary>
                        <p className="text-xs">هذه الاختيارات تخص هذا البكج فقط. لا تغيّر إعدادات المنتج الأصلي. تُراجع سياسة تكلفة كل مكوّن قبل اعتماد التكلفة.</p>
                        {inherited[row.product_id || productId]?.loading && <p role="status">جارٍ تحميل المكونات…</p>}
                        {inherited[row.product_id || productId]?.error && <p role="alert">{inherited[row.product_id || productId].error}<button type="button" onClick={() => loadInherited(row.product_id || productId)}>إعادة المحاولة</button></p>}
                        {inherited[row.product_id || productId]?.components?.length === 0 && <p>لا توجد مكونات موروثة مرتبطة بهذا المنتج.</p>}
                        {(inherited[row.product_id || productId]?.components || []).map((component) => <div key={`${component.binding_kind}:${component.binding_id}`} className="mt-2 rounded-lg border p-2 text-sm">
                            <label className="flex gap-2"><input type="checkbox" aria-label={`استخدام ${component.name || component.resource_id} في القاعدة ${index + 1}`} checked={(row.component_overrides || []).find((entry) => entry.binding_kind === component.binding_kind && entry.binding_id === component.binding_id)?.use !== false} onChange={(event) => toggleInherited(index, component, event.target.checked)} />{component.name || component.resource_id}</label>
                            <p>{component.quantity} {component.unit} · تكلفة الوحدة: {bundleMoney(component.unit_cost)}</p>
                            <p>{component.condition ? `مشروط: ${(catalog[row.product_id]?.options || customerOptions).find((option) => String(option.id) === String(component.condition.option_id))?.name || "خيار المنتج الداخلي"} / ${(catalog[row.product_id]?.options || customerOptions).find((option) => String(option.id) === String(component.condition.option_id))?.values?.find((value) => String(value.id) === String(component.condition.value_id))?.name || "قيمة مرتبطة"}` : "مرتبط بالمنتج"}</p>
                            <p>سياسة التكلفة: {({ independent: "مستقلة", included_in_base: "مشمولة في تكلفة المنتج" })[component.policy?.cost_treatment] || "غير محددة — تحتاج مراجعة إعدادات المنتج الأصلي"}</p>
                        </div>)}
                    </details>}
                    {["product", "current_product"].includes(row.kind) && <BundleProductPolicy productId={row.product_id || productId} suppliers={suppliers} onSaved={() => { setExpansion(null); setInherited((current) => { const next = { ...current }; delete next[row.product_id || productId]; return next; }); }} />}
                    {row.kind === "product" && childOptions[row.product_id] && <LinkedProductMappings row={row} parentOptions={customerOptions} childOptions={childOptions[row.product_id]} variants={catalog[row.product_id]?.variants || []} onChange={(patch) => update(index, patch)} />}
                    {row.kind === "current_product" && <CurrentProductSlots row={row} variants={rootVariants} options={customerOptions} onChange={(patch) => update(index, patch)} />}
                    {row.kind === "product" && <div className="space-y-2"><button type="button" className="text-sm underline" onClick={() => loadChildOptions(row.product_id)}>خيارات المنتج الداخلي</button>{childOptions[row.product_id]?.length === 0 && <p className="text-xs">لا توجد خيارات لهذا المنتج.</p>}{(childOptions[row.product_id] || []).filter((option) => bundleOptionVisible(option, row.selected_options || [])).map((option) => <fieldset key={option.id}><legend>{option.name}</legend>{(option.values || []).map((value) => <label key={value.id} className="ml-3 inline-flex gap-2"><input type={["checkbox", "multiple", "multi_select"].includes(option.type) ? "checkbox" : "radio"} name={`child-${index}-${option.id}`} checked={(row.selected_options || []).some((choice) => choice.option_id === String(option.id) && choice.value_id === String(value.id))} onChange={(e) => {
                        const multiple = ["checkbox", "multiple", "multi_select"].includes(option.type);
                        let next = [...(row.selected_options || []).filter((entry) => entry.option_id !== String(option.id) || (multiple && entry.value_id !== String(value.id))), ...(e.target.checked ? [{ option_id: String(option.id), value_id: String(value.id) }] : [])];
                        for (let i = 0; i < childOptions[row.product_id].length; i += 1) next = next.filter((entry) => bundleOptionVisible(childOptions[row.product_id].find((item) => String(item.id) === entry.option_id) || {}, next));
                        update(index, { selected_options: next });
                    }} />{value.name}</label>)}</fieldset>)}</div>}
                    <div className="flex gap-4 text-sm"><button type="button" disabled={definition.items.length >= limits.max_items} onClick={() => edit({ ...definition, items: [...definition.items, { ...row, rule_id: newRuleId(), repeat_policy: "sum", condition: { option_id: "", value_id: "" } }] })}>{row.kind === "resource" ? "إضافة قاعدة أخرى لهذا المكوّن" : "إضافة قاعدة أخرى لهذا المنتج"}</button><button type="button" disabled={index === 0} onClick={() => move(index, -1)}>للأعلى</button><button type="button" disabled={index === definition.items.length - 1} onClick={() => move(index, 1)}>للأسفل</button><button type="button" className="text-red-700" onClick={() => edit({ ...definition, items: definition.items.filter((_, i) => i !== index) })}>حذف المكوّن</button></div>
                </fieldset>)}
                {definition.items.some((item) => item.kind === "current_product") && <section className="space-y-2 rounded-lg border p-3"><h3 className="font-bold">نطاق مكونات Components V2 الأصلية</h3><p className="text-xs">حدد صراحةً هل ترتبط المكونات الأصلية بكل وحدة فعلية أو بالبكج كاملًا لتجنب تكرار التغليف.</p>{scopeResources.map((resource) => <label className="block" key={resource.resource_id}>{resource.name || resource.resource_id}<select className={control} value={definition.component_scopes?.[resource.resource_id] || ""} onChange={(e) => edit({ ...definition, component_scopes: { ...(definition.component_scopes || {}), [resource.resource_id]: e.target.value } })}><option value="">حدد النطاق</option><option value="per_bundle">لكل بكج</option><option value="per_unit">لكل وحدة فعلية</option></select></label>)}</section>}
            </>}
            <button type="button" className="rounded-xl bg-violet-700 px-4 py-2 text-white disabled:opacity-50" disabled={busy || !dirty || invalid || missingScope || unresolvedCatalog || incompleteMappings} onClick={save}>{busy ? "جارٍ الحفظ…" : "حفظ التركيب وحساب التكلفة"}</button>
        </fieldset>}
        {notice && <p role="status" className="text-sm text-emerald-800">{notice}</p>}
        {dirty && <p className="text-xs text-amber-800">احفظ التغييرات للتحقق من جميع المستويات وحساب التكلفة من الكتالوج.</p>}
        {definition?.product_type === "composite" && <fieldset disabled={busy} className="space-y-3 rounded-xl border p-3"><legend>معاينة اختيارات العميل</legend><p className="text-sm text-slate-600">اختيارات تجريبية لحساب المكونات والتكلفة فقط؛ لا تغيّر إعدادات المنتج أو طلبات العملاء.</p><div className="flex flex-wrap items-end gap-4"><button type="button" className="shrink-0 text-sm underline" onClick={() => clearPreview()}>مسح جميع اختيارات المعاينة</button><label className="block min-w-48 flex-1">كمية سلة للمعاينة<input aria-label="كمية سلة للمعاينة" className={control} type="number" min="1" max="1000" step="1" value={saleQuantity} onChange={(e) => setSaleQuantity(e.target.value)} /></label></div>{rootVariants.length > 0 && <label>متغير البكج<select className={control} value={rootVariant} onChange={(e) => { setRootVariant(e.target.value); setExpansion(null); }}><option value="">اختر المتغير</option>{rootVariants.map((item) => <option key={item.id} value={item.id}>{item.display_name || item.name || item.sku || item.id}</option>)}</select></label>}<p className="text-xs">المكونات العامة تُضاف دائمًا، والمشروطة عند اختيار قيمتها. النصوص لا تنشئ مخزونًا؛ المقاسات المربوطة بالقطع تُعرض كتخصيص لدى المورد.</p>{customerOptions.filter((option) => (option.values?.length || textOption(option)) && bundleOptionVisible(option, choices)).map((option) => <fieldset key={option.id}><legend>{option.name}{option.required ? " *" : ""}</legend><button type="button" className="mb-2 block text-sm underline" aria-label={`مسح اختيار ${option.name}`} onClick={() => clearPreview(option.id)}>مسح الاختيار</button>{option.values?.length ? option.values.map((value) => <label key={value.id} className="ml-4 inline-flex gap-2"><input type={["checkbox", "multiple", "multi_select"].includes(option.type) ? "checkbox" : "radio"} name={`bundle-option-${productId}-${option.id}`} checked={choices.some((entry) => entry.option_id === String(option.id) && entry.value_id === String(value.id))} onChange={(e) => choose(option, value.id, e.target.checked)} />{value.name}</label>) : <input aria-label={option.name} className={control} placeholder="تخصيص تجريبي للمعاينة" value={textAnswers[option.id] || ""} onChange={(e) => { setTextAnswers((current) => ({ ...current, [option.id]: e.target.value })); setExpansion(null); }} />}</fieldset>)}<button type="button" disabled={dirty || busy} className="rounded-lg border p-2 disabled:opacity-50" onClick={preview}>معاينة المكونات والتكلفة للاختيارات</button></fieldset>}
        {expansion?.needs_selection ? <p role="status">اختر قيم الخيارات والمقاسات المطلوبة لمعاينة التركيب النهائي.</p> : expansion && <div className="space-y-2 rounded-xl bg-slate-50 p-3">
            {expansion.unresolved_components?.length > 0 && <p role="alert" className="text-amber-800">التكلفة غير مكتملة: توجد مكونات تحتاج تحديد سياسة تكلفتها في المنتج الأصلي.</p>}
            {(expansion.component_decisions || []).length > 0 && <section className="space-y-2"><h3 className="font-bold">قرارات المكونات الموروثة</h3>{expansion.component_decisions.map((decision, index) => <div key={index} className="rounded-lg border bg-white p-2 text-sm"><p>{decision.name || "مكوّن داخلي"} · {decision.use ? "مستخدم" : "مستثنى"} · الكمية لكل اختيار: {decision.quantity}</p><p>التكلفة: {({ independent: "مستقلة", included_in_base: "مشمولة في المنتج" })[decision.cost_treatment] || "غير محددة"} · التجهيز: {({ inhouse: "داخلي", supplier: "عند المورد", purchased_complete: "منتج جاهز" })[decision.assembly_origin] || "غير محدد"}</p>{decision.reason && <p className="text-amber-800">سبب المراجعة: {({ component_provenance_required: "تحديد مصدر التجهيز وسياسة التكلفة مطلوب", supplier_omission_unconfirmed: "استثناء المورد غير مؤكد بدليل", supplier_component_execution_required: "يلزم تحديد تنفيذ المكوّن لدى المورد", included_component_inventory_cost_basis_required: "يلزم تحديد أساس تكلفة المخزون للمكوّن المشمول" })[decision.reason] || "تحتاج سياسة المكوّن مراجعة"}</p>}<p className="break-all">مسار القاعدة: {rulePathLabel(decision.path)}</p></div>)}</section>}
            <h3 className="font-bold">تكلفة المكونات النهائية — الإصدار {definition.revision}</h3>
            {Number.isInteger(Number(saleQuantity)) && Number(saleQuantity) > 0 && <>
                {expansion.units_per_selection != null && <p className="font-bold">كمية سلة {saleQuantity} × وحدات الاختيار {expansion.units_per_selection} = {Number(saleQuantity) * Number(expansion.units_per_selection)} وحدة فعلية للتجهيز</p>}
                {expansion.current_product_units_per_selection != null && <p>وحدات نفس المنتج الحالي: {Number(saleQuantity) * Number(expansion.current_product_units_per_selection)}</p>}
                {(expansion.leaves || []).map((leaf, index) => <div key={index} className="rounded-lg border bg-white p-2 text-sm"><p>{leaf.name || "عنصر مرتبط"}{leaf.slot_id ? ` · القطعة ${leaf.slot_id}` : ""}{leaf.variant_id ? ` · النسخة ${catalog[leaf.product_id]?.variants?.find((variant) => String(variant.id) === String(leaf.variant_id))?.display_name || catalog[leaf.product_id]?.variants?.find((variant) => String(variant.id) === String(leaf.variant_id))?.name || "المحددة"}` : ""} × {Number(leaf.quantity) * Number(saleQuantity)}: {bundleMoney(bundleLeafCost(leaf) == null ? null : Number(bundleLeafCost(leaf)) * Number(saleQuantity))}</p><p>المتاح: {leaf.available_quantity ?? "غير متاح"} · المورد: {leaf.supplier_name || (leaf.supplier_id ? supplierName(leaf.supplier_id) : leaf.sourcing_mode === "auto" ? "لم يُحدد مورد" : "من المخزون")}</p>
                    <p>حساب الكمية: {leaf.quantity} لكل اختيار × {saleQuantity} كمية سلة = {Number(leaf.quantity) * Number(saleQuantity)}</p>
                    {(leaf.paths || []).map((path, pathIndex) => <p key={pathIndex} className="break-all">مسار القاعدة {pathIndex + 1}: {rulePathLabel(path)}</p>)}
                    {leaf.preview_only && <p className="text-xs">التوريد أدناه معاينة لاختيار واحد؛ لا يحجز مخزونًا ولا ينشئ طلب توريد.</p>}
                    {(leaf.sourcing_segments || []).map((segment, segmentIndex) => <p key={segment.leaf_id || segmentIndex}>التوريد: {segment.sourcing_mode === "stock" ? "من المخزون" : "من المورد"} · الكمية: {segment.quantity} · المحجوز تقديريًا: {segment.reserved_quantity ?? "غير متاح"}{segment.supplier_id ? ` · المورد: ${supplierName(segment.supplier_id)}` : ""}</p>)}
                    {(leaf.state === "needs_source" || Number(leaf.unresolved_quantity) > 0) && <p className="text-amber-800">يحتاج مصدر توريد: {leaf.unresolved_quantity ?? leaf.pending_quantity ?? "غير محدد"} — أكمل سياسة التوريد للمنتج.</p>}
{Object.keys(leaf.customization || {}).length > 0 && <p>تخصيص القطعة: {Object.entries(leaf.customization).map(([key, value]) => `${customerOptions.find((option) => String(option.id) === key)?.name || key}: ${typeof value === "object" ? JSON.stringify(value) : value}`).join("، ")}</p>}</div>)}
                {expansion.known_cost != null && <p>التكلفة المعروفة فقط: {bundleMoney(Number(expansion.known_cost) * Number(saleQuantity))}{expansion.cost_complete === false ? " — ليست إجماليًا نهائيًا" : ""}</p>}
                {(expansion.unresolved_components || []).length > 0 && <ul className="text-sm text-amber-800">{expansion.unresolved_components.map((component, index) => <li key={index}>تكلفة غير محسومة: {component.name || "مكوّن داخلي"} · {component.use ? "مستخدم" : "مستثنى"}</li>)}</ul>}
                <p>إجمالي التكلفة التقديرية: {bundleMoney(expansion.estimated_cost == null ? null : Number(expansion.estimated_cost) * Number(saleQuantity))}</p>
                <p>الربح التقديري قبل المصاريف الأخرى: {bundleMoney(salePrice !== null && salePrice !== undefined && Number.isFinite(Number(salePrice)) && expansion.estimated_cost != null ? (Number(salePrice) - Number(expansion.estimated_cost)) * Number(saleQuantity) : null)}</p>
            </>}
        </div>}
    </section>;
}
