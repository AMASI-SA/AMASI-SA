import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../../lib/api";
import DriverPanel from "./DriverPanel";
import { DRIVER_BASE, REVIEW_QUEUE, loadDriverStatement, reviewPresentation } from "./driverAdapter";
jest.mock("../../../lib/api", () => ({ get: jest.fn() }));
const context = { store_drivers: [{ id: "driver-1", name: "موصل تجريبي" }], couriers: [], bank_port: { ready: false, code: "mz2_shipping_bank_port_not_integrated" }, driver_payment_destination: { ready: false, code: "mz2_driver_payment_destination_not_integrated" } };
let root, node;
beforeEach(() => { jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); });
afterEach(() => { act(() => root.unmount()); node.remove(); });
async function render() { await act(async () => root.render(<DriverPanel />)); }
test("loads exact F context then pending evidence only; no bank legacy endpoint or writes", async () => {
    api.get.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: { items: [{ id: "review-1", status: "pending", payment_method: "card_terminal", amount: 500, driver_id: "driver-1", receipt_reference: "proof-1" }] } });
    await render();
    expect(api.get.mock.calls).toEqual([[`${DRIVER_BASE}/context`], [REVIEW_QUEUE, { params: { limit: 250 } }]]);
    expect(node.textContent).toContain("ليس تحصيلاً نهائيًا"); expect(node.textContent).toContain("500.00");
    expect(node.textContent).toContain("mz2_driver_payment_destination_not_integrated");
    expect(node.textContent).toContain("driver_payment_review_history_read_contract_missing");
    expect(node.querySelector('a')).toBeNull(); expect(node.textContent).not.toContain("تم التحصيل");
});
test("404 stops after context and shows blocked instead of legacy fallback", async () => {
    api.get.mockRejectedValue({ response: { status: 404 } }); await render();
    expect(node.textContent).toContain("BLOCKED_BY_BACKEND"); expect(api.get).toHaveBeenCalledTimes(1);
});
test("connected bank port removes stale wiring blocker while destination proof remains blocked", async () => {
    api.get.mockResolvedValueOnce({ data: { ...context, bank_port: { ready: true, code: null } } })
        .mockResolvedValueOnce({ data: { items: [] } });
    await render();
    expect(node.textContent).not.toContain("shipping_bank_binding_readiness_required");
    expect(node.textContent).not.toContain("بانتظار الربط البنكي الأصلي");
    expect(node.textContent).toContain("mz2_driver_payment_destination_not_integrated");
    expect(node.textContent).toContain("BLOCKED_BY_BACKEND");
    expect(api.get.mock.calls).toEqual([[`${DRIVER_BASE}/context`], [REVIEW_QUEUE, { params: { limit: 250 } }]]);
    expect(node.querySelectorAll("button")).toHaveLength(1);
});
test.each([false, undefined, "true"])("bank port without explicit ready=true retains blocker (%s)", async ready => {
    api.get.mockResolvedValueOnce({ data: { ...context, bank_port: { ready } } })
        .mockResolvedValueOnce({ data: { items: [] } });
    await render();
    expect(node.textContent).toContain("shipping_bank_binding_readiness_required");
    expect(node.textContent).toContain("mz2_driver_payment_destination_not_integrated");
});
test("server failures show retry and no replacement data", async () => {
    api.get.mockRejectedValue({ response: { status: 500 } }); await render();
    expect(node.querySelector('[role="alert"]')).not.toBeNull(); expect(node.textContent).not.toContain("0.00");
});
test("empty review queue is explicitly unrelated to driver balance", async () => {
    api.get.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: { items: [] } }); await render();
    expect(node.textContent).toContain("لا تعني قائمة المراجعة الفارغة");
});
test("selecting driver displays native balances unchanged, no netting from entries", async () => {
    api.get.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: { items: [] } }).mockResolvedValueOnce({ data: { ledger_source: "accounting_v2", party_type: "store_driver", party_id: "driver-1", cod_receivable: "900.00", payable: "30.00", entries: [{ id: "row", side: "debit", amount: "17.00" }] } });
    await render(); await act(async () => { const input = node.querySelector('select'); input.value = JSON.stringify(["store_driver", "driver-1"]); input.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(api.get).toHaveBeenLastCalledWith(`${DRIVER_BASE}/statements/store_driver/driver-1`);
    expect(node.textContent).toContain("900.00"); expect(node.textContent).toContain("30.00"); expect(node.textContent).not.toContain("870.00");
    expect(node.querySelectorAll('[aria-label="المبلغ غير متاح"]').length).toBeGreaterThan(0);
});
test("legacy or mismatched statement is rejected", async () => {
    api.get.mockResolvedValue({ data: { ledger_source: "legacy", party_type: "store_driver", party_id: "driver-1", cod_receivable: 0 } });
    await expect(loadDriverStatement("store_driver", "driver-1")).rejects.toThrow("shipping_native_statement_required");
});
test("loading skeleton does not claim zero", async () => { api.get.mockReturnValue(new Promise(() => {})); await render(); expect(node.querySelector('.ac-skeleton')).not.toBeNull(); expect(node.textContent).not.toContain("0.00"); });
test.each(["approved", "rejected"])("pending queue cannot silently represent %s history", async status => {
    api.get.mockResolvedValueOnce({ data: context }).mockResolvedValueOnce({ data: { items: [{ status, amount: 80 }] } }); await render();
    expect(node.textContent).toContain("driver_pending_review_contract_unavailable"); expect(node.textContent).not.toContain("80.00");
});
test("approved financial result requires both backend posted state and journal", () => {
    expect(reviewPresentation({ status: "approved" }).effect).toContain("غير متاحة");
    expect(reviewPresentation({ status: "approved", financial_handoff_status: "posted", financial_txn_group_id: "native-journal" }).effect).toContain("native-journal");
    expect(reviewPresentation({ status: "rejected", financial_handoff_status: "rejected_no_financial_effect" }).effect).toContain("دون أثر مالي");
    expect(reviewPresentation({ status: "pending", financial_handoff_status: "posted" }).label).toContain("ليس تحصيلاً نهائيًا");
});

test("network transport failure offers retry instead of capability status", async () => {
    api.get.mockRejectedValue(new Error("Network Error")); await render();
    expect(node.querySelector('[role="alert"]')).not.toBeNull(); expect(node.textContent).toContain("إعادة المحاولة"); expect(node.textContent).not.toContain("BLOCKED_BY_BACKEND");
});
test("unimplemented native route is blocked without probing another endpoint", async () => {
    api.get.mockRejectedValue({ response: { status: 501 } }); await render();
    expect(node.textContent).toContain("BLOCKED_BY_BACKEND"); expect(api.get).toHaveBeenCalledTimes(1);
});


test("connected bank evidence adapter is described separately from blocked POS without adding approval controls", async () => {
    api.get.mockResolvedValueOnce({ data: { ...context, bank_port: { ready: true, code: null },
        driver_payment_destination: { ...context.driver_payment_destination, methods: {
            bank_transfer: { adapter_connected: true, evidence_required: "verified_bank_statement_arrival" },
            card_terminal: { adapter_connected: false, code: "mz2_driver_payment_destination_not_integrated" },
        } } } }).mockResolvedValueOnce({ data: { items: [] } });
    await render();
    expect(node.querySelector('[data-testid="driver-bank-proof-requirement"]').textContent).toContain("حركة وصول بنكية موثقة");
    expect(node.textContent).not.toContain("driver_bank_destination_readiness_required");
    expect(node.textContent).toContain("mz2_driver_payment_destination_not_integrated");
    expect(node.textContent).toContain("مصدرًا أصليًا يثبت نجاح عملية POS");
    expect(node.textContent).toContain("جميع ضوابط الافتتاحية والتفعيل والإيقاف والصلاحيات");
    expect(node.querySelectorAll("button")).toHaveLength(1);
    expect(api.get.mock.calls).toEqual([[`${DRIVER_BASE}/context`], [REVIEW_QUEUE, { params: { limit: 250 } }]]);
});
