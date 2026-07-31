const TERMINAL_STATUSES = new Set(["complete", "partial", "failed"]);

function normalizedPath(config) {
    return String(config?.url || "").split("?", 1)[0];
}

function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

export function rewriteMetaDashboardRequest(config) {
    const method = String(config?.method || "get").toLowerCase();
    const path = normalizedPath(config);

    if (method === "get" && path === "/dashboard/meta-summary") {
        return {
            ...config,
            url: "/integrations-v2/meta_ads/dashboard-summary",
            _mezanMetaDashboardSummary: true,
        };
    }

    if (method === "post" && path === "/meta/sync") {
        const requestedDays = Number(config?.data?.days ?? 1);
        const days = Number.isInteger(requestedDays)
            ? Math.max(1, Math.min(31, requestedDays))
            : 1;
        return {
            ...config,
            url: "/integrations-v2/meta_ads/sync-async",
            data: { days },
            _mezanMetaDashboardSync: true,
        };
    }

    return config;
}

export function isMetaDashboardAsyncSyncResponse(response) {
    return response?.config?._mezanMetaDashboardSync === true
        && Boolean(response?.data?.run_id)
        && !TERMINAL_STATUSES.has(String(response?.data?.status || ""));
}

export async function pollMetaDashboardSyncJob({
    accepted,
    loadJob,
    pollIntervalMs = 1200,
    timeoutMs = 20 * 60 * 1000,
}) {
    let result = accepted || {};
    if (!result.run_id) throw new Error("meta_dashboard_sync_run_id_missing");
    if (TERMINAL_STATUSES.has(String(result.status || ""))) return result;

    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        await wait(pollIntervalMs);
        result = await loadJob(result.run_id);
        if (TERMINAL_STATUSES.has(String(result?.status || ""))) return result;
    }
    throw new Error("meta_dashboard_sync_poll_timeout");
}

export function toLegacyMetaSyncPayload(result = {}) {
    return {
        status: result.status || "unknown",
        upserted: Number(result.rows_saved || 0),
        rows_saved: Number(result.rows_saved || 0),
        accounts_complete: Number(result.accounts_complete || 0),
        accounts_attempted: Number(result.accounts_attempted || 0),
        errors_count: Number(result.errors_count || 0),
        source_mode: "meta_marketing_reporting_v2",
        source_only: true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
        error: result.error || null,
    };
}
