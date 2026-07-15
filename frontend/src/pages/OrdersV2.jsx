import {
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react";
import { useNavigate } from "react-router-dom";
import {
    CalendarBlank,
    CaretLeft,
    CreditCard,
    MagnifyingGlass,
    Package,
    SpinnerGap,
    Truck,
    User,
    WarningCircle,
    X,
} from "@phosphor-icons/react";

import { useOrders } from "../hooks/useOrders";

function formatMoney(value) {
    const number = Number(value || 0);

    return `${number.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function formatOrderDate(value) {
    if (!value) return "تاريخ الإنشاء غير متاح";

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
        return String(value);
    }

    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        timeZone: "Asia/Riyadh",
        day: "numeric",
        month: "short",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
    }).format(date);
}

function statusClass(status) {
    const value = String(status || "").toLowerCase();

    if (
        value.includes("completed") ||
        value.includes("delivered") ||
        value.includes("تم التنفيذ") ||
        value.includes("تم التوصيل")
    ) {
        return "border-emerald-200 bg-emerald-50 text-emerald-800";
    }

    if (
        value.includes("cancel") ||
        value.includes("ملغ") ||
        value.includes("refunded") ||
        value.includes("مسترج")
    ) {
        return "border-rose-200 bg-rose-50 text-rose-800";
    }

    if (
        value.includes("review") ||
        value.includes("مراجعة") ||
        value.includes("pending")
    ) {
        return "border-black bg-black text-white";
    }

    return "border-sky-200 bg-sky-50 text-sky-800";
}

export default function OrdersV2() {
    const navigate = useNavigate();
    const loadMoreRef = useRef(null);

    const {
        orders,
        hasMore,
        loading,
        initialLoading,
        error,
        searchMode,
        loadMore,
        reload,
        searchExactOrder,
    } = useOrders();

    const [searchDraft, setSearchDraft] = useState("");

    useEffect(() => {
        const node = loadMoreRef.current;

        if (
            !node ||
            !hasMore ||
            initialLoading ||
            searchMode
        ) {
            return undefined;
        }

        const observer = new IntersectionObserver(
            (entries) => {
                if (entries[0]?.isIntersecting) {
                    loadMore();
                }
            },
            { rootMargin: "300px" }
        );

        observer.observe(node);

        return () => observer.disconnect();
    }, [
        hasMore,
        initialLoading,
        loadMore,
        searchMode,
    ]);

    const shownCount = useMemo(
        () => orders.length,
        [orders]
    );

    function submitSearch(event) {
        event.preventDefault();
        searchExactOrder(searchDraft);
    }

    async function clearSearch() {
        setSearchDraft("");
        await reload();
    }

    return (
        <div
            className="space-y-5"
            dir="rtl"
            data-testid="orders-v2-page"
        >
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-center gap-2">
                        <div className="rounded-xl bg-violet-100 p-2 text-violet-700">
                            <Package size={24} weight="fill" />
                        </div>

                        <div>
                            <h1 className="text-2xl font-extrabold text-slate-950">
                                الطلبات
                            </h1>
                            <p className="mt-1 text-sm text-slate-500">
                                مركز الطلبات الموحد من Order Engine
                            </p>
                        </div>
                    </div>

                    <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-3">
                        <div className="text-xs font-bold text-violet-700">
                            الطلبات المعروضة
                        </div>
                        <div className="num mt-1 text-2xl font-extrabold text-violet-950">
                            {shownCount.toLocaleString("en-US")}
                        </div>
                    </div>
                </div>

                <form
                    onSubmit={submitSearch}
                    className="mt-5 flex gap-2"
                >
                    <div className="relative flex-1">
                        <MagnifyingGlass
                            size={20}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
                        />

                        <input
                            value={searchDraft}
                            onChange={(event) =>
                                setSearchDraft(event.target.value)
                            }
                            placeholder="ابحث برقم الطلب الدقيق…"
                            className="w-full rounded-xl border border-slate-200 bg-slate-50 py-3 pr-11 pl-4 outline-none transition focus:border-violet-400 focus:bg-white focus:ring-2 focus:ring-violet-100"
                            data-testid="orders-v2-search"
                        />
                    </div>

                    <button
                        type="submit"
                        disabled={loading}
                        className="rounded-xl bg-violet-700 px-5 py-3 text-sm font-bold text-white transition hover:bg-violet-800 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                        بحث
                    </button>

                    {searchMode && (
                        <button
                            type="button"
                            onClick={clearSearch}
                            className="inline-flex items-center gap-1 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-bold text-slate-700"
                        >
                            <X size={17} />
                            مسح
                        </button>
                    )}
                </form>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="border-b border-slate-100 px-5 py-4">
                    <h2 className="font-extrabold text-slate-900">
                        {searchMode
                            ? "نتيجة البحث"
                            : "أحدث الطلبات حسب تاريخ الإنشاء"}
                    </h2>

                    <p className="mt-1 text-xs text-slate-500">
                        تحديث حالة الطلب لا يغيّر موضعه في القائمة.
                    </p>
                </div>

                {error && (
                    <div className="m-5 flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800">
                        <WarningCircle size={22} weight="fill" />

                        <div>
                            <div className="font-bold">
                                تعذّر تحميل الطلبات
                            </div>
                            <div className="mt-1 text-sm">
                                {error}
                            </div>
                        </div>
                    </div>
                )}

                {initialLoading ? (
                    <div className="flex min-h-72 items-center justify-center">
                        <SpinnerGap
                            size={32}
                            className="animate-spin text-violet-600"
                        />
                    </div>
                ) : orders.length === 0 ? (
                    <div className="flex min-h-72 flex-col items-center justify-center text-slate-500">
                        <Package
                            size={48}
                            className="mb-3 text-slate-300"
                        />
                        <div className="font-bold">
                            لا توجد طلبات مطابقة
                        </div>
                    </div>
                ) : (
                    <div className="divide-y divide-slate-100">
                        {orders.map((order) => {
                            const status =
                                order.status_native ||
                                order.status ||
                                "غير محدد";

                            const paymentMethod =
                                order.payment?.method_native ||
                                order.payment?.method ||
                                "غير محدد";

                            return (
                                <button
                                    type="button"
                                    key={order.order_number}
                                    onClick={() =>
                                        navigate(
                                            `/orders-v2/${encodeURIComponent(
                                                order.order_number
                                            )}`
                                        )
                                    }
                                    className="grid w-full grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-2 px-4 py-3 text-right transition hover:bg-violet-50/40 lg:grid-cols-[1.5fr_1fr_1fr_auto] lg:gap-4 lg:px-5 lg:py-5"
                                    data-testid={`orders-v2-row-${order.order_number}`}
                                >
                                    <div className="col-start-1 row-start-1 flex min-w-0 items-start gap-2.5 lg:col-auto lg:row-auto lg:gap-3">
                                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-600 lg:h-11 lg:w-11">
                                            <User size={22} weight="fill" />
                                        </div>

                                        <div className="min-w-0">
                                            <div className="flex min-w-0 items-center gap-2">
                                                <div className="truncate text-sm font-extrabold text-slate-950 lg:text-base">
                                                    {order.customer?.name ||
                                                        "عميل بدون اسم"}
                                                </div>
                                                {order.is_new && (
                                                    <span className="shrink-0 rounded-full border border-rose-300 bg-white px-2 py-0.5 text-[10px] font-extrabold text-rose-600 lg:text-xs">
                                                        جديد
                                                    </span>
                                                )}
                                            </div>

                                            <div className="num mt-0.5 text-xs font-bold text-violet-700 lg:mt-1 lg:text-sm">
                                                #{order.order_number}
                                            </div>

                                            <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-500 lg:mt-2 lg:text-xs">
                                                <CalendarBlank size={15} />
                                                <span>
                                                    {formatOrderDate(
                                                        order.created_at
                                                    )}
                                                </span>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="col-start-1 row-start-2 flex min-w-0 flex-wrap items-center gap-1.5 pr-[50px] lg:col-auto lg:row-auto lg:block lg:space-y-2 lg:pr-0">
                                        <span
                                            className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold lg:px-2.5 lg:py-1 lg:text-xs ${statusClass(
                                                status
                                            )}`}
                                        >
                                            {status}
                                        </span>

                                        <div className="flex items-center gap-1 text-[10px] text-slate-600 lg:gap-1.5 lg:text-xs">
                                            <CreditCard size={14} className="lg:h-4 lg:w-4" />
                                            <span>{paymentMethod}</span>
                                        </div>

                                        <div className="flex items-center gap-1 text-[10px] text-slate-600 lg:hidden">
                                            <Package size={14} />
                                            <span>
                                                {Number(
                                                    order.items?.length || 0
                                                ).toLocaleString("en-US")} منتج
                                            </span>
                                        </div>

                                        {order.payment?.receiving_bank_name && (
                                            <div className="text-xs font-bold text-slate-700">
                                                البنك:{" "}
                                                {
                                                    order.payment
                                                        .receiving_bank_name
                                                }
                                            </div>
                                        )}
                                    </div>

                                    <div className="hidden space-y-2 text-sm text-slate-600 lg:block">
                                        <div className="flex items-center gap-1.5">
                                            <Truck size={16} />
                                            <span>
                                                {order.shipping?.company ||
                                                    "غير محدد"}
                                            </span>
                                        </div>

                                        <div className="flex items-center gap-1.5">
                                            <Package size={16} />
                                            <span>
                                                {Number(
                                                    order.items?.length || 0
                                                ).toLocaleString("en-US")}{" "}
                                                منتج
                                            </span>
                                        </div>
                                    </div>

                                    <div className="col-start-2 row-span-2 row-start-1 flex min-w-[92px] flex-col items-end justify-center gap-2 lg:col-auto lg:row-auto lg:min-w-0 lg:flex-row lg:items-center lg:justify-end lg:gap-4">
                                        <div className="num whitespace-nowrap text-base font-extrabold text-slate-950 lg:text-lg">
                                            {formatMoney(
                                                order.totals?.total
                                            )}
                                        </div>
                                        <CaretLeft
                                            size={18}
                                            className="text-slate-400 lg:h-5 lg:w-5"
                                        />
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                )}

                <div
                    ref={loadMoreRef}
                    className="flex min-h-20 items-center justify-center border-t border-slate-100"
                    data-testid="orders-v2-load-more-sentinel"
                >
                    {loading && !initialLoading && (
                        <div className="flex items-center gap-2 text-sm font-bold text-violet-700">
                            <SpinnerGap
                                size={20}
                                className="animate-spin"
                            />
                            تحميل 15 طلبًا إضافيًا…
                        </div>
                    )}

                    {!hasMore &&
                        orders.length > 0 &&
                        !searchMode && (
                            <div className="text-sm text-slate-400">
                                تم عرض جميع الطلبات المتاحة
                            </div>
                        )}
                </div>
            </section>
        </div>
    );
}
