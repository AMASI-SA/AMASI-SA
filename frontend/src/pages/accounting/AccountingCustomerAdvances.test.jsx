import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import AccountingCustomerAdvances from "./AccountingCustomerAdvances";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
let node, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    api.get.mockResolvedValue({ data: { items: [{ id: "advance", order_number: "SYN-ORDER", provider: "tamara", amount: "115.00", paid: "40.00", remaining: "75.00", state: "partially_paid", cancellation: {}, capture_txn_group_id: "capture-group", due_txn_group_id: "due-group" }], payments: [] } });
});
afterEach(() => { act(() => root.unmount()); node.remove(); jest.clearAllMocks(); });
test("read permission sees liabilities without capture or repayment actions", async () => {
    await act(async () => root.render(<AccountingCustomerAdvances accountingPermissions={["accounting.movements.view"]} />));
    expect(node.textContent).toContain("115.00"); expect(node.textContent).toContain("75.00");
    expect(node.textContent).toContain("due-group"); expect(node.querySelector("input")).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
});
test("capture never defaults the reviewed original VAT to zero", async () => {
    await act(async () => root.render(<AccountingCustomerAdvances accountingPermissions={["accounting.advances.recognize"]} />));
    expect(node.querySelector('[aria-label="الضريبة المثبتة للتحصيل المقدم"]').value).toBe("");
    const capture = [...node.querySelectorAll("button")].find(x => x.textContent === "إثبات التحصيل المقدم الموثق");
    expect(capture.disabled).toBe(true);
    expect(node.textContent).toContain("الحالات الضريبية معلقة لمراجعة المحاسب");
    expect(node.textContent).not.toContain("اعتماد رد التحصيل المقدم المنفذ");
});

test("different executor requires a reviewed document and exposes all provider choices", async () => {
    await act(async () => root.render(<AccountingCustomerAdvances accountingPermissions={["accounting.advances.refund"]} />));
    const select = node.querySelector('[aria-label="جهة رد المقدم SYN-ORDER"]');
    expect([...select.options].map(o => o.value)).toEqual(["salla", "tamara", "tabby", "emkan", "bank"]);
    await act(async () => { select.value = "tabby"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(node.querySelector('[aria-label="مستند رد المقدم SYN-ORDER"]')).not.toBeNull();
    expect(node.textContent).toContain("يربط العميل والطلب ومبلغ الرد وتاريخه ومعرّف الاسترداد");
    expect([...node.querySelectorAll("button")].find(b => b.textContent === "اعتماد رد التحصيل المقدم المنفذ").disabled).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
});
