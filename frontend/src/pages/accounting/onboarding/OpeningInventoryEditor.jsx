import React, { useState } from "react";
import { inventoryTotal, scaledDecimal } from "./onboardingDecimal";

const inputClass = "w-full rounded-lg border border-slate-300 bg-white p-2 text-sm";
const buttonClass = "rounded-lg border border-slate-300 px-3 py-2 text-sm";
const allocation = () => ({ location_id: "", quantity: "", scanned_location_barcode: "", preparation_state: "requires_preparation" });
export const emptyOpeningInventoryRow = () => ({ item_type: "PRODUCT", product_id: "", variant_id: "", selected_options: {}, resource_id: "", category_id: "", inventory_account_id: "", opening_quantity: "", opening_unit_cost: "", opening_total_cost: "", allocations: [] });
const same = (a, b) => String(a ?? "") === String(b ?? "");
const positive = value => { const scaled = scaledDecimal(value); return scaled !== null && scaled > 0n; };
const productId = product => product.id || product.mezan_product_id;
const text = value => typeof value === "object" ? String(value?.name || value?.label || value?.value || "") : String(value ?? "");
export const variantLabel = variant => (variant.selections || []).map(s => `${text(s.name || s.option_name)}: ${text(s.value || s.value_name)}`).filter(s => s !== ": ").join(" · ") || variant.display_name || variant.name || variant.id;
export const searchInventoryItems = (items, query) => {
    const term = query.trim().toLocaleLowerCase();
    return items.filter(item => [item.name, item.sku, item.barcode, item.id, item.mezan_product_id, item.salla_product_id, item.code, ...(item.variants || []).flatMap(v => [v.sku, v.barcode])].some(v => String(v ?? "").toLocaleLowerCase().includes(term)));
};
const fieldLabel = field => ({ product_id: "المنتج", variant_id: "متغير المنتج", resource_id: "المكوّن", item_type: "هوية البند", opening_quantity: "الكمية", opening_unit_cost: "تكلفة الوحدة", opening_total_cost: "الإجمالي", allocations: "التوزيع" }[field] || (field.startsWith("selected_options.") ? "خيار المنتج" : field.includes("location_id") ? "خانة التخزين" : field.includes("barcode") ? "باركود الخانة" : field.includes("quantity") ? "كمية التوزيع" : field));
const chosenOptions = row => Object.entries(row.selected_options || {}).sort(([a], [b]) => a.localeCompare(b));

// Financial valuation facts only. Physical quantities are optional draft metadata.
export function validateOpeningInventoryRows(rows = [], context = {}) {
    const errors = [], identities = new Set();
    rows.forEach((row, index) => {
        const fields = new Set();
        const error = (field, message) => { if (!fields.has(field)) { fields.add(field); errors.push({ row: index, field, message }); } };
        const product = (context.products || []).find(item => same(productId(item), row.product_id));
        const component = (context.components || []).find(item => same(item.id, row.resource_id));
        let identity;
        if (row.item_type === "PRODUCT") {
            if (!product) error("product_id", "اختر منتجًا من كتالوج ميزان 2.");
            const hasVariants = product?.variants_required || product?.variants_count || product?.variants?.length;
            if (hasVariants && !row.variant_id) error("variant_id", "اختر متغير المنتج الحقيقي من الكتالوج.");
            if (row.variant_id && !(product?.variants || []).some(v => same(v.id, row.variant_id))) error("variant_id", "متغير المنتج غير مطابق للكتالوج.");
            if (!hasVariants) {
                for (const option of product?.options || []) {
                    const selected = row.selected_options?.[option.id];
                    if (!selected || !(option.values || []).some(v => same(v.id, selected))) error(`selected_options.${option.id}`, `اختر ${option.name} من خيارات الكتالوج.`);
                }
                for (const [id] of chosenOptions(row)) if (!(product?.options || []).some(o => same(o.id, id))) error(`selected_options.${id}`, "الخيار غير موجود في كتالوج المنتج.");
            }
            if (positive(row.opening_quantity) && !Number.isInteger(Number(row.opening_quantity))) error("opening_quantity", "كمية المنتج يجب أن تكون عددًا صحيحًا.");
            identity = `product:${row.product_id}:${row.variant_id || ""}:${JSON.stringify(chosenOptions(row))}`;
        } else if (row.item_type === "STOCK_COMPONENT") {
            if (!component || component.kind === "service" || component.track_inventory !== true || (row.category_id && !(component.category_ids || []).some(id => same(id, row.category_id)))) error("resource_id", "اختر مكوّنًا مخزنيًا من ميزان 2 تابعًا للتصنيف.");
            if (!component?.unit) error("resource_id", "وحدة المكوّن غير محددة في الكتالوج؛ يلزم تصحيحها.");
            identity = `component:${row.resource_id}`;
        } else error("item_type", "نوع بند المخزون غير صالح.");
        if (identity && identities.has(identity)) error("item_type", "الهوية مكررة؛ اجمع كمياتها في سطر واحد.");
        identities.add(identity);
        if (!positive(row.opening_quantity)) error("opening_quantity", "الكمية مطلوبة وموجبة.");
        if (!positive(row.opening_unit_cost)) error("opening_unit_cost", "تكلفة الوحدة مطلوبة وموجبة.");
        const total = inventoryTotal(row.opening_quantity, row.opening_unit_cost);
        if (total && scaledDecimal(total, 2) !== scaledDecimal(row.opening_total_cost, 2)) error("opening_total_cost", "الإجمالي لا يطابق الكمية × تكلفة الوحدة.");
        const locations = new Set();
        (row.allocations || []).forEach((a, ai) => {
            const field = `allocations.${ai}`;
            if (!(context.locations || []).some(l => same(l.id, a.location_id))) error(`${field}.location_id`, "اختر خانة صحيحة للتوزيع المدخل.");
            if (locations.has(a.location_id)) error(`${field}.location_id`, "لا تكرر الخانة داخل البند.");
            locations.add(a.location_id);
            if (!positive(a.quantity)) error(`${field}.quantity`, "كمية التوزيع مطلوبة وموجبة.");
            if (positive(a.quantity) && row.item_type === "PRODUCT" && !Number.isInteger(Number(a.quantity))) error(`${field}.quantity`, "كمية المنتج في الخانة يجب أن تكون عددًا صحيحًا.");
            const location = (context.locations || []).find(l => same(l.id, a.location_id));
            if (a.scanned_location_barcode && location?.barcode && a.scanned_location_barcode !== location.barcode) error(`${field}.scanned_location_barcode`, "باركود الخانة لا يطابق الكتالوج.");
        });
        if (row.allocations?.length && positive(row.opening_quantity) && row.allocations.every(a => positive(a.quantity)) && row.allocations.reduce((sum, a) => sum + scaledDecimal(a.quantity), 0n) !== scaledDecimal(row.opening_quantity)) error("allocations", "مجموع كميات التوزيع يجب أن يساوي كمية البند.");
    });
    return errors;
}
function Field({ label, children }) { return <label className="block space-y-1"><span>{label}</span>{children}</label>; }
function Image({ src, name }) { return src ? <img src={src} alt={name || "صورة المنتج"} className="h-16 w-16 rounded object-contain" loading="lazy" /> : <span>لا توجد صورة في الكتالوج</span>; }
function Picker({ items, label, onSelect, render }) {
    const [query, setQuery] = useState("");
    const matches = searchInventoryItems(items, query);
    return <div><Field label={label}><input type="search" aria-label={label} className={inputClass} value={query} onChange={e => setQuery(e.target.value)} placeholder="الاسم / SKU / الباركود / الرقم" /></Field><p>{matches.length} نتيجة</p><ul className="max-h-64 overflow-y-auto" aria-label={`نتائج ${label}`}>{matches.slice(0, 50).map(item => <li key={item.id}><button type="button" className={`${buttonClass} flex w-full items-center gap-3 text-right`} onClick={() => onSelect(item)}>{render(item)}</button></li>)}</ul>{matches.length > 50 && <p>أدخل بحثًا أدق لعرض بقية النتائج.</p>}</div>;
}
export default function OpeningInventoryEditor({ value = [], onChange, context = {} }) {
    const { products = [], components = [], categories = [], locations = [], inventory_accounts = [] } = context;
    const edit = (index, patch) => onChange(value.map((row, i) => i === index ? { ...row, ...patch } : row));
    const editAllocation = (index, ai, patch) => edit(index, { allocations: value[index].allocations.map((a, i) => i === ai ? { ...a, ...patch } : a) });
    const cost = (index, field, nextValue) => { const next = { ...value[index], [field]: nextValue }; next.opening_total_cost = inventoryTotal(next.opening_quantity, next.opening_unit_cost); edit(index, next); };
    const errors = validateOpeningInventoryRows(value, context);
    const quantity = value.reduce((sum, row) => sum + (scaledDecimal(row.opening_quantity) || 0n), 0n);
    const total = value.reduce((sum, row) => sum + (scaledDecimal(inventoryTotal(row.opening_quantity, row.opening_unit_cost), 2) || 0n), 0n);
    return <section dir="rtl" className="space-y-4" aria-label="المخزون الافتتاحي">
        <p>تقييم المخزون المالي مستقل عن الجرد الفعلي. التوزيع على المستودعات والخانات اختياري؛ حفظ هذه المسودة لا يغيّر المخزون أو القيود.</p>
        <div aria-label="ملخص المخزون">بنود المنتجات: {value.filter(r => r.item_type === "PRODUCT").length} · بنود المكونات: {value.filter(r => r.item_type === "STOCK_COMPONENT").length} · مجموع الكمية: {String(quantity / 1000000n)}.{String(quantity % 1000000n).padStart(6, "0")} · قيمة المخزون SAR: {String(total / 100n)}.{String(total % 100n).padStart(2, "0")}</div>
        {value.map((row, index) => {
            const product = products.find(p => same(productId(p), row.product_id));
            const variant = product?.variants?.find(v => same(v.id, row.variant_id));
            const component = components.find(c => same(c.id, row.resource_id));
            const numberField = (field, label, step) => <Field label={label}><input aria-label={`${label} ${index + 1}`} className={inputClass} type="number" min="0" step={step} value={row[field] ?? ""} onChange={e => cost(index, field, e.target.value)} /></Field>;
            return <fieldset key={index} className="space-y-3 rounded-xl border p-4"><legend>بند المخزون {index + 1} — {product?.name || component?.name || "غير محدد"}</legend>
                <Field label="نوع البند"><select aria-label={`نوع البند ${index + 1}`} className={inputClass} value={row.item_type} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), item_type: e.target.value })}><option value="PRODUCT">منتج</option><option value="STOCK_COMPONENT">مكوّن مخزني</option></select></Field>
                {row.item_type === "PRODUCT" ? <>
                    <Picker label={`بحث المنتج ${index + 1}`} items={products} onSelect={p => edit(index, { ...emptyOpeningInventoryRow(), product_id: productId(p) })} render={p => <><Image src={p.main_image} name={p.name} /><span>{p.name} · SKU: {p.sku || "—"} · Barcode: {p.barcode || "—"} · #{p.salla_product_id || p.id}<small className="block">{(p.options || []).map(o => `${o.name}: ${(o.values || []).map(v => v.name).join(" / ")}`).join(" · ")}</small></span></>} />
                    {product && <div aria-label={`المنتج المختار ${index + 1}`}><Image src={variant?.image || product.main_image} name={product.name} /><strong>{product.name}</strong><p>{variant ? variantLabel(variant) : ""} · {variant?.sku || product.sku} · {variant?.barcode || product.barcode}</p></div>}
                    {(product?.variants_required || product?.variants_count || product?.variants?.length > 0) ? <Field label="متغير المنتج — مطلوب"><select aria-label={`خيار المنتج ${index + 1}`} className={inputClass} value={row.variant_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), product_id: row.product_id, variant_id: e.target.value })}><option value="">اختر المتغير</option>{(product.variants || []).map(v => <option key={v.id} value={v.id}>{variantLabel(v)} · {v.sku} · {v.barcode}</option>)}</select></Field> : (product?.options || []).map(option => <Field key={option.id} label={option.name}><select className={inputClass} aria-label={`${option.name} ${index + 1}`} value={row.selected_options?.[option.id] || ""} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), product_id: row.product_id, selected_options: { ...(row.selected_options || {}), [option.id]: e.target.value } })}><option value="">اختر {option.name}</option>{(option.values || []).map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>)}
                </> : <>
                    <Field label="التصنيف"><select aria-label={`التصنيف ${index + 1}`} className={inputClass} value={row.category_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", category_id: e.target.value })}><option value="">كل التصنيفات</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
                    <Picker label={`بحث المكوّن ${index + 1}`} items={components.filter(c => c.kind !== "service" && c.track_inventory === true && (!row.category_id || (c.category_ids || []).some(id => same(id, row.category_id))))} onSelect={c => edit(index, { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", category_id: row.category_id, resource_id: c.id })} render={c => <span>{c.name} · {c.code} · {c.unit || "وحدة غير محددة"} · {(c.category_ids || []).map(id => categories.find(cat => same(cat.id, id))?.name).filter(Boolean).join("، ")}</span>} />
                    <p>المكوّن: {component?.name || "غير محدد"} · الوحدة: {component?.unit || "غير محددة"}</p>
                </>}
                {inventory_accounts.length > 0 && <Field label="حساب المخزون"><select aria-label={`حساب المخزون ${index + 1}`} className={inputClass} value={row.inventory_account_id || ""} onChange={e => edit(index, { inventory_account_id: e.target.value })}><option value="">اختر الحساب</option>{inventory_accounts.map(a => <option key={a.entity_id} value={a.entity_id}>{a.label}</option>)}</select></Field>}
                <div className="grid gap-3 sm:grid-cols-3">{numberField("opening_quantity", "الكمية", row.item_type === "PRODUCT" ? "1" : "any")}{numberField("opening_unit_cost", "تكلفة الوحدة", "0.000001")}<Field label="الإجمالي"><input aria-label={`الإجمالي ${index + 1}`} className={inputClass} readOnly value={inventoryTotal(row.opening_quantity, row.opening_unit_cost)} /></Field></div>
                {(row.allocations || []).map((a, ai) => <fieldset key={ai} className="space-y-2 rounded-lg border p-3"><legend>توزيع اختياري {ai + 1}</legend>
                    <Field label="المستودع / الخانة"><select aria-label={`الخانة ${index + 1}-${ai + 1}`} className={inputClass} value={a.location_id} onChange={e => editAllocation(index, ai, { location_id: e.target.value, scanned_location_barcode: "" })}><option value="">اختر الخانة</option>{locations.map(l => <option key={l.id} value={l.id}>{l.warehouse_name || l.warehouse_id} / {l.code || l.name || l.id}</option>)}</select></Field>
                    <Field label="كمية الخانة"><input aria-label={`كمية الخانة ${index + 1}-${ai + 1}`} className={inputClass} type="number" min="0" step={row.item_type === "PRODUCT" ? "1" : "any"} value={a.quantity} onChange={e => editAllocation(index, ai, { quantity: e.target.value })} /></Field>
                    <Field label="باركود الخانة — اختياري"><input aria-label={`باركود الخانة ${index + 1}-${ai + 1}`} className={inputClass} value={a.scanned_location_barcode} onChange={e => editAllocation(index, ai, { scanned_location_barcode: e.target.value })} /></Field>
                    <button type="button" className={buttonClass} onClick={() => edit(index, { allocations: row.allocations.filter((_, i) => i !== ai) })}>حذف التوزيع</button>
                </fieldset>)}
                {errors.some(e => e.row === index) && <ul role="alert" className="text-sm text-rose-700" aria-label={`نواقص البند ${index + 1}`}>{errors.filter(e => e.row === index).map(e => <li key={e.field}>بند {index + 1} {product?.name || component?.name}: {fieldLabel(e.field)} — {e.message}</li>)}</ul>}
                <div className="flex gap-2"><button type="button" className={buttonClass} onClick={() => edit(index, { allocations: [...(row.allocations || []), allocation()] })}>إضافة توزيع اختياري</button><button type="button" className={buttonClass} onClick={() => onChange(value.filter((_, i) => i !== index))}>حذف البند</button></div>
            </fieldset>;
        })}
        <button type="button" className={buttonClass} onClick={() => onChange([...value, emptyOpeningInventoryRow()])}>إضافة منتج أو مكوّن</button>
    </section>;
}
