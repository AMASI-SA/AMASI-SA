jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
}));

import {
    MARKETING_PLATFORM_PROVIDERS,
    isMarketingPlatformProvider,
} from "./MarketingPlatformWorkspace";

test("focused marketing workspace accepts only advertising providers", () => {
    expect(MARKETING_PLATFORM_PROVIDERS).toEqual([
        "snapchat_ads",
        "tiktok_ads",
        "meta_ads",
        "google_ads",
    ]);

    MARKETING_PLATFORM_PROVIDERS.forEach((provider) => {
        expect(isMarketingPlatformProvider(provider)).toBe(true);
    });

    [
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
