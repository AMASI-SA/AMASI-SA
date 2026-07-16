import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useParams } from "react-router-dom";
import CompactOrderTimeline from "../components/CompactOrderTimeline";
import { useOrder } from "../hooks/useOrders";
import OriginalOrderDetailsV2 from "./OrderDetailsV2.jsx";

/**
 * Thin compatibility wrapper.
 *
 * App.js imports `./pages/OrderDetailsV2` without an extension. The frontend
 * resolver loads this .js file before the legacy .jsx page. We keep the
 * approved page intact and replace only its timeline placeholder with the
 * compact horizontal audit trail.
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

    return (
        <>
            <OriginalOrderDetailsV2 />
            {timelineHost && order ? createPortal(<CompactOrderTimeline order={order} />, timelineHost) : null}
        </>
    );
}
