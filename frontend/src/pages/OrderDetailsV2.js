import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useParams } from "react-router-dom";
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

/**
 * Thin compatibility wrapper.
 * Keeps the approved page intact, mounts the compact timeline, and localizes
 * only the values inside the order-source card.
 */
export default function OrderDetailsV2() {
    const { orderNumber } = useParams();
    const { order } = useOrder(orderNumber);
    const [timelineHost, setTimelineHost] = useState(null);

    useEffect(() => {
        let replacement = null;
        let original = null;
        let observer = null;

        const mountTimeline = () => {
            original = document.querySelector('[data-testid="order-v2-timeline"]');
            if (!original || original.dataset.compactTimelineReplaced === "true") return false;

            replacement = document.createElement("div");
            replacement.dataset.compactTimelineHost = "true";
            replacement.setAttribute("dir", "rtl");

            original.dataset.compactTimelineReplaced = "true";
            original.style.display = "none";
            original.insertAdjacentElement("afterend", replacement);
            setTimelineHost(replacement);
            return true;
        };

        if (!mountTimeline()) {
            observer = new MutationObserver(() => {
                if (mountTimeline()) observer?.disconnect();
            });
            observer.observe(document.body, { childList: true, subtree: true });
        }

        return () => {
            observer?.disconnect();
            setTimelineHost(null);
            replacement?.remove();
            if (original) {
                original.style.display = "";
                delete original.dataset.compactTimelineReplaced;
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
            {timelineHost && order ? createPortal(<CompactOrderTimeline order={order} />, timelineHost) : null}
        </>
    );
}
