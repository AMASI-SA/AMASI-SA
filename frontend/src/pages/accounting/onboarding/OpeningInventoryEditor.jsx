import React, { useState } from "react";
import { inventoryTotal, scaledDecimal } from "./onboardingDecimal";
import { inventoryImage, optionSummary, productIdentity, rowProductIdentity, searchProducts } from "./inventoryCatalogPresentation";

const inputClass = "w-full rounded-lg border border-slate-300 bg-white p-2 text-sm";
const buttonClass = "rounded-lg border border-slate-300 px-3 py-2 text-sm";
const allocation = () => ({ location_id: "", quantity: "", scanned_location_barcode: "" });
export const emptyOpeningInventoryRow = () => ({ item_type: "PRODUCT", product_v2_id: "", product_id: "", variant_id: "", resource_id: "", category_id: "", inventory_account_id: "", opening_quantity: "", opening_unit_cost: "", opening_total_cost: "", allocations: [] });
const same = (a, b) => String(a ?? "") === String(b ?? "");
const positive = value => { const scaled = scaledDecimal(value); return scaled !== null && scaled > 0n; };
const eligible = row => row.status === "active" && row.archived !== true && row.is_active !== false && row.track_inventory === true && row.kind !== "service";
const fields = { product_id: "المنتج", variant_id: "خيار المنتج", resource_id: "المكوّن", item_type: "هوية البند", opening_quantity: "الكمية", opening_unit_cost: "تكلفة الوحدة", opening_total_cost: "الإجمالي", allocations: "التوزيع" };

export function validateOpeningInventoryRows(rows = [], context = {}) {
    const errors = [], identities = new Set();
    rows.forEach((row, index) => {
        const product = (context.products || []).find(item => same(productIdentity(item), rowProductIdentity(row)));
        const component = (context.components || []).find(item => same(item.id, row.resource_id));
        const error = (field, reason) => {
            if (!errors.some(e => e.row === index && e.field === field)) errors.push({ row: index, field, message: `البند ${index + 1}${(product || component)?.name ? ` (${(product || component).name})` : ""} — ${fields[field] || field}: ${reason}` });
        };
        let identity;
        if (row.item_type === "PRODUCT") {
            if (!product) error("product_id", "اختر منتجًا من كتالوج V2.");
            if ((product?.variants_required || product?.variants_count || product?.variants?.length || product?.options?.length) && !row.variant_id) error("variant_id", "اختر الخيار الأصلي؛ إذا لم يتوفر يلزم استكمال الكتالوج.");
            if (row.variant_id && !(product?.variants || []).some(v => same(v.id, row.variant_id))) error("variant_id", "الخيار غير مطابق للكتالوج.");
            identity = `product:${rowProductIdentity(row)}:${row.variant_id || ""}`;
        } else if (row.item_type === "STOCK_COMPONENT") {
            if (!component || !eligible(component) || !(component.category_ids || []).some(id => same(id, row.category_id))) error("resource_id", "اختر مكوّنًا مخزنيًا نشطًا تابعًا للتصنيف.");
            else if (!component.unit) error("resource_id", "وحدة المكوّن غير محددة في الكتالوج.");
            identity = `component:${row.resource_id}`;
        } else error("item_type", "نوع البند غير صالح.");
        if (identity && identities.has(identity)) error("item_type", "الهوية مكررة؛ اجمع كمياتها في سطر واحد.");
        identities.add(identity);
        for (const field of ["opening_quantity", "opening_unit_cost"]) if (!positive(row[field])) error(field, "أدخل قيمة موجبة بحد أقصى 6 منازل عشرية.");
        if (positive(row.opening_quantity) && row.item_type === "PRODUCT" && scaledDecimal(row.opening_quantity) % 1000000n !== 0n) error("opening_quantity", "كمية المنتج يجب أن تكون عددًا صحيحًا.");
        if (positive(row.opening_quantity) && positive(row.opening_unit_cost) && inventoryTotal(row.opening_quantity, row.opening_unit_cost) !== row.opening_total_cost) error("opening_total_cost", "الإجمالي لا يطابق الكمية × تكلفة الوحدة.");
        const seenLocations = new Set();
        (row.allocations || []).forEach((a, ai) => {
            const field = `التوزيع ${ai + 1}`;
            const location = (context.locations || []).find(l => same(l.id, a.location_id));
            if (!location) error(`${field} / الخانة`, "اختر خانة من الكتالوج.");
            else if (seenLocations.has(a.location_id)) error(`${field} / الخانة`, "الخانة مكررة في البند.");
            seenLocations.add(a.location_id);
            if (!positive(a.quantity)) error(`${field} / الكمية`, "أدخل كمية موجبة.");
            else if (row.item_type === "PRODUCT" && scaledDecimal(a.quantity) % 1000000n !== 0n) error(`${field} / الكمية`, "كمية المنتج يجب أن تكون عددًا صحيحًا.");
            if (a.scanned_location_barcode && location?.barcode_value && a.scanned_location_barcode !== location.barcode_value) error(`${field} / الباركود`, "لا يطابق باركود الخانة المختارة.");
        });
        if (row.allocations?.length && positive(row.opening_quantity) && row.allocations.every(a => positive(a.quantity)) && row.allocations.reduce((sum, a) => sum + scaledDecimal(a.quantity), 0n) !== scaledDecimal(row.opening_quantity)) error("allocations", "مجموع كميات الخانات يجب أن يساوي كمية السطر.");
    });
    return errors;
}
function Field({ label, children }) { return <label className="block space-y-1"><span>{label}</span>{children}</label>; }
function ProductImage({ product, variant }) {
    const src = inventoryImage(product, variant);
    return src ? <img className="h-16 w-16 rounded-lg object-contain" src={src} alt={product.name || "صورة المنتج"} loading="lazy" /> : <span className="text-xs text-slate-500">لا توجد صورة في الكتالوج</span>;
}
function ProductPicker({ products, selected, index, onSelect }) {
    const [query, setQuery] = useState("");
    const [open, setOpen] = useState(!selected);
    const results = searchProducts(products, query);
    return <div className="space-y-2">
        <Field label="بحث المنتج بالاسم أو SKU أو الباركود أو المعرّف"><input aria-label={`بحث المنتج ${index + 1}`} className={inputClass} value={query} onFocus={() => setOpen(true)} onChange={e => { setQuery(e.target.value); setOpen(true); }} /></Field>
        {selected && <button type="button" className={buttonClass} onClick={() => setOpen(!open)}>تغيير المنتج: {selected.name}</button>}
        {open && <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border p-2" aria-label={`نتائج المنتجات ${index + 1}`}><p className="text-xs">{results.length} نتيجة{results.length > 50 ? " · أضف تفاصيل للبحث لعرض بقية النتائج" : ""}</p>{results.slice(0, 50).map(product => <button key={productIdentity(product)} type="button" className="flex w-full items-center gap-3 rounded-lg p-2 text-right hover:bg-emerald-50 focus:bg-emerald-50" onClick={() => { onSelect(product); setOpen(false); setQuery(""); }}><ProductImage product={product} /><span><strong>{product.name}</strong><span className="block text-xs">SKU: {product.sku || "—"} · barcode: {product.barcode || "—"}</span><span className="block text-xs">{optionSummary(product) || "بلا خيارات"}</span></span></button>)}</div>}
    </div>;
}
const fixed = (value, scale) => `${value / 10n ** BigInt(scale)}.${String(value % 10n ** BigInt(scale)).padStart(scale, "0")}`;
export default function OpeningInventoryEditor({ value = [], onChange, context = {} }) {
    const { products = [], components = [], categories = [], locations = [] } = context;
    const edit = (index, patch) => onChange(value.map((row, i) => i === index ? { ...row, ...patch } : row));
    const editAllocation = (index, ai, patch) => edit(index, { allocations: value[index].allocations.map((a, i) => i === ai ? { ...a, ...patch } : a) });
    const cost = (index, field, nextValue) => { const next = { ...value[index], [field]: nextValue }; next.opening_total_cost = inventoryTotal(next.opening_quantity, next.opening_unit_cost); edit(index, next); };
    const errors = validateOpeningInventoryRows(value, context);
    const quantity = value.reduce((sum, row) => sum + (scaledDecimal(row.opening_quantity) || 0n), 0n);
    const total = value.reduce((sum, row) => sum + (scaledDecimal(inventoryTotal(row.opening_quantity, row.opening_unit_cost), 2) || 0n), 0n);
    return <section dir="rtl" className="space-y-4" aria-label="المخزون الافتتاحي">
        <p className="text-sm text-slate-600">مسودة تقييم مالي. التوزيع على المستودع والخانات اختياري، وحفظه لا يثبت اعتماد المخزون الفعلي.</p>
        <div aria-label="ملخص المخزون" className="grid gap-3 rounded-xl bg-emerald-50 p-4 sm:grid-cols-4"><p>أسطر المنتجات: {value.filter(r => r.item_type === "PRODUCT").length}</p><p>أسطر المكوّنات: {value.filter(r => r.item_type === "STOCK_COMPONENT").length}</p><p>مجموع الكمية: {fixed(quantity, 6).replace(/\.?0+$/, "")} <small>(وحدات متنوعة)</small></p><p>قيمة المخزون: <strong>{fixed(total, 2)} SAR</strong></p></div>
        {value.map((row, index) => {
            const product = products.find(p => same(productIdentity(p), rowProductIdentity(row)));
            const variant = product?.variants?.find(v => same(v.id, row.variant_id));
            const component = components.find(c => same(c.id, row.resource_id));
            const numberField = (field, label, step) => <Field label={label}><input aria-label={`${label} ${index + 1}`} className={inputClass} type="number" min="0" step={step} value={row[field] ?? ""} onChange={e => cost(index, field, e.target.value)} /></Field>;
            return <fieldset key={index} className="space-y-3 rounded-xl border p-4"><legend className="px-2 font-bold">بند المخزون {index + 1}</legend>
                <Field label="نوع البند"><select aria-label={`نوع البند ${index + 1}`} className={inputClass} value={row.item_type} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), item_type: e.target.value })}><option value="PRODUCT">منتج</option><option value="STOCK_COMPONENT">مكوّن مخزني</option></select></Field>
                {row.item_type === "PRODUCT" ? <>
                    <ProductPicker products={products} selected={product} index={index} onSelect={p => edit(index, { ...emptyOpeningInventoryRow(), product_v2_id: productIdentity(p), product_id: productIdentity(p) })} />
                    {(product?.variants_required || product?.options?.length || product?.variants?.length) ? <Field label="خيار المنتج — مطلوب"><select aria-label={`خيار المنتج ${index + 1}`} className={inputClass} value={row.variant_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), product_v2_id: rowProductIdentity(row), product_id: rowProductIdentity(row), variant_id: e.target.value })}><option value="">اختر الخيار الأصلي</option>{(product.variants || []).map(v => <option key={v.id} value={v.id}>{optionSummary(product, v) || v.id} · {v.sku || ""} · {v.barcode || ""}</option>)}</select></Field> : null}
                    {product && <div className="flex items-center gap-3 rounded-lg bg-slate-50 p-3"><ProductImage product={product} variant={variant} /><div><strong>{product.name}</strong><p>{variant ? optionSummary(product, variant) : optionSummary(product)}</p><p className="text-xs">SKU: {variant?.sku || product.sku || "—"} · barcode: {variant?.barcode || product.barcode || "—"}</p></div></div>}
                </> : <>
                    <Field label="التصنيف"><select aria-label={`التصنيف ${index + 1}`} className={inputClass} value={row.category_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", category_id: e.target.value })}><option value="">اختر التصنيف</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
                    <Field label="المكوّن"><select aria-label={`المكوّن ${index + 1}`} className={inputClass} value={row.resource_id} onChange={e => edit(index, { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", category_id: row.category_id, resource_id: e.target.value })}><option value="">اختر المكوّن</option>{components.filter(c => eligible(c) && (c.category_ids || []).some(id => same(id, row.category_id))).map(c => <option key={c.id} value={c.id}>{c.name} · {c.code} · {c.unit}</option>)}</select></Field>
                    <p>الوحدة المسجلة: <strong>{component?.unit || "غير محددة"}</strong></p>
                </>}
                <div className="grid gap-3 sm:grid-cols-3">{numberField("opening_quantity", "الكمية", row.item_type === "PRODUCT" ? "1" : "any")}{numberField("opening_unit_cost", "تكلفة الوحدة", "0.000001")}<Field label="الإجمالي"><input aria-label={`الإجمالي ${index + 1}`} className={inputClass} readOnly value={inventoryTotal(row.opening_quantity, row.opening_unit_cost)} /></Field></div>
                {(row.allocations || []).map((a, ai) => <fieldset key={ai} className="space-y-2 rounded-lg border bg-slate-50 p-3"><legend>توزيع اختياري {ai + 1}</legend>
                    <Field label="المستودع / الخانة"><select aria-label={`الخانة ${index + 1}-${ai + 1}`} className={inputClass} value={a.location_id} onChange={e => editAllocation(index, ai, { location_id: e.target.value, scanned_location_barcode: "" })}><option value="">اختر الخانة</option>{locations.map(l => <option key={l.id} value={l.id}>{l.warehouse_name || l.warehouse_id} / {l.code || l.name || l.id} · {l.provenance || "AMBIGUOUS"}</option>)}</select></Field>
                    <p className="text-xs">AMBIGUOUS: مصدر إنشاء الخانة غير مثبت بعقد V2؛ لا تُعد دليل اعتماد فعلي.</p>
                    <Field label="كمية الخانة"><input aria-label={`كمية الخانة ${index + 1}-${ai + 1}`} className={inputClass} type="number" min="0" step={row.item_type === "PRODUCT" ? "1" : "any"} value={a.quantity} onChange={e => editAllocation(index, ai, { quantity: e.target.value })} /></Field>
                    <Field label="باركود الخانة — اختياري"><input aria-label={`باركود الخانة ${index + 1}-${ai + 1}`} className={inputClass} value={a.scanned_location_barcode} onChange={e => editAllocation(index, ai, { scanned_location_barcode: e.target.value })} /></Field>
                    <button type="button" className={buttonClass} onClick={() => edit(index, { allocations: row.allocations.filter((_, i) => i !== ai) })}>حذف التوزيع</button>
                </fieldset>)}
                {errors.some(e => e.row === index) && <ul className="text-sm text-rose-700" aria-label={`نواقص البند ${index + 1}`}>{errors.filter(e => e.row === index).map(e => <li key={e.field}>{e.message}</li>)}</ul>}
                <div className="flex gap-2"><button type="button" className={buttonClass} onClick={() => edit(index, { allocations: [...(row.allocations || []), allocation()] })}>إضافة توزيع اختياري</button><button type="button" className={buttonClass} onClick={() => onChange(value.filter((_, i) => i !== index))}>حذف البند</button></div>
            </fieldset>;
        })}
        <button type="button" className={buttonClass} onClick={() => onChange([...value, emptyOpeningInventoryRow()])}>إضافة منتج أو مكوّن</button>
    </section>;
}
