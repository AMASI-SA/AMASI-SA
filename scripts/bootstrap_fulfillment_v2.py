"""One-shot branch wiring for the Fulfillment V2 workspace.

This temporary script is removed before merge. It edits only the central route,
sidebar, stage-one review page, and stage-one review backend.
"""
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    print(f"PATCH {path}: matches={count} marker={old.splitlines()[0]!r}", flush=True)
    if count != 1:
        raise SystemExit(f"Expected exactly one match in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "frontend/src/App.js",
    'import OrderReview from "./pages/OrderReview";',
    'import FulfillmentV2 from "./pages/FulfillmentV2";',
)

replace_once(
    "frontend/src/App.js",
    '''            <Route
                path="/order-review"
                element={<ProtectedRoute><Layout><OrderReview /></Layout></ProtectedRoute>}
            />''',
    '''            <Route
                path="/fulfillment-v2"
                element={<ProtectedRoute><Layout><FulfillmentV2 /></Layout></ProtectedRoute>}
            />
            <Route
                path="/order-review"
                element={<ProtectedRoute><Navigate to="/fulfillment-v2?stage=pending_review" replace /></ProtectedRoute>}
            />''',
)

replace_once(
    "frontend/src/components/Sidebar.jsx",
    '''            {
                to: "/orders-v2",
                label: "الطلبات",
                icon: Package,
                testid: "nav-mezan-os-orders",
            },
            {
                to: "/products-v2",''',
    '''            {
                to: "/orders-v2",
                label: "الطلبات",
                icon: Package,
                testid: "nav-mezan-os-orders",
            },
            {
                to: "/fulfillment-v2",
                label: "إدارة رفع الطلبات",
                icon: Queue,
                testid: "nav-mezan-os-fulfillment",
            },
            {
                to: "/products-v2",''',
)

replace_once(
    "backend/order_review_routes.py",
    "from salla_integration.service import SallaError, call_salla",
    "from salla_integration.auto_sync import schedule_salla_auto_sync\n"
    "from salla_integration.service import SallaError, call_salla",
)

replace_once(
    "backend/order_review_routes.py",
    '''        reviewer = _require_reviewer(user)
        merchant_id = _merchant_user_id(reviewer)
        try:
            page = await list_orders(''',
    '''        reviewer = _require_reviewer(user)
        merchant_id = _merchant_user_id(reviewer)
        # Non-blocking, throttled Salla Direct ingestion. It reads only the
        # light order list and order items, performs no Qoyod API calls, and
        # never delays the local queue response.
        schedule_salla_auto_sync(db, merchant_id)
        try:
            page = await list_orders(''',
)

replace_once(
    "frontend/src/pages/OrderReview.jsx",
    '''    const load = useCallback(async ({ cursor = null, append = false } = {}) => {
        setLoading(true);
        setError("");
        try {
            const result = await listPendingOrderReviews({ limit: 50, cursor });
            setOrders((current) => {
                if (!append) return result.items;
                const rows = new Map(current.map((order) => [order.order_number, order]));
                result.items.forEach((order) => rows.set(order.order_number, order));
                return Array.from(rows.values());
            });
            setNextCursor(result.nextCursor);
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);''',
    '''    const load = useCallback(async ({ cursor = null, append = false, background = false } = {}) => {
        if (!background) setLoading(true);
        setError("");
        try {
            const result = await listPendingOrderReviews({ limit: 50, cursor });
            setOrders((current) => {
                if (!append) return result.items;
                const rows = new Map(current.map((order) => [order.order_number, order]));
                result.items.forEach((order) => rows.set(order.order_number, order));
                return Array.from(rows.values());
            });
            setNextCursor(result.nextCursor);
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            if (!background) setLoading(false);
        }
    }, []);

    useEffect(() => {
        load();
        const refresh = () => {
            if (document.hidden || !navigator.onLine) return;
            load({ background: true });
        };
        const intervalId = window.setInterval(refresh, 10_000);
        window.addEventListener("focus", refresh);
        window.addEventListener("online", refresh);
        document.addEventListener("visibilitychange", refresh);
        return () => {
            window.clearInterval(intervalId);
            window.removeEventListener("focus", refresh);
            window.removeEventListener("online", refresh);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [load]);''',
)

print("Fulfillment V2 wiring completed.", flush=True)
