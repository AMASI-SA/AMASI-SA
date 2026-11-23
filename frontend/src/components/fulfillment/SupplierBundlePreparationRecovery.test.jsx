import React, { act } from "react";
import { createRoot } from "react-dom/client";
import SupplierBundlePreparationRecovery from "./SupplierBundlePreparationRecovery";
import { useOptionalAuth } from "../../context/AuthContext";
import {
    inspectSupplierBundlePreparation,
    listPendingSupplierBundlePreparations,
    notifySupplierBundlePreparationCommitted,
    retrySupplierBundlePreparation,
} from "../../services/supplierBundlePreparation";

jest.mock("../../services/supplierBundlePreparation", () => ({
    inspectSupplierBundlePreparation: jest.fn(), listPendingSupplierBundlePreparations: jest.fn(),
    notifySupplierBundlePreparationCommitted: jest.fn(), retrySupplierBundlePreparation: jest.fn(),
}));
jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));

let root;
let container;
let previousAct;
const scope = "a".repeat(64);
const row = { clientRequestId: "synthetic-request", fileTitle: "بكج قطعتين", physicalQuantity: 2, commercialQuantity: 1 };
const button = (text) => [...container.querySelectorAll("button")].find((node) => node.textContent === text);
const click = async (text) => act(async () => button(text).click());

beforeEach(() => {
    jest.clearAllMocks();
    useOptionalAuth.mockReturnValue({ authStatus: "authenticated", user: { id: "owner", role: "owner" } });
    listPendingSupplierBundlePreparations.mockResolvedValue({ sessionScope: scope, requests: [row] });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    previousAct = globalThis.IS_REACT_ACT_ENVIRONMENT; globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => {
    if (root) await act(async () => root.unmount()); container.remove(); globalThis.IS_REACT_ACT_ENVIRONMENT = previousAct;
});

test("recovery remains available without catalog cards and makes no request until explicitly opened", async () => {
    await act(async () => root.render(<SupplierBundlePreparationRecovery />));
    expect(listPendingSupplierBundlePreparations).not.toHaveBeenCalled();
    expect(inspectSupplierBundlePreparation).not.toHaveBeenCalled();
    expect(retrySupplierBundlePreparation).not.toHaveBeenCalled();
    await click("التحقق من محاولات البكج السابقة");
    expect(listPendingSupplierBundlePreparations).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain("2 قطعة من 1 بطاقة");
    expect(button("إعادة المحاولة الأصلية")).toBeUndefined();
});

test.each(["ABSENT", "PENDING"])("a %s status requires a separate explicit retry action", async (state) => {
    inspectSupplierBundlePreparation.mockResolvedValue({ state });
    retrySupplierBundlePreparation.mockResolvedValue({ state: "PENDING" });
    await act(async () => root.render(<SupplierBundlePreparationRecovery />));
    await click("التحقق من محاولات البكج السابقة");
    await click("التحقق من نتيجة الملف");
    expect(inspectSupplierBundlePreparation).toHaveBeenCalledWith({ sessionScope: scope, clientRequestId: row.clientRequestId, isCurrent: expect.any(Function) });
    expect(retrySupplierBundlePreparation).not.toHaveBeenCalled();
    await click("إعادة المحاولة الأصلية");
    expect(retrySupplierBundlePreparation).toHaveBeenCalledWith({ sessionScope: scope, clientRequestId: row.clientRequestId, isCurrent: expect.any(Function) });
    expect(notifySupplierBundlePreparationCommitted).not.toHaveBeenCalled();
});

test("verified committed recovery delivers the existing file and never retries publication", async () => {
    const batch = { batch_id: "existing-batch", physical_quantity: 2, assignment_contract: "preparation_assignment_v2", schedule_committed: true };
    inspectSupplierBundlePreparation.mockResolvedValue({ state: "COMMITTED", batch });
    const onCommitted = jest.fn(); const busy = jest.fn();
    await act(async () => root.render(<SupplierBundlePreparationRecovery onCommitted={onCommitted} onBusyChange={busy} />));
    await click("التحقق من محاولات البكج السابقة"); await click("التحقق من نتيجة الملف");
    expect(onCommitted).toHaveBeenCalledWith(batch, expect.any(Function)); expect(notifySupplierBundlePreparationCommitted).toHaveBeenCalledWith(batch);
    expect(retrySupplierBundlePreparation).not.toHaveBeenCalled();
    expect(container.textContent).toContain("لا توجد محاولات معلقة");
    expect(busy.mock.calls).toEqual([[true], [false], [true], [false]]);
});

test("scope or status errors retain the visible attempt without claiming success", async () => {
    inspectSupplierBundlePreparation.mockRejectedValue(new Error("تغيّر الحساب المفتوح."));
    const onCommitted = jest.fn();
    await act(async () => root.render(<SupplierBundlePreparationRecovery onCommitted={onCommitted} />));
    await click("التحقق من محاولات البكج السابقة"); await click("التحقق من نتيجة الملف");
    expect(container.querySelector('[role="alert"]').textContent).toBe("تغيّر الحساب المفتوح.");
    expect(container.textContent).toContain(row.fileTitle); expect(onCommitted).not.toHaveBeenCalled();
    expect(button("إعادة المحاولة الأصلية")).toBeUndefined();
});

test("active creation disables recovery so the two operations cannot share page metadata", async () => {
    await act(async () => root.render(<SupplierBundlePreparationRecovery disabled />));
    expect(button("التحقق من محاولات البكج السابقة").disabled).toBe(true);
    await click("التحقق من محاولات البكج السابقة");
    expect(listPendingSupplierBundlePreparations).not.toHaveBeenCalled();
});

test("a proven not-applied retry releases the parent attempt ID before a new command", async () => {
    inspectSupplierBundlePreparation.mockResolvedValue({ state: "PENDING" });
    const rejection = new Error("لم يكتمل إنشاء الملف."); rejection.notApplied = true;
    retrySupplierBundlePreparation.mockRejectedValue(rejection);
    const onNotApplied = jest.fn();
    await act(async () => root.render(<SupplierBundlePreparationRecovery onNotApplied={onNotApplied} />));
    await click("التحقق من محاولات البكج السابقة"); await click("التحقق من نتيجة الملف"); await click("إعادة المحاولة الأصلية");
    expect(onNotApplied).toHaveBeenCalledTimes(1); expect(container.textContent).not.toContain(row.fileTitle);
    expect(notifySupplierBundlePreparationCommitted).not.toHaveBeenCalled();
});

test.each([
    { authStatus: "authenticated", user: { id: "other", role: "owner" } },
    { authStatus: "authenticated", user: { id: "owner", role: "employee", created_by: "different-merchant" } },
    { authStatus: "unavailable", user: { id: "owner", role: "owner" } },
])("changed account context invalidates a held completion without late callbacks: %j", async (nextAuth) => {
    let resolveStatus;
    inspectSupplierBundlePreparation.mockReturnValue(new Promise((resolve) => { resolveStatus = resolve; }));
    const onCommitted = jest.fn(); const busy = jest.fn();
    const render = () => <SupplierBundlePreparationRecovery onCommitted={onCommitted} onBusyChange={busy} />;
    await act(async () => root.render(render()));
    await click("التحقق من محاولات البكج السابقة"); await click("التحقق من نتيجة الملف");
    const isCurrent = inspectSupplierBundlePreparation.mock.calls[0][0].isCurrent;
    expect(isCurrent()).toBe(true);
    useOptionalAuth.mockReturnValue(nextAuth);
    await act(async () => root.render(render()));
    expect(isCurrent()).toBe(false); busy.mockClear();
    await act(async () => resolveStatus({ state: "COMMITTED", batch: { batch_id: "old" } }));
    expect(onCommitted).not.toHaveBeenCalled(); expect(notifySupplierBundlePreparationCommitted).not.toHaveBeenCalled();
    expect(busy).not.toHaveBeenCalled(); expect(container.textContent).not.toContain(row.fileTitle);
});

test("unmounted not-applied completion cannot release a newer parent attempt ID", async () => {
    inspectSupplierBundlePreparation.mockResolvedValue({ state: "PENDING" });
    let rejectRetry;
    retrySupplierBundlePreparation.mockReturnValue(new Promise((_resolve, reject) => { rejectRetry = reject; }));
    const onNotApplied = jest.fn(); const busy = jest.fn();
    await act(async () => root.render(<SupplierBundlePreparationRecovery onNotApplied={onNotApplied} onBusyChange={busy} />));
    await click("التحقق من محاولات البكج السابقة"); await click("التحقق من نتيجة الملف"); await click("إعادة المحاولة الأصلية");
    await act(async () => root.unmount()); root = null; busy.mockClear();
    const rejection = new Error("not applied"); rejection.notApplied = true;
    await act(async () => rejectRetry(rejection));
    expect(onNotApplied).not.toHaveBeenCalled(); expect(busy).not.toHaveBeenCalled();
    expect(notifySupplierBundlePreparationCommitted).not.toHaveBeenCalled();
});

test("disabling then enabling recovery does not revive an older action generation", async () => {
    let resolveStatus;
    inspectSupplierBundlePreparation.mockReturnValue(new Promise((resolve) => { resolveStatus = resolve; }));
    const onCommitted = jest.fn();
    await act(async () => root.render(<SupplierBundlePreparationRecovery onCommitted={onCommitted} />));
    await click("التحقق من محاولات البكج السابقة"); await click("التحقق من نتيجة الملف");
    await act(async () => root.render(<SupplierBundlePreparationRecovery onCommitted={onCommitted} disabled />));
    await act(async () => root.render(<SupplierBundlePreparationRecovery onCommitted={onCommitted} />));
    await act(async () => resolveStatus({ state: "COMMITTED", batch: { batch_id: "old" } }));
    expect(onCommitted).not.toHaveBeenCalled(); expect(notifySupplierBundlePreparationCommitted).not.toHaveBeenCalled();
});

test("recovery requires an authenticated account context before lookup", async () => {
    useOptionalAuth.mockReturnValue(null);
    await act(async () => root.render(<SupplierBundlePreparationRecovery />));
    expect(button("التحقق من محاولات البكج السابقة").disabled).toBe(true);
    await click("التحقق من محاولات البكج السابقة");
    expect(listPendingSupplierBundlePreparations).not.toHaveBeenCalled();
});
