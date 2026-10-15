import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import AccountingBankReceipts, { SettlementReceiptLink } from "./AccountingBankReceipts";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));
let root, node;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    api.get.mockResolvedValue({ data: { items: [], bindings: [{provider:"tabby",bank_account_id:"bank",bank_account_name:"SYN bank"}] } });
});
afterEach(() => { act(() => root.unmount()); node.remove(); jest.clearAllMocks(); });
test("viewer can read receipts but cannot enter an amount", async () => {
    await act(async () => root.render(<AccountingBankReceipts accountingPermissions={["accounting.movements.view"]} />));
    expect(node.querySelector("form")).toBeNull();
    expect(node.textContent).toContain("لا يتغير الرصيد");
    expect(api.post).not.toHaveBeenCalled();
});
test("entry permission exposes platform, bank, amount, message and editable date without approval", async () => {
    await act(async () => root.render(<AccountingBankReceipts accountingPermissions={["accounting.receipts.create"]} />));
    expect(node.querySelector('[aria-label="المبلغ الواصل"]')).not.toBeNull();
    expect(node.querySelector('[aria-label="رسالة البنك"]')).not.toBeNull();
    expect(node.querySelector('[aria-label="تاريخ الوصول"]').value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(node.textContent).toContain("SYN bank");
    expect(node.textContent).not.toContain("اعتماد وترحيل");
});
test("multiple candidates require an explicit choice and never auto-link", async () => {
    api.get.mockResolvedValue({ data: { items: ["one","two"].map(id => ({
        id, provider:"tabby",bank_account_id:"bank",amount:"104.65",received_on:"2026-09-19",bank_reference:id
    })) } });
    await act(async () => root.render(<SettlementReceiptLink draft={{id:"draft",provider:"tabby",bank_account_id:"bank",status:"draft",amounts:{reported_net:104.65}}} canLink onLinked={() => {}} />));
    expect(node.querySelector("select").value).toBe("");
    expect(node.textContent).toContain("يوجد أكثر من احتمال");
    expect([...node.querySelectorAll("button")].find(b => b.textContent.includes("تأكيد")).disabled).toBe(true);
    expect(api.put).not.toHaveBeenCalled();
});
