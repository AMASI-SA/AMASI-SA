import { useCallback, useEffect, useState } from "react";
import {
    CheckCircle,
    DownloadSimple,
    Package,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import { printStoreCourierLabel } from "../../lib/storeCourierLabelPrint";

import {
    issueCompletedOrderCarrierLabel,
    listCompletedFulfillmentOrders,
    refreshCompletedOrderCarrierLabel,
} from "../../services/fulfillmentV2";

const wait = (milliseconds) => new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
});

function savedCarrierSnapshot(order) {
    return {
        ready: Boolean(order.carrier_label_ready),
        label_url: order.carrier_label_url || "",
        label_type: order.carrier_label_type || "",
        courier_name: order.carrier_name || order.shipping_company || "شركة الشحن",
        tracking_number: order.carrier_tracking_number || "",
        status: order.carrier_label_status || "",
        order_status_completed: order.salla_order_status === "completed",
        message: order.carrier_label_message || "",
        error_code: order.carrier_label_error_code || "",
        error_message: order.carrier_label_error_message || "",
    };
}

export function CarrierLabelControl({ order, permissions, busy, onIssue }) {
    const snapshot = order.carrierSnapshot || savedCarrierSnapshot(order);
    const ready = Boolean(snapshot.ready && snapshot.label_url);
    const storeCourierReady = Boolean(
        snapshot.ready
        && snapshot.label_type === "store_courier"
        && snapshot.print_data?.qr_code
    );
    const sallaCompleted = Boolean(snapshot.order_status_completed);
    const courier = snapshot.courier_name || order.shipping_company || "شركة الشحن";

    if (ready || storeCourierReady) {
        return (
            <div className="mt-3 space-y-2" data-testid="official-carrier-label-ready">
                <div className="flex items-center justify-center gap-2 rounded-2xl bg-emerald-50 px-3 py-2 text-xs font-black text-emerald-800">
                    <CheckCircle size={19} weight="fill" /> تم التنفيذ في سلة · البوليصة جاهزة
                </div>
                {storeCourierReady ? (
                    <button
                        type="button"
                        onClick={() => {
                            const printWindow = window.open("about:blank", "_blank");
                            if (printWindow) printWindow.opener = null;
                            if (!printStoreCourierLabel(printWindow, snapshot.print_data)) {
                                printWindow?.close();
                            }
                        }}
                        className="inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-violet-700 px-4 text-base font-black text-white"
                        data-testid="print-store-courier-label"
                    >
                        <DownloadSimple size={24} weight="bold" /> طباعة بوليصة مندوب المتجر
                    </button>
                ) : (
                    <a
                        href={snapshot.label_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        download
                        className="inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-violet-700 px-4 text-base font-black text-white"
                        data-testid="download-official-carrier-label"
                    >
                        <DownloadSimple size={24} weight="bold" /> تحميل بوليصة {courier}
                    </a>
                )}
                {snapshot.tracking_number && (
                    <div className="text-center text-xs font-bold text-slate-500" dir="ltr">
                        {snapshot.tracking_number}
                    </div>
                )}
            </div>
        );
    }

    return (
        <div className="mt-3">
            {sallaCompleted && snapshot.label_type !== "store_courier" && (
                <div className="mb-2 rounded-2xl bg-amber-50 px-3 py-2 text-center text-xs font-black text-amber-900">
                    تم التنفيذ في سلة — ننتظر رابط البوليصة من {courier}
                </div>
            )}
            {(snapshot.error_message || snapshot.message) && (
                <div className={`mb-2 rounded-2xl px-3 py-2 text-center text-xs font-bold ${snapshot.error_message ? "bg-rose-50 text-rose-900" : "bg-slate-50 text-slate-700"}`}>
                    {snapshot.error_message || snapshot.message}
                </div>
            )}
            <button
                type="button"
                onClick={() => onIssue(order)}
                disabled={!permissions.can_print || busy}
                className="inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 text-sm font-black text-white disabled:opacity-50"
                data-testid="issue-official-carrier-label"
            >
                {busy ? <SpinnerGap size={23} className="animate-spin" /> : <Package size={23} weight="fill" />}
                {busy
                    ? "جاري تحويل سلة وانتظار البوليصة..."
                    : snapshot.label_type === "store_courier"
                        ? "تجهيز بوليصة مندوب المتجر"
                        : sallaCompleted
                        ? "إعادة التحقق من رابط البوليصة"
                        : "تحويل سلة إلى تم التنفيذ وإصدار البوليصة"}
            </button>
        </div>
    );
}

export default function CompletedFulfillmentOrders() {
    const [orders, setOrders] = useState([]);
    const [permissions, setPermissions] = useState({});
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const result = await listCompletedFulfillmentOrders({ limit: 100 });
            setOrders((result.items || []).map((order) => ({
                ...order,
                carrierSnapshot: savedCarrierSnapshot(order),
            })));
            setPermissions(result.permissions || {});
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const setSnapshot = (orderNumber, snapshot) => {
        setOrders((current) => current.map((order) => (
            order.order_number === orderNumber
                ? { ...order, carrierSnapshot: { ...snapshot } }
                : order
        )));
    };

    const issue = async (order) => {
        const orderNumber = order.order_number;
        setBusy(orderNumber);
        setError("");
        try {
            let result = await issueCompletedOrderCarrierLabel(orderNumber);
            setSnapshot(orderNumber, result);

            // The courier app normally returns its signed PDF link in about
            // two seconds. Keep the employee on one screen while we wait.
            for (let attempt = 0; attempt < 5 && result?.order_status_completed && !result?.ready; attempt += 1) {
                await wait(2000);
                result = await refreshCompletedOrderCarrierLabel(orderNumber);
                setSnapshot(orderNumber, result);
            }

            if (result?.ready && result?.label_type === "store_courier") {
                toast.success("تم تجهيز بوليصة مندوب المتجر من بيانات العميل والطلب");
            } else if (result?.ready && result?.label_url) {
                toast.success("وصلت بوليصة الشحن الرسمية وأصبحت جاهزة للتحميل");
            } else if (result?.order_status_completed) {
                toast.success("تحول الطلب في سلة إلى تم التنفيذ؛ رابط البوليصة ما زال قيد التجهيز");
            }
        } catch (issueError) {
            setError(issueError.message);
            setOrders((current) => current.map((row) => (
                row.order_number === orderNumber
                    ? {
                        ...row,
                        carrierSnapshot: {
                            ...(row.carrierSnapshot || {}),
                            ready: false,
                            error_message: issueError.message,
                        },
                    }
                    : row
            )));
        } finally {
            setBusy("");
        }
    };

    if (loading) {
        return <div className="flex min-h-72 items-center justify-center"><SpinnerGap size={34} className="animate-spin text-emerald-700" /></div>;
    }

    return (
        <section className="mx-auto w-full max-w-4xl space-y-4" dir="rtl" data-testid="completed-fulfillment-orders">
            <div className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5">
                <div className="flex items-start gap-3">
                    <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-emerald-700 text-white"><CheckCircle size={28} weight="fill" /></span>
                    <div>
                        <div className="text-xs font-black text-emerald-700">المرحلة الرابعة</div>
                        <h2 className="mt-1 text-2xl font-black text-emerald-950">تم التنفيذ</h2>
                        <p className="mt-1 text-sm font-bold leading-6 text-emerald-800">حوّل الطلب في سلة إلى تم التنفيذ، ثم انتظر رابط شركة الشحن وحمّل البوليصة الرسمية.</p>
                    </div>
                </div>
            </div>

            {error && <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black text-rose-900" role="alert"><WarningCircle size={22} weight="fill" />{error}</div>}

            {!orders.length ? (
                <div className="rounded-3xl border-2 border-dashed border-slate-200 bg-white p-10 text-center text-slate-500"><CheckCircle size={44} className="mx-auto mb-2 text-emerald-500" />لا توجد طلبات مكتملة من التجميع بعد.</div>
            ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                    {orders.map((order) => (
                        <article key={order.order_number} className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <div className="text-xs font-black text-slate-400">طلب مكتمل في ميزان</div>
                                    <div className="mt-1 text-xl font-black">#{order.order_number}</div>
                                    <div className="mt-1 text-sm font-bold text-slate-600">{order.customer_name || "—"} · {order.city || "—"}</div>
                                </div>
                                <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-700"><Package size={25} weight="duotone" /></span>
                            </div>
                            <div className="mt-3 space-y-2 rounded-2xl bg-slate-50 p-3">
                                {(order.items || []).map((item, index) => (
                                    <div key={`${item.order_item_id || item.sku}-${index}`} className="flex items-center justify-between gap-3 text-xs font-bold">
                                        <span className="min-w-0 truncate">{item.name || "منتج"}</span>
                                        <span className="shrink-0 text-slate-500">× {item.quantity || 1}</span>
                                    </div>
                                ))}
                            </div>
                            <CarrierLabelControl
                                order={order}
                                permissions={permissions}
                                busy={busy === order.order_number}
                                onIssue={issue}
                            />
                        </article>
                    ))}
                </div>
            )}
        </section>
    );
}
