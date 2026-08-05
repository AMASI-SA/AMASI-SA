import api from "../lib/api";

function text(value) {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

function number(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeSelectionAccount(account = {}) {
    return {
        account_id: text(
            account.account_id
            || account.ad_account_id
            || account.external_account_id,
        ),
        mezan_integration_account_id: text(account.mezan_integration_account_id),
        display_name: text(account.display_name || account.name),
        currency: text(account.currency),
        timezone: text(account.timezone),
        account_status: text(account.account_status),
        selected: account.selected === true,
        selection_status: text(account.selection_status),
        selected_at: text(account.selected_at),
    };
}

function normalizeSelectionPayload(payload = {}) {
    const accounts = Array.isArray(payload?.accounts)
        ? payload.accounts.map(normalizeSelectionAccount).filter((account) => account.account_id)
        : [];
    return {
        provider: "snapchat_ads",
        discovered_count: Math.max(0, number(payload?.discovered_count, accounts.length)),
        selected_count: Math.max(
            0,
            number(
                payload?.selected_count,
                accounts.filter((account) => account.selected).length,
            ),
        ),
        selection_required: payload?.selection_required === true,
        accounts,
    };
}

function normalizePerformanceAccount(account = {}) {
    return {
        account_id: text(account.account_id),
        display_name: text(account.display_name || account.name),
        currency: text(account.currency),
        timezone: text(account.timezone),
        rows: Math.max(0, number(account.rows)),
        spend_native: number(account.spend_native),
        spend_sar: number(account.spend_sar),
        purchase_value_native: number(account.purchase_value_native),
        purchase_value_sar: number(account.purchase_value_sar),
    };
}

export async function startSnapchatConnection() {
    const response = await api.post("/integrations-v2/snapchat/connect/start");
    const authorizationUrl = String(response.data?.authorization_url || "").trim();
    if (!authorizationUrl) throw new Error("snapchat_authorization_url_missing");

    let parsed;
    try {
        parsed = new URL(authorizationUrl);
    } catch {
        throw new Error("snapchat_authorization_url_invalid");
    }
    if (
        parsed.protocol !== "https:"
        || parsed.hostname !== "accounts.snapchat.com"
        || parsed.pathname !== "/login/oauth2/authorize"
    ) {
        throw new Error("snapchat_authorization_url_untrusted");
    }
    return {
        authorization_url: authorizationUrl,
        expires_at: response.data?.expires_at || null,
        provider: response.data?.provider || "snapchat_ads",
        scopes: Array.isArray(response.data?.scopes) ? response.data.scopes : [],
    };
}

export async function getSnapchatAccountSelection() {
    const response = await api.get(
        "/integrations-v2/snapchat_ads/accounts-selection",
    );
    return normalizeSelectionPayload(response.data);
}

export async function saveSnapchatAccountSelection(accountIds = []) {
    const normalizedIds = [...new Set(
        (Array.isArray(accountIds) ? accountIds : [])
            .map((value) => String(value || "").trim())
            .filter(Boolean),
    )];
    if (!normalizedIds.length) {
        const error = new Error("snapchat_account_selection_required");
        error.code = "snapchat_account_selection_required";
        throw error;
    }
    const response = await api.put(
        "/integrations-v2/snapchat_ads/accounts-selection",
        { account_ids: normalizedIds },
    );
    return normalizeSelectionPayload(response.data);
}

export async function getSnapchatSelectedPerformanceSummary({
    fromDate = null,
    toDate = null,
} = {}) {
    const params = {};
    if (fromDate) params.from_date = fromDate;
    if (toDate) params.to_date = toDate;
    const response = await api.get(
        "/integrations-v2/snapchat_ads/performance-summary",
        { params },
    );
    const payload = response.data || {};
    if (
        payload.source_only !== true
        || payload.accounting_write_reached === true
        || payload.qoyod_write_reached === true
    ) {
        throw new Error("snapchat_summary_safety_contract_failed");
    }
    const accounts = Array.isArray(payload.accounts)
        ? payload.accounts.map(normalizePerformanceAccount)
        : [];
    return {
        provider: "snapchat_ads",
        date_from: text(payload.date_from),
        date_to: text(payload.date_to),
        selected_account_ids: Array.isArray(payload.selected_account_ids)
            ? payload.selected_account_ids.map(text).filter(Boolean)
            : [],
        selected_account_count: Math.max(
            0,
            number(payload.selected_account_count, accounts.length),
        ),
        rows_included: Math.max(0, number(payload.rows_included)),
        unselected_rows_excluded: Math.max(
            0,
            number(payload.unselected_rows_excluded),
        ),
        spend_sar: number(payload.spend_sar),
        purchase_value_sar: number(payload.purchase_value_sar),
        accounts,
        source_only: true,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    };
}

export const SNAPCHAT_ACCOUNT_SELECTION_CLIENT_POLICY = Object.freeze({
    endpoint: "/integrations-v2/snapchat_ads/accounts-selection",
    requires_at_least_one_account: true,
    deduplicates_account_ids: true,
    provider_writes_allowed: false,
    campaign_writes_allowed: false,
    accounting_writes_allowed: false,
});
