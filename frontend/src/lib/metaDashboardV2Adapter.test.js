import {
    isMetaDashboardAsyncSyncResponse,
    pollMetaDashboardSyncJob,
    rewriteMetaDashboardRequest,
    toLegacyMetaSyncPayload,
} from "./metaDashboardV2Adapter";


test("rewrites the legacy Meta dashboard summary to the V2 read-only route", () => {
    const result = rewriteMetaDashboardRequest({
        method: "get",
        url: "/dashboard/meta-summary",
    });
    expect(result.url).toBe("/integrations-v2/meta_ads/dashboard-summary");
    expect(result._mezanMetaDashboardSummary).toBe(true);
});


test("rewrites legacy Meta sync to the async V2 reporting route", () => {
    const result = rewriteMetaDashboardRequest({
        method: "post",
        url: "/meta/sync",
        data: { days: 1 },
    });
    expect(result.url).toBe("/integrations-v2/meta_ads/sync-async");
    expect(result.data).toEqual({ days: 1 });
    expect(result._mezanMetaDashboardSync).toBe(true);
});


test("polls a dashboard Meta job and returns the legacy-safe payload", async () => {
    const loadJob = jest.fn(async () => ({
        run_id: "run-1",
        status: "complete",
        rows_saved: 1,
        accounts_complete: 1,
        accounts_attempted: 1,
        errors_count: 0,
    }));
    const result = await pollMetaDashboardSyncJob({
        accepted: { run_id: "run-1", status: "queued" },
        loadJob,
        pollIntervalMs: 0,
        timeoutMs: 1000,
    });
    expect(result.status).toBe("complete");
    expect(loadJob).toHaveBeenCalledWith("run-1");

    const payload = toLegacyMetaSyncPayload(result);
    expect(payload.upserted).toBe(1);
    expect(payload.source_mode).toBe("meta_marketing_reporting_v2");
    expect(payload.source_only).toBe(true);
    expect(payload.provider_write_reached).toBe(false);
    expect(payload.accounting_write_reached).toBe(false);
    expect(payload.qoyod_write_reached).toBe(false);
});


test("recognizes only legacy-dashboard async responses", () => {
    expect(isMetaDashboardAsyncSyncResponse({
        config: { _mezanMetaDashboardSync: true },
        data: { run_id: "run-1", status: "queued" },
    })).toBe(true);
    expect(isMetaDashboardAsyncSyncResponse({
        config: {},
        data: { run_id: "run-1", status: "queued" },
    })).toBe(false);
});
