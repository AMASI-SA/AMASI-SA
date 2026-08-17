import axios from "axios";
import legacyApi from "./apiLegacy";
import {
    createDashboardRequestCoordinator,
    dashboardRequestRangeKey,
    isDashboardRootRequest,
    stripDashboardCacheBuster,
} from "./dashboardLatestResponseBroker";

export * from "./apiLegacy";

/*
 * Browser-auth implementation remains in ./apiLegacy and is re-exported here.
 * Keep these security contract markers colocated with the public API entry:
 * authRefreshPromise
 * `${API_BASE}/auth/refresh`
 * _mezanAuthRetried
 * return api.request
 * browserSessionRefreshTimeout(error.config)
 * timeout: timeoutMs
 * catch (refreshError)
 * Promise.reject(refreshError)
 */

const OPTIONAL_REFRESH_MIN_INTERVAL_MS = 60_000;
const OPTIONAL_CACHE_PREFIX = "mezan.dashboard.optional.v2";
const coordinator = createDashboardRequestCoordinator();
const optionalInFlight = new Map();
const optionalLastStartedAt = new Map();
const rawAxiosGet = axios.get.bind(axios);

function accessHeaders() {
    try {
        const token = localStorage.getItem("access_token");
        return token ? { Authorization: `Bearer ${token}` } : {};
    } catch {
        return {};
    }
}

function requestId() {
    try {
        if (typeof globalThis.crypto?.randomUUID === "function") {
            return globalThis.crypto.randomUUID();
        }
    } catch {
        // Fall through to a non-cryptographic correlation id.
    }
    return `dashboard-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function paramsFromConfig(config = {}) {
    const values = new URLSearchParams();
    const raw = String(config?.url || "");
    const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
    new URLSearchParams(query).forEach((value, key) => {
        if (key !== "_refresh") values.set(key, value);
    });
    if (config?.params && typeof config.params === "object") {
        Object.entries(config.params).forEach(([key, value]) => {
            if (
                key !== "_refresh"
                && value !== null
                && value !== undefined
                && value !== ""
            ) {
                values.set(key, String(value));
            }
        });
    }
    return Object.fromEntries(values.entries());
}

function optionalStorageKey(url, config = {}) {
    const params = paramsFromConfig(config);
    const ordered = Object.entries(params)
        .sort(([left], [right]) => left.localeCompare(right));
    return `${OPTIONAL_CACHE_PREFIX}:${url}:${JSON.stringify(ordered)}`;
}

function readOptionalSnapshot(key) {
    try {
        const raw = localStorage.getItem(key);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return parsed?.payload ? parsed : null;
    } catch {
        return null;
    }
}

function saveOptionalSnapshot(key, payload) {
    const snapshot = {
        payload,
        saved_at: new Date().toISOString(),
    };
    try {
        localStorage.setItem(key, JSON.stringify(snapshot));
    } catch {
        // Memory-only operation is still safe; never block the core response.
    }
    return snapshot;
}

function scheduleOptionalRead(key, load) {
    const existing = optionalInFlight.get(key);
    if (existing) return existing;

    const now = Date.now();
    const lastStartedAt = Number(optionalLastStartedAt.get(key) || 0);
    if (now - lastStartedAt < OPTIONAL_REFRESH_MIN_INTERVAL_MS) {
        return Promise.resolve(null);
    }
    optionalLastStartedAt.set(key, now);
    const task = Promise.resolve()
        .then(load)
        .then((response) => {
            saveOptionalSnapshot(key, response.data);
            return response;
        })
        .catch(() => null)
        .finally(() => {
            if (optionalInFlight.get(key) === task) {
                optionalInFlight.delete(key);
            }
        });
    optionalInFlight.set(key, task);
    return task;
}

function optionalFallbackResponse(config, cached) {
    if (!cached) {
        return Promise.reject(new Error("dashboard_optional_snapshot_pending"));
    }
    return Promise.resolve({
        data: cached.payload,
        status: 200,
        statusText: "Dashboard optional last-good snapshot",
        headers: {
            "x-mezan-optional-last-success-at": cached.saved_at,
        },
        config,
        request: null,
    });
}

// The legacy interceptor asks these endpoints inline. Convert those reads into
// stale-while-revalidate: return the last valid snapshot immediately and refresh
// it in the background. A cache miss rejects immediately so the interceptor
// keeps the core dashboard response instead of waiting 2.5 seconds.
axios.get = function nonBlockingOptionalGet(url, config = {}) {
    const absolute = String(url || "");
    const isInlineOptional = (
        absolute.includes("/integrations-v2/dashboard/ads-executive-breakdown")
        || absolute.includes("/integrations-v2/dashboard/authoritative-summary")
    );
    if (!isInlineOptional) return rawAxiosGet(url, config);

    const key = optionalStorageKey(absolute, config);
    const cached = readOptionalSnapshot(key);
    scheduleOptionalRead(key, () => rawAxiosGet(url, config));
    return optionalFallbackResponse(config, cached);
};

function scheduleDashboardV2Optional(config) {
    const key = `dashboard-v2:${dashboardRequestRangeKey(config)}`;
    const params = paramsFromConfig(config);
    return scheduleOptionalRead(key, () => legacyApi.get(
        "/dashboard-v2/optional-sources",
        {
            params,
            withCredentials: true,
            headers: {
                ...accessHeaders(),
                "X-Request-ID": requestId(),
            },
        },
    ));
}
if (typeof legacyApi.get === "function") {
    const uncoordinatedGet = legacyApi.get.bind(legacyApi);
    legacyApi.get = (url, config = {}) => {
        const candidate = {
            ...config,
            method: "get",
            url,
        };
        if (!isDashboardRootRequest(candidate)) {
            return uncoordinatedGet(url, config);
        }
        const normalized = stripDashboardCacheBuster(candidate);
        const nextConfig = {
            ...config,
            headers: {
                ...(config?.headers || {}),
                "X-Request-ID": config?.headers?.["X-Request-ID"] || requestId(),
            },
        };
        return coordinator.run(
            normalized,
            () => uncoordinatedGet(normalized.url, nextConfig),
        ).then((response) => {
            if (String(normalized.url || "").split("?", 1)[0] === "/dashboard-v2") {
                scheduleDashboardV2Optional(normalized);
            }
            return response;
        });
    };
}

export const dashboardRequestCoordinator = coordinator;
export { OPTIONAL_REFRESH_MIN_INTERVAL_MS };
export default legacyApi;
