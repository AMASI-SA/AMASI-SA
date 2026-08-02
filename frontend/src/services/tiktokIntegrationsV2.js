import api from "../lib/api";

const TERMINAL_SYNC_STATUSES = new Set(["complete", "partial", "failed"]);

function waitFor(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function safeSyncResult(value = {}) {
    const sourceOnly = value.source_only === true;
    const protectedWriteReached = [
        value.provider_write_reached,
        value.campaign_write_reached,
        value.accounting_write_reached,
        value.qoyod_write_reached,
    ].some((item) => item === true);
    if (!sourceOnly || protectedWriteReached) {
        throw new Error("tiktok_reporting_safety_contract_failed");
    }
    return {
        run_id: String(value.run_id || "").trim(),
        provider: "tiktok_ads",
        status: String(value.status || "unknown"),
        started_at: value.started_at || null,
        finished_at: value.finished_at || null,
        date_from: value.date_from || null,
        date_to: value.date_to || null,
        accounts_attempted: Number(value.accounts_attempted || 0),
        accounts_complete: Number(value.accounts_complete || 0),
        rows_saved: Number(value.rows_saved || 0),
        errors_count: Number(value.errors_count || 0),
        source_only: true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
        error: value.error && typeof value.error === "object"
            ? {
                code: String(value.error.code || "tiktok_reporting_failed"),
                message: String(value.error.message || "تعذر مزامنة TikTok."),
                retryable: Boolean(value.error.retryable),
            }
            : null,
    };
}

export async function startTikTokConnection() {
    const response = await api.post("/integrations-v2/tiktok/connect/start");
    const authorizationUrl = String(response.data?.authorization_url || "").trim();
    if (!authorizationUrl) throw new Error("tiktok_authorization_url_missing");

    let parsed;
    try {
        parsed = new URL(authorizationUrl);
    } catch {
        throw new Error("tiktok_authorization_url_invalid");
    }
    if (parsed.protocol !== "https:" || parsed.hostname !== "ads.tiktok.com") {
        throw new Error("tiktok_authorization_url_untrusted");
    }
    return {
        authorization_url: authorizationUrl,
        expires_at: response.data?.expires_at || null,
        provider: response.data?.provider || "tiktok_ads",
    };
}

export async function startTikTokReportingSync({ days = 30 } = {}) {
    const parsedDays = Number(days);
    if (!Number.isInteger(parsedDays) || parsedDays < 1 || parsedDays > 31) {
        throw new Error("invalid_tiktok_sync_days");
    }
    const response = await api.post(
        "/integrations-v2/tiktok_ads/sync-async",
        { days: parsedDays },
    );
    const result = safeSyncResult(response.data);
    if (!result.run_id) throw new Error("tiktok_reporting_run_id_missing");
    return result;
}

export async function getTikTokReportingSync(runId) {
    const safeRunId = String(runId || "").trim();
    if (!safeRunId) throw new Error("tiktok_reporting_run_id_missing");
    const response = await api.get(
        `/integrations-v2/tiktok_ads/sync-async/${encodeURIComponent(safeRunId)}`,
    );
    return safeSyncResult(response.data);
}

export async function syncTikTokReporting({
    days = 30,
    attempts = 160,
    intervalMs = 3000,
    wait = waitFor,
} = {}) {
    let current = await startTikTokReportingSync({ days });
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        if (TERMINAL_SYNC_STATUSES.has(current.status)) {
            if (current.status === "failed") {
                const error = new Error(
                    current.error?.message || "تعذر مزامنة TikTok.",
                );
                error.code = current.error?.code || "tiktok_reporting_failed";
                error.result = current;
                throw error;
            }
            return current;
        }
        await wait(intervalMs);
        current = await getTikTokReportingSync(current.run_id);
    }
    const error = new Error("tiktok_reporting_poll_timeout");
    error.code = "tiktok_reporting_poll_timeout";
    throw error;
}

export { safeSyncResult as normalizeTikTokReportingSync };
