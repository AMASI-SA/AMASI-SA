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

export function normalizeMetaManagementReadiness(payload = {}) {
    assertReadOnlyEnvelope(payload, "unsafe_meta_management_readiness_response");
    const capabilities = payload.capabilities && typeof payload.capabilities === "object"
        ? Object.fromEntries(Object.entries(payload.capabilities).map(([key, value]) => [key, value === true]))
        : {};
    const accounts = Array.isArray(payload.accounts) ? payload.accounts.map((account) => ({
        account_id: String(account?.account_id || "").trim(),
        display_name: account?.display_name || null,
        currency: account?.currency || null,
        timezone: account?.timezone || null,
        account_status: account?.account_status ?? null,
        disable_reason: account?.disable_reason ?? null,
        readable: account?.readable === true,
        role_verified: account?.role_verified === true,
        tasks: Array.isArray(account?.tasks) ? account.tasks.map(String) : [],
        write_task_present: account?.write_task_present === true,
        ready: account?.ready === true,
        errors: Array.isArray(account?.errors) ? account.errors.map(String) : [],
    })).filter((account) => account.account_id) : [];
    return {
        provider: "meta_ads",
        checked_at: payload.checked_at || null,
        token_valid: payload.token_valid === true,
        scopes: Array.isArray(payload.scopes) ? payload.scopes.map(String) : [],
        missing_scopes: Array.isArray(payload.missing_scopes) ? payload.missing_scopes.map(String) : [],
        accounts,
        capabilities,
        write_ready: payload.write_ready === true,
        read_only_check: payload.read_only_check === true,
        source_only: true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    };
}

export async function getMetaManagementReadiness() {
    const response = await api.get("/integrations-v2/meta_ads/management-readiness");
    return normalizeMetaManagementReadiness(response.data);
}

function normalizeMetaEntity(row = {}) {
    const safe = row && typeof row === "object" ? row : {};
    return {
        id: String(safe.id || "").trim(),
        name: safe.name || null,
        campaign_id: safe.campaign_id ? String(safe.campaign_id) : null,
        adset_id: safe.adset_id ? String(safe.adset_id) : null,
        status: safe.status || null,
        effective_status: safe.effective_status || null,
        objective: safe.objective || null,
        buying_type: safe.buying_type || null,
        daily_budget: safe.daily_budget ?? null,
        lifetime_budget: safe.lifetime_budget ?? null,
        budget_remaining: safe.budget_remaining ?? null,
        bid_amount: safe.bid_amount ?? null,
        bid_strategy: safe.bid_strategy || null,
        billing_event: safe.billing_event || null,
        optimization_goal: safe.optimization_goal || null,
        start_time: safe.start_time || null,
        stop_time: safe.stop_time || safe.end_time || null,
        creative: safe.creative && typeof safe.creative === "object"
            ? { id: safe.creative.id || null, name: safe.creative.name || null }
            : null,
    };
}

export async function getMetaManagementHierarchy(accountId) {
    const normalized = String(accountId || "").trim();
    if (!normalized) throw new Error("meta_account_id_required");
    const response = await api.get("/integrations-v2/meta_ads/management-hierarchy", {
        params: { account_id: normalized },
    });
    assertReadOnlyEnvelope(response.data, "unsafe_meta_management_hierarchy_response");
    return {
        account_id: String(response.data?.account_id || normalized),
        campaigns: Array.isArray(response.data?.campaigns)
            ? response.data.campaigns.map(normalizeMetaEntity).filter((row) => row.id)
            : [],
        adsets: Array.isArray(response.data?.adsets)
            ? response.data.adsets.map(normalizeMetaEntity).filter((row) => row.id)
            : [],
        ads: Array.isArray(response.data?.ads)
            ? response.data.ads.map(normalizeMetaEntity).filter((row) => row.id)
            : [],
        counts: response.data?.counts || {},
        fetched_at: response.data?.fetched_at || null,
    };
}

export async function previewMetaManagementMutation(input) {
    const response = await api.post(
        "/integrations-v2/meta_ads/management-proposals",
        input,
    );
    const safe = response.data && typeof response.data === "object" ? response.data : {};
    if (safe.provider_write_reached === true || safe.status !== "previewed") {
        throw new Error("unsafe_meta_management_preview_response");
    }
    return safe;
}

export async function approveAndExecuteMetaManagementProposal(proposalId) {
    const normalized = String(proposalId || "").trim();
    if (!normalized) throw new Error("meta_proposal_id_required");
    const response = await api.post(
        `/integrations-v2/meta_ads/management-proposals/${encodeURIComponent(normalized)}/approve-and-execute`,
    );
    return response.data && typeof response.data === "object" ? response.data : {};
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
