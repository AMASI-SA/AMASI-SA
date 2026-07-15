import {
    useCallback,
    useEffect,
    useRef,
    useState,
} from "react";

import {
    getOrderItem,
    getOrderItems,
} from "../services/orderItemEngine";

const ORDER_ITEM_REFRESH_INTERVAL_MS = 10_000;

function uniqueOrderItems(rows) {
    const unique = new Map();

    for (const item of rows || []) {
        const key = String(
            item?.order_item_id || ""
        ).trim();

        if (key && !unique.has(key)) {
            unique.set(key, item);
        }
    }

    return Array.from(unique.values());
}

export function useOrderItems(orderNumber) {
    const requestIdRef = useRef(0);
    const requestInFlightRef = useRef(false);
    const mountedRef = useRef(true);

    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const load = useCallback(async ({ background = false } = {}) => {
        const normalizedOrderNumber =
            String(orderNumber || "").trim();

        if (!normalizedOrderNumber) {
            setItems([]);
            setError("رقم الطلب مطلوب.");
            setLoading(false);
            return;
        }

        if (requestInFlightRef.current) return;

        requestInFlightRef.current = true;
        const requestId = ++requestIdRef.current;

        if (!background) {
            setLoading(true);
            setError("");
        }

        try {
            const result = await getOrderItems(
                normalizedOrderNumber
            );

            if (
                !mountedRef.current ||
                requestId !== requestIdRef.current
            ) {
                return;
            }

            setItems(uniqueOrderItems(result));
            setError("");
        } catch (loadError) {
            if (
                !mountedRef.current ||
                requestId !== requestIdRef.current
            ) {
                return;
            }

            if (!background) setItems([]);
            setError(loadError.message);
        } finally {
            requestInFlightRef.current = false;
            if (
                mountedRef.current &&
                requestId === requestIdRef.current &&
                !background
            ) {
                setLoading(false);
            }
        }
    }, [orderNumber]);

    useEffect(() => {
        mountedRef.current = true;
        load();

        const refresh = () => {
            if (!document.hidden && navigator.onLine) {
                load({ background: true });
            }
        };

        const intervalId = window.setInterval(
            refresh,
            ORDER_ITEM_REFRESH_INTERVAL_MS
        );

        window.addEventListener("focus", refresh);
        window.addEventListener("online", refresh);
        document.addEventListener("visibilitychange", refresh);

        return () => {
            mountedRef.current = false;
            requestIdRef.current += 1;
            window.clearInterval(intervalId);
            window.removeEventListener("focus", refresh);
            window.removeEventListener("online", refresh);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [load]);

    return {
        items,
        loading,
        error,
        reload: () => load(),
        refresh: () => load({ background: true }),
    };
}

export function useOrderItem(
    orderNumber,
    orderItemId
) {
    const requestIdRef = useRef(0);
    const requestInFlightRef = useRef(false);
    const mountedRef = useRef(true);

    const [item, setItem] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const load = useCallback(async ({ background = false } = {}) => {
        if (requestInFlightRef.current) return;

        requestInFlightRef.current = true;
        const requestId = ++requestIdRef.current;

        if (!background) {
            setLoading(true);
            setError("");
        }

        try {
            const result = await getOrderItem(
                orderNumber,
                orderItemId
            );

            if (
                !mountedRef.current ||
                requestId !== requestIdRef.current
            ) {
                return;
            }

            setItem(result);
            setError("");
        } catch (loadError) {
            if (
                !mountedRef.current ||
                requestId !== requestIdRef.current
            ) {
                return;
            }

            setError(loadError.message);
        } finally {
            requestInFlightRef.current = false;
            if (
                mountedRef.current &&
                requestId === requestIdRef.current &&
                !background
            ) {
                setLoading(false);
            }
        }
    }, [orderItemId, orderNumber]);

    useEffect(() => {
        mountedRef.current = true;
        load();

        const refresh = () => {
            if (!document.hidden && navigator.onLine) {
                load({ background: true });
            }
        };

        const intervalId = window.setInterval(
            refresh,
            ORDER_ITEM_REFRESH_INTERVAL_MS
        );

        window.addEventListener("focus", refresh);
        window.addEventListener("online", refresh);
        document.addEventListener("visibilitychange", refresh);

        return () => {
            mountedRef.current = false;
            requestIdRef.current += 1;
            window.clearInterval(intervalId);
            window.removeEventListener("focus", refresh);
            window.removeEventListener("online", refresh);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [load]);

    return {
        item,
        loading,
        error,
        reload: () => load(),
        refresh: () => load({ background: true }),
    };
}
