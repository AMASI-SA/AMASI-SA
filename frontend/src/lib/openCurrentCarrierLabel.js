import { refreshCompletedOrderCarrierLabel } from "../services/fulfillmentV2";
import { verifyShippingLabel } from "../services/orderEngine";
import { API_BASE } from "./api";
import { printStoreCourierLabel } from "./storeCourierLabelPrint";
import { createPendingLabelPrint } from "./pendingLabelPrint";

const listeners = new Set();
const readyListeners = new Set();
export const pendingLabelPrint = createPendingLabelPrint({
    storage: { getItem: key => window.localStorage.getItem(key), setItem: (key, value) => window.localStorage.setItem(key, value) },
    load: (source, order) => source === "owner" ? verifyShippingLabel(order) : refreshCompletedOrderCarrierLabel(order),
    openWindow: () => window.open("about:blank", "_blank"),
    renderCourier: printStoreCourierLabel,
    changed: jobs => listeners.forEach(listener => listener(jobs)),
    onReady: (order, result) => readyListeners.forEach(listener => listener(order, result)),
});
export const pendingPrintScope = user => user?.id && (user.is_owner || user.permissions?.includes("fulfillment.labels.print"))
    ? `${API_BASE}:${user.id}` : null;
export function subscribePendingLabelPrint(listener) { listeners.add(listener); return () => listeners.delete(listener); }
export function subscribePendingLabelReady(listener) { readyListeners.add(listener); return () => readyListeners.delete(listener); }

// The initiating screen may disappear; auth and server permission remain authority.
export function openCurrentCarrierLabel(orderNumber, _isCurrent, source = "fulfillment") {
    return pendingLabelPrint.request(orderNumber, source);
}
