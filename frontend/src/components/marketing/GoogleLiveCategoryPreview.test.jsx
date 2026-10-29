import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import fixture from "../../services/__fixtures__/googleLiveCategoryPreview.json";
import GoogleLiveCategoryPreview from "./GoogleLiveCategoryPreview";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() } }));
const clone = (value) => JSON.parse(JSON.stringify(value));
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset(); api.put.mockReset(); api.post.mockReset(); api.get.mockResolvedValue({ data: clone(fixture.response) });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleLiveCategoryPreview plan={fixture.plan} isOwner {...props} />)); }
async function read() { await act(async () => container.querySelector("button").click()); }
async function select(value) { await act(async () => { const item = container.querySelector("select"); item.value = value; item.dispatchEvent(new Event("change", { bubbles: true })); }); }

test("explicit owner read shows times membership and exclusions with no selection or save action", async () => {
    await render(); expect(api.get).not.toHaveBeenCalled(); await read();
    expect(container.querySelectorAll("[data-salla-product-id]")).toHaveLength(3);
    expect(container.textContent).toContain(fixture.response.observed_finished_at);
    expect(container.textContent).toContain("اكتمل تصفح الصفحات ضمن هذه القراءة");
    expect(container.textContent).toContain("لا تشكل لقطة متزامنة ثابتة");
    expect(container.textContent).toContain("مستبعد في الخطة");
    expect(container.textContent).toContain("internal-product");
    expect(container.textContent).toContain("المخزون غير مثبت");
    expect(container.textContent).toContain("غير مؤهل للاختيار الإعلاني");
    expect(api.put).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
    expect(container.querySelectorAll("button")).toHaveLength(1); expect(container.querySelector("a")).toBeNull();
});
test.each([false, undefined, null, 1])("non-owner %s cannot request preview", async (isOwner) => { await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled(); });
test.each(["dirty", "unsaved", "missing"])("%s plan makes no request", async (kind) => {
    await render(kind === "dirty" ? { planDirty: true } : { plan: { ...fixture.plan, ...(kind === "unsaved" ? { revision: 0 } : { fields: { ...fixture.plan.fields, category_ids: [] } }) } });
    expect(container.querySelector("button")).toBeNull(); expect(api.get).not.toHaveBeenCalled();
});
test.each(["revision", "same-revision-fields", "plan", "owner", "dirty"])("%s change aborts and discards late observation", async (kind) => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await read(); const signal = api.get.mock.calls[0][1].signal;
    const change = kind === "owner" ? { isOwner: false } : kind === "dirty" ? { planDirty: true } : { plan: { ...fixture.plan, ...(kind === "revision" ? { revision: 2 } : kind === "plan" ? { plan_id: "other" } : { fields: { ...fixture.plan.fields, instruction: "توجيه جديد" } }) } };
    await render(change); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: fixture.response })); expect(container.querySelector("[data-live-category-result]")).toBeNull();
});
test("multiple saved IDs require a choice and changing it cancels previous result", async () => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render({ plan: { ...fixture.plan, fields: { ...fixture.plan.fields, category_ids: ["8", "9"] } } });
    expect(container.querySelector("button").disabled).toBe(true); await select("8"); expect(api.get).not.toHaveBeenCalled(); await read();
    const signal = api.get.mock.calls[0][1].signal; await select("9"); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: fixture.response })); expect(container.querySelector("[data-live-category-result]")).toBeNull();
});
test("URL-only matches saved exact URL without link navigation or request on mount", async () => {
    await render({ plan: { ...fixture.plan, fields: { ...fixture.plan.fields, category_ids: [], category_url: fixture.response.category_url } } });
    expect(api.get).not.toHaveBeenCalled(); await read(); expect(api.get.mock.calls[0][1].params).toEqual({ expected_revision: 1 });
    expect(container.querySelector("a")).toBeNull(); expect(container.querySelector("[data-live-category-result]")).not.toBeNull();
});
test("only one request may be pending and unmount cancels late response", async () => {
    let finish; api.get.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await read(); await read(); expect(api.get).toHaveBeenCalledTimes(1); const signal = api.get.mock.calls[0][1].signal;
    await act(async () => root.render(null)); expect(signal.aborted).toBe(true); await act(async () => finish({ data: fixture.response })); expect(container.textContent).toBe("");
});
test.each(["permission_unproved", "native_read_unavailable"])("%s is scoped to this read with no OAuth redirect or plan locking", async (reason) => {
    api.get.mockRejectedValue({ response: { status: 409, data: { detail: { code: "google_live_category_unavailable", reason, message: "private upstream detail" } } } });
    await render(); await read(); expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).toContain("لم تتغير إعدادات الربط"); expect(container.textContent).not.toContain("private upstream detail");
    expect(container.querySelector("button").disabled).toBe(false); expect(container.querySelector("a")).toBeNull();
    expect(api.put).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});
test.each(["revision", "missing", "malformed"])("%s locks invalid observation until saved scope reload", async (kind) => {
    if (kind === "malformed") api.get.mockResolvedValue({ data: { ...fixture.response, inventory_verified: true } });
    else api.get.mockRejectedValue({ response: { status: kind === "missing" ? 404 : 409, data: { detail: { code: "google_commercial_plan_revision_conflict" } } } });
    await render(); await read(); expect(container.querySelector("button").disabled).toBe(true); expect(container.querySelector("[data-live-category-result]")).toBeNull();
    await render({ plan: { ...fixture.plan, revision: 2 } }); expect(container.querySelector("button").disabled).toBe(false);
});
test("partial and unknown source statuses remain visible without availability or completeness claims", async () => {
    const data = clone(fixture.response); data.pagination_traversal_complete = false; data.category_status = "unknown"; data.products.forEach((row) => { row.category_hidden = null; });
    api.get.mockResolvedValue({ data }); await render(); await read();
    expect(container.textContent).toContain("تصفح الصفحات جزئي"); expect(container.textContent).toContain("حالة القسم: غير مثبتة"); expect(container.textContent).not.toContain("اكتمل تصفح الصفحات");
});
test("zero observed products is scoped to read pages and retry removes prior result", async () => {
    api.get.mockResolvedValueOnce({ data: { ...fixture.response, products: [], products_observed: 0 } });
    await render(); await read(); expect(container.textContent).toContain("لم تظهر منتجات في الصفحات المقروءة");
    api.get.mockRejectedValueOnce({ response: { status: 503 } }); await read(); expect(container.querySelector("[data-live-category-result]")).toBeNull(); expect(container.querySelector("button").disabled).toBe(false);
});
