import { act } from "react";
import { createRoot } from "react-dom/client";
import PurchaseInvoices, {
    ApprovalDialog, InvoiceDialog, buildFullPurchaseApproval, buildPurchaseDraft,
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
        supplier: [{ entity_id: "supplier-account", label: "الموردون" }],
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
        input_vat_account_id: "", supplier_account_id: "supplier-account", notes: "",
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
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}
const byTest = (id) => container.querySelector('[data-testid="' + id + '"]');
function field(label, element = "select") {
    return Array.from(container.querySelectorAll("label")).find((node) => node.querySelector("span")?.textContent === label)?.querySelector(element);
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
    await change(byTest("pinv-line-0-product"), "p-two");
    await change(byTest("pinv-line-0-variant"), "v-two");
    await change(byTest("pinv-line-0-quantity"), "3");
    await change(byTest("pinv-line-0-total"), "36");
    await change(field("حساب المخزون"), "inventory-account");
    await change(field("حساب ذمة المورد"), "supplier-account");
    await submit();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/purchase-invoices");
    expect(api.post.mock.calls[0][1].lines[0]).toMatchObject({ product_id: "p-two", variant_id: "v-two", quantity: 3, unit_cost: 12 });
    expect(api.post.mock.calls[0][1]).not.toHaveProperty("operation_id");
    expect(onSaved).toHaveBeenCalled();
});
test("changing category clears component identity and does not offer services", async () => {
    await render(<InvoiceDialog suppliers={[]} catalog={catalog} editing={null} onClose={jest.fn()} onSaved={jest.fn()} />);
    await change(byTest("pinv-line-0-type"), "STOCK_COMPONENT");
    await change(byTest("pinv-line-0-category"), "metal");
    expect(byTest("pinv-line-0-component").textContent).toContain("مكوّن");
    expect(byTest("pinv-line-0-component").textContent).not.toContain("خدمة");
    expect(byTest("pinv-line-0-component").textContent).not.toContain("متوقف");
    await change(byTest("pinv-line-0-component"), "stock");
    await change(byTest("pinv-line-0-category"), "packing");
    expect(byTest("pinv-line-0-component").value).toBe("");
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
    expect(container.querySelector("fieldset").disabled).toBe(true);
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
    const options = Array.from(byTest("pinv-line-0-product").options).map((option) => option.value);
    expect(options).toEqual(["", "p-two"]);
    await change(byTest("pinv-line-0-product"), "p-two");
    expect(byTest("pinv-line-0-variant").value).toBe("");
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
    expect(byTest("pinv-approve-receive").closest("fieldset").disabled).toBe(true);
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
    expect(container.textContent).toContain("تغيرت مراجعة الفاتورة");
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
    expect(byTest("pinv-approve-receive").closest("fieldset").disabled).toBe(true);
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
