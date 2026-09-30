import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OpeningInventoryEditor, { emptyOpeningInventoryRow, validateOpeningInventoryRows, searchInventoryItems } from "./OpeningInventoryEditor";

const context = {
    products: [{ id: "p1", name: "عباية", sku: "ABAYA", barcode: "12345", main_image: "https://catalog.test/product.jpg", variants: [{ id: "v1", sku: "BLACK54", image: "https://catalog.test/black.jpg", selections: [{ name: "اللون", value: "أسود" }, { name: "المقاس", value: "54" }] }, { id: "v2", name: "بيج 56" }] }, { id: "p2", name: "شال", sku: "SHAWL", barcode: "6789" }],
    components: [{ id: "c1", name: "قماش", code: "FABRIC", unit: "meter", category_ids: ["fabric"], track_inventory: true }],
    categories: [{ id: "fabric", name: "أقمشة" }],
    locations: [{ id: "l1", code: "A-1", warehouse_name: "المستودع الرئيسي", barcode: "LOC-A1" }],
};
const productRow = () => ({ ...emptyOpeningInventoryRow(), product_id: "p1", variant_id: "v1", opening_quantity: "2", opening_unit_cost: "3.25", opening_total_cost: "6.50" });
const componentRow = () => ({ ...productRow(), item_type: "STOCK_COMPONENT", product_id: "", variant_id: "", resource_id: "c1", category_id: "fabric", opening_quantity: "1.5", opening_unit_cost: "4", opening_total_cost: "6.00" });

test("financial valuation needs no warehouse, bin, barcode or physical approval", () => {
    expect(validateOpeningInventoryRows([productRow()], context)).toEqual([]);
    expect(productRow().allocations).toEqual([]);
    expect(productRow()).not.toHaveProperty("inventory_physical_approval_verified");
    expect(validateOpeningInventoryRows([{ ...productRow(), variant_id: "invented" }], context).some(e => e.field === "variant_id")).toBe(true);
});
test("real variant identities stay separate; exact duplicate rejected", () => {
    expect(validateOpeningInventoryRows([productRow(), { ...productRow(), variant_id: "v2" }], context)).toEqual([]);
    expect(validateOpeningInventoryRows([productRow(), productRow()], context).some(e => e.field === "item_type")).toBe(true);
});
test("optional distribution validates when entered, barcode optional, exact decimal reconciliation", () => {
    const row = { ...productRow(), allocations: [{ location_id: "l1", quantity: "2", scanned_location_barcode: "" }] };
    expect(validateOpeningInventoryRows([row], context)).toEqual([]);
    expect(validateOpeningInventoryRows([{ ...row, allocations: [{ location_id: "l1", quantity: "1" }] }], context).some(e => e.field === "allocations")).toBe(true);
    expect(validateOpeningInventoryRows([{ ...row, allocations: [{ location_id: "l1", quantity: "2", scanned_location_barcode: "wrong" }] }], context).some(e => e.field.includes("barcode"))).toBe(true);
});
test("component units preserved; services and missing unit rejected", () => {
    expect(validateOpeningInventoryRows([componentRow()], context)).toEqual([]);
    for (const patch of [{ unit: "" }, { kind: "service" }, { track_inventory: false }, { category_ids: ["other"] }]) expect(validateOpeningInventoryRows([componentRow()], { ...context, components: [{ ...context.components[0], ...patch }] }).some(e => e.field === "resource_id")).toBe(true);
});
test.each(["عباية", "ABAYA", "12345", "p1", "BLACK54"])("product search uses catalogue name/SKU/barcode/id: %s", query => {
    expect(searchInventoryItems(context.products, query).map(p => p.id)).toEqual(["p1"]);
});
test("validation deduplicates per field and never repeats generic total errors", () => {
    const errors = validateOpeningInventoryRows([{ ...emptyOpeningInventoryRow(), product_id: "p1" }], context);
    expect(new Set(errors.map(e => `${e.row}:${e.field}`)).size).toBe(errors.length);
    expect(errors.map(e => e.field)).toEqual(["variant_id", "opening_quantity", "opening_unit_cost"]);
});
let container, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(() => { act(() => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
function input(node, value) { act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(node, value); node.dispatchEvent(new Event("input", { bubbles: true })); }); }
test("search picker shows true image and canonical variant labels, no manual option fields", () => {
    const onChange = jest.fn();
    act(() => root.render(<OpeningInventoryEditor value={[productRow()]} onChange={onChange} context={context} />));
    expect(container.querySelector("section").dir).toBe("rtl");
    expect(container.querySelector('[aria-label="المنتج المختار 1"] img').getAttribute("src")).toBe("https://catalog.test/black.jpg");
    expect(container.querySelector('[aria-label="خيار المنتج 1"]').textContent).toContain("اللون: أسود · المقاس: 54");
    expect(container.textContent).not.toMatch(/مواصفات المنتج \/ العميل|إضافة مواصفة/);
    expect(container.querySelector('[aria-label="الإجمالي 1"]').readOnly).toBe(true);
    expect(container.querySelector('[aria-label="ملخص المخزون"]').textContent).toContain("6.50");
    input(container.querySelector('[aria-label="بحث المنتج 1"]'), "6789");
    const list = container.querySelector('[aria-label="نتائج بحث المنتج 1"]');
    expect(list.textContent).toContain("شال"); expect(list.textContent).not.toContain("عباية");
    act(() => list.querySelector("button").click());
    expect(onChange.mock.calls[0][0][0]).toMatchObject({ product_id: "p2", variant_id: "", allocations: [], opening_quantity: "" });
});
test("changing variant clears old quantity; exact decimal half-up total auto-calculated", () => {
    const onChange = jest.fn();
    act(() => root.render(<OpeningInventoryEditor value={[{ ...productRow(), opening_quantity: "1" }]} onChange={onChange} context={context} />));
    input(container.querySelector('[aria-label="تكلفة الوحدة 1"]'), "10.075");
    expect(onChange.mock.calls[0][0][0].opening_total_cost).toBe("10.08");
    const select = container.querySelector('[aria-label="خيار المنتج 1"]');
    act(() => { select.value = "v2"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(onChange.mock.calls[1][0][0]).toMatchObject({ product_id: "p1", variant_id: "v2", opening_quantity: "", allocations: [] });
});
test("catalogue options without independent variants use real option/value IDs only", () => {
    const optionsContext = { ...context, products: [{ id: "p1", name: "وشاح", options: [{ id: "color", name: "اللون", values: [{ id: "navy", name: "كحلي" }] }] }] };
    const row = { ...productRow(), variant_id: "", selected_options: { color: "navy" } };
    expect(validateOpeningInventoryRows([row], optionsContext)).toEqual([]);
    expect(validateOpeningInventoryRows([{ ...row, selected_options: { color: "fabricated" } }], optionsContext).some(e => e.field === "selected_options.color")).toBe(true);
    act(() => root.render(<OpeningInventoryEditor value={[row]} onChange={jest.fn()} context={optionsContext} />));
    expect(container.querySelector('[aria-label="اللون 1"]').value).toBe("navy");
});
