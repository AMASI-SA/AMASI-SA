import { useCallback, useEffect, useMemo, useState } from "react";
import {
    CheckCircle, ClipboardText, Package, Printer, SpinnerGap, Truck, WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    claimReadyToShipBatch,
    confirmFulfillmentBatchHandoff,
    confirmFulfillmentBatchPacked,
    listFulfillmentBatches,
    listReadyToShipOrders,
    printFulfillmentBatch,
} from "../../services/fulfillmentV2";

const STATUS_LABELS = {
    claimed: "تم الاستلام",
    printed: "تمت الطباعة",
    packed: "تم التغليف",
    handed_off: "سُلّمت للناقل",
};

export default function ReadyToShipOrders() {
    const [orders, setOrders] = useState([]);
    const [batches, setBatches] = useState([]);
    const [permissions, setPermissions] = useState({});
    const [selected, setSelected] = useState([]);
    const [reprintReasons, setReprintReasons] = useState({});
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [queue, batchResult] = await Promise.all([
                listReadyToShipOrders({ limit: 100 }),
                listFulfillmentBatches({ limit: 50 }),
            ]);
            setOrders(queue.items || []);
            setPermissions(queue.permissions || {});
            setBatches(batchResult.items || []);
            setSelected((current) => current.filter((orderNumber) => (queue.items || []).some((row) => row.order_number === orderNumber && !row.claimed)));
        } catch (error) {
            toast.error(error.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const unclaimed = useMemo(() => orders.filter((row) => !row.claimed), [orders]);
    const allSelected = unclaimed.length > 0 && unclaimed.every((row) => selected.includes(row.order_number));

    function toggle(orderNumber) {
        setSelected((current) => current.includes(orderNumber)
            ? current.filter((value) => value !== orderNumber)
            : [...current, orderNumber]);
    }

    async function claim() {
        if (!selected.length) return;
        setBusy("claim");
        try {
            const result = await claimReadyToShipBatch(selected);
            toast.success(`تم استلام دفعة ${result.batch?.id} وفيها ${selected.length} طلب`);
            setSelected([]);
            await load();
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy("");
        }
    }

    async function print(batch) {
        const isReprint = Number(batch.print_count || 0) > 0;
        const reason = String(reprintReasons[batch.id] || "").trim();
        if (isReprint && !reason) {
            toast.error("اكتب سبب إعادة الطباعة أولًا.");
            return;
        }
        setBusy(`print:${batch.id}`);
        try {
            await printFulfillmentBatch(batch.id, reason);
            toast.success(isReprint ? "تم تسجيل إعادة الطباعة وتنزيل الملف" : "تم تنزيل ملف الدفعة");
            setReprintReasons((current) => ({ ...current, [batch.id]: "" }));
            await load();
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy("");
        }
    }

    async function pack(batch) {
        setBusy(`pack:${batch.id}`);
        try {
            await confirmFulfillmentBatchPacked(batch.id);
            toast.success("تم تأكيد التغليف");
            await load();
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy("");
        }
    }

    async function handoff(batch) {
        setBusy(`handoff:${batch.id}`);
        try {
            await confirmFulfillmentBatchHandoff(batch.id);
            toast.success("تم تأكيد تسليم الدفعة لشركة الشحن");
            await load();
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy("");
        }
    }

    if (loading) return <div className="flex min-h-80 items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-600" /></div>;

    return (
        <div className="space-y-5" data-testid="ready-to-ship-orders">
            <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <h2 className="text-xl font-black text-emerald-950"><Package className="ml-1 inline" /> الطلبات الجاهزة للشحن</h2>
                        <p className="mt-1 text-sm leading-6 text-emerald-800">وصلت تلقائيًا بعد تحقق نوع المنتج والدفع والعنوان ومخزون ميزان. لم تُرفع في ملف تجهيز المورد.</p>
                    </div>
                    <button type="button" disabled={!permissions.can_claim || !selected.length || busy === "claim"} onClick={claim} className="rounded-xl bg-emerald-700 px-5 py-3 font-black text-white disabled:opacity-50">{busy === "claim" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <ClipboardText className="ml-1 inline" />} استلام وإنشاء دفعة ({selected.length})</button>
                </div>
            </section>

            <section className="overflow-hidden rounded-2xl border bg-white shadow-sm">
                <div className="flex items-center justify-between border-b p-4">
                    <div><h3 className="font-black">قائمة العنونة والشحن</h3><p className="text-xs text-slate-500">{orders.length} طلب ظاهر حسب فروعك وصلاحياتك</p></div>
                    {!!unclaimed.length && <label className="flex items-center gap-2 text-xs font-bold"><input type="checkbox" checked={allSelected} onChange={() => setSelected(allSelected ? [] : unclaimed.map((row) => row.order_number))} /> تحديد غير المستلم</label>}
                </div>
                {!orders.length ? <div className="p-10 text-center text-slate-400"><CheckCircle size={44} className="mx-auto mb-2 text-emerald-500" />لا توجد طلبات جاهزة في قائمتك الآن.</div> : <div className="divide-y">{orders.map((order) => (
                    <article key={order.order_number} className={`grid gap-3 p-4 lg:grid-cols-[auto_minmax(0,1fr)_220px] lg:items-center ${order.claimed ? "bg-slate-50" : ""}`}>
                        <input type="checkbox" disabled={order.claimed} checked={selected.includes(order.order_number)} onChange={() => toggle(order.order_number)} className="h-5 w-5" />
                        <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2"><span className="text-lg font-black">#{order.order_number}</span>{order.claimed && <span className="rounded-full bg-violet-100 px-2 py-1 text-[11px] font-bold text-violet-800">ضمن دفعة {order.claim_batch_id}</span>}</div>
                            <p className="mt-1 text-sm text-slate-600">{order.customer_name || "—"} · {order.customer_mobile || "—"} · {order.city || "—"}</p>
                            <p className="mt-1 text-xs text-slate-400">
                                {order.items_count} منتجات · {(order.warehouse_ids || []).length
                                    ? `${order.warehouse_ids.length} فرع/مخزن من المخزون`
                                    : "المخزون غير مرتبط بفرع — لا يمكن الاستلام"}
                            </p>
                        </div>
                        <div className="rounded-xl bg-white p-3 text-xs"><div className="font-black">{order.shipping_company || "شركة الشحن غير محددة"}</div><div className="mt-1 text-slate-500">{order.claimed_by_name ? `المستلم: ${order.claimed_by_name}` : "بانتظار استلام الموظف"}</div></div>
                    </article>
                ))}</div>}
            </section>

            <section className="space-y-3">
                <div><h3 className="text-lg font-black">دفعاتي للطباعة والتسليم</h3><p className="text-xs text-slate-500">الطباعة الأولى محمية من التكرار؛ إعادة الطباعة تتطلب سببًا وتسجل باسم الموظف.</p></div>
                {!batches.length && <div className="rounded-2xl border border-dashed bg-white p-8 text-center text-slate-400">لم تُنشأ دفعات شحن بعد.</div>}
                {batches.map((batch) => {
                    const printed = Number(batch.print_count || 0) > 0;
                    const canReprint = printed && permissions.can_reprint;
                    return (
                        <article key={batch.id} className="rounded-2xl border bg-white p-4 shadow-sm">
                            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                                <div><div className="font-black">{batch.id}</div><div className="mt-1 text-xs text-slate-500">{(batch.order_numbers || []).length} طلب · {STATUS_LABELS[batch.status] || batch.status}</div></div>
                                <div className="flex flex-wrap items-center gap-2">
                                    {printed && <input value={reprintReasons[batch.id] || ""} onChange={(event) => setReprintReasons((current) => ({ ...current, [batch.id]: event.target.value }))} disabled={!canReprint} placeholder="سبب إعادة الطباعة" className="rounded-xl border px-3 py-2 text-sm disabled:bg-slate-100" />}
                                    <button type="button" disabled={!permissions.can_print || (printed && !canReprint) || busy === `print:${batch.id}`} onClick={() => print(batch)} className={`rounded-xl px-4 py-2 text-sm font-black ${printed ? "border border-amber-300 bg-amber-50 text-amber-900" : "bg-violet-700 text-white"} disabled:opacity-50`}><Printer className="ml-1 inline" />{printed ? `إعادة طباعة (${batch.print_count})` : "طباعة الملف"}</button>
                                    {printed && batch.status !== "packed" && batch.status !== "handed_off" && <button type="button" disabled={!permissions.can_pack || busy === `pack:${batch.id}`} onClick={() => pack(batch)} className="rounded-xl bg-sky-700 px-4 py-2 text-sm font-black text-white disabled:opacity-50"><Package className="ml-1 inline" /> تأكيد التغليف</button>}
                                    {batch.status === "packed" && <button type="button" disabled={!permissions.can_handoff || busy === `handoff:${batch.id}`} onClick={() => handoff(batch)} className="rounded-xl bg-emerald-700 px-4 py-2 text-sm font-black text-white disabled:opacity-50"><Truck className="ml-1 inline" /> تسليم للناقل</button>}
                                    {batch.status === "handed_off" && <span className="rounded-xl bg-emerald-50 px-3 py-2 text-sm font-black text-emerald-800"><CheckCircle className="ml-1 inline" /> تم التسليم</span>}
                                </div>
                            </div>
                            {printed && !permissions.can_reprint && <p className="mt-3 text-xs text-amber-800"><WarningCircle className="ml-1 inline" /> إعادة الطباعة تحتاج صلاحية مستقلة من المالك.</p>}
                        </article>
                    );
                })}
            </section>
        </div>
    );
}
