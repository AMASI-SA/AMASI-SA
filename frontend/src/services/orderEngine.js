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

function shippingLabelErrorMessage(error) {
    const fallback = "تعذّر إصدار بوليصة الشحن من سلة.";
    const message = errorMessage(error, fallback);
    const status = Number(error?.response?.status || 0);
    if (
        status >= 500
        || /cloudflare|origin web server|invalid or incomplete response|network error|timeout|\b520\b/i.test(message)
    ) {
        return "تعذّر الاتصال بسلة أثناء تجهيز البوليصة. لم تُطبع الشحنة؛ حاول مرة أخرى.";
    }
    return message;
}

export async function listOrders({
    limit = ORDER_PAGE_SIZE,
    cursor = null,
    statusGroup = null,
    statusExact = null,
    filters = null,
} = {}) {
    if (isPreviewDemoEnvironment()) {
        return listPreviewOrders({ limit, cursor, statusExact: statusExact || statusGroup });
    }
    try {
        const params = { limit };
        if (cursor) params.cursor = cursor;
        if (statusExact) params.status_exact = statusExact;
        else if (statusGroup) params.status_group = statusGroup;
        Object.entries(filters || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && String(value).trim()) params[key] = value;
        });
        const { data } = await api.get("/orders-v2", { params });
        return {
            items: Array.isArray(data?.items) ? data.items : [],
            nextCursor: data?.next_cursor || null,
            skippedInvalid: Number(data?.skipped_invalid || 0),
            resultSummary: data?.result_summary || null,
        };
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل الطلبات."));
    }
}

export async function listLatestSoldProductOrders({
    limit = ORDER_PAGE_SIZE,
    cursor = null,
} = {}) {
    if (isPreviewDemoEnvironment()) {
        return listPreviewOrders({ limit, cursor });
    }
    try {
        const params = { limit };
        if (cursor) params.cursor = cursor;
        const { data } = await api.get("/orders-v2/latest-sold-products", { params });
        return {
            items: Array.isArray(data?.items) ? data.items : [],
            nextCursor: data?.next_cursor || null,
            skippedInvalid: Number(data?.skipped_invalid || 0),
        };
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحميل أحدث المنتجات المباعة."));
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

export async function refreshOrderFromSalla(orderNumber, { force = true } = {}) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        return {
            ok: true,
            found: true,
            updated: false,
            skipped: true,
            source: "preview",
        };
    }
    try {
        const { data } = await api.post(
            `/orders-v2/${encodeURIComponent(normalized)}/refresh-from-salla`,
            null,
            { params: { force: Boolean(force) } },
        );
        return data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحديث الطلب من سلة."));
    }
}

// Compatibility alias for callers that used the earlier name.
export async function openOrderFromSalla(orderNumber) {
    return refreshOrderFromSalla(orderNumber, { force: true });
}


export async function markOrderRead(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized || isPreviewDemoEnvironment()) return { ok: true, read: true };
    try {
        const { data } = await api.post(`/orders-v2/${encodeURIComponent(normalized)}/read`);
        return data;
    } catch (error) {
        throw new Error(errorMessage(error, "تعذّر تحديث حالة قراءة الطلب."));
    }
}

export async function issueShippingLabel(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        throw new Error("إصدار البوليصة غير متاح في المعاينة التجريبية.");
    }
    try {
        const { data } = await api.post(
            `/orders-v2/${encodeURIComponent(normalized)}/shipping-label`,
        );
        return data;
    } catch (error) {
        throw new Error(shippingLabelErrorMessage(error));
    }
}


export async function verifyShippingLabel(orderNumber) {
    const normalized = String(orderNumber || "").trim();
    if (!normalized) throw new Error("رقم الطلب مطلوب.");
    if (isPreviewDemoEnvironment()) {
        return { ok: true, ready: false, source: "preview" };
    }
    try {
        const { data } = await api.post(
            `/orders-v2/${encodeURIComponent(normalized)}/shipping-label/refresh`,
        );
        return data;
    } catch (error) {
        throw new Error(shippingLabelErrorMessage(error));
    }
}
