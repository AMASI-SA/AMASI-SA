export const ADVERTISING_PROVIDER_IDS = Object.freeze([
    "snapchat_ads",
    "tiktok_ads",
    "meta_ads",
    "google_ads",
]);

const ADVERTISING_PROVIDER_SET = new Set(ADVERTISING_PROVIDER_IDS);

export function integrationWorkspaceFromSearchParams(searchParams) {
    const requested = searchParams?.get?.("workspace");
    if (requested === "accounts" || requested === "financial") return requested;
    return "apps";
}

export function focusedIntegrationProvider(searchParams, providers = []) {
    const requested = String(searchParams?.get?.("provider") || "").trim();
    if (!requested) return "";
    if (
        integrationWorkspaceFromSearchParams(searchParams) === "accounts"
        && !ADVERTISING_PROVIDER_SET.has(requested)
    ) {
        return "";
    }
    return (providers || []).some((provider) => provider?.provider === requested)
        ? requested
        : "";
}

export function providersForIntegrationWorkspace(providers = [], workspace = "apps") {
    const rows = Array.isArray(providers) ? providers : [];
    if (workspace !== "accounts") return rows;
    const byProvider = new Map(
        rows
            .filter((provider) => ADVERTISING_PROVIDER_SET.has(provider?.provider))
            .map((provider) => [provider.provider, provider]),
    );
    return ADVERTISING_PROVIDER_IDS
        .map((provider) => byProvider.get(provider))
        .filter(Boolean);
}

export function summarizeAdvertisingWorkspace(providers = []) {
    const rows = providersForIntegrationWorkspace(providers, "accounts");
    const accounts = rows.flatMap((provider) => (
        Array.isArray(provider?.accounts) ? provider.accounts : []
    ));
    const attentionProviders = rows.filter((provider) => (
        ["needs_reauth", "expired", "error"].includes(provider?.connection_status)
        || ["degraded", "unhealthy", "error"].includes(provider?.health?.status)
        || (!provider?.permissions?.unknown && (provider?.permissions?.missing || []).length > 0)
    ));
    return {
        providers_total: rows.length,
        api_connections: rows.filter(
            (provider) => provider?.connection_provenance === "api_connection",
        ).length,
        connected_providers: rows.filter(
            (provider) => provider?.connection_status === "connected",
        ).length,
        accounts_visible: accounts.length,
        currencies: new Set(accounts.map((account) => account?.currency).filter(Boolean)).size,
        timezones: new Set(accounts.map((account) => account?.timezone).filter(Boolean)).size,
        attention_required: attentionProviders.length,
    };
}
