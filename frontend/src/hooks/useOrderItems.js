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

    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const load = useCallback(async () => {
        const normalizedOrderNumber =
            String(orderNumber || "").trim();

        const requestId = ++requestIdRef.current;

        if (!normalizedOrderNumber) {
            setItems([]);
            setError("رقم الطلب مطلوب.");
            setLoading(false);
            return;
        }

        setLoading(true);
        setError("");

        try {
            const result = await getOrderItems(
                normalizedOrderNumber
            );

            if (requestId !== requestIdRef.current) {
                return;
            }

            setItems(uniqueOrderItems(result));
        } catch (loadError) {
            if (requestId !== requestIdRef.current) {
                return;
            }

            setItems([]);
            setError(loadError.message);
        } finally {
            if (requestId === requestIdRef.current) {
                setLoading(false);
            }
        }
    }, [orderNumber]);

    useEffect(() => {
        load();

        return () => {
            requestIdRef.current += 1;
        };
    }, [load]);

    return {
        items,
        loading,
        error,
        reload: load,
    };
}

export function useOrderItem(
    orderNumber,
    orderItemId
) {
    const requestIdRef = useRef(0);

    const [item, setItem] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const load = useCallback(async () => {
        const requestId = ++requestIdRef.current;

        setLoading(true);
        setError("");
        setItem(null);

        try {
            const result = await getOrderItem(
                orderNumber,
                orderItemId
            );

            if (requestId !== requestIdRef.current) {
                return;
            }

            setItem(result);
        } catch (loadError) {
            if (requestId !== requestIdRef.current) {
                return;
            }

            setError(loadError.message);
        } finally {
            if (requestId === requestIdRef.current) {
                setLoading(false);
            }
        }
    }, [orderItemId, orderNumber]);

    useEffect(() => {
        load();

        return () => {
            requestIdRef.current += 1;
        };
    }, [load]);

    return {
        item,
        loading,
        error,
        reload: load,
    };
}
