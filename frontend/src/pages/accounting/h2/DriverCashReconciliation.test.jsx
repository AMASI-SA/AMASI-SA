import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../../lib/api";
import DriverCashReconciliation from "./DriverCashReconciliation";
jest.mock("../../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
const endpoint = "/accounting-module/shipping-v2/driver-cash/driver-1";
const data = { schema: "mz2.driver.physical_cash.v1", scope: "captured_delivered_cash_only", driver_id: "driver-1",
    totals: { confirmed_cash: "480.00", eligible_confirmed_cash: "480.00", expected_cod: "500.00", variance: "-20.00", matched_handover: "0.00", confirmed_cash_remaining: "480.00" },
    coverage: { complete: false, missing_confirmation_collection_ids: [] }, reconciliations: [],
    items: [{ id: "collection-1", order_number: "42", cod_amount: "500.00", physical_cash_amount: "480.00", variance: "-20.00", remaining_amount: "480.00", eligible_for_reconciliation: true }],
    handover_candidates: [{ source_type: "native_cash_settlement", source_id: "native-settlement", amount: "200.00", journal_reversed: false }, { source_type: "operational_cod_remittance", source_id: "operational", amount: "100.00", journal_reversed: false }] };
let node, root, uuid;
beforeEach(() => { jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); uuid = 0; Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: () => `request-${++uuid}-synthetic-only` } }); api.get.mockResolvedValue({ data }); });
afterEach(() => { act(() => root.unmount()); node.remove(); });
async function render() { await act(async () => root.render(<DriverCashReconciliation driverId="driver-1" />)); }
async function change(label, value) { await act(async () => { const input = node.querySelector(`[aria-label="${label}"]`); const setter = Object.getOwnPropertyDescriptor(input.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set; setter.call(input, value); input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true })); }); }
async function submit() { await act(async () => node.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }
async function fill() { await change("توريد قائم للمطابقة", JSON.stringify(["native_cash_settlement", "native-settlement"])); await change("مبلغ مطابقة الطلب 42", "200.00"); await change("سبب ربط النقد", "Matched documented driver handover"); }
test("explicit existing source and allocation create metadata only; no automatic variance settlement", async () => {
    api.post.mockResolvedValue({ data: { state: "recorded", id: "link", financial_effect: "none" } }); await render();
    expect(node.querySelector('[aria-label="مبلغ مطابقة الطلب 42"]').value).toBe(""); expect(api.post).not.toHaveBeenCalled();
    expect(node.textContent).toContain("لا يسوي الفروقات"); expect(node.textContent).toContain("توريد تشغيلي — لا يثبت قيد MZ2");
    await fill(); await submit();
    expect(api.post.mock.calls).toEqual([[`${endpoint}/reconciliations`, { request_id: "request-1-synthetic-only", source_type: "native_cash_settlement", source_id: "native-settlement", allocations: [{ collection_id: "collection-1", amount: "200.00" }], reason: "Matched documented driver handover" }]]);
    expect(node.textContent).toContain("حُفظ ربط المطابقة دون إنشاء قيد مالي"); expect(api.get.mock.calls.every(([url]) => url === endpoint)).toBe(true);
});
test("uncertain request preserves exact id and allocation for retry and disables edits", async () => {
    api.post.mockRejectedValueOnce(new Error("Network Error")).mockResolvedValueOnce({ data: { state: "already_recorded", id: "link", financial_effect: "none" } }); await render(); await fill(); await submit();
    expect(node.querySelector("fieldset").disabled).toBe(true); expect(node.textContent).toContain("إعادة إرسال الربط نفسه"); await submit();
    expect(api.post.mock.calls[1]).toEqual(api.post.mock.calls[0]); expect(uuid).toBe(1); expect(node.querySelector("fieldset").disabled).toBe(false);
});
test("unverified success never reports a recorded match", async () => {
    api.post.mockResolvedValue({ data: { state: "posted", txn_group_id: "unexpected-journal" } }); await render(); await fill(); await submit();
    expect(node.textContent).toContain("driver_cash_matching_result_unverified"); expect(node.textContent).not.toContain("حُفظ ربط المطابقة"); expect(node.querySelector("fieldset").disabled).toBe(true);
});
test("foreign driver response cannot populate matching controls", async () => {
    api.get.mockResolvedValue({ data: { ...data, driver_id: "foreign" } }); await render(); expect(node.querySelector("form")).toBeNull(); expect(node.textContent).toContain("driver_cash_matching_contract_unavailable"); expect(api.post).not.toHaveBeenCalled();
});
