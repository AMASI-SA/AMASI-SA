import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL || ""}/api`;
const UNREAD_BADGE_ATTR = "data-orders-unread-badge";
const GIFT_BADGE_ATTR = "data-order-gift-detail-badge";

function removeNode(selector) {
    document.querySelector(selector)?.remove();
}

function mountUnreadBadge(count) {
    const link = document.querySelector('[data-testid="nav-orders"]');
    if (!link) return false;

    let badge = link.querySelector(`[${UNREAD_BADGE_ATTR}="true"]`);
    if (!count) {
        badge?.remove();
        return true;
    }

    if (!badge) {
        badge = document.createElement("span");
        badge.setAttribute(UNREAD_BADGE_ATTR, "true");
        badge.className = "inline-flex min-w-[22px] h-[22px] items-center justify-center rounded-full bg-rose-500 px-1.5 text-[11px] font-extrabold text-white num";
        badge.title = "طلبات غير مقروءة";
        link.appendChild(badge);
    }
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.setAttribute("aria-label", `${count} طلب غير مقروء`);
    return true;
}

async function fetchUnreadOrdersCount() {
    let cursor = null;
    let unread = 0;
    let pages = 0;

    do {
        const params = { limit: 50 };
        if (cursor) params.cursor = cursor;
        const { data } = await axios.get(`${API}/orders-v2`, { params });
        const items = Array.isArray(data?.items) ? data.items : [];
        unread += items.filter((order) => order?.is_new === true).length;
        cursor = data?.next_cursor || null;
        pages += 1;
    } while (cursor && pages < 100);

    return unread;
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

export default function OrderUiEnhancements() {
    const location = useLocation();

    useEffect(() => {
        let active = true;
        let observer = null;
        let latestCount = 0;

        const refreshUnread = async () => {
            try {
                const count = await fetchUnreadOrdersCount();
                latestCount = count;
                if (active) mountUnreadBadge(count);
            } catch {
                // Sidebar enhancements must never block navigation.
            }
        };

        refreshUnread();
        const interval = window.setInterval(refreshUnread, 30000);
        observer = new MutationObserver(() => mountUnreadBadge(latestCount));
        observer.observe(document.body, { childList: true, subtree: true });

        return () => {
            active = false;
            window.clearInterval(interval);
            observer?.disconnect();
            removeNode(`[${UNREAD_BADGE_ATTR}="true"]`);
        };
    }, []);

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

    return null;
}
