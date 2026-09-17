import { act } from "react";
import { createRoot } from "react-dom/client";
import { OrderTrackingNotesPanel } from "./OrderTrackingNotes";
import { getTrackedOrder, createTrackingInstruction } from "../services/orderTrackingNotes";
jest.mock("react-router-dom", () => ({ useSearchParams: () => [new URLSearchParams()] }), { virtual: true });
jest.mock("../services/orderTrackingNotes", () => ({ getTrackedOrder: jest.fn(), createTrackingInstruction: jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
let host, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
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
