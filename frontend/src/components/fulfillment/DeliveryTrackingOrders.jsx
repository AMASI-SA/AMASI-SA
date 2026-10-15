import { useCallback, useEffect, useState } from "react";
import {
    CheckCircle,
    MapPin,
    Package,
    SpinnerGap,
    Truck,
    WarningCircle,
} from "@phosphor-icons/react";

import { listDeliveryTrackingShipments } from "../../services/fulfillmentV2";

const STAGE_COPY = {
    delivering: {
        eyebrow: "المرحلة 8",
        title: "جاري التوصيل",
        description: "شحنات شركات الشحن التي بدأت رحلة التوصيل إلى العميل.",
        empty: "لا توجد شحنات خارجية في جاري التوصيل الآن.",
        badge: "جاري التوصيل",
    },
    delivered: {
        eyebrow: "المرحلة 9",
        title: "تم التوصيل",
        description: "شحنات شركات الشحن التي اكتمل تسليمها للعميل.",
        empty: "لا توجد شحنات خارجية مكتملة التوصيل الآن.",
        badge: "تم التوصيل",
    },
};

function dateLabel(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}

export default function DeliveryTrackingOrders({ stage = "delivering" }) {
    const normalizedStage = stage === "delivered" ? "delivered" : "delivering";
    const copy = STAGE_COPY[normalizedStage];
    const [orders, setOrders] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const load = useCallback(async ({ silent = false } = {}) => {
        if (!silent) setLoading(true);
        setError("");
        try {
            const result = await listDeliveryTrackingShipments({
                stage: normalizedStage,
                limit: 100,
            });
            setOrders(result.items || []);
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            if (!silent) setLoading(false);
        }
    }, [normalizedStage]);

    useEffect(() => { void load(); }, [load]);

    useEffect(() => {
        const timer = window.setInterval(() => {
            void load({ silent: true });
        }, 15000);
        return () => window.clearInterval(timer);
    }, [load]);

    if (loading) {
        return <div className="flex min-h-72 items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-700" /></div>;
    }

    const delivered = normalizedStage === "delivered";
    const headerClasses = delivered
        ? "border-emerald-200 bg-emerald-50 text-emerald-950"
        : "border-amber-200 bg-amber-50 text-amber-950";
    const iconClasses = delivered
        ? "bg-emerald-700 text-white"
        : "bg-amber-500 text-white";
    const badgeClasses = delivered
        ? "bg-emerald-100 text-emerald-800"
        : "bg-amber-100 text-amber-900";

    return (
        <section className="mx-auto w-full max-w-5xl space-y-4" dir="rtl" data-testid={`delivery-tracking-${normalizedStage}`}>
            <div className={`rounded-3xl border p-5 ${headerClasses}`}>
                <div className="flex items-start gap-3">
                    <span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl ${iconClasses}`}>
                        {delivered ? <CheckCircle size={28} weight="fill" /> : <Truck size={28} weight="duotone" />}
                    </span>
                    <div>
                        <div className="text-xs font-black opacity-70">{copy.eyebrow}</div>
                        <h2 className="mt-1 text-2xl font-black">{copy.title}</h2>
                        <p className="mt-1 text-sm font-bold leading-6 opacity-80">{copy.description}</p>
                    </div>
                </div>
            </div>

            <div className="rounded-2xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm font-bold leading-6 text-violet-950" data-testid="delivery-tracking-sync-source">
                تتغير هذه المرحلة فقط بعد مزامنة الحالة من صفحة الطلبات في ميزان. لا توجد مزامنة منفصلة مباشرة من سلة هنا، وطلبات مندوب المتجر لها مسار مستقل.
            </div>

            {error && (
                <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black text-rose-900" role="alert">
                    <WarningCircle size={22} weight="fill" /> {error}
                </div>
            )}

            {!orders.length ? (
                <div className="rounded-3xl border-2 border-dashed border-slate-200 bg-white p-10 text-center text-slate-500">
                    <Package size={44} className="mx-auto mb-2 text-slate-400" weight="duotone" />
                    <div className="font-black">{copy.empty}</div>
                </div>
            ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                    {orders.map((order) => (
                        <article key={order.order_number} className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="delivery-tracking-order">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <div className="text-xs font-black text-slate-400">طلب #{order.order_number}</div>
                                    <div className="mt-1 text-lg font-black text-slate-950">{order.customer_name || "—"}</div>
                                    <div className="mt-1 flex items-center gap-1 text-xs font-bold text-slate-500"><MapPin size={16} /> {order.city || "—"}</div>
                                </div>
                                <span className={`rounded-full px-3 py-1 text-xs font-black ${badgeClasses}`}>{copy.badge}</span>
                            </div>

                            <div className="mt-4 grid grid-cols-2 gap-2 rounded-2xl bg-slate-50 p-3 text-xs">
                                <div><div className="font-bold text-slate-400">شركة الشحن</div><div className="mt-1 font-black text-slate-800">{order.carrier_name || order.shipping_company || "—"}</div></div>
                                <div><div className="font-bold text-slate-400">رقم التتبع</div><div className="mt-1 font-black text-slate-800" dir="ltr">{order.carrier_tracking_number || "—"}</div></div>
                                <div><div className="font-bold text-slate-400">موظف تسليم الشحن</div><div className="mt-1 font-black text-slate-800">{order.carrier_handoff_employee_name || "—"}</div></div>
                                <div><div className="font-bold text-slate-400">استلمها الموظف</div><div className="mt-1 font-black text-slate-800">{dateLabel(order.carrier_handoff_scanned_at)}</div></div>
                                <div><div className="font-bold text-slate-400">آخر تحديث</div><div className="mt-1 font-black text-slate-800">{dateLabel(delivered ? order.delivered_at : order.delivering_at)}</div></div>
                            </div>
                        </article>
                    ))}
                </div>
            )}
        </section>
    );
}
