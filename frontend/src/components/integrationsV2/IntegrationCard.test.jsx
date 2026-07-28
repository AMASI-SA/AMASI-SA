import { renderToStaticMarkup } from "react-dom/server";
import IntegrationCard from "./IntegrationCard";

test("integration card renders required status, permissions, AI limits, and safe actions", () => {
    const sentinel = "SENTINEL-TOKEN-MUST-NOT-RENDER";
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={{
                provider: "meta_ads",
                name: "Meta Ads",
                name_ar: "إعلانات ميتا",
                connection_status: "connected",
                source_mode: "legacy_connection",
                accounts: [{
                    mezan_integration_account_id: "account-1",
                    display_name: "AMASI Ads",
                    external_account_id: "act_123",
                    currency: "SAR",
                    timezone: "Asia/Riyadh",
                }],
                permissions: {
                    current: ["ads_read"],
                    missing: ["read_insights"],
                    unknown: false,
                },
                last_sync_at: "2026-07-28T10:00:00+00:00",
                data_delay_minutes: 30,
                health: { score: 82, data_quality: "good" },
                latest_error: null,
                ai: {
                    can: ["قراءة أداء الحملات"],
                    cannot: ["تعديل الميزانيات"],
                },
                actions: {
                    test_connection: { enabled: true },
                    reconnect: { enabled: true },
                    settings: { enabled: true },
                    disconnect: { enabled: true, reason: sentinel },
                },
            }}
            settingsAvailable
            onTest={() => {}}
            onSettings={() => {}}
        />,
    );

    expect(markup).toContain("إعلانات ميتا");
    expect(markup).toContain("AMASI Ads");
    expect(markup).toContain("ads_read");
    expect(markup).toContain("read_insights");
    expect(markup).toContain("قراءة أداء الحملات");
    expect(markup).toContain("تعديل الميزانيات");
    expect(markup).toContain("integration-meta_ads-reconnect");
    const disconnectButton = markup.match(
        /<button[^>]*data-testid="integration-meta_ads-disconnect"[^>]*>/,
    )?.[0];
    expect(disconnectButton).toContain("disabled");
    expect(markup).not.toContain(sentinel);
});
