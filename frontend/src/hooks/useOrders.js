import { useCallback, useEffect, useRef, useState } from "react";

import {
    getOrder,
    listOrders,
    ORDER_PAGE_SIZE,
} from "../services/orderEngine";

const ORDER_REFRESH_INTERVAL_MS = 10_000;

function uniqueOrders(rows) {
    const unique = new Map();
    for (const order of rows || []) {
        const key = String(order?.order_number || "").trim();
        if (key && !unique.has(key)) unique.set(key, order);
    }
    return Array.from(unique.values());
}

export function useOrders({ statusGroup = null, statusExact = null } = {}) {
    const requestIdRef = useRef(0);
    const refreshInFlightRef = useRef(false);
    const ordersRef = useRef([]);
    const searchModeRef = useRef(false);

    const [orders, setOrders] = useState([]);
    const [nextCursor, setNextCursor] = useState(null);
    const [hasMore, setHasMore] = useState(true);
    const [loading, setLoading] = useState(false);
    const [initialLoading, setInitialLoading] = useState(true);
    const [error, setError] = useState("");
    const [searchMode, setSearchMode] = useState(false);
    const [resultSummary, setResultSummary] = useState(null);

    useEffect(() => { ordersRef.current = orders; }, [orders]);
    useEffect(() => { searchModeRef.current = searchMode; }, [searchMode]);

    const loadFirstPage = useCallback(async ({ background = false } = {}) => {
        if (background && refreshInFlightRef.current) return;
        const requestId = ++requestIdRef.current;
        refreshInFlightRef.current = true;

        if (!background) {
            setOrders([]);
            setNextCursor(null);
            setHasMore(false);
            setInitialLoading(true);
            setLoading(true);
            setError("");
            setSearchMode(false);
            setResultSummary(null);
        }

        try {
            const result = await listOrders({
                limit: ORDER_PAGE_SIZE,
                statusGroup,
                statusExact,
            });
            if (requestId !== requestIdRef.current) return;
            const refreshedOrders = uniqueOrders(result.items);
            if (background) {
                // Exact-status pages must never retain rows that moved to another state.
                if (statusExact || statusGroup) {
                    setOrders(refreshedOrders);
                } else {
                    setOrders((current) => {
                        const keys = new Set(refreshedOrders.map((order) => String(order.order_number)));
                        const retained = current.filter((order) => !keys.has(String(order.order_number)));
                        return uniqueOrders([...refreshedOrders, ...retained]);
                    });
                }
            } else {
                setOrders(refreshedOrders);
                setNextCursor(result.nextCursor);
                setHasMore(Boolean(result.nextCursor));
            }
        } catch (loadError) {
            if (requestId !== requestIdRef.current) return;
            if (!background) {
                setOrders([]);
                setNextCursor(null);
                setHasMore(false);
            }
            setError(loadError.message);
        } finally {
            if (requestId === requestIdRef.current) {
                if (!background) {
                    setLoading(false);
                    setInitialLoading(false);
                }
                refreshInFlightRef.current = false;
            }
        }
    }, [statusExact, statusGroup]);

    useEffect(() => { loadFirstPage(); }, [loadFirstPage]);

    const loadMore = useCallback(async () => {
        if (loading || initialLoading || searchMode || !hasMore || !nextCursor || refreshInFlightRef.current) return;
        refreshInFlightRef.current = true;
        setLoading(true);
        setError("");
        try {
            const result = await listOrders({
                limit: ORDER_PAGE_SIZE,
                cursor: nextCursor,
                statusGroup,
                statusExact,
            });
            setOrders((current) => uniqueOrders([...current, ...result.items]));
            setNextCursor(result.nextCursor);
            setHasMore(Boolean(result.nextCursor));
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
            refreshInFlightRef.current = false;
        }
    }, [hasMore, initialLoading, loading, nextCursor, searchMode, statusExact, statusGroup]);

    const searchExactOrder = useCallback(async (orderNumber) => {
        const normalized = String(orderNumber || "").trim();
        if (!normalized) {
            await loadFirstPage();
            return;
        }
        const requestId = ++requestIdRef.current;
        refreshInFlightRef.current = true;
        setInitialLoading(true);
        setLoading(true);
        setError("");
        setSearchMode(true);
        try {
            const order = await getOrder(normalized);
            if (requestId !== requestIdRef.current) return;
            setOrders(order ? [order] : []);
            setNextCursor(null);
            setHasMore(false);
        } catch (searchError) {
            if (requestId !== requestIdRef.current) return;
            setOrders([]);
            setNextCursor(null);
            setHasMore(false);
            setError(searchError.message);
        } finally {
            if (requestId === requestIdRef.current) {
                setLoading(false);
                setInitialLoading(false);
                refreshInFlightRef.current = false;
            }
        }
    }, [loadFirstPage]);

    const searchCombined = useCallback(async (filters) => {
        const requestId = ++requestIdRef.current;
        refreshInFlightRef.current = true;
        setInitialLoading(true);
        setLoading(true);
        setError("");
        setSearchMode(true);
        try {
            const result = await listOrders({ limit: 50, filters });
            if (requestId !== requestIdRef.current) return;
            setOrders(uniqueOrders(result.items));
            setResultSummary(result.resultSummary);
            setNextCursor(null);
            setHasMore(false);
        } catch (searchError) {
            if (requestId !== requestIdRef.current) return;
            setOrders([]);
            setResultSummary(null);
            setError(searchError.message);
        } finally {
            if (requestId === requestIdRef.current) {
                setLoading(false);
                setInitialLoading(false);
                refreshInFlightRef.current = false;
            }
        }
    }, []);

    const refreshVisibleOrders = useCallback(async () => {
        if (
            refreshInFlightRef.current ||
            (typeof document !== "undefined" && document.hidden) ||
            (typeof navigator !== "undefined" && !navigator.onLine)
        ) return;
        if (searchModeRef.current) {
            const orderNumber = String(ordersRef.current[0]?.order_number || "").trim();
            if (!orderNumber) return;
            refreshInFlightRef.current = true;
            try {
                const order = await getOrder(orderNumber);
                if (order) setOrders([order]);
                setError("");
            } catch (refreshError) {
                setError(refreshError.message);
            } finally {
                refreshInFlightRef.current = false;
            }
            return;
        }
        await loadFirstPage({ background: true });
    }, [loadFirstPage]);

    useEffect(() => {
        const intervalId = window.setInterval(refreshVisibleOrders, ORDER_REFRESH_INTERVAL_MS);
        const handleFocus = () => refreshVisibleOrders();
        const handleVisibilityChange = () => { if (!document.hidden) refreshVisibleOrders(); };
        window.addEventListener("focus", handleFocus);
        window.addEventListener("online", handleFocus);
        document.addEventListener("visibilitychange", handleVisibilityChange);
        return () => {
            window.clearInterval(intervalId);
            window.removeEventListener("focus", handleFocus);
            window.removeEventListener("online", handleFocus);
            document.removeEventListener("visibilitychange", handleVisibilityChange);
        };
    }, [refreshVisibleOrders]);

    return {
        orders,
        hasMore,
        loading,
        initialLoading,
        error,
        searchMode,
        loadMore,
        reload: () => loadFirstPage(),
        refresh: refreshVisibleOrders,
        searchExactOrder,
        searchCombined,
        resultSummary,
    };
}

export function useOrder(orderNumber) {
    const requestInFlightRef = useRef(false);
    const mountedRef = useRef(true);
    const [order, setOrder] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const load = useCallback(async ({ background = false } = {}) => {
        const normalized = String(orderNumber || "").trim();
        if (!normalized || requestInFlightRef.current) return;
        requestInFlightRef.current = true;
        if (!background) {
            setLoading(true);
            setError("");
        }
        try {
            // Read the local unified order only. Opening or refreshing this page
            // must never call Salla API, because a lighter API snapshot can
            // overwrite richer shipping fields already saved from webhooks.
            const result = await getOrder(normalized);
            if (mountedRef.current) {
                setOrder(result);
                setError("");
            }
        } catch (loadError) {
            if (mountedRef.current) setError(loadError.message);
        } finally {
            requestInFlightRef.current = false;
            if (mountedRef.current && !background) setLoading(false);
        }
    }, [orderNumber]);

    useEffect(() => {
        mountedRef.current = true;
        load();
        const refresh = () => {
            if (!document.hidden && navigator.onLine) load({ background: true });
        };
        const intervalId = window.setInterval(refresh, ORDER_REFRESH_INTERVAL_MS);
        window.addEventListener("focus", refresh);
        window.addEventListener("online", refresh);
        document.addEventListener("visibilitychange", refresh);
        return () => {
            mountedRef.current = false;
            window.clearInterval(intervalId);
            window.removeEventListener("focus", refresh);
            window.removeEventListener("online", refresh);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [load]);

    return { order, loading, error, reload: () => load(), refresh: () => load({ background: true }) };
}
