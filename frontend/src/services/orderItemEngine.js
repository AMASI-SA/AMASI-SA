import api from "../lib/api";

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;

    if (typeof detail === "string" && detail.trim()) {
        return detail;
    }

    if (detail && typeof detail === "object") {
        if (
            typeof detail.message === "string" &&
            detail.message.trim()
        ) {
            return detail.message;
        }

        if (detail.code === "order_item_not_found") {
            return "لم يتم العثور على عنصر الطلب.";
        }

        if (detail.code === "order_not_found") {
            return "لم يتم العثور على الطلب.";
        }

        if (detail.code === "owner_only") {
            return "هذه الصفحة متاحة للمالك فقط.";
        }

        if (detail.code === "invalid_order_item_request") {
            return "بيانات طلب عناصر المنتجات غير صالحة.";
        }
    }

    return error?.message || fallback;
}

function normalizeOrderNumber(orderNumber) {
    const normalized = String(orderNumber || "").trim();

    if (!normalized) {
        throw new Error("رقم الطلب مطلوب.");
    }

    return normalized;
}

export async function getOrderItems(orderNumber) {
    const normalized = normalizeOrderNumber(orderNumber);

    try {
        const { data } = await api.get(
            `/orders-v2/${encodeURIComponent(normalized)}/items`
        );

        return Array.isArray(data) ? data : [];
    } catch (error) {
        throw new Error(
            errorMessage(
                error,
                "تعذّر تحميل عناصر الطلب."
            )
        );
    }
}

export async function getOrderItem(
    orderNumber,
    orderItemId
) {
    const normalizedOrderNumber =
        normalizeOrderNumber(orderNumber);

    const normalizedItemId =
        String(orderItemId || "").trim();

    if (!normalizedItemId) {
        throw new Error("معرّف عنصر الطلب مطلوب.");
    }

    try {
        const { data } = await api.get(
            `/orders-v2/${encodeURIComponent(
                normalizedOrderNumber
            )}/items/${encodeURIComponent(
                normalizedItemId
            )}`
        );

        return data;
    } catch (error) {
        throw new Error(
            errorMessage(
                error,
                "تعذّر تحميل عنصر الطلب."
            )
        );
    }
}
