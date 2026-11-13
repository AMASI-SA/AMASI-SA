import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PendingOrdersDialog, { distinctPendingOrders } from "./PendingOrdersDialog";
import { operationalApi, requestId } from "./api";

jest.mock("./api", () => ({ operationalApi: { previewDeliveryAfterStart: jest.fn(), confirmDeliveryAfterStart: jest.fn() },
    requestId: jest.fn(() => "delivery-confirmation-request") }));

global.IS_REACT_ACT_ENVIRONMENT = true;
let host, root;
beforeEach(() => {
    jest.clearAllMocks();
    requestId.mockReturnValue("delivery-confirmation-request");
    operationalApi.previewDeliveryAfterStart.mockResolvedValue(preview());
    operationalApi.confirmDeliveryAfterStart.mockResolvedValue(confirmation());
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { if (root) act(() => root.unmount()); host.remove(); jest.restoreAllMocks(); });
const details = count => Array.from({length: count}, (_, i) => ({order_id: `id-${i}`, order_number: `100${i}`,
    reasons: [{code: "date_missing", message: `تاريخ التوصيل غير مؤكد للطلب ${i}`}] }));
const click = async text => act(async () => [...document.querySelectorAll("button")].find(node => node.textContent.includes(text)).click());

test("clicking seven pending orders shows seven order numbers and reasons without hiding the known balance", async () => {
    await act(async () => root.render(<><span>الرصيد المحتسب 930.00</span><PendingOrdersDialog count={7} details={details(7)} name="المندوب" /></>));
    await click("7");
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog.querySelectorAll("tbody tr")).toHaveLength(7);
    for (let i = 0; i < 7; i++) {
        expect(dialog.textContent).toContain(`100${i}`);
        expect(dialog.textContent).toContain(`تاريخ التوصيل غير مؤكد للطلب ${i}`);
    }
    expect(host.textContent).toContain("930.00");
    await click("إغلاق");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
});

test("duplicate reasons count one order and every page remains reachable", async () => {
    const all = details(21);
    all.push({...all[0], reasons: [{code: "fee_missing", message: "التعرفة غير مثبتة"}]});
    expect(distinctPendingOrders(all)).toHaveLength(21);
    await act(async () => root.render(<PendingOrdersDialog count={21} details={all} name="شركة الشحن" />));
    await click("21");
    let dialog = document.querySelector('[role="dialog"]');
    expect(dialog.querySelectorAll("tbody tr")).toHaveLength(10);
    expect(dialog.textContent).toContain("التعرفة غير مثبتة");
    await act(async () => dialog.querySelector('[aria-label="الصفحة التالية — الطلبات غير المؤكدة"]').click());
    await act(async () => dialog.querySelector('[aria-label="الصفحة التالية — الطلبات غير المؤكدة"]').click());
    expect(dialog.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(dialog.textContent).toContain("10020");
});

test("an unavailable order number remains an explicitly labeled identity", async () => {
    await act(async () => root.render(<PendingOrdersDialog count={1} details={[{order_id: "source-id", reasons: [{code: "identity", message: "رقم الطلب يحتاج مطابقة"}]}]} />));
    await click("1");
    expect(document.querySelector('[role="dialog"]').textContent).toContain("معرف الطلب: source-id");
});

const scope = "owner-session", start = "2026-11-01T00:00:00Z", previewHash = "a".repeat(64);
const target = { order_id: "old-order", order_number: "1001", party_type: "store_driver", party_id: "driver-one" };
const eligible = () => ({ order_id: target.order_id, order_number: target.order_number,
    reasons: [{ code: "operational_delivery_timestamp_missing", message: "تاريخ التوصيل غير مؤكد" }],
    delivery_after_start_confirmation: { can_confirm: true, state: "available", party_type: target.party_type, party_id: target.party_id } });
const saved = state => ({ id: "confirmation-one", confirmed_at: "2026-11-05T10:00:00Z", state: state || "pending_reconciliation" });
const preview = () => ({ can_confirm: true, confirmation_state: "available", preview_hash: previewHash,
    session_scope: scope, period_start: start, issues: [], recorded_confirmation: null });
const confirmation = () => ({ ok: true, pending_reconciliation: true, session_scope: scope,
    order_id: target.order_id, order_number: target.order_number, recorded_confirmation: saved() });
const confirmButton = () => document.querySelector('button[aria-label="تأكيد التوصيل بعد بداية التشغيل"]');
const control = patch => ({ allowed: true, sessionScope: scope, periodStart: start, onSaved: jest.fn(), ...patch });
const renderConfirmation = async (controls = control(), rows = [eligible()]) => {
    await act(async () => root.render(<PendingOrdersDialog count={rows.length} details={rows} name="المندوب" confirmationControl={controls} />));
};
const openConfirmation = async controls => { await renderConfirmation(controls); await click("طلبات غير مؤكدة"); };

test.each(["absent", "malformed"])("idle rows with %s confirmation metadata show their reason without starting a request", async metadata => {
    const row = eligible();
    if (metadata === "absent") delete row.delivery_after_start_confirmation;
    if (metadata === "malformed") row.delivery_after_start_confirmation.party_id = "";
    await renderConfirmation(control(), [row]); await click("طلبات غير مؤكدة");
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.textContent).toContain(row.order_number);
    expect(dialog.textContent).toContain("تاريخ التوصيل غير مؤكد");
    expect(dialog.textContent).not.toContain("جارٍ التحقق وحفظ التأكيد…");
    expect(confirmButton()).toBeNull();
    expect(operationalApi.previewDeliveryAfterStart).not.toHaveBeenCalled();
    expect(operationalApi.confirmDeliveryAfterStart).not.toHaveBeenCalled();
});

test("an explicit confirmation keeps its busy status until the response arrives", async () => {
    let resolve;
    operationalApi.confirmDeliveryAfterStart.mockReturnValue(new Promise(done => { resolve = done; }));
    await openConfirmation(); await act(async () => confirmButton().click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.querySelector('tbody [role="status"]').textContent).toBe("جارٍ التحقق وحفظ التأكيد…");
    expect(operationalApi.previewDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
    await act(async () => resolve(confirmation()));
    expect(dialog.textContent).not.toContain("جارٍ التحقق وحفظ التأكيد…");
    expect(dialog.textContent).toContain("تم حفظ التأكيد؛ بانتظار تحديث الأرصدة");
});

test("one explicit click previews then confirms exactly once and retains the saved proof", async () => {
    const controls = control();
    await openConfirmation(controls);
    expect(confirmButton()).not.toBeNull();
    const button = confirmButton();
    await act(async () => { button.click(); button.click(); });
    expect(operationalApi.previewDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(operationalApi.previewDeliveryAfterStart).toHaveBeenCalledWith({ ...target, expected_session_scope: scope, visible: true },
        { signal: expect.any(AbortSignal) });
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledWith({ ...target, expected_session_scope: scope, visible: true,
        request_id: "delivery-confirmation-request", expected_preview_hash: previewHash, confirmed_after_start: true });
    expect(requestId).toHaveBeenCalledTimes(1);
    expect(controls.onSaved).toHaveBeenCalledTimes(1);
    expect(controls.onSaved).toHaveBeenCalledWith({ ...target, ...saved(), session_scope: scope }, { refreshAllowed: true });
    expect(confirmButton()).toBeNull();
    expect(document.querySelector('[role="dialog"]').textContent).toContain("تم حفظ التأكيد؛ بانتظار تحديث الأرصدة");
    await click("إغلاق"); await click("طلبات غير مؤكدة");
    expect(confirmButton()).toBeNull();
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
});

test.each(["permission", "metadata", "identity", "session"])("%s is required before a confirmation button is offered", async missing => {
    const controls = control(), row = eligible();
    if (missing === "permission") controls.allowed = false;
    if (missing === "metadata") row.delivery_after_start_confirmation.can_confirm = false;
    if (missing === "identity") delete row.delivery_after_start_confirmation.party_id;
    if (missing === "session") controls.sessionScope = "";
    await renderConfirmation(controls, [row]); await click("طلبات غير مؤكدة");
    expect(confirmButton()).toBeNull();
    expect(operationalApi.previewDeliveryAfterStart).not.toHaveBeenCalled();
});

test.each(["scope", "period", "hash"])("a stale %s preview cannot submit confirmation", async changed => {
    const response = preview();
    if (changed === "scope") response.session_scope = "another-session";
    if (changed === "period") response.period_start = "2026-11-02T00:00:00Z";
    if (changed === "hash") response.preview_hash = "invalid";
    operationalApi.previewDeliveryAfterStart.mockResolvedValue(response);
    await openConfirmation(); await act(async () => confirmButton().click());
    expect(operationalApi.confirmDeliveryAfterStart).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]').textContent).toContain("حدّث التقرير");
});

test("preview permission rejection is visible and cannot proceed to a write", async () => {
    operationalApi.previewDeliveryAfterStart.mockRejectedValue({ response: { status: 403, data: { detail: { message: "تأكيد المالك فقط" } } } });
    await openConfirmation(); await act(async () => confirmButton().click());
    expect(operationalApi.confirmDeliveryAfterStart).not.toHaveBeenCalled();
    expect(confirmButton()).toBeNull();
    expect(document.querySelector('[role="dialog"]').textContent).toContain("تأكيد المالك فقط");
});

test.each(["close", "hidden", "offline", "scope", "period", "permission", "metadata", "unmount"])("%s during preview cancels the chain even if transport resolves after abort", async change => {
    let resolve;
    const controls = control();
    const visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    const online = jest.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    operationalApi.previewDeliveryAfterStart.mockReturnValue(new Promise(done => { resolve = done; }));
    await openConfirmation(controls); await act(async () => confirmButton().click());
    const signal = operationalApi.previewDeliveryAfterStart.mock.calls[0][1].signal;
    if (change === "close") await click("إغلاق");
    if (change === "hidden") await act(async () => { visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange")); });
    if (change === "offline") await act(async () => { online.mockReturnValue(false); window.dispatchEvent(new Event("offline")); });
    if (change === "scope") await renderConfirmation({ ...controls, sessionScope: "new-session" });
    if (change === "period") await renderConfirmation({ ...controls, periodStart: "2026-11-02T00:00:00Z" });
    if (change === "permission") await renderConfirmation({ ...controls, allowed: false });
    if (change === "metadata") await renderConfirmation(controls, [{ ...eligible(), delivery_after_start_confirmation: {
        ...eligible().delivery_after_start_confirmation, can_confirm: false, state: "pending_reconciliation" } }]);
    if (change === "unmount") { act(() => root.unmount()); root = null; }
    await act(async () => {
        visibility.mockReturnValue("visible"); online.mockReturnValue(true);
        document.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("online"));
        resolve(preview());
    });
    if (change !== "metadata") expect(signal.aborted).toBe(true);
    expect(operationalApi.confirmDeliveryAfterStart).not.toHaveBeenCalled();
    expect(operationalApi.previewDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(controls.onSaved).not.toHaveBeenCalled();
});

test.each(["pending_reconciliation", "applied"])("persisted %s metadata is displayed instead of a second confirmation", async state => {
    const row = eligible();
    row.delivery_after_start_confirmation = { ...row.delivery_after_start_confirmation, can_confirm: false, state,
        confirmation_id: "confirmation-one", confirmed_at: saved().confirmed_at };
    await renderConfirmation(control(), [row]); await click("طلبات غير مؤكدة");
    expect(confirmButton()).toBeNull();
    expect(document.querySelector('[role="dialog"]').textContent).toContain(state === "applied"
        ? "تم تطبيق التأكيد في التقرير" : "تم حفظ التأكيد؛ بانتظار تحديث الأرصدة");
    expect(operationalApi.previewDeliveryAfterStart).not.toHaveBeenCalled();
});

test("a preview that finds a saved proof never submits it again", async () => {
    operationalApi.previewDeliveryAfterStart.mockResolvedValue({ ...preview(), can_confirm: false,
        confirmation_state: "pending_reconciliation", recorded_confirmation: saved() });
    await openConfirmation(); await act(async () => confirmButton().click());
    expect(confirmButton()).toBeNull();
    expect(operationalApi.confirmDeliveryAfterStart).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]').textContent).toContain("تم حفظ التأكيد");
});

test.each(["close", "hidden", "scope", "unmount"])("%s after submission keeps the write independent and prevents a later refresh chain", async change => {
    let resolve;
    const controls = control();
    const visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    operationalApi.confirmDeliveryAfterStart.mockReturnValue(new Promise(done => { resolve = done; }));
    await openConfirmation(controls); await act(async () => confirmButton().click());
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(operationalApi.confirmDeliveryAfterStart.mock.calls[0]).toHaveLength(1);
    if (change === "close") await click("إغلاق");
    if (change === "hidden") await act(async () => { visibility.mockReturnValue("hidden"); document.dispatchEvent(new Event("visibilitychange")); });
    if (change === "scope") await renderConfirmation({ ...controls, sessionScope: "another-session" });
    if (change === "unmount") { act(() => root.unmount()); root = null; }
    await act(async () => resolve(confirmation()));
    expect(controls.onSaved).not.toHaveBeenCalled();
    if (change === "close") await click("طلبات غير مؤكدة");
    if (["close", "hidden"].includes(change)) {
        expect(confirmButton()).toBeNull();
        expect(document.querySelector('[role="dialog"]').textContent).toContain("تم حفظ التأكيد");
    }
    await act(async () => { visibility.mockReturnValue("visible"); document.dispatchEvent(new Event("visibilitychange")); });
    expect(controls.onSaved).not.toHaveBeenCalled();
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
});

test("an already applied confirmation response remains successful without another write", async () => {
    const controls = control();
    operationalApi.confirmDeliveryAfterStart.mockResolvedValue({ ...confirmation(), pending_reconciliation: false, recorded_confirmation: saved("applied") });
    await openConfirmation(controls); await act(async () => confirmButton().click());
    expect(controls.onSaved).toHaveBeenCalledTimes(1);
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[role="dialog"]').textContent).toContain("تم تطبيق التأكيد في التقرير");
    expect(confirmButton()).toBeNull();
});

test("conflicting explicit executor metadata cannot choose a confirmation target from duplicate rows", async () => {
    const row = eligible();
    const conflicting = { ...row, delivery_after_start_confirmation: { ...row.delivery_after_start_confirmation, party_id: "different-driver" } };
    await renderConfirmation(control(), [row, conflicting]); await click("طلبات غير مؤكدة");
    expect(document.querySelectorAll('[role="dialog"] tbody tr')).toHaveLength(1);
    expect(confirmButton()).toBeNull();
    expect(operationalApi.previewDeliveryAfterStart).not.toHaveBeenCalled();
});

test("an uncertain write is not retried or presented as saved", async () => {
    const controls = control();
    operationalApi.confirmDeliveryAfterStart.mockRejectedValue({ code: "ERR_NETWORK" });
    await openConfirmation(controls); await act(async () => confirmButton().click());
    expect(operationalApi.confirmDeliveryAfterStart).toHaveBeenCalledTimes(1);
    expect(controls.onSaved).not.toHaveBeenCalled();
    expect(confirmButton()).toBeNull();
    expect(document.querySelector('[role="dialog"]').textContent).toContain("تعذر التحقق من حفظ التأكيد");
});
