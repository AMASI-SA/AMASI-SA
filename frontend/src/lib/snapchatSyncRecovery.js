const TERMINAL_SYNC_STATUSES = new Set(["complete", "partial", "failed"]);
const RECOVERABLE_HTTP_STATUSES = new Set([408, 499, 502, 504, 522, 524]);
const RECOVERABLE_TRANSPORT_CODES = new Set(["ECONNABORTED", "ERR_NETWORK"]);

export function isSnapchatSyncRequest(config = {}) {
    const method = String(config?.method || "").trim().toLowerCase();
    const rawUrl = typeof config?.url === "string" ? config.url : "";
    const pathname = rawUrl.split("?", 1)[0].replace(/\/+$/, "");
    return method === "post"
        && pathname.endsWith("/integrations-v2/snapchat_ads/sync");
}

export function shouldRecoverSnapchatSyncFailure(error) {
    if (!isSnapchatSyncRequest(error?.config)) return false;

    // A structured Backend error is authoritative and must be shown directly.
    const detailCode = error?.response?.data?.detail?.code;
    if (typeof detailCode === "string" && detailCode.trim()) return false;

    const status = Number(error?.response?.status || 0);
    const transportCode = String(error?.code || "").trim().toUpperCase();
    return status === 0
        || RECOVERABLE_HTTP_STATUSES.has(status)
        || RECOVERABLE_TRANSPORT_CODES.has(transportCode);
}

export function findTerminalSnapchatSyncRun(runs, startedAfterMs) {
    const threshold = Number(startedAfterMs || 0) - 15_000;
    return (Array.isArray(runs) ? runs : [])
        .filter((run) => {
            if (run?.provider !== "snapchat_ads") return false;
            if (run?.run_type !== "analytics_refresh") return false;
            if (!TERMINAL_SYNC_STATUSES.has(run?.status)) return false;
            const startedAt = Date.parse(run?.started_at || "");
            return Number.isFinite(startedAt) && startedAt >= threshold;
        })
        .sort((left, right) => (
            Date.parse(right?.started_at || "") - Date.parse(left?.started_at || "")
        ))[0] || null;
}

function recoveredPayload(run) {
    const summary = run?.summary && typeof run.summary === "object"
        ? run.summary
        : {};
    return {
        ...summary,
        run_id: run?.run_id || summary.run_id || null,
        provider: "snapchat_ads",
        status: run?.status || summary.status || "failed",
    };
}

export async function recoverSnapchatSyncAfterTransportFailure({
    error,
    loadRuns,
    wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    attempts = 36,
    intervalMs = 5_000,
} = {}) {
    if (!shouldRecoverSnapchatSyncFailure(error)) return null;
    if (typeof loadRuns !== "function") return null;

    const startedAfterMs = Number(
        error?.config?._mezanSyncStartedAt || Date.now(),
    );
    const boundedAttempts = Math.min(Math.max(Number(attempts) || 1, 1), 60);

    for (let attempt = 0; attempt < boundedAttempts; attempt += 1) {
        try {
            const runs = await loadRuns();
            const run = findTerminalSnapchatSyncRun(runs, startedAfterMs);
            if (run) {
                return { run, payload: recoveredPayload(run) };
            }
        } catch {
            // A transient activity-read failure must not replace the original error.
        }
        if (attempt + 1 < boundedAttempts) await wait(intervalMs);
    }
    return null;
}
