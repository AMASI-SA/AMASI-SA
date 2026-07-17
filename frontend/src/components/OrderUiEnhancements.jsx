import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL || ""}/api`;
const GIFT_BADGE_ATTR = "data-order-gift-detail-badge";

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

    return null;
}
