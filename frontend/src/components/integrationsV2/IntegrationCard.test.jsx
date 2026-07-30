import { renderToStaticMarkup } from "react-dom/server";
import CapabilityMatrix from "./CapabilityMatrix";
import IntegrationActivityPanel from "./IntegrationActivityPanel";
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
            snapchatScope={{
                selection: {
                    selected_count: 1,
                    accounts: [{
                        account_id: "snap-account-1",
                        display_name: "متجر أماسي",
                        selected: true,
                        currency: "SAR",
                        timezone: "Asia/Riyadh",
                    }],
                },
                summary: {
                    selected_account_count: 1,
                    selected_account_ids: ["snap-account-1"],
                    rows_included: 1,
                    unselected_rows_excluded: 0,
                    spend_sar: 10,
                    accounts: [{
                        account_id: "snap-account-1",
                        currency: "SAR",
                        spend_native: 10,
                        spend_sar: 10,
                    }],
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

test("Snapchat card marks only the selected accounts, shows scoped spend, and downgrades bounded Pixel diagnostics", () => {
    const rawDiagnostic = "Snapchat tracking diagnostics completed with bounded unavailable endpoints";
    const markup = renderToStaticMarkup(
        <IntegrationCard
            integration={{
                provider: "snapchat_ads",
                name: "Snapchat Ads",
                name_ar: "إعلانات سناب شات",
                connection_status: "connected",
                connection_provenance: "api_connection",
                accounts: [
                    {
                        mezan_integration_account_id: "unused-mezan",
                        display_name: "حساب غير مستخدم",
                        external_account_id: "unused-account",
                        currency: "USD",
                        timezone: "America/Los_Angeles",
                    },
                    {
                        mezan_integration_account_id: "usd-mezan",
                        display_name: "متجر أماسي Self Service",
                        external_account_id: "usd-account",
                        currency: "USD",
                        timezone: "America/Los_Angeles",
                    },
                    {
                        mezan_integration_account_id: "sar-mezan",
                        display_name: "متجر أماسي سعودي",
                        external_account_id: "sar-account",
                        currency: "SAR",
                        timezone: "Asia/Riyadh",
                    },
                ],
                permissions: {
                    current: ["snapchat-marketing-api"],
                    missing: [],
                    unknown: false,
                },
                last_sync_at: "2026-07-30T20:06:37+00:00",
                data_delay_minutes: 0,
                health: { score: 100, data_quality: "complete" },
                latest_error: {
                    code: "snapchat_tracking_diagnostics_partial",
                    message: rawDiagnostic,
                },
                ai: { can: ["تحليل الإنفاق"], cannot: ["تعديل الحملات"] },
                actions: {
                    test_connection: { enabled: true },
                    sync_data: { enabled: true },
                    reconnect: { enabled: true },
                    settings: { enabled: true },
                    disconnect: { enabled: false },
                },
            }}
            snapchatScope={{
                selection: {
                    discovered_count: 3,
                    selected_count: 2,
                    selection_required: false,
                    accounts: [
                        {
                            account_id: "usd-account",
                            display_name: "متجر أماسي Self Service",
                            currency: "USD",
                            timezone: "America/Los_Angeles",
                            selected: true,
                        },
                        {
                            account_id: "sar-account",
                            display_name: "متجر أماسي سعودي",
                            currency: "SAR",
                            timezone: "Asia/Riyadh",
                            selected: true,
                        },
                        {
                            account_id: "unused-account",
                            display_name: "حساب غير مستخدم",
                            currency: "USD",
                            timezone: "America/Los_Angeles",
                            selected: false,
                        },
                    ],
                },
                summary: {
                    date_from: "2026-07-30",
                    date_to: "2026-07-30",
                    selected_account_count: 2,
                    selected_account_ids: ["usd-account", "sar-account"],
                    rows_included: 2,
                    unselected_rows_excluded: 7,
                    spend_sar: 384.44,
                    accounts: [
                        {
                            account_id: "usd-account",
                            currency: "USD",
                            spend_native: 100.223069,
                            spend_sar: 375.84,
                        },
                        {
                            account_id: "sar-account",
                            currency: "SAR",
                            spend_native: 8.6,
                            spend_sar: 8.6,
                        },
                    ],
                },
            }}
            onTest={() => {}}
            onSync={() => {}}
            onSettings={() => {}}
        />,
    );

    expect(markup).toContain("2 محدد");
    expect((markup.match(/محدد للمزامنة/g) || [])).toHaveLength(2);
    expect(markup).toContain("384.44 SAR");
    expect(markup).toContain("100.22 USD");
    expect(markup).toContain("375.84 SAR");
    expect(markup).toContain("7 صف لحسابات غير محددة تم استبعاده");
    expect(markup).toContain("1 حساب مكتشف غير داخل في الإجمالي");
    expect(markup).toContain("مزامنة الحملات والمصروفات");
    expect(markup).toContain("مكتملة");
    expect(markup).toContain("تشخيص Pixel");
    expect(markup).toContain("جزئي");
    expect(markup).toContain('data-testid="snapchat-tracking-notice"');
    expect(markup).toContain("لا تعني فشل مزامنة");
    expect(markup).not.toContain(rawDiagnostic);
    expect(markup).not.toContain(">آخر خطأ<");
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
            snapchatScope={{
                selection: { selected_count: 0, accounts: [] },
                summary: { selected_account_count: 0, selected_account_ids: [], accounts: [] },
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

test("activity panel labels completed syncs and keeps Pixel diagnostics amber", () => {
    const rawDiagnostic = "Pixel endpoint returned 400 for c1bb1dae";
    const markup = renderToStaticMarkup(
        <IntegrationActivityPanel
            runs={[
                {
                    run_id: "async-1",
                    provider: "snapchat_ads",
                    run_type: "analytics_refresh_async",
                    status: "complete",
                    finished_at: "2026-07-30T20:06:37+00:00",
                },
                {
                    run_id: "tracking-1",
                    provider: "snapchat_ads",
                    run_type: "tracking_diagnostics",
                    status: "partial",
                    finished_at: "2026-07-30T20:07:00+00:00",
                },
            ]}
            errors={[
                {
                    error_id: "tracking-error",
                    provider: "snapchat_ads",
                    code: "snapchat_tracking_http_400",
                    message: rawDiagnostic,
                    occurred_at: "2026-07-30T20:07:00+00:00",
                },
                {
                    error_id: "real-error",
                    provider: "meta_ads",
                    code: "provider_unavailable",
                    safe_message: "تعذر الاتصال بالمنصة",
                    occurred_at: "2026-07-30T20:08:00+00:00",
                },
            ]}
        />,
    );

    expect(markup).toContain("مهمة مزامنة Snapchat الخلفية");
    expect(markup).toContain("مكتمل");
    expect(markup).toContain("تشخيص Pixel");
    expect(markup).toContain("جزئي");
    expect(markup).toContain('data-testid="tracking-diagnostic-notice"');
    expect(markup).toContain("لا يؤثر ذلك في مزامنة الحملات أو المصروفات");
    expect(markup).not.toContain(rawDiagnostic);
    expect(markup).toContain('data-testid="integration-error-item"');
    expect(markup).toContain("تعذر الاتصال بالمنصة");
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
