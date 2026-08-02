import { renderToStaticMarkup } from "react-dom/server";
import IntegrationCard from "./IntegrationCard";

const GOOGLE_INTEGRATION = {
    provider: "google_ads",
    name: "Google Ads",
    name_ar: "إعلانات Google",
    connection_status: "connected",
    connection_provenance: "api_connection",
    accounts: [
        {
            mezan_integration_account_id: "google-1",
            display_name: "Google Ads 2583431942",
            external_account_id: "2583431942",
            currency: "SAR",
            timezone: "Asia/Riyadh",
        },
        {
            mezan_integration_account_id: "google-2",
            display_name: "Google Ads 5025346701",
            external_account_id: "5025346701",
            currency: "SAR",
            timezone: "Asia/Riyadh",
        },
    ],
    permissions: {
        current: ["adwords"],
        missing: [],
        unknown: false,
    },
    last_sync_at: "2026-08-02T18:30:00+00:00",
    health: { score: 100, data_quality: "good" },
    latest_error: null,
    ai: {
        can: ["قراءة أداء الحملات"],
        cannot: ["إنشاء الحملات"],
    },
    actions: {
        test_connection: { enabled: true },
        reconnect: { enabled: false },
        settings: { enabled: false },
    },
};

beforeEach(() => {
    window.history.pushState({}, "", "/integrations-v2?workspace=accounts");
});

afterEach(() => {
    window.history.pushState({}, "", "/");
});

test("advertising accounts workspace renders a compact account-first card", () => {
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={GOOGLE_INTEGRATION}
            onTest={() => {}}
            onSettings={() => {}}
        />,
    );

    expect(markup).toContain('data-layout="advertising-account-compact"');
    expect(markup).toContain("إعلانات Google");
    expect(markup).toContain("الحسابات الظاهرة");
    expect(markup).toContain("Google Ads 2583431942");
    expect(markup).toContain("2583431942");
    expect(markup).toContain("SAR");
    expect(markup).toContain("Asia/Riyadh");
    expect(markup).toContain("إدارة الحسابات والتقارير");
    expect(markup).toContain('data-testid="integration-google_ads-test"');

    expect(markup).not.toContain("ما يستطيع الذكاء الاصطناعي فعله");
    expect(markup).not.toContain("ما لا يستطيع فعله الآن");
    expect(markup).not.toContain("الصلاحيات الحالية");
    expect(markup).not.toContain("قراءة أداء الحملات");
    expect(markup).not.toContain("إنشاء الحملات");
});

test("outside the accounts workspace the full applications card remains unchanged", () => {
    window.history.pushState({}, "", "/integrations-v2");
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={GOOGLE_INTEGRATION}
            onTest={() => {}}
            onSettings={() => {}}
        />,
    );

    expect(markup).not.toContain('data-layout="advertising-account-compact"');
    expect(markup).toContain("ما يستطيع الذكاء الاصطناعي فعله");
    expect(markup).toContain("قراءة أداء الحملات");
    expect(markup).toContain("الصلاحيات الحالية");
});
