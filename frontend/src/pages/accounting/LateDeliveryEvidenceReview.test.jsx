import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import LateDeliveryEvidenceReview from "./LateDeliveryEvidenceReview";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
let root, node;
const BASE = "/accounting-module/shipping-v2/late-delivery-evidence";
const permissions = ["accounting.shipping.view", "accounting.shipping.contracts.review"];
const item = { attachment: { id: "late-1", driver_id: "driver-1", order_number: "100", source: { delivered_at: "2026-09-01", c3: { present: true, seal: "unchanged" } }, attached_at: "2026-10-02", financial_effect: "none" }, state: "pending", decision: null };
beforeEach(() => { jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); URL.createObjectURL = jest.fn(() => "blob:original"); URL.revokeObjectURL = jest.fn(); api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("/original") ? new Blob(["exact-image"], { type: "image/png" }) : { items: [item] } })); api.post.mockImplementation((url, payload) => Promise.resolve({ data: { ...item, state: payload.decision, decision: { decision: payload.decision, note: payload.note, reviewed_at: "2026-10-02" } } })); });
afterEach(() => { act(() => root.unmount()); node.remove(); });
const mount = (value = permissions) => act(async () => root.render(<LateDeliveryEvidenceReview accountingPermissions={value} />));
const button = text => [...node.querySelectorAll("button")].find(row => row.textContent === text);
const click = text => act(async () => button(text).click());
const note = value => act(async () => { const field = node.querySelector("textarea"); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(field, value); field.dispatchEvent(new Event("input", { bubbles: true })); });
test("no view permission sends no requests; view permission does not authorize decisions", async () => {
    await mount([]); expect(api.get).not.toHaveBeenCalled(); expect(node.textContent).toBe("");
    await mount(["accounting.shipping.view"]); expect(api.get).toHaveBeenCalledWith(BASE); expect(button("قبول المرفق")).toBeUndefined(); expect(button("رفض المرفق")).toBeUndefined(); expect(button("عرض الملف الأصلي")).toBeUndefined(); expect(api.get.mock.calls).toEqual([[BASE]]); expect(api.post).not.toHaveBeenCalled();
});
test.each(["approved", "rejected"])("authenticated original precedes %s; decision retries use the same request id", async decision => {
    api.post.mockRejectedValueOnce(new Error("lost response")); await mount();
    const action = decision === "approved" ? "قبول المرفق" : "رفض المرفق";
    expect(button(action).disabled).toBe(true); await click(action); expect(api.post).not.toHaveBeenCalled();
    await click("عرض الملف الأصلي"); expect(api.get).toHaveBeenCalledWith(BASE + "/late-1/original", { responseType: "blob" });
    expect(node.querySelector("img").src).toBe("blob:original"); expect(button(action).disabled).toBe(true);
    await act(async () => { const field = node.querySelector("textarea"); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(field, "تم فحص الصورة الأصلية"); field.dispatchEvent(new Event("input", { bubbles: true })); });
    await click(action); expect(node.querySelector('[role="alert"]')).not.toBeNull(); await click(action);
    const first = api.post.mock.calls[0], second = api.post.mock.calls[1]; expect(first).toEqual(second);
    expect(second[0]).toBe(BASE + "/late-1/review"); expect(second[1]).toEqual({ decision, note: "تم فحص الصورة الأصلية", request_id: expect.any(String) }); expect(second[1].request_id.length).toBeGreaterThanOrEqual(8);
    expect(button(action)).toBeUndefined(); expect(node.textContent).toContain("2026-09-01"); expect(node.textContent).toContain("2026-10-02");
    expect(node.querySelector('[role="status"]').textContent).toContain(decision === "approved" ? "لم تُسجّل تسوية أو حركة مالية" : "لم يتغير سجل التوصيل الأصلي");
});
test("original download failure blocks review and reports error", async () => {
    api.get.mockImplementation(url => url.endsWith("/original") ? Promise.reject({ response: { data: { detail: { code: "late_evidence_hash_mismatch" } } } }) : Promise.resolve({ data: { items: [item] } }));
    await mount(); await click("عرض الملف الأصلي"); expect(node.textContent).toContain("late_evidence_hash_mismatch"); expect(button("قبول المرفق").disabled).toBe(true); expect(api.post).not.toHaveBeenCalled();
});
test("original object URL is released and malformed review response gives no success", async () => {
    api.post.mockResolvedValue({ data: { state: "posted" } }); await mount(); await click("عرض الملف الأصلي"); await note("تم الفحص"); await click("قبول المرفق");
    expect(node.querySelector('[role="alert"]')).not.toBeNull(); expect(node.querySelector('[role="status"]')).toBeNull();
    await act(async () => root.render(null)); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:original");
});

test.each(["", "ab", "  a  "])("review note '%s' cannot approve or reject after viewing the original", async value => {
    await mount(); await click("عرض الملف الأصلي"); await note(value);
    expect(button("قبول المرفق").disabled).toBe(true); expect(button("رفض المرفق").disabled).toBe(true);
    await click("قبول المرفق"); await click("رفض المرفق"); expect(api.post).not.toHaveBeenCalled();
    await note("  سبب واضح  "); expect(button("قبول المرفق").disabled).toBe(false); expect(button("رفض المرفق").disabled).toBe(false);
    await click("رفض المرفق"); expect(api.post).toHaveBeenCalledWith(BASE + "/late-1/review", { request_id: expect.any(String), decision: "rejected", note: "سبب واضح" });
});
