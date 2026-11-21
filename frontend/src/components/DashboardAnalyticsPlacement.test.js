const fs = require("fs");
const path = require("path");
const React = require("react");
const { createRoot } = require("react-dom/client");

jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => require("react").createElement("a", { href: to, ...props }, children),
}));

const { ProfitCard } = require("../pages/AdvancedDashboard");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

const dashboardCompatibilitySource = read("pages/Dashboard.jsx");
const advancedDashboardSource = read("pages/AdvancedDashboard.jsx");
const frontendIndexSource = read("index.js");
const analyticsPlacementSource = read("components/DashboardAnalyticsPlacement.jsx");
const snapchatPlacementSource = read("components/DashboardSnapchatAccountsPlacement.jsx");


test("all retired dashboard compatibility routes redirect to the advanced dashboard", () => {
    expect(dashboardCompatibilitySource).toContain(
        '<Navigate to="/dashboard-advanced" replace />',
    );
    expect(dashboardCompatibilitySource).not.toContain("AdvancedDashboard");
    expect(dashboardCompatibilitySource).not.toMatch(/api\.(get|post|put|patch|delete)\(/);
    expect(dashboardCompatibilitySource).not.toContain("setInterval(");
    expect(dashboardCompatibilitySource).not.toContain("setTimeout(");
    expect(dashboardCompatibilitySource).not.toContain("useEffect(");
    expect(dashboardCompatibilitySource).not.toContain("useState(");
});


test("legacy dashboard placement components mount no observers, portals, or polling", () => {
    for (const source of [analyticsPlacementSource, snapchatPlacementSource]) {
        expect(source).toContain("return null;");
        expect(source).not.toContain("MutationObserver");
        expect(source).not.toContain("createPortal");
        expect(source).not.toContain("requestAnimationFrame");
        expect(source).not.toContain("setInterval(");
        expect(source).not.toMatch(/api\.(get|post|put|patch|delete)\(/);
    }
});


test("the advanced dashboard owns GA4, ads, and profit details", () => {
    expect(advancedDashboardSource).toContain("<AdsExecutiveBreakdownTable");
    expect(advancedDashboardSource).toContain('data-testid="advanced-ga-active-chart"');
    expect(advancedDashboardSource).toContain("<GaLive data={ga} />");
    expect(advancedDashboardSource).toContain("<DashboardAdsSpendCard");
    expect(advancedDashboardSource).toContain("getDashboardAdsSpend");
    expect(advancedDashboardSource).toContain("mergeDashboardWithPlatformSpend");
    expect(advancedDashboardSource).not.toContain("chartData");
    expect(frontendIndexSource).not.toContain("dashboardExecutivePlatformSpendInterceptor");
});


test.each([true, false])("advanced profit opens its own payment fee details with completeness=%s", (complete) => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        React.act(() => root.render(React.createElement(ProfitCard, { data: {
            totals: {
                total_sales: 1000, total_orders: 1, total_product_cost: 100, total_ads_cost: 50,
                product_cost_complete: true, ads_spend_data_complete: true,
                ads_spend_amount_available: true, payment_fees_complete: complete,
                payment_fee_review_reasons: complete ? [] : ["payment_awaiting_collection"],
                total_payment_fees: complete ? 25 : null, net_profit: complete ? 825 : null,
            },
            ads_v2: { spend_quality: { status: "complete", amount_complete: true, amount_available: true } },
            payment_breakdown: [{ key: "paypal", name: "باي بال", orders_count: 1, total_sales: 1000,
                fee_amount: complete ? 25 : null }],
        } })));
        const trigger = container.querySelector('[data-testid="advanced-profit-row-payment"]');
        expect(trigger).not.toBeNull();
        expect(container.querySelector('[data-testid="advanced-profit-payment-details"]')).toBeNull();
        React.act(() => trigger.click());
        const details = container.querySelector('[data-testid="advanced-profit-payment-details"]');
        expect(details).not.toBeNull();
        expect(details.textContent).toContain("باي بال");
        expect(details.querySelector("tbody tr td:last-child").textContent).toBe(complete ? "25.00" : "غير مكتمل");
        expect(details.querySelector("tbody tr:last-child td:last-child").textContent).toBe(complete ? "25.00" : "غير مكتمل");
        expect(trigger.getAttribute("aria-expanded")).toBe("true");
        const hero = container.querySelector('[data-testid="advanced-profit-net-hero"]');
        expect(hero.textContent).toContain(complete ? "825.00" : "بانتظار تحصيل بعض الطلبات");
        React.act(() => trigger.click());
        expect(container.querySelector('[data-testid="advanced-profit-payment-details"]')).toBeNull();
    } finally {
        React.act(() => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});


test("the advanced dashboard retains governed date refresh and latest-snapshot behavior", () => {
    expect(advancedDashboardSource).toContain(
        "const result = await loadDashboardPeriodSnapshot({",
    );
    expect(advancedDashboardSource).toContain('endpoint: "/dashboard-v2/cards/executive"');
    expect(advancedDashboardSource).toContain(
        "isLatest: (sequence) => sequence === requestSequenceRef.current",
    );
    expect(advancedDashboardSource).toContain(
        "const visibleData = dataKey === filterKey ? data : null",
    );
    expect(advancedDashboardSource).toContain("<ProfitCard data={visibleData}");
    expect(advancedDashboardSource).toContain("isLatest(requestSequence)");
    expect(advancedDashboardSource).not.toContain("setData(null)");
    expect(advancedDashboardSource).toContain("requestSequenceRef");
    expect(advancedDashboardSource).toContain("DASHBOARD_AUTO_REFRESH_MS");
    expect(advancedDashboardSource).toContain("Keep the last good cart snapshot");
    expect(advancedDashboardSource).not.toContain(
        "/dashboard-v2/unified-marketing-shadow",
    );
    expect(advancedDashboardSource).toContain("/ads-manager/ai-monitor/unified-shadow");
    expect(advancedDashboardSource).toContain("القرارات غير مفعلة");
    expect(advancedDashboardSource).toContain("التغطية غير مكتملة");
    expect(advancedDashboardSource).toContain("مصالح مباشرة مع Snapchat");
});
