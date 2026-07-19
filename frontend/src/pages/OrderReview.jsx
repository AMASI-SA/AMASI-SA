import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowLeft, CheckCircle, Clipboard, FloppyDisk, MagnifyingGlass,
    SpinnerGap, WarningCircle, WhatsappLogo, X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    completeOrderReview,
    getOrderReview,
    listPendingOrderReviews,
    updateOrderReviewItem,
} from "../services/orderReviewEngine";

function money(value, currency = "SAR") {
    const amount = Number(value || 0);
    return `${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

function paymentText(order) {
    return String(order?.payment?.method_native || order?.payment?.method || "غير محدد").trim();
}

function rowTone(order) {
    const method = paymentText(order).toLowerCase();
    if (method === "cod" || method.includes("cash on delivery") || method.includes("الدفع عند الاستلام")) {
        return "bg-rose-50 hover:bg-rose-100/70";
    }
    if (method.includes("bank transfer") || method.includes("تحويل بنكي") || method.includes("حوالة بنكية")) {
        return "bg-amber-50 hover:bg-amber-100/70";
    }
    return "bg-white hover:bg-slate-50";
}

function copy(value, label = "القيمة") {
    navigator.clipboard.writeText(String(value || ""));
    toast.success(`تم نسخ ${label}`);
}

function Field({ label, value, dir }) {
    return (
        <div className="rounded-xl bg-slate-50 p-3">
            <div className="text-xs font-bold text-slate-400">{label}</div>
            <div className="mt-1 break-words font-semibold text-slate-800" dir={dir}>{value || "—"}</div>
        </div>
    );
}

function ProductReviewCard({ item, workflowRevision, orderNumber, onChanged }) {
    const [preparationNote, setPreparationNote] = useState(item.preparation_note || "");
    const [internalNote, setInternalNote] = useState(item.internal_note || "");
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        setPreparationNote(item.preparation_note || "");
        setInternalNote(item.internal_note || "");
    }, [item.internal_note, item.preparation_note]);

    const save = async (patch, successMessage) => {
        setBusy(true);
        try {
            const next = await updateOrderReviewItem(orderNumber, item.order_item_id, {
                expected_revision: workflowRevision,
                ...patch,
            });
            onChanged(next);
            toast.success(successMessage);
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy(false);
        }
    };

    const options = Array.isArray(item.options) ? item.options : [];
    return (
        <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="grid gap-4 p-4 sm:grid-cols-[150px_minmax(0,1fr)]">
                <div>
                    <div className="aspect-square overflow-hidden rounded-xl bg-slate-100">
                        {item.selected_image_url ? (
                            <img src={item.selected_image_url} alt={item.name} className="h-full w-full object-cover" />
                        ) : (
                            <div className="flex h-full items-center justify-center text-sm text-slate-400">لا توجد صورة</div>
                        )}
                    </div>
                    <div className="mt-2 text-center text-[11px] font-bold text-teal-700">
                        {item.selected_image_source === "learned_preference"
                            ? "صورة محفوظة لنفس الخيارات"
                            : item.selected_image_source === "manual" ? "اختيار الموظف" : "الصورة الافتراضية"}
                    </div>
                </div>
                <div className="min-w-0">
                    <h3 className="text-lg font-extrabold text-slate-900">{item.name}</h3>
                    <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-500">
                        <span>SKU: <b dir="ltr">{item.sku || "—"}</b></span>
                        <span>الكمية: <b>{item.quantity}</b></span>
                        {item.color && <span>اللون: <b>{item.color}</b></span>}
                        {item.size && <span>المقاس: <b>{item.size}</b></span>}
                    </div>
                    {options.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                            {options.map((option, index) => (
                                <span key={`${option.name}-${index}`} className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-bold text-violet-800">
                                    {option.name}: {String(option.value ?? "")}
                                </span>
                            ))}
                        </div>
                    )}
                    <div className="mt-4">
                        <div className="mb-2 text-sm font-extrabold text-slate-700">اختر صورة التجهيز المطابقة</div>
                        <div className="flex gap-2 overflow-x-auto pb-2">
                            {(item.gallery || []).map((url, index) => {
                                const selected = url === item.selected_image_url;
                                return (
                                    <button
                                        type="button"
                                        key={`${url}-${index}`}
                                        disabled={busy || selected}
                                        onClick={() => save({ selected_image_url: url }, "تم حفظ الصورة لهذه الخيارات وستُستخدم تلقائيًا لاحقًا.")}
                                        className={`h-20 w-20 shrink-0 overflow-hidden rounded-xl border-2 ${selected ? "border-teal-500 ring-2 ring-teal-100" : "border-slate-200 hover:border-violet-400"}`}
                                        aria-label={`اختيار صورة التجهيز رقم ${index + 1}`}
                                    >
                                        <img src={url} alt="" className="h-full w-full object-cover" />
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                </div>
            </div>
            <div className="grid gap-3 border-t bg-slate-50/70 p-4 sm:grid-cols-2">
                <label className="block">
                    <span className="mb-1 block text-sm font-extrabold text-slate-700">تعليمات التجهيز — تُطبع مع القطعة</span>
                    <textarea value={preparationNote} onChange={(event) => setPreparationNote(event.target.value)} maxLength={1200} className="min-h-24 w-full rounded-xl border border-slate-200 bg-white p-3 outline-none focus:border-teal-500" placeholder="مثال: اعتماد الصورة الفضية، مع تغليف الورد…" />
                </label>
                <label className="block">
                    <span className="mb-1 block text-sm font-extrabold text-slate-700">ملاحظة داخلية — لا تُطبع</span>
                    <textarea value={internalNote} onChange={(event) => setInternalNote(event.target.value)} maxLength={2000} className="min-h-24 w-full rounded-xl border border-slate-200 bg-white p-3 outline-none focus:border-violet-500" placeholder="ملاحظة للموظفين فقط" />
                </label>
                <button
                    type="button"
                    disabled={busy}
                    onClick={() => save({ preparation_note: preparationNote.trim() || null, internal_note: internalNote.trim() || null }, "تم حفظ ملاحظات المنتج.")}
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-4 py-3 font-bold text-white disabled:opacity-50 sm:col-span-2 sm:justify-self-end"
                >
                    {busy ? <SpinnerGap className="animate-spin" /> : <FloppyDisk />}
                    حفظ ملاحظات المنتج
                </button>
            </div>
        </article>
    );
}

function ReviewDrawer({ orderNumber, onClose, onCompleted }) {
    const [detail, setDetail] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [completing, setCompleting] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            setDetail(await getOrderReview(orderNumber));
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, [orderNumber]);

    useEffect(() => { load(); }, [load]);

    const order = detail?.order;
    const customer = order?.customer || {};
    const payment = order?.payment || {};
    const shipping = order?.shipping || {};
    const address = shipping.address || customer.shipping_address || {};
    const whatsapp = String(customer.mobile || "").replace(/\D/g, "");

    const finish = async () => {
        setCompleting(true);
        try {
            const result = await completeOrderReview(orderNumber, detail.revision);
            if (result.salla_status_sync === "pending") {
                toast.warning("تم اعتماد المراجعة في ميزان، وتغيير حالة سلة بانتظار المزامنة.");
            } else {
                toast.success("تمت مراجعة الطلب وانتقل من هذه المرحلة.");
            }
            onCompleted(orderNumber);
        } catch (finishError) {
            toast.error(finishError.message);
            await load();
        } finally {
            setCompleting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-[80] flex bg-slate-950/45" dir="rtl">
            <button type="button" className="hidden flex-1 md:block" onClick={onClose} aria-label="إغلاق" />
            <section className="h-full w-full overflow-y-auto bg-slate-50 shadow-2xl md:max-w-5xl">
                <header className="sticky top-0 z-10 flex items-center justify-between border-b bg-white/95 px-5 py-4 backdrop-blur">
                    <div>
                        <h2 className="text-xl font-extrabold">مراجعة الطلب #{orderNumber}</h2>
                        <p className="text-sm text-slate-500">المرحلة الأولى — لا يتم إنشاء ملف تجهيز هنا</p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl border p-2"><X size={22} /></button>
                </header>
                {loading ? (
                    <div className="flex min-h-96 items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-600" /></div>
                ) : error ? (
                    <div className="m-6 rounded-2xl border border-rose-200 bg-rose-50 p-5 text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>
                ) : (
                    <div className="space-y-5 p-4 sm:p-6">
                        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-white p-4">
                            <div><b className="text-lg">#{order.order_number}</b><div className="text-sm text-slate-500">{new Date(order.created_at).toLocaleString("ar-SA")}</div></div>
                            <button type="button" onClick={() => copy(order.order_number, "رقم الطلب")} className="inline-flex items-center gap-2 rounded-xl border px-3 py-2 font-bold"><Clipboard /> نسخ رقم الطلب</button>
                        </div>

                        <section className="rounded-2xl border bg-white p-4">
                            <h3 className="mb-3 text-lg font-extrabold">معلومات العميل</h3>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                <Field label="الاسم" value={customer.name} />
                                <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs font-bold text-slate-400">رقم الجوال</div><div className="mt-1 flex items-center gap-2 font-semibold" dir="ltr"><span>{customer.mobile || "—"}</span>{whatsapp && <a href={`https://wa.me/${whatsapp}`} target="_blank" rel="noreferrer" className="text-emerald-600"><WhatsappLogo size={22} weight="fill" /></a>}</div></div>
                                <Field label="البريد" value={customer.email} dir="ltr" />
                                <Field label="الدولة" value={address.country || customer.shipping_address?.country} />
                                <Field label="المدينة" value={address.city || customer.shipping_address?.city} />
                            </div>
                        </section>

                        <div className="grid gap-5 lg:grid-cols-2">
                            <section className="rounded-2xl border bg-white p-4">
                                <h3 className="mb-3 text-lg font-extrabold">معلومات الدفع</h3>
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="طريقة الدفع" value={paymentText(order)} />
                                    <Field label="إجمالي الطلب" value={money(order.totals?.total, order.totals?.currency)} dir="ltr" />
                                    <Field label="المبلغ المدفوع" value={money(payment.paid_amount, order.totals?.currency)} dir="ltr" />
                                    <Field label="المبلغ المتبقي" value={money(payment.remaining_amount, order.totals?.currency)} dir="ltr" />
                                </div>
                            </section>
                            <section className="rounded-2xl border bg-white p-4">
                                <h3 className="mb-3 text-lg font-extrabold">معلومات الشحن</h3>
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="شركة الشحن" value={shipping.company || shipping.method} />
                                    <Field label="المدينة" value={address.city} />
                                    <Field label="الحي" value={address.district} />
                                    <Field label="الشارع" value={address.street} />
                                </div>
                            </section>
                        </div>

                        <section>
                            <div className="mb-3 flex items-center justify-between"><h3 className="text-xl font-extrabold">منتجات الطلب</h3><span className="rounded-full bg-violet-100 px-3 py-1 text-sm font-bold text-violet-800">{detail.items.length} منتج</span></div>
                            <div className="grid gap-4 xl:grid-cols-3">
                                {detail.items.map((item) => (
                                    <ProductReviewCard key={item.order_item_id} item={item} workflowRevision={detail.revision} orderNumber={orderNumber} onChanged={setDetail} />
                                ))}
                            </div>
                        </section>

                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                            <p className="text-sm font-semibold text-emerald-900">اعتماد المراجعة يجمّد الصورة والتعليمات المختارة، ويخرج الطلب من هذه الصفحة نهائيًا. لا يتم إنشاء ملف تجهيز أو بوليصة شحن في هذه المرحلة.</p>
                            <button type="button" disabled={completing} onClick={finish} className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-5 py-3 text-lg font-extrabold text-white disabled:opacity-50 sm:w-auto">
                                {completing ? <SpinnerGap className="animate-spin" /> : <CheckCircle weight="fill" />}
                                تمت المراجعة
                            </button>
                        </div>
                    </div>
                )}
            </section>
        </div>
    );
}

export default function OrderReview() {
    const [orders, setOrders] = useState([]);
    const [nextCursor, setNextCursor] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [selectedOrder, setSelectedOrder] = useState(null);
    const [search, setSearch] = useState("");

    const load = useCallback(async ({ cursor = null, append = false } = {}) => {
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

    useEffect(() => { load(); }, [load]);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return orders;
        return orders.filter((order) => [order.order_number, order.customer?.name, order.customer?.mobile, paymentText(order)].some((value) => String(value || "").toLowerCase().includes(q)));
    }, [orders, search]);

    return (
        <div className="mx-auto max-w-7xl space-y-5 p-4" dir="rtl">
            <header className="rounded-2xl border bg-white p-5 shadow-sm">
                <h1 className="text-2xl font-extrabold text-slate-900">طلبات بانتظار المراجعة</h1>
                <p className="mt-1 text-sm text-slate-500">المرحلة الأولى من محرك تجهيز الطلب — مراجعة بيانات العميل والدفع والشحن والمنتجات.</p>
                <div className="relative mt-4 max-w-xl">
                    <MagnifyingGlass className="absolute right-3 top-3 text-slate-400" />
                    <input value={search} onChange={(event) => setSearch(event.target.value)} className="w-full rounded-xl border py-2.5 pr-10 pl-3 outline-none focus:border-violet-500" placeholder="ابحث برقم الطلب أو العميل أو طريقة الدفع" />
                </div>
            </header>

            <section className="overflow-hidden rounded-2xl border bg-white shadow-sm">
                <div className="hidden grid-cols-[70px_1fr_1fr_1fr_1fr] gap-3 border-b bg-slate-100 px-4 py-3 text-sm font-extrabold text-slate-600 md:grid">
                    <div>تفاصيل</div><div>رقم الطلب</div><div>تاريخ الطلب</div><div>طريقة الدفع</div><div>العميل</div>
                </div>
                {loading ? (
                    <div className="flex min-h-64 items-center justify-center"><SpinnerGap size={32} className="animate-spin text-violet-600" /></div>
                ) : error ? (
                    <div className="m-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>
                ) : filtered.length === 0 ? (
                    <div className="flex min-h-64 items-center justify-center text-slate-500">لا توجد طلبات بانتظار المراجعة</div>
                ) : filtered.map((order) => (
                    <button key={order.order_number} type="button" onClick={() => setSelectedOrder(order.order_number)} className={`grid w-full gap-2 border-b px-4 py-4 text-right last:border-b-0 md:grid-cols-[70px_1fr_1fr_1fr_1fr] md:items-center ${rowTone(order)}`}>
                        <span className="inline-flex items-center gap-1 font-bold text-violet-700"><ArrowLeft /> <span className="md:hidden">التفاصيل</span></span>
                        <span className="font-extrabold" dir="ltr">#{order.order_number}</span>
                        <span className="text-sm text-slate-600">{new Date(order.created_at).toLocaleString("ar-SA")}</span>
                        <span className="font-bold">{paymentText(order)}</span>
                        <span>{order.customer?.name || "عميل بدون اسم"}</span>
                    </button>
                ))}
            </section>
            {nextCursor && (
                <button
                    type="button"
                    disabled={loading}
                    onClick={() => load({ cursor: nextCursor, append: true })}
                    className="mx-auto flex items-center gap-2 rounded-xl border bg-white px-5 py-3 font-bold text-violet-700 disabled:opacity-50"
                >
                    {loading && <SpinnerGap className="animate-spin" />}
                    تحميل طلبات إضافية
                </button>
            )}
            <div className="rounded-xl border border-slate-200 bg-white p-3 text-sm text-slate-600">
                <b>دليل الألوان:</b> أحمر للدفع عند الاستلام، أصفر للتحويل البنكي، وأبيض لبقية طرق الدفع.
            </div>
            {selectedOrder && (
                <ReviewDrawer
                    orderNumber={selectedOrder}
                    onClose={() => setSelectedOrder(null)}
                    onCompleted={(orderNumber) => {
                        setOrders((current) => current.filter((order) => order.order_number !== orderNumber));
                        setSelectedOrder(null);
                    }}
                />
            )}
        </div>
    );
}
