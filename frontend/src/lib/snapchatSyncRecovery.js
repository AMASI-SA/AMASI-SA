const TERMINAL_SYNC_STATUSES = new Set(["complete", "partial", "failed"]);
const ACTIVE_SYNC_STATUSES = new Set(["queued", "running"]);
const RECOVERABLE_HTTP_STATUSES = new Set([408, 499, 502, 504, 522, 524]);
const RECOVERABLE_TRANSPORT_CODES = new Set(["ECONNABORTED", "ERR_NETWORK"]);
const RETRYABLE_POLL_HTTP_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504, 522, 524]);

function syncPath(config = {}) {
    const rawUrl = typeof config?.url === "string" ? config.url : "";
    return rawUrl.split("?", 1)[0].replace(/\/+$/, "");
}

function syncStatus(value) {
    return String(value || "").trim().toLowerCase();
}

function codedError(code) {
    const error = new Error(code);
    error.code = code;
    return error;
}

function shouldRetryPollFailure(error) {
    const status = Number(error?.response?.status || 0);
    const transportCode = String(error?.code || "").trim().toUpperCase();
    return status === 0
        || RETRYABLE_POLL_HTTP_STATUSES.has(status)
        || RECOVERABLE_TRANSPORT_CODES.has(transportCode);
}

export function isSnapchatSyncRequest(config = {}) {
    if (config?._mezanSnapchatSyncRequest === true) return true;
    const method = String(config?.method || "").trim().toLowerCase();
    return method === "post"
        && syncPath(config).endsWith("/integrations-v2/snapchat_ads/sync");
}

export function rewriteSnapchatSyncRequest(config = {}) {
    if (!isSnapchatSyncRequest(config)) return config;

    const rawUrl = typeof config?.url === "string" ? config.url : "";
    const queryIndex = rawUrl.indexOf("?");
    const pathname = queryIndex >= 0 ? rawUrl.slice(0, queryIndex) : rawUrl;
    const suffix = queryIndex >= 0 ? rawUrl.slice(queryIndex) : "";
    const normalizedPath = pathname.replace(/\/+$/, "");
    const rewrittenPath = normalizedPath.endsWith(
        "/integrations-v2/snapchat_ads/sync",
    )
        ? `${normalizedPath}-async`
        : normalizedPath;

    return {
        ...config,
        url: `${rewrittenPath}${suffix}`,
        _mezanSnapchatSyncRequest: true,
        _mezanSnapchatAsyncSync: true,
    };
}

export function isSnapchatAsyncSyncResponse(response = {}) {
    return response?.config?._mezanSnapchatAsyncSync === true;
}

export async function pollSnapchatAsyncSyncJob({
    accepted,
    loadJob,
    wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    attempts = 360,
    intervalMs = 2_000,
} = {}) {
    const runId = String(accepted?.run_id || "").trim();
    if (!runId) throw codedError("snapchat_async_sync_run_id_missing");
    if (typeof loadJob !== "function") {
        throw codedError("snapchat_async_sync_loader_missing");
    }

    const acceptedStatus = syncStatus(accepted?.status);
    if (TERMINAL_SYNC_STATUSES.has(acceptedStatus)) return accepted;
    if (!ACTIVE_SYNC_STATUSES.has(acceptedStatus)) {
        throw codedError("snapchat_async_sync_status_invalid");
    }

    const boundedAttempts = Math.min(
        Math.max(Number(attempts) || 1, 1),
        1_000,
    );
    const boundedInterval = Math.min(
        Math.max(Number(intervalMs) || 0, 0),
        30_000,
    );

    for (let attempt = 0; attempt < boundedAttempts; attempt += 1) {
        try {
            const job = await loadJob(runId);
            const status = syncStatus(job?.status);
            if (TERMINAL_SYNC_STATUSES.has(status)) return job;
            if (!ACTIVE_SYNC_STATUSES.has(status)) {
                throw codedError("snapchat_async_sync_status_invalid");
            }
        } catch (error) {
            if (error?.code === "snapchat_async_sync_status_invalid") throw error;
            if (!shouldRetryPollFailure(error)) throw error;
        }

        if (attempt + 1 < boundedAttempts) {
            await wait(boundedInterval);
        }
    }

    throw codedError("snapchat_async_sync_timeout");
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
