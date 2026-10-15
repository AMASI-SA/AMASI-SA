import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import AccountingReceivables from "./AccountingReceivables.jsx";

jest.mock("../../lib/api", () => ({ get: jest.fn(), put: jest.fn(), post: jest.fn() }), { virtual: true });
let node, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("sales-tax")
        ? { revision: 0, versions: [], audit: [] }
        : { payments: [{ provider_id: "SYN-1", order_reference_id: "SYN-ORDER", refunds: [] }] } }));
    api.post.mockResolvedValue({ data: { state: "eligible", preview_hash: "a".repeat(64),
        tax: { gross: "115.00", net: "100.00", tax: "15.00", rate: "15" } } });
});
afterEach(() => { act(() => root.unmount()); node.remove(); jest.clearAllMocks(); });
const render = async permissions => { await act(async () => root.render(<AccountingReceivables accountingPermissions={permissions} />)); };
const click = async text => {
    const button = [...node.querySelectorAll("button")].find(item => item.textContent === text);
    await act(async () => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
};

test("viewer sees missing policy and preview but no tax save or posting", async () => {
    await render(["accounting.settlements.view"]);
    expect(node.textContent).toContain("لم تُضبط نسبة");
    await click("معاينة المؤهل والمرفوض");
    expect(node.textContent).toContain("100.00");
    expect(node.textContent).toContain("15.00 / 15%");
    expect(node.textContent).not.toContain("إثبات الحركة المعاينة");
    expect(node.querySelector("form")).toBeNull();
    expect(api.put).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("authorized accountant executes only the reviewed hash and sees journal result", async () => {
    await render(["accounting.settlements.view", "accounting.receivables.post"]);
    await click("معاينة المؤهل والمرفوض");
    api.post.mockResolvedValueOnce({ data: { state: "posted", txn_group_id: "SYN-GROUP",
        tax: { gross: "115.00", net: "100.00", tax: "15.00", rate: "15" } } });
    await click("إثبات الحركة المعاينة");
    expect(api.post).toHaveBeenLastCalledWith(expect.stringContaining("/execute"), {
        provider: "tamara", payment_id: "SYN-1", refund_id: null, preview_hash: "a".repeat(64),
    });
    expect(node.textContent).toContain("SYN-GROUP");
    expect(node.textContent).not.toContain("إثبات الحركة المعاينة");
});

test("rejected evidence cannot be posted", async () => {
    api.post.mockResolvedValue({ data: { state: "rejected", reasons: ["order_principal_conflict"] } });
    await render(["accounting.receivables.post"]);
    await click("معاينة المؤهل والمرفوض");
    expect(node.textContent).toContain("إجمالي الطلب لا يطابق مبلغ الدفع");
    expect(node.textContent).not.toContain("إثبات الحركة المعاينة");
});

test("manual rate input has no automatic default and explicit zero can be entered", async () => {
    await render(["accounting.rules.manage"]);
    const input = node.querySelector('[aria-label="النسبة اليدوية"]');
    expect(input.value).toBe("");
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    await act(async () => {
        for (const [label, value] of [["النسبة اليدوية", "0"], ["تاريخ السريان", "2020-01-01T12:00"], ["سبب التعديل", "اختبار الصفر الصريح"]]) {
            const field = node.querySelector('[aria-label="' + label + '"]'); set.call(field, value);
            field.dispatchEvent(new Event("input", { bubbles: true }));
        }
    });
    api.put.mockResolvedValue({ data: { revision: 1, versions: [{ rate: "0" }], audit: [] } });
    await act(async () => node.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(api.put).toHaveBeenCalledWith(expect.stringContaining("/sales-tax"),
        expect.objectContaining({ rate: "0", revision: 0, reason: "اختبار الصفر الصريح" }));
});
