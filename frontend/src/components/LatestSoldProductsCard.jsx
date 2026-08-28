import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, PackageOpen } from "lucide-react";
import {
    listLatestSoldProductOrders,
    ORDER_PAGE_SIZE,
} from "../services/orderEngine";

const ORDERS_PER_PAGE = 5;
const AUTO_REFRESH_INTERVAL_MS = 10_000;

function orderNumberValue(order) {
    const value = Number(order?.order_number || 0);
    return Number.isFinite(value) ? value : 0;
}

function orderTimestamp(order) {
    const value = order?.created_at || order?.order_date;
    const timestamp = new Date(value || 0).getTime();
    return Number.isFinite(timestamp) ? timestamp : 0;
}

export function sortOrdersNewestFirst(orders = []) {
    return [...orders].sort((left, right) => {
        const numberDifference = orderNumberValue(right) - orderNumberValue(left);
        return numberDifference || orderTimestamp(right) - orderTimestamp(left);
    });
}

export function mergeOrdersNewestFirst(current = [], incoming = []) {
    const unique = new Map();
    [...current, ...incoming].forEach((order) => {
        const orderNumber = String(order?.order_number || "").trim();
        if (orderNumber) unique.set(orderNumber, order);
    });
    return sortOrdersNewestFirst(Array.from(unique.values()));
}

function itemQuantity(item) {
    const quantity = Math.floor(Number(item?.quantity || 1));
    return Number.isFinite(quantity) && quantity > 0 ? quantity : 1;
}

export function expandSoldProductRows(orders = []) {
    return orders.flatMap((order) => (order?.items || []).flatMap((item, itemIndex) => {
        const quantity = itemQuantity(item);
        return Array.from({ length: quantity }, (_, unitIndex) => ({
            order,
            item,
            itemIndex,
            unitIndex,
            quantity,
        }));
    }));
}

function orderTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "—";
    return new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Riyadh",
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
    }).format(date);
}

function itemDetails(item) {
    return [item?.size, item?.color, item?.material, item?.sku]
        .map((value) => String(value || "").trim())
        .filter(Boolean)
        .join(" · ");
}

function productImageUrls(item) {
    return [item?.image_url, ...(item?.image_urls || [])]
        .map((value) => String(value || "").trim())
        .filter((value, index, values) => value && values.indexOf(value) === index);
}

function ProductImage({ item }) {
    const urls = useMemo(() => productImageUrls(item), [item]);
    const signature = urls.join("|");
    const [imageIndex, setImageIndex] = useState(0);

    useEffect(() => {
        setImageIndex(0);
    }, [signature]);

    const imageUrl = urls[imageIndex] || "";
    return <div className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-100 bg-slate-50">
        {imageUrl
            ? <img
                src={imageUrl}
                alt={item?.name || ""}
                loading="lazy"
                className="h-full w-full object-cover"
                onError={() => setImageIndex((current) => current + 1)}
            />
            : <PackageOpen className="h-5 w-5 text-slate-300" />}
    </div>;
}

export default function LatestSoldProductsCard({
    orders: suppliedOrders,
    hasMoreOrders: suppliedHasMoreOrders = false,
    ordersLoading: suppliedOrdersLoading = false,
    onLoadMoreOrders,
}) {
    const usesDedicatedFeed = suppliedOrders === undefined;
    const [feedOrders, setFeedOrders] = useState([]);
    const [feedCursor, setFeedCursor] = useState(null);
    const [feedHasMore, setFeedHasMore] = useState(false);
    const [feedLoading, setFeedLoading] = useState(usesDedicatedFeed);
    const [feedError, setFeedError] = useState("");
    const [visibleOrderCount, setVisibleOrderCount] = useState(ORDERS_PER_PAGE);
    const requestInFlightRef = useRef(false);
    const loadedAdditionalPagesRef = useRef(false);
    const orders = usesDedicatedFeed ? feedOrders : suppliedOrders;
    const hasMoreOrders = usesDedicatedFeed ? feedHasMore : suppliedHasMoreOrders;
    const ordersLoading = usesDedicatedFeed ? feedLoading : suppliedOrdersLoading;
    const sortedOrders = useMemo(() => sortOrdersNewestFirst(orders), [orders]);
    const visibleOrders = sortedOrders.slice(0, visibleOrderCount);
    const rows = useMemo(() => expandSoldProductRows(visibleOrders), [visibleOrders]);
    const canShowMore = visibleOrderCount < sortedOrders.length || hasMoreOrders;

    const loadFirstPage = useCallback(async (background = false) => {
        if (!usesDedicatedFeed || requestInFlightRef.current) return;
        requestInFlightRef.current = true;
        if (!background) {
            setFeedLoading(true);
            setFeedError("");
        }
        try {
            const result = await listLatestSoldProductOrders({
                limit: ORDER_PAGE_SIZE,
            });
            setFeedOrders((current) => (
                background
                    ? mergeOrdersNewestFirst(current, result.items)
                    : sortOrdersNewestFirst(result.items)
            ));
            if (!background || !loadedAdditionalPagesRef.current) {
                setFeedCursor(result.nextCursor);
                setFeedHasMore(Boolean(result.nextCursor));
            }
        } catch (error) {
            if (!background) {
                setFeedOrders([]);
                setFeedCursor(null);
                setFeedHasMore(false);
                setFeedError(error.message);
            }
        } finally {
            requestInFlightRef.current = false;
            if (!background) setFeedLoading(false);
        }
    }, [usesDedicatedFeed]);

    useEffect(() => {
        if (!usesDedicatedFeed) return undefined;

        void loadFirstPage(false);
        const refresh = () => {
            if (document.visibilityState === "visible") {
                void loadFirstPage(true);
            }
        };
        const interval = window.setInterval(refresh, AUTO_REFRESH_INTERVAL_MS);
        window.addEventListener("focus", refresh);
        window.addEventListener("online", refresh);
        document.addEventListener("visibilitychange", refresh);

        return () => {
            window.clearInterval(interval);
            window.removeEventListener("focus", refresh);
            window.removeEventListener("online", refresh);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [loadFirstPage, usesDedicatedFeed]);

    const loadMoreOrders = useCallback(async () => {
        if (!usesDedicatedFeed) {
            onLoadMoreOrders?.();
            return;
        }
        if (
            requestInFlightRef.current
            || feedLoading
            || !feedHasMore
            || !feedCursor
        ) return;

        requestInFlightRef.current = true;
        setFeedLoading(true);
        setFeedError("");
        try {
            const result = await listLatestSoldProductOrders({
                limit: ORDER_PAGE_SIZE,
                cursor: feedCursor,
            });
            setFeedOrders((current) => (
                mergeOrdersNewestFirst(current, result.items)
            ));
            setFeedCursor(result.nextCursor);
            setFeedHasMore(Boolean(result.nextCursor));
            loadedAdditionalPagesRef.current = true;
        } catch (error) {
            setFeedError(error.message);
        } finally {
            requestInFlightRef.current = false;
            setFeedLoading(false);
        }
    }, [
        feedCursor,
        feedHasMore,
        feedLoading,
        onLoadMoreOrders,
        usesDedicatedFeed,
    ]);

    const showMore = () => {
        const nextVisibleCount = visibleOrderCount + ORDERS_PER_PAGE;
        setVisibleOrderCount(nextVisibleCount);
        if (
            nextVisibleCount > sortedOrders.length
            && hasMoreOrders
            && !ordersLoading
        ) {
            void loadMoreOrders();
        }
    };

    return <section data-testid="advanced-latest-sold-products" className="overflow-hidden rounded-2xl border border-emerald-200 bg-white shadow-sm">
        <div className="flex min-h-14 items-center justify-between gap-3 border-b border-emerald-800 bg-emerald-700 px-4 py-3 text-white">
            <h2 className="flex items-center gap-2 font-extrabold"><PackageOpen className="h-5 w-5" />أحدث المنتجات المباعة</h2>
            <span className="shrink-0 rounded-full bg-white/15 px-2 py-1 text-[10px] font-bold">{visibleOrders.length} طلبات</span>
        </div>

        <div
            data-testid="latest-sold-products-scroll"
            className="h-[520px] max-h-[60vh] overflow-y-auto overscroll-contain [scrollbar-gutter:stable] divide-y divide-slate-100"
        >
            {rows.length ? rows.map(({ order, item, itemIndex, unitIndex, quantity }) => {
                const orderNumber = String(order?.order_number || "");
                const details = itemDetails(item);
                const key = `${orderNumber}-${item?.order_item_id || item?.product_id || itemIndex}-${unitIndex}`;
                return <Link
                    key={key}
                    to={`/orders-v2/${encodeURIComponent(orderNumber)}?returnTo=${encodeURIComponent("/dashboard-advanced")}`}
                    dir="rtl"
                    className="flex min-h-[76px] items-center gap-3 px-4 py-3 text-right hover:bg-emerald-50/40"
                    data-testid={`latest-sold-product-${key}`}
                >
                    <ProductImage item={item} />
                    <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-extrabold text-slate-900">{item?.name || "منتج بدون اسم"}</p>
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] font-bold text-slate-400">
                            <span className="num whitespace-nowrap">طلب #{orderNumber}</span>
                            <span>•</span>
                            <time className="num whitespace-nowrap" dateTime={order?.created_at || order?.order_date || undefined}>{orderTime(order?.created_at || order?.order_date)}</time>
                            {quantity > 1 && <><span>•</span><span className="whitespace-nowrap text-emerald-700">قطعة {unitIndex + 1} من {quantity}</span></>}
                        </div>
                        {details && <p className="mt-1 truncate text-[10px] text-slate-500">{details}</p>}
                    </div>
                </Link>;
            }) : <div className="flex h-full items-center justify-center p-6 text-center text-xs font-bold text-slate-400">
                {ordersLoading
                    ? "جارٍ تحميل أحدث المنتجات…"
                    : feedError || "لا توجد منتجات في الطلبات المحمّلة."}
            </div>}
        </div>

        {feedError && !ordersLoading && <div className="border-t border-rose-100 p-3">
            <button
                type="button"
                onClick={() => loadFirstPage(false)}
                className="w-full rounded-xl border border-rose-200 bg-rose-50 px-4 py-2 text-xs font-extrabold text-rose-700 hover:bg-rose-100"
            >
                إعادة المحاولة
            </button>
        </div>}

        {canShowMore && <div className="border-t border-slate-100 p-3">
            <button
                type="button"
                onClick={showMore}
                disabled={ordersLoading}
                className="flex w-full items-center justify-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-xs font-extrabold text-emerald-800 hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60"
            >
                <ChevronDown className={`h-4 w-4 ${ordersLoading ? "animate-bounce" : ""}`} />
                {ordersLoading ? "جارٍ تحميل الطلبات الأقدم…" : "المزيد"}
            </button>
        </div>}
    </section>;
}
