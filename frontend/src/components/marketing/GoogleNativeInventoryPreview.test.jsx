import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import fixture from "../../services/__fixtures__/googleNativeInventoryPreview.json";
import GoogleNativeInventoryPreview from "./GoogleNativeInventoryPreview";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
const clone = (value) => JSON.parse(JSON.stringify(value));
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); api.post.mockResolvedValue({ data: clone(fixture.response) });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleNativeInventoryPreview plan={fixture.plan} category={fixture.category} isOwner {...props} />)); }
async function select(ids = ["1", "2", "3"]) { for (const id of ids) await act(async () => container.querySelector(`[aria-label="فحص مخزون المنتج ${id}"]`).click()); }
async function read() { await act(async () => container.querySelector("button").click()); }

test("explicit owner selections request no operation until button; display raw parent and variant observations separately", async () => {
    await render(); expect(container.querySelector("button").disabled).toBe(true); expect(api.post).not.toHaveBeenCalled();
    await select(); expect(api.post).not.toHaveBeenCalled(); await read();
    expect(api.post).toHaveBeenCalledTimes(1); expect(api.put).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
    expect(container.querySelectorAll("[data-native-product-id]")).toHaveLength(3);
    expect(container.querySelector('[data-native-product-id="1"] [data-parent-stock]').textContent).toContain("صفر مع حالة غير محدود مجهولة؛ لا يثبت النفاد");
    expect(container.querySelector('[data-native-variant-id="7"]').textContent).toContain("الكمية: 2");
    expect(container.textContent).toContain("Size: Large"); expect(container.textContent).toContain("وسم النفاد: نعم");
    expect(container.textContent).toContain("غير محدود: نعم بحسب المصدر");
    expect(container.textContent).toContain("رُصدت بنية منتج بسيط");
    expect(container.textContent).toContain("مدة التجهيز غير معلومة"); expect(container.textContent).toContain("لا يمنح هذا الفحص اختيارًا إعلانيًا");
    expect(container.textContent).toContain(fixture.response.observed_finished_at); expect(container.querySelector("a")).toBeNull();
});
test.each([false, undefined, null, 1])("non-owner %s cannot request", async (isOwner) => { await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.post).not.toHaveBeenCalled(); });
test("dirty plan and unresolved exclusion identities cannot request native observations", async () => {
    await render({ planDirty: true }); expect(container.querySelector("button")).toBeNull();
    const plan = clone(fixture.plan), category = clone(fixture.category); plan.fields.excluded_product_ids = ["internal-id"]; category.unresolved_exclusion_ids = ["internal-id"];
    await render({ plan, category }); expect(container.querySelector("button")).toBeNull(); expect(api.post).not.toHaveBeenCalled();
});
test("excluded checkboxes are disabled and five explicit requested products is the maximum", async () => {
    const plan = clone(fixture.plan), category = clone(fixture.category); plan.fields.excluded_product_ids = ["2"];
    category.products = Array.from({ length: 7 }, (_, index) => ({ ...category.products[0], salla_product_id: String(index + 1), excluded: index === 1 })); category.products_observed = 7;
    await render({ plan, category }); expect(container.querySelector('[aria-label="فحص مخزون المنتج 2"]').disabled).toBe(true);
    await select(["1", "3", "4", "5", "6"]); expect(container.querySelector('[aria-label="فحص مخزون المنتج 7"]').disabled).toBe(true);
    expect(container.textContent).toContain("المحدد للفحص: 5 من 5"); expect(api.post).not.toHaveBeenCalled();
});
test.each(["revision", "same-revision-fields", "plan", "owner", "dirty", "category", "refresh", "selection"])("%s change cancels and discards a late response", async (kind) => {
    let finish; api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await select(); await read(); const signal = api.post.mock.calls[0][2].signal;
    if (kind === "selection") await select(["3"]);
    else if (kind === "owner") await render({ isOwner: false });
    else if (kind === "dirty") await render({ planDirty: true });
    else if (["category", "refresh"].includes(kind)) await render({ category: { ...fixture.category, ...(kind === "category" ? { category_id: "9" } : { response_digest: "a".repeat(64) }) } });
    else await render({ plan: { ...fixture.plan, ...(kind === "revision" ? { revision: 4 } : kind === "plan" ? { plan_id: "other" } : { fields: { ...fixture.plan.fields, message: "رسالة جديدة" } }) } });
    expect(signal.aborted).toBe(true); await act(async () => finish({ data: fixture.response })); expect(container.querySelector("[data-native-inventory-result]")).toBeNull();
});
test("single request and unmount abort", async () => {
    let finish; api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await select(); await read(); await read(); expect(api.post).toHaveBeenCalledTimes(1); const signal = api.post.mock.calls[0][2].signal;
    await act(async () => root.render(null)); expect(signal.aborted).toBe(true); await act(async () => finish({ data: fixture.response })); expect(container.textContent).toBe("");
});
test.each(["permission_unproved", "native_read_unavailable"])("%s is scoped to this read without relink or raw detail", async (reason) => {
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: { code: "google_native_inventory_unavailable", reason, message: "private detail" } } } });
    await render(); await select(); await read(); expect(container.textContent).toContain("لم تتغير إعدادات الربط"); expect(container.textContent).not.toContain("private detail");
    expect(container.querySelector("a")).toBeNull(); expect(container.querySelector("button").disabled).toBe(false); expect(api.put).not.toHaveBeenCalled();
});
test.each(["revision", "malformed"])("%s response is not displayed and locks pending saved-scope refresh", async (kind) => {
    if (kind === "revision") api.post.mockRejectedValue({ response: { status: 409, data: { detail: { code: "google_commercial_plan_revision_conflict" } } } });
    else api.post.mockResolvedValue({ data: { ...fixture.response, globally_ready: true } });
    await render(); await select(); await read(); expect(container.querySelector("[data-native-inventory-result]")).toBeNull(); expect(container.querySelector("button").disabled).toBe(true);
});
test("partial traversal and unbound option labels stay explicit", async () => {
    const data = clone(fixture.response); data.category_traversal_complete = false; data.products[0].variant_traversal_complete = false; data.products[0].variants[0].source_option_labels = null;
    data.products[0].blocking_reasons = data.products[0].blocking_reasons.filter((reason) => reason !== "native_option_value_out_of_stock");
    api.post.mockResolvedValue({ data }); await render(); await select(); await read();
    expect(container.textContent).toContain("إعادة تصفح القسم جزئية"); expect(container.textContent).toContain("تصفح المتغيرات جزئي"); expect(container.textContent).toContain("ربط تسميات الخيارات غير مثبت");
});
