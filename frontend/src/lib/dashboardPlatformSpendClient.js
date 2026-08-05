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
    if (cached && now - cached.loadedAt <= Math.max(0, Number(maxAgeMs) || 0)) {
        return cached.payload;
    }
    if (inFlight.has(key)) return inFlight.get(key);

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

    inFlight.set(key, request);
    try {
        return await request;
    } finally {
        if (inFlight.get(key) === request) inFlight.delete(key);
    }
}

export function clearDashboardPlatformSpendCache() {
    cache.clear();
    inFlight.clear();
}

export { DEFAULT_MAX_AGE_MS };
