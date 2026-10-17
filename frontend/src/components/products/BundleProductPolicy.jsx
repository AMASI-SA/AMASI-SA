import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";
import { getProductV2 } from "../../services/mezanProductsV2";
import { bundleError, getProductBundleComponents } from "../../services/productBundles";

const control = "w-full rounded-lg border bg-white p-2 text-sm";
let nextPolicyRule = 0;
const textOption = (option) => ["text", "textarea", "long_text"].includes(option.type || "text") && !option.values?.length;
const newRuleId = () => globalThis.crypto?.randomUUID?.() || `policy-rule-${Date.now()}-${++nextPolicyRule}`;
const componentFields = (value = {}) => ({ cost_treatment: value.cost_treatment || "unknown", assembly_origin: value.assembly_origin || "unknown", supplier_omission_supported: value.supplier_omission_supported === true, evidence_ref: value.evidence_ref || "" });
const ruleFields = (value = {}) => ({ rule_id: value.rule_id || newRuleId(), variant_id: value.variant_id || null, conditions: Array.isArray(value.conditions) ? value.conditions.map(({ option_id, value_id }) => ({ option_id, value_id })) : [], customization: value.customization && typeof value.customization === "object" && !Array.isArray(value.customization) ? value.customization : {}, supplier_id: value.supplier_id || null, allow_split: value.allow_split === true });
const sourcingFields = (value = {}) => ({ stock_first: true, allow_split: value?.allow_split === true, supplier_id: value?.supplier_id || null, rules: Array.isArray(value?.rules) ? value.rules.filter((rule) => rule && typeof rule === "object").map(ruleFields) : [] });
const policyFields = (value = {}) => ({ revision: value.revision, composition_components: Object.fromEntries(Object.entries(value.composition_components || {}).map(([key, policy]) => [key, componentFields(policy || {})])), sourcing: sourcingFields(value.sourcing) });


export default function BundleProductPolicy({ productId, suppliers = [], onSaved }) {
    const [data, setData] = useState(null);
    const [components, setComponents] = useState([]);
    const [product, setProduct] = useState({});
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const request = useRef(0);
    const attemptedProduct = useRef(null);
    const activeProduct = useRef(productId);
    activeProduct.current = productId;
    const [loadedProduct, setLoadedProduct] = useState(null);
    useEffect(() => { attemptedProduct.current = null; request.current += 1; setData(null); setComponents([]); setProduct({}); setLoadedProduct(null); setError(""); setNotice(""); setBusy(false); return () => { request.current += 1; }; }, [productId]);
    async function load() {
        attemptedProduct.current = productId;
        const token = ++request.current;
        const id = productId;
        setBusy(true); setError("");
        try {
            const [response, bindings, details] = await Promise.all([api.get(`/products-v2/${encodeURIComponent(productId)}/bundle-policy`), getProductBundleComponents(productId), getProductV2(productId)]);
            if (token !== request.current || activeProduct.current !== id) return;
            setLoadedProduct(id); setData(policyFields(response.data));
            setComponents(bindings.components || []); setProduct({ ...(details.product || details), options: details.options || details.product?.options || [], variants: details.variants || details.product?.variants || [] });
        } catch (err) { if (token === request.current && activeProduct.current === id) setError(bundleError(err)); } finally { if (token === request.current && activeProduct.current === id) setBusy(false); }
    }
    function changeComponent(key, patch) {
        setData((current) => ({ ...current, composition_components: { ...current.composition_components, [key]: { cost_treatment: "unknown", assembly_origin: "unknown", supplier_omission_supported: false, evidence_ref: "", ...current.composition_components[key], ...patch } } }));
    }
    function changeSource(patch) { setData((current) => ({ ...current, sourcing: { ...current.sourcing, ...patch } })); }
    function changeRule(index, patch) { changeSource({ rules: data.sourcing.rules.map((rule, i) => i === index ? { ...rule, ...patch } : rule) }); }
    const supplierSelect = (value, change, label) => <label>{label}<select className={control} value={value || ""} onChange={(event) => change(event.target.value || null)}><option value="">غير محدد — يحتاج تحديد مصدر عند النقص</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.company_name || supplier.name || supplier.id}</option>)}</select></label>;
    async function save() {
        if (loadedProduct !== productId || !data || invalidPolicy) return;
        const token = ++request.current;
        const id = productId;
        setBusy(true); setError(""); setNotice("");
        try {
            const result = await api.put(`/products-v2/${encodeURIComponent(productId)}/bundle-policy`, { expected_revision: data.revision, composition_components: policyFields(data).composition_components, sourcing: sourcingFields(data.sourcing) });
            if (token !== request.current || activeProduct.current !== id) return;
            setData(policyFields(result.data)); setNotice("حُفظت سياسة المنتج. الطلبات السابقة تحتفظ بسياساتها التاريخية."); onSaved?.();
        } catch (err) { if (token === request.current && activeProduct.current === id) setError(bundleError(err)); } finally { if (token === request.current && activeProduct.current === id) setBusy(false); }
    }
    const invalidPolicy = data && (Object.keys(data.composition_components).some((key) => !components.some((component) => `${component.binding_kind}:${component.binding_id}` === key)) || data.sourcing.rules.some((rule) => (!rule.variant_id && !rule.conditions.length && !Object.keys(rule.customization).length) || (rule.variant_id && !(product.variants || []).some((variant) => String(variant.id) === String(rule.variant_id))) || rule.conditions.some((condition) => !(product.options || []).some((option) => String(option.id) === condition.option_id && (option.values || []).some((value) => String(value.id) === condition.value_id))) || Object.keys(rule.customization).some((id) => !(product.options || []).some((option) => String(option.id) === id && textOption(option)))));
    return <details className="rounded-xl border p-3" onToggle={(event) => { if (event.target === event.currentTarget && event.currentTarget.open && attemptedProduct.current !== productId) load(); }}>
        <summary className="cursor-pointer font-bold">سياسة المنتج الأصلية للتكلفة والتوريد</summary>
        <p className="my-2 text-sm text-amber-800">هذا إعداد مستقل للمنتج الأصلي، ويُستخدم في البكجات الجديدة. إضافة المنتج أو استبعاده من بكج لا تحفظ هذه الإعدادات. أدخل أساسًا موثوقًا قبل اعتماد التكلفة؛ لا تتغير مكونات المنتج الأصلية هنا.</p>
        {error && <p role="alert">{error}<button type="button" disabled={busy} onClick={load}>إعادة تحميل السياسة</button></p>}{notice && <p role="status">{notice}</p>}
        {busy && !data && <p role="status">جارٍ تحميل سياسة المنتج…</p>}
        {data && loadedProduct === productId && <fieldset disabled={busy} className="space-y-3">
            {components.map((component) => { const key = `${component.binding_kind}:${component.binding_id}`; const policy = data.composition_components[key] || {}; return <fieldset key={key} className="rounded-lg border p-2"><legend>{component.name}</legend>
                <label>أساس تكلفة {component.name}<select className={control} value={policy.cost_treatment || "unknown"} onChange={(event) => changeComponent(key, { cost_treatment: event.target.value })}><option value="unknown">غير محسوم</option><option value="independent">تكلفة مستقلة عن سعر المنتج</option><option value="included_in_base">مشمولة في سعر المنتج</option></select></label>
                <label>من يجهّز {component.name}<select className={control} value={policy.assembly_origin || "unknown"} onChange={(event) => changeComponent(key, { assembly_origin: event.target.value })}><option value="unknown">غير محسوم</option><option value="inhouse">فريق التجهيز</option><option value="supplier">المورد أثناء التجهيز</option><option value="purchased_complete">ضمن منتج جاهز مشتَرى</option></select></label>
                {policy.assembly_origin === "supplier" && <label className="block"><input type="checkbox" checked={policy.supplier_omission_supported || false} onChange={(event) => changeComponent(key, { supplier_omission_supported: event.target.checked })} />توجد موافقة موثقة من المورد على الاستبعاد</label>}
                <label>مرجع أساس التكلفة والتجهيز<input className={control} value={policy.evidence_ref || ""} onChange={(event) => changeComponent(key, { evidence_ref: event.target.value })} placeholder="مرجع فاتورة أو اتفاق المورد أو توزيع التكلفة المعتمد" /></label>
            </fieldset>; })}
            {invalidPolicy && <p role="alert">توجد قاعدة غير مكتملة أو مراجع لم تعد متاحة في المنتج. أكمل القاعدة أو احذفها، وأعد تحميل السياسة إذا تغيرت المكونات.</p>}
            <p className="text-sm">يستخدم المخزون المطابق أولًا. لا يُختار مورد تلقائيًا عند نقص البيانات.</p>
            {supplierSelect(data.sourcing.supplier_id, (supplier_id) => changeSource({ supplier_id }), "المورد المعتمد عند نقص المخزون")}
            <label className="block"><input type="checkbox" checked={data.sourcing.allow_split} onChange={(event) => changeSource({ allow_split: event.target.checked })} />السماح بتجهيز جزء من المخزون وبقية الكمية من المورد</label>
            {data.sourcing.rules.map((rule, index) => <fieldset key={rule.rule_id} className="space-y-2 rounded-lg border p-2"><legend>سياسة حسب اختيارات المنتج {index + 1}</legend>
                <label>النسخة / المقاس<select className={control} value={rule.variant_id || ""} onChange={(event) => changeRule(index, { variant_id: event.target.value || null })}><option value="">أي نسخة</option>{(product.variants || []).map((variant) => <option key={variant.id} value={variant.id}>{variant.display_name || variant.name || variant.sku || variant.id}</option>)}</select></label>
                {(product.options || []).map((option) => option.values?.length ? <label key={option.id}>{option.name}<select className={control} value={rule.conditions.find((condition) => condition.option_id === String(option.id))?.value_id || ""} onChange={(event) => changeRule(index, { conditions: [...rule.conditions.filter((condition) => condition.option_id !== String(option.id)), ...(event.target.value ? [{ option_id: String(option.id), value_id: event.target.value }] : [])] })}><option value="">أي اختيار</option>{option.values.map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}</select></label> : textOption(option) ? <label key={option.id}>{option.name} — مطابقة نص كامل<input className={control} value={rule.customization[String(option.id)] || ""} onChange={(event) => { const customization = { ...rule.customization }; if (event.target.value) customization[String(option.id)] = event.target.value; else delete customization[String(option.id)]; changeRule(index, { customization }); }} /></label> : null)}
                {supplierSelect(rule.supplier_id, (supplier_id) => changeRule(index, { supplier_id }), "مورد هذه القاعدة")}
                <label className="block"><input type="checkbox" checked={rule.allow_split} onChange={(event) => changeRule(index, { allow_split: event.target.checked })} />السماح بالتجزئة لهذه القاعدة</label>
                <button type="button" onClick={() => changeSource({ rules: data.sourcing.rules.filter((_, i) => i !== index) })}>حذف سياسة الاختيار</button>
            </fieldset>)}
            <button type="button" onClick={() => changeSource({ rules: [...data.sourcing.rules, { rule_id: newRuleId(), conditions: [], variant_id: null, customization: {}, supplier_id: null, allow_split: false }] })}>إضافة سياسة حسب الخيار أو التخصيص</button>
            <button type="button" className="block rounded-lg bg-slate-800 p-2 text-white" disabled={invalidPolicy} onClick={save}>حفظ سياسة المنتج الأصلية</button>
        </fieldset>}
    </details>;
}
