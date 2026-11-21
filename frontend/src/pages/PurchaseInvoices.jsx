/**
 * Purchase drafts are non-financial. Approval receives every line together.
 * Legacy invoices remain read-only; operation identities come from the server.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Plus, FileText } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import { todaySA } from "../lib/dates";
import InventorySuiteFrame,{InventoryPageHeader,InventorySheet} from "../components/inventory/suite/InventorySuiteFrame";
import {InventoryProductPicker,InventoryProductOptions,InventoryProductPhoto} from "../components/inventory/suite/InventoryProductFields";
import {inventoryConfiguration,inventoryVariantFor,validateInventoryConfiguration,inventoryConfigDescription,inventoryReceiptSpecifications} from "../lib/inventoryProductConfiguration";

const inputCls = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100";
const buttonCls = "rounded-lg bg-emerald-800 px-4 py-2 text-sm font-bold text-white disabled:opacity-50";
const EMPTY_CATALOG = { products: [], categories: [], components: [], locations: [], account_mappings: {} };
const fmt = (value) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const valueId = (value) => String(value ?? "");
const money = (value) => Math.round((Number(value) + Number.EPSILON) * 100) / 100;
const freshLine = () => ({
    item_type: "PRODUCT", product_id: "", variant_id: "", resource_id: "", category_id: "",
    product_name: "", sku: "", catalog_query: "", quantity: "1", unit_cost: "", cost_basis: "unit", line_total: "",
});
const freshForm = () => ({
    supplier_counterparty_id: "", invoice_number: "", invoice_date: todaySA(), due_date: "",
    tax_amount: "0", tax_treatment: "none", tax_evidence_ref: "", tax_evidence_verified: false, inventory_account_id: "",
    input_vat_account_id: "", supplier_account_id: "", notes: "", lines: [freshLine()],
});

export function isManagedDraft(invoice) {
    return invoice?.schema_version === "g47-v1" && invoice.state === "draft";
}

export function purchasableComponents(catalog, categoryId) {
    if (!categoryId) return [];
    return (catalog.components || []).filter((row) =>
        row.track_inventory === true && row.status !== "inactive"
        && (row.category_ids || []).map(valueId).includes(valueId(categoryId)));
}

export function updatePurchaseLine(line, field, value) {
    const next = { ...line, [field]: value };
    if (field === "line_total") next.cost_basis = "total";
    if (field === "unit_cost") next.cost_basis = "unit";
    const quantity = Number(next.quantity);
    if (next.cost_basis === "total") {
        next.unit_cost = next.line_total !== "" && quantity > 0
            ? String(Number((Number(next.line_total) / quantity).toFixed(6))) : "";
    } else {
        next.line_total = next.unit_cost !== "" && quantity > 0
            ? String(money(quantity * Number(next.unit_cost))) : "";
    }
    return next;
}

function productForLine(catalog, line) {
    return (catalog.products || []).find((row) =>
        valueId(row.product_id ?? row.id) === valueId(line.product_id));
}

function canonicalLine(line, catalog) {
    if (line.item_type === "PRODUCT") {
        const product = productForLine(catalog, line);
        if (!product) throw new Error("اختر المنتج من الكتالوج الحالي.");
        const variants = product.variants || [];
        const variant = variants.find((row) => valueId(row.variant_id) === valueId(line.variant_id));
        if ((product.variants_required || variants.length > 0) && !variant) {
            throw new Error("اختر خيار المنتج المحدد؛ لا يمكن استلام المنتج العام بدلًا منه.");
        }
        if (line.variant_id && !variant) throw new Error("خيار المنتج غير موجود في الكتالوج الحالي.");
        return {
            item_type: "PRODUCT", product_id: valueId(product.product_id ?? product.id),
            variant_id: variant ? valueId(variant.variant_id) : null,
            product_name: product.name, sku: (variant ? variant.sku : product.sku) || "",
        };
    }
    if (line.item_type === "STOCK_COMPONENT") {
        const category = (catalog.categories || []).find((row) => valueId(row.id) === valueId(line.category_id));
        const component = purchasableComponents(catalog, category?.id).find((row) =>
            valueId(row.resource_id ?? row.id) === valueId(line.resource_id));
        if (!category || !component) throw new Error("اختر تصنيفًا ومكوّنًا مخزنيًا فعالًا تابعًا له؛ خدمات العمل لا تدخل فاتورة المخزون.");
        return {
            item_type: "STOCK_COMPONENT", category_id: valueId(category.id),
            resource_id: valueId(component.resource_id ?? component.id),
            product_name: component.name, sku: component.code || "",
        };
    }
    throw new Error("نوع البند غير مسموح في فاتورة المخزون.");
}

function mappingExists(catalog, kind, id) {
    return !!id && (catalog.account_mappings?.[kind] || []).some((row) => valueId(row.entity_id) === valueId(id));
}

export function buildPurchaseDraft(form, catalog) {
    if (!form.supplier_counterparty_id) throw new Error("اختر المورد.");
    const supplierIdentity = (catalog.supplier_identities || []).find((row) => row.counterparty_id === form.supplier_counterparty_id);
    if (!supplierIdentity || supplierIdentity.entity_id !== form.supplier_account_id) throw new Error("المورد يحتاج ربط هوية محاسبية مثبتًا وقابلًا للسداد.");
    if (!form.invoice_date) throw new Error("حدد تاريخ الفاتورة.");
    if (!form.lines.length) throw new Error("أضف بندًا واحدًا على الأقل.");
    const lines = form.lines.map((line) => {
        const identity = canonicalLine(line, catalog);
        if (line.purchase_configuration) validateInventoryConfiguration(productForLine(catalog,line),line.purchase_configuration,line.variant_id);
        const quantity = Number(line.quantity);
        const unitCost = Number(line.unit_cost);
        if (line.quantity === "" || !Number.isFinite(quantity) || quantity <= 0) throw new Error("أدخل كمية موجبة لكل بند.");
        if (line.item_type === "PRODUCT" && !Number.isInteger(quantity)) throw new Error("كمية المنتج يجب أن تكون عددًا صحيحًا.");
        if (line.unit_cost === "" || !Number.isFinite(unitCost) || unitCost < 0) throw new Error("أدخل تكلفة وحدة صحيحة لكل بند.");
        if (Number(unitCost.toFixed(6)) !== unitCost || Number(quantity.toFixed(6)) !== quantity) throw new Error("دقة الكمية والتكلفة لا تتجاوز ست خانات عشرية.");
        return { ...(line.id ? { id: line.id } : {}), ...identity, quantity, unit_cost: unitCost,
            ...(line.purchase_configuration ? {purchase_configuration: line.purchase_configuration} : {}) };
    });
    const tax = Number(form.tax_amount);
    if (form.tax_amount === "" || !Number.isFinite(tax) || tax < 0) throw new Error("أدخل مبلغ ضريبة صحيحًا.");
    if (!["none", "non_deductible", "deductible"].includes(form.tax_treatment)) throw new Error("حدد المعالجة الضريبية.");
    if (tax > 0 && form.tax_treatment === "none") throw new Error("حدد معالجة الضريبة المثبتة على الفاتورة.");
    if (!mappingExists(catalog, "inventory", form.inventory_account_id)
        || !mappingExists(catalog, "supplier", form.supplier_account_id)) {
        throw new Error("اختر حساب المخزون وحساب المورد من الربط المحاسبي المعتمد.");
    }
    if (form.tax_treatment === "deductible"
        && (!form.tax_evidence_ref.trim() || form.tax_evidence_verified !== true
            || !mappingExists(catalog, "input_vat", form.input_vat_account_id))) {
        throw new Error("الضريبة القابلة للخصم تحتاج مستندًا ضريبيًا مرفوعًا وإقرار مراجعته وحساب ضريبة مدخلات معتمدًا.");
    }
    return {
        supplier_counterparty_id: form.supplier_counterparty_id,
        invoice_number: form.invoice_number.trim() || null, invoice_date: form.invoice_date,
        due_date: form.due_date || null, lines, tax_amount: tax,
        tax_treatment: form.tax_treatment, tax_evidence_ref: form.tax_evidence_ref.trim() || null,
        tax_evidence_verified: form.tax_treatment === "deductible" && form.tax_evidence_verified === true,
        inventory_account_id: form.inventory_account_id, supplier_account_id: form.supplier_account_id,
        input_vat_account_id: form.tax_treatment === "deductible" ? form.input_vat_account_id : null,
        notes: form.notes.trim(),
    };
}

export function buildFullPurchaseApproval(invoice, receipts, catalog) {
    if (!isManagedDraft(invoice) || !invoice.approval_operation_id || !Number.isInteger(invoice.revision)) {
        throw new Error("أعد تحميل الفاتورة للتحقق من هوية عملية الاعتماد.");
    }
    if (!invoice.lines?.length || receipts.length !== invoice.lines.length) throw new Error("يجب تجهيز استلام جميع بنود الفاتورة معًا.");
    const result = invoice.lines.map((line) => {
        const receipt = receipts.find((row) => valueId(row.line_id) === valueId(line.id));
        const location = (catalog.locations || []).find((row) => valueId(row.id) === valueId(receipt?.location_id));
        if (!line.id || !receipt || !location) throw new Error("اختر خانة مخزون لكل بند.");
        const barcode = String(receipt.scanned_location_barcode || "").trim();
        if (!barcode || barcode.toUpperCase() !== String(location.barcode || location.code || "").trim().toUpperCase()) {
            throw new Error("باركود الخانة لا يطابق الخانة المختارة.");
        }
        if (!["requires_preparation", "ready_complete"].includes(receipt.preparation_state)) throw new Error("حدد حالة تجهيز كل بند.");
        if (line.purchase_configuration && receipt.preparation_state !== (line.purchase_configuration.state === "ready" ? "ready_complete" : "requires_preparation")) throw new Error("حالة التجهيز لا تطابق مواصفات المسودة المحفوظة.");
        return {
            line_id: line.id, quantity: Number(line.quantity), location_id: location.id,
            scanned_location_barcode: barcode, preparation_state: receipt.preparation_state,
            specifications: line.purchase_configuration ? inventoryReceiptSpecifications(line) : {},
        };
    });
    return { expected_revision: invoice.revision, operation_id: invoice.approval_operation_id, receipts: result };
}

function failureMessage(error, fallback) {
    const detail = error.response?.data?.detail;
    const configurationMessages={
        purchase_configuration_definition_changed:"تغير تعريف خيارات المنتج بعد حفظ المسودة. راجع الخيارات وحالة العملية قبل الاعتماد أو إعادة المحاولة.",
        purchase_configuration_definition_incomplete:"تعريف خيارات المنتج غير مكتمل في ميزان. أكمل التعريف قبل حفظ البند المخصص.",
        purchase_variant_configuration_unavailable:"تعذر ربط خيارات هذا المنتج بنسخته المخزنية. راجع تعريف نسخ المنتج.",
        purchase_variant_configuration_mismatch:"اللون والمقاس لا يطابقان النسخة المخزنية المحددة. أعد اختيارهما من المنتج.",
        purchase_receipt_configuration_mismatch:"مواصفات الاستلام أو حالة التجهيز تختلف عن المسودة المحفوظة. أعد فتح مراجعة الاستلام.",
        purchase_configuration_option_names_ambiguous:"توجد أسماء خيارات متكررة للمنتج؛ يلزم تصحيح تعريفها قبل الاستلام.",
    };
    if(configurationMessages[detail?.code])return configurationMessages[detail.code];
    const formatted = formatApiErrorDetail(detail);
    return formatted || error.message || fallback;
}

function Field({ label, children }) {
    return <label className="block space-y-1 text-sm font-bold text-slate-700"><span>{label}</span>{children}</label>;
}

function MappingSelect({ label, kind, catalog, value, onChange, disabled }) {
    return <Field label={label}><select className={inputCls} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="">اختر حسابًا معتمدًا</option>
        {(catalog.account_mappings?.[kind] || []).map((row) =>
            <option key={row.entity_id} value={row.entity_id}>{row.label}</option>)}
    </select></Field>;
}

export function InvoiceDialog({ suppliers, catalog, editing, onClose, onSaved, readOnly: accessReadOnly = false }) {
    const readOnly = accessReadOnly || (!!editing && !isManagedDraft(editing));
    const [form, setForm] = useState(() => editing ? {
        ...freshForm(), ...editing,
        tax_amount: String(editing.tax_amount ?? 0),
        tax_evidence_ref: editing.tax_evidence_ref || "",
        inventory_account_id: editing.inventory_account_id || "",
        input_vat_account_id: editing.input_vat_account_id || "",
        supplier_account_id: editing.supplier_account_id || "",
        notes: editing.notes || "", invoice_number: editing.invoice_number || "",
        lines: (editing.lines || []).map((line) => updatePurchaseLine({
            ...freshLine(), ...line, quantity: String(line.quantity),
            sku: line.code || line.sku || "",
            unit_cost: String(line.unit_cost ?? line.unit_price ?? ""),
        }, "unit_cost", String(line.unit_cost ?? line.unit_price ?? ""))),
    } : freshForm());
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [uploading, setUploading] = useState(false);
    const [evidenceFilename, setEvidenceFilename] = useState("");
    const saving = useRef(false);
    const set = (key, value) => setForm((current) => ({
        ...current, [key]: value,
        ...(key === "supplier_counterparty_id" ? {
            supplier_account_id: (catalog.supplier_identities || []).find((row) => row.counterparty_id === value)?.entity_id || "",
        } : {}),
        ...(["supplier_counterparty_id", "invoice_number"].includes(key)
            ? { tax_evidence_ref: "", tax_evidence_verified: false } : {}),
    }));
    const replaceLine = (index, transform) => setForm((current) => ({
        ...current, lines: current.lines.map((line, i) => i === index ? transform(line) : line),
    }));
    const chooseItem = (index,item,query) => replaceLine(index,old=>{
        if(item.picker_kind === "component") return {...freshLine(),...(old.id?{id:old.id}:{}),item_type:"STOCK_COMPONENT",resource_id:valueId(item.resource_id??item.id),category_id:item.category_ids?.includes(old.category_id)?old.category_id:item.category_ids?.[0]||"",product_name:item.name,sku:item.code||""};
        const exact=(item.variants||[]).find(v=>[v.sku,v.barcode].some(value=>value&&String(value).toLowerCase()===query.trim().toLowerCase()));
        const config=item.options?.length?inventoryConfiguration(item,exact?.variant_id):undefined;
        return {...freshLine(),...(old.id?{id:old.id}:{}),product_id:valueId(item.product_id??item.id),product_name:item.name,sku:exact?.sku||item.sku||"",variant_id:exact?.variant_id||"",...(config?{purchase_configuration:config}:{})};
    });
    const configureItem=(index,product,config)=>replaceLine(index,old=>{const variant=inventoryVariantFor(product,config,old.variant_id);return {...old,purchase_configuration:config,variant_id:variant?.variant_id||"",sku:variant?.sku||product.sku||""};});
    const subtotal = money(form.lines.reduce((sum, line) => sum + (Number(line.quantity) || 0) * (Number(line.unit_cost) || 0), 0));
    const tax = Number(form.tax_amount) || 0;
    const submit = async (event) => {
        event.preventDefault();
        if (readOnly || saving.current || uploading) return;
        let payload;
        try {
            payload = buildPurchaseDraft(form, catalog);
            if (editing) {
                if (!Number.isInteger(editing.revision)) throw new Error("أعد تحميل مراجعة المسودة قبل تعديلها.");
                payload.expected_revision = editing.revision;
            }
        } catch (err) { setError(err.message); return; }
        saving.current = true; setBusy(true); setError("");
        try {
            if (editing) await api.put("/purchase-invoices/" + encodeURIComponent(editing.id), payload);
            else await api.post("/purchase-invoices", payload);
            toast.success("حُفظت المسودة دون قيد مالي أو حركة مخزون.");
            await onSaved();
            onClose();
        } catch (err) { setError(failureMessage(err, "تعذر حفظ المسودة.")); }
        finally { saving.current = false; setBusy(false); }
    };
    const uploadTaxEvidence = async (file) => {
        if (!file || readOnly || uploading) return;
        if (!form.supplier_counterparty_id || !form.invoice_number.trim()) {
            setError("اختر المورد وأدخل رقم الفاتورة قبل رفع المستند الضريبي."); return;
        }
        if (!["application/pdf", "image/png", "image/jpeg"].includes(file.type) || file.size > 10 * 1024 * 1024) {
            setError("ارفع PDF أو PNG أو JPEG بحجم لا يتجاوز 10 MiB."); return;
        }
        const body = new FormData();
        body.append("file", file);
        body.append("supplier_counterparty_id", form.supplier_counterparty_id);
        body.append("invoice_number", form.invoice_number.trim());
        setUploading(true); setError("");
        setForm((current) => ({ ...current, tax_evidence_ref: "", tax_evidence_verified: false }));
        try {
            const response = await api.post("/purchase-invoices/tax-evidence", body);
            if (!response.data.file_id) throw new Error("لم يثبت حفظ المستند الضريبي.");
            setForm((current) => ({ ...current, tax_evidence_ref: response.data.file_id, tax_evidence_verified: false }));
            setEvidenceFilename(response.data.filename || file.name);
        } catch (err) { setError(failureMessage(err, "تعذر رفع المستند الضريبي.")); }
        finally { setUploading(false); }
    };
    return <InventorySheet open onClose={onClose} title={readOnly?"عرض فاتورة الشراء":editing?"تعديل مسودة شراء":"فاتورة شراء جديدة"} description="ابحث عن الصنف وحدد خياراته، ثم أدخل الكمية والتكلفة. حفظ المسودة لا يغيّر المخزون أو ذمة المورد." busy={busy||uploading} wide footer={<><span data-testid="pinv-totals">الإجمالي <strong>{fmt(subtotal+tax)} SAR</strong></span>{!readOnly&&<button className={buttonCls} type="submit" form="native-purchase-form" disabled={busy||uploading} data-testid="pinv-save-draft">{uploading?"جارٍ رفع المستند…":busy?"جارٍ حفظ المسودة…":"حفظ المسودة"}</button>}</>}>
        <form id="native-purchase-form" onSubmit={submit} className="space-y-5 mz-native-invoices" data-testid="pinv-dialog" dir="rtl">
            {error&&<p role="alert" className="rounded-lg bg-rose-50 p-3 text-rose-800">{error}</p>}
            <fieldset disabled={busy||uploading||readOnly} className="space-y-5">
                <div className="grid gap-3 sm:grid-cols-2"><Field label="المورد"><select className={inputCls} value={form.supplier_counterparty_id} onChange={e=>set("supplier_counterparty_id",e.target.value)} data-testid="pinv-supplier"><option value="">اختر المورد</option>{suppliers.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select></Field><Field label="تاريخ الفاتورة"><input className={inputCls} type="date" value={form.invoice_date} onChange={e=>set("invoice_date",e.target.value)}/></Field></div>
                <Field label="رقم الفاتورة"><input className={inputCls} value={form.invoice_number} onChange={e=>set("invoice_number",e.target.value)} placeholder="اختياري في المسودة"/></Field>
                <h3 className="font-black text-lg">بنود الفاتورة</h3><div className="mz-purchase-lines">{form.lines.map((line,index)=>{
                    const product=productForLine(catalog,line),component=catalog.components.find(c=>valueId(c.resource_id??c.id)===valueId(line.resource_id));
                    const item=product||component;
                    const items=[...catalog.products.map(p=>({...p,picker_kind:"product",picker_id:"product:"+(p.product_id??p.id)})),...catalog.components.filter(c=>c.track_inventory&&c.status!=="inactive"&&(c.category_ids||[]).length&&(!line.category_id||c.category_ids.includes(line.category_id))).map(c=>({...c,id:c.resource_id??c.id,picker_id:"component:"+(c.resource_id??c.id),picker_kind:"component",sku:c.code}))];
                    return <section key={line.id||index} className="mz-purchase-line" data-testid={"pinv-line-"+index}>
                        <header><strong>البند {index+1}</strong>{!readOnly&&<div><button type="button" className="rounded border px-2 py-1" onClick={()=>set("lines",[...form.lines.slice(0,index+1),{...JSON.parse(JSON.stringify(line)),id:undefined,quantity:"1",cost_basis:"unit",line_total:line.unit_cost||""},...form.lines.slice(index+1)])} disabled={form.lines.length>=100}>نسخ البند</button>{form.lines.length>1&&<button type="button" className="text-rose-800" onClick={()=>set("lines",form.lines.filter((_,i)=>i!==index))}>إزالة</button>}</div>}</header>
                        {readOnly?<div className="mz-purchase-read-line"><InventoryProductPhoto product={item||{name:line.product_name}}/><div><strong>{line.product_name}</strong><p>{line.sku}</p><p className="mz-purchase-config-summary">{inventoryConfigDescription(line)}</p></div></div>:<>
                            <InventoryProductPicker items={items} selected={item} query={line.catalog_query||""} onQueryChange={query=>replaceLine(index,old=>({...old,catalog_query:query}))} onPick={(picked,query)=>chooseItem(index,picked,query)} testId={"pinv-line-"+index+"-search"} label={"بحث صنف البند "+(index+1)}/>
                            {line.item_type==="STOCK_COMPONENT"&&<Field label="تصنيف المكوّن"><select className={inputCls} data-testid={"pinv-line-"+index+"-category"} value={line.category_id} onChange={e=>replaceLine(index,old=>({...old,category_id:e.target.value,resource_id:"",product_name:"",sku:""}))}>{catalog.categories.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>}
                            {product&&line.purchase_configuration?<InventoryProductOptions product={product} config={line.purchase_configuration} index={index} variantId={line.variant_id} onChange={config=>configureItem(index,product,config)} onVariant={value=>replaceLine(index,old=>({...old,variant_id:value}))}/>:product&&(product.variants_required||product.variants?.length>0)&&<Field label="خيار المنتج"><select className={inputCls} value={line.variant_id||""} data-testid={"pinv-line-"+index+"-variant"} onChange={e=>replaceLine(index,old=>({...old,variant_id:e.target.value,sku:product.variants.find(v=>valueId(v.variant_id)===e.target.value)?.sku||""}))}><option value="">اختر النسخة المحددة</option>{product.variants.map(v=><option key={v.variant_id} value={v.variant_id}>{v.name} · {v.sku}</option>)}</select></Field>}
                            {product&&!line.purchase_configuration&&(product.options?.length?<button type="button" className="rounded-lg border px-3 py-2 text-emerald-800" onClick={()=>configureItem(index,product,inventoryConfiguration(product,line.variant_id))}>تحديد خيارات هذا البند</button>:<p className="mz-product-deferred">لا يحتوي تعريف هذا المنتج على حقول تخصيص. أضف خياراته من شاشة الأصناف عند الحاجة إلى أحرف أو اسم حسب الطلب.</p>)}
                        </>}
                        <div className="grid gap-3 sm:grid-cols-3"><Field label="الكمية"><input className={inputCls} type="number" min={line.item_type==="PRODUCT"?"1":"0.000001"} step={line.item_type==="PRODUCT"?"1":"0.000001"} value={line.quantity} data-testid={"pinv-line-"+index+"-quantity"} onChange={e=>replaceLine(index,old=>updatePurchaseLine(old,"quantity",e.target.value))}/></Field><Field label="تكلفة الوحدة قبل الضريبة"><input className={inputCls} type="number" min="0" step="0.000001" value={line.unit_cost} data-testid={"pinv-line-"+index+"-unit-cost"} onChange={e=>replaceLine(index,old=>updatePurchaseLine(old,"unit_cost",e.target.value))}/></Field><Field label="إجمالي البند قبل الضريبة"><input className={inputCls} type="number" min="0" step="0.01" value={line.line_total} data-testid={"pinv-line-"+index+"-total"} onChange={e=>replaceLine(index,old=>updatePurchaseLine(old,"line_total",e.target.value))}/></Field></div>
                    </section>;
                })}</div>
                {!readOnly&&<button type="button" className="w-full rounded-lg border px-4 py-3 font-bold" disabled={form.lines.length>=100} onClick={()=>set("lines",[...form.lines,freshLine()])}><Plus size={16} className="inline"/> إضافة بند</button>}
                <details open={!!form.tax_amount&&Number(form.tax_amount)>0||!form.inventory_account_id}><summary className="font-bold">الضريبة والحسابات والتفاصيل الإضافية</summary><div className="space-y-4 pt-4">
                    <div className="grid gap-3 md:grid-cols-2"><Field label="تاريخ الاستحقاق"><input className={inputCls} type="date" value={form.due_date||""} onChange={e=>set("due_date",e.target.value)}/></Field><Field label="مبلغ الضريبة"><input className={inputCls} type="number" min="0" step="0.01" value={form.tax_amount} onChange={e=>set("tax_amount",e.target.value)}/></Field></div>
                    <Field label="معالجة الضريبة"><select className={inputCls} value={form.tax_treatment} onChange={e=>set("tax_treatment",e.target.value)}><option value="none">لا توجد ضريبة</option><option value="non_deductible">غير قابلة للخصم — تضاف لتكلفة المخزون</option><option value="deductible">قابلة للخصم — مستند وربط معتمد</option></select></Field>
                    {form.tax_treatment==="deductible"&&<><Field label="الفاتورة الضريبية — PDF أو صورة حتى 10 MiB"><input className={inputCls} type="file" accept="application/pdf,image/png,image/jpeg" data-testid="pinv-tax-upload" onChange={e=>{uploadTaxEvidence(e.target.files?.[0]);e.target.value="";}}/>{form.tax_evidence_ref&&<span>مستند محفوظ: {evidenceFilename||"الفاتورة الضريبية المرفقة"}</span>}</Field><label className="flex gap-2 text-sm"><input type="checkbox" checked={form.tax_evidence_verified===true} disabled={!form.tax_evidence_ref} onChange={e=>set("tax_evidence_verified",e.target.checked)} data-testid="pinv-tax-attestation"/>راجعت المستند وأؤكد أنه فاتورة ضريبية صحيحة تخص هذا المورد وهذه الفاتورة.</label></>}
                    <div className="grid gap-3 md:grid-cols-2"><MappingSelect label="حساب المخزون" kind="inventory" catalog={catalog} value={form.inventory_account_id} onChange={value=>set("inventory_account_id",value)}/><MappingSelect label="حساب ذمة المورد" kind="supplier" catalog={catalog} value={form.supplier_account_id} onChange={value=>set("supplier_account_id",value)} disabled/>{form.tax_treatment==="deductible"&&<MappingSelect label="حساب ضريبة المدخلات" kind="input_vat" catalog={catalog} value={form.input_vat_account_id} onChange={value=>set("input_vat_account_id",value)}/>}</div><Field label="ملاحظات"><textarea className={inputCls} value={form.notes} onChange={e=>set("notes",e.target.value)}/></Field>
                </div></details>
            </fieldset>
        </form>
    </InventorySheet>;
}


const STATUS_LABEL = {
    unpaid: { label: "غير مسددة", tone: "bg-rose-50 text-rose-800 border-rose-200" },
    partial: { label: "مدفوعة جزئيًا", tone: "bg-amber-50 text-amber-800 border-amber-200" },
    paid: { label: "مسددة", tone: "bg-emerald-50 text-emerald-800 border-emerald-200" },
};

export function SupplierStatement({ cpId, open, onClose, onPayment }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        if (!open || !cpId) return;
        let active = true;
        setLoading(true); setData(null); setError("");
        api.get(`/purchase-invoices/supplier/${encodeURIComponent(cpId)}/statement`)
            .then((r) => { if (active) setData(r.data); })
            .catch((e) => { if (active) setError(failureMessage(e, "تعذر تحميل كشف المورد.")); })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [open, cpId]);

    if (!open) return null;
    return <InventorySheet open={open} onClose={onClose} title="كشف حساب المورد" description="فواتير المورد والمبالغ المسددة والرصيد المتبقي من العقود المحاسبية الحالية." wide>
        <div data-testid="pinv-statement-dialog" className="mz-native-invoices" dir="rtl">
                    {error ? <p role="alert" className="text-rose-800">{error}</p> : loading || !data ? (
                        <div className="text-center text-slate-500 text-sm py-8">جاري التحميل…</div>
                    ) : (
                        <>
                            <div className="mb-4">
                                <div className="text-xs text-slate-500">المورد</div>
                                <div className="text-base font-extrabold text-slate-900">{data.supplier.name}</div>
                                {data.ledger_backend === "v2" && <p className="mt-2 text-sm">الرصيد المستحق من الدفتر المحاسبي المعتمد، ويشمل الرصيد الافتتاحي. إجماليات الفواتير أدناه تخص فواتير الشراء المعتمدة في الدورة الحالية.</p>}
                                {onPayment && data.ledger_backend === "v2" && Number(data.unallocated_payable_amount) > 0 && <button type="button" className={buttonCls + " mt-3"} onClick={() => onPayment(cpId)} data-testid="pinv-pay-opening">سداد رصيد المورد غير المرتبط بفواتير الشراء</button>}
                            </div>
                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
                                <div className="rounded-lg bg-slate-50 border border-slate-200 p-3">
                                    <div className="text-[11px] text-slate-600 font-bold">إجمالي فواتير الشراء</div>
                                    <div className="num text-lg font-extrabold text-slate-900 mt-1">{fmt(data.totals.total_invoiced)} ر.س</div>
                                </div>
                                <div className="rounded-lg bg-emerald-50 border border-emerald-200 p-3">
                                    <div className="text-[11px] text-emerald-800 font-bold">إجمالي المسدَّد</div>
                                    <div className="num text-lg font-extrabold text-emerald-900 mt-1">{fmt(data.totals.total_paid)} ر.س</div>
                                </div>
                                <div className="rounded-lg bg-rose-50 border border-rose-200 p-3" data-testid="pinv-statement-balance">
                                    <div className="text-[11px] text-rose-800 font-bold">الرصيد المتبقي له</div>
                                    <div className="num text-lg font-extrabold text-rose-900 mt-1">{fmt(data.totals.balance_owed)} ر.س</div>
                                </div>
                            </div>
                            <div className="overflow-x-auto border border-slate-200 rounded-lg">
                                <table className="mezan-table w-full text-sm">
                                    <thead className="bg-slate-50 text-slate-600 text-xs">
                                        <tr>
                                            <th className="text-right p-2 font-bold">رقم</th>
                                            <th className="text-right p-2 font-bold">تاريخ</th>
                                            <th className="text-right p-2 font-bold">الإجمالي</th>
                                            <th className="text-right p-2 font-bold">المسدَّد</th>
                                            <th className="text-right p-2 font-bold">المتبقي</th>
                                            <th className="text-right p-2 font-bold">الحالة</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.invoices.length === 0 && (
                                            <tr><td colSpan={6} className="p-6 text-center text-slate-500 text-xs">لا توجد فواتير بعد</td></tr>
                                        )}
                                        {data.invoices.map((i) => {
                                            const s = STATUS_LABEL[i.status] || STATUS_LABEL.unpaid;
                                            return (
                                                <tr key={i.id} className="border-t border-slate-100 hover:bg-slate-50/60">
                                                    <td className="p-2 text-xs">{i.invoice_number || "—"}</td>
                                                    <td className="p-2 text-xs">{i.invoice_date}</td>
                                                    <td className="p-2 num text-xs font-bold text-slate-900">{fmt(i.total)}</td>
                                                    <td className="p-2 num text-xs text-emerald-700">{fmt(i.paid_amount)}</td>
                                                    <td className="p-2 num text-xs font-bold text-rose-700">{fmt(i.remaining_amount)}</td>
                                                    <td className="p-2"><span className={`px-2 py-0.5 rounded text-[11px] font-bold border ${s.tone}`}>{s.label}</span></td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
        </div>
    </InventorySheet>;
}

export function buildSupplierPayment(context, form) {
    if (!context?.operation_id || !Number.isInteger(context.expected_payment_revision)) throw new Error("أعد تحميل بيانات السداد.");
    const amount = String(form.amount || "").trim();
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0) throw new Error("أدخل مبلغًا موجبًا بدقة هللتين.");
    if (Number(amount) > Number(context.remaining_amount)) throw new Error("المبلغ يتجاوز الرصيد المستحق.");
    const bank = (context.banks || []).find((row) => row.id === form.paid_from_account_id);
    if (!bank) throw new Error("اختر حساب السداد المعتمد.");
    if (Number(amount) > Number(bank.balance)) throw new Error("رصيد حساب السداد غير كافٍ.");
    if (!form.payment_date) throw new Error("حدد تاريخ السداد.");
    return {
        operation_id: context.operation_id, expected_payment_revision: context.expected_payment_revision,
        amount, paid_from_account_id: bank.id, payment_date: form.payment_date, notes: form.notes.trim(),
    };
}

export function PaymentDialog({ invoiceId, supplierId, onClose, onSaved }) {
    const base = supplierId
        ? "/purchase-invoices/supplier/" + encodeURIComponent(supplierId)
        : "/purchase-invoices/" + encodeURIComponent(invoiceId);
    const [context, setContext] = useState(null);
    const [form, setForm] = useState({ amount: "", paid_from_account_id: "", payment_date: todaySA(), notes: "" });
    const [request, setRequest] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [loading, setLoading] = useState(true);
    const saving = useRef(false);
    useEffect(() => {
        let active = true;
        api.get(base + "/payment-context").then(({ data }) => {
            if (active) { setContext(data); setForm((old) => ({ ...old, amount: String(data.remaining_amount) })); }
        }).catch((err) => { if (active) setError(failureMessage(err, "تعذر التحقق من رصيد المورد.")); })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [base]);
    const submitPayment = async (event) => {
        event.preventDefault();
        if (saving.current || !context || loading) return;
        let payload = request;
        try { payload = payload || buildSupplierPayment(context, form); }
        catch (err) { setError(err.message); return; }
        // Keep the exact operation and fields after an uncertain response.
        // A retry must never create a new payment or change its amount.
        setRequest(payload); saving.current = true; setBusy(true); setError("");
        try {
            const { data } = await api.post(base + "/payments", payload);
            if (data.ok !== true || data.operation?.status !== "succeeded") throw new Error("لم يتأكد اكتمال السداد. أعد محاولة العملية نفسها.");
            toast.success("تم تسجيل سداد المورد.");
            await onSaved();
            onClose();
        } catch (err) { setError(failureMessage(err, "تعذر تأكيد نتيجة السداد. إعادة المحاولة تستخدم العملية نفسها.")); }
        finally { saving.current = false; setBusy(false); }
    };
    return <InventorySheet open onClose={onClose} title="سداد المورد" description="اختر الحساب والمبلغ والتاريخ. تعيد المحاولة العملية المحفوظة نفسها عند تعذر تأكيد النتيجة." busy={busy} footer={<button className={buttonCls} type="submit" form="native-supplier-payment-form" disabled={!context || busy || Number(context?.remaining_amount) <= 0} data-testid="pinv-payment-submit">{busy ? "جارٍ تأكيد السداد…" : request ? "إعادة محاولة السداد نفسه" : "تأكيد السداد"}</button>}>
        <form id="native-supplier-payment-form" onSubmit={submitPayment} className="space-y-5 mz-native-invoices" dir="rtl" data-testid="pinv-payment-dialog">
            {loading && <p>جارٍ التحقق من رصيد المورد والحسابات…</p>}
            {error && <p role="alert" className="rounded bg-rose-50 p-3 text-rose-800">{error}</p>}
            {context && <p>المستحق: {fmt(context.remaining_amount)} SAR</p>}
            <fieldset className="space-y-3" disabled={!context || busy || !!request}>
                <Field label="مبلغ السداد"><input className={inputCls} inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} data-testid="pinv-payment-amount" /></Field>
                <Field label="حساب السداد"><select className={inputCls} value={form.paid_from_account_id} onChange={(e) => setForm({ ...form, paid_from_account_id: e.target.value })} data-testid="pinv-payment-bank"><option value="">اختر الحساب</option>{(context?.banks || []).map((bank) => <option key={bank.id} value={bank.id}>{bank.name} — {fmt(bank.balance)} SAR</option>)}</select></Field>
                <Field label="تاريخ السداد"><input className={inputCls} type="date" value={form.payment_date} onChange={(e) => setForm({ ...form, payment_date: e.target.value })} data-testid="pinv-payment-date" /></Field>
                <Field label="ملاحظات"><input className={inputCls} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
            </fieldset>
            {request && error && <p className="text-sm">بيانات المحاولة محفوظة. إعادة المحاولة لا تنشئ سدادًا ثانيًا.</p>}
        </form>
    </InventorySheet>;
}


// ── Main page ───────────────────────────────────────────────────────

function operationStatus(invoice) {
    return invoice?.operation?.status || invoice?.approval_operation?.status || "";
}

function invoiceStateLabel(invoice) {
    if (invoice.schema_version !== "g47-v1") return "سابقة — للقراءة فقط";
    if (invoice.state === "approved") return "معتمدة ومستلمة";
    if (invoice.state === "draft") return "مسودة — بلا أثر مالي";
    return "عملية اعتماد تحتاج متابعة";
}

export function ApprovalDialog({ invoice: initialInvoice, catalog, onClose, onSaved, readOnly=false }) {
    const [invoice, setInvoice] = useState(initialInvoice);
    const [receipts, setReceipts] = useState(() => initialInvoice.operation?.request?.receipts || initialInvoice.lines.map((line) => ({
        line_id: line.id, location_id: "", scanned_location_barcode: "", preparation_state: line.purchase_configuration?.state === "ready" ? "ready_complete" : "requires_preparation",
    })));
    const [busy, setBusy] = useState(false);
    const [uncertain, setUncertain] = useState(false);
    const [error, setError] = useState("");
    const submitting = useRef(false);
    const status = operationStatus(invoice);
    const frozenRequest = invoice.operation?.request;
    const resumable = invoice.state === "approving" && ["pending", "failed", "recovery_required"].includes(status)
        && frozenRequest?.operation_id === invoice.approval_operation_id && frozenRequest?.expected_revision === invoice.revision;
    const blockedOperation = uncertain || (!resumable && !!status && !["failed", "succeeded"].includes(status));
    const updateReceipt = (index, key, value) => setReceipts((current) => current.map((row, i) =>
        i === index ? { ...row, [key]: value } : row));
    const refresh = async () => {
        const response = await api.get("/purchase-invoices/" + encodeURIComponent(invoice.id));
        const latest = response.data.invoice || response.data;
        if (latest.approval_operation_id !== initialInvoice.approval_operation_id || latest.revision !== initialInvoice.revision) {
            setUncertain(true);
            setError("تغيرت مراجعة الفاتورة؛ أغلق هذه النافذة وأعد فتح الفاتورة لمراجعة جميع البنود.");
        } else setUncertain(false);
        setInvoice(latest);
        if (latest.operation?.request?.receipts) setReceipts(latest.operation.request.receipts);
        await onSaved();
        return latest;
    };
    const submit = async (event) => {
        event.preventDefault();
        if (readOnly || submitting.current || blockedOperation || (!isManagedDraft(invoice) && !resumable)) return;
        let payload;
        try { payload = resumable ? frozenRequest : buildFullPurchaseApproval(invoice, receipts, catalog); }
        catch (err) { setError(err.message); return; }
        submitting.current = true; setBusy(true); setError("");
        try {
            const response = await api.post("/purchase-invoices/" + encodeURIComponent(invoice.id) + "/approve-receive", payload);
            const latest = response.data.invoice || response.data;
            if (latest.state === "approved" && (response.data.operation?.status || operationStatus(latest)) === "succeeded") {
                toast.success("تم اعتماد الفاتورة واستلام جميع بنودها.");
                await onSaved(); onClose();
            } else {
                setUncertain(true);
                setError("لم يثبت اكتمال العملية بعد. تحقق من حالتها قبل أي محاولة أخرى.");
                await refresh();
            }
        } catch (err) {
            setError(failureMessage(err, "تعذر تأكيد نتيجة الاعتماد؛ تحقق من حالة العملية."));
            setUncertain(true);
            try { await refresh(); } catch (_) { /* Keep the same operation locked until its state is known. */ }
        } finally { submitting.current = false; setBusy(false); }
    };
    return <InventorySheet open onClose={onClose} title="اعتماد واستلام الفاتورة" description="راجع جميع الكميات ومواقع الاستلام وباركود الخانات. الاعتماد يثبت المخزون والقيد وذمة المورد وفق القواعد الحالية." wide busy={busy} footer={<><span>{invoice.lines.length} بنود · الكميات كاملة</span>{!readOnly&&(resumable?<button type="submit" form="native-receipt-form" className={buttonCls} disabled={busy||blockedOperation} data-testid="pinv-resume-approval">إعادة محاولة الاستلام</button>:<button type="submit" form="native-receipt-form" className={buttonCls} disabled={busy||blockedOperation||!isManagedDraft(invoice)} data-testid="pinv-approve-receive">اعتماد واستلام جميع البنود</button>)}</>}>
        <form id="native-receipt-form" onSubmit={submit} className="space-y-5 mz-native-invoices" dir="rtl" data-testid="pinv-approval-dialog">
            {readOnly&&<p role="status">عرض حالة الاستلام فقط؛ لا تتوفر صلاحية الاعتماد.</p>}
            {error&&<p role="alert" className="rounded-lg bg-rose-50 p-3 text-rose-800">{error}</p>}
            {status&&<p role="status">حالة العملية: {status}</p>}{resumable&&<p className="mz-product-deferred">تُعاد العملية المحفوظة بنفس البنود والكميات والخانات؛ لا تُنشأ عملية استلام جديدة.</p>}
            <fieldset disabled={readOnly||busy||blockedOperation||!isManagedDraft(invoice)} className="mz-purchase-lines">
                {invoice.lines.map((line,index)=><section key={line.id} className="mz-purchase-line">
                    <div className="mz-purchase-read-line"><InventoryProductPhoto product={productForLine(catalog,line)||{name:line.product_name}}/><div><h3 className="font-bold">{line.product_name}</h3><p className="text-sm">الرقم المخزني <bdi>{line.code||line.sku}</bdi> · الكمية كاملة: {line.quantity}</p><p className="mz-purchase-config-summary">{inventoryConfigDescription(line)}</p></div></div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label={"خانة البند "+(index+1)}><select className={inputCls} value={receipts[index]?.location_id||""} onChange={e=>updateReceipt(index,"location_id",e.target.value)} data-testid={"pinv-receipt-"+index+"-location"}><option value="">اختر الخانة</option>{catalog.locations.map(row=><option key={row.id} value={row.id}>{row.display_code||row.code||row.name}{row.warehouse_name?" · "+row.warehouse_name:""}</option>)}</select></Field>
                        <Field label={"باركود خانة البند "+(index+1)}><input className={inputCls} value={receipts[index]?.scanned_location_barcode||""} onChange={e=>updateReceipt(index,"scanned_location_barcode",e.target.value)} data-testid={"pinv-receipt-"+index+"-barcode"}/></Field>
                    </div>
                    <Field label={"تجهيز البند "+(index+1)}><select className={inputCls} disabled={!!line.purchase_configuration} value={receipts[index]?.preparation_state||"requires_preparation"} onChange={e=>updateReceipt(index,"preparation_state",e.target.value)}><option value="requires_preparation">يحتاج تجهيزًا</option><option value="ready_complete">جاهز كاملًا</option></select></Field>
                </section>)}
            </fieldset>
            <button type="button" disabled={busy} className="rounded-lg border px-4 py-2" onClick={async()=>{
                setBusy(true);
                try{await refresh();}catch(err){setUncertain(true);setError(failureMessage(err,"تعذر التحقق من العملية."));}
                finally{setBusy(false);}
            }}>تحديث حالة العملية</button>
        </form>
    </InventorySheet>;
}

export default function PurchaseInvoices({accessMode=null}) {
    const [invoices, setInvoices] = useState([]);
    const [suppliers, setSuppliers] = useState([]);
    const [catalog, setCatalog] = useState(EMPTY_CATALOG);
    const canWrite=accessMode!=="read"&&catalog.can_write===true;
    const canPay=accessMode!=="read"&&catalog.can_pay===true;
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [dialog, setDialog] = useState(null);
    const [approval, setApproval] = useState(null);
    const [payment, setPayment] = useState(null);
    const [statementSupplier, setStatementSupplier] = useState(null);
    const [search, setSearch] = useState("");
    const load = async () => {
        setLoading(true); setError("");
        try {
            const [inv, cat] = await Promise.all([
                api.get("/purchase-invoices?limit=500"),
                api.get("/purchase-invoices/catalog"),
            ]);
            setInvoices(inv.data.items || []);
            setSuppliers((cat.data.supplier_identities || []).filter((row) =>
                row.id && row.id === row.entity_id && row.id === row.counterparty_id));
            setCatalog({ ...EMPTY_CATALOG, ...cat.data });
        } catch (err) { setError(failureMessage(err, "تعذر تحميل فواتير الشراء.")); }
        finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);
    const selectedId = new URLSearchParams(window.location.search).get("invoice");
    const filtered = useMemo(() => invoices.filter((row) =>
        (!selectedId || valueId(row.id) === selectedId)
        && (row.invoice_number + " " + row.supplier_name).toLowerCase().includes(search.trim().toLowerCase())),
    [invoices, selectedId, search]);
    const openApproval = async (row) => {
        try {
            const response = await api.get("/purchase-invoices/" + encodeURIComponent(row.id));
            const latest = response.data.invoice || response.data;
            if (latest.schema_version !== "g47-v1" || latest.state === "approved") { setDialog({ invoice: latest }); return; }
            setApproval(latest);
        } catch (err) { setError(failureMessage(err, "تعذر تحميل الفاتورة.")); }
    };
    return <InventorySuiteFrame active="purchases"><div dir="rtl" className="space-y-5 mz-native-invoices" data-testid="purchase-invoices-page">
        <InventoryPageHeader title="المشتريات" description="احفظ مسودة، ثم راجع الاستلام والخانات. تظهر حالة كل فاتورة ومستحقات المورد في مكان واحد.">
            <button className={buttonCls+" is-primary"} disabled={!canWrite || loading || !!error} onClick={() => setDialog({ invoice: null })} data-testid="pinv-new-btn"><Plus size={16} className="inline" /> مسودة شراء جديدة</button>
        </InventoryPageHeader>
        {!canWrite&&<p role="status" className="rounded-lg bg-amber-50 p-3 text-amber-900">عرض المشتريات فقط؛ إنشاء المسودات والاستلام والسداد يتطلب صلاحية التعديل.</p>}
        <Link to="/inventory-receiving-v2" className="text-sm text-emerald-800 underline">متابعة الاستلام والمخزون</Link>
        {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-rose-800">{error}<button type="button" className="mr-3 underline" onClick={load}>إعادة التحميل</button></p>}
        <label className="block text-sm">بحث بالفاتورة أو المورد<input className={inputCls} value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        {selectedId && <a href="/purchase-invoices" className="text-sm underline">عرض جميع الفواتير</a>}
        {loading ? <p>جارٍ التحميل…</p> : <div className="overflow-auto rounded-xl border bg-white"><table className="w-full text-right text-sm mz-purchase-table">
            <thead><tr>{["الفاتورة", "المورد", "التاريخ", "الإجمالي SAR", "الحالة", "الإجراءات"].map((label) => <th key={label} className="p-3">{label}</th>)}</tr></thead>
            <tbody>{filtered.map((row) => <tr key={row.id} className="border-t" data-testid={"pinv-row-" + row.id}>
                <td className="p-3" data-label="الفاتورة">{row.invoice_number || "—"}</td><td className="p-3" data-label="المورد"><button className="text-emerald-800 underline" onClick={() => setStatementSupplier(row.supplier_counterparty_id)}>{row.supplier_name || "—"}</button></td><td className="p-3" data-label="التاريخ">{row.invoice_date}</td><td className="p-3" data-label="الإجمالي SAR">{fmt(row.total)}</td>
                <td className="p-3" data-label="الحالة">{invoiceStateLabel(row)}{row.state === "approved" && <div className="text-xs">{STATUS_LABEL[row.payment_status || row.status]?.label || "غير مسددة"} · المتبقي {fmt(row.remaining_amount)}</div>}{operationStatus(row) && <div className="text-xs">{operationStatus(row)}</div>}</td>
                <td className="flex flex-wrap gap-2 p-3" data-label="الإجراءات"><button className="rounded border px-3 py-2" onClick={() => setDialog({ invoice: row })} data-testid={"pinv-open-" + row.id}><FileText className="inline" size={16} /> {isManagedDraft(row)&&canWrite ? "تعديل المسودة" : "عرض"}</button>
                    {canWrite && isManagedDraft(row) && <button className={buttonCls} onClick={() => openApproval(row)} data-testid={"pinv-approve-" + row.id}>اعتماد واستلام كامل</button>}
                    {canPay && row.schema_version === "g47-v1" && row.state === "approved" && Number(row.remaining_amount) > 0 && <button className={buttonCls} onClick={() => setPayment({ invoiceId: row.id })} data-testid={"pinv-pay-" + row.id}>سداد المورد</button>}
                    {row.schema_version === "g47-v1" && !["draft", "approved"].includes(row.state) && <button className="rounded border px-3 py-2" onClick={() => openApproval(row)}>متابعة حالة الاعتماد</button>}
                </td>
            </tr>)}</tbody>
        </table>{!filtered.length && <p className="p-8 text-center">لا توجد فواتير مطابقة.</p>}</div>}
        {dialog && <InvoiceDialog suppliers={suppliers} catalog={catalog} editing={dialog.invoice} readOnly={!canWrite} onClose={() => setDialog(null)} onSaved={load} />}
        {approval && <ApprovalDialog invoice={approval} catalog={catalog} readOnly={!canWrite} onClose={() => setApproval(null)} onSaved={load} />}
        <SupplierStatement cpId={statementSupplier} open={!!statementSupplier} onClose={() => setStatementSupplier(null)} onPayment={canWrite?(supplierId) => { setStatementSupplier(null); setPayment({ supplierId }); }:undefined} />
        {canWrite && payment && <PaymentDialog {...payment} onClose={() => setPayment(null)} onSaved={load} />}
    </div></InventorySuiteFrame>;
}
