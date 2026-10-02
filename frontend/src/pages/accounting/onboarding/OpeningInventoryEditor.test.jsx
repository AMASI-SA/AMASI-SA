import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import OpeningInventoryEditor, { emptyOpeningInventoryRow, validateOpeningInventoryRows } from "./OpeningInventoryEditor";
const context = {
    products: [{ id: "p1", product_v2_id: "p1", name: "عباية", sku: "AB-1", barcode: "12345", main_image: "/product.png", options: [{ id: "color", name: "اللون", values: [{ id: "black", name: "أسود" }, { id: "blue", name: "أزرق" }] }], variants: [{ id: "v1", sku: "AB-BLK", barcode: "VAR-1", image_url: "/variant.png", options: [{ option_id: "color", value_id: "black" }] }, { id: "v2", sku: "AB-BLU", options: [{ name: "اللون", value: "أزرق" }] }] }],
    components: [{ id: "c1", name: "قماش", code: "FAB", unit: "meter", status: "active", category_ids: ["fabric"], track_inventory: true }],
    categories: [{ id: "fabric", name: "أقمشة" }], locations: [{ id: "l1", code: "A-1", warehouse_name: "الرئيسي", barcode_value: "LOC-1", provenance: "AMBIGUOUS" }],
};
const row = (variant = "v1") => ({ ...emptyOpeningInventoryRow(), product_v2_id: "p1", product_id: "p1", variant_id: variant, opening_quantity: "2", opening_unit_cost: "3.25", opening_total_cost: "6.50" });
let node, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); });
afterEach(() => { act(() => root.unmount()); node.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
function render(rows = [row()], ctx = context) { function Harness() { const [value, setValue] = useState(rows); return <OpeningInventoryEditor value={value} onChange={setValue} context={ctx} />; } act(() => root.render(<Harness />)); }
const field = label => node.querySelector(`[aria-label="${label}"]`);
function value(label, next) { act(() => { const el = field(label); Object.getOwnPropertyDescriptor(el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(el, next); el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true })); }); }
const click = text => act(() => [...node.querySelectorAll("button")].find(b => b.textContent.includes(text)).click());

test.each(["عباية", "AB-1", "12345", "p1", "VAR-1"])("search by %s shows real image, SKU barcode and options", query => {
    render([emptyOpeningInventoryRow()]); value("بحث المنتج 1", query);
    expect(field("نتائج المنتجات 1").textContent).toContain("عباية"); expect(node.querySelector("img").getAttribute("src")).toBe("/product.png");
    expect(field("نتائج المنتجات 1").textContent).toContain("12345"); expect(node.textContent).toContain("اللون: أسود، أزرق");
    click("عباية"); value("خيار المنتج 1", "v1"); expect(node.textContent).toContain("اللون: أسود"); expect(node.textContent).toContain("AB-BLK"); expect(node.querySelector("img").getAttribute("src")).toBe("/variant.png");
});
test("variant ID is preserved and distinct variants never collapse identities", () => {
    expect(validateOpeningInventoryRows([row(), row("v2")], context)).toEqual([]);
    expect(validateOpeningInventoryRows([row(), row()], context).filter(e => e.field === "item_type")).toHaveLength(1);
    render([row("v2")]); expect(field("خيار المنتج 1").value).toBe("v2"); expect(node.querySelector("img").getAttribute("src")).toBe("/product.png");
    expect(node.textContent).not.toContain("مواصفات المنتج / العميل");
});
test("missing variant and legacy-only identity are invalid", () => {
    expect(validateOpeningInventoryRows([{ ...row(), variant_id: "" }], context).map(e => e.field)).toContain("variant_id");
    expect(validateOpeningInventoryRows([{ ...row(), product_v2_id: "legacy", product_id: "legacy" }], context).map(e => e.field)).toContain("product_id");
});
test("component permits fractions but only active stock components with registered units", () => {
    const component = { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", resource_id: "c1", category_id: "fabric", opening_quantity: "1.5", opening_unit_cost: "4", opening_total_cost: "6.00" };
    expect(validateOpeningInventoryRows([component], context)).toEqual([]);
    for (const patch of [{ unit: "" }, { kind: "service" }, { status: "inactive" }, { track_inventory: false }, { category_ids: ["other"] }]) expect(validateOpeningInventoryRows([component], { ...context, components: [{ ...context.components[0], ...patch }] }).filter(e => e.field === "resource_id")).toHaveLength(1);
    render([component]); expect(field("المكوّن 1").textContent).toContain("FAB · meter"); expect(node.textContent).not.toContain("خيار المنتج");
});
test("total is calculated using decimal half-up and summary updates", () => {
    render(); value("تكلفة الوحدة 1", "10.075"); value("الكمية 1", "1");
    expect(field("الإجمالي 1").value).toBe("10.08"); expect(field("الإجمالي 1").readOnly).toBe(true); expect(field("ملخص المخزون").textContent).toContain("10.08 SAR");
});
test("absent placement permits financial draft; supplied optional placement is validated", () => {
    expect(validateOpeningInventoryRows([row()], context)).toEqual([]);
    const located = { ...row(), allocations: [{ location_id: "l1", quantity: "2", scanned_location_barcode: "" }] };
    expect(validateOpeningInventoryRows([located], context)).toEqual([]);
    expect(validateOpeningInventoryRows([{ ...located, allocations: [{ ...located.allocations[0], quantity: "1", scanned_location_barcode: "wrong" }] }], context).map(e => e.message).join(" ")).toMatch(/الباركود.*مجموع/);
    render([located]); expect(field("الخانة 1-1").textContent).toContain("AMBIGUOUS"); click("حذف التوزيع"); expect(field("الخانة 1-1")).toBeNull();
});
test("each error includes item, field and one reason without duplicate quantity errors", () => {
    const errors = validateOpeningInventoryRows([{ ...row(), opening_quantity: "-0.5" }], context);
    expect(errors.filter(e => e.field === "opening_quantity")).toHaveLength(1); expect(errors[0].message).toContain("البند 1 (عباية) — الكمية:");
});
test("changing variants clears stale quantity costs and physical distribution", () => {
    render(); value("خيار المنتج 1", "v2"); expect(field("الكمية 1").value).toBe(""); expect(field("الإجمالي 1").value).toBe(""); expect(field("خيار المنتج 1").value).toBe("v2");
});

test("customization options do not require a nonexistent stock combination", () => {
    const customized = { ...context.products[0], variants: [], variants_required: false, options: [{ id: "text", name: "كتابة الاسم", type: "text" }] };
    const rows = [{ ...row(), variant_id: "" }];
    const ctx = { ...context, products: [customized] };
    expect(validateOpeningInventoryRows(rows, ctx)).toEqual([]);
    render(rows, ctx);
    expect(field("خيار المنتج 1")).toBeNull();
});

test("missing stock combination source is explained and no generated choice is offered", () => {
    const product = { ...context.products[0], variants: [], variants_required: true, unresolved_variants_count: 2 };
    render([{ ...row(), variant_id: "" }], { ...context, products: [product] });
    expect(field("خيار المنتج 1").options).toHaveLength(1);
    expect(node.textContent).toContain("هوية تركيبة المخزون غير مكتملة");
    expect(validateOpeningInventoryRows([{ ...row(), variant_id: "" }], { ...context, products: [product] }).map(error => error.field)).toContain("variant_id");
});

test("separate component area uses existing identity and unit without requiring any SKU", () => {
    const component = { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", resource_id: "c1", category_id: "fabric", opening_quantity: "1.5", opening_unit_cost: "4", opening_total_cost: "6.00" };
    const ctx = { ...context, components: [{ ...context.components[0], code: undefined }] };
    render([row(), component], ctx);
    expect(field("المنتجات وتركيبات المخزون")).not.toBeNull();
    expect(field("المكونات والمواد المخزنية").textContent).toContain("c1");
    expect(field("المكونات والمواد المخزنية").textContent).toContain("meter");
    expect(field("المكونات والمواد المخزنية").textContent).not.toContain("SKU");
    expect(validateOpeningInventoryRows([component], ctx)).toEqual([]);
});

test("incomplete owner input is not displayed as a zero inventory valuation", () => {
    render([emptyOpeningInventoryRow()]);
    expect(field("ملخص المخزون").textContent).toContain("بانتظار استكمال الكميات والتكلفة");
    expect(field("ملخص المخزون").textContent).not.toContain("0.00 SAR");
});

test("distribution exposes the difference without changing owner-entered quantity", () => {
    render([{ ...row(), allocations: [{ location_id: "l1", quantity: "1", scanned_location_barcode: "" }] }]);
    expect(field("مطابقة توزيع البند 1").textContent).toContain("الفرق عن كمية السطر: 1");
    expect(field("الكمية 1").value).toBe("2");
    value("كمية الخانة 1-1", "2");
    expect(field("مطابقة توزيع البند 1").textContent).toContain("الفرق عن كمية السطر: 0");
    expect(field("الإجمالي 1").value).toBe("6.50");
});

test("empty product search is explained without inventing a selectable product", () => {
    render([emptyOpeningInventoryRow()], { ...context, products: [] });
    expect(field("نتائج المنتجات 1").textContent).toContain("لا تتوفر منتجات");
    expect(field("نتائج المنتجات 1").querySelectorAll("button")).toHaveLength(0);
});

test("components remain one inventory identity across categories", () => {
    const component = { ...emptyOpeningInventoryRow(), item_type: "STOCK_COMPONENT", resource_id: "c1", category_id: "fabric", opening_quantity: "1", opening_unit_cost: "4", opening_total_cost: "4.00" };
    const ctx = { ...context, components: [{ ...context.components[0], category_ids: ["fabric", "other"] }] };
    expect(validateOpeningInventoryRows([component, { ...component, category_id: "other" }], ctx).filter(error => error.field === "item_type")).toHaveLength(1);
});
