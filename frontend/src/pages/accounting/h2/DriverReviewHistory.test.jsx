import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../../lib/api";
import DriverReviewHistory from "./DriverReviewHistory";
import DriverPanel from "./DriverPanel";
import { DRIVER_BASE, REVIEW_QUEUE, loadDriverHistory } from "./driverAdapter";
jest.mock("../../../lib/api", () => ({ get: jest.fn() }));

const pos = { id: "native-review-2", review_id: "review-1", review_revision: 2, driver_id: "driver-1", order_number: "order-1",
    status: "approved", payment_method: "card_terminal", amount: "500.00", reviewed_by: "accountant-1", reviewed_at: "2026-09-30T12:00:00+00:00",
    receipt_reference: "receipt-2", note: "Matched actual evidence", financial_handoff_status: "posted", financial_txn_group_id: "native-journal-2", journal_reversed: false,
    destination: { destination_kind: "pos_receivable", entity_type: "asset", entity_id: "exact-pos-identity", sub_account: "other_receivable", display_name: "ذمة شبكة موثقة", source_namespace: "manual_pos_receipt", source_record_id: "receipt-hash", source_revision: "proof-revision" } };
const rejection = { ...pos, id: "native-review-1", review_revision: 1, status: "rejected", receipt_reference: "receipt-1", financial_handoff_status: "rejected_no_financial_effect", financial_txn_group_id: null, destination: null };
const response = (items = [pos], changes = {}) => ({ data: { schema: "mz2.driver.review_history.v1", ledger_source: "accounting_v2", scope: "native_v2_decisions_only", read_only: true,
    items, has_more: false, next_cursor: null, coverage: { native_only: true, unlinked_current_decisions: 0, unlinked_current_review_ids: [] }, ...changes } });
const drivers = [{ id: "driver-1", name: "الموصل التجريبي" }];
let root, node;
beforeEach(() => { jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); });
afterEach(() => { act(() => root.unmount()); node.remove(); });
async function render(element = <DriverReviewHistory drivers={drivers} />) { await act(async () => root.render(element)); }
async function button(text) { await act(async () => Array.from(node.querySelectorAll("button")).find(el => el.textContent === text).click()); }

test("native revisions show canonical POS identity, rejection, actor and separate bank settlement without writes", async () => {
    api.get.mockResolvedValue(response([pos, rejection])); await render();
    expect(api.get.mock.calls).toEqual([[`${DRIVER_BASE}/driver-payment-history`, { params: { limit: 50 } }]]);
    for (const text of ["ذمة شبكة موثقة", "asset / exact-pos-identity / other_receivable", "نسخة 1", "نسخة 2", "accountant-1", "receipt-1", "receipt-2", "رفض دون أثر مالي", "وصول المال للبنك يحتاج تسوية منفصلة", "لا يمثل رصيد النقد الفعلي"]) expect(node.textContent).toContain(text);
    expect(node.querySelectorAll("button")).toHaveLength(1);
});
test("older page cursor is exact; a failed page hides stale financial rows and retries exact request", async () => {
    api.get.mockResolvedValueOnce(response([pos], { has_more: true, next_cursor: "opaque-cursor" })).mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: "journal_corrupt" } } } }).mockResolvedValueOnce(response([rejection]));
    await render(); await button("القرارات الأقدم");
    expect(node.textContent).toContain("journal_corrupt"); expect(node.textContent).not.toContain("native-journal-2"); expect(node.textContent).not.toContain("0.00");
    await button("إعادة المحاولة");
    expect(api.get.mock.calls.slice(1)).toEqual(Array(2).fill([`${DRIVER_BASE}/driver-payment-history`, { params: { limit: 50, cursor: "opaque-cursor" } }]));
    expect(node.textContent).toContain("رفض دون أثر مالي"); expect(node.textContent).not.toContain("native-journal-2");
});
test("filter resets pagination and asks backend, without deriving history from pending queue", async () => {
    api.get.mockResolvedValueOnce(response([pos], { has_more: true, next_cursor: "cursor" })).mockResolvedValueOnce(response([rejection])).mockResolvedValueOnce(response([]));
    await render(); await button("القرارات الأقدم");
    await act(async () => { const select = node.querySelectorAll("select")[1]; select.value = "bank_transfer"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(api.get).toHaveBeenLastCalledWith(`${DRIVER_BASE}/driver-payment-history`, { params: { limit: 50, payment_method: "bank_transfer" } });
    expect(node.textContent).toContain("لا تثبت انعدام الذمة"); expect(node.textContent).not.toContain("نسخة 1");
});
test("coverage gaps and reversed journals remain visible without claiming current balances", async () => {
    api.get.mockResolvedValue(response([{ ...pos, journal_reversed: true }], { coverage: { native_only: true, unlinked_current_decisions: 1, unlinked_current_review_ids: ["old-unlinked"] } }));
    await render(); expect(node.textContent).toContain("عُكس القيد لاحقًا"); expect(node.textContent).toContain("التغطية غير مكتملة"); expect(node.textContent).toContain("old-unlinked");
    expect(node.textContent).toContain("native-journal-2");
});
test.each([
    { ledger_source: "legacy" }, { scope: "all_history" }, { read_only: false }, { has_more: true, next_cursor: null },
    { items: [{ ...pos, status: "pending" }] }, { items: [{ ...pos, financial_txn_group_id: null }] },
    { items: [{ ...pos, destination: { ...pos.destination, entity_type: "bank" } }] },
    { items: [{ ...rejection, financial_txn_group_id: "journal" }] },
    { items: [pos, pos] }, { coverage: { native_only: true, unlinked_current_decisions: 2, unlinked_current_review_ids: [] } },
])("rejects incompatible history without endpoint fallback (%j)", async changes => {
    api.get.mockResolvedValue(response([pos], changes)); await expect(loadDriverHistory()).rejects.toThrow("driver_native_history_contract_unavailable"); expect(api.get).toHaveBeenCalledTimes(1);
});
test("H2 connects available native history capability while pending queue stays separate", async () => {
    const context = { store_drivers: drivers, couriers: [], stages: {}, bank_port: { ready: true }, driver_payment_destination: {}, driver_review_history: { ready: true, scope: "native_v2_decisions_only" } };
    api.get.mockImplementation(url => Promise.resolve(url === `${DRIVER_BASE}/context` ? { data: context } : url === REVIEW_QUEUE ? { data: { items: [] } } : response([pos])));
    await render(<DriverPanel />);
    expect(api.get.mock.calls.map(call => call[0]).sort()).toEqual([`${DRIVER_BASE}/context`, `${DRIVER_BASE}/driver-payment-history`, REVIEW_QUEUE].sort());
    expect(node.textContent).not.toContain("driver_payment_review_history_read_contract_missing"); expect(node.textContent).toContain("native-journal-2"); expect(node.textContent).toContain("لا تعني قائمة المراجعة الفارغة");
});
