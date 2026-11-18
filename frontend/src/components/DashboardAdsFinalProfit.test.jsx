import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DashboardAdsSpendCardContent } from "./DashboardAdsSpendCard";
import { ProfitCard } from "../pages/AdvancedDashboard";

// The financial rendering contract does not require navigation. Match the
// existing executive-card suites at Jest's unsupported router export boundary.
jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
}));

jest.mock("recharts", () => ({
    ResponsiveContainer: ({ children }) => <div>{children}</div>,
    LineChart: ({ children }) => <div>{children}</div>,
    Line: () => null, CartesianGrid: () => null, Tooltip: () => null,
    XAxis: () => null, YAxis: () => null,
}));

function fixture() {
    const day = "2026-11-18";
    const quality = { status: "complete", amount_available: true, amount_complete: true, provisional: false };
    const data = { date_from: day, date_to: day, total_sar: 200,
        provider_totals_sar: { snapchat: 100, meta: 60, tiktok: 30, google: 10 },
        spend_quality: { ...quality }, providers: { snapchat: {}, meta: {}, tiktok: {}, google: {} },
        daily_spend: [], hourly_spend: [] };
    const executiveSnapshot = { range: { from_date: day, to_date: day },
        totals: { total_sales: 1000, total_orders: 4, total_ads_cost: 200,
            operating_expenses_total: 75.25, net_profit: 420.75, ad_bank_commission_fees: 2.3,
            product_cost_complete: true, sales_currency_conversion_complete: true,
            ads_spend_data_complete: true, ads_spend_amount_available: true, ads_spend_provisional: false },
        ads_v2: { spend_quality: { ...quality },
            breakdown: { snapchat: 100, meta: 60, tiktok: 30, google_transitional: 10 },
            account_results: { coverage: { status: "partial", unassigned_orders: 2 } } } };
    return { data, executiveSnapshot, fromDate: day, toDate: day };
}

function render(input) {
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(<>
        <DashboardAdsSpendCardContent {...input} />
        <ProfitCard data={input.executiveSnapshot} platformSpend={input.data}
            fromDate={input.fromDate} toDate={input.toDate} />
    </>);
    return { host,
        net: host.querySelector('[data-testid="dashboard-ads-final-net-profit"]'),
        operating: host.querySelector('[data-testid="dashboard-ads-operating-expenses"]'),
        executive: host.querySelector('[data-testid="advanced-profit-net-hero"]') };
}

test("the account table and executive summary show the same server net after operating and bank fees exactly once", () => {
    const input = fixture();
    const before = JSON.stringify(input);
    const { host, net, operating, executive } = render(input);
    expect(net.querySelector("td").textContent).toBe("420.75");
    expect(executive.textContent).toContain("420.75");
    expect(operating.querySelector("td").textContent).toBe("−75.25");
    expect(net.textContent).toContain("صافي الربح بعد جميع المصروفات");
    expect(net.textContent).toContain("عمولات البنك محسوبة مرة واحدة");
    expect(host.querySelectorAll('[data-testid="dashboard-ads-account-table"] thead th')).toHaveLength(7);
    expect(JSON.stringify(input)).toBe(before);
});

test.each([0, -24.08])("both views preserve signed final profit %s and show zero operating expenses", (value) => {
    const input = fixture();
    Object.assign(input.executiveSnapshot.totals, { net_profit: value, operating_expenses_total: 0 });
    const { net, operating, executive } = render(input);
    expect(net.querySelector("td").textContent).toBe(value.toFixed(2));
    expect(executive.textContent).toContain(value.toFixed(2));
    expect(operating.querySelector("td").textContent).toBe("0.00");
});

test("available provisional spend labels the same final net as provisional in both views", () => {
    const input = fixture();
    Object.assign(input.data.spend_quality, { status: "provisional", amount_complete: false, provisional: true });
    Object.assign(input.executiveSnapshot.ads_v2.spend_quality, input.data.spend_quality);
    Object.assign(input.executiveSnapshot.totals, { ads_spend_data_complete: false, ads_spend_provisional: true });
    const { net, executive } = render(input);
    expect(net.querySelector("td").textContent).toBe("420.75تقديري");
    expect(executive.textContent).toContain("420.75");
    expect(executive.textContent).toContain("تقديري حتى آخر مزامنة");
});

test.each(["product", "currency", "provider split", "executive date", "spend date", "missing spend", "malformed net"])(
    "%s uncertainty hides stale final profit in both real components", (fault) => {
        const input = fixture();
        if (fault === "product") input.executiveSnapshot.totals.product_cost_complete = false;
        if (fault === "currency") input.executiveSnapshot.totals.sales_currency_conversion_complete = false;
        if (fault === "provider split") Object.assign(input.data.provider_totals_sar, { meta: 70, tiktok: 20 });
        if (fault === "executive date") input.executiveSnapshot.range.from_date = "2026-11-17";
        if (fault === "spend date") input.data.date_from = "2026-11-17";
        if (fault === "missing spend") input.data = null;
        if (fault === "malformed net") input.executiveSnapshot.totals.net_profit = [];
        const { net, executive } = render(input);
        expect(net.querySelector("td").textContent).not.toContain("420.75");
        expect(executive.textContent).not.toContain("420.75");
        expect(net.querySelector("td").dataset.state).not.toBe("available");
        if (fault === "product") {
            expect(net.textContent).toContain("ناقص التكلفة");
            expect(executive.textContent).toContain("ناقص التكلفة");
        }
    },
);
