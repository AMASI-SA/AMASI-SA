import { useCallback, useEffect, useState } from "react";
import {
    CheckCircle,
    Package,
    Printer,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    listCompletedFulfillmentOrders,
    printFulfillmentBatch,
} from "../../services/fulfillmentV2";

export default function CompletedFulfillmentOrders() {
    const [orders, setOrders] = useState([]);
    const [permissions, setPermissions] = useState({});
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [reprintReasons, setReprintReasons] = useState({});

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const result = await listCompletedFulfillmentOrders({ limit: 100 });
            setOrders(result.items || []);
            setPermissions(result.permissions || {});
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const print = async (order) => {
        const batchId = order.print_batch_id;
        const isReprint = Number(order.print_count || 0) > 0;
        const reason = String(reprintReasons[batchId] || "").trim();
        if (isReprint && !reason) {
            setError("اكتب سبب إعادة طباعة الشحنة أولًا.");
            return;
        }
        setBusy(batchId);
        setError("");
        try {
            await printFulfillmentBatch(batchId, reason);
            toast.success(isReprint ? "تمت إعادة طباعة الشحنة" : "تم تنزيل ملف الشحنة");
            setReprintReasons((current) => ({ ...current, [batchId]: "" }));
            await load();
        } catch (printError) {
            setError(printError.message);
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
                        <p className="mt-1 text-sm font-bold leading-6 text-emerald-800">طلبات اكتمل تجميع كل منتجاتها وعنونتها. زر طباعة الشحنة يبقى هنا دائمًا.</p>
                    </div>
                </div>
            </div>

            {error && <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black text-rose-900" role="alert"><WarningCircle size={22} weight="fill" />{error}</div>}

            {!orders.length ? (
                <div className="rounded-3xl border-2 border-dashed border-slate-200 bg-white p-10 text-center text-slate-500"><CheckCircle size={44} className="mx-auto mb-2 text-emerald-500" />لا توجد طلبات مكتملة من التجميع بعد.</div>
            ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                    {orders.map((order) => {
                        const isReprint = Number(order.print_count || 0) > 0;
                        return (
                            <article key={order.order_number} className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <div className="text-xs font-black text-slate-400">طلب مكتمل</div>
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
                                {isReprint && (
                                    <input value={reprintReasons[order.print_batch_id] || ""} onChange={(event) => setReprintReasons((current) => ({ ...current, [order.print_batch_id]: event.target.value }))} placeholder="سبب إعادة الطباعة" className="mt-3 h-12 w-full rounded-2xl border border-amber-300 px-3 text-sm font-bold" />
                                )}
                                <button type="button" onClick={() => print(order)} disabled={!permissions.can_print || (isReprint && !permissions.can_reprint) || !order.print_batch_id || busy === order.print_batch_id} className="mt-3 inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-violet-700 px-4 text-base font-black text-white disabled:opacity-50">
                                    {busy === order.print_batch_id ? <SpinnerGap size={23} className="animate-spin" /> : <Printer size={24} weight="fill" />}
                                    {isReprint ? "إعادة طباعة الشحنة" : "طباعة الشحنة"}
                                </button>
                                {isReprint && !permissions.can_reprint && <p className="mt-2 text-center text-xs font-black text-amber-800">إعادة الطباعة تحتاج صلاحية من المالك.</p>}
                            </article>
                        );
                    })}
                </div>
            )}
        </section>
    );
}
