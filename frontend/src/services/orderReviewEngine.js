import api from "../lib/api";

function message(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail?.message) return detail.message;
    if (detail?.code === "review_revision_conflict") return "تم تعديل الطلب من موظف آخر. حدّث البيانات ثم أعد المحاولة.";
    if (detail?.code === "review_already_completed") return "تم اعتماد مراجعة هذا الطلب سابقًا.";
    return error?.message || fallback;
}

export async function listPendingOrderReviews({ limit = 15, cursor = null } = {}) {
    try {
        const params = { limit };
        if (cursor) params.cursor = cursor;
        const { data } = await api.get("/order-reviews-v1", { params });
        return {
            items: Array.isArray(data?.items) ? data.items : [],
            nextCursor: data?.next_cursor || null,
        };
    } catch (error) {
        throw new Error(message(error, "تعذّر تحميل الطلبات بانتظار المراجعة."));
    }
}

export async function listReviewedOrderReviews({ limit = 50 } = {}) {
    try {
        const { data } = await api.get("/order-reviews-v1/reviewed", { params: { limit } });
        return { items: Array.isArray(data?.items) ? data.items : [] };
    } catch (error) {
        throw new Error(message(error, "تعذّر تحميل الطلبات التي تمت مراجعتها."));
    }
}

export async function getOrderReview(orderNumber) {
    try {
        const { data } = await api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`);
        return data;
    } catch (error) {
        throw new Error(message(error, "تعذّر تحميل بيانات المراجعة."));
    }
}

export async function updateOrderReviewItem(orderNumber, orderItemId, payload) {
    try {
        const { data } = await api.patch(
            `/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}`,
            payload,
        );
        return data;
    } catch (error) {
        throw new Error(message(error, "تعذّر حفظ إعدادات المنتج."));
    }
}

export async function completeOrderReview(orderNumber, expectedRevision) {
    try {
        const { data } = await api.post(
            `/order-reviews-v1/${encodeURIComponent(orderNumber)}/complete`,
            { expected_revision: expectedRevision },
        );
        return data;
    } catch (error) {
        throw new Error(message(error, "تعذّر اعتماد مراجعة الطلب."));
    }
}

export async function createOrderReviewOperationalItem(orderNumber, payload) {
    try {
        const { data } = await api.post(
            `/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items`,
            payload,
        );
        return data;
    } catch (error) {
        throw new Error(message(error, "تعذّر إضافة المنتج التشغيلي."));
    }
}

export async function updateOrderReviewOperationalItemStatus(orderNumber, operationalItemId, payload) {
    try {
        const { data } = await api.patch(
            `/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items/${encodeURIComponent(operationalItemId)}`,
            payload,
        );
        return data;
    } catch (error) {
        throw new Error(message(error, "تعذّر تحديث حالة المنتج التشغيلي."));
    }
}
