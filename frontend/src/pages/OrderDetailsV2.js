import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useParams } from "react-router-dom";
import {
    Bank,
    CheckCircle,
    FilePdf,
    Package,
    Wallet,
} from "@phosphor-icons/react";
import CompactOrderTimeline from "../components/CompactOrderTimeline";
import { useOrder } from "../hooks/useOrders";
import OriginalOrderDetailsV2 from "./OrderDetailsV2.jsx";

const SOURCE_LABELS = {
    ig: "إنستقرام",
    instagram: "إنستقرام",
    fb: "فيسبوك",
    facebook: "فيسبوك",
    meta: "ميتا (فيسبوك وإنستقرام)",
    snapchat: "سناب شات",
    snap: "سناب شات",
    tiktok: "تيك توك",
    google: "جوجل",
    youtube: "يوتيوب",
    x: "إكس (تويتر)",
    twitter: "إكس (تويتر)",
    linkedin: "لينكدإن",
    whatsapp: "واتساب",
    email: "البريد الإلكتروني",
    sms: "رسائل نصية",
    direct: "دخول مباشر",
    store: "المتجر الإلكتروني",
    referral: "إحالة",
    organic: "بحث طبيعي",
};

const MEDIUM_LABELS = {
    social: "وسائل التواصل الاجتماعي",
    paid: "إعلان مدفوع",
    paid_social: "إعلان مدفوع عبر وسائل التواصل",
    cpc: "إعلان بالنقرة",
    ppc: "إعلان بالنقرة",
    organic: "زيارات مجانية",
    display: "إعلانات عرض",
    email: "بريد إلكتروني",
    affiliate: "تسويق بالعمولة",
    referral: "إحالة",
};

const DEVICE_LABELS = {
    mobile: "جوال",
    desktop: "كمبيوتر",
    tablet: "جهاز لوحي",
};

const PAYMENT_LABELS = {
    mada: "مدى",
    credit_card: "البطاقة الائتمانية",
    card: "البطاقة الائتمانية",
    apple_pay: "Apple Pay",
    stc_pay: "STC Pay",
    tabby: "تابي",
    tabby_installment: "تابي",
    tamara: "تمارا",
    tamara_installment: "تمارا",
    emkan: "إمكان",
    bank: "التحويل البنكي",
    bank_transfer: "التحويل البنكي",
    transfer: "التحويل البنكي",
    cod: "الدفع عند الاستلام",
    cash_on_delivery: "الدفع عند الاستلام",
};

const BANK_LABELS = {
    alrajhi: "مصرف الراجحي",
    al_rajhi: "مصرف الراجحي",
    rajhi: "مصرف الراجحي",
    riyadbank: "بنك الرياض",
    alinma: "مصرف الإنماء",
    al_inma: "مصرف الإنماء",
    inma: "مصرف الإنماء",
    ahli: "البنك الأهلي السعودي",
    alahli: "البنك الأهلي السعودي",
    snb: "البنك الأهلي السعودي",
    ncb: "البنك الأهلي السعودي",
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
        return readable(firstPresent(value.name, value.label, value.title, value.value, value.code, value.slug, value.id));
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
        order?.source_native,
        order?.source_name,
        sourceObject.source_native,
        sourceObject.source,
        attribution.source,
        attribution.utm_source,
        typeof order?.source === "string" ? order.source : null,
    );
    const medium = firstPresent(order?.utm_medium, sourceObject.utm_medium, attribution.medium, attribution.utm_medium);
    const campaign = firstPresent(
        order?.utm_campaign,
        order?.campaign_name,
        sourceObject.utm_campaign,
        sourceObject.campaign_name,
        attribution.campaign_name,
        attribution.campaign,
        attribution.utm_campaign,
    );
    const channel = firstPresent(order?.channel_native, order?.channel, order?.order_channel, sourceObject.channel, sourceObject.source_event);
    const device = firstPresent(order?.device_name, order?.device, order?.client_device, sourceObject.device, sourceObject.device_type, attribution.device);

    return {
        المصدر: translate(source, SOURCE_LABELS),
        الوسيط: translate(medium, MEDIUM_LABELS),
        الحملة: readable(campaign),
        القناة: translate(channel, SOURCE_LABELS),
        الجهاز: translate(device, DEVICE_LABELS),
    };
}

function PreparationPdfCard() {
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-preparation-pdf">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-3">
                    <div className="rounded-xl bg-violet-100 p-3 text-violet-700">
                        <Package size={24} weight="fill" />
                    </div>
                    <div>
                        <h2 className="text-lg font-extrabold text-slate-950">تجهيز وطباعة المنتجات</h2>
                        <p className="mt-1 text-sm leading-6 text-slate-500">رفع ملف طلبات سلة وتحويل المنتجات إلى بطاقات PDF جاهزة للطباعة.</p>
                    </div>
                </div>
                <a
                    href="/product-preparation"
                    className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-teal-200 bg-teal-50 px-4 py-3 text-sm font-extrabold text-teal-800 transition hover:bg-teal-100"
                >
                    <FilePdf size={20} weight="fill" />
                    فتح تجهيز المنتجات
                </a>
            </div>
        </section>
    );
}

function normalizeBankName(payment) {
    const raw = readable(firstPresent(
        payment?.receiving_bank_name,
        payment?.destination_bank_name,
        payment?.transfer_bank_name,
        payment?.bank_name,
        payment?.bank,
        payment?.receiving_bank_code,
        payment?.bank_code,
    ));
    if (raw === "—") return "البنك غير محدد";
    const normalized = raw.toLowerCase().replace(/[^a-z0-9أ-ي]+/g, "_").replace(/^_+|_+$/g, "");
    return BANK_LABELS[normalized] || raw;
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

    if (isCod) {
        return (
            <div className="flex h-full min-h-[240px] flex-col justify-center" data-testid="order-v2-payment-summary" data-payment-method="cod">
                <div className="flex items-center gap-4">
                    <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500">
                        <Wallet size={31} weight="duotone" />
                    </div>
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
                        <a href={attachment} target="_blank" rel="noreferrer" className="shrink-0">
                            <img src={attachment} alt="إيصال التحويل" className="h-28 w-24 rounded-none border-2 border-teal-300 object-cover" />
                        </a>
                    ) : (
                        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border border-teal-200 text-teal-500">
                            <Bank size={32} weight="duotone" />
                        </div>
                    )}
                    <div>
                        <div className="flex items-center gap-3 text-2xl font-medium text-slate-700">
                            <CheckCircle size={46} className="text-teal-300" weight="regular" />
                            تم تحويل المبلغ
                        </div>
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

/**
 * Thin compatibility wrapper.
 * Keeps the approved page intact and mounts focused enhancements only.
 */
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
            let mountedSomething = false;

            timelineOriginal = document.querySelector('[data-testid="order-v2-timeline"]');
            if (timelineOriginal && timelineOriginal.dataset.compactTimelineReplaced !== "true") {
                timelineReplacement = document.createElement("div");
                timelineReplacement.dataset.compactTimelineHost = "true";
                timelineReplacement.setAttribute("dir", "rtl");
                timelineOriginal.dataset.compactTimelineReplaced = "true";
                timelineOriginal.style.display = "none";
                timelineOriginal.insertAdjacentElement("afterend", timelineReplacement);
                setTimelineHost(timelineReplacement);
                mountedSomething = true;
            }

            const itemsSection = document.querySelector('[data-testid="order-v2-items"]');
            if (itemsSection && !document.querySelector('[data-preparation-pdf-host="true"]')) {
                preparationReplacement = document.createElement("div");
                preparationReplacement.dataset.preparationPdfHost = "true";
                preparationReplacement.setAttribute("dir", "rtl");
                itemsSection.insertAdjacentElement("afterend", preparationReplacement);
                setPreparationHost(preparationReplacement);
                mountedSomething = true;
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
                mountedSomething = true;
            }

            return mountedSomething;
        };

        mountEnhancements();
        observer = new MutationObserver(() => mountEnhancements());
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

        const values = sourceValues(order);
        let observer = null;

        const applyTranslations = () => {
            const advanced = document.querySelector('[data-testid="order-v2-advanced-info"]');
            if (!advanced) return false;

            const heading = Array.from(advanced.querySelectorAll("h3")).find((node) => node.textContent?.trim() === "مصدر الطلب");
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
        };

        if (!applyTranslations()) {
            observer = new MutationObserver(() => {
                if (applyTranslations()) observer?.disconnect();
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
