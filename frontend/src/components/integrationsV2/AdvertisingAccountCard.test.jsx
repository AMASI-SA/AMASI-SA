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

test("advertising accounts workspace renders a compact account-first card", () => {
    const markup = renderToStaticMarkup(
        <IntegrationCard
            compactAdvertisingAccounts
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

test("the full applications card remains unchanged when compact mode is not selected", () => {
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

test("Snapchat displays unknown grants separately from scopes for the next link", () => {
    const integration = { ...GOOGLE_INTEGRATION, provider: "snapchat_ads",
        permissions: { current: [], missing: [], unknown: true } };
    const compact = renderToStaticMarkup(<IntegrationCard compactAdvertisingAccounts integration={integration} />);
    expect(compact).toContain("الصلاحيات الممنوحة غير مؤكدة");
    expect(compact).toContain("الصلاحيات المطلوبة عند الربط لا تثبت موافقة سناب عليها");
    expect(compact).not.toContain("صلاحيات حالية: 0");
    const full = renderToStaticMarkup(<IntegrationCard integration={integration} />);
    expect(full).toContain("لم نتمكن من التحقق من الصلاحيات");
    expect(full).toContain("الصلاحيات المطلوبة عند الربط لا تثبت موافقة سناب عليها");
});
