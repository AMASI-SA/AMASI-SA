import api from "./lib/api";

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DASHBOARD_V2_PATH = "/dashboard-v2";
export const ADS_DATE_RANGE_APPLIED_EVENT = "mezan:ads-date-range-applied";
export const APPLIED_RANGE_GUARD_WINDOW_MS = 2_000;

let installed = false;
let lastAppliedRange = null;
let lastAppliedAt = 0;

function validRange(from, to) {
    return ISO_DATE_RE.test(from || "")
        && ISO_DATE_RE.test(to || "")
        && to >= from;
}

function pathOnly(config = {}) {
    const raw = String(config?.url || "");
    const withoutQuery = raw.split("?", 1)[0].replace(/\/+$/, "");
    if (!withoutQuery) return "";
    try {
        return new URL(withoutQuery, "https://mezan.local").pathname.replace(/\/+$/, "");
    } catch {
        return withoutQuery;
    }
}

function configQuery(config = {}) {
    const params = new URLSearchParams();
    const raw = String(config?.url || "");
    const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
    new URLSearchParams(query).forEach((value, key) => params.set(key, value));
    if (config?.params && typeof config.params === "object") {
        Object.entries(config.params).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== "") {
                params.set(key, String(value));
            }
        });
    }
    return params;
}

export function rememberAppliedDashboardRange(value = {}, now = Date.now()) {
    const from = String(value?.dateFrom || value?.from || "").trim();
    const to = String(value?.dateTo || value?.to || from).trim();
    if (!validRange(from, to)) return null;
    lastAppliedRange = { from, to };
    lastAppliedAt = Number(now) || Date.now();
    return { ...lastAppliedRange };
}

export function recentAppliedDashboardRange(now = Date.now()) {
    if (!lastAppliedRange) return null;
    const age = Math.max(0, Number(now) - lastAppliedAt);
    return age <= APPLIED_RANGE_GUARD_WINDOW_MS
        ? { ...lastAppliedRange }
        : null;
}

export function resetAppliedDashboardRangeForTests() {
    lastAppliedRange = null;
    lastAppliedAt = 0;
}

function captureAppliedRange(event) {
    rememberAppliedDashboardRange(event?.detail || {});
}

export function rewriteDashboardRequestToVisibleRange(
    config = {},
    _root = document,
    now = Date.now(),
) {
    const method = String(config?.method || "get").toLowerCase();
    const path = pathOnly(config);
    if (method !== "get" || path !== DASHBOARD_V2_PATH) return config;

    const applied = recentAppliedDashboardRange(now);
    if (!applied) {
        // The request already carries the authoritative React filter state.
        // Never replace it from DOM text or stale rendered attributes.
        return config;
    }

    const params = configQuery(config);
    const requestedFrom = String(params.get("from_date") || "").trim();
    const requestedTo = String(params.get("to_date") || requestedFrom).trim();
    if (requestedFrom === applied.from && requestedTo === applied.to) {
        return config;
    }

    // Only during the short transition immediately after «حفظ وتطبيق» do we
    // repair an old silent request. This prevents an interval closure from
    // restoring today's range, without permanently overriding future filters.
    params.set("from_date", applied.from);
    params.set("to_date", applied.to);
    return {
        ...config,
        url: `${path}?${params.toString()}`,
        params: undefined,
        _mezanDashboardAppliedRangeGuard: true,
        _mezanDashboardAppliedRange: `${applied.from}:${applied.to}`,
    };
}

export function installDashboardVisibleRangeRequestGuard() {
    if (installed) return;
    installed = true;
    if (typeof window !== "undefined") {
        window.addEventListener(ADS_DATE_RANGE_APPLIED_EVENT, captureAppliedRange);
    }
    api.interceptors.request.use((config) => (
        rewriteDashboardRequestToVisibleRange(config)
    ));
}

installDashboardVisibleRangeRequestGuard();
