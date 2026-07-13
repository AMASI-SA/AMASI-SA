import {
    useCallback,
    useEffect,
    useRef,
    useState,
} from "react";

import {
    getOrder,
    listOrders,
    ORDER_PAGE_SIZE,
} from "../services/orderEngine";

function uniqueOrders(rows) {
    const unique = new Map();

    for (const order of rows) {
        const key = String(order?.order_number || "").trim();

        if (key && !unique.has(key)) {
            unique.set(key, order);
        }
    }

    return Array.from(unique.values());
}

export function useOrders() {
    const requestIdRef = useRef(0);

    const [orders, setOrders] = useState([]);
    const [nextCursor, setNextCursor] = useState(null);
    const [hasMore, setHasMore] = useState(true);
    const [loading, setLoading] = useState(false);
    const [initialLoading, setInitialLoading] = useState(true);
    const [error, setError] = useState("");
    const [searchMode, setSearchMode] = useState(false);

    const loadFirstPage = useCallback(async () => {
        const requestId = ++requestIdRef.current;

        setInitialLoading(true);
        setLoading(true);
        setError("");
        setSearchMode(false);

        try {
            const result = await listOrders({
                limit: ORDER_PAGE_SIZE,
            });

            if (requestId !== requestIdRef.current) return;

            setOrders(uniqueOrders(result.items));
            setNextCursor(result.nextCursor);
            setHasMore(Boolean(result.nextCursor));
        } catch (loadError) {
            if (requestId !== requestIdRef.current) return;

            setOrders([]);
            setNextCursor(null);
            setHasMore(false);
            setError(loadError.message);
        } finally {
            if (requestId === requestIdRef.current) {
                setLoading(false);
                setInitialLoading(false);
            }
        }
    }, []);

    useEffect(() => {
        loadFirstPage();
    }, [loadFirstPage]);

    const loadMore = useCallback(async () => {
        if (
            loading ||
            initialLoading ||
            searchMode ||
            !hasMore ||
            !nextCursor
        ) {
            return;
        }

        setLoading(true);
        setError("");

        try {
            const result = await listOrders({
                limit: ORDER_PAGE_SIZE,
                cursor: nextCursor,
            });

            setOrders((current) =>
                uniqueOrders([...current, ...result.items])
            );
            setNextCursor(result.nextCursor);
            setHasMore(Boolean(result.nextCursor));
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, [
        hasMore,
        initialLoading,
        loading,
        nextCursor,
        searchMode,
    ]);

    const searchExactOrder = useCallback(async (orderNumber) => {
        const normalized = String(orderNumber || "").trim();

        if (!normalized) {
            await loadFirstPage();
            return;
        }

        const requestId = ++requestIdRef.current;

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
            }
        }
    }, [loadFirstPage]);

    return {
        orders,
        hasMore,
        loading,
        initialLoading,
        error,
        searchMode,
        loadMore,
        reload: loadFirstPage,
        searchExactOrder,
    };
}

export function useOrder(orderNumber) {
    const [order, setOrder] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let cancelled = false;

        async function load() {
            setLoading(true);
            setError("");
            setOrder(null);

            try {
                const result = await getOrder(orderNumber);

                if (!cancelled) {
                    setOrder(result);
                }
            } catch (loadError) {
                if (!cancelled) {
                    setError(loadError.message);
                }
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        }

        load();

        return () => {
            cancelled = true;
        };
    }, [orderNumber]);

    return {
        order,
        loading,
        error,
    };
}
