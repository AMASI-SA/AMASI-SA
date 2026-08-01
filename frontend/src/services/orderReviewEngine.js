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
        return { items: Array.isArray(data?.items) ? data.items : [], nextCursor: data?.next_cursor || null };
    } catch (error) { throw new Error(message(error, "تعذّر تحميل الطلبات بانتظار المراجعة.")); }
}

export async function listReviewedOrderReviews({ limit = 50 } = {}) {
    try {
        const { data } = await api.get("/order-reviews-v1/reviewed", { params: { limit } });
        return { items: Array.isArray(data?.items) ? data.items : [] };
    } catch (error) { throw new Error(message(error, "تعذّر تحميل الطلبات التي تمت مراجعتها.")); }
}

export async function listReviewedProductCatalog({ limit = 500 } = {}) {
    try {
        const { data } = await api.get("/reviewed-products-v1/catalog", { params: { limit } });
        return {
            products: Array.isArray(data?.products) ? data.products : [],
            categories: Array.isArray(data?.categories) ? data.categories : [],
            summary: data?.summary || {},
            truncated: Boolean(data?.truncated),
        };
    } catch (error) { throw new Error(message(error, "تعذّر تحميل منتجات مرحلة تمت المراجعة.")); }
}

export async function getOrderReview(orderNumber) {
    try { return (await api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`)).data; }
    catch (error) { throw new Error(message(error, "تعذّر تحميل بيانات المراجعة.")); }
}

export async function updateOrderReviewItem(orderNumber, orderItemId, payload) {
    try { return (await api.patch(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر حفظ إعدادات المنتج.")); }
}

export async function completeOrderReview(orderNumber, expectedRevision) {
    try { return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/complete`, { expected_revision: expectedRevision })).data; }
    catch (error) { throw new Error(message(error, "تعذّر اعتماد مراجعة الطلب.")); }
}

export async function createOrderReviewOperationalItem(orderNumber, payload) {
    try { return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر إضافة المنتج التشغيلي.")); }
}

export async function updateOrderReviewOperationalItemStatus(orderNumber, operationalItemId, payload) {
    try { return (await api.patch(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items/${encodeURIComponent(operationalItemId)}`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر تحديث حالة المنتج التشغيلي.")); }
}

export async function unlinkOrderReviewOperationalItem(orderNumber, operationalItemId, expectedRevision) {
    try { return (await api.delete(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/operational-items/${encodeURIComponent(operationalItemId)}`, { params: { expected_revision: expectedRevision } })).data; }
    catch (error) { throw new Error(message(error, "تعذّر إلغاء ربط المنتج التشغيلي.")); }
}

export async function saveOrderReviewImageChoice(orderNumber, orderItemId, payload) {
    try { return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}/image-choice`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر حفظ اختيار صورة التجهيز.")); }
}

export async function uploadOrderReviewMezanImage(orderNumber, orderItemId, payload) {
    try { return (await api.post(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}/mezan-images`, payload)).data; }
    catch (error) { throw new Error(message(error, "تعذّر رفع صورة ميزان.")); }
}

export async function deleteOrderReviewMezanImage(orderNumber, orderItemId, imageId) {
    try { return (await api.delete(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}/mezan-images/${encodeURIComponent(imageId)}`)).data; }
    catch (error) { throw new Error(message(error, "تعذّر حذف صورة ميزان.")); }
}
