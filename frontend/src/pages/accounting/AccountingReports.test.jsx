import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import AccountingReports from "./AccountingReports";
jest.mock("../../lib/api", () => ({ get: jest.fn() }));
const BASE = "/financial-provider-apps/accounting-module/reports";
const ready = { status: "available", operation_id: "MZ2-FIN-CUTOVER-001", cutover_at: "2026-08-01T00:00:00+03:00",
    opening_balance_txn_group_id: "SYN-APPROVED-OPENING", assets: { banks: 1000 },
    liabilities: { customer_refund_payable: 115, customer_advance: 50 },
    totals: { total_assets: 1000, total_liabilities: 165, net_position: 835 } };
let root, node;
const click = async label => act(async () => [...node.querySelectorAll("button")].find(button => button.textContent === label).click());
async function date(value) {
    await act(async () => {
        const input = node.querySelector('input[type="date"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
    });
}
async function submit() { await act(async () => node.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }
beforeEach(() => {
    jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
});
afterEach(() => { act(() => root.unmount()); node.remove(); });

test("current report uses only MZ2 endpoint and shows customer liabilities and scope", async () => {
    api.get.mockResolvedValue({ data: ready });
    await act(async () => root.render(<AccountingReports />));
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith(`${BASE}/financial-position`, { params: {} });
    expect(node.textContent).toContain("التزام استرداد العميل");
    expect(node.textContent).toContain("تحصيلات العملاء المقدمة");
    expect(node.textContent).toContain("115.00");
    expect(node.textContent).toContain("SYN-APPROVED-OPENING");
    expect(node.querySelector("a")).toBeNull();
    await click("المركز المالي");
    expect(node.textContent).toContain("115.00");
    expect(api.get).toHaveBeenCalledTimes(1);
});

test("historical selection is explicit and only as_of is sent; current clears it", async () => {
    api.get.mockResolvedValue({ data: ready });
    await act(async () => root.render(<AccountingReports />));
    await date("2026-08-31");
    expect(api.get).toHaveBeenCalledTimes(1);
    await submit();
    expect(api.get).toHaveBeenLastCalledWith(`${BASE}/financial-position`, { params: { as_of: "2026-08-31" } });
    expect(node.textContent).toContain("2026-08-31 — نهاية اليوم بتوقيت الرياض");
    await click("التقرير الحالي");
    expect(api.get).toHaveBeenLastCalledWith(`${BASE}/financial-position`, { params: {} });
    expect(node.querySelector('input[type="date"]').value).toBe("");
});

test.each(["not_ready", "needs_opening_balance"])("%s does not display financial zero or stale legacy amounts", async status => {
    api.get.mockResolvedValue({ data: { ...ready, status, reason: "opening_evidence_required", assets: { banks: 999999 } } });
    await act(async () => root.render(<AccountingReports />));
    expect(node.textContent).toContain("لا تمثل هذه الحالة رصيدًا صفريًا");
    expect(node.textContent).toContain("opening_evidence_required");
    expect(node.querySelector('[data-testid="accounting-financial-position"]')).toBeNull();
    expect(node.textContent).not.toContain("999,999");
    expect(node.textContent).not.toContain("0.00");
    expect(api.get.mock.calls.every(([url]) => url.startsWith(BASE))).toBe(true);
});

test("request failure clears the previous report and never falls back to shared readers", async () => {
    api.get.mockResolvedValueOnce({ data: ready }).mockRejectedValueOnce(new Error("forbidden"));
    await act(async () => root.render(<AccountingReports />));
    expect(node.textContent).toContain("115.00");
    await date("2026-09-02"); await submit();
    expect(node.querySelector('[role="alert"]').textContent).toContain("تعذر تحميل تقرير ميزان 2");
    expect(node.textContent).not.toContain("115.00");
    expect(node.querySelector('[data-testid="accounting-report-readiness"]')).toBeNull();
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(api.get.mock.calls.every(([url]) => url.startsWith(BASE))).toBe(true);
});

test("only selected trial or journal report is fetched and its rows are shown", async () => {
    api.get.mockResolvedValueOnce({ data: ready });
    await act(async () => root.render(<AccountingReports />));
    api.get.mockResolvedValueOnce({ data: { ...ready, items: [{ entity_type: "liability", entity_id: "SYN-CASE", sub_account: "customer_refund_payable", debits: 40, credits: 115, net: -75 }] } });
    await click("ميزان المراجعة");
    expect(api.get).toHaveBeenLastCalledWith(`${BASE}/trial-balance`, { params: {} });
    expect(node.querySelector('table[aria-label="ميزان مراجعة ميزان 2"]')).not.toBeNull();
    expect(node.textContent).toContain("-75.00");
    api.get.mockResolvedValueOnce({ data: { ...ready, items: [{ id: "leg", txn_group_id: "SYN-PAYMENT-GROUP", entity_id: "SYN-CASE", sub_account: "customer_refund_payable", side: "debit", amount: 40, metadata: { accounting_at: "2026-09-02T07:00:00Z" } }] } });
    await click("القيود اليومية");
    expect(api.get).toHaveBeenLastCalledWith(`${BASE}/journals`, { params: {} });
    expect(node.textContent).toContain("SYN-PAYMENT-GROUP");
    expect(node.textContent).toContain("2026-09-02T07:00:00Z");
    expect(api.get).toHaveBeenCalledTimes(3);
    expect(node.querySelector('table[aria-label="ميزان مراجعة ميزان 2"]')).toBeNull();
});

test("a superseded slow response cannot overwrite a newly selected report", async () => {
    let resolveFirst;
    api.get.mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve; }));
    await act(async () => root.render(<AccountingReports />));
    api.get.mockResolvedValueOnce({ data: { status: "needs_opening_balance", reason: "not_verified" } });
    await click("القيود اليومية");
    await act(async () => resolveFirst({ data: ready }));
    expect(node.textContent).toContain("بانتظار رصيد افتتاحي معتمد");
    expect(node.textContent).not.toContain("115.00");
    expect(node.querySelector("table")).toBeNull();
});
