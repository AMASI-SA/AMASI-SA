import {
    findTerminalSnapchatSyncRun,
    recoverSnapchatSyncAfterTransportFailure,
    shouldRecoverSnapchatSyncFailure,
} from "./snapchatSyncRecovery";

const syncConfig = {
    method: "post",
    url: "/integrations-v2/snapchat_ads/sync",
    _mezanSyncStartedAt: Date.parse("2026-07-30T11:54:30Z"),
};

test("only transport or gateway failures recover through the Snapchat sync log", () => {
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

    expect(shouldRecoverSnapchatSyncFailure({
        config: { method: "get", url: "/integrations-v2/overview" },
        code: "ERR_NETWORK",
    })).toBe(false);
});

test("terminal run selection ignores old, unrelated and still-running rows", () => {
    const run = findTerminalSnapchatSyncRun([
        {
            run_id: "old",
            provider: "snapchat_ads",
            run_type: "analytics_refresh",
            status: "complete",
            started_at: "2026-07-30T11:00:00Z",
        },
        {
            run_id: "local-test",
            provider: "snapchat_ads",
            run_type: "local_connection_test",
            status: "passed",
            started_at: "2026-07-30T11:54:31Z",
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

test("a detached request recovers the completed audited result", async () => {
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
        provider: "snapchat_ads",
        status: "complete",
        accounts_complete: 9,
        rows_saved: 11818,
        source_only: true,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    });
});
