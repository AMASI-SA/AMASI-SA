import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PendingSallaEditPanel from "./PendingSallaEditPanel";
import { applyPendingSallaEdit, getPendingSallaEdits } from "../../services/sallaPendingEdit";

jest.mock("../../services/sallaPendingEdit", () => ({ applyPendingSallaEdit: jest.fn(), getPendingSallaEdits: jest.fn() }));
let host, root, applied;
const change = { event_id: "event", change_id: "change", change_type: "edit_options", application_state: "pending_application", classification: "components", old_data: { options: { color: "عنابي" } }, stage: "preparation", affected_employees: [{ id: "employee", name: "المسؤول" }], notification_status: "unread", units: [{ order_item_id: "item", unit_index: 1, generation: 0, revision: 7, change_id: "change" }], new_data: { product_name: "هدية", image_url: "https://example.test/product.png", quantity: 2, variant_id: "variant", options: { color: "أزرق", engraving: { text: "أسماء" } }, custom_fields: { message: "رسالة العميل" } }, apply_allowed: true, expected_revision: 7, expected_generation: "generation" };
const data = { enabled: true, changes: [change], employees: [{ id: "employee", name: "موظف" }] };
const button = text => [...host.querySelectorAll("button")].find(node => node.textContent === text);
async function click(text) { await act(async () => button(text).click()); }
async function render(orderNumber = "A") { await act(async () => root.render(<PendingSallaEditPanel orderNumber={orderNumber} onApplied={applied} />)); }
async function choose() {
    await click("اعتماد إعادة التجهيز");
    await act(async () => {
        const select = host.querySelector("select");
        select.value = "employee";
        select.dispatchEvent(new Event("change", { bubbles: true }));
        const textarea = host.querySelector("textarea");
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(textarea, "معالجة التعديل");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { randomUUID: () => "test-uuid" } });
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    jest.resetAllMocks(); applied = jest.fn(); getPendingSallaEdits.mockResolvedValue(data);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

test("shows source options read-only and hides action unless server explicitly allows EDIT_OPTIONS", async () => {
    getPendingSallaEdits.mockResolvedValue({ ...data, changes: [{ ...change, apply_allowed: false }, { ...change, event_id: "add", change_type: "add_product" }] });
    await render();
    expect(host.textContent).toContain("أزرق");
    expect(host.textContent).toContain("عنابي");
    expect(host.textContent).toContain("المسؤول");
    expect(host.textContent).toContain("preparation");
    expect(host.textContent).toContain("unread");
    expect(host.textContent).toContain("أسماء");
    expect(host.textContent).toContain("رسالة العميل");
    expect(host.textContent).toContain("variant");
    expect(host.querySelector("img").getAttribute("src")).toBe("https://example.test/product.png");
    expect(host.textContent).toContain("تم تعديل خيارات المنتج في سلة");
    expect(button("اعتماد إعادة التجهيز")).toBeUndefined();
    expect(host.querySelector("input")).toBeNull();
});
test("confirmation can cancel and success refreshes list and order", async () => {
    await render(); await click("اعتماد إعادة التجهيز"); await click("إلغاء");
    expect(applyPendingSallaEdit).not.toHaveBeenCalled();
    await choose();
    applyPendingSallaEdit.mockResolvedValue({ change_id: "change" });
    getPendingSallaEdits.mockResolvedValue({ ...data, changes: [] });
    await click("تأكيد الإرسال");
    expect(applyPendingSallaEdit).toHaveBeenCalledWith("A", expect.objectContaining({ event_id: "event", employee_id: "employee", expected_revision: 7, expected_generation: "generation", reason: "معالجة التعديل" }));
    expect(applied).toHaveBeenCalledTimes(1);
    expect(applyPendingSallaEdit.mock.calls[0][1].units).toEqual(change.units);
    expect(applyPendingSallaEdit.mock.calls[0][1]).not.toHaveProperty("options");
    expect(applyPendingSallaEdit.mock.calls[0][1]).not.toHaveProperty("new_data");
    expect(host.textContent).toContain("مرجع التغيير: change");
    expect(host.textContent).toContain("لا توجد تعديلات خيارات معلّقة");
});
test.each([
    { classification: "reconciliation_required" }, { classification: "exception_required" }, { classification: undefined },
    { change_type: "add_product" }, { change_type: undefined }, { change_type: "cancel_product" },
    { application_state: undefined }, { application_state: "applied" },
    { application_state: "exception_required" }, { apply_allowed: false }, { apply_allowed: undefined },
])("requires explicit pending EDIT_OPTIONS and server permission: %j", async invalid => {
    getPendingSallaEdits.mockResolvedValue({ ...data, changes: [{ ...change, ...invalid }] });
    await render();
    expect(button("اعتماد إعادة التجهيز")).toBeUndefined();
    expect(applyPendingSallaEdit).not.toHaveBeenCalled();
});
test.each(["Manual Hold", "PR2 reconciliation hold"])("blocked %s cannot open confirmation", async reason => {
    getPendingSallaEdits.mockResolvedValue({ ...data, changes: [{ ...change, apply_allowed: false, reason }] });
    await render();
    expect(host.textContent).toContain(reason);
    expect(button("اعتماد إعادة التجهيز")).toBeUndefined();
    expect(host.querySelector('[role="dialog"]')).toBeNull();
});
test("unknown failure retries identical request and locks payload", async () => {
    await render(); await choose();
    applyPendingSallaEdit.mockRejectedValueOnce(new Error("network"));
    await click("تأكيد الإرسال");
    const payload = applyPendingSallaEdit.mock.calls[0][1];
    expect(host.querySelector("textarea").disabled).toBe(true);
    applyPendingSallaEdit.mockResolvedValue({});
    await click("إعادة المحاولة بنفس الطلب");
    expect(applyPendingSallaEdit.mock.calls[1][1]).toEqual(payload);
});
test("successful assignment reports a remaining execution barrier", async () => {
    await render(); await choose();
    applyPendingSallaEdit.mockResolvedValue({ change_id: "change", eligible_for_execution: false });
    await click("تأكيد الإرسال");
    expect(host.textContent).toContain("تم اعتماد التغيير، لكن التجهيز متوقف بسبب حاجز آخر.");
    expect(host.textContent).toContain("مرجع التغيير: change");
    expect(applied).toHaveBeenCalledTimes(1);
});
test("409 refreshes server permissions and closes stale confirmation", async () => {
    await render(); await choose();
    applyPendingSallaEdit.mockRejectedValue({ status: 409 });
    getPendingSallaEdits.mockResolvedValue({ ...data, changes: [{ ...change, apply_allowed: false }] });
    await click("تأكيد الإرسال");
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(button("اعتماد إعادة التجهيز")).toBeUndefined();
    expect(host.textContent).toContain("تغيّرت حالة الطلب");
});
test("late response for previous order cannot show its products", async () => {
    let resolve;
    getPendingSallaEdits.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await render("A");
    expect(host.textContent).toContain("جاري تحميل");
    getPendingSallaEdits.mockResolvedValue({ ...data, changes: [] });
    await render("B");
    await act(async () => resolve(data));
    expect(host.textContent).not.toContain("هدية");
});
test("in-flight apply cannot be duplicated or notify the next order", async () => {
    let resolve;
    await render(); await choose();
    applyPendingSallaEdit.mockImplementation(() => new Promise(done => { resolve = done; }));
    await click("تأكيد الإرسال");
    expect(button("جاري الإرسال…").disabled).toBe(true);
    await click("جاري الإرسال…");
    expect(applyPendingSallaEdit).toHaveBeenCalledTimes(1);
    await render("B"); await act(async () => resolve({}));
    expect(applied).not.toHaveBeenCalled();
});
test("flag-off and forbidden lists expose no operation", async () => {
    getPendingSallaEdits.mockResolvedValue({ enabled: false }); await render(); expect(host.textContent).toBe("");
    getPendingSallaEdits.mockRejectedValue({ status: 403 }); await render("B"); expect(host.textContent).toBe("");
});

 test("representation-only explicitly offers approval without re-preparation", async () => {
    getPendingSallaEdits.mockResolvedValue({ ...data, changes: [{ ...change, classification: "representation_only" }] });
    await render();
    expect(button("اعتماد التغيير")).toBeDefined();
    expect(button("اعتماد إعادة التجهيز")).toBeUndefined();
    expect(host.textContent).toContain("لا يحتاج إعادة تجهيز");
    await click("اعتماد التغيير");
    expect(host.querySelector('[role="dialog"]').textContent).toContain("اعتماد التغيير");
 });
