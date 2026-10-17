import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import BundleEditor from "./BundleEditor";
import { resizeCurrentSlots } from "./CurrentProductSlots";
import * as bundles from "../../services/productBundles";
import { getProductV2, listProductsV2 } from "../../services/mezanProductsV2";
import { getMezanComponentWorkspace } from "../../services/mezanComponentCatalog";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";

jest.mock("../../lib/api", () => ({ get: jest.fn(), put: jest.fn() }));
jest.mock("../../services/productBundles", () => ({ ...jest.requireActual("../../services/productBundles"), getProductBundleComponents: jest.fn(), getProductBundle: jest.fn(), saveProductBundle: jest.fn(), previewProductBundle: jest.fn() }));
jest.mock("../../services/mezanProductsV2", () => ({ getProductV2: jest.fn(), listProductsV2: jest.fn() }));
jest.mock("../../services/mezanComponentCatalog", () => ({ getMezanComponentWorkspace: jest.fn() }));
jest.mock("../../services/mezanSuppliersV2", () => ({ loadMezanSuppliersWorkspace: jest.fn() }));

let node;
let root;
const items = [1, 2, 3, 4].map((id) => ({ kind: "product", product_id: `p${id}`, name: `مكوّن ${id}`, quantity: "1", unit: "unit", supplier_id: `s${id}`, sourcing_mode: "supplier", required: true }));
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    bundles.getProductBundleComponents.mockResolvedValue({ components: [] });
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
    expect(search.value).toBe("");
    expect(node.querySelector('[role="listbox"]')).toBeNull();
    expect(button("إضافة مكوّن (4/100)")).toBeUndefined();
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
    expect(search.value).toBe("");
    expect(node.textContent).toContain("منتج بدون صورة");
    expect(node.querySelector('[role="listbox"]')).toBeNull();
    jest.useRealTimers();
});

const selectValue = async (select, value) => act(async () => { select.value = value; select.dispatchEvent(new Event("change", { bubbles: true })); });
const labeledSelect = (text) => [...node.querySelectorAll("label")].find((label) => label.firstChild?.textContent === text)?.querySelector("select");

test("rapid repeated selection adds one card, persists identity, and allows explicit quantity", async () => {
    jest.useFakeTimers();
    listProductsV2.mockResolvedValue({ items: [{ id: "new", name: "منتج اختبار", unit: "piece", main_image: "/product.png" }] });
    bundles.saveProductBundle.mockResolvedValue({ definition: { revision: 4, product_type: "composite", items }, expansion: null });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await inputValue(node.querySelector('[role="combobox"]'), "منتج");
    await act(async () => jest.advanceTimersByTime(300));
    const result = node.querySelector('[role="option"]');
    await act(async () => { result.click(); result.click(); });
    expect(node.querySelectorAll('[aria-label^="كمية المكوّن"]')).toHaveLength(5);
    expect(node.textContent).toContain("منتج اختبار");
    expect(node.querySelector('img[src="/product.png"]')).not.toBeNull();
    expect(node.querySelector('[role="combobox"]').value).toBe("");
    await inputValue(node.querySelector('[aria-label="كمية المكوّن 5"]'), "2");
    await act(async () => button("حفظ التركيب وحساب التكلفة").click());
    const saved = bundles.saveProductBundle.mock.calls[0][1];
    expect(saved.schema_version).toBe(2);
    expect(saved.items[4]).toMatchObject({ product_id: "new", quantity: "2", unit: "piece", sourcing_mode: "auto", repeat_policy: "sum" });
    expect(saved.items[4].rule_id).toBeTruthy();
    expect(api.put).not.toHaveBeenCalled();
    jest.useRealTimers();
});

test("resource card hides supplier for stock and locks catalog unit", async () => {
    getMezanComponentWorkspace.mockResolvedValue({ components: [{ id: "wrap", name: "تغليف", unit: "sheet" }] });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await selectValue(labeledSelect("مصدر المكوّن"), "resource");
    await inputValue(node.querySelector('[role="combobox"]'), "تغليف");
    await act(async () => node.querySelector('[role="option"]').click());
    expect(labeledSelect("المورد")).toBeUndefined();
    expect([...node.querySelectorAll("label")].filter((label) => label.firstChild?.textContent === "الوحدة")).toHaveLength(0);
    await selectValue(labeledSelect("التوريد"), "supplier");
    expect(labeledSelect("المورد")).toBeTruthy();
});

test("conditional binding uses separate stable field and value and blocks incomplete rule", async () => {
    bundles.saveProductBundle.mockResolvedValue({ definition: { revision: 4, product_type: "composite", items }, expansion: null });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await selectValue(labeledSelect("إضافة المكوّن"), "conditional");
    expect(button("حفظ التركيب وحساب التكلفة").disabled).toBe(true);
    await selectValue(labeledSelect("حقل خيار العميل"), "size");
    await selectValue(labeledSelect("قيمة خيار العميل"), "4");
    await act(async () => button("حفظ التركيب وحساب التكلفة").click());
    expect(bundles.saveProductBundle.mock.calls[0][1].items[0].condition).toEqual({ option_id: "size", value_id: "4" });
});

test("inherited exclusion saves a row override without altering source product", async () => {
    bundles.getProductBundleComponents.mockResolvedValue({ product_id: "p1", components: [{ binding_kind: "product_resource", binding_id: "binding-wrap", resource_id: "wrap", name: "تغليف أصلي", quantity: "1", unit: "piece", unit_cost: "2", policy: { cost_treatment: "independent" } }] });
    bundles.saveProductBundle.mockResolvedValue({ definition: { revision: 4, product_type: "composite", items }, expansion: null });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    const details = node.querySelector("details");
    await act(async () => { details.open = true; details.dispatchEvent(new Event("toggle", { bubbles: true })); });
    expect(bundles.getProductBundleComponents).toHaveBeenCalledWith("p1");
    const checkbox = node.querySelector('[aria-label="استخدام تغليف أصلي في القاعدة 1"]');
    expect(checkbox.checked).toBe(true);
    await act(async () => checkbox.click());
    await act(async () => button("حفظ التركيب وحساب التكلفة").click());
    expect(bundles.saveProductBundle.mock.calls[0][1].items[0].component_overrides).toEqual([{ binding_kind: "product_resource", binding_id: "binding-wrap", use: false }]);
    expect(bundles.saveProductBundle.mock.calls[0][0]).toBe("bundle");
    expect(api.put).not.toHaveBeenCalled();
});

test("unknown inherited policy is displayed explicitly and incomplete expansion warns", async () => {
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 3, product_type: "composite", items }, expansion: { estimated_cost: null, leaves: [], unresolved_components: [{ binding_id: "unknown" }] }, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    bundles.getProductBundleComponents.mockResolvedValue({ components: [{ binding_kind: "product_resource", binding_id: "unknown", resource_id: "wrap", name: "تغليف", quantity: "1", unit: "piece", unit_cost: null, policy: {} }] });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    const details = node.querySelector("details");
    await act(async () => { details.open = true; details.dispatchEvent(new Event("toggle", { bubbles: true })); });
    expect(node.textContent).toContain("غير محددة — تحتاج مراجعة إعدادات المنتج الأصلي");
    expect(node.textContent).toContain("التكلفة غير مكتملة");
});

test("preview shows inherited decisions, unresolved costs, sourcing segments and rule quantities", async () => {
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 3, product_type: "composite", items }, limits: { max_items: 100, max_depth: 5, max_leaves: 500 }, expansion: {
        known_cost: "20", estimated_cost: null, cost_complete: false,
        component_decisions: [{ name: "تغليف مستثنى", use: false, quantity: "1", cost_treatment: "independent", assembly_origin: "inhouse", binding_kind: "product_resource", binding_id: "wrap", path: ["bundle@3:rule1"] }],
        unresolved_components: [{ name: "سلسال غير محسوم", use: true }],
        leaves: [{ name: "منتج تلقائي", supplier_id: "technical-supplier-uuid", quantity: "3", unit_cost: "5", sourcing_mode: "auto", state: "needs_source", unresolved_quantity: "2", preview_only: true, paths: [["bundle@3:rule2"]], sourcing_segments: [{ sourcing_mode: "stock", quantity: "1", reserved_quantity: "1" }] }]
    } });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    expect(node.textContent).toContain("تغليف مستثنى · مستثنى");
    expect(node.textContent).toContain("التكلفة المعروفة فقط");
    expect(node.textContent).toContain("سلسال غير محسوم");
    expect(node.textContent).toContain("التوريد: من المخزون · الكمية: 1");
    expect(node.textContent).toContain("يحتاج مصدر توريد: 2");
    expect(node.textContent).toContain("حساب الكمية: 3 لكل اختيار × 1 كمية سلة = 3");
    expect(node.textContent).not.toContain("bundle@3:rule2");
    expect(node.textContent).toContain("تركيب المنتج الداخلي");
    expect(node.textContent).not.toContain("product_resource:wrap");
    expect(node.textContent).not.toContain("technical-supplier-uuid");
});

test("failed product details block saving and do not claim a verified unit", async () => {
    getProductV2.mockImplementation((id) => id === "bundle" ? Promise.resolve({ options: [] }) : Promise.reject(new Error("offline")));
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await inputValue(node.querySelector('[aria-label="كمية المكوّن 1"]'), "2");
    expect(button("حفظ التركيب وحساب التكلفة").disabled).toBe(true);
    expect(node.textContent).toContain("بانتظار التحقق من كتالوج المنتج");
});

test("explicit extra rule receives a distinct id and requires a selected condition", async () => {
    bundles.saveProductBundle.mockResolvedValue({ definition: { revision: 4, product_type: "composite", items }, expansion: null });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await act(async () => button("إضافة قاعدة أخرى لهذا المنتج").click());
    expect(node.querySelectorAll('[aria-label^="كمية المكوّن"]')).toHaveLength(5);
    expect(button("حفظ التركيب وحساب التكلفة").disabled).toBe(true);
    await selectValue(labeledSelect("حقل خيار العميل"), "size");
    await selectValue(labeledSelect("قيمة خيار العميل"), "4");
    await act(async () => button("حفظ التركيب وحساب التكلفة").click());
    const saved = bundles.saveProductBundle.mock.calls[0][1].items;
    expect(saved[4].product_id).toBe(saved[0].product_id);
    expect(saved[4].rule_id).not.toBe(saved[0].rule_id);
    expect(saved[4].condition).toEqual({ option_id: "size", value_id: "4" });
});

test("linked product variant, option and text mappings save stable parent and child identifiers", async () => {
    getProductV2.mockImplementation((id) => Promise.resolve(id === "bundle" ? { options: [{ id: "size", name: "المقاس", values: [{ id: "M", name: "M" }] }, { id: "gift", name: "الإهداء", type: "text" }] } : { variants: [{ id: "child-M", name: "نسخة M" }], options: [{ id: "child-size", name: "مقاس داخلي", values: [{ id: "medium", name: "متوسط" }] }, { id: "engraving", name: "نص الحفر", type: "text" }] }));
    bundles.saveProductBundle.mockResolvedValue({ definition: { revision: 4, product_type: "composite", items }, expansion: null });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await act(async () => button("خيارات المنتج الداخلي").click());
    await act(async () => button("إضافة ربط نسخة المنتج").click());
    expect(button("حفظ التركيب وحساب التكلفة").disabled).toBe(true);
    await selectValue(labeledSelect("حقل البكج للنسخة"), "size");
    await selectValue(labeledSelect("قيمة البكج للنسخة"), "M");
    await selectValue(labeledSelect("النسخة المطلوبة"), "child-M");
    await act(async () => button("إضافة ربط خيار داخلي").click());
    await selectValue(labeledSelect("حقل البكج المصدر"), "size");
    await selectValue(labeledSelect("قيمة البكج المصدر"), "M");
    await selectValue(labeledSelect("حقل المنتج الهدف"), "child-size");
    await selectValue(labeledSelect("قيمة المنتج الهدف"), "medium");
    await act(async () => button("إضافة ربط نص تخصيص").click());
    await selectValue(labeledSelect("نص البكج المصدر"), "gift");
    await selectValue(labeledSelect("نص المنتج الهدف"), "engraving");
    await act(async () => button("حفظ التركيب وحساب التكلفة").click());
    const saved = bundles.saveProductBundle.mock.calls[0][1].items[0];
    expect(saved.variant_rules).toEqual([{ conditions: [{ option_id: "size", value_id: "M" }], variant_id: "child-M" }]);
    expect(saved.option_mappings).toEqual([{ option_id: "size", value_id: "M", target_option_id: "child-size", target_value_id: "medium" }]);
    expect(saved.customization_mappings).toEqual([{ option_id: "gift", target_option_id: "engraving" }]);
});

test("mapped child customization text is forwarded in preview without unrelated answers", async () => {
    getProductV2.mockResolvedValue({ options: [{ id: "gift", name: "الإهداء", type: "text" }, { id: "note", name: "ملاحظة", type: "text" }] });
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 1, product_type: "composite", items: [{ ...items[0], customization_mappings: [{ option_id: "gift", target_option_id: "engraving" }] }] }, expansion: null, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    bundles.previewProductBundle.mockResolvedValue({ expansion: { leaves: [] } });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await inputValue(node.querySelector('[aria-label="الإهداء"]'), "أحبك");
    await inputValue(node.querySelector('[aria-label="ملاحظة"]'), "سري");
    await act(async () => button("معاينة المكونات والتكلفة للاختيارات").click());
    expect(bundles.previewProductBundle.mock.calls[0][3].option_answers).toEqual({ gift: "أحبك" });
});

test("products use catalog units without a manual unit field", async () => {
    getProductV2.mockResolvedValue({ name: "منتج كتالوج", unit: "unit", options: [] });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    expect([...node.querySelectorAll("label")].some((label) => label.firstChild?.textContent === "الوحدة" && label.querySelector("input"))).toBe(false);
    expect(node.textContent).toContain("قطعة");
});

test("saved product identity, quantity and condition survive a fresh mount", async () => {
    const savedRow = { ...items[0], rule_id: "persisted-rule", quantity: "3", condition: { option_id: "size", value_id: "4" } };
    const persisted = { revision: 4, schema_version: 2, product_type: "composite", items: [savedRow] };
    bundles.getProductBundle.mockResolvedValue({ definition: { revision: 3, product_type: "composite", items: [items[0]] }, expansion: null, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    bundles.saveProductBundle.mockResolvedValue({ definition: persisted, expansion: null });
    getProductV2.mockResolvedValue({ name: "سلسال ثابت", main_image: "/saved-product.png", options: [{ id: "size", name: "حجم البكج", values: [{ id: "4", name: "أربعة" }] }] });
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    await inputValue(node.querySelector('[aria-label="كمية المكوّن 1"]'), "3");
    await selectValue(labeledSelect("إضافة المكوّن"), "conditional");
    await selectValue(labeledSelect("حقل خيار العميل"), "size");
    await selectValue(labeledSelect("قيمة خيار العميل"), "4");
    await act(async () => button("حفظ التركيب وحساب التكلفة").click());
    expect(bundles.saveProductBundle.mock.calls[0][1].items[0]).toMatchObject({ quantity: "3", product_id: "p1", condition: { option_id: "size", value_id: "4" } });
    bundles.getProductBundle.mockResolvedValue({ definition: persisted, expansion: null, limits: { max_items: 100, max_depth: 5, max_leaves: 500 } });
    await act(async () => root.unmount()); root = createRoot(node);
    await act(async () => root.render(<BundleEditor productId="bundle" />));
    expect(node.textContent).toContain("سلسال ثابت");
    expect(node.querySelector('img[src="/saved-product.png"]')).not.toBeNull();
    expect(node.querySelector('[aria-label="كمية المكوّن 1"]').value).toBe("3");
    expect(labeledSelect("حقل خيار العميل").value).toBe("size");
    expect(labeledSelect("قيمة خيار العميل").value).toBe("4");
    expect(button("حفظ التركيب وحساب التكلفة").disabled).toBe(true);
});
