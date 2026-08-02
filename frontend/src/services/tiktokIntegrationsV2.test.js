import api from "../lib/api";
import {
    getTikTokReportingSync,
    normalizeTikTokReportingSync,
    startTikTokConnection,
    startTikTokReportingSync,
    syncTikTokReporting,
} from "./tiktokIntegrationsV2";

jest.mock("../lib/api", () => ({
    get: jest.fn(),
    post: jest.fn(),
}));

describe("TikTok Integrations V2 client", () => {
    beforeEach(() => {
        api.get.mockReset();
        api.post.mockReset();
    });

    test("accepts only the official TikTok Ads authorization host", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://ads.tiktok.com/marketing_api/auth?app_id=1&state=signed",
                provider: "tiktok_ads",
            },
        });
        const result = await startTikTokConnection();
        expect(result.provider).toBe("tiktok_ads");
        expect(result.authorization_url).toContain("https://ads.tiktok.com/");
    });

    test("rejects an untrusted authorization host", async () => {
        api.post.mockResolvedValue({
            data: {
                authorization_url: "https://evil.example/marketing_api/auth?state=stolen",
            },
        });
        await expect(startTikTokConnection()).rejects.toThrow(
            "tiktok_authorization_url_untrusted",
        );
    });

    test("starts a bounded timeout-safe reporting job", async () => {
        api.post.mockResolvedValue({
            data: {
                run_id: "run-1",
                provider: "tiktok_ads",
                status: "queued",
                source_only: true,
                provider_write_reached: false,
                campaign_write_reached: false,
                accounting_write_reached: false,
                qoyod_write_reached: false,
            },
        });
        const result = await startTikTokReportingSync({ days: 30 });
        expect(api.post).toHaveBeenCalledWith(
            "/integrations-v2/tiktok_ads/sync-async",
            { days: 30 },
        );
        expect(result).toMatchObject({
            run_id: "run-1",
            status: "queued",
            source_only: true,
        });
    });

    test("polls queued and running jobs until completion", async () => {
        api.post.mockResolvedValue({
            data: {
                run_id: "run-2",
                status: "queued",
                source_only: true,
                provider_write_reached: false,
                campaign_write_reached: false,
                accounting_write_reached: false,
                qoyod_write_reached: false,
            },
        });
        api.get
            .mockResolvedValueOnce({
                data: {
                    run_id: "run-2",
                    status: "running",
                    source_only: true,
                    provider_write_reached: false,
                    campaign_write_reached: false,
                    accounting_write_reached: false,
                    qoyod_write_reached: false,
                },
            })
            .mockResolvedValueOnce({
                data: {
                    run_id: "run-2",
                    status: "complete",
                    accounts_attempted: 1,
                    accounts_complete: 1,
                    rows_saved: 30,
                    errors_count: 0,
                    source_only: true,
                    provider_write_reached: false,
                    campaign_write_reached: false,
                    accounting_write_reached: false,
                    qoyod_write_reached: false,
                },
            });
        const wait = jest.fn().mockResolvedValue(undefined);
        const result = await syncTikTokReporting({
            days: 30,
            attempts: 3,
            intervalMs: 1,
            wait,
        });
        expect(result).toMatchObject({
            status: "complete",
            accounts_complete: 1,
            rows_saved: 30,
        });
        expect(api.get).toHaveBeenCalledTimes(2);
        expect(wait).toHaveBeenCalledTimes(2);
    });

    test("loads a job only by its encoded run id", async () => {
        api.get.mockResolvedValue({
            data: {
                run_id: "run/3",
                status: "partial",
                source_only: true,
                provider_write_reached: false,
                campaign_write_reached: false,
                accounting_write_reached: false,
                qoyod_write_reached: false,
            },
        });
        const result = await getTikTokReportingSync("run/3");
        expect(api.get).toHaveBeenCalledWith(
            "/integrations-v2/tiktok_ads/sync-async/run%2F3",
        );
        expect(result.status).toBe("partial");
    });

    test("fails closed if a protected write is reported", () => {
        expect(() => normalizeTikTokReportingSync({
            run_id: "unsafe-run",
            status: "complete",
            source_only: false,
            accounting_write_reached: true,
        })).toThrow("tiktok_reporting_safety_contract_failed");
    });
});
