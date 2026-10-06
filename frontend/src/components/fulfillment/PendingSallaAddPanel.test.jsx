import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PendingSallaAddPanel from "./PendingSallaAddPanel";
import { applyPendingSallaAdd, getPendingSallaAdds } from "../../services/sallaPendingAdd";

jest.mock("../../services/sallaPendingAdd", () => ({ applyPendingSallaAdd: jest.fn(), getPendingSallaAdds: jest.fn() }));
let host, root, applied;
const change = { event_id: "event", change_id: "change", change_type: "add_product", application_state: "pending_application", new_data: { product_name: "هدية", image_url: "https://example.test/product.png", quantity: 2, variant_id: "variant", options: { color: "أزرق", engraving: { text: "أسماء" } }, custom_fields: { message: "رسالة العميل" } }, apply_allowed: true, expected_revision: 7, expected_generation: "generation" };
const data = { enabled: true, changes: [change], employees: [{ id: "employee", name: "موظف" }] };
const button = text => [...host.querySelectorAll("button")].find(node => node.textContent === text);
async function click(text) { await act(async () => button(text).click()); }
async function render(orderNumber = "A") { await act(async () => root.render(<PendingSallaAddPanel orderNumber={orderNumber} onApplied={applied} />)); }
async function choose() {
    await click("إرسال إلى تمت المراجعة");
    await act(async () => {
        const select = host.querySelector("select");
        select.value = "employee";
        select.dispatchEvent(new Event("change", { bubbles: true }));
        const textarea = host.querySelector("textarea");
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(textarea, "إسناد الإضافة");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { randomUUID: () => "test-uuid" } });
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    jest.resetAllMocks(); applied = jest.fn(); getPendingSallaAdds.mockResolvedValue(data);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

test("shows source options read-only and hides action unless server explicitly allows ADD", async () => {
    getPendingSallaAdds.mockResolvedValue({ ...data, changes: [{ ...change, apply_allowed: false }, { ...change, event_id: "edit", change_type: "edit_options" }] });
    await render();
    expect(host.textContent).toContain("أزرق");
    expect(host.textContent).toContain("أسماء");
    expect(host.textContent).toContain("رسالة العميل");
    expect(host.textContent).toContain("variant");
    expect(host.querySelector("img").getAttribute("src")).toBe("https://example.test/product.png");
    expect(host.textContent).toContain("منتج مضاف إلى الطلب");
    expect(button("إرسال إلى تمت المراجعة")).toBeUndefined();
    expect(host.querySelector("input")).toBeNull();
});
test("confirmation can cancel and success refreshes list and order", async () => {
    await render(); await click("إرسال إلى تمت المراجعة"); await click("إلغاء");
    expect(applyPendingSallaAdd).not.toHaveBeenCalled();
    await choose();
    applyPendingSallaAdd.mockResolvedValue({ change_id: "change" });
    getPendingSallaAdds.mockResolvedValue({ ...data, changes: [] });
    await click("تأكيد الإرسال");
    expect(applyPendingSallaAdd).toHaveBeenCalledWith("A", expect.objectContaining({ event_id: "event", employee_id: "employee", expected_revision: 7, expected_generation: "generation", reason: "إسناد الإضافة" }));
    expect(applied).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("مرجع التغيير: change");
    expect(host.textContent).toContain("لا توجد إضافات معلّقة");
});
test.each([
    { change_type: undefined }, { change_type: "cancel_product" },
    { application_state: undefined }, { application_state: "applied" },
    { application_state: "exception_required" }, { apply_allowed: false }, { apply_allowed: undefined },
])("requires explicit pending ADD and server permission: %j", async invalid => {
    getPendingSallaAdds.mockResolvedValue({ ...data, changes: [{ ...change, ...invalid }] });
    await render();
    expect(button("إرسال إلى تمت المراجعة")).toBeUndefined();
    expect(applyPendingSallaAdd).not.toHaveBeenCalled();
});
test.each(["Manual Hold", "PR2 reconciliation hold"])("blocked %s cannot open confirmation", async reason => {
    getPendingSallaAdds.mockResolvedValue({ ...data, changes: [{ ...change, apply_allowed: false, reason }] });
    await render();
    expect(host.textContent).toContain(reason);
    expect(button("إرسال إلى تمت المراجعة")).toBeUndefined();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
});
test("unknown failure retries identical request and locks payload", async () => {
    await render(); await choose();
    applyPendingSallaAdd.mockRejectedValueOnce(new Error("network"));
    await click("تأكيد الإرسال");
    const payload = applyPendingSallaAdd.mock.calls[0][1];
    expect(host.querySelector("textarea").disabled).toBe(true);
    applyPendingSallaAdd.mockResolvedValue({});
    await click("إعادة المحاولة بنفس الطلب");
    expect(applyPendingSallaAdd.mock.calls[1][1]).toEqual(payload);
});
test("successful assignment reports a remaining execution barrier", async () => {
    await render(); await choose();
    applyPendingSallaAdd.mockResolvedValue({ change_id: "change", eligible_for_execution: false });
    await click("تأكيد الإرسال");
    expect(host.textContent).toContain("تمت المراجعة والإسناد، لكن التجهيز متوقف بسبب حاجز آخر.");
    expect(host.textContent).toContain("مرجع التغيير: change");
    expect(applied).toHaveBeenCalledTimes(1);
});
test("409 refreshes server permissions and closes stale confirmation", async () => {
    await render(); await choose();
    applyPendingSallaAdd.mockRejectedValue({ status: 409 });
    getPendingSallaAdds.mockResolvedValue({ ...data, changes: [{ ...change, apply_allowed: false }] });
    await click("تأكيد الإرسال");
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(button("إرسال إلى تمت المراجعة")).toBeUndefined();
    expect(host.textContent).toContain("تغيّرت حالة الطلب");
});
test("late response for previous order cannot show its products", async () => {
    let resolve;
    getPendingSallaAdds.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await render("A");
    expect(host.textContent).toContain("جاري تحميل");
    getPendingSallaAdds.mockResolvedValue({ ...data, changes: [] });
    await render("B");
    await act(async () => resolve(data));
    expect(host.textContent).not.toContain("هدية");
});
test("in-flight apply cannot be duplicated or notify the next order", async () => {
    let resolve;
    await render(); await choose();
    applyPendingSallaAdd.mockImplementation(() => new Promise(done => { resolve = done; }));
    await click("تأكيد الإرسال");
    expect(button("جاري الإرسال…").disabled).toBe(true);
    await click("جاري الإرسال…");
    expect(applyPendingSallaAdd).toHaveBeenCalledTimes(1);
    await render("B"); await act(async () => resolve({}));
    expect(applied).not.toHaveBeenCalled();
});
test("flag-off and forbidden lists expose no operation", async () => {
    getPendingSallaAdds.mockResolvedValue({ enabled: false }); await render(); expect(host.textContent).toBe("");
    getPendingSallaAdds.mockRejectedValue({ status: 403 }); await render("B"); expect(host.textContent).toBe("");
});
