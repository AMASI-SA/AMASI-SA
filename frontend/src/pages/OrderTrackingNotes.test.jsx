import { act } from "react";
import { createRoot } from "react-dom/client";
import { OrderTrackingNotesPanel } from "./OrderTrackingNotes";
import { getTrackedOrder, createTrackingInstruction, getMyTrackingInstructionHistory, rejectTrackingInstruction } from "../services/orderTrackingNotes";
jest.mock("react-router-dom", () => ({ useSearchParams: () => [new URLSearchParams()] }), { virtual: true });
jest.mock("../services/orderTrackingNotes", () => ({ getTrackedOrder: jest.fn(), createTrackingInstruction: jest.fn(), getMyTrackingInstructionHistory: jest.fn(), rejectTrackingInstruction: jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("../components/driver/InstructionTaskComments", () => () => null);
let host, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks(); getMyTrackingInstructionHistory.mockResolvedValue({ items: [] });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
test("embedded panel binds to opened order and hides cross-order search", async () => {
    getTrackedOrder.mockResolvedValue({ order: {}, instructions: [{ id: "n1", note: "تعليمات الطلب الأول", status: "completed" }] });
    await act(async () => root.render(<OrderTrackingNotesPanel key="101" orderNumber="101" embedded />));
    expect(getTrackedOrder).toHaveBeenCalledWith("101");
    expect(host.textContent).toContain("تعليمات الطلب الأول");
    expect(host.querySelector('input[placeholder="رقم الطلب، اسم العميل، أو رقم الجوال"]')).toBeNull();
    expect(host.querySelector("main")).toBeNull();
    let resolve;
    getTrackedOrder.mockImplementation(() => new Promise((r) => { resolve = r; }));
    await act(async () => root.render(<OrderTrackingNotesPanel key="202" orderNumber="202" embedded />));
    expect(host.textContent).not.toContain("تعليمات الطلب الأول");
    await act(async () => resolve({ order: {}, instructions: [] }));
    expect(getTrackedOrder).toHaveBeenLastCalledWith("202");
    expect(createTrackingInstruction).not.toHaveBeenCalled();
});
test("standalone panel retains order search", async () => {
    await act(async () => root.render(<OrderTrackingNotesPanel />));
    expect(host.querySelector('input[placeholder="رقم الطلب، اسم العميل، أو رقم الجوال"]')).not.toBeNull();
    expect(getTrackedOrder).not.toHaveBeenCalled();
});

test.each([true, false])("ready-item stop retries only after explicit customer confirmation (%s)", async (accepted) => {
    getTrackedOrder.mockResolvedValue({ order: { items: [{ id: "i", name: "منتج تجريبي" }] }, instructions: [] });
    const decision = { confirmation_required: true, preparation_revision: "revision-1" };
    createTrackingInstruction.mockRejectedValueOnce({ response: { data: { detail: {
        code: "ready_item_customer_confirmation_required", message: "هذا المنتج جاهز", items: { i: decision },
    } } } }).mockResolvedValueOnce({});
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(accepted);
    await act(async () => root.render(<OrderTrackingNotesPanel orderNumber="101" embedded />));
    const select = [...host.querySelectorAll("select")].find((node) => [...node.options].some((o) => o.value === "delete_product"));
    await act(async () => { select.value = "delete_product"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => host.querySelector('input[type="checkbox"]').click());
    const note = host.querySelector("textarea");
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(note, "طلب العميل حذف المنتج");
        note.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(createTrackingInstruction).toHaveBeenCalledTimes(accepted ? 2 : 1);
    if (accepted) expect(createTrackingInstruction.mock.calls[1]).toEqual(["101", expect.objectContaining({
        scope: "item", target_ids: ["i"], ready_item_confirmations: { i: {
            customer_requested: true, item_id: "i", preparation_revision: "revision-1", reason: "طلب العميل حذف المنتج",
        } },
    })]);
    confirm.mockRestore();
});

test("specific future stage excludes current stage and preserves per-product previous/requested values", async () => {
    getTrackedOrder.mockResolvedValue({ order: { items: [{ id: "i", name: "منتج تجريبي" }] }, instructions: [] });
    createTrackingInstruction.mockReset().mockResolvedValue({});
    await act(async () => root.render(<OrderTrackingNotesPanel orderNumber="101" embedded />));
    const action = [...host.querySelectorAll("select")].find((node) => [...node.options].some((o) => o.value === "edit_product"));
    await act(async () => { action.value = "edit_product"; action.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => host.querySelector('input[type="checkbox"]').click());
    const clickText = async text => act(async () => [...host.querySelectorAll("button")].find(button => button.textContent === text).click());
    await clickText("التجميع والعنونة"); await clickText("إضافة حقل تغيّر");
    async function input(selector, value) {
        const element = host.querySelector(selector);
        await act(async () => { Object.getOwnPropertyDescriptor(element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value").set.call(element, value); element.dispatchEvent(new Event("input", { bubbles: true })); });
    }
    await input("textarea", "تعديل المقاس"); await input('[aria-label="اسم الحقل 1"]', "المقاس"); await input('[aria-label="القيمة السابقة 1"]', "M"); await input('[aria-label="القيمة المطلوبة 1"]', "L");
    await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(createTrackingInstruction).toHaveBeenCalledWith("101", expect.objectContaining({ target_stages: ["assembly_labeling"], proposed_changes: [{ field: "المقاس", previous: "M", requested: "L", order_item_id: "i" }] }));
});

test.each([true, false])("only creator resume capability exposes blocking approval (%s)", async capability => {
    getTrackedOrder.mockResolvedValue({ order: {}, instructions: [{ id: "n", action_type: "delete_product", status: "waiting_customer_service_approval", note: "إزالة المنتج", can_approve_resume: capability }] });
    await act(async () => root.render(<OrderTrackingNotesPanel orderNumber="101" embedded />));
    expect(host.textContent.includes("اعتماد التأكيد وإغلاق المهمة")).toBe(capability);
    expect(host.textContent.includes("رفض وإعادة للموظف")).toBe(capability);
});

test("general note cannot request blocking execution or approval", async () => {
    getTrackedOrder.mockResolvedValue({ order: {}, instructions: [] });
    createTrackingInstruction.mockReset().mockResolvedValue({});
    await act(async () => root.render(<OrderTrackingNotesPanel orderNumber="101" embedded />));
    expect(host.textContent).not.toContain("المطلوب قبل السماح بالإكمال");
    await act(async () => { const note = host.querySelector("textarea"); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(note, "ملاحظة للتجهيز"); note.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(createTrackingInstruction).toHaveBeenCalledWith("101", expect.objectContaining({ action_type: "general", enforcement: "notice", required_action: "none", approval_required: false }));
});


test("creator rejection sends selected product task IDs only", async () => {
    getTrackedOrder.mockResolvedValue({ order: {}, instructions: [{ id: "n", action_type: "delete_product", task_protocol: 1, status: "waiting_customer_service_approval", note: "remove", can_approve_resume: true, tasks: [{ id: "t1", product_name: "قميص", status: "submitted" }, { id: "t2", product_name: "بنطال", status: "submitted" }] }] });
    rejectTrackingInstruction.mockResolvedValue({});
    const prompt = jest.spyOn(window, "prompt").mockReturnValue("أزل القطعة المحددة فعليًا");
    try {
        await act(async () => root.render(<OrderTrackingNotesPanel orderNumber="101" embedded />));
        const reject = () => [...host.querySelectorAll("button")].find((button) => button.textContent.includes("رفض وإعادة للموظف"));
        await act(async () => reject().click());
        expect(rejectTrackingInstruction).not.toHaveBeenCalled(); expect(prompt).not.toHaveBeenCalled();
        await act(async () => host.querySelector('[aria-label="إعادة مهمة قميص"]').click());
        await act(async () => reject().click());
        expect(rejectTrackingInstruction).toHaveBeenCalledWith("n", "أزل القطعة المحددة فعليًا", ["t1"]);
    } finally { prompt.mockRestore(); }
});


test.each([true, false])("only creator closure capability exposes ending a note (%s)", async capability => {
    getTrackedOrder.mockResolvedValue({ order: {}, instructions: [{ id: "note", action_type: "general", status: "active", note: "ملاحظة", can_close_instruction: capability }] });
    await act(async () => root.render(<OrderTrackingNotesPanel orderNumber="101" embedded />));
    expect(host.textContent.includes("إنهاء الملاحظة")).toBe(capability);
});
