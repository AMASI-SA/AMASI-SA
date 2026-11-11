import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useOptionalAuth } from "../../context/AuthContext";
import { operationalApi } from "./api";
import DailyFinancialMovements, { dailyRange } from "./DailyFinancialMovements";
jest.mock("./api", () => ({ operationalApi: { dailyFinancialMovements: jest.fn() }, messageFor: () => "تعذر تحميل الحركات" }));
jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
jest.mock("./financialPosition.css", () => ({}));
jest.mock("../../lib/safeSpreadsheet", () => ({}));
global.IS_REACT_ACT_ENVIRONMENT = true;
const fixture = query => ({ ...query, page_size: 10, timezone: "Asia/Riyadh", total: 11, total_pages: 2, has_next: query.page === 1, has_previous: query.page === 2, snapshot: "abc", financial_source: "operational", coverage: { complete: true, notes: [] }, totals_complete: true, totals_by_currency: [{ currency: "SAR", incoming: "200.00", outgoing: "10.00", noncash: "30.00" }], items: [{ id: "one", employee_name: "المحاسب", employee_source: "recorded", account_name: "حساب مصرح", direction: "outgoing", kind: "payment", amount: "10.00", currency: "SAR", counterparty_name: "المورد", activity_at: "2026-11-07T21:10:00Z", recorded_at: "2026-11-07T21:10:00Z", occurred_at: "2026-11-06T10:00:00Z", account_balance_status: "available", account_balance_after: "9007199254740993.25", account_balance_currency: "SAR", counterparty_balance_status: "available", counterparty_balance_after: "-120.00", counterparty_balance_currency: "SAR", counterparty_balance_basis: "confirmed_obligations" }] });
let host, root;
const render = async () => { await act(async () => root.render(<DailyFinancialMovements />)); };
const click = async text => { await act(async () => [...host.querySelectorAll("button")].find(node => node.textContent.includes(text)).click()); };
const change = async (label, value) => { const node = host.querySelector(`[aria-label="${label}"]`); await act(async () => { Object.getOwnPropertyDescriptor(node.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(node, value); node.dispatchEvent(new Event(node.tagName === "SELECT" ? "change" : "input", { bubbles: true })); }); };
beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); useOptionalAuth.mockReturnValue({ user: { id: "employee", owner_id: "owner", role: "employee" } }); operationalApi.dailyFinancialMovements.mockImplementation(query => Promise.resolve(fixture(query))); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.clearAllMocks(); });
test("Riyadh date presets are inclusive across month/year boundaries", () => { expect(dailyRange("today", "2026-01-01")).toEqual({ from_date: "2026-01-01", to_date: "2026-01-01" }); expect(dailyRange("yesterday", "2026-01-01")).toEqual({ from_date: "2025-12-31", to_date: "2025-12-31" }); expect(dailyRange("week", "2026-01-01").from_date).toBe("2025-12-26"); expect(dailyRange("month", "2026-01-01").from_date).toBe("2025-12-03"); });
test("shows actual actor, historical amounts and distinct times without float rounding", async () => { await render(); const row = host.querySelector('[data-testid="daily-movement-row"]'); expect(row.textContent).toContain("المحاسب"); expect(row.textContent).toContain("9,007,199,254,740,993.25"); expect(row.textContent).toContain("-120.00"); expect(row.textContent).toContain("علينا للجهة"); expect(row.textContent).toContain("وقت التسجيل:"); expect(row.textContent).toContain("وقت الحدث الأصلي:"); expect(row.textContent).toContain("المستحقات المؤكدة فقط"); expect(host.querySelector('[aria-label="إجماليات الفترة"]').textContent).toContain("200.00"); });
test("paginates on server snapshot and refresh returns to page one", async () => { await render(); await click("الصفحة التالية"); expect(operationalApi.dailyFinancialMovements).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, page_size: 10, snapshot: "abc" })); await click("إعادة تحميل الصفحة الأولى"); expect(operationalApi.dailyFinancialMovements).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 })); expect(operationalApi.dailyFinancialMovements.mock.calls.at(-1)[0].snapshot).toBeUndefined(); });
test("custom range is applied only after submission", async () => { await render(); await change("الفترة", "custom"); await change("من تاريخ", "2026-11-01"); await change("إلى تاريخ", "2026-11-07"); expect(operationalApi.dailyFinancialMovements).toHaveBeenCalledTimes(1); await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); expect(operationalApi.dailyFinancialMovements).toHaveBeenLastCalledWith({ from_date: "2026-11-01", to_date: "2026-11-07", page: 1, page_size: 10 }); });
test("unknown history and missing full-range totals stay unavailable", async () => { operationalApi.dailyFinancialMovements.mockImplementation(query => { const data = fixture(query); delete data.totals_by_currency; data.items[0].account_balance_status = "unavailable"; data.items[0].account_balance_after = "999.00"; data.items[0].counterparty_balance_after = null; return Promise.resolve(data); }); await render(); const row = host.querySelector('[data-testid="daily-movement-row"]'); expect(row.cells[6].textContent).toBe("غير متاح"); expect(row.cells[7].textContent).toBe("غير متاح"); expect(host.textContent).not.toContain("999.00"); expect(host.textContent).toContain("إجماليات الفترة غير متاحة"); });
test("permission denial clears visible rows", async () => { await render(); operationalApi.dailyFinancialMovements.mockRejectedValue({ response: { status: 403 } }); await click("إعادة تحميل الصفحة الأولى"); expect(host.querySelector('[data-testid="daily-movement-row"]')).toBeNull(); expect(host.querySelector('[role="alert"]').textContent).toContain("صلاحية"); });
test("old actor response cannot disclose data after account switch", async () => { let resolve; operationalApi.dailyFinancialMovements.mockImplementationOnce(query => new Promise(done => { resolve = () => done(fixture(query)); })); await render(); useOptionalAuth.mockReturnValue({ user: { id: "other", owner_id: "owner", role: "employee" } }); operationalApi.dailyFinancialMovements.mockRejectedValue({ response: { status: 403 } }); await render(); await act(async () => resolve()); expect(host.querySelector('[data-testid="daily-movement-row"]')).toBeNull(); });
test("cursor change requires explicit first-page snapshot", async () => { await render(); operationalApi.dailyFinancialMovements.mockRejectedValue({ response: { status: 409 } }); await click("الصفحة التالية"); expect(host.querySelector('[data-testid="daily-movement-row"]')).toBeNull(); expect(host.textContent).toContain("تغيرت الحركات أو الصلاحيات"); });
test("works independently of review page and exposes no write controls", async () => { await render(); expect(Object.keys(operationalApi)).toEqual(["dailyFinancialMovements"]); expect(host.textContent).not.toContain("اعتماد الإيصال"); expect(host.textContent).not.toContain("رفض الإيصال"); });

test.each([
    { total: null }, { total: -1 }, { total_pages: null }, { snapshot: null },
    { timezone: "UTC" }, { items: Array.from({ length: 11 }, (_, index) => ({ id: `overflow-${index}` })) },
])("invalid scoped pagination contract never renders money: %j", async patch => {
    operationalApi.dailyFinancialMovements.mockImplementation(query => Promise.resolve({ ...fixture(query), ...patch }));
    await render();
    expect(host.querySelector('[data-testid="daily-movement-row"]')).toBeNull();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test("editing custom dates keeps the displayed result's applied range explicit", async () => {
    await render();
    const query = operationalApi.dailyFinancialMovements.mock.calls[0][0];
    await change("الفترة", "custom"); await change("من تاريخ", "2026-10-01");
    expect(host.querySelector('[aria-label="الفترة المعروضة"]').textContent).toContain(query.from_date);
    expect(host.querySelector('[aria-label="الفترة المعروضة"]').textContent).not.toContain("2026-10-01");
});

test("incomplete totals remain explicitly qualified and currencies stay separate", async () => {
    operationalApi.dailyFinancialMovements.mockImplementation(query => Promise.resolve({ ...fixture(query), totals_complete: false,
        totals_by_currency: [{ currency: "SAR", incoming: "200.00", outgoing: "10.00", noncash: "30.00" }, { currency: "AED", incoming: "900.00", outgoing: "0.00", noncash: "0.00" }] }));
    await render();
    const totals = host.querySelector('[aria-label="إجماليات الفترة"]');
    expect(totals.textContent).toContain("غير مكتملة");
    expect(totals.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(totals.textContent).not.toContain("1,100.00");
    expect(totals.textContent).toContain("وليس رصيد الحساب");
});
