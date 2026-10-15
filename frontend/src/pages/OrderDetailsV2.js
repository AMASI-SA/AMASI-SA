import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useParams } from "react-router-dom";
import {
    ArrowSquareOut,
    Bank,
    CheckCircle,
    Copy,
    FilePdf,
    Package,
    Wallet,
    WarningCircle,
} from "@phosphor-icons/react";
import CompactOrderTimeline from "../components/CompactOrderTimeline";
import { useOrder } from "../hooks/useOrders";
import { resolveOrderCampaign } from "../lib/orderCampaignAttribution";
import OriginalOrderDetailsV2 from "./OrderDetailsV2.jsx";

const ENABLE_LEGACY_COMPACT_TIMELINE = false;

const SOURCE_LABELS = {
    ig: "إنستقرام", instagram: "إنستقرام", fb: "فيسبوك", facebook: "فيسبوك",
    meta: "ميتا (فيسبوك وإنستقرام)", snapchat: "سناب شات", snap: "سناب شات",
    tiktok: "تيك توك", google: "جوجل", youtube: "يوتيوب", x: "إكس (تويتر)",
    twitter: "إكس (تويتر)", linkedin: "لينكدإن", whatsapp: "واتساب",
    email: "البريد الإلكتروني", sms: "رسائل نصية", direct: "دخول مباشر",
    store: "المتجر الإلكتروني", referral: "إحالة", organic: "بحث طبيعي",
};

const MEDIUM_LABELS = {
    social: "وسائل التواصل الاجتماعي", paid: "إعلان مدفوع",
    paid_social: "إعلان مدفوع عبر وسائل التواصل", cpc: "إعلان بالنقرة",
    ppc: "إعلان بالنقرة", organic: "زيارات مجانية", display: "إعلانات عرض",
    email: "بريد إلكتروني", affiliate: "تسويق بالعمولة", referral: "إحالة",
};

const DEVICE_LABELS = { mobile: "جوال", desktop: "كمبيوتر", tablet: "جهاز لوحي" };

const PAYMENT_LABELS = {
    mada: "مدى", credit_card: "البطاقة الائتمانية", card: "البطاقة الائتمانية",
    apple_pay: "Apple Pay", google_pay: "Google Pay", stc_pay: "STC Pay", tabby: "تابي",
    tabby_installment: "تابي", tamara: "تمارا", tamara_installment: "تمارا",
    emkan: "إمكان", bank: "التحويل البنكي", bank_transfer: "التحويل البنكي",
    transfer: "التحويل البنكي", cod: "الدفع عند الاستلام",
    cash_on_delivery: "الدفع عند الاستلام",
};

const BANK_LABELS = {
    alrajhi: "مصرف الراجحي", al_rajhi: "مصرف الراجحي", rajhi: "مصرف الراجحي",
    bank_rajhi: "مصرف الراجحي", riyadbank: "بنك الرياض", alinma: "مصرف الإنماء",
    al_inma: "مصرف الإنماء", inma: "مصرف الإنماء", bank_inma: "مصرف الإنماء",
    ahli: "البنك الأهلي السعودي", alahli: "البنك الأهلي السعودي",
    snb: "البنك الأهلي السعودي", ncb: "البنك الأهلي السعودي",
    bank_ahli: "البنك الأهلي السعودي",
};

function isPresent(value) {
    return value !== null && value !== undefined && value !== "";
}

function firstPresent(...values) {
    return values.find(isPresent);
}

function readable(value) {
    if (!isPresent(value)) return "—";
    if (typeof value === "object") {
        return readable(firstPresent(value.name, value.label, value.title, value.display_name, value.value, value.code, value.slug, value.id));
    }
    return String(value).trim() || "—";
}

function translate(value, dictionary) {
    const raw = readable(value);
    if (raw === "—") return raw;
    return dictionary[raw.toLowerCase()] || raw;
}

function sourceValues(order) {
    const sourceObject = typeof order?.source === "object" && order.source ? order.source : {};
    const attribution = firstPresent(order?.utm, order?.marketing, order?.attribution, sourceObject.attribution, sourceObject.utm) || {};
    const source = firstPresent(
        order?.source_native, order?.source_name, sourceObject.source_native,
        sourceObject.source, attribution.source, attribution.utm_source,
        typeof order?.source === "string" ? order.source : null,
    );
    const medium = firstPresent(order?.utm_medium, sourceObject.utm_medium, attribution.medium, attribution.utm_medium);
    const { campaignDisplay, campaignId } = resolveOrderCampaign(order);
    const channel = firstPresent(order?.channel_native, order?.channel, order?.order_channel, sourceObject.channel, sourceObject.source_event);
    const device = firstPresent(order?.device_name, order?.device, order?.client_device, sourceObject.device, sourceObject.device_type, attribution.device);

    return {
        المصدر: translate(source, SOURCE_LABELS),
        الوسيط: translate(medium, MEDIUM_LABELS),
        الحملة: readable(campaignDisplay),
        "معرّف الحملة": readable(campaignId),
        القناة: translate(channel, SOURCE_LABELS),
        الجهاز: translate(device, DEVICE_LABELS),
    };
}

function eventActor(event) {
    return readable(firstPresent(
        event?.actor_name, event?.actor, event?.user_name, event?.user,
        event?.employee_name, event?.employee, event?.updated_by,
        event?.created_by, event?.performed_by, event?.staff,
    ));
}

function eventText(event) {
    return [event?.type, event?.event, event?.action, event?.title, event?.status, event?.status_to, event?.new_status]
        .map((value) => readable(value))
        .filter((value) => value !== "—")
        .join(" ")
        .toLowerCase();
}

function newestMatchingEvent(events, keywords, requireActor = false) {
    return [...events].reverse().find((event) => {
        const text = eventText(event);
        if (!keywords.some((keyword) => text.includes(keyword))) return false;
        return !requireActor || eventActor(event) !== "—";
    });
}

function staffValues(order) {
    const assignments = firstPresent(order?.assignments, order?.responsibilities, order?.staff) || {};
    const events = Array.isArray(order?.timeline) ? order.timeline : [];
    const latestWithActor = [...events].reverse().find((event) => eventActor(event) !== "—");
    const actorFor = (keywords) => eventActor(newestMatchingEvent(events, keywords, true));

    return {
        "مسؤول الطلب": readable(firstPresent(assignments.owner, assignments.order_owner, assignments.assigned_to, order?.assigned_to, actorFor(["assign", "مسؤول", "تعيين"]))),
        التجهيز: readable(firstPresent(assignments.preparation, assignments.fulfillment, assignments.preparation_employee, actorFor(["prepar", "fulfill", "تجهيز"]))),
        الشحن: readable(firstPresent(assignments.shipping, assignments.shipping_employee, actorFor(["ship", "شحن", "بوليصة"]))),
        "خدمة العملاء": readable(firstPresent(assignments.customer_service, assignments.support, assignments.customer_service_employee, actorFor(["customer", "support", "عميل", "خدمة"]))),
        "آخر محدث": eventActor(latestWithActor),
    };
}

function trackingValues(order) {
    const operations = firstPresent(order?.fulfillment, order?.operations, order?.tracking) || {};
    const events = Array.isArray(order?.timeline) ? order.timeline : [];
    const statusFor = (keywords) => {
        const event = newestMatchingEvent(events, keywords, false);
        return readable(firstPresent(event?.status_to, event?.new_status, event?.status, event?.title, event?.action));
    };
    return {
        التجهيز: readable(firstPresent(operations.preparation_status, operations.status, order?.preparation_status, statusFor(["prepar", "fulfill", "تجهيز"]))),
        الطباعة: readable(firstPresent(operations.print_status, order?.print_status, statusFor(["print", "طباعة"]))),
        التغليف: readable(firstPresent(operations.packing_status, order?.packing_status, statusFor(["pack", "تغليف"]))),
        "التسليم للشركة": readable(firstPresent(operations.handover_status, order?.handover_status, statusFor(["handover", "تسليم للشركة"]))),
        التوصيل: readable(firstPresent(operations.delivery_status, order?.shipping?.status, order?.shipment_status, statusFor(["deliver", "توصيل"]))),
    };
}

function PreparationPdfCard() {
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-preparation-pdf">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-3">
                    <div className="rounded-xl bg-violet-100 p-3 text-violet-700"><Package size={24} weight="fill" /></div>
                    <div>
                        <h2 className="text-lg font-extrabold text-slate-950">تجهيز وطباعة المنتجات</h2>
                        <p className="mt-1 text-sm leading-6 text-slate-500">رفع ملف طلبات سلة وتحويل المنتجات إلى بطاقات PDF جاهزة للطباعة.</p>
                    </div>
                </div>
                <a href="/product-preparation" className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-teal-200 bg-teal-50 px-4 py-3 text-sm font-extrabold text-teal-800 transition hover:bg-teal-100">
                    <FilePdf size={20} weight="fill" /> فتح تجهيز المنتجات
                </a>
            </div>
        </section>
    );
}

function normalizeBankName(payment) {
    const raw = readable(firstPresent(
        payment?.receiving_bank_name, payment?.destination_bank_name,
        payment?.transfer_bank_name, payment?.bank_name, payment?.bank,
        payment?.receiving_bank_code, payment?.bank_code,
    ));
    if (raw === "—") return "البنك غير محدد";
    const normalized = raw.toLowerCase().replace(/[^a-z0-9أ-ي]+/g, "_").replace(/^_+|_+$/g, "");
    return BANK_LABELS[normalized] || raw;
}

function formatCollectionMoney(value, currency = "SAR") {
    return new Intl.NumberFormat("ar-SA-u-nu-latn", {
        style: "currency",
        currency: currency || "SAR",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(Number(value || 0));
}

function PaymentSummary({ order }) {
    const payment = order?.payment || {};
    const methodRaw = readable(firstPresent(payment.method_native, payment.method, order?.payment_method));
    const methodKey = methodRaw.toLowerCase();
    const methodLabel = PAYMENT_LABELS[methodKey] || methodRaw || "طريقة الدفع غير محددة";
    const statusRaw = readable(firstPresent(payment.status_native, payment.status));
    const statusKey = statusRaw.toLowerCase();
    const isPaid = payment.is_paid === true || ["paid", "completed", "تم الدفع", "مدفوع"].some((token) => statusKey.includes(token));
    const isCod = ["cod", "cash_on_delivery"].includes(methodKey);
    const isBank = ["bank", "bank_transfer", "transfer"].includes(methodKey);
    const bankName = normalizeBankName(payment);
    const attachment = firstPresent(payment.receipt_url, payment.attachment_url, payment.proof_url, payment.transfer_receipt_url);
    const orderStatus = order?.status_native || order?.status;
    const normalizedOrderStatus = String(orderStatus || "")
        .trim()
        .replace(/[إأآٱ]/g, "ا")
        .replace(/[\u200B-\u200D\uFEFF]/g, "");
    const waitingForPayment = normalizedOrderStatus === "بانتظار الدفع";
    const paidAmount = Number(firstPresent(payment.paid_amount, order?.paid_amount, 0) || 0);
    const remainingAmount = Number(firstPresent(payment.remaining_amount, order?.remaining_amount, 0) || 0);
    const hasRemainingAmount = payment.has_remaining_amount === true
        || order?.has_remaining_amount === true
        || remainingAmount > 0;
    const checkoutUrl = String(firstPresent(payment.checkout_url, order?.payment_checkout_url, "") || "").trim();
    const collectionCurrency = order?.totals?.currency || order?.currency || "SAR";

    if (waitingForPayment) {
        return (
            <div className="flex h-full min-h-[240px] flex-col justify-center" data-testid="order-v2-payment-summary" data-order-status="بانتظار الدفع" data-collection-status="unpaid">
                <div className="mb-4 flex items-center gap-4">
                    <WarningCircle size={48} className="shrink-0 text-rose-500" weight="fill" />
                    <div>
                        <div className="text-2xl font-bold text-rose-600">بانتظار الدفع</div>
                        <div className="mt-2 text-sm font-medium text-rose-500">{methodLabel}</div>
                    </div>
                </div>
                <div className="space-y-3 border-y border-rose-100 py-4 text-base">
                    <div className="flex items-center justify-between gap-4">
                        <span className="font-bold text-slate-500">المبلغ المدفوع</span>
                        <span className="num font-extrabold text-slate-800">{formatCollectionMoney(paidAmount, collectionCurrency)}</span>
                    </div>
                    <div className="flex items-center justify-between gap-4">
                        <span className="font-bold text-slate-500">المبلغ المطلوب</span>
                        <span className="num text-xl font-extrabold text-rose-600">{formatCollectionMoney(remainingAmount, collectionCurrency)}</span>
                    </div>
                </div>
                {checkoutUrl ? (
                    <div className="mt-5 flex flex-wrap gap-2">
                        <button type="button" onClick={() => navigator.clipboard?.writeText(checkoutUrl)} className="inline-flex items-center gap-2 rounded-lg border border-rose-200 px-3 py-2 text-sm font-bold text-rose-700 transition hover:bg-rose-50">
                            <Copy size={18} /> نسخ رابط الدفع
                        </button>
                        <a href={checkoutUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-lg bg-rose-600 px-3 py-2 text-sm font-bold text-white transition hover:bg-rose-700">
                            <ArrowSquareOut size={18} /> فتح رابط الدفع
                        </a>
                    </div>
                ) : (
                    <div className="mt-4 text-sm font-bold text-slate-400">رابط الدفع غير متاح من سلة.</div>
                )}
            </div>
        );
    }

    if (hasRemainingAmount && remainingAmount > 0) {
        return (
            <div className="flex h-full min-h-[240px] flex-col justify-center" data-testid="order-v2-payment-summary" data-collection-status={paidAmount > 0 ? "partial" : "unpaid"}>
                <div className="mb-4 flex items-center gap-3">
                    <WarningCircle size={42} className="shrink-0 text-amber-500" weight="fill" />
                    <div>
                        <div className="text-xl font-extrabold text-amber-700">متبقي على العميل</div>
                        <div className="mt-1 text-sm font-bold text-slate-500">{methodLabel}</div>
                    </div>
                </div>
                <div className="space-y-3 border-y border-slate-100 py-4 text-base">
                    <div className="flex items-center justify-between gap-4">
                        <span className="font-bold text-slate-500">المبلغ المدفوع</span>
                        <span className="num font-extrabold text-slate-800">{formatCollectionMoney(paidAmount, collectionCurrency)}</span>
                    </div>
                    <div className="flex items-center justify-between gap-4">
                        <span className="font-bold text-slate-500">المبلغ المتبقي</span>
                        <span className="num text-xl font-extrabold text-amber-600">{formatCollectionMoney(remainingAmount, collectionCurrency)}</span>
                    </div>
                </div>
                {checkoutUrl ? (
                    <div className="mt-5 flex flex-wrap gap-2">
                        <button type="button" onClick={() => navigator.clipboard?.writeText(checkoutUrl)} className="inline-flex items-center gap-2 rounded-lg border border-teal-200 px-3 py-2 text-sm font-bold text-teal-700 transition hover:bg-teal-50">
                            <Copy size={18} /> نسخ رابط الدفع
                        </button>
                        <a href={checkoutUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-lg bg-teal-600 px-3 py-2 text-sm font-bold text-white transition hover:bg-teal-700">
                            <ArrowSquareOut size={18} /> فتح رابط الدفع
                        </a>
                    </div>
                ) : (
                    <div className="mt-4 text-sm font-bold text-slate-400">رابط تحصيل المتبقي غير متاح من سلة.</div>
                )}
            </div>
        );
    }

    if (isCod) {
        return (
            <div className="flex h-full min-h-[240px] flex-col justify-center" data-testid="order-v2-payment-summary" data-payment-method="cod">
                <div className="flex items-center gap-4">
                    <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500"><Wallet size={31} weight="duotone" /></div>
                    <div className="text-2xl font-medium text-slate-700">الدفع عند الاستلام</div>
                </div>
            </div>
        );
    }

    if (isBank) {
        return (
            <div className="flex h-full min-h-[240px] flex-col justify-center" data-testid="order-v2-payment-summary" data-payment-method="bank" data-receiving-bank={bankName}>
                <div className="flex items-center gap-5">
                    {attachment ? (
                        <a href={attachment} target="_blank" rel="noreferrer" className="shrink-0"><img src={attachment} alt="إيصال التحويل" className="h-28 w-24 rounded-none border-2 border-teal-300 object-cover" /></a>
                    ) : (
                        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border border-teal-200 text-teal-500"><Bank size={32} weight="duotone" /></div>
                    )}
                    <div>
                        <div className="flex items-center gap-3 text-2xl font-medium text-slate-700"><CheckCircle size={46} className="text-teal-300" weight="regular" /> تم تحويل المبلغ</div>
                        <div className="mt-2 text-xl text-slate-400">{bankName}</div>
                    </div>
                </div>
                <div className="mt-8 text-center text-lg leading-8 text-rose-500">نرجو التحقق من وصول المبلغ للحساب الخاص بكم قبل تنفيذ الطلب</div>
            </div>
        );
    }

    return (
        <div className="flex h-full min-h-[240px] flex-col justify-center" data-testid="order-v2-payment-summary" data-payment-method={methodKey}>
            <div className="flex items-center gap-4">
                {(isPaid || statusRaw === "—") && <CheckCircle size={48} className="shrink-0 text-teal-300" weight="regular" />}
                <div>
                    <div className="text-2xl font-medium text-slate-700">تم الدفع بواسطة {methodLabel}</div>
                    {isPaid && <div className="mt-2 text-sm text-slate-400">تمت إضافة مبلغ الطلب إلى رصيد المدفوعات الإلكترونية</div>}
                </div>
            </div>
        </div>
    );
}

function applyCardValues(cardTitle, values) {
    const advanced = document.querySelector('[data-testid="order-v2-advanced-info"]');
    if (!advanced) return false;
    const heading = Array.from(advanced.querySelectorAll("h3")).find((node) => node.textContent?.trim() === cardTitle);
    const card = heading?.closest("div.rounded-2xl");
    if (!card) return false;
    const rows = Array.from(card.children).filter((node) => node.querySelectorAll?.("span").length >= 2);
    rows.forEach((row) => {
        const spans = row.querySelectorAll("span");
        const label = spans[0]?.textContent?.trim();
        const valueNode = spans[spans.length - 1];
        if (!label || !valueNode || !(label in values)) return;
        valueNode.textContent = values[label];
        valueNode.setAttribute("title", values[label]);
        if (label === "الحملة") {
            valueNode.classList.remove("truncate");
            valueNode.style.whiteSpace = "normal";
            valueNode.style.overflowWrap = "anywhere";
        }
    });
    return true;
}

export default function OrderDetailsV2() {
    const { orderNumber } = useParams();
    const { order } = useOrder(orderNumber);
    const [timelineHost, setTimelineHost] = useState(null);
    const [preparationHost, setPreparationHost] = useState(null);
    const [paymentHost, setPaymentHost] = useState(null);

    useEffect(() => {
        let timelineReplacement = null;
        let timelineOriginal = null;
        let preparationReplacement = null;
        let paymentReplacement = null;
        let paymentOriginalBody = null;
        let observer = null;

        const mountEnhancements = () => {
            timelineOriginal = document.querySelector('[data-testid="order-v2-timeline"]');
            if (ENABLE_LEGACY_COMPACT_TIMELINE && timelineOriginal && timelineOriginal.dataset.compactTimelineReplaced !== "true") {
                timelineReplacement = document.createElement("div");
                timelineReplacement.dataset.compactTimelineHost = "true";
                timelineReplacement.setAttribute("dir", "rtl");
                timelineOriginal.dataset.compactTimelineReplaced = "true";
                timelineOriginal.style.display = "none";
                timelineOriginal.insertAdjacentElement("afterend", timelineReplacement);
                setTimelineHost(timelineReplacement);
            }

            const itemsSection = document.querySelector('[data-testid="order-v2-items"]');
            if (itemsSection && !document.querySelector('[data-preparation-pdf-host="true"]')) {
                preparationReplacement = document.createElement("div");
                preparationReplacement.dataset.preparationPdfHost = "true";
                preparationReplacement.setAttribute("dir", "rtl");
                itemsSection.insertAdjacentElement("afterend", preparationReplacement);
                setPreparationHost(preparationReplacement);
            }

            const paymentCard = document.querySelector('[data-testid="order-v2-payment"]');
            if (paymentCard && paymentCard.dataset.paymentSummaryReplaced !== "true") {
                paymentOriginalBody = paymentCard.querySelector(':scope > div.flex-1');
                paymentReplacement = document.createElement("div");
                paymentReplacement.dataset.paymentSummaryHost = "true";
                paymentReplacement.setAttribute("dir", "rtl");
                paymentCard.dataset.paymentSummaryReplaced = "true";
                if (paymentOriginalBody) paymentOriginalBody.style.display = "none";
                paymentCard.appendChild(paymentReplacement);
                setPaymentHost(paymentReplacement);
            }
        };

        mountEnhancements();
        observer = new MutationObserver(mountEnhancements);
        observer.observe(document.body, { childList: true, subtree: true });

        return () => {
            observer?.disconnect();
            setTimelineHost(null);
            setPreparationHost(null);
            setPaymentHost(null);
            timelineReplacement?.remove();
            preparationReplacement?.remove();
            paymentReplacement?.remove();
            if (paymentOriginalBody) paymentOriginalBody.style.display = "";
            const paymentCard = document.querySelector('[data-testid="order-v2-payment"]');
            if (paymentCard) delete paymentCard.dataset.paymentSummaryReplaced;
            if (timelineOriginal) {
                timelineOriginal.style.display = "";
                delete timelineOriginal.dataset.compactTimelineReplaced;
            }
        };
    }, [orderNumber]);

    useEffect(() => {
        if (!order) return undefined;
        let observer = null;
        const applyValues = () => {
            const sourceDone = applyCardValues("مصدر الطلب", sourceValues(order));
            const staffDone = applyCardValues("الموظفون والمسؤوليات", staffValues(order));
            const trackingDone = applyCardValues("متابعة الطلب", trackingValues(order));
            return sourceDone && staffDone && trackingDone;
        };
        if (!applyValues()) {
            observer = new MutationObserver(() => {
                if (applyValues()) observer?.disconnect();
            });
            observer.observe(document.body, { childList: true, subtree: true });
        }
        return () => observer?.disconnect();
    }, [order]);

    return (
        <>
            <OriginalOrderDetailsV2 />
            {paymentHost && order ? createPortal(<PaymentSummary order={order} />, paymentHost) : null}
            {preparationHost ? createPortal(<PreparationPdfCard />, preparationHost) : null}
            {timelineHost && order ? createPortal(<CompactOrderTimeline order={order} />, timelineHost) : null}
        </>
    );
}
