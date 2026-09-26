/**
 * Purchase drafts are non-financial. Approval receives every line together.
 * Legacy invoices remain read-only; operation identities come from the server.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Receipt, Plus, Trash, FileText } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import { todaySA } from "../lib/dates";

const inputCls = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100";
const buttonCls = "rounded-lg bg-violet-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-50";
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
    if (!form.invoice_date) throw new Error("حدد تاريخ الفاتورة.");
    if (!form.lines.length) throw new Error("أضف بندًا واحدًا على الأقل.");
    const lines = form.lines.map((line) => {
        const identity = canonicalLine(line, catalog);
        const quantity = Number(line.quantity);
        const unitCost = Number(line.unit_cost);
        if (line.quantity === "" || !Number.isFinite(quantity) || quantity <= 0) throw new Error("أدخل كمية موجبة لكل بند.");
        if (line.item_type === "PRODUCT" && !Number.isInteger(quantity)) throw new Error("كمية المنتج يجب أن تكون عددًا صحيحًا.");
        if (line.unit_cost === "" || !Number.isFinite(unitCost) || unitCost < 0) throw new Error("أدخل تكلفة وحدة صحيحة لكل بند.");
        if (Number(unitCost.toFixed(6)) !== unitCost || Number(quantity.toFixed(6)) !== quantity) throw new Error("دقة الكمية والتكلفة لا تتجاوز ست خانات عشرية.");
        return { ...(line.id ? { id: line.id } : {}), ...identity, quantity, unit_cost: unitCost };
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
        return {
            line_id: line.id, quantity: Number(line.quantity), location_id: location.id,
            scanned_location_barcode: barcode, preparation_state: receipt.preparation_state,
            specifications: {},
        };
    });
    return { expected_revision: invoice.revision, operation_id: invoice.approval_operation_id, receipts: result };
}

function failureMessage(error, fallback) {
    const detail = error.response?.data?.detail;
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

export function InvoiceDialog({ suppliers, catalog, editing, onClose, onSaved }) {
    const readOnly = !!editing && !isManagedDraft(editing);
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
        ...(["supplier_counterparty_id", "invoice_number"].includes(key)
            ? { tax_evidence_ref: "", tax_evidence_verified: false } : {}),
    }));
    const replaceLine = (index, transform) => setForm((current) => ({
        ...current, lines: current.lines.map((line, i) => i === index ? transform(line) : line),
    }));
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
    return <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4" data-testid="pinv-dialog">
        <form onSubmit={submit} className="mx-auto my-6 max-w-5xl space-y-5 rounded-xl bg-white p-5" dir="rtl">
            <div className="flex items-center justify-between"><h2 className="text-xl font-black">{readOnly ? "عرض الفاتورة — للقراءة فقط" : editing ? "تعديل مسودة شراء" : "مسودة شراء جديدة"}</h2><button type="button" onClick={onClose} disabled={busy}>إغلاق</button></div>
            <p className="text-sm text-slate-600">{readOnly ? "الفواتير القديمة والمعتمدة لا تُعدل أو تُستلم مجددًا من هذا المسار." : "حفظ المسودة لا ينشئ التزامًا أو قيدًا أو مخزونًا. الاعتماد والاستلام الكامل إجراء مستقل بعد مراجعتها."}</p>
            {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-rose-800">{error}</p>}
            <fieldset disabled={busy || uploading || readOnly} className="space-y-5">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    <Field label="المورد"><select className={inputCls} value={form.supplier_counterparty_id} onChange={(event) => set("supplier_counterparty_id", event.target.value)} data-testid="pinv-supplier"><option value="">اختر المورد</option>{suppliers.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
                    <Field label="رقم الفاتورة"><input className={inputCls} value={form.invoice_number} onChange={(event) => set("invoice_number", event.target.value)} /></Field>
                    <Field label="تاريخ الفاتورة"><input className={inputCls} type="date" value={form.invoice_date} onChange={(event) => set("invoice_date", event.target.value)} /></Field>
                    <Field label="تاريخ الاستحقاق"><input className={inputCls} type="date" value={form.due_date || ""} onChange={(event) => set("due_date", event.target.value)} /></Field>
                </div>
                {form.lines.map((line, index) => {
                    const product = productForLine(catalog, line);
                    return <section className="space-y-3 rounded-xl border border-slate-200 p-4" key={line.id || index} data-testid={"pinv-line-" + index}>
                        <div className="flex justify-between"><h3 className="font-black">بند {index + 1}</h3>{!readOnly && <button type="button" aria-label={"حذف بند " + (index + 1)} disabled={form.lines.length === 1} onClick={() => set("lines", form.lines.filter((_, i) => i !== index))}><Trash size={18} /></button>}</div>
                        {readOnly ? <p>{line.product_name} · {line.sku || "بدون SKU"}</p> : <div className="grid gap-3 md:grid-cols-3">
                            <Field label="نوع البند"><select className={inputCls} value={line.item_type} data-testid={"pinv-line-" + index + "-type"} onChange={(event) => replaceLine(index, (old) => ({ ...freshLine(), ...(old.id ? { id: old.id } : {}), item_type: event.target.value }))}><option value="PRODUCT">منتج</option><option value="STOCK_COMPONENT">مكوّن مخزني</option></select></Field>
                            {line.item_type === "PRODUCT" ? <>
                                <Field label="بحث بالاسم أو SKU أو barcode"><input className={inputCls} value={line.catalog_query || ""} data-testid={"pinv-line-" + index + "-search"} onChange={(event) => replaceLine(index, (old) => ({ ...old, catalog_query: event.target.value }))} /></Field>
                                <Field label="المنتج"><select className={inputCls} value={line.product_id || ""} data-testid={"pinv-line-" + index + "-product"} onChange={(event) => replaceLine(index, (old) => {
                                    const row = catalog.products.find((item) => valueId(item.product_id ?? item.id) === event.target.value);
                                    return { ...old, product_id: event.target.value, variant_id: "", product_name: row?.name || "", sku: row?.sku || "" };
                                })}><option value="">اختر المنتج</option>{catalog.products.filter((row) => valueId(row.product_id ?? row.id) === valueId(line.product_id) || [row.name, row.sku, row.barcode, ...(row.variants || []).flatMap((variant) => [variant.name, variant.sku, variant.barcode])].some((value) => String(value || "").toLowerCase().includes((line.catalog_query || "").trim().toLowerCase()))).map((row) => <option key={row.product_id ?? row.id} value={row.product_id ?? row.id}>{row.name} · {row.sku || "بدون SKU"} · {row.barcode || ""}</option>)}</select></Field>
                                {(product?.variants_required || product?.variants?.length > 0) && <Field label="خيار المنتج"><select className={inputCls} value={line.variant_id || ""} data-testid={"pinv-line-" + index + "-variant"} onChange={(event) => replaceLine(index, (old) => ({ ...old, variant_id: event.target.value, sku: product.variants.find((row) => valueId(row.variant_id) === event.target.value)?.sku || "" }))}><option value="">اختر الخيار المحدد</option>{(product.variants || []).map((row) => <option key={row.variant_id} value={row.variant_id}>{row.name || row.sku || row.variant_id}</option>)}</select></Field>}
                            </> : <>
                                <Field label="تصنيف المكوّن"><select className={inputCls} value={line.category_id || ""} data-testid={"pinv-line-" + index + "-category"} onChange={(event) => replaceLine(index, (old) => ({ ...old, category_id: event.target.value, resource_id: "", product_name: "", sku: "" }))}><option value="">اختر التصنيف</option>{catalog.categories.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
                                <Field label="المكوّن المخزني"><select className={inputCls} value={line.resource_id || ""} disabled={!line.category_id} data-testid={"pinv-line-" + index + "-component"} onChange={(event) => replaceLine(index, (old) => {
                                    const row = purchasableComponents(catalog, old.category_id).find((item) => valueId(item.resource_id ?? item.id) === event.target.value);
                                    return { ...old, resource_id: event.target.value, product_name: row?.name || "", sku: row?.code || "" };
                                })}><option value="">اختر المكوّن</option>{purchasableComponents(catalog, line.category_id).map((row) => <option key={row.resource_id ?? row.id} value={row.resource_id ?? row.id}>{row.name} · {row.code}</option>)}</select></Field>
                            </>}
                        </div>}
                        <div className="grid gap-3 sm:grid-cols-4">
                            <Field label="SKU / رمز المكوّن"><input className={inputCls} value={line.sku || ""} readOnly /></Field>
                            <Field label="الكمية"><input className={inputCls} type="number" min={line.item_type === "PRODUCT" ? "1" : "0.000001"} step={line.item_type === "PRODUCT" ? "1" : "0.000001"} value={line.quantity} data-testid={"pinv-line-" + index + "-quantity"} onChange={(event) => replaceLine(index, (old) => updatePurchaseLine(old, "quantity", event.target.value))} /></Field>
                            <Field label="تكلفة الوحدة قبل الضريبة"><input className={inputCls} type="number" min="0" step="0.000001" value={line.unit_cost} data-testid={"pinv-line-" + index + "-unit-cost"} onChange={(event) => replaceLine(index, (old) => updatePurchaseLine(old, "unit_cost", event.target.value))} /></Field>
                            <Field label="إجمالي البند قبل الضريبة"><input className={inputCls} type="number" min="0" step="0.01" value={line.line_total} data-testid={"pinv-line-" + index + "-total"} onChange={(event) => replaceLine(index, (old) => updatePurchaseLine(old, "line_total", event.target.value))} /></Field>
                        </div>
                    </section>;
                })}
                {!readOnly && <button type="button" className="rounded-lg border px-4 py-2 font-bold" onClick={() => set("lines", [...form.lines, freshLine()])}><Plus size={16} className="inline" /> إضافة بند</button>}
                <div className="grid gap-3 md:grid-cols-3">
                    <Field label="مبلغ الضريبة"><input className={inputCls} type="number" min="0" step="0.01" value={form.tax_amount} onChange={(event) => set("tax_amount", event.target.value)} /></Field>
                    <Field label="معالجة الضريبة"><select className={inputCls} value={form.tax_treatment} onChange={(event) => set("tax_treatment", event.target.value)}><option value="none">لا توجد ضريبة</option><option value="non_deductible">غير قابلة للخصم — تضاف لتكلفة المخزون</option><option value="deductible">قابلة للخصم — مستند وربط معتمد</option></select></Field>
                    {form.tax_treatment === "deductible" && <Field label="الفاتورة الضريبية — PDF أو صورة حتى 10 MiB"><input className={inputCls} type="file" accept="application/pdf,image/png,image/jpeg" data-testid="pinv-tax-upload" onChange={(event) => { uploadTaxEvidence(event.target.files?.[0]); event.target.value = ""; }} />{form.tax_evidence_ref && <span className="block text-xs text-emerald-800">مستند محفوظ: {evidenceFilename || "الفاتورة الضريبية المرفقة"}</span>}</Field>}
                </div>
                {form.tax_treatment === "deductible" && <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={form.tax_evidence_verified === true} disabled={!form.tax_evidence_ref} onChange={(event) => set("tax_evidence_verified", event.target.checked)} data-testid="pinv-tax-attestation" />راجعت المستند وأؤكد أنه فاتورة ضريبية صحيحة تخص هذا المورد وهذه الفاتورة.</label>}
                <div className="grid gap-3 md:grid-cols-3">
                    <MappingSelect label="حساب المخزون" kind="inventory" catalog={catalog} value={form.inventory_account_id} onChange={(value) => set("inventory_account_id", value)} />
                    <MappingSelect label="حساب ذمة المورد" kind="supplier" catalog={catalog} value={form.supplier_account_id} onChange={(value) => set("supplier_account_id", value)} />
                    {form.tax_treatment === "deductible" && <MappingSelect label="حساب ضريبة المدخلات" kind="input_vat" catalog={catalog} value={form.input_vat_account_id} onChange={(value) => set("input_vat_account_id", value)} />}
                </div>
                <Field label="ملاحظات"><textarea className={inputCls} value={form.notes} onChange={(event) => set("notes", event.target.value)} /></Field>
            </fieldset>
            <p className="rounded-lg bg-slate-50 p-3 font-bold" data-testid="pinv-totals">قبل الضريبة: {fmt(subtotal)} · الضريبة: {fmt(tax)} · الإجمالي: {fmt(subtotal + tax)} SAR</p>
            {!readOnly && <button className={buttonCls} type="submit" disabled={busy || uploading} data-testid="pinv-save-draft">{uploading ? "جارٍ رفع المستند…" : busy ? "جارٍ حفظ المسودة…" : "حفظ المسودة"}</button>}
        </form>
    </div>;
}

const STATUS_LABEL = {
    unpaid: { label: "غير مسددة", tone: "bg-rose-50 text-rose-800 border-rose-200" },
    partial: { label: "مدفوعة جزئيًا", tone: "bg-amber-50 text-amber-800 border-amber-200" },
    paid: { label: "مسددة", tone: "bg-emerald-50 text-emerald-800 border-emerald-200" },
};

function SupplierStatement({ cpId, open, onClose }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!open || !cpId) return;
        setLoading(true);
        api.get(`/purchase-invoices/supplier/${cpId}/statement`)
            .then((r) => setData(r.data))
            .catch((e) => toast.error(formatApiErrorDetail(e.response?.data?.detail)))
            .finally(() => setLoading(false));
    }, [open, cpId]);

    if (!open) return null;
    return (
        <div className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm flex items-start justify-center overflow-y-auto p-4" data-testid="pinv-statement-dialog">
            <div dir="rtl" className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
                <div className="flex items-center justify-between p-5 border-b border-slate-100">
                    <h2 className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                        <FileText size={22} weight="duotone" className="text-violet-700" />
                        كشف حساب مورد
                    </h2>
                    <button onClick={onClose} className="text-slate-500 hover:text-slate-900 text-2xl">×</button>
                </div>
                <div className="p-5">
                    {loading || !data ? (
                        <div className="text-center text-slate-500 text-sm py-8">جاري التحميل…</div>
                    ) : (
                        <>
                            <div className="mb-4">
                                <div className="text-xs text-slate-500">المورد</div>
                                <div className="text-base font-extrabold text-slate-900">{data.supplier.name}</div>
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
            </div>
        </div>
    );
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

export function ApprovalDialog({ invoice: initialInvoice, catalog, onClose, onSaved }) {
    const [invoice, setInvoice] = useState(initialInvoice);
    const [receipts, setReceipts] = useState(() => initialInvoice.operation?.request?.receipts || initialInvoice.lines.map((line) => ({
        line_id: line.id, location_id: "", scanned_location_barcode: "", preparation_state: "requires_preparation",
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
        if (submitting.current || blockedOperation || (!isManagedDraft(invoice) && !resumable)) return;
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
    return <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4" data-testid="pinv-approval-dialog">
        <form onSubmit={submit} className="mx-auto my-6 max-w-4xl space-y-4 rounded-xl bg-white p-5" dir="rtl">
            <div className="flex justify-between"><h2 className="text-xl font-black">اعتماد واستلام كامل الفاتورة</h2><button type="button" onClick={onClose} disabled={busy}>إغلاق</button></div>
            <p className="text-sm text-slate-600">تُستلم جميع الكميات أدناه في عملية واحدة مرتبطة بالقيد وذمة المورد. راجع الخانات والباركود قبل الاعتماد.</p>
            {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-rose-800">{error}</p>}
            {status && <p role="status">حالة العملية: {status}</p>}
            <fieldset disabled={busy || blockedOperation || !isManagedDraft(invoice)} className="space-y-3">
                {invoice.lines.map((line, index) => <section key={line.id} className="space-y-3 rounded-lg border p-3">
                    <h3 className="font-bold">{line.product_name} · {line.code || line.sku} · الكمية كاملة: {line.quantity}</h3>
                    <div className="grid gap-3 md:grid-cols-3">
                        <Field label={"خانة البند " + (index + 1)}><select className={inputCls} value={receipts[index]?.location_id || ""} onChange={(event) => updateReceipt(index, "location_id", event.target.value)} data-testid={"pinv-receipt-" + index + "-location"}><option value="">اختر الخانة</option>{catalog.locations.map((row) => <option key={row.id} value={row.id}>{row.code} · {row.warehouse_id}</option>)}</select></Field>
                        <Field label={"باركود خانة البند " + (index + 1)}><input className={inputCls} value={receipts[index]?.scanned_location_barcode || ""} onChange={(event) => updateReceipt(index, "scanned_location_barcode", event.target.value)} data-testid={"pinv-receipt-" + index + "-barcode"} /></Field>
                        <Field label={"تجهيز البند " + (index + 1)}><select className={inputCls} value={receipts[index]?.preparation_state || "requires_preparation"} onChange={(event) => updateReceipt(index, "preparation_state", event.target.value)}><option value="requires_preparation">يحتاج تجهيزًا</option><option value="ready_complete">جاهز كاملًا</option></select></Field>
                    </div>
                </section>)}
                <button type="submit" className={buttonCls} data-testid="pinv-approve-receive">اعتماد واستلام جميع البنود</button>
            </fieldset>
            {resumable && <button type="submit" className={buttonCls} disabled={busy || blockedOperation} data-testid="pinv-resume-approval">إعادة محاولة العملية المحفوظة دون تغيير البنود أو الخانات</button>}
            <button type="button" disabled={busy} className="rounded-lg border px-4 py-2" onClick={async () => {
                setBusy(true);
                try { await refresh(); } catch (err) { setUncertain(true); setError(failureMessage(err, "تعذر التحقق من العملية.")); }
                finally { setBusy(false); }
            }}>تحديث حالة العملية</button>
        </form>
    </div>;
}

export default function PurchaseInvoices() {
    const [invoices, setInvoices] = useState([]);
    const [suppliers, setSuppliers] = useState([]);
    const [catalog, setCatalog] = useState(EMPTY_CATALOG);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [dialog, setDialog] = useState(null);
    const [approval, setApproval] = useState(null);
    const [statementSupplier, setStatementSupplier] = useState(null);
    const [search, setSearch] = useState("");
    const load = async () => {
        setLoading(true); setError("");
        try {
            const [inv, cp, cat] = await Promise.all([
                api.get("/purchase-invoices?limit=500"), api.get("/counterparties?kind=supplier"),
                api.get("/purchase-invoices/catalog"),
            ]);
            setInvoices(inv.data.items || []);
            setSuppliers((cp.data.items || []).filter((row) => row.kind === "supplier" || row.kind === "general"));
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
    return <div dir="rtl" className="space-y-5" data-testid="purchase-invoices-page">
        <header className="flex flex-wrap justify-between gap-3">
            <div><h1 className="flex items-center gap-2 text-2xl font-black"><Receipt size={28} />فواتير المشتريات</h1>
                <p className="mt-2 text-sm text-slate-600">احفظ مسودة ثم راجعها لاعتماد الفاتورة واستلام جميع كمياتها. الفواتير السابقة للقراءة فقط.</p></div>
            <button className={buttonCls} disabled={loading || !!error} onClick={() => setDialog({ invoice: null })} data-testid="pinv-new-btn"><Plus size={16} className="inline" /> مسودة شراء جديدة</button>
        </header>
        <Link to="/inventory-receiving-v2" className="text-sm text-violet-700 underline">مساحة المخزون والاستلام</Link>
        {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-rose-800">{error}<button type="button" className="mr-3 underline" onClick={load}>إعادة التحميل</button></p>}
        <label className="block text-sm">بحث بالفاتورة أو المورد<input className={inputCls} value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        {selectedId && <a href="/purchase-invoices" className="text-sm underline">عرض جميع الفواتير</a>}
        {loading ? <p>جارٍ التحميل…</p> : <div className="overflow-auto rounded-xl border bg-white"><table className="w-full text-right text-sm">
            <thead><tr>{["الفاتورة", "المورد", "التاريخ", "الإجمالي SAR", "الحالة", "الإجراءات"].map((label) => <th key={label} className="p-3">{label}</th>)}</tr></thead>
            <tbody>{filtered.map((row) => <tr key={row.id} className="border-t" data-testid={"pinv-row-" + row.id}>
                <td className="p-3">{row.invoice_number || "—"}</td><td className="p-3"><button className="text-violet-700 underline" onClick={() => setStatementSupplier(row.supplier_counterparty_id)}>{row.supplier_name || "—"}</button></td><td className="p-3">{row.invoice_date}</td><td className="p-3">{fmt(row.total)}</td>
                <td className="p-3">{invoiceStateLabel(row)}{operationStatus(row) && <div className="text-xs">{operationStatus(row)}</div>}</td>
                <td className="flex flex-wrap gap-2 p-3"><button className="rounded border px-3 py-2" onClick={() => setDialog({ invoice: row })} data-testid={"pinv-open-" + row.id}><FileText className="inline" size={16} /> {isManagedDraft(row) ? "تعديل المسودة" : "عرض"}</button>
                    {isManagedDraft(row) && <button className={buttonCls} onClick={() => openApproval(row)} data-testid={"pinv-approve-" + row.id}>اعتماد واستلام كامل</button>}
                    {row.schema_version === "g47-v1" && !["draft", "approved"].includes(row.state) && <button className="rounded border px-3 py-2" onClick={() => openApproval(row)}>متابعة حالة الاعتماد</button>}
                </td>
            </tr>)}</tbody>
        </table>{!filtered.length && <p className="p-8 text-center">لا توجد فواتير مطابقة.</p>}</div>}
        {dialog && <InvoiceDialog suppliers={suppliers} catalog={catalog} editing={dialog.invoice} onClose={() => setDialog(null)} onSaved={load} />}
        {approval && <ApprovalDialog invoice={approval} catalog={catalog} onClose={() => setApproval(null)} onSaved={load} />}
        <SupplierStatement cpId={statementSupplier} open={!!statementSupplier} onClose={() => setStatementSupplier(null)} />
    </div>;
}
