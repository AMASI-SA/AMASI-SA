import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProductOperationsEditor from "./ProductOperationsEditor";
import ProductOptionCostEditor from "./ProductOptionCostEditor";
import MezanComponentsProduction from "../../pages/MezanComponentsProduction";
import * as products from "../../services/mezanProductsV2";
import * as catalog from "../../services/mezanComponentCatalog";
import { toast } from "sonner";

jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("../../services/mezanProductsV2", () => ({
    getProductOperations: jest.fn(), linkProductGroups: jest.fn(), linkProductResource: jest.fn(),
    shareProductComponent: jest.fn(), unlinkProductGroup: jest.fn(), unlinkProductResource: jest.fn(),
    getProductOptionCosts: jest.fn(), refreshProductV2Details: jest.fn(), saveProductOptionCost: jest.fn(), deleteProductOptionCost: jest.fn(),
}));
jest.mock("../../services/productCostSetup", () => ({ saveProductCostCategory: jest.fn(), saveProductCostCompletion: jest.fn() }));
jest.mock("../../services/mezanComponentCatalog", () => ({
    ...jest.requireActual("../../services/mezanComponentCatalog"), getMezanComponentWorkspace: jest.fn(),
    addMezanComponentPreview: jest.fn(), saveMezanComponentPreviewCost: jest.fn(),
}));

const option = { id: "gift", name: "تغليف هدية", values: [{ id: "yes", name: "نعم" }] };
function fixture() {
    return {
        ok: true, product: { id: "product", cost_category_id: "clothes", cost_category_name: "ملابس" },
        cost_setup: { cost_category_id: "clothes", cost_category_name: "ملابس" }, groups: [], product_links: [],
        categories: [{ id: "clothes", name: "ملابس" }, { id: "packing", name: "تغليف" }],
        resources: [
            { id: "bag", name: "كيس ملابس", kind: "stock_component", track_inventory: true, category_ids: ["clothes"], unit_cost: 2 },
            { id: "name", name: "تطريز خاص", kind: "service", track_inventory: false, category_ids: ["clothes"], unit_cost: 10 },
            { id: "box", name: "صندوق خشبي", kind: "stock_component", track_inventory: true, category_ids: ["packing"], unit_cost: 35 },
            { id: "wrap", name: "خدمة تغليف", kind: "service", track_inventory: false, category_ids: ["packing"], unit_cost: 4 },
        ],
    };
}
let node, root, data;
const select = label => node.querySelector(`select[aria-label="${label}"]`);
const button = text => [...node.querySelectorAll("button")].find(row => row.textContent.trim() === text);
const options = label => [...select(label).options].map(row => row.value).filter(Boolean);
const render = component => act(async () => root.render(component));
const click = element => act(async () => element.click());
const change = (element, value) => act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(element, value);
    element.dispatchEvent(new Event("change", { bubbles: true }));
});
const input = (element, value) => act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
});
beforeEach(() => {
    jest.clearAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); data = fixture();
    products.getProductOperations.mockResolvedValue(data);
    products.linkProductResource.mockResolvedValue(data);
    products.getProductOptionCosts.mockResolvedValue({ bindings: [], resources: data.resources, cost_category_id: "clothes" });
    products.refreshProductV2Details.mockResolvedValue({ product: { options: [option] } });
    products.saveProductOptionCost.mockResolvedValue({ ok: true });
    catalog.getMezanComponentWorkspace.mockResolvedValue({ components: data.resources, categories: data.categories });
});
afterEach(() => { act(() => root.unmount()); node.remove(); });

test("product resource selectors keep components and services separate inside the current category", async () => {
    await render(<ProductOperationsEditor productId="product" />);
    expect(options("مكونات تصنيف المنتج")).toEqual(["resource:bag"]);
    expect(options("خدمات تصنيف المنتج")).toEqual(["resource:name"]);
    expect(button("إضافة المكوّن").disabled).toBe(true);
    await change(select("خدمات تصنيف المنتج"), "resource:name");
    await click(button("إضافة الخدمة"));
    expect(products.linkProductResource).toHaveBeenCalledWith("product", "name", 1);
    expect(products.shareProductComponent).not.toHaveBeenCalled();
});

test("selecting another category lists its components only and saves one share of the same component", async () => {
    products.shareProductComponent.mockResolvedValue({ ...data, shared_resource_id: "box", shared_category_id: "clothes" });
    await render(<ProductOperationsEditor productId="product" />);
    await click(button("إضافة مكوّن من تصنيف آخر"));
    expect(options("التصنيف الآخر")).toEqual(["packing"]);
    expect(select("مكوّنات التصنيف").disabled).toBe(true);
    await change(select("التصنيف الآخر"), "packing");
    expect(options("مكوّنات التصنيف")).toEqual(["box"]);
    await change(select("مكوّنات التصنيف"), "box");
    await click(button("حفظ ومشاركة المكوّن"));
    expect(products.shareProductComponent).toHaveBeenCalledTimes(1);
    expect(products.shareProductComponent).toHaveBeenCalledWith("product", "box", "packing", "clothes");
    expect(node.querySelector('[role="dialog"]')).toBeNull();
});

test("canceling category sharing does not add a resource or change classifications", async () => {
    await render(<ProductOperationsEditor productId="product" />);
    await click(button("إضافة مكوّن من تصنيف آخر"));
    await change(select("التصنيف الآخر"), "packing");
    await click(button("إلغاء"));
    expect(products.shareProductComponent).not.toHaveBeenCalled();
    expect(products.linkProductResource).not.toHaveBeenCalled();
});

test("a stale classification response leaves the share selection open and reports its message", async () => {
    products.shareProductComponent.mockRejectedValue({ response: { data: { detail: { code: "product_cost_category_changed", message: "تغيّر التصنيف" } } } });
    await render(<ProductOperationsEditor productId="product" />);
    await click(button("إضافة مكوّن من تصنيف آخر"));
    await change(select("التصنيف الآخر"), "packing");
    await change(select("مكوّنات التصنيف"), "box");
    await click(button("حفظ ومشاركة المكوّن"));
    expect(node.querySelector('[role="dialog"]')).not.toBeNull();
    expect(toast.error).toHaveBeenCalledWith("تغيّر التصنيف");
});

test("no product category means no broad resource chooser or cross-category sharing", async () => {
    products.getProductOperations.mockResolvedValue({ ...data, cost_setup: {}, product: {} });
    await render(<ProductOperationsEditor productId="product" />);
    expect(select("مكونات تصنيف المنتج")).toBeNull();
    expect(button("إضافة مكوّن من تصنيف آخر")).toBeUndefined();
    expect(node.textContent).toContain("حدد تصنيف المنتج أولًا");
});

test("option-cost editor offers independent component and service selections without foreign-category resources", async () => {
    await render(<ProductOptionCostEditor productId="product" options={[option]} />);
    await click(button("إضافة تكلفة"));
    expect(options("مكوّن خيار العميل")).toEqual(["bag"]);
    await click(button("خدمة"));
    expect(select("مكوّن خيار العميل")).toBeNull();
    expect(options("خدمة خيار العميل")).toEqual(["name"]);
    await click(button("حفظ"));
    expect(products.saveProductOptionCost).toHaveBeenCalledWith("product", "gift", "yes", {
        mode: "resource", resource_id: "name", direct_amount: null, quantity: 1,
    });
});

test("existing option links remain visible even when their resource belongs to a previous classification", async () => {
    products.getProductOptionCosts.mockResolvedValue({ resources: data.resources, cost_category_id: "clothes", bindings: [
        { option_id: "gift", value_id: "yes", mode: "resource", resource_id: "box", quantity: 1, resource: data.resources[2] },
    ] });
    await render(<ProductOptionCostEditor productId="product" options={[option]} />);
    await click(button("تعديل التكلفة"));
    expect(select("مكوّن خيار العميل").value).toBe("box");
    expect(options("مكوّن خيار العميل")).toEqual(["bag", "box"]);
    expect(products.saveProductOptionCost).not.toHaveBeenCalled();
});

test("general catalog separates components and services through its selector", async () => {
    await render(<MezanComponentsProduction />);
    expect(select("عرض الكتالوج").value).toBe("component");
    expect(node.textContent).toContain("صندوق خشبي");
    expect(node.textContent).not.toContain("تطريز خاص");
    await change(select("عرض الكتالوج"), "service");
    expect(node.textContent).toContain("تطريز خاص");
    expect(node.textContent).not.toContain("صندوق خشبي");
});

test("creating a service from the components catalog never saves its cost to the visible component", async () => {
    const component = { id: "component-a", name: "مكوّن أصلي", code: "A", kind: "stock_component",
        track_inventory: true, initial_unit_cost: 2, status: "active", category_ids: ["clothes"] };
    const service = { id: "service-b", name: "خدمة جديدة", code: "B", kind: "service",
        track_inventory: false, reference_cost: { amount: 50 }, status: "active", category_ids: ["clothes"] };
    catalog.getMezanComponentWorkspace.mockResolvedValueOnce({ components: [component], categories: data.categories })
        .mockResolvedValue({ components: [component, service], categories: data.categories });
    catalog.addMezanComponentPreview.mockResolvedValue({ ok: true, resource: service });
    catalog.saveMezanComponentPreviewCost.mockResolvedValue({ ok: true });
    await render(<MezanComponentsProduction />);
    expect(node.querySelector("main h2").textContent).toBe(component.name);
    expect(node.querySelector('main input[type="number"]').value).toBe("2");
    await click(button("إضافة مكوّن أو خدمة"));
    const form = node.querySelector("form");
    await change(form.querySelector("select"), "service");
    await input(form.querySelector('input[placeholder="مثال: تطريز الاسم"]'), service.name);
    await input(form.querySelector('input[placeholder="مثال: EMBROIDERY_NAME"]'), service.code);
    await click(form.querySelector('fieldset input[type="checkbox"]'));
    await input(form.querySelector('input[type="number"]'), "50");
    await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(catalog.addMezanComponentPreview).toHaveBeenCalledWith(expect.objectContaining({ kind: "service", unit_cost: 50 }));
    expect(node.querySelector("form")).toBeNull();
    const visibleName = node.querySelector("main h2").textContent;
    expect([component.name, service.name]).toContain(visibleName);
    const expected = visibleName === component.name ? { id: component.id, amount: 2 } : { id: service.id, amount: 50 };
    expect(node.querySelector('main input[type="number"]').value).toBe(String(expected.amount));
    await click([...node.querySelectorAll("main button")].find(row => row.textContent.trim() === "حفظ"));
    expect(catalog.saveMezanComponentPreviewCost).toHaveBeenCalledTimes(1);
    expect(catalog.saveMezanComponentPreviewCost).toHaveBeenCalledWith(expected.id, expected.amount);
});
