jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
}));

import {
    MARKETING_PLATFORM_PROVIDERS,
    isMarketingPlatformProvider,
} from "./MarketingPlatformWorkspace";

test("marketing performance workspace accepts report platform identifiers only", () => {
    expect(MARKETING_PLATFORM_PROVIDERS).toEqual([
        "snapchat",
        "tiktok",
        "meta",
        "google",
    ]);

    MARKETING_PLATFORM_PROVIDERS.forEach((provider) => {
        expect(isMarketingPlatformProvider(provider)).toBe(true);
    });

    [
        "snapchat_ads",
        "tiktok_ads",
        "meta_ads",
        "google_ads",
        "google_analytics_4",
        "google_merchant_center",
        "salla",
        "qoyod",
        "",
        null,
    ].forEach((provider) => {
        expect(isMarketingPlatformProvider(provider)).toBe(false);
    });
});

test("account decision history has stable deep-link query parameters", () => {
    const fs = require("fs");
    const path = require("path");
    const source = fs.readFileSync(
        path.join(__dirname, "MarketingPlatformWorkspace.jsx"),
        "utf8",
    );
    expect(source).toContain('params.set("tab", next.tab)');
    expect(source).toContain('params.set("account", next.accountId)');
    expect(source).toContain('params.set("history_page"');
    expect(source).toContain("<AdAccountDecisionHistory");
    expect(source).toContain("onSelect={selectHistoryAccount}");
});
