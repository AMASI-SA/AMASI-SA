const DASHBOARD_ROOT_PATHS = new Set(["/dashboard", "/dashboard-v2"]);

function pathOnly(input = "") {
    return String(input || "")
        .split("?", 1)[0]
        .replace(/\/+$/, "");
}

function queryParams(config = {}) {
    const params = new URLSearchParams();
    const raw = String(config?.url || "");
    const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
    new URLSearchParams(query).forEach((value, key) => {
        if (key !== "_refresh") params.set(key, value);
    });
    if (config?.params && typeof config.params === "object") {
        Object.entries(config.params).forEach(([key, value]) => {
            if (
                key !== "_refresh"
                && value !== null
                && value !== undefined
                && value !== ""
            ) {
                params.set(key, String(value));
            }
        });
    }
    return params;
}

export function isDashboardRootRequest(config = {}) {
    const method = String(config?.method || "get").toLowerCase();
    return method === "get" && DASHBOARD_ROOT_PATHS.has(pathOnly(config?.url));
}

export function dashboardRequestRangeKey(config = {}) {
    const params = queryParams(config);
    const entries = [...params.entries()]
        .sort(([left], [right]) => left.localeCompare(right));
    const query = entries
        .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
        .join("&");
    return `${pathOnly(config?.url)}${query ? `?${query}` : ""}`;
}

export function stripDashboardCacheBuster(config = {}) {
    if (!isDashboardRootRequest(config)) return config;
    const raw = String(config?.url || "");
    const [path, query = ""] = raw.split("?", 2);
    const params = new URLSearchParams(query);
    params.delete("_refresh");
    return {
        ...config,
        url: `${path}${params.toString() ? `?${params.toString()}` : ""}`,
    };
}

/**
 * Share identical dashboard reads and make the newest selected range
 * authoritative without a second waiter queue.
 *
 * A stale caller chains directly to the newest real request promise. Therefore
 * it cannot remain pending after that request settles, and identical focus /
 * visibility / mount refreshes create one HTTP request instead of a fan-out.
 */
export function createDashboardRequestCoordinator() {
    let sequence = 0;
    let latestEntry = null;
    const inFlightByKey = new Map();

    const run = (config, load) => {
        if (!isDashboardRootRequest(config)) {
            return Promise.resolve().then(load);
        }

        const key = dashboardRequestRangeKey(config);
        const existing = inFlightByKey.get(key);
        if (existing) {
            existing.sharedCallers += 1;
            return existing.publicPromise;
        }

        const entry = {
            id: ++sequence,
            key,
            sharedCallers: 0,
            rawPromise: null,
            publicPromise: null,
        };
        latestEntry = entry;
        entry.rawPromise = Promise.resolve().then(load);
        entry.publicPromise = entry.rawPromise.then(
            (response) => {
                if (latestEntry === entry) return response;
                return latestEntry.publicPromise;
            },
            (error) => {
                if (latestEntry !== entry) return latestEntry.publicPromise;
                throw error;
            },
        ).finally(() => {
            if (inFlightByKey.get(key) === entry) {
                inFlightByKey.delete(key);
            }
        });
        inFlightByKey.set(key, entry);
        return entry.publicPromise;
    };

    return {
        run,
        snapshot() {
            return {
                latestId: latestEntry?.id || 0,
                latestKey: latestEntry?.key || null,
                inFlight: inFlightByKey.size,
                sharedCallers: [...inFlightByKey.values()]
                    .reduce((total, entry) => total + entry.sharedCallers, 0),
            };
        },
    };
}

// Kept as a compatibility export for older imports. New code should use the
// request coordinator, which has no detached waiter queue.
export function createLatestResponseBroker() {
    let latest = null;
    return {
        begin(meta = {}) {
            latest = {
                id: Number(latest?.id || 0) + 1,
                rangeKey: String(meta.rangeKey || ""),
            };
            return latest;
        },
        isLatest(token) {
            return Boolean(token && latest && token.id === latest.id);
        },
        resolve(_token, response) {
            return Promise.resolve(response);
        },
        reject(_token, error) {
            return Promise.reject(error);
        },
        snapshot() {
            return {
                latestId: latest?.id || 0,
                latestRangeKey: latest?.rangeKey || null,
                waiting: 0,
            };
        },
    };
}
