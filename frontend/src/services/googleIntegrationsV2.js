import api from "../lib/api";

const GOOGLE_ACCOUNT_SELECTION_PATH = "/integrations-v2/google_ads/accounts-selection";
const MAX_REPORTING_ACCOUNTS = 50;

function accountIds(values, { allowEmpty = false } = {}) {
    if (!Array.isArray(values) || values.length > MAX_REPORTING_ACCOUNTS
        || (!allowEmpty && values.length === 0)) throw new Error("invalid_google_account_selection");
    const ids = values.map((value) => typeof value === "string" ? value.trim().replace(/-/g, "") : "");
    if (ids.some((id) => !/^\d{10}$/.test(id)) || new Set(ids).size !== ids.length) {
        throw new Error("invalid_google_account_selection");
    }
    return ids;
}

export function normalizeGoogleAccountSelection(payload) {
    if (payload?.provider !== "google_ads" || payload?.source_only !== true
        || ["provider_write_reached", "campaign_write_reached", "accounting_write_reached", "qoyod_write_reached"]
            .some((key) => payload[key] !== false)
        || !Array.isArray(payload.accounts)
        || typeof payload.selection_configured !== "boolean"
        || typeof payload.selection_required !== "boolean") {
        throw new Error("invalid_google_account_selection_response");
    }
    const expected = accountIds(payload.expected_account_ids, { allowEmpty: true });
    const ids = accountIds(payload.accounts.map((account) => account?.account_id), { allowEmpty: true });
    const missing = accountIds(payload.missing_selected_account_ids ?? [], { allowEmpty: true });
    if (expected.length !== ids.length || ids.some((id) => !expected.includes(id))
        || payload.accounts.some((account) => typeof account.selected !== "boolean")) {
        throw new Error("invalid_google_account_selection_response");
    }
    const accounts = payload.accounts.map((account, index) => ({
        account_id: ids[index],
        display_name: typeof account.display_name === "string" ? account.display_name.slice(0, 240) : null,
        currency: typeof account.currency === "string" ? account.currency : null,
        timezone: typeof account.timezone === "string" ? account.timezone : null,
        selected: account.selected,
        selection_status: typeof account.selection_status === "string" ? account.selection_status : null,
        selected_at: typeof account.selected_at === "string" ? account.selected_at : null,
    }));
    return {
        provider: "google_ads",
        accounts,
        expected_account_ids: expected,
        missing_selected_account_ids: missing,
        discovered_count: accounts.length,
        selected_count: accounts.filter((account) => account.selected).length,
        selection_required: payload.selection_required,
        selection_configured: payload.selection_configured,
        source_only: true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
        ...(typeof payload.changed === "boolean" ? { changed: payload.changed } : {}),
    };
}

export async function getGoogleAccountSelection() {
    const response = await api.get(GOOGLE_ACCOUNT_SELECTION_PATH);
    return normalizeGoogleAccountSelection(response.data);
}

export async function saveGoogleAccountSelection(selectedAccountIds, expectedAccountIds) {
    const selected = accountIds(selectedAccountIds);
    const expected = accountIds(expectedAccountIds);
    if (selected.some((id) => !expected.includes(id))) throw new Error("invalid_google_account_selection");
    let response;
    try {
        response = await api.put(GOOGLE_ACCOUNT_SELECTION_PATH, {
            selected_account_ids: selected,
            expected_account_ids: expected,
        });
    } catch (error) {
        const failure = error && typeof error === "object" ? error : new Error("google_account_selection_save_failed");
        if (!failure.response) failure.applyOutcomeUnknown = true;
        throw failure;
    }
    try {
        return normalizeGoogleAccountSelection(response.data);
    } catch (error) {
        error.applyOutcomeUnknown = true;
        throw error;
    }
}

export async function startGoogleConnection() {
    const response = await api.post("/integrations-v2/google/connect/start");
    const authorizationUrl = String(response.data?.authorization_url || "").trim();
    if (!authorizationUrl) throw new Error("google_authorization_url_missing");
    let parsed;
    try {
        parsed = new URL(authorizationUrl);
    } catch {
        throw new Error("google_authorization_url_invalid");
    }
    if (parsed.protocol !== "https:" || parsed.hostname !== "accounts.google.com") {
        throw new Error("google_authorization_url_untrusted");
    }
    return {
        authorization_url: authorizationUrl,
        expires_at: response.data?.expires_at || null,
        providers: Array.isArray(response.data?.providers) ? response.data.providers : [],
    };
}
