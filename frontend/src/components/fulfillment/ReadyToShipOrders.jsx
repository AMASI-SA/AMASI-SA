import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowLeft,
    Camera,
    CheckCircle,
    ClipboardText,
    DownloadSimple,
    MagnifyingGlass,
    Package,
    Printer,
    SpinnerGap,
    Truck,
    UserCircle,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    confirmFulfillmentBatchHandoff,
    confirmFulfillmentBatchPacked,
    listFulfillmentBatches,
    listReadyToShipOrders,
    printFulfillmentBatch,
} from "../../services/fulfillmentV2";
import {
    markAssemblyPieceReady,
    newAssemblyReadyRequestId,
    searchAssemblyOrder,
} from "../../services/preparationWorkService";
import { CameraScanner } from "./PreparationEmployeeReceivingWorkspace";
import { printStoreCourierLabel } from "../../lib/storeCourierLabelPrint";
import CustomerServiceInstructionBanner from "./CustomerServiceInstructionBanner";

const ASSEMBLY_BLOCKERS = {
    assembly_piece_preparation_receipt_required: "استلم المنتج من موظف التجهيز أولًا",
    assembly_piece_stopped: "المنتج متوقف",
};

const STATUS_LABELS = {
    claimed: "بانتظار الطباعة",
    printed: "تمت الطباعة",
    packed: "تم التجميع والتغليف",
    handed_off: "سُلّمت للناقل",
};

function ProductImage({ piece }) {
    if (piece.image_url) {
        return (
            <img
                src={piece.image_url}
                alt={piece.product_name || "منتج"}
                className="h-24 w-24 shrink-0 rounded-2xl border border-slate-200 object-cover sm:h-28 sm:w-28"
            />
        );
    }
    return (
        <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-2xl bg-slate-100 text-slate-400 sm:h-28 sm:w-28">
            <Package size={38} weight="duotone" />
        </div>
    );
}

export function AssemblyProductCard({ piece, busy, onReady, onUpdated }) {
    return (
        <article
            className={`overflow-hidden rounded-3xl border-2 bg-white shadow-sm ${piece.search_match ? "border-violet-500 ring-4 ring-violet-100" : piece.assembly_ready ? "border-emerald-300" : "border-slate-200"}`}
            data-testid="assembly-product-card"
        >
            {piece.search_match && (
                <div className="bg-violet-700 px-4 py-2 text-center text-xs font-black text-white">
                    هذا هو المنتج الذي تم تصويره
                </div>
            )}
            <div className="p-4">
                <div className="flex items-start gap-3">
                    <ProductImage piece={piece} />
                    <div className="min-w-0 flex-1">
                        <div className="text-[10px] font-black text-slate-400">القطعة {piece.unit_index || "—"}</div>
                        <h3 className="mt-1 text-lg font-black leading-7 text-slate-950">{piece.product_name || "منتج"}</h3>
                        {piece.sku && <div className="mt-1 break-all text-xs font-bold text-slate-500">SKU: {piece.sku}</div>}
                        <div className="mt-2 inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-black text-slate-700">
                            <UserCircle size={15} weight="fill" /> جهّزه: {piece.responsible_employee_name || "—"}
                        </div>
                    </div>
                </div>

                <div className="mt-4 rounded-2xl bg-slate-50 p-3">
                    <div className="text-[11px] font-black text-slate-500">مواصفات العميل</div>
                    {piece.specifications?.length ? (
                        <div className="mt-2 grid grid-cols-2 gap-2">
                            {piece.specifications.map((spec, index) => (
                                <div key={`${spec.name}-${spec.value}-${index}`} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                    <span className="block text-[10px] font-bold text-slate-400">{spec.name}</span>
                                    <span className="mt-0.5 block text-sm font-black text-slate-900">{spec.value}</span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="mt-1 text-sm font-bold text-slate-600">لا توجد خيارات خاصة لهذا المنتج.</div>
                    )}
                </div>

                {!!piece.services?.length && (
                    <div className="mt-3 rounded-2xl border border-violet-100 bg-violet-50 p-3">
                        <div className="text-[11px] font-black text-violet-700">الخدمات المنفذة</div>
                        <div className="mt-2 flex flex-wrap gap-2">
                            {piece.services.map((service, index) => (
                                <span key={`${service.name}-${index}`} className="rounded-full bg-white px-3 py-1 text-xs font-black text-violet-950">
                                    {service.name}
                                </span>
                            ))}
                        </div>
                    </div>
                )}

                <div className="mt-3"><CustomerServiceInstructionBanner instructions={piece.customer_service_instructions || []} stage="assembly_labeling" onUpdated={onUpdated} /></div>

                {piece.can_mark_ready ? (
                    <button
                        type="button"
                        onClick={() => onReady(piece)}
                        disabled={busy}
                        className="mt-4 inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-emerald-700 px-4 text-lg font-black text-white shadow-sm transition active:scale-[0.99] disabled:opacity-60"
                        data-testid="mark-assembly-piece-ready"
                    >
                        {busy ? <SpinnerGap size={24} className="animate-spin" /> : <CheckCircle size={25} weight="fill" />}
                        {busy ? "جاري الحفظ..." : "جاهز"}
                    </button>
                ) : piece.assembly_ready ? (
                    <div className="mt-4 flex min-h-14 items-center justify-center gap-2 rounded-2xl bg-emerald-50 px-4 text-base font-black text-emerald-800">
                        <CheckCircle size={25} weight="fill" /> تم — جاهز
                    </div>
                ) : (
                    <div className="mt-4 flex min-h-14 items-center justify-center gap-2 rounded-2xl bg-amber-50 px-4 text-center text-sm font-black text-amber-900">
                        <WarningCircle size={22} weight="fill" /> {ASSEMBLY_BLOCKERS[piece.assembly_blocker_code] || "المنتج غير جاهز"}
                    </div>
                )}
            </div>
        </article>
    );
}

function ReadyOrderCard({ order, onOpen }) {
    const total = Number(order.assembly_piece_count || order.items_count || 0);
    const ready = Number(order.assembly_ready_count || 0);
    return (
        <article className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
            <button type="button" onClick={() => onOpen(order.order_number)} className="w-full text-right">
                <div className="flex items-start justify-between gap-3">
                    <div>
                        <div className="text-xs font-black text-slate-400">طلب جاهز للتجميع</div>
                        <div className="mt-1 text-xl font-black text-slate-950">#{order.order_number}</div>
                        <div className="mt-1 text-sm font-bold text-slate-600">{order.customer_name || "—"} · {order.city || "—"}</div>
                    </div>
                    <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-700">
                        <Package size={27} weight="duotone" />
                    </span>
                </div>
                <div className="mt-4 flex items-center justify-between rounded-2xl bg-slate-50 px-3 py-2 text-xs font-black">
                    <span>{total || order.items_count} منتجات</span>
                    <span className="text-emerald-700">جاهز {ready} من {total || order.items_count}</span>
                </div>
                <div className="mt-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 px-4 text-sm font-black text-white">
                    عرض كل منتجات الطلب <ArrowLeft size={18} weight="bold" />
                </div>
            </button>
        </article>
    );
}

export default function ReadyToShipOrders() {
    const [orders, setOrders] = useState([]);
    const [batches, setBatches] = useState([]);
    const [permissions, setPermissions] = useState({});
    const [query, setQuery] = useState("");
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(true);
    const [searching, setSearching] = useState(false);
    const [cameraOpen, setCameraOpen] = useState(false);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [success, setSuccess] = useState("");
    const [reprintReasons, setReprintReasons] = useState({});

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [queue, batchResult] = await Promise.all([
                listReadyToShipOrders({ limit: 100 }),
                listFulfillmentBatches({ limit: 50 }),
            ]);
            setOrders((queue.items || []).filter(
                (order) => order.ready_to_ship_source === "preparation_receipt",
            ));
            setPermissions(queue.permissions || {});
            setBatches(batchResult.items || []);
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const openOrder = useCallback(async (rawValue) => {
        const value = String(rawValue ?? query).trim();
        if (!value) {
            setError("اكتب رقم الطلب أو افتح الكاميرا لتصوير المنتج.");
            return null;
        }
        setQuery(value);
        setSearching(true);
        setError("");
        setSuccess("");
        try {
            const data = await searchAssemblyOrder(value);
            setResult(data);
            return data;
        } catch (searchError) {
            setResult(null);
            setError(searchError.message);
            return null;
        } finally {
            setSearching(false);
        }
    }, [query]);

    const handleDetected = useCallback(async (value) => {
        setCameraOpen(false);
        await openOrder(value);
    }, [openOrder]);

    const handleReady = async (piece) => {
        setBusy(`ready:${piece.piece_id}`);
        setError("");
        setSuccess("");
        try {
            const response = await markAssemblyPieceReady(
                piece.piece_id,
                newAssemblyReadyRequestId(),
            );
            const refreshed = await searchAssemblyOrder(result.order_number);
            setResult({
                ...refreshed,
                progress: response.progress,
                carrier_label: response.carrier_label,
            });
            if (response.progress?.order_completed) {
                if (response.carrier_label?.ready) {
                    setSuccess("اكتملت المنتجات، وتحول الطلب في سلة إلى تم التنفيذ، ووصلت البوليصة.");
                } else if (response.carrier_label?.order_status_completed) {
                    setSuccess("اكتملت المنتجات وتحول الطلب في سلة إلى تم التنفيذ. ننتظر رابط البوليصة من شركة الشحن.");
                } else {
                    setSuccess("اكتملت المنتجات داخل ميزان. افتح تم التنفيذ لإعادة ربط سلة وإصدار البوليصة.");
                }
            } else {
                setSuccess(`تم تسجيل المنتج جاهزًا — المتبقي ${response.progress?.total_count - response.progress?.ready_count}.`);
            }
            await load();
        } catch (readyError) {
            setError(readyError.message);
        } finally {
            setBusy("");
        }
    };

    const batchById = useMemo(
        () => Object.fromEntries(batches.map((batch) => [batch.id, batch])),
        [batches],
    );

    const print = async (batchId) => {
        if (!batchId) return;
        const batch = batchById[batchId] || {};
        const isReprint = Number(batch.print_count || 0) > 0;
        const reason = String(reprintReasons[batchId] || "").trim();
        if (isReprint && !reason) {
            setError("اكتب سبب إعادة الطباعة أولًا.");
            return;
        }
        setBusy(`print:${batchId}`);
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

    const pack = async (batch) => {
        setBusy(`pack:${batch.id}`);
        try {
            await confirmFulfillmentBatchPacked(batch.id);
            toast.success("تم تأكيد التجميع والتغليف إن وُجد");
            await load();
        } catch (packError) {
            setError(packError.message);
        } finally {
            setBusy("");
        }
    };

    const handoff = async (batch) => {
        setBusy(`handoff:${batch.id}`);
        try {
            await confirmFulfillmentBatchHandoff(batch.id);
            toast.success("تم تأكيد تسليم الدفعة لشركة الشحن");
            await load();
        } catch (handoffError) {
            setError(handoffError.message);
        } finally {
            setBusy("");
        }
    };

    const completed = Boolean(
        result?.progress?.order_completed
        || result?.summary?.all_ready
        || result?.stage === "completed",
    );
    const carrierLabel = result?.carrier_label || {};

    return (
        <section className="mx-auto w-full max-w-3xl space-y-4" dir="rtl" data-testid="ready-to-ship-orders">
            <div className="overflow-hidden rounded-3xl border border-violet-200 bg-white shadow-sm">
                <div className="bg-violet-700 px-4 py-5 text-white sm:px-6">
                    <div className="text-xs font-black text-violet-100">المرحلة الثالثة</div>
                    <h2 className="mt-1 text-2xl font-black">التجميع والعنونة</h2>
                    <p className="mt-2 text-sm font-bold leading-6 text-violet-100">افتح طلبًا جاهزًا أو صوّر أي منتج؛ ستظهر كل منتجات الطلب ومعلوماتها.</p>
                </div>
                <div className="grid grid-cols-3 border-b border-slate-100 bg-slate-50 text-center text-[10px] font-black sm:text-xs">
                    <div className="px-2 py-3 text-slate-500"><CheckCircle className="mx-auto mb-1" size={18} weight="fill" />الاستلام من التجهيز</div>
                    <div className="border-x border-violet-200 bg-violet-50 px-2 py-3 text-violet-800"><Package className="mx-auto mb-1" size={18} weight="fill" />التجميع والعنونة</div>
                    <div className="px-2 py-3 text-slate-500"><ClipboardText className="mx-auto mb-1" size={18} weight="duotone" />تم التنفيذ</div>
                </div>
                <form onSubmit={(event) => { event.preventDefault(); void openOrder(); }} className="p-4 sm:p-5">
                    <label htmlFor="assembly-search" className="mb-2 block text-sm font-black text-slate-900">ابحث برقم الطلب أو المنتج</label>
                    <div className="flex gap-2">
                        <div className="relative min-w-0 flex-1">
                            <MagnifyingGlass size={21} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" weight="bold" />
                            <input id="assembly-search" value={query} onChange={(event) => setQuery(event.target.value)} inputMode="search" autoComplete="off" placeholder="رقم الطلب أو باركود المنتج" className="h-14 w-full rounded-2xl border-2 border-slate-200 bg-white pr-11 pl-3 text-base font-black text-slate-950 outline-none placeholder:text-slate-400 focus:border-violet-500" />
                        </div>
                        <button type="button" onClick={() => { setError(""); setCameraOpen(true); }} className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-violet-700 text-white shadow-sm" aria-label="فتح الكاميرا للبحث عن منتج التجميع" data-testid="open-assembly-camera">
                            <Camera size={27} weight="fill" />
                        </button>
                    </div>
                    <button type="submit" disabled={searching} className="mt-2 inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 text-sm font-black text-white disabled:opacity-50">
                        {searching ? <><SpinnerGap size={20} className="animate-spin" />جاري البحث</> : <><MagnifyingGlass size={20} weight="bold" />عرض منتجات الطلب</>}
                    </button>
                </form>
            </div>

            {error && <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black leading-6 text-rose-900" role="alert"><WarningCircle size={22} className="mt-0.5 shrink-0" weight="fill" />{error}</div>}
            {success && <div className="flex items-start gap-2 rounded-2xl border border-emerald-300 bg-emerald-50 p-4 text-sm font-black leading-6 text-emerald-900" role="status"><CheckCircle size={22} className="mt-0.5 shrink-0" weight="fill" />{success}</div>}

            {searching && <div className="flex min-h-40 items-center justify-center rounded-3xl border bg-white"><SpinnerGap size={34} className="animate-spin text-violet-700" /></div>}

            {result && !searching && (
                <div className="space-y-3" data-testid="assembly-order-products">
                    <button
                        type="button"
                        onClick={() => { setResult(null); setSuccess(""); setError(""); }}
                        className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border-2 border-slate-200 bg-white px-4 text-sm font-black text-slate-800 shadow-sm"
                    >
                        العودة إلى الطلبات الجاهزة
                    </button>
                    <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
                        <div><div className="text-[10px] font-black text-slate-400">الطلب</div><div className="text-xl font-black">#{result.order_number}</div></div>
                        <div className="text-left"><div className="text-[10px] font-black text-slate-400">جاهز</div><div className="text-base font-black text-emerald-700">{result.summary?.ready || 0} من {result.summary?.total || 0}</div></div>
                    </div>

                    {completed && (
                        <div className="rounded-3xl border-2 border-emerald-400 bg-emerald-50 p-5 text-center" data-testid="assembly-order-completed">
                            <CheckCircle size={44} weight="fill" className="mx-auto text-emerald-700" />
                            <h3 className="mt-2 text-xl font-black text-emerald-950">انتقل الطلب إلى تم التنفيذ</h3>
                            <p className="mt-1 text-sm font-bold text-emerald-800">اكتملت كل المنتجات. الطباعة أدناه هي بوليصة الناقل الرسمية، أو بوليصة ميزان إذا كان الناقل مندوب المتجر.</p>
                            {carrierLabel.ready && carrierLabel.label_url && (
                                <a href={carrierLabel.label_url} target="_blank" rel="noopener noreferrer" download className="mt-3 inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-violet-700 px-4 text-base font-black text-white" data-testid="assembly-download-official-carrier-label">
                                    <DownloadSimple size={24} weight="bold" /> تحميل بوليصة {carrierLabel.courier_name || "شركة الشحن"}
                                </a>
                            )}
                            {carrierLabel.ready && carrierLabel.label_type === "store_courier" && carrierLabel.print_data?.qr_code && (
                                <button type="button" onClick={() => {
                                    const printWindow = window.open("about:blank", "_blank");
                                    if (printWindow) printWindow.opener = null;
                                    if (!printStoreCourierLabel(printWindow, carrierLabel.print_data)) printWindow?.close();
                                }} className="mt-3 inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-violet-700 px-4 text-base font-black text-white" data-testid="assembly-print-store-courier-label">
                                    <Printer size={24} weight="fill" /> طباعة بوليصة مندوب المتجر
                                </button>
                            )}
                            {!carrierLabel.ready && (
                                <div className="mt-3 rounded-2xl bg-amber-50 px-3 py-3 text-sm font-black text-amber-900">
                                    {carrierLabel.message || "لم يصل رابط البوليصة بعد. افتح تم التنفيذ لإعادة المحاولة دون إعادة تجهيز المنتجات."}
                                </div>
                            )}
                            <Link to="/fulfillment-v2?stage=completed" className="mt-2 inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border border-emerald-300 bg-white px-4 text-sm font-black text-emerald-900">فتح تم التنفيذ <ArrowLeft size={19} weight="bold" /></Link>
                        </div>
                    )}

                    {result.pieces?.map((piece) => (
                        <AssemblyProductCard key={piece.piece_id} piece={piece} busy={busy === `ready:${piece.piece_id}`} onReady={handleReady} onUpdated={() => openOrder(result.order_number)} />
                    ))}
                </div>
            )}

            {!result && (
                <section className="space-y-3">
                    <div className="flex items-center justify-between px-1">
                        <div><h3 className="text-lg font-black text-slate-950">الطلبات الجاهزة المكتملة</h3><p className="text-xs font-bold text-slate-500">اضغط على الطلب لعرض كل منتجاته.</p></div>
                        <span className="rounded-full bg-violet-100 px-3 py-1 text-xs font-black text-violet-800">{orders.length}</span>
                    </div>
                    {loading ? (
                        <div className="flex min-h-36 items-center justify-center rounded-3xl border bg-white"><SpinnerGap size={32} className="animate-spin text-violet-700" /></div>
                    ) : !orders.length ? (
                        <div className="rounded-3xl border-2 border-dashed border-slate-200 bg-white p-8 text-center text-slate-500"><CheckCircle size={42} className="mx-auto mb-2 text-emerald-500" />لا توجد طلبات جاهزة للتجميع الآن.</div>
                    ) : (
                        <div className="grid gap-3 sm:grid-cols-2">
                            {orders.map((order) => <ReadyOrderCard key={order.order_number} order={order} onOpen={openOrder} />)}
                        </div>
                    )}
                </section>
            )}

            {!!batches.length && (
                <details className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm">
                    <summary className="cursor-pointer text-sm font-black text-slate-800">دفعات الطباعة والتسليم السابقة ({batches.length})</summary>
                    <div className="mt-4 space-y-3">
                        {batches.map((batch) => {
                            const printed = Number(batch.print_count || 0) > 0;
                            return (
                                <article key={batch.id} className="rounded-2xl border bg-slate-50 p-3">
                                    <div className="font-black">{(batch.order_numbers || []).map((value) => `#${value}`).join("، ") || batch.id}</div>
                                    <div className="mt-1 text-xs font-bold text-slate-500">{STATUS_LABELS[batch.status] || batch.status}</div>
                                    {printed && <input value={reprintReasons[batch.id] || ""} onChange={(event) => setReprintReasons((current) => ({ ...current, [batch.id]: event.target.value }))} placeholder="سبب إعادة الطباعة" className="mt-2 h-11 w-full rounded-xl border bg-white px-3 text-sm" />}
                                    <div className="mt-2 grid gap-2 sm:grid-cols-3">
                                        <button type="button" disabled={!permissions.can_print || busy === `print:${batch.id}`} onClick={() => print(batch.id)} className="min-h-11 rounded-xl bg-violet-700 px-3 text-sm font-black text-white disabled:opacity-50"><Printer className="ml-1 inline" />{printed ? "إعادة الطباعة" : "طباعة الشحنة"}</button>
                                        {printed && batch.status !== "packed" && batch.status !== "handed_off" && <button type="button" disabled={!permissions.can_pack || busy === `pack:${batch.id}`} onClick={() => pack(batch)} className="min-h-11 rounded-xl bg-sky-700 px-3 text-sm font-black text-white disabled:opacity-50"><Package className="ml-1 inline" />تأكيد التجميع</button>}
                                        {batch.status === "packed" && <button type="button" disabled={!permissions.can_handoff || busy === `handoff:${batch.id}`} onClick={() => handoff(batch)} className="min-h-11 rounded-xl bg-emerald-700 px-3 text-sm font-black text-white disabled:opacity-50"><Truck className="ml-1 inline" />تسليم للناقل</button>}
                                    </div>
                                </article>
                            );
                        })}
                    </div>
                </details>
            )}

            <div className="rounded-2xl border border-violet-200 bg-violet-50 p-4 text-sm font-bold leading-6 text-violet-950">
                التغليف ليس مرحلة مستقلة؛ يتم هنا فقط إذا احتاجه المنتج، ثم ينتقل الطلب إلى <strong>تم التنفيذ</strong>.
            </div>

            {cameraOpen && <CameraScanner onDetected={handleDetected} onClose={() => setCameraOpen(false)} />}
        </section>
    );
}
