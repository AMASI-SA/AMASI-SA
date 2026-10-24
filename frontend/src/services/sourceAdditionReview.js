import api from "../lib/api";

const route = (number) => `/order-reviews-v1/${encodeURIComponent(number)}`;

export async function getSourceAdditions(orderNumber) {
    const { data } = await api.get(`${route(orderNumber)}/source-additions`);
    if (!Number.isInteger(data?.revision) || data.revision < 0
        || typeof data.source_fingerprint !== "string" || !Array.isArray(data.items)
        || data.items.some((row) => !row || typeof row.order_item_id !== "string"
            || !row.order_item_id || typeof row.eligible !== "boolean" || typeof row.reviewed !== "boolean")
        || new Set(data.items.map((row) => row.order_item_id)).size !== data.items.length) {
        throw new Error("تعذّر التحقق من حالة مراجعة المنتجات المضافة. حدّث البيانات.");
    }
    return data;
}

export async function reviewSourceAddition(orderNumber, orderItemId, snapshot) {
    const { data } = await api.post(`${route(orderNumber)}/items/${encodeURIComponent(orderItemId)}/review-addition`, {
        expected_revision: snapshot.revision,
        source_fingerprint: snapshot.source_fingerprint,
    });
    if (data?.ok !== true || data.order_item_id !== orderItemId) {
        throw new Error("لم يصل تأكيد مراجعة المنتج؛ يجري التحقق من حالته.");
    }
    return data;
}

export function sourceAdditionError(error) {
    const detail = error?.response?.data?.detail;
    if (typeof detail?.message === "string" && detail.message) return detail.message;
    if (detail?.code === "component_insufficient_stock") return "مخزون أحد مكونات التجهيز غير كافٍ. لم يتم اعتماد المنتج المضاف.";
    if (typeof detail?.code === "string" && detail.code.startsWith("component_")) return "تعذّرت مطابقة مكونات التجهيز مع الطلب. لم يتم اعتماد المنتج؛ تحقق من المكونات ثم أعد المحاولة.";
    if (error?.response?.status === 409) return "تغيّرت بيانات الطلب. راجع الحالة المحدّثة ثم أعد المحاولة.";
    if ([401, 403].includes(error?.response?.status)) return "تعذّر التحقق من صلاحية مراجعة المنتج. حدّث تسجيل الدخول والصلاحيات.";
    return error?.message || "تعذّر التحقق من مراجعة المنتج. حدّث البيانات قبل المحاولة.";
}
