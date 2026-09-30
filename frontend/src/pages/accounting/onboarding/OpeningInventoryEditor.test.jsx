import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OpeningInventoryEditor, { emptyOpeningInventoryRow, validateOpeningInventoryRows } from "./OpeningInventoryEditor";

const context = {
    products: [{ id: "p1", name: "منتج", variants: [{ id: "v1", name: "أزرق" }] }],
    components: [{ id: "c1", name: "قماش", unit: "meter", category_ids: ["fabric"], track_inventory: true }],
    categories: [{ id: "fabric", name: "أقمشة" }],
    locations: [{ id: "l1", code: "A-1", warehouse_name: "المستودع الرئيسي" }],
};
const productRow = () => ({ ...emptyOpeningInventoryRow(), product_id: "p1", variant_id: "v1", opening_quantity: "2", opening_unit_cost: "3.25", opening_total_cost: "6.50", allocations: [{ location_id: "l1", quantity: "2", scanned_location_barcode: "LOC-A1", preparation_state: "ready_complete", specification_fields: [{ name: "اللون", value: "أزرق" }] }] });
const componentRow = () => ({ ...productRow(), item_type: "STOCK_COMPONENT", product_id: "", variant_id: "", resource_id: "c1", category_id: "fabric", opening_quantity: "1.5", opening_unit_cost: "4", opening_total_cost: "6.00", allocations: [{ ...productRow().allocations[0], quantity: "1.5" }] });

test("product requires real variant and integral row/allocation quantity", () => {
    expect(validateOpeningInventoryRows([productRow()], context)).toEqual([]);
    expect(validateOpeningInventoryRows([{ ...productRow(), variant_id: "" }], context).some(e => e.field === "variant_id")).toBe(true);
    const fractional = { ...productRow(), opening_quantity: "1.5", opening_unit_cost: "4", opening_total_cost: "6", allocations: [{ ...productRow().allocations[0], quantity: "1.5" }] };
    const errors = validateOpeningInventoryRows([fractional], context);
    expect(errors.some(e => e.field === "opening_quantity")).toBe(true);
    expect(errors.some(e => e.field === "allocations.0")).toBe(true);
});

test("component accepts fractional registered units, rejects missing units/services/category mismatch", () => {
    expect(validateOpeningInventoryRows([componentRow()], context)).toEqual([]);
    for (const patch of [{ unit: "" }, { kind: "service" }, { category_ids: ["other"] }]) {
        expect(validateOpeningInventoryRows([componentRow()], { ...context, components: [{ ...context.components[0], ...patch }] }).some(e => e.field === "resource_id")).toBe(true);
    }
});

test("quantity reconciliation, barcode, cost, duplicate identities and specification validation", () => {
    const row = productRow();
    expect(validateOpeningInventoryRows([{ ...row, opening_total_cost: "7" }], context).some(e => e.field === "opening_total_cost")).toBe(true);
    expect(validateOpeningInventoryRows([{ ...row, allocations: [{ ...row.allocations[0], quantity: "1", scanned_location_barcode: "", specification_fields: [{ name: "اللون", value: "" }] }] }], context).map(e => e.message).join(" ")).toMatch(/باركود.*مواصفة.*مجموع/);
    expect(validateOpeningInventoryRows([row, row], context).some(e => e.field === "item_type")).toBe(true);
    expect(validateOpeningInventoryRows([{ ...row, opening_quantity: "0" }], context).some(e => e.field === "opening_quantity")).toBe(true);
});

let container, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });

test("RTL rendering restores draft variants/specifications and exposes no submit controls", () => {
    const onChange = jest.fn();
    act(() => root.render(<OpeningInventoryEditor value={[productRow()]} onChange={onChange} context={context} />));
    expect(container.querySelector("section").dir).toBe("rtl");
    expect(container.querySelector('[aria-label="خيار المنتج 1"]').value).toBe("v1");
    expect(container.querySelector('[aria-label="قيمة المواصفة 1-1-1"]').value).toBe("أزرق");
    expect(container.querySelector('[aria-label="الكمية 1"]').step).toBe("1");
    expect(container.querySelector('[aria-label="الخانة 1-1"]').textContent).toContain("المستودع الرئيسي");
    expect([...container.querySelectorAll("button")].every(button => button.type === "button")).toBe(true);
    expect(container.textContent).not.toMatch(/تفعيل|ترحيل|اعتماد/);
    expect(onChange).not.toHaveBeenCalled();
    act(() => root.render(<OpeningInventoryEditor value={[componentRow()]} onChange={onChange} context={context} />));
    expect(container.textContent).toContain("meter");
    expect(container.querySelector('[aria-label="الكمية 1"]').step).toBe("any");
    expect(container.querySelector('[aria-label="الكمية 1"]').value).toBe("1.5");
});

test("controlled editing returns draft-only changes and clears stale variant on product change", () => {
    const onChange = jest.fn();
    act(() => root.render(<OpeningInventoryEditor value={[productRow()]} onChange={onChange} context={context} />));
    const select = container.querySelector('[aria-label="المنتج 1"]');
    act(() => { select.value = ""; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(onChange.mock.calls[0][0][0]).toMatchObject({ product_id: "", variant_id: "", opening_quantity: "", opening_unit_cost: "", opening_total_cost: "", allocations: [{ specification_fields: [], quantity: "", location_id: "" }] });
    const addSpecification = [...container.querySelectorAll("button")].find(button => button.textContent === "إضافة مواصفة");
    act(() => addSpecification.click());
    expect(onChange.mock.calls[1][0][0].allocations[0].specification_fields).toHaveLength(2);
    expect(productRow().allocations[0].specification_fields).toHaveLength(1);
});

test("inventory uses decimal half-up rounding for 10.075 instead of binary floating point", () => {
    const row = { ...productRow(), opening_quantity: "1", opening_unit_cost: "10.075", opening_total_cost: "10.08", allocations: [{ ...productRow().allocations[0], quantity: "1" }] };
    expect(validateOpeningInventoryRows([row], context)).toEqual([]);
    expect(validateOpeningInventoryRows([{ ...row, opening_total_cost: "10.07" }], context).some(e => e.field === "opening_total_cost")).toBe(true);
    const onChange = jest.fn();
    act(() => root.render(<OpeningInventoryEditor value={[{ ...row, opening_unit_cost: "1", opening_total_cost: "1" }]} onChange={onChange} context={context} />));
    const input = container.querySelector('[aria-label="تكلفة الوحدة 1"]');
    act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "10.075"); input.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(onChange.mock.calls[0][0][0].opening_total_cost).toBe("10.08");
});

test("changing variant clears the former variant quantity costs and specifications", () => {
    const onChange = jest.fn();
    const twoVariants = { ...context, products: [{ ...context.products[0], variants: [...context.products[0].variants, { id: "v2", name: "أحمر" }] }] };
    act(() => root.render(<OpeningInventoryEditor value={[productRow()]} onChange={onChange} context={twoVariants} />));
    const select = container.querySelector('[aria-label="خيار المنتج 1"]');
    act(() => { select.value = "v2"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(onChange.mock.calls[0][0][0]).toMatchObject({ product_id: "p1", variant_id: "v2", opening_quantity: "", opening_unit_cost: "", opening_total_cost: "", allocations: [{ specification_fields: [], location_id: "" }] });
});
