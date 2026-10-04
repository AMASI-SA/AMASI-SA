import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { ProfitCard } from "./AdvancedDashboard";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
jest.mock("react-router-dom", () => ({ Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a> }));
const filters = { from: "2026-10-01", to: "2026-10-04", payment_methods: ["mada"], shipping_companies: ["iMile"] };
const pagination = cursor => ({ has_more: Boolean(cursor), next_cursor: cursor, total: 101 });
const carrier = (name, orders_count = 5) => ({ name, orders_count, total_cost: 25, cost_per_unit: 5 });
const payment = name => ({ key: name, name, orders_count: 4, total_sales: 100, fee_amount: 9 });
const seed = () => ({ totals: { total_shipping_cost: 9876, total_payment_fees: 5432 },
    shipping_breakdown: [carrier("iMile")], payment_breakdown: [payment("mada")],
    financial_pagination: { shipping: pagination("shipping-next"), payments: pagination("payments-next") } });
const response = items => ({ data: { items, pagination: pagination(null), totals: { total_shipping_cost: 999999 } } });
function deferred() { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; }
let root, container;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
const render = async (data, props = {}) => act(async () => root.render(<ProfitCard data={data} filters={filters} {...props} />));
const open = async kind => act(async () => container.querySelector(`[data-testid="advanced-profit-row-${kind}"]`).click());
const click = async (label, text = "التالي") => act(async () => [...container.querySelector(`[aria-label="${label}"]`).querySelectorAll("button")].find(button => button.textContent === text).click());

test("shipping pages replace carriers and preserve full-period totals and current counts", async () => {
    const data = seed();
    api.get.mockResolvedValueOnce(response([carrier("Store Courier", 17)]));
    await render(data);
    await open("shipping");
    const detail = () => container.querySelector('[data-testid="advanced-profit-shipping-details"]');
    expect(detail().textContent).toContain("iMile");
    await click("صفحات شركات الشحن");
    expect(detail().textContent).not.toContain("iMile");
    expect(detail().textContent).toContain("Store Courier");
    expect(detail().textContent).toContain("17");
    expect(detail().textContent).toContain("9,876.00");
    expect(detail().textContent).not.toContain("999,999");
    const url = new URL(api.get.mock.calls[0][0], "https://test.example");
    expect(url.pathname).toBe("/dashboard-v2/financial-details");
    expect(Object.fromEntries(url.searchParams)).toEqual({ from_date: filters.from, to_date: filters.to,
        payment_methods: "mada", shipping_companies: "iMile", kind: "shipping", limit: "50", cursor: "shipping-next" });
    api.get.mockResolvedValueOnce(response([carrier("iMile")]));
    await click("صفحات شركات الشحن", "السابق");
    expect(new URL(api.get.mock.calls[1][0], "https://test.example").searchParams.has("cursor")).toBe(false);
});

test("payment parent and account child pages are bounded and preserve the fee footer", async () => {
    const data = seed();
    data.payment_breakdown = [{ key: "ad_bank_commissions", name: "Bank commissions",
        sub_methods: [payment("account-one")], sub_methods_pagination: pagination("accounts-next") }];
    api.get.mockResolvedValueOnce(response([payment("account-two")])).mockResolvedValueOnce(response([payment("tabby")]));
    await render(data);
    await open("payment");
    await click("صفحات Bank commissions");
    expect(container.textContent).not.toContain("account-one");
    expect(container.textContent).toContain("account-two");
    let query = new URL(api.get.mock.calls[0][0], "https://test.example").searchParams;
    expect(query.get("kind")).toBe("payment_methods");
    expect(query.get("parent_key")).toBe("ad_bank_commissions");
    expect(query.get("limit")).toBe("50");
    await click("صفحات طرق الدفع");
    expect(container.textContent).not.toContain("account-two");
    expect(container.textContent).toContain("tabby");
    expect(container.querySelector('[data-testid="advanced-profit-payment-details"]').textContent).toContain("5,432.00");
    query = new URL(api.get.mock.calls[1][0], "https://test.example").searchParams;
    expect(query.get("kind")).toBe("payments");
    expect(query.has("parent_key")).toBe(false);
});

test("duplicate clicks and stale detail responses cannot replace a new summary", async () => {
    const pending = deferred();
    api.get.mockReturnValueOnce(pending.promise);
    await render(seed());
    await open("shipping");
    await click("صفحات شركات الشحن");
    await click("صفحات شركات الشحن", "جارٍ التحميل…");
    expect(api.get).toHaveBeenCalledTimes(1);
    const fresh = seed();
    fresh.shipping_breakdown = [carrier("Fresh Courier")];
    await render(fresh, { filters: { ...filters, from: "2026-10-02" } });
    await act(async () => pending.resolve(response([carrier("Stale Courier")])));
    expect(container.textContent).toContain("Fresh Courier");
    expect(container.textContent).not.toContain("Stale Courier");
});

test("failed detail page retains rows and retries the same cursor; summary loading disables paging", async () => {
    const data = seed();
    api.get.mockRejectedValueOnce(new Error("temporary")).mockResolvedValueOnce(response([carrier("Recovered")]));
    await render(data);
    await open("shipping");
    await click("صفحات شركات الشحن");
    expect(container.textContent).toContain("iMile");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    await click("صفحات شركات الشحن", "إعادة المحاولة");
    expect(api.get.mock.calls[0][0]).toBe(api.get.mock.calls[1][0]);
    expect(container.textContent).toContain("Recovered");
    await render(seed(), { loading: true });
    await click("صفحات شركات الشحن");
    expect(api.get).toHaveBeenCalledTimes(2);
});
