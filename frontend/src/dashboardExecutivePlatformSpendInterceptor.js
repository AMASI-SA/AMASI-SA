import api from "./lib/api";
import { loadDashboardPlatformSpend } from "./lib/dashboardPlatformSpendClient";
import { mergeDashboardWithPlatformSpend } from "./lib/dashboardPlatformSpendMerge";

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
let installed = false;

function pathOnly(config = {}) {
    const raw = String(config?.url || "");
    return raw.split("?", 1)[0].replace(/\/+$/, "");
}

function queryParams(config = {}) {
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

function selectedRange(config = {}) {
    const params = queryParams(config);
    const from = String(params.get("from_date") || "").trim();
    const to = String(params.get("to_date") || from).trim();
    if (!ISO_DATE_RE.test(from) || !ISO_DATE_RE.test(to) || to < from) {
        return null;
    }
    return { from, to };
}

function isDashboardV2Response(response = {}) {
    const method = String(response?.config?.method || "get").toLowerCase();
    return method === "get" && pathOnly(response?.config) === "/dashboard-v2";
}

async function platformSpendForRange(range) {
    try {
        return await loadDashboardPlatformSpend({
            dateFrom: range.from,
            dateTo: range.to,
            refresh: true,
        });
    } catch {
        return loadDashboardPlatformSpend({
            dateFrom: range.from,
            dateTo: range.to,
            refresh: false,
            maxAgeMs: 0,
        });
    }
}

export function installDashboardExecutivePlatformSpendInterceptor() {
    if (installed) return;
    installed = true;

    api.interceptors.response.use(async (response) => {
        if (!isDashboardV2Response(response)) return response;
        const range = selectedRange(response.config);
        if (!range) return response;

        try {
            const platformSpend = await platformSpendForRange(range);
            if (
                platformSpend?.date_from !== range.from
                || platformSpend?.date_to !== range.to
            ) {
                return response;
            }
            return {
                ...response,
                data: mergeDashboardWithPlatformSpend(
                    response.data,
                    platformSpend,
                ),
                statusText: "Dashboard V2 aligned with selected-period four-platform spend",
            };
        } catch {
            // Keep the original Dashboard response available when a provider
            // refresh and the saved read both fail. The next refresh retries.
            return response;
        }
    });
}

installDashboardExecutivePlatformSpendInterceptor();

export { isDashboardV2Response, selectedRange };
