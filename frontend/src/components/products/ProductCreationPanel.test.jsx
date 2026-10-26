import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProductCreationPanel from "./ProductCreationPanel";
import { CHILD_SIZE_VALUES, creationOptionsError, removeCreationOption } from "./productCreationOptions";
import * as api from "../../services/mezanProductsV2";

// Jest 27 does not resolve Radix conditional exports. Load the package's
// actual default CommonJS module locally, without replacing the real dialog
// or changing module resolution for unrelated suites.
jest.mock("@radix-ui/primitive/is-development", () => {
    const path = require("path");
    const packagePath = path.resolve(path.dirname(require.resolve("@radix-ui/primitive")), "../package.json");
    let target = require(packagePath).exports["./is-development"];
    while (target && typeof target === "object") {
        target = target.require || target.node || target.default;
    }
    if (typeof target !== "string") throw new Error("Radix CommonJS export missing");
    return jest.requireActual(path.resolve(path.dirname(packagePath), target));
}, { virtual: true });

jest.mock("../../services/mezanProductsV2", () => ({
    listProductCreationDrafts: jest.fn(), createProductCreationDraft: jest.fn(),
    updateProductCreationDraft: jest.fn(), previewProductCreationDraft: jest.fn(),
    approveProductCreationDraft: jest.fn(), publishProductCreationDraft: jest.fn(),
}));
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));
jest.mock("@phosphor-icons/react", () => ({
    CheckCircle: () => null, Eye: () => null, Package: () => null,
    Plus: () => null, RocketLaunch: () => null, X: () => null,
}));

let container;
let root;

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    api.listProductCreationDrafts.mockResolvedValue({ items: [] });
    api.createProductCreationDraft.mockImplementation(async (payload) => ({ draft: { ...payload, id: "draft-1", status: "draft" } }));
    api.updateProductCreationDraft.mockImplementation(async (id, payload) => ({ draft: { ...payload, id, status: "draft" } }));
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
});

function button(text) {
    return [...container.querySelectorAll("button")].find((element) => element.textContent.trim() === text);
}

async function click(text) {
    await act(async () => button(text).click());
}

async function change(label, value) {
    const wrapper = [...container.querySelectorAll("label")].find((element) => element.querySelector("span")?.textContent === label);
    const input = wrapper.querySelector("input,select,textarea");
    await act(async () => {
        const prototype = Object.getPrototypeOf(input);
        Object.getOwnPropertyDescriptor(prototype, "value").set.call(input, value);
        input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}

async function check(label) {
    const wrapper = [...container.querySelectorAll("label")].find((element) => element.textContent.trim() === label);
    await act(async () => wrapper.querySelector('input[type="checkbox"]').click());
}

async function submit() {
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

test("creates a service with age sizes and customer text, then previews both", async () => {
    await act(async () => root.render(<ProductCreationPanel />));
    await click("منتج جديد");
    await change("اسم المنتج", "هودي بالاسم");
    await change("SKU فريد", "HOOD-100");
    await change("السعر", "239");
    await change("نوع المنتج في سلة", "service");
    await change("وزن الشحن (كجم)", "0.25");
    expect(container.textContent).toContain("خيارات التشغيل والمخزون مجمّدة مؤقتًا");
    await click("إضافة قائمة اختيارات");
    expect(container.querySelector('button[type="submit"]').disabled).toBe(true);
    await click("استخدام مقاسات سنة إلى 10 سنوات");
    await act(async () => container.querySelector('input[type="checkbox"]').click());
    await click("إضافة حقل نص");
    await change("اسم الخيار 2", "الاسم المطلوب");
    await submit();

    expect(api.createProductCreationDraft).toHaveBeenCalledTimes(1);
    expect(api.createProductCreationDraft.mock.calls[0][0]).toMatchObject({
        product_type: "service",
        weight: 0.25, fulfillment_type: "requires_preparation", inventory_policy: "finished_goods_inventory_not_tracked",
        options: [
            { name: "المقاس", type: "choice", required: true, values: CHILD_SIZE_VALUES },
            { name: "الاسم المطلوب", type: "text", required: false, values: [] },
        ],
    });
    const preview = container.querySelector('section[aria-label="معاينة خيارات العميل"]');
    expect(preview.querySelectorAll("option")).toHaveLength(11);
    expect(preview.textContent).toContain("10 سنوات");
    expect(preview.querySelector('input[placeholder="يكتب العميل النص هنا"]').readOnly).toBe(true);
});

test("editing an approved draft updates its type and options without losing media", async () => {
    const draft = {
        id: "approved-1", status: "approved", name: "منتج محفوظ", sku: "SAVED-1", price: 100,
        product_type: "service", fulfillment_type: "requires_preparation",
        weight: 0.25,
        inventory_policy: "branch_stock_required", stockout_policy: "close_when_out_of_stock",
        low_stock_threshold: 3, category_ids: [10],
        image_urls: ["https://cdn.example/1.png", "https://cdn.example/2.png"],
        options: [{ name: "المقاس", type: "choice", required: true, values: ["S", "M"] }],
    };
    api.listProductCreationDrafts.mockResolvedValue({ items: [draft] });
    await act(async () => root.render(<ProductCreationPanel />));
    await act(async () => [...container.querySelectorAll("button")].find((element) => element.textContent.includes("منتج محفوظ")).click());
    await click("تعديل المسودة");
    await change("نوع المنتج في سلة", "product");
    await click("إضافة قيمة جديدة للخيار 1");
    await change("قيمة 3 للخيار 1", "L");
    await submit();

    expect(api.createProductCreationDraft).not.toHaveBeenCalled();
    expect(api.updateProductCreationDraft).toHaveBeenCalledWith("approved-1", expect.objectContaining({
        product_type: "product", image_urls: draft.image_urls, category_ids: [10],
        options: [{ name: "المقاس", type: "choice", required: true, values: ["S", "M", "L"] }],
    }));
    expect(button("معاينة وفحص")).toBeTruthy();
    expect(button("إنشاء المنتج في سلة")).toBeUndefined();
});

test("duplicate choice values block saving and removing an option restores the form", async () => {
    await act(async () => root.render(<ProductCreationPanel />));
    await click("منتج جديد");
    await click("إضافة قائمة اختيارات");
    await change("اسم الخيار 1", "المقاس");
    await change("قيمة 1 للخيار 1", "S");
    await change("قيمة 2 للخيار 1", " S ");
    await submit();
    expect(api.createProductCreationDraft).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]').textContent).toContain("قيمتين مختلفتين");
    await click("حذف");
    expect(container.querySelector('[role="alert"]')).toBeNull();
});

test("gift text and its help appear and become required only when the customer chooses yes", async () => {
    await act(async () => root.render(<ProductCreationPanel />));
    await click("منتج جديد");
    await change("اسم المنتج", "هدية مخصصة");
    await change("SKU فريد", "GIFT-100");
    await change("السعر", "239");
    await change("نوع المنتج في سلة", "service");
    await change("وزن الشحن (كجم)", "0.25");
    await click("إضافة قائمة اختيارات");
    await change("اسم الخيار 1", "هل تريد إضافة كرت إهداء");
    await change("الوصف التوضيحي للخيار 1 (اختياري)", "اختر");
    await change("قيمة 1 للخيار 1", "نعم");
    await change("قيمة 2 للخيار 1", "لا بدون كرت إهداء");
    await check("إجابة مطلوبة للخيار 1");
    await click("إضافة حقل نص");
    await change("اسم الخيار 2", "كرت الإهداء");
    await change("الوصف التوضيحي للخيار 2 (اختياري)", "اكتب اللي تبيه على كرت الإهداء");
    await check("إجابة مطلوبة للخيار 2");
    await check("شرط ظهور الحقل 2");
    expect(container.querySelector('button[type="submit"]').disabled).toBe(true);
    await change("الحقل المرتبط بالخيار 2", "0");
    await change("قيمة الشرط للخيار 2", "نعم");
    await submit();

    const options = api.createProductCreationDraft.mock.calls[0][0].options;
    expect(options[0]).toMatchObject({ description: "اختر", values: ["نعم", "لا بدون كرت إهداء"] });
    expect(options[1]).toMatchObject({
        name: "كرت الإهداء", required: true, description: "اكتب اللي تبيه على كرت الإهداء",
        condition: { option_index: 0, operator: "=", value: "نعم" },
    });
    const preview = container.querySelector('section[aria-label="معاينة خيارات العميل"]');
    expect(preview.querySelector("input")).toBeNull();
    await change("هل تريد إضافة كرت إهداء · مطلوب", "نعم");
    expect(preview.querySelector("input").required).toBe(true);
    expect(preview.textContent).toContain("اكتب اللي تبيه على كرت الإهداء");
    await change("هل تريد إضافة كرت إهداء · مطلوب", "لا بدون كرت إهداء");
    expect(preview.querySelector("input")).toBeNull();
    await click("تعديل المسودة");
    await submit();
    expect(api.updateProductCreationDraft.mock.calls[0][1].options).toEqual(options);
});

test("removing a parent invalidates its conditions and cycles cannot be saved", () => {
    const options = [
        { name: "إهداء", type: "choice", required: true, values: ["نعم", "لا"] },
        { name: "النص", type: "text", required: true, values: [], condition: { option_index: 0, operator: "=", value: "نعم" } },
    ];
    expect(creationOptionsError(removeCreationOption(options, 0))).toContain("أكمل شرط الظهور");
    expect(creationOptionsError([
        { ...options[0], condition: { option_index: 1, operator: "=", value: "نعم" } },
        { ...options[0], name: "خيار ثان", condition: { option_index: 0, operator: "=", value: "نعم" } },
    ])).toContain("حلقة");
});
