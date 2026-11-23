import api from "../lib/api";
import { bundleError } from "./productBundles";

const path = (orderNumber) => `/fulfillment-v2/orders/${encodeURIComponent(orderNumber)}/bundle-adoption`;

export async function previewBundleAdoption(orderNumber, orderLineId) {
    return (await api.post(`${path(orderNumber)}/preview`, { order_line_id: orderLineId })).data;
}

export async function applyBundleAdoption(orderNumber, payload) {
    return (await api.post(`${path(orderNumber)}/apply`, payload)).data;
}

export function newBundleAdoptionRequestId() {
    if (globalThis.crypto?.randomUUID) return `bundle-adoption:${globalThis.crypto.randomUUID()}`;
    if (!globalThis.crypto?.getRandomValues) throw new Error("تعذّر إنشاء مرجع العملية. حدّث المتصفح وأعد المحاولة.");
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    return `bundle-adoption:${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

export function bundleAdoptionError(error) {
    return bundleError(error);
}
