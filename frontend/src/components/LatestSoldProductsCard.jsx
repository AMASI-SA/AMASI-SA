import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, PackageOpen } from "lucide-react";

const ORDERS_PER_PAGE = 5;

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

export default function LatestSoldProductsCard({
    orders = [],
    hasMoreOrders = false,
    ordersLoading = false,
    onLoadMoreOrders,
}) {
    const [visibleOrderCount, setVisibleOrderCount] = useState(ORDERS_PER_PAGE);
    const sortedOrders = useMemo(() => sortOrdersNewestFirst(orders), [orders]);
    const visibleOrders = sortedOrders.slice(0, visibleOrderCount);
    const rows = useMemo(() => expandSoldProductRows(visibleOrders), [visibleOrders]);
    const canShowMore = visibleOrderCount < sortedOrders.length || hasMoreOrders;

    const showMore = () => {
        const nextVisibleCount = visibleOrderCount + ORDERS_PER_PAGE;
        setVisibleOrderCount(nextVisibleCount);
        if (nextVisibleCount > sortedOrders.length && hasMoreOrders && !ordersLoading) {
            onLoadMoreOrders?.();
        }
    };

    return <section data-testid="advanced-latest-sold-products" className="overflow-hidden rounded-2xl border border-emerald-200 bg-white shadow-sm">
        <div className="flex min-h-14 items-center justify-between gap-3 border-b border-emerald-800 bg-emerald-700 px-4 py-3 text-white">
            <h2 className="flex items-center gap-2 font-extrabold"><PackageOpen className="h-5 w-5" />أحدث المنتجات المباعة</h2>
            <span className="shrink-0 rounded-full bg-white/15 px-2 py-1 text-[10px] font-bold">{visibleOrders.length} طلبات</span>
        </div>

        {rows.length ? <div className="divide-y divide-slate-100">
            {rows.map(({ order, item, itemIndex, unitIndex, quantity }) => {
                const orderNumber = String(order?.order_number || "");
                const details = itemDetails(item);
                const imageUrl = String(item?.image_url || item?.image_urls?.[0] || "").trim();
                const key = `${orderNumber}-${item?.order_item_id || item?.product_id || itemIndex}-${unitIndex}`;
                return <Link
                    key={key}
                    to={`/orders-v2/${encodeURIComponent(orderNumber)}?returnTo=${encodeURIComponent("/dashboard-advanced")}`}
                    dir="rtl"
                    className="flex min-h-[76px] items-center gap-3 px-4 py-3 text-right hover:bg-emerald-50/40"
                    data-testid={`latest-sold-product-${key}`}
                >
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-100 bg-slate-50">
                        {imageUrl
                            ? <img src={imageUrl} alt="" loading="lazy" className="h-full w-full object-cover" onError={(event) => { event.currentTarget.style.display = "none"; }} />
                            : <PackageOpen className="h-5 w-5 text-slate-300" />}
                    </div>
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
            })}
        </div> : <div className="p-6 text-center text-xs font-bold text-slate-400">لا توجد منتجات في الطلبات المحمّلة.</div>}

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
