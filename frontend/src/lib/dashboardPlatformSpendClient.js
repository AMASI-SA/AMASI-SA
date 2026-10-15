import axios from "axios";

const API_BASE = `${process.env.REACT_APP_BACKEND_URL}/api`;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DEFAULT_MAX_AGE_MS = 4 * 60 * 1000;

const cache = new Map();
const inFlight = new Map();

function accessToken() {
    try {
        return localStorage.getItem("access_token");
    } catch {
        return null;
    }
}

function headers() {
    const token = accessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
}

function rangeKey(dateFrom, dateTo) {
    return `${dateFrom}:${dateTo}`;
}

function requestKey(key, refresh) {
    return `${key}:${refresh ? "refresh" : "read"}`;
}

function validateRange(dateFrom, dateTo) {
    if (!ISO_DATE_RE.test(dateFrom || "") || !ISO_DATE_RE.test(dateTo || "")) {
        throw new Error("invalid_dashboard_platform_spend_range");
    }
    if (dateTo < dateFrom) {
        throw new Error("invalid_dashboard_platform_spend_range");
    }
}

export async function loadDashboardPlatformSpend({
    dateFrom,
    dateTo,
    refresh = true,
    maxAgeMs = DEFAULT_MAX_AGE_MS,
} = {}) {
    const safeFrom = String(dateFrom || "").trim();
    const safeTo = String(dateTo || dateFrom || "").trim();
    validateRange(safeFrom, safeTo);

    const key = rangeKey(safeFrom, safeTo);
    const cached = cache.get(key);
    const now = Date.now();

    // A user-triggered or Dashboard-triggered refresh must never reuse a stale
    // payload. The old behavior returned the four-minute cache even when
    // refresh=true, which let the yellow card show fresh Google Ads spend while
    // the executive summary kept an older zero value for the same date.
    if (
        !refresh
        && cached
        && now - cached.loadedAt <= Math.max(0, Number(maxAgeMs) || 0)
    ) {
        return cached.payload;
    }

    const refreshKey = requestKey(key, true);
    const readKey = requestKey(key, false);
    if (refresh) {
        if (inFlight.has(refreshKey)) return inFlight.get(refreshKey);
    } else {
        // A fresh provider refresh is stronger than a saved read. Reuse it so
        // the card and executive summary receive exactly the same payload.
        if (inFlight.has(refreshKey)) return inFlight.get(refreshKey);
        if (inFlight.has(readKey)) return inFlight.get(readKey);
    }

    const activeKey = refresh ? refreshKey : readKey;
    const request = (async () => {
        const config = {
            withCredentials: true,
            headers: headers(),
        };
        let response;
        if (refresh) {
            response = await axios.post(
                `${API_BASE}/integrations-v2/dashboard/ads-platform-spend/refresh`,
                { date_from: safeFrom, date_to: safeTo },
                config,
            );
        } else {
            response = await axios.get(
                `${API_BASE}/integrations-v2/dashboard/ads-platform-spend`,
                {
                    ...config,
                    params: { date_from: safeFrom, date_to: safeTo },
                },
            );
        }
        const payload = response?.data && typeof response.data === "object"
            ? response.data
            : {};
        cache.set(key, { loadedAt: Date.now(), payload });
        return payload;
    })();

    inFlight.set(activeKey, request);
    try {
        return await request;
    } finally {
        if (inFlight.get(activeKey) === request) inFlight.delete(activeKey);
    }
}

export function clearDashboardPlatformSpendCache() {
    cache.clear();
    inFlight.clear();
}

export { DEFAULT_MAX_AGE_MS };
