import { renderToStaticMarkup } from "react-dom/server";
import api from "../lib/api";
import IntegrationActivityPanel from "../components/integrationsV2/IntegrationActivityPanel";
import IntegrationCard from "../components/integrationsV2/IntegrationCard";
import {
    findTerminalSnapchatSyncRun,
    pollSnapchatAsyncSyncJob,
    recoverSnapchatSyncAfterTransportFailure,
    rewriteSnapchatSyncRequest,
    shouldRecoverSnapchatSyncFailure,
} from "../lib/snapchatSyncRecovery";
import {
    getSnapchatAccountSelection,
    getSnapchatSelectedPerformanceSummary,
    startSnapchatConnection,
} from "./snapchatIntegrationsV2";

jest.mock("../lib/api", () => ({
    get: jest.fn(),
    post: jest.fn(),
}));

const syncConfig = {
    method: "post",
    url: "/integrations-v2/snapchat_ads/sync",
    _mezanSyncStartedAt: Date.parse("2026-07-30T11:54:30Z"),
};

describe("Snapchat Integrations V2 client", () => {
    beforeEach(() => {
        api.get.mockReset();
        api.post.mockReset();
    });

    test("accepts only the official Snapchat authorization endpoint", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://accounts.snapchat.com/login/oauth2/authorize?client_id=1&state=signed",
                provider: "snapchat_ads",
                scopes: ["snapchat-marketing-api", "snapchat-offline-conversions-api"],
            },
        });
        const result = await startSnapchatConnection();
        expect(result.provider).toBe("snapchat_ads");
        expect(result.scopes).toContain("snapchat-marketing-api");
        expect(result.authorization_url).toContain("https://accounts.snapchat.com/");
    });

    test("rejects an untrusted authorization host", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://evil.example/login/oauth2/authorize?state=stolen",
            },
        });
        await expect(startSnapchatConnection()).rejects.toThrow(
            "snapchat_authorization_url_untrusted",
        );
    });

    test("rejects a trusted host with the wrong path", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://accounts.snapchat.com/phishing?state=stolen",
            },
        });
        await expect(startSnapchatConnection()).rejects.toThrow(
            "snapchat_authorization_url_untrusted",
        );
    });

    test("reads the explicit owner-selected Snapchat account scope", async () => {
        api.get.mockResolvedValueOnce({
            data: {
                discovered_count: 9,
                selected_count: 2,
                selection_required: false,
                accounts: [
                    {
                        account_id: "usd-account",
                        display_name: "متجر أماسي Self Service",
                        currency: "USD",
                        timezone: "America/Los_Angeles",
                        selected: true,
                        selection_status: "selected",
                    },
                    {
                        account_id: "unused-account",
                        display_name: "حساب غير مستخدم",
                        selected: false,
                        selection_status: "discovered",
                    },
                ],
            },
        });

        const result = await getSnapchatAccountSelection();

        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/accounts-selection",
        );
        expect(result).toMatchObject({
            discovered_count: 9,
            selected_count: 2,
            selection_required: false,
        });
        expect(result.accounts[0]).toMatchObject({
            account_id: "usd-account",
            selected: true,
            currency: "USD",
        });
    });

    test("reads only the selected-account performance summary", async () => {
        api.get.mockResolvedValueOnce({
            data: {
                provider: "snapchat_ads",
                date_from: "2026-07-30",
                date_to: "2026-07-30",
                selected_account_ids: ["usd-account", "sar-account"],
                selected_account_count: 2,
                rows_included: 2,
                unselected_rows_excluded: 7,
                spend_sar: 384.44,
                purchase_value_sar: 1920.6,
                accounts: [
                    {
                        account_id: "usd-account",
                        display_name: "متجر أماسي Self Service",
                        currency: "USD",
                        spend_native: 100.223069,
                        spend_sar: 375.84,
                    },
                    {
                        account_id: "sar-account",
                        display_name: "متجر أماسي سعودي",
                        currency: "SAR",
                        spend_native: 8.6,
                        spend_sar: 8.6,
                    },
                ],
                source_only: true,
                accounting_write_reached: false,
                qoyod_write_reached: false,
            },
        });

        const result = await getSnapchatSelectedPerformanceSummary({
            fromDate: "2026-07-30",
            toDate: "2026-07-30",
        });

        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/snapchat_ads/performance-summary",
            { params: { from_date: "2026-07-30", to_date: "2026-07-30" } },
        );
        expect(result).toMatchObject({
            selected_account_count: 2,
            unselected_rows_excluded: 7,
            spend_sar: 384.44,
            source_only: true,
            accounting_write_reached: false,
            qoyod_write_reached: false,
        });
        expect(result.accounts).toHaveLength(2);
    });

    test("fails closed when the selected-account summary reaches protected writes", async () => {
        api.get.mockResolvedValueOnce({
            data: {
                source_only: false,
                accounting_write_reached: true,
                qoyod_write_reached: false,
            },
        });

        await expect(getSnapchatSelectedPerformanceSummary()).rejects.toThrow(
            "snapchat_summary_safety_contract_failed",
        );
    });

    test("renders the selected accounts first with scoped spend and an amber Pixel notice", () => {
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
        expect(markup).toContain("القيم غير المعروفة فارغة");
        expect(markup).not.toContain(rawDiagnostic);
        expect(markup).not.toContain(">آخر خطأ<");
    });

    test("renders completed background syncs green and Pixel diagnostics amber", () => {
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

    test("rewrites only the native sync request to the asynchronous endpoint", () => {
        const rewritten = rewriteSnapchatSyncRequest({
            ...syncConfig,
            url: "/integrations-v2/snapchat_ads/sync?source=control-center",
            data: { days: 30 },
        });

        expect(rewritten.url).toBe(
            "/integrations-v2/snapchat_ads/sync-async?source=control-center",
        );
        expect(rewritten.data).toEqual({ days: 30 });
        expect(rewritten._mezanSnapchatSyncRequest).toBe(true);
        expect(rewritten._mezanSnapchatAsyncSync).toBe(true);

        const unrelated = { method: "post", url: "/orders/sync" };
        expect(rewriteSnapchatSyncRequest(unrelated)).toBe(unrelated);
    });

    test("polls queued and running jobs until the asynchronous sync completes", async () => {
        const loadJob = jest
            .fn()
            .mockResolvedValueOnce({ run_id: "job-1", status: "queued" })
            .mockResolvedValueOnce({ run_id: "job-1", status: "running" })
            .mockResolvedValueOnce({
                run_id: "job-1",
                provider: "snapchat_ads",
                status: "complete",
                accounts_attempted: 2,
                accounts_complete: 2,
                rows_saved: 15604,
                errors_count: 0,
                source_only: true,
                accounting_write_reached: false,
                qoyod_write_reached: false,
            });
        const wait = jest.fn().mockResolvedValue(undefined);

        const result = await pollSnapchatAsyncSyncJob({
            accepted: { run_id: "job-1", status: "queued" },
            loadJob,
            wait,
            attempts: 3,
            intervalMs: 1,
        });

        expect(loadJob).toHaveBeenCalledTimes(3);
        expect(loadJob).toHaveBeenNthCalledWith(1, "job-1");
        expect(wait).toHaveBeenCalledTimes(2);
        expect(result).toMatchObject({
            status: "complete",
            accounts_complete: 2,
            rows_saved: 15604,
            source_only: true,
            accounting_write_reached: false,
            qoyod_write_reached: false,
        });
    });

    test("does not hide a non-retryable status polling failure", async () => {
        const unauthorized = Object.assign(new Error("unauthorized"), {
            response: { status: 401 },
        });
        await expect(pollSnapchatAsyncSyncJob({
            accepted: { run_id: "job-2", status: "queued" },
            loadJob: jest.fn().mockRejectedValue(unauthorized),
            wait: jest.fn().mockResolvedValue(undefined),
            attempts: 3,
            intervalMs: 1,
        })).rejects.toBe(unauthorized);
    });

    test("recovers only transport failures for the native sync endpoint", () => {
        expect(shouldRecoverSnapchatSyncFailure({
            config: syncConfig,
            response: { status: 504, data: "Gateway Timeout" },
        })).toBe(true);
        expect(shouldRecoverSnapchatSyncFailure({
            config: syncConfig,
            response: {
                status: 409,
                data: { detail: { code: "snapchat_needs_reauth" } },
            },
        })).toBe(false);
    });

    test("selects the terminal sync started by the detached request", () => {
        const run = findTerminalSnapchatSyncRun([
            {
                run_id: "old",
                provider: "snapchat_ads",
                run_type: "analytics_refresh",
                status: "complete",
                started_at: "2026-07-30T11:00:00Z",
            },
            {
                run_id: "running",
                provider: "snapchat_ads",
                run_type: "analytics_refresh",
                status: "running",
                started_at: "2026-07-30T11:54:32Z",
            },
            {
                run_id: "complete",
                provider: "snapchat_ads",
                run_type: "analytics_refresh",
                status: "complete",
                started_at: "2026-07-30T11:54:33Z",
            },
        ], Date.parse("2026-07-30T11:54:30Z"));
        expect(run?.run_id).toBe("complete");
    });

    test("recovers the completed audited result after a gateway timeout", async () => {
        const loadRuns = jest
            .fn()
            .mockResolvedValueOnce([{
                run_id: "run-1",
                provider: "snapchat_ads",
                run_type: "analytics_refresh",
                status: "running",
                started_at: "2026-07-30T11:54:33Z",
            }])
            .mockResolvedValueOnce([{
                run_id: "run-1",
                provider: "snapchat_ads",
                run_type: "analytics_refresh",
                status: "complete",
                started_at: "2026-07-30T11:54:33Z",
                summary: {
                    accounts_attempted: 9,
                    accounts_complete: 9,
                    rows_saved: 11818,
                    errors_count: 0,
                    source_only: true,
                    accounting_write_reached: false,
                    qoyod_write_reached: false,
                },
            }]);
        const wait = jest.fn().mockResolvedValue(undefined);

        const recovered = await recoverSnapchatSyncAfterTransportFailure({
            error: {
                config: syncConfig,
                response: { status: 504, data: "Gateway Timeout" },
            },
            loadRuns,
            wait,
            attempts: 3,
            intervalMs: 1,
        });

        expect(loadRuns).toHaveBeenCalledTimes(2);
        expect(wait).toHaveBeenCalledTimes(1);
        expect(recovered?.payload).toMatchObject({
            run_id: "run-1",
            status: "complete",
            accounts_complete: 9,
            rows_saved: 11818,
            source_only: true,
            accounting_write_reached: false,
            qoyod_write_reached: false,
        });
    });
});
