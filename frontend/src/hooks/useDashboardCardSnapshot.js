import { useCallback, useEffect, useRef, useState } from "react";
import api from "../lib/api";
import {
    DASHBOARD_CARD_REQUEST_TIMEOUT_MS,
    dashboardCardQuery,
    nextDashboardCardReadAt,
    validDashboardCardSnapshot,
} from "../services/dashboardCardSnapshot";

const resolved = () => Promise.resolve();

function canReadAutomatically() {
    return !document.hidden && navigator.onLine !== false;
}

/** Card-local snapshots cannot leak into another period or authenticated mount. */
export default function useDashboardCardSnapshot({
    endpoint,
    filters,
    intervalMs,
    errorMessage = "تعذر تحديث بيانات البطاقة",
    apiClient = api,
}) {
    const query = dashboardCardQuery(filters);
    const key = `${endpoint}?${query}`;
    const [state, setState] = useState({ key: null, data: null, loading: false, error: null });
    const refreshRef = useRef(resolved);
    const refresh = useCallback(() => refreshRef.current(true), []);

    useEffect(() => {
        let active = true;
        let inFlight = null;
        let controller = null;
        let timer = null;
        let dueAt = 0;

        const schedule = () => {
            window.clearTimeout(timer);
            if (active) timer = window.setTimeout(() => { void load(false); }, Math.max(1000, dueAt - Date.now()));
        };
        const load = (manual = false) => {
            if (!active) return Promise.resolve();
            if (inFlight) return inFlight;
            if (!manual && (!canReadAutomatically() || Date.now() < dueAt)) return Promise.resolve();
            window.clearTimeout(timer);
            controller = new AbortController();
            setState((previous) => ({
                key,
                data: previous.key === key ? previous.data : null,
                loading: true,
                error: null,
            }));
            inFlight = Promise.resolve().then(async () => {
                if (!active) return;
                try {
                    const response = await apiClient.get(`${key}${manual ? `${query ? "&" : ""}refresh=true` : ""}`, {
                        timeout: DASHBOARD_CARD_REQUEST_TIMEOUT_MS,
                        _mezanAuthDeadlineAt: Date.now() + DASHBOARD_CARD_REQUEST_TIMEOUT_MS,
                        signal: controller.signal,
                    });
                    if (!active) return;
                    if (!validDashboardCardSnapshot(endpoint, response?.data, intervalMs)) {
                        throw new Error("invalid_dashboard_card_snapshot");
                    }
                    dueAt = nextDashboardCardReadAt(response.data, intervalMs);
                    setState({ key, data: response.data, loading: false, error: null });
                } catch {
                    if (!active) return;
                    // Failed work must not quietly restore a one-minute loop.
                    // The card's explicit refresh remains available immediately.
                    dueAt = Date.now() + intervalMs;
                    setState((previous) => ({
                        key,
                        data: previous.key === key ? previous.data : null,
                        loading: false,
                        error: errorMessage,
                    }));
                } finally {
                    inFlight = null;
                    if (active) schedule();
                }
            });
            return inFlight;
        };

        refreshRef.current = load;
        setState({ key, data: null, loading: false, error: null });
        void load(false);
        const resume = () => { void load(false); };
        window.addEventListener("focus", resume);
        window.addEventListener("online", resume);
        document.addEventListener("visibilitychange", resume);
        return () => {
            active = false;
            window.clearTimeout(timer);
            controller?.abort();
            window.removeEventListener("focus", resume);
            window.removeEventListener("online", resume);
            document.removeEventListener("visibilitychange", resume);
            if (refreshRef.current === load) refreshRef.current = resolved;
        };
    }, [endpoint, key, query, intervalMs, apiClient, errorMessage]);

    return state.key === key
        ? { ...state, refresh }
        : { key, data: null, loading: true, error: null, refresh };
}
