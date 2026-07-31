const fs = require("fs");
const path = require("path");

const componentsDir = __dirname;
const layoutSource = fs.readFileSync(
    path.join(componentsDir, "Layout.jsx"),
    "utf8",
);
const placementSource = fs.readFileSync(
    path.join(componentsDir, "DashboardAnalyticsPlacement.jsx"),
    "utf8",
);


test("Layout delegates GA4 cards to the profit-summary placement", () => {
    expect(layoutSource).toContain(
        'import DashboardAnalyticsPlacement from "./DashboardAnalyticsPlacement"',
    );
    expect(layoutSource).toContain(
        "<DashboardAnalyticsPlacement active={isMainDashboard} />",
    );
    expect(layoutSource).not.toContain(
        'import GoogleAnalyticsRealtimeCards from "./GoogleAnalyticsRealtimeCards"',
    );
    expect(layoutSource).not.toContain(
        'import GoogleAnalyticsTrafficSourcesCard from "./GoogleAnalyticsTrafficSourcesCard"',
    );
});


test("GA4 cards are inserted immediately after the executive profit summary", () => {
    expect(placementSource).toContain(
        "'[data-testid=\"profit-summary-card\"]'",
    );
    expect(placementSource).toContain(
        'profitSummary.insertAdjacentElement("afterend", currentHost)',
    );
    expect(placementSource).toContain("<GoogleAnalyticsRealtimeCards />");
    expect(placementSource).toContain("<GoogleAnalyticsTrafficSourcesCard />");
});
