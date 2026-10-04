import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { TopProductsCard, SummaryStrip } from "./AdvancedDashboard";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
jest.mock("react-router-dom", () => ({ Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a> }));

const filters = { from: "2026-10-01", to: "2026-10-04", payment_methods: ["mada"], shipping_companies: ["aramex"] };
const fullSummary = { product_count: 100, total_units: 1000, total_sales: 12345, total_cost: 2345, priced_net_profit: 10000, priced_profit_count: 98, has_unpriced_products: true, sales_currency_conversion_complete: true };
const row = identity => ({ identity, salla_product_id: identity, name: `name-${identity}`, units_sold: 1, total_sales: 10, total_cost: 2, net_profit: 8, cost_status: "complete" });
const initial = ids => ({ product_rows: ids.map(row), product_pagination: { next_cursor: "cursor-two", has_more: true, total: 100 }, product_profit_summary: fullSummary, missing_products_count: 75 });
const response = (ids, cursor = null) => ({ data: { items: ids.map(row), pagination: { next_cursor: cursor, has_more: Boolean(cursor), total: 100 }, product_profit_summary: fullSummary } });
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
let root, container;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
const render = async (summary, selectedFilters = filters) => act(async () => root.render(<TopProductsCard rows={summary.product_rows} summary={summary} filters={selectedFilters} />));
const click = async label => act(async () => [...container.querySelectorAll("button")].find(button => button.textContent === label).click());

test("product pages replace previous rows, while every footer total remains full-period", async () => {
    const summary = initial(Array.from({ length: 50 }, (_, i) => `first-${i}`));
    api.get.mockResolvedValueOnce(response(["second-page"]));
    await render(summary);
    expect(api.get).not.toHaveBeenCalled();
    for (let i = 0; i < 9; i += 1) await click("المزيد");
    expect(container.querySelectorAll('[data-testid^="advanced-top-product-row-"]')).toHaveLength(50);
    const footer = container.querySelector('[data-testid="advanced-top-products-footer"]').textContent;
    expect(footer).toContain("1,000");
    expect(footer).toContain("12,345.00");
    expect(footer).toContain("2,345.00");
    expect(footer).toContain("10,000.00");
    await click("المزيد");
    expect(container.querySelectorAll('[data-testid^="advanced-top-product-row-"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="advanced-top-product-row-first-0"]')).toBeNull();
    expect(container.querySelector('[data-testid="advanced-top-product-row-second-page"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="advanced-top-products-footer"]').textContent).toBe(footer);
    const query = new URL(api.get.mock.calls[0][0], "https://test.example").searchParams;
    expect(Object.fromEntries(query)).toEqual({ from_date: filters.from, to_date: filters.to, payment_methods: "mada", shipping_companies: "aramex", kind: "products", limit: "50", cursor: "cursor-two" });
    api.get.mockResolvedValueOnce(response(["first-again"], "cursor-two"));
    await click("الصفحة السابقة");
    expect(new URL(api.get.mock.calls[1][0], "https://test.example").searchParams.has("cursor")).toBe(false);
    expect(container.querySelector('[data-testid="advanced-top-product-row-first-again"]')).not.toBeNull();
});

test("duplicate clicks and stale responses cannot overwrite a refreshed product snapshot", async () => {
    const oldRequest = deferred(), currentRequest = deferred();
    const old = initial(["old"]), fresh = { ...initial(["fresh"]), product_pagination: { next_cursor: "fresh-cursor", has_more: true, total: 100 } };
    api.get.mockReturnValueOnce(oldRequest.promise).mockReturnValueOnce(currentRequest.promise);
    await render(old);
    await click("المزيد");
    await render(fresh, { ...filters, from: "2026-10-02" });
    await click("المزيد");
    await act(async () => oldRequest.resolve(response(["stale"])));
    expect(container.querySelector('[data-testid="advanced-top-product-row-stale"]')).toBeNull();
    const loadingButton = [...container.querySelectorAll("button")].find(button => button.textContent === "جارٍ التحميل…");
    expect(loadingButton.disabled).toBe(true);
    await act(async () => loadingButton.click());
    expect(api.get).toHaveBeenCalledTimes(2);
    await act(async () => currentRequest.resolve(response(["current-page"])));
    expect(container.querySelector('[data-testid="advanced-top-product-row-current-page"]')).not.toBeNull();
});

test("failed product pages preserve the current page and retry the same cursor", async () => {
    const seed = initial(["kept"]);
    api.get.mockRejectedValueOnce(new Error("temporary")).mockResolvedValueOnce(response(["recovered"]));
    await render(seed);
    await click("المزيد");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="advanced-top-product-row-kept"]')).not.toBeNull();
    await click("المزيد");
    expect(api.get.mock.calls[0][0]).toBe(api.get.mock.calls[1][0]);
    expect(container.querySelector('[data-testid="advanced-top-product-row-recovered"]')).not.toBeNull();
});

test("missing costs open a paginated dashboard dialog, never a partial-cohort product_ids link", async () => {
    const seed = initial(["sold"]);
    api.get.mockResolvedValueOnce({ data: { items: [{ ...row("missing-one"), cost_status: "missing" }], pagination: { has_more: true, next_cursor: "missing-next", total: 75 } } })
        .mockResolvedValueOnce({ data: { items: [{ ...row("missing-two"), catalog_product_found: false }], pagination: { has_more: false, next_cursor: null, total: 75 } } });
    await act(async () => root.render(<SummaryStrip data={{ product_cost_v2: seed }} filters={filters} />));
    await act(async () => container.querySelector("button").click());
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    const firstQuery = new URL(api.get.mock.calls[0][0], "https://test.example").searchParams;
    expect(firstQuery.get("kind")).toBe("missing");
    expect(firstQuery.get("limit")).toBe("50");
    const costLink = container.querySelector('[role="dialog"] a');
    expect(costLink.getAttribute("href")).toContain("product=missing-one");
    expect(costLink.getAttribute("href")).not.toContain("product_ids");
    expect(costLink.getAttribute("target")).toBe("_blank");
    await click("التالي");
    expect(container.textContent).not.toContain("name-missing-one");
    expect(container.textContent).toContain("name-missing-two");
    expect(container.querySelector('[role="dialog"] a')).toBeNull();
    expect(container.textContent).toContain("75 منتجًا");
});

test("a period summary refresh disables old product cursors", async () => {
    const seed = initial(["old-period"]);
    await render(seed);
    await act(async () => root.render(<TopProductsCard rows={seed.product_rows} summary={seed} filters={{ ...filters, from: "2026-10-03" }} loading />));
    const more = [...container.querySelectorAll("button")].find(button => button.textContent === "المزيد");
    expect(more.disabled).toBe(true);
    await act(async () => more.click());
    expect(api.get).not.toHaveBeenCalled();
});

test("missing-product retry repeats the failed next-page cursor without dropping the current page", async () => {
    const seed = initial(["sold"]);
    api.get.mockResolvedValueOnce({ data: { items: [row("missing-one")], pagination: { has_more: true, next_cursor: "missing-next", total: 75 } } })
        .mockRejectedValueOnce(new Error("temporary"))
        .mockResolvedValueOnce({ data: { items: [row("missing-two")], pagination: { has_more: false, total: 75 } } });
    await act(async () => root.render(<SummaryStrip data={{ product_cost_v2: seed }} filters={filters} />));
    await act(async () => container.querySelector("button").click());
    await click("التالي");
    expect(container.textContent).toContain("name-missing-one");
    await click("إعادة المحاولة");
    expect(api.get.mock.calls[1][0]).toBe(api.get.mock.calls[2][0]);
    expect(container.textContent).toContain("name-missing-two");
    expect(container.textContent).not.toContain("name-missing-one");
});
