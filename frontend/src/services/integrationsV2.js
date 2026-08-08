import api from "../lib/api";

export const PROVIDER_ORDER = Object.freeze([
    "salla", "openai", "snapchat_ads", "tiktok_ads", "meta_ads",
    "google_analytics_4", "google_search_console", "google_merchant_center",
    "google_ads", "qoyod", "shipping_companies",
]);

const PROVIDER_DEFAULTS = Object.freeze({
    salla: ["Salla", "سلة", "commerce"],
    openai: ["OpenAI", "OpenAI · ذكاء ميزان", "ai"],
    snapchat_ads: ["Snapchat Ads", "إعلانات سناب شات", "advertising"],
    tiktok_ads: ["TikTok Ads", "إعلانات تيك توك", "advertising"],
    meta_ads: ["Meta Ads", "إعلانات ميتا", "advertising"],
    google_analytics_4: ["Google Analytics 4", "إحصاءات Google 4", "analytics"],
    google_search_console: ["Google Search Console", "Google Search Console", "search"],
    google_merchant_center: ["Google Merchant Center", "Google Merchant Center", "commerce"],
    google_ads: ["Google Ads", "إعلانات Google", "advertising"],
    qoyod: ["Qoyod", "قيود", "accounting"],
    shipping_companies: ["Shipping Companies", "شركات الشحن", "shipping"],
});

const SECRET_KEY_RE = /(?:^|[_-])(?:access[_-]?token|refresh[_-]?token|token|secret|client[_-]?secret|api[_-]?key|authorization|password|credential|ciphertext|private[_-]?key|signing[_-]?key)(?:$|[_-])/i;
const SECRET_TEXT_RE = /(bearer\s+[a-z0-9._~+/=-]{8,}|access[\s_-]*token|refresh[\s_-]*token|client[\s_-]*secret|app[\s_-]*secret|api[\s_-]*key|(?:token|secret|authorization|password|cookie|credential)\s*[:=])/i;
const SAFE_CAPABILITY_STATES = new Set(["available", "approval_required", "blocked_missing_permission", "blocked_missing_data", "not_connected", "planned", "unknown"]);
const SAFE_CONNECTION_PROVENANCE = new Set(["api_connection", "legacy_integration", "data_feed", "disconnected", "planned", "unknown"]);

export const CONNECTION_PROVENANCE_LABELS = Object.freeze({
    api_connection: "ربط API مباشر", legacy_integration: "تكامل قائم سابقًا",
    data_feed: "تغذية بيانات فقط", disconnected: "غير مرتبط",
    planned: "مستقبلي", unknown: "غير معروف",
});

function text(value, fallback = "") { return typeof value === "string" ? value : fallback; }
function nullableText(value) { return typeof value === "string" && value.trim() ? value : null; }
function safeNumber(value, { min = null, max = null } = {}) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return null;
    if (min !== null && parsed < min) return min;
    if (max !== null && parsed > max) return max;
    return parsed;
}

function isSecretKey(key) {
    const value = String(key || "").trim();
    if (SECRET_KEY_RE.test(value)) return true;
    const compact = value.toLowerCase().replace(/[^a-z0-9]/g, "");
    return ["token", "secret", "cookie"].includes(compact)
        || [
            "accesstoken",
            "refreshtoken",
            "clientsecret",
            "appsecret",
            "apikey",
            "authorization",
            "password",
            "credential",
            "ciphertext",
            "privatekey",
            "signingkey",
        ].some((fragment) => compact.includes(fragment));
}

export function redactIntegrationValue(value) {
    if (Array.isArray(value)) return value.map(redactIntegrationValue);
    if (value && typeof value === "object") {
        return Object.entries(value).reduce((safe, [key, item]) => {
            if (isSecretKey(key)) return safe;
            safe[key] = redactIntegrationValue(item);
            return safe;
        }, {});
    }
    if (typeof value === "string" && SECRET_TEXT_RE.test(value)) return "تم حجب تفاصيل حساسة";
    return value;
}

function normalizeCapability(entry = {}) {
    const state = SAFE_CAPABILITY_STATES.has(entry?.state) ? entry.state : "unknown";
    return { state, available: state === "available" && entry?.available !== false, approval_required: Boolean(entry?.approval_required), blocked_by_policy: Boolean(entry?.blocked_by_policy), reason: text(entry?.reason, "لا تتوفر أدلة كافية على هذه القدرة.") };
}

function normalizeCapabilities(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.entries(value).reduce((safe, [key, entry]) => { if (/^[a-z0-9_.-]+$/i.test(key)) safe[key] = normalizeCapability(entry); return safe; }, {});
}

function safeInternalHref(value) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\") || /[\r\n]/.test(value)) return null;
    try {
        const parsed = new URL(value, "https://mezan.local");
        if (parsed.origin !== "https://mezan.local") return null;
        return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    } catch { return null; }
}

function inferConnectionProvenance({ value, connectionStatus, sourceMode }) {
    if (SAFE_CONNECTION_PROVENANCE.has(value)) return value;
    if (connectionStatus === "planned") return "planned";
    if (["not_connected", "not_configured"].includes(connectionStatus)) return "disconnected";
    if (connectionStatus === "data_available" || ["data_feed", "legacy_data"].includes(sourceMode)) return "data_feed";
    return "unknown";
}

function normalizeAccount(account, provider, fallbackStatus, fallbackSource, fallbackProvenance) {
    const safe = redactIntegrationValue(account || {});
    return {
        mezan_integration_account_id: nullableText(safe.mezan_integration_account_id), provider,
        external_account_id: nullableText(safe.external_account_id), store_id: nullableText(safe.store_id),
        ad_account_id: nullableText(safe.ad_account_id), display_name: nullableText(safe.display_name),
        currency: nullableText(safe.currency), timezone: nullableText(safe.timezone),
        local_today: /^\d{4}-\d{2}-\d{2}$/.test(safe.local_today || "")
            ? safe.local_today
            : null,
        connection_status: text(safe.connection_status, fallbackStatus), capabilities: normalizeCapabilities(safe.capabilities),
        permissions: Array.isArray(safe.permissions) ? safe.permissions.filter((item) => typeof item === "string") : [],
        last_sync_at: nullableText(safe.last_sync_at), data_delay_minutes: safeNumber(safe.data_delay_minutes, { min: 0 }),
        health_score: safeNumber(safe.health_score, { min: 0, max: 100 }), source_mode: text(safe.source_mode, fallbackSource),
        connection_provenance: inferConnectionProvenance({ value: safe.connection_provenance || fallbackProvenance, connectionStatus: text(safe.connection_status, fallbackStatus), sourceMode: text(safe.source_mode, fallbackSource) }),
    };
}

function normalizeActions(actions = {}) {
    return ["test_connection", "sync_data", "reconnect", "settings", "disconnect"].reduce((result, key) => {
        const action = actions?.[key] || {};
        const href = safeInternalHref(action.href);
        result[key] = { enabled: key === "disconnect" ? false : Boolean(action.enabled), reason: nullableText(action.reason), href: key === "disconnect" ? null : href };
        return result;
    }, {});
}

function fallbackProvider(provider) {
    const [name, nameAr, category] = PROVIDER_DEFAULTS[provider] || [provider, provider, "other"];
    const planned = provider === "shipping_companies";
    return {
        provider, name, name_ar: nameAr, category,
        connection_status: planned ? "planned" : "not_configured",
        connection_provenance: planned ? "planned" : "disconnected",
        source_mode: planned ? "planned" : "none", accounts: [],
        permissions: { current: [], missing: [], unknown: true }, capabilities: {},
        last_sync_at: null, data_delay_minutes: null,
        health: { status: planned ? "planned" : "unknown", score: null, checked_at: null, data_quality: "unknown" },
        latest_error: null, ai: { can: [], cannot: ["يلزم ربط موثق قبل تفعيل أي قدرة."] }, actions: normalizeActions(),
    };
}

export function normalizeProviderCard(raw, provider) {
    const fallback = fallbackProvider(provider);
    const safe = redactIntegrationValue(raw || {});
    let connectionStatus = text(safe.connection_status, fallback.connection_status);
    const sourceMode = text(safe.source_mode, fallback.source_mode);
    const connectionProvenance = inferConnectionProvenance({ value: safe.connection_provenance, connectionStatus, sourceMode });
    if (connectionStatus === "connected" && ["data_feed", "disconnected", "unknown"].includes(connectionProvenance)) connectionStatus = connectionProvenance === "data_feed" ? "data_available" : connectionProvenance === "disconnected" ? "not_connected" : "unknown";
    const latestError = safe.latest_error && typeof safe.latest_error === "object" ? { code: nullableText(safe.latest_error.code), message: nullableText(safe.latest_error.message || safe.latest_error.safe_message), occurred_at: nullableText(safe.latest_error.occurred_at) } : null;
    return {
        ...fallback, provider, name: text(safe.name, fallback.name), name_ar: text(safe.name_ar, fallback.name_ar), category: text(safe.category, fallback.category), connection_status: connectionStatus, connection_provenance: connectionProvenance, source_mode: sourceMode,
        accounts: Array.isArray(safe.accounts) ? safe.accounts.map((account) => normalizeAccount(account, provider, connectionStatus, sourceMode, connectionProvenance)) : [],
        permissions: { current: Array.isArray(safe.permissions?.current) ? safe.permissions.current.filter((item) => typeof item === "string") : [], missing: Array.isArray(safe.permissions?.missing) ? safe.permissions.missing.filter((item) => typeof item === "string") : [], unknown: Boolean(safe.permissions?.unknown) },
        capabilities: normalizeCapabilities(safe.capabilities), last_sync_at: nullableText(safe.last_sync_at), data_delay_minutes: safeNumber(safe.data_delay_minutes, { min: 0 }),
        health: { status: text(safe.health?.status, fallback.health.status), score: safeNumber(safe.health?.score, { min: 0, max: 100 }), checked_at: nullableText(safe.health?.checked_at), data_quality: text(safe.health?.data_quality, "unknown") },
        latest_error: latestError?.message || latestError?.code ? latestError : null,
        ai: { can: Array.isArray(safe.ai?.can) ? safe.ai.can.filter((item) => typeof item === "string") : [], cannot: Array.isArray(safe.ai?.cannot) ? safe.ai.cannot.filter((item) => typeof item === "string") : fallback.ai.cannot },
        actions: normalizeActions(safe.actions),
    };
}

export function summarizeProviders(providers) {
    const rows = Array.isArray(providers) ? providers : [];
    return {
        total: rows.length, connected: rows.filter((row) => row.connection_status === "connected").length,
        api_connections: rows.filter((row) => row.connection_provenance === "api_connection").length,
        legacy_integrations: rows.filter((row) => row.connection_provenance === "legacy_integration").length,
        data_feeds: rows.filter((row) => row.connection_provenance === "data_feed").length,
        disconnected: rows.filter((row) => row.connection_provenance === "disconnected").length,
        planned: rows.filter((row) => row.connection_provenance === "planned").length,
        unknown: rows.filter((row) => row.connection_provenance === "unknown").length,
        healthy: rows.filter((row) => row.health?.status === "healthy").length,
        missing_permissions: rows.filter((row) => !row.permissions?.unknown && (row.permissions?.missing || []).length > 0).length,
        attention_required: rows.filter((row) => ["needs_reauth", "expired", "error"].includes(row.connection_status) || ["degraded", "unhealthy", "error"].includes(row.health?.status)).length,
    };
}

export function normalizeIntegrationOverview(payload) {
    const safe = redactIntegrationValue(payload || {});
    const incoming = new Map((Array.isArray(safe.providers) ? safe.providers : []).filter((row) => PROVIDER_ORDER.includes(row?.provider)).map((row) => [row.provider, row]));
    const providers = PROVIDER_ORDER.map((provider) => normalizeProviderCard(incoming.get(provider), provider));
    return {
        generated_at: nullableText(safe.generated_at),
        providers,
        summary: summarizeProviders(providers),
        safety_policy: {
            phase: safeNumber(safe.safety_policy?.phase, { min: 1 }) || 1,
            read_only: safe.safety_policy?.read_only === true,
            analytics_refresh_enabled: (
                safe.safety_policy?.analytics_refresh_enabled === true
            ),
            provider_mutations_enabled: false,
            advertising_mutations_enabled: false,
            accounting_mutations_enabled: false,
            mutation_lifecycle: Array.isArray(safe.safety_policy?.mutation_lifecycle)
                ? safe.safety_policy.mutation_lifecycle.filter((item) => typeof item === "string")
                : [],
            policy: nullableText(safe.safety_policy?.policy),
        },
    };
}

export function filterIntegrationProviders(providers, { query = "", status = "all" } = {}) {
    const needle = String(query || "").trim().toLowerCase();
    return (providers || []).filter((provider) => {
        const matchesStatus = status === "all" || status === provider.connection_provenance || (status === "attention" && (["needs_reauth", "expired", "error"].includes(provider.connection_status) || (provider.permissions?.missing || []).length > 0 || ["degraded", "unhealthy", "error"].includes(provider.health?.status)));
        if (!matchesStatus) return false;
        if (!needle) return true;
        return [provider.name, provider.name_ar, provider.provider, ...(provider.accounts || []).flatMap((account) => [account.display_name, account.external_account_id])].filter(Boolean).join(" ").toLowerCase().includes(needle);
    });
}

export function summarizeCapabilityStates(capabilities) {
    return Object.values(capabilities || {}).reduce((summary, entry) => { const state = SAFE_CAPABILITY_STATES.has(entry?.state) ? entry.state : "unknown"; summary[state] = (summary[state] || 0) + 1; return summary; }, {});
}

export function normalizeIntegrationSyncResult(payload) {
    const safe = redactIntegrationValue(payload || {});
    const reportedStatus = ["complete", "partial", "failed"].includes(
        safe.status || safe.sync_status,
    )
        ? (safe.status || safe.sync_status)
        : "failed";
    const sourceOnly = safe.source_only === true;
    const accountingWriteReached = safe.accounting_write_reached === true;
    const qoyodWriteReached = safe.qoyod_write_reached === true;
    const status = (
        sourceOnly
        && !accountingWriteReached
        && !qoyodWriteReached
    ) ? reportedStatus : "failed";
    return {
        run_id: nullableText(safe.run_id),
        provider: "snapchat_ads",
        status,
        date_from: nullableText(safe.date_from),
        date_to: nullableText(safe.date_to),
        accounts_attempted: safeNumber(
            safe.accounts_attempted ?? safe.accounts_synced,
            { min: 0 },
        ) || 0,
        accounts_complete: safeNumber(safe.accounts_complete, { min: 0 }) || 0,
        rows_saved: safeNumber(safe.rows_saved, { min: 0 }) || 0,
        errors_count: safeNumber(
            safe.errors_count ?? (Array.isArray(safe.errors) ? safe.errors.length : 0),
            { min: 0 },
        ) || 0,
        business_timezone: text(safe.business_timezone, "Asia/Riyadh"),
        source_only: sourceOnly,
        accounting_write_reached: accountingWriteReached,
        qoyod_write_reached: qoyodWriteReached,
    };
}

export async function getIntegrationsOverview() { const response = await api.get("/integrations-v2/overview"); return normalizeIntegrationOverview(response.data); }
export async function getIntegrationsActivity({ provider = "", limit = 50 } = {}) {
    const params = { limit }; if (provider) params.provider = provider;
    const [runs, errors] = await Promise.all([api.get("/integrations-v2/sync-runs", { params }), api.get("/integrations-v2/errors", { params })]);
    return { runs: Array.isArray(runs.data?.items) ? redactIntegrationValue(runs.data.items) : [], errors: Array.isArray(errors.data?.items) ? redactIntegrationValue(errors.data.items) : [] };
}
export async function testIntegrationConnection(provider) { if (!PROVIDER_ORDER.includes(provider)) throw new Error("unsupported_provider"); const response = await api.post(`/integrations-v2/${encodeURIComponent(provider)}/test-connection`); return redactIntegrationValue(response.data); }
export async function syncIntegrationData(provider, { days = 30 } = {}) {
    if (provider !== "snapchat_ads") throw new Error("unsupported_sync_provider");
    const parsedDays = Number(days);
    if (!Number.isInteger(parsedDays) || parsedDays < 1 || parsedDays > 62) {
        throw new Error("invalid_sync_days");
    }
    const response = await api.post(
        `/integrations-v2/${encodeURIComponent(provider)}/sync`,
        { days: parsedDays },
    );
    return normalizeIntegrationSyncResult(response.data);
}
