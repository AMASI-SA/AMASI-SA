import { renderToStaticMarkup } from "react-dom/server";

import {
    GoogleAdsReadinessCard,
    isAllPlatformsLocation,
} from "./GoogleAdsAllPlatformsCard";

test("Google Ads card is limited to the all-platforms workspace", () => {
    expect(isAllPlatformsLocation({ pathname: "/ads-manager", search: "" })).toBe(true);
    expect(isAllPlatformsLocation({ pathname: "/ads-manager", search: "?provider=all" })).toBe(true);
    expect(isAllPlatformsLocation({ pathname: "/ads-manager", search: "?provider=google" })).toBe(false);
    expect(isAllPlatformsLocation({ pathname: "/integrations-v2", search: "?provider=google_ads" })).toBe(false);
});

test("Google Ads appears without fabricated performance metrics", () => {
    const markup = renderToStaticMarkup(
        <GoogleAdsReadinessCard
            integration={{
                provider: "google_ads",
                connection_status: "connected",
                connection_provenance: "api_connection",
                accounts: [
                    { external_account_id: "customer-1" },
                    { external_account_id: "customer-2" },
                ],
                last_sync_at: "2026-08-01T15:00:00+00:00",
                health: { score: 88 },
            }}
        />,
    );

    expect(markup).toContain('data-testid="ads-provider-google"');
    expect(markup).toContain("إعلانات Google");
    expect(markup).toContain("تقارير الأداء غير مفعّلة بعد");
    expect(markup).toContain("لا تدخل في الإجماليات");
    expect(markup).toContain("/ads-manager?provider=google");
    expect(markup).toContain("/integrations-v2?provider=google_ads");
    expect(markup).not.toContain("0.00 ر.س");
});
