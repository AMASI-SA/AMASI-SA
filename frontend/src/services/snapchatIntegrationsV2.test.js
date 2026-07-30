import api from "../lib/api";
import {
    findTerminalSnapchatSyncRun,
    pollSnapchatAsyncSyncJob,
    recoverSnapchatSyncAfterTransportFailure,
    rewriteSnapchatSyncRequest,
    shouldRecoverSnapchatSyncFailure,
} from "../lib/snapchatSyncRecovery";
import { startSnapchatConnection } from "./snapchatIntegrationsV2";

jest.mock("../lib/api", () => ({
    post: jest.fn(),
}));

const syncConfig = {
    method: "post",
    url: "/integrations-v2/snapchat_ads/sync",
    _mezanSyncStartedAt: Date.parse("2026-07-30T11:54:30Z"),
};

describe("Snapchat Integrations V2 client", () => {
    beforeEach(() => {
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
