import { useCallback, useEffect, useRef, useState } from "react";
import {
    ArrowClockwise,
    Barcode,
    CheckCircle,
    Clock,
    CurrencyCircleDollar,
    MapPin,
    Package,
    Phone,
    SpinnerGap,
    Truck,
    WarningCircle,
    WhatsappLogo,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    completeStoreCourierShipment,
    listMyStoreCourierShipments,
    pickupStoreCourierShipment,
} from "../../services/storeCourierDispatch";
import ShippingBarcodeScanner from "./ShippingBarcodeScanner";


const STAGE_COPY = {
    waiting: {
        eyebrow: "إدارة الموصلين",
        title: "الشحنات المسندة لي",
        description: "عند استلام الشحنة من المخزن صوّر QR الموجود على بوليصة أماسي؛ بعدها تنتقل مباشرة إلى جاري التوصيل.",
        empty: "لا توجد شحنات مسندة لك بانتظار الاستلام.",
        badge: "بانتظار الاستلام",
    },
    delivering: {
        eyebrow: "مسار مندوب المتجر",
        title: "جاري التوصيل",
        description: "هذه الشحنات في عهدتك الآن. استخدم بيانات العميل والعنوان ثم أكد التسليم بعد وصول الطلب فعليًا.",
        empty: "لا توجد شحنات في جاري التوصيل الآن.",
        badge: "جاري التوصيل",
    },
    delivered: {
        eyebrow: "مسار مندوب المتجر",
        title: "تم التوصيل",
        description: "سجل الشحنات التي أكملت توصيلها للعميل من حسابك.",
        empty: "لم تُسجل شحنات مكتملة التوصيل بعد.",
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


function moneyLabel(value, currency = "SAR") {
    const amount = Number(value || 0);
    try {
        return new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: currency || "SAR",
            maximumFractionDigits: 2,
        }).format(Number.isFinite(amount) ? amount : 0);
    } catch {
        return `${Number.isFinite(amount) ? amount.toFixed(2) : "0.00"} SAR`;
    }
}


function digits(value) {
    return String(value || "").replace(/\D/g, "");
}


function mapsHref(shipment) {
    const latitude = shipment.address?.latitude;
    const longitude = shipment.address?.longitude;
    const query = latitude != null && longitude != null
        ? `${latitude},${longitude}`
        : shipment.address_text || shipment.city || "";
    return query
        ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`
        : "";
}


function DeliveryConfirmation({ shipment, busy, error, onConfirm, onClose }) {
    const [note, setNote] = useState("");
    return (
        <div className="fixed inset-0 z-[115] flex items-end justify-center bg-slate-950/75 p-0 sm:items-center sm:p-4" dir="rtl" role="dialog" aria-modal="true" aria-label="تأكيد تم التوصيل">
            <section className="w-full max-w-lg overflow-hidden rounded-t-3xl bg-white shadow-2xl sm:rounded-3xl">
                <header className="flex items-start justify-between gap-3 bg-emerald-800 p-5 text-white">
                    <div>
                        <div className="text-xs font-black text-emerald-100">طلب #{shipment.order_number}</div>
                        <h3 className="mt-1 text-xl font-black">تأكيد تم التوصيل</h3>
                    </div>
                    <button type="button" onClick={onClose} disabled={busy} className="rounded-xl bg-white/10 p-2 disabled:opacity-50" aria-label="إغلاق">
                        <X size={22} weight="bold" />
                    </button>
                </header>
                <div className="space-y-4 p-5">
                    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-black leading-7 text-amber-950">
                        أكد فقط بعد تسليم الشحنة فعليًا إلى العميل؛ بعد الحفظ ستنتقل من «جاري التوصيل» إلى «تم التوصيل».
                    </div>
                    <label className="block text-sm font-black text-slate-700">
                        ملاحظة التوصيل — اختيارية
                        <textarea
                            value={note}
                            onChange={(event) => setNote(event.target.value)}
                            maxLength={500}
                            rows={3}
                            className="mt-2 w-full rounded-2xl border border-slate-300 p-3 text-sm font-bold outline-none focus:border-emerald-600"
                            placeholder="مثال: تم التسليم للعميل شخصيًا"
                        />
                    </label>
                    {error && (
                        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900" role="alert">{error}</div>
                    )}
                    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                        <button type="button" onClick={onClose} disabled={busy} className="min-h-12 rounded-xl border border-slate-200 px-5 text-sm font-black text-slate-700 disabled:opacity-50">إلغاء</button>
                        <button
                            type="button"
                            onClick={() => onConfirm(note)}
                            disabled={busy}
                            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl bg-emerald-700 px-5 text-sm font-black text-white disabled:opacity-50"
                            data-testid="confirm-store-courier-delivered"
                        >
                            {busy ? <SpinnerGap size={21} className="animate-spin" /> : <CheckCircle size={22} weight="fill" />}
                            تسجيل تم التوصيل
                        </button>
                    </div>
                </div>
            </section>
        </div>
    );
}


export default function StoreCourierMyShipments({ stage = "waiting" }) {
    const normalizedStage = ["waiting", "delivering", "delivered"].includes(stage)
        ? stage
        : "waiting";
    const copy = STAGE_COPY[normalizedStage];
    const [shipments, setShipments] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [scannerShipment, setScannerShipment] = useState(null);
    const [scannerBusy, setScannerBusy] = useState(false);
    const [scannerError, setScannerError] = useState("");
    const [deliveryShipment, setDeliveryShipment] = useState(null);
    const [deliveryBusy, setDeliveryBusy] = useState(false);
    const [deliveryError, setDeliveryError] = useState("");
    const scannerLock = useRef(false);

    const load = useCallback(async ({ silent = false } = {}) => {
        if (!silent) setLoading(true);
        setError("");
        try {
            const result = await listMyStoreCourierShipments({
                stage: normalizedStage,
                limit: 100,
            });
            setShipments(result.items || []);
        } catch (loadError) {
            if (!silent) setError(loadError.message);
        } finally {
            if (!silent) setLoading(false);
        }
    }, [normalizedStage]);

    useEffect(() => {
        setScannerShipment(null);
        setDeliveryShipment(null);
        void load();
    }, [load]);

    useEffect(() => {
        const timer = window.setInterval(() => {
            void load({ silent: true });
        }, 15000);
        return () => window.clearInterval(timer);
    }, [load]);

    const openPickupScanner = (shipment) => {
        setScannerError("");
        setScannerShipment(shipment);
    };

    const handlePickupBarcode = useCallback(async (barcode) => {
        if (!scannerShipment || scannerLock.current) return;
        scannerLock.current = true;
        setScannerBusy(true);
        setScannerError("");
        try {
            const result = await pickupStoreCourierShipment(
                scannerShipment.order_number,
                barcode,
            );
            setShipments((current) => current.filter(
                (row) => row.order_number !== scannerShipment.order_number,
            ));
            toast.success(
                result.already_picked_up
                    ? "الشحنة موجودة في جاري التوصيل مسبقًا"
                    : "تم استلام الشحنة ونقلها إلى جاري التوصيل",
            );
            setScannerShipment(null);
        } catch (pickupError) {
            setScannerError(pickupError.message);
        } finally {
            scannerLock.current = false;
            setScannerBusy(false);
        }
    }, [scannerShipment]);

    const openDeliveryConfirmation = (shipment) => {
        setDeliveryError("");
        setDeliveryShipment(shipment);
    };

    const confirmDelivery = async (note) => {
        if (!deliveryShipment || deliveryBusy) return;
        setDeliveryBusy(true);
        setDeliveryError("");
        try {
            const result = await completeStoreCourierShipment(
                deliveryShipment.order_number,
                note,
            );
            setShipments((current) => current.filter(
                (row) => row.order_number !== deliveryShipment.order_number,
            ));
            toast.success(
                result.already_delivered
                    ? "الشحنة مسجلة تم التوصيل مسبقًا"
                    : "تم تسجيل توصيل الشحنة للعميل",
            );
            setDeliveryShipment(null);
        } catch (deliveryFailure) {
            setDeliveryError(deliveryFailure.message);
        } finally {
            setDeliveryBusy(false);
        }
    };

    if (loading) {
        return (
            <div className="flex min-h-72 items-center justify-center" data-testid="store-courier-my-shipments-loading">
                <SpinnerGap size={36} className="animate-spin text-emerald-700" />
            </div>
        );
    }

    const headerClass = normalizedStage === "delivered"
        ? "from-emerald-800 via-emerald-700 to-teal-700"
        : normalizedStage === "delivering"
            ? "from-amber-700 via-orange-600 to-amber-600"
            : "from-violet-800 via-violet-700 to-indigo-700";
    const HeaderIcon = normalizedStage === "delivered"
        ? CheckCircle
        : normalizedStage === "delivering"
            ? Truck
            : Package;

    return (
        <section className="mx-auto w-full max-w-6xl space-y-5" dir="rtl" data-testid={`store-courier-my-shipments-${normalizedStage}`}>
            <header className={`overflow-hidden rounded-3xl bg-gradient-to-l ${headerClass} p-5 text-white shadow-sm sm:p-7`}>
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="flex items-start gap-4">
                        <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-white/15">
                            <HeaderIcon size={31} weight="duotone" />
                        </span>
                        <div>
                            <div className="text-xs font-black text-white/75">{copy.eyebrow}</div>
                            <h2 className="mt-1 text-2xl font-black sm:text-3xl">{copy.title}</h2>
                            <p className="mt-2 max-w-3xl text-sm font-bold leading-7 text-white/80">{copy.description}</p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => void load()}
                        className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-white/15 px-4 text-sm font-black text-white"
                        data-testid="refresh-store-courier-my-shipments"
                    >
                        <ArrowClockwise size={20} weight="bold" /> تحديث
                    </button>
                </div>
            </header>

            {error && (
                <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-black text-rose-900" role="alert">
                    <WarningCircle size={22} weight="fill" /> {error}
                </div>
            )}

            <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
                <div className="flex items-center gap-2 text-sm font-black text-slate-700">
                    <Clock size={21} className="text-violet-700" weight="duotone" /> تحديث تلقائي كل 15 ثانية
                </div>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-700">{shipments.length} شحنة</span>
            </div>

            {!shipments.length ? (
                <div className="rounded-3xl border-2 border-dashed border-slate-200 bg-white p-12 text-center text-slate-500">
                    <Package size={48} className="mx-auto mb-3 text-slate-400" weight="duotone" />
                    <div className="text-base font-black">{copy.empty}</div>
                </div>
            ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                    {shipments.map((shipment) => {
                        const phone = shipment.recipient_mobile || shipment.customer_mobile || "";
                        const phoneNumber = digits(phone);
                        const mapUrl = mapsHref(shipment);
                        const remaining = Number(shipment.remaining_amount || 0);
                        return (
                            <article key={shipment.order_number} className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm" data-testid="store-courier-my-shipment">
                                <div className="flex items-start justify-between gap-3 border-b border-slate-100 p-4">
                                    <div>
                                        <div className="text-xs font-black text-slate-400">طلب #{shipment.order_number}</div>
                                        <div className="mt-1 text-xl font-black text-slate-950">{shipment.recipient_name || shipment.customer_name || "—"}</div>
                                        <div className="mt-1 text-xs font-bold text-slate-500">أُسندت: {dateLabel(shipment.assigned_at)}</div>
                                    </div>
                                    <span className={`rounded-full px-3 py-1 text-[11px] font-black ${normalizedStage === "delivered" ? "bg-emerald-100 text-emerald-800" : normalizedStage === "delivering" ? "bg-amber-100 text-amber-900" : "bg-violet-100 text-violet-800"}`}>
                                        {copy.badge}
                                    </span>
                                </div>

                                <div className="space-y-3 p-4">
                                    <div className="rounded-2xl bg-slate-50 p-3">
                                        <div className="flex items-start gap-2 text-sm font-black text-slate-800">
                                            <MapPin size={20} className="mt-0.5 shrink-0 text-violet-700" weight="fill" />
                                            <span>{shipment.address_text || shipment.city || "العنوان غير متوفر"}</span>
                                        </div>
                                        {phone && <div className="mt-2 text-sm font-black text-slate-700" dir="ltr">{phone}</div>}
                                    </div>

                                    <div className={`flex items-center justify-between rounded-2xl border p-3 ${remaining > 0 ? "border-amber-200 bg-amber-50" : "border-emerald-200 bg-emerald-50"}`}>
                                        <div className="flex items-center gap-2 text-sm font-black text-slate-800">
                                            <CurrencyCircleDollar size={22} weight="duotone" /> المبلغ المطلوب تحصيله
                                        </div>
                                        <div className="font-black" dir="ltr">{moneyLabel(remaining, shipment.currency)}</div>
                                    </div>

                                    {!!shipment.items?.length && (
                                        <div className="rounded-2xl border border-slate-200 p-3">
                                            <div className="mb-2 text-xs font-black text-slate-500">محتويات الطلب</div>
                                            <div className="space-y-1.5">
                                                {shipment.items.slice(0, 4).map((item, index) => (
                                                    <div key={`${item.order_item_id || item.sku || item.name}-${index}`} className="flex items-center justify-between gap-3 text-xs font-bold text-slate-700">
                                                        <span className="min-w-0 truncate">{item.name || "منتج"}</span>
                                                        <span className="shrink-0">× {item.quantity || 1}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    <div className="grid grid-cols-3 gap-2">
                                        <a href={phone ? `tel:${phone}` : undefined} aria-disabled={!phone} className={`inline-flex min-h-11 items-center justify-center gap-1 rounded-xl border text-xs font-black ${phone ? "border-slate-200 text-slate-700" : "pointer-events-none border-slate-100 text-slate-300"}`}>
                                            <Phone size={19} weight="fill" /> اتصال
                                        </a>
                                        <a href={phoneNumber ? `https://wa.me/${phoneNumber}` : undefined} target="_blank" rel="noreferrer" aria-disabled={!phoneNumber} className={`inline-flex min-h-11 items-center justify-center gap-1 rounded-xl border text-xs font-black ${phoneNumber ? "border-emerald-200 text-emerald-700" : "pointer-events-none border-slate-100 text-slate-300"}`}>
                                            <WhatsappLogo size={19} weight="fill" /> واتساب
                                        </a>
                                        <a href={mapUrl || undefined} target="_blank" rel="noreferrer" aria-disabled={!mapUrl} className={`inline-flex min-h-11 items-center justify-center gap-1 rounded-xl border text-xs font-black ${mapUrl ? "border-violet-200 text-violet-700" : "pointer-events-none border-slate-100 text-slate-300"}`}>
                                            <MapPin size={19} weight="fill" /> الموقع
                                        </a>
                                    </div>

                                    {normalizedStage === "waiting" && (
                                        <button
                                            type="button"
                                            onClick={() => openPickupScanner(shipment)}
                                            className="inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-violet-700 px-4 text-base font-black text-white"
                                            data-testid="pickup-store-courier-shipment"
                                        >
                                            <Barcode size={24} weight="bold" /> استلام الشحنة وتصوير QR
                                        </button>
                                    )}
                                    {normalizedStage === "delivering" && (
                                        <button
                                            type="button"
                                            onClick={() => openDeliveryConfirmation(shipment)}
                                            className="inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-emerald-700 px-4 text-base font-black text-white"
                                            data-testid="mark-store-courier-delivered"
                                        >
                                            <CheckCircle size={24} weight="fill" /> تم التوصيل
                                        </button>
                                    )}
                                    {normalizedStage === "delivered" && (
                                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-3 text-center text-sm font-black text-emerald-900">
                                            تم التوصيل: {dateLabel(shipment.delivered_at)}
                                            {shipment.delivery_note && <div className="mt-1 text-xs font-bold">{shipment.delivery_note}</div>}
                                        </div>
                                    )}
                                </div>
                            </article>
                        );
                    })}
                </div>
            )}

            {scannerShipment && (
                <ShippingBarcodeScanner
                    title={`استلام شحنة #${scannerShipment.order_number}`}
                    description="صوّر QR الموجود على بوليصة أماسي. لن يقبل ميزان باركود طلب آخر أو شحنة غير مسندة لك."
                    busy={scannerBusy}
                    error={scannerError}
                    onDetected={handlePickupBarcode}
                    onClose={() => !scannerBusy && setScannerShipment(null)}
                />
            )}

            {deliveryShipment && (
                <DeliveryConfirmation
                    shipment={deliveryShipment}
                    busy={deliveryBusy}
                    error={deliveryError}
                    onConfirm={confirmDelivery}
                    onClose={() => !deliveryBusy && setDeliveryShipment(null)}
                />
            )}
        </section>
    );
}
