import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import AccountingReports from "./AccountingReports";
import AccountingShippingCod from "./AccountingShippingCod";
import { BalanceBreakdown, formatAccountingMoney, MoneyDisplay } from "./AccountingUI";
jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() }));
let node, root;
const current = { status: "available", ledger_backend: "v2", operation_id: "MZ2-FIN-CUTOVER-001", items: [] };
const debit = { id: "leg-d", txn_group_id: "journal-one", entity_type: "supplier", entity_id: "supplier-v2-1", sub_account: "advance", side: "debit", amount: "125.25", effective_at: "2026-09-10T00:00:00+03:00", metadata: { accounting_at: "2026-09-12", evidence_ref: "source-one" } };
const credit = { ...debit, id: "leg-c", entity_type: "bank", entity_id: "bank-v2-1", sub_account: "main", side: "credit" };
beforeEach(() => {
    jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
});
afterEach(() => { act(() => root.unmount()); node.remove(); });
async function render(element) { await act(async () => root.render(element)); }
async function click(label) { await act(async () => [...node.querySelectorAll("button")].find(button => button.textContent === label).click()); }
async function change(element, value) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(element.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(element, value);
        element.dispatchEvent(new Event("change", { bubbles: true })); element.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
test.each([null, undefined, "", " ", NaN, Infinity, false, {}, "oops"])("missing/invalid money is unavailable, not zero: %s", value => {
    expect(formatAccountingMoney(value)).toBe("—");
});
test("documented zero and negative amounts remain explicit; outstanding and advance never net", async () => {
    expect(formatAccountingMoney(0)).toBe("0.00 ر.س");
    expect(formatAccountingMoney(-1250.5)).toBe("-1,250.50 ر.س");
    await render(<><MoneyDisplay value={null} /><BalanceBreakdown outstanding={500} advance={200} available /></>);
    expect(node.textContent).toContain("500.00"); expect(node.textContent).toContain("200.00"); expect(node.textContent).not.toContain("300.00");
    expect(node.querySelector('[aria-label="المبلغ غير متاح"]')).not.toBeNull();
    await render(<BalanceBreakdown outstanding={500} advance={200} available={false} />);
    expect(node.textContent).not.toContain("500.00"); expect(node.textContent).not.toContain("0.00");
});
test("journal detail includes all legs despite local account filter and uses effective_at", async () => {
    api.get.mockResolvedValue({ data: { ...current, items: [debit, credit] } });
    await render(<AccountingReports />); await click("القيود اليومية");
    await change(node.querySelector('input[type="search"]'), "supplier-v2-1");
    expect(node.querySelectorAll('table[aria-label="قيود ميزان 2"] tbody tr')).toHaveLength(1);
    const trigger = node.querySelector('button[aria-label="تفاصيل القيد journal-one"]');
    await act(async () => { trigger.focus(); trigger.click(); });
    const dialog = node.querySelector("dialog[open]");
    expect(dialog).not.toBeNull(); expect(dialog.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(dialog.textContent).toContain("bank-v2-1"); expect(dialog.textContent).toContain("2026-09-10T00:00:00+03:00");
    expect(dialog.textContent).not.toContain("2026-09-12"); expect(dialog.textContent).toContain("source-one");
    await act(async () => dialog.dispatchEvent(new Event("cancel", { bubbles: true, cancelable: true })));
    expect(node.querySelector("dialog[open]")).toBeNull(); expect(document.activeElement).toBe(trigger);
    expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
    expect(api.get.mock.calls.every(([, options]) => Object.keys(options.params).length === 0)).toBe(true);
});
test("domain view requires explicit native V2 provenance and never asks a legacy reader", async () => {
    api.get.mockResolvedValue({ data: { ...current, ledger_backend: "legacy", items: [{ entity_type: "supplier", entity_id: "old", net: 9876 }] } });
    await render(<AccountingReports initialDomain="supplier" />);
    expect(node.textContent).toContain("كشف الحساب الموحد غير متاح"); expect(node.textContent).not.toContain("9,876");
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/financial-provider-apps/accounting-module/reports/trial-balance", { params: {} });
});
test("trial balances remain server values, separated by exact subaccount, with local pagination", async () => {
    const items = Array.from({ length: 25 }, (_, i) => ({ entity_type: "supplier", entity_id: "id-" + i, sub_account: i % 2 ? "advance" : "payable", debits: null, credits: 900, net: -333 }));
    api.get.mockResolvedValue({ data: { ...current, items } });
    await render(<AccountingReports initialDomain="supplier" />);
    expect(node.querySelectorAll("tbody tr")).toHaveLength(20); expect(node.textContent).toContain("-333.00");
    expect(node.textContent).not.toContain("-900.00"); await click("التالي");
    expect(node.querySelectorAll("tbody tr")).toHaveLength(5);
    await change(node.querySelector('input[type="search"]'), "id-0");
    expect(node.querySelectorAll("tbody tr")).toHaveLength(1); expect(node.textContent).toContain("1–1 من 1");
    await click("إعادة تعيين"); expect(node.querySelectorAll("tbody tr")).toHaveLength(20);
    expect(api.get).toHaveBeenCalledTimes(1);
});
test("ad and tax views expose gaps without inferring platforms, spend or tax periods", async () => {
    api.get.mockResolvedValue({ data: { ...current, items: [] } });
    await render(<AccountingReports initialDomain="ad_account" />);
    expect(node.textContent).toContain("الصرف اليومي والتمويل غير متاح");
    expect(node.textContent).toContain("لا توجد قيود في نطاق التقرير");
    expect(node.textContent).not.toContain("0.00");
    await change(node.querySelector("select"), "tax");
    expect(node.textContent).toContain("فترات الإقرار وملفات الأدلة الضريبية غير متاحة");
    expect(api.get).toHaveBeenCalledTimes(1);
});

test("shipping presentation preserves zero and never substitutes amount for missing COD custody", async () => {
    api.get.mockResolvedValue({ data: {
        latest_rates: [], counterparties: [], bank_movements: [], courier_candidates: [],
        driver_candidates: [
            { assignment_id: "zero", order_number: "zero-custody", cod_custody_amount: 0, amount: 9876 },
            { assignment_id: "missing", order_number: "missing-custody", amount: 9876 },
        ],
    } });
    await render(<AccountingShippingCod />);
    expect(node.textContent).toContain("تحصيل 0.00 ر.س");
    expect(node.textContent).toContain("تحصيل —");
    expect(node.textContent).not.toContain("9,876");
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe("/financial-provider-apps/accounting-module/shipping-p02/workspace");
    expect(api.post).not.toHaveBeenCalled();
});

test("shipping read failure remains an error and retries only the same native endpoint", async () => {
    api.get.mockRejectedValue(new Error("unavailable"));
    await render(<AccountingShippingCod />);
    expect(node.querySelector('[role="alert"]').textContent).toContain("تعذر تحميل الشحن والتحصيل");
    expect(node.textContent).not.toContain("0.00");
    await click("إعادة المحاولة");
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(api.get.mock.calls.every(([url]) => url === "/financial-provider-apps/accounting-module/shipping-p02/workspace")).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
});
