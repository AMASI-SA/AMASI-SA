import api from "./lib/api";

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DASHBOARD_V2_PATH = "/dashboard-v2";
let installed = false;

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

export function visibleDashboardRange(root = document) {
    const node = root?.querySelector?.('[data-testid="advanced-filters"]');
    const from = String(node?.dataset?.fromDate || "").trim();
    const to = String(node?.dataset?.toDate || from).trim();
    if (!ISO_DATE_RE.test(from) || !ISO_DATE_RE.test(to) || to < from) {
        return null;
    }
    return { from, to };
}

export function rewriteDashboardRequestToVisibleRange(config = {}, root = document) {
    const method = String(config?.method || "get").toLowerCase();
    const path = pathOnly(config);
    if (method !== "get" || path !== DASHBOARD_V2_PATH) return config;

    const visible = visibleDashboardRange(root);
    if (!visible) return config;

    const params = configQuery(config);
    const requestedFrom = String(params.get("from_date") || "").trim();
    const requestedTo = String(params.get("to_date") || requestedFrom).trim();
    if (requestedFrom === visible.from && requestedTo === visible.to) {
        return config;
    }

    params.set("from_date", visible.from);
    params.set("to_date", visible.to);
    return {
        ...config,
        url: `${path}?${params.toString()}`,
        params: undefined,
        _mezanDashboardVisibleRangeGuard: true,
        _mezanDashboardVisibleRange: `${visible.from}:${visible.to}`,
    };
}

export function installDashboardVisibleRangeRequestGuard() {
    if (installed) return;
    installed = true;
    api.interceptors.request.use((config) => (
        rewriteDashboardRequestToVisibleRange(config)
    ));
}

installDashboardVisibleRangeRequestGuard();
