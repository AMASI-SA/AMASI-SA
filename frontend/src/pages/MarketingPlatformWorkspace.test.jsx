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
        "salla",
        "qoyod",
        "",
        null,
    ].forEach((provider) => {
        expect(isMarketingPlatformProvider(provider)).toBe(false);
    });
});
