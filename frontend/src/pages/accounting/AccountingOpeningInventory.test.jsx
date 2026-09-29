import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AccountingOpeningInventory, { buildOpeningInventoryDocument } from "./AccountingOpeningInventory";
import { approveOpeningInventory, getOpeningInventoryContext, getOpeningInventoryImport, importOpeningInventory } from "../../services/openingInventory";
jest.mock("../../services/openingInventory", () => ({ approveOpeningInventory: jest.fn(), getOpeningInventoryContext: jest.fn(), getOpeningInventoryImport: jest.fn(), importOpeningInventory: jest.fn() }));

const catalog = () => ({
    products: [{ id: "product", name: "Catalog product", variants_required: true, variants: [{ id: "variant", name: "Blue" }] }],
    components: [{ id: "component", name: "Fabric", category_ids: ["materials"], unit: "meter" }],
    categories: [{ id: "materials", name: "Materials" }], locations: [{ id: "slot", code: "SLOT" }],
    inventory_accounts: [{ entity_id: "inventory", label: "Inventory asset" }], imports: [],
    cutover: { cutover_at: "2026-09-29T21:00:00Z", opening_txn_group_id: "opening", evidence_ref: "evidence" },
});
const row = () => ({ item_type: "PRODUCT", product_id: "product", variant_id: "variant", inventory_account_id: "inventory", opening_quantity: "2", opening_unit_cost: "5", opening_total_cost: "10.00", allocations: [{ location_id: "slot", quantity: "2", scanned_location_barcode: "SLOT", preparation_state: "ready_complete", specification_fields: [{ name: "اللون", value: "أزرق" }] }] });
let root, node;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    getOpeningInventoryContext.mockResolvedValue(catalog());
});
afterEach(() => { act(() => root.unmount()); node.remove(); jest.resetAllMocks(); });
async function change(label, value) {
    const input = node.querySelector(`[aria-label="${label}"]`);
    await act(async () => {
        const proto = input.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(input, value);
        input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}
test("nonowner has no opening inventory controls or requests", async () => {
    await act(async () => root.render(<AccountingOpeningInventory isOwner={false} />));
    expect(node.textContent).toBe(""); expect(getOpeningInventoryContext).not.toHaveBeenCalled();
});
test("mount reads only and warns that explicit opening/transition is required", async () => {
    await act(async () => root.render(<AccountingOpeningInventory isOwner />));
    expect(node.textContent).toContain("هذه الشاشة لا تنفّذ تلك الإجراءات");
    expect(importOpeningInventory).not.toHaveBeenCalled(); expect(approveOpeningInventory).not.toHaveBeenCalled();
});
test("selection validates variant, component category, allocation and real opening identity", () => {
    expect(() => buildOpeningInventoryDocument([{ ...row(), variant_id: "" }], catalog())).toThrow("Variant");
    expect(() => buildOpeningInventoryDocument([{ ...row(), opening_quantity: "3" }], catalog())).toThrow("مجموع");
    expect(() => buildOpeningInventoryDocument([row()], { ...catalog(), cutover: {} })).toThrow("الافتتاح");
    const component = { ...row(), item_type: "STOCK_COMPONENT", resource_id: "component", category_id: "materials" };
    const document = buildOpeningInventoryDocument([component], catalog());
    expect(document.rows[0]).toMatchObject({ resource_id: "component", category_id: "materials" });
    expect(document.rows[0]).not.toHaveProperty("product_id"); expect(document.rows[0]).not.toHaveProperty("unit");
    expect(document.rows[0].allocations[0].specifications).toEqual({ "اللون": "أزرق" });
    expect(() => buildOpeningInventoryDocument([{ ...component, resource_id: "service" }], catalog())).toThrow("الخدمات");
});
test("component selectors expose catalog unit without offering unit conversion or service entry", async () => {
    await act(async () => root.render(<AccountingOpeningInventory isOwner />));
    await change("نوع البند 1", "STOCK_COMPONENT");
    expect(node.querySelector('[aria-label="المكون 1"]').options).toHaveLength(1);
    await change("التصنيف 1", "materials"); await change("المكون 1", "component");
    expect(node.textContent).toContain("meter");
    expect(node.textContent).toContain("لا تُحوّل الوحدة هنا");
    expect(node.querySelector('input[name="unit"]')).toBeNull();
    expect(importOpeningInventory).not.toHaveBeenCalled();
});
test("owner imports selected variant then sees server plan and explicitly reviews before approval", async () => {
    const imported = { id: "import", state: "imported", evidence_sha256: "a".repeat(64), baseline_sha256: "b".repeat(64), plan: [{ cost_key: "cost", identity: { product_name: "SERVER VALIDATED PRODUCT" }, opening_quantity: "2", opening_unit_cost: "5.000000", opening_total_cost: "10.00", inventory_account_id: "inventory", allocations: [{ location_id: "slot", quantity: "2", preparation_state: "ready_complete", specifications: {} }] }] };
    importOpeningInventory.mockResolvedValue(imported);
    approveOpeningInventory.mockResolvedValue({ ...imported, state: "approved" });
    await act(async () => root.render(<AccountingOpeningInventory isOwner />));
    await change("المنتج 1", "product"); await change("خيار المنتج 1", "variant"); await change("حساب المخزون 1", "inventory");
    await change("الكمية 1", "2"); await change("تكلفة الوحدة 1", "5"); await change("الخانة 1-1", "slot"); await change("كمية الخانة 1-1", "2"); await change("باركود الخانة 1-1", "SLOT");
    await act(async () => node.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(importOpeningInventory).toHaveBeenCalledTimes(1);
    expect(importOpeningInventory.mock.calls[0][0].rows[0]).toMatchObject({ product_id: "product", variant_id: "variant", opening_total_cost: "10.00" });
    expect(node.textContent).toContain("SERVER VALIDATED PRODUCT"); expect(approveOpeningInventory).not.toHaveBeenCalled();
    const approval = [...node.querySelectorAll("button")].find(b => b.textContent.includes("اعتماد تهيئة"));
    expect(approval.disabled).toBe(true);
    await act(async () => node.querySelector('input[type="checkbox"]').click());
    await act(async () => approval.click());
    expect(approveOpeningInventory).toHaveBeenCalledWith(imported);
    expect(node.textContent).toContain("لم يُنشأ قيد قيمة مخزون إضافي");
});
test("existing import is explicitly resumed without importing or approving again", async () => {
    getOpeningInventoryContext.mockResolvedValue({ ...catalog(), imports: [{ id: "prior", state: "imported", created_at: "Synthetic" }] });
    getOpeningInventoryImport.mockResolvedValue({ id: "prior", state: "imported", plan: [] });
    await act(async () => root.render(<AccountingOpeningInventory isOwner />));
    await act(async () => [...node.querySelectorAll("button")].find(b => b.textContent.includes("عرض استيراد")).click());
    expect(getOpeningInventoryImport).toHaveBeenCalledWith("prior");
    expect(importOpeningInventory).not.toHaveBeenCalled(); expect(approveOpeningInventory).not.toHaveBeenCalled();
});
