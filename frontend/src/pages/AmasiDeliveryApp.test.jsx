import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { DeliveryPaymentModal } from "./AmasiDeliveryApp";
import DriverPhysicalCash from "../components/driver/DriverPhysicalCash";
jest.mock("../lib/api", () => ({ post: jest.fn(), get: jest.fn() }));
jest.mock("../components/BarcodeCameraScanner", () => () => null);
jest.mock("../context/AuthContext", () => ({ useAuth: () => ({}) }));
jest.mock("sonner", () => ({ toast: { success: jest.fn() } }));
let node, root, saved;
beforeEach(() => {
  jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
  node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); saved = jest.fn();
  api.post.mockImplementation(async url => ({ data: url.endsWith("delivery-proof") ? { proof_reference: "proof-1" } : url.endsWith("receipt") ? { receipt_reference: "receipt-1" } : { status: "delivered" } }));
});
afterEach(() => { act(() => root.unmount()); node.remove(); });
async function render(amount = 100) { await act(async () => root.render(<DeliveryPaymentModal assignment={{ id: "assignment-1", barcode: "barcode-1", outstanding_amount: amount }} banks={[{ id: "bank-1", name: "البنك" }]} onClose={() => {}} onSaved={saved} busy={false} setBusy={() => {}} />)); }
async function click(text) { await act(async () => Array.from(node.querySelectorAll("button")).find(el => el.textContent === text).click()); }
async function change(label, value) { const el = node.querySelector(`[aria-label="${label}"]`); await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(el, value); el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true })); }); }
async function file(label) { const el = node.querySelector(`[aria-label="${label}"]`); await act(async () => { Object.defineProperty(el, "files", { configurable: true, value: [new File(["image"], "proof.png", { type: "image/png" })] }); el.dispatchEvent(new Event("change", { bubbles: true })); }); }
async function submit() { await act(async () => node.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }
const statusPayload = () => api.post.mock.calls.find(([url]) => url.endsWith("/deliveries/status"))?.[1];

test.each([["100.00", "مطابق"], ["80.25", "نقص في النقد المستلم"], ["110.00", "زيادة في النقد المستلم"], ["0.00", "نقص في النقد المستلم"]])("cash %s requires explicit driver confirmation and preserves exact input", async (value, text) => {
  await render(); await click("كاش");
  expect(node.querySelector('[aria-label="النقد المستلم فعليًا"]').value).toBe("");
  await change("النقد المستلم فعليًا", value); await file("صورة إثبات تسليم الطلب"); await submit();
  expect(api.post).not.toHaveBeenCalled(); expect(node.querySelector('[role="alert"]').textContent).toContain("أكد");
  await act(async () => node.querySelector('[type="checkbox"]').click());
  expect(node.textContent).toContain(text); await submit();
  expect(statusPayload()).toEqual({ barcode: "barcode-1", target_status: "delivered", payment_method: "cash", delivery_proof_reference: "proof-1", receipt_reference: null, bank_account_id: null, physical_cash_amount: value, physical_cash_confirmed: true });
  expect(api.post.mock.calls[0][1].get("assignment_id")).toBe("assignment-1"); expect(saved).toHaveBeenCalledTimes(1);
});
test.each(["", "-1", "NaN", "1.234", "1000001"])("invalid cash %s cannot complete", async value => {
  await render(); await click("كاش"); await change("النقد المستلم فعليًا", value); await act(async () => node.querySelector('[type="checkbox"]').click()); await submit(); expect(api.post).not.toHaveBeenCalled();
});
test("changing amount clears earlier confirmation", async () => {
  await render(); await click("كاش"); await change("النقد المستلم فعليًا", "100"); await act(async () => node.querySelector('[type="checkbox"]').click()); await change("النقد المستلم فعليًا", "99"); expect(node.querySelector('[type="checkbox"]').checked).toBe(false);
});
test.each(["شبكة", "تحويل بنكي"])("%s preserves receipt/bank flow and omits physical cash", async method => {
  await render(); await click(method); await file("صورة إيصال الدفع"); await file("صورة إثبات تسليم الطلب");
  if (method === "تحويل بنكي") await change("حساب المؤسسة", "bank-1");
  await submit(); expect(statusPayload()).not.toHaveProperty("physical_cash_amount"); expect(statusPayload()).not.toHaveProperty("physical_cash_confirmed");
  expect(statusPayload()).toMatchObject({ receipt_reference: "receipt-1", delivery_proof_reference: "proof-1", bank_account_id: method === "تحويل بنكي" ? "bank-1" : null });
});
test("paid order needs independent delivery proof but no cash confirmation", async () => {
  await render(0); await submit(); expect(api.post).not.toHaveBeenCalled(); expect(node.textContent).toContain("صورة إثبات تسليم الطلب مطلوبة");
  await file("صورة إثبات تسليم الطلب"); await submit(); expect(statusPayload()).toMatchObject({ payment_method: null, delivery_proof_reference: "proof-1" }); expect(statusPayload()).not.toHaveProperty("physical_cash_amount");
});
test("failed upload cannot submit delivered or report success", async () => {
  await render(0); await file("صورة إثبات تسليم الطلب"); api.post.mockRejectedValue(new Error("offline")); await submit(); expect(api.post).toHaveBeenCalledTimes(1); expect(statusPayload()).toBeUndefined(); expect(saved).not.toHaveBeenCalled(); expect(node.querySelector('[role="alert"]')).not.toBeNull();
});
test("custody coverage is explicit and never replaced by COD or zero on failure", async () => {
  api.get.mockResolvedValue({ data: { schema: "mz2.driver.physical_cash.v1", scope: "captured_delivered_cash_only", items: [], reconciliations: [], totals: { confirmed_cash: "80.25", eligible_confirmed_cash: "80.25", expected_cod: "100.00", variance: "-19.75", matched_handover: "0", confirmed_cash_remaining: "80.25" }, coverage: { complete: false, missing_confirmation_collection_ids: ["old-1"] } } });
  await act(async () => root.render(<DriverPhysicalCash />)); expect(node.textContent).toContain("التغطية غير مكتملة"); expect(node.textContent).toContain("80.25"); expect(node.textContent).toContain("19.75"); expect(node.textContent).toContain("لا يمثل الرصيد المحاسبي");
  api.get.mockRejectedValue(new Error("blocked")); await click("تحديث سجل النقد"); expect(node.textContent).toContain("الرصيد غير معلوم"); expect(node.textContent).not.toContain("80.25"); expect(api.post).not.toHaveBeenCalled();
});
test.each([{}, { schema: "legacy", scope: "captured_delivered_cash_only" }, { schema: "mz2.driver.physical_cash.v1", scope: "all_history" }])("unverified custody contract stays unknown", async response => {
  api.get.mockResolvedValue({ data: response }); await act(async () => root.render(<DriverPhysicalCash />));
  expect(node.textContent).toContain("الرصيد غير معلوم"); expect(node.querySelector("dl")).toBeNull(); expect(api.post).not.toHaveBeenCalled();
});
test("malformed successful delivery-proof upload does not deliver", async () => {
  await render(0); await file("صورة إثبات تسليم الطلب"); api.post.mockResolvedValue({ data: {} }); await submit();
  expect(statusPayload()).toBeUndefined(); expect(saved).not.toHaveBeenCalled(); expect(node.querySelector('[role="alert"]')).not.toBeNull();
});
test("order evidence shows actual variance, changed-source ineligibility and distinct existing handover proofs", async () => {
  api.get.mockResolvedValue({ data: {
    schema: "mz2.driver.physical_cash.v1", scope: "captured_delivered_cash_only",
    totals: { confirmed_cash: "80.25", eligible_confirmed_cash: "0", expected_cod: "100", variance: "-19.75", matched_handover: "20", confirmed_cash_remaining: "60.25" }, coverage: { complete: true, missing_confirmation_collection_ids: [] },
    items: [{ id: "collection-1", order_number: "order-42", cod_amount: "100", physical_cash_amount: "80.25", variance: "-19.75", confirmed_at: "2026-10-01", confirmation_actor: "driver-account", eligible_for_reconciliation: false, current_status: "cancelled", reconciliation_reason: "source_changed", allocated_amount: "20", remaining_amount: "60.25", delivery_proof_reference: "proof-1", seal: "seal-1" }],
    reconciliations: [{ id: "link-1", source: { source_type: "native_cash_settlement", source_id: "event-1", amount: "20", txn_group_id: "journal-1", journal_reversed: true }, allocations: [{ collection_id: "collection-1", amount: "20" }] }, { id: "link-2", source: { source_type: "operational_cod_remittance", source_id: "operational-1", amount: "10" }, allocations: [] }],
  } });
  await act(async () => root.render(<DriverPhysicalCash />));
  for (const value of ["order-42", "80.25", "19.75", "نقص", "source_changed", "driver-account", "journal-1", "القيد معكوس", "مصدر تشغيلي؛ لا يثبت قيد توريد مالي", "collection-1"]) expect(node.textContent).toContain(value);
  expect(api.post).not.toHaveBeenCalled();
});
test("accountant reuse reads only supplied driver endpoint and refreshes after explicit reconciliation", async () => {
  api.get.mockRejectedValue(new Error("blocked"));
  const endpoint = "/accounting-module/shipping-v2/driver-cash/driver-1";
  await act(async () => root.render(<DriverPhysicalCash endpoint={endpoint} refreshKey={0} />));
  await act(async () => root.render(<DriverPhysicalCash endpoint={endpoint} refreshKey={1} />));
  expect(api.get.mock.calls).toEqual([[endpoint], [endpoint]]); expect(api.post).not.toHaveBeenCalled();
});
test.each([undefined, 503])("lost cash response (%s) retries identical payload without uploading another proof", async status => {
  await render(); await click("كاش"); await change("النقد المستلم فعليًا", "80.25");
  await act(async () => node.querySelector('[type="checkbox"]').click()); await file("صورة إثبات تسليم الطلب");
  let attempts = 0;
  api.post.mockImplementation(async url => {
    if (url.endsWith("delivery-proof")) return { data: { proof_reference: "original-proof" } };
    attempts += 1;
    if (attempts === 1) throw Object.assign(new Error("response lost"), status ? { response: { status } } : {});
    return { data: { status: "delivered" } };
  });
  await submit();
  expect(node.querySelector('[aria-label="النقد المستلم فعليًا"]').disabled).toBe(true);
  expect(node.querySelector('[aria-label="صورة إثبات تسليم الطلب"]').disabled).toBe(true);
  expect(node.querySelector('[aria-label="إغلاق"]').disabled).toBe(true);
  expect(saved).not.toHaveBeenCalled();
  await click("إعادة إرسال التأكيد نفسه");
  const posts = api.post.mock.calls.filter(([url]) => url.endsWith("/deliveries/status"));
  expect(posts).toHaveLength(2); expect(posts[1][1]).toEqual(posts[0][1]);
  expect(posts[1][1]).toMatchObject({ delivery_proof_reference: "original-proof", physical_cash_amount: "80.25", physical_cash_confirmed: true });
  expect(api.post.mock.calls.filter(([url]) => url.endsWith("delivery-proof"))).toHaveLength(1);
  expect(saved).toHaveBeenCalledTimes(1);
});
