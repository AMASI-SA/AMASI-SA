import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL || ""}/api`;
const GIFT_BADGE_ATTR = "data-order-gift-detail-badge";
const WEBHOOK_MONITOR_ATTR = "data-salla-webhook-monitor";

function removeNode(selector) {
    document.querySelector(selector)?.remove();
}

function mountGiftBadge(order) {
    removeNode(`[${GIFT_BADGE_ATTR}="true"]`);
    if (!order?.is_gift) return true;

    const page = document.querySelector('[data-testid="order-details-v2-page"]');
    if (!page) return false;

    const heading = Array.from(page.querySelectorAll("h1")).find((node) =>
        String(node.textContent || "").includes("الطلب")
    );
    if (!heading) return false;

    const badge = document.createElement("span");
    badge.setAttribute(GIFT_BADGE_ATTR, "true");
    badge.className = "me-2 inline-flex items-center gap-1 rounded-full bg-emerald-500 px-3 py-1 align-middle text-sm font-extrabold text-white shadow-sm";
    badge.textContent = "🎁 هدية";
    badge.title = "هذا الطلب هدية";
    heading.appendChild(badge);
    return true;
}

function formatDate(value) {
    if (!value) return "—";
    try {
        return new Intl.DateTimeFormat("ar-SA", {
            dateStyle: "medium",
            timeStyle: "short",
            timeZone: "Asia/Riyadh",
        }).format(new Date(value));
    } catch {
        return String(value);
    }
}

function createElement(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
}

function renderWebhookMonitor(panel, payload) {
    panel.replaceChildren();

    const header = createElement("div", "flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-5 py-4");
    const titleWrap = createElement("div");
    titleWrap.appendChild(createElement("h3", "text-lg font-extrabold text-slate-900", "مراقبة أحداث Webhook من سلة"));
    titleWrap.appendChild(createElement(
        "p",
        "mt-1 text-xs leading-relaxed text-slate-500",
        "نعرض الحدث كـ «يعمل» فقط بعد وصوله فعليًا من سلة. عدم وصوله لا يثبت أنه غير مفعّل حتى نجري اختبارًا مناسبًا."
    ));
    header.appendChild(titleWrap);

    const refreshButton = createElement("button", "rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm font-bold text-indigo-700 hover:bg-indigo-100", "تحديث الحالة");
    refreshButton.type = "button";
    refreshButton.addEventListener("click", async () => {
        refreshButton.disabled = true;
        refreshButton.textContent = "جاري التحديث…";
        try {
            const { data } = await axios.get(`${API}/salla/webhook-monitor`, { withCredentials: true });
            renderWebhookMonitor(panel, data);
        } catch {
            refreshButton.disabled = false;
            refreshButton.textContent = "تعذر التحديث — حاول مرة أخرى";
        }
    });
    header.appendChild(refreshButton);
    panel.appendChild(header);

    const fallback = createElement(
        "div",
        "mx-5 mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-900",
        payload?.api_fallback_enabled
            ? "✅ الاعتماد الاحتياطي على Salla API ما زال مفعّلًا أثناء اختبار Webhooks."
            : "مسار API الاحتياطي غير مفعّل."
    );
    panel.appendChild(fallback);

    const summary = createElement("div", "mx-5 mt-3 flex flex-wrap gap-2 text-xs font-bold");
    summary.appendChild(createElement(
        "span",
        "rounded-full bg-emerald-100 px-3 py-1 text-emerald-800",
        `وصل فعليًا: ${payload?.received_events || 0}`
    ));
    summary.appendChild(createElement(
        "span",
        "rounded-full bg-slate-100 px-3 py-1 text-slate-700",
        `إجمالي الأحداث المراقبة: ${payload?.total_monitored_events || 0}`
    ));
    panel.appendChild(summary);

    const groups = [
        ["orders", "أحداث الطلبات"],
        ["shipping", "أحداث الشحن"],
    ];

    for (const [groupKey, groupLabel] of groups) {
        const events = Array.isArray(payload?.events)
            ? payload.events.filter((item) => item.group === groupKey)
            : [];
        if (!events.length) continue;

        const section = createElement("section", "px-5 py-4");
        section.appendChild(createElement("h4", "mb-3 text-sm font-extrabold text-slate-800", groupLabel));
        const grid = createElement("div", "grid grid-cols-1 gap-2 lg:grid-cols-2");

        for (const event of events) {
            const working = event.status === "working";
            const card = createElement(
                "div",
                `rounded-xl border p-3 ${working ? "border-emerald-200 bg-emerald-50/60" : "border-slate-200 bg-slate-50"}`
            );
            const top = createElement("div", "flex items-center justify-between gap-2");
            const name = createElement("div");
            name.appendChild(createElement("div", "text-sm font-extrabold text-slate-900", event.label || event.event));
            name.appendChild(createElement("code", "mt-0.5 block text-[10px] text-slate-500", event.event));
            top.appendChild(name);
            top.appendChild(createElement(
                "span",
                `shrink-0 rounded-full px-2 py-1 text-[11px] font-extrabold ${working ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-700"}`,
                working ? "يعمل — وصل من سلة" : "لم يصل بعد"
            ));
            card.appendChild(top);

            const meta = createElement("div", "mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-600");
            meta.appendChild(createElement("span", "", `آخر وصول: ${formatDate(event.last_received_at)}`));
            meta.appendChild(createElement("span", "", `عدد الوصول: ${event.delivery_count || 0}`));
            if (event.last_order_number) {
                meta.appendChild(createElement("span", "font-bold text-slate-800", `آخر طلب: ${event.last_order_number}`));
            }
            card.appendChild(meta);
            grid.appendChild(card);
        }
        section.appendChild(grid);
        panel.appendChild(section);
    }
}

async function mountSallaWebhookMonitor() {
    const connectedCard = document.querySelector('[data-testid="salla-connected-card"]');
    if (!connectedCard) return false;

    let panel = document.querySelector(`[${WEBHOOK_MONITOR_ATTR}="true"]`);
    if (!panel) {
        panel = createElement("div", "mt-5 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm");
        panel.setAttribute(WEBHOOK_MONITOR_ATTR, "true");
        connectedCard.insertAdjacentElement("afterend", panel);
    }
    panel.replaceChildren(createElement("div", "p-5 text-sm font-bold text-slate-500", "جاري قراءة سجل أحداث سلة…"));

    try {
        const { data } = await axios.get(`${API}/salla/webhook-monitor`, { withCredentials: true });
        renderWebhookMonitor(panel, data);
    } catch (error) {
        panel.replaceChildren(createElement(
            "div",
            "m-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-700",
            error?.response?.data?.detail?.message || "تعذر تحميل مراقبة أحداث Webhook."
        ));
    }
    return true;
}

export default function OrderUiEnhancements() {
    const location = useLocation();

    useEffect(() => {
        let active = true;
        let observer = null;
        const match = location.pathname.match(/^\/orders-v2\/([^/]+)$/);

        removeNode(`[${GIFT_BADGE_ATTR}="true"]`);
        if (!match) return undefined;

        const orderNumber = decodeURIComponent(match[1]);
        const apply = async () => {
            try {
                const { data } = await axios.get(`${API}/orders-v2/${encodeURIComponent(orderNumber)}`);
                if (!active) return;
                if (!mountGiftBadge(data)) {
                    observer = new MutationObserver(() => {
                        if (mountGiftBadge(data)) observer?.disconnect();
                    });
                    observer.observe(document.body, { childList: true, subtree: true });
                }
            } catch {
                // The original page owns its error state.
            }
        };

        apply();
        return () => {
            active = false;
            observer?.disconnect();
            removeNode(`[${GIFT_BADGE_ATTR}="true"]`);
        };
    }, [location.pathname]);

    useEffect(() => {
        let active = true;
        let observer = null;
        const isSallaSettings = location.pathname.includes("salla");

        removeNode(`[${WEBHOOK_MONITOR_ATTR}="true"]`);
        if (!isSallaSettings) return undefined;

        const apply = async () => {
            if (!active) return;
            const mounted = await mountSallaWebhookMonitor();
            if (mounted) observer?.disconnect();
        };

        apply();
        observer = new MutationObserver(() => { apply(); });
        observer.observe(document.body, { childList: true, subtree: true });

        return () => {
            active = false;
            observer?.disconnect();
            removeNode(`[${WEBHOOK_MONITOR_ATTR}="true"]`);
        };
    }, [location.pathname]);

    return null;
}
