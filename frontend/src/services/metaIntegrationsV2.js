import api from "../lib/api";

const META_OAUTH_PATH = /^\/v\d{1,2}\.\d\/dialog\/oauth$/;
const TERMINAL_REPORTING_STATUSES = new Set(["complete", "partial", "failed"]);

function assertReadOnlyEnvelope(payload, code) {
    if (
        payload?.source_only !== true
        || payload?.provider_write_reached === true
        || payload?.campaign_write_reached === true
        || payload?.accounting_write_reached === true
        || payload?.qoyod_write_reached === true
    ) {
        throw new Error(code);
    }
}

function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function startMetaConnection() {
    const response = await api.post("/integrations-v2/meta/connect/start");
    const authorizationUrl = String(response.data?.authorization_url || "").trim();
    if (!authorizationUrl) throw new Error("meta_authorization_url_missing");

    let parsed;
    try {
        parsed = new URL(authorizationUrl);
    } catch {
        throw new Error("meta_authorization_url_invalid");
    }
    if (
        parsed.protocol !== "https:"
        || parsed.hostname !== "www.facebook.com"
        || !META_OAUTH_PATH.test(parsed.pathname)
    ) {
        throw new Error("meta_authorization_url_untrusted");
    }
    return {
        authorization_url: authorizationUrl,
        expires_at: response.data?.expires_at || null,
        provider: response.data?.provider || "meta_ads",
        scopes: Array.isArray(response.data?.scopes) ? response.data.scopes : [],
        graph_version: response.data?.graph_version || null,
    };
}

export function normalizeMetaAccountSelection(payload = {}) {
    assertReadOnlyEnvelope(payload, "unsafe_meta_account_selection_response");
    const accounts = Array.isArray(payload.accounts)
        ? payload.accounts.map((account) => ({
            account_id: String(account?.account_id || "").trim(),
            mezan_integration_account_id: account?.mezan_integration_account_id || null,
            display_name: account?.display_name || null,
            currency: account?.currency || null,
            timezone: account?.timezone || null,
            business_id: account?.business_id || null,
            business_name: account?.business_name || null,
            account_status: account?.account_status ?? null,
            selected: account?.selected === true,
            selection_status: account?.selection_status || "discovered",
            selected_at: account?.selected_at || null,
        })).filter((account) => account.account_id)
        : [];
    return {
        provider: "meta_ads",
        discovered_count: Number(payload.discovered_count || accounts.length || 0),
        selected_count: Number(
            payload.selected_count
            ?? accounts.filter((account) => account.selected).length,
        ),
        selection_required: payload.selection_required === true,
        accounts,
        source_only: true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    };
}

export async function getMetaAccountSelection() {
    const response = await api.get("/integrations-v2/meta_ads/accounts-selection");
    return normalizeMetaAccountSelection(response.data);
}

export async function saveMetaAccountSelection(accountIds) {
    const normalized = [...new Set(
        (Array.isArray(accountIds) ? accountIds : [])
            .map((value) => String(value || "").trim())
            .filter(Boolean),
    )];
    if (!normalized.length) throw new Error("meta_account_selection_empty");
    const response = await api.put(
        "/integrations-v2/meta_ads/accounts-selection",
        { account_ids: normalized },
    );
    return normalizeMetaAccountSelection(response.data);
}

export function normalizeMetaReportingResult(payload = {}) {
    assertReadOnlyEnvelope(payload, "unsafe_meta_reporting_response");
    const status = String(payload.status || "unknown");
    return {
        run_id: String(payload.run_id || "").trim(),
        provider: "meta_ads",
        status,
        started_at: payload.started_at || null,
        finished_at: payload.finished_at || null,
        date_from: payload.date_from || null,
        date_to: payload.date_to || null,
        accounts_attempted: Number(payload.accounts_attempted || 0),
        accounts_complete: Number(payload.accounts_complete || 0),
        rows_saved: Number(payload.rows_saved || 0),
        errors_count: Number(payload.errors_count || 0),
        source_only: true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
        error: payload.error && typeof payload.error === "object"
            ? {
                code: payload.error.code || "meta_reporting_failed",
                message: payload.error.message || "تعذر إكمال مزامنة Meta.",
                retryable: payload.error.retryable === true,
            }
            : null,
    };
}

export async function getMetaReportingRun(runId) {
    const safeRunId = String(runId || "").trim();
    if (!safeRunId) throw new Error("meta_reporting_run_id_missing");
    const response = await api.get(
        `/integrations-v2/meta_ads/sync-async/${encodeURIComponent(safeRunId)}`,
    );
    return normalizeMetaReportingResult(response.data);
}

export async function startMetaReportingSync({
    days = 7,
    pollIntervalMs = 1500,
    timeoutMs = 20 * 60 * 1000,
} = {}) {
    const parsedDays = Number(days);
    if (!Number.isInteger(parsedDays) || parsedDays < 1 || parsedDays > 31) {
        throw new Error("invalid_meta_reporting_days");
    }
    const response = await api.post(
        "/integrations-v2/meta_ads/sync-async",
        { days: parsedDays },
    );
    let result = normalizeMetaReportingResult(response.data);
    if (!result.run_id) throw new Error("meta_reporting_run_id_missing");
    if (TERMINAL_REPORTING_STATUSES.has(result.status)) return result;

    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        await wait(pollIntervalMs);
        result = await getMetaReportingRun(result.run_id);
        if (TERMINAL_REPORTING_STATUSES.has(result.status)) return result;
    }
    throw new Error("meta_reporting_poll_timeout");
}
