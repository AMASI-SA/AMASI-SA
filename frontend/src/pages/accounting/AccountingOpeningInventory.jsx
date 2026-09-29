import { useEffect, useState } from "react";
import { approveOpeningInventory, getOpeningInventoryContext, getOpeningInventoryImport, importOpeningInventory } from "../../services/openingInventory";

const allocation = () => ({ location_id: "", quantity: "", scanned_location_barcode: "", preparation_state: "requires_preparation", specification_fields: [] });
const emptyRow = () => ({ item_type: "PRODUCT", product_id: "", variant_id: "", category_id: "", resource_id: "", inventory_account_id: "", opening_quantity: "", opening_unit_cost: "", opening_total_cost: "", allocations: [allocation()] });
const inputClass = "min-h-10 w-full rounded border border-slate-300 bg-white px-2 py-2 text-sm";
const buttonClass = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40";
const positive = value => value !== "" && Number.isFinite(Number(value)) && Number(value) > 0;
const publicError = error => error?.response?.data?.detail?.code || "تعذر إكمال الطلب؛ راجع الحقول وحالة الافتتاح المالي.";

export function buildOpeningInventoryDocument(rows, context) {
    const cutover = context?.cutover || {};
    if (!cutover.cutover_at || !cutover.opening_txn_group_id || !cutover.evidence_ref) throw new Error("يجب ترحيل الافتتاح المالي وربط دليل المخزون أولًا.");
    if (!rows.length) throw new Error("أدخل المخزون الفعلي الموجب فقط؛ لا تنشئ كمية افتراضية.");
    const usedLocations = new Set();
    const usedIdentities = new Set();
    const documents = rows.map(row => {
        let identity;
        if (row.item_type === "PRODUCT") {
            const product = context.products.find(p => p.id === row.product_id);
            if (!product) throw new Error("اختر منتجًا فعليًا من الكتالوج.");
            if (product.variants_required && !row.variant_id) throw new Error("اختيار Variant إلزامي لهذا المنتج.");
            if (row.variant_id && !product.variants.some(v => v.id === row.variant_id)) throw new Error("خيار المنتج غير مطابق.");
            if (!Number.isInteger(Number(row.opening_quantity))) throw new Error("كمية المنتج يجب أن تكون عددًا صحيحًا.");
            identity = { item_type: "PRODUCT", product_id: product.id, variant_id: row.variant_id || null };
        } else {
            const component = context.components.find(c => c.id === row.resource_id && (c.category_ids || []).map(String).includes(row.category_id));
            if (!component) throw new Error("اختر التصنيف ومكوّنًا مخزنيًا تابعًا له؛ الخدمات غير مسموحة.");
            identity = { item_type: "STOCK_COMPONENT", resource_id: component.id, category_id: row.category_id };
        }
        const key = JSON.stringify(identity);
        if (usedIdentities.has(key)) throw new Error("اجمع خانات الهوية نفسها في سطر واحد.");
        usedIdentities.add(key);
        if (!context.inventory_accounts.some(a => a.entity_id === row.inventory_account_id)) throw new Error("اختر حساب المخزون من الافتتاح المالي المرحّل.");
        if (![row.opening_quantity, row.opening_unit_cost, row.opening_total_cost].every(positive)) throw new Error("الكمية وتكلفة الوحدة والإجمالي مطلوبة وموجبة.");
        const allocations = row.allocations.map(a => {
            if (!context.locations.some(l => l.id === a.location_id) || !a.scanned_location_barcode.trim() || !positive(a.quantity)) throw new Error("حدد الخانة وامسح باركودها وأدخل الكمية.");
            if (usedLocations.has(a.location_id)) throw new Error("لا تكرر الخانة بين التوزيعات.");
            usedLocations.add(a.location_id);
            if (row.item_type === "PRODUCT" && !Number.isInteger(Number(a.quantity))) throw new Error("كمية المنتج في الخانة يجب أن تكون عددًا صحيحًا.");
            const specifications = {};
            for (const field of a.specification_fields || []) {
                const name = field.name.trim(), value = field.value.trim();
                if (!name || !value || Object.hasOwn(specifications, name) || ["__proto__", "constructor", "prototype"].includes(name)) throw new Error("أدخل اسم وقيمة كل مواصفة دون تكرار.");
                specifications[name] = value;
            }
            return { location_id: a.location_id, quantity: a.quantity, scanned_location_barcode: a.scanned_location_barcode, preparation_state: a.preparation_state, specifications };
        });
        if (Math.abs(allocations.reduce((sum, a) => sum + Number(a.quantity), 0) - Number(row.opening_quantity)) > 0.0000001) throw new Error("مجموع كميات الخانات يجب أن يساوي كمية السطر.");
        return { ...identity, inventory_account_id: row.inventory_account_id, opening_quantity: row.opening_quantity, opening_unit_cost: row.opening_unit_cost, opening_total_cost: row.opening_total_cost, allocations };
    });
    return { schema_version: "g47-opening-inventory-v1", cost_policy_version: "moving-weighted-average-v1", cutover_at: cutover.cutover_at, opening_txn_group_id: cutover.opening_txn_group_id, evidence_ref: cutover.evidence_ref, rows: documents };
}

function SpecificationFields({ fields = [], onChange }) {
    return <div className="space-y-2"><span>المواصفات</span>{fields.map((field, index) => <div key={index} className="flex gap-2"><input className={inputClass} aria-label={`اسم المواصفة ${index + 1}`} placeholder="مثال: اللون" value={field.name} onChange={e => onChange(fields.map((f, i) => i === index ? { ...f, name: e.target.value } : f))} /><input className={inputClass} aria-label={`قيمة المواصفة ${index + 1}`} placeholder="مثال: أزرق" value={field.value} onChange={e => onChange(fields.map((f, i) => i === index ? { ...f, value: e.target.value } : f))} /><button type="button" className={buttonClass} onClick={() => onChange(fields.filter((_, i) => i !== index))}>حذف المواصفة</button></div>)}<button type="button" className={buttonClass} onClick={() => onChange([...fields, { name: "", value: "" }])}>إضافة مواصفة</button></div>;
}

export default function AccountingOpeningInventory({ isOwner = false }) {
    const [context, setContext] = useState(null);
    const [rows, setRows] = useState([emptyRow()]);
    const [imported, setImported] = useState(null);
    const [reviewed, setReviewed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const refresh = async () => { try { setContext(await getOpeningInventoryContext()); } catch (err) { setError(publicError(err)); } };
    useEffect(() => { if (isOwner) refresh(); }, [isOwner]); // Read-only; no import or approval on mount.
    if (!isOwner) return null;
    const edit = (index, patch) => { setRows(current => current.map((r, i) => i === index ? { ...r, ...patch } : r)); setImported(null); setReviewed(false); };
    const editAllocation = (index, aIndex, patch) => edit(index, { allocations: rows[index].allocations.map((a, i) => i === aIndex ? { ...a, ...patch } : a) });
    const editCost = (index, field, value) => {
        const next = { ...rows[index], [field]: value };
        if (positive(next.opening_quantity) && positive(next.opening_unit_cost)) next.opening_total_cost = (Number(next.opening_quantity) * Number(next.opening_unit_cost)).toFixed(2);
        edit(index, next);
    };
    const preview = async event => {
        event.preventDefault(); if (busy) return;
        setError(""); setReviewed(false);
        let document;
        try { document = buildOpeningInventoryDocument(rows, context); } catch (err) { setError(err.message); return; }
        setBusy(true);
        try { setImported(await importOpeningInventory(document)); } catch (err) { setError(publicError(err)); } finally { setBusy(false); }
    };
    const approve = async () => {
        if (busy || !reviewed || !imported || imported.state !== "imported") return;
        setBusy(true); setError("");
        try { setImported(await approveOpeningInventory(imported)); setReviewed(false); await refresh(); } catch (err) { setError(publicError(err)); } finally { setBusy(false); }
    };
    const resume = async id => {
        if (busy) return; setBusy(true); setError(""); setReviewed(false);
        try { setImported(await getOpeningInventoryImport(id)); } catch (err) { setError(publicError(err)); } finally { setBusy(false); }
    };
    const initialized = context?.initialization?.state === "approved";
    return <section dir="rtl" className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5" data-testid="opening-inventory">
        <h3 className="text-lg font-black">المخزون الفعلي عند القطع</h3>
        <p className="text-sm text-slate-600">جهّز البيانات محليًا، ثم راجع خطة الخادم واعتمد الكميات صراحة. الاستيراد لا يغيّر المخزون. الاعتماد يطابق قيمة المخزون مع الافتتاح المالي المرحّل ولا ينشئ قيدًا ماليًا ثانيًا.</p>
        <p className="rounded-lg bg-amber-50 p-3 text-sm">الاستيراد والاعتماد يتطلبان افتتاحًا ماليًا موثقًا وفعالًا وانتقال الكاتب إلى ميزان 2 وفتح الكتابات بإجراء صريح. هذه الشاشة لا تنفّذ تلك الإجراءات. جهّز المخزون قبل أي نشاط محاسبي تشغيلي.</p>
        {error && <p role="alert" className="text-rose-800">{error}</p>}
        <button type="button" className={buttonClass} disabled={busy} onClick={refresh}>تحديث بيانات الافتتاح والكتالوج</button>
        {!context ? <p>جارٍ تحميل بيانات المالك…</p> : <>
            <dl className="text-sm"><dt>لحظة القطع</dt><dd dir="ltr">{context.cutover?.cutover_at || "لم تُحدد"}</dd><dt>قيد الافتتاح المالي</dt><dd dir="ltr">{context.cutover?.opening_txn_group_id || "لم يُرحّل"}</dd><dt>دليل المخزون</dt><dd>{context.cutover?.evidence_ref || "غير مربوط"}</dd></dl>
            {(context.imports || []).map(item => <button type="button" key={item.id} disabled={busy} className={buttonClass} onClick={() => resume(item.id)}>عرض استيراد {item.created_at} · {item.state}</button>)}
            {initialized ? <p role="status" className="text-emerald-800">تم اعتماد المخزون الافتتاحي؛ لا يُعاد إدخال الكميات.</p> : <form onSubmit={preview} data-testid="opening-inventory-form"><fieldset disabled={busy} className="space-y-4">
                {rows.map((row, index) => {
                    const product = context.products.find(p => p.id === row.product_id);
                    const component = context.components.find(c => c.id === row.resource_id);
                    return <div key={index} className="space-y-3 rounded-xl border p-3" data-testid={`opening-stock-row-${index}`}>
                        <h4 className="font-bold">بند {index + 1}</h4>
                        <label>نوع البند<select aria-label={`نوع البند ${index + 1}`} className={inputClass} value={row.item_type} onChange={e => edit(index, { ...emptyRow(), item_type: e.target.value })}><option value="PRODUCT">منتج</option><option value="STOCK_COMPONENT">مكوّن مخزني</option></select></label>
                        {row.item_type === "PRODUCT" ? <><label>المنتج<select aria-label={`المنتج ${index + 1}`} className={inputClass} value={row.product_id} onChange={e => edit(index, { product_id: e.target.value, variant_id: "" })}><option value="">اختر المنتج</option>{context.products.map(p => <option key={p.id} value={p.id}>{p.name} · {p.sku}</option>)}</select></label>{product?.variants_required && <label>خيار المنتج<select aria-label={`خيار المنتج ${index + 1}`} className={inputClass} value={row.variant_id} onChange={e => edit(index, { variant_id: e.target.value })}><option value="">اختر الخيار — إلزامي</option>{product.variants.map(v => <option key={v.id} value={v.id}>{v.name} · {v.sku}</option>)}</select></label>}</> : <><label>التصنيف<select aria-label={`التصنيف ${index + 1}`} className={inputClass} value={row.category_id} onChange={e => edit(index, { category_id: e.target.value, resource_id: "" })}><option value="">اختر التصنيف</option>{context.categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><label>المكوّن<select aria-label={`المكون ${index + 1}`} className={inputClass} value={row.resource_id} onChange={e => edit(index, { resource_id: e.target.value })}><option value="">اختر المكوّن</option>{context.components.filter(c => (c.category_ids || []).map(String).includes(row.category_id)).map(c => <option key={c.id} value={c.id}>{c.name} · {c.code}</option>)}</select></label><p>وحدة الكتالوج: <strong>{component?.unit || "غير محددة في الكتالوج"}</strong> — لا تُحوّل الوحدة هنا.</p></>}
                        <label>حساب المخزون<select aria-label={`حساب المخزون ${index + 1}`} className={inputClass} value={row.inventory_account_id} onChange={e => edit(index, { inventory_account_id: e.target.value })}><option value="">اختر حساب الافتتاح المرحّل</option>{context.inventory_accounts.map(a => <option key={a.entity_id} value={a.entity_id}>{a.label}</option>)}</select></label>
                        <div className="grid gap-2 sm:grid-cols-3">{[["opening_quantity", "الكمية"], ["opening_unit_cost", "تكلفة الوحدة"], ["opening_total_cost", "التكلفة الإجمالية"]].map(([field, label]) => <label key={field}>{label}<input aria-label={`${label} ${index + 1}`} className={inputClass} type="number" min="0" step={field === "opening_total_cost" ? "0.01" : "0.000001"} value={row[field]} onChange={e => field === "opening_total_cost" ? edit(index, { [field]: e.target.value }) : editCost(index, field, e.target.value)} /></label>)}</div>
                        {row.allocations.map((a, ai) => <div key={ai} className="grid gap-2 rounded border bg-slate-50 p-3 sm:grid-cols-2"><label>الخانة<select aria-label={`الخانة ${index + 1}-${ai + 1}`} className={inputClass} value={a.location_id} onChange={e => editAllocation(index, ai, { location_id: e.target.value })}><option value="">اختر خانة تخزين دائم</option>{context.locations.map(l => <option key={l.id} value={l.id}>{l.code}</option>)}</select></label><label>كمية الخانة<input aria-label={`كمية الخانة ${index + 1}-${ai + 1}`} className={inputClass} type="number" min="0" step="0.000001" value={a.quantity} onChange={e => editAllocation(index, ai, { quantity: e.target.value })} /></label><label>مسح باركود الخانة<input aria-label={`باركود الخانة ${index + 1}-${ai + 1}`} className={inputClass} value={a.scanned_location_barcode} onChange={e => editAllocation(index, ai, { scanned_location_barcode: e.target.value })} /></label><label>حالة التجهيز<select aria-label={`حالة التجهيز ${index + 1}-${ai + 1}`} className={inputClass} value={a.preparation_state} onChange={e => editAllocation(index, ai, { preparation_state: e.target.value })}><option value="requires_preparation">يحتاج تجهيز</option><option value="ready_complete">جاهز مكتمل</option></select></label><SpecificationFields fields={a.specification_fields} onChange={fields => editAllocation(index, ai, { specification_fields: fields })} />{row.allocations.length > 1 && <button type="button" className={buttonClass} onClick={() => edit(index, { allocations: row.allocations.filter((_, i) => i !== ai) })}>حذف توزيع الخانة</button>}</div>)}
                        <button type="button" className={buttonClass} onClick={() => edit(index, { allocations: [...row.allocations, allocation()] })}>إضافة توزيع لخانة أخرى</button>
                        {rows.length > 1 && <button type="button" className={buttonClass} onClick={() => { setRows(rows.filter((_, i) => i !== index)); setImported(null); setReviewed(false); }}>حذف البند</button>}
                    </div>;
                })}
                <button type="button" className={buttonClass} onClick={() => { setRows([...rows, emptyRow()]); setImported(null); setReviewed(false); }}>إضافة منتج أو مكوّن</button>
                <button className={buttonClass} type="submit" disabled={!context.cutover?.opening_txn_group_id}>استيراد ومعاينة خطة الخادم — دون تهيئة الكميات</button>
            </fieldset></form>}
        </>}
        {imported && <section className="space-y-3 rounded-xl border border-emerald-200 p-4" data-testid="opening-inventory-server-plan"><h4 className="font-black">خطة الخادم المحفوظة · {imported.state}</h4><p className="text-xs">الكميات والقيمة التالية من استجابة الخادم، وليست معاينة محلية.</p>
            {(imported.plan || []).map((plan, index) => <div key={plan.cost_key || index} className="rounded border p-3"><strong>{plan.identity.product_name || plan.identity.code || plan.identity.product_id || plan.identity.resource_id}</strong><p>الكمية: {plan.opening_quantity} {plan.identity.unit || ""} · تكلفة الوحدة: {plan.opening_unit_cost} · الإجمالي: {plan.opening_total_cost} ر.س</p><p>حساب المخزون: {plan.inventory_account_id}</p>{plan.allocations.map((a, i) => <p key={i}>الخانة: {context?.locations.find(l => l.id === a.location_id)?.code || a.location_id} · {a.quantity} · {a.preparation_state} · {Object.entries(a.specifications || {}).map(([name, value]) => `${name}: ${value}`).join("، ")}</p>)}</div>)}
            {imported.state === "imported" && <><label className="flex gap-2"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={busy} />راجعت خطة الخادم والخانات والتكلفة المطابقة للافتتاح المالي</label><button className={buttonClass} type="button" disabled={busy || !reviewed} onClick={approve}>اعتماد تهيئة الكميات والتكلفة مرة واحدة</button></>}
            {imported.state === "approved" && <p role="status">تم اعتماد المخزون الافتتاحي؛ لم يُنشأ قيد قيمة مخزون إضافي.</p>}
        </section>}
    </section>;
}
