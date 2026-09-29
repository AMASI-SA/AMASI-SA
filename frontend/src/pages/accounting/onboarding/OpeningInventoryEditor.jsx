import React from "react";
import { inventoryTotal, scaledDecimal } from "./onboardingDecimal";

const inputClass = "w-full rounded-lg border border-slate-300 bg-white p-2 text-sm";
const buttonClass = "rounded-lg border border-slate-300 px-3 py-2 text-sm";
const allocation = () => ({ location_id: "", quantity: "", scanned_location_barcode: "", preparation_state: "requires_preparation", specification_fields: [] });
export const emptyOpeningInventoryRow = () => ({ item_type: "PRODUCT", product_id: "", variant_id: "", resource_id: "", category_id: "", inventory_account_id: "", opening_quantity: "", opening_unit_cost: "", opening_total_cost: "", allocations: [allocation()] });
const same = (a, b) => String(a ?? "") === String(b ?? "");
const positive = value => { const scaled = scaledDecimal(value); return scaled !== null && scaled > 0n; };
const productId = product => product.id || product.mezan_product_id;

// Draft validation only: this never builds or sends an import/approval document.
export function validateOpeningInventoryRows(rows = [], context = {}) {
    const errors = [], identities = new Set(), locations = new Set();
    rows.forEach((row, index) => {
        const error = (field, message) => errors.push({ row: index, field, message });
        const product = (context.products || []).find(item => same(productId(item), row.product_id));
        const component = (context.components || []).find(item => same(item.id, row.resource_id));
        let identity;
        if (row.item_type === "PRODUCT") {
            if (!product) error("product_id", "اختر منتجًا من الكتالوج.");
            if ((product?.variants_required || product?.variants_count || product?.variants?.length) && !row.variant_id) error("variant_id", "اختر خيار المنتج المطلوب.");
            if (row.variant_id && !(product?.variants || []).some(v => same(v.id, row.variant_id))) error("variant_id", "خيار المنتج غير مطابق للكتالوج.");
            if (!Number.isInteger(Number(row.opening_quantity))) error("opening_quantity", "كمية المنتج يجب أن تكون عددًا صحيحًا.");
            identity = `product:${row.product_id}:${row.variant_id || ""}`;
        } else if (row.item_type === "STOCK_COMPONENT") {
            if (!component || component.kind === "service" || component.track_inventory === false || !(component.category_ids || []).some(id => same(id, row.category_id))) error("resource_id", "اختر مكوّنًا مخزنيًا تابعًا للتصنيف.");
            if (!component?.unit) error("resource_id", "وحدة المكوّن غير محددة في الكتالوج؛ يلزم تصحيح بيانات الكتالوج.");
            identity = `component:${row.resource_id}`;
        } else error("item_type", "نوع بند المخزون غير صالح.");
        if (identity && identities.has(identity)) error("item_type", "اجمع الهوية نفسها في سطر واحد ووزع كمياتها على الخانات.");
        identities.add(identity);
        for (const field of ["opening_quantity", "opening_unit_cost", "opening_total_cost"]) if (!positive(row[field])) error(field, "الكمية وتكلفة الوحدة والإجمالي مطلوبة وموجبة.");
        if ([row.opening_quantity, row.opening_unit_cost, row.opening_total_cost].every(positive) && scaledDecimal(inventoryTotal(row.opening_quantity, row.opening_unit_cost), 2) !== scaledDecimal(row.opening_total_cost, 2)) error("opening_total_cost", "الإجمالي لا يطابق الكمية × تكلفة الوحدة.");
        if (!row.allocations?.length) error("allocations", "أضف توزيعًا لخانة التخزين.");
        (row.allocations || []).forEach((a, ai) => {
            const field = `allocations.${ai}`;
            if (!(context.locations || []).some(l => same(l.id, a.location_id))) error(field, "اختر خانة من المستودع.");
            if (locations.has(a.location_id)) error(field, "لا تكرر خانة التخزين.");
            locations.add(a.location_id);
            if (!String(a.scanned_location_barcode || "").trim()) error(field, "باركود الخانة مطلوب.");
            if (!positive(a.quantity)) error(field, "كمية الخانة مطلوبة وموجبة.");
            if (row.item_type === "PRODUCT" && !Number.isInteger(Number(a.quantity))) error(field, "كمية المنتج في الخانة يجب أن تكون عددًا صحيحًا.");
            if (!["requires_preparation", "ready_complete"].includes(a.preparation_state)) error(field, "اختر حالة التجهيز.");
            const names = new Set();
            for (const spec of a.specification_fields || []) {
                const name = String(spec.name || "").trim();
                if (!name || !String(spec.value || "").trim() || names.has(name) || ["__proto__", "constructor", "prototype"].includes(name)) error(field, "أدخل اسم وقيمة كل مواصفة دون تكرار.");
                names.add(name);
            }
        });
        if (Math.abs((row.allocations || []).reduce((sum, a) => sum + Number(a.quantity || 0), 0) - Number(row.opening_quantity)) > 0.0000001) error("allocations", "مجموع كميات الخانات يجب أن يساوي كمية السطر.");
    });
    return errors;
}

function Field({ label, children }) { return <label className="block space-y-1"><span>{label}</span>{children}</label>; }

export default function OpeningInventoryEditor({ value = [], onChange, context = {} }) {
    const { products = [], components = [], categories = [], locations = [], inventory_accounts = [] } = context;
    const edit = (index, patch) => onChange(value.map((row, i) => i === index ? { ...row, ...patch } : row));
    const editAllocation = (index, ai, patch) => edit(index, { allocations: value[index].allocations.map((a, i) => i === ai ? { ...a, ...patch } : a) });
    const cost = (index, field, nextValue) => {
        const next = { ...value[index], [field]: nextValue };
        next.opening_total_cost = positive(next.opening_quantity) && positive(next.opening_unit_cost) ? inventoryTotal(next.opening_quantity, next.opening_unit_cost) : "";
        edit(index, next);
    };
    const errors = validateOpeningInventoryRows(value, context);
    return <section dir="rtl" className="space-y-4" aria-label="المخزون الافتتاحي">
        <p className="text-sm text-slate-600">بيانات المخزون الافتتاحي للمراجعة. حفظ المسودة لا يغيّر كميات المخزون أو القيود المالية.</p>
        {value.map((row, index) => {
            const product = products.find(p => same(productId(p), row.product_id));
            const component = components.find(c => same(c.id, row.resource_id));
            const numberField = (field, label, step) => <Field label={label}><input aria-label={`${label} ${index + 1}`} className={inputClass} type="number" min="0" step={step} value={row[field] ?? ""} onChange={e => cost(index, field, e.target.value)} /></Field>;
            return <fieldset key={index} className="space-y-3 rounded-xl border p-4"><legend className="px-2 font-bold">بند المخزون {index + 1}</legend>
                <Field label="نوع البند"><select aria-label={`نوع البند ${index + 1}`} className={inputClass} value={row.item_type} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), item_type: e.target.value })}><option value="PRODUCT">منتج</option><option value="STOCK_COMPONENT">مكوّن مخزني</option></select></Field>
                {row.item_type === "PRODUCT" ? <>
                    <Field label="المنتج"><select aria-label={`المنتج ${index + 1}`} className={inputClass} value={row.product_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), product_id: e.target.value })}><option value="">اختر المنتج</option>{products.map(p => <option key={productId(p)} value={productId(p)}>{p.name} · {p.sku}</option>)}</select></Field>
                    {(product?.variants_required || product?.variants_count || product?.variants?.length > 0) ? <Field label="خيار المنتج — مطلوب"><select aria-label={`خيار المنتج ${index + 1}`} className={inputClass} value={row.variant_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), product_id: row.product_id, variant_id: e.target.value })}><option value="">اختر الخيار</option>{(product.variants || []).map(v => <option key={v.id} value={v.id}>{v.name || v.sku || v.id}</option>)}</select></Field> : null}
                </> : <>
                    <Field label="التصنيف"><select aria-label={`التصنيف ${index + 1}`} className={inputClass} value={row.category_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", category_id: e.target.value })}><option value="">اختر التصنيف</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
                    <Field label="المكوّن"><select aria-label={`المكوّن ${index + 1}`} className={inputClass} value={row.resource_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", category_id: row.category_id, resource_id: e.target.value })}><option value="">اختر المكوّن</option>{components.filter(c => c.kind !== "service" && c.track_inventory !== false && (c.category_ids || []).some(id => same(id, row.category_id))).map(c => <option key={c.id} value={c.id}>{c.name} · {c.code}</option>)}</select></Field>
                    <p>الوحدة المسجلة في الكتالوج: <strong>{component?.unit || "غير محددة"}</strong></p>
                </>}
                {inventory_accounts.length > 0 && <Field label="حساب المخزون"><select aria-label={`حساب المخزون ${index + 1}`} className={inputClass} value={row.inventory_account_id || ""} onChange={e => edit(index, { inventory_account_id: e.target.value })}><option value="">اختر الحساب</option>{inventory_accounts.map(a => <option key={a.entity_id} value={a.entity_id}>{a.label}</option>)}</select></Field>}
                <div className="grid gap-3 sm:grid-cols-3">{numberField("opening_quantity", "الكمية", row.item_type === "PRODUCT" ? "1" : "any")}{numberField("opening_unit_cost", "تكلفة الوحدة", "0.000001")}<Field label="الإجمالي"><input aria-label={`الإجمالي ${index + 1}`} className={inputClass} readOnly value={row.opening_total_cost || ""} /></Field></div>
                {(row.allocations || []).map((a, ai) => <fieldset key={ai} className="space-y-2 rounded-lg border bg-slate-50 p-3"><legend>توزيع {ai + 1}</legend>
                    <Field label="المستودع / الخانة"><select aria-label={`الخانة ${index + 1}-${ai + 1}`} className={inputClass} value={a.location_id} onChange={e => editAllocation(index, ai, { location_id: e.target.value, scanned_location_barcode: "" })}><option value="">اختر الخانة</option>{locations.map(l => <option key={l.id} value={l.id}>{l.warehouse_name || l.warehouse_id} / {l.code || l.name || l.id}</option>)}</select></Field>
                    <Field label="كمية الخانة"><input aria-label={`كمية الخانة ${index + 1}-${ai + 1}`} className={inputClass} type="number" min="0" step={row.item_type === "PRODUCT" ? "1" : "any"} value={a.quantity} onChange={e => editAllocation(index, ai, { quantity: e.target.value })} /></Field>
                    <Field label="باركود الخانة"><input aria-label={`باركود الخانة ${index + 1}-${ai + 1}`} className={inputClass} value={a.scanned_location_barcode} onChange={e => editAllocation(index, ai, { scanned_location_barcode: e.target.value })} /></Field>
                    <Field label="حالة التجهيز"><select aria-label={`حالة التجهيز ${index + 1}-${ai + 1}`} className={inputClass} value={a.preparation_state} onChange={e => editAllocation(index, ai, { preparation_state: e.target.value })}><option value="requires_preparation">يحتاج تجهيز</option><option value="ready_complete">جاهز مكتمل</option></select></Field>
                    <p>مواصفات المنتج / العميل</p>
                    {(a.specification_fields || []).map((spec, si) => <div key={si} className="flex gap-2">{["name", "value"].map(field => <input key={field} aria-label={`${field === "name" ? "اسم" : "قيمة"} المواصفة ${index + 1}-${ai + 1}-${si + 1}`} className={inputClass} value={spec[field]} onChange={e => editAllocation(index, ai, { specification_fields: a.specification_fields.map((s, i) => i === si ? { ...s, [field]: e.target.value } : s) })} />)}<button type="button" className={buttonClass} onClick={() => editAllocation(index, ai, { specification_fields: a.specification_fields.filter((_, i) => i !== si) })}>حذف المواصفة</button></div>)}
                    <button type="button" className={buttonClass} onClick={() => editAllocation(index, ai, { specification_fields: [...(a.specification_fields || []), { name: "", value: "" }] })}>إضافة مواصفة</button>
                    {row.allocations.length > 1 && <button type="button" className={buttonClass} onClick={() => edit(index, { allocations: row.allocations.filter((_, i) => i !== ai) })}>حذف التوزيع</button>}
                </fieldset>)}
                {errors.filter(e => e.row === index).length > 0 && <ul className="text-sm text-rose-700" aria-label={`نواقص البند ${index + 1}`}>{errors.filter(e => e.row === index).map((e, i) => <li key={i}>{e.message}</li>)}</ul>}
                <div className="flex gap-2"><button type="button" className={buttonClass} onClick={() => edit(index, { allocations: [...(row.allocations || []), allocation()] })}>إضافة توزيع</button><button type="button" className={buttonClass} onClick={() => onChange(value.filter((_, i) => i !== index))}>حذف البند</button></div>
            </fieldset>;
        })}
        <button type="button" className={buttonClass} onClick={() => onChange([...value, emptyOpeningInventoryRow()])}>إضافة منتج أو مكوّن</button>
    </section>;
}
