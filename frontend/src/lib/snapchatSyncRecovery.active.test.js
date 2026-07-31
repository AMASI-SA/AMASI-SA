import {
    recoverSnapchatSyncAfterTransportFailure,
    shouldRecoverSnapchatSyncFailure,
} from "./snapchatSyncRecovery";

function activeConflictError() {
    return {
        config: {
            method: "post",
            url: "/integrations-v2/snapchat_ads/sync-async",
            _mezanSnapchatSyncRequest: true,
            _mezanSyncStartedAt: Date.parse("2026-07-31T18:00:00Z"),
        },
        response: {
            status: 409,
            data: {
                detail: {
                    code: "snapchat_analytics_sync_in_progress",
                    message: "A Snapchat native data sync is already running.",
                    run_id: "active-run-1",
                },
            },
        },
    };
}

test("treats an already-running Snapchat sync as joinable", () => {
    expect(shouldRecoverSnapchatSyncFailure(activeConflictError())).toBe(true);
});

test("waits for the active run and returns its terminal payload", async () => {
    let reads = 0;
    const recovered = await recoverSnapchatSyncAfterTransportFailure({
        error: activeConflictError(),
        loadRuns: async () => {
            reads += 1;
            if (reads === 1) {
                return [{
                    run_id: "active-run-1",
                    provider: "snapchat_ads",
                    run_type: "analytics_refresh",
                    status: "running",
                    started_at: "2026-07-31T18:00:01Z",
                    summary: {},
                }];
            }
            return [{
                run_id: "active-run-1",
                provider: "snapchat_ads",
                run_type: "analytics_refresh",
                status: "complete",
                started_at: "2026-07-31T18:00:01Z",
                summary: {
                    accounts_attempted: 2,
                    accounts_complete: 2,
                    rows_saved: 48,
                    errors_count: 0,
                },
            }];
        },
        wait: async () => {},
        attempts: 2,
        intervalMs: 0,
    });

    expect(reads).toBe(2);
    expect(recovered.payload).toMatchObject({
        run_id: "active-run-1",
        provider: "snapchat_ads",
        status: "complete",
        accounts_attempted: 2,
        accounts_complete: 2,
        rows_saved: 48,
    });
});
