import { act } from "react";
import { createRoot } from "react-dom/client";
import PurchaseInvoices, {
    ApprovalDialog, InvoiceDialog, PaymentDialog, SupplierStatement, buildSupplierPayment, buildFullPurchaseApproval, buildPurchaseDraft,
    isManagedDraft, purchasableComponents, updatePurchaseLine,
} from "./PurchaseInvoices";
import api from "../lib/api";

jest.mock("../lib/api", () => ({
    __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() },
    formatApiErrorDetail: (detail) => typeof detail === "string" ? detail : detail?.code || "",
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("react-router-dom", () => ({
    Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}));

const catalog = {
    supplier_identities: [{id: "supplier", counterparty_id: "supplier", entity_id: "supplier", name: "مورد"}],
    products: [
        { product_id: "p-one", name: "منتج أول", sku: "SAME", variants: [], variants_required: false },
        { product_id: "p-two", name: "منتج ثان", sku: "SAME", variants_required: true,
            variants: [{ variant_id: "v-two", sku: "V-SKU", name: "خيار محدد" }] },
        { product_id: "p-unloaded", name: "غير محمل", variants_required: true, variants: [] },
    ],
    categories: [{ id: "metal", name: "معدن" }, { id: "packing", name: "تغليف" }],
    components: [
        { resource_id: "stock", name: "مكوّن", code: "STOCK", category_ids: ["metal"], track_inventory: true },
        { resource_id: "service", name: "خدمة", category_ids: ["metal"], track_inventory: false },
        { resource_id: "stopped", name: "متوقف", category_ids: ["metal"], track_inventory: true, status: "inactive" },
        { resource_id: "other", name: "تصنيف آخر", category_ids: ["packing"], track_inventory: true },
    ],
    locations: [{ id: "loc", code: "A1", barcode: "LOC-A1", warehouse_id: "warehouse" }],
    account_mappings: {
        inventory: [{ entity_id: "inventory-account", label: "المخزون" }],
        supplier: [{ entity_id: "supplier", label: "الموردون" }],
        input_vat: [{ entity_id: "vat-account", label: "الضريبة" }],
    },
};
const productLine = { id: "line-1", item_type: "PRODUCT", product_id: "p-two", variant_id: "v-two",
    product_name: "اسم مرسل غير موثوق", sku: "WRONG", quantity: "3", unit_cost: "12" };
const componentLine = { id: "line-2", item_type: "STOCK_COMPONENT", category_id: "metal", resource_id: "stock",
    product_name: "مكوّن", sku: "STOCK", quantity: "1.5", unit_cost: "2" };
function form(overrides = {}) {
    return {
        supplier_counterparty_id: "supplier", invoice_number: "INV-1", invoice_date: "2026-09-26",
        due_date: "", tax_amount: "0", tax_treatment: "none", tax_evidence_ref: "",
        tax_evidence_verified: false, inventory_account_id: "inventory-account",
        input_vat_account_id: "", supplier_account_id: "supplier", notes: "",
        lines: [{ ...productLine }, { ...componentLine }], ...overrides,
    };
}
function draft(overrides = {}) {
    return {
        ...form(), id: "invoice-1", schema_version: "g47-v1", state: "draft", revision: 4,
        approval_operation_id: "server:owner:invoice-1:4", supplier_name: "مورد",
        lines: [productLine, componentLine], ...overrides,
    };
}
function receipts() {
    return [productLine, componentLine].map((line) => ({
        line_id: line.id, quantity: 0.25, location_id: "loc",
        scanned_location_barcode: "LOC-A1", preparation_state: "requires_preparation",
    }));
}
let container;
let root;
beforeEach(() => {
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.history.replaceState({}, "", "/purchase-invoices");
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
});
async function render(node) { await act(async () => root.render(node)); }
async function change(node, value) {
    await act(async () => {
        const proto = node.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(node, value);
        node.dispatchEvent(new Event(node.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}
async function submit() {
    await act(async () => document.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}
const byTest = (id) => document.querySelector('[data-testid="' + id + '"]');
function field(label, element = "select") {
    return Array.from(document.querySelectorAll("label")).find((node) => node.querySelector("span")?.textContent === label)?.querySelector(element);
}

async function pick(index,id){
    const search=byTest(`pinv-line-${index}-search`);
    await act(async()=>search.focus());
    await act(async()=>document.querySelector(`[data-testid="pinv-line-${index}"] [role="option"][data-product-id="${id}"]`).click());
}

test("draft uses exact product and variant identity instead of a duplicated SKU or free text", () => {
    const result = buildPurchaseDraft(form(), catalog);
    expect(result.lines[0]).toMatchObject({ product_id: "p-two", variant_id: "v-two", sku: "V-SKU", product_name: "منتج ثان", quantity: 3, unit_cost: 12 });
    expect(result.lines[0]).not.toHaveProperty("resource_id");
    expect(result.lines[1]).toMatchObject({ item_type: "STOCK_COMPONENT", resource_id: "stock", category_id: "metal", quantity: 1.5 });
});
test.each([
    { ...productLine, variant_id: "" },
    { ...productLine, variant_id: "another-product-variant" },
    { ...productLine, product_id: "p-unloaded", variant_id: "" },
    { ...productLine, product_id: "unknown" },
])("missing or mismatched product/variant cannot be silently saved", (line) => {
    expect(() => buildPurchaseDraft(form({ lines: [line] }), catalog)).toThrow();
});
test("only active inventory components in the selected category are purchasable", () => {
    expect(purchasableComponents(catalog, "metal").map((row) => row.resource_id)).toEqual(["stock"]);
    expect(purchasableComponents(catalog, "")).toEqual([]);
    for (const resource of ["service", "stopped", "other"]) {
        expect(() => buildPurchaseDraft(form({ lines: [{ ...componentLine, resource_id: resource }] }), catalog)).toThrow();
    }
    expect(() => buildPurchaseDraft(form({ lines: [{ ...componentLine, item_type: "SERVICE" }] }), catalog)).toThrow();
});
test("invalid rows are rejected instead of silently dropped and product quantities must be integers", () => {
    expect(() => buildPurchaseDraft(form({ lines: [productLine, { ...componentLine, quantity: "" }] }), catalog)).toThrow();
    expect(() => buildPurchaseDraft(form({ lines: [{ ...productLine, quantity: "1.5" }] }), catalog)).toThrow();
    expect(() => buildPurchaseDraft(form({ lines: [{ ...componentLine, unit_cost: "" }] }), catalog)).toThrow();
});
test("unit and total costs calculate in both directions without guessing blank cost", () => {
    expect(updatePurchaseLine({ quantity: "4", unit_cost: "", line_total: "", cost_basis: "unit" }, "unit_cost", "12.50"))
        .toMatchObject({ line_total: "50" });
    expect(updatePurchaseLine({ quantity: "4", unit_cost: "", line_total: "", cost_basis: "unit" }, "line_total", "50"))
        .toMatchObject({ unit_cost: "12.5", cost_basis: "total" });
    expect(updatePurchaseLine({ quantity: "4", unit_cost: "12.5", line_total: "50", cost_basis: "total" }, "quantity", "5"))
        .toMatchObject({ unit_cost: "10", line_total: "50" });
    expect(updatePurchaseLine({ quantity: "4", unit_cost: "", line_total: "", cost_basis: "unit" }, "quantity", "5").unit_cost).toBe("");
});
test("deductible tax needs attested evidence and approved mappings; non-deductible tax remains explicit", () => {
    const taxable = form({ tax_amount: "5.85", tax_treatment: "deductible", input_vat_account_id: "vat-account" });
    expect(() => buildPurchaseDraft(taxable, catalog)).toThrow();
    expect(() => buildPurchaseDraft({ ...taxable, tax_evidence_ref: "file" }, catalog)).toThrow();
    expect(buildPurchaseDraft({ ...taxable, tax_evidence_ref: "file", tax_evidence_verified: true }, catalog))
        .toMatchObject({ tax_evidence_ref: "file", tax_evidence_verified: true });
    expect(() => buildPurchaseDraft({ ...taxable, tax_evidence_ref: "file", tax_evidence_verified: true, inventory_account_id: "invented" }, catalog)).toThrow();
    expect(buildPurchaseDraft(form({ tax_amount: "5.85", tax_treatment: "non_deductible" }), catalog))
        .toMatchObject({ tax_treatment: "non_deductible", input_vat_account_id: null });
});
test("approval receives every line in full and preserves server identity on repeated attempts", () => {
    const first = buildFullPurchaseApproval(draft(), receipts(), catalog);
    expect(first).toMatchObject({ expected_revision: 4, operation_id: "server:owner:invoice-1:4" });
    expect(first.receipts.map((row) => row.quantity)).toEqual([3, 1.5]);
    expect(first).toEqual(buildFullPurchaseApproval(draft(), receipts(), catalog));
    expect(() => buildFullPurchaseApproval(draft(), receipts().slice(0, 1), catalog)).toThrow();
    expect(() => buildFullPurchaseApproval(draft(), receipts().map((row) => ({ ...row, scanned_location_barcode: "WRONG" })), catalog)).toThrow();
});
test("legacy and already approved invoices cannot use the approval payload", () => {
    expect(isManagedDraft({ ...draft(), schema_version: undefined })).toBe(false);
    expect(() => buildFullPurchaseApproval({ ...draft(), schema_version: undefined }, receipts(), catalog)).toThrow();
    expect(() => buildFullPurchaseApproval(draft({ state: "approved" }), receipts(), catalog)).toThrow();
});
test("new draft UI writes only the draft endpoint with canonical identities and no approval", async () => {
    api.post.mockResolvedValue({ data: draft() });
    const onSaved = jest.fn();
    await render(<InvoiceDialog suppliers={[{ id: "supplier", name: "مورد" }]} catalog={catalog} editing={null} onClose={jest.fn()} onSaved={onSaved} />);
    await change(byTest("pinv-supplier"), "supplier");
    await pick(0,"p-two");
    await change(byTest("pinv-line-0-variant"), "v-two");
    await change(byTest("pinv-line-0-quantity"), "3");
    await change(byTest("pinv-line-0-total"), "36");
    await change(field("حساب المخزون"), "inventory-account");
    await change(field("حساب ذمة المورد"), "supplier");
    await submit();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/purchase-invoices");
    expect(api.post.mock.calls[0][1].lines[0]).toMatchObject({ product_id: "p-two", variant_id: "v-two", quantity: 3, unit_cost: 12 });
    expect(api.post.mock.calls[0][1]).not.toHaveProperty("operation_id");
    expect(onSaved).toHaveBeenCalled();
});

test("native catalog alone supplies the supplier picker and draft without a legacy endpoint", async () => {
    api.get.mockImplementation(async (url) => {
        if (url === "/purchase-invoices?limit=500") return { data: { items: [] } };
        if (url === "/purchase-invoices/catalog") return { data: catalog };
        throw new Error("Unexpected non-native source: " + url);
    });
    api.post.mockResolvedValue({ data: draft() });
    await render(<PurchaseInvoices />);
    await act(async () => byTest("pinv-new-btn").click());
    expect(byTest("pinv-supplier").textContent).toContain("مورد");
    await change(byTest("pinv-supplier"), "supplier");
    await pick(0,"p-two");
    await change(byTest("pinv-line-0-variant"), "v-two");
    await change(byTest("pinv-line-0-quantity"), "3");
    await change(byTest("pinv-line-0-total"), "36");
    await change(field("حساب المخزون"), "inventory-account");
    await change(field("حساب ذمة المورد"), "supplier");
    await submit();
    expect(api.post).toHaveBeenCalledWith("/purchase-invoices", expect.objectContaining({
        supplier_counterparty_id: "supplier", supplier_account_id: "supplier",
    }));
    expect(api.get.mock.calls.some(([url]) => url.includes("counterparties"))).toBe(false);
});
test("changing category clears component identity and does not offer services", async () => {
    await render(<InvoiceDialog suppliers={[]} catalog={catalog} editing={null} onClose={jest.fn()} onSaved={jest.fn()} />);
    await act(async()=>byTest("pinv-line-0-search").focus());
    const results=document.querySelector('[role="listbox"]');
    expect(results.textContent).toContain("مكوّن");
    expect(results.textContent).not.toContain("خدمة");
    expect(results.textContent).not.toContain("متوقف");
    await pick(0,"stock");
    await change(byTest("pinv-line-0-category"), "packing");
    expect(byTest("pinv-line-0-search")).not.toBeNull();
    await act(async()=>byTest("pinv-line-0-search").focus());
    expect(document.querySelector('[role="option"][data-product-id="stock"]')).toBeNull();
    expect(document.querySelector('[role="option"][data-product-id="other"]')).not.toBeNull();
});

test("editing a draft preserves line IDs and supplies the displayed revision for compare-and-swap", async () => {
    api.put.mockResolvedValue({ data: draft({ revision: 5 }) });
    await render(<InvoiceDialog suppliers={[{ id: "supplier", name: "مورد" }]} catalog={catalog} editing={draft()} onClose={jest.fn()} onSaved={jest.fn()} />);
    await submit();
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put.mock.calls[0][0]).toBe("/purchase-invoices/invoice-1");
    expect(api.put.mock.calls[0][1].expected_revision).toBe(4);
    expect(api.put.mock.calls[0][1].lines.map((line) => line.id)).toEqual(["line-1", "line-2"]);
    expect(api.post).not.toHaveBeenCalled();
});
test("legacy invoice dialog is read-only and submitting cannot mutate it", async () => {
    await render(<InvoiceDialog suppliers={[]} catalog={catalog} editing={draft({ schema_version: undefined })} onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(document.querySelector("fieldset").disabled).toBe(true);
    expect(byTest("pinv-save-draft")).toBeNull();
    await submit();
    expect(api.put).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
});
test("tax evidence uploads source bytes with supplier/invoice and attestation is cleared by invoice changes", async () => {
    const invoice = draft({ tax_treatment: "deductible", tax_amount: "5.85", input_vat_account_id: "vat-account" });
    api.post.mockResolvedValue({ data: { file_id: "verified-file", sha256: "hash", filename: "invoice.pdf" } });
    await render(<InvoiceDialog suppliers={[{ id: "supplier", name: "مورد" }]} catalog={catalog} editing={invoice} onClose={jest.fn()} onSaved={jest.fn()} />);
    const file = new File(["%PDF evidence"], "invoice.pdf", { type: "application/pdf" });
    await act(async () => {
        Object.defineProperty(byTest("pinv-tax-upload"), "files", { value: [file], configurable: true });
        byTest("pinv-tax-upload").dispatchEvent(new Event("change", { bubbles: true }));
    });
    const [url, payload] = api.post.mock.calls[0];
    expect(url).toBe("/purchase-invoices/tax-evidence");
    expect(payload.get("file")).toBe(file);
    expect(payload.get("supplier_counterparty_id")).toBe("supplier");
    expect(payload.get("invoice_number")).toBe("INV-1");
    expect(byTest("pinv-tax-attestation").checked).toBe(false);
    await act(async () => byTest("pinv-tax-attestation").click());
    expect(byTest("pinv-tax-attestation").checked).toBe(true);
    await change(field("رقم الفاتورة", "input"), "INV-CHANGED");
    expect(byTest("pinv-tax-attestation").checked).toBe(false);
    expect(byTest("pinv-tax-attestation").disabled).toBe(true);
});
test("failed transport is reconciled before retry, and retry uses the same full approval operation", async () => {
    api.post.mockRejectedValueOnce(new Error("network uncertain")).mockResolvedValueOnce({
        data: { invoice: draft({ state: "approved" }), operation: { status: "succeeded" } },
    });
    api.get.mockResolvedValue({ data: draft({ operation: { status: "failed" } }) });
    await render(<ApprovalDialog invoice={draft()} catalog={catalog} onClose={jest.fn()} onSaved={jest.fn()} />);
    for (let i = 0; i < 2; i += 1) {
        await change(byTest("pinv-receipt-" + i + "-location"), "loc");
        await change(byTest("pinv-receipt-" + i + "-barcode"), "LOC-A1");
    }
    await submit();
    expect(api.get).toHaveBeenCalledWith("/purchase-invoices/invoice-1");
    await submit();
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[0][1]).toEqual(api.post.mock.calls[1][1]);
    expect(api.post.mock.calls[0][1].receipts.map((row) => row.quantity)).toEqual([3, 1.5]);
});
test.each(["pending", "failed", "recovery_required"])("saved %s operation retries its immutable server request", async (status) => {
    const request = buildFullPurchaseApproval(draft(), receipts(), catalog);
    api.post.mockResolvedValue({ data: draft({ state: "approved", operation: { status: "succeeded" } }) });
    await render(<ApprovalDialog invoice={draft({ state: "approving", operation: { status, request } })} catalog={catalog} onClose={jest.fn()} onSaved={jest.fn()} />);
    expect(byTest("pinv-receipt-0-location").closest("fieldset").disabled).toBe(true);
    expect(byTest("pinv-resume-approval").disabled).toBe(false);
    await submit();
    expect(api.post).toHaveBeenCalledWith("/purchase-invoices/invoice-1/approve-receive", request);
});

test("product search finds a variant barcode without substituting product identity", async () => {
    const searchable = { ...catalog, products: catalog.products.map((p) => p.product_id === "p-two" ? { ...p, variants: [{ ...p.variants[0], barcode: "998877" }] } : p) };
    await render(<InvoiceDialog suppliers={[{ id: "supplier", name: "Supplier" }]} catalog={searchable} editing={null} onClose={jest.fn()} onSaved={jest.fn()} />);
    await change(byTest("pinv-line-0-search"), "998877");
    const options=Array.from(document.querySelectorAll('[role="option"]')).map(option=>option.dataset.productId);
    expect(options).toEqual(["p-two"]);
    await act(async()=>document.querySelector('[role="option"][data-product-id="p-two"]').click());
    expect(byTest("pinv-line-0-variant").value).toBe("v-two");
});

test("pending operation without a persisted request prevents a second approval", async () => {
    api.post.mockResolvedValue({ data: { invoice: draft(), operation: { status: "pending" } } });
    api.get.mockResolvedValue({ data: draft({ operation: { status: "pending" } }) });
    await render(<ApprovalDialog invoice={draft()} catalog={catalog} onClose={jest.fn()} onSaved={jest.fn()} />);
    for (let i = 0; i < 2; i += 1) {
        await change(byTest("pinv-receipt-" + i + "-location"), "loc");
        await change(byTest("pinv-receipt-" + i + "-barcode"), "LOC-A1");
    }
    await submit();
    expect(byTest("pinv-approve-receive").disabled).toBe(true);
    await submit();
    expect(api.post).toHaveBeenCalledTimes(1);
});
test("revision changes after failure require review instead of silently approving a newer draft", async () => {
    api.post.mockRejectedValue(new Error("conflict"));
    api.get.mockResolvedValue({ data: draft({ revision: 5, approval_operation_id: "server:owner:invoice-1:5" }) });
    await render(<ApprovalDialog invoice={draft()} catalog={catalog} onClose={jest.fn()} onSaved={jest.fn()} />);
    for (let i = 0; i < 2; i += 1) {
        await change(byTest("pinv-receipt-" + i + "-location"), "loc");
        await change(byTest("pinv-receipt-" + i + "-barcode"), "LOC-A1");
    }
    await submit();
    expect(document.body.textContent).toContain("تغيرت مراجعة الفاتورة");
    await submit();
    expect(api.post).toHaveBeenCalledTimes(1);
});
test("unknown operation state after failed reconciliation locks retry until an explicit successful read", async () => {
    api.post.mockRejectedValue(new Error("network uncertain"));
    api.get.mockRejectedValue(new Error("read unavailable"));
    await render(<ApprovalDialog invoice={draft()} catalog={catalog} onClose={jest.fn()} onSaved={jest.fn()} />);
    for (let i = 0; i < 2; i += 1) {
        await change(byTest("pinv-receipt-" + i + "-location"), "loc");
        await change(byTest("pinv-receipt-" + i + "-barcode"), "LOC-A1");
    }
    await submit();
    expect(byTest("pinv-approve-receive").disabled).toBe(true);
    await submit();
    expect(api.post).toHaveBeenCalledTimes(1);
});
test("list offers approval only for new drafts, never legacy mutations", async () => {
    api.get.mockImplementation((url) => {
        if (url.includes("catalog")) return Promise.resolve({ data: catalog });
        if (url.includes("counterparties")) return Promise.resolve({ data: { items: [] } });
        return Promise.resolve({ data: { items: [draft(), draft({ id: "legacy", schema_version: undefined }), draft({ id: "approved", state: "approved" })] } });
    });
    await render(<PurchaseInvoices />);
    expect(byTest("pinv-approve-invoice-1")).not.toBeNull();
    expect(byTest("pinv-approve-legacy")).toBeNull();
    expect(byTest("pinv-approve-approved")).toBeNull();
    expect(container.querySelector('[data-testid^="pinv-delete"]')).toBeNull();
});

const paymentContext = {
    operation_id: "stable-payment-attempt", expected_payment_revision: 0, remaining_amount: "100.00",
    supplier_entity_id: "supplier", banks: [{ id: "bank", name: "البنك", balance: "500.00" }],
};
const paymentForm = { amount: "40.25", paid_from_account_id: "bank", payment_date: "2026-09-26", notes: "دفعة أولى" };
test("supplier payment keeps the server operation, exact amount and bank identity", () => {
    expect(buildSupplierPayment(paymentContext, paymentForm)).toEqual({
        ...paymentForm, operation_id: "stable-payment-attempt", expected_payment_revision: 0,
    });
});
test("unlinked supplier and mismatched supplier account cannot produce a purchase draft", () => {
    expect(() => buildPurchaseDraft(form(), { ...catalog, supplier_identities: [] })).toThrow();
    expect(() => buildPurchaseDraft(form({ supplier_account_id: "another-supplier" }), catalog)).toThrow();
});
test.each([
    { amount: "100.01" }, { amount: "-1" }, { amount: "0" }, { amount: "1.001" },
    { paid_from_account_id: "unapproved-bank" }, { payment_date: "" },
])("invalid supplier payment is rejected before submit: %j", (change) => {
    expect(() => buildSupplierPayment(paymentContext, { ...paymentForm, ...change })).toThrow();
});
test("supplier payment rejects insufficient displayed bank funds and missing operation identity", () => {
    expect(() => buildSupplierPayment({ ...paymentContext, banks: [{ id: "bank", balance: "10" }] }, paymentForm)).toThrow();
    expect(() => buildSupplierPayment({ ...paymentContext, operation_id: null }, paymentForm)).toThrow();
});
test("partial payment uses the invoice V2 route and closes only after committed success", async () => {
    api.get.mockResolvedValue({ data: paymentContext });
    api.post.mockResolvedValue({ data: { ok: true, operation: { status: "succeeded" } } });
    const onSaved = jest.fn(), onClose = jest.fn();
    await render(<PaymentDialog invoiceId="invoice-1" onSaved={onSaved} onClose={onClose} />);
    expect(api.get).toHaveBeenCalledWith("/purchase-invoices/invoice-1/payment-context");
    await change(byTest("pinv-payment-amount"), "40.25");
    await change(byTest("pinv-payment-bank"), "bank");
    await change(byTest("pinv-payment-date"), "2026-09-26");
    await submit();
    expect(api.post).toHaveBeenCalledWith("/purchase-invoices/invoice-1/payments", expect.objectContaining({
        operation_id: "stable-payment-attempt", amount: "40.25", expected_payment_revision: 0,
        paid_from_account_id: "bank", payment_date: "2026-09-26",
    }));
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
});
test("lost payment response retries the same operation and immutable fields without reloading context", async () => {
    api.get.mockResolvedValue({ data: paymentContext });
    api.post.mockRejectedValueOnce(new Error("response lost")).mockResolvedValueOnce({
        data: { ok: true, operation: { status: "succeeded" } },
    });
    const onSaved = jest.fn();
    await render(<PaymentDialog invoiceId="invoice-1" onSaved={onSaved} onClose={jest.fn()} />);
    await change(byTest("pinv-payment-bank"), "bank");
    await submit();
    expect(onSaved).not.toHaveBeenCalled();
    expect(byTest("pinv-payment-bank").closest("fieldset").disabled).toBe(true);
    await submit();
    expect(api.post.mock.calls[0]).toEqual(api.post.mock.calls[1]);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(onSaved).toHaveBeenCalledTimes(1);
});
test("unallocated supplier payable uses supplier identity without a name fallback", async () => {
    api.get.mockResolvedValue({ data: paymentContext });
    api.post.mockResolvedValue({ data: { ok: true, operation: { status: "succeeded" } } });
    await render(<PaymentDialog supplierId="supplier/id" onSaved={jest.fn()} onClose={jest.fn()} />);
    await change(byTest("pinv-payment-bank"), "bank");
    await submit();
    expect(api.get).toHaveBeenCalledWith("/purchase-invoices/supplier/supplier%2Fid/payment-context");
    expect(api.post.mock.calls[0][0]).toBe("/purchase-invoices/supplier/supplier%2Fid/payments");
});
test("denied payment context and unconfirmed result never claim a payment succeeded", async () => {
    api.get.mockRejectedValueOnce(new Error("permission denied"));
    const onSaved = jest.fn();
    await render(<PaymentDialog invoiceId="invoice-1" onSaved={onSaved} onClose={jest.fn()} />);
    expect(byTest("pinv-payment-submit").disabled).toBe(true);
    await submit();
    expect(api.post).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
});
test("pending payment response retains the attempt and does not announce completion", async () => {
    api.get.mockResolvedValue({ data: paymentContext });
    api.post.mockResolvedValue({ data: { ok: true, operation: { status: "pending" } } });
    const onSaved = jest.fn(), onClose = jest.fn();
    await render(<PaymentDialog invoiceId="invoice-1" onSaved={onSaved} onClose={onClose} />);
    await change(byTest("pinv-payment-bank"), "bank");
    await submit();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("لم يتأكد اكتمال السداد");
});
test("V2 supplier statement offers unallocated payment and cannot retain another supplier's stale data", async () => {
    api.get.mockResolvedValueOnce({ data: { supplier: { name: "المورد الأول" }, ledger_backend: "v2",
        unallocated_payable_amount: "30", totals: { total_invoiced: 100, total_paid: 70, balance_owed: 30 }, invoices: [] } });
    const onPayment = jest.fn();
    await render(<SupplierStatement cpId="one" open onClose={jest.fn()} onPayment={onPayment} />);
    await act(async () => byTest("pinv-pay-opening").click());
    expect(onPayment).toHaveBeenCalledWith("one");
    api.get.mockRejectedValueOnce(new Error("identity unavailable"));
    await render(<SupplierStatement cpId="two" open onClose={jest.fn()} onPayment={onPayment} />);
    expect(document.body.textContent).not.toContain("المورد الأول");
    expect(byTest("pinv-pay-opening")).toBeNull();
    expect(document.body.textContent).toContain("identity unavailable");
});

const configuredCatalog={...catalog,products:[{
    product_id:"pullover",name:"بلوفر",sku:"PARENT",image_url:"/pullover-test.webp",variants_required:true,
    options:[
        {id:"color",name:"اللون",type:"color",required:true,values:[{id:"green",name:"أخضر"},{id:"red",name:"أحمر"}]},
        {id:"size",name:"المقاس",type:"select",required:true,values:[{id:"m",name:"M"}]},
        {id:"letters",name:"الأحرف",type:"text",required:true,values:[]},
    ],variants:[
        {variant_id:"green-m",name:"أخضر M",sku:"P-G-M",barcode:"123456",selections:[{option_id:"color",value_id:"green"},{option_id:"size",value_id:"m"}]},
        {variant_id:"red-m",name:"أحمر M",sku:"P-R-M",selections:[{option_id:"color",value_id:"red"},{option_id:"size",value_id:"m"}]},
    ],
}]};
const configuredLine={...productLine,product_id:"pullover",variant_id:"green-m",product_name:"بلوفر",sku:"P-G-M",
    purchase_configuration:{state:"ready",selections:[{option_id:"color",value_id:"green"},{option_id:"size",value_id:"m"}],inputs:[{option_id:"letters",value:"AB"}]},
    purchase_option_labels:[{name:"اللون",value:"أخضر"},{name:"المقاس",value:"M"},{name:"الأحرف",value:"AB"}],
    purchase_configuration_definition_hash:"server-definition"};

test("native photo and SKU search configures canonical color size and real product-defined letters",async()=>{
    api.post.mockResolvedValue({data:draft({lines:[configuredLine]})});
    await render(<InvoiceDialog suppliers={[{id:"supplier",name:"مورد"}]} catalog={configuredCatalog} onClose={jest.fn()} onSaved={jest.fn()}/>);
    await change(byTest("pinv-supplier"),"supplier");
    await change(byTest("pinv-line-0-search"),"123456");
    expect(document.querySelector('[role="option"] img').getAttribute("src")).toBe("/pullover-test.webp");
    expect(document.querySelector('[role="option"]').textContent).toContain("PARENT");
    await act(async()=>document.querySelector('[role="option"]').click());
    expect(document.querySelector('[aria-label="اللون: أخضر"]').checked).toBe(true);
    expect(document.querySelector('[aria-label="المقاس: M"]').checked).toBe(true);
    expect(document.querySelector('[aria-label="الأحرف · البند 1"]')).toBeNull();
    await act(async()=>document.querySelector('[aria-label="جاهز بالمواصفات"]').click());
    await change(document.querySelector('[aria-label="الأحرف · البند 1"]'),"AB");
    await change(byTest("pinv-line-0-unit-cost"),"12");
    await change(field("حساب المخزون"),"inventory-account");
    await submit();
    expect(api.post).toHaveBeenCalledWith("/purchase-invoices",expect.objectContaining({lines:[expect.objectContaining({
        product_id:"pullover",variant_id:"green-m",sku:"P-G-M",purchase_configuration:configuredLine.purchase_configuration,
    })]}));
    const savedPayload=api.post.mock.calls[0][1].lines[0];
    expect(savedPayload).not.toHaveProperty("purchase_option_labels");
    expect(savedPayload).not.toHaveProperty("purchase_configuration_definition_hash");
});

test("ready required lettering blocks save and switching to customer request clears the text",async()=>{
    const editing=draft({lines:[{...configuredLine,purchase_configuration:{...configuredLine.purchase_configuration,inputs:[]}}]});
    await render(<InvoiceDialog suppliers={[{id:"supplier",name:"مورد"}]} catalog={configuredCatalog} editing={editing} onClose={jest.fn()} onSaved={jest.fn()}/>);
    await submit();
    expect(api.put).not.toHaveBeenCalled();
    expect(document.querySelector('[role="alert"]').textContent).toContain("أكمل خيارات");
    await change(document.querySelector('[aria-label="الأحرف · البند 1"]'),"AB");
    await act(async()=>document.querySelector('[aria-label="يُخصّص عند طلب العميل"]').click());
    expect(document.querySelector('[aria-label="الأحرف · البند 1"]')).toBeNull();
    api.put.mockResolvedValue({data:editing});
    await submit();
    expect(api.put.mock.calls[0][1].lines[0].purchase_configuration).toEqual({...configuredLine.purchase_configuration,state:"raw",inputs:[]});
});

test("saved native config reopens and approval sends persisted lettering and canonical identity",async()=>{
    const invoice=draft({lines:[configuredLine]});
    await render(<InvoiceDialog suppliers={[{id:"supplier",name:"مورد"}]} catalog={configuredCatalog} editing={invoice} onClose={jest.fn()} onSaved={jest.fn()}/>);
    expect(document.querySelector('[aria-label="الأحرف · البند 1"]').value).toBe("AB");
    await render(<ApprovalDialog invoice={invoice} catalog={configuredCatalog} onClose={jest.fn()} onSaved={jest.fn()}/>);
    expect(document.body.textContent).toContain("الأحرف: AB");
    await change(byTest("pinv-receipt-0-location"),"loc");
    await change(byTest("pinv-receipt-0-barcode"),"LOC-A1");
    api.post.mockResolvedValue({data:{...invoice,state:"approved",operation:{status:"succeeded"}}});
    await submit();
    expect(api.post).toHaveBeenCalledWith("/purchase-invoices/invoice-1/approve-receive",expect.objectContaining({
        operation_id:invoice.approval_operation_id,expected_revision:invoice.revision,
        receipts:[expect.objectContaining({line_id:"line-1",preparation_state:"ready_complete",specifications:{اللون:"أخضر",المقاس:"M",الأحرف:"AB"}})],
    }));
});

test("read-only purchase access keeps drafts and approval mutation disabled",async()=>{
    api.get.mockImplementation(url=>Promise.resolve({data:url.includes("catalog")?configuredCatalog:{items:[draft({lines:[configuredLine]})]}}));
    await render(<PurchaseInvoices accessMode="read"/>);
    expect(byTest("pinv-new-btn").disabled).toBe(true);
    expect(byTest("pinv-approve-invoice-1")).toBeNull();
    await act(async()=>byTest("pinv-open-invoice-1").click());
    expect(byTest("pinv-save-draft")).toBeNull();
    await submit();
    expect(api.put).not.toHaveBeenCalled();
    await render(<ApprovalDialog invoice={draft({lines:[configuredLine]})} catalog={configuredCatalog} readOnly onClose={jest.fn()} onSaved={jest.fn()}/>);
    await submit();
    expect(api.post).not.toHaveBeenCalled();
});
