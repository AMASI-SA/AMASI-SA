import api from "../lib/api";
import {
    getPreviewOrder,
    getPreviewOrderSummary,
    isPreviewDemoEnvironment,
    listPreviewOrders,
} from "../demo/orderPreviewFixtures";

export const ORDER_PAGE_SIZE = 15;

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
        if (typeof detail.message === "string" && detail.message.trim()) return detail.message;
        if (detail.code === "order_not_found") return "لم يتم العثور على الطلب.";
        if (detail.code === "owner_only") return "هذه الصفحة متاحة للمالك فقط.";
    }
    return error?.message || fallback;
}

export async function listOrders({
    limit = ORDER_PAGE_SIZE,
    cursor = null,
    statusGroup = null,
    statusExact = null,
} = {}) {
    if (isPreviewDemoEnvironment()) {
        return listPreviewOrders({ limit, cursor, statusExact: statusExact || statusGroup });
    }
    try {
        const params = { limit };
        if (cursor) params.cursor = cursor;
        if (statusExact) params.status_exact = statusExact;
        else if (statusGroup) params.status_group = statusGroup;
        const { data } = await api.get("/orders-v2", { params });
        return {
            items: Array.isArray(data?.items) ? data.items : [],
            nextCursor: data?.next_cursor || null,
            skippedInvalid: Number(data?.skipped_invalid || 0),
        };
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل الطلبات."));
    }
}

export async function getOrderFilterSummary() {
    if (isPreviewDemoEnvironment()) return getPreviewOrderSummary();
    try {
        const { data } = await api.get("/orders-v2/filters/summary");
        return {
            total: Number(data?.total || 0),
            statusCards: Array.isArray(data?.status_cards) ? data.status_cards : [],
            statusCounts: data?.status_counts || {},
        };
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل عدادات الطلبات."));
    }
}

export async function getOrder(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        const order = getPreviewOrder(normalized);
        if (!order) throw new Error("لم يتم العثور على الطلب التجريبي.");
        return order;
    }
    try {
        const { data } = await api.get(`/orders-v2/${encodeURIComponent(normalized)}`);
        return data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل تفاصيل الطلب."));
    }
}

export async function openOrderFromSalla(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) return null;
    if (isPreviewDemoEnvironment()) {
        return { ok: true, demo: true, read_only: true, no_external_calls: true };
    }
    try {
        const { data } = await api.post(`/orders/${encodeURIComponent(normalized)}/resync`);
        return data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحديث الطلب عند فتحه."));
    }
}
