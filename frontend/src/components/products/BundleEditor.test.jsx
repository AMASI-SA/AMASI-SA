import React, { act } from "react";
import { createRoot } from "react-dom/client";
import BundleEditor from "./BundleEditor";
import { resizeCurrentSlots } from "./CurrentProductSlots";
import * as bundles from "../../services/productBundles";
import { getProductV2, listProductsV2 } from "../../services/mezanProductsV2";
import { getMezanComponentWorkspace } from "../../services/mezanComponentCatalog";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";

jest.mock("../../services/productBundles", () => ({ ...jest.requireActual("../../services/productBundles"), getProductBundle: jest.fn(), saveProductBundle: jest.fn(), previewProductBundle: jest.fn() }));
jest.mock("../../services/mezanProductsV2", () => ({ getProductV2: jest.fn(), listProductsV2: jest.fn() }));
jest.mock("../../services/mezanComponentCatalog", () => ({ getMezanComponentWorkspace: jest.fn() }));
jest.mock("../../services/mezanSuppliersV2", () => ({ loadMezanSuppliersWorkspace: jest.fn() }));

let node;
let root;
const items = [1, 2, 3, 4].map((id) => ({ kind: "product", product_id: `p${id}`, name: `مكوّن ${id}`, quantity: "1", unit: "unit", supplier_id: `s${id}`, sourcing_mode: "supplier", required: true }));
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 3, product_type: "composite", items }, expansion: null, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    getMezanComponentWorkspace.mockResolvedValue({ components: [] });
    loadMezanSuppliersWorkspace.mockResolvedValue({ suppliers: [1, 2, 3, 4].map((id) => ({ id: `s${id}`, company_name: `مورد ${id}` })) });
    getProductV2.mockResolvedValue({ options: [{ id: "size", name: "عدد المنتجات", type: "select", required: true, values: [{ id: "2", name: "اثنان" }, { id: "3", name: "ثلاثة" }, { id: "4", name: "أربعة" }] }] });
    listProductsV2.mockResolvedValue({ items: [] });
    node = document.createElement("div"); root = createRoot(node);
});
afterEach(async () => { await act(async () => root.unmount()); });
const button = (text) => [...node.querySelectorAll("button")].find((item) => item.textContent === text);

test("four independent suppliers survive edits and optimistic save", async () => {
    bundles.saveProductBundle.mockResolvedValue({ definition: { revision: 4, product_type: "composite", items }, expansion: { estimated_cost: "120", leaves: [] } });
    await act(async () => root.render(<BundleEditor productId="bundle" salePrice={200} />));
    expect(node.textContent).toContain("4/100");
    await act(async () => button("للأسفل").click());
    await act(async () => button("حفظ التركيب وحساب التكلفة").click());
    const saved = bundles.saveProductBundle.mock.calls[0][1];
    expect(saved.expected_revision).toBe(3);
    expect(saved.items.map((item) => item.supplier_id)).toEqual(["s2", "s1", "s3", "s4"]);
    expect(saved.items.every((item) => !Object.hasOwn(item, "name"))).toBe(true);
    expect(node.textContent).toContain("الربح التقديري");
});

test.each(["2", "3", "4"])("previews %s product choice using stable IDs", async (size) => {
    bundles.previewProductBundle.mockResolvedValue({ expansion: { estimated_cost: "60", leaves: [] } });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    const index = ["2", "3", "4"].indexOf(size);
    await act(async () => node.querySelectorAll('input[type="radio"]')[index].click());
    await act(async () => button("معاينة المكونات والتكلفة للاختيارات").click());
    expect(bundles.previewProductBundle).toHaveBeenCalledWith("bundle", [{ option_id: "size", value_id: size }]);
});

test("failed loading cannot enable an empty overwrite", async () => {
    bundles.getProductBundle.mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    expect(node.querySelector('[role="alert"]')).not.toBeNull();
    expect(button("حفظ التركيب وحساب التكلفة")).toBeUndefined();
});

test("saved component names come from the frozen reachable product catalog", async () => {
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 3, product_type: "composite", items: items.map(({ name, ...item }) => item) }, expansion: { needs_selection: true, frozen_definitions: { p1: { catalog_product: { name: "صندوق هدايا", mezan_product_id: "p1" } } } }, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    expect([...node.querySelectorAll("legend")].some((legend) => legend.textContent.includes("صندوق هدايا"))).toBe(true);
});

async function inputValue(input, value) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
}

test.each([1, 2, 3, 4, 10])("current product quantity %s saves without a duplicate product reference", async (quantity) => {
    const current = { kind: "current_product", quantity: "1", unit: "unit", sourcing_mode: "stock", required: true };
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 1, product_type: "composite", items: [current] }, expansion: null, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    bundles.saveProductBundle.mockResolvedValue({ definition: { revision: 2, product_type: "composite", items: [{ ...current, quantity: String(quantity) }] }, expansion: null });
    await act(async () => root.render(<BundleEditor productId="current" />));
    await inputValue(node.querySelector('[aria-label="كمية المكوّن 1"]'), String(quantity === 1 ? 2 : quantity));
    if (quantity === 1) await inputValue(node.querySelector('[aria-label="كمية المكوّن 1"]'), "1");
    await act(async () => button("حفظ التركيب وحساب التكلفة").click());
    const saved = bundles.saveProductBundle.mock.calls[0][1].items[0];
    expect(saved.kind).toBe("current_product"); expect(saved.quantity).toBe(String(quantity));
    expect(saved.product_id).toBeUndefined(); expect(saved.resource_id).toBeUndefined();
});

test("sale quantity three multiplies two physical units without changing definition", async () => {
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 1, product_type: "composite", items: [] }, expansion: { units_per_selection: 2, current_product_units_per_selection: 2, estimated_cost: "120", leaves: [] }, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    await act(async () => root.render(<BundleEditor productId="current" />));
    await inputValue(node.querySelector('[aria-label="كمية سلة للمعاينة"]'), "3");
    expect(node.textContent).toContain("كمية سلة 3 × وحدات الاختيار 2 = 6 وحدة فعلية");
    expect(bundles.saveProductBundle).not.toHaveBeenCalled();
});

test("slot resizing preserves different M and XL identities", () => {
    expect(resizeCurrentSlots([{ slot_id: "1", variant_id: "M" }, { slot_id: "2", variant_id: "XL" }], 3)).toEqual([{ slot_id: "1", variant_id: "M" }, { slot_id: "2", variant_id: "XL" }, { slot_id: "3" }]);
});

test("current product requires an explicit scope for original Components V2 bindings", async () => {
    const current = { kind: "current_product", quantity: "1", unit: "unit", sourcing_mode: "stock", required: true };
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 1, product_type: "composite", items: [current] }, expansion: null, scope_resources: [{ resource_id: "pack", name: "تغليف" }], limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    await act(async () => root.render(<BundleEditor productId="current" />));
    await inputValue(node.querySelector('[aria-label="كمية المكوّن 1"]'), "2");
    expect(button("حفظ التركيب وحساب التكلفة").disabled).toBe(true);
    const scope = [...node.querySelectorAll("label")].find((label) => label.textContent.startsWith("تغليف")).querySelector("select");
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(scope), "value").set.call(scope, "per_bundle"); scope.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(button("حفظ التركيب وحساب التكلفة").disabled).toBe(false);
});

test("only explicitly linked supplier customization text is sent in preview", async () => {
    getProductV2.mockResolvedValue({ options: [{ id: "size1", name: "مقاس القطعة", type: "text", required: true }, { id: "message", name: "بطاقة الإهداء", type: "text" }] });
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 1, product_type: "composite", items: [{ kind: "current_product", quantity: "1", unit: "unit", sourcing_mode: "supplier", supplier_id: "s1", slots: [{ slot_id: "1", customization_option_ids: ["size1"] }] }] }, expansion: null, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    bundles.previewProductBundle.mockResolvedValue({ expansion: { leaves: [] } });
    await act(async () => root.render(<BundleEditor productId="current" />));
    await inputValue(node.querySelector('[aria-label="مقاس القطعة"]'), "XL");
    await inputValue(node.querySelector('[aria-label="بطاقة الإهداء"]'), "نص خاص");
    await act(async () => button("معاينة المكونات والتكلفة للاختيارات").click());
    expect(bundles.previewProductBundle.mock.calls[0][3]).toEqual({ present_option_ids: ["size1", "message"], filled_option_ids: ["size1", "message"], option_answers: { size1: "XL" } });
});

test("preview selection can be cleared without saving bundle changes", async () => {
    bundles.previewProductBundle.mockResolvedValue({ expansion: { leaves: [] } });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await act(async () => node.querySelector('input[type="radio"]').click());
    await act(async () => node.querySelector('[aria-label="مسح اختيار عدد المنتجات"]').click());
    expect([...node.querySelectorAll('input[type="radio"]')].every((input) => !input.checked)).toBe(true);
    await act(async () => button("معاينة المكونات والتكلفة للاختيارات").click());
    expect(bundles.previewProductBundle).toHaveBeenCalledWith("bundle", []);
    expect(bundles.saveProductBundle).not.toHaveBeenCalled();
});

test("one searchable picker shows image and name and adds selected product", async () => {
    jest.useFakeTimers();
    listProductsV2.mockResolvedValue({ items: [{ id: "necklace", name: "قلادة ذهبية", sku: "AMS1", main_image: "https://example.com/necklace.png" }] });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    const search = node.querySelector('[role="combobox"]');
    await inputValue(search, "قلادة");
    await act(async () => jest.advanceTimersByTime(300));
    expect(listProductsV2).toHaveBeenLastCalledWith({ query: "قلادة", perPage: 50 });
    const result = node.querySelector('[role="option"]');
    expect(result.textContent).toContain("قلادة ذهبية");
    expect(result.querySelector("img").getAttribute("src")).toBe("https://example.com/necklace.png");
    await act(async () => result.click());
    expect(search.value).toBe("قلادة ذهبية");
    expect(node.querySelector('[role="listbox"]')).toBeNull();
    await act(async () => button("إضافة مكوّن (4/100)").click());
    expect([...node.querySelectorAll("legend")].some((legend) => legend.textContent.includes("قلادة ذهبية"))).toBe(true);
    jest.useRealTimers();
});

test("clear all resets text preview without modifying saved definition", async () => {
    getProductV2.mockResolvedValue({ options: [{ id: "note", name: "ملاحظات", type: "text" }] });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await inputValue(node.querySelector('[aria-label="ملاحظات"]'), "هدية");
    await act(async () => button("مسح جميع اختيارات المعاينة").click());
    expect(node.querySelector('[aria-label="ملاحظات"]').value).toBe("");
    expect(button("حفظ التركيب وحساب التكلفة").disabled).toBe(true);
});

test("clearing a parent preview choice also removes hidden dependent selections", async () => {
    getProductV2.mockResolvedValue({ options: [
        { id: "size", name: "المقاس", values: [{ id: "M", name: "M" }] },
        { id: "color", name: "اللون", show_if: [{ option_id: "size", value_id: "M" }], values: [{ id: "red", name: "أحمر" }] }
    ] });
    bundles.previewProductBundle.mockResolvedValue({ expansion: { leaves: [] } });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await act(async () => node.querySelector('input[type="radio"]').click());
    await act(async () => node.querySelectorAll('input[type="radio"]')[1].click());
    await act(async () => node.querySelector('[aria-label="مسح اختيار المقاس"]').click());
    expect(node.querySelectorAll('input[type="radio"]')).toHaveLength(1);
    await act(async () => button("معاينة المكونات والتكلفة للاختيارات").click());
    expect(bundles.previewProductBundle).toHaveBeenCalledWith("bundle", []);
});

test("picker supports keyboard selection without a second select", async () => {
    jest.useFakeTimers();
    listProductsV2.mockResolvedValue({ items: [{ id: "p-new", name: "منتج بدون صورة" }] });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    const search = node.querySelector('[role="combobox"]');
    await inputValue(search, "منتج");
    await act(async () => jest.advanceTimersByTime(300));
    expect(node.querySelector('[role="option"]').textContent).toContain("بدون صورة");
    await act(async () => search.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
    await act(async () => search.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    expect(search.value).toBe("منتج بدون صورة");
    expect(node.querySelector('[role="listbox"]')).toBeNull();
    jest.useRealTimers();
});
