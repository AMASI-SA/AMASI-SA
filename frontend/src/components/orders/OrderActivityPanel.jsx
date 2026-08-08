import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowsClockwise,
    ClockCounterClockwise,
    CreditCard,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";

import {
    getOrderActivity,
    refreshOrderActivity,
} from "../../services/orderActivity";

function formatDate(value) {
    if (!value) return "—";

    const parsed = new Date(
        String(value).includes("T")
            ? value
            : String(value).replace(" ", "T")
    );

    if (Number.isNaN(parsed.getTime())) {
        return String(value);
    }

    return new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    }).format(parsed);
}

function formatMoney(amount, currency = "SAR") {
    const value = Number(amount || 0);

    const formatted = value.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 3,
    });

    return currency === "SAR"
        ? `${formatted} ر.س`
        : `${formatted} ${currency}`;
}

function paymentTitle(payment) {
    const amount = formatMoney(
        payment.amount,
        payment.currency || "SAR"
    );

    const method = payment.payment_method
        ? ` · ${payment.payment_method}`
        : "";

    return `عملية دفع ${amount}${method}`;
}

export default function OrderActivityPanel({ orderNumber }) {
    const [activity, setActivity] = useState({
        events: [],
        payments: [],
    });

    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState("");
    const [paymentWarning, setPaymentWarning] = useState("");

    const loadLocal = useCallback(async () => {
        const normalized = String(orderNumber || "").trim();

        if (!normalized) {
            setLoading(false);
            return;
        }

        try {
            setError("");

            const result = await getOrderActivity(normalized);

            setActivity({
                events: Array.isArray(result?.events)
                    ? result.events
                    : [],
                payments: Array.isArray(result?.payments)
                    ? result.payments
                    : [],
            });
        } catch (loadError) {
            setError(
                loadError?.message ||
                "تعذر تحميل سجل الطلب."
            );
        } finally {
            setLoading(false);
        }
    }, [orderNumber]);

    useEffect(() => {
        setLoading(true);
        setPaymentWarning("");
        void loadLocal();
    }, [loadLocal]);

    async function handleRefresh() {
        const normalized = String(orderNumber || "").trim();

        if (!normalized || refreshing) return;

        setRefreshing(true);
        setError("");
        setPaymentWarning("");

        try {
            const result = await refreshOrderActivity(normalized);

            setActivity({
                events: Array.isArray(result?.events)
                    ? result.events
                    : [],
                payments: Array.isArray(result?.payments)
                    ? result.payments
                    : [],
            });

            if (result?.payment_fetch_error) {
                setPaymentWarning(
                    "تم تحديث أحداث الطلب، لكن تفاصيل معاملات الدفع غير متاحة من صلاحيات سلة الحالية."
                );
            }
        } catch (refreshError) {
            setError(
                refreshError?.message ||
                "تعذر تحديث سجل الطلب من سلة."
            );
        } finally {
            setRefreshing(false);
        }
    }

    const timeline = useMemo(() => {
        const events = (activity.events || []).map((event) => ({
            key:
                event.fingerprint ||
                `event-${event.source_event_id}-${event.occurred_at}`,
            kind: "event",
            title: event.title || "حدث في الطلب",
            occurredAt: event.occurred_at || "",
            actorName: event.actor_name || "",
            status: "",
        }));

        const payments = (activity.payments || []).map((payment) => ({
            key:
                payment.fingerprint ||
                `payment-${payment.transaction_id}-${payment.occurred_at}`,
            kind: "payment",
            title: paymentTitle(payment),
            occurredAt: payment.occurred_at || "",
            actorName: "",
            status: payment.status || "",
        }));

        return [...events, ...payments].sort((a, b) => {
            const left = String(a.occurredAt || "");
            const right = String(b.occurredAt || "");
            return left.localeCompare(right);
        });
    }, [activity]);

    return (
        <section
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
            data-testid="order-v2-timeline"
        >
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                    <div className="rounded-lg bg-violet-100 p-2 text-violet-700">
                        <ClockCounterClockwise
                            size={20}
                            weight="fill"
                        />
                    </div>

                    <div>
                        <h2 className="font-extrabold text-slate-950">
                            سجل الطلب
                        </h2>

                        <div className="mt-0.5 text-xs text-slate-400">
                            أحداث سلة ومعاملات الدفع
                        </div>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={handleRefresh}
                    disabled={refreshing || !orderNumber}
                    className="inline-flex items-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-extrabold text-violet-800 transition hover:bg-violet-100 disabled:cursor-wait disabled:opacity-60"
                    data-testid="order-v2-activity-refresh"
                >
                    <ArrowsClockwise
                        size={17}
                        weight="bold"
                        className={
                            refreshing ? "animate-spin" : ""
                        }
                    />

                    {refreshing
                        ? "جاري تحديث السجل…"
                        : "تحديث السجل من سلة"}
                </button>
            </div>

            {error && (
                <div className="mb-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
                    <WarningCircle
                        size={19}
                        weight="fill"
                        className="mt-0.5 shrink-0"
                    />
                    <span>{error}</span>
                </div>
            )}

            {paymentWarning && (
                <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-800">
                    {paymentWarning}
                </div>
            )}

            {loading ? (
                <div className="flex min-h-32 items-center justify-center">
                    <SpinnerGap
                        size={26}
                        className="animate-spin text-violet-600"
                    />
                </div>
            ) : timeline.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center">
                    <ClockCounterClockwise
                        size={30}
                        className="mx-auto text-slate-300"
                    />

                    <div className="mt-3 text-sm font-bold text-slate-600">
                        لم يتم تحميل تاريخ هذا الطلب بعد
                    </div>

                    <div className="mt-1 text-xs text-slate-400">
                        اضغط «تحديث السجل من سلة» لجلب الأحداث بدون تغيير حالة الطلب.
                    </div>
                </div>
            ) : (
                <div className="space-y-3">
                    {timeline.map((entry) => (
                        <div
                            key={entry.key}
                            className="flex items-start gap-3 rounded-xl border border-slate-100 bg-slate-50 p-3"
                        >
                            <div
                                className={
                                    entry.kind === "payment"
                                        ? "rounded-full bg-emerald-100 p-2 text-emerald-700"
                                        : "rounded-full bg-violet-100 p-2 text-violet-700"
                                }
                            >
                                {entry.kind === "payment" ? (
                                    <CreditCard
                                        size={16}
                                        weight="fill"
                                    />
                                ) : (
                                    <ClockCounterClockwise
                                        size={16}
                                        weight="fill"
                                    />
                                )}
                            </div>

                            <div className="min-w-0 flex-1">
                                <div className="text-sm font-extrabold text-slate-900">
                                    {entry.title}
                                </div>

                                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-400">
                                    <span className="num">
                                        {formatDate(
                                            entry.occurredAt
                                        )}
                                    </span>

                                    {entry.actorName && (
                                        <span>
                                            بواسطة:{" "}
                                            {entry.actorName}
                                        </span>
                                    )}

                                    {entry.status && (
                                        <span>
                                            الحالة:{" "}
                                            {entry.status}
                                        </span>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </section>
    );
}
