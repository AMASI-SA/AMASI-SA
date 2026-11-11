import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { toast } from "sonner";
import ProductOptionCostEditor from "./ProductOptionCostEditor";
import { deleteProductOptionCost, getProductOptionCosts, refreshProductV2Details, saveProductOptionCost } from "../../services/mezanProductsV2";

jest.mock("../../services/mezanProductsV2", () => ({
    deleteProductOptionCost: jest.fn(), getProductOptionCosts: jest.fn(),
    refreshProductV2Details: jest.fn(), saveProductOptionCost: jest.fn(),
}));
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

const options = [
    { id: "name", name: "الاسم", type: "text", values: [] },
    { id: "embroider", name: "هل تريد تطريز الاسم على الشال", type: "radio", values: [{ id: "yes", name: "نعم", additional_price: 10 }] },
];
const legacy = { id: "legacy-name", option_id: "name", value_id: "__option__", option_name: "الاسم", value_name: "الاسم", mode: "direct", direct_amount: 10, resolved_amount: 10 };
const service = { id: "embroidery", option_id: "embroider", value_id: "yes", option_name: options[1].name, value_name: "نعم", mode: "resource", resource_id: "service", quantity: 1, resource: { name: "تطريز الاسم" }, resolved_amount: 8 };
const costs = (bindings = [legacy, service]) => ({ bindings, resources: [{ id: "service", name: "تطريز الاسم", unit_cost: 8, status: "active" }] });
const deferred = () => { let resolve, reject; const promise = new Promise((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; };
let node, root;
const button = (label, parent = node) => [...parent.querySelectorAll("button")].find((item) => item.textContent.trim() === label);
const costRow = (text) => [...node.querySelectorAll('[role="group"]')].find((item) => item.textContent.includes(text));
const dialog = () => node.querySelector('[role="dialog"]');

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.resetAllMocks();
    getProductOptionCosts.mockResolvedValue(costs());
    refreshProductV2Details.mockResolvedValue({ product: { options, custom_fields: [] } });
    saveProductOptionCost.mockResolvedValue({ ok: true });
    deleteProductOptionCost.mockResolvedValue({ ok: true, deleted: 1 });
    node = document.createElement("div");
    root = createRoot(node);
});
afterEach(async () => act(async () => root.unmount()));
async function render(props = {}) {
    await act(async () => root.render(<ProductOptionCostEditor productId="shawl" options={options} {...props} />));
}
async function click(label, parent = node) { await act(async () => button(label, parent).click()); }
async function setAmount(value) {
    const input = dialog().querySelector('input[type="number"]');
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
}

test("legacy option-level direct cost is visible beside the separate embroidery service", async () => {
    await render();
    expect(node.textContent).toContain("+10.00 ر.س");
    expect(node.textContent).toContain("تطريز الاسم · +8.00 ر.س");
    expect(costRow("+10.00").textContent).toContain("عند ورود الخيار في الطلب");
    expect(node.textContent).not.toContain("لا توجد تكلفة إضافية");
    expect(button("إضافة تكلفة")).toBeUndefined();
    expect(saveProductOptionCost).not.toHaveBeenCalled();
    expect(deleteProductOptionCost).not.toHaveBeenCalled();
});

test("editing a legacy binding preserves its stored identity and option-present meaning", async () => {
    await render();
    await click("تعديل التكلفة", costRow("+10.00"));
    expect(dialog().textContent).toContain("عند ورود هذا الخيار في الطلب");
    expect(dialog().textContent).not.toContain("قيمة غير فارغة");
    await setAmount("12");
    await click("حفظ", dialog());
    expect(saveProductOptionCost).toHaveBeenCalledWith("shawl", "name", "__option__", {
        mode: "direct", resource_id: null, direct_amount: "12", quantity: 1,
    });
});

test("deleting the legacy cost sends only its exact key and keeps the separate service", async () => {
    getProductOptionCosts.mockResolvedValueOnce(costs()).mockResolvedValue(costs([service]));
    await render();
    await click("تعديل التكلفة", costRow("+10.00"));
    await click("حذف", dialog());
    expect(deleteProductOptionCost).toHaveBeenCalledTimes(1);
    expect(deleteProductOptionCost).toHaveBeenCalledWith("shawl", "name", "__option__");
    expect(node.textContent).not.toContain("+10.00 ر.س");
    expect(node.textContent).toContain("تطريز الاسم · +8.00 ر.س");
});

test("coexisting legacy and filled costs stay separate with a visible conflict", async () => {
    const filled = { ...legacy, id: "filled-name", value_id: "filled", direct_amount: 3, resolved_amount: 3 };
    getProductOptionCosts.mockResolvedValue(costs([legacy, filled, service]));
    await render();
    expect(node.querySelector('[role="alert"]').textContent).toContain("أكثر من ربط تكلفة");
    expect(costRow("+10.00")).not.toBe(costRow("+3.00"));
    await click("تعديل التكلفة", costRow("+3.00"));
    expect(dialog().textContent).toContain("قيمة غير فارغة");
    await click("حفظ", dialog());
    expect(saveProductOptionCost.mock.calls[0].slice(0, 3)).toEqual(["shawl", "name", "filled"]);
    expect(deleteProductOptionCost).not.toHaveBeenCalled();
});

test("saved costs for removed values and removed options remain visible and deletable", async () => {
    const removedValue = { ...legacy, id: "old-choice", option_id: "embroider", value_id: "removed", value_name: "اختيار سابق", resolved_amount: 7 };
    const removedOption = { ...legacy, id: "old-option", option_id: "gone", option_name: "خيار سابق", value_id: "old-value", value_name: "قيمة سابقة", resolved_amount: 6 };
    getProductOptionCosts.mockResolvedValue(costs([service, removedValue, removedOption]));
    await render();
    expect(node.textContent).toContain("+7.00 ر.س");
    expect(node.textContent).toContain("+6.00 ر.س");
    await click("مراجعة الربط", costRow("+6.00"));
    expect(dialog().textContent).toContain("غير موجود");
    expect(button("حفظ", dialog()).disabled).toBe(true);
    await click("حذف", dialog());
    expect(deleteProductOptionCost).toHaveBeenCalledWith("shawl", "gone", "old-value");
});

test("pending reads never describe the field as free or enable cost writes", async () => {
    const pending = deferred();
    getProductOptionCosts.mockReturnValue(pending.promise);
    await render();
    expect(node.querySelector('[role="status"]').textContent).toContain("تحميل");
    expect(node.textContent).not.toContain("لا توجد تكلفة إضافية");
    expect(button("إضافة تكلفة")).toBeUndefined();
    await act(async () => pending.resolve(costs()));
    expect(node.textContent).toContain("+10.00 ر.س");
});

test.each(["costs", "details"])("a failed %s read is explicit and retry cannot imply a zero cost", async (failure) => {
    (failure === "costs" ? getProductOptionCosts : refreshProductV2Details).mockRejectedValueOnce(new Error("offline"));
    await render();
    expect(node.querySelector('[role="alert"]').textContent).toContain("تعذر تحميل");
    expect(node.textContent).not.toContain("لا توجد تكلفة إضافية");
    expect(button("إضافة تكلفة")).toBeUndefined();
    await click("إعادة تحميل التكاليف");
    expect(node.textContent).toContain("+10.00 ر.س");
});

test("a product switch hides previous costs immediately and ignores the old response", async () => {
    const old = deferred(), next = deferred();
    getProductOptionCosts.mockImplementation((id) => id === "old" ? old.promise : next.promise);
    await render({ productId: "old" });
    await render({ productId: "new" });
    await act(async () => old.resolve(costs()));
    expect(node.textContent).not.toContain("+10.00 ر.س");
    expect(node.textContent).not.toContain("لا توجد تكلفة إضافية");
    await act(async () => next.resolve(costs([service])));
    expect(node.textContent).not.toContain("+10.00 ر.س");
    expect(node.textContent).toContain("+8.00 ر.س");
    const third = deferred();
    getProductOptionCosts.mockReturnValue(third.promise);
    await render({ productId: "third" });
    expect(node.textContent).not.toContain("+8.00 ر.س");
    expect(node.textContent).not.toContain("لا توجد تكلفة إضافية");
});

test("overlapping refreshes for the same product retain only the latest successful read", async () => {
    const old = deferred(), latest = deferred();
    getProductOptionCosts.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise);
    await render();
    await act(async () => window.dispatchEvent(new CustomEvent("mezan:product-resource-links-changed", { detail: { productId: "shawl" } })));
    await act(async () => latest.resolve(costs([service])));
    expect(node.textContent).toContain("+8.00 ر.س");
    await act(async () => old.resolve(costs()));
    expect(node.textContent).not.toContain("+10.00 ر.س");
    expect(node.textContent).toContain("+8.00 ر.س");
});

test("a malformed costs response is an error rather than an empty binding list", async () => {
    getProductOptionCosts.mockResolvedValue({ resources: [] });
    await render();
    expect(node.querySelector('[role="alert"]').textContent).toContain("تعذر تحميل");
    expect(node.textContent).not.toContain("لا توجد تكلفة إضافية");
    expect(button("إضافة تكلفة")).toBeUndefined();
});

test("an old product save cannot close the new editor or reload old costs", async () => {
    const pendingSave = deferred();
    saveProductOptionCost.mockReturnValue(pendingSave.promise);
    await render({ productId: "old" });
    await click("تعديل التكلفة", costRow("+10.00"));
    await click("حفظ", dialog());
    await render({ productId: "new" });
    await click("تعديل التكلفة", costRow("+8.00"));
    await act(async () => pendingSave.resolve({ ok: true }));
    expect(dialog().textContent).toContain("هل تريد تطريز الاسم");
    expect(getProductOptionCosts.mock.calls.map(([id]) => id)).toEqual(["old", "new"]);
    expect(toast.success).not.toHaveBeenCalled();
});

test("a failed delete keeps the exact binding available for retry", async () => {
    deleteProductOptionCost.mockRejectedValueOnce(new Error("offline"));
    await render();
    await click("تعديل التكلفة", costRow("+10.00"));
    await click("حذف", dialog());
    expect(dialog()).not.toBeNull();
    expect(button("حذف", dialog()).disabled).toBe(false);
    expect(toast.error).toHaveBeenCalledWith("تعذر حذف التكلفة. أعد المحاولة.");
    expect(node.textContent).toContain("+10.00 ر.س");
    expect(toast.success).not.toHaveBeenCalled();
});

test("Salla additional sale price is not represented as an extra cost without a saved binding", async () => {
    getProductOptionCosts.mockResolvedValue(costs([]));
    await render();
    expect(node.textContent).toContain("لا توجد تكلفة إضافية");
    expect(node.textContent).not.toContain("+10.00 ر.س");
    expect(saveProductOptionCost).not.toHaveBeenCalled();
});

test("an explicit zero custom-field binding keeps its filled condition and exact field identity", async () => {
    const field = { id: "gift", name: "رسالة الهدية", type: "textarea", cost_subject_id: "field:gift", cost_value_id: "filled" };
    const zero = { ...legacy, id: "gift-cost", option_id: "field:gift", value_id: "filled", direct_amount: 0, resolved_amount: 0 };
    refreshProductV2Details.mockResolvedValue({ product: { options: [], custom_fields: [field] } });
    getProductOptionCosts.mockResolvedValue(costs([zero]));
    await render();
    expect(node.textContent).toContain("رسالة الهدية");
    expect(node.textContent).toContain("+0.00 ر.س");
    expect(node.textContent).not.toContain("لا توجد تكلفة إضافية");
    await click("تعديل التكلفة", costRow("+0.00"));
    expect(dialog().textContent).toContain("قيمة غير فارغة");
    await click("حفظ", dialog());
    expect(saveProductOptionCost.mock.calls[0].slice(0, 3)).toEqual(["shawl", "field:gift", "filled"]);
});

test("the alias conflict response stays visible without silently replacing another cost", async () => {
    getProductOptionCosts.mockResolvedValue(costs([]));
    saveProductOptionCost.mockRejectedValue({ response: { data: { detail: { code: "option_cost_binding_conflict" } } } });
    await render();
    await click("إضافة تكلفة", costRow("عند تعبئة الحقل"));
    await click("حفظ", dialog());
    expect(toast.error.mock.calls[0][0]).toContain("ربط تكلفة محفوظ");
    expect(dialog()).not.toBeNull();
    expect(deleteProductOptionCost).not.toHaveBeenCalled();
});
