import api from "../lib/api";
import {
    findTerminalSnapchatSyncRun,
    recoverSnapchatSyncAfterTransportFailure,
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
