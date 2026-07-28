import { renderToStaticMarkup } from "react-dom/server";
import CapabilityMatrix from "./CapabilityMatrix";
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
                connection_provenance: "api_connection",
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
    expect(markup).toContain("ربط API مباشر");
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
    expect(markup).not.toContain("legacy_connection");
});

test("data feed is never rendered as an API connection or a confirmed permission gap", () => {
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={{
                provider: "tiktok_ads",
                name: "TikTok Ads",
                name_ar: "إعلانات تيك توك",
                connection_status: "data_available",
                connection_provenance: "data_feed",
                source_mode: "data_feed",
                accounts: [],
                permissions: { current: [], missing: [], unknown: false },
                health: { score: 22, data_quality: "stale" },
                ai: { can: ["قراءة البيانات المحلية"], cannot: ["إدارة الحملات"] },
                actions: {
                    test_connection: { enabled: true },
                    reconnect: { enabled: false, reason: "لا يوجد موصل API" },
                    settings: { enabled: false },
                    disconnect: { enabled: false },
                },
            }}
            onTest={() => {}}
            onSettings={() => {}}
        />,
    );

    expect(markup).toContain("تغذية بيانات فقط");
    expect(markup).toContain("تصل بيانات دون حساب API مرتبط");
    expect(markup).toContain("لا تُحسب قبل وجود ربط API");
    expect(markup).toContain("فحص محلي");
    expect(markup).not.toContain("ربط API مباشر");
    expect(markup).not.toContain(">متصل<");
    expect(markup).not.toContain("data_feed");
});

test("Snapchat card owns the V2 analytics sync action without a legacy settings link", () => {
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={{
                provider: "snapchat_ads",
                name: "Snapchat Ads",
                name_ar: "إعلانات سناب شات",
                connection_status: "connected",
                connection_provenance: "legacy_integration",
                source_mode: "legacy_connection",
                accounts: [{
                    mezan_integration_account_id: "snap-1",
                    display_name: "متجر أماسي",
                    external_account_id: "snap-account-1",
                    currency: "SAR",
                    timezone: "Asia/Riyadh",
                }],
                permissions: { current: [], missing: [], unknown: true },
                health: { score: 75, data_quality: "degraded" },
                ai: { can: ["تحليل الأداء"], cannot: ["تعديل الحملات"] },
                actions: {
                    test_connection: { enabled: true },
                    sync_data: { enabled: true },
                    reconnect: { enabled: false, href: null },
                    settings: { enabled: false, href: null },
                    disconnect: { enabled: false },
                },
            }}
            onTest={() => {}}
            onSync={() => {}}
            onSettings={() => {}}
        />,
    );

    expect(markup).toContain('data-testid="integration-snapchat_ads-sync"');
    expect(markup).toContain("مزامنة 30 يوم");
    expect(markup).not.toContain("/snapchat-accounts");
});

test("Snapchat sync button is disabled while a V2 sync is running", () => {
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={{
                provider: "snapchat_ads",
                name: "Snapchat Ads",
                name_ar: "إعلانات سناب شات",
                connection_status: "connected",
                connection_provenance: "legacy_integration",
                accounts: [],
                permissions: { current: [], missing: [], unknown: true },
                health: { score: 60, data_quality: "unknown" },
                ai: { can: [], cannot: [] },
                actions: { sync_data: { enabled: true } },
            }}
            syncing
            onTest={() => {}}
            onSync={() => {}}
            onSettings={() => {}}
        />,
    );
    const syncButton = markup.match(
        /<button[^>]*data-testid="integration-snapchat_ads-sync"[^>]*>/,
    )?.[0];
    expect(syncButton).toContain("disabled");
    expect(markup).toContain("جاري المزامنة…");
});

test("capability matrix distinguishes local reads from unavailable mutations", () => {
    const markup = renderToStaticMarkup(
        <CapabilityMatrix
            providers={[{
                provider: "tiktok_ads",
                name: "TikTok Ads",
                name_ar: "إعلانات تيك توك",
                connection_provenance: "data_feed",
                capabilities: {
                    "insights.read": {
                        state: "available",
                        reason: "local rows",
                    },
                    "campaigns.create": {
                        state: "not_connected",
                        reason: "management connection required",
                    },
                },
            }]}
        />,
    );

    expect(markup).toContain("تغذية بيانات فقط");
    expect(markup).toContain("قراءة محلية متاحة");
    expect(markup).toContain("غير متصل");
    expect(markup).not.toContain("يحتاج اعتماد");
});
